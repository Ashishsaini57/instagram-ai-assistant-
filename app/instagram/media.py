"""Image URL safety checks and public URLs for files in MEDIA_DIR."""

import ipaddress
import re
from urllib.parse import quote, urlparse

from app.config import Settings

_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$")
_PRIVATE_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home", ".corp")


def image_url_problem(url: str | None) -> str | None:
    """Return why an image URL can't be sent via Instagram DM, or None if it is acceptable."""
    if not url:
        return "missing_url"
    if len(url) > 2000:
        return "url_too_long"
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return "not_https"
    host = (parsed.hostname or "").lower()
    if not host:
        return "missing_host"
    if host == "localhost" or host.endswith(_PRIVATE_SUFFIXES):
        return "private_host"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return None if "." in host else "private_host"
    return None if ip.is_global else "private_host"


def public_media_url(settings: Settings, filename: str) -> str:
    """https URL for a file in MEDIA_DIR, served by this app at /media/<filename>."""
    if not settings.public_base_url:
        raise ValueError("PUBLIC_BASE_URL is not configured")
    if not _FILENAME_RE.match(filename) or ".." in filename:
        raise ValueError("Invalid media filename")
    return f"{settings.public_base_url}/media/{quote(filename)}"
