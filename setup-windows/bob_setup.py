"""Bob Setup for Windows.

Two ways to set up a Raspberry Pi as Bob:

  1. Write a microSD card  (recommended, for a brand-new Pi)
     Choose the card: the app downloads Raspberry Pi OS Lite (64-bit) from raspberrypi.com, writes and checks it
     (flasher.py), then writes your Wi-Fi, a login, SSH, and Bob's settings (Gemini key, name, town,
     settings-page password) onto it. Put the card in the Pi and power it on: it joins your Wi-Fi and installs
     Bob by itself (10-20 minutes the first time). A card already flashed with Raspberry Pi Imager (no
     customisation) can have just the settings added.

  2. Install on a Pi that is already running
     Give its address, user name and password; the app installs Bob over the network (SSH) and shows progress.

Nothing is sent anywhere except to your own Pi (the only download is the Raspberry Pi OS image). The Gemini key
and passwords sit on the card only until first boot, when the installer moves them into a file only Bob can read
and deletes the copy on the card.
"""
import json
import os
import re
import secrets
import string
import sys
import threading
import webbrowser

import flasher

REPO = 'arancool3000/bob-assistant'
try:
    from _version import TAG            # written by the release workflow: the app installs its own release
except ImportError:
    TAG = 'main'
INSTALL_URL = 'https://raw.githubusercontent.com/%s/%s/install.sh' % (REPO, TAG)
KEY_URL = 'https://aistudio.google.com/apikey'
COUNTRIES = ['GB', 'US', 'IE', 'CA', 'AU', 'NZ', 'DE', 'FR', 'ES', 'IT', 'NL', 'BE', 'SE', 'NO', 'DK', 'FI', 'PL',
             'PT', 'CH', 'AT', 'IN', 'JP', 'SG', 'ZA', 'BR', 'MX']
VOICES = ['Charon', 'Puck', 'Kore', 'Fenrir', 'Aoede', 'Leda', 'Orus', 'Zephyr']


# ---- what goes on the card (pure functions: tested on any OS) -----------------------------------------

def _yaml_str(s):
    """A YAML double-quoted string, safe for any password or Wi-Fi name."""
    return '"' + str(s).replace('\\', '\\\\').replace('"', '\\"') + '"'


RESERVED_USERS = {'root', 'bob-assistant', 'bob-skill', 'daemon', 'nobody'}


def _common(d, errs):
    if not d.get('gemini_api_key', '').strip():
        errs.append('Gemini API key is empty (get one free at aistudio.google.com/apikey)')
    if len(d.get('web_password', '')) < 6:
        errs.append('Settings page password: at least 6 characters')


def validate_card(d):
    """Checks for preparing a new SD card."""
    errs = []
    if not re.match(r'^[a-z]([a-z0-9-]{0,30}[a-z0-9])?$', d.get('hostname', '')):
        errs.append('Pi name: lower case letters, digits and -, starting with a letter, not ending in - (e.g. bob)')
    if not re.match(r'^[a-z_][a-z0-9_-]{0,30}$', d.get('username', '')) or d.get('username') in RESERVED_USERS:
        errs.append('User name: lower case letters and digits, not root (e.g. pi)')
    if len(d.get('password', '')) < 8:
        errs.append('Pi password: at least 8 characters')
    ssid = d.get('wifi_ssid', '')
    if not ssid:
        errs.append('Wi-Fi name is empty')
    elif len(ssid.encode('utf-8')) > 32:
        errs.append('Wi-Fi name: at most 32 bytes')
    pw = d.get('wifi_password', '')
    if pw and not (8 <= len(pw) <= 63 or re.match(r'^[0-9a-fA-F]{64}$', pw)):
        errs.append('Wi-Fi password: 8 to 63 characters (or empty for an open network)')
    _common(d, errs)
    return errs


def validate_ssh(d):
    """Checks for installing on a Pi that is already running (no Wi-Fi or new password needed)."""
    errs = []
    if not d.get('username'):
        errs.append('User name: the one you log in to the Pi with')
    if not d.get('password'):
        errs.append('Pi password: the one you log in to the Pi with')
    _common(d, errs)
    return errs


validate = validate_card            # older name, kept for scripts


def firstboot_json(d):
    out = {k: d.get(k, '') for k in ('gemini_api_key', 'web_password', 'name', 'wake_phrase', 'town', 'voice')}
    out['kindlehub_helper'] = d.get('kindlehub_helper') is True        # "Help run KindleHub": off unless ticked
    return json.dumps(out, indent=2)


