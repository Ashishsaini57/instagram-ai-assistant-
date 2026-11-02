import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from app.config import get_settings
from app.instagram.sender import InstagramSender
from app.models import (
    ActionKind,
    AppSetting,
    Comment,
    CommentStatus,
    DraftStatus,
    Message,
    OutboundAction,
    ReplyDraft,
    SendStatus,
)

PASSWORD = "test-dashboard-pass"
SESSION_KEY = "test-session-secret-key-0123456789"
ACCESS_TOKEN = "IGAA-fake-token-must-not-leak"
CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def no_network_factory():
    raise AssertionError("No Instagram client may be created in DRY_RUN")


@pytest.fixture
def app_client(webhook_env, monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_PASSWORD", PASSWORD)
    monkeypatch.setenv("SESSION_SECRET_KEY", SESSION_KEY)
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", ACCESS_TOKEN)
    monkeypatch.setenv("MEDIA_DIR", str(tmp_path / "media"))
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "price-list.png").write_bytes(b"\x89PNG fake")
    get_settings.cache_clear()
    from app.main import create_app

    app = create_app()
    app.state.sender = InstagramSender(app.state.settings, client_factory=no_network_factory)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db(app_client):
    with app_client.app.state.session_factory() as session:
        yield session


def csrf(client, path="/dashboard/login"):
    return CSRF_RE.search(client.get(path).text).group(1)


def login(client, password=PASSWORD, username="admin"):
    token = csrf(client)
    return client.post(
        "/dashboard/login",
        data={"csrf_token": token, "username": username, "password": password},
        follow_redirects=False,
    )


@pytest.fixture
def auth(app_client):
    assert login(app_client).status_code == 303
    return app_client


def post(client, path, data=None, page="/dashboard"):
    return client.post(path, data={"csrf_token": csrf(client, page), **(data or {})})


_ids = iter(range(1, 100_000))


def add_draft(db, created_at=None, dm_text="Hi! Details here.", **fields):
    c = Comment(
        ig_comment_id=f"7{next(_ids)}",
        from_id="555",
        from_username="fan",
        text="Price kya hai?",
        status=CommentStatus.PENDING_REVIEW,
        created_at=created_at or datetime.now(UTC),
    )
    db.add(c)
    db.flush()
    d = ReplyDraft(
        comment_id=c.id,
        status=fields.pop("status", DraftStatus.PENDING_REVIEW),
        intent="price_or_dm_request",
        language="hinglish",
        confidence=0.9,
        reply_text="DM check karo!",
        suggest_dm=bool(dm_text),
        dm_text=dm_text,
        **fields,
    )
    db.add(d)
    db.commit()
    return d


def add_message(db, sender="555", age=timedelta(hours=1), text="Hi, price?"):
    db.add(
        Message(
            ig_message_id=f"mid.{next(_ids)}",
            sender_id=sender,
            recipient_id="17841400000000001",
            text=text,
            sent_at=datetime.now(UTC) - age,
        )
    )
    db.commit()


def actions(db):
    db.expire_all()
    return db.scalars(select(OutboundAction).order_by(OutboundAction.id)).all()


