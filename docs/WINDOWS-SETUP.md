# Setting up with Bob-Setup.exe (Windows)

## You need
- The SD card in your PC (a USB card reader is fine)
- [Raspberry Pi Imager](https://www.raspberrypi.com/software/) and **Bob-Setup.exe** from the
  [latest release](https://github.com/arancool3000/bob-assistant/releases/latest) (a `.sha256` checksum is next to it)
- Your Wi-Fi name and password (an ordinary home network: not hidden, not "enterprise" or a hotel login page),
  and a free [Gemini API key](https://aistudio.google.com/apikey)

## Steps
1. **Imager:** choose your Pi, then **Raspberry Pi OS (other) → Raspberry Pi OS Lite (64-bit)**, then your card.
   When Imager offers OS customisation (settings for user, Wi-Fi…), **skip it**: Bob Setup does that. Write.
2. Windows may say the card needs formatting: **click Cancel, never format it**. Imager ejects the card when it
   finishes, so **unplug it and plug it back in** until a drive called **bootfs** appears.
3. **Bob-Setup.exe:** Windows SmartScreen may warn about an unknown app (it isn't code-signed yet):
   *More info → Run anyway*.
   - *1. Your details:* Wi-Fi name and password, country, Gemini key, a password for Bob's settings page.
   - *2. The Pi:* its network name (`bob`), a new user name and password for the Pi itself, Bob's name, wake
     phrase (common English words), voice, your town.
   - *3. Set up:* pick the **bootfs** drive (press *Refresh* if it's not listed) → **Save to SD card**.
4. In Windows, right-click **bootfs → Eject**, then take the card out. Put it in the Pi, connect the USB
   speakerphone, and power on.
5. Wait **10–20 minutes** the first time: the Pi joins your Wi-Fi, sets its clock, installs Bob, and plays a
   **rising three-note chime** when he's ready. Say **"Hey Bob"**.
6. Settings: `http://bob.local:8080` (use your Pi's name). If `.local` names don't work on your phone, use the
   Pi's IP address from your router's device list: `http://192.168.x.y:8080`.

## Installing on a Pi that's already running
Tab *3. Set up* → **B**: type the Pi's address (`bob.local` or its IP). On tab 2 enter the user name and password
the Pi **already** has (Wi-Fi fields aren't needed). The first time, the app shows the Pi's key fingerprint and
asks you to confirm. Progress shows in the window; the settings page address is printed at the end.

## Sounds Bob makes
| Sound | Meaning |
|---|---|
| Rising three notes | Ready (after starting up or installing) |
| Two quick notes | Listening — talk now |
| Low falling two notes | That didn't work (no internet, or a key problem) |
| Three high notes, three times | A timer finished |

Bob can't be interrupted while he is speaking (the mic is muted so he never hears himself): let him finish.

## If something goes wrong
| Problem | Fix |
|---|---|
| No chime after 25 minutes, no settings page | Probably Wi-Fi: check the name/password (capitals matter) and country. Re-flash the card with Imager and run Bob Setup again. |
| Settings page opens but Bob is silent | No sound device at install time. Plug in the speakerphone and run the installer again (see *Sound* in HARDWARE.md). |
| See whether it is still installing | SSH in (`ssh pi@bob.local` in PowerShell, with your user name) and run `tail -f /var/log/bob-install.log`. Also `/var/log/cloud-init-output.log`. |
| `bob.local` doesn't open | Use the Pi's IP address from your router: `http://192.168.x.y:8080`. |
| He doesn't hear you | Settings page → Wake sensitivity up a little; speak towards the speakerphone. |
| He hears you but plays the low tone | Check the Gemini key on the settings page. |
| What happened? | `sudo journalctl -u bob-assistant -n 50` |
