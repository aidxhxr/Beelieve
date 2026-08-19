# ingestion bridge

Moves hive telemetry from MQTT into Kafka.

It subscribes to `beelieve/+/+/telemetry` and `beelieve/+/+/status` (QoS 1),
validates every payload against the contract in `docs/ARCHITECTURE.md`, and
checks that the ids in the topic match the ones in the payload. Good readings
get an `ingested_at` stamp and go to `hive.telemetry.raw`, keyed by `hive_id`.
Anything broken goes to `hive.telemetry.dlq` with the original bytes and the
reason it failed, so nothing silently disappears. When a hive's last-will fires
(`offline` on the status topic), the bridge emits a `sensor_offline` warning on
`hive.alerts`.

The producer runs with `acks=all`, lz4 compression and 50 ms linger; delivery
failures are logged. SIGTERM flushes the producer and disconnects cleanly.

## Running it

```sh
docker compose up ingestion
```

or locally: `pip install -r requirements.txt && python -m app.main`

Config is env vars only — the `MQTT_*` set and `KAFKA_BOOTSTRAP_SERVERS` from
`/.env.example`. Set `LOG_LEVEL=DEBUG` for a line per message; counters
(received/valid/invalid) are logged once a minute either way.

## Tests

```sh
pip install -r requirements-dev.txt
pytest
```

No broker needed — the tests fake the Kafka producer and feed messages straight
into the bridge.
