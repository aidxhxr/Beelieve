"""Synthetic labeled dataset generator for bootstrapping the Beelieve models.

Produces realistic hive telemetry feature vectors (raw fields + the exact
rolling features from docs/ARCHITECTURE.md) across seasons, with labels:

* ``swarm_within_72h`` -- binary. Pre-swarm signature: rising 400-600 Hz
  acoustic ratio, brood temperature instability, weight plateau in a season
  where the colony should be gaining.
* ``health_score``     -- continuous [0, 1], composed from stressors
  (thermoregulation failure, humidity extremes, CO2 buildup, starvation
  risk, acoustic distress, sensor coverage).
* ``anomaly_kind``     -- multiclass: none, queenless_acoustic,
  temp_out_of_band, sudden_weight_drop, sensor_fault.

Usage:
    python -m training.datagen --n-samples 60000 --seed 42
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from training.features import ANOMALY_KINDS, FEATURE_COLUMNS

logger = logging.getLogger("training.datagen")

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "data" / "generated" / "dataset.parquet"

SEASONS: list[str] = ["spring", "summer", "autumn", "winter"]
SEASON_WEIGHTS: list[float] = [0.30, 0.30, 0.25, 0.15]

# Scenario mix. "pre_swarm" only occurs in spring/early summer; the sampler
# re-rolls it for other seasons, so effective rates differ slightly.
SCENARIOS: list[str] = [
    "normal",
    "pre_swarm",
    "queenless",
    "temp_anomaly",
    "weight_drop",
    "sensor_fault",
]
SCENARIO_WEIGHTS: list[float] = [0.70, 0.09, 0.06, 0.05, 0.05, 0.05]

SCENARIO_TO_ANOMALY: dict[str, str] = {
    "normal": "none",
    "pre_swarm": "none",  # swarming is a prediction, not an anomaly kind
    "queenless": "queenless_acoustic",
    "temp_anomaly": "temp_out_of_band",
    "weight_drop": "sudden_weight_drop",
    "sensor_fault": "sensor_fault",
}


@dataclass(frozen=True)
class SeasonProfile:
    """Season-level priors for a healthy colony."""

    temp_ambient: tuple[float, float]  # mean, std
    weight_base: tuple[float, float]  # min, max of colony weight (kg)
    daily_gain: tuple[float, float]  # healthy weight_delta_24h range (kg)
    humidity: tuple[float, float]  # mean, std (%)
    co2: tuple[float, float]  # mean, std (ppm)


SEASON_PROFILES: dict[str, SeasonProfile] = {
    "spring": SeasonProfile((16.0, 6.0), (28.0, 48.0), (0.3, 2.0), (58.0, 8.0), (3800.0, 900.0)),
    "summer": SeasonProfile((26.0, 5.0), (38.0, 70.0), (0.2, 2.5), (55.0, 7.0), (4200.0, 1000.0)),
    "autumn": SeasonProfile((12.0, 6.0), (34.0, 58.0), (-0.4, 0.4), (62.0, 8.0), (3500.0, 800.0)),
    "winter": SeasonProfile((-2.0, 7.0), (24.0, 44.0), (-0.25, -0.02), (68.0, 9.0), (2600.0, 700.0)),
}

# Healthy normalized band energies (sum ~= 1.0), matching the architecture
# example: low (100-300 Hz) dominant, high (400-600 Hz) minor.
BASE_BANDS = np.array([0.31, 0.22, 0.18, 0.16, 0.13])


def _band_energies(rng: np.random.Generator, low_shift: float, high_shift: float) -> np.ndarray:
    """Sample normalized band energies, tilting mass toward low or high bands.

    ``low_shift`` / ``high_shift`` >= 0 move energy into the 100-300 Hz and
    400-600 Hz regions respectively; output is renormalized to sum to 1.
    """
    tilt = np.array([low_shift, low_shift * 0.8, 0.0, high_shift * 0.9, high_shift])
    raw = BASE_BANDS + tilt + rng.normal(0.0, 0.02, size=5)
    raw = np.clip(raw, 0.01, None)
    return raw / raw.sum()


def _health_from_stressors(
    rng: np.random.Generator,
    temp_brood: float,
    temp_std: float,
    humidity: float,
    co2: float,
    weight: float,
    weight_delta_24h: float,
    ratio_low: float,
    ratio_high: float,
    readings: float,
    season: str,
) -> float:
    """Composite continuous health label in [0, 1] (1 = thriving)."""
    penalty = 0.0
    # Thermoregulation: brood nest should hold ~34.5 C (winter cluster runs cooler).
    target = 33.0 if season == "winter" else 34.6
    penalty += min(0.40, 0.055 * abs(temp_brood - target) ** 1.5)
    penalty += min(0.20, 0.12 * max(0.0, temp_std - 0.45))
    # Humidity extremes promote chalkbrood / desiccation.
    penalty += min(0.15, 0.006 * max(0.0, abs(humidity - 60.0) - 12.0))
    # CO2 buildup -> poor ventilation / clustering stress.
    penalty += min(0.15, 0.00004 * max(0.0, co2 - 6500.0))
    # Starvation risk: light hive and/or sustained losses.
    penalty += min(0.20, 0.015 * max(0.0, 30.0 - weight))
    penalty += min(0.20, 0.10 * max(0.0, -weight_delta_24h - 0.3))
    # Acoustic distress: strong deviation from the healthy band profile.
    penalty += min(0.20, 0.9 * max(0.0, ratio_low - 0.62) + 0.9 * max(0.0, ratio_high - 0.38))
    # Sparse telemetry lowers confidence in "healthy".
    penalty += min(0.10, 0.02 * max(0.0, 4.0 - readings))
    score = 1.0 - penalty + rng.normal(0.0, 0.03)
    return float(np.clip(score, 0.0, 1.0))


def _sample_row(rng: np.random.Generator, hive_id: str, season: str) -> dict[str, object]:
    """Generate one labeled feature row for (hive, season, scenario)."""
    scenario = rng.choice(SCENARIOS, p=SCENARIO_WEIGHTS)
    # Swarming is a spring / early-summer phenomenon.
    if scenario == "pre_swarm" and season not in ("spring", "summer"):
        scenario = "normal"

    profile = SEASON_PROFILES[season]

    # --- healthy baseline -------------------------------------------------
    temp_brood = float(rng.normal(33.0 if season == "winter" else 34.6, 0.45))
    temp_std = float(abs(rng.normal(0.22, 0.12)))
    humidity = float(np.clip(rng.normal(*profile.humidity), 15.0, 99.0))
    weight = float(rng.uniform(*profile.weight_base))
    audio_db = float(rng.normal(48.0, 4.0))
    co2 = float(np.clip(rng.normal(*profile.co2), 400.0, 20000.0))
    delta_24h = float(rng.uniform(*profile.daily_gain))
    delta_1h = float(delta_24h / 24.0 + rng.normal(0.0, 0.05))
    readings = float(rng.integers(50, 62))
    low_shift, high_shift = 0.0, 0.0
    swarm_label = 0

    # --- scenario overrides ----------------------------------------------
    if scenario == "pre_swarm":
        # Rising 400-600 Hz ratio (piping / worker excitement), brood temp
        # instability from reduced brood care, and a weight plateau even
        # though the season says the colony should be gaining.
        high_shift = float(rng.uniform(0.08, 0.22))
        temp_std = float(abs(rng.normal(0.95, 0.30)))
        temp_brood = float(rng.normal(34.9, 0.9))
        delta_24h = float(rng.normal(0.0, 0.12))
        delta_1h = float(rng.normal(0.0, 0.05))
        audio_db = float(rng.normal(53.0, 3.5))
        swarm_label = 1
    elif scenario == "queenless":
        # Queenless "roar": louder hive, energy tilted into low bands,
        # erratic thermoregulation, stagnant-to-negative weight trend.
        low_shift = float(rng.uniform(0.10, 0.25))
        audio_db = float(rng.normal(57.0, 3.0))
        temp_std = float(abs(rng.normal(0.75, 0.25)))
        delta_24h = float(rng.normal(-0.25, 0.20))
        delta_1h = float(delta_24h / 24.0 + rng.normal(0.0, 0.05))
    elif scenario == "temp_anomaly":
        # Brood nest escapes the viable 32-37 C band (chilled or overheating).
        if rng.random() < 0.5:
            temp_brood = float(rng.uniform(24.0, 31.3))
        else:
            temp_brood = float(rng.uniform(37.8, 42.5))
        temp_std = float(abs(rng.normal(1.3, 0.5)))
    elif scenario == "weight_drop":
        # Robbing, absconding, or a knocked-over hive: sharp 1 h loss.
        delta_1h = float(-rng.uniform(1.2, 6.0))
        delta_24h = float(delta_1h + rng.normal(-0.5, 0.4))
        audio_db = float(rng.normal(54.0, 4.0))

    bands = _band_energies(rng, low_shift, high_shift)
    ratio_low = float(bands[0] + bands[1])
    ratio_high = float(bands[3] + bands[4])

    # A mild borderline zone keeps the swarm classes overlapping: some healthy
    # spring colonies flirt with the signature without swarming.
    if scenario == "normal" and season == "spring" and rng.random() < 0.06:
        bands = _band_energies(rng, 0.0, float(rng.uniform(0.03, 0.08)))
        ratio_low = float(bands[0] + bands[1])
        ratio_high = float(bands[3] + bands[4])
        temp_std = float(abs(rng.normal(0.55, 0.20)))

    row: dict[str, object] = {
        "hive_id": hive_id,
        "season": season,
        "temp_brood_c": temp_brood,
        "humidity_pct": humidity,
        "weight_kg": weight,
        "audio_db": audio_db,
        "audio_b100_200": float(bands[0]),
        "audio_b200_300": float(bands[1]),
        "audio_b300_400": float(bands[2]),
        "audio_b400_500": float(bands[3]),
        "audio_b500_600": float(bands[4]),
        "co2_ppm": co2,
        "weight_delta_1h": delta_1h,
        "weight_delta_24h": delta_24h,
        "temp_brood_mean_6h": float(temp_brood + rng.normal(0.0, 0.15)),
        "temp_brood_std_6h": temp_std,
        "humidity_mean_6h": float(humidity + rng.normal(0.0, 1.0)),
        "audio_db_mean_1h": float(audio_db + rng.normal(0.0, 0.8)),
        "audio_band_ratio_low": ratio_low,
        "audio_band_ratio_high": ratio_high,
        "co2_mean_3h": float(co2 + rng.normal(0.0, 150.0)),
        "readings_in_last_hour": readings,
    }

    if scenario == "sensor_fault":
        # Flat-lined, missing, or physically impossible channels.
        fault_mode = rng.choice(["missing", "impossible", "stuck"])
        channels = list(rng.choice(
            ["temp_brood_c", "humidity_pct", "weight_kg", "audio_db", "co2_ppm"],
            size=int(rng.integers(1, 4)),
            replace=False,
        ))
        for channel in channels:
            if fault_mode == "missing":
                row[channel] = None
            elif fault_mode == "impossible":
                row[channel] = {
                    "temp_brood_c": float(rng.choice([-38.0, 84.0])),
                    "humidity_pct": float(rng.choice([0.0, 100.0])),
                    "weight_kg": float(rng.choice([-3.0, 0.0, 240.0])),
                    "audio_db": float(rng.choice([0.0, 120.0])),
                    "co2_ppm": float(rng.choice([0.0, 50000.0])),
                }[channel]
                if channel == "temp_brood_c":
                    row["temp_brood_mean_6h"] = row[channel]
            else:  # stuck sensor: zero variance where there should be some
                if channel == "temp_brood_c":
                    row["temp_brood_std_6h"] = 0.0
        row["readings_in_last_hour"] = float(rng.integers(1, 12))

    row["swarm_within_72h"] = swarm_label
    row["health_score"] = _health_from_stressors(
        rng,
        temp_brood=float(row["temp_brood_c"] or 34.6),
        temp_std=float(row["temp_brood_std_6h"]),
        humidity=float(row["humidity_pct"] or 60.0),
        co2=float(row["co2_ppm"] or 4000.0),
        weight=float(row["weight_kg"] or 40.0),
        weight_delta_24h=float(row["weight_delta_24h"]),
        ratio_low=ratio_low,
        ratio_high=ratio_high,
        readings=float(row["readings_in_last_hour"]),
        season=season,
    )
    if scenario == "sensor_fault":
        # A faulty sensor says nothing about the colony -- pull the label
        # toward "unknown-but-probably-fine" with wider noise.
        row["health_score"] = float(np.clip(rng.normal(0.72, 0.12), 0.0, 1.0))
    row["anomaly_kind"] = SCENARIO_TO_ANOMALY[scenario]
    return row


def generate(n_samples: int, seed: int, n_hives: int | None = None) -> pd.DataFrame:
    """Generate ``n_samples`` labeled rows across a fleet of synthetic hives."""
    rng = np.random.default_rng(seed)
    if n_hives is None:
        n_hives = max(12, n_samples // 400)
    hive_ids = [f"KZ-SYN-{i:04d}" for i in range(n_hives)]

    rows: list[dict[str, object]] = []
    for i in range(n_samples):
        hive_id = hive_ids[int(rng.integers(0, n_hives))]
        season = str(rng.choice(SEASONS, p=SEASON_WEIGHTS))
        rows.append(_sample_row(rng, hive_id, season))
        if (i + 1) % 10000 == 0:
            logger.info("generated %d / %d rows", i + 1, n_samples)

    df = pd.DataFrame(rows)
    # Enforce contract: feature columns exist, ordered, float dtype (None -> NaN).
    for col in FEATURE_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    ordered = ["hive_id", "season", *FEATURE_COLUMNS, "swarm_within_72h", "health_score", "anomaly_kind"]
    df = df[ordered]
    df["swarm_within_72h"] = df["swarm_within_72h"].astype("int8")
    df["anomaly_kind"] = pd.Categorical(df["anomaly_kind"], categories=ANOMALY_KINDS)
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n-samples", type=int, default=60000, help="number of rows to generate")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed")
    parser.add_argument("--n-hives", type=int, default=None, help="synthetic fleet size (default: n_samples // 400)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output parquet path")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    df = generate(args.n_samples, args.seed, args.n_hives)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(args.out, index=False)

    logger.info("wrote %d rows x %d cols to %s", len(df), df.shape[1], args.out)
    logger.info("swarm_within_72h positives: %.2f%%", 100.0 * df["swarm_within_72h"].mean())
    logger.info("anomaly_kind counts:\n%s", df["anomaly_kind"].value_counts().to_string())


if __name__ == "__main__":
    main()