def firstboot_script(country='GB'):
    """Runs on the Pi at first boot (as a service that retries until it succeeds): Wi-Fi on, clock right,
    then the installer. Its log is /var/log/bob-install.log."""
    return '\n'.join([
        '#!/bin/bash',
        '# Bob first boot: installs Bob, then switches itself off. Log: /var/log/bob-install.log',
        'exec >>/var/log/bob-install.log 2>&1',
        'set -o pipefail',
        'export HOME=/root DEBIAN_FRONTEND=noninteractive',
        'echo "== first boot $(date)"',
        'rfkill unblock wifi 2>/dev/null; for f in /var/lib/systemd/rfkill/*:wlan; do [ -e "$f" ] && echo 0 > "$f"; done',
        'command -v raspi-config >/dev/null && raspi-config nonint do_wifi_country %s' % country,
        'for i in $(seq 1 120); do curl -fsS -o /dev/null --max-time 10 https://github.com && break; sleep 5; done',
        'for i in $(seq 1 60); do [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" = yes ] && break; sleep 5; done',
        'curl -fsSL --retry 5 --retry-all-errors -o /root/bob-install.sh %s || exit 1' % INSTALL_URL,
        'bash /root/bob-install.sh --config /etc/bob/firstboot.json || exit 1',
        'rm -f /root/bob-install.sh',
        'systemctl disable bob-firstboot.service',
        'echo "== Bob installed $(date)"',
        '',
    ])


FIRSTBOOT_SERVICE = (
    '[Unit]\nDescription=Install Bob on first boot\nAfter=network-online.target time-sync.target\nWants=network-online.target\n'
    '[Service]\nType=oneshot\nExecStart=/bin/bash /usr/local/sbin/bob-firstboot.sh\nRestart=on-failure\nRestartSec=60\n'
    'TimeoutStartSec=3600\n[Install]\nWantedBy=multi-user.target\n')


def cloud_init_files(d):
    """user-data, network-config and meta-data for Raspberry Pi OS images that use cloud-init (2025 onwards)."""
    groups = 'users,adm,dialout,audio,netdev,video,plugdev,input,gpio,spi,i2c,render,sudo'
    def block(path, perm, text):
        return ['  - path: %s' % path, '    permissions: "%s"' % perm, '    content: |'] + \
            ['      ' + line for line in text.splitlines()]
    user_data = '\n'.join([
        '#cloud-config',
        '# written by Bob Setup',
        'hostname: %s' % d['hostname'],
        'manage_etc_hosts: true',
        'users:',
        '  - name: %s' % d['username'],
        '    groups: %s' % groups,
        '    shell: /bin/bash',
        '    lock_passwd: false',
        '    sudo: ALL=(ALL) NOPASSWD:ALL',
        'chpasswd:',
        '  expire: false',
        '  users:',
        '    - {name: %s, password: %s, type: text}' % (d['username'], _yaml_str(d['password'])),
        'ssh_pwauth: true',
        'apt:',
        '  conf: |',
        '    Acquire { Check-Date "false"; };',
        'write_files:',
    ] + block('/etc/bob/firstboot.json', '0600', firstboot_json(d))
      + block('/usr/local/sbin/bob-firstboot.sh', '0700', firstboot_script(d.get('country', 'GB')))
      + block('/etc/systemd/system/bob-firstboot.service', '0644', FIRSTBOOT_SERVICE) + [
        'runcmd:',
        '  - [systemctl, enable, --now, ssh]',
        '  - [systemctl, daemon-reload]',
        '  - [systemctl, enable, bob-firstboot.service]',
        '  - [systemctl, start, --no-block, bob-firstboot.service]',
        '',
    ])
    ap = ['        %s:' % _yaml_str(d['wifi_ssid'])]
    ap += (['          password: %s' % _yaml_str(d['wifi_password'])] if d.get('wifi_password') else ['          {}'])
    network = '\n'.join([
        'network:',
        '  version: 2',
        '  wifis:',
        '    renderer: NetworkManager',
        '    wlan0:',
        '      dhcp4: true',
        '      optional: true',
        '      regulatory-domain: %s' % d.get('country', 'GB'),
        '      access-points:',
    ] + ap + [''])
    meta = 'dsmode: local\ninstance-id: bob-%s\nlocal-hostname: %s\n' % (secrets.token_hex(4), d['hostname'])
    return {'user-data': user_data, 'network-config': network, 'meta-data': meta}


