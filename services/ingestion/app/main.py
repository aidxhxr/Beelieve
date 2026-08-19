"""Ingestion bridge entrypoint: wires paho-mqtt and confluent-kafka to Bridge."""

from __future__ import annotations

import logging
import signal
import sys
import threading
from types import FrameType
from typing import Any

import paho.mqtt.client as mqtt
from confluent_kafka import Producer

from app import __version__
from app.bridge import Bridge
from app.config import Settings

logger = logging.getLogger("beelieve.ingestion")


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )


def build_producer(settings: Settings) -> Producer:
    return Producer(
        {
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "client.id": settings.mqtt_client_id,
            "acks": "all",
            "enable.idempotence": True,
            "linger.ms": 50,
            "compression.type": "lz4",
        }
    )


def build_mqtt_client(settings: Settings, bridge: Bridge) -> mqtt.Client:
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=settings.mqtt_client_id,
        clean_session=False,
        protocol=mqtt.MQTTv311,
    )
    if settings.mqtt_username:
        client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    def on_connect(
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        if reason_code.is_failure:
            logger.error("MQTT connect failed: %s", reason_code)
            return
        logger.info(
            "MQTT connected to %s:%d; subscribing to %s and %s (QoS 1)",
            settings.mqtt_host,
            settings.mqtt_port,
            settings.telemetry_topic_filter,
            settings.status_topic_filter,
        )
        client.subscribe(
            [
                (settings.telemetry_topic_filter, 1),
                (settings.status_topic_filter, 1),
            ]
        )

    def on_disconnect(
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.DisconnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        if reason_code.is_failure:
            logger.warning("MQTT disconnected unexpectedly: %s (will reconnect)", reason_code)
        else:
            logger.info("MQTT disconnected")

    def on_message(client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        bridge.handle_message(msg.topic, msg.payload)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    return client


def main() -> int:
    settings = Settings()
    configure_logging(settings.log_level)
    logger.info(
        "starting ingestion bridge v%s (mqtt=%s:%d kafka=%s)",
        __version__,
        settings.mqtt_host,
        settings.mqtt_port,
        settings.kafka_bootstrap_servers,
    )

    producer = build_producer(settings)
    bridge = Bridge(
        producer,
        raw_topic=settings.kafka_raw_topic,
        dlq_topic=settings.kafka_dlq_topic,
        alerts_topic=settings.kafka_alerts_topic,
    )
    client = build_mqtt_client(settings, bridge)

    stop = threading.Event()

    def handle_signal(signum: int, frame: FrameType | None) -> None:
        logger.info("received %s; shutting down", signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    client.connect(
        settings.mqtt_host, settings.mqtt_port, keepalive=settings.mqtt_keepalive_seconds
    )
    client.loop_start()

    try:
        while not stop.wait(settings.stats_interval_seconds):
            bridge.log_stats()
            producer.poll(0)
    finally:
        logger.info("draining: disconnecting MQTT, flushing Kafka producer")
        client.disconnect()
        client.loop_stop()
        remaining = producer.flush(settings.shutdown_flush_timeout_seconds)
        if remaining:
            logger.error("shutdown flush timed out with %d undelivered message(s)", remaining)
        bridge.log_stats()
        logger.info("ingestion bridge stopped")

    return 0


if __name__ == "__main__":
    sys.exit(main())
