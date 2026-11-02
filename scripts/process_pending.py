"""Draft replies for comments still in status 'new' (e.g. after the daily AI limit was hit)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.ai.client import get_reply_generator  # noqa: E402
from app.ai.pipeline import process_comments  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import create_db_engine, init_db  # noqa: E402


def main() -> int:
    settings = get_settings()
    engine = create_db_engine(settings.database_url)
    init_db(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    created = process_comments(factory, settings, get_reply_generator(settings))
    print(f"Drafts created: {created}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
