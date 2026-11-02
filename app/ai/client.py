"""Reply generators: the real OpenAI client and a free offline stub."""

import json
import re
from typing import Any, Protocol

from openai import OpenAI

from app.ai.prompts import build_system_prompt, build_user_message
from app.ai.schemas import (
    AI_REPLY_JSON_SCHEMA,
    AIReplyResult,
    CommentInput,
    GenerationOutput,
    Intent,
    Language,
)
from app.config import Settings


class AIGenerationError(Exception):
    pass


class ReplyGenerator(Protocol):
    def generate(self, comment: CommentInput) -> GenerationOutput: ...


class OpenAIReplyGenerator:
    """One chat-completions call per comment with a strict JSON schema response."""

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._settings = settings
        if client is None:
            if settings.openai_api_key is None:
                raise AIGenerationError("OPENAI_API_KEY is not configured")
            client = OpenAI(
                api_key=settings.openai_api_key.get_secret_value(),
                timeout=settings.openai_timeout_seconds,
                max_retries=2,
            )
        self._client = client

    def generate(self, comment: CommentInput) -> GenerationOutput:
        s = self._settings
        response = self._client.chat.completions.create(
            model=s.openai_model,
            messages=[
                {"role": "system", "content": build_system_prompt(s)},
                {"role": "user", "content": build_user_message(comment)},
            ],
            response_format={"type": "json_schema", "json_schema": AI_REPLY_JSON_SCHEMA},
            max_completion_tokens=s.openai_max_output_tokens,
        )
        message = response.choices[0].message
        if getattr(message, "refusal", None):
            raise AIGenerationError("Model refused to answer")
        try:
            result = AIReplyResult.model_validate(json.loads(message.content or ""))
        except ValueError as exc:
            raise AIGenerationError(f"Invalid AI output: {exc.__class__.__name__}") from exc
        usage = getattr(response, "usage", None)
        return GenerationOutput(
            result=result,
            model=getattr(response, "model", None) or s.openai_model,
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
        )


_DEVANAGARI = re.compile(r"[\u0900-\u097F]")
_HINGLISH_WORDS = {
    "hai", "kya", "kaise", "kaisa", "bhai", "kitna", "kitne", "kahan", "kaha", "aap", "mujhe",
    "nahi", "bahut", "accha", "acha", "kab", "karo", "chahiye", "yaar", "mast", "bhi", "ho",
}  # fmt: skip
_PRICE_WORDS = {"price", "cost", "rate", "kitna", "kitne", "dm", "details", "buy", "link", "kimat"}


_OFFLINE_REPLIES = {
    Language.ENGLISH: {
        Intent.PRICE_OR_DM_REQUEST: "Thanks for asking! Sharing the details in DM.",
        Intent.QUESTION: "Great question! Will share more soon.",
        Intent.PRAISE: "Thank you so much!",
    },
    Language.HINGLISH: {
        Intent.PRICE_OR_DM_REQUEST: "Puchne ke liye thanks! Details DM mein share kar rahe hain.",
        Intent.QUESTION: "Accha sawaal hai! Jaldi share karenge.",
        Intent.PRAISE: "Bahut bahut shukriya!",
    },
    Language.HINDI: {
        Intent.PRICE_OR_DM_REQUEST: "पूछने के लिए धन्यवाद! डिटेल्स DM में भेज रहे हैं।",
        Intent.QUESTION: "अच्छा सवाल है! जल्द ही शेयर करेंगे।",
        Intent.PRAISE: "बहुत बहुत धन्यवाद!",
    },
}


class OfflineReplyGenerator:
    """Free, deterministic stand-in used when OPENAI_API_KEY is not set (local dev only)."""

    model_name = "offline-stub"

    def generate(self, comment: CommentInput) -> GenerationOutput:
        text = comment.text.lower()
        words = set(re.findall(r"[a-z]+", text))
        if _DEVANAGARI.search(comment.text):
            language = Language.HINDI
        elif words & _HINGLISH_WORDS:
            language = Language.HINGLISH
        else:
            language = Language.ENGLISH

        if words & _PRICE_WORDS or "कीमत" in comment.text:
            intent = Intent.PRICE_OR_DM_REQUEST
        elif "?" in text:
            intent = Intent.QUESTION
        else:
            intent = Intent.PRAISE

        suggest_dm = intent == Intent.PRICE_OR_DM_REQUEST
        result = AIReplyResult(
            intent=intent,
            language=language,
            should_reply=True,
            reply_text=_OFFLINE_REPLIES[language][intent],
            suggest_dm=suggest_dm,
            dm_text="Hi! Thanks for your interest. Here are the details." if suggest_dm else None,
            confidence=0.6,
        )
        return GenerationOutput(result=result, model=self.model_name)


def get_reply_generator(settings: Settings) -> ReplyGenerator:
    if settings.openai_configured:
        return OpenAIReplyGenerator(settings)
    return OfflineReplyGenerator()