# --- login & protection -------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["/dashboard", "/dashboard/messages", "/dashboard/activity", "/dashboard/settings"],
)
def test_pages_require_login(app_client, path):
    resp = app_client.get(path, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard/login"


@pytest.mark.parametrize(
    "path",
    [
        "/dashboard/drafts/1/approve",
        "/dashboard/drafts/1/reject",
        "/dashboard/messages/555/text",
        "/dashboard/messages/555/image",
        "/dashboard/settings",
        "/dashboard/settings/reset",
    ],
)
def test_actions_require_login(app_client, db, path):
    add_draft(db)
    resp = app_client.post(
        path, data={"csrf_token": csrf(app_client), "text": "hi"}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard/login"
    assert actions(db) == []


def test_root_redirects_to_dashboard(app_client):
    assert app_client.get("/", follow_redirects=False).headers["location"] == "/dashboard"


def test_login_success_and_logout(app_client):
    resp = login(app_client)
    assert resp.status_code == 303 and resp.headers["location"] == "/dashboard"
    assert "Review queue" in app_client.get("/dashboard").text
    post(app_client, "/dashboard/logout")
    assert app_client.get("/dashboard", follow_redirects=False).status_code == 303


@pytest.mark.parametrize(("user", "pw"), [("admin", "wrong"), ("other", PASSWORD), ("", "")])
def test_login_wrong_credentials(app_client, user, pw):
    resp = login(app_client, password=pw, username=user)
    assert resp.status_code == 401
    assert "Wrong username or password" in resp.text
    assert app_client.get("/dashboard", follow_redirects=False).status_code == 303


def test_login_requires_csrf(app_client):
    app_client.get("/dashboard/login")
    resp = app_client.post("/dashboard/login", data={"username": "admin", "password": PASSWORD})
    assert resp.status_code == 403


def test_lockout_after_repeated_failures(app_client):
    for _ in range(5):
        assert login(app_client, password="nope").status_code == 401
    resp = login(app_client)  # correct password, but locked
    assert resp.status_code == 429
    assert "Too many failed attempts" in resp.text


def test_dashboard_locked_without_password(webhook_env, monkeypatch):
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    get_settings.cache_clear()
    from app.main import create_app

    with TestClient(create_app()) as client:
        assert "dashboard is locked" in client.get("/dashboard/login").text
        assert login(client, password="").status_code == 401
        assert login(client, password="anything").status_code == 401


def test_password_change_logs_out_existing_sessions(auth):
    app = auth.app
    app.state.settings = app.state.settings.model_copy(
        update={"dashboard_password": SecretStr("new-password")}
    )
    assert auth.get("/dashboard", follow_redirects=False).status_code == 303


def test_security_headers_and_no_secrets(auth, db):
    add_draft(db)
    add_message(db)
    for path in ["/dashboard", "/dashboard/messages", "/dashboard/activity", "/dashboard/settings"]:
        resp = auth.get(path)
        assert resp.status_code == 200
        for secret in (PASSWORD, SESSION_KEY, ACCESS_TOKEN, "test-app-secret", "test-verify-token"):
            assert secret not in resp.text
        assert resp.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in resp.headers["content-security-policy"]
        assert resp.headers["cache-control"] == "no-store"


def test_session_cookie_flags(app_client):
    resp = login(app_client)
    cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert PASSWORD not in resp.headers["set-cookie"]


# --- review queue -------------------------------------------------------------


def test_queue_shows_drafts_and_dry_run_banner(auth, db):
    add_draft(db, low_confidence=True)
    page = auth.get("/dashboard").text
    assert "DRY RUN" in page
    assert "Price kya hai?" in page and "DM check karo!" in page
    assert "LOW CONFIDENCE" in page
    assert "Private reply window open" in page


def test_approve_is_dry_run(auth, db):
    draft = add_draft(db)
    resp = post(auth, f"/dashboard/drafts/{draft.id}/approve", {"reply_text": "Shukriya! 🙏"})
    assert "dry run — recorded, NOT sent" in resp.text
    [action] = actions(db)
    assert (action.kind, action.status, action.text) == (
        ActionKind.COMMENT_REPLY,
        SendStatus.DRY_RUN,
        "Shukriya! 🙏",
    )
    db.refresh(draft)
    assert draft.status == DraftStatus.DRY_RUN
    assert "Shukriya" in auth.get("/dashboard?view=dry_run").text


def test_approve_with_edited_dm(auth, db):
    draft = add_draft(db)
    post(
        auth,
        f"/dashboard/drafts/{draft.id}/approve",
        {
            "reply_text": "DM check karo!",
            "dm_text": "Hi! Price list bhej raha hoon.",
            "send_dm": "1",
        },
    )
    kinds = {a.kind: (a.status, a.text) for a in actions(db)}
    assert kinds[ActionKind.PRIVATE_REPLY] == (SendStatus.DRY_RUN, "Hi! Price list bhej raha hoon.")


def test_second_private_reply_blocked(auth, db):
    draft = add_draft(db)
    data = {"reply_text": "Thanks!", "send_dm": "1"}
    post(auth, f"/dashboard/drafts/{draft.id}/approve", data)
    resp = post(auth, f"/dashboard/drafts/{draft.id}/approve", data, page="/dashboard?view=dry_run")
    assert "blocked (private_reply_already_sent)" in resp.text


def test_expired_private_reply_window_disables_dm(auth, db):
    add_draft(db, created_at=datetime.now(UTC) - timedelta(days=8))
    page = auth.get("/dashboard").text
    assert "Private reply window expired" in page
    assert re.search(r'name="send_dm" value="1"\s+disabled', page)


def test_invalid_reply_rejected_without_sending(auth, db):
    draft = add_draft(db)
    resp = post(auth, f"/dashboard/drafts/{draft.id}/approve", {"reply_text": "see www.x.com"})
    assert "contains_link" in resp.text
    assert actions(db) == []
    db.refresh(draft)
    assert draft.status == DraftStatus.PENDING_REVIEW


def test_reject(auth, db):
    draft = add_draft(db)
    resp = post(auth, f"/dashboard/drafts/{draft.id}/reject")
    assert f"Draft #{draft.id} rejected" in resp.text
    db.refresh(draft)
    assert draft.status == DraftStatus.REJECTED
    assert actions(db) == []


def test_post_without_csrf_rejected(auth, db):
    draft = add_draft(db)
    resp = auth.post(f"/dashboard/drafts/{draft.id}/approve", data={"reply_text": "Hi"})
    assert resp.status_code == 403
    resp = auth.post(
        f"/dashboard/drafts/{draft.id}/approve", data={"reply_text": "Hi", "csrf_token": "forged"}
    )
    assert resp.status_code == 403
    assert actions(db) == []


# --- messages -----------------------------------------------------------------


def test_messages_window_states(auth, db):
    add_message(db, sender="111", age=timedelta(hours=2))
    add_message(db, sender="222", age=timedelta(hours=30))
    page = auth.get("/dashboard/messages").text
    assert "window open" in page and "window closed" in page
    assert "/dashboard/messages/111/text" in page
    assert "/dashboard/messages/222/text" not in page


def test_send_text_dm_dry_run_and_closed_window(auth, db):
    add_message(db, sender="111", age=timedelta(hours=2))
    add_message(db, sender="222", age=timedelta(hours=30))
    page = "/dashboard/messages"
    post(auth, "/dashboard/messages/111/text", {"text": "Hello!"}, page)
    resp = post(auth, "/dashboard/messages/222/text", {"text": "Hello!"}, page)
    assert "blocked (messaging_window_closed)" in resp.text
    assert [(a.recipient_id, a.status) for a in actions(db)] == [
        ("111", SendStatus.DRY_RUN),
        ("222", SendStatus.BLOCKED),
    ]


def test_image_dm_validation(auth, db, monkeypatch):
    add_message(db, sender="111")
    page = "/dashboard/messages"
    resp = post(
        auth, "/dashboard/messages/111/image", {"image_url": "https://10.0.0.5/a.png"}, page
    )
    assert "blocked (image_private_host)" in resp.text
    resp = post(auth, "/dashboard/messages/111/image", {"media_file": "price-list.png"}, page)
    assert "PUBLIC_BASE_URL" in resp.text
    resp = post(auth, "/dashboard/messages/111/image", {"media_file": "../secret.png"}, page)
    assert "not in the media folder" in resp.text
    resp = post(auth, "/dashboard/messages/111/image", {}, page)
    assert "Choose a media file" in resp.text
    assert [a.status for a in actions(db)] == [SendStatus.BLOCKED]


def test_image_dm_from_media_folder(auth, db):
    app = auth.app
    app.state.settings = app.state.settings.model_copy(
        update={"public_base_url": "https://example.com"}
    )
    add_message(db, sender="111")
    post(
        auth,
        "/dashboard/messages/111/image",
        {"media_file": "price-list.png"},
        "/dashboard/messages",
    )
    [action] = actions(db)
    assert (action.kind, action.status, action.image_url) == (
        ActionKind.DM_IMAGE,
        SendStatus.DRY_RUN,
        "https://example.com/media/price-list.png",
    )


def test_invalid_user_id_rejected(auth):
    resp = post(auth, "/dashboard/messages/bad%20id/text", {"text": "hi"}, "/dashboard/messages")
    assert resp.status_code == 404


# --- activity & settings ------------------------------------------------------


def test_activity_log_and_filter(auth, db):
    add_message(db, sender="111")
    page = "/dashboard/messages"
    post(auth, "/dashboard/messages/111/text", {"text": "Hello there"}, page)
    post(auth, "/dashboard/messages/999/text", {"text": "Cold DM"}, page)
    all_page = auth.get("/dashboard/activity").text
    assert "Hello there" in all_page and "Cold DM" in all_page
    blocked = auth.get("/dashboard/activity?status=blocked").text
    assert "Cold DM" in blocked and "Hello there" not in blocked


def test_settings_save_and_safety_rule(auth, db):
    page = "/dashboard/settings"
    resp = post(
        auth,
        page,
        {"auto_reply_enabled": "on", "review_queue_enabled": "on", "creator_name": "Ashish"},
        page,
    )
    assert "Turn off the review queue" in resp.text
    assert db.scalars(select(AppSetting)).all() == []

    resp = post(
        auth,
        page,
        {
            "auto_reply_enabled": "on",
            "creator_name": "Ashish",
            "creator_persona": "Fitness creator",
        },
        page,
    )
    assert "Settings saved" in resp.text
    health = auth.get("/health").json()
    assert health["auto_reply_enabled"] is True and health["review_queue_enabled"] is False
    assert health["dry_run"] is True


def test_settings_cannot_disable_dry_run(auth):
    page = "/dashboard/settings"
    post(
        auth,
        page,
        {
            "dry_run": "false",
            "DRY_RUN": "false",
            "review_queue_enabled": "on",
            "creator_name": "Ashish",
        },
        page,
    )
    assert auth.get("/health").json()["dry_run"] is True
    assert auth.app.state.settings.can_send_to_instagram is False
    assert 'name="dry_run"' not in auth.get(page).text


def test_settings_reset(auth, db):
    page = "/dashboard/settings"
    post(auth, page, {"creator_name": "Ashish", "review_queue_enabled": "on"}, page)
    resp = post(auth, "/dashboard/settings/reset", page=page)
    assert "reset to the .env values" in resp.text
    assert db.scalars(select(AppSetting)).all() == []
