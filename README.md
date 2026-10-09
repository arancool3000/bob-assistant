# Bob

**A voice assistant for the Raspberry Pi that you build by talking to it.**

Bob starts with no devices at all. You say *"Hey Bob"*, ask him things, and when you want him to do something
new — switch an LED, read a sensor, press a relay, call a web service — you wire it up and **tell him**:

> "Hey Bob, I've connected an LED to GPIO 17. Make a skill that turns it on and off."

Bob writes the skill, reads back what it will do, installs it only after you say *yes*, tests it, and from then
on it's one of his abilities.

- **Talks naturally** — Google's Gemini Live voice: quick, with eight voices to choose from, and he can search the web.
- **Wakes on the Pi** — *"Hey Bob"* is heard offline; nothing leaves the Pi until then.
- **Builds himself** — skills for anything on the GPIO pins, I²C, serial or the web.
- **Updates himself** — new releases install automatically, and roll back if they fail.
- **Has a settings page** — `http://bob.local:8080`, from any phone or computer at home.
- **Has a Windows setup app** — Wi-Fi, password and key, without typing a single command.

---

## What you need

| | |
|---|---|
| **Raspberry Pi** | **Pi 5 (4 GB or 8 GB)** recommended. Pi 4 (2 GB+) works. Pi 3 / Zero are too slow. |
| **microSD card** | 16 GB or bigger, A1/A2 rated, plus a USB card reader if your PC has no slot |
| **Power supply** | The official one for your Pi (27 W USB-C for a Pi 5). A case with a fan or heatsink for a Pi 5. |
| **Microphone + speaker** | **A USB speakerphone** is easiest (one plug, built-in echo cancelling), e.g. any "USB conference speaker". A USB mic plus a USB or 3.5 mm speaker (Pi 4) also works. *The Pi 5 has no headphone socket.* |
| **Network** | Wi-Fi (an ordinary home network) or Ethernet, with internet |
| **A Gemini API key** | Free from [Google AI Studio](https://aistudio.google.com/apikey). The free tier has usage limits, and Google may use free-tier data to improve its products — see [Privacy](docs/PRIVACY-SECURITY.md). |
| **A computer** | Windows for the setup app — or any computer for the manual install. |

Optional, for building things: a breadboard, jumper wires, LEDs + 330 Ω resistors, buttons, a relay module,
sensors (DHT22, BME280…), servos with their own power supply. See [docs/HARDWARE.md](docs/HARDWARE.md).

## Set up (Windows, no commands)

1. Download **Bob-Setup.exe** from the [latest release](https://github.com/arancool3000/bob-assistant/releases/latest)
   and put the microSD card in your PC (a USB card reader is fine).
2. Open **Bob-Setup.exe** (allow it to make changes: it writes the card). Fill in your Wi-Fi, a Pi password,
   your Gemini key, and a password for Bob's settings page.
3. On the last tab choose the card and press **Write card**, then type ERASE. The app downloads Raspberry Pi OS
   Lite (64-bit) from raspberrypi.com, writes and checks it, adds your settings and ejects the card
   (10–20 minutes). No Raspberry Pi Imager needed.
4. Put the card in the Pi, plug in the speakerphone, then power. The first start installs Bob by itself
   (**10–20 minutes**). A rising three-note chime means he's ready.
5. Say **"Hey Bob"**. Settings: `http://bob.local:8080` (or the Pi's IP address from your router).

Full walkthrough with troubleshooting: [docs/WINDOWS-SETUP.md](docs/WINDOWS-SETUP.md).

Optional, off by default: let your Pi [help run KindleHub](docs/HELP-RUN-KINDLEHUB.md) (chess moves only) and get
KindleTube and KindlePoki as on KindleHub Plus.

## Set up (Mac, Linux, or a Pi you already have)

Flash **Raspberry Pi OS Lite (64-bit)** with Raspberry Pi Imager and use its own settings for Wi-Fi, user and
SSH. Then, on the Pi (over SSH):

```bash
curl -fsSL https://raw.githubusercontent.com/arancool3000/bob-assistant/main/install.sh | sudo bash
```

The installer ends by printing a **setup code**. Open `http://<your-pi-name>.local:8080` (or `http://<its IP>:8080`),
enter the code, choose a password and paste your Gemini key. Say "Hey Bob".

## Things to say

- "What's the weather tomorrow?" · "Set a timer for 10 minutes" · "Remember the spare key is in the blue box"
- "What's in the news?" (Bob can search the web) · "How hot is the Pi running?"
- "I've wired a button to GPIO 27 — make a skill that tells me if it's pressed." · "Make a skill that switches the relay on GPIO 22."
- "What skills do you have?" · "Show me the code for the desk LED skill." · "Delete the test skill."
- "Goodbye" ends the conversation (or just stop talking).

How skills work, with examples: [docs/SKILLS.md](docs/SKILLS.md).

## Updates

Every Pi checks GitHub once a day and installs the newest **tagged release** by itself (the updater runs as root).
If Bob doesn't start on the new version, it goes straight back to the one that worked and skips that release.
Turn it off, or check now, on the settings page.
Details: [docs/UPDATES.md](docs/UPDATES.md).

## Privacy and safety

- The wake word is recognised **on the Pi**. Audio goes to Google only *during* a conversation, using **your**
  API key under Google's terms.
- Conversations are **not recorded** on the Pi.
- Skills run as their own user (`bob-skill`): they can use the GPIO, I²C, SPI and serial ports, but can't read
  Bob's settings or API key. Installing one needs your yes, enforced in code; read or delete any of them on the
  settings page.
- The settings page is for your home network only. Do not forward port 8080 to the internet.

More: [docs/PRIVACY-SECURITY.md](docs/PRIVACY-SECURITY.md).

## How it fits together

```
 microphone ──► wake word (Vosk, offline) ──"hey bob"──► Gemini Live (voice in, voice out) ──► speaker
                                                               │ tool calls
                                       built-in tools ◄────────┴────────► your skills (one Python file each)
                                       time · weather · timers · notes · volume · Pi status · build skills
```

| Path | What |
|---|---|
| `/opt/bob-assistant` | the code (a git checkout of the release in use) |
| `/etc/bob/config.json` | settings |
| `/etc/bob/secrets.json` | Gemini key and settings-page password hash (readable only by Bob) |
| `/var/lib/bob-assistant-skills/` | the skills Bob has made |
| Services | `bob-assistant` (voice), `bob-assistant-skills` (runs skills as `bob-skill`), `bob-assistant-web` (settings page), `bob-assistant-update.timer` |

Logs: `journalctl -u bob-assistant -f`

## Contributing

Issues and pull requests are welcome. Run `python -m pytest tests` before sending one. Releases are tags
(`v0.2.0`), and every tag reaches every Pi, so keep `main` working.

MIT licence.
