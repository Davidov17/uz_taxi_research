"""Composition root: wires the bot, dispatcher, DB engine, and routers.

Not imported by anything else in the codebase (tests exercise the
application/presentation layers directly), so constructing a Bot with a
real token only happens when this module is actually run.
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from telegram_bot.infrastructure.config import settings
from telegram_bot.infrastructure.db.session import make_engine, make_session_factory
from telegram_bot.infrastructure.logging_config import configure_logging
from telegram_bot.presentation.handlers.survey import router as survey_router
from telegram_bot.presentation.middlewares import DbSessionMiddleware

logger = logging.getLogger(__name__)


async def run() -> None:
    configure_logging(settings.log_level)
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is not set — see .env.example")

    engine = make_engine()
    session_factory = make_session_factory(engine)

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.middleware(DbSessionMiddleware(session_factory))
    dp.include_router(survey_router)

    logger.info("Starting bot polling (environment=%s)", settings.environment)
    try:
        # handle_signals=True (aiogram's default) installs SIGINT/SIGTERM
        # handlers that stop polling cleanly — the `finally` below then
        # still runs, so `docker compose stop`/SIGTERM drains in-flight
        # updates and disposes the DB pool instead of being killed cold.
        await dp.start_polling(bot)
    finally:
        logger.info("Shutting down: disposing DB engine")
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
