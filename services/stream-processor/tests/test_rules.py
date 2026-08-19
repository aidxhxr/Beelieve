"""Pure-logic tests for rule-based alerting and debouncing (no Kafka, no DB)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.rules import AlertDebouncer, RuleAlert, evaluate_reading

NOW = datetime(2026, 8, 18, 12, 0, 0, tzinfo=UTC)

NO_FEATURES: dict[str, object] = {"weight_delta_1h": None}


def kinds(alerts: list[RuleAlert]) -> set[tuple[str, str]]:
    return {(a.kind, a.severity) for a in alerts}


class TestBatteryRule:
    def test_low_battery_warning(self) -> None:
        alerts = evaluate_reading({"battery_v": 3.2}, NO_FEATURES)
        assert kinds(alerts) == {("low_battery", "warning")}
        assert alerts[0].source == "rule"

    def test_battery_at_threshold_ok(self) -> None:
        assert evaluate_reading({"battery_v": 3.3}, NO_FEATURES) == []

    def test_battery_missing_ok(self) -> None:
        assert evaluate_reading({}, NO_FEATURES) == []


class TestTempRule:
    def test_in_band_no_alert(self) -> None:
        assert evaluate_reading({"temp_brood_c": 34.0}, NO_FEATURES) == []

    def test_boundaries_are_inclusive(self) -> None:
        assert evaluate_reading({"temp_brood_c": 30.0}, NO_FEATURES) == []
        assert evaluate_reading({"temp_brood_c": 38.0}, NO_FEATURES) == []

    def test_warning_band(self) -> None:
        for temp in (29.0, 39.5, 25.0, 40.0):
            alerts = evaluate_reading({"temp_brood_c": temp}, NO_FEATURES)
            assert kinds(alerts) == {("temp_anomaly", "warning")}, temp

    def test_critical_band(self) -> None:
        for temp in (24.9, 40.1, 10.0, 50.0):
            alerts = evaluate_reading({"temp_brood_c": temp}, NO_FEATURES)
            assert kinds(alerts) == {("temp_anomaly", "critical")}, temp

    def test_missing_temp_ok(self) -> None:
        assert evaluate_reading({"temp_brood_c": None}, NO_FEATURES) == []


class TestWeightDropRule:
    def test_drop_over_threshold_is_critical(self) -> None:
        alerts = evaluate_reading({}, {"weight_delta_1h": -2.0})
        assert kinds(alerts) == {("weight_drop", "critical")}

    def test_threshold_itself_does_not_fire(self) -> None:
        assert evaluate_reading({}, {"weight_delta_1h": -1.5}) == []

    def test_small_drop_ok(self) -> None:
        assert evaluate_reading({}, {"weight_delta_1h": -0.4}) == []

    def test_missing_feature_ok(self) -> None:
        assert evaluate_reading({}, {}) == []
        assert evaluate_reading({}, {"weight_delta_1h": None}) == []


class TestCombined:
    def test_multiple_rules_fire_together(self) -> None:
        alerts = evaluate_reading(
            {"battery_v": 3.0, "temp_brood_c": 24.0},
            {"weight_delta_1h": -3.2},
        )
        assert kinds(alerts) == {
            ("low_battery", "warning"),
            ("temp_anomaly", "critical"),
            ("weight_drop", "critical"),
        }


class TestAlertDebouncer:
    def test_first_fire_allowed_then_debounced(self) -> None:
        deb = AlertDebouncer(interval=timedelta(hours=6))
        assert deb.should_fire("H1", "low_battery", NOW) is True
        assert deb.should_fire("H1", "low_battery", NOW + timedelta(hours=1)) is False
        assert deb.should_fire("H1", "low_battery", NOW + timedelta(hours=5, minutes=59)) is False

    def test_fires_again_after_interval(self) -> None:
        deb = AlertDebouncer(interval=timedelta(hours=6))
        assert deb.should_fire("H1", "temp_anomaly", NOW) is True
        assert deb.should_fire("H1", "temp_anomaly", NOW + timedelta(hours=6)) is True

    def test_kinds_and_hives_are_independent(self) -> None:
        deb = AlertDebouncer(interval=timedelta(hours=6))
        assert deb.should_fire("H1", "low_battery", NOW) is True
        assert deb.should_fire("H1", "temp_anomaly", NOW) is True
        assert deb.should_fire("H2", "low_battery", NOW) is True
        assert deb.should_fire("H1", "low_battery", NOW) is False
