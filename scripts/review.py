"""Command-line review queue: list, approve or reject AI reply drafts.

Respects DRY_RUN and every safety check (same code path the dashboard will use).
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.actions import APPROVABLE, ActionError, approve_draft, reject_draft  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import create_db_engine, init_db  # noqa: E402
from app.instagram.sender import InstagramSender  # noqa: E402
from app.models import Comment, OutboundAction, ReplyDraft  # noqa: E402


def _describe(action: OutboundAction) -> str:
    detail = action.reason or action.error or action.ig_response_id or ""
    return f"{action.kind}: {action.status} {detail}".strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="show drafts waiting for review")
    approve = sub.add_parser("approve", help="approve a draft (posts reply; DRY_RUN by default)")
    approve.add_argument("draft_id", type=int)
    approve.add_argument("--text", help="edited reply text")
    approve.add_argument("--dm", action="store_true", help="also send the suggested private reply")
    reject = sub.add_parser("reject", help="reject a draft")
    reject.add_argument("draft_id", type=int)
    actions = sub.add_parser("actions", help="show recent outbound actions")
    actions.add_argument("-n", type=int, default=20)
    args = parser.parse_args()

    settings = get_settings()
    engine = create_db_engine(settings.database_url)
    init_db(engine)
    print(f"[DRY_RUN={'on' if not settings.can_send_to_instagram else 'OFF - LIVE'}]")

    with Session(engine) as db:
        try:
            if args.cmd == "list":
                query = select(ReplyDraft).where(ReplyDraft.status.in_(list(APPROVABLE)))
                for d in db.scalars(query.order_by(ReplyDraft.id)):
                    c = db.get(Comment, d.comment_id)
                    flag = " LOW-CONFIDENCE" if d.low_confidence else ""
                    print(f"#{d.id} [{d.status}{flag}] @{c.from_username}: {c.text}")
                    print(f"    reply: {d.reply_text}")
                    if d.suggest_dm:
                        print(f"    DM:    {d.dm_text}")
            elif args.cmd == "approve":
                result = approve_draft(
                    db, args.draft_id, InstagramSender(settings), args.text, send_dm=args.dm
                )
                print(_describe(result.reply_action))
                if result.dm_action:
                    print(_describe(result.dm_action))
            elif args.cmd == "reject":
                reject_draft(db, args.draft_id)
                print(f"Draft #{args.draft_id} rejected")
            else:
                query = select(OutboundAction).order_by(OutboundAction.id.desc()).limit(args.n)
                for a in db.scalars(query):
                    print(f"#{a.id} {a.created_at:%Y-%m-%d %H:%M} {_describe(a)}")
        except ActionError as exc:
            print(f"Error: {exc}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
