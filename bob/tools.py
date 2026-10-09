"""Bob's built-in abilities: the everyday ones, before anyone has taught him anything.

Time, weather (Open-Meteo, free, no key), timers, notes he remembers, volume, the Pi's own health, ending the
conversation -- plus the skill-building tools in skills.py. Google Search comes built into Gemini Live.
"""
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request

from . import audio, config, skills

NOTES = os.path.join(config.STATE, 'notes.json')
WEATHER_CODES = {0: 'clear', 1: 'mostly clear', 2: 'partly cloudy', 3: 'overcast', 45: 'fog', 48: 'fog',
                 51: 'light drizzle', 53: 'drizzle', 55: 'heavy drizzle', 61: 'light rain', 63: 'rain',
                 65: 'heavy rain', 71: 'light snow', 73: 'snow', 75: 'heavy snow', 80: 'showers',
                 81: 'showers', 82: 'heavy showers', 95: 'thunderstorm', 96: 'thunderstorm', 99: 'thunderstorm'}

S = lambda d: {'type': 'STRING', 'description': d}      # noqa: E731
N = lambda d: {'type': 'NUMBER', 'description': d}      # noqa: E731
OBJ = lambda props=None, req=None: {'type': 'OBJECT', 'properties': props or {}, 'required': req or []}   # noqa: E731

DECLARATIONS = [
    {'name': 'get_time', 'description': 'The current local date and time.', 'parameters': OBJ()},
    {'name': 'get_weather', 'description': 'Weather now and today (and tomorrow) for the town in settings, or another place.',
     'parameters': OBJ({'place': S('optional: another town or city')})},
    {'name': 'set_timer', 'description': 'Start a timer. When it ends, a chime plays.',
     'parameters': OBJ({'minutes': N('minutes'), 'seconds': N('seconds'), 'name': S('optional name, e.g. pasta')})},
    {'name': 'list_timers', 'description': 'Timers running now and the time left on each.', 'parameters': OBJ()},
    {'name': 'cancel_timer', 'description': 'Cancel a timer by name, or all of them.', 'parameters': OBJ({'name': S('name, or "all"')})},
    {'name': 'remember', 'description': 'Remember a fact the user tells you, for later conversations.',
     'parameters': OBJ({'fact': S('the fact, in a short sentence')}, ['fact'])},
    {'name': 'recall', 'description': 'What you have been asked to remember (optionally about one topic).',
     'parameters': OBJ({'about': S('optional topic')})},
    {'name': 'forget', 'description': 'Forget remembered facts that mention some words.', 'parameters': OBJ({'about': S('words to match')}, ['about'])},
    {'name': 'set_volume', 'description': 'Speaker volume, 0-100.', 'parameters': OBJ({'percent': N('0-100')}, ['percent'])},
    {'name': 'pi_status', 'description': 'The Raspberry Pi itself: temperature, uptime, disk space, memory, IP address, version.',
     'parameters': OBJ()},
    {'name': 'gpio_guide', 'description': 'Which GPIO pins are free to wire things to and how (BCM numbers), to help the user wire hardware for a new skill.',
     'parameters': OBJ()},
    {'name': 'end_conversation', 'description': 'Go back to sleep (the user said bye, thanks, that is all).', 'parameters': OBJ()},
] + skills.DECLARATIONS


class Timers:
    def __init__(self, ring):
        self.ring, self.items, self.lock = ring, {}, threading.Lock()

    def add(self, secs, name):
        nm = name or 'timer %d' % (len(self.items) + 1)
        due = time.time() + secs
        with self.lock:
            self.items[nm] = due
        threading.Thread(target=self._wait, args=(nm, due), daemon=True).start()
        return nm, due

    def _wait(self, nm, due):
        time.sleep(max(0, due - time.time()))
        with self.lock:
            if self.items.get(nm) != due:
                return
            del self.items[nm]
        self.ring(nm)

    def view(self):
        now = time.time()
        with self.lock:
            return [{'name': n, 'seconds_left': round(d - now)} for n, d in sorted(self.items.items(), key=lambda kv: kv[1])]

    def cancel(self, name):
        with self.lock:
            if not name or name == 'all':
                n = len(self.items)
                self.items.clear()
                return n
            hit = [k for k in self.items if name.lower() in k.lower()]
            for k in hit:
                del self.items[k]
            return len(hit)


