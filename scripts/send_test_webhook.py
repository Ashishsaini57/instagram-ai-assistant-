"""Send a fake, correctly signed Instagram webhook to your LOCAL server.

Uses META_APP_SECRET from your environment / .env (use a fake value during development).
This never contacts Meta or Instagram.

Examples:
    python scripts/send_test_webhook.py --text "Price kya hai?"
    python scripts/send_test_webhook.py --dm --text "Hi, can you share details?"
"""

import argparse
import json
import sys
import time
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.security import SIGNATURE_HEADER, compute_signature  # noqa: E402

LOCAL_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0", "::1"}  # noqa: S104


def build_payload(args: argparse.Namespace, ig_user_id: str) -> dict:
    now = int(time.time())
    if args.dm:
        return {
            "object": "instagram",
            "entry": [
                {
                    "id": ig_user_id,
                    "time": now,
                    "messaging": [
                        {
                            "sender": {"id": args.from_id},
                            "recipient": {"id": ig_user_id},
                            "timestamp": now * 1000,
                            "message": {"mid": f"mid.test.{uuid.uuid4().hex}", "text": args.text},
                        }
                    ],
                }
            ],
        }
    return {
        "object": "instagram",
        "entry": [
            {
                "id": ig_user_id,
                "time": now,
                "changes": [
                    {
                        "field": "comments",
                        "value": {
                            "from": {"id": args.from_id, "username": args.username},
                            "media": {"id": args.media_id, "media_product_type": args.media_type},
                            "id": str(uuid.uuid4().int)[:17],
                            "text": args.text,
                        },
                    }
                ],
            }
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://127.0.0.1:8000/webhook")
    parser.add_argument("--text", default="Wow! Ye kahan se liya? 😍")
    parser.add_argument("--username", default="test_fan")
    parser.add_argument("--from-id", default="1000000000000001")
    parser.add_argument("--media-id", default="18000000000000001")
    parser.add_argument("--media-type", default="REELS", choices=["REELS", "FEED"])
    parser.add_argument("--dm", action="store_true", help="send a DM event instead of a comment")
    args = parser.parse_args()

    parsed = urlparse(args.url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in LOCAL_HOSTS:
        parser.error("--url must point to your local server (localhost / 127.0.0.1)")

    settings = get_settings()
    if not settings.meta_app_secret:
        parser.error("Set META_APP_SECRET (any fake value) in .env and restart the server.")
    ig_user_id = settings.instagram_user_id or "17841400000000000"

    body = json.dumps(build_payload(args, ig_user_id), ensure_ascii=False).encode()
    signature = compute_signature(body, settings.meta_app_secret.get_secret_value())
    req = urllib.request.Request(  # noqa: S310  (URL restricted to localhost above)
        args.url,
        data=body,
        headers={"Content-Type": "application/json", SIGNATURE_HEADER: signature},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
        print(resp.status, resp.read().decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
