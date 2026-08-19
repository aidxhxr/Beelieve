"""Scoring core of ml-inference: model loading, per-message scoring, payload
assembly, alert derivation, and debouncing.

Pure logic -- no Kafka in this module -- so it is directly unit-testable with
fake boosters (anything exposing ``predict``).
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from app.heuristics import (
    heuristic_anomaly,
    heuristic_health_score,
    heuristic_swarm_risk,
)
from training.features import ANOMALY_KINDS, FEATURE_COLUMNS


class BoosterLike(Protocol):
    """Minimal LightGBM Booster surface used here (fake-able in tests)."""

    def predict(self, data: Any, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class Scores:
    """Result of scoring one enriched reading with all three heads."""

    swarm_risk: float
    health_score: float
    anomaly_is: bool
    anomaly_kind: str
    anomaly_score: float


class ModelArtifactsMissing(RuntimeError):
    """Raised when required model files are absent from MODEL_DIR."""


def _clip01(x: float) -> float:
    return float(max(0.0, min(1.0, x)))


def _vector_to_row(vector: Sequence[float | None]) -> np.ndarray:
    """Feature vector -> (1, n_features) float array; None -> NaN."""
    if len(vector) != len(FEATURE_COLUMNS):
        raise ValueError(
            f"feature vector has {len(vector)} values, expected {len(FEATURE_COLUMNS)}"
        )
    row = np.array(
        [np.nan if v is None else float(v) for v in vector], dtype=np.float64
    )
    return row.reshape(1, -1)


def _anomaly_from_probs(probs: np.ndarray) -> tuple[bool, str, float]:
    """Multiclass probabilities -> (is_anomaly, kind, score).

    ``score`` is P(any anomaly) = 1 - P(none), so it is monotone and in
    [0, 1]; ``kind`` is the most probable non-none class when anomalous.
    """
    p_none = float(probs[0])
    score = _clip01(1.0 - p_none)
    if score <= 0.5:
        return False, "none", score
    kind_idx = 1 + int(np.argmax(probs[1:]))
    return True, ANOMALY_KINDS[kind_idx], score


class MLScorer:
    """Scores feature vectors with the three trained LightGBM boosters."""

    def __init__(
        self,
        swarm: BoosterLike,
        health: BoosterLike,
        anomaly: BoosterLike,
        model_version: str,
    ) -> None:
        self._swarm = swarm
        self._health = health
        self._anomaly = anomaly
        self.model_version = model_version

    @staticmethod
    def artifact_paths(model_dir: Path, model_version: str) -> dict[str, Path]:
        """Expected artifact locations (see models/README.md naming)."""
        return {
            "swarm": model_dir / f"swarm-{model_version}.txt",
            "health": model_dir / f"health-{model_version}.txt",
            "anomaly": model_dir / f"anomaly-{model_version}.txt",
        }

    @classmethod
    def load(cls, model_dir: Path, model_version: str) -> "MLScorer":
        """Load the three boosters from MODEL_DIR, failing fast if any is absent."""
        import lightgbm as lgb  # imported here so tests with fakes need no lightgbm

        paths = cls.artifact_paths(model_dir, model_version)
        missing = [str(p) for p in paths.values() if not p.is_file()]
        if missing:
            raise ModelArtifactsMissing(
                "model artifacts missing: "
                + ", ".join(missing)
                + " -- train them (python -m training.datagen && python -m training.train) "
                "and mount them at MODEL_DIR, or set ML_ALLOW_HEURISTIC=true for "
                "DEGRADED rule-based scoring"
            )
        boosters = {name: lgb.Booster(model_file=str(path)) for name, path in paths.items()}
        return cls(boosters["swarm"], boosters["health"], boosters["anomaly"], model_version)

    def score(self, vector: Sequence[float | None]) -> Scores:
        row = _vector_to_row(vector)

        swarm_risk = _clip01(float(np.asarray(self._swarm.predict(row)).reshape(-1)[0]))
        health = _clip01(float(np.asarray(self._health.predict(row)).reshape(-1)[0]))
        probs = np.asarray(self._anomaly.predict(row), dtype=np.float64).reshape(-1)
        if probs.size != len(ANOMALY_KINDS):
            raise ValueError(
                f"anomaly head returned {probs.size} probabilities, expected {len(ANOMALY_KINDS)}"
            )
        is_anomaly, kind, score = _anomaly_from_probs(probs)
        return Scores(swarm_risk, health, is_anomaly, kind, score)


class HeuristicScorer:
    """Transparent rule-based fallback with the same interface as MLScorer.

    Used only in DEGRADED mode (ML_ALLOW_HEURISTIC=true, artifacts absent).
    The model_version is suffixed so downstream consumers can tell heuristic
    scores from model scores.
    """

    def __init__(self, model_version: str) -> None:
        self.model_version = f"{model_version}-heuristic-fallback"

    def score(self, vector: Sequence[float | None]) -> Scores:
        if len(vector) != len(FEATURE_COLUMNS):
            raise ValueError(
                f"feature vector has {len(vector)} values, expected {len(FEATURE_COLUMNS)}"
            )
        features = dict(zip(FEATURE_COLUMNS, vector))
        is_anomaly, kind, score = heuristic_anomaly(features)
        return Scores(
            swarm_risk=heuristic_swarm_risk(features),
            health_score=heuristic_health_score(features),
            anomaly_is=is_anomaly,
            anomaly_kind=kind,
            anomaly_score=score,
        )


def build_prediction_payload(
    hive_id: str, ts: str, model_version: str, scores: Scores
) -> dict[str, Any]:
    """Assemble the exact `hive.predictions` payload from docs/ARCHITECTURE.md."""
    return {
        "hive_id": hive_id,
        "ts": ts,
        "model_version": model_version,
        "swarm_risk": round(scores.swarm_risk, 4),
        "health_score": round(scores.health_score, 4),
        "anomaly": {
            "is_anomaly": scores.anomaly_is,
            "kind": scores.anomaly_kind,
            "score": round(scores.anomaly_score, 4),
        },
    }


def derive_alerts(
    hive_id: str,
    ts: str,
    scores: Scores,
    swarm_threshold: float = 0.8,
    queenless_threshold: float = 0.85,
) -> list[dict[str, Any]]:
    """Critical alert events implied by one set of scores (`hive.alerts` contract).

    * swarm_risk > swarm_threshold -> `swarm_imminent`
    * anomaly (is_anomaly, score > queenless_threshold, kind
      queenless_acoustic) -> `queenless`
    """
    alerts: list[dict[str, Any]] = []
    if scores.swarm_risk > swarm_threshold:
        alerts.append(
            {
                "hive_id": hive_id,
                "ts": ts,
                "severity": "critical",
                "kind": "swarm_imminent",
                "message": (
                    f"Swarm predicted within 72h (risk {scores.swarm_risk:.2f}): "
                    "rising 400-600Hz acoustic activity, brood temperature "
                    "instability and weight plateau. Inspect for queen cells now."
                ),
                "source": "ml",
            }
        )
    if (
        scores.anomaly_is
        and scores.anomaly_kind == "queenless_acoustic"
        and scores.anomaly_score > queenless_threshold
    ):
        alerts.append(
            {
                "hive_id": hive_id,
                "ts": ts,
                "severity": "critical",
                "kind": "queenless",
                "message": (
                    f"Queenless acoustic signature detected (score "
                    f"{scores.anomaly_score:.2f}). Verify queen presence / "
                    "introduce a new queen."
                ),
                "source": "ml",
            }
        )
    return alerts


class Debouncer:
    """In-memory per-(hive, kind) alert debounce.

    ``should_emit`` returns True at most once per ``interval_seconds`` for a
    given key. State is process-local by design (an ml-inference restart may
    re-alert once, which is acceptable for critical alerts).
    """

    def __init__(self, interval_seconds: float, clock: Any = time.monotonic) -> None:
        self._interval = float(interval_seconds)
        self._clock = clock
        self._last_emit: dict[tuple[str, str], float] = {}

    def should_emit(self, hive_id: str, kind: str) -> bool:
        now = float(self._clock())
        key = (hive_id, kind)
        last = self._last_emit.get(key)
        if last is not None and (now - last) < self._interval:
            return False
        self._last_emit[key] = now
        if len(self._last_emit) > 100_000:  # bound memory on huge fleets
            cutoff = now - self._interval
            self._last_emit = {k: t for k, t in self._last_emit.items() if t >= cutoff}
        return True
