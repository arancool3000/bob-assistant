"""The skill host (service bob-assistant-skills): where skills actually run.

It is one long-running process, as its own user `bob-skill` (in the gpio, i2c, spi and dialout groups, and
nothing else). That gives two things a short-lived process could not:

  * state that lasts -- a skill's objects (a gpiozero LED, an open serial port) stay alive between calls, so an
    output you switch on stays on;
  * isolation -- bob-skill cannot read Bob's settings or API key, change Bob's files, or stop Bob.

Bob talks to it over a Unix socket with one JSON line per request:
    {"cmd": "run", "name": "desk_led", "args": {"on": true}}   ->  the skill's result dict
    {"cmd": "reload"}                                           ->  restarts the host (new or changed skills)

A call that takes longer than 30 s is answered with an error (the stuck call is left behind; the host keeps
serving others). When the skills change the host restarts itself, so every skill is loaded fresh.
"""
import importlib.util
import json
import os
import socket
import sys
import threading
import traceback

SOCK = os.environ.get('BOB_SKILL_SOCK', '/run/bob-assistant-skills/skills.sock')
TIMEOUT_S = 30
_modules = {}
_lock = threading.Lock()


def _restart():
    """Start clean: under systemd the process exits and is restarted at once; in tests the cache is cleared."""
    if os.environ.get('BOB_HOST_INPROC'):
        _modules.clear()
    else:
        os._exit(0)


def _load(path):
    name = os.path.basename(path)[:-3]
    mt = os.path.getmtime(path)
    with _lock:
        hit = _modules.get(name)
        if hit and hit[0] == mt:
            return hit[1]
        if hit:                                   # changed since it was loaded: start clean
            _restart()
        spec = importlib.util.spec_from_file_location('skill_' + name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _modules[name] = (mt, mod)
        return mod


def run_skill(path, args):
    """Run one skill in this process, with a time limit. Always returns a dict."""
    box = {}

    def go():
        try:
            mod = _load(path)
            res = mod.run(**(args or {}))
            box['r'] = res if isinstance(res, dict) else {'result': res, 'say': str(res)[:200]}
        except TypeError as e:
            box['r'] = {'error': 'wrong arguments for this skill: %s' % e}
        except Exception as e:
            tb = traceback.extract_tb(sys.exc_info()[2])
            box['r'] = {'error': '%s: %s (line %s)' % (type(e).__name__, e, tb[-1].lineno if tb else '?')}
    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(TIMEOUT_S)
    if t.is_alive():
        return {'error': 'the skill took longer than %d seconds' % TIMEOUT_S}
    return box.get('r', {'error': 'the skill returned nothing'})


def _serve_one(conn, skills_dir):
    with conn:
        conn.settimeout(TIMEOUT_S + 5)
        buf = b''
        while not buf.endswith(b'\n'):
            part = conn.recv(65536)
            if not part:
                return
            buf += part
            if len(buf) > 1_000_000:
                return
        try:
            req = json.loads(buf)
        except ValueError:
            conn.sendall(b'{"error": "bad request"}\n')
            return
        if req.get('cmd') == 'reload':
            conn.sendall(b'{"ok": true}\n')
            _restart()
            return
        name = str(req.get('name', ''))
        path = os.path.join(skills_dir, name + '.py')
        if not name.replace('_', '').isalnum() or not name[:1].isalpha() or not os.path.isfile(path):
            res = {'error': 'there is no skill called %s' % name}
        else:
            res = run_skill(path, req.get('args') if isinstance(req.get('args'), dict) else {})
        conn.sendall((json.dumps(res, default=str) + '\n').encode())


def serve(skills_dir, sock_path=SOCK, ready=None):
    try:
        os.unlink(sock_path)
    except OSError:
        pass
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(sock_path)
    os.chmod(sock_path, 0o660)                    # bob-skill and members of its group (Bob) only
    s.listen(16)
    if ready:
        ready.set()
    while True:
        conn, _ = s.accept()
        threading.Thread(target=_serve_one, args=(conn, skills_dir), daemon=True).start()


def call(req, sock_path=SOCK, timeout=TIMEOUT_S + 10):
    """From Bob: send one request to the host and return its answer."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(sock_path)
        s.sendall((json.dumps(req) + '\n').encode())
        buf = b''
        while not buf.endswith(b'\n'):
            part = s.recv(65536)
            if not part:
                break
            buf += part
    finally:
        s.close()
    return json.loads(buf) if buf.strip() else {'error': 'the skill host gave no answer'}


if __name__ == '__main__':
    serve(os.environ.get('BOB_SKILLS', '/var/lib/bob-assistant-skills'))
