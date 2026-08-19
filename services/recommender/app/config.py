"""Environment-driven configuration (12-factor, see .env.example)."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

#: Values of HF_API_KEY that mean "no real key configured".
_PLACEHOLDER_KEYS = frozenset({"", "HF_API_KEY", "CHANGE_ME", "changeme", "xxx"})


class Settings(BaseSettings):
    """All configuration comes from env vars; names match .env.example."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    # TimescaleDB
    database_url: str = "postgresql://beelieve:CHANGE_ME@timescaledb:5432/beelieve"

    # Hugging Face
    hf_api_key: str = ""
    hf_model_id: str = "aidxhxr/beelieve-mistral-7b-advisor"
    hf_base_model: str = "mistralai/Mistral-7B-Instruct-v0.3"

    # Service
    recommender_port: int = 8100
    recommender_lang_default: str = "en"

    # Tunables (env-overridable, sane defaults)
    hf_timeout_seconds: float = 30.0
    hf_max_tokens: int = 512
    hf_temperature: float = 0.3
    alerts_lookback_hours: int = 72

    @property
    def hf_enabled(self) -> bool:
        """True when a real-looking Hugging Face API key is configured."""
        return self.hf_api_key.strip() not in _PLACEHOLDER_KEYS


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
