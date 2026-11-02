import os

import pytest

from app.config import Settings, get_settings

_PREFIXES = ("APP_", "META_", "INSTAGRAM_", "OPENAI_", "DASHBOARD_", "SESSION_")
_EXACT = {
    "DRY_RUN",
    "REVIEW_QUEUE_ENABLED",
    "AUTO_REPLY_ENABLED",
    "AUTO_DM_ENABLED",
    "LOG_LEVEL",
    "DATABASE_URL",
}


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    """Strip real app settings from the environment so tests never see real credentials."""
    for key in list(os.environ):
        if key in _EXACT or key.startswith(_PREFIXES):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def make_settings():
    def _make(**overrides) -> Settings:
        return Settings(_env_file=None, **overrides)

    return _make
