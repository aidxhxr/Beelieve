"""Pure-logic tests for Pydantic v2 request/response models (no DB)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models import (
    AckAlertRequest,
    AlertOut,
    HiveSummary,
    LoginRequest,
    OverviewOut,
    PredictionOut,
    RecommendationOut,
    RegisterRequest,
    Resolution,
    UserOut,
    WSEnvelope,
)

NOW = datetime(2026, 8, 18, 12, 0, 0, tzinfo=UTC)


class TestAuthModels:
    def test_register_defaults(self) -> None:
        req = RegisterRequest(email="bee@example.com", password="secretpass")
        assert req.locale == "en"
        assert req.full_name == ""

    def test_register_rejects_short_password(self) -> None:
        with pytest.raises(ValidationError):
            RegisterRequest(email="bee@example.com", password="short")

    def test_register_rejects_unknown_locale(self) -> None:
        with pytest.raises(ValidationError):
            RegisterRequest(email="bee@example.com", password="secretpass", locale="fr")

    def test_login_rejects_bad_email(self) -> None:
        with pytest.raises(ValidationError):
            LoginRequest(email="not-an-email", password="x")

    def test_user_is_admin_property(self) -> None:
        base = {"id": uuid4(), "email": "a@b.co", "full_name": "", "locale": "en", "created_at": NOW}
        assert UserOut(**base, role="admin").is_admin
        assert not UserOut(**base, role="beekeeper").is_admin


class TestDomainModels:
    def test_prediction_bounds_enforced(self) -> None:
        valid = {
            "time": NOW,
            "model_version": "lgbm-2026.08",
            "swarm_risk": 0.87,
            "health_score": 0.62,
            "is_anomaly": True,
            "anomaly_kind": "queenless_acoustic",
            "anomaly_score": 0.91,
        }
        pred = PredictionOut(**valid)
        assert pred.swarm_risk == pytest.approx(0.87)
        with pytest.raises(ValidationError):
            PredictionOut(**{**valid, "swarm_risk": 1.5})
        with pytest.raises(ValidationError):
            PredictionOut(**{**valid, "health_score": -0.1})

    def test_alert_severity_and_source_literals(self) -> None:
        valid = {
            "time": NOW,
            "hive_id": "KZ-ALA-0042",
            "severity": "critical",
            "kind": "swarm_imminent",
            "message": "Swarm risk 0.87",
            "source": "ml",
            "acked": False,
        }
        assert AlertOut(**valid).severity == "critical"
        with pytest.raises(ValidationError):
            AlertOut(**{**valid, "severity": "fatal"})
        with pytest.raises(ValidationError):
            AlertOut(**{**valid, "source": "human"})

    def test_ack_request_parses_iso_time(self) -> None:
        req = AckAlertRequest.model_validate(
            {"hive_id": "KZ-ALA-0042", "time": "2026-08-18T12:00:00Z"}
        )
        assert req.time == NOW

    def test_hive_summary_optional_nested(self) -> None:
        summary = HiveSummary(
            id="KZ-ALA-0042",
            apiary_id="apiary-almaty-01",
            apiary_name="Almaty 01",
            name="Hive 42",
            hive_type="dadant",
            is_active=True,
        )
        assert summary.latest_reading is None
        assert summary.latest_prediction is None
        assert summary.open_alerts == 0

    def test_recommendation_priority_bounds(self) -> None:
        valid = {
            "id": uuid4(),
            "hive_id": "KZ-ALA-0042",
            "created_at": NOW,
            "locale": "kk",
            "model_id": "aidxhxr/beelieve-mistral-7b-advisor",
            "priority": 1,
            "title": "Add a super",
            "body": "Weight gain suggests a strong flow.",
        }
        assert RecommendationOut(**valid).context == {}
        with pytest.raises(ValidationError):
            RecommendationOut(**{**valid, "priority": 6})

    def test_overview_severity_keys(self) -> None:
        overview = OverviewOut(
            hive_count=12,
            active_hives=11,
            alerts_by_severity={"critical": 1, "warning": 3, "info": 0},
        )
        assert overview.avg_health_score is None
        assert overview.weight_trend_7d == []
        with pytest.raises(ValidationError):
            OverviewOut(
                hive_count=1,
                active_hives=1,
                alerts_by_severity={"catastrophic": 1},
            )


class TestWireFormats:
    def test_resolution_enum_values(self) -> None:
        assert Resolution("raw") is Resolution.raw
        assert [r.value for r in Resolution] == ["raw", "hourly", "daily"]
        with pytest.raises(ValueError):
            Resolution("weekly")

    def test_ws_envelope_types(self) -> None:
        env = WSEnvelope(type="telemetry", data={"hive_id": "KZ-ALA-0042"})
        assert env.model_dump()["type"] == "telemetry"
        with pytest.raises(ValidationError):
            WSEnvelope(type="gossip", data={})
