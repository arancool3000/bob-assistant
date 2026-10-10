"""Skills: abilities Kevin writes for himself when you ask by voice.

"Hey Kevin, I've wired an LED to GPIO 17 -- make a skill that turns it on and off." Kevin writes a small Python file,
reads back what it will do, and only installs it after you say yes. Each skill is one file in
/var/lib/bob-assistant-skills/ that looks like this:

    SKILL = {
        "name": "desk_led",
        "description": "Turn the LED on GPIO 17 on or off.",
        "parameters": {"type": "OBJECT", "properties": {"on": {"type": "BOOLEAN"}}, "required": ["on"]},
    }

    def run(on=True):
        from gpiozero import LED
        ...
        return {"say": "LED on."}

Skills run in the skill host (skillhost.py): one long-running process as the separate `bob-skill` user, so a
skill's objects stay alive between calls (an LED you switch on stays on) and a skill can never read Kevin's API key,
change his files or stop him. Installing or deleting a skill needs a yes from you: the tool refuses
confirmed=true unless that exact code was offered for confirmation first. New skills can be used straight away
through run_skill, and appear as tools of their own from the next conversation.
"""
import ast
import difflib
import glob
import hashlib
import json
import os
import re
import shutil
import time

from . import config, skillhost

SKILLS_DIR = os.environ.get('BOB_SKILLS', '/var/lib/bob-assistant-skills')
NAME_RE = re.compile(r'^[a-z][a-z0-9_]{1,40}$')
MAX_BYTES = 20000
RESERVED = {'create_skill', 'run_skill', 'list_skills', 'show_skill', 'delete_skill', 'test_skill', 'disable_skill',
            'enable_skill', 'rollback_skill', 'i2c_scan', 'install_example_skill'}
HISTORY = os.path.join(SKILLS_DIR, '.history')     # earlier versions of each skill, newest last (10 kept)
KEEP_VERSIONS = 10
PENDING = os.path.join(config.STATE, 'pending-skills')   # skills waiting for a yes, shown on the settings page
EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'examples', 'skills')


TYPES = {'STRING', 'NUMBER', 'INTEGER', 'BOOLEAN', 'ARRAY', 'OBJECT'}
_offered = {}                          # sha256(name + code) -> when it was offered for confirmation


def _path(name):
    if not NAME_RE.match(str(name or '')):
        raise ValueError('a skill name is lower case letters, digits and _ (e.g. desk_led)')
    return os.path.join(SKILLS_DIR, name + '.py')


def _check_schema(props):
    if not isinstance(props, dict):
        raise ValueError('parameters.properties must be a dict')
    for k, v in props.items():
        if not isinstance(v, dict) or str(v.get('type', '')).upper() not in TYPES:
            raise ValueError('argument %r needs a "type": one of %s' % (k, ', '.join(sorted(TYPES))))
        v['type'] = v['type'].upper()
        if v['type'] == 'OBJECT':
            _check_schema(v.get('properties', {}))
        if v['type'] == 'ARRAY' and isinstance(v.get('items'), dict):
            _check_schema({'item': v['items']})


def validate(name, code):
    """Return the SKILL dict, or raise ValueError with a reason Kevin can read out and fix."""
    if not NAME_RE.match(name or ''):
        raise ValueError('a skill name is lower case letters, digits and _ (e.g. desk_led)')
    if name in RESERVED:
        raise ValueError('that name is taken by a built-in')
    if len(code.encode()) > MAX_BYTES:
        raise ValueError('the code is over 20 KB: keep a skill small')
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise ValueError('the code does not parse: %s (line %s)' % (e.msg, e.lineno))
    skill, has_run = None, False
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == 'run':
            has_run = True
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'SKILL' for t in node.targets):
            try:
                skill = ast.literal_eval(node.value)
            except ValueError:
                raise ValueError('SKILL must be a plain dict (no function calls inside it)')
    if not has_run:
        raise ValueError('a skill needs a run(...) function')
    if not isinstance(skill, dict) or not isinstance(skill.get('description'), str) or not skill['description'].strip():
        raise ValueError('a skill needs SKILL = {"name": ..., "description": "...", "parameters": {...}}')
    skill['name'] = name
    params = skill.get('parameters') or {'type': 'OBJECT', 'properties': {}}
    if not isinstance(params, dict) or str(params.get('type', '')).upper() != 'OBJECT':
        raise ValueError('parameters must be {"type": "OBJECT", "properties": {...}}')
    params['type'] = 'OBJECT'
    params.setdefault('properties', {})
    _check_schema(params['properties'])
    req = params.get('required', [])
    if not isinstance(req, list) or any(r not in params['properties'] for r in req):
        raise ValueError('"required" must list argument names from properties')
    skill['parameters'] = params
    return skill


