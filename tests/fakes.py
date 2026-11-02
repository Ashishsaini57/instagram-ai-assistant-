from app.ai.schemas import AIReplyResult, CommentInput, GenerationOutput


class FakeGenerator:
    """Records calls and returns a canned result (or raises)."""

    def __init__(self, result: AIReplyResult | None = None, error: Exception | None = None):
        self.result = result or make_result()
        self.error = error
        self.calls: list[CommentInput] = []
        self.settings_seen: list = []

    def generate(self, comment: CommentInput, settings=None) -> GenerationOutput:
        self.calls.append(comment)
        self.settings_seen.append(settings)
        if self.error:
            raise self.error
        return GenerationOutput(
            result=self.result, model="fake-model", input_tokens=120, output_tokens=30
        )


def make_result(**overrides) -> AIReplyResult:
    data = {
        "intent": "praise",
        "language": "english",
        "should_reply": True,
        "reply_text": "Thank you so much!",
        "suggest_dm": False,
        "dm_text": None,
        "confidence": 0.9,
    }
    data.update(overrides)
    return AIReplyResult.model_validate(data)
