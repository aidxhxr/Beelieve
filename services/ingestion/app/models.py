"""Pydantic v2 models for the Beelieve MQTT/Kafka data contracts.

Contract source: docs/ARCHITECTURE.md. Fields other than ``hive_id``,
``apiary_id`` and ``ts`` are optional so a single failed sensor never drops a
whole reading.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

HIVE_ID_MAX_LEN = 64
APIARY_ID_MAX_LEN = 64

AlertSeverity = Literal["critical", "warning", "info"]
AlertKind = Literal[
    "swarm_imminent",
    "queenless",
    "temp_anomaly",
    "weight_drop",
    "sensor_offline",
    "low_battery",
]
AlertSource = Literal["ml", "rule"]


class TelemetryPayload(BaseModel):
    """One telemetry reading published on ``beelieve/{apiary_id}/{hive_id}/telemetry``."""

    model_config = ConfigDict(extra="ignore")

    hive_id: str = Field(min_length=1, max_length=HIVE_ID_MAX_LEN)
    apiary_id: str = Field(min_length=1, max_length=APIARY_ID_MAX_LEN)
    ts: datetime

    temp_brood_c: float | None = Field(default=None, ge=-20.0, le=60.0)
    temp_ambient_c: float | None = Field(default=None, ge=-60.0, le=70.0)
    humidity_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    weight_kg: float | None = Field(default=None, ge=0.0, le=500.0)
    audio_db: float | None = Field(default=None, ge=0.0, le=140.0)
    audio_bands: dict[str, float] | None = None
    co2_ppm: float | None = Field(default=None, ge=0.0, le=100_000.0)
    battery_v: float | None = Field(default=None, ge=0.0, le=6.0)
    fw: str | None = Field(default=None, max_length=32)

    @field_validator("ts")
    @classmethod
    def _ts_must_be_utc(cls, value: datetime) -> datetime:
        """Require a timezone-aware ISO-8601 timestamp; normalize to UTC."""
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("ts must be a timezone-aware ISO-8601 UTC timestamp")
        return value.astimezone(timezone.utc)

    @field_validator("audio_bands")
    @classmethod
    def _bands_must_be_sane(
        cls, value: dict[str, float] | None
    ) -> dict[str, float] | None:
        """Normalized FFT energies: every band finite and non-negative."""
        if value is None:
            return None
        if not value:
            raise ValueError("audio_bands must not be empty when present")
        for band, energy in value.items():
            if not band:
                raise ValueError("audio_bands keys must be non-empty strings")
            if not math.isfinite(energy) or energy < 0.0:
                raise ValueError(
                    f"audio_bands[{band!r}] must be a finite, non-negative number"
                )
        return value

    def to_kafka_record(self, ingested_at: datetime) -> dict[str, Any]:
        """Serialize for ``hive.telemetry.raw``: contract payload + ``ingested_at``."""
        record = self.model_dump(mode="json", exclude_none=True)
        record["ingested_at"] = isoformat_utc(ingested_at)
        return record


class Alert(BaseModel):
    """Event for the ``hive.alerts`` Kafka topic."""

    model_config = ConfigDict(extra="forbid")

    hive_id: str = Field(min_length=1, max_length=HIVE_ID_MAX_LEN)
    ts: datetime
    severity: AlertSeverity
    kind: AlertKind
    message: str = Field(min_length=1, max_length=1024)
    source: AlertSource

    def to_kafka_record(self) -> dict[str, Any]:
        record = self.model_dump(mode="json")
        record["ts"] = isoformat_utc(self.ts)
        return record


def isoformat_utc(value: datetime) -> str:
    """Render a datetime as ISO-8601 UTC with a ``Z`` suffix."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
