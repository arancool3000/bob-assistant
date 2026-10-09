"""Help run KindleHub (service bob-assistant-helper). OFF unless the owner switches it on.

When it is on, this Pi lends a little of its spare time to KindleHub (kindlehub.pro, free games for e-readers):
it plays chess moves for KindleHub's computer opponent. That is the only job it ever does:

  - a chess position (FEN) comes in, one move goes out, worked out by Stockfish on this Pi
  - nothing else is accepted: no code, no web addresses, no files. A position that is not a well-formed FEN,
    or any other message, is ignored
  - the Pi connects OUT to KindleHub over one WebSocket: no port is opened and nothing on your network is reachable
  - one move at a time, at most 1.5 seconds each, one CPU core, lowest priority, capped at half a core by
    systemd (bob-assistant-helper.service) - Bob always comes first
  - it runs as its own system user (bob-helper) with no access to Bob's settings, key, skills or your files

Perks: link the Pi to your KindleHub account (the code is on Bob's settings page; enter it in KindleHub under
Settings > Helper Pi). While the Pi has played moves for KindleHub in 10 different hours of the last week, your
account gets KindleTube and KindlePoki as on the Plus plan. Only hours with moves count; being switched on is not
enough, and several Pis do not add up.

Switch it off on the settings page (or `sudo systemctl disable --now bob-assistant-helper`): it stops at once.
"""
import asyncio
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time

URL = os.environ.get('BOB_HELPER_URL', 'wss://kindlehub-helpers.arancool3000.workers.dev/connect')
STATE = os.environ.get('STATE_DIRECTORY', os.environ.get('BOB_HELPER_STATE', '/var/lib/bob-assistant-helper'))
IDENTITY = os.path.join(STATE, 'identity.json')       # 0600: this Pi's helper id and secret
STATUS = os.path.join(STATE, 'status.json')           # 0644: what the settings page shows
FEN_RE = re.compile(r'^[pnbrqkPNBRQK1-8]{1,8}(/[pnbrqkPNBRQK1-8]{1,8}){7} [wb] (-|[KQkq]{1,4}) (-|[a-h][36]) \d{1,3} \d{1,4}\Z')
MOVE_RE = re.compile(r'^[a-h][1-8][a-h][1-8][qrbn]?$')
MS_MIN, MS_MAX = 80, 1500
ELO_MIN, ELO_MAX = 1320, 3190
VERSION = '1'


def _version():
    try:
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'VERSION')) as f:
            return 'bob-' + f.read().strip()
    except OSError:
        return 'bob'


