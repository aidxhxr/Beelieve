"""Transparent rule-based fallback scoring (DEGRADED mode).

Used when ``ML_ALLOW_HEURISTIC=true`` and the LightGBM artifacts are absent,
so the prediction pipeline stays alive with interpretable rules instead of
dying. Also reused by ``training.export_from_timescale`` to bootstrap a
health-score label for real data that has no human annotation yet.

Everything here is pure and dependency-free: functions take a mapping of
feature name -> float | None (see ``training.features.FEATURE_COLUMNS``) and
return values on the exact scales the models use, so the two scorers are
drop-in interchangeable.
"""

from __future__ import annotations

from collections.abc import Mapping

FeatureMap = Mapping[str, float | None]

# Viable brood-nest band (deg C); outside of it brood dies.
BROOD_TEMP_LOW = 32.0
BROOD_TEMP_HIGH = 37.0
# Healthy acoustic profile (normalized band-energy ratios).
RATIO_LOW_BASELINE = 0.53
RATIO_HIGH_BASELINE = 0.29


def _get(features: FeatureMap, name: str) -> float | None:
    value = features.get(name)
    if value is None:
        return None
    value = float(value)
    return value if value == value else None  # NaN -> None


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def heuristic_swarm_risk(features: FeatureMap) -> float:
    """Rule-based swarm risk in [0, 1].

    Pre-swarm signature: elevated 400-600 Hz energy ratio (piping / worker
    excitement), brood-temperature instability, and a nectar-flow weight
    plateau (the colony stops gaining because foragers idle before swarming).
    """
    ratio_high = _get(features, "audio_band_ratio_high")
    temp_std = _get(features, "temp_brood_std_6h")
    delta_24h = _get(features, "weight_delta_24h")

    risk = 0.0
    if ratio_high is not None:
        # 0 at baseline 0.29, saturating ~0.55 at ratio 0.50.
        risk += _clip01((ratio_high - RATIO_HIGH_BASELINE) / 0.21) * 0.55
    if temp_std is not None:
        # 0 below 0.45 C, saturating 0.25 at 1.5 C.
        risk += _clip01((temp_std - 0.45) / 1.05) * 0.25
    if delta_24h is not None:
        # Plateau: |delta| < 0.15 kg/day contributes fully, fades out by 0.5.
        risk += _clip01((0.5 - abs(delta_24h)) / 0.35) * 0.20
    return _clip01(risk)


def heuristic_health_score(features: FeatureMap) -> float:
    """Composite rule-based health score in [0, 1] (1 = thriving)."""
    penalty = 0.0

    temp = _get(features, "temp_brood_mean_6h")
    if temp is None:
        temp = _get(features, "temp_brood_c")
    if temp is not None:
        penalty += min(0.40, 0.055 * abs(temp - 34.6) ** 1.5)

    temp_std = _get(features, "temp_brood_std_6h")
    if temp_std is not None:
        penalty += min(0.20, 0.12 * max(0.0, temp_std - 0.45))

    humidity = _get(features, "humidity_mean_6h")
    if humidity is None:
        humidity = _get(features, "humidity_pct")
    if humidity is not None:
        penalty += min(0.15, 0.006 * max(0.0, abs(humidity - 60.0) - 12.0))

    co2 = _get(features, "co2_mean_3h")
    if co2 is None:
        co2 = _get(features, "co2_ppm")
    if co2 is not None:
        penalty += min(0.15, 0.00004 * max(0.0, co2 - 6500.0))

    weight = _get(features, "weight_kg")
    if weight is not None:
        penalty += min(0.20, 0.015 * max(0.0, 30.0 - weight))
    delta_24h = _get(features, "weight_delta_24h")
    if delta_24h is not None:
        penalty += min(0.20, 0.10 * max(0.0, -delta_24h - 0.3))

    ratio_low = _get(features, "audio_band_ratio_low")
    ratio_high = _get(features, "audio_band_ratio_high")
    if ratio_low is not None:
        penalty += min(0.20, 0.9 * max(0.0, ratio_low - 0.62))
    if ratio_high is not None:
        penalty += min(0.20, 0.9 * max(0.0, ratio_high - 0.38))

    readings = _get(features, "readings_in_last_hour")
    if readings is not None:
        penalty += min(0.10, 0.02 * max(0.0, 4.0 - readings))

    return _clip01(1.0 - penalty)


def heuristic_anomaly(features: FeatureMap) -> tuple[bool, str, float]:
    """Rule-based anomaly detection -> (is_anomaly, kind, score in [0, 1]).

    Evaluates each anomaly kind independently and reports the strongest.
    """
    candidates: list[tuple[str, float]] = []

    # sensor_fault: missing or physically impossible channels.
    missing = 0
    impossible = 0
    temp = _get(features, "temp_brood_c")
    humidity = _get(features, "humidity_pct")
    weight = _get(features, "weight_kg")
    audio = _get(features, "audio_db")
    co2 = _get(features, "co2_ppm")
    for value in (temp, humidity, weight, audio, co2):
        if value is None:
            missing += 1
    if temp is not None and not (-30.0 < temp < 60.0):
        impossible += 1
    if humidity is not None and not (1.0 <= humidity <= 99.5):
        impossible += 1
    if weight is not None and not (0.5 < weight < 200.0):
        impossible += 1
    if audio is not None and not (5.0 < audio < 110.0):
        impossible += 1
    if co2 is not None and not (100.0 < co2 < 40000.0):
        impossible += 1
    if impossible or missing:
        candidates.append(("sensor_fault", _clip01(0.55 * impossible + 0.25 * missing)))

    # temp_out_of_band: brood nest escaped the viable band (only meaningful
    # if the reading itself is plausible).
    band_temp = _get(features, "temp_brood_mean_6h")
    if band_temp is None:
        band_temp = temp
    if band_temp is not None and -30.0 < band_temp < 60.0:
        if band_temp < BROOD_TEMP_LOW:
            candidates.append(("temp_out_of_band", _clip01(0.6 + (BROOD_TEMP_LOW - band_temp) / 8.0)))
        elif band_temp > BROOD_TEMP_HIGH:
            candidates.append(("temp_out_of_band", _clip01(0.6 + (band_temp - BROOD_TEMP_HIGH) / 8.0)))

    # sudden_weight_drop: > 1 kg lost within an hour.
    delta_1h = _get(features, "weight_delta_1h")
    if delta_1h is not None and delta_1h < -1.0:
        candidates.append(("sudden_weight_drop", _clip01(0.6 + (-delta_1h - 1.0) / 5.0)))

    # queenless_acoustic: loud low-frequency "roar" with stagnant weight.
    ratio_low = _get(features, "audio_band_ratio_low")
    audio_mean = _get(features, "audio_db_mean_1h")
    if audio_mean is None:
        audio_mean = audio
    if ratio_low is not None and ratio_low > 0.62:
        score = 0.5 + _clip01((ratio_low - 0.62) / 0.15) * 0.3
        if audio_mean is not None and audio_mean > 54.0:
            score += 0.15
        delta_24h = _get(features, "weight_delta_24h")
        if delta_24h is not None and delta_24h < 0.0:
            score += 0.05
        candidates.append(("queenless_acoustic", _clip01(score)))

    if not candidates:
        return False, "none", 0.0
    kind, score = max(candidates, key=lambda kv: kv[1])
    if score <= 0.5:
        return False, "none", score
    return True, kind, score
