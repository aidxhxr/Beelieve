"""Contract validation tests for app.models (no network)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.models import Alert, TelemetryPayload, isoformat_utc
from pydantic import ValidationError

FULL_PAYLOAD = {
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
}


class TestTelemetryPayload:
    def test_full_contract_payload_is_valid(self) -> None:
        reading = TelemetryPayload.model_validate(FULL_PAYLOAD)
        assert reading.hive_id == "KZ-ALA-0042"
        assert reading.apiary_id == "apiary-almaty-01"
        assert reading.ts == datetime(2026, 8, 18, 12, 0, 0, tzinfo=UTC)
        assert reading.audio_bands is not None
        assert reading.audio_bands["b100_200"] == pytest.approx(0.31)
        assert reading.co2_ppm == pytest.approx(4200.0)

    def test_sensor_fields_are_optional(self) -> None:
        reading = TelemetryPayload.model_validate(
            {
                "hive_id": "KZ-ALA-0001",
                "apiary_id": "apiary-almaty-01",
                "ts": "2026-08-18T12:00:00+00:00",
            }
        )
        assert reading.temp_brood_c is None
        assert reading.weight_kg is None
        assert reading.audio_bands is None

    @pytest.mark.parametrize("missing", ["hive_id", "apiary_id", "ts"])
    def test_required_fields(self, missing: str) -> None:
        payload = dict(FULL_PAYLOAD)
        del payload[missing]
        with pytest.raises(ValidationError):
            TelemetryPayload.model_validate(payload)

    def test_naive_timestamp_rejected(self) -> None:
        payload = {**FULL_PAYLOAD, "ts": "2026-08-18T12:00:00"}
        with pytest.raises(ValidationError, match="timezone-aware"):
            TelemetryPayload.model_validate(payload)

    def test_non_utc_offset_normalized_to_utc(self) -> None:
        payload = {**FULL_PAYLOAD, "ts": "2026-08-18T17:00:00+05:00"}
        reading = TelemetryPayload.model_validate(payload)
        assert reading.ts == datetime(2026, 8, 18, 12, 0, 0, tzinfo=UTC)
        assert reading.ts.tzinfo == UTC

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("ts", "not-a-date"),
            ("humidity_pct", 140.0),
            ("humidity_pct", -1.0),
            ("battery_v", 12.0),
            ("weight_kg", -3.0),
            ("temp_brood_c", "hot"),
            ("hive_id", ""),
        ],
    )
    def test_out_of_range_or_wrong_type_rejected(self, field: str, value: object) -> None:
        payload = {**FULL_PAYLOAD, field: value}
        with pytest.raises(ValidationError):
            TelemetryPayload.model_validate(payload)

    @pytest.mark.parametrize(
        "bands",
        [
            {"b100_200": -0.1},
            {"b100_200": float("nan")},
            {"b100_200": float("inf")},
            {"": 0.5},
            {},
        ],
    )
    def test_bad_audio_bands_rejected(self, bands: dict[str, float]) -> None:
        payload = {**FULL_PAYLOAD, "audio_bands": bands}
        with pytest.raises(ValidationError):
            TelemetryPayload.model_validate(payload)

    def test_unknown_fields_ignored(self) -> None:
        payload = {**FULL_PAYLOAD, "future_sensor": 1.0}
        reading = TelemetryPayload.model_validate(payload)
        assert "future_sensor" not in reading.model_dump()

    def test_to_kafka_record_adds_ingested_at_and_drops_missing(self) -> None:
        reading = TelemetryPayload.model_validate(
            {
                "hive_id": "KZ-ALA-0007",
                "apiary_id": "apiary-almaty-01",
                "ts": "2026-08-18T12:00:00Z",
                "weight_kg": 41.0,
            }
        )
        ingested_at = datetime(2026, 8, 18, 12, 0, 3, tzinfo=UTC)
        record = reading.to_kafka_record(ingested_at)
        assert record["ingested_at"] == "2026-08-18T12:00:03.000Z"
        assert record["weight_kg"] == 41.0
        assert "temp_brood_c" not in record  # missing sensors stay missing


class TestAlert:
    def test_alert_contract_round_trip(self) -> None:
        alert = Alert(
            hive_id="KZ-ALA-0042",
            ts=datetime(2026, 8, 18, 12, 0, 5, tzinfo=UTC),
            severity="warning",
            kind="sensor_offline",
            message="Hive went offline",
            source="rule",
        )
        record = alert.to_kafka_record()
        assert record == {
            "hive_id": "KZ-ALA-0042",
            "ts": "2026-08-18T12:00:05.000Z",
            "severity": "warning",
            "kind": "sensor_offline",
            "message": "Hive went offline",
            "source": "rule",
        }

    @pytest.mark.parametrize(
        ("field", "value"),
        [("severity", "fatal"), ("kind", "bees_angry"), ("source", "human")],
    )
    def test_alert_enums_enforced(self, field: str, value: str) -> None:
        data = {
            "hive_id": "KZ-ALA-0042",
            "ts": "2026-08-18T12:00:05Z",
            "severity": "warning",
            "kind": "sensor_offline",
            "message": "x",
            "source": "rule",
            field: value,
        }
        with pytest.raises(ValidationError):
            Alert.model_validate(data)


def test_isoformat_utc_uses_z_suffix() -> None:
    assert isoformat_utc(
        datetime(2026, 8, 18, 12, 0, 0, tzinfo=UTC)
    ) == "2026-08-18T12:00:00.000Z"
