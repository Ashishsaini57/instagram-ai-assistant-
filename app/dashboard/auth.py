"""Dashboard login, session and CSRF helpers."""

import hashlib
import hmac
import math
import secrets
import threading
import time
from collections.abc import Callable

from fastapi import HTTPException, Request

from app.config import Settings

SESSION_MAX_AGE = 12 * 3600
MAX_FAILURES = 5
FAILURE_WINDOW_SECONDS = 15 * 60
LOCKOUT_SECONDS = 5 * 60
LOGIN_PATH = "/dashboard/login"


class LoginLimiter:
    """Pause logins for a client after repeated wrong passwords (in memory, per process)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def locked_for(self, key: str) -> int:
        with self._lock:
            return max(0, math.ceil(self._locked_until.get(key, 0) - self._clock()))

    def record_failure(self, key: str) -> None:
        with self._lock:
            now = self._clock()
            recent = [t for t in self._failures.get(key, []) if now - t < FAILURE_WINDOW_SECONDS]
            recent.append(now)
            if len(recent) >= MAX_FAILURES:
                self._locked_until[key] = now + LOCKOUT_SECONDS
                recent = []
            self._failures[key] = recent

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)


def dashboard_enabled(settings: Settings) -> bool:
    return bool(settings.dashboard_password and settings.dashboard_password.get_secret_value())


def credentials_valid(settings: Settings, username: str, password: str) -> bool:
    if not dashboard_enabled(settings):
        return False
    expected = settings.dashboard_password.get_secret_value()
    user_ok = hmac.compare_digest(username.encode(), settings.dashboard_username.encode())
    pass_ok = hmac.compare_digest(password.encode(), expected.encode())
    return user_ok and pass_ok


def session_secret(settings: Settings) -> str:
    if settings.session_secret_key and settings.session_secret_key.get_secret_value():
        return settings.session_secret_key.get_secret_value()
    # Development only (production requires SESSION_SECRET_KEY): logins reset on restart.
    return secrets.token_hex(32)


def password_fingerprint(settings: Settings) -> str:
    """Changes when the password changes, so existing sessions are logged out."""
    secret = session_secret_key_bytes(settings)
    password = settings.dashboard_password.get_secret_value().encode()
    return hmac.new(secret, password, hashlib.sha256).hexdigest()[:32]


def session_secret_key_bytes(settings: Settings) -> bytes:
    key = settings.session_secret_key.get_secret_value() if settings.session_secret_key else ""
    return (key or "dev-only").encode()


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


def check_csrf(request: Request, submitted: str | None) -> None:
    expected = request.session.get("csrf")
    if not expected or not submitted or not hmac.compare_digest(expected, submitted):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token")


def current_user(request: Request) -> str | None:
    settings: Settings = request.app.state.settings
    user = request.session.get("user")
    if not user or not dashboard_enabled(settings):
        return None
    fingerprint = request.session.get("pw", "")
    if not hmac.compare_digest(fingerprint, password_fingerprint(settings)):
        return None
    return user


def require_user(request: Request) -> str:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=303, headers={"Location": LOGIN_PATH})
    return user


def login_session(request: Request, settings: Settings) -> None:
    request.session.clear()
    request.session["user"] = settings.dashboard_username
    request.session["pw"] = password_fingerprint(settings)
    request.session["csrf"] = secrets.token_urlsafe(32)


def client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"