def _off(name):
    return _path(name) + '.off'


def _current(name):
    """The file holding a skill now: name.py, or name.py.off when it is switched off. None if neither."""
    for p in (_path(name), _off(name)):
        if os.path.exists(p):
            return p
    return None


def _keep_version(name):
    """Copy the skill as it is now into its history before it is replaced."""
    cur = _current(name)
    if not cur:
        return
    d = os.path.join(HISTORY, name)
    os.makedirs(d, exist_ok=True)
    shutil.copy2(cur, os.path.join(d, '%d.py' % int(time.time() * 1000)))
    for old in sorted(glob.glob(os.path.join(d, '*.py')))[:-KEEP_VERSIONS]:
        os.remove(old)


def versions(name):
    """Earlier versions of a skill, newest first: [{'id', 'when', 'code'}]."""
    _path(name)
    out = []
    for p in sorted(glob.glob(os.path.join(HISTORY, name, '*.py')), reverse=True):
        vid = os.path.basename(p)[:-3]
        with open(p) as f:
            out.append({'id': vid, 'when': time.strftime('%d %b %H:%M', time.localtime(int(vid) / 1000)), 'code': f.read()})
    return out


def save(name, code):
    os.makedirs(SKILLS_DIR, exist_ok=True)
    _keep_version(name)
    off = os.path.exists(_off(name))
    tmp = _path(name) + '.tmp'
    with open(tmp, 'w') as f:
        f.write(code)
    os.replace(tmp, _off(name) if off else _path(name))     # a switched-off skill stays off when it is replaced


def set_enabled(name, on):
    """Switch one skill off (kept, not loaded) or back on, without touching any other skill or an update."""
    src, dst = (_off(name), _path(name)) if on else (_path(name), _off(name))
    if not os.path.exists(src):
        return os.path.exists(dst)            # already in that state (or no such skill: False)
    os.replace(src, dst)
    reload_host()
    return True


def is_enabled(name):
    return os.path.exists(_path(name))


def rollback(name, version_id=None):
    """Put an earlier version back (the newest earlier one unless version_id says which). The current one is kept
    in the history too, so a rollback can itself be undone."""
    vs = versions(name)
    if not vs:
        raise ValueError('%s has no earlier version' % name)
    v = next((x for x in vs if x['id'] == str(version_id)), None) if version_id else vs[0]
    if not v:
        raise ValueError('no such version of %s' % name)
    validate(name, v['code'])
    save(name, v['code'])
    reload_host()
    return v


# ---- what a skill can reach (shown before you say yes) ------------------------------------------------

ACCESS_IMPORTS = [
    (('gpiozero', 'RPi', 'lgpio', 'pigpio'), 'GPIO pins (switching things on and off, reading buttons)'),
    (('smbus', 'smbus2', 'board', 'busio', 'spidev', 'adafruit_'), 'I2C / SPI sensors and chips'),
    (('serial',), 'serial ports (USB devices, Arduinos)'),
    (('urllib', 'http', 'requests', 'socket', 'websocket', 'websockets', 'ftplib', 'smtplib', 'ssl', 'aiohttp'),
     'the internet and your home network'),
    (('subprocess', 'pty', 'multiprocessing'), 'running other programs on the Pi'),
    (('ctypes', 'importlib'), 'low-level or dynamically loaded code'),
    (('picamera2', 'cv2'), 'a camera'),
    (('sounddevice', 'pyaudio', 'alsaaudio'), 'the microphone or speaker'),
]
SENSITIVE = ('the internet and your home network', 'running other programs on the Pi', 'low-level or dynamically loaded code',
             'a camera', 'the microphone or speaker', 'writing files')


