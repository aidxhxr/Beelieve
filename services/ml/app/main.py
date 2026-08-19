"""ml-inference entrypoint: Kafka poll loop scoring every enriched reading.

Consumer group ``ml-inference`` on ``hive.telemetry.enriched`` -> LightGBM
scores -> ``hive.predictions`` (keyed by hive_id), plus debounced critical
alerts to ``hive.alerts``. Manual offset commits, graceful shutdown, and
p50/p99 scoring-latency logs every 60 s.

Run: python -m app.main
"""

from __future__ import annotations

import json
import logging
import signal
import time
from datetime import UTC, datetime
from types import FrameType
from typing import Any

import numpy as np
from confluent_kafka import Consumer, KafkaError, KafkaException, Message, Producer

from app.config import Settings, get_settings
from app.scorer import (
    Debouncer,
    HeuristicScorer,
    MLScorer,
    ModelArtifactsMissing,
    build_prediction_payload,
    derive_alerts,
)
from training.features import to_feature_vector

logger = logging.getLogger("ml-inference")


class LatencyWindow:
    """Collects per-message scoring latencies and reports p50/p99 per window."""

    def __init__(self) -> None:
        self._samples_ms: list[float] = []

    def add(self, latency_ms: float) -> None:
        self._samples_ms.append(latency_ms)

    def snapshot_and_reset(self) -> tuple[int, float, float] | None:
        """Return (count, p50_ms, p99_ms) for the window, or None if empty."""
        if not self._samples_ms:
            return None
        arr = np.asarray(self._samples_ms, dtype=np.float64)
        self._samples_ms = []
        return len(arr), float(np.percentile(arr, 50)), float(np.percentile(arr, 99))


def load_scorer(settings: Settings) -> MLScorer | HeuristicScorer:
    """Load boosters from MODEL_DIR; fail fast unless DEGRADED mode is enabled."""
    try:
        scorer = MLScorer.load(settings.model_dir, settings.model_version)
        logger.info(
            "loaded LightGBM boosters %s from %s", settings.model_version, settings.model_dir
        )
        return scorer
    except ModelArtifactsMissing as exc:
        if not settings.ml_allow_heuristic:
            logger.critical("%s", exc)
            raise SystemExit(f"ml-inference cannot start: {exc}") from exc
        logger.critical(
            "DEGRADED MODE -- %s. ML_ALLOW_HEURISTIC=true, so falling back to "
            "transparent rule-based scoring. Predictions are heuristic, NOT "
            "model output; train and deploy artifacts as soon as possible.",
            exc,
        )
        return HeuristicScorer(settings.model_version)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _on_delivery(err: Any, msg: Message) -> None:
    if err is not None:
        logger.error("delivery failed for %s [%s]: %s", msg.topic(), msg.key(), err)


