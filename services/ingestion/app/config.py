"""Environment-driven configuration for the ingestion bridge (12-factor)."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from environment variables (see /.env.example)."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    # MQTT (Mosquitto)
    mqtt_host: str = Field(default="mosquitto", alias="MQTT_HOST")
    mqtt_port: int = Field(default=1883, alias="MQTT_PORT")
    mqtt_username: str | None = Field(default=None, alias="MQTT_USERNAME")
    mqtt_password: str | None = Field(default=None, alias="MQTT_PASSWORD")
    mqtt_client_id: str = Field(default="beelieve-ingestion", alias="MQTT_CLIENT_ID")
    mqtt_keepalive_seconds: int = Field(default=60, alias="MQTT_KEEPALIVE_SECONDS")

    # Kafka
    kafka_bootstrap_servers: str = Field(
        default="kafka:9092", alias="KAFKA_BOOTSTRAP_SERVERS"
    )

    # Topics (contract: docs/ARCHITECTURE.md)
    telemetry_topic_filter: str = "beelieve/+/+/telemetry"
    status_topic_filter: str = "beelieve/+/+/status"
    kafka_raw_topic: str = "hive.telemetry.raw"
    kafka_dlq_topic: str = "hive.telemetry.dlq"
    kafka_alerts_topic: str = "hive.alerts"

    # Operations
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    stats_interval_seconds: float = Field(default=60.0, alias="STATS_INTERVAL_SECONDS")
    shutdown_flush_timeout_seconds: float = Field(
        default=10.0, alias="SHUTDOWN_FLUSH_TIMEOUT_SECONDS"
    )
