"""Regression test for the Alembic migration chain itself.

Every other test file relies on `conftest.py`'s session-scoped `engine`
fixture, which resets the *shared* test database to head once per test
run — that exercises "upgrade to head works" implicitly, but never
downgrade, and never in isolation (a failure there would just look like
every other test failing to set up). This file drives migrations against
a disposable, uniquely-named database created and dropped for this test
alone, so it can safely run the full base->head->base->head cycle without
disturbing the connection pool / savepoint machinery the rest of the
suite depends on.
"""

import asyncio
import os
import uuid

import asyncpg
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from alembic.config import Config
from telegram_bot.infrastructure.config import settings
from telegram_bot.infrastructure.db.seed_data import REFERENCE_DATA

pytestmark = pytest.mark.asyncio

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _asyncpg_dsn(sqlalchemy_url: str) -> str:
    return sqlalchemy_url.replace("postgresql+asyncpg://", "postgresql://")


def _maintenance_dsn() -> str:
    base = _asyncpg_dsn(settings.test_database_url)
    root, _, _ = base.rpartition("/")
    return f"{root}/postgres"


def _run_alembic(db_url: str, target: str, *, downgrade_first: bool = False) -> None:
    os.environ["DATABASE_URL"] = db_url
    cfg = Config(os.path.join(PROJECT_ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(PROJECT_ROOT, "migrations"))
    if downgrade_first:
        command.downgrade(cfg, "base")
    command.upgrade(cfg, target)


async def test_migration_chain_up_down_up_on_fresh_database():
    db_name = f"market_research_bot_migtest_{uuid.uuid4().hex[:12]}"
    maintenance_dsn = _maintenance_dsn()
    root_url, _, _ = _asyncpg_dsn(settings.test_database_url).rpartition("/")
    sqlalchemy_db_url = f"{root_url}/{db_name}".replace("postgresql://", "postgresql+asyncpg://")

    conn = await asyncpg.connect(maintenance_dsn)
    try:
        await conn.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await conn.close()

    original_database_url = os.environ.get("DATABASE_URL")
    try:
        await asyncio.to_thread(_run_alembic, sqlalchemy_db_url, "head")
        await _assert_migrated_state(sqlalchemy_db_url)

        # base -> head again, proving downgrade() is not a no-op/dead path
        # and the re-upgrade is idempotent from a clean slate.
        await asyncio.to_thread(_run_alembic, sqlalchemy_db_url, "head", downgrade_first=True)
        await _assert_migrated_state(sqlalchemy_db_url)
    finally:
        if original_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = original_database_url
        conn = await asyncpg.connect(maintenance_dsn)
        try:
            await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        finally:
            await conn.close()


async def _assert_migrated_state(sqlalchemy_db_url: str) -> None:
    engine = create_async_engine(sqlalchemy_db_url)
    try:
        async with engine.connect() as conn:
            table_names = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
            assert "surveys" in table_names
            assert "attachments" in table_names
            assert "metric_observations" in table_names
            assert "survey_platforms" in table_names

            city_count = (await conn.execute(text("SELECT COUNT(*) FROM cities"))).scalar_one()
            assert city_count == len(REFERENCE_DATA["cities"])
    finally:
        await engine.dispose()