def _notes():
    try:
        with open(NOTES) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _save_notes(n):
    os.makedirs(os.path.dirname(NOTES), exist_ok=True)
    with open(NOTES + '.tmp', 'w') as f:
        json.dump(n[-500:], f)
    os.replace(NOTES + '.tmp', NOTES)


def geocode(place):
    q = urllib.parse.urlencode({'name': place, 'count': 1, 'language': 'en', 'format': 'json'})
    with urllib.request.urlopen('https://geocoding-api.open-meteo.com/v1/search?' + q, timeout=10) as r:
        res = (json.load(r).get('results') or [None])[0]
    if not res:
        raise ValueError('I could not find a place called %s' % place)
    return res['latitude'], res['longitude'], res.get('timezone', 'auto'), '%s, %s' % (res['name'], res.get('country', ''))


def weather(lat, lon, tz='auto'):
    q = urllib.parse.urlencode({'latitude': lat, 'longitude': lon, 'timezone': tz or 'auto', 'forecast_days': 2,
                                'current': 'temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m',
                                'daily': 'temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code'})
    with urllib.request.urlopen('https://api.open-meteo.com/v1/forecast?' + q, timeout=10) as r:
        d = json.load(r)
    c, day = d['current'], d['daily']
    return {'now': {'temp_c': c['temperature_2m'], 'feels_like_c': c['apparent_temperature'],
                    'sky': WEATHER_CODES.get(c['weather_code'], 'unknown'), 'wind_kmh': c['wind_speed_10m'],
                    'humidity_pct': c['relative_humidity_2m']},
            'today': {'high_c': day['temperature_2m_max'][0], 'low_c': day['temperature_2m_min'][0],
                      'rain_chance_pct': day['precipitation_probability_max'][0], 'sky': WEATHER_CODES.get(day['weather_code'][0], '')},
            'tomorrow': {'high_c': day['temperature_2m_max'][1], 'low_c': day['temperature_2m_min'][1],
                         'rain_chance_pct': day['precipitation_probability_max'][1], 'sky': WEATHER_CODES.get(day['weather_code'][1], '')}}


def _ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('192.0.2.1', 9))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


def pi_status():
    out = {}
    try:
        with open('/sys/class/thermal/thermal_zone0/temp') as f:
            out['cpu_temp_c'] = round(int(f.read()) / 1000.0, 1)
    except OSError:
        pass
    try:
        with open('/proc/uptime') as f:
            out['uptime_hours'] = round(float(f.read().split()[0]) / 3600, 1)
    except OSError:
        pass
    du = shutil.disk_usage('/')
    out['disk_free_gb'] = round(du.free / 1e9, 1)
    try:
        with open('/proc/meminfo') as f:
            m = {l.split(':')[0]: int(l.split()[1]) for l in f}
        out['memory_free_mb'] = round(m.get('MemAvailable', 0) / 1024)
    except (OSError, ValueError):
        pass
    out['ip'] = _ip()
    try:
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'VERSION')) as f:
            out['version'] = f.read().strip()
    except OSError:
        pass
    return out


GPIO_GUIDE = {
    'numbering': 'BCM (GPIO numbers), as gpiozero uses -- not the physical pin numbers',
    'free_for_anything': [4, 5, 6, 12, 13, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27],
    'physical_pin_of': {4: 7, 5: 29, 6: 31, 12: 32, 13: 33, 16: 36, 17: 11, 18: 12, 19: 35, 20: 38, 21: 40, 22: 15, 23: 16, 24: 18, 25: 22, 26: 37, 27: 13},
    'i2c': {'sda': 2, 'scl': 3, 'note': 'switched on by the installer (needs one reboot after install)'},
    'serial': {'tx': 14, 'rx': 15, 'note': 'turn off the serial console in raspi-config first'},
    'spi': {'mosi': 10, 'miso': 9, 'sclk': 11, 'ce0': 8, 'ce1': 7},
    'power': '3.3V on pins 1 and 17, 5V on pins 2 and 4, ground on 6, 9, 14, 20, 25, 30, 34, 39',
    'rules': ['GPIO pins are 3.3 V: never connect 5 V to a GPIO pin',
              'an LED needs a resistor (220-330 ohm) in series',
              'motors, bare relay coils and long LED strips need their own power supply and a driver -- never power them from a GPIO pin (a ready-made relay module is fine)',
              'never switch mains voltage unless the relay is rated, enclosed and wired by someone qualified',
              'buttons: one leg to the GPIO pin, the other to ground (gpiozero Button uses the internal pull-up)'],
}


