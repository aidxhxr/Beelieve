"""Export real labeled training data from TimescaleDB into the parquet format
that ``training.train`` consumes (same columns as ``training.datagen``).

Rolling features are reconstructed in SQL with window functions over
``sensor_readings`` (RANGE frames on the hypertable's time column) so they
match what the stream-processor computes online:

    weight_delta_1h / weight_delta_24h, temp_brood_mean_6h,
    temp_brood_std_6h, humidity_mean_6h, audio_db_mean_1h,
    audio_band_ratio_low, audio_band_ratio_high, co2_mean_3h,
    readings_in_last_hour

Labels come from the ``alerts`` hypertable:

    * swarm_within_72h -- 1 if a ``swarm_imminent`` alert for the hive fires
      within the next 72 h of the reading.
    * anomaly_kind     -- alert kind within +/-30 min of the reading, mapped
      to the model's label space (queenless -> queenless_acoustic,
      temp_anomaly -> temp_out_of_band, weight_drop -> sudden_weight_drop);
      otherwise ``none``.
    * health_score     -- no human annotation exists in the schema, so it is
      bootstrapped with the transparent composite in ``app.heuristics``
      (documented, deterministic; replace with vet-graded labels when
      available).

Usage:
    python -m training.export_from_timescale \\
        --start 2026-03-01 --end 2026-08-01 --out data/generated/real.parquet
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import psycopg

from app.heuristics import heuristic_health_score
from training.features import ANOMALY_KINDS, FEATURE_COLUMNS

logger = logging.getLogger("training.export_from_timescale")

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "data" / "generated" / "real.parquet"

# alerts.kind -> anomaly label space (other alert kinds carry no anomaly label).
ALERT_KIND_TO_ANOMALY: dict[str, str] = {
    "queenless": "queenless_acoustic",
    "temp_anomaly": "temp_out_of_band",
    "weight_drop": "sudden_weight_drop",
    "sensor_offline": "sensor_fault",
}

EXPORT_SQL = """
WITH windowed AS (
    SELECT
        r.time,
        r.hive_id,
        r.temp_brood_c,
        r.humidity_pct,
        r.weight_kg,
        r.audio_db,
        r.audio_b100_200,
        r.audio_b200_300,
        r.audio_b300_400,
        r.audio_b400_500,
        r.audio_b500_600,
        r.co2_ppm,
        r.weight_kg - first_value(r.weight_kg) OVER w1h  AS weight_delta_1h,
        r.weight_kg - first_value(r.weight_kg) OVER w24h AS weight_delta_24h,
        avg(r.temp_brood_c)        OVER w6h AS temp_brood_mean_6h,
        stddev_samp(r.temp_brood_c) OVER w6h AS temp_brood_std_6h,
        avg(r.humidity_pct)        OVER w6h AS humidity_mean_6h,
        avg(r.audio_db)            OVER w1h AS audio_db_mean_1h,
        r.audio_b100_200 + r.audio_b200_300 AS audio_band_ratio_low,
        r.audio_b400_500 + r.audio_b500_600 AS audio_band_ratio_high,
        avg(r.co2_ppm)             OVER w3h AS co2_mean_3h,
        count(*)                   OVER w1h AS readings_in_last_hour
    FROM sensor_readings r
    WHERE r.time >= %(start)s AND r.time < %(end)s
    WINDOW
        w1h  AS (PARTITION BY r.hive_id ORDER BY r.time
                 RANGE BETWEEN INTERVAL '1 hour'  PRECEDING AND CURRENT ROW),
        w3h  AS (PARTITION BY r.hive_id ORDER BY r.time
                 RANGE BETWEEN INTERVAL '3 hours' PRECEDING AND CURRENT ROW),
        w6h  AS (PARTITION BY r.hive_id ORDER BY r.time
                 RANGE BETWEEN INTERVAL '6 hours' PRECEDING AND CURRENT ROW),
        w24h AS (PARTITION BY r.hive_id ORDER BY r.time
                 RANGE BETWEEN INTERVAL '24 hours' PRECEDING AND CURRENT ROW)
)
SELECT
    w.*,
    EXISTS (
        SELECT 1 FROM alerts a
        WHERE a.hive_id = w.hive_id
          AND a.kind = 'swarm_imminent'
          AND a.time >= w.time
          AND a.time <  w.time + INTERVAL '72 hours'
    ) AS swarm_within_72h,
    (
        SELECT a.kind FROM alerts a
        WHERE a.hive_id = w.hive_id
          AND a.kind IN ('queenless', 'temp_anomaly', 'weight_drop', 'sensor_offline')
          AND a.time BETWEEN w.time - INTERVAL '30 minutes'
                         AND w.time + INTERVAL '30 minutes'
        ORDER BY abs(EXTRACT(EPOCH FROM (a.time - w.time)))
        LIMIT 1
    ) AS alert_kind
FROM windowed w
ORDER BY w.hive_id, w.time
"""


def export(dsn: str, start: datetime | date, end: datetime | date) -> pd.DataFrame:
    """Run the export query and shape rows into the training dataset format."""
    logger.info("exporting sensor_readings from %s to %s", start, end)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(EXPORT_SQL, {"start": start, "end": end})
        columns = [desc.name for desc in cur.description or []]
        rows = cur.fetchall()
    logger.info("fetched %d rows", len(rows))

    df = pd.DataFrame(rows, columns=columns)
    if df.empty:
        return df

    for col in FEATURE_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")

    df["swarm_within_72h"] = df["swarm_within_72h"].astype(bool).astype("int8")
    df["anomaly_kind"] = (
        df["alert_kind"].map(ALERT_KIND_TO_ANOMALY).fillna("none").astype(str)
    )
    df["anomaly_kind"] = pd.Categorical(df["anomaly_kind"], categories=ANOMALY_KINDS)

    # Bootstrapped health label (see module docstring).
    feature_frame = df[FEATURE_COLUMNS]
    df["health_score"] = [
        heuristic_health_score({k: (None if pd.isna(v) else float(v)) for k, v in row.items()})
        for row in feature_frame.to_dict(orient="records")
    ]

    df["season"] = pd.to_datetime(df["time"], utc=True).dt.month.map(
        lambda m: ("winter", "winter", "spring", "spring", "spring", "summer",
                   "summer", "summer", "autumn", "autumn", "autumn", "winter")[m - 1]
    )
    ordered = ["hive_id", "season", *FEATURE_COLUMNS, "swarm_within_72h", "health_score", "anomaly_kind"]
    return df[ordered]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL"),
                        help="postgres DSN (default: $DATABASE_URL)")
    parser.add_argument("--start", type=date.fromisoformat, required=True, help="inclusive start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=date.fromisoformat, required=True, help="exclusive end date (YYYY-MM-DD)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output parquet path")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if not args.dsn:
        raise SystemExit("no DSN: pass --dsn or set DATABASE_URL")
    if args.start >= args.end:
        raise SystemExit("--start must be before --end")

    df = export(args.dsn, args.start, args.end)
    if df.empty:
        raise SystemExit("query returned no rows -- nothing exported")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(args.out, index=False)
    logger.info("wrote %d rows to %s", len(df), args.out)
    logger.info("swarm positives: %.2f%% | anomaly counts:\n%s",
                100.0 * df["swarm_within_72h"].mean(),
                df["anomaly_kind"].value_counts().to_string())


if __name__ == "__main__":
    main()
