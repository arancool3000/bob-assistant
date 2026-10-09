"""The settings page (service bob-assistant-web): http://<pi-name>.local:8080 on your home network.

Status, the Gemini key, wake phrase and sensitivity, voice, town, volume, the skills Bob has made (read or
delete them), updates. Signed in with the password chosen at setup; nothing here works from outside your
network unless you forward the port yourself (don't).
"""
import html
import json
import os
import secrets
import subprocess
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import audio, config, helper, skills, tools, updater, wake

SESSIONS = {}
SESSION_HOURS = 24 * 30
FAILS = {}                              # client address -> recent wrong passwords: at most 10 per 10 minutes each
SETUP_CODE_FILE = os.path.join(config.ETC, 'setup-code')
VOSK_MODEL = os.path.join(config.STATE, 'vosk-model')
VOICES = ['Charon', 'Puck', 'Kore', 'Fenrir', 'Aoede', 'Leda', 'Orus', 'Zephyr']

CSS = """
:root{color-scheme:dark;--bg:#0b0b10;--card:rgba(255,255,255,.06);--line:rgba(255,255,255,.09);--txt:#f5f5f7;--dim:rgba(235,235,245,.6);
 --acc:#8a86ff;--brand:linear-gradient(135deg,#2f8cff,#5e5ce6 52%,#a65cf0)}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(120% 60% at 10% -10%,rgba(94,92,230,.35),transparent 60%),var(--bg);
 color:var(--txt);font:16px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;min-height:100vh}
main{max-width:640px;margin:auto;padding:24px 16px 60px}h1{font-size:30px;margin:6px 0 2px}.sub{color:var(--dim);margin:0 0 18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:16px;margin:14px 0}
.card h2{font-size:15px;margin:0 0 10px;color:var(--dim);font-weight:600}
label{display:block;font-size:13px;color:var(--dim);margin:10px 0 4px}
input,select{width:100%;padding:11px 12px;border-radius:12px;border:1px solid var(--line);background:rgba(0,0,0,.25);color:var(--txt);font:inherit}
button,.btn{display:inline-block;border:0;border-radius:12px;padding:11px 16px;font:inherit;font-weight:600;color:#fff;background:var(--brand);cursor:pointer;text-decoration:none}
button.plain{background:rgba(255,255,255,.1)}button.bad{background:rgba(255,69,58,.25);color:#ff8a80}
.row{display:flex;gap:10px;align-items:center;justify-content:space-between;padding:9px 0;border-top:1px solid var(--line)}.row:first-of-type{border-top:0}
.pill{font-size:13px;padding:4px 10px;border-radius:999px;background:rgba(255,255,255,.1)}.ok{color:#30d158}.warn{color:#ffd60a}
pre{white-space:pre-wrap;background:rgba(0,0,0,.35);padding:12px;border-radius:12px;font-size:12.5px;overflow:auto;max-height:50vh}
.face{width:64px;height:64px;border-radius:22%;background:var(--brand);display:inline-grid;grid-auto-flow:column;gap:12px;place-content:center;vertical-align:middle;margin-right:12px}
.face i{width:8px;height:18px;border-radius:9px;background:#fff;display:block}
"""


def page(title, body):
    return ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>%s</title><style>%s</style><main>%s</main>' % (html.escape(title), CSS, body))


def esc(x):
    return html.escape(str(x if x is not None else ''))


def bob_state():
    try:
        with open(os.path.join(config.STATE, 'state.json')) as f:
            d = json.load(f)
        return d.get('state', '?'), d.get('phrase')
    except (OSError, ValueError):
        return 'starting', None


def _allowed_hosts():
    import socket
    names = {'localhost', '127.0.0.1', socket.gethostname().lower(), socket.gethostname().lower() + '.local'}
    try:
        names.update(subprocess.run(['hostname', '-I'], capture_output=True, text=True, timeout=3).stdout.split())
    except Exception:
        pass
    return names


def _num(v, lo, hi, default):
    try:
        x = float(v)
        if x != x or x in (float('inf'), float('-inf')):
            return default
        return int(max(lo, min(hi, x)))
    except (TypeError, ValueError):
        return default