def identity():
    """This Pi's helper id and secret, made once and kept (only this service can read them)."""
    try:
        with open(IDENTITY) as f:
            d = json.load(f)
        if re.match(r'^[0-9a-f]{16}$', d.get('id', '')) and re.match(r'^[0-9a-f]{64}$', d.get('secret', '')):
            return d
    except (OSError, ValueError):
        pass
    d = {'id': secrets.token_hex(8), 'secret': secrets.token_hex(32)}
    os.makedirs(STATE, exist_ok=True)
    fd = os.open(IDENTITY + '.tmp', os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(d, f)
    os.replace(IDENTITY + '.tmp', IDENTITY)
    return d


def write_status(**kw):
    st = {}
    try:
        with open(STATUS) as f:
            st = json.load(f)
    except (OSError, ValueError):
        pass
    st.update(kw, updated=time.time())
    tmp = STATUS + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(st, f)
    os.chmod(tmp, 0o644)
    os.replace(tmp, STATUS)


def read_status():
    """For the settings page: the status file, if the helper has ever run."""
    try:
        with open(STATUS) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def check_job(msg):
    """The one job there is, checked and clamped here, whatever the server sent. None if it is not that job."""
    if not isinstance(msg, dict) or msg.get('t') != 'job':
        return None
    fen = str(msg.get('fen', '')).strip()
    j = str(msg.get('j', ''))
    if not FEN_RE.match(fen) or not re.match(r'^[0-9a-f-]{8,40}$', j):
        return None
    try:
        ms = max(MS_MIN, min(MS_MAX, int(msg.get('ms') or 800)))
    except (TypeError, ValueError):
        ms = 800
    elo = msg.get('elo')
    try:
        elo = max(ELO_MIN, min(ELO_MAX, int(elo))) if elo is not None else None
    except (TypeError, ValueError):
        elo = None
    return {'j': j, 'fen': fen, 'ms': ms, 'elo': elo}


class Engine:
    """One Stockfish process, kept running, spoken to over UCI."""

    def __init__(self, cmd=None):
        self.cmd = cmd or [shutil.which('stockfish') or '/usr/games/stockfish']
        self.p = None

    async def start(self):
        self.p = await asyncio.create_subprocess_exec(*self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                      stderr=subprocess.DEVNULL)
        await self._send('uci')
        await self._until('uciok', 10)
        for opt in ('Threads value 1', 'Hash value 32'):
            await self._send('setoption name ' + opt)
        await self._send('isready')
        await self._until('readyok', 10)

    async def _send(self, line):
        self.p.stdin.write((line + '\n').encode())
        await self.p.stdin.drain()

    async def _until(self, word, timeout):
        end = time.monotonic() + timeout
        while True:
            left = end - time.monotonic()
            if left <= 0:
                raise TimeoutError(word)
            line = (await asyncio.wait_for(self.p.stdout.readline(), left)).decode(errors='replace').strip()
            if not line and self.p.stdout.at_eof():
                raise EOFError('stockfish stopped')
            if line.startswith(word):
                return line

    async def move(self, fen, ms, elo):
        if self.p is None or self.p.returncode is not None:
            await self.start()
        if elo:
            await self._send('setoption name UCI_LimitStrength value true')
            await self._send('setoption name UCI_Elo value %d' % elo)
        else:
            await self._send('setoption name UCI_LimitStrength value false')
        await self._send('position fen ' + fen)
        await self._send('go movetime %d' % ms)
        depth = 0
        end = time.monotonic() + ms / 1000 + 2
        while True:
            left = end - time.monotonic()
            if left <= 0:
                await self._send('stop')
                left = 1
            line = (await asyncio.wait_for(self.p.stdout.readline(), left)).decode(errors='replace').strip()
            if not line and self.p.stdout.at_eof():
                raise EOFError('stockfish stopped')
            m = re.match(r'^info .*?\bdepth (\d+)', line)
            if m:
                depth = int(m.group(1))
            if line.startswith('bestmove'):
                parts = line.split()
                mv = parts[1] if len(parts) > 1 else ''
                return (mv if MOVE_RE.match(mv) else None), depth


async def _connect(headers):
    import websockets
    try:
        return await websockets.connect(URL, additional_headers=headers, open_timeout=20, max_size=4096,
                                        ping_interval=None)
    except TypeError:                                   # websockets 12-13
        return await websockets.connect(URL, extra_headers=headers, open_timeout=20, max_size=4096,
                                        ping_interval=None)


async def session(engine, ident, log):
    headers = {'Authorization': 'Bearer %s.%s' % (ident['id'], ident['secret']),
               'X-Helper-Version': _version(), 'X-Helper-Cores': str(os.cpu_count() or 0)}
    ws = await _connect(headers)
    jobs = 0
    try:
        async def pinger():
            while True:
                await asyncio.sleep(30)
                await ws.send('{"t":"ping"}')
        ping = asyncio.ensure_future(pinger())
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                if isinstance(msg, dict) and msg.get('t') == 'hello':
                    write_status(connected=True, link_code=str(msg.get('link_code', ''))[:12],
                                 linked=bool(msg.get('linked')), since=time.time())
                    log('connected to KindleHub (link code %s)' % msg.get('link_code'))
                    continue
                job = check_job(msg)
                if not job:
                    continue
                try:
                    mv, depth = await engine.move(job['fen'], job['ms'], job['elo'])
                except Exception as e:
                    log('engine problem: %s' % e)
                    engine.p = None
                    mv, depth = None, 0
                await ws.send(json.dumps({'t': 'res', 'j': job['j'], 'move': mv or '', 'depth': depth}))
                jobs += 1
                if jobs % 50 == 0:
                    write_status(jobs_this_session=jobs)
        finally:
            ping.cancel()
    finally:
        await ws.close()
        write_status(connected=False)


async def main(log=print):
    if not (shutil.which('stockfish') or os.path.exists('/usr/games/stockfish')):
        log('stockfish is not installed (sudo apt install stockfish): not starting')
        write_status(connected=False, problem='stockfish missing')
        await asyncio.sleep(3600)
        return
    ident = identity()
    engine = Engine()
    wait = 5
    while True:
        started = time.monotonic()
        try:
            await session(engine, ident, log)
        except Exception as e:
            log('not connected: %s' % str(e)[:200])
            write_status(connected=False)
        if time.monotonic() - started > 120:
            wait = 5
        await asyncio.sleep(wait)
        wait = min(300, wait * 2)


if __name__ == '__main__':
    try:
        asyncio.run(main(lambda m: print(m, flush=True)))
    except KeyboardInterrupt:
        sys.exit(0)
