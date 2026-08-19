"""Pydantic v2 request/response models for the Beelieve API."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

Severity = Literal["critical", "warning", "info"]
AlertSource = Literal["ml", "rule"]
Locale = Literal["en", "ru", "kk"]
Role = Literal["beekeeper", "admin"]


class Resolution(StrEnum):
    """Time resolution for the readings endpoint."""

    raw = "raw"
    hourly = "hourly"
    daily = "daily"


# ── Auth ─────────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = ""
    locale: Locale = "en"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    full_name: str
    locale: str
    role: Role
    created_at: datetime

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Token lifetime in seconds")
    user: UserOut


# ── Apiaries / hives ─────────────────────────────────────────────────


class ApiaryOut(BaseModel):
    id: str
    name: str
    latitude: float | None = None
    longitude: float | None = None
    region: str | None = None
    created_at: datetime
    hive_count: int = 0


class HiveDetail(BaseModel):
    id: str
    apiary_id: str
    apiary_name: str
    name: str
    hive_type: str
    queen_year: int | None = None
    frames: int | None = None
    installed_at: date | None = None
    is_active: bool
    last_seen_at: datetime | None = None
    created_at: datetime


class LatestReading(BaseModel):
    time: datetime
    temp_brood_c: float | None = None
    temp_ambient_c: float | None = None
    humidity_pct: float | None = None
    weight_kg: float | None = None
    audio_db: float | None = None
    co2_ppm: float | None = None
    battery_v: float | None = None


class PredictionOut(BaseModel):
    # `model_version` is a domain field, not a pydantic namespace clash.
    model_config = ConfigDict(protected_namespaces=())

    time: datetime
    model_version: str
    swarm_risk: float = Field(ge=0.0, le=1.0)
    health_score: float = Field(ge=0.0, le=1.0)
    is_anomaly: bool
    anomaly_kind: str
    anomaly_score: float = Field(ge=0.0, le=1.0)


class HiveSummary(BaseModel):
    id: str
    apiary_id: str
    apiary_name: str
    name: str
    hive_type: str
    is_active: bool
    last_seen_at: datetime | None = None
    latest_reading: LatestReading | None = None
    latest_prediction: PredictionOut | None = None
    open_alerts: int = 0


# ── Readings ─────────────────────────────────────────────────────────


class ReadingRaw(BaseModel):
    time: datetime
    temp_brood_c: float | None = None
    temp_ambient_c: float | None = None
    humidity_pct: float | None = None
    weight_kg: float | None = None
    audio_db: float | None = None
    audio_b100_200: float | None = None
    audio_b200_300: float | None = None
    audio_b300_400: float | None = None
    audio_b400_500: float | None = None
    audio_b500_600: float | None = None
    co2_ppm: float | None = None
    battery_v: float | None = None


class ReadingHourly(BaseModel):
    time: datetime
    temp_brood_c: float | None = None
    temp_ambient_c: float | None = None
    humidity_pct: float | None = None
    weight_kg: float | None = None
    weight_range_kg: float | None = None
    audio_db: float | None = None
    co2_ppm: float | None = None
    battery_v: float | None = None
    n_readings: int


class ReadingDaily(BaseModel):
    time: datetime
    temp_brood_c: float | None = None
    humidity_pct: float | None = None
    weight_kg: float | None = None
    weight_delta_kg: float | None = None
    audio_db: float | None = None
    n_readings: int


# ── Alerts ───────────────────────────────────────────────────────────


class AlertOut(BaseModel):
    time: datetime
    hive_id: str
    severity: Severity
    kind: str
    message: str
    source: AlertSource
    acked: bool


class AckAlertRequest(BaseModel):
    hive_id: str
    time: datetime


class AckAlertResponse(BaseModel):
    acked: bool
    updated: int


# ── Recommendations ──────────────────────────────────────────────────


class RecommendationOut(BaseModel):
    # `model_id` is a domain field, not a pydantic namespace clash.
    model_config = ConfigDict(protected_namespaces=())

    id: UUID
    hive_id: str
    created_at: datetime
    locale: str
    model_id: str
    priority: int = Field(ge=1, le=5)
    title: str
    body: str
    context: dict[str, Any] = Field(default_factory=dict)


# ── Overview ─────────────────────────────────────────────────────────


class WeightTrendPoint(BaseModel):
    bucket: datetime
    total_weight_kg: float | None = None


class OverviewOut(BaseModel):
    hive_count: int
    active_hives: int
    alerts_by_severity: dict[Severity, int]
    avg_health_score: float | None = None
    avg_swarm_risk: float | None = None
    weight_trend_7d: list[WeightTrendPoint] = Field(default_factory=list)


# ── WebSocket ────────────────────────────────────────────────────────


class WSEnvelope(BaseModel):
    """Message envelope streamed over /ws."""

    type: Literal["telemetry", "prediction", "alert", "ping", "pong"]
    data: dict[str, Any] = Field(default_factory=dict)
