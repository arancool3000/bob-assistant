# Privacy and security

**What leaves the Pi:** nothing until the wake phrase is heard (Vosk recognises it offline). During a
conversation your microphone audio and Bob's answers go to Google's Gemini Live API under **your** API key. On
Google's free tier, Google may use what you send to improve its products; on a paid tier it does not. Read
Google's current [Gemini API terms](https://ai.google.dev/gemini-api/terms) and check availability and limits
in your country. Weather lookups go to Open-Meteo (your town only). Updates come from this repository.

**What is kept:** settings, facts you asked Bob to remember (`/var/lib/bob-assistant/notes.json`), skills, and
logs of which tools ran. Conversations are not recorded.

**Secrets:** the Gemini key and the settings-page password hash are in `/etc/bob/secrets.json`, readable only by
the `bob-assistant` user. The setup app's first-boot files on the SD card (which hold the passwords, the key and
the Wi-Fi password) are wiped by the installer once it has used them.

**Isolation:** Bob runs as `bob-assistant`, skills as `bob-skill` (no access to Bob's files or key), the updater
as root in its own folder that only root can write. The settings page may only restart Bob and start an update check (one
sudoers line). Installing or deleting a skill needs your confirmed yes. The Pi user created by the setup app can
use `sudo` without a password (as Raspberry Pi Imager's users can); change that with `sudo visudo` if you prefer.

**The settings page** is plain HTTP on your home network, protected by your password (PBKDF2; at most 10 wrong
passwords per 10 minutes). On a manual install it asks for a one-time setup code (printed at the end of the
install) before the first password, so nobody else on your network can claim it. It only answers to the Pi's
own name and addresses. Do not expose port 8080 to the internet; for remote access use a VPN such as Tailscale (open the Pi by its
Tailscale IP address: the page only answers to the Pi's own names and addresses).

**Report a problem:** open a GitHub issue, or for anything sensitive use GitHub's private vulnerability reporting.

## Help run KindleHub
Off by default. When switched on, the Pi connects out to kindlehub.pro and plays chess moves, nothing else, as its
own user with no access to Bob's files or key. Details: [HELP-RUN-KINDLEHUB.md](HELP-RUN-KINDLEHUB.md).
