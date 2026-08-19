"""Apiary, hive, readings, predictions, alerts and recommendation endpoints."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from psycopg import AsyncConnection
from psycopg.rows import DictRow

from app.config import get_settings
from app.db import get_db
from app.models import (
    AckAlertRequest,
    AckAlertResponse,
    AlertOut,
    ApiaryOut,
    HiveDetail,
    HiveSummary,
    LatestReading,
    PredictionOut,
    ReadingDaily,
    ReadingHourly,
    ReadingRaw,
    RecommendationOut,
    Resolution,
    UserOut,
)
from app.security import get_current_user

logger = logging.getLogger("beelieve.api.hives")

router = APIRouter(tags=["hives"])

RECOMMENDER_TIMEOUT_S = 30.0


async def ensure_hive_access(
    conn: AsyncConnection[DictRow],
    user: UserOut,
    hive_id: str,
) -> dict[str, Any]:
    """Return the hive row (with apiary metadata) or 404.

    Non-admin users get 404 (not 403) for hives they do not own, so the API
    does not leak which hive ids exist.
    """
    cur = await conn.execute(
        """
        SELECT h.id, h.apiary_id, h.name, h.hive_type, h.queen_year, h.frames,
               h.installed_at, h.is_active, h.last_seen_at, h.created_at,
               a.name AS apiary_name, a.owner_id
        FROM hives h
        JOIN apiaries a ON a.id = h.apiary_id
        WHERE h.id = %(hive_id)s
        """,
        {"hive_id": hive_id},
    )
    row = await cur.fetchone()
    if row is None or (not user.is_admin and row["owner_id"] != user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hive not found")
    return row


# ── Apiaries ─────────────────────────────────────────────────────────


@router.get("/apiaries", response_model=list[ApiaryOut])
async def list_apiaries(
    user: UserOut = Depends(get_current_user),
    conn: AsyncConnection[DictRow] = Depends(get_db),
) -> list[ApiaryOut]:
    """List the user's apiaries with hive counts (admin sees all)."""
    cur = await conn.execute(
        """
        SELECT a.id, a.name, a.latitude, a.longitude, a.region, a.created_at,
               count(h.id) AS hive_count
        FROM apiaries a
        LEFT JOIN hives h ON h.apiary_id = a.id
        WHERE %(is_admin)s OR a.owner_id = %(user_id)s
        GROUP BY a.id
        ORDER BY a.name
        """,
        {"is_admin": user.is_admin, "user_id": user.id},
    )
    rows = await cur.fetchall()
    return [ApiaryOut.model_validate(row) for row in rows]


