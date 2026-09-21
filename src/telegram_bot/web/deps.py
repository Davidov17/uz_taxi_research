"""FastAPI dependencies. The engine/session factory are created lazily and
cached at module level so the app doesn't open a DB connection at import
time — tests override `get_db` entirely via `app.dependency_overrides`,
the same pattern already used for the Telegram bot's test harness (see
tests/conftest.py's `db_middleware`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from telegram_bot.infrastructure.db.session import make_engine, make_session_factory

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Public accessor so other in-process consumers (the webhook-mode
    bot in web/app.py) share this same lazily-created engine/pool instead
    of opening a second one — important on a memory-constrained free-tier
    host running bot and dashboard in one process."""
    global _engine, _session_factory
    if _session_factory is None:
        _engine = make_engine()
        _session_factory = make_session_factory(_engine)
    return _session_factory


async def get_db() -> AsyncIterator[AsyncSession]:
    async with get_session_factory()() as session:
        yield session


async def ping_db() -> None:
    """Used by the /health endpoint — a real round trip to Postgres, not
    just "the process is running", so a database outage shows up as an
    unhealthy container rather than a silently-broken bot/dashboard."""
    async with get_session_factory()() as session:
        await session.execute(text("SELECT 1"))


async def dispose_engine() -> None:
    """Called from the FastAPI lifespan's shutdown phase so the process
    doesn't leave open connections behind when Docker sends SIGTERM."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
