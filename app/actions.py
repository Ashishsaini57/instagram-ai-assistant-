"""Review-queue actions: approve (send via the safety gate) or reject a reply draft."""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config import Settings
from app.instagram.sender import InstagramSender
from app.models import (
    Comment,
    CommentStatus,
    DraftStatus,
    OutboundAction,
    ReplyDraft,
    SendStatus,
)
from app.text_rules import dm_problem, reply_problem

# A draft that was actually posted can never be approved again.
APPROVABLE = {DraftStatus.PENDING_REVIEW, DraftStatus.DRY_RUN, DraftStatus.SEND_FAILED}

_DRAFT_STATUS = {
    SendStatus.SENT: DraftStatus.POSTED,
    SendStatus.DRY_RUN: DraftStatus.DRY_RUN,
    SendStatus.BLOCKED: DraftStatus.SEND_FAILED,
    SendStatus.FAILED: DraftStatus.SEND_FAILED,
}
_COMMENT_STATUS = {
    DraftStatus.POSTED: CommentStatus.REPLIED,
    DraftStatus.DRY_RUN: CommentStatus.DRY_RUN,
    DraftStatus.SEND_FAILED: CommentStatus.FAILED,
}


class ActionError(Exception):
    pass


@dataclass
class ApprovalResult:
    draft: ReplyDraft
    reply_action: OutboundAction
    dm_action: OutboundAction | None = None


def _load(db: Session, draft_id: int) -> tuple[ReplyDraft, Comment]:
    draft = db.get(ReplyDraft, draft_id)
    if draft is None:
        raise ActionError("Draft not found")
    comment = db.get(Comment, draft.comment_id)
    if comment is None:
        raise ActionError("Comment not found")
    return draft, comment


def approve_draft(
    db: Session,
    draft_id: int,
    sender: InstagramSender,
    edited_text: str | None = None,
    send_dm: bool = False,
    edited_dm_text: str | None = None,
) -> ApprovalResult:
    draft, comment = _load(db, draft_id)
    if draft.status not in APPROVABLE:
        raise ActionError(f"Draft cannot be approved in status '{draft.status}'")

    text = (edited_text if edited_text is not None else draft.reply_text or "").strip()
    if problem := reply_problem(text):
        raise ActionError(f"Reply text is not allowed: {problem}")
    dm_text = (edited_dm_text if edited_dm_text is not None else draft.dm_text or "").strip()
    if send_dm:
        if not dm_text:
            raise ActionError("This draft has no DM text")
        if problem := dm_problem(dm_text):
            raise ActionError(f"DM text is not allowed: {problem}")
        draft.dm_text = dm_text

    draft.reply_text = text
    reply_action = sender.reply_to_comment(db, comment, text, draft_id=draft.id)
    draft.status = _DRAFT_STATUS[SendStatus(reply_action.status)]
    comment.status = _COMMENT_STATUS[DraftStatus(draft.status)]

    dm_action = None
    if send_dm and reply_action.status in (SendStatus.SENT, SendStatus.DRY_RUN):
        dm_action = sender.send_private_reply(db, comment, draft.dm_text, draft_id=draft.id)
    db.commit()
    return ApprovalResult(draft=draft, reply_action=reply_action, dm_action=dm_action)


def reject_draft(db: Session, draft_id: int) -> ReplyDraft:
    draft, comment = _load(db, draft_id)
    if draft.status not in APPROVABLE:
        raise ActionError(f"Draft cannot be rejected in status '{draft.status}'")
    draft.status = DraftStatus.REJECTED
    comment.status = CommentStatus.REJECTED
    db.commit()
    return draft


def auto_handle_draft(
    db: Session, draft: ReplyDraft, settings: Settings, sender: InstagramSender
) -> ApprovalResult | None:
    """Automatic mode: only when AUTO_REPLY_ENABLED (which requires the review queue off)."""
    if not settings.auto_reply_enabled or settings.review_queue_enabled:
        return None
    if draft.status != DraftStatus.PENDING_REVIEW or draft.low_confidence:
        return None
    send_dm = settings.auto_dm_enabled and draft.suggest_dm and bool(draft.dm_text)
    return approve_draft(db, draft.id, sender, send_dm=send_dm)
