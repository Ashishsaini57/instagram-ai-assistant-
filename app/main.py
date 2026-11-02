"""FastAPI application entrypoint."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from app import __version__
from app.ai.client import get_reply_generator
from app.config import AppEnv, Settings, get_settings
from app.dashboard.auth import SESSION_MAX_AGE, LoginLimiter, session_secret
from app.dashboard.routes import STATIC_DIR
from app.dashboard.routes import router as dashboard_router
from app.db import create_db_engine, get_db, init_db
from app.instagram.sender import InstagramSender
from app.runtime_settings import effective_settings
from app.webhooks import router as webhook_router

_SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' https:; style-src 'self'; script-src 'none'; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
}


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
    app.state.settings = settings
    app.state.login_limiter = LoginLimiter()
    app.state.session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.reply_generator = get_reply_generator(settings)
    app.state.sender = InstagramSender(settings)
    app.include_router(webhook_router)
    app.include_router(dashboard_router)
    app.mount("/dashboard/static", StaticFiles(directory=STATIC_DIR), name="dashboard-static")
    # Public image folder for DM attachments (Instagram must be able to fetch these URLs).
    app.mount("/media", StaticFiles(directory=settings.media_dir, check_dir=False), name="media")

    @app.middleware("http")
    async def dashboard_security_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/dashboard"):
            response.headers.update(_SECURITY_HEADERS)
        return response

    app.add_middleware(
        SessionMiddleware,
        secret_key=session_secret(settings),
        session_cookie="iga_session",
        max_age=SESSION_MAX_AGE,
        same_site="lax",
        https_only=settings.app_env == AppEnv.PRODUCTION,
    )

    @app.get("/health")
    def health(
        base: Annotated[Settings, Depends(get_settings)], db: Annotated[Session, Depends(get_db)]
    ) -> dict:
        try:
            settings = effective_settings(db, base)
        except SQLAlchemyError:  # health must answer even if the database is unavailable
            settings = base
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
