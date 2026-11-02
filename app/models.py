"""Database tables."""

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class CommentStatus(StrEnum):
    NEW = "new"
    IGNORED_SELF = "ignored_self"
    PENDING_REVIEW = "pending_review"
    SKIPPED = "skipped"
    FAILED = "failed"


class DraftStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    SKIPPED = "skipped"
    FAILED = "failed"


class WebhookEvent(Base):
    """Audit log of every validly signed webhook delivery."""

    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    object: Mapped[str | None] = mapped_column(String(64))
    field: Mapped[str | None] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ig_comment_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    media_id: Mapped[str | None] = mapped_column(String(64), index=True)
    media_product_type: Mapped[str | None] = mapped_column(String(32))
    parent_comment_id: Mapped[str | None] = mapped_column(String(64))
    from_id: Mapped[str | None] = mapped_column(String(64), index=True)
    from_username: Mapped[str | None] = mapped_column(String(255))
    text: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default=CommentStatus.NEW, index=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("webhook_events.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ig_message_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    sender_id: Mapped[str] = mapped_column(String(64), index=True)
    recipient_id: Mapped[str] = mapped_column(String(64))
    text: Mapped[str | None] = mapped_column(Text)
    attachments_json: Mapped[str | None] = mapped_column(Text)
    is_echo: Mapped[bool] = mapped_column(Boolean, default=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event_id: Mapped[int | None] = mapped_column(ForeignKey("webhook_events.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReplyDraft(Base):
    """AI analysis + proposed reply for one comment. Nothing here is sent automatically."""

    __tablename__ = "reply_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    comment_id: Mapped[int] = mapped_column(ForeignKey("comments.id"), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    skip_reason: Mapped[str | None] = mapped_column(String(64))
    intent: Mapped[str | None] = mapped_column(String(32))
    language: Mapped[str | None] = mapped_column(String(16))
    confidence: Mapped[float | None] = mapped_column(Float)
    low_confidence: Mapped[bool] = mapped_column(Boolean, default=False)
    reply_text: Mapped[str | None] = mapped_column(Text)
    suggest_dm: Mapped[bool] = mapped_column(Boolean, default=False)
    dm_text: Mapped[str | None] = mapped_column(Text)
    ai_called: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    model: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
