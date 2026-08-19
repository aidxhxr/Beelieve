# stream-processor

Real-time pipeline stage of Beelieve (see `docs/ARCHITECTURE.md`).

A single-threaded confluent-kafka consumer (group `stream-processor`) that:

- consumes `hive.telemetry.raw`, maintains per-hive in-memory rolling windows
  (24 h `deque` of `(ts, reading)`), computes the contract rolling features
  (`weight_delta_1h/24h`, `temp_brood_mean/std_6h`, `humidity_mean_6h`,
  `audio_db_mean_1h`, `audio_band_ratio_low/high`, `co2_mean_3h`,
  `readings_in_last_hour`; missing data → `null`) and produces raw + `features`
  to `hive.telemetry.enriched`, keyed by `hive_id`;
- writes every raw reading to the TimescaleDB `sensor_readings` hypertable via
  psycopg 3 `executemany` batches (flush at 500 rows or 2 s, whichever first)
  and updates `hives.last_seen_at`;
- consumes `hive.predictions` and `hive.alerts` and persists them into the
  `predictions` and `alerts` hypertables;
- fires rule-based alerts (`source: "rule"`) — low battery (< 3.3 V, warning),
  brood-temperature anomaly (warning outside [30, 38] °C, critical outside
  [25, 40] °C) and 1-hour weight drop (< −1.5 kg, critical) — debounced to one
  alert per (hive, kind) per 6 h, produced to `hive.alerts` (and persisted on
  the consume-back path, same as ML alerts).

Delivery is at-least-once: offsets are committed manually only after the DB
batch transaction commits; SIGTERM/SIGINT triggers a graceful flush → commit →
close. DB outages are retried with capped exponential backoff (the loop blocks,
so Kafka offsets never run ahead of the database).

## Configuration

Env-only (12-factor), see `/.env.example`: `KAFKA_BOOTSTRAP_SERVERS`,
`DATABASE_URL`, plus optional tuning (`BATCH_MAX_ROWS`, `BATCH_MAX_SECONDS`,
`ALERT_DEBOUNCE_SECONDS`, `WINDOW_RETENTION_HOURS`, `STATS_INTERVAL_SECONDS`,
`LOG_LEVEL`) — defaults in `app/config.py`.

## Run

```sh
pip install -r requirements.txt
python -m app.main
```

## Tests

Pure-logic tests (no Kafka or DB required):

```sh
pip install pytest
pytest tests/
```
