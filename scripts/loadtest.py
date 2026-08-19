#!/usr/bin/env python3
"""MQTT load generator for the Beelieve ingestion bridge.

Hammers the broker with contract-shaped telemetry from a fleet of synthetic
hives at a fixed message rate, optionally mixing in malformed payloads to
exercise the DLQ path, then prints a throughput/ack summary.

This is a standalone dev tool: it only needs paho-mqtt.

Usage:
    python scripts/loadtest.py --hives 50 --rate 500 --duration 30
    python scripts/loadtest.py --invalid-fraction 0.1        # 10% garbage -> DLQ

Connection settings default to localhost:1883 and can be overridden by flags
or the usual MQTT_* environment variables.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import signal
import sys
import threading
import time
from datetime import UTC, datetime
from types import FrameType
from typing import Any

import paho.mqtt.client as mqtt

APIARY_ID = "apiary-load-01"
BAND_NAMES = ("b100_200", "b200_300", "b300_400", "b400_500", "b500_600")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default=os.environ.get("MQTT_HOST", "localhost"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("MQTT_PORT", "1883"))
    )
    parser.add_argument("--username", default=os.environ.get("MQTT_USERNAME"))
    parser.add_argument("--password", default=os.environ.get("MQTT_PASSWORD"))
    parser.add_argument(
        "--hives", type=int, default=50, help="number of synthetic hives (default 50)"
    )
    parser.add_argument(
        "--rate",
        type=float,
        default=200.0,
        help="total messages per second across all hives (default 200)",
    )
    parser.add_argument(
        "--duration", type=float, default=30.0, help="seconds to run (default 30)"
    )
    parser.add_argument(
        "--invalid-fraction",
        type=float,
        default=0.0,
        help="fraction of payloads deliberately malformed, 0..1 (default 0)",
    )
    parser.add_argument("--seed", type=int, default=None, help="RNG seed")
    parser.add_argument("--qos", type=int, choices=(0, 1), default=1)
    args = parser.parse_args()
    if not 0.0 <= args.invalid_fraction <= 1.0:
        parser.error("--invalid-fraction must be within [0, 1]")
    if args.rate <= 0 or args.hives <= 0 or args.duration <= 0:
        parser.error("--rate, --hives and --duration must be positive")
    return args


def valid_payload(hive_id: str, rng: random.Random) -> dict[str, Any]:
    raw = [rng.uniform(0.5, 1.5) for _ in BAND_NAMES]
    total = sum(raw)
    return {
        "hive_id": hive_id,
        "apiary_id": APIARY_ID,
        "ts": datetime.now(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "temp_brood_c": round(rng.uniform(34.5, 35.5), 2),
        "temp_ambient_c": round(rng.uniform(15.0, 35.0), 2),
        "humidity_pct": round(rng.uniform(40.0, 75.0), 1),
        "weight_kg": round(rng.uniform(30.0, 60.0), 2),
        "audio_db": round(rng.uniform(40.0, 60.0), 1),
        "audio_bands": {n: round(v / total, 3) for n, v in zip(BAND_NAMES, raw, strict=False)},
        "co2_ppm": rng.randint(1000, 8000),
        "battery_v": round(rng.uniform(3.5, 4.2), 3),
        "fw": "loadtest",
    }


def corrupt(payload: dict[str, Any], rng: random.Random) -> bytes:
    """Break a valid payload in one of several contract-violating ways."""
    mode = rng.randrange(4)
    if mode == 0:  # missing required field
        payload.pop("hive_id", None)
        return json.dumps(payload).encode()
    if mode == 1:  # naive / garbage timestamp
        payload["ts"] = "yesterday at noon"
        return json.dumps(payload).encode()
    if mode == 2:  # out-of-range sensor value
        payload["humidity_pct"] = 4200.0
        return json.dumps(payload).encode()
    return json.dumps(payload).encode()[:-7]  # truncated JSON


class LoadTest:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.rng = random.Random(args.seed)
        self.hive_ids = [f"KZ-LOAD-{i:04d}" for i in range(1, args.hives + 1)]
        self.sent = 0
        self.sent_invalid = 0
        self.acked = 0
        self.errors = 0
        self.stop = threading.Event()
        self._lock = threading.Lock()

        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id="beelieve-loadtest",
            protocol=mqtt.MQTTv311,
        )
        if args.username:
            self.client.username_pw_set(args.username, args.password)
        self.client.on_publish = self._on_publish

    def _on_publish(
        self,
        client: mqtt.Client,
        userdata: Any,
        mid: int,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        with self._lock:
            if reason_code.is_failure:
                self.errors += 1
            else:
                self.acked += 1

    def run(self) -> int:
        args = self.args
        self.client.connect(args.host, args.port, keepalive=30)
        self.client.loop_start()

        interval = 1.0 / args.rate
        deadline = time.monotonic() + args.duration
        next_send = time.monotonic()
        started = time.monotonic()
        last_report = started

        while not self.stop.is_set() and time.monotonic() < deadline:
            now = time.monotonic()
            if now < next_send:
                time.sleep(min(next_send - now, 0.01))
                continue
            next_send += interval

            hive_id = self.rng.choice(self.hive_ids)
            topic = f"beelieve/{APIARY_ID}/{hive_id}/telemetry"
            payload = valid_payload(hive_id, self.rng)
            if self.rng.random() < args.invalid_fraction:
                body = corrupt(payload, self.rng)
                self.sent_invalid += 1
            else:
                body = json.dumps(payload, separators=(",", ":")).encode()
            self.client.publish(topic, body, qos=args.qos)
            self.sent += 1

            if now - last_report >= 5.0:
                elapsed = now - started
                print(
                    f"  {elapsed:6.1f}s  sent={self.sent}  acked={self.acked}  "
                    f"rate={self.sent / elapsed:.0f} msg/s",
                    flush=True,
                )
                last_report = now

        # Let in-flight QoS-1 messages drain before disconnecting.
        drain_deadline = time.monotonic() + 10.0
        while time.monotonic() < drain_deadline:
            with self._lock:
                if self.acked + self.errors >= self.sent:
                    break
            time.sleep(0.05)
        self.client.disconnect()
        self.client.loop_stop()

        elapsed = time.monotonic() - started
        print()
        print(f"hives            {args.hives}")
        print(f"duration         {elapsed:.1f}s")
        print(f"sent             {self.sent} ({self.sent_invalid} deliberately invalid)")
        print(f"acked            {self.acked}")
        print(f"publish errors   {self.errors}")
        print(f"throughput       {self.sent / elapsed:.1f} msg/s")
        unacked = self.sent - self.acked - self.errors
        if unacked:
            print(f"WARNING: {unacked} message(s) never acknowledged before timeout")
            return 1
        return 0


def main() -> int:
    args = parse_args()
    test = LoadTest(args)

    def handle_signal(signum: int, frame: FrameType | None) -> None:
        print("\ninterrupted; draining...", flush=True)
        test.stop.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    print(
        f"load test: {args.hives} hives -> mqtt://{args.host}:{args.port}, "
        f"{args.rate:.0f} msg/s for {args.duration:.0f}s "
        f"({args.invalid_fraction:.0%} invalid)"
    )
    return test.run()


if __name__ == "__main__":
    sys.exit(main())
