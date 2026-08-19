"""Rule-based alerting on raw telemetry (source: "rule").

Pure evaluation logic plus an in-memory debouncer. Alert kinds/severities
match the ``hive.alerts`` contract in docs/ARCHITECTURE.md and the CHECK
constraints on the ``alerts`` hypertable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

LOW_BATTERY_THRESHOLD_V = 3.3
TEMP_WARNING_RANGE_C = (30.0, 38.0)
TEMP_CRITICAL_RANGE_C = (25.0, 40.0)
WEIGHT_DROP_THRESHOLD_KG = -1.5
DEFAULT_DEBOUNCE = timedelta(hours=6)


@dataclass(frozen=True)
class RuleAlert:
    """One alert produced by rule evaluation (before debouncing)."""

    kind: str
    severity: str
    message: str
    source: str = "rule"


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def evaluate_reading(
    reading: dict[str, Any], features: dict[str, Any]
) -> list[RuleAlert]:
    """Evaluate all telemetry rules for a single raw reading.

    ``features`` is the rolling-feature dict computed for the same reading
    (used for ``weight_delta_1h``). Missing fields simply skip their rule.
    """
    alerts: list[RuleAlert] = []

    battery_v = reading.get("battery_v")
    if _is_number(battery_v) and battery_v < LOW_BATTERY_THRESHOLD_V:
        alerts.append(
            RuleAlert(
                kind="low_battery",
                severity="warning",
                message=(
                    f"Battery voltage {battery_v:.2f} V is below "
                    f"{LOW_BATTERY_THRESHOLD_V} V; sensor node may go offline soon."
                ),
            )
        )

    temp = reading.get("temp_brood_c")
    if _is_number(temp):
        crit_lo, crit_hi = TEMP_CRITICAL_RANGE_C
        warn_lo, warn_hi = TEMP_WARNING_RANGE_C
        if temp < crit_lo or temp > crit_hi:
            alerts.append(
                RuleAlert(
                    kind="temp_anomaly",
                    severity="critical",
                    message=(
                        f"Brood temperature {temp:.1f} C is critically outside "
                        f"[{crit_lo}, {crit_hi}] C; brood is at risk."
                    ),
                )
            )
        elif temp < warn_lo or temp > warn_hi:
            alerts.append(
                RuleAlert(
                    kind="temp_anomaly",
                    severity="warning",
                    message=(
                        f"Brood temperature {temp:.1f} C is outside the normal "
                        f"[{warn_lo}, {warn_hi}] C band."
                    ),
                )
            )

    weight_delta_1h = features.get("weight_delta_1h")
    if _is_number(weight_delta_1h) and weight_delta_1h < WEIGHT_DROP_THRESHOLD_KG:
        alerts.append(
            RuleAlert(
                kind="weight_drop",
                severity="critical",
                message=(
                    f"Hive lost {abs(weight_delta_1h):.2f} kg in the last hour "
                    "(possible swarm departure or theft)."
                ),
            )
        )

    return alerts


@dataclass
class AlertDebouncer:
    """At most one alert per (hive, kind) per ``interval``, in memory."""

    interval: timedelta = DEFAULT_DEBOUNCE
    _last_fired: dict[tuple[str, str], datetime] = field(default_factory=dict)

    def should_fire(self, hive_id: str, kind: str, now: datetime) -> bool:
        """Return True (and record the firing) if the alert is not debounced."""
        key = (hive_id, kind)
        last = self._last_fired.get(key)
        if last is not None and (now - last) < self.interval:
            return False
        self._last_fired[key] = now
        return True
