"""
config.py -- Single source of truth for all application configuration.

All environment variables are declared here. No module should call os.getenv()
directly; import `settings` from this module instead.

If a required variable is missing, the application will refuse to start with a
clear error message rather than failing silently at request time.
"""
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",         # ignore unknown env vars (e.g. Docker internals)
        case_sensitive=False,   # OPENROUTER_API_KEY and openrouter_api_key both work
    )

    # -- OpenRouter ----------------------------------------------------------
    openrouter_api_key: str
    openrouter_model: str = "google/gemini-flash-1.5"

    # -- SQLite Database -----------------------------------------------------
    db_path: str = "/app/data/sousvid.db"

    # -- Whisper -------------------------------------------------------------
    whisper_model: str = "small"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"

    # -- Video processing ----------------------------------------------------
    max_frames: int = 6
    cookies_file: str = "/app/cookies/cookies.txt"

    # -- Redis / Celery --------------------------------------------------------
    redis_url: str = "redis://redis:6379/0"

    # -- Auth / JWT -----------------------------------------------------------
    # If left empty, a secret is auto-generated at startup and persisted to
    # <data_dir>/.jwt_secret so it survives container restarts without
    # requiring a manual .env change.
    jwt_secret: str = ""
    jwt_expire_hours: int = 168  # 7 days

    @field_validator("openrouter_api_key")
    @classmethod
    def api_key_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError(
                "OPENROUTER_API_KEY is required but not set. "
                "Get your key at https://openrouter.ai and add it to .env."
            )
        return v


# Module-level singleton -- import this everywhere instead of os.getenv()
settings = Settings()