@router.get("/apiaries/{apiary_id}/hives", response_model=list[HiveDetail])
async def list_apiary_hives(
    apiary_id: str,
    user: UserOut = Depends(get_current_user),
    conn: AsyncConnection[DictRow] = Depends(get_db),
) -> list[HiveDetail]:
    """List all hives in one apiary the user owns."""
    cur = await conn.execute(
        "SELECT id, name, owner_id FROM apiaries WHERE id = %(apiary_id)s",
        {"apiary_id": apiary_id},
    )
    apiary = await cur.fetchone()
    if apiary is None or (not user.is_admin and apiary["owner_id"] != user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Apiary not found")

    cur = await conn.execute(
        """
        SELECT h.id, h.apiary_id, h.name, h.hive_type, h.queen_year, h.frames,
               h.installed_at, h.is_active, h.last_seen_at, h.created_at,
               %(apiary_name)s AS apiary_name
        FROM hives h
        WHERE h.apiary_id = %(apiary_id)s
        ORDER BY h.id
        """,
        {"apiary_id": apiary_id, "apiary_name": apiary["name"]},
    )
    rows = await cur.fetchall()
    return [HiveDetail.model_validate(row) for row in rows]


# ── Hives ────────────────────────────────────────────────────────────


@router.get("/hives", response_model=list[HiveSummary])
async def list_hives(
    user: UserOut = Depends(get_current_user),
    conn: AsyncConnection[DictRow] = Depends(get_db),
) -> list[HiveSummary]:
    """All of the user's hives with latest reading, latest prediction and open
    alert count — one SQL round-trip using LATERAL joins."""
    cur = await conn.execute(
        """
        SELECT
            h.id, h.apiary_id, h.name, h.hive_type, h.is_active, h.last_seen_at,
            a.name AS apiary_name,
            r.time           AS r_time,
            r.temp_brood_c   AS r_temp_brood_c,
            r.temp_ambient_c AS r_temp_ambient_c,
            r.humidity_pct   AS r_humidity_pct,
            r.weight_kg      AS r_weight_kg,
            r.audio_db       AS r_audio_db,
            r.co2_ppm        AS r_co2_ppm,
            r.battery_v      AS r_battery_v,
            p.time           AS p_time,
            p.model_version  AS p_model_version,
            p.swarm_risk     AS p_swarm_risk,
            p.health_score   AS p_health_score,
            p.is_anomaly     AS p_is_anomaly,
            p.anomaly_kind   AS p_anomaly_kind,
            p.anomaly_score  AS p_anomaly_score,
            COALESCE(al.open_alerts, 0) AS open_alerts
        FROM hives h
        JOIN apiaries a ON a.id = h.apiary_id
        LEFT JOIN LATERAL (
            SELECT sr.time, sr.temp_brood_c, sr.temp_ambient_c, sr.humidity_pct,
                   sr.weight_kg, sr.audio_db, sr.co2_ppm, sr.battery_v
            FROM sensor_readings sr
            WHERE sr.hive_id = h.id
            ORDER BY sr.time DESC
            LIMIT 1
        ) r ON TRUE
        LEFT JOIN LATERAL (
            SELECT pr.time, pr.model_version, pr.swarm_risk, pr.health_score,
                   pr.is_anomaly, pr.anomaly_kind, pr.anomaly_score
            FROM predictions pr
            WHERE pr.hive_id = h.id
            ORDER BY pr.time DESC
            LIMIT 1
        ) p ON TRUE
        LEFT JOIN LATERAL (
            SELECT count(*) AS open_alerts
            FROM alerts x
            WHERE x.hive_id = h.id AND NOT x.acked
        ) al ON TRUE
        WHERE %(is_admin)s OR a.owner_id = %(user_id)s
        ORDER BY h.id
        """,
        {"is_admin": user.is_admin, "user_id": user.id},
    )
    rows = await cur.fetchall()

    summaries: list[HiveSummary] = []
    for row in rows:
        latest_reading = (
            LatestReading(
                time=row["r_time"],
                temp_brood_c=row["r_temp_brood_c"],
                temp_ambient_c=row["r_temp_ambient_c"],
                humidity_pct=row["r_humidity_pct"],
                weight_kg=row["r_weight_kg"],
                audio_db=row["r_audio_db"],
                co2_ppm=row["r_co2_ppm"],
                battery_v=row["r_battery_v"],
            )
            if row["r_time"] is not None
            else None
        )
        latest_prediction = (
            PredictionOut(
                time=row["p_time"],
                model_version=row["p_model_version"],
                swarm_risk=row["p_swarm_risk"],
                health_score=row["p_health_score"],
                is_anomaly=row["p_is_anomaly"],
                anomaly_kind=row["p_anomaly_kind"],
                anomaly_score=row["p_anomaly_score"],
            )
            if row["p_time"] is not None
            else None
        )
        summaries.append(
            HiveSummary(
                id=row["id"],
                apiary_id=row["apiary_id"],
                apiary_name=row["apiary_name"],
                name=row["name"],
                hive_type=row["hive_type"],
                is_active=row["is_active"],
                last_seen_at=row["last_seen_at"],
                latest_reading=latest_reading,
                latest_prediction=latest_prediction,
                open_alerts=row["open_alerts"],
            )
        )
    return summaries


@router.get("/hives/{hive_id}", response_model=HiveDetail)
async def get_hive(
    hive_id: str,
    user: UserOut = Depends(get_current_user),
    conn: AsyncConnection[DictRow] = Depends(get_db),
) -> HiveDetail:
    """Hive detail including metadata."""
    row = await ensure_hive_access(conn, user, hive_id)
    return HiveDetail.model_validate(row)


# ── Readings ─────────────────────────────────────────────────────────


@router.get("/hives/{hive_id}/readings", response_model=None)
async def get_readings(
    hive_id: str,
    hours: int = Query(default=24, ge=1, le=8760),
    resolution: Resolution = Query(default=Resolution.raw),
    user: UserOut = Depends(get_current_user),
    conn: AsyncConnection[DictRow] = Depends(get_db),
) -> list[ReadingRaw] | list[ReadingHourly] | list[ReadingDaily]:
    """Sensor readings for a hive.

    `raw` reads sensor_readings; `hourly` / `daily` read the Timescale
    continuous aggregates readings_hourly / readings_daily (real-time
    aggregation enabled, so recent buckets are included).
    """
    await ensure_hive_access(conn, user, hive_id)
    params = {"hive_id": hive_id, "hours": hours}

    if resolution is Resolution.raw:
        cur = await conn.execute(
            """
            SELECT time, temp_brood_c, temp_ambient_c, humidity_pct, weight_kg,
                   audio_db, audio_b100_200, audio_b200_300, audio_b300_400,
                   audio_b400_500, audio_b500_600, co2_ppm, battery_v
            FROM sensor_readings
            WHERE hive_id = %(hive_id)s
              AND time >= now() - make_interval(hours => %(hours)s)
            ORDER BY time
            """,
            params,
        )
        return [ReadingRaw.model_validate(row) for row in await cur.fetchall()]

    if resolution is Resolution.hourly:
        cur = await conn.execute(
            """
            SELECT bucket AS time, temp_brood_c, temp_ambient_c, humidity_pct,
                   weight_kg, weight_range_kg, audio_db, co2_ppm, battery_v,
                   n_readings
            FROM readings_hourly
            WHERE hive_id = %(hive_id)s
              AND bucket >= now() - make_interval(hours => %(hours)s)
            ORDER BY bucket
            """,
            params,
        )
        return [ReadingHourly.model_validate(row) for row in await cur.fetchall()]

    cur = await conn.execute(
        """
        SELECT bucket AS time, temp_brood_c, humidity_pct, weight_kg,
               weight_delta_kg, audio_db, n_readings
        FROM readings_daily
        WHERE hive_id = %(hive_id)s
          AND bucket >= now() - make_interval(hours => %(hours)s)
        ORDER BY bucket
        """,
        params,
    )
    return [ReadingDaily.model_validate(row) for row in await cur.fetchall()]


# ── Predictions ──────────────────────────────────────────────────────


@router.get("/hives/{hive_id}/predictions", response_model=list[PredictionOut])
async def get_predictions(
    hive_id: str,
    hours: int = Query(default=72, ge=1, le=8760),
    user: UserOut = Depends(get_current_user),
    conn: AsyncConnection[DictRow] = Depends(get_db),
) -> list[PredictionOut]:
    """Model scores for a hive over the last `hours` hours."""
    await ensure_hive_access(conn, user, hive_id)
    cur = await conn.execute(
        """
        SELECT time, model_version, swarm_risk, health_score,
               is_anomaly, anomaly_kind, anomaly_score
        FROM predictions
        WHERE hive_id = %(hive_id)s
          AND time >= now() - make_interval(hours => %(hours)s)
        ORDER BY time
        """,
        {"hive_id": hive_id, "hours": hours},
    )
    return [PredictionOut.model_validate(row) for row in await cur.fetchall()]


# ── Alerts ───────────────────────────────────────────────────────────


@router.get("/hives/{hive_id}/alerts", response_model=list[AlertOut])
async def get_alerts(
    hive_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    user: UserOut = Depends(get_current_user),
    conn: AsyncConnection[DictRow] = Depends(get_db),
) -> list[AlertOut]:
    """Most recent alerts for a hive."""
    await ensure_hive_access(conn, user, hive_id)
    cur = await conn.execute(
        """
        SELECT time, hive_id, severity, kind, message, source, acked
        FROM alerts
        WHERE hive_id = %(hive_id)s
        ORDER BY time DESC
        LIMIT %(limit)s
        """,
        {"hive_id": hive_id, "limit": limit},
    )
    return [AlertOut.model_validate(row) for row in await cur.fetchall()]


@router.post("/alerts/ack", response_model=AckAlertResponse)
async def ack_alert(
    payload: AckAlertRequest,
    user: UserOut = Depends(get_current_user),
    conn: AsyncConnection[DictRow] = Depends(get_db),
) -> AckAlertResponse:
    """Acknowledge an alert identified by (hive_id, time)."""
    await ensure_hive_access(conn, user, payload.hive_id)
    cur = await conn.execute(
        """
        UPDATE alerts
        SET acked = TRUE
        WHERE hive_id = %(hive_id)s AND time = %(time)s
        """,
        {"hive_id": payload.hive_id, "time": payload.time},
    )
    if cur.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    logger.info(
        "alert acked",
        extra={"hive_id": payload.hive_id, "alert_time": payload.time.isoformat()},
    )
    return AckAlertResponse(acked=True, updated=cur.rowcount)


# ── Recommendations ──────────────────────────────────────────────────


@router.get("/hives/{hive_id}/recommendations", response_model=list[RecommendationOut])
async def get_recommendations(
    hive_id: str,
    limit: int = Query(default=10, ge=1, le=100),
    user: UserOut = Depends(get_current_user),
    conn: AsyncConnection[DictRow] = Depends(get_db),
) -> list[RecommendationOut]:
    """Latest stored Mistral recommendations for a hive."""
    await ensure_hive_access(conn, user, hive_id)
    cur = await conn.execute(
        """
        SELECT id, hive_id, created_at, locale, model_id, priority, title, body, context
        FROM recommendations
        WHERE hive_id = %(hive_id)s
        ORDER BY created_at DESC
        LIMIT %(limit)s
        """,
        {"hive_id": hive_id, "limit": limit},
    )
    return [RecommendationOut.model_validate(row) for row in await cur.fetchall()]


@router.post("/hives/{hive_id}/recommendations/refresh")
async def refresh_recommendations(
    hive_id: str,
    user: UserOut = Depends(get_current_user),
    conn: AsyncConnection[DictRow] = Depends(get_db),
) -> Any:
    """Ask the Mistral recommender service to generate fresh recommendations.

    Proxies to the recommender; returns 502 if it is unreachable or errors.
    """
    await ensure_hive_access(conn, user, hive_id)
    settings = get_settings()
    url = f"{settings.recommender_url.rstrip('/')}/recommendations"
    try:
        async with httpx.AsyncClient(timeout=RECOMMENDER_TIMEOUT_S) as client:
            response = await client.post(
                url,
                json={"hive_id": hive_id, "locale": user.locale},
            )
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.error(
            "recommender refresh failed",
            extra={"hive_id": hive_id, "error": str(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Recommender service is unavailable",
        ) from exc