def _nm_escape(v):
    """A value for a NetworkManager keyfile (glib key-file syntax): backslashes escaped."""
    return str(v).replace('\\', '\\\\')


def firstrun_script(d):
    """firstrun.sh for older Bookworm images (the way Raspberry Pi Imager sets a card up). Runs once, as root,
    before the normal boot: user, name, SSH, Wi-Fi, and the first-boot install service."""
    def q(s):
        return "'" + str(s).replace("'", "'\\''") + "'"
    pw = d.get('wifi_password')
    sec = ('[wifi-security]\nkey-mgmt=wpa-psk\npsk=%s\n' % _nm_escape(pw)) if pw else ''
    nm = ('[connection]\nid=bob-wifi\nuuid=@UUID@\ntype=wifi\ninterface-name=wlan0\nautoconnect=true\n'
          '[wifi]\nmode=infrastructure\nssid=%s\n%s[ipv4]\nmethod=auto\n[ipv6]\nmethod=auto\n') % (_nm_escape(d['wifi_ssid']), sec)
    return '\n'.join([
        '#!/bin/bash',
        '# written by Bob Setup: runs once on first boot, then removes itself',
        'set +e',
        'BOOT=/boot/firmware; [ -d $BOOT ] || BOOT=/boot',
        'finish() { rm -f $BOOT/firstrun.sh; sed -i "s| systemd.run.*||g" $BOOT/cmdline.txt; exit 0; }',
        'command -v cloud-init >/dev/null && [ -e $BOOT/user-data ] && finish     # a cloud-init image sets itself up',
        'HOST=%s; USERN=%s; PASS=%s' % (q(d['hostname']), q(d['username']), q(d['password'])),
        'CUR=$(getent passwd 1000 | cut -d: -f1)',
        'if [ -n "$CUR" ] && [ "$CUR" != "$USERN" ]; then usermod -l "$USERN" "$CUR"; usermod -m -d "/home/$USERN" "$USERN"; groupmod -n "$USERN" "$CUR"; fi',
        'id "$USERN" >/dev/null 2>&1 || useradd -m -s /bin/bash "$USERN"',
        'for g in sudo adm users audio video plugdev input render netdev gpio i2c spi dialout; do getent group $g >/dev/null && usermod -aG $g "$USERN"; done',
        'echo "$USERN:$PASS" | chpasswd',
        '[ -x /usr/lib/userconf-pi/userconf ] && /usr/lib/userconf-pi/userconf "$USERN" "$(echo "$PASS" | openssl passwd -6 -stdin)"',
        'echo "$USERN ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/010_bob-user; chmod 440 /etc/sudoers.d/010_bob-user',
        'raspi-config nonint do_hostname "$HOST"',
        'systemctl enable ssh',
        'rfkill unblock wifi 2>/dev/null; for f in /var/lib/systemd/rfkill/*:wlan; do [ -e "$f" ] && echo 0 > "$f"; done',
        'raspi-config nonint do_wifi_country %s' % q(d.get('country', 'GB')),
        "cat > /etc/NetworkManager/system-connections/bob-wifi.nmconnection <<'NMEOF'",
        nm.rstrip('\n'),
        'NMEOF',
        'sed -i "s/@UUID@/$(cat /proc/sys/kernel/random/uuid)/" /etc/NetworkManager/system-connections/bob-wifi.nmconnection',
        'chmod 600 /etc/NetworkManager/system-connections/bob-wifi.nmconnection',
        'mkdir -p /etc/bob; mv $BOOT/bob-firstboot.json /etc/bob/firstboot.json; chmod 600 /etc/bob/firstboot.json',
        "cat > /usr/local/sbin/bob-firstboot.sh <<'FBEOF'",
        firstboot_script(d.get('country', 'GB')).rstrip('\n'),
        'FBEOF',
        'chmod 700 /usr/local/sbin/bob-firstboot.sh',
        "cat > /etc/systemd/system/bob-firstboot.service <<'SVCEOF'",
        FIRSTBOOT_SERVICE.rstrip('\n'),
        'SVCEOF',
        'systemctl enable bob-firstboot.service',
        'finish',
        '',
    ])


