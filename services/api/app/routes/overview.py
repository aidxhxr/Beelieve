"""Fleet-level overview statistics for the dashboard home screen."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from psycopg import AsyncConnection
from psycopg.rows import DictRow

from app.db import get_db
from app.models import OverviewOut, Severity, UserOut, WeightTrendPoint
from app.security import get_current_user

logger = logging.getLogger("beelieve.api.overview")

router = APIRouter(tags=["overview"])

_SEVERITIES: tuple[Severity, ...] = ("critical", "warning", "info")


@router.get("/overview", response_model=OverviewOut)
async def get_overview(
    user: UserOut = Depends(get_current_user),
    conn: AsyncConnection[DictRow] = Depends(get_db),
) -> OverviewOut:
    """Fleet stats over the user's hives: counts, open alerts by severity,
    average latest health score / swarm risk, and 7-day total-weight trend."""
    scope = {"is_admin": user.is_admin, "user_id": user.id}

    cur = await conn.execute(
        """
        SELECT count(*) AS hive_count,
               count(*) FILTER (WHERE h.is_active) AS active_hives
        FROM hives h
        JOIN apiaries a ON a.id = h.apiary_id
        WHERE %(is_admin)s OR a.owner_id = %(user_id)s
        """,
        scope,
    )
    counts = await cur.fetchone()
    assert counts is not None

    cur = await conn.execute(
        """
        SELECT al.severity, count(*) AS n
        FROM alerts al
        JOIN hives h ON h.id = al.hive_id
        JOIN apiaries a ON a.id = h.apiary_id
        WHERE NOT al.acked
          AND (%(is_admin)s OR a.owner_id = %(user_id)s)
        GROUP BY al.severity
        """,
        scope,
    )
    severity_rows = await cur.fetchall()
    alerts_by_severity: dict[Severity, int] = {severity: 0 for severity in _SEVERITIES}
    for row in severity_rows:
        if row["severity"] in alerts_by_severity:
            alerts_by_severity[row["severity"]] = row["n"]

    cur = await conn.execute(
        """
        SELECT avg(p.health_score) AS avg_health_score,
               avg(p.swarm_risk)   AS avg_swarm_risk
        FROM hives h
        JOIN apiaries a ON a.id = h.apiary_id
        LEFT JOIN LATERAL (
            SELECT pr.health_score, pr.swarm_risk
            FROM predictions pr
            WHERE pr.hive_id = h.id
            ORDER BY pr.time DESC
            LIMIT 1
        ) p ON TRUE
        WHERE %(is_admin)s OR a.owner_id = %(user_id)s
        """,
        scope,
    )
    averages = await cur.fetchone()
    assert averages is not None

    cur = await conn.execute(
        """
        SELECT d.bucket, sum(d.weight_kg) AS total_weight_kg
        FROM readings_daily d
        JOIN hives h ON h.id = d.hive_id
        JOIN apiaries a ON a.id = h.apiary_id
        WHERE d.bucket >= now() - INTERVAL '7 days'
          AND (%(is_admin)s OR a.owner_id = %(user_id)s)
        GROUP BY d.bucket
        ORDER BY d.bucket
        """,
        scope,
    )
    trend_rows = await cur.fetchall()

    return OverviewOut(
        hive_count=counts["hive_count"],
        active_hives=counts["active_hives"],
        alerts_by_severity=alerts_by_severity,
        avg_health_score=averages["avg_health_score"],
        avg_swarm_risk=averages["avg_swarm_risk"],
        weight_trend_7d=[WeightTrendPoint.model_validate(row) for row in trend_rows],
    )
