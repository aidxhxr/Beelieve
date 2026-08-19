"""Beelieve recommender FastAPI service.

POST /recommendations       — generate + store advice for a hive
GET  /recommendations/{id}  — recent stored recommendations
GET  /healthz               — liveness/config probe
"""
from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Literal

from fastapi import FastAPI, HTTPException, Query
from huggingface_hub import InferenceClient
from pydantic import BaseModel, Field

from app import __version__
from app.config import Settings, get_settings
from app.db import (
    close_pool,
    fetch_context,
    fetch_recent_recommendations,
    hive_exists,
    insert_recommendations,
    open_pool,
    ping,
)
from app.fallback import FALLBACK_MODEL_ID, generate_fallback
from app.parse import ParsedRecommendation, parse_recommendations
from app.prompts import DEFAULT_LOCALE, SUPPORTED_LOCALES, build_messages

# ── structured logging ───────────────────────────────────────────────


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "ctx", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info and record.exc_info[0] is not None:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def _setup_logging() -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("recommender")
    if not logger.handlers:
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


log = _setup_logging()


def _log(level: int, msg: str, **ctx: Any) -> None:
    log.log(level, msg, extra={"ctx": ctx})


# ── API models ───────────────────────────────────────────────────────

Locale = Literal["en", "ru", "kk"]


class RecommendationRequest(BaseModel):
    hive_id: str = Field(min_length=1, max_length=64, examples=["KZ-ALA-0042"])
    locale: Locale | None = None


class RecommendationOut(BaseModel):
    id: uuid.UUID
    hive_id: str
    created_at: datetime
    locale: str
    model_id: str
    priority: int = Field(ge=1, le=5)
    title: str
    body: str


class RecommendationsResponse(BaseModel):
    hive_id: str
    locale: str
    model_id: str
    recommendations: list[RecommendationOut]


class HealthResponse(BaseModel):
    status: str
    version: str
    model_id: str
    hf_enabled: bool
    db: str


# ── model invocation ─────────────────────────────────────────────────


def _call_hf(
    settings: Settings, messages: list[dict[str, str]], hive_id: str
) -> str | None:
    """Call the fine-tuned model via the HF Inference API; one retry, timeout.

    Returns the raw completion text, or None on failure.
    """
    client = InferenceClient(
        model=settings.hf_model_id,
        token=settings.hf_api_key,
        timeout=settings.hf_timeout_seconds,
    )
    last_error: Exception | None = None
    for attempt in (1, 2):
        started = time.monotonic()
        try:
            completion = client.chat_completion(
                messages=messages,
                temperature=settings.hf_temperature,
                max_tokens=settings.hf_max_tokens,
            )
            text = completion.choices[0].message.content or ""
            _log(
                logging.INFO,
                "hf_completion_ok",
                hive_id=hive_id,
                attempt=attempt,
                latency_ms=round((time.monotonic() - started) * 1000),
                chars=len(text),
            )
            return text
        except Exception as exc:  # network, auth, model loading, rate limit
            last_error = exc
            _log(
                logging.WARNING,
                "hf_completion_failed",
                hive_id=hive_id,
                attempt=attempt,
                latency_ms=round((time.monotonic() - started) * 1000),
                error=f"{type(exc).__name__}: {exc}",
            )
            if attempt == 1:
                time.sleep(1.0)
    _log(logging.ERROR, "hf_completion_gave_up", hive_id=hive_id, error=str(last_error))
    return None


def generate_recommendations(
    settings: Settings, ctx: dict[str, Any], locale: str, hive_id: str
) -> tuple[list[ParsedRecommendation], str]:
    """Try the fine-tuned model; fall back to deterministic rules."""
    if settings.hf_enabled:
        raw = _call_hf(settings, build_messages(ctx, locale), hive_id)
        if raw is not None:
            recs = parse_recommendations(raw)
            if recs:
                return recs, settings.hf_model_id
            _log(logging.WARNING, "hf_output_unparseable", hive_id=hive_id, chars=len(raw))
    else:
        _log(logging.INFO, "hf_disabled_using_fallback", hive_id=hive_id)
    return generate_fallback(ctx, locale), FALLBACK_MODEL_ID


# ── app ──────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    open_pool()
    _log(
        logging.INFO,
        "service_started",
        port=settings.recommender_port,
        hf_enabled=settings.hf_enabled,
        model_id=settings.hf_model_id,
    )
    try:
        yield
    finally:
        close_pool()
        _log(logging.INFO, "service_stopped")


app = FastAPI(title="Beelieve Recommender", version=__version__, lifespan=lifespan)


@app.get("/healthz", response_model=HealthResponse)
def healthz() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        version=__version__,
        model_id=settings.hf_model_id if settings.hf_enabled else FALLBACK_MODEL_ID,
        hf_enabled=settings.hf_enabled,
        db="up" if ping() else "down",
    )


@app.post("/recommendations", response_model=RecommendationsResponse)
def create_recommendations(req: RecommendationRequest) -> RecommendationsResponse:
    settings = get_settings()
    locale = req.locale or (
        settings.recommender_lang_default
        if settings.recommender_lang_default in SUPPORTED_LOCALES
        else DEFAULT_LOCALE
    )

    ctx = fetch_context(req.hive_id, settings.alerts_lookback_hours)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"hive {req.hive_id!r} not found")

    recs, model_id = generate_recommendations(settings, ctx, locale, req.hive_id)
    stored = insert_recommendations(req.hive_id, locale, model_id, recs, ctx)
    _log(
        logging.INFO,
        "recommendations_created",
        hive_id=req.hive_id,
        locale=locale,
        model_id=model_id,
        count=len(stored),
    )
    return RecommendationsResponse(
        hive_id=req.hive_id,
        locale=locale,
        model_id=model_id,
        recommendations=[RecommendationOut(**row) for row in stored],
    )


@app.get("/recommendations/{hive_id}", response_model=list[RecommendationOut])
def list_recommendations(
    hive_id: str, limit: int = Query(default=10, ge=1, le=100)
) -> list[RecommendationOut]:
    if not hive_exists(hive_id):
        raise HTTPException(status_code=404, detail=f"hive {hive_id!r} not found")
    rows = fetch_recent_recommendations(hive_id, limit)
    return [RecommendationOut(**row) for row in rows]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app", host="0.0.0.0", port=get_settings().recommender_port
    )