def _drive_info(root):
    """(is removable, volume label) for a Windows drive, without touching empty card readers."""
    import ctypes
    k32 = ctypes.windll.kernel32
    removable = k32.GetDriveTypeW(root) == 2
    label = ctypes.create_unicode_buffer(261)
    k32.GetVolumeInformationW(root, label, 261, None, None, None, None, 0)
    return removable, label.value


def is_boot_partition(root):
    return os.path.exists(os.path.join(root, 'config.txt')) and os.path.exists(os.path.join(root, 'cmdline.txt'))


def boot_drives(show_all=False):
    """Removable drives that look like a Raspberry Pi OS boot partition, as (root, label)."""
    if os.name != 'nt':
        return []
    import ctypes
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    out = []
    for i, letter in enumerate(string.ascii_uppercase):
        if not mask & (1 << i) or letter in 'AB':
            continue
        root = '%s:\\' % letter
        try:
            removable, label = _drive_info(root)
        except Exception:
            removable, label = False, ''
        if (removable or show_all) and is_boot_partition(root):
            out.append((root, label))
    return out


def uses_cloud_init(root):
    """Raspberry Pi OS images from October 2025 set themselves up with cloud-init; older ones use firstrun.sh."""
    if any(os.path.exists(os.path.join(root, n)) for n in ('user-data', 'meta-data', 'network-config')):
        return True
    try:
        with open(os.path.join(root, 'issue.txt'), encoding='utf-8', errors='replace') as f:
            m = re.search(r'(\d{4})-(\d{2})-(\d{2})', f.read())
        if m:
            return (int(m.group(1)), int(m.group(2))) >= (2025, 10)
    except OSError:
        pass
    return False


def _write(path, text):
    with open(path, 'w', newline='\n', encoding='utf-8') as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    with open(path, encoding='utf-8') as f:            # read it back: a card that lies about writes is caught here
        if f.read() != text:
            raise IOError('%s did not write correctly' % os.path.basename(path))


def prepare_card(root, d):
    """Write everything onto the boot partition at `root`. Returns a list of what was done."""
    if not is_boot_partition(root):
        raise IOError('%s is not a Raspberry Pi boot drive (no config.txt / cmdline.txt)' % root)
    done = []
    if uses_cloud_init(root):
        for name, text in cloud_init_files(d).items():
            path = os.path.join(root, name)
            if os.path.exists(path) and not os.path.exists(path + '.orig'):
                os.replace(path, path + '.orig')
            _write(path, text)
            done.append('wrote ' + name)
    else:
        _write(os.path.join(root, 'firstrun.sh'), firstrun_script(d))
        _write(os.path.join(root, 'bob-firstboot.json'), firstboot_json(d))
        cmd_path = os.path.join(root, 'cmdline.txt')
        with open(cmd_path, encoding='utf-8') as f:
            cmd = f.read().strip()
        cmd = re.sub(r' systemd\.run.*', '', cmd)
        cmd += ' systemd.run=/boot/firmware/firstrun.sh systemd.run_success_action=reboot systemd.unit=kernel-command-line.target'
        _write(cmd_path, cmd + '\n')
        done += ['wrote firstrun.sh', 'wrote bob-firstboot.json', 'updated cmdline.txt']
    _write(os.path.join(root, 'ssh'), '')
    done.append('enabled SSH')
    return done


def install_over_ssh(host, user, password, d, out, confirm_key=None):
    """Install Bob on a running Pi. `out(text)` receives progress lines; `confirm_key(fingerprint)` must return
    True to trust a Pi seen for the first time (defaults to trusting it)."""
    import paramiko

    class Ask(paramiko.MissingHostKeyPolicy):
        def missing_host_key(self, client, hostname, key):
            fp = ':'.join('%02x' % b for b in key.get_fingerprint())
            if confirm_key and not confirm_key('%s %s' % (key.get_name(), fp)):
                raise paramiko.SSHException('host key not accepted')

    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(Ask())
    out('Connecting to %s...' % host)
    cli.connect(host, username=user, password=password, timeout=15, banner_timeout=15, auth_timeout=15,
                look_for_keys=False, allow_agent=False)
    tag = secrets.token_hex(4)
    cfg, script = '/tmp/bob-firstboot-%s.json' % tag, '/tmp/bob-install-%s.sh' % tag
    try:
        sftp = cli.open_sftp()
        sftp.open(cfg, 'w').close()
        sftp.chmod(cfg, 0o600)                 # private before anything is written into it
        with sftp.file(cfg, 'w') as f:
            f.write(firstboot_json(d))
        sftp.close()
        _i, o, _e = cli.exec_command('curl -fsSL --retry 3 -o %s %s && echo OK' % (script, INSTALL_URL), timeout=120)
        if 'OK' not in o.read().decode():
            raise RuntimeError('the Pi could not download the installer (is it online?)')
        stdin, stdout, _stderr = cli.exec_command('sudo -S -p "" bash %s --config %s 2>&1' % (script, cfg))
        stdout.channel.settimeout(1800)
        stdin.write(password + '\n')
        stdin.flush()
        stdin.channel.shutdown_write()           # nothing else will ever be typed: nothing can wait for it
        for line in iter(stdout.readline, ''):
            out(line.rstrip())
        return stdout.channel.recv_exit_status()
    finally:
        try:
            cli.exec_command('rm -f %s %s' % (script, cfg))
        except Exception:
            pass
        cli.close()