class InferenceService:
    """Owns the consumer/producer pair and the scoring poll loop."""

    def __init__(self, settings: Settings, scorer: MLScorer | HeuristicScorer) -> None:
        self.settings = settings
        self.scorer = scorer
        self.debouncer = Debouncer(settings.alert_debounce_seconds)
        self.latency = LatencyWindow()
        self._running = True
        self._uncommitted = 0
        self._last_commit = time.monotonic()
        self._last_latency_log = time.monotonic()

        self.consumer = Consumer(
            {
                "bootstrap.servers": settings.kafka_bootstrap_servers,
                "group.id": settings.consumer_group,
                "enable.auto.commit": False,  # manual commits only
                "enable.auto.offset.store": True,
                "auto.offset.reset": "earliest",
                "max.poll.interval.ms": 300_000,
                "session.timeout.ms": 30_000,
            }
        )
        self.producer = Producer(
            {
                "bootstrap.servers": settings.kafka_bootstrap_servers,
                "enable.idempotence": True,
                "linger.ms": 5,
                "compression.type": "lz4",
            }
        )

    # -- lifecycle ---------------------------------------------------------

    def stop(self, signum: int, _frame: FrameType | None) -> None:
        logger.info("received signal %s, shutting down gracefully", signal.Signals(signum).name)
        self._running = False

    def run(self) -> None:
        self.consumer.subscribe([self.settings.topic_enriched])
        logger.info(
            "consuming %s (group=%s) -> %s / %s | model_version=%s",
            self.settings.topic_enriched,
            self.settings.consumer_group,
            self.settings.topic_predictions,
            self.settings.topic_alerts,
            self.scorer.model_version,
        )
        try:
            while self._running:
                msg = self.consumer.poll(timeout=1.0)
                if msg is not None:
                    self._handle(msg)
                self.producer.poll(0)  # serve delivery callbacks
                self._maybe_commit()
                self._maybe_log_latency()
        finally:
            self._shutdown()

    def _shutdown(self) -> None:
        logger.info("flushing producer and committing final offsets")
        try:
            remaining = self.producer.flush(10.0)
            if remaining:
                logger.error("%d messages still undelivered after flush timeout", remaining)
        except KafkaException:
            logger.exception("producer flush failed")
        try:
            if self._uncommitted:
                self.consumer.commit(asynchronous=False)
        except KafkaException:
            logger.exception("final offset commit failed")
        self.consumer.close()
        logger.info("shutdown complete")

    # -- message handling --------------------------------------------------

    def _handle(self, msg: Message) -> None:
        err = msg.error()
        if err is not None:
            if err.code() == KafkaError._PARTITION_EOF:
                return
            if err.fatal():
                raise KafkaException(err)
            logger.error("consumer error: %s", err)
            return

        self._uncommitted += 1  # even skipped messages advance the offset
        raw = msg.value()
        try:
            enriched: dict[str, Any] = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
            logger.warning("skipping non-JSON message at %s[%d]@%d",
                           msg.topic(), msg.partition(), msg.offset())
            return

        hive_id = enriched.get("hive_id")
        if not isinstance(hive_id, str) or not hive_id:
            key = msg.key()
            hive_id = key.decode("utf-8", "replace") if isinstance(key, bytes) else None
        if not hive_id:
            logger.warning("skipping message without hive_id at %s[%d]@%d",
                           msg.topic(), msg.partition(), msg.offset())
            return

        start = time.perf_counter()
        vector = to_feature_vector(enriched)
        scores = self.scorer.score(vector)
        self.latency.add((time.perf_counter() - start) * 1000.0)

        ts = _utc_now_iso()
        payload = build_prediction_payload(hive_id, ts, self.scorer.model_version, scores)
        self._produce(self.settings.topic_predictions, hive_id, payload)

        for alert in derive_alerts(
            hive_id,
            ts,
            scores,
            swarm_threshold=self.settings.swarm_alert_threshold,
            queenless_threshold=self.settings.queenless_alert_threshold,
        ):
            if self.debouncer.should_emit(hive_id, alert["kind"]):
                logger.warning("ALERT %s for %s: %s", alert["kind"], hive_id, alert["message"])
                self._produce(self.settings.topic_alerts, hive_id, alert)
            else:
                logger.debug("debounced %s alert for %s", alert["kind"], hive_id)

    def _produce(self, topic: str, key: str, payload: dict[str, Any]) -> None:
        value = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        try:
            self.producer.produce(topic, key=key.encode("utf-8"), value=value, on_delivery=_on_delivery)
        except BufferError:
            logger.warning("producer queue full, blocking to drain")
            self.producer.poll(1.0)
            self.producer.produce(topic, key=key.encode("utf-8"), value=value, on_delivery=_on_delivery)

    # -- periodic work -----------------------------------------------------

    def _maybe_commit(self) -> None:
        if not self._uncommitted:
            return
        now = time.monotonic()
        if (
            self._uncommitted >= self.settings.commit_every_messages
            or (now - self._last_commit) >= self.settings.commit_interval_seconds
        ):
            try:
                self.consumer.commit(asynchronous=True)
            except KafkaException:
                logger.exception("offset commit failed")
            else:
                self._uncommitted = 0
                self._last_commit = now

    def _maybe_log_latency(self) -> None:
        now = time.monotonic()
        if (now - self._last_latency_log) < self.settings.latency_log_interval_seconds:
            return
        self._last_latency_log = now
        snapshot = self.latency.snapshot_and_reset()
        if snapshot is None:
            logger.info("scoring latency: no messages in the last window")
            return
        count, p50, p99 = snapshot
        level = logging.WARNING if p99 > 10.0 else logging.INFO
        logger.log(level, "scoring latency over %d msgs: p50=%.2fms p99=%.2fms (target <10ms)",
                   count, p50, p99)


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    scorer = load_scorer(settings)
    service = InferenceService(settings, scorer)
    signal.signal(signal.SIGINT, service.stop)
    signal.signal(signal.SIGTERM, service.stop)
    service.run()


if __name__ == "__main__":
    main()
