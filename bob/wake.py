"""The wake phrase, heard on the Pi itself (Vosk, offline): nothing leaves the Pi until "hey bob" is heard.

The recogniser is limited to the wake phrase plus "[unk]" (anything else), which makes it fast and hard to
fool, and a word must reach a confidence bar set by the sensitivity setting (0-100 %).
"""
import json
import os
import time

from . import audio


def min_confidence(sensitivity):
    """55 % -> 0.675. Higher sensitivity = a lower bar = wakes more easily."""
    s = max(0, min(100, float(sensitivity)))
    return round(0.95 - 0.5 * s / 100.0, 3)


def decide(words, phrase, sensitivity):
    """Vosk's word list (with confidences) -> did that contain the wake phrase, confidently enough?"""
    want = phrase.lower().split()
    if not want:
        return False
    got = [w for w in words if w.get('word') and w['word'] != '[unk]']
    texts = [w['word'].lower() for w in got]
    bar = min_confidence(sensitivity)
    for i in range(len(texts) - len(want) + 1):
        if texts[i:i + len(want)] == want:
            confs = [got[i + k].get('conf', 0) for k in range(len(want))]
            if min(confs) >= bar:
                return True
    return False


def known_words(model_dir):
    """The words the offline model can hear (a wake phrase must be made of these)."""
    try:
        with open(os.path.join(model_dir, 'graph', 'words.txt'), encoding='utf-8') as f:
            return {line.split()[0] for line in f if line.strip()}
    except OSError:
        return None


def check_phrase(phrase, model_dir):
    """None if the phrase is usable, else a reason."""
    words = [w for w in str(phrase or '').lower().replace(',', ' ').split() if w]
    if not 1 <= len(words) <= 4:
        return 'use one to four words, e.g. "hey bob"'
    if any(not w.isalpha() for w in words):
        return 'letters only'
    vocab = known_words(model_dir)
    if vocab is not None:
        unknown = [w for w in words if w not in vocab]
        if unknown:
            return 'the offline recogniser does not know %s: pick common English words' % ', '.join(unknown)
    return None


class WakeWord:
    def __init__(self, cfg, model_dir):
        import vosk
        vosk.SetLogLevel(-1)
        if not os.path.isdir(model_dir):
            raise RuntimeError('the wake word model is missing at %s (run install.sh again)' % model_dir)
        self.cfg = cfg
        self.model = vosk.Model(model_dir)
        phrase = ' '.join(str(cfg.get('wake_phrase', '') or '').lower().replace(',', ' ').split())
        self.phrase = phrase if phrase and not check_phrase(phrase, model_dir) else 'hey bob'

    def wait(self, mic, stop=None):
        """Block until the wake phrase is heard (returns True), or stop() says to give up (returns False)."""
        import vosk
        grammar = json.dumps([self.phrase, '[unk]'])
        rec = vosk.KaldiRecognizer(self.model, audio.RATE_IN, grammar)
        rec.SetWords(True)
        while True:
            if stop and stop():
                return False
            data = mic.read()
            if not data:
                time.sleep(0.5)
                mic.start()
                continue
            if rec.AcceptWaveform(data):
                r = json.loads(rec.Result())
                if decide(r.get('result', []), self.phrase, self.cfg.get('wake_sensitivity', 55)):
                    return True
