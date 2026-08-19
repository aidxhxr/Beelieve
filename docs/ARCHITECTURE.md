# Beelieve — System Architecture

Beelieve is a precision-beekeeping platform: in-hive sensor nodes stream telemetry
over MQTT, a real-time pipeline lands it in TimescaleDB via Kafka, a LightGBM model
scores every hive continuously (swarm risk, health, anomalies), and a fine-tuned
Mistral-7B model turns raw signals into actionable recommendations for beekeepers.

```
┌─────────────┐   MQTT    ┌───────────┐   Kafka    ┌──────────────────┐
│ ESP32 hive  │──────────▶│ Mosquitto │───────────▶│ ingestion bridge │
│ sensor node │           └───────────┘            │ (validate, key)  │
└─────────────┘                                    └────────┬─────────┘
                                                            │ hive.telemetry.raw
                        ┌───────────────────────────────────┼──────────────────┐
                        ▼                                   ▼                  │
              ┌──────────────────┐                ┌──────────────────┐         │
              │ stream processor │                │  ml-inference    │         │
              │ rolling features │───enriched────▶│  LightGBM scorer │         │
              │ Timescale writer │                └────────┬─────────┘         │
              └────────┬─────────┘                         │ hive.predictions  │
                       ▼                                   ▼                   │
              ┌──────────────────┐                ┌──────────────────┐         │
              │   TimescaleDB    │◀───────────────│      alerts      │◀────────┘
              │  hypertables +   │                └──────────────────┘
              │  continuous aggs │
              └────────┬─────────┘
                       │
              ┌────────▼─────────┐     ┌────────────────────┐
              │   FastAPI + WS   │────▶│ Mistral-7B (LoRA)  │
              │   backend API    │     │ recommender svc    │
              └────────┬─────────┘     └────────────────────┘
                       ▼
              ┌──────────────────┐
              │ React dashboard  │
              └──────────────────┘
```

## Data contracts

### MQTT

Topic: `beelieve/{apiary_id}/{hive_id}/telemetry` — QoS 1, JSON payload:

```json
{
  "hive_id": "KZ-ALA-0042",
  "apiary_id": "apiary-almaty-01",
  "ts": "2026-08-18T12:00:00Z",
  "temp_brood_c": 34.8,
  "temp_ambient_c": 27.1,
  "humidity_pct": 58.2,
  "weight_kg": 42.35,
  "audio_db": 52.1,
  "audio_bands": {"b100_200": 0.31, "b200_300": 0.22, "b300_400": 0.18, "b400_500": 0.16, "b500_600": 0.13},
  "co2_ppm": 4200,
  "battery_v": 3.91,
  "fw": "1.4.2"
}
```

`ts` is ISO-8601 UTC. `audio_bands` are normalized FFT energies (sum ≈ 1.0) over
hive-acoustics bands. Fields other than `hive_id`, `apiary_id`, `ts` are optional
(sensor faults must not drop the whole reading).

Status topic (LWT): `beelieve/{apiary_id}/{hive_id}/status` — `online`/`offline`, retained.

### Kafka topics (all keyed by `hive_id`)

| Topic | Producer | Consumers | Payload |
|---|---|---|---|
| `hive.telemetry.raw` | ingestion bridge | stream-processor, ml-inference | validated MQTT payload + `ingested_at` |
| `hive.telemetry.enriched` | stream-processor | ml-inference | raw + rolling features (below) |
| `hive.predictions` | ml-inference | stream-processor (persist), api | model scores |
| `hive.alerts` | ml-inference, stream-processor | stream-processor (persist), api | alert events |
| `hive.telemetry.dlq` | ingestion bridge | — | malformed payloads + error reason |

### Rolling features (stream-processor → `hive.telemetry.enriched`)

Per hive, computed over in-memory windows: `weight_delta_1h`, `weight_delta_24h`,
`temp_brood_mean_6h`, `temp_brood_std_6h`, `humidity_mean_6h`, `audio_db_mean_1h`,
`audio_band_ratio_low` (b100_200+b200_300), `audio_band_ratio_high` (b400_500+b500_600),
`co2_mean_3h`, `readings_in_last_hour`.

### Predictions (`hive.predictions`)

```json
{
  "hive_id": "KZ-ALA-0042",
  "ts": "2026-08-18T12:00:05Z",
  "model_version": "lgbm-2026.08",
  "swarm_risk": 0.87,
  "health_score": 0.62,
  "anomaly": {"is_anomaly": true, "kind": "queenless_acoustic", "score": 0.91}
}
```

`swarm_risk`, `health_score`, `anomaly.score` ∈ [0, 1].
Anomaly kinds: `queenless_acoustic`, `temp_out_of_band`, `sudden_weight_drop`,
`sensor_fault`, `none`.

### Alerts (`hive.alerts`)

```json
{"hive_id": "...", "ts": "...", "severity": "critical|warning|info",
 "kind": "swarm_imminent|queenless|temp_anomaly|weight_drop|sensor_offline|low_battery",
 "message": "...", "source": "ml|rule"}
```

## Storage (TimescaleDB)

Schema in `db/init/`: relational tables `users`, `apiaries`, `hives`;
hypertables `sensor_readings`, `predictions`, `alerts`; `recommendations` for
Mistral outputs; continuous aggregates `readings_hourly` and `readings_daily`
with real-time aggregation; compression after 7 days, 12-month retention on raw.

## ML

- **LightGBM** (`services/ml`): gradient-boosted trees over the rolling-feature
  vector. Three heads trained separately: swarm-risk classifier, health-score
  regressor, anomaly classifier. Trained offline on labeled seasons exported from
  TimescaleDB (synthetic generator included for bootstrapping); served online by a
  Kafka consumer that scores every enriched reading (<10 ms/reading).
- **Mistral-7B recommender** (`services/recommender`): LoRA fine-tune
  (PEFT/QLoRA scripts included) on beekeeper-advice instruction pairs; serving via
  Hugging Face Inference endpoint (`HF_API_KEY`) with a deterministic template
  fallback. Input: hive snapshot + latest predictions + recent alerts; output:
  ranked, actionable recommendations in the beekeeper's language (en/ru/kk).

## Services & env

All services are 12-factor; configuration is env-only — see `.env.example`.
`docker-compose.yml` brings up Mosquitto, Kafka (KRaft), TimescaleDB, all
services, the hive simulator, and the dashboard.
