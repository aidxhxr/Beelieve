"""Pure-logic tests for app.scorer with fake boosters -- no Kafka, no model files."""

from __future__ import annotations

from typing import Any

import pytest
from app.scorer import (
    Debouncer,
    HeuristicScorer,
    MLScorer,
    Scores,
    build_prediction_payload,
    derive_alerts,
)
from training.features import ANOMALY_KINDS, FEATURE_COLUMNS


class FakeBooster:
    """Duck-typed LightGBM Booster returning a fixed prediction."""

    def __init__(self, output: Any) -> None:
        self.output = output
        self.last_input: Any = None

    def predict(self, data: Any, **_kwargs: Any) -> Any:
        self.last_input = data
        return self.output


def make_scorer(
    swarm: float = 0.2,
    health: float = 0.9,
    probs: list[float] | None = None,
    version: str = "lgbm-test",
) -> MLScorer:
    if probs is None:
        probs = [0.9, 0.025, 0.025, 0.025, 0.025]
    return MLScorer(
        swarm=FakeBooster([swarm]),
        health=FakeBooster([health]),
        anomaly=FakeBooster([probs]),
        model_version=version,
    )


def vector(**overrides: float | None) -> list[float | None]:
    base: dict[str, float | None] = {name: 0.0 for name in FEATURE_COLUMNS}
    base.update(overrides)
    return [base[name] for name in FEATURE_COLUMNS]


class TestMLScorer:
    def test_scores_all_three_heads(self) -> None:
        scores = make_scorer(swarm=0.42, health=0.77).score(vector())
        assert scores == Scores(0.42, 0.77, False, "none", pytest.approx(0.1))

    def test_anomaly_kind_is_argmax_of_non_none_classes(self) -> None:
        scores = make_scorer(probs=[0.05, 0.10, 0.70, 0.10, 0.05]).score(vector())
        assert scores.anomaly_is is True
        assert scores.anomaly_kind == "temp_out_of_band"
        assert scores.anomaly_score == pytest.approx(0.95)

    def test_not_anomalous_when_none_dominates(self) -> None:
        scores = make_scorer(probs=[0.6, 0.4, 0.0, 0.0, 0.0]).score(vector())
        assert scores.anomaly_is is False
        assert scores.anomaly_kind == "none"
        assert scores.anomaly_score == pytest.approx(0.4)

    def test_regressor_output_clipped_to_unit_interval(self) -> None:
        scores = make_scorer(swarm=1.3, health=-0.2).score(vector())
        assert scores.swarm_risk == 1.0
        assert scores.health_score == 0.0

    def test_none_features_become_nan_input(self) -> None:
        scorer = make_scorer()
        scorer.score(vector(temp_brood_c=None, weight_kg=None))
        row = scorer._swarm.last_input  # type: ignore[attr-defined]
        assert row.shape == (1, len(FEATURE_COLUMNS))
        idx_temp = FEATURE_COLUMNS.index("temp_brood_c")
        assert row[0, idx_temp] != row[0, idx_temp]  # NaN

    def test_wrong_vector_length_rejected(self) -> None:
        with pytest.raises(ValueError, match="expected"):
            make_scorer().score([0.0] * (len(FEATURE_COLUMNS) - 1))

    def test_wrong_probability_count_rejected(self) -> None:
        with pytest.raises(ValueError, match="probabilities"):
            make_scorer(probs=[0.5, 0.5]).score(vector())


class TestPredictionPayload:
    def test_exact_architecture_contract(self) -> None:
        scores = Scores(0.87, 0.62, True, "queenless_acoustic", 0.91)
        payload = build_prediction_payload("KZ-ALA-0042", "2026-08-18T12:00:05Z", "lgbm-2026.08", scores)
        assert payload == {
            "hive_id": "KZ-ALA-0042",
            "ts": "2026-08-18T12:00:05Z",
            "model_version": "lgbm-2026.08",
            "swarm_risk": 0.87,
            "health_score": 0.62,
            "anomaly": {"is_anomaly": True, "kind": "queenless_acoustic", "score": 0.91},
        }


class TestDeriveAlerts:
    def test_swarm_alert_above_threshold(self) -> None:
        scores = Scores(0.81, 0.5, False, "none", 0.1)
        alerts = derive_alerts("H1", "2026-08-18T12:00:05Z", scores)
        assert len(alerts) == 1
        alert = alerts[0]
        assert alert["kind"] == "swarm_imminent"
        assert alert["severity"] == "critical"
        assert alert["source"] == "ml"
        assert alert["hive_id"] == "H1"
        assert set(alert) == {"hive_id", "ts", "severity", "kind", "message", "source"}

    def test_no_swarm_alert_at_threshold(self) -> None:
        scores = Scores(0.80, 0.5, False, "none", 0.1)
        assert derive_alerts("H1", "t", scores) == []

    def test_queenless_alert_requires_kind_and_score(self) -> None:
        queenless = Scores(0.1, 0.5, True, "queenless_acoustic", 0.86)
        alerts = derive_alerts("H1", "t", queenless)
        assert [a["kind"] for a in alerts] == ["queenless"]
        assert alerts[0]["severity"] == "critical"
        assert alerts[0]["source"] == "ml"

        weak = Scores(0.1, 0.5, True, "queenless_acoustic", 0.85)
        assert derive_alerts("H1", "t", weak) == []

        other_kind = Scores(0.1, 0.5, True, "sudden_weight_drop", 0.99)
        assert derive_alerts("H1", "t", other_kind) == []

    def test_both_alerts_can_fire_together(self) -> None:
        scores = Scores(0.95, 0.2, True, "queenless_acoustic", 0.95)
        kinds = [a["kind"] for a in derive_alerts("H1", "t", scores)]
        assert kinds == ["swarm_imminent", "queenless"]


