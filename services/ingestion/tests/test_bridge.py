"""Routing tests for app.bridge using a fake producer (no network)."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
from app.bridge import Bridge, TopicInfo, parse_topic

TELEMETRY_TOPIC = "beelieve/apiary-almaty-01/KZ-ALA-0042/telemetry"
STATUS_TOPIC = "beelieve/apiary-almaty-01/KZ-ALA-0042/status"

VALID_PAYLOAD: dict[str, Any] = {
    "hive_id": "KZ-ALA-0042",
    "apiary_id": "apiary-almaty-01",
    "ts": "2026-08-18T12:00:00Z",
    "temp_brood_c": 34.8,
    "weight_kg": 42.35,
    "audio_bands": {"b100_200": 0.31, "b400_500": 0.16},
}


@dataclass
class ProducedMessage:
    topic: str
    key: bytes | None
    value: bytes

    @property
    def json(self) -> dict[str, Any]:
        return json.loads(self.value)


@dataclass
class FakeProducer:
    """In-memory stand-in for confluent_kafka.Producer."""

    messages: list[ProducedMessage] = field(default_factory=list)
    fail_delivery: bool = False
    polled: int = 0
    flushed: bool = False

    def produce(
        self,
        topic: str,
        value: bytes | None = None,
        key: bytes | None = None,
        on_delivery: Callable[[Any, Any], None] | None = None,
    ) -> None:
        assert value is not None
        self.messages.append(ProducedMessage(topic=topic, key=key, value=value))
        if on_delivery is not None:
            err = "broker unreachable" if self.fail_delivery else None
            on_delivery(err, _FakeDeliveredMessage(topic, key))

    def poll(self, timeout: float) -> int:
        self.polled += 1
        return 0

    def flush(self, timeout: float) -> int:
        self.flushed = True
        return 0

    def by_topic(self, topic: str) -> list[ProducedMessage]:
        return [m for m in self.messages if m.topic == topic]


class _FakeDeliveredMessage:
    def __init__(self, topic: str, key: bytes | None) -> None:
        self._topic = topic
        self._key = key

    def topic(self) -> str:
        return self._topic

    def key(self) -> bytes | None:
        return self._key

    def partition(self) -> int:
        return 0

    def offset(self) -> int:
        return 0


@pytest.fixture()
def producer() -> FakeProducer:
    return FakeProducer()


@pytest.fixture()
def bridge(producer: FakeProducer) -> Bridge:
    return Bridge(
        producer,
        raw_topic="hive.telemetry.raw",
        dlq_topic="hive.telemetry.dlq",
        alerts_topic="hive.alerts",
    )


class TestParseTopic:
    def test_telemetry_topic(self) -> None:
        assert parse_topic(TELEMETRY_TOPIC) == TopicInfo(
            apiary_id="apiary-almaty-01", hive_id="KZ-ALA-0042", channel="telemetry"
        )

    def test_status_topic(self) -> None:
        assert parse_topic(STATUS_TOPIC).channel == "status"

    @pytest.mark.parametrize(
        "topic",
        [
            "beelieve/apiary/hive",  # too few levels
            "beelieve/apiary/hive/telemetry/extra",  # too many levels
            "other/apiary/hive/telemetry",  # wrong root
            "beelieve//hive/telemetry",  # empty apiary
            "beelieve/apiary//telemetry",  # empty hive
            "beelieve/apiary/hive/commands",  # unknown channel
        ],
    )
    def test_invalid_topics_raise(self, topic: str) -> None:
        with pytest.raises(ValueError):
            parse_topic(topic)


class TestTelemetryRouting:
    def test_valid_reading_goes_to_raw_keyed_by_hive_id(
        self, bridge: Bridge, producer: FakeProducer
    ) -> None:
        bridge.handle_message(TELEMETRY_TOPIC, json.dumps(VALID_PAYLOAD).encode())

        raw = producer.by_topic("hive.telemetry.raw")
        assert len(raw) == 1
        assert producer.by_topic("hive.telemetry.dlq") == []
        msg = raw[0]
        assert msg.key == b"KZ-ALA-0042"
        body = msg.json
        assert body["hive_id"] == "KZ-ALA-0042"
        assert body["ts"] == "2026-08-18T12:00:00Z"
        assert body["ingested_at"].endswith("Z")
        assert bridge.stats()["valid"] == 1

    def test_invalid_json_goes_to_dlq_with_reason(
        self, bridge: Bridge, producer: FakeProducer
    ) -> None:
        bridge.handle_message(TELEMETRY_TOPIC, b"{not json")

        assert producer.by_topic("hive.telemetry.raw") == []
        dlq = producer.by_topic("hive.telemetry.dlq")
        assert len(dlq) == 1
        body = dlq[0].json
        assert body["topic"] == TELEMETRY_TOPIC
        assert body["payload"] == "{not json"
        assert body["error"]
        assert bridge.stats()["invalid"] == 1

    def test_schema_violation_goes_to_dlq(
        self, bridge: Bridge, producer: FakeProducer
    ) -> None:
        bad = {**VALID_PAYLOAD, "humidity_pct": 250.0}
        bridge.handle_message(TELEMETRY_TOPIC, json.dumps(bad).encode())

        dlq = producer.by_topic("hive.telemetry.dlq")
        assert len(dlq) == 1
        assert "humidity_pct" in dlq[0].json["error"]

    def test_topic_payload_identity_mismatch_goes_to_dlq(
        self, bridge: Bridge, producer: FakeProducer
    ) -> None:
        mismatched = {**VALID_PAYLOAD, "hive_id": "KZ-ALA-0099"}
        bridge.handle_message(TELEMETRY_TOPIC, json.dumps(mismatched).encode())

        assert producer.by_topic("hive.telemetry.raw") == []
        dlq = producer.by_topic("hive.telemetry.dlq")
        assert len(dlq) == 1
        assert "mismatch" in dlq[0].json["error"]
        # keyed by the topic's hive_id so DLQ triage can group by hive
        assert dlq[0].key == b"KZ-ALA-0042"

    def test_non_object_json_goes_to_dlq(
        self, bridge: Bridge, producer: FakeProducer
    ) -> None:
        bridge.handle_message(TELEMETRY_TOPIC, b"[1, 2, 3]")
        assert len(producer.by_topic("hive.telemetry.dlq")) == 1

    def test_unroutable_topic_goes_to_dlq(
        self, bridge: Bridge, producer: FakeProducer
    ) -> None:
        bridge.handle_message("beelieve/x/y/z/telemetry", b"{}")
        dlq = producer.by_topic("hive.telemetry.dlq")
        assert len(dlq) == 1
        assert dlq[0].key is None


class TestStatusRouting:
    def test_offline_status_produces_sensor_offline_alert(
        self, bridge: Bridge, producer: FakeProducer
    ) -> None:
        bridge.handle_message(STATUS_TOPIC, b"offline")

        alerts = producer.by_topic("hive.alerts")
        assert len(alerts) == 1
        alert = alerts[0].json
        assert alerts[0].key == b"KZ-ALA-0042"
        assert alert["hive_id"] == "KZ-ALA-0042"
        assert alert["kind"] == "sensor_offline"
        assert alert["severity"] == "warning"
        assert alert["source"] == "rule"
        assert alert["message"]
        assert alert["ts"].endswith("Z")

    def test_online_status_produces_nothing(
        self, bridge: Bridge, producer: FakeProducer
    ) -> None:
        bridge.handle_message(STATUS_TOPIC, b"online")
        assert producer.messages == []
        assert bridge.stats()["status_events"] == 1


class TestDeliveryReporting:
    def test_delivery_failures_are_counted(self, producer: FakeProducer) -> None:
        producer.fail_delivery = True
        bridge = Bridge(producer)
        bridge.handle_message(TELEMETRY_TOPIC, json.dumps(VALID_PAYLOAD).encode())
        assert bridge.stats()["delivery_failures"] == 1

    def test_stats_counts_received(self, bridge: Bridge) -> None:
        bridge.handle_message(TELEMETRY_TOPIC, json.dumps(VALID_PAYLOAD).encode())
        bridge.handle_message(TELEMETRY_TOPIC, b"garbage")
        stats = bridge.stats()
        assert stats["received"] == 2
        assert stats["valid"] == 1
        assert stats["invalid"] == 1
