"""Environment-driven configuration for the hive simulator (12-factor)."""

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
    mqtt_keepalive_seconds: int = Field(default=60, alias="MQTT_KEEPALIVE_SECONDS")

    # Simulation
    sim_num_hives: int = Field(default=12, ge=1, le=9999, alias="SIM_NUM_HIVES")
    sim_interval_seconds: float = Field(
        default=10.0, gt=0.0, alias="SIM_INTERVAL_SECONDS"
    )
    sim_apiary_id: str = Field(default="apiary-almaty-01", alias="SIM_APIARY_ID")
    sim_seed: int | None = Field(default=None, alias="SIM_SEED")
    sim_firmware_version: str = Field(default="1.4.2", alias="SIM_FIRMWARE_VERSION")

    # Operations
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
