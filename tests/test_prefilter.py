import pytest

from app.ai.prefilter import prefilter_reason


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("🔥🔥😍", "emoji_only"),
        ("❤️ !!", "emoji_only"),
        ("Check www.spam.com now", "contains_link"),
        ("follow me https://x.y", "contains_link"),
        ("visit cheapfollowers.xyz", "contains_link"),
        ("niceeeeeeeeeeeeeee", "repeated_characters"),
        ("Buy FOLLOWERS here", "blocked_word"),
    ],
)
def test_skipped(text, reason):
    assert prefilter_reason(text, ["followers"]) == reason


@pytest.mark.parametrize(
    "text",
    ["Nice reel!", "Price kya hai?", "बहुत बढ़िया 🔥", "Where did you buy this?", "100% true"],
)
def test_allowed(text):
    assert prefilter_reason(text, ["followers"]) is None