def access_report(code):
    """What a skill's code reaches for, read from the code itself (its imports and calls), in plain words.
    It reports, it does not sandbox: the hard limits are the skill host's (see BOUNDARY)."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return ['the code does not parse']
    mods, calls = set(), set()
    writes = reads = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split('.')[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split('.')[0])
        elif isinstance(node, ast.Call):
            f = node.func
            nm = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else '')
            calls.add(nm)
            if nm == 'open':
                mode = ''
                if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                    mode = str(node.args[1].value)
                for kw in node.keywords:
                    if kw.arg == 'mode' and isinstance(kw.value, ast.Constant):
                        mode = str(kw.value.value)
                if any(c in mode for c in 'wax+'):
                    writes = True
                else:
                    reads = True
            if nm in ('write_text', 'write_bytes', 'remove', 'unlink', 'rmtree', 'rename', 'replace', 'makedirs', 'mkdir'):
                writes = True
            if nm in ('read_text', 'read_bytes', 'listdir', 'glob', 'walk', 'scandir'):
                reads = True
    out = []
    for names, what in ACCESS_IMPORTS:
        if any(m == n or (n.endswith('_') and m.startswith(n)) for m in mods for n in names):
            out.append(what)
    if 'system' in calls or 'popen' in calls:
        if 'running other programs on the Pi' not in out:
            out.append('running other programs on the Pi')
    if {'exec', 'eval', 'compile', '__import__'} & calls and 'low-level or dynamically loaded code' not in out:
        out.append('low-level or dynamically loaded code')
    if writes:
        out.append('writing files')
    if reads:
        out.append('reading files')
    return out or ['nothing beyond plain Python']


BOUNDARY = ('Every skill runs as the separate bob-skill user: it can only write in /var/lib/bob-skill, cannot read '
            "Kevin's settings or API key, and cannot change Kevin or other skills' files. It can use the network.")


def diff(old, new):
    return ''.join(difflib.unified_diff((old or '').splitlines(True), new.splitlines(True), 'installed', 'new', n=3))


def _pending_path(name):
    _path(name)
    return os.path.join(PENDING, name + '.json')


def set_pending(name, code, why=''):
    """Write the skill waiting for a yes where the settings page shows it: full code, changes, what it can reach."""
    os.makedirs(PENDING, mode=0o750, exist_ok=True)
    cur = _current(name)
    old = open(cur).read() if cur else ''
    d = {'name': name, 'code': code, 'sha': _fingerprint('create', name, code), 'replaces': bool(cur),
         'diff': diff(old, code) if cur else '', 'access': access_report(code), 'at': time.time(), 'why': why}
    with open(_pending_path(name) + '.tmp', 'w') as f:
        json.dump(d, f)
    os.replace(_pending_path(name) + '.tmp', _pending_path(name))
    return d


def pending():
    out = []
    for p in sorted(glob.glob(os.path.join(PENDING, '*.json'))):
        try:
            with open(p) as f:
                d = json.load(f)
            if time.time() - d.get('at', 0) < 86400:
                out.append(d)
            else:
                os.remove(p)
        except (OSError, ValueError):
            pass
    return out


def get_pending(name):
    try:
        with open(_pending_path(name)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def clear_pending(name, rejected=False):
    """Done with the waiting skill. rejected=True (Reject on the page) also remembers that exact code as turned
    down, so a spoken yes to it afterwards is refused."""
    try:
        d = get_pending(name)
        if rejected and d:
            open(os.path.join(PENDING, d['sha'] + '.rejected'), 'w').close()
        os.remove(_pending_path(name))
    except (OSError, ValueError):
        pass


def was_rejected(name, code):
    return os.path.exists(os.path.join(PENDING, _fingerprint('create', name, code) + '.rejected'))


def approve(name, sha):
    """The settings page's Approve: installs exactly the code that was shown (sha binds the two)."""
    d = get_pending(name)
    if not d or d.get('sha') != sha:
        raise ValueError('that skill is no longer waiting, or it changed since the page was opened')
    validate(name, d['code'])
    save(name, d['code'])
    clear_pending(name)
    reload_host()
    return d