class TestDebouncer:
    def test_debounces_per_hive_and_kind_for_interval(self) -> None:
        clock = {"now": 0.0}
        debouncer = Debouncer(6 * 3600, clock=lambda: clock["now"])

        assert debouncer.should_emit("H1", "swarm_imminent") is True
        assert debouncer.should_emit("H1", "swarm_imminent") is False
        # Different kind and different hive are independent keys.
        assert debouncer.should_emit("H1", "queenless") is True
        assert debouncer.should_emit("H2", "swarm_imminent") is True

        clock["now"] = 6 * 3600 - 1.0
        assert debouncer.should_emit("H1", "swarm_imminent") is False
        clock["now"] = 6 * 3600 + 1.0
        assert debouncer.should_emit("H1", "swarm_imminent") is True


class TestHeuristicScorer:
    """DEGRADED-mode fallback keeps the same interface and sane behavior."""

    def test_model_version_marks_fallback(self) -> None:
        assert HeuristicScorer("lgbm-2026.08").model_version == "lgbm-2026.08-heuristic-fallback"

    def test_healthy_hive_scores_low_risk_high_health(self) -> None:
        healthy = vector(
            temp_brood_c=34.6, humidity_pct=58.0, weight_kg=42.0, audio_db=48.0,
            audio_b100_200=0.31, audio_b200_300=0.22, audio_b300_400=0.18,
            audio_b400_500=0.16, audio_b500_600=0.13, co2_ppm=4000.0,
            weight_delta_1h=0.05, weight_delta_24h=1.2, temp_brood_mean_6h=34.6,
            temp_brood_std_6h=0.2, humidity_mean_6h=58.0, audio_db_mean_1h=48.0,
            audio_band_ratio_low=0.53, audio_band_ratio_high=0.29,
            co2_mean_3h=4000.0, readings_in_last_hour=60.0,
        )
        scores = HeuristicScorer("v").score(healthy)
        assert scores.swarm_risk < 0.3
        assert scores.health_score > 0.8
        assert scores.anomaly_is is False
        assert scores.anomaly_kind == "none"

    def test_pre_swarm_signature_scores_high_risk(self) -> None:
        pre_swarm = vector(
            temp_brood_c=35.0, weight_kg=40.0,
            audio_band_ratio_high=0.48, audio_band_ratio_low=0.40,
            temp_brood_std_6h=1.3, weight_delta_24h=0.05, weight_delta_1h=0.0,
            humidity_pct=58.0, audio_db=53.0, co2_ppm=4000.0,
            temp_brood_mean_6h=35.0, humidity_mean_6h=58.0, audio_db_mean_1h=53.0,
            co2_mean_3h=4000.0, readings_in_last_hour=60.0,
            audio_b100_200=0.22, audio_b200_300=0.18, audio_b300_400=0.12,
            audio_b400_500=0.25, audio_b500_600=0.23,
        )
        assert HeuristicScorer("v").score(pre_swarm).swarm_risk > 0.8

    def test_queenless_roar_detected_as_anomaly(self) -> None:
        queenless = vector(
            temp_brood_c=34.2, weight_kg=38.0, audio_db=58.0,
            audio_band_ratio_low=0.74, audio_band_ratio_high=0.12,
            audio_db_mean_1h=57.5, weight_delta_24h=-0.4, weight_delta_1h=-0.02,
            humidity_pct=60.0, co2_ppm=4000.0, temp_brood_mean_6h=34.2,
            temp_brood_std_6h=0.8, humidity_mean_6h=60.0, co2_mean_3h=4000.0,
            readings_in_last_hour=60.0,
            audio_b100_200=0.40, audio_b200_300=0.34, audio_b300_400=0.14,
            audio_b400_500=0.07, audio_b500_600=0.05,
        )
        scores = HeuristicScorer("v").score(queenless)
        assert scores.anomaly_is is True
        assert scores.anomaly_kind == "queenless_acoustic"
        assert scores.anomaly_score > 0.85

    def test_scores_stay_in_unit_intervals(self) -> None:
        sensor_fault = vector(
            temp_brood_c=None, humidity_pct=None, weight_kg=-3.0,
            audio_db=120.0, co2_ppm=50000.0, temp_brood_mean_6h=None,
            weight_delta_1h=None, readings_in_last_hour=2.0,
        )
        scores = HeuristicScorer("v").score(sensor_fault)
        for value in (scores.swarm_risk, scores.health_score, scores.anomaly_score):
            assert 0.0 <= value <= 1.0
        assert scores.anomaly_is is True
        assert scores.anomaly_kind == "sensor_fault"
        assert scores.anomaly_kind in ANOMALY_KINDS
