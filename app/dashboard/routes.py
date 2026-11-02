"""Dashboard pages. Every action goes through app.actions / InstagramSender (all safety checks)."""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import __version__
from app.actions import ActionError, approve_draft, reject_draft
from app.ai.pipeline import ai_calls_today
from app.config import Settings
from app.dashboard.auth import (
    LOGIN_PATH,
    check_csrf,
    client_key,
    credentials_valid,
    csrf_token,
    current_user,
    dashboard_enabled,
    login_session,
    require_user,
)
from app.db import get_db
from app.instagram.media import list_media_images, public_media_url
from app.instagram.sender import (
    MESSAGING_WINDOW,
    PRIVATE_REPLY_WINDOW,
    InstagramSender,
    last_incoming_message_at,
    private_reply_used,
)
from app.models import (
    ActionKind,
    Comment,
    DraftStatus,
    Message,
    OutboundAction,
    ReplyDraft,
    SendStatus,
    as_utc,
)
from app.runtime_settings import (
    TEXT_LIMITS,
    SettingsError,
    effective_settings,
    reset_settings,
    save_settings,
)

STATIC_DIR = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

router = APIRouter(include_in_schema=False)
DbDep = Annotated[Session, Depends(get_db)]
UserDep = Annotated[str, Depends(require_user)]
CsrfField = Annotated[str, Form()]

_USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_QUEUE_VIEWS = {
    "pending": [DraftStatus.PENDING_REVIEW, DraftStatus.SEND_FAILED],
    "dry_run": [DraftStatus.DRY_RUN],
}
_KIND_LABELS = {
    ActionKind.COMMENT_REPLY: "Comment reply",
    ActionKind.PRIVATE_REPLY: "Private reply",
    ActionKind.DM_TEXT: "Text DM",
    ActionKind.DM_IMAGE: "Image DM",
}


# --- helpers ------------------------------------------------------------------


def _base(request: Request) -> Settings:
    return request.app.state.settings


def _sender(request: Request) -> InstagramSender:
    return request.app.state.sender


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def _flash(request: Request, kind: str, text: str) -> None:
    request.session["flash"] = [*request.session.get("flash", []), [kind, text]]


def _flash_action(request: Request, action: OutboundAction | None) -> None:
    if action is None:
        return
    label = _KIND_LABELS.get(ActionKind(action.kind), action.kind)
    if action.status == SendStatus.DRY_RUN:
        _flash(request, "ok", f"{label}: dry run — recorded, NOT sent (DRY_RUN is on).")
    elif action.status == SendStatus.SENT:
        _flash(request, "ok", f"{label}: sent.")
    elif action.status == SendStatus.BLOCKED:
        _flash(request, "error", f"{label}: blocked ({action.reason}).")
    else:
        _flash(request, "error", f"{label}: failed ({action.error or 'unknown error'}).")


def _localtime(settings: Settings):
    zone = ZoneInfo(settings.dashboard_timezone)

    def fmt(value: datetime | None) -> str:
        return as_utc(value).astimezone(zone).strftime("%d %b %Y, %H:%M") if value else "—"

    return fmt


