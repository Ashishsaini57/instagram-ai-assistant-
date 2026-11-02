"""Print the most recent comments and messages stored in the local database."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import create_db_engine, init_db  # noqa: E402
from app.models import Comment, Message, ReplyDraft  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-n", type=int, default=10)
    args = parser.parse_args()

    engine = create_db_engine(get_settings().database_url)
    init_db(engine)
    with Session(engine) as db:
        print("== Comments ==")
        for c in db.scalars(select(Comment).order_by(Comment.id.desc()).limit(args.n)):
            print(f"[{c.status}] {c.media_product_type} @{c.from_username}: {c.text}")
        print("== Reply drafts (review queue) ==")
        for d in db.scalars(select(ReplyDraft).order_by(ReplyDraft.id.desc()).limit(args.n)):
            flag = " LOW-CONFIDENCE" if d.low_confidence else ""
            detail = d.reply_text or d.skip_reason or d.error
            print(f"[{d.status}{flag}] comment#{d.comment_id} {d.intent}/{d.language}: {detail}")
            if d.suggest_dm:
                print(f"    DM suggestion: {d.dm_text}")
        print("== Messages ==")
        for m in db.scalars(select(Message).order_by(Message.id.desc()).limit(args.n)):
            kind = "echo" if m.is_echo else "in"
            print(f"[{kind}] {m.sender_id}: {m.text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
