"""Example skill: the room's temperature (and humidity and pressure) from a BME280 or BMP280 on I2C.

Read-only: it never switches anything, so it is the safest first skill. Wiring (Pi off first):
  VIN -> 3.3 V (pin 1)   GND -> GND (pin 6)   SDA -> GPIO 2 (pin 3)   SCL -> GPIO 3 (pin 5)
Then: "Hey Bob, find what's on I2C" (it should say a BME280 / BMP280 at 0x76 or 0x77),
and:  "Hey Bob, install the example temperature skill."  Ask "how warm is it?" afterwards.
Pure smbus2: no extra library, the Bosch formulas are below.
"""
import struct
import time

from smbus2 import SMBus

SKILL = {
    "name": "room_temperature",
    "description": "The temperature in the room now (plus humidity and air pressure if the sensor has them), "
                   "from the BME280/BMP280 sensor on the I2C pins.",
    "parameters": {"type": "OBJECT", "properties": {
        "units": {"type": "STRING", "description": "celsius (default) or fahrenheit"}}},
}

CHIPS = {0x60: "BME280", 0x58: "BMP280", 0x56: "BMP280", 0x57: "BMP280"}


def _find(bus):
    for addr in (0x76, 0x77):
        try:
            chip = bus.read_byte_data(addr, 0xD0)
        except OSError:
            continue
        if chip in CHIPS:
            return addr, CHIPS[chip]
    return None, None


def _calibration(bus, addr, humid):
    c = bus.read_i2c_block_data(addr, 0x88, 24)
    t1, t2, t3, p1, p2, p3, p4, p5, p6, p7, p8, p9 = struct.unpack("<HhhHhhhhhhhh", bytes(c))
    cal = {"T": (t1, t2, t3), "P": (p1, p2, p3, p4, p5, p6, p7, p8, p9)}
    if humid:
        h1 = bus.read_byte_data(addr, 0xA1)
        e = bus.read_i2c_block_data(addr, 0xE1, 7)
        h2 = struct.unpack("<h", bytes(e[0:2]))[0]
        h3 = e[2]
        h4 = (e[3] << 4) | (e[4] & 0x0F)
        h5 = (e[5] << 4) | (e[4] >> 4)
        h4 = h4 - 4096 if h4 > 2047 else h4
        h5 = h5 - 4096 if h5 > 2047 else h5
        h6 = struct.unpack("<b", bytes([e[6]]))[0]
        cal["H"] = (h1, h2, h3, h4, h5, h6)
    return cal


def compensate(cal, adc_t, adc_p, adc_h=None):
    """Bosch's floating-point formulas (BME280 datasheet, section 8.1). Returns (°C, hPa, %RH or None)."""
    t1, t2, t3 = cal["T"]
    v1 = (adc_t / 16384.0 - t1 / 1024.0) * t2
    v2 = ((adc_t / 131072.0 - t1 / 8192.0) ** 2) * t3
    t_fine = v1 + v2
    temp = t_fine / 5120.0
    p1, p2, p3, p4, p5, p6, p7, p8, p9 = cal["P"]
    v1 = t_fine / 2.0 - 64000.0
    v2 = v1 * v1 * p6 / 32768.0 + v1 * p5 * 2.0
    v2 = v2 / 4.0 + p4 * 65536.0
    v1 = (p3 * v1 * v1 / 524288.0 + p2 * v1) / 524288.0
    v1 = (1.0 + v1 / 32768.0) * p1
    pres = None
    if v1:
        p = (1048576.0 - adc_p - v2 / 4096.0) * 6250.0 / v1
        p += (p9 * p * p / 2147483648.0 + p * p8 / 32768.0 + p7) / 16.0
        pres = p / 100.0
    hum = None
    if adc_h is not None and "H" in cal:
        h1, h2, h3, h4, h5, h6 = cal["H"]
        h = t_fine - 76800.0
        h = (adc_h - (h4 * 64.0 + h5 / 16384.0 * h)) * (h2 / 65536.0 * (1.0 + h6 / 67108864.0 * h * (1.0 + h3 / 67108864.0 * h)))
        hum = max(0.0, min(100.0, h * (1.0 - h1 * h / 524288.0)))
    return temp, pres, hum


def read(bus):
    addr, chip = _find(bus)
    if not addr:
        return None
    humid = chip == "BME280"
    cal = _calibration(bus, addr, humid)
    if humid:
        bus.write_byte_data(addr, 0xF2, 0x01)            # humidity x1 (must come before the next line)
    bus.write_byte_data(addr, 0xF4, 0x25)                # temperature x1, pressure x1, one "forced" measurement
    for _ in range(50):
        time.sleep(0.01)
        if not bus.read_byte_data(addr, 0xF3) & 0x08:    # no longer measuring
            break
    d = bus.read_i2c_block_data(addr, 0xF7, 8)
    adc_p = (d[0] << 12) | (d[1] << 4) | (d[2] >> 4)
    adc_t = (d[3] << 12) | (d[4] << 4) | (d[5] >> 4)
    adc_h = (d[6] << 8) | d[7] if humid else None
    t, p, h = compensate(cal, adc_t, adc_p, adc_h)
    return {"chip": chip, "address": "0x%02X" % addr, "temperature_c": round(t, 1),
            "pressure_hpa": round(p, 1) if p else None, "humidity_percent": round(h) if h is not None else None}


def run(units="celsius"):
    try:
        with SMBus(1) as bus:
            r = read(bus)
    except OSError as e:
        return {"error": "the I2C bus is not available (%s)" % e,
                "say": "I can't reach the I2C pins. Is I2C switched on, and has the Pi been rebooted since?"}
    if not r:
        return {"error": "no BME280 or BMP280 at 0x76 or 0x77",
                "say": "I can't find the temperature sensor. Check SDA goes to pin 3, SCL to pin 5, and its power."}
    f = str(units or "").lower().startswith("f")
    t = r["temperature_c"] * 9 / 5 + 32 if f else r["temperature_c"]
    say = "It's %.1f degrees%s" % (t, " Fahrenheit" if f else "")
    if r["humidity_percent"] is not None:
        say += ", %d percent humidity" % r["humidity_percent"]
    r["say"] = say + "."
    return r