def load_all():
    """Every installed skill's SKILL dict (bad files are skipped and reported, never fatal)."""
    out, bad = [], []
    if not os.path.isdir(SKILLS_DIR):
        return out, bad
    for fn in sorted(os.listdir(SKILLS_DIR)):
        if not fn.endswith('.py'):
            continue
        name = fn[:-3]
        try:
            with open(os.path.join(SKILLS_DIR, fn)) as f:
                out.append(validate(name, f.read()))
        except Exception as e:                    # one broken file never stops Kevin
            bad.append({'name': name, 'problem': str(e)[:200]})
    return out, bad


def declarations():
    """The skills as Gemini tools, named skill_<name>."""
    skills, _bad = load_all()
    return [{'name': 'skill_' + s['name'], 'description': s['description'][:900], 'parameters': s['parameters']}
            for s in skills]


def run(name, args=None):
    """Run one skill in the skill host and return its result dict."""
    if not os.path.exists(_path(name)):
        return {'error': 'there is no skill called %s' % name}
    try:
        return skillhost.call({'cmd': 'run', 'name': name, 'args': args or {}})
    except (OSError, ValueError) as e:
        return {'error': 'the skill host is not answering (%s)' % str(e)[:80]}


def reload_host():
    """New or changed skills: the host restarts so every skill loads fresh."""
    try:
        skillhost.call({'cmd': 'reload'}, timeout=5)
    except Exception:
        pass
    time.sleep(0.3)
    for _ in range(50):                           # wait until it really answers again (up to ~10 s)
        try:
            if skillhost.call({'cmd': 'ping'}, timeout=2).get('ok'):
                return True
        except (OSError, ValueError):
            pass
        time.sleep(0.2)
    return False


def delete(name):
    try:
        cur = _current(name)
        if not cur:
            return False
        _keep_version(name)                    # deleted, but its versions stay a while (Rollback brings it back)
        os.remove(cur)
        reload_host()
        return True
    except (OSError, ValueError):
        return False


def source(name):
    try:
        cur = _current(name)
        if not cur:
            return None
        with open(cur) as f:
            return f.read()
    except (OSError, ValueError):
        return None


def disabled_names():
    try:
        return sorted(f[:-7] for f in os.listdir(SKILLS_DIR) if f.endswith('.py.off'))
    except OSError:
        return []


def _fingerprint(*parts):
    return hashlib.sha256('\x00'.join(str(p) for p in parts).encode()).hexdigest()


def offer(*parts):
    """Remember that this exact thing was offered for a yes."""
    now = time.time()
    for k in [k for k, t in _offered.items() if now - t > 900]:
        del _offered[k]
    _offered[_fingerprint(*parts)] = now


def was_offered(*parts, min_gap=2.0):
    """True if this exact thing was offered for a yes at least min_gap seconds ago (time for the user to answer)."""
    t = _offered.get(_fingerprint(*parts))
    return bool(t and min_gap <= time.time() - t <= 900)


# ---- the tools Kevin uses to build skills -------------------------------------------------------------

S = lambda d: {'type': 'STRING', 'description': d}      # noqa: E731
B = lambda d: {'type': 'BOOLEAN', 'description': d}     # noqa: E731

