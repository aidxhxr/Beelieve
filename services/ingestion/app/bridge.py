"""Core MQTT -> Kafka bridging logic.

This module is deliberately free of paho-mqtt and confluent-kafka imports so
the validation / routing logic is unit-testable with fakes. The wiring to real
clients lives in :mod:`app.main`.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, NamedTuple, Protocol

from pydantic import ValidationError

from app.models import Alert, TelemetryPayload, isoformat_utc, utc_now

logger = logging.getLogger("beelieve.ingestion")

TELEMETRY_CHANNEL = "telemetry"
STATUS_CHANNEL = "status"
_VALID_CHANNELS = frozenset({TELEMETRY_CHANNEL, STATUS_CHANNEL})


class TopicInfo(NamedTuple):
    apiary_id: str
    hive_id: str
    channel: str


def parse_topic(topic: str) -> TopicInfo:
    """Parse ``beelieve/{apiary_id}/{hive_id}/{telemetry|status}``.

    Raises ``ValueError`` for anything that does not match the contract.
    """
    parts = topic.split("/")
    if len(parts) != 4:
        raise ValueError(f"topic {topic!r} does not have 4 levels")
    root, apiary_id, hive_id, channel = parts
    if root != "beelieve":
        raise ValueError(f"topic {topic!r} does not start with 'beelieve'")
    if not apiary_id or not hive_id:
        raise ValueError(f"topic {topic!r} has an empty apiary_id or hive_id level")
    if channel not in _VALID_CHANNELS:
        raise ValueError(f"topic {topic!r} channel must be 'telemetry' or 'status'")
    return TopicInfo(apiary_id=apiary_id, hive_id=hive_id, channel=channel)


class ProducerLike(Protocol):
    """The subset of confluent_kafka.Producer the bridge relies on."""

    def produce(
        self,
        topic: str,
        value: bytes | None = None,
        key: bytes | None = None,
        on_delivery: Any = None,
    ) -> None: ...

    def poll(self, timeout: float) -> int: ...

    def flush(self, timeout: float) -> int: ...


class Bridge:
    """Validates MQTT messages and routes them to the right Kafka topic."""

    def __init__(
        self,
        producer: ProducerLike,
        *,
        raw_topic: str = "hive.telemetry.raw",
        dlq_topic: str = "hive.telemetry.dlq",
        alerts_topic: str = "hive.alerts",
    ) -> None:
        self._producer = producer
        self._raw_topic = raw_topic
        self._dlq_topic = dlq_topic
        self._alerts_topic = alerts_topic
        self._lock = threading.Lock()
        self._received = 0
        self._valid = 0
        self._invalid = 0
        self._status_events = 0
        self._delivery_failures = 0

    # ── entry point ──────────────────────────────────────────────────

    def handle_message(self, topic: str, payload: bytes) -> None:
        """Route one inbound MQTT message. Never raises."""
        with self._lock:
            self._received += 1
        try:
            info = parse_topic(topic)
        except ValueError as exc:
            logger.debug("unroutable topic %r: %s", topic, exc)
            self._count_invalid()
            self._to_dlq(topic, payload, str(exc), hive_id=None)
            return

        if info.channel == TELEMETRY_CHANNEL:
            self._handle_telemetry(info, topic, payload)
        else:
            self._handle_status(info, payload)

    # ── telemetry ────────────────────────────────────────────────────

    def _handle_telemetry(self, info: TopicInfo, topic: str, payload: bytes) -> None:
        try:
            data = json.loads(payload)
            if not isinstance(data, dict):
                raise ValueError("payload must be a JSON object")
            reading = TelemetryPayload.model_validate(data)
        except (ValueError, ValidationError) as exc:
            reason = _validation_reason(exc)
            logger.debug("invalid telemetry on %s: %s", topic, reason)
            self._count_invalid()
            self._to_dlq(topic, payload, reason, hive_id=info.hive_id)
            return

        if reading.hive_id != info.hive_id or reading.apiary_id != info.apiary_id:
            reason = (
                "topic/payload identity mismatch: topic says "
                f"apiary_id={info.apiary_id!r} hive_id={info.hive_id!r}, payload says "
                f"apiary_id={reading.apiary_id!r} hive_id={reading.hive_id!r}"
            )
            logger.debug("rejected telemetry on %s: %s", topic, reason)
            self._count_invalid()
            self._to_dlq(topic, payload, reason, hive_id=info.hive_id)
            return

        record = reading.to_kafka_record(ingested_at=utc_now())
        self._produce(
            self._raw_topic,
            key=reading.hive_id.encode("utf-8"),
            value=json.dumps(record, separators=(",", ":")).encode("utf-8"),
        )
        with self._lock:
            self._valid += 1
        logger.debug(
            "telemetry ok hive=%s apiary=%s ts=%s -> %s",
            reading.hive_id,
            reading.apiary_id,
            record["ts"],
            self._raw_topic,
        )

    # ── status / LWT ─────────────────────────────────────────────────

    def _handle_status(self, info: TopicInfo, payload: bytes) -> None:
        status = payload.decode("utf-8", errors="replace").strip().lower()
        with self._lock:
            self._status_events += 1
        logger.debug("status hive=%s apiary=%s -> %r", info.hive_id, info.apiary_id, status)

        if status != "offline":
            return

        alert = Alert(
            hive_id=info.hive_id,
            ts=utc_now(),
            severity="warning",
            kind="sensor_offline",
            message=(
                f"Sensor node for hive {info.hive_id} in apiary {info.apiary_id} "
                "went offline (MQTT last-will)"
            ),
            source="rule",
        )
        self._produce(
            self._alerts_topic,
            key=info.hive_id.encode("utf-8"),
            value=json.dumps(alert.to_kafka_record(), separators=(",", ":")).encode(
                "utf-8"
            ),
        )

    # ── DLQ ──────────────────────────────────────────────────────────

    def _to_dlq(
        self, topic: str, payload: bytes, error: str, *, hive_id: str | None
    ) -> None:
        envelope = {
            "topic": topic,
            "payload": payload.decode("utf-8", errors="replace"),
            "error": error,
            "ingested_at": isoformat_utc(utc_now()),
        }
        self._produce(
            self._dlq_topic,
            key=hive_id.encode("utf-8") if hive_id else None,
            value=json.dumps(envelope, separators=(",", ":")).encode("utf-8"),
        )

    # ── plumbing ─────────────────────────────────────────────────────

    def _produce(self, kafka_topic: str, *, key: bytes | None, value: bytes) -> None:
        try:
            self._producer.produce(
                kafka_topic, value=value, key=key, on_delivery=self._on_delivery
            )
        except BufferError:
            # Local queue full: give librdkafka a moment to drain, then retry once.
            logger.warning("producer queue full; draining before retrying %s", kafka_topic)
            self._producer.poll(1.0)
            self._producer.produce(
                kafka_topic, value=value, key=key, on_delivery=self._on_delivery
            )
        # Serve delivery callbacks without blocking the MQTT thread.
        self._producer.poll(0)

    def _on_delivery(self, err: Any, msg: Any) -> None:
        if err is not None:
            with self._lock:
                self._delivery_failures += 1
            logger.error(
                "kafka delivery failed topic=%s key=%r: %s",
                msg.topic(),
                msg.key(),
                err,
            )
        else:
            logger.debug(
                "kafka delivered topic=%s partition=%s offset=%s",
                msg.topic(),
                msg.partition(),
                msg.offset(),
            )

    def _count_invalid(self) -> None:
        with self._lock:
            self._invalid += 1

    # ── observability ────────────────────────────────────────────────

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "received": self._received,
                "valid": self._valid,
                "invalid": self._invalid,
                "status_events": self._status_events,
                "delivery_failures": self._delivery_failures,
            }

    def log_stats(self) -> None:
        s = self.stats()
        logger.info(
            "stats received=%d valid=%d invalid=%d status_events=%d delivery_failures=%d",
            s["received"],
            s["valid"],
            s["invalid"],
            s["status_events"],
            s["delivery_failures"],
        )


def _validation_reason(exc: Exception) -> str:
    """Compact, single-line reason string for the DLQ envelope."""
    if isinstance(exc, ValidationError):
        parts = [
            f"{'.'.join(str(loc) for loc in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()
        ]
        return "validation error: " + "; ".join(parts)
    return f"{type(exc).__name__}: {exc}"
