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
    APP_NAME: str = "Gemuse"
    APP_ENV: Literal["development", "production", "test"] = "production"
    LOG_LEVEL: str = "INFO"
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = Field(default=8000, ge=1, le=65535)
    BACKEND_PORT: int = Field(default=8005, ge=1, le=65535)
    CORS_ORIGINS: str = (
        "https://gemuse.zyuuuukak1n.dev,https://gemini.google.com,"
        "http://localhost:3000,http://localhost:3005,http://127.0.0.1:3005"
    )

    # Security & Auth
    API_SECRET_TOKEN: str = Field(..., min_length=16)
    APPROVAL_HMAC_SECRET: str = Field(..., min_length=16)
    APPROVAL_TOKEN_TTL_SECONDS: int = Field(default=600, ge=30, le=86400)
    GEMUSE_INGEST_TOKEN: str = ""

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
    MCP_TOOL_TIMEOUT_SECONDS: float = Field(default=15.0, ge=1.0, le=120.0)

    # Step 2: Discord Multi-Server Integration
    DISCORD_BOT_TOKEN: str = ""
    DISCORD_API_BASE_URL: str = "https://discord.com/api/v10"

    # Step 2: Multi-Mail Integration (Outlook / iCloud / Gmail)
    OUTLOOK_CLIENT_ID: str = ""
    OUTLOOK_CLIENT_SECRET: str = ""
    OUTLOOK_TENANT_ID: str = ""
    OUTLOOK_REFRESH_TOKEN: str = ""

    ICLOUD_EMAIL: str = ""
    ICLOUD_APP_PASSWORD: str = ""
    ICLOUD_IMAP_HOST: str = "imap.mail.me.com"
    ICLOUD_IMAP_PORT: int = Field(default=993, ge=1, le=65535)
    ICLOUD_SMTP_HOST: str = "smtp.mail.me.com"
    ICLOUD_SMTP_PORT: int = Field(default=587, ge=1, le=65535)

    # Step 2: Google Workspace (Gmail / Calendar / Drive)
    GOOGLE_OAUTH_CLIENT_ID: str = ""
    GOOGLE_OAUTH_CLIENT_SECRET: str = ""
    GOOGLE_OAUTH_REFRESH_TOKEN: str = ""
    GOOGLE_CALENDAR_ID: str = "primary"

    # Step 2: Social & Media (X / YouTube)
    X_BEARER_TOKEN: str = ""
    X_USER_ID: str = ""
    YOUTUBE_API_KEY: str = ""
    YOUTUBE_OAUTH_REFRESH_TOKEN: str = ""

    # Step 3: Isolated Browser Sandbox & noVNC
    BROWSER_SANDBOX_URL: str = "http://browser:7000"
    BROWSER_NOVNC_URL: str = "http://browser:6080"
    NOVNC_PASSWORD: str = ""
    BROWSER_ARTIFACT_TTL_SECONDS: int = Field(default=86400, ge=60, le=2592000)

    # Step 3: Proactive Engine & Notifications
    PROACTIVE_RULES_PATH: str = "/app/config/proactive_rules.yaml"
    PROACTIVE_TIMEZONE: str = "Asia/Tokyo"
    DISCORD_NOTIFY_WEBHOOK_URL: str = ""
    VAPID_PUBLIC_KEY: str = ""
    VAPID_PRIVATE_KEY: str = ""
    VAPID_CLAIMS_EMAIL: str = "mailto:admin@example.com"

    # Step 3: Twilio Voice Bridge & Gemini Live API
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_PHONE_NUMBER: str = ""
    PUBLIC_BASE_URL: str = ""
    GEMINI_LIVE_MODEL: str = "gemini-4-hydrogen-live"

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
        origins = [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]
        if "https://gemini.google.com" not in origins:
            origins.append("https://gemini.google.com")
        return origins


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached Settings instance."""
    return Settings()