def _remaining(delta: timedelta) -> str:
    minutes = max(0, int(delta.total_seconds() // 60))
    days, rest = divmod(minutes, 24 * 60)
    hours, mins = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h left"
    return f"{hours}h {mins}m left"


def _render(
    request: Request, name: str, active: str, status_code: int = 200, **context
) -> HTMLResponse:
    base = _base(request)
    return templates.TemplateResponse(
        request,
        name,
        {
            "active": active,
            "user": current_user(request),
            "csrf_token": csrf_token(request),
            "live": base.can_send_to_instagram,
            "flashes": request.session.pop("flash", []),
            "fmt": _localtime(base),
            "timezone": base.dashboard_timezone,
            **context,
        },
        status_code=status_code,
    )


def _private_reply_state(db: Session, comment: Comment, now: datetime) -> tuple[bool, str]:
    if private_reply_used(db, comment.id):
        return False, "Private reply already used"
    left = as_utc(comment.created_at) + PRIVATE_REPLY_WINDOW - now
    if left <= timedelta(0):
        return False, "Private reply window expired (7 days)"
    return True, f"Private reply window open — {_remaining(left)}"


def _check_user_id(user_id: str) -> None:
    if not _USER_ID_RE.match(user_id):
        raise HTTPException(status_code=404)


# --- login --------------------------------------------------------------------


@router.get("/")
def root() -> RedirectResponse:
    return _redirect("/dashboard")


@router.get(LOGIN_PATH)
def login_page(request: Request):
    if current_user(request):
        return _redirect("/dashboard")
    return _render(
        request, "login.html", "login", enabled=dashboard_enabled(_base(request)), error=None
    )


@router.post(LOGIN_PATH)
def login(
    request: Request,
    csrf_token: CsrfField = "",
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
):
    check_csrf(request, csrf_token)
    settings = _base(request)
    limiter = request.app.state.login_limiter
    key = client_key(request)
    enabled = dashboard_enabled(settings)
    if wait := limiter.locked_for(key):
        return _render(
            request,
            "login.html",
            "login",
            enabled=enabled,
            error=f"Too many failed attempts. Try again in {wait // 60 + 1} minute(s).",
            status_code=429,
        )
    if not credentials_valid(settings, username[:200], password[:200]):
        limiter.record_failure(key)
        return _render(
            request,
            "login.html",
            "login",
            enabled=enabled,
            error="Wrong username or password.",
            status_code=401,
        )
    limiter.reset(key)
    login_session(request, settings)
    return _redirect("/dashboard")


@router.post("/dashboard/logout")
def logout(request: Request, csrf_token: CsrfField = ""):
    check_csrf(request, csrf_token)
    request.session.clear()
    return _redirect(LOGIN_PATH)


# --- review queue -------------------------------------------------------------


@router.get("/dashboard")
def queue(request: Request, db: DbDep, user: UserDep, view: str = "pending"):
    if view not in _QUEUE_VIEWS:
        view = "pending"
    now = datetime.now(UTC)
    drafts = db.scalars(
        select(ReplyDraft)
        .where(ReplyDraft.status.in_(_QUEUE_VIEWS[view]))
        .order_by(ReplyDraft.id)
        .limit(200)
    ).all()
    items = []
    for draft in drafts:
        comment = db.get(Comment, draft.comment_id)
        dm_ok, dm_state = _private_reply_state(db, comment, now)
        items.append({"draft": draft, "comment": comment, "dm_ok": dm_ok, "dm_state": dm_state})
    counts = {
        name: db.scalar(
            select(func.count()).select_from(ReplyDraft).where(ReplyDraft.status.in_(statuses))
        )
        for name, statuses in _QUEUE_VIEWS.items()
    }
    return _render(request, "queue.html", "queue", items=items, view=view, counts=counts)


@router.post("/dashboard/drafts/{draft_id}/approve")
def approve(
    request: Request,
    draft_id: int,
    db: DbDep,
    user: UserDep,
    csrf_token: CsrfField = "",
    reply_text: Annotated[str, Form()] = "",
    dm_text: Annotated[str | None, Form()] = None,
    send_dm: Annotated[str, Form()] = "",
):
    check_csrf(request, csrf_token)
    try:
        result = approve_draft(
            db,
            draft_id,
            _sender(request),
            edited_text=reply_text,
            send_dm=send_dm == "1",
            edited_dm_text=dm_text,
        )
    except ActionError as exc:
        db.rollback()
        _flash(request, "error", f"Draft #{draft_id}: {exc}")
    else:
        _flash_action(request, result.reply_action)
        _flash_action(request, result.dm_action)
    return _redirect("/dashboard")


@router.post("/dashboard/drafts/{draft_id}/reject")
def reject(request: Request, draft_id: int, db: DbDep, user: UserDep, csrf_token: CsrfField = ""):
    check_csrf(request, csrf_token)
    try:
        reject_draft(db, draft_id)
    except ActionError as exc:
        _flash(request, "error", f"Draft #{draft_id}: {exc}")
    else:
        _flash(request, "ok", f"Draft #{draft_id} rejected.")
    return _redirect("/dashboard")


# --- messages -----------------------------------------------------------------


@router.get("/dashboard/messages")
def messages(request: Request, db: DbDep, user: UserDep):
    base = _base(request)
    now = datetime.now(UTC)
    query = select(Message.sender_id, func.max(Message.id)).where(Message.is_echo.is_(False))
    if base.instagram_user_id:
        query = query.where(Message.sender_id != base.instagram_user_id)
    rows = db.execute(
        query.group_by(Message.sender_id).order_by(func.max(Message.id).desc()).limit(100)
    ).all()
    conversations = []
    for sender_id, last_id in rows:
        last_at = last_incoming_message_at(db, sender_id)
        left = (last_at + MESSAGING_WINDOW - now) if last_at else timedelta(0)
        username = db.scalar(
            select(Comment.from_username)
            .where(Comment.from_id == sender_id, Comment.from_username.is_not(None))
            .limit(1)
        )
        conversations.append(
            {
                "user_id": sender_id,
                "username": username,
                "last": db.get(Message, last_id),
                "last_at": last_at,
                "open": left > timedelta(0),
                "left": _remaining(left),
            }
        )
    return _render(
        request,
        "messages.html",
        "messages",
        conversations=conversations,
        media_files=list_media_images(base),
        public_base_url=base.public_base_url,
    )


@router.post("/dashboard/messages/{user_id}/text")
def send_text(
    request: Request,
    user_id: str,
    db: DbDep,
    user: UserDep,
    csrf_token: CsrfField = "",
    text: Annotated[str, Form()] = "",
):
    check_csrf(request, csrf_token)
    _check_user_id(user_id)
    _flash_action(request, _sender(request).send_text_dm(db, user_id, text.strip()))
    return _redirect("/dashboard/messages")


@router.post("/dashboard/messages/{user_id}/image")
def send_image(
    request: Request,
    user_id: str,
    db: DbDep,
    user: UserDep,
    csrf_token: CsrfField = "",
    media_file: Annotated[str, Form()] = "",
    image_url: Annotated[str, Form()] = "",
):
    check_csrf(request, csrf_token)
    _check_user_id(user_id)
    base = _base(request)
    media_file, image_url = media_file.strip(), image_url.strip()
    if media_file and image_url:
        _flash(request, "error", "Choose a media file OR paste an image link, not both.")
        return _redirect("/dashboard/messages")
    if media_file:
        if media_file not in list_media_images(base):
            _flash(request, "error", "That file is not in the media folder.")
            return _redirect("/dashboard/messages")
        try:
            image_url = public_media_url(base, media_file)
        except ValueError as exc:
            _flash(request, "error", f"{exc}. Set PUBLIC_BASE_URL to send media-folder images.")
            return _redirect("/dashboard/messages")
    if not image_url:
        _flash(request, "error", "Choose a media file or paste a public https image link.")
        return _redirect("/dashboard/messages")
    _flash_action(request, _sender(request).send_image_dm(db, user_id, image_url))
    return _redirect("/dashboard/messages")


# --- activity log -------------------------------------------------------------


@router.get("/dashboard/activity")
def activity(request: Request, db: DbDep, user: UserDep, status: str = ""):
    query = select(OutboundAction).order_by(OutboundAction.id.desc()).limit(200)
    if status in {s.value for s in SendStatus}:
        query = query.where(OutboundAction.status == status)
    else:
        status = ""
    actions = db.scalars(query).all()
    comments = {
        c.id: c
        for c in db.scalars(
            select(Comment).where(Comment.id.in_({a.comment_id for a in actions if a.comment_id}))
        )
    }
    return _render(
        request,
        "activity.html",
        "activity",
        actions=actions,
        comments=comments,
        status=status,
        statuses=[s.value for s in SendStatus],
        labels={k.value: v for k, v in _KIND_LABELS.items()},
    )


# --- settings -----------------------------------------------------------------


@router.get("/dashboard/settings")
def settings_page(request: Request, db: DbDep, user: UserDep):
    base = _base(request)
    return _render(
        request,
        "settings.html",
        "settings",
        s=effective_settings(db, base),
        base=base,
        limits=TEXT_LIMITS,
        ai_calls_today=ai_calls_today(db),
        ai_provider="OpenAI" if base.openai_configured else "Offline stub (no OPENAI_API_KEY)",
        version=__version__,
    )


@router.post("/dashboard/settings")
def save_settings_form(
    request: Request,
    db: DbDep,
    user: UserDep,
    csrf_token: CsrfField = "",
    review_queue_enabled: Annotated[bool, Form()] = False,
    auto_reply_enabled: Annotated[bool, Form()] = False,
    auto_dm_enabled: Annotated[bool, Form()] = False,
    reply_emoji_allowed: Annotated[bool, Form()] = False,
    creator_name: Annotated[str, Form()] = "",
    creator_persona: Annotated[str, Form()] = "",
    dm_offer_text: Annotated[str, Form()] = "",
    blocked_words: Annotated[str, Form()] = "",
):
    check_csrf(request, csrf_token)
    # Only these fields are read; anything else in the form (e.g. dry_run) is ignored.
    updates = {
        "review_queue_enabled": review_queue_enabled,
        "auto_reply_enabled": auto_reply_enabled,
        "auto_dm_enabled": auto_dm_enabled,
        "reply_emoji_allowed": reply_emoji_allowed,
        "creator_name": creator_name,
        "creator_persona": creator_persona,
        "dm_offer_text": dm_offer_text,
        "blocked_words": blocked_words,
    }
    try:
        save_settings(db, _base(request), updates)
    except SettingsError as exc:
        _flash(request, "error", f"Not saved: {exc}")
    else:
        _flash(request, "ok", "Settings saved. DRY_RUN is unchanged (it can only be set in .env).")
    return _redirect("/dashboard/settings")


@router.post("/dashboard/settings/reset")
def reset_settings_form(request: Request, db: DbDep, user: UserDep, csrf_token: CsrfField = ""):
    check_csrf(request, csrf_token)
    reset_settings(db)
    _flash(request, "ok", "Settings reset to the .env values.")
    return _redirect("/dashboard/settings")
