"""Beelieve API application: FastAPI app, lifespan, CORS, logging, health."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.config import get_settings
from app.db import close_pool, get_pool, open_pool
from app.routes import auth, hives, overview
from app.ws import (
    EVENT_QUEUE_MAXSIZE,
    ConnectionManager,
    KafkaEventBridge,
    dispatch_events,
)
from app.ws import router as ws_router

logger = logging.getLogger("beelieve.api")

_STANDARD_LOG_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    """Structured (JSON lines) log output; `extra` kwargs become fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_LOG_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # uvicorn's own loggers propagate to root so everything is JSON.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers = []
        uv_logger.propagate = True


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the DB pool and start the Kafka→WebSocket bridge; tear both down."""
    settings = get_settings()
    setup_logging(settings.log_level)

    await open_pool(settings)

    loop = asyncio.get_running_loop()
    event_queue: asyncio.Queue[tuple[str, str | None, dict[str, Any]]] = asyncio.Queue(
        maxsize=EVENT_QUEUE_MAXSIZE
    )
    manager = ConnectionManager()
    bridge = KafkaEventBridge(settings.kafka_bootstrap_servers, loop, event_queue)
    bridge.start()
    dispatch_task = asyncio.create_task(dispatch_events(event_queue, manager), name="ws-dispatch")

    app.state.ws_manager = manager
    app.state.kafka_bridge = bridge
    app.state.event_queue = event_queue
    app.state.dispatch_task = dispatch_task

    logger.info("api started", extra={"version": __version__})
    try:
        yield
    finally:
        bridge.stop()
        dispatch_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await dispatch_task
        await close_pool()
        logger.info("api stopped")


app = FastAPI(
    title="Beelieve API",
    description="Backend for the Beelieve precision-beekeeping dashboard.",
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(hives.router)
app.include_router(overview.router)
app.include_router(ws_router)


@app.get("/healthz", tags=["health"])
async def healthz() -> dict[str, str]:
    """Liveness/readiness: verifies the database answers."""
    try:
        pool = get_pool()
        async with pool.connection(timeout=5.0) as conn:
            await conn.execute("SELECT 1")
    except Exception as exc:
        logger.error("healthz failed", extra={"error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database unavailable",
        ) from exc
    return {"status": "ok"}
