from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import async_sessionmaker


class DbSessionMiddleware(BaseMiddleware):
    """Opens one AsyncSession per update, injected into handlers as `db`.
    Commits on success (making every persisted answer durable as soon as
    its handler returns) and rolls back if the handler raised.
    """

    def __init__(self, session_factory: async_sessionmaker):
        self.session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self.session_factory() as db:
            data["db"] = db
            try:
                result = await handler(event, data)
            except Exception:
                await db.rollback()
                raise
            await db.commit()
            return result
