"""Admin dashboard app: the JSON API under /api plus the static
HTML/JS/CSS frontend that consumes it. Run locally with:

    uv run uvicorn telegram_bot.web.app:app --reload

then open http://localhost:8000/ in a browser.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from telegram_bot.infrastructure.config import settings
from telegram_bot.infrastructure.logging_config import configure_logging
from telegram_bot.web.api import router as api_router
from telegram_bot.web.deps import dispose_engine, ping_db

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging(settings.log_level)
    if settings.public_base_url:
        from telegram_bot.web.bot_webhook import start_webhook, stop_webhook

        await start_webhook()
    yield
    if settings.public_base_url:
        await stop_webhook()
    await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(title="Driver Market Research — Admin Dashboard", lifespan=lifespan)
    app.include_router(api_router)
    if settings.public_base_url:
        from telegram_bot.web.bot_webhook import router as webhook_router

        app.include_router(webhook_router)

    @app.get("/health")
    async def health() -> dict:
        """Liveness/readiness probe for docker-compose and any reverse
        proxy/uptime monitor in front of this service — confirms the
        process is up *and* can actually reach the database, not just
        that uvicorn is accepting connections."""
        try:
            await ping_db()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="database unavailable") from exc
        return {"status": "ok"}

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


app = create_app()
