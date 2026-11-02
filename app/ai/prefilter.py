"""Free checks that skip comments before spending an AI call."""

import re
import unicodedata

_URL = re.compile(r"(https?://|www\.|\b[\w-]+\.(com|in|net|org|io|xyz|ly|me)\b)", re.IGNORECASE)
_REPEATED = re.compile(r"(.)\1{9,}")


def _is_emoji_or_symbols_only(text: str) -> bool:
    for ch in text:
        if ch.isspace():
            continue
        category = unicodedata.category(ch)
        if category.startswith(("L", "N")):
            return False
    return True


def prefilter_reason(text: str, blocked_words: list[str]) -> str | None:
    """Return a skip reason, or None if the comment should go to the AI."""
    stripped = text.strip()
    if not stripped:
        return "empty"
    if _is_emoji_or_symbols_only(stripped):
        return "emoji_only"
    if _URL.search(stripped):
        return "contains_link"
    if _REPEATED.search(stripped):
        return "repeated_characters"
    lowered = stripped.lower()
    if any(word in lowered for word in blocked_words):
        return "blocked_word"
    return None
