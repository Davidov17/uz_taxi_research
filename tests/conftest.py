import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, Message
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from alembic import command
from alembic.config import Config
from telegram_bot.infrastructure.config import settings
from telegram_bot.infrastructure.db.models import City, Interviewer, Platform, Survey
from telegram_bot.infrastructure.db.seed_data import REFERENCE_DATA
from telegram_bot.presentation.handlers.survey import router as survey_router

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", settings.test_database_url)


def _run_migrations_to_head() -> None:
    """Reset the test database to a known-clean state via the real Alembic
    migrations (base -> head), so tests exercise the exact same DDL/seed
    path used in production rather than a Base.metadata.create_all() shortcut.

    migrations/env.py reads its connection URL from the DATABASE_URL env var
    (falling back to the dev DB) regardless of what's passed to Config, so
    that env var is how we point it at the test database instead.
    """
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    cfg = Config(os.path.join(PROJECT_ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(PROJECT_ROOT, "migrations"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


@pytest_asyncio.fixture(scope="session")
async def engine() -> AsyncIterator[AsyncEngine]:
    # env.py drives its own asyncio.run() internally; run it in a worker
    # thread so it doesn't collide with the event loop this fixture is
    # already executing under.
    await asyncio.to_thread(_run_migrations_to_head)
    eng = create_async_engine(TEST_DATABASE_URL)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """One test = one outer transaction that is always rolled back.

    Uses SQLAlchemy 2.0's built-in join_transaction_mode="create_savepoint"
    (the documented recipe for joining a Session into an external
    transaction for test suites): the ORM session runs inside a SAVEPOINT
    of our outer, never-committed transaction, so session.commit() inside
    test/application code only releases the savepoint and a fresh one is
    started automatically — nothing survives past the outer rollback.
    """
    async with engine.connect() as conn:
        outer_txn = await conn.begin()
        try:
            async with AsyncSession(
                bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
            ) as db_session:
                yield db_session
        finally:
            # The Session must close (and settle its own savepoint
            # bookkeeping, even from a post-flush-error "pending rollback"
            # state) before we tear down the outer transaction — doing it
            # the other way around races two rollbacks on one connection.
            await outer_txn.rollback()


# ---- Convenience lookups / factories -------------------------------------


@pytest_asyncio.fixture
async def city(session: AsyncSession) -> City:
    result = await session.execute(
        City.__table__.select().where(City.code == REFERENCE_DATA["cities"][0]["code"])
    )
    row = result.first()
    return await session.get(City, row.id)


@pytest_asyncio.fixture
async def platform_yandex(session: AsyncSession) -> Platform:
    result = await session.execute(Platform.__table__.select().where(Platform.code == "yandex_go"))
    row = result.first()
    return await session.get(Platform, row.id)


@pytest_asyncio.fixture
async def platform_uklon(session: AsyncSession) -> Platform:
    result = await session.execute(Platform.__table__.select().where(Platform.code == "uklon"))
    row = result.first()
    return await session.get(Platform, row.id)


@pytest_asyncio.fixture
async def interviewer(session: AsyncSession) -> Interviewer:
    interviewer = Interviewer(
        telegram_user_id=int(uuid.uuid4().int % 10**9),
        full_name="Test Interviewer",
    )
    session.add(interviewer)
    await session.flush()
    return interviewer


@pytest_asyncio.fixture
async def survey(session: AsyncSession, interviewer: Interviewer, city: City) -> Survey:
    survey = Survey(
        human_code=f"TST-{uuid.uuid4().hex[:8]}",
        interviewer_id=interviewer.id,
        city_id=city.id,
        survey_datetime=datetime.now(timezone.utc),
    )
    session.add(survey)
    await session.flush()
    return survey


# ---- Shared aiogram test harness ------------------------------------------
#
# `survey_router` is a module-level singleton — aiogram refuses to attach one
# Router to more than one Dispatcher — so every test module that drives it
# through a real Dispatcher must share the *same* Dispatcher instance rather
# than each building its own. Hence these live here (session-scoped) instead
# of being duplicated per test file.


class FakeBotSession(BaseSession):
    """Records outgoing Bot API calls instead of hitting the network, and
    returns just enough of a plausible response for aiogram to accept it."""

    def __init__(self):
        super().__init__()
        self.calls: list = []

    async def close(self) -> None:
        pass

    async def stream_content(self, *args, **kwargs):
        yield b""

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        name = type(method).__name__
        if name in ("SendMessage", "SendPhoto"):
            chat_id = getattr(method, "chat_id")
            return Message(
                message_id=len(self.calls),
                date=datetime.now(timezone.utc),
                chat=Chat(id=chat_id, type="private"),
                text=getattr(method, "text", None),
            )
        return True


class StaticDbMiddleware:
    """Test stand-in for DbSessionMiddleware: injects whichever test's
    savepoint-wrapped session is currently set, so everything a handler
    does stays inside that test's transaction."""

    def __init__(self):
        self.db = None

    async def __call__(self, handler, event, data):
        data["db"] = self.db
        return await handler(event, data)


@pytest.fixture
def bot():
    return Bot(token="123456:FAKE-TOKEN-FOR-TESTS", session=FakeBotSession())


@pytest.fixture(scope="session")
def db_middleware():
    return StaticDbMiddleware()


@pytest.fixture(scope="session")
def dp(db_middleware):
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.update.middleware(db_middleware)
    dispatcher.include_router(survey_router)
    return dispatcher


@pytest.fixture(autouse=True)
def _wire_db_session(db_middleware, session):
    db_middleware.db = session
    yield
    db_middleware.db = None


@pytest.fixture(autouse=True)
def _reset_dp_fsm_storage(dp):
    """`dp` is session-scoped (see above), so its MemoryStorage would
    otherwise carry FSM state between tests — harmless as long as test
    files use disjoint user-id ranges, but that's a fragile thing to rely
    on implicitly. Clear it before every test instead.
    """
    dp.storage.storage.clear()
