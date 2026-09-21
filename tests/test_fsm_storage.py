"""Tests for the Postgres-backed FSM storage (infrastructure/fsm_storage.py)
that replaced MemoryStorage — the whole point is that state/data survive
being re-read via a brand new PostgresStorage instance, not just held in
Python memory, which is what actually matters on a host that restarts the
process between requests (e.g. Render's free tier waking from idle)."""

from contextlib import asynccontextmanager

import pytest
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import StorageKey

from telegram_bot.infrastructure.fsm_storage import PostgresStorage

pytestmark = pytest.mark.asyncio


class ExampleStates(StatesGroup):
    one = State()
    two = State()


def _key(user_id: int = 1) -> StorageKey:
    return StorageKey(bot_id=999, chat_id=user_id, user_id=user_id)


def _session_factory(session):
    """PostgresStorage expects an async_sessionmaker-shaped callable —
    something that returns a fresh async-context-manager each call. The
    shared `session` fixture is one savepoint-wrapped AsyncSession for the
    whole test (see conftest.py); AsyncSession.__aexit__ calls close(), so
    using it directly as `lambda: session` would close it after the first
    call. This thin wrapper's __aexit__ does nothing instead, matching
    conftest's own note that commit() inside it just releases a savepoint.
    """

    @asynccontextmanager
    async def factory():
        yield session

    return factory


async def test_get_state_and_data_default_empty(session):
    storage = PostgresStorage(_session_factory(session))
    assert await storage.get_state(_key()) is None
    assert await storage.get_data(_key()) == {}


async def test_set_and_get_state_round_trips(session):
    storage = PostgresStorage(_session_factory(session))
    await storage.set_state(_key(), ExampleStates.one)
    assert await storage.get_state(_key()) == "ExampleStates:one"


async def test_set_state_with_plain_string(session):
    storage = PostgresStorage(_session_factory(session))
    await storage.set_state(_key(), "some_state")
    assert await storage.get_state(_key()) == "some_state"


async def test_set_state_with_none_clears_it(session):
    storage = PostgresStorage(_session_factory(session))
    await storage.set_state(_key(), ExampleStates.one)
    await storage.set_state(_key(), None)
    assert await storage.get_state(_key()) is None


async def test_set_and_get_data_round_trips(session):
    storage = PostgresStorage(_session_factory(session))
    await storage.set_data(_key(), {"cursor": {"section_index": 3}})
    assert await storage.get_data(_key()) == {"cursor": {"section_index": 3}}


async def test_set_data_replaces_not_merges(session):
    storage = PostgresStorage(_session_factory(session))
    await storage.set_data(_key(), {"a": 1, "b": 2})
    await storage.set_data(_key(), {"c": 3})
    assert await storage.get_data(_key()) == {"c": 3}


async def test_state_and_data_are_independent(session):
    storage = PostgresStorage(_session_factory(session))
    await storage.set_data(_key(), {"cursor": "x"})
    await storage.set_state(_key(), ExampleStates.two)
    assert await storage.get_data(_key()) == {"cursor": "x"}
    assert await storage.get_state(_key()) == "ExampleStates:two"


async def test_different_keys_are_isolated(session):
    storage = PostgresStorage(_session_factory(session))
    await storage.set_data(_key(1), {"who": "alice"})
    await storage.set_data(_key(2), {"who": "bob"})
    assert await storage.get_data(_key(1)) == {"who": "alice"}
    assert await storage.get_data(_key(2)) == {"who": "bob"}


async def test_state_survives_a_fresh_storage_instance(session):
    """The actual point of this storage: a brand new PostgresStorage
    (standing in for a brand new process after a restart) sees the same
    state/data a previous instance wrote."""
    await PostgresStorage(_session_factory(session)).set_state(_key(), ExampleStates.one)
    await PostgresStorage(_session_factory(session)).set_data(_key(), {"resumed": True})

    fresh = PostgresStorage(_session_factory(session))
    assert await fresh.get_state(_key()) == "ExampleStates:one"
    assert await fresh.get_data(_key()) == {"resumed": True}
