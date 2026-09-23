"""Wires the Telegram bot into the dashboard's FastAPI app via a webhook
route, for hosts that only run one persistent process per free web
service (e.g. Render) rather than docker-compose's separate bot/web
containers. Only active when settings.public_base_url is set — see
infrastructure/config.py; docker-compose's own bot service keeps using
main.py's long-polling and never imports this module.
"""

from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Update
from fastapi import APIRouter, HTTPException, Request

from telegram_bot.infrastructure.config import settings
from telegram_bot.infrastructure.fsm_storage import PostgresStorage
from telegram_bot.presentation.handlers.survey import router as survey_router
from telegram_bot.presentation.middlewares import DbSessionMiddleware
from telegram_bot.web.deps import get_session_factory

logger = logging.getLogger(__name__)

WEBHOOK_PATH = "/telegram/webhook"

router = APIRouter()

_bot: Bot | None = None
_dispatcher: Dispatcher | None = None


def _build_bot_and_dispatcher() -> tuple[Bot, Dispatcher]:
    session_factory = get_session_factory()
    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=PostgresStorage(session_factory))
    dp.update.middleware(DbSessionMiddleware(session_factory))
    dp.include_router(survey_router)
    return bot, dp


async def start_webhook() -> None:
    """Called from the FastAPI lifespan on startup. Builds the bot/
    dispatcher once (module-level singletons, reused across requests —
    constructing a fresh Bot per webhook call would leak an aiohttp
    session each time) and registers the webhook URL with Telegram."""
    global _bot, _dispatcher
    if not settings.bot_token:
        logger.warning("BOT_TOKEN not set — skipping webhook setup")
        return
    _bot, _dispatcher = _build_bot_and_dispatcher()
    url = settings.public_base_url.rstrip("/") + WEBHOOK_PATH
    await _bot.set_webhook(
        url=url,
        secret_token=settings.webhook_secret or None,
        drop_pending_updates=False,
    )
    logger.info("Telegram webhook set to %s", url)


async def stop_webhook() -> None:
    global _bot
    if _bot is not None:
        await _bot.session.close()
        _bot = None


@router.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request) -> dict:
    if settings.webhook_secret:
        header = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if header != settings.webhook_secret:
            raise HTTPException(status_code=401, detail="invalid secret token")
    if _bot is None or _dispatcher is None:
        raise HTTPException(status_code=503, detail="bot not initialized")

    payload = await request.json()
    update = Update.model_validate(payload)
    try:
        await _dispatcher.feed_update(_bot, update)
    except Exception:
        # Unlike long-polling (where aiogram catches a handler's exception
        # internally and just logs it, so the loop keeps going),
        # feed_update() propagates one straight out. Letting that reach
        # FastAPI as a 500 makes Telegram treat *this* update as
        # undelivered and keep retrying it before sending anything newer —
        # exactly the "bot looks completely stuck" failure mode. Always
        # acknowledge the update instead; the underlying bug still needs
        # fixing, but it should never block the whole chat.
        logger.exception("Unhandled error processing Telegram update %s", update.update_id)
    return {"ok": True}
