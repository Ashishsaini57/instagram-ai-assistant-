from fastapi.testclient import TestClient

from app.main import create_app


def test_health_reports_safe_defaults_without_secrets(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    monkeypatch.setattr("app.ai.client.OpenAI", lambda **kwargs: object())  # offline dummy
    client = TestClient(create_app())

    resp = client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["dry_run"] is True
    assert body["review_queue_enabled"] is True
    assert body["auto_reply_enabled"] is False
    assert body["openai_configured"] is True
    assert body["instagram_configured"] is False
    assert "sk-should-not-leak" not in resp.text
