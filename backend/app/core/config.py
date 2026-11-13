"""Application settings validated via Pydantic BaseSettings."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables and .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Application
    APP_NAME: str = "Personal Muse"
    APP_ENV: Literal["development", "production", "test"] = "production"
    LOG_LEVEL: str = "INFO"
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = Field(default=8000, ge=1, le=65535)
    CORS_ORIGINS: str = "http://localhost:3000"

    # Security & Auth
    API_SECRET_TOKEN: str = Field(..., min_length=16)
    APPROVAL_HMAC_SECRET: str = Field(..., min_length=16)
    APPROVAL_TOKEN_TTL_SECONDS: int = Field(default=600, ge=30, le=86400)

    CF_ACCESS_ENABLED: bool = False
    CF_ACCESS_TEAM_DOMAIN: str = ""
    CF_ACCESS_AUD: str = ""

    # Gemini 4 Hydrogen
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-4-hydrogen"
    GEMINI_EMBEDDING_MODEL: str = "text-embedding-004"
    GEMINI_TIMEOUT_SECONDS: int = Field(default=120, ge=5, le=600)

    # Database & Redis
    DATABASE_URL: str = Field(..., min_length=1)
    REDIS_URL: str = Field(..., min_length=1)
    ARQ_MAX_JOBS: int = Field(default=10, ge=1, le=100)
    ARQ_JOB_TIMEOUT_SECONDS: int = Field(default=600, ge=30, le=3600)

    # Memory & File Paths
    CORE_MEMORY_PATH: Path = Path("/app/data/core_memory.md")
    SHARED_FILES_DIR: Path = Path("/shared_files")
    CONFIG_DIR: Path = Path("/app/config")
    MEMORY_SIMILARITY_THRESHOLD: float = Field(default=0.55, ge=0.0, le=1.0)
    MEMORY_TOP_K: int = Field(default=5, ge=1, le=50)

    @field_validator("LOG_LEVEL")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        normalized = value.upper().strip()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if normalized not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(allowed)}")
        return normalized

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached Settings instance."""
    return Settings()

