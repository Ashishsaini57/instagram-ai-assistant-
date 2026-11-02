"""Turn new comments into reply drafts for the review queue. Never sends anything."""

import logging
import re
from collections.abc import Iterable
from datetime import UTC, datetime, time

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.ai.client import ReplyGenerator
from app.ai.prefilter import prefilter_reason
from app.ai.prompts import MAX_REPLY_CHARS
from app.ai.schemas import AIReplyResult, CommentInput, Intent
from app.config import Settings
from app.models import Comment, CommentStatus, DraftStatus, ReplyDraft

logger = logging.getLogger(__name__)

MAX_DM_CHARS = 1000
_URL = re.compile(r"(https?://|www\.|\b[\w-]+\.(com|in|net|org|io|xyz|ly|me)\b)", re.IGNORECASE)
_EMOJI = re.compile(
    "[\U0001f000-\U0001faff\U00002600-\U000027bf\U0001f900-\U0001f9ff\ufe0f\u200d]+"
)
_DM_INTENTS = {Intent.PRICE_OR_DM_REQUEST, Intent.QUESTION}
_NO_REPLY_INTENTS = {Intent.SPAM, Intent.ABUSIVE}


def ai_calls_today(db: Session) -> int:
    start = datetime.combine(datetime.now(UTC).date(), time.min, tzinfo=UTC)
    return db.scalar(
        select(func.count())
        .select_from(ReplyDraft)
        .where(ReplyDraft.ai_called.is_(True), ReplyDraft.created_at >= start)
    )


def apply_rules(result: AIReplyResult, comment: Comment, settings: Settings) -> dict:
    """Enforce safety rules in code; the AI's suggestion never overrides them."""
    fields: dict = {
        "intent": result.intent.value,
        "language": result.language.value,
        "confidence": result.confidence,
        "low_confidence": result.confidence < settings.ai_min_confidence,
        "suggest_dm": False,
        "dm_text": None,
        "reply_text": None,
    }
    if result.intent in _NO_REPLY_INTENTS or not result.should_reply:
        return {**fields, "status": DraftStatus.SKIPPED, "skip_reason": f"ai_{result.intent}"}

    reply = result.reply_text.strip()
    if not settings.reply_emoji_allowed:
        reply = re.sub(r"\s{2,}", " ", _EMOJI.sub("", reply)).strip()
    if not reply or len(reply) > MAX_REPLY_CHARS or _URL.search(reply):
        return {**fields, "status": DraftStatus.FAILED, "error": "Reply failed safety checks"}
    if settings.reply_mention_username and comment.from_username:
        reply = f"@{comment.from_username} {reply}"
    fields["reply_text"] = reply

    dm = (result.dm_text or "").strip()
    if result.suggest_dm and result.intent in _DM_INTENTS and dm and len(dm) <= MAX_DM_CHARS:
        if not _URL.search(dm):
            fields["suggest_dm"] = True
            fields["dm_text"] = dm
    return {**fields, "status": DraftStatus.PENDING_REVIEW}


def process_comment(
    db: Session, comment: Comment, settings: Settings, generator: ReplyGenerator
) -> ReplyDraft | None:
    if comment.status != CommentStatus.NEW:
        return None
    if db.scalar(select(ReplyDraft.id).where(ReplyDraft.comment_id == comment.id)):
        return None

    reason = prefilter_reason(comment.text, settings.blocked_words_list)
    if reason:
        return _save(db, comment, status=DraftStatus.SKIPPED, skip_reason=reason)

    if ai_calls_today(db) >= settings.openai_daily_limit:
        logger.warning("Daily AI limit reached; comment %s stays queued", comment.id)
        return None

    comment_input = CommentInput(
        text=comment.text,
        username=comment.from_username,
        media_product_type=comment.media_product_type,
        is_reply=comment.parent_comment_id is not None,
    )
    try:
        output = generator.generate(comment_input)
    except Exception as exc:  # any AI/network/validation failure must not crash processing
        logger.warning("AI generation failed for comment %s: %s", comment.id, type(exc).__name__)
        return _save(
            db,
            comment,
            status=DraftStatus.FAILED,
            ai_called=True,
            error=f"{type(exc).__name__}: {str(exc)[:300]}",
        )

    return _save(
        db,
        comment,
        ai_called=True,
        model=output.model,
        input_tokens=output.input_tokens,
        output_tokens=output.output_tokens,
        **apply_rules(output.result, comment, settings),
    )


def _save(db: Session, comment: Comment, **fields) -> ReplyDraft:
    draft = ReplyDraft(comment_id=comment.id, **fields)
    comment.status = {
        DraftStatus.PENDING_REVIEW: CommentStatus.PENDING_REVIEW,
        DraftStatus.SKIPPED: CommentStatus.SKIPPED,
        DraftStatus.FAILED: CommentStatus.FAILED,
    }[fields["status"]]
    db.add(draft)
    db.commit()
    return draft


def process_comments(
    session_factory: sessionmaker[Session],
    settings: Settings,
    generator: ReplyGenerator,
    comment_ids: Iterable[int] | None = None,
) -> int:
    """Process the given comments (or all NEW ones). Returns number of drafts created."""
    created = 0
    with session_factory() as db:
        query = select(Comment).where(Comment.status == CommentStatus.NEW).order_by(Comment.id)
        if comment_ids is not None:
            query = query.where(Comment.id.in_(list(comment_ids)))
        for comment in db.scalars(query).all():
            if process_comment(db, comment, settings, generator) is not None:
                created += 1
    return created