DECLARATIONS = [
    {'name': 'create_skill', 'description': (
        'Write a new skill for yourself in Python when the user asks for something you cannot do yet -- especially '
        'hardware he has wired to the Pi (LEDs, buttons, relays, sensors, servos, motors on the GPIO pins, I2C or '
        'serial). The code must define SKILL = {"name", "description", "parameters": {"type": "OBJECT", '
        '"properties": {...}, "required": [...]}} and def run(**args) returning a dict with a short "say". Use '
        'gpiozero for GPIO (BCM pin numbers; create devices at the top of the file so outputs stay on between calls), '
        'smbus2 for I2C, serial (pyserial) for serial, urllib for the web; save files under /var/lib/bob-skill. First call '
        'WITHOUT confirmed: you get a summary to read to the user; only after they say yes call again with '
        'confirmed=true. Then try it with test_skill.'),
     'parameters': {'type': 'OBJECT', 'properties': {
         'name': S('lower_case_name, e.g. desk_led'), 'code': S('the whole Python file'),
         'confirmed': B('true only after the user said yes to installing it')}, 'required': ['name', 'code']}},
    {'name': 'test_skill', 'description': 'Run one of your skills once with some arguments to check it works, and report what happened.',
     'parameters': {'type': 'OBJECT', 'properties': {'name': S('the skill'), 'args_json': S('its arguments as JSON, e.g. {"on": true}')},
                    'required': ['name']}},
    {'name': 'run_skill', 'description': 'Use one of your skills (the way to use a brand-new one in this same conversation).',
     'parameters': {'type': 'OBJECT', 'properties': {'name': S('the skill'), 'args_json': S('its arguments as JSON')}, 'required': ['name']}},
    {'name': 'list_skills', 'description': 'The skills you have made, with what each does (and any that are broken).',
     'parameters': {'type': 'OBJECT', 'properties': {}}},
    {'name': 'show_skill', 'description': 'The code of one skill, to read or improve it.',
     'parameters': {'type': 'OBJECT', 'properties': {'name': S('the skill')}, 'required': ['name']}},
    {'name': 'delete_skill', 'description': 'Remove a skill. Ask the user first; call with confirmed=true after their yes.',
     'parameters': {'type': 'OBJECT', 'properties': {'name': S('the skill'), 'confirmed': B('true after a yes')}, 'required': ['name']}},
    {'name': 'disable_skill', 'description': 'Switch one skill off without deleting it (it stops being loaded; nothing else changes). Use when a skill misbehaves.',
     'parameters': {'type': 'OBJECT', 'properties': {'name': S('the skill')}, 'required': ['name']}},
    {'name': 'enable_skill', 'description': 'Switch a skill that was switched off back on.',
     'parameters': {'type': 'OBJECT', 'properties': {'name': S('the skill')}, 'required': ['name']}},
    {'name': 'rollback_skill', 'description': 'Put back the previous version of one skill (the last 10 are kept; a deleted skill can be brought back too). Ask first; confirmed=true after a yes.',
     'parameters': {'type': 'OBJECT', 'properties': {'name': S('the skill'), 'confirmed': B('true after a yes')}, 'required': ['name']}},
    {'name': 'i2c_scan', 'description': 'Find what is plugged into the I2C pins (SDA pin 3, SCL pin 5): the addresses that answer and what each probably is. Read-only. Use before writing a skill for an I2C sensor.',
     'parameters': {'type': 'OBJECT', 'properties': {}}},
    {'name': 'install_example_skill', 'description': 'Offer one of the ready-made example skills (with no name: list them). The best first one is room_temperature, a read-only BME280/BMP280 sensor. Goes through the same yes as create_skill.',
     'parameters': {'type': 'OBJECT', 'properties': {'name': S('e.g. room_temperature'), 'confirmed': B('true after a yes')}}},
]


def _args(args_json):
    if isinstance(args_json, dict):
        return args_json
    try:
        a = json.loads(args_json) if args_json else {}
        return a if isinstance(a, dict) else {}
    except ValueError:
        raise ValueError('args_json is not valid JSON')


