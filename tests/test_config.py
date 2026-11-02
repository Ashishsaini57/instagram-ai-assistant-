import pytest
from pydantic import ValidationError


def test_defaults_are_safe(make_settings):
    s = make_settings()
    assert s.dry_run is True
    assert s.review_queue_enabled is True
    assert s.auto_reply_enabled is False
    assert s.auto_dm_enabled is False
    assert s.can_send_to_instagram is False
    assert s.openai_model == "gpt-4o-mini"


def test_reads_from_environment(monkeypatch, make_settings):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4.1-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
    s = make_settings()
    assert s.openai_model == "gpt-4.1-mini"
    assert s.openai_configured is True


def test_secrets_are_masked(make_settings):
    s = make_settings(
        openai_api_key="sk-super-secret",
        instagram_access_token="ig-super-secret",
        meta_app_secret="meta-super-secret",
    )
    dumped = repr(s) + str(s.model_dump())
    for secret in ("sk-super-secret", "ig-super-secret", "meta-super-secret"):
        assert secret not in dumped


def test_auto_reply_requires_review_queue_off(make_settings):
    with pytest.raises(ValidationError, match="AUTO_REPLY_ENABLED"):
        make_settings(auto_reply_enabled=True, review_queue_enabled=True)
    s = make_settings(auto_reply_enabled=True, review_queue_enabled=False)
    assert s.auto_reply_enabled is True


def test_disabling_dry_run_requires_instagram_credentials(make_settings):
    with pytest.raises(ValidationError, match="DRY_RUN=false"):
        make_settings(dry_run=False)


def test_can_send_only_when_live_and_configured(make_settings):
    creds = {"instagram_user_id": "123", "instagram_access_token": "fake-token"}
    assert make_settings(dry_run=True, **creds).can_send_to_instagram is False
    assert make_settings(dry_run=False, **creds).can_send_to_instagram is True


def test_production_requires_dashboard_secrets(make_settings):
    with pytest.raises(ValidationError, match="DASHBOARD_PASSWORD"):
        make_settings(app_env="production")
    s = make_settings(app_env="production", dashboard_password="pw", session_secret_key="key")
    assert s.app_env == "production"
