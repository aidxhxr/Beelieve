"""Hive simulator entrypoint: one MQTT client (with LWT) per simulated hive."""

from __future__ import annotations

import json
import logging
import random
import signal
import sys
import threading
from types import FrameType
from typing import Any

import paho.mqtt.client as mqtt

from app import __version__
from app.config import Settings
from app.hive import HiveProfile, SimulatedHive

logger = logging.getLogger("beelieve.simulator")

# Scripted anomalies (1-based hive numbers), applied when SIM_NUM_HIVES covers them:
# two hives ramp into pre-swarm acoustics, one is queenless.
PRE_SWARM_HIVE_NUMBERS: frozenset[int] = frozenset({3, 8})
QUEENLESS_HIVE_NUMBERS: frozenset[int] = frozenset({5})


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )


def profile_for(hive_number: int) -> HiveProfile:
    if hive_number in PRE_SWARM_HIVE_NUMBERS:
        return HiveProfile.PRE_SWARM
    if hive_number in QUEENLESS_HIVE_NUMBERS:
        return HiveProfile.QUEENLESS
    return HiveProfile.NORMAL


class HiveNode:
    """One simulated sensor node: an MQTT client bound to one hive's topics."""

    def __init__(self, hive: SimulatedHive, settings: Settings) -> None:
        self.hive = hive
        base = f"beelieve/{hive.apiary_id}/{hive.hive_id}"
        self.telemetry_topic = f"{base}/telemetry"
        self.status_topic = f"{base}/status"

        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"sim-{hive.hive_id}",
            protocol=mqtt.MQTTv311,
        )
        if settings.mqtt_username:
            self._client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
        # Last will: broker marks this hive offline if the node dies uncleanly.
        self._client.will_set(self.status_topic, payload="offline", qos=1, retain=True)
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)
        self._client.on_connect = self._on_connect
        self._settings = settings

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        if reason_code.is_failure:
            logger.error("%s: MQTT connect failed: %s", self.hive.hive_id, reason_code)
            return
        client.publish(self.status_topic, payload="online", qos=1, retain=True)
        logger.info("%s connected; status -> online", self.hive.hive_id)

    def connect(self) -> None:
        self._client.connect(
            self._settings.mqtt_host,
            self._settings.mqtt_port,
            keepalive=self._settings.mqtt_keepalive_seconds,
        )
        self._client.loop_start()

    def publish_telemetry(self) -> None:
        payload = self.hive.sample()
        info = self._client.publish(
            self.telemetry_topic,
            payload=json.dumps(payload, separators=(",", ":")),
            qos=1,
        )
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            logger.warning(
                "%s: publish queued with rc=%s (broker offline?)",
                self.hive.hive_id,
                info.rc,
            )
        logger.debug("%s telemetry: %s", self.hive.hive_id, payload)

    def shutdown(self) -> None:
        """Graceful stop: retained offline status, then a clean disconnect."""
        try:
            info = self._client.publish(
                self.status_topic, payload="offline", qos=1, retain=True
            )
            info.wait_for_publish(timeout=5.0)
        except (RuntimeError, ValueError) as exc:
            logger.warning("%s: could not publish offline status: %s", self.hive.hive_id, exc)
        self._client.disconnect()
        self._client.loop_stop()


def build_nodes(settings: Settings) -> list[HiveNode]:
    nodes: list[HiveNode] = []
    for number in range(1, settings.sim_num_hives + 1):
        hive_id = f"KZ-ALA-{number:04d}"
        rng = (
            random.Random(settings.sim_seed + number)
            if settings.sim_seed is not None
            else random.Random()
        )
        hive = SimulatedHive(
            hive_id,
            settings.sim_apiary_id,
            profile=profile_for(number),
            rng=rng,
            fw=settings.sim_firmware_version,
        )
        nodes.append(HiveNode(hive, settings))
    return nodes


def main() -> int:
    settings = Settings()
    configure_logging(settings.log_level)
    logger.info(
        "starting hive simulator v%s: %d hives in %s, every %.1fs -> mqtt://%s:%d",
        __version__,
        settings.sim_num_hives,
        settings.sim_apiary_id,
        settings.sim_interval_seconds,
        settings.mqtt_host,
        settings.mqtt_port,
    )

    nodes = build_nodes(settings)
    for node in nodes:
        logger.info(
            "hive %s profile=%s", node.hive.hive_id, node.hive.profile.value
        )

    stop = threading.Event()

    def handle_signal(signum: int, frame: FrameType | None) -> None:
        logger.info("received %s; shutting down", signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    for node in nodes:
        node.connect()

    ticks = 0
    try:
        while not stop.wait(settings.sim_interval_seconds):
            for node in nodes:
                node.publish_telemetry()
            ticks += 1
            if ticks % 30 == 0:
                logger.info(
                    "published %d telemetry rounds (%d messages)",
                    ticks,
                    ticks * len(nodes),
                )
    finally:
        logger.info("stopping: marking hives offline and disconnecting")
        for node in nodes:
            node.shutdown()
        logger.info("hive simulator stopped after %d rounds", ticks)

    return 0


if __name__ == "__main__":
    sys.exit(main())
