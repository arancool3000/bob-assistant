# Skills: teaching Bob by talking

A skill is one small Python file Bob writes for himself. You ask; he writes it and reads back a short summary of
what it does; it is installed only after you say yes; then he tests it.

## A conversation

> **You:** Hey Bob, I've put an LED on GPIO 17 with a resistor. Can you make a skill to switch it?
> **Bob:** I can add a skill called desk LED: turns the LED on GPIO 17 on or off. It takes on. Shall I install it?
> **You:** Yes.
> **Bob:** Installed desk LED. Trying it now. Is it on?
> **You:** Yep!

In the same conversation Bob uses a new skill through `run_skill`; from the next conversation it is one of his
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
- `run(**args)` returns a dictionary; a short `"say"` is what Bob tells you.
- **Things made at the top of the file stay alive** between calls (skills run in a long-running skill host), so
  outputs stay on and serial ports stay open. When any skill is added, changed or deleted the host restarts and
  every skill loads fresh (outputs go back to off).
- Ready to use: `gpiozero` (GPIO), `smbus2` (I²C), `serial` (pyserial), `urllib` (web) and the standard library.
  A skill can keep files in `/var/lib/bob-skill/`.
- Each call has a **30-second limit**.

## What skills cannot do (yet)

- **Watch and speak by themselves.** Bob only talks in a conversation, so "tell me when the button is pressed" is
  not possible; "is the button pressed?" is.
- **Install libraries.** For a sensor that needs one (DHT22, BME280 drivers…), install it once over SSH:
  `sudo /opt/bob-assistant/venv/bin/pip install <package>` (for example `adafruit-circuitpython-dht`), then ask
  Bob for the skill.

## Managing skills

- "What skills do you have?" · "Show me the desk LED code" · "Change the desk LED skill to blink twice"
- "Delete the test skill" (he asks first)
- The settings page lists every skill with its code and a delete button. A broken skill file is listed with the
  reason and never stops Bob from starting.

## How safe is it?

- **The yes is enforced in code**, not just asked for: Bob's install call is refused unless that exact code was
  offered for confirmation first and a moment has passed for you to answer. The same goes for deleting.
- The summary Bob reads you is written by the AI from the skill's description. For anything important, read
  the code on the settings page.
- Skills run as their own user, `bob-skill`, which can use the GPIO, I²C, SPI and serial ports and nothing else
  of Bob's: it cannot read his settings or API key, change his files, or stop him.
- Bob is told never to suggest wiring that could damage the Pi, but you are the one with the wires: check before
  you connect anything.