# ---- the window ----------------------------------------------------------------------------------------

def gui():
    import queue
    import tkinter as tk
    from tkinter import messagebox, ttk

    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)        # crisp text on high-DPI screens
        except Exception:
            pass
    root = tk.Tk()
    root.title('Bob Setup')
    root.geometry('700x880')
    root.minsize(620, 780)
    try:
        ttk.Style().theme_use('vista' if os.name == 'nt' else 'clam')
    except tk.TclError:
        pass
    frm = ttk.Frame(root, padding=18)
    frm.pack(fill='both', expand=True)
    ttk.Label(frm, text='Bob Setup', font=('Segoe UI', 20, 'bold')).pack(anchor='w')
    ttk.Label(frm, text='A voice assistant for your Raspberry Pi that you build by talking to it.', foreground='#555').pack(anchor='w', pady=(0, 10))
    nb = ttk.Notebook(frm)
    nb.pack(fill='both', expand=True)
    v = {}

    def field(parent, label, key, default='', show=None, values=None, hint=None, editable=False):
        ttk.Label(parent, text=label).pack(anchor='w', pady=(8, 2))
        var = tk.StringVar(value=default)
        if values:
            w = ttk.Combobox(parent, textvariable=var, values=values, state='normal' if editable else 'readonly')
        else:
            w = ttk.Entry(parent, textvariable=var, show=show or '')
        w.pack(fill='x')
        if hint:
            ttk.Label(parent, text=hint, foreground='#777', font=('Segoe UI', 8)).pack(anchor='w')
        v[key] = var

    p1 = ttk.Frame(nb, padding=12)
    nb.add(p1, text='1. Your details')
    field(p1, 'Wi-Fi name', 'wifi_ssid', hint='Exactly as it appears on your phone (capitals matter). Not needed for option B.')
    field(p1, 'Wi-Fi password', 'wifi_password', show='•')
    field(p1, 'Wi-Fi country (two letters)', 'country', 'GB', values=COUNTRIES, editable=True)
    field(p1, 'Gemini API key', 'gemini_api_key', show='•', hint='Free from Google AI Studio. Bob uses it to talk.')
    ttk.Button(p1, text='Get a free Gemini key...', command=lambda: webbrowser.open(KEY_URL)).pack(anchor='w', pady=4)
    field(p1, "Password for Bob's settings page", 'web_password', show='•')

    p2 = ttk.Frame(nb, padding=12)
    nb.add(p2, text='2. The Pi')
    field(p2, 'Pi name on your network', 'hostname', 'bob', hint='Settings page: http://<name>.local:8080')
    field(p2, 'Pi user name', 'username', 'pi')
    field(p2, 'Pi password (for SSH)', 'password', show='•', hint='Option A: a new password. Option B: the one the Pi already has.')
    field(p2, 'Assistant name', 'name', 'Bob')
    field(p2, 'Wake phrase', 'wake_phrase', 'hey bob', hint='Common English words only (the offline recogniser must know them).')
    field(p2, 'Voice', 'voice', 'Charon', values=VOICES)
    field(p2, 'Your town (for the weather)', 'town', '')
    helper_on = tk.BooleanVar(value=False)                  # off by default: only the owner turns it on
    ttk.Checkbutton(p2, text='Help run KindleHub (optional)', variable=helper_on).pack(anchor='w', pady=(12, 0))
    ttk.Label(p2, text='Lends the Pi\'s spare time to KindleHub (free games for e-readers): it plays chess moves for '
                       'its computer opponent and nothing else, using at most half of one core at lowest priority. '
                       'Perks: link it to your KindleHub account for Plus features and your bug reports first. '
                       'Switch it off any time on Bob\'s settings page.', foreground='#777', font=('Segoe UI', 8),
              wraplength=600).pack(anchor='w')

    p3 = ttk.Frame(nb, padding=12)
    nb.add(p3, text='3. Set up')
    log = tk.Text(p3, height=14, wrap='word', font=('Consolas', 9))
    lines = queue.Queue()

    def out(text):
        lines.put(text)

    ui = {'progress': None}

    def pump():
        try:
            while True:
                log.insert('end', lines.get_nowait() + '\n')
                log.see('end')
        except queue.Empty:
            pass
        p = ui['progress']
        if p:
            stage, done, total = p
            bar['value'] = 1000 * done / max(total, 1)
            status.set('%s %.1f of %.1f GB' % ('Writing' if stage == 'write' else 'Checking', done / 1e9, total / 1e9))
        else:
            bar['value'] = 0
            status.set('')
        root.after(100, pump)

    def data():
        keep = ('password', 'wifi_password', 'web_password', 'wifi_ssid')     # never trim these
        d = {k: (var.get() if k in keep else var.get().strip()) for k, var in v.items()}
        d['kindlehub_helper'] = bool(helper_on.get())
        return d

    def check(validator):
        d = data()
        errs = validator(d)
        if errs:
            messagebox.showerror('Bob Setup', 'Please fix:\n\n- ' + '\n- '.join(errs))
            return None
        return d

    ttk.Label(p3, text='A. New Pi: write the microSD card', font=('Segoe UI', 11, 'bold')).pack(anchor='w')
    ttk.Label(p3, text='Put the card in this PC (a USB card reader is fine) and choose it. Bob Setup downloads Raspberry '
                       'Pi OS Lite (64-bit) from raspberrypi.com (about 550 MB), writes it, checks it and adds your '
                       'settings. Everything on the card is erased.', wraplength=600).pack(anchor='w')
    card = tk.StringVar()
    cards = {}
    row = ttk.Frame(p3)
    row.pack(fill='x', pady=4)
    cd = ttk.Combobox(row, textvariable=card, state='readonly', width=44)
    cd.pack(side='left')
    flash_btn = ttk.Button(row, text='Write card')
    prow = ttk.Frame(p3)
    prow.pack(fill='x')
    bar = ttk.Progressbar(prow, maximum=1000)
    bar.pack(side='left', fill='x', expand=True)
    cancel_btn = ttk.Button(prow, text='Cancel', state='disabled')
    cancel_btn.pack(side='left', padx=6)
    status = tk.StringVar()
    ttk.Label(p3, textvariable=status, foreground='#555').pack(anchor='w')
    stop = threading.Event()

    def refresh_cards():
        cards.clear()
        try:
            for dsk in flasher.choose_disks(flasher.list_disks()):
                cards[flasher.describe(dsk)] = dsk
        except Exception as e:
            out('Could not list the cards: %s' % e)
        cd['values'] = list(cards)
        card.set(next(iter(cards), ''))
        if not cards and os.name == 'nt':
            out('No microSD card found. Put one in (or plug in the card reader), then press Refresh.')

    def progress(stage, done, total):
        ui['progress'] = (stage, done, total)

    def confirm_erase(desc):
        top = tk.Toplevel(root)
        top.title('Erase this card?')
        top.transient(root)
        top.grab_set()
        ttk.Label(top, text='Everything on this card will be erased:\n\n%s\n\nType ERASE to continue.' % desc,
                  padding=14, wraplength=440).pack()
        typed = tk.StringVar()
        ent = ttk.Entry(top, textvariable=typed)
        ent.pack(padx=14, fill='x')
        ent.focus_set()
        res = {}

        def ok(_e=None):
            if typed.get().strip().upper() == 'ERASE':
                res['ok'] = True
                top.destroy()
            else:
                messagebox.showerror('Bob Setup', 'Type ERASE to erase the card, or press Cancel.', parent=top)
        btns = ttk.Frame(top, padding=14)
        btns.pack()
        ttk.Button(btns, text='Erase and write', command=ok).pack(side='left', padx=4)
        ttk.Button(btns, text='Cancel', command=top.destroy).pack(side='left', padx=4)
        ent.bind('<Return>', ok)
        root.wait_window(top)
        return res.get('ok', False)

    def busy(on):
        for b in (flash_btn, card_btn, ssh_btn):
            b.state(['disabled'] if on else ['!disabled'])
        cancel_btn.state(['!disabled'] if on else ['disabled'])
        if not on:
            ui['progress'] = None

    def do_flash():
        d = check(validate_card)
        if not d:
            return
        disk = cards.get(card.get())
        if not disk:
            messagebox.showerror('Bob Setup', 'Choose the microSD card (press Refresh after putting it in).')
            return
        if not flasher.is_admin():
            messagebox.showerror('Bob Setup', 'Writing a card needs administrator rights: close Bob Setup, right-click it '
                                              'and choose "Run as administrator".')
            return
        if not confirm_erase(flasher.describe(disk)):
            return
        stop.clear()
        busy(True)

        def work():
            try:
                out('Looking up the newest Raspberry Pi OS Lite (64-bit)...')
                img = flasher.latest_os()
                out('Using %s (released %s).' % (img['url'].rsplit('/', 1)[-1], img.get('release_date') or '?'))
                if int(disk['Size']) < int(img['extract_size']):
                    raise IOError('the card is too small for the image')
                out('Clearing the card...')
                flasher.clear_disk(disk)
                out('Downloading and writing (10-20 minutes)...')
                target = flasher.DiskTarget(disk['Number'])
                try:
                    flasher.write_image(flasher.http_opener(img['url']), target, int(img['extract_size']),
                                        img['extract_sha256'], progress, stop.is_set)
                finally:
                    target.close()
                out('Written and checked. Adding your settings...')
                boot = flasher.boot_drive_of(disk['Number'])
                for line in prepare_card(boot, d):
                    out(line)
                ejected = flasher.eject(boot)
                out('\nDone. ' + ('The card was ejected: you can take it out.' if ejected else
                                  'In File Explorer, use "Eject" on the bootfs drive before taking the card out.'))
                out('Put it in the Pi with the USB speakerphone plugged in, and switch on.')
                out('Give it 10-20 minutes the first time; a rising three-note chime means Bob is ready. Then say "%s".' % d['wake_phrase'])
                out('Settings: http://%s.local:8080 (or the Pi\'s IP address from your router).' % d['hostname'])
            except flasher.Cancelled:
                out('Stopped. The card is blank now: write it again before using it.')
            except Exception as e:
                out('Could not write the card: %s' % e)
            finally:
                root.after(0, lambda: busy(False))
        threading.Thread(target=work, daemon=True).start()
    ttk.Button(row, text='Refresh', command=refresh_cards).pack(side='left', padx=6)
    flash_btn.pack(side='left')
    flash_btn.configure(command=do_flash)
    cancel_btn.configure(command=stop.set)

    ttk.Label(p3, text='Already flashed it with Raspberry Pi Imager (no customisation)? Choose its bootfs drive:',
              wraplength=600).pack(anchor='w', pady=(10, 0))
    drive = tk.StringVar()
    drives = {}
    row = ttk.Frame(p3)
    row.pack(fill='x', pady=4)
    dd = ttk.Combobox(row, textvariable=drive, state='readonly', width=22)
    dd.pack(side='left')
    show_all = tk.BooleanVar(value=False)

    def refresh():
        drives.clear()
        for r, label in boot_drives(show_all.get()):
            drives['%s  %s' % (r, label or '')] = r
        dd['values'] = list(drives)
        drive.set(next(iter(drives), ''))
    ttk.Button(row, text='Refresh', command=refresh).pack(side='left', padx=6)
    card_btn = ttk.Button(row, text='Save settings only')
    card_btn.pack(side='left')
    ttk.Checkbutton(row, text='Show non-removable drives', variable=show_all, command=refresh).pack(side='left', padx=6)

    def do_card():
        d = check(validate_card)
        if not d:
            return
        r = drives.get(drive.get())
        if not r or not is_boot_partition(r):
            messagebox.showerror('Bob Setup', 'Pick the SD card\'s boot drive (press Refresh if you swapped cards).')
            return
        card_btn.state(['disabled'])
        try:
            for line in prepare_card(r, d):
                out(line)
            out('\nDone. In Windows, use "Eject" on the bootfs drive before unplugging the card.')
            out('Put it in the Pi with the USB speakerphone plugged in, and switch on.')
            out('Give it 10-20 minutes the first time; a rising three-note chime means Bob is ready. Then say "%s".' % d['wake_phrase'])
            out('Settings: http://%s.local:8080 (or the Pi\'s IP address from your router).' % d['hostname'])
        except Exception as e:
            messagebox.showerror('Bob Setup', 'Could not write the card: %s' % e)
        finally:
            card_btn.state(['!disabled'])
    card_btn.configure(command=do_card)

    ttk.Separator(p3).pack(fill='x', pady=10)
    ttk.Label(p3, text='B. Pi already running: install over the network', font=('Segoe UI', 11, 'bold')).pack(anchor='w')
    host = tk.StringVar(value='bob.local')
    hrow = ttk.Frame(p3)
    hrow.pack(fill='x', pady=4)
    ttk.Label(hrow, text='Pi address').pack(side='left')
    ttk.Entry(hrow, textvariable=host, width=22).pack(side='left', padx=6)
    ssh_btn = ttk.Button(hrow, text='Install')
    ssh_btn.pack(side='left')

    def confirm_key(fp):
        """Asked from the worker thread; answered on the window's thread."""
        ans, ev = {}, threading.Event()

        def ask():
            ans['ok'] = messagebox.askyesno('Bob Setup', 'First time connecting to this Pi.\nIts key is:\n\n%s\n\nConnect?' % fp)
            ev.set()
        root.after(0, ask)
        ev.wait()
        return ans.get('ok', False)

    def do_ssh():
        d = check(validate_ssh)
        if not d:
            return
        ssh_btn.state(['disabled'])

        def work():
            try:
                code = install_over_ssh(host.get().strip(), d['username'], d['password'], d, out, confirm_key)
                if code == 0:
                    out('\nBob is installed. Say "%s". Settings: http://%s:8080' % (d['wake_phrase'], host.get().strip()))
                else:
                    out('\nThe installer stopped (code %s). The lines above say why.' % code)
            except Exception as e:
                out('Could not install: %s' % e)
            finally:
                root.after(0, lambda: ssh_btn.state(['!disabled']))
        threading.Thread(target=work, daemon=True).start()
    ssh_btn.configure(command=do_ssh)
    log.pack(fill='both', expand=True, pady=(10, 0))
    refresh()
    refresh_cards()
    pump()
    root.mainloop()