def handle(name, args):
    """Run one of the skill-building tools. Returns a result dict, or None if `name` is not one of them."""
    a = args or {}
    if name == 'install_example_skill':
        ex = sorted(os.path.basename(p)[:-3] for p in glob.glob(os.path.join(EXAMPLES, '*.py')))
        want = str(a.get('name') or '').strip().lower().replace(' ', '_')
        if not want:
            return {'examples': ex, 'say': 'Ready-made skills: %s.' % ', '.join(e.replace('_', ' ') for e in ex)}
        if want not in ex:
            return {'error': 'no example called %s' % want, 'examples': ex}
        with open(os.path.join(EXAMPLES, want + '.py')) as f:
            return handle('create_skill', {'name': want, 'code': f.read(), 'confirmed': a.get('confirmed')})
    if name == 'create_skill':
        try:
            skill = validate(a.get('name', ''), a.get('code', ''))
        except ValueError as e:
            return {'error': str(e), 'fix': 'correct the code and call create_skill again'}
        nm, code = skill['name'], a.get('code', '')
        props = ', '.join(skill['parameters'].get('properties', {}).keys()) or 'no settings'
        page_only = config.load().get('skill_approval') == 'page'
        if not a.get('confirmed') or not was_offered('create', nm, code):
            offer('create', nm, code)
            d = set_pending(nm, code)
            risky = [x for x in d['access'] if x in SENSITIVE]
            return {'needs_confirmation': True, 'access': d['access'],
                    'say': '%s a skill called %s: %s It takes %s.%s The full code%s and everything it can reach are on '
                           'the settings page. %s' % (
                        'I will replace' if d['replaces'] else 'I can add', nm.replace('_', ' '),
                        skill['description'].rstrip('.') + '.', props,
                        (' It uses %s.' % ' and '.join(risky)) if risky else '',
                        ', what changed,' if d['replaces'] else '',
                        'Approve it there.' if page_only else 'Shall I install it, or do you want to read it there first?')}
        if page_only:
            return {'needs_confirmation': True, 'say': 'Skills are approved on the settings page only: it is waiting there.'}
        if was_rejected(nm, code):
            return {'error': 'that exact skill was rejected on the settings page',
                    'say': 'That one was turned down on the settings page, so I have not installed it.'}
        save(nm, code)
        clear_pending(nm)
        reload_host()
        return {'ok': True, 'installed': nm, 'next': 'try it with test_skill',
                'say': 'Installed %s.' % nm.replace('_', ' ')}
    if name in ('disable_skill', 'enable_skill'):
        on = name == 'enable_skill'
        try:
            ok = set_enabled(a.get('name', ''), on)
        except ValueError as e:
            return {'error': str(e)}
        return {'ok': ok, 'say': ('%s is %s.' % (str(a.get('name')).replace('_', ' '), 'on' if on else 'switched off')) if ok
                else 'There is no skill called %s.' % a.get('name')}
    if name == 'rollback_skill':
        nm = a.get('name', '')
        try:
            vs = versions(nm)
        except ValueError as e:
            return {'error': str(e)}
        if not vs:
            return {'error': 'no earlier version', 'say': 'There is no earlier version of %s.' % str(nm).replace('_', ' ')}
        if not a.get('confirmed') or not was_offered('rollback', nm, vs[0]['id']):
            offer('rollback', nm, vs[0]['id'])
            return {'needs_confirmation': True, 'say': 'Put %s back to the version from %s?' % (nm.replace('_', ' '), vs[0]['when'])}
        try:
            v = rollback(nm, vs[0]['id'])
        except ValueError as e:
            return {'error': str(e)}
        return {'ok': True, 'say': '%s is back to the version from %s.' % (nm.replace('_', ' '), v['when'])}
    if name == 'i2c_scan':
        try:
            return skillhost.call({'cmd': 'i2c_scan'})
        except (OSError, ValueError) as e:
            return {'error': 'the skill host is not answering (%s)' % str(e)[:80]}
    if name in ('test_skill', 'run_skill'):
        try:
            return run(a.get('name', ''), _args(a.get('args_json')))
        except (ValueError, TypeError) as e:
            return {'error': str(e)}
    if name == 'list_skills':
        skills, bad = load_all()
        return {'skills': [{'name': s['name'], 'does': s['description']} for s in skills], 'broken': bad,
                'switched_off': disabled_names(),
                'say': ('No skills yet.' if not skills else 'I have %d: %s.' % (len(skills), ', '.join(s['name'].replace('_', ' ') for s in skills)))}
    if name == 'show_skill':
        src = source(a.get('name', ''))
        return {'code': src} if src is not None else {'error': 'no skill called %s' % a.get('name')}
    if name == 'delete_skill':
        if not a.get('confirmed') or not was_offered('delete', a.get('name', '')):
            offer('delete', a.get('name', ''))
            return {'needs_confirmation': True, 'say': 'Delete the %s skill?' % str(a.get('name', '')).replace('_', ' ')}
        clear_pending(a.get('name', '')) if NAME_RE.match(str(a.get('name', ''))) else None
        return {'ok': delete(a.get('name', '')), 'say': 'Deleted. It can still be brought back with a rollback.'}
    if name.startswith('skill_'):
        try:
            return run(name[len('skill_'):], a)
        except ValueError as e:
            return {'error': str(e)}
    return None


def stamp():
    """Newest change to the skills folder (so a session knows its tool list is out of date)."""
    try:
        return max([os.path.getmtime(SKILLS_DIR)] + [os.path.getmtime(os.path.join(SKILLS_DIR, f)) for f in os.listdir(SKILLS_DIR)])
    except OSError:
        return time.time()
