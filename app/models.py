"""Database tables."""

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class CommentStatus(StrEnum):
    NEW = "new"
    IGNORED_SELF = "ignored_self"


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
