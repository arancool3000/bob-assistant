"""Bob's main loop (service bob-assistant): listen for the wake phrase on the Pi, then talk with Gemini Live.

    python -m bob.main
"""
import asyncio
import json
import os
import signal
import sys
import time

from . import audio, config, live, tools, wake

VOSK_MODEL = os.environ.get('BOB_VOSK_MODEL', os.path.join(config.STATE, 'vosk-model'))
STATE_FILE = os.path.join(config.STATE, 'state.json')


def log(msg):
    sys.stdout.write('%s %s\n' % (time.strftime('%H:%M:%S'), msg))
    sys.stdout.flush()


def set_state(s):
    """What Bob is doing, for the web page (asleep / listening / speaking / working / needs_setup)."""
    try:
        os.makedirs(config.STATE, exist_ok=True)
        with open(STATE_FILE + '.tmp', 'w') as f:
            json.dump({'state': s, 'at': time.time()}, f)
        os.replace(STATE_FILE + '.tmp', STATE_FILE)
    except OSError:
        pass


def main():
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
    cfg = config.load()
    key = config.load_secrets()['gemini_api_key']
    while not key:
        set_state('needs_setup')
        log('no Gemini API key yet: add it on the web page (http://<this pi>:%s) or in /etc/bob/secrets.json' % cfg['web_port'])
        time.sleep(15)
        cfg, key = config.load(), config.load_secrets()['gemini_api_key']
    try:
        audio.set_volume(cfg.get('volume', 70))
    except Exception as e:
        log('volume not set: %s' % e)
    speaker = audio.Speaker(audio.safe_device(cfg.get('speaker_device')))
    chime = audio.tone()

    def ring(name):
        log('timer done: %s' % name)
        for _ in range(3):
            speaker.play(audio.tone((1046, 1318, 1568), 150, 0.3))
            time.sleep(1.2)

    toolbox = tools.Tools(cfg, ring)
    mic = audio.Mic(audio.safe_device(cfg.get('mic_device'))).start()
    ww = wake.WakeWord(cfg, VOSK_MODEL)
    set_state('asleep')
    speaker.play(audio.tone((660, 990, 1320), 120, 0.2))      # "I'm ready" -- also how a first install says it is done
    log('listening for "%s" (on this Pi; nothing leaves it until then)' % cfg.get('wake_phrase'))
    failures = 0
    while True:
        try:
            if not ww.wait(mic):
                continue
        except Exception as e:
            log('listening failed: %s' % e)
            time.sleep(2)
            mic.start()
            continue
        log('woken')
        speaker.play(chime)
        time.sleep(0.25)
        try:
            cfg.update(config.load())                     # settings changed on the web page apply now
            toolbox.cfg = cfg
            why = asyncio.run(live.converse(cfg, config.load_secrets()['gemini_api_key'], mic, speaker, toolbox,
                                            log=log, on_state=set_state))
            log('conversation ended (%s)' % why)
            failures = 0
        except Exception as e:
            failures += 1
            log('conversation failed: %s' % str(e)[:200])
            speaker.play(audio.tone((440, 330), 160, 0.25))     # a low "that didn't work" tone
            time.sleep(min(30, 2 ** failures))
        set_state('asleep')


if __name__ == '__main__':
    main()
