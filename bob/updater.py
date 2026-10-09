"""Automatic updates (service bob-assistant-update, run daily by its timer; "Check now" on the settings page runs
bob-assistant-update-now, which is the same with --force).

Only tagged releases (v1.2.3) are installed, never work in progress. The steps:
  1. fetch the tags from the project's repository; a tag that was moved to different code is refused
  2. if a newer release exists (and has not failed here before): check it out and run `install.sh --upgrade`,
     which brings the services, permissions, packages and Python requirements in line with that release
  3. restart Bob and check he stays up
  4. if he does not, go back to the version that worked, upgrade back, restart, and remember the bad release

Its own files live in /var/lib/bob-assistant-update (root only), so nothing Bob or a skill can write is ever
written to by root.

Run by hand:  sudo /opt/bob-assistant/venv/bin/python -m bob.updater [--force]
"""
import json
import os
import re
import subprocess
import sys
import time

HOME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPD = os.environ.get('BOB_UPDATE_STATE', '/var/lib/bob-assistant-update')
LOG = os.path.join(UPD, 'update.log')
FAILED = os.path.join(UPD, 'failed.json')
NOTICE = os.path.join(UPD, 'updated.json')          # readable by Bob: "you were just updated to ..."
SERVICES = ('bob-assistant-skills', 'bob-assistant', 'bob-assistant-web')
TAG_RE = re.compile(r'^v(\d+)\.(\d+)\.(\d+)$')


def _ensure_dir():
    os.makedirs(UPD, mode=0o755, exist_ok=True)
    st = os.lstat(UPD)
    if os.path.islink(UPD) or (os.geteuid() == 0 and st.st_uid != 0):
        raise RuntimeError('%s must be a root-owned directory' % UPD)


def log(msg):
    line = '%s %s' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
    print(line, flush=True)
    try:
        _ensure_dir()
        fd = os.open(LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o644)
        with os.fdopen(fd, 'a') as f:
            f.write(line + '\n')
    except (OSError, RuntimeError):
        pass


def _write_json(path, data):
    _ensure_dir()
    tmp = path + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0), 0o644)
    with os.fdopen(fd, 'w') as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def git(*args, check=True):
    r = subprocess.run(['git', '-C', HOME] + list(args), capture_output=True, text=True, timeout=180)
    if check and r.returncode:
        raise RuntimeError('git %s: %s' % (' '.join(args), (r.stderr or r.stdout).strip()[-300:]))
    return r.stdout.strip()


def version_key(tag):
    m = TAG_RE.match(tag or '')
    return tuple(int(x) for x in m.groups()) if m else None


def newest_tag(tags, skip=()):
    good = [t for t in tags if version_key(t) and t not in skip]
    return max(good, key=version_key) if good else None


def current_tag():
    """The release tag at HEAD, if any."""
    tags = git('tag', '--points-at', 'HEAD', check=False).split()
    good = [t for t in tags if version_key(t)]
    return max(good, key=version_key) if good else None


def _upgrade():
    """Bring services, sudoers, packages and requirements in line with the checked-out release."""
    subprocess.run(['bash', os.path.join(HOME, 'install.sh'), '--upgrade'], check=True, timeout=1800,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def _restart():
    for s in SERVICES:
        subprocess.run(['systemctl', 'restart', s], capture_output=True, timeout=60)
    # Only if the owner switched it on: try-restart never starts a stopped service.
    subprocess.run(['systemctl', 'try-restart', 'bob-assistant-helper'], capture_output=True, timeout=60)


def _healthy(wait=40):
    end = time.time() + wait
    while time.time() < end:
        time.sleep(3)
        r = subprocess.run(['systemctl', 'is-active', 'bob-assistant'], capture_output=True, text=True)
        if r.stdout.strip() == 'active':
            time.sleep(10)                                    # and still up a moment later
            r = subprocess.run(['systemctl', 'is-active', 'bob-assistant'], capture_output=True, text=True)
            return r.stdout.strip() == 'active'
    return False


def check(force=False):
    if '--force' not in sys.argv and not force:
        try:
            with open('/etc/bob/config.json') as f:
                if json.load(f).get('auto_update') is False:
                    log('automatic updates are off')
                    return {'updated': False, 'reason': 'off'}
        except (OSError, ValueError):
            pass
    known = {t: git('rev-list', '-n1', t, check=False) for t in git('tag', '--list', 'v*', check=False).split()}
    r = subprocess.run(['git', '-C', HOME, 'fetch', '--tags', '--quiet', 'origin'], capture_output=True, text=True, timeout=180)
    if r.returncode and 'would clobber existing tag' in (r.stderr or ''):
        log('a release tag was moved to different code upstream: refusing it (%s)' % r.stderr.strip()[-200:])
    elif r.returncode:
        raise RuntimeError('could not reach the repository: %s' % (r.stderr or '').strip()[-200:])
    for t, sha in known.items():                      # belt and braces: a tag must not change under us
        if sha and git('rev-list', '-n1', t, check=False) != sha:
            raise RuntimeError('release %s changed upstream: refusing to update' % t)
    failed = set(_read_json(FAILED, []))
    tag = newest_tag(git('tag', '--list', 'v*').split(), skip=failed)
    head = git('rev-parse', 'HEAD')
    if not tag or git('rev-list', '-n1', tag) == head:
        log('up to date (%s)' % (current_tag() or head[:8]))
        return {'updated': False, 'version': current_tag()}
    have = current_tag()
    if have and version_key(tag) <= version_key(have):
        log('up to date (%s)' % have)
        return {'updated': False, 'version': have}
    log('updating %s -> %s' % (have or head[:8], tag))
    try:
        git('checkout', '--quiet', '--force', tag)
        _upgrade()
        _restart()
        if not _healthy():
            raise RuntimeError('Bob did not start on %s' % tag)
    except Exception as e:
        log('update failed (%s): going back' % str(e)[:200])
        git('checkout', '--quiet', '--force', head, check=False)
        try:
            _upgrade()
        except Exception:
            pass
        _restart()
        failed.add(tag)
        _write_json(FAILED, sorted(failed))
        return {'updated': False, 'error': str(e)[:200]}
    _write_json(NOTICE, {'version': tag.lstrip('v'), 'at': time.time()})
    log('now on %s' % tag)
    return {'updated': True, 'version': tag}


if __name__ == '__main__':
    import fcntl
    try:
        _ensure_dir()
        lock = open(os.path.join(UPD, 'lock'), 'w')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)        # the timer and "Check now" never overlap
    except BlockingIOError:
        log('another update check is running')
        sys.exit(0)
    try:
        print(json.dumps(check(force='--force' in sys.argv)))
    except Exception as e:
        log('update check failed: %s' % e)
        sys.exit(1)
