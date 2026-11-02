"""Thin client for the official Instagram Graph API (graph.instagram.com).

Only used when DRY_RUN=false; all sends go through app.instagram.sender.InstagramSender.
"""

import logging
import re
import time
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_RATE_LIMIT_CODES = {4, 17, 32, 613}


class InstagramAPIError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: int | None = None,
        subcode: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.subcode = subcode
        self.retryable = retryable


def _check_id(value: str | None) -> str:
    if not value or not _ID_RE.match(value):
        raise InstagramAPIError("Invalid Instagram ID")
    return value


class InstagramClient:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.BaseTransport | None = None,
        retry_delay: float = 1.0,
    ) -> None:
        if not settings.instagram_configured:
            raise InstagramAPIError("Instagram credentials are not configured")
        self._token = settings.instagram_access_token.get_secret_value()
        self._user_id = settings.instagram_user_id
        self._retry_delay = retry_delay
        base_url = (
            f"{settings.meta_graph_api_base_url.rstrip('/')}/{settings.meta_graph_api_version}"
        )
        # Token goes in the Authorization header, never in the URL (keeps it out of logs).
        self._http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=settings.instagram_request_timeout_seconds,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def reply_to_comment(self, comment_id: str, message: str) -> str:
        """POST /{ig-comment-id}/replies — public reply under a comment."""
        data = self._post(f"/{_check_id(comment_id)}/replies", data={"message": message})
        return str(data.get("id", ""))

    def send_private_reply(self, comment_id: str, text: str) -> str:
        """Private reply: one DM to the author of a comment (recipient.comment_id)."""
        return self._send_message({"comment_id": _check_id(comment_id)}, {"text": text})

    def send_text_message(self, recipient_id: str, text: str) -> str:
        return self._send_message({"id": _check_id(recipient_id)}, {"text": text})

    def send_image_message(self, recipient_id: str, image_url: str) -> str:
        message = {"attachment": {"type": "image", "payload": {"url": image_url}}}
        return self._send_message({"id": _check_id(recipient_id)}, message)

    def _send_message(self, recipient: dict[str, str], message: dict[str, Any]) -> str:
        data = self._post(
            f"/{_check_id(self._user_id)}/messages",
            json={"recipient": recipient, "message": message},
        )
        return str(data.get("message_id", ""))

    def _post(self, path: str, **kwargs: Any) -> dict[str, Any]:
        for attempt in (1, 2):
            try:
                response = self._http.post(path, **kwargs)
            except httpx.TransportError as exc:
                error = InstagramAPIError(f"Network error: {type(exc).__name__}", retryable=True)
            else:
                if response.is_success:
                    try:
                        body = response.json()
                    except ValueError:
                        body = {}
                    return body if isinstance(body, dict) else {}
                error = self._error_from(response)
            if not error.retryable or attempt == 2:
                raise error
            logger.warning("Retrying Instagram API call after: %s", error)
            time.sleep(self._retry_delay)
        raise AssertionError("unreachable")

    def _error_from(self, response: httpx.Response) -> InstagramAPIError:
        try:
            body = response.json()
        except ValueError:
            body = {}
        error = body.get("error") if isinstance(body, dict) else None
        if not isinstance(error, dict):
            error = {}
        code = error.get("code") if isinstance(error.get("code"), int) else None
        subcode = (
            error.get("error_subcode") if isinstance(error.get("error_subcode"), int) else None
        )
        message = self._sanitize(str(error.get("message") or response.reason_phrase or "error"))
        retryable = (
            response.status_code >= 500 or response.status_code == 429 or code in _RATE_LIMIT_CODES
        )
        return InstagramAPIError(
            f"Instagram API error {response.status_code} (code {code}): {message}"[:500],
            status_code=response.status_code,
            code=code,
            subcode=subcode,
            retryable=retryable,
        )

    def _sanitize(self, text: str) -> str:
        return text.replace(self._token, "***") if self._token else text
