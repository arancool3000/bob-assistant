# Hardware

## The Pi

| Model | Works? | Notes |
|---|---|---|
| Raspberry Pi 5, 4 GB / 8 GB | **Best** | Needs a USB sound device (no headphone socket). Use a case with a fan or heatsink: the wake word listens all the time. |
| Raspberry Pi 5, 2 GB | Yes | Fine for Bob alone. |
| Raspberry Pi 4, 2 GB+ | Yes | Its 3.5 mm socket can drive a powered speaker; you still need a USB microphone. |
| Raspberry Pi 3B+ / Zero 2 W | Not supported | Too slow for the wake word and live audio together. |

Also: a **microSD card** (16 GB+, A1/A2) and a **USB card reader** if your PC has no slot, the **official power
supply**, and Wi-Fi (or an Ethernet cable). Use **Raspberry Pi OS Lite (64-bit)**.

## Sound

**A USB speakerphone** (the round "conference speaker" kind) is easiest: one cable, a microphone that hears
across a room, and echo cancelling. Plug it in **before** the first start: the installer makes the first USB sound
device the default for both microphone and speaker.

- Plugged it in later? Run the installer again (it keeps your settings):
  `curl -fsSL https://raw.githubusercontent.com/arancool3000/bob-assistant/main/install.sh | sudo bash`
- USB microphone + 3.5 mm speaker (Pi 4): on the settings page set *Speaker device* to `plughw:CARD=Headphones,DEV=0`
  and *Microphone device* to your USB mic (find names with `arecord -L` / `aplay -L` over SSH).
- Keep your own `/etc/asound.conf`: the installer only replaces a file it wrote itself (first line
  `# written by bob-assistant install.sh`).

## Wiring things for skills

Bob uses **BCM GPIO numbers** ("GPIO 17"), not physical pin numbers; the table below gives both. Ask him *"Which
pins are free?"* any time.

| GPIO | Physical pin | Use |
|---|---|---|
| 2, 3 | 3, 5 | I²C (SDA, SCL) for sensors (switched on by the installer; reboot once) |
| 4, 17, 27, 22 | 7, 11, 13, 15 | free |
| 5, 6, 13, 19, 26 | 29, 31, 33, 35, 37 | free |
| 12, 16, 18, 20, 21, 23, 24, 25 | 32, 36, 12, 38, 40, 16, 18, 22 | free (18 is good for servos) |
| 14, 15 | 8, 10 | serial (turn off the serial console first: `sudo raspi-config` → Interface → Serial) |
| 3.3 V | 1, 17 | power for sensors |
| 5 V | 2, 4 | power for modules that ask for 5 V |
| Ground | 6, 9, 14, 20, 25, 30, 34, 39 | |

**Rules that keep the Pi (and you) safe:**
1. GPIO pins are **3.3 V**. Never connect 5 V to a GPIO pin.
2. An LED always needs a **resistor** (220–330 Ω) in series.
3. Motors, solenoids, long LED strips and **bare relay coils** need their own power supply and a driver — never
   power them from a GPIO pin. A ready-made *relay module* (it has its own driver) is fine on 5 V and a GPIO pin.
4. **Never switch mains (230 V / 120 V) yourself** unless the relay is rated for it, fully enclosed, and wired by
   someone qualified. For lamps and fans, smart plugs are the safe choice.
5. A button goes between a GPIO pin and **ground** (the internal pull-up does the rest).
6. Wire everything with the Pi **switched off**.

### Starter examples

| You wire | Then say |
|---|---|
| **Start here (read-only):** BME280 / BMP280: VIN → 3.3 V (pin 1), GND → pin 6, SDA → pin 3, SCL → pin 5 | "Find what's on I2C", then "Install the example temperature skill." |
| LED + 330 Ω resistor: GPIO 17 (pin 11) → resistor → LED long leg; short leg → GND (pin 9) | "Make a skill to turn the LED on GPIO 17 on and off." |
| Button: GPIO 27 (pin 13) ↔ GND (pin 14) | "Make a skill that tells me if the button on GPIO 27 is pressed." |
| Relay module: VCC → 5 V (pin 2), GND → pin 6, IN → GPIO 22 (pin 15) | "Make a skill that switches the relay on GPIO 22." |
| Servo: signal → GPIO 18 (pin 12), power from a separate 5 V supply, grounds joined | "Make a skill to move the servo on GPIO 18 to an angle." |
| DHT22 *(advanced)* | First over SSH: `sudo /opt/bob-assistant/venv/bin/pip install adafruit-circuitpython-dht`, then ask Bob. |
