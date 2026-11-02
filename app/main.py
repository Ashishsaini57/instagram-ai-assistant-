"""FastAPI application entrypoint."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import sessionmaker

from app import __version__
from app.ai.client import get_reply_generator
from app.config import Settings, get_settings
from app.db import create_db_engine, init_db
from app.instagram.sender import InstagramSender
from app.webhooks import router as webhook_router


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())

    engine = create_db_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        init_db(engine)
        yield
        engine.dispose()

    app = FastAPI(title="Instagram AI Assistant", version=__version__, lifespan=lifespan)
    app.state.engine = engine
    app.state.session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.reply_generator = get_reply_generator(settings)
    app.state.sender = InstagramSender(settings)
    app.include_router(webhook_router)
    # Public image folder for DM attachments (Instagram must be able to fetch these URLs).
    app.mount("/media", StaticFiles(directory=settings.media_dir, check_dir=False), name="media")

    @app.get("/health")
    def health(settings: Annotated[Settings, Depends(get_settings)]) -> dict:
        # Only booleans are exposed — never secret values.
        return {
            "status": "ok",
            "version": __version__,
            "env": settings.app_env.value,
            "dry_run": settings.dry_run,
            "review_queue_enabled": settings.review_queue_enabled,
            "auto_reply_enabled": settings.auto_reply_enabled,
            "auto_dm_enabled": settings.auto_dm_enabled,
            "meta_configured": settings.meta_configured,
            "instagram_configured": settings.instagram_configured,
            "openai_configured": settings.openai_configured,
            "public_base_url_configured": settings.public_base_url is not None,
        }

    return app


app = create_app()
