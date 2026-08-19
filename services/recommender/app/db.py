"""TimescaleDB access for the recommender service (psycopg 3 + pool).

Produces the shared context dict documented in app/prompts.py.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import get_settings
from app.parse import ParsedRecommendation

_pool: ConnectionPool | None = None


def open_pool() -> ConnectionPool:
    """Open (or return) the process-wide connection pool."""
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = ConnectionPool(
            conninfo=settings.database_url,
            min_size=1,
            max_size=8,
            kwargs={"row_factory": dict_row},
            open=True,
            name="recommender",
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def ping() -> bool:
    """Cheap connectivity check for /healthz."""
    try:
        with open_pool().connection(timeout=3) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def fetch_context(hive_id: str, alerts_lookback_hours: int = 72) -> dict[str, Any] | None:
    """Gather hive/apiary metadata, latest reading, latest prediction, recent alerts.

    Returns None when the hive does not exist. All timestamps are ISO strings so
    the dict is both prompt-renderable and JSON-serializable (for the
    recommendations.context JSONB column).
    """
    with open_pool().connection() as conn:
        hive_row = conn.execute(
            """
            SELECT h.id, h.name, h.hive_type, h.queen_year, h.frames,
                   h.installed_at, h.is_active, h.last_seen_at,
                   a.id AS apiary_id, a.name AS apiary_name, a.region,
                   a.latitude, a.longitude
            FROM hives h
            JOIN apiaries a ON a.id = h.apiary_id
            WHERE h.id = %s
            """,
            (hive_id,),
        ).fetchone()
        if hive_row is None:
            return None

        reading = conn.execute(
            """
            SELECT time, temp_brood_c, temp_ambient_c, humidity_pct, weight_kg,
                   audio_db, co2_ppm, battery_v
            FROM sensor_readings
            WHERE hive_id = %s
            ORDER BY time DESC
            LIMIT 1
            """,
            (hive_id,),
        ).fetchone()

        prediction = conn.execute(
            """
            SELECT time, model_version, swarm_risk, health_score,
                   is_anomaly, anomaly_kind, anomaly_score
            FROM predictions
            WHERE hive_id = %s
            ORDER BY time DESC
            LIMIT 1
            """,
            (hive_id,),
        ).fetchone()

        alerts = conn.execute(
            """
            SELECT time, severity, kind, message
            FROM alerts
            WHERE hive_id = %s AND time >= now() - make_interval(hours => %s)
            ORDER BY time DESC
            LIMIT 20
            """,
            (hive_id, alerts_lookback_hours),
        ).fetchall()

    return {
        "hive": {
            "id": hive_row["id"],
            "name": hive_row["name"],
            "hive_type": hive_row["hive_type"],
            "queen_year": hive_row["queen_year"],
            "frames": hive_row["frames"],
        },
        "apiary": {
            "id": hive_row["apiary_id"],
            "name": hive_row["apiary_name"],
            "region": hive_row["region"],
            "latitude": hive_row["latitude"],
            "longitude": hive_row["longitude"],
        },
        "reading": (
            {key: _iso(value) for key, value in reading.items()} if reading else None
        ),
        "prediction": (
            {key: _iso(value) for key, value in prediction.items()} if prediction else None
        ),
        "alerts": [{key: _iso(value) for key, value in a.items()} for a in alerts],
    }


def insert_recommendations(
    hive_id: str,
    locale: str,
    model_id: str,
    recs: list[ParsedRecommendation],
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    """Persist recommendations; return stored rows (id, created_at included)."""
    stored: list[dict[str, Any]] = []
    with open_pool().connection() as conn:
        for rec in recs:
            row = conn.execute(
                """
                INSERT INTO recommendations
                    (hive_id, locale, model_id, priority, title, body, context)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                RETURNING id, hive_id, created_at, locale, model_id,
                          priority, title, body
                """,
                (
                    hive_id,
                    locale,
                    model_id,
                    rec.priority,
                    rec.title,
                    rec.body,
                    json.dumps(context, default=str),
                ),
            ).fetchone()
            assert row is not None
            stored.append(row)
    return stored


def fetch_recent_recommendations(hive_id: str, limit: int = 10) -> list[dict[str, Any]]:
    with open_pool().connection() as conn:
        return conn.execute(
            """
            SELECT id, hive_id, created_at, locale, model_id, priority, title, body
            FROM recommendations
            WHERE hive_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (hive_id, limit),
        ).fetchall()


def hive_exists(hive_id: str) -> bool:
    with open_pool().connection() as conn:
        return conn.execute("SELECT 1 FROM hives WHERE id = %s", (hive_id,)).fetchone() is not None
