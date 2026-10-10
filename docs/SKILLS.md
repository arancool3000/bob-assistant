# Skills: teaching Kevin by talking

A skill is one small Python file Kevin writes for himself. You ask; he writes it and reads back a short summary of
what it does; it is installed only after you say yes; then he tests it.

## Your first skill: a temperature sensor (read-only)

The safest first skill switches nothing: it only reads. Wire a **BME280 or BMP280** (Pi off first):
VIN → 3.3 V (pin 1), GND → pin 6, SDA → pin 3, SCL → pin 5. Then:

> **You:** Hey Kevin, find what's on I2C.
> **Kevin:** Found 1: BME280 / BMP280 at 0x76.
> **You:** Install the example temperature skill.
> **Kevin:** I can add a skill called room temperature … The full code and everything it can reach are on the
> settings page. Shall I install it, or do you want to read it there first?

That one exercises the whole path (finding the device, the code, the approval, Kevin's answer, and switching it off
or deleting it again) without moving anything. The code is in
[examples/skills/room_temperature.py](../examples/skills/room_temperature.py).

## Before you say yes: the code, not just Kevin's summary

Every skill Kevin offers also appears at the top of the **settings page** (`http://<pi-name>.local:8080`) under
*Waiting for your yes*, with:
- **the exact code** that would be installed, or, when it replaces a skill, **the diff** against the installed version;
- **what it can reach**, read from the code itself (its imports and calls): GPIO, I²C/SPI, serial, the internet and
  your network, running other programs, writing or reading files, the camera or microphone. The sensitive ones are
  marked *check*, and Kevin also says them out loud ("It uses the internet and your home network.");
- **Approve and install** / **Reject**. Approve installs exactly the code on the page (it is bound to it by a hash),
  and a skill you reject there is refused even if you later say yes to it out loud.

Settings → *Approving new skills* → **Only Approve on this page** turns off approval by voice entirely.

What a skill can reach is a report, not a sandbox. The hard limits are the skill host's: every skill runs as the
separate `bob-skill` user, can only write in `/var/lib/bob-skill`, cannot read Kevin's settings or API key, and
cannot change Kevin's or other skills' files. It can use the network.

## One bad skill: switch it off, or roll it back

Each skill is managed on its own, with nothing to do with Kevin's updates:
- **Switch off / on** (settings page, or "Hey Kevin, switch off the desk LED skill"): the file is kept but not loaded.
- **Earlier versions**: whenever a skill is replaced or deleted, the old one is kept (the last 10). The skill's
  *Code & versions* page shows each one with what changed, and **Put this version back** restores it; or say
  "Roll back the desk LED skill". A rollback is itself kept, so it can be undone. A deleted skill can be brought back
  the same way.
- A skill that fails to load is listed as broken and never stops Kevin or the other skills.

## A conversation

> **You:** Hey Kevin, I've put an LED on GPIO 17 with a resistor. Can you make a skill to switch it?
> **Kevin:** I can add a skill called desk LED: turns the LED on GPIO 17 on or off. It takes on. The full code and
> everything it can reach are on the settings page. Shall I install it, or do you want to read it there first?
> **You:** Yes.
> **Kevin:** Installed desk LED. Trying it now. Is it on?
> **You:** Yep!

In the same conversation Kevin uses a new skill through `run_skill`; from the next conversation it is one of his
own tools (`skill_desk_led`).

## What a skill looks like

```python
SKILL = {
    "name": "desk_led",
    "description": "Turn the LED on GPIO 17 on or off.",
    "parameters": {"type": "OBJECT", "properties": {"on": {"type": "BOOLEAN"}}, "required": ["on"]},
}

from gpiozero import LED
led = LED(17)                      # made once, when the skill loads: it stays alive, so the LED stays on

def run(on=True):
    led.on() if on else led.off()
    return {"say": "LED on." if on else "LED off."}
```

- `SKILL` is a plain dictionary: a description and the arguments as `{"type": "OBJECT", "properties": {...}}`,
  each argument with a `type` of `STRING`, `NUMBER`, `INTEGER`, `BOOLEAN`, `ARRAY` or `OBJECT`.
- `run(**args)` returns a dictionary; a short `"say"` is what Kevin tells you.
- **Things made at the top of the file stay alive** between calls (skills run in a long-running skill host), so
  outputs stay on and serial ports stay open. When any skill is added, changed or deleted the host restarts and
  every skill loads fresh (outputs go back to off).
- Ready to use: `gpiozero` (GPIO), `smbus2` (I²C), `serial` (pyserial), `urllib` (web) and the standard library.
  A skill can keep files in `/var/lib/bob-skill/`.
- Each call has a **30-second limit**.

## What skills cannot do (yet)

- **Watch and speak by themselves.** Kevin only talks in a conversation, so "tell me when the button is pressed" is
  not possible; "is the button pressed?" is.
- **Install libraries.** For a sensor that needs one (DHT22, BME280 drivers…), install it once over SSH:
  `sudo /opt/bob-assistant/venv/bin/pip install <package>` (for example `adafruit-circuitpython-dht`), then ask
  Kevin for the skill.

## Managing skills

- "What skills do you have?" · "Show me the desk LED code" · "Change the desk LED skill to blink twice"
- "Delete the test skill" (he asks first)
- The settings page lists every skill with its code and a delete button. A broken skill file is listed with the
  reason and never stops Kevin from starting.

## How safe is it?

- **The yes is enforced in code**, not just asked for: Kevin's install call is refused unless that exact code was
  offered for confirmation first and a moment has passed for you to answer. The same goes for deleting.
- The summary Kevin reads you is written by the AI from the skill's description. For anything important, read
  the code on the settings page.
- Skills run as their own user, `bob-skill`, which can use the GPIO, I²C, SPI and serial ports and nothing else
  of Kevin's: it cannot read his settings or API key, change his files, or stop him.
- Kevin is told never to suggest wiring that could damage the Pi, but you are the one with the wires: check before
  you connect anything.
