"""Environment-driven configuration (12-factor, see /.env.example)."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration for the stream processor.

    Every field maps 1:1 to an environment variable (case-insensitive).
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Kafka
    kafka_bootstrap_servers: str = "kafka:9092"
    consumer_group: str = "stream-processor"
    topic_telemetry_raw: str = "hive.telemetry.raw"
    topic_telemetry_enriched: str = "hive.telemetry.enriched"
    topic_predictions: str = "hive.predictions"
    topic_alerts: str = "hive.alerts"

    # TimescaleDB
    database_url: str = "postgresql://beelieve:beelieve@timescaledb:5432/beelieve"

    # Batched writer: flush every N rows or M seconds, whichever comes first.
    batch_max_rows: int = Field(default=500, ge=1)
    batch_max_seconds: float = Field(default=2.0, gt=0)

    # DB reconnect/retry backoff.
    db_retry_initial_seconds: float = Field(default=0.5, gt=0)
    db_retry_max_seconds: float = Field(default=30.0, gt=0)

    # Rolling windows / alerting
    window_retention_hours: float = Field(default=24.0, gt=0)
    alert_debounce_seconds: float = Field(default=6 * 3600.0, gt=0)

    # Operational
    stats_interval_seconds: float = Field(default=30.0, gt=0)
    poll_timeout_seconds: float = Field(default=0.2, gt=0)
    log_level: str = "INFO"
