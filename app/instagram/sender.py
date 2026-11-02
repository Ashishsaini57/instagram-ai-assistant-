"""The single gate for every outbound Instagram action.

Order of checks: content rules -> Meta messaging rules -> DRY_RUN gate -> API call.
With DRY_RUN=true (or missing credentials) no network call is ever made.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.instagram.client import InstagramAPIError, InstagramClient
from app.instagram.media import image_url_problem
from app.models import (
    ActionKind,
    Comment,
    CommentStatus,
    Message,
    OutboundAction,
    SendStatus,
    as_utc,
)
from app.text_rules import dm_problem, reply_problem

logger = logging.getLogger(__name__)

PRIVATE_REPLY_WINDOW = timedelta(days=7)
MESSAGING_WINDOW = timedelta(hours=24)


def private_reply_used(db: Session, comment_id: int) -> bool:
    return (
        db.scalar(
            select(OutboundAction.id).where(
                OutboundAction.comment_id == comment_id,
                OutboundAction.kind == ActionKind.PRIVATE_REPLY,
                OutboundAction.status.in_([SendStatus.SENT, SendStatus.DRY_RUN]),
            )
        )
        is not None
    )


def last_incoming_message_at(db: Session, user_id: str) -> datetime | None:
    rows = db.execute(
        select(Message.sent_at, Message.created_at)
        .where(Message.sender_id == user_id, Message.is_echo.is_(False))
        .order_by(Message.id.desc())
        .limit(20)
    ).all()
    times = [as_utc(sent or created) for sent, created in rows if (sent or created)]
    return max(times) if times else None


def messaging_window_open(db: Session, user_id: str, now: datetime | None = None) -> bool:
    last = last_incoming_message_at(db, user_id)
    return last is not None and (now or datetime.now(UTC)) - last <= MESSAGING_WINDOW


class InstagramSender:
    def __init__(
        self,
        settings: Settings,
        client_factory: Callable[[], InstagramClient] | None = None,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory or (lambda: InstagramClient(settings))
        self._client: InstagramClient | None = None

    # --- public actions ---------------------------------------------------

    def reply_to_comment(
        self, db: Session, comment: Comment, text: str, draft_id: int | None = None
    ) -> OutboundAction:
        action = OutboundAction(
            kind=ActionKind.COMMENT_REPLY, comment_id=comment.id, draft_id=draft_id, text=text
        )
        if problem := reply_problem(text):
            return self._block(db, action, f"text_{problem}")
        if comment.status == CommentStatus.IGNORED_SELF:
            return self._block(db, action, "own_comment")
        return self._dispatch(db, action, lambda c: c.reply_to_comment(comment.ig_comment_id, text))

    def send_private_reply(
        self, db: Session, comment: Comment, text: str, draft_id: int | None = None
    ) -> OutboundAction:
        action = OutboundAction(
            kind=ActionKind.PRIVATE_REPLY,
            comment_id=comment.id,
            draft_id=draft_id,
            recipient_id=comment.from_id,
            text=text,
        )
        if problem := dm_problem(text):
            return self._block(db, action, f"text_{problem}")
        if comment.status == CommentStatus.IGNORED_SELF or self._is_own(comment.from_id):
            return self._block(db, action, "own_comment")
        if datetime.now(UTC) - as_utc(comment.created_at) > PRIVATE_REPLY_WINDOW:
            return self._block(db, action, "private_reply_window_expired")
        if private_reply_used(db, comment.id):
            return self._block(db, action, "private_reply_already_sent")
        return self._dispatch(
            db, action, lambda c: c.send_private_reply(comment.ig_comment_id, text)
        )

    def send_text_dm(self, db: Session, recipient_id: str, text: str) -> OutboundAction:
        action = OutboundAction(kind=ActionKind.DM_TEXT, recipient_id=recipient_id, text=text)
        if problem := dm_problem(text):
            return self._block(db, action, f"text_{problem}")
        if reason := self._dm_recipient_problem(db, recipient_id):
            return self._block(db, action, reason)
        return self._dispatch(db, action, lambda c: c.send_text_message(recipient_id, text))

    def send_image_dm(self, db: Session, recipient_id: str, image_url: str) -> OutboundAction:
        action = OutboundAction(
            kind=ActionKind.DM_IMAGE, recipient_id=recipient_id, image_url=image_url
        )
        if problem := image_url_problem(image_url):
            return self._block(db, action, f"image_{problem}")
        if reason := self._dm_recipient_problem(db, recipient_id):
            return self._block(db, action, reason)
        return self._dispatch(db, action, lambda c: c.send_image_message(recipient_id, image_url))

    # --- internals --------------------------------------------------------

    def _is_own(self, user_id: str | None) -> bool:
        return bool(user_id) and user_id == self._settings.instagram_user_id

    def _dm_recipient_problem(self, db: Session, recipient_id: str) -> str | None:
        if not recipient_id:
            return "missing_recipient"
        if self._is_own(recipient_id):
            return "own_account"
        if not messaging_window_open(db, recipient_id):
            return "messaging_window_closed"
        return None

    def _get_client(self) -> InstagramClient:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    def _block(self, db: Session, action: OutboundAction, reason: str) -> OutboundAction:
        action.status = SendStatus.BLOCKED
        action.reason = reason
        logger.info("Blocked %s: %s", action.kind, reason)
        return self._save(db, action)

    def _dispatch(
        self, db: Session, action: OutboundAction, call: Callable[[InstagramClient], str]
    ) -> OutboundAction:
        if not self._settings.can_send_to_instagram:
            action.status = SendStatus.DRY_RUN
            logger.info(
                "DRY_RUN: would send %s (comment=%s, recipient=%s)",
                action.kind,
                action.comment_id,
                action.recipient_id,
            )
            return self._save(db, action)
        try:
            action.ig_response_id = call(self._get_client()) or None
            action.status = SendStatus.SENT
        except InstagramAPIError as exc:
            action.status = SendStatus.FAILED
            action.error = str(exc)[:500]
            logger.warning("Instagram %s failed: %s", action.kind, action.error)
        return self._save(db, action)

    def _save(self, db: Session, action: OutboundAction) -> OutboundAction:
        try:
            with db.begin_nested():
                db.add(action)
        except IntegrityError:
            # Unique index: another private reply for this comment was recorded concurrently.
            action = OutboundAction(
                kind=action.kind,
                comment_id=action.comment_id,
                draft_id=action.draft_id,
                recipient_id=action.recipient_id,
                text=action.text,
                status=SendStatus.BLOCKED,
                reason="private_reply_already_sent",
            )
            db.add(action)
        db.commit()
        return action
