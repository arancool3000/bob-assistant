"""Skills: abilities Bob writes for himself when you ask by voice.

"Hey Bob, I've wired an LED to GPIO 17 -- make a skill that turns it on and off." Bob writes a small Python file,
reads back what it will do, and only installs it after you say yes. Each skill is one file in
/var/lib/bob-assistant/skills/ that looks like this:

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
skill's objects stay alive between calls (an LED you switch on stays on) and a skill can never read Bob's API key,
change his files or stop him. Installing or deleting a skill needs a yes from you: the tool refuses
confirmed=true unless that exact code was offered for confirmation first. New skills can be used straight away
through run_skill, and appear as tools of their own from the next conversation.
"""
import ast
import hashlib
import json
import os
import re
import time

from . import skillhost

SKILLS_DIR = os.environ.get('BOB_SKILLS', '/var/lib/bob-assistant-skills')
NAME_RE = re.compile(r'^[a-z][a-z0-9_]{1,40}$')
MAX_BYTES = 20000
RESERVED = {'create_skill', 'run_skill', 'list_skills', 'show_skill', 'delete_skill', 'test_skill'}


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
    """Return the SKILL dict, or raise ValueError with a reason Bob can read out and fix."""
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


def save(name, code):
    os.makedirs(SKILLS_DIR, exist_ok=True)
    tmp = _path(name) + '.tmp'
    with open(tmp, 'w') as f:
        f.write(code)
    os.replace(tmp, _path(name))


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
        except Exception as e:                    # one broken file never stops Bob
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
    for _ in range(40):                           # wait for it to come back (up to ~8 s)
        time.sleep(0.2)
        try:
            if os.path.exists(skillhost.SOCK):
                return True
        except OSError:
            pass
    return False


def delete(name):
    try:
        os.remove(_path(name))
        reload_host()
        return True
    except (OSError, ValueError):
        return False


def source(name):
    try:
        with open(_path(name)) as f:
            return f.read()
    except (OSError, ValueError):
        return None


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


# ---- the tools Bob uses to build skills -------------------------------------------------------------

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
    if name == 'create_skill':
        try:
            skill = validate(a.get('name', ''), a.get('code', ''))
        except ValueError as e:
            return {'error': str(e), 'fix': 'correct the code and call create_skill again'}
        props = ', '.join(skill['parameters'].get('properties', {}).keys()) or 'no settings'
        if not a.get('confirmed') or not was_offered('create', skill['name'], a.get('code', '')):
            offer('create', skill['name'], a.get('code', ''))
            exists = os.path.exists(_path(skill['name']))
            return {'needs_confirmation': True,
                    'say': '%s a skill called %s: %s It takes %s. Shall I install it?' % (
                        'I will replace' if exists else 'I can add', skill['name'].replace('_', ' '),
                        skill['description'].rstrip('.') + '.', props)}
        save(skill['name'], a['code'])
        reload_host()
        return {'ok': True, 'installed': skill['name'], 'next': 'try it with test_skill',
                'say': 'Installed %s.' % skill['name'].replace('_', ' ')}
    if name in ('test_skill', 'run_skill'):
        try:
            return run(a.get('name', ''), _args(a.get('args_json')))
        except (ValueError, TypeError) as e:
            return {'error': str(e)}
    if name == 'list_skills':
        skills, bad = load_all()
        return {'skills': [{'name': s['name'], 'does': s['description']} for s in skills], 'broken': bad,
                'say': ('No skills yet.' if not skills else 'I have %d: %s.' % (len(skills), ', '.join(s['name'].replace('_', ' ') for s in skills)))}
    if name == 'show_skill':
        src = source(a.get('name', ''))
        return {'code': src} if src is not None else {'error': 'no skill called %s' % a.get('name')}
    if name == 'delete_skill':
        if not a.get('confirmed') or not was_offered('delete', a.get('name', '')):
            offer('delete', a.get('name', ''))
            return {'needs_confirmation': True, 'say': 'Delete the %s skill?' % str(a.get('name', '')).replace('_', ' ')}
        return {'ok': delete(a.get('name', '')), 'say': 'Deleted.'}
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