def service_enabled(name):
    r = subprocess.run(['systemctl', 'is-enabled', name], capture_output=True, text=True)
    return r.stdout.strip() == 'enabled'


def helper_card():
    """The "Help run KindleHub" switch (bob/helper.py). Off unless the owner turns it on."""
    on = service_enabled('bob-assistant-helper')
    st = helper.read_status() if on else {}
    what = ('<p class="sub">Lend this Pi\'s spare time to KindleHub (free games for e-readers): it plays chess moves '
            'for KindleHub\'s computer opponent, and nothing else. One move at a time, at most half of one CPU core, '
            'lowest priority, so Bob always comes first. It connects out and opens no ports, and runs as its own user '
            'with no access to Bob\'s key or your files.</p>'
            '<p class="sub">Perk: link this Pi to your KindleHub account. While it has played moves for KindleHub in '
            '10 different hours of the last week, your account gets KindleTube and KindlePoki as on the Plus plan.</p>')
    if not on:
        return ('<form method="post" action="/helper" class="card"><h2>Help run KindleHub</h2>%s'
                '<div class="row"><span>Status</span><span class="pill">off</span></div>'
                '<input type="hidden" name="on" value="1"><p><button class="plain">Switch on</button></p></form>') % what
    if st.get('connected'):
        state = '<span class="pill ok">helping</span>'
    elif st.get('problem'):
        state = '<span class="pill warn">%s</span>' % esc(st['problem'])
    else:
        state = '<span class="pill warn">connecting</span>'
    link = ''
    if st.get('link_code') and not st.get('linked'):
        link = ('<div class="row"><span>Link code</span><b>%s</b></div><p class="sub">For the perks: in KindleHub open '
                'Settings, then Helper Pi, and enter this code.</p>') % esc(st['link_code'])
    elif st.get('linked'):
        link = '<div class="row"><span>KindleHub account</span><span class="pill ok">linked</span></div>'
    return ('<form method="post" action="/helper" class="card"><h2>Help run KindleHub</h2>%s'
            '<div class="row"><span>Status</span>%s</div>%s'
            '<input type="hidden" name="on" value="0"><p><button class="plain">Switch off</button></p></form>') % (what, state, link)


def service_active(name):
    r = subprocess.run(['systemctl', 'is-active', name], capture_output=True, text=True)
    return r.stdout.strip()


