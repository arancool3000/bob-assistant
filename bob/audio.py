"""Microphone in, speaker out, through ALSA (arecord / aplay), which Raspberry Pi OS Lite has out of the box.

  Mic      16 kHz mono 16-bit, what both the wake word and Gemini Live want
  Speaker  24 kHz mono 16-bit, what Gemini Live sends back

Kevin is half-duplex: while he is speaking the microphone stream is still read (so it never backs up) but not sent
anywhere, which stops him from hearing himself on a speaker that sits next to the mic.
"""
import math
import queue
import select
import struct
import subprocess
import threading
import time

import re

RATE_IN, RATE_OUT = 16000, 24000
DEVICE_RE = re.compile(r'^[A-Za-z0-9_:,.=-]{1,80}$')


def safe_device(name):
    """An ALSA device name, or 'default'. Refuses anything that could be an ALSA file/pipe plugin."""
    n = str(name or 'default')
    return n if DEVICE_RE.match(n) and 'file' not in n.lower() and 'pipe' not in n.lower() else 'default'
CHUNK = 3200            # 100 ms of 16 kHz mono PCM16


class Mic:
    def __init__(self, device='default'):
        self.device = device or 'default'
        self.proc = None

    def start(self):
        self.stop()
        self.proc = subprocess.Popen(['arecord', '-q', '-D', self.device, '-f', 'S16_LE', '-r', str(RATE_IN),
                                      '-c', '1', '-t', 'raw'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     bufsize=0)
        return self

    def read(self, timeout=3.0):
        """100 ms of audio, or b'' if the microphone went away or stalled (the caller restarts it)."""
        if not self.proc or self.proc.poll() is not None:
            return b''
        data = b''
        while len(data) < CHUNK:
            r, _, _ = select.select([self.proc.stdout], [], [], timeout)
            if not r:                                   # a USB mic that stopped sending: restart it
                self.stop()
                return b''
            part = self.proc.stdout.read(CHUNK - len(data))
            if not part:
                return b''
            data += part
        return data

    def stop(self):
        if self.proc:
            try:
                self.proc.kill()
                self.proc.wait(timeout=2)
            except Exception:
                pass
        self.proc = None


class Speaker:
    """aplay behind a queue, so a slow or missing sound card can never freeze a conversation. flush() is instant:
    it bumps a generation number (queued and in-flight chunks of an older generation are dropped) and kills aplay."""

    def __init__(self, device='default', rate=RATE_OUT):
        self.device, self.rate = device or 'default', rate
        self.q = queue.Queue()
        self.proc = None
        self.gen = 0
        self.busy_until = 0.0
        threading.Thread(target=self._pump, daemon=True).start()

    def _open(self):
        self.proc = subprocess.Popen(['aplay', '-q', '-D', self.device, '-f', 'S16_LE', '-r', str(self.rate),
                                      '-c', '1', '-t', 'raw'], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def _pump(self):
        while True:
            gen, data = self.q.get()
            if gen != self.gen:
                continue                                # from before a flush
            try:
                p = self.proc
                if not p or p.poll() is not None:
                    self._open()
                    p = self.proc
                p.stdin.write(data)
                p.stdin.flush()
            except Exception:
                self.proc = None

    def play(self, pcm):
        now = time.time()
        self.busy_until = max(self.busy_until, now) + len(pcm) / (2.0 * self.rate)
        self.q.put((self.gen, pcm))

    def speaking(self, tail=0.35):
        return time.time() < self.busy_until + tail

    def flush(self):
        """Interrupted: drop what is queued and stop at once."""
        self.gen += 1
        p, self.proc = self.proc, None
        if p:
            try:
                p.kill()
            except Exception:
                pass
        self.busy_until = 0.0


def tone(freqs=(880, 1320), ms=110, volume=0.25, rate=RATE_OUT):
    """A short soft chime as PCM16 (the 'I'm listening' sound): each note fades in and out."""
    out = bytearray()
    for f in freqs:
        n = int(rate * ms / 1000)
        for i in range(n):
            env = min(1.0, i / (rate * 0.008), (n - i) / (rate * 0.03))
            out += struct.pack('<h', int(32767 * volume * env * math.sin(2 * math.pi * f * i / rate)))
    return bytes(out)


def set_volume(percent):
    """The first playback control the sound card has (Master, PCM, Speaker or Headphone)."""
    pct = max(0, min(100, int(percent)))
    for control in ('Master', 'PCM', 'Speaker', 'Headphone'):
        r = subprocess.run(['amixer', '-q', 'sset', control, '%d%%' % pct], capture_output=True)
        if r.returncode == 0:
            return pct
    raise RuntimeError('no volume control found on this sound card')
