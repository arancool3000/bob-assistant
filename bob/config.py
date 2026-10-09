"""Settings. Two files, both written by the installer (or the Windows setup app's first-boot file):

  /etc/bob/config.json   everyday settings anyone on the Pi may read (wake phrase, voice, town, ...)
  /etc/bob/secrets.json  the Gemini API key and the web page password hash -- readable only by Bob (0600)

Everything has a default, so a missing key never stops Bob from starting.
"""
import hashlib
import json
import os
import secrets as _secrets

ETC = os.environ.get('BOB_ETC', '/etc/bob')
STATE = os.environ.get('BOB_STATE', '/var/lib/bob-assistant')
CONFIG_FILE = os.path.join(ETC, 'config.json')
SECRETS_FILE = os.path.join(ETC, 'secrets.json')

DEFAULTS = {
    'name': 'Bob',                       # what he calls himself
    'wake_phrase': 'hey bob',            # said to wake him; any two short words work best
    'wake_sensitivity': 55,              # 0-100: higher wakes more easily (and falsely)
    'voice': 'Charon',                   # a Gemini Live voice: Charon, Puck, Kore, Fenrir, Aoede, Leda, Orus, Zephyr
    'language': 'en-GB',
    'model': 'gemini-3.8-live',          # tried first; if your key cannot use it, the fallback is used
    'model_fallback': 'gemini-3.1-flash-live-preview',
    'personality': 'Calm, warm and a little dry. Short answers unless asked for more.',
    'town': '',                          # for the weather; set in setup or on the web page
    'latitude': None,
    'longitude': None,
    'timezone': '',
    'mic_device': 'default',             # ALSA device names (arecord -L / aplay -L)
    'speaker_device': 'default',
    'volume': 70,
    'idle_seconds': 12,                  # how long Bob waits for more before going back to sleep
    'auto_update': True,
    'update_channel': 'stable',          # stable = tagged releases only
    'repo': 'https://github.com/arancool3000/bob-assistant',
    'web_port': 8080,
    'log_conversations': False,          # transcripts are never written unless this is turned on
}


def _read(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def load():
    cfg = dict(DEFAULTS)
    cfg.update(_read(CONFIG_FILE))
    return cfg


def save(cfg):
    os.makedirs(ETC, exist_ok=True)
    clean = {k: v for k, v in cfg.items() if k in DEFAULTS}
    tmp = CONFIG_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(clean, f, indent=2)
    os.replace(tmp, CONFIG_FILE)


def load_secrets():
    s = _read(SECRETS_FILE)
    key = os.environ.get('GEMINI_API_KEY') or s.get('gemini_api_key', '')
    return {'gemini_api_key': key, 'web_password': s.get('web_password', '')}


def save_secrets(s):
    os.makedirs(ETC, exist_ok=True)
    tmp = SECRETS_FILE + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(s, f, indent=2)
    os.replace(tmp, SECRETS_FILE)


def hash_password(pw, salt=None):
    """PBKDF2 for the web page password; the file stores 'pbkdf2$<salt>$<hash>', never the password."""
    salt = salt or _secrets.token_hex(8)
    h = hashlib.pbkdf2_hmac('sha256', pw.encode(), salt.encode(), 200000).hex()
    return 'pbkdf2$%s$%s' % (salt, h)


def check_password(pw, stored):
    try:
        _algo, salt, _h = stored.split('$')
    except (ValueError, AttributeError):
        return False
    return _secrets.compare_digest(hash_password(pw, salt), stored)


def state_path(*parts):
    p = os.path.join(STATE, *parts)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p
