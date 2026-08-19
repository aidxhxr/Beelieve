"""Kafka wiring: consume raw telemetry / predictions / alerts, enrich, persist.

Delivery semantics: offsets are committed manually, only after the batched DB
writer has committed its transaction — at-least-once end to end. Rule alerts
are produced to ``hive.alerts`` and persisted when they are consumed back
(same path as ML alerts), so there is a single persistence code path.
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from typing import Any

from confluent_kafka import Consumer, KafkaError, KafkaException, Message, Producer

from .config import Settings
from .db import BatchedWriter
from .features import Window, compute_features, prune_window
from .rules import AlertDebouncer, evaluate_reading

log = logging.getLogger(__name__)

_ALLOWED_SEVERITIES = frozenset({"critical", "warning", "info"})
_ALLOWED_SOURCES = frozenset({"ml", "rule"})


def parse_ts(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp into an aware UTC datetime, or None."""
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class StreamProcessor:
    """Single-threaded poll loop tying consumer, feature engine, rules and DB."""

    def __init__(
        self,
        settings: Settings,
        db: BatchedWriter,
        consumer: Consumer | None = None,
        producer: Producer | None = None,
    ) -> None:
        self._settings = settings
        self._db = db
        self._consumer = consumer or Consumer(
            {
                "bootstrap.servers": settings.kafka_bootstrap_servers,
                "group.id": settings.consumer_group,
                "enable.auto.commit": False,
                "auto.offset.reset": "earliest",
                "partition.assignment.strategy": "cooperative-sticky",
            }
        )
        self._producer = producer or Producer(
            {
                "bootstrap.servers": settings.kafka_bootstrap_servers,
                "enable.idempotence": True,
                "linger.ms": 50,
                "compression.type": "lz4",
            }
        )
        self._windows: dict[str, Window] = defaultdict(deque)
        self._window_retention = timedelta(hours=settings.window_retention_hours)
        self._debouncer = AlertDebouncer(
            interval=timedelta(seconds=settings.alert_debounce_seconds)
        )
        self._stopped = False
        self._uncommitted = 0
        self._last_commit = time.monotonic()
        self._last_stats = time.monotonic()
        self._stats: dict[str, int] = defaultdict(int)

    # ── lifecycle ────────────────────────────────────────────────────

    def stop(self) -> None:
        """Request a graceful shutdown (signal-handler safe)."""
        self._stopped = True

    def run(self) -> None:
        """Blocking poll loop; returns after a graceful shutdown."""
        topics = [
            self._settings.topic_telemetry_raw,
            self._settings.topic_predictions,
            self._settings.topic_alerts,
        ]
        self._consumer.subscribe(topics)
        log.info(
            "stream-processor started; group=%s topics=%s",
            self._settings.consumer_group, topics,
        )
        try:
            while not self._stopped:
                msg = self._consumer.poll(self._settings.poll_timeout_seconds)
                if msg is not None:
                    self._on_message(msg)
                self._producer.poll(0)
                self._maybe_flush_and_commit()
                self._maybe_log_stats()
        finally:
            self._shutdown()

    def _shutdown(self) -> None:
        log.info("Shutting down: flushing batch, committing offsets, closing")
        try:
            self._db.flush()
            self._commit()
        finally:
            try:
                self._producer.flush(10)
            finally:
                self._consumer.close()
                self._db.close()
        log.info("Shutdown complete")

    # ── message handling ─────────────────────────────────────────────

    def _on_message(self, msg: Message) -> None:
        err = msg.error()
        if err is not None:
            if err.code() != KafkaError._PARTITION_EOF:
                log.error("Consumer error: %s", err)
            return

        payload = self._decode(msg)
        self._uncommitted += 1
        if payload is None:
            return

        topic = msg.topic()
        if topic == self._settings.topic_telemetry_raw:
            self._handle_raw(payload)
        elif topic == self._settings.topic_predictions:
            self._handle_prediction(payload)
        elif topic == self._settings.topic_alerts:
            self._handle_alert(payload)
        else:  # pragma: no cover — subscribe list makes this unreachable
            log.warning("Message from unexpected topic %s", topic)

    def _decode(self, msg: Message) -> dict[str, Any] | None:
        try:
            payload = json.loads(msg.value())
        except (TypeError, ValueError) as exc:
            log.warning(
                "Dropping undecodable message from %s[%s]@%s: %s",
                msg.topic(), msg.partition(), msg.offset(), exc,
            )
            self._stats["dropped"] += 1
            return None
        if not isinstance(payload, dict):
            self._stats["dropped"] += 1
            return None
        return payload

    def _handle_raw(self, payload: dict[str, Any]) -> None:
        hive_id = payload.get("hive_id")
        ts = parse_ts(payload.get("ts"))
        if not isinstance(hive_id, str) or not hive_id or ts is None:
            log.warning("Dropping raw reading without valid hive_id/ts")
            self._stats["dropped"] += 1
            return

        window = self._windows[hive_id]
        window.append((ts, payload))
        prune_window(window, ts, self._window_retention)
        features = compute_features(window, ts)

        enriched = dict(payload)
        enriched["features"] = features
        enriched["enriched_at"] = _utcnow_iso()
        self._produce(self._settings.topic_telemetry_enriched, hive_id, enriched)
        self._stats["enriched_out"] += 1

        self._db.add_reading(ts, hive_id, payload)
        self._stats["raw_in"] += 1

        for alert in evaluate_reading(payload, features):
            if not self._debouncer.should_fire(hive_id, alert.kind, ts):
                self._stats["alerts_debounced"] += 1
                continue
            event = {
                "hive_id": hive_id,
                "ts": payload["ts"],
                "severity": alert.severity,
                "kind": alert.kind,
                "message": alert.message,
                "source": alert.source,
            }
            self._produce(self._settings.topic_alerts, hive_id, event)
            self._stats["rule_alerts_out"] += 1

    def _handle_prediction(self, payload: dict[str, Any]) -> None:
        hive_id = payload.get("hive_id")
        ts = parse_ts(payload.get("ts"))
        swarm_risk = payload.get("swarm_risk")
        health_score = payload.get("health_score")
        if (
            not isinstance(hive_id, str)
            or ts is None
            or not isinstance(swarm_risk, (int, float))
            or not isinstance(health_score, (int, float))
        ):
            log.warning("Dropping malformed prediction: %r", payload)
            self._stats["dropped"] += 1
            return
        anomaly = payload.get("anomaly")
        if not isinstance(anomaly, dict):
            anomaly = {}
        anomaly_score = anomaly.get("score")
        self._db.add_prediction(
            ts=ts,
            hive_id=hive_id,
            model_version=str(payload.get("model_version", "unknown")),
            swarm_risk=float(swarm_risk),
            health_score=float(health_score),
            is_anomaly=bool(anomaly.get("is_anomaly", False)),
            anomaly_kind=str(anomaly.get("kind", "none")),
            anomaly_score=float(anomaly_score) if isinstance(anomaly_score, (int, float)) else 0.0,
        )
        self._stats["predictions_in"] += 1

    def _handle_alert(self, payload: dict[str, Any]) -> None:
        hive_id = payload.get("hive_id")
        ts = parse_ts(payload.get("ts"))
        severity = payload.get("severity")
        source = payload.get("source")
        kind = payload.get("kind")
        message = payload.get("message", "")
        if (
            not isinstance(hive_id, str)
            or ts is None
            or severity not in _ALLOWED_SEVERITIES
            or source not in _ALLOWED_SOURCES
            or not isinstance(kind, str)
            or not kind
        ):
            log.warning("Dropping malformed alert: %r", payload)
            self._stats["dropped"] += 1
            return
        self._db.add_alert(
            ts=ts,
            hive_id=hive_id,
            severity=severity,
            kind=kind,
            message=str(message),
            source=source,
        )
        self._stats["alerts_in"] += 1

    # ── producing ────────────────────────────────────────────────────

    def _produce(self, topic: str, key: str, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, separators=(",", ":"), default=str).encode()
        while True:
            try:
                self._producer.produce(
                    topic, value=data, key=key.encode(), on_delivery=self._on_delivery
                )
                return
            except BufferError:
                # Local queue full: serve delivery callbacks, then retry.
                self._producer.poll(0.5)

    @staticmethod
    def _on_delivery(err: KafkaError | None, msg: Message) -> None:
        if err is not None:
            log.error("Delivery failed for %s: %s", msg.topic(), err)

    # ── flush / commit / stats ───────────────────────────────────────

    def _maybe_flush_and_commit(self) -> None:
        if self._db.should_flush():
            self._db.flush()
            self._commit()
        elif (
            self._uncommitted > 0
            and self._db.pending == 0
            and (time.monotonic() - self._last_commit) >= self._settings.batch_max_seconds
        ):
            # Everything consumed so far is already durable (or was dropped);
            # commit so the group does not lag behind on quiet topics.
            self._commit()

    def _commit(self) -> None:
        if self._uncommitted == 0:
            return
        try:
            self._consumer.commit(asynchronous=False)
        except KafkaException as exc:
            kerr = exc.args[0] if exc.args else None
            if kerr is not None and kerr.code() == KafkaError._NO_OFFSET:
                pass  # nothing consumed on any partition yet
            else:
                log.error("Offset commit failed: %s", exc)
                return
        self._uncommitted = 0
        self._last_commit = time.monotonic()

    def _maybe_log_stats(self) -> None:
        now = time.monotonic()
        if (now - self._last_stats) < self._settings.stats_interval_seconds:
            return
        self._last_stats = now
        log.info(
            "stats: raw_in=%d enriched_out=%d predictions_in=%d alerts_in=%d "
            "rule_alerts_out=%d alerts_debounced=%d dropped=%d db_rows_written=%d "
            "hives_tracked=%d db_pending=%d",
            self._stats["raw_in"],
            self._stats["enriched_out"],
            self._stats["predictions_in"],
            self._stats["alerts_in"],
            self._stats["rule_alerts_out"],
            self._stats["alerts_debounced"],
            self._stats["dropped"],
            self._db.rows_written_total,
            len(self._windows),
            self._db.pending,
        )
