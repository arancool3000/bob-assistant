#!/bin/bash
# Bob installer for Raspberry Pi OS (64-bit, Bookworm or newer).
#
#   curl -fsSL https://raw.githubusercontent.com/arancool3000/bob-assistant/main/install.sh | sudo bash
#
# Options:
#   --config FILE   a first-boot settings file (written by the Windows setup app): API key, password, name...
#   --branch NAME   install a branch instead of the newest release (for development)
#   --upgrade       used by the updater: bring packages, users, services and permissions in line with the
#                   release already checked out in /opt/bob-assistant (no download, keeps everything)
#
# Safe to run again: it updates what is there and keeps your settings and skills.
set -euo pipefail

REPO="https://github.com/arancool3000/bob-assistant"
HOME_DIR=/opt/bob-assistant
STATE=/var/lib/bob-assistant
SKILLS=/var/lib/bob-assistant-skills
SKILL_DATA=/var/lib/bob-skill
UPD=/var/lib/bob-assistant-update
ETC=/etc/bob
USER_NAME=bob-assistant
SKILL_USER=bob-skill
VOSK_URL="https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
CONFIG_FILE=""
BRANCH=""
UPGRADE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --config) [ $# -ge 2 ] || { echo "--config needs a file"; exit 2; }; CONFIG_FILE="$2"; shift 2 ;;
    --branch) [ $# -ge 2 ] || { echo "--branch needs a name"; exit 2; }; BRANCH="$2"; shift 2 ;;
    --upgrade) UPGRADE=1; shift ;;
    *) echo "unknown option $1"; exit 2 ;;
  esac
done

