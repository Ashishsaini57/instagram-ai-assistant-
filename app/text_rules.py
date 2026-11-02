"""Shared text safety rules for replies and DMs."""

import re

URL_RE = re.compile(r"(https?://|www\.|\b[\w-]+\.(com|in|net|org|io|xyz|ly|me)\b)", re.IGNORECASE)
MAX_REPLY_CHARS = 300
MAX_DM_CHARS = 1000


def contains_link(text: str) -> bool:
    return bool(URL_RE.search(text))


def reply_problem(text: str | None) -> str | None:
    """Return why a public comment reply is not allowed, or None if it is fine."""
    return _problem(text, MAX_REPLY_CHARS)


def dm_problem(text: str | None) -> str | None:
    return _problem(text, MAX_DM_CHARS)


def _problem(text: str | None, limit: int) -> str | None:
    if not text or not text.strip():
        return "empty"
    if len(text) > limit:
        return "too_long"
    if contains_link(text):
        return "contains_link"
    return None
