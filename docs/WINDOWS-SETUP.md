# Setting up with Kevin-Setup.exe (Windows)

## You need
- The microSD card in your PC (a USB card reader is fine). Everything on it will be erased.
- **Kevin-Setup.exe** from the [latest release](https://github.com/arancool3000/kevin-assistant/releases/latest)
  (a `.sha256` checksum is next to it)
- Your Wi-Fi name and password (an ordinary home network: not hidden, not "enterprise" or a hotel login page),
  and a free [Gemini API key](https://aistudio.google.com/apikey)
- About 600 MB of internet download (the Raspberry Pi OS image; nothing is kept on your PC afterwards)

## Steps
1. Open **Kevin-Setup.exe**. Windows SmartScreen may warn about an unknown app (it isn't code-signed yet):
   *More info → Run anyway*. Then say **Yes** when Windows asks to let it make changes: writing a card needs
   administrator rights.
   - *1. Your details:* Wi-Fi name and password, country, Gemini key, a password for Kevin's settings page.
   - *2. The Pi:* its network name (`kevin`), a new user name and password for the Pi itself, Kevin's name, wake
     phrase (common English words), voice, your town.
   - *3. Set up* → **A**: choose your card (press *Refresh* after putting it in) → **Write card** → type
     **ERASE**.
2. The app downloads **Raspberry Pi OS Lite (64-bit)** straight from raspberrypi.com, writes it, reads the whole
   card back to check it, adds your settings, and ejects the card. Only SD cards and USB card readers from
   4 GB to 512 GB are offered; the disk Windows runs from never is. If Windows pops up "you need to format the
   disk", click **Cancel**, never format it.
3. Take the card out, put it in the Pi, connect the USB speakerphone, and power on.

Already flashed the card with Raspberry Pi Imager (with *no* customisation)? Under the card list, pick its
**bootfs** drive and press **Save settings only**.

4. Wait **10–20 minutes** the first time: the Pi joins your Wi-Fi, sets its clock, installs Kevin, and plays a
   **rising three-note chime** when he's ready. Say **"Hey Kevin"**.
5. Settings: `http://kevin.local:8080` (use your Pi's name). If `.local` names don't work on your phone, use the
   Pi's IP address from your router's device list: `http://192.168.x.y:8080`.

## Installing on a Pi that's already running
Tab *3. Set up* → **B**: type the Pi's address (`kevin.local` or its IP). On tab 2 enter the user name and password
the Pi **already** has (Wi-Fi fields aren't needed). The first time, the app shows the Pi's key fingerprint and
asks you to confirm. Progress shows in the window; the settings page address is printed at the end.

## Sounds Kevin makes
| Sound | Meaning |
|---|---|
| Rising three notes | Ready (after starting up or installing) |
| Two quick notes | Listening — talk now |
| Low falling two notes | That didn't work (no internet, or a key problem) |
| Three high notes, three times | A timer finished |

Kevin can't be interrupted while he is speaking (the mic is muted so he never hears himself): let him finish.

## If something goes wrong
| Problem | Fix |
|---|---|
| No chime after 25 minutes, no settings page | Probably Wi-Fi: check the name/password (capitals matter) and country. Run Kevin Setup again and write the card again. |
| Settings page opens but Kevin is silent | No sound device at install time. Plug in the speakerphone and run the installer again (see *Sound* in HARDWARE.md). |
| See whether it is still installing | SSH in (`ssh pi@kevin.local` in PowerShell, with your user name) and run `tail -f /var/log/bob-install.log`. Also `/var/log/cloud-init-output.log`. |
| `kevin.local` doesn't open | Use the Pi's IP address from your router: `http://192.168.x.y:8080`. |
| He doesn't hear you | Settings page → Wake sensitivity up a little; speak towards the speakerphone. |
| He hears you but plays the low tone | Check the Gemini key on the settings page. |
| "Run as administrator" message | Close the app, right-click **Kevin-Setup.exe** → *Run as administrator*. |
| "the card did not read back what was written" | The card is faulty or fake (common with very cheap cards): use another, ideally a known brand. |
| What happened? | `sudo journalctl -u bob-assistant -n 50` |