DEMO = {'hostname': 'bob', 'username': 'pi', 'password': 'pa"ss word1', 'wifi_ssid': 'Home "Net"',
        'wifi_password': 'secret123', 'country': 'GB', 'gemini_api_key': 'AIza-demo', 'web_password': 'webpass',
        'name': 'Bob', 'wake_phrase': 'hey bob', 'town': 'London', 'voice': 'Charon'}


if __name__ == '__main__':
    if '--print' in sys.argv:                 # show what would be written
        for n, t in cloud_init_files(DEMO).items():
            print('==== %s\n%s' % (n, t))
        print('==== firstrun.sh\n' + firstrun_script(DEMO))
    elif '--selftest' in sys.argv:            # the release build runs this: writes a report next to the exe
        import tempfile
        ok = True
        try:
            assert validate_card(DEMO) == [] and validate_ssh(DEMO) == []
            tmp = tempfile.mkdtemp()
            for n in ('config.txt', 'cmdline.txt'):
                open(os.path.join(tmp, n), 'w').write('console=tty1 rootwait\n' if n == 'cmdline.txt' else '')
            prepare_card(tmp, DEMO)
            import importlib.util
            import hashlib
            import lzma
            img = os.urandom(3 << 20)                     # a small "image": the writer's whole path, minus the card
            xz = lzma.compress(img)
            target = flasher.FileTarget(os.path.join(tmp, 'card.img'))
            flasher.write_image(lambda off: __import__('io').BytesIO(xz[off:]), target, len(img), hashlib.sha256(img).hexdigest())
            target.close()
            assert open(os.path.join(tmp, 'card.img'), 'rb').read()[:len(img)] == img
            assert importlib.util.find_spec('paramiko'), 'paramiko is not bundled'
        except Exception as e:
            ok = False
            msg = repr(e)
        with open(os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), 'selftest.txt'), 'w') as f:
            f.write('ok\n' if ok else 'FAILED %s\n' % msg)
        sys.exit(0 if ok else 1)
    else:
        gui()
