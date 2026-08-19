# Beelieve hive sensor node firmware

ESP32 (Arduino framework, PlatformIO) firmware for the in-hive sensor node.
Each wake cycle it reads brood temperature/humidity (SHT31), ambient
temperature (DS18B20), hive weight (HX711 load cell), 1 s of hive acoustics
(INMP441, 5-band Goertzel energies 100–600 Hz + dB SPL), CO2 (MH-Z19, optional)
and battery voltage, then publishes the telemetry JSON defined in
[`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) to
`beelieve/{apiary_id}/{hive_id}/telemetry` and deep-sleeps for
`TELEMETRY_INTERVAL_S` (default 600 s).

Offline readings are buffered to a LittleFS ring file and replayed
oldest-first on reconnect. Presence is reported on
`beelieve/{apiary_id}/{hive_id}/status` (retained `online`/`offline`, with an
MQTT Last Will for ungraceful drops). A sensor fault omits that field only —
the rest of the reading still ships.

> Delivery note: PubSubClient cannot emit QoS 1 PUBLISH packets. At-least-once
> delivery is provided at the application layer instead: a reading leaves the
> LittleFS queue only after `publish()` confirms the packet reached the socket.

## Wiring

All sensors run at 3V3 except the MH-Z19 (5 V supply, 3V3-safe UART).

| Sensor | Signal | ESP32 pin | Notes |
|---|---|---|---|
| SHT31 (brood temp + RH) | SDA | GPIO21 | I2C addr `0x44` |
| | SCL | GPIO22 | |
| DS18B20 (ambient temp) | DATA | GPIO4 | 4.7 kΩ pull-up to 3V3 |
| HX711 (load cell amp) | DOUT | GPIO16 | |
| | SCK | GPIO17 | |
| INMP441 (I2S MEMS mic) | SCK (BCLK) | GPIO26 | |
| | WS (LRCL) | GPIO25 | |
| | SD (DOUT) | GPIO33 | L/R pin → GND (left channel) |
| MH-Z19 (CO2) | TX → | GPIO35 (RX2) | input-only pin, fine for RX |
| | RX ← | GPIO32 (TX2) | sensor VIN = 5 V |
| Battery divider | midpoint | GPIO34 (ADC1_CH6) | 100 kΩ / 100 kΩ from VBAT to GND |

Firmware tolerates any absent sensor: the corresponding JSON field is simply
omitted. The MH-Z19 needs ~3 min warm-up after cold power-up; it stays powered
across deep sleep, so readings from the second cycle onward are valid.

Pin assignments live in `src/config.h` (`PIN_*` defines).

## Configuration

Copy `include/secrets.h.example` to `include/secrets.h` and set `WIFI_SSID`,
`WIFI_PASS`, `MQTT_HOST`, `HIVE_ID`, `APIARY_ID` (optionally `MQTT_PORT`,
`MQTT_USER`, `MQTT_PASS`). The real `secrets.h` is gitignored. Alternatively,
inject any of these via `build_flags` in `platformio.ini` — build flags win.
Add `-DDEBUG` to `build_flags` for verbose serial logging.

## Build / flash

```sh
pio run                 # build
pio run -t upload       # flash (adjust upload_port if auto-detect fails)
pio device monitor      # serial log @ 115200 baud
```

## Scale calibration

Calibration factor (ADC counts per kg) and tare offset persist in NVS, so this
is done once per assembled scale.

1. Flash the node and open `pio device monitor`.
2. After boot the firmware prints
   `send any key within 2500 ms for calibration console` — press any key.
3. With the platform **empty**, type `tare`.
4. Place a known reference mass (e.g. 2.000 kg) on the platform and type
   `cal 2.000`.
5. Verify with `weight` (put a different known mass on to cross-check);
   `raw` and `info` help diagnose wiring/factor issues.
6. Type `exit` — the node resumes its normal measure→publish→sleep cycle.

The console times out after 5 minutes idle. Values persist across flashes
(NVS), unless the flash is fully erased (`pio run -t erase`).

## Power budget (rule of thumb)

With `TELEMETRY_INTERVAL_S = 600` the node is awake ~8–12 s per cycle
(sensors ≈ 2–3 s, WiFi + MQTT + NTP ≈ 4–8 s) at an average ~80–120 mA, and
deep-sleeps the remaining ~590 s at ~10 µA MCU sleep current. That averages
roughly 1.5–2.5 mAh/h for the ESP32 → about 6–10 weeks on a 3000 mAh Li-ion
cell **if peripherals are power-gated**.

Caveats:

- The HX711 is powered down between readings by firmware; SHT31/DS18B20 idle
  currents are negligible.
- The MH-Z19 draws ~20–60 mA continuously (it must stay warm) and dominates
  the budget — on battery-only nodes either omit it or feed it from a small
  solar+battery rail. CO2 is an optional field, so omitting the sensor is safe.
- Longer intervals scale almost linearly: at 1800 s the radio duty cycle drops
  to a third.

## Layout

```
firmware/
├── platformio.ini            # env:esp32dev, libs, build flags
├── include/secrets.h.example # config template (copy to secrets.h)
├── src/
│   ├── main.cpp              # wake cycle orchestration + JSON build
│   ├── config.h              # pins, timing, thresholds, logging macros
│   ├── sensors.{h,cpp}       # SHT31, DS18B20, HX711(+NVS cal), INMP441
│   │                         #   Goertzel bank, MH-Z19, battery ADC,
│   │                         #   serial calibration console
│   ├── net.{h,cpp}           # WiFi backoff, SNTP, MQTT + LWT status
│   ├── buffer.{h,cpp}        # LittleFS append/replay offline queue
│   └── power.{h,cpp}         # deep sleep + RTC boot counter
└── README.md
```