class Tools:
    def __init__(self, cfg, ring=None):
        self.cfg = cfg
        self.timers = Timers(ring or (lambda name: None))
        self.ended = False

    def declarations(self):
        return DECLARATIONS + skills.declarations()

    def builtin_declarations(self):
        return list(DECLARATIONS)

    def call(self, name, args):
        a = args or {}
        try:
            r = skills.handle(name, a)
            if r is not None:
                return r
            fn = getattr(self, 't_' + name, None)
            if not fn:
                return {'error': 'no tool called %s' % name}
            return fn(**a)
        except TypeError as e:
            return {'error': 'wrong arguments: %s' % e}
        except Exception as e:
            return {'error': str(e)[:300]}

    def t_get_time(self):
        return {'now': time.strftime('%A %d %B %Y, %H:%M'), 'timezone': time.strftime('%Z')}

    def t_get_weather(self, place=None):
        c = self.cfg
        if place:
            lat, lon, tz, label = geocode(place)
        elif c.get('latitude') is not None:
            lat, lon, tz, label = c['latitude'], c['longitude'], c.get('timezone') or 'auto', c.get('town') or 'here'
        elif c.get('town'):
            lat, lon, tz, label = geocode(c['town'])
        else:
            return {'error': 'no town set: say which town, or set it on the web page'}
        return dict(weather(lat, lon, tz), place=label)

    def t_set_timer(self, minutes=None, seconds=None, name=None):
        secs = float(minutes or 0) * 60 + float(seconds or 0)
        if secs <= 0:
            return {'error': 'how long?'}
        nm, due = self.timers.add(secs, name)
        return {'ok': True, 'name': nm, 'ends_at': time.strftime('%H:%M:%S', time.localtime(due))}

    def t_list_timers(self):
        return {'timers': self.timers.view()}

    def t_cancel_timer(self, name='all'):
        return {'cancelled': self.timers.cancel(name)}

    def t_remember(self, fact):
        n = _notes()
        n.append({'at': time.strftime('%Y-%m-%d'), 'fact': str(fact)[:400]})
        _save_notes(n)
        return {'ok': True}

    def t_recall(self, about=None):
        n = _notes()
        if about:
            w = str(about).lower()
            n = [x for x in n if w in x['fact'].lower()]
        return {'facts': n[-40:]}

    def t_forget(self, about):
        n = _notes()
        keep = [x for x in n if str(about).lower() not in x['fact'].lower()]
        _save_notes(keep)
        return {'forgot': len(n) - len(keep)}

    def t_set_volume(self, percent):
        pct = audio.set_volume(percent)
        self.cfg['volume'] = pct
        try:
            cfg = config.load()
            cfg['volume'] = pct
            config.save(cfg)                    # kept across restarts
        except OSError:
            pass
        return {'ok': True, 'volume': pct}

    def t_pi_status(self):
        return pi_status()

    def t_gpio_guide(self):
        return GPIO_GUIDE

    def t_end_conversation(self):
        self.ended = True
        return {'ok': True}


def memory_lines(limit=30):
    """Remembered facts, for the start of every conversation."""
    n = _notes()[-limit:]
    return '\n'.join('- %s' % x['fact'] for x in n)


def update_notice():
    """If the updater installed a new version since Bob last said so, say so once."""
    seen_path = os.path.join(config.STATE, 'announced.json')
    try:
        with open('/var/lib/bob-assistant-update/updated.json') as f:
            d = json.load(f)
    except (OSError, ValueError):
        return ''
    try:
        with open(seen_path) as f:
            if json.load(f).get('version') == d.get('version'):
                return ''
    except (OSError, ValueError):
        pass
    try:
        with open(seen_path, 'w') as f:
            json.dump({'version': d.get('version')}, f)
    except OSError:
        pass
    return 'You were just updated to version %s: mention it briefly if it fits.' % d.get('version')


def run_cmd(cmd, timeout=10):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
