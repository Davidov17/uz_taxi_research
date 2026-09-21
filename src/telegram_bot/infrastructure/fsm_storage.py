"""A Postgres-backed aiogram FSM storage, so an interviewer's exact
in-progress question position survives a bot process restart instead of
vanishing with MemoryStorage. Not just a nice-to-have on a free-tier host
that spins the process down after ~15 minutes idle — without this, every
such restart would silently reset anyone mid-survey back to the language
picker (their already-*submitted* answers stay safe in the DB either way;
only the FSM cursor pointing at "which question is next" is at risk).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey, StateType
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from telegram_bot.infrastructure.db.models import FsmState


def _key_str(key: StorageKey) -> str:
    return ":".join(
        str(part)
        for part in (key.bot_id, key.chat_id, key.user_id, key.thread_id, key.business_connection_id, key.destiny)
    )


class PostgresStorage(BaseStorage):
    """Opens one short-lived session per call via the shared
    session_factory, rather than threading a request-scoped `db` through
    aiogram's own storage interface (which has no parameter for one)."""

    def __init__(self, session_factory: async_sessionmaker):
        self._session_factory = session_factory

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        state_str = state.state if isinstance(state, State) else state
        async with self._session_factory() as db:
            stmt = pg_insert(FsmState).values(key=_key_str(key), state=state_str, data={})
            stmt = stmt.on_conflict_do_update(index_elements=[FsmState.key], set_={"state": state_str})
            await db.execute(stmt)
            await db.commit()

    async def get_state(self, key: StorageKey) -> str | None:
        async with self._session_factory() as db:
            row = await db.get(FsmState, _key_str(key))
            return row.state if row is not None else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        payload = dict(data)
        async with self._session_factory() as db:
            stmt = pg_insert(FsmState).values(key=_key_str(key), state=None, data=payload)
            stmt = stmt.on_conflict_do_update(index_elements=[FsmState.key], set_={"data": payload})
            await db.execute(stmt)
            await db.commit()

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        async with self._session_factory() as db:
            row = await db.get(FsmState, _key_str(key))
            return dict(row.data) if row is not None and row.data else {}

    async def close(self) -> None:
        pass
