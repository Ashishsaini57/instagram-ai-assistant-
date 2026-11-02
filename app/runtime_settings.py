"""Dashboard-editable settings stored in the database, layered over environment settings.

Only switches and persona text can be changed here. DRY_RUN, credentials and secrets are
deliberately NOT editable: going live always requires editing the environment and restarting.
"""

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AppSetting
from app.text_rules import contains_link

BOOL_FIELDS = (
    "review_queue_enabled",
    "auto_reply_enabled",
    "auto_dm_enabled",
    "reply_emoji_allowed",
)
TEXT_LIMITS = {
    "creator_name": 100,
    "creator_persona": 500,
    "dm_offer_text": 500,
    "blocked_words": 1000,
}
EDITABLE = (*BOOL_FIELDS, *TEXT_LIMITS)
_NO_LINKS = ("creator_name", "creator_persona", "dm_offer_text")


class SettingsError(ValueError):
    pass


def load_overrides(db: Session) -> dict[str, bool | str]:
    rows = db.scalars(select(AppSetting).where(AppSetting.key.in_(EDITABLE))).all()
    return {r.key: (r.value == "true") if r.key in BOOL_FIELDS else r.value for r in rows}


def _safe(settings: Settings) -> Settings:
    # Defence in depth: never auto-send while the review queue is on.
    if settings.auto_reply_enabled and settings.review_queue_enabled:
        return settings.model_copy(update={"auto_reply_enabled": False})
    return settings


def effective_settings(db: Session, base: Settings) -> Settings:
    overrides = load_overrides(db)
    return _safe(base.model_copy(update=overrides)) if overrides else base


def validate_updates(updates: dict) -> dict[str, bool | str]:
    unknown = sorted(set(updates) - set(EDITABLE))
    if unknown:
        raise SettingsError(f"Not editable from the dashboard: {', '.join(unknown)}")
    clean: dict[str, bool | str] = {}
    for key, value in updates.items():
        if key in BOOL_FIELDS:
            if not isinstance(value, bool):
                raise SettingsError(f"{key} must be true or false")
            clean[key] = value
            continue
        text = str(value or "").strip()
        if len(text) > TEXT_LIMITS[key]:
            raise SettingsError(f"{key} must be at most {TEXT_LIMITS[key]} characters")
        if key in _NO_LINKS and contains_link(text):
            raise SettingsError(f"{key} must not contain links")
        clean[key] = text
    if "creator_name" in clean and not clean["creator_name"]:
        raise SettingsError("creator_name cannot be empty")
    return clean


def save_settings(db: Session, base: Settings, updates: dict) -> Settings:
    clean = validate_updates(updates)
    candidate = base.model_copy(update={**load_overrides(db), **clean})
    if candidate.auto_reply_enabled and candidate.review_queue_enabled:
        raise SettingsError("Turn off the review queue before enabling automatic replies.")
    for key, value in clean.items():
        row = db.get(AppSetting, key) or AppSetting(key=key)
        row.value = ("true" if value else "false") if isinstance(value, bool) else value
        db.add(row)
    db.commit()
    return effective_settings(db, base)


def reset_settings(db: Session) -> None:
    db.execute(delete(AppSetting))
    db.commit()
