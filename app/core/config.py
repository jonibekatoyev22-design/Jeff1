"""
Centralized application configuration.

Every secret is read from environment variables via pydantic-settings.
Never hardcode secrets here — only .env.example (with placeholder values)
is committed to version control; the real .env file must stay untracked.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    app_env: str = "development"
    app_secret_key: str
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30

    # Database
    database_url: str

    # Redis / Celery
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/0"
    celery_result_backend: str = "redis://redis:6379/1"

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # Object storage
    s3_endpoint_url: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_bucket_name: str = "ai-assessment-documents"
    s3_region: str = "auto"

    # Email
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "no-reply@example.com"

    # Frontend
    frontend_url: str = "http://localhost:5173"

    # Whoever registers with this email is auto-promoted to ADMIN and
    # auto-approved, both at registration time and on every app startup
    # (so setting this for an account that already exists also works).
    # Leave blank to disable.
    admin_email: str = ""

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance — environment is read once per process."""
    return Settings()
