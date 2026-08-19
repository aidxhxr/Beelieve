# services/ml — LightGBM training + online inference

Scores every hive continuously: swarm risk, health score, anomalies
(see `docs/ARCHITECTURE.md`, "ML"). Two halves sharing one feature contract:

- `training/` — offline: synthetic data generation, TimescaleDB export,
  LightGBM training for the three heads.
- `app/` — online: Kafka consumer (group `ml-inference`) on
  `hive.telemetry.enriched`, producing `hive.predictions` and critical
  `hive.alerts`, keyed by `hive_id`.
- `training/features.py` — **the contract**: `FEATURE_COLUMNS` order,
  `to_feature_vector()`, `ANOMALY_KINDS`. Both halves import it; changing it
  requires retraining and a `MODEL_VERSION` bump.

## Setup

Python 3.11:

```sh
cd services/ml
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

All commands below run from `services/ml` (so `training` and `app` are
importable as top-level packages).

## Training workflow

1. Generate a labeled synthetic dataset (bootstrapping; seedable):

   ```sh
   python -m training.datagen --n-samples 60000 --seed 42
   # -> data/generated/dataset.parquet
   ```

   Once real data exists, export it instead (rolling features reconstructed
   with SQL window functions, labels from the `alerts` hypertable):

   ```sh
   DATABASE_URL=postgresql://... python -m training.export_from_timescale \
       --start 2026-03-01 --end 2026-08-01 --out data/generated/real.parquet
   ```

2. Train the three heads (grouped train/valid split by `hive_id`, class
   weights for imbalance, early stopping):

   ```sh
   python -m training.train --data data/generated/dataset.parquet
   # -> models/swarm-lgbm-2026.08.txt, health-..., anomaly-...,
   #    metrics-....json, feature_importance-....json
   ```

   Artifact naming is documented in `models/README.md`; `--model-version`
   defaults to `$MODEL_VERSION` (`lgbm-2026.08`).

## Inference

```sh
KAFKA_BOOTSTRAP_SERVERS=localhost:9092 MODEL_DIR=./models \
MODEL_VERSION=lgbm-2026.08 python -m app.main
```

Behavior:

- Loads the three boosters from `MODEL_DIR` at startup and **fails fast**
  with a clear error if any artifact is missing. Set
  `ML_ALLOW_HEURISTIC=true` to instead run in DEGRADED mode: transparent
  rule-based scoring (`app/heuristics.py`) keeps the pipeline alive, logged
  loudly and tagged with a `-heuristic-fallback` model_version suffix.
- Per message: feature vector via `training.features.to_feature_vector`,
  three-head scoring, exact predictions payload to `hive.predictions`.
- Alerts (source `ml`, severity `critical`, debounced 6 h per
  `(hive, kind)` in memory): `swarm_imminent` when `swarm_risk > 0.8`;
  `queenless` when the anomaly head says `queenless_acoustic` with
  `score > 0.85`.
- Manual offset commits, graceful shutdown on SIGINT/SIGTERM, and p50/p99
  scoring-latency logs every 60 s (target < 10 ms per message).

Configuration is env-only (`app/config.py`): `KAFKA_BOOTSTRAP_SERVERS`,
`MODEL_DIR`, `MODEL_VERSION`, `ML_ALLOW_HEURISTIC`, plus tunable thresholds
(`SWARM_ALERT_THRESHOLD`, `QUEENLESS_ALERT_THRESHOLD`,
`ALERT_DEBOUNCE_SECONDS`, ...).

## Docker

```sh
docker build -t beelieve-ml services/ml
docker run --rm -e KAFKA_BOOTSTRAP_SERVERS=kafka:9092 \
    -v $(pwd)/services/ml/models:/models beelieve-ml
```

## Tests

Pure-logic tests (no Kafka, fake boosters):

```sh
python -m pytest tests/ -v
```
