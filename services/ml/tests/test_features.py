"""Contract tests for training.features (shared by training and inference)."""

from __future__ import annotations

from training.features import (
    ANOMALY_KINDS,
    FEATURE_COLUMNS,
    RAW_FEATURES,
    ROLLING_FEATURES,
    to_feature_vector,
)


def enriched_msg() -> dict[str, object]:
    """A full `hive.telemetry.enriched` message per docs/ARCHITECTURE.md."""
    return {
        "hive_id": "KZ-ALA-0042",
        "apiary_id": "apiary-almaty-01",
        "ts": "2026-08-18T12:00:00Z",
        "temp_brood_c": 34.8,
        "temp_ambient_c": 27.1,
        "humidity_pct": 58.2,
        "weight_kg": 42.35,
        "audio_db": 52.1,
        "audio_bands": {
            "b100_200": 0.31,
            "b200_300": 0.22,
            "b300_400": 0.18,
            "b400_500": 0.16,
            "b500_600": 0.13,
        },
        "co2_ppm": 4200,
        "battery_v": 3.91,
        "fw": "1.4.2",
        "ingested_at": "2026-08-18T12:00:01Z",
        "weight_delta_1h": 0.05,
        "weight_delta_24h": 1.2,
        "temp_brood_mean_6h": 34.7,
        "temp_brood_std_6h": 0.2,
        "humidity_mean_6h": 58.9,
        "audio_db_mean_1h": 51.8,
        "audio_band_ratio_low": 0.53,
        "audio_band_ratio_high": 0.29,
        "co2_mean_3h": 4150.0,
        "readings_in_last_hour": 60,
    }


class TestContract:
    def test_column_order_is_raw_then_rolling(self) -> None:
        assert FEATURE_COLUMNS == RAW_FEATURES + ROLLING_FEATURES
        assert len(FEATURE_COLUMNS) == len(set(FEATURE_COLUMNS))

    def test_rolling_feature_names_match_architecture(self) -> None:
        assert ROLLING_FEATURES == [
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

    def test_anomaly_kinds_match_architecture(self) -> None:
        assert ANOMALY_KINDS[0] == "none"
        assert set(ANOMALY_KINDS) == {
            "none",
            "queenless_acoustic",
            "temp_out_of_band",
            "sudden_weight_drop",
            "sensor_fault",
        }


class TestToFeatureVector:
    def test_full_message_maps_positionally(self) -> None:
        vector = to_feature_vector(enriched_msg())
        assert len(vector) == len(FEATURE_COLUMNS)
        by_name = dict(zip(FEATURE_COLUMNS, vector))
        assert by_name["temp_brood_c"] == 34.8
        assert by_name["co2_ppm"] == 4200.0
        assert by_name["audio_b100_200"] == 0.31
        assert by_name["audio_b500_600"] == 0.13
        assert by_name["weight_delta_24h"] == 1.2
        assert by_name["readings_in_last_hour"] == 60.0
        assert all(v is not None for v in vector)

    def test_missing_fields_become_none_not_errors(self) -> None:
        vector = to_feature_vector({"hive_id": "KZ-ALA-0042", "ts": "2026-08-18T12:00:00Z"})
        assert vector == [None] * len(FEATURE_COLUMNS)

    def test_partial_audio_bands(self) -> None:
        msg = enriched_msg()
        msg["audio_bands"] = {"b100_200": 0.4}
        by_name = dict(zip(FEATURE_COLUMNS, to_feature_vector(msg)))
        assert by_name["audio_b100_200"] == 0.4
        assert by_name["audio_b200_300"] is None
        assert by_name["audio_b500_600"] is None

    def test_flat_band_keys_supported_for_export_format(self) -> None:
        msg = {"audio_b100_200": 0.35, "audio_b400_500": 0.2}
        by_name = dict(zip(FEATURE_COLUMNS, to_feature_vector(msg)))
        assert by_name["audio_b100_200"] == 0.35
        assert by_name["audio_b400_500"] == 0.2

    def test_malformed_values_become_none(self) -> None:
        msg = enriched_msg()
        msg["temp_brood_c"] = "not-a-number"
        msg["weight_kg"] = None
        msg["humidity_pct"] = True  # bool is not a measurement
        msg["co2_ppm"] = float("nan")
        by_name = dict(zip(FEATURE_COLUMNS, to_feature_vector(msg)))
        assert by_name["temp_brood_c"] is None
        assert by_name["weight_kg"] is None
        assert by_name["humidity_pct"] is None
        assert by_name["co2_ppm"] is None

    def test_numeric_strings_are_coerced(self) -> None:
        msg = enriched_msg()
        msg["temp_brood_c"] = "34.1"
        by_name = dict(zip(FEATURE_COLUMNS, to_feature_vector(msg)))
        assert by_name["temp_brood_c"] == 34.1