class Handler(BaseHTTPRequestHandler):
    server_version = 'bob'

    def log_message(self, *a):
        pass

    # -- helpers --
    def _send(self, code, body, ctype='text/html; charset=utf-8', headers=None):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, to, headers=None):
        h = {'Location': to}
        h.update(headers or {})
        self._send(303, '', headers=h)

    def _cookie(self):
        for part in (self.headers.get('Cookie') or '').split(';'):
            k, _, v = part.strip().partition('=')
            if k == 'bob':
                return v
        return ''

    def _signed_in(self):
        exp = SESSIONS.get(self._cookie())
        return bool(exp and exp > time.time())

    def _form(self):
        try:
            n = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            return {}
        if n <= 0 or n > 200000:
            return {}
        try:
            return {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(n).decode('utf-8', 'replace')).items()}
        except Exception:
            return {}

    def _host_ok(self):
        """Only answer to the Pi's own names and addresses (stops a web page using DNS tricks to reach us)."""
        host = (self.headers.get('Host') or '').rsplit(':', 1)[0].strip('[]').lower()
        return host in _allowed_hosts()

    def _same_origin(self):
        o = self.headers.get('Origin')
        return not o or o.split('//', 1)[-1] == self.headers.get('Host')

    # -- pages --
    def do_GET(self):
        if not self._host_ok():
            return self._send(421, 'unknown host')
        path = self.path.split('?')[0]
        sec = config.load_secrets()
        if not sec.get('web_password'):
            return self._send(200, self.first_run())
        if path == '/login' or not self._signed_in():
            return self._send(200, self.login())
        if path == '/skill':
            name = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get('name', [''])[0]
            src = skills.source(name)
            return self._send(200, page(name, '<p><a class="btn plain" href="/">Back</a></p><h1>%s</h1><pre>%s</pre>' % (
                esc(name), esc(src or 'not found'))))
        if path == '/log':
            try:
                with open(updater.LOG) as f:
                    tail = f.read()[-6000:]
            except OSError:
                tail = 'No updates yet.'
            return self._send(200, page('Updates', '<p><a class="btn plain" href="/">Back</a></p><h1>Update log</h1><pre>%s</pre>' % esc(tail)))
        return self._send(200, self.home())

    def do_POST(self):
        if not self._host_ok() or not self._same_origin():
            return self._send(403, 'not from this page')
        try:
            return self._post()
        except Exception as e:
            return self._send(400, 'that did not work: %s' % esc(str(e)[:120]))

    def _post(self):
        path = self.path.split('?')[0]
        f = self._form()
        sec = config.load_secrets()
        if path == '/first-run' and not sec.get('web_password'):
            try:
                with open(SETUP_CODE_FILE) as cf:
                    code = cf.read().strip()
            except OSError:
                code = ''
            if not code or not secrets.compare_digest(f.get('code', '').strip().upper(), code.upper()):
                time.sleep(1)
                return self._send(200, self.first_run('That setup code is not right. It was shown at the end of the install '
                                                      '(and is in /etc/bob/setup-code on the Pi).'))
            if len(f.get('password', '')) < 6:
                return self._send(200, self.first_run('Use at least 6 characters.'))
            sec['web_password'] = config.hash_password(f['password'])
            if f.get('api_key', '').strip():
                sec['gemini_api_key'] = f['api_key'].strip()
            config.save_secrets({'gemini_api_key': sec.get('gemini_api_key', ''), 'web_password': sec['web_password']})
            try:
                os.remove(SETUP_CODE_FILE)
            except OSError:
                pass
            return self._redirect('/login')
        if path == '/login':
            now = time.time()
            who = self.client_address[0] if self.client_address else '?'
            for k in list(FAILS):
                FAILS[k] = [t for t in FAILS[k] if now - t < 600]
                if not FAILS[k]:
                    del FAILS[k]
            if len(FAILS.get(who, [])) >= 10 or sum(len(v) for v in FAILS.values()) >= 100:
                return self._send(429, self.login('Too many tries: wait ten minutes.'))
            for k in [k for k, exp in SESSIONS.items() if exp < now]:
                del SESSIONS[k]
            if config.check_password(f.get('password', ''), sec.get('web_password', '')):
                tok = secrets.token_urlsafe(24)
                SESSIONS[tok] = time.time() + SESSION_HOURS * 3600
                return self._redirect('/', {'Set-Cookie': 'bob=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=%d' % (tok, SESSION_HOURS * 3600)})
            FAILS.setdefault(who, []).append(now)
            time.sleep(1)
            return self._send(200, self.login('Wrong password.'))
        if not self._signed_in():
            return self._redirect('/login')
        if path == '/settings':
            cfg = config.load()
            for k in ('name', 'personality', 'language'):
                if k in f:
                    cfg[k] = f[k].strip()[:300]
            if f.get('voice') in VOICES:
                cfg['voice'] = f['voice']
            for k in ('mic_device', 'speaker_device'):
                if k in f:
                    cfg[k] = audio.safe_device(f[k].strip())
            if f.get('wake_phrase', '').strip():
                phrase = ' '.join(f['wake_phrase'].lower().replace(',', ' ').split())
                why = wake.check_phrase(phrase, VOSK_MODEL)
                if why:
                    return self._send(200, page('Bob', '<p class="warn">Wake phrase not changed: %s.</p><p><a class="btn" href="/">Back</a></p>' % esc(why)))
                cfg['wake_phrase'] = phrase
            cfg['wake_sensitivity'] = _num(f.get('wake_sensitivity'), 20, 90, cfg['wake_sensitivity'])
            cfg['volume'] = _num(f.get('volume'), 0, 100, cfg['volume'])
            cfg['idle_seconds'] = _num(f.get('idle_seconds'), 5, 60, cfg['idle_seconds'])
            cfg['auto_update'] = f.get('auto_update') == 'on'
            if f.get('town', '').strip() != cfg.get('town'):
                cfg['town'] = f.get('town', '').strip()[:100]
                cfg.update(latitude=None, longitude=None)
                if cfg['town']:
                    try:
                        lat, lon, tz, _label = tools.geocode(cfg['town'])
                        cfg.update(latitude=lat, longitude=lon, timezone=tz)
                    except Exception:
                        pass
            config.save(cfg)
            if f.get('api_key', '').strip():
                sec['gemini_api_key'] = f['api_key'].strip()
                config.save_secrets(sec)
            subprocess.run(['sudo', '-n', '/usr/bin/systemctl', 'restart', 'bob-assistant'], capture_output=True)
            return self._redirect('/?saved=1')
        if path == '/helper':                          # "Help run KindleHub": off by default, on only when asked
            verb = 'enable' if f.get('on') == '1' else 'disable'
            subprocess.run(['sudo', '-n', '/usr/bin/systemctl', verb, '--now', 'bob-assistant-helper.service'],
                           capture_output=True, timeout=60)
            return self._redirect('/')
        if path == '/delete-skill':
            skills.delete(f.get('name', ''))
            return self._redirect('/')
        if path == '/update':
            subprocess.Popen(['sudo', '-n', '/usr/bin/systemctl', 'start', '--no-block', 'bob-assistant-update-now.service'])
            return self._redirect('/log')
        if path == '/password':
            if not config.check_password(f.get('current', ''), sec.get('web_password', '')):
                return self._send(200, page('Bob', '<p class="warn">Current password is not right.</p><p><a class="btn" href="/">Back</a></p>'))
            if len(f.get('password', '')) >= 6:
                sec['web_password'] = config.hash_password(f['password'])
                config.save_secrets(sec)
                SESSIONS.clear()
            return self._redirect('/login')
        return self._send(404, 'not found')

    # -- views --
    def first_run(self, err=''):
        return page('Set up Bob', '<h1><span class="face"><i></i><i></i></span>Set up Bob</h1><p class="sub">Choose a password for this page%s.</p>'
                    '<form method="post" action="/first-run" class="card"><label>Setup code (shown at the end of the install)</label><input name="code" required autocomplete="off">'
                    '<label>Password for this page</label><input type="password" name="password" required minlength="6">'
                    '<label>Gemini API key (from aistudio.google.com/apikey)</label><input name="api_key" autocomplete="off">'
                    '<p class="warn">%s</p><button>Save</button></form>' % (', and add your Gemini key if setup did not', esc(err)))

    def login(self, err=''):
        return page('Bob', '<h1><span class="face"><i></i><i></i></span>Bob</h1><form method="post" action="/login" class="card">'
                    '<label>Password</label><input type="password" name="password" autofocus><p class="warn">%s</p>'
                    '<button>Sign in</button></form>' % esc(err))

    def home(self):
        cfg, sec = config.load(), config.load_secrets()
        pi_st = {}
        try:
            pi_st = tools.pi_status()
        except Exception:
            pass
        st, active_phrase = bob_state()
        sk, bad = skills.load_all()
        state_txt = {'asleep': 'Listening for "%s"' % (active_phrase or cfg['wake_phrase']), 'listening': 'In a conversation',
                     'speaking': 'Speaking', 'working': 'Using a tool', 'needs_setup': 'Needs your Gemini key'}.get(st, st)
        voice_opts = ''.join('<option%s>%s</option>' % (' selected' if v == cfg['voice'] else '', v) for v in VOICES)
        skills_html = ''.join('<div class="row"><span><b>%s</b><br><span class="sub">%s</span></span><span><a class="btn plain" href="/skill?name=%s">Code</a> '
                              '<form method="post" action="/delete-skill" style="display:inline" onsubmit="return confirm(\'Delete %s?\')">'
                              '<input type="hidden" name="name" value="%s"><button class="bad">Delete</button></form></span></div>'
                              % (esc(s['name']), esc(s['description']), esc(s['name']), esc(s['name']), esc(s['name'])) for s in sk) \
            or '<p class="sub">None yet. Wire something to the Pi and say: "Hey Bob, make a skill for it."</p>'
        if bad:
            skills_html += ''.join('<p class="warn">%s is broken: %s</p>' % (esc(b['name']), esc(b['problem'])) for b in bad)
        return page('Bob', (
            '<h1><span class="face"><i></i><i></i></span>%s</h1><p class="sub">%s · version %s</p>'
            '<div class="card"><h2>Status</h2>'
            '<div class="row"><span>Bob</span><span class="pill %s">%s</span></div>'
            '<div class="row"><span>Gemini key</span><span class="pill %s">%s</span></div>'
            '<div class="row"><span>Pi</span><span class="sub">%s°C · %s GB free · %s</span></div></div>'
            '<form method="post" action="/settings" class="card"><h2>Settings</h2>'
            '<label>Name</label><input name="name" value="%s">'
            '<label>Wake phrase</label><input name="wake_phrase" value="%s">'
            '<label>Wake sensitivity (higher wakes more easily) · %s%%</label><input type="range" name="wake_sensitivity" min="20" max="90" value="%s">'
            '<label>Voice</label><select name="voice">%s</select>'
            '<label>Personality</label><input name="personality" value="%s">'
            '<label>Town (for the weather)</label><input name="town" value="%s">'
            '<label>Volume</label><input type="range" name="volume" min="0" max="100" value="%s">'
            '<label>Seconds Bob waits for you before going back to sleep</label><input type="number" name="idle_seconds" min="5" max="60" value="%s">'
            '<label>Language (e.g. en-GB, en-US)</label><input name="language" value="%s">'
            '<label>Microphone device (from arecord -L; "default" is the USB device the installer chose)</label><input name="mic_device" value="%s">'
            '<label>Speaker device (from aplay -L)</label><input name="speaker_device" value="%s">'
            '<label>New Gemini API key (leave empty to keep the current one)</label><input name="api_key" autocomplete="off">'
            '<label><input type="checkbox" name="auto_update" style="width:auto" %s> Install updates automatically</label>'
            '<p><button>Save</button></p></form>'
            '<div class="card"><h2>Skills Bob has made</h2>%s</div>'
            '%s'
            '<div class="card"><h2>Updates</h2><form method="post" action="/update" style="display:inline"><button class="plain">Check now</button></form> '
            '<a class="btn plain" href="/log">Update log</a></div>'
            '<form method="post" action="/password" class="card"><h2>Change this page\'s password</h2><label>Current password</label><input type="password" name="current">'
            '<label>New password</label><input type="password" name="password" minlength="6">'
            '<p><button class="plain">Change</button></p></form>') % (
                esc(cfg['name']), esc(pi_st.get('ip') or ''), esc(pi_st.get('version', '?')),
                'ok' if st in ('asleep', 'listening', 'speaking', 'working') else 'warn', esc(state_txt),
                'ok' if sec.get('gemini_api_key') else 'warn', 'set' if sec.get('gemini_api_key') else 'missing',
                esc(pi_st.get('cpu_temp_c', '?')), esc(pi_st.get('disk_free_gb', '?')), esc(service_active('bob-assistant')),
                esc(cfg['name']), esc(cfg['wake_phrase']), esc(cfg['wake_sensitivity']), esc(cfg['wake_sensitivity']), voice_opts,
                esc(cfg['personality']), esc(cfg['town']), esc(cfg['volume']), esc(cfg['idle_seconds']), esc(cfg['language']),
                esc(cfg['mic_device']), esc(cfg['speaker_device']), 'checked' if cfg.get('auto_update') else '',
                skills_html, helper_card()))


def main():
    cfg = config.load()
    srv = ThreadingHTTPServer(('0.0.0.0', int(cfg.get('web_port', 8080))), Handler)
    srv.daemon_threads = True
    srv.serve_forever()


if __name__ == '__main__':
    main()
