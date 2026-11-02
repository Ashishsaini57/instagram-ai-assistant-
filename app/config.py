"""Application settings loaded from environment variables (and an optional .env file).

All secrets use ``SecretStr`` so they are masked in logs, reprs and error messages.
Safe defaults: DRY_RUN is on, the review queue is on, and automatic replies/DMs are off.
"""

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnv(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # General
    app_env: AppEnv = AppEnv.DEVELOPMENT
    log_level: str = "INFO"
    database_url: str = "sqlite:///./data/app.db"

    # Safety switches
    dry_run: bool = True
    review_queue_enabled: bool = True
    auto_reply_enabled: bool = False
    auto_dm_enabled: bool = False

    # Meta app (Instagram API with Instagram Login)
    meta_app_id: str | None = None
    meta_app_secret: SecretStr | None = None
    meta_webhook_verify_token: SecretStr | None = None
    meta_graph_api_base_url: str = "https://graph.instagram.com"
    meta_graph_api_version: str = "v23.0"

    # Instagram account
    instagram_user_id: str | None = None
    instagram_access_token: SecretStr | None = None

    # OpenAI
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4o-mini"
    openai_max_output_tokens: int = Field(default=200, ge=16, le=2000)
    openai_timeout_seconds: float = Field(default=20.0, gt=0, le=120)

    # Dashboard
    dashboard_username: str = "admin"
    dashboard_password: SecretStr | None = None
    session_secret_key: SecretStr | None = None

    @property
    def meta_configured(self) -> bool:
        return bool(self.meta_app_id and self.meta_app_secret and self.meta_webhook_verify_token)

    @property
    def instagram_configured(self) -> bool:
        return bool(self.instagram_user_id and self.instagram_access_token)

    @property
    def openai_configured(self) -> bool:
        return self.openai_api_key is not None

    @property
    def can_send_to_instagram(self) -> bool:
        """True only when dry-run is off AND credentials exist.

        Every outbound Instagram call must check this.
        """
        return not self.dry_run and self.instagram_configured

    @model_validator(mode="after")
    def _validate(self) -> "Settings":
        if self.auto_reply_enabled and self.review_queue_enabled:
            raise ValueError(
                "AUTO_REPLY_ENABLED and REVIEW_QUEUE_ENABLED cannot both be true; "
                "disable the review queue to enable automatic replies."
            )
        if not self.dry_run and not self.instagram_configured:
            raise ValueError("DRY_RUN=false requires INSTAGRAM_USER_ID and INSTAGRAM_ACCESS_TOKEN.")
        if self.app_env == AppEnv.PRODUCTION:
            missing = [
                name
                for name, value in (
                    ("DASHBOARD_PASSWORD", self.dashboard_password),
                    ("SESSION_SECRET_KEY", self.session_secret_key),
                )
                if value is None or not value.get_secret_value()
            ]
            if missing:
                raise ValueError(f"Missing required settings in production: {', '.join(missing)}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
