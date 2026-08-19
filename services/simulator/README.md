# hive simulator

Dev-only stand-in for real hives so you can run the pipeline without hardware.

It spins up `SIM_NUM_HIVES` fake hives (`KZ-ALA-0001` and up, all in
`apiary-almaty-01`) and publishes telemetry to
`beelieve/{apiary_id}/{hive_id}/telemetry` every `SIM_INTERVAL_SECONDS`.
Each hive gets its own MQTT client with a retained last-will on the status
topic, same as a real ESP32 node would.

The numbers aren't just random noise: brood temp sits in the 34.5–35.5 °C band
and wobbles with the day/night cycle, weight climbs during the nectar flow and
dips around midday when the foragers are out, CO2 creeps up at night, the
battery slowly drains. Fields drop out now and then to look like flaky sensors.
Hives 3 and 8 are scripted to drift into pre-swarm acoustics (energy moving
into the 400–600 Hz bands) and hive 5 plays queenless (low-band roar) — assuming
you run enough hives to include them.

## Running it

```sh
docker compose --profile dev up hive-simulator
```

or locally: `pip install -r requirements.txt && python -m app.main`

Config is all env vars (see `/.env.example`): the `MQTT_*` set plus
`SIM_NUM_HIVES` and `SIM_INTERVAL_SECONDS`. `SIM_SEED` makes runs
reproducible, `LOG_LEVEL=DEBUG` prints every payload.

On SIGTERM it publishes a retained `offline` for each hive before
disconnecting; if you kill it hard, the broker's last-will does that instead.
