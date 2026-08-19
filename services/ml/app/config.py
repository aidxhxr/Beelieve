"""Environment-only configuration for the ml-inference service (12-factor).

Reads the platform env vars from ``.env.example`` (KAFKA_BOOTSTRAP_SERVERS,
MODEL_DIR, MODEL_VERSION) plus service-local tunables, all overridable via
environment.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All knobs of the online scorer; field name == env var (case-insensitive)."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore", protected_namespaces=())

    # Kafka
    kafka_bootstrap_servers: str = "kafka:9092"
    consumer_group: str = "ml-inference"
    topic_enriched: str = "hive.telemetry.enriched"
    topic_predictions: str = "hive.predictions"
    topic_alerts: str = "hive.alerts"

    # Models
    model_dir: Path = Path("/models")
    model_version: str = "lgbm-2026.08"
    #: DEGRADED mode: if true and artifacts are missing, fall back to
    #: transparent rule-based scoring instead of failing fast.
    ml_allow_heuristic: bool = False

    # Alerting
    swarm_alert_threshold: float = 0.8
    queenless_alert_threshold: float = 0.85
    alert_debounce_seconds: float = 6 * 3600.0

    # Ops
    latency_log_interval_seconds: float = 60.0
    commit_every_messages: int = 200
    commit_interval_seconds: float = 5.0
    log_level: str = "INFO"


def get_settings() -> Settings:
    return Settings()
