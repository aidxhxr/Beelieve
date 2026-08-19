"""Batched TimescaleDB writer (psycopg v3).

Buffers sensor readings, predictions, alerts and hive last-seen updates in
memory, flushing them in a single transaction with ``executemany`` when the
buffer reaches ``batch_max_rows`` or ``batch_max_seconds`` has elapsed —
whichever comes first. Connection failures are retried with capped
exponential backoff; ``flush`` only returns after the data is committed,
which is what allows the Kafka consumer to commit offsets afterwards
(at-least-once delivery).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

import psycopg

from .config import Settings

log = logging.getLogger(__name__)

_INSERT_READING = """
INSERT INTO sensor_readings (
    time, hive_id, temp_brood_c, temp_ambient_c, humidity_pct, weight_kg,
    audio_db, audio_b100_200, audio_b200_300, audio_b300_400, audio_b400_500,
    audio_b500_600, co2_ppm, battery_v, fw
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

_UPDATE_LAST_SEEN = """
UPDATE hives
SET last_seen_at = GREATEST(COALESCE(last_seen_at, %s), %s)
WHERE id = %s
"""

_INSERT_PREDICTION = """
INSERT INTO predictions (
    time, hive_id, model_version, swarm_risk, health_score,
    is_anomaly, anomaly_kind, anomaly_score
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
"""

_INSERT_ALERT = """
INSERT INTO alerts (time, hive_id, severity, kind, message, source)
VALUES (%s, %s, %s, %s, %s, %s)
"""


class BatchedWriter:
    """Buffered, retrying writer for the three hypertables + hives.last_seen_at."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._conn: psycopg.Connection[Any] | None = None
        self._readings: list[tuple[Any, ...]] = []
        self._predictions: list[tuple[Any, ...]] = []
        self._alerts: list[tuple[Any, ...]] = []
        self._last_seen: dict[str, datetime] = {}
        self._last_flush = time.monotonic()
        self.rows_written_total = 0

    # ── buffering ────────────────────────────────────────────────────

    def add_reading(self, ts: datetime, hive_id: str, reading: dict[str, Any]) -> None:
        """Buffer one raw sensor reading and bump the hive's last-seen time."""
        bands = reading.get("audio_bands")
        if not isinstance(bands, dict):
            bands = {}
        self._readings.append(
            (
                ts,
                hive_id,
                reading.get("temp_brood_c"),
                reading.get("temp_ambient_c"),
                reading.get("humidity_pct"),
                reading.get("weight_kg"),
                reading.get("audio_db"),
                bands.get("b100_200"),
                bands.get("b200_300"),
                bands.get("b300_400"),
                bands.get("b400_500"),
                bands.get("b500_600"),
                reading.get("co2_ppm"),
                reading.get("battery_v"),
                reading.get("fw"),
            )
        )
        current = self._last_seen.get(hive_id)
        if current is None or ts > current:
            self._last_seen[hive_id] = ts

    def add_prediction(
        self,
        ts: datetime,
        hive_id: str,
        model_version: str,
        swarm_risk: float,
        health_score: float,
        is_anomaly: bool,
        anomaly_kind: str,
        anomaly_score: float,
    ) -> None:
        """Buffer one model prediction row."""
        self._predictions.append(
            (ts, hive_id, model_version, swarm_risk, health_score,
             is_anomaly, anomaly_kind, anomaly_score)
        )

    def add_alert(
        self,
        ts: datetime,
        hive_id: str,
        severity: str,
        kind: str,
        message: str,
        source: str,
    ) -> None:
        """Buffer one alert row."""
        self._alerts.append((ts, hive_id, severity, kind, message, source))

    # ── flush policy ─────────────────────────────────────────────────

    @property
    def pending(self) -> int:
        """Number of buffered rows not yet committed."""
        return len(self._readings) + len(self._predictions) + len(self._alerts)

    def should_flush(self) -> bool:
        """True when the row-count or time threshold has been reached."""
        if self.pending == 0:
            return False
        if self.pending >= self._settings.batch_max_rows:
            return True
        return (time.monotonic() - self._last_flush) >= self._settings.batch_max_seconds

    # ── flushing ─────────────────────────────────────────────────────

    def flush(self) -> int:
        """Write all buffered rows in one transaction; retry until it succeeds.

        Returns the number of rows written. Safe to call with empty buffers.
        """
        if self.pending == 0 and not self._last_seen:
            self._last_flush = time.monotonic()
            return 0

        rows = self.pending
        delay = self._settings.db_retry_initial_seconds
        while True:
            try:
                conn = self._connect()
                with conn.cursor() as cur:
                    if self._readings:
                        cur.executemany(_INSERT_READING, self._readings)
                    if self._last_seen:
                        cur.executemany(
                            _UPDATE_LAST_SEEN,
                            [(ts, ts, hive_id) for hive_id, ts in self._last_seen.items()],
                        )
                    if self._predictions:
                        cur.executemany(_INSERT_PREDICTION, self._predictions)
                    if self._alerts:
                        cur.executemany(_INSERT_ALERT, self._alerts)
                conn.commit()
                break
            except psycopg.Error as exc:
                log.warning(
                    "DB flush failed (%s: %s); retrying in %.1fs",
                    type(exc).__name__, exc, delay,
                )
                self._close_connection()
                time.sleep(delay)
                delay = min(delay * 2, self._settings.db_retry_max_seconds)

        self._readings.clear()
        self._predictions.clear()
        self._alerts.clear()
        self._last_seen.clear()
        self._last_flush = time.monotonic()
        self.rows_written_total += rows
        return rows

    # ── connection management ────────────────────────────────────────

    def _connect(self) -> psycopg.Connection[Any]:
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(self._settings.database_url, autocommit=False)
            log.info("Connected to TimescaleDB")
        return self._conn

    def _close_connection(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001 — best-effort close on a broken conn
                pass
            self._conn = None

    def close(self) -> None:
        """Close the connection. Callers must flush first."""
        self._close_connection()
