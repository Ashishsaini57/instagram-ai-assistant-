"""Structured AI output and the JSON schema sent to OpenAI (strict structured outputs)."""

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Intent(StrEnum):
    QUESTION = "question"
    PRAISE = "praise"
    PRICE_OR_DM_REQUEST = "price_or_dm_request"
    COMPLAINT = "complaint"
    SPAM = "spam"
    ABUSIVE = "abusive"
    OTHER = "other"


class Language(StrEnum):
    HINDI = "hindi"
    ENGLISH = "english"
    HINGLISH = "hinglish"


class AIReplyResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Intent
    language: Language
    should_reply: bool
    reply_text: str
    suggest_dm: bool
    dm_text: str | None
    confidence: float = Field(ge=0, le=1)


AI_REPLY_JSON_SCHEMA: dict = {
    "name": "comment_reply",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "intent",
            "language",
            "should_reply",
            "reply_text",
            "suggest_dm",
            "dm_text",
            "confidence",
        ],
        "properties": {
            "intent": {"type": "string", "enum": [i.value for i in Intent]},
            "language": {"type": "string", "enum": [lang.value for lang in Language]},
            "should_reply": {"type": "boolean"},
            "reply_text": {"type": "string"},
            "suggest_dm": {"type": "boolean"},
            "dm_text": {"type": ["string", "null"]},
            "confidence": {"type": "number"},
        },
    },
}


@dataclass(frozen=True)
class CommentInput:
    text: str
    username: str | None
    media_product_type: str | None
    is_reply: bool


@dataclass(frozen=True)
class GenerationOutput:
    result: AIReplyResult
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
