"""Environment-driven configuration (12-factor, see .env.example)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All service configuration, read exclusively from environment variables."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    # TimescaleDB
    database_url: str = "postgresql://beelieve:beelieve@timescaledb:5432/beelieve"
    db_pool_min_size: int = 1
    db_pool_max_size: int = 10

    # Kafka
    kafka_bootstrap_servers: str = "kafka:9092"

    # Auth
    jwt_secret: str = "change-me-in-env"
    jwt_expires_min: int = 1440

    # HTTP
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "http://localhost:3030"

    # Downstream services
    recommender_url: str = "http://recommender:8100"

    # Logging
    log_level: str = "INFO"

    @property
    def cors_origins_list(self) -> list[str]:
        """CORS_ORIGINS is a comma-separated list of allowed origins."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
