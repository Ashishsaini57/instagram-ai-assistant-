import json

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import create_app
from app.models import Comment, CommentStatus, Message, WebhookEvent
from tests.conftest import (
    TEST_IG_USER_ID,
    TEST_VERIFY_TOKEN,
    comment_payload,
    message_payload,
    signed_post,
)


def _db(client):
    return client.app.state.session_factory()


def _count(client, model):
    with _db(client) as db:
        return db.scalar(select(func.count()).select_from(model))


# --- Verification handshake -------------------------------------------------


def test_handshake_returns_challenge(client):
    resp = client.get(
        "/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": TEST_VERIFY_TOKEN,
            "hub.challenge": "1158201444",
        },
    )
    assert resp.status_code == 200
    assert resp.text == "1158201444"


def test_handshake_wrong_token(client):
    resp = client.get(
        "/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"},
    )
    assert resp.status_code == 403


def test_handshake_wrong_mode_or_missing_challenge(client):
    params = {
        "hub.mode": "unsubscribe",
        "hub.verify_token": TEST_VERIFY_TOKEN,
        "hub.challenge": "1",
    }
    assert client.get("/webhook", params=params).status_code == 403
    params = {"hub.mode": "subscribe", "hub.verify_token": TEST_VERIFY_TOKEN}
    assert client.get("/webhook", params=params).status_code == 403


def test_handshake_rejected_when_token_not_configured():
    with TestClient(create_app()) as c:
        resp = c.get(
            "/webhook",
            params={"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "1"},
        )
    assert resp.status_code == 403


# --- Signature verification -------------------------------------------------


def test_valid_signature_accepted(client):
    resp = signed_post(client, comment_payload())
    assert resp.status_code == 200
    assert resp.json()["comments"] == 1


def test_wrong_secret_rejected_and_nothing_stored(client):
    resp = signed_post(client, comment_payload(), secret="attacker-secret")
    assert resp.status_code == 403
    assert _count(client, WebhookEvent) == 0
    assert _count(client, Comment) == 0


def test_missing_signature_rejected(client):
    resp = signed_post(client, comment_payload(), signature="")
    assert resp.status_code == 403
    assert _count(client, Comment) == 0


def test_tampered_body_rejected(client):
    body = json.dumps(comment_payload()).encode()
    from app.security import compute_signature

    sig = compute_signature(body, "test-app-secret")
    tampered = body.replace(b"Nice reel!", b"Hacked!!!!")
    resp = signed_post(client, tampered, signature=sig)
    assert resp.status_code == 403
    assert _count(client, Comment) == 0


def test_rejected_when_app_secret_not_configured():
    with TestClient(create_app()) as c:
        resp = signed_post(c, comment_payload())
        assert resp.status_code == 403
        assert _count(c, Comment) == 0


def test_oversized_body_rejected(client):
    big = b'{"object":"instagram","pad":"' + b"x" * 1_100_000 + b'"}'
    resp = signed_post(client, big)
    assert resp.status_code == 413


def test_invalid_json_with_valid_signature(client):
    resp = signed_post(client, b"not json")
    assert resp.status_code == 400


# --- Comment ingestion ------------------------------------------------------


def test_reel_comment_stored(client):
    signed_post(client, comment_payload(text="बहुत बढ़िया! Love it"))
    with _db(client) as db:
        c = db.scalars(select(Comment)).one()
    assert c.ig_comment_id == "17890000000000001"
    assert c.media_id == "18000000000000001"
    assert c.media_product_type == "REELS"
    assert c.from_id == "555"
    assert c.from_username == "fan_user"
    assert c.text == "बहुत बढ़िया! Love it"
    # Stored, then drafted in the background by the offline generator (no OpenAI key in tests).
    assert c.status == CommentStatus.PENDING_REVIEW
    assert c.parent_comment_id is None
    assert c.event_id is not None


def test_post_comment_reply_stores_parent(client):
    signed_post(client, comment_payload(comment_id="2", media_type="FEED", parent_id="1"))
    with _db(client) as db:
        c = db.scalars(select(Comment)).one()
    assert c.media_product_type == "FEED"
    assert c.parent_comment_id == "1"


def test_own_comment_ignored(client):
    resp = signed_post(client, comment_payload(from_id=TEST_IG_USER_ID, username="me"))
    assert resp.status_code == 200
    with _db(client) as db:
        c = db.scalars(select(Comment)).one()
    assert c.status == CommentStatus.IGNORED_SELF


def test_duplicate_delivery_deduplicated(client):
    first = signed_post(client, comment_payload())
    second = signed_post(client, comment_payload())
    assert first.json()["comments"] == 1
    assert second.json()["comments"] == 0
    assert second.json()["duplicates"] == 1
    assert _count(client, Comment) == 1
    assert _count(client, WebhookEvent) == 2


def test_duplicate_within_same_payload(client):
    payload = comment_payload()
    payload["entry"].append(payload["entry"][0])
    resp = signed_post(client, payload)
    assert resp.json()["comments"] == 1
    assert resp.json()["duplicates"] == 1


def test_unknown_field_acknowledged(client):
    payload = {
        "object": "instagram",
        "entry": [{"id": TEST_IG_USER_ID, "changes": [{"field": "mentions", "value": {}}]}],
    }
    resp = signed_post(client, payload)
    assert resp.status_code == 200
    assert resp.json()["ignored"] == 1
    assert _count(client, Comment) == 0
    assert _count(client, WebhookEvent) == 1


# --- Message ingestion ------------------------------------------------------


def test_incoming_message_stored(client):
    resp = signed_post(client, message_payload())
    assert resp.json()["messages"] == 1
    with _db(client) as db:
        m = db.scalars(select(Message)).one()
    assert m.ig_message_id == "mid.abc123"
    assert m.sender_id == "555"
    assert m.recipient_id == TEST_IG_USER_ID
    assert m.text == "Hi, price?"
    assert m.is_echo is False
    assert m.sent_at is not None


def test_echo_message_flagged(client):
    signed_post(client, message_payload(mid="mid.echo", sender=TEST_IG_USER_ID, is_echo=True))
    with _db(client) as db:
        m = db.scalars(select(Message)).one()
    assert m.is_echo is True


def test_non_message_events_ignored(client):
    payload = message_payload()
    item = payload["entry"][0]["messaging"][0]
    del item["message"]
    item["read"] = {"mid": "mid.abc123"}
    resp = signed_post(client, payload)
    assert resp.status_code == 200
    assert resp.json()["ignored"] == 1
    assert _count(client, Message) == 0
