"""Meta Instagram webhook receiver: verification handshake + signed event ingestion.

This module only receives and stores events. It never calls Instagram.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai.pipeline import process_comments
from app.config import Settings, get_settings
from app.db import get_db
from app.models import Comment, CommentStatus, Message, WebhookEvent
from app.security import SIGNATURE_HEADER, is_valid_signature, is_valid_verify_token

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 1_048_576

router = APIRouter()

SettingsDep = Annotated[Settings, Depends(get_settings)]
DbDep = Annotated[Session, Depends(get_db)]


@router.get("/webhook", response_class=PlainTextResponse)
def verify_subscription(
    settings: SettingsDep,
    mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    verify_token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> str:
    expected = (
        settings.meta_webhook_verify_token.get_secret_value()
        if settings.meta_webhook_verify_token
        else None
    )
    if (
        mode != "subscribe"
        or challenge is None
        or not is_valid_verify_token(verify_token, expected)
    ):
        raise HTTPException(status_code=403, detail="Verification failed")
    return challenge


@router.post("/webhook")
async def receive_webhook(
    request: Request, background: BackgroundTasks, settings: SettingsDep, db: DbDep
) -> dict:
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Payload too large")
    raw_body = await request.body()
    if len(raw_body) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Payload too large")

    app_secret = settings.meta_app_secret.get_secret_value() if settings.meta_app_secret else None
    if not is_valid_signature(raw_body, request.headers.get(SIGNATURE_HEADER), app_secret):
        logger.warning("Rejected webhook with missing or invalid signature")
        raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        payload = json.loads(raw_body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid payload")

    result, new_comment_ids = ingest_payload(db, payload, settings)
    logger.info("Webhook processed: %s", result)
    if new_comment_ids and settings.ai_processing_enabled:
        background.add_task(
            process_comments,
            request.app.state.session_factory,
            settings,
            request.app.state.reply_generator,
            new_comment_ids,
        )
    return {"status": "ok", **result}


def ingest_payload(
    db: Session, payload: dict[str, Any], settings: Settings
) -> tuple[dict[str, int], list[int]]:
    """Store comments/messages. Returns counts and DB ids of new comments awaiting AI."""
    counts = {"comments": 0, "messages": 0, "duplicates": 0, "ignored": 0}
    new_comments: list[Comment] = []
    entries = payload.get("entry") or []

    event = WebhookEvent(
        object=_str(payload.get("object")),
        field=_first_field(entries),
        payload_json=json.dumps(payload, ensure_ascii=False),
    )
    db.add(event)
    db.flush()

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        own_ids = {i for i in (_str(entry.get("id")), settings.instagram_user_id) if i}

        for change in entry.get("changes") or []:
            if isinstance(change, dict) and change.get("field") == "comments":
                comment = _comment_from_change(change.get("value") or {}, own_ids, event.id)
                if _store(db, comment, counts) and comment.status == CommentStatus.NEW:
                    new_comments.append(comment)
            else:
                counts["ignored"] += 1

        for item in entry.get("messaging") or []:
            message = (
                _message_from_item(item, own_ids, event.id) if isinstance(item, dict) else None
            )
            if message is None:
                counts["ignored"] += 1
            else:
                _store(db, message, counts)

    db.commit()
    return counts, [c.id for c in new_comments]


def _store(db: Session, row: Comment | Message | None, counts: dict[str, int]) -> bool:
    if row is None:
        counts["ignored"] += 1
        return False
    model = type(row)
    key_col = Comment.ig_comment_id if model is Comment else Message.ig_message_id
    key = row.ig_comment_id if isinstance(row, Comment) else row.ig_message_id
    if db.scalar(select(model.id).where(key_col == key)) is not None:
        counts["duplicates"] += 1
        return False
    try:
        with db.begin_nested():
            db.add(row)
    except IntegrityError:
        counts["duplicates"] += 1
        return False
    counts["comments" if model is Comment else "messages"] += 1
    return True


def _comment_from_change(value: dict[str, Any], own_ids: set[str], event_id: int) -> Comment | None:
    comment_id = _str(value.get("id"))
    if not comment_id:
        return None
    sender = value.get("from") or {}
    media = value.get("media") or {}
    from_id = _str(sender.get("id"))
    return Comment(
        ig_comment_id=comment_id,
        media_id=_str(media.get("id")),
        media_product_type=_str(media.get("media_product_type")),
        parent_comment_id=_str(value.get("parent_id")),
        from_id=from_id,
        from_username=_str(sender.get("username")),
        text=_str(value.get("text")) or "",
        status=CommentStatus.IGNORED_SELF if from_id in own_ids else CommentStatus.NEW,
        event_id=event_id,
    )


def _message_from_item(item: dict[str, Any], own_ids: set[str], event_id: int) -> Message | None:
    message = item.get("message")
    if not isinstance(message, dict):
        return None  # reactions, read receipts, etc.
    mid = _str(message.get("mid"))
    sender_id = _str((item.get("sender") or {}).get("id"))
    recipient_id = _str((item.get("recipient") or {}).get("id"))
    if not mid or not sender_id or not recipient_id:
        return None
    attachments = message.get("attachments")
    return Message(
        ig_message_id=mid,
        sender_id=sender_id,
        recipient_id=recipient_id,
        text=_str(message.get("text")),
        attachments_json=json.dumps(attachments, ensure_ascii=False) if attachments else None,
        is_echo=bool(message.get("is_echo")) or sender_id in own_ids,
        sent_at=_from_millis(item.get("timestamp")),
        event_id=event_id,
    )


def _first_field(entries: list[Any]) -> str | None:
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            if isinstance(change, dict) and change.get("field"):
                return _str(change["field"])
        if entry.get("messaging"):
            return "messages"
    return None


def _str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _from_millis(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
