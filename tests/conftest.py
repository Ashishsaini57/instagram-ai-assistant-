import json
import os

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.security import SIGNATURE_HEADER, compute_signature

_PREFIXES = (
    "APP_",
    "META_",
    "INSTAGRAM_",
    "OPENAI_",
    "DASHBOARD_",
    "SESSION_",
    "AI_",
    "CREATOR_",
    "REPLY_",
    "DM_",
)
_EXACT = {
    "DRY_RUN",
    "REVIEW_QUEUE_ENABLED",
    "AUTO_REPLY_ENABLED",
    "AUTO_DM_ENABLED",
    "LOG_LEVEL",
    "DATABASE_URL",
    "BLOCKED_WORDS",
}

TEST_APP_SECRET = "test-app-secret"
TEST_VERIFY_TOKEN = "test-verify-token"
TEST_IG_USER_ID = "17841400000000001"


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    """Strip real app settings from the environment so tests never see real credentials."""
    for key in list(os.environ):
        if key in _EXACT or key.startswith(_PREFIXES):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")

    def _no_real_openai(*args, **kwargs):
        raise RuntimeError("Real OpenAI client must never be created in tests")

    monkeypatch.setattr("app.ai.client.OpenAI", _no_real_openai)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def make_settings():
    def _make(**overrides) -> Settings:
        return Settings(_env_file=None, **overrides)

    return _make


@pytest.fixture
def webhook_env(monkeypatch):
    monkeypatch.setenv("META_APP_SECRET", TEST_APP_SECRET)
    monkeypatch.setenv("META_WEBHOOK_VERIFY_TOKEN", TEST_VERIFY_TOKEN)
    monkeypatch.setenv("INSTAGRAM_USER_ID", TEST_IG_USER_ID)
    get_settings.cache_clear()


@pytest.fixture
def client(webhook_env):
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


def signed_post(client: TestClient, payload, secret: str = TEST_APP_SECRET, signature=None):
    body = json.dumps(payload).encode() if not isinstance(payload, bytes) else payload
    headers = {"Content-Type": "application/json"}
    sig = signature if signature is not None else compute_signature(body, secret)
    if sig:
        headers[SIGNATURE_HEADER] = sig
    return client.post("/webhook", content=body, headers=headers)


def comment_payload(
    comment_id="17890000000000001",
    text="Nice reel!",
    from_id="555",
    username="fan_user",
    media_type="REELS",
    parent_id=None,
):
    value = {
        "from": {"id": from_id, "username": username},
        "media": {"id": "18000000000000001", "media_product_type": media_type},
        "id": comment_id,
        "text": text,
    }
    if parent_id:
        value["parent_id"] = parent_id
    return {
        "object": "instagram",
        "entry": [
            {
                "id": TEST_IG_USER_ID,
                "time": 1760000000,
                "changes": [{"field": "comments", "value": value}],
            }
        ],
    }


def message_payload(mid="mid.abc123", sender="555", text="Hi, price?", is_echo=False):
    message = {"mid": mid, "text": text}
    if is_echo:
        message["is_echo"] = True
    return {
        "object": "instagram",
        "entry": [
            {
                "id": TEST_IG_USER_ID,
                "time": 1760000000,
                "messaging": [
                    {
                        "sender": {"id": sender},
                        "recipient": {"id": TEST_IG_USER_ID},
                        "timestamp": 1760000000123,
                        "message": message,
                    }
                ],
            }
        ],
    }
