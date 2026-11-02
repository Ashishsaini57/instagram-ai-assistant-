"""FastAPI application entrypoint."""

import logging
from typing import Annotated

from fastapi import Depends, FastAPI

from app import __version__
from app.config import Settings, get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())

    app = FastAPI(title="Instagram AI Assistant", version=__version__)

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
        }

    return app


app = create_app()
