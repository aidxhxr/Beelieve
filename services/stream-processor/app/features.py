"""Pure rolling-feature computation over per-hive in-memory windows.

A window is a ``collections.deque`` of ``(ts, reading)`` tuples, appended in
arrival order (timestamps are expected to be roughly monotonic per hive).
All functions here are pure: no I/O, no globals, deterministic for a given
window and reference time. Missing data always yields ``None`` for the
affected feature, never an exception.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from math import sqrt
from typing import Any

Reading = dict[str, Any]
WindowEntry = tuple[datetime, Reading]
Window = deque[WindowEntry]

#: Longest horizon any feature needs; entries older than this can be pruned.
DEFAULT_RETENTION = timedelta(hours=24)

_HOUR_1 = timedelta(hours=1)
_HOUR_3 = timedelta(hours=3)
_HOUR_6 = timedelta(hours=6)
_HOUR_24 = timedelta(hours=24)

FEATURE_NAMES: tuple[str, ...] = (
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
)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def prune_window(window: Window, now: datetime, retention: timedelta = DEFAULT_RETENTION) -> None:
    """Drop entries older than ``retention`` relative to ``now`` (in place)."""
    cutoff = now - retention
    while window and window[0][0] < cutoff:
        window.popleft()


def _values_in(window: Window, field: str, now: datetime, horizon: timedelta) -> list[float]:
    """Numeric values of ``field`` for entries with ``ts`` in [now - horizon, now]."""
    cutoff = now - horizon
    return [
        float(reading[field])
        for ts, reading in window
        if cutoff <= ts <= now and _is_number(reading.get(field))
    ]


def rolling_mean(window: Window, field: str, now: datetime, horizon: timedelta) -> float | None:
    """Mean of ``field`` over the horizon; ``None`` if no values are present."""
    values = _values_in(window, field, now, horizon)
    if not values:
        return None
    return sum(values) / len(values)


def rolling_std(window: Window, field: str, now: datetime, horizon: timedelta) -> float | None:
    """Population standard deviation over the horizon; ``None`` with < 2 values."""
    values = _values_in(window, field, now, horizon)
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    return sqrt(sum((v - mean) ** 2 for v in values) / len(values))


def weight_delta(window: Window, now: datetime, horizon: timedelta) -> float | None:
    """Newest minus oldest ``weight_kg`` among readings within the horizon.

    Requires at least two weighted readings at distinct timestamps inside the
    horizon; otherwise there is no meaningful delta and ``None`` is returned.
    """
    cutoff = now - horizon
    points = [
        (ts, float(reading["weight_kg"]))
        for ts, reading in window
        if cutoff <= ts <= now and _is_number(reading.get("weight_kg"))
    ]
    if len(points) < 2:
        return None
    oldest = min(points, key=lambda p: p[0])
    newest = max(points, key=lambda p: p[0])
    if oldest[0] == newest[0]:
        return None
    return newest[1] - oldest[1]


def readings_in_last_hour(window: Window, now: datetime) -> int:
    """Count of readings with ``ts`` in [now - 1h, now]."""
    cutoff = now - _HOUR_1
    return sum(1 for ts, _ in window if cutoff <= ts <= now)


def _band_sum(reading: Reading, bands: tuple[str, str]) -> float | None:
    audio_bands = reading.get("audio_bands")
    if not isinstance(audio_bands, dict):
        return None
    a, b = (audio_bands.get(name) for name in bands)
    if not (_is_number(a) and _is_number(b)):
        return None
    return float(a) + float(b)


def audio_band_ratio_low(reading: Reading) -> float | None:
    """b100_200 + b200_300 of the current reading's normalized FFT energies."""
    return _band_sum(reading, ("b100_200", "b200_300"))


def audio_band_ratio_high(reading: Reading) -> float | None:
    """b400_500 + b500_600 of the current reading's normalized FFT energies."""
    return _band_sum(reading, ("b400_500", "b500_600"))


def compute_features(window: Window, now: datetime) -> dict[str, float | int | None]:
    """Compute all rolling features declared in docs/ARCHITECTURE.md.

    ``now`` is the timestamp of the reading being enriched; the band ratios
    are taken from the newest reading in the window (the one just appended).
    """
    latest: Reading = max(window, key=lambda e: e[0])[1] if window else {}
    return {
        "weight_delta_1h": weight_delta(window, now, _HOUR_1),
        "weight_delta_24h": weight_delta(window, now, _HOUR_24),
        "temp_brood_mean_6h": rolling_mean(window, "temp_brood_c", now, _HOUR_6),
        "temp_brood_std_6h": rolling_std(window, "temp_brood_c", now, _HOUR_6),
        "humidity_mean_6h": rolling_mean(window, "humidity_pct", now, _HOUR_6),
        "audio_db_mean_1h": rolling_mean(window, "audio_db", now, _HOUR_1),
        "audio_band_ratio_low": audio_band_ratio_low(latest),
        "audio_band_ratio_high": audio_band_ratio_high(latest),
        "co2_mean_3h": rolling_mean(window, "co2_ppm", now, _HOUR_3),
        "readings_in_last_hour": readings_in_last_hour(window, now),
    }