say() { printf '\n\033[1;35m== %s\033[0m\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "Run me with sudo."; exit 1; }
[ "$(uname -m)" = "aarch64" ] || echo "Warning: this is $(uname -m); Bob is tested on 64-bit Raspberry Pi OS (aarch64)."
export DEBIAN_FRONTEND=noninteractive
APT="apt-get -o DPkg::Lock::Timeout=300 -qq"

newest_release() {   # the same rule the updater uses: vX.Y.Z only, highest version
  git -C "$HOME_DIR" tag --list 'v*' | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -1
}

say "System packages"
$APT update
$APT install -y git python3-venv python3-pip alsa-utils unzip curl avahi-daemon \
  python3-gpiozero python3-lgpio python3-serial python3-smbus2 i2c-tools >/dev/null
systemctl enable --now avahi-daemon >/dev/null 2>&1 || true
if command -v raspi-config >/dev/null; then                 # I2C and SPI on, for sensors (applies after a reboot)
  raspi-config nonint do_i2c 0 || true
  raspi-config nonint do_spi 0 || true
fi

say "Users"
id "$USER_NAME" >/dev/null 2>&1 || useradd --system --home-dir "$STATE" --shell /usr/sbin/nologin "$USER_NAME"
id "$SKILL_USER" >/dev/null 2>&1 || useradd --system --home-dir "$SKILL_DATA" --shell /usr/sbin/nologin "$SKILL_USER"
getent group audio >/dev/null && usermod -aG audio "$USER_NAME"
for g in gpio i2c spi dialout video; do getent group "$g" >/dev/null && usermod -aG "$g" "$SKILL_USER"; done
usermod -aG "$SKILL_USER" "$USER_NAME"                       # Bob may talk to the skill host's socket

if [ "$UPGRADE" -eq 0 ]; then
  say "Getting Bob"
  if [ -d "$HOME_DIR/.git" ]; then
    git -C "$HOME_DIR" fetch --tags --quiet origin
  else
    git clone --quiet "$REPO" "$HOME_DIR"
  fi
  if [ -n "$BRANCH" ]; then
    git -C "$HOME_DIR" checkout --quiet --force "$BRANCH"
    git -C "$HOME_DIR" pull --quiet --ff-only origin "$BRANCH" || true
  else
    TAG=$(newest_release || true)
    if [ -n "$TAG" ]; then git -C "$HOME_DIR" checkout --quiet --force "$TAG"; fi
  fi
fi
echo "Version: $(cat "$HOME_DIR/VERSION")"

say "Python environment"
[ -d "$HOME_DIR/venv" ] || python3 -m venv --system-site-packages "$HOME_DIR/venv"
"$HOME_DIR/venv/bin/pip" install -q --upgrade pip
"$HOME_DIR/venv/bin/pip" install -q -r "$HOME_DIR/requirements.txt"

say "Folders"
install -d -m 750 -o "$USER_NAME" -g "$USER_NAME" "$STATE" "$ETC"
install -d -m 2750 -o "$USER_NAME" -g "$SKILL_USER" "$SKILLS"          # Bob writes skills, the host reads them
install -d -m 750 -o "$SKILL_USER" -g "$SKILL_USER" "$SKILL_DATA"
install -d -m 755 -o root -g root "$UPD"                                # the updater's own, root only

if [ ! -d "$STATE/vosk-model" ]; then
  say "Wake word model (offline, about 40 MB)"
  TMP=$(mktemp -d)
  curl -fsSL --retry 3 -o "$TMP/m.zip" "$VOSK_URL"
  unzip -q "$TMP/m.zip" -d "$TMP"
  mv "$TMP"/vosk-model-small-en-us-* "$STATE/vosk-model"
  rm -rf "$TMP"
  chown -R "$USER_NAME:$USER_NAME" "$STATE/vosk-model"
fi

say "Sound"
# The Pi 5 has no headphone socket: use the first USB sound device (a USB speakerphone is easiest). Only a file
# Bob wrote is ever replaced; delete /etc/asound.conf's first line to keep your own.
MARK="# written by bob-assistant install.sh"
USB_ID=""
for d in /proc/asound/card*; do
  if [ -e "$d/usbid" ]; then USB_ID=$(cat "$d/id"); break; fi
done
if [ -n "$USB_ID" ] && { [ ! -e /etc/asound.conf ] || head -1 /etc/asound.conf | grep -qF "$MARK"; }; then
  cat > /etc/asound.conf <<EOF
$MARK
pcm.!default { type asym playback.pcm "plughw:CARD=$USB_ID,DEV=0" capture.pcm "plughw:CARD=$USB_ID,DEV=0" }
ctl.!default { type hw card "$USB_ID" }
EOF
  echo "Using the USB sound device \"$USB_ID\" for the microphone and speaker."
elif [ -z "$USB_ID" ]; then
  echo "No USB sound device found: plug one in and run this installer again (or set the devices on the settings page)."
fi

say "Settings"
# Run as Bob's own user, so nothing in his folders can trick root into writing elsewhere.
if [ -n "$CONFIG_FILE" ] && [ -f "$CONFIG_FILE" ]; then
  install -m 600 -o "$USER_NAME" -g "$USER_NAME" "$CONFIG_FILE" "$STATE/.firstboot.json"
  shred -u "$CONFIG_FILE" 2>/dev/null || rm -f "$CONFIG_FILE"        # it held the API key and passwords in plain text
fi
runuser -u "$USER_NAME" -- env BOB_ETC="$ETC" BOB_STATE="$STATE" "$HOME_DIR/venv/bin/python" - <<'PYEOF'
import json, os, sys
sys.path.insert(0, '/opt/bob-assistant')
from bob import config
cfg = config.load()
sec = {}
try:
    with open(config.SECRETS_FILE) as f:
        sec = json.load(f)
except (OSError, ValueError):
    pass
first_path = os.path.join(config.STATE, '.firstboot.json')
if os.path.exists(first_path):
    with open(first_path) as f:
        first = json.load(f)
    for k in ('name', 'wake_phrase', 'voice', 'town', 'personality', 'language'):
        if first.get(k):
            cfg[k] = first[k]
    if first.get('gemini_api_key'):
        sec['gemini_api_key'] = first['gemini_api_key'].strip()
    if first.get('web_password'):
        sec['web_password'] = config.hash_password(first['web_password'])
    os.remove(first_path)
if cfg.get('town') and cfg.get('latitude') is None:
    try:
        from bob import tools
        lat, lon, tz, _ = tools.geocode(cfg['town'])
        cfg.update(latitude=lat, longitude=lon, timezone=tz)
    except Exception as e:
        print('town not found (%s): set it on the settings page later' % e)
config.save(cfg)
config.save_secrets({'gemini_api_key': sec.get('gemini_api_key', ''), 'web_password': sec.get('web_password', '')})
print('settings saved')
PYEOF
TZ_NAME=$(runuser -u "$USER_NAME" -- "$HOME_DIR/venv/bin/python" -c "import json;print(json.load(open('$ETC/config.json')).get('timezone') or '')" 2>/dev/null || true)
if [ -n "$TZ_NAME" ] && [ -e "/usr/share/zoneinfo/$TZ_NAME" ]; then timedatectl set-timezone "$TZ_NAME" || true; fi
HAS_PW=$(runuser -u "$USER_NAME" -- "$HOME_DIR/venv/bin/python" -c "import json;print('yes' if json.load(open('$ETC/secrets.json')).get('web_password') else 'no')" 2>/dev/null || echo no)
SETUP_CODE=""
if [ "$HAS_PW" != "yes" ]; then                             # the settings page asks for this before the first password
  if [ -f "$ETC/setup-code" ]; then
    SETUP_CODE=$(cat "$ETC/setup-code")                      # keep the code already shown
  else
    SETUP_CODE=$(tr -dc 'A-HJ-NP-Z2-9' </dev/urandom | head -c 8)
    runuser -u "$USER_NAME" -- sh -c "umask 077; printf '%s\n' '$SETUP_CODE' > '$ETC/setup-code'"
  fi
fi

say "Services"
SUDOERS_TMP=$(mktemp)
cat > "$SUDOERS_TMP" <<EOF
# the settings page may restart Bob and start an update check, nothing else
$USER_NAME ALL=(root) NOPASSWD: /usr/bin/systemctl restart bob-assistant, /usr/bin/systemctl start --no-block bob-assistant-update-now.service
EOF
visudo -cf "$SUDOERS_TMP" >/dev/null
install -m 440 -o root -g root "$SUDOERS_TMP" /etc/sudoers.d/bob-assistant
rm -f "$SUDOERS_TMP"
install -m 644 "$HOME_DIR"/systemd/*.service "$HOME_DIR"/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable bob-assistant-skills.service bob-assistant.service bob-assistant-web.service bob-assistant-update.timer >/dev/null
if [ "$UPGRADE" -eq 0 ]; then
  systemctl restart bob-assistant-skills.service bob-assistant-web.service bob-assistant.service
  systemctl start bob-assistant-update.timer
fi

# The setup app's first-boot files on the SD card held passwords and the key: wipe them now they have been used.
BOOT=/boot/firmware; [ -d "$BOOT" ] || BOOT=/boot
if [ -f "$BOOT/user-data" ] && grep -q "written by Bob Setup" "$BOOT/user-data"; then
  printf '#cloud-config\n# Bob Setup: used on first boot and cleared (the settings now live on the Pi itself)\n' > "$BOOT/user-data"
  printf 'network:\n  version: 2\n' > "$BOOT/network-config"
  rm -f "$BOOT/user-data.orig" "$BOOT/network-config.orig" "$BOOT/meta-data.orig"
fi

IP=$(hostname -I | awk '{print $1}')
say "Done"
if [ "$UPGRADE" -eq 0 ]; then
  echo "Bob is running. Say \"hey bob\"."
  echo "Settings page: http://$(hostname).local:8080  (or http://$IP:8080)"
  if [ -n "$SETUP_CODE" ]; then echo "Setup code for the settings page: $SETUP_CODE"; fi
  echo "Updates install themselves from new releases; turn that off on the settings page."
  echo "I2C and SPI are switched on: reboot once (sudo reboot) before using sensors that need them."
fi
