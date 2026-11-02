"""Meta webhook request verification."""

import hashlib
import hmac

SIGNATURE_HEADER = "X-Hub-Signature-256"
_PREFIX = "sha256="


def compute_signature(raw_body: bytes, app_secret: str) -> str:
    digest = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return f"{_PREFIX}{digest}"


def is_valid_signature(
    raw_body: bytes, signature_header: str | None, app_secret: str | None
) -> bool:
    """Check Meta's X-Hub-Signature-256 header: HMAC-SHA256 of the raw body, keyed by app secret."""
    if not app_secret or not signature_header or not signature_header.startswith(_PREFIX):
        return False
    expected = compute_signature(raw_body, app_secret)
    return hmac.compare_digest(expected.encode(), signature_header.strip().encode())


def is_valid_verify_token(provided: str | None, expected: str | None) -> bool:
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided.encode(), expected.encode())
