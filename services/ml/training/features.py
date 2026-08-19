"""Feature contract between offline training and online inference.

This module is the single source of truth for the model input space.
Training (datagen / export_from_timescale / train) writes datasets whose
feature columns are exactly ``FEATURE_COLUMNS`` in this order, and online
inference builds its per-message vector with ``to_feature_vector``.

Never reorder or rename entries here without retraining and bumping
MODEL_VERSION: LightGBM boosters are positional.
"""

from __future__ import annotations

from typing import Any, Final

#: Raw sensor fields carried unchanged on `hive.telemetry.enriched`
#: (see docs/ARCHITECTURE.md, MQTT payload). Audio bands are normalized
#: FFT energy ratios (sum ~= 1.0).
RAW_FEATURES: Final[list[str]] = [
    "temp_brood_c",
    "humidity_pct",
    "weight_kg",
    "audio_db",
    "audio_b100_200",
    "audio_b200_300",
    "audio_b300_400",
    "audio_b400_500",
    "audio_b500_600",
    "co2_ppm",
]

#: Rolling features computed by the stream-processor
#: (docs/ARCHITECTURE.md, "Rolling features" -- names must match exactly).
ROLLING_FEATURES: Final[list[str]] = [
    "weight_delta_1h",
    "weight_delta_24h",
    "temp_brood_mean_6h",
    "temp_brood_std_6h",
    "humidity_mean_6h",
    "audio_db_mean_1h",
    "audio_band_ratio_low",
    "audio_band_ratio_high",
    "co2_mean_3h",
    "readings_in_last_hour",
]

#: The model input space, in booster column order.
FEATURE_COLUMNS: Final[list[str]] = RAW_FEATURES + ROLLING_FEATURES

#: Multiclass label space of the anomaly head. Index == LightGBM class id.
#: docs/ARCHITECTURE.md: none, queenless_acoustic, temp_out_of_band,
#: sudden_weight_drop, sensor_fault.
ANOMALY_KINDS: Final[list[str]] = [
    "none",
    "queenless_acoustic",
    "temp_out_of_band",
    "sudden_weight_drop",
    "sensor_fault",
]

#: audio_bands keys on the wire -> flat feature names.
_AUDIO_BAND_KEYS: Final[dict[str, str]] = {
    "b100_200": "audio_b100_200",
    "b200_300": "audio_b200_300",
    "b300_400": "audio_b300_400",
    "b400_500": "audio_b400_500",
    "b500_600": "audio_b500_600",
}


def _as_float(value: Any) -> float | None:
    """Coerce a payload value to float; anything non-numeric becomes None.

    Sensor faults must not drop the reading, so missing / malformed fields
    map to None (LightGBM handles NaN natively at scoring time).
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
        return result if result == result else None  # reject NaN on the wire
    if isinstance(value, str):
        try:
            result = float(value)
        except ValueError:
            return None
        return result if result == result else None
    return None


def to_feature_vector(enriched_msg: dict[str, Any]) -> list[float | None]:
    """Build the model input vector from a `hive.telemetry.enriched` message.

    Accepts audio bands either nested under ``audio_bands`` (the wire format)
    or already flattened as ``audio_b100_200`` etc. (the export format).
    Returns exactly ``len(FEATURE_COLUMNS)`` values aligned with
    ``FEATURE_COLUMNS``; absent or malformed fields are None.
    """
    flat: dict[str, float | None] = {}

    audio_bands = enriched_msg.get("audio_bands")
    if isinstance(audio_bands, dict):
        for wire_key, feature_name in _AUDIO_BAND_KEYS.items():
            flat[feature_name] = _as_float(audio_bands.get(wire_key))

    vector: list[float | None] = []
    for name in FEATURE_COLUMNS:
        if name in flat:
            vector.append(flat[name])
        else:
            vector.append(_as_float(enriched_msg.get(name)))
    return vector
