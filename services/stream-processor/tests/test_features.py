"""Pure-logic tests for rolling-feature computation (no Kafka, no DB)."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.features import (
    FEATURE_NAMES,
    Window,
    audio_band_ratio_high,
    audio_band_ratio_low,
    compute_features,
    prune_window,
    readings_in_last_hour,
    rolling_mean,
    rolling_std,
    weight_delta,
)

NOW = datetime(2026, 8, 18, 12, 0, 0, tzinfo=timezone.utc)


def window_of(*entries: tuple[float, dict[str, Any]]) -> Window:
    """Build a window from (minutes_before_now, reading) pairs, oldest first."""
    return deque(
        (NOW - timedelta(minutes=minutes), reading) for minutes, reading in entries
    )


class TestWeightDelta:
    def test_newest_minus_oldest_within_horizon(self) -> None:
        window = window_of(
            (50, {"weight_kg": 42.0}),
            (25, {"weight_kg": 41.0}),
            (0, {"weight_kg": 40.2}),
        )
        assert weight_delta(window, NOW, timedelta(hours=1)) == pytest.approx(-1.8)

    def test_ignores_entries_outside_horizon(self) -> None:
        window = window_of(
            (90, {"weight_kg": 50.0}),  # outside 1h horizon
            (30, {"weight_kg": 42.0}),
            (0, {"weight_kg": 42.5}),
        )
        assert weight_delta(window, NOW, timedelta(hours=1)) == pytest.approx(0.5)
        # the 24h horizon still sees the old reading
        assert weight_delta(window, NOW, timedelta(hours=24)) == pytest.approx(-7.5)

    def test_single_reading_is_none(self) -> None:
        window = window_of((0, {"weight_kg": 42.0}))
        assert weight_delta(window, NOW, timedelta(hours=1)) is None

    def test_missing_weights_are_skipped(self) -> None:
        window = window_of(
            (40, {"weight_kg": None}),
            (20, {"temp_brood_c": 34.0}),
            (0, {"weight_kg": 42.0}),
        )
        assert weight_delta(window, NOW, timedelta(hours=1)) is None

    def test_empty_window_is_none(self) -> None:
        assert weight_delta(deque(), NOW, timedelta(hours=1)) is None


class TestRollingMeanStd:
    def test_mean_over_horizon(self) -> None:
        window = window_of(
            (300, {"temp_brood_c": 34.0}),
            (120, {"temp_brood_c": 35.0}),
            (0, {"temp_brood_c": 36.0}),
        )
        assert rolling_mean(window, "temp_brood_c", NOW, timedelta(hours=6)) == pytest.approx(35.0)

    def test_mean_excludes_old_entries(self) -> None:
        window = window_of(
            (7 * 60, {"co2_ppm": 9000}),  # outside 3h
            (60, {"co2_ppm": 4000}),
            (0, {"co2_ppm": 5000}),
        )
        assert rolling_mean(window, "co2_ppm", NOW, timedelta(hours=3)) == pytest.approx(4500.0)

    def test_mean_none_when_field_absent(self) -> None:
        window = window_of((0, {"weight_kg": 42.0}))
        assert rolling_mean(window, "humidity_pct", NOW, timedelta(hours=6)) is None

    def test_std_population(self) -> None:
        window = window_of(
            (60, {"temp_brood_c": 34.0}),
            (0, {"temp_brood_c": 36.0}),
        )
        # population std of [34, 36] is 1.0
        assert rolling_std(window, "temp_brood_c", NOW, timedelta(hours=6)) == pytest.approx(1.0)

    def test_std_none_with_fewer_than_two_values(self) -> None:
        window = window_of((0, {"temp_brood_c": 34.0}))
        assert rolling_std(window, "temp_brood_c", NOW, timedelta(hours=6)) is None

    def test_non_numeric_values_ignored(self) -> None:
        window = window_of(
            (30, {"audio_db": "loud"}),
            (0, {"audio_db": 52.0}),
        )
        assert rolling_mean(window, "audio_db", NOW, timedelta(hours=1)) == pytest.approx(52.0)


class TestAudioBandRatios:
    def test_low_and_high_ratios(self) -> None:
        reading = {
            "audio_bands": {
                "b100_200": 0.31, "b200_300": 0.22, "b300_400": 0.18,
                "b400_500": 0.16, "b500_600": 0.13,
            }
        }
        assert audio_band_ratio_low(reading) == pytest.approx(0.53)
        assert audio_band_ratio_high(reading) == pytest.approx(0.29)

    def test_missing_bands_dict_is_none(self) -> None:
        assert audio_band_ratio_low({}) is None
        assert audio_band_ratio_high({"audio_bands": None}) is None

    def test_partial_bands_is_none(self) -> None:
        reading = {"audio_bands": {"b100_200": 0.5}}
        assert audio_band_ratio_low(reading) is None


class TestReadingsInLastHour:
    def test_counts_only_last_hour(self) -> None:
        window = window_of((90, {}), (59, {}), (30, {}), (0, {}))
        assert readings_in_last_hour(window, NOW) == 3

    def test_empty(self) -> None:
        assert readings_in_last_hour(deque(), NOW) == 0


class TestPruneWindow:
    def test_drops_entries_older_than_retention(self) -> None:
        window = window_of((25 * 60, {"weight_kg": 1.0}), (60, {"weight_kg": 2.0}), (0, {"weight_kg": 3.0}))
        prune_window(window, NOW, timedelta(hours=24))
        assert len(window) == 2
        assert window[0][0] == NOW - timedelta(minutes=60)


class TestComputeFeatures:
    def test_all_contract_features_present(self) -> None:
        window = window_of((30, {"weight_kg": 42.0}), (0, {"weight_kg": 41.0}))
        features = compute_features(window, NOW)
        assert set(features) == set(FEATURE_NAMES)

    def test_empty_window_yields_nones_and_zero_count(self) -> None:
        features = compute_features(deque(), NOW)
        assert features["readings_in_last_hour"] == 0
        for name in FEATURE_NAMES:
            if name != "readings_in_last_hour":
                assert features[name] is None, name

    def test_full_reading(self) -> None:
        reading = {
            "temp_brood_c": 34.8, "humidity_pct": 58.2, "weight_kg": 42.35,
            "audio_db": 52.1, "co2_ppm": 4200,
            "audio_bands": {
                "b100_200": 0.31, "b200_300": 0.22, "b300_400": 0.18,
                "b400_500": 0.16, "b500_600": 0.13,
            },
        }
        earlier = dict(reading, weight_kg=43.85, temp_brood_c=35.2)
        window = window_of((30, earlier), (0, reading))
        features = compute_features(window, NOW)
        assert features["weight_delta_1h"] == pytest.approx(-1.5)
        assert features["weight_delta_24h"] == pytest.approx(-1.5)
        assert features["temp_brood_mean_6h"] == pytest.approx(35.0)
        assert features["temp_brood_std_6h"] == pytest.approx(0.2)
        assert features["humidity_mean_6h"] == pytest.approx(58.2)
        assert features["audio_db_mean_1h"] == pytest.approx(52.1)
        assert features["audio_band_ratio_low"] == pytest.approx(0.53)
        assert features["audio_band_ratio_high"] == pytest.approx(0.29)
        assert features["co2_mean_3h"] == pytest.approx(4200.0)
        assert features["readings_in_last_hour"] == 2
