"""Idempotent application of the reference data in seed_data.py.

Used by the Alembic data migration (migrations/versions/0002_...) and by
test fixtures / a future admin reseed script, so there is exactly one
implementation of "how reference rows get written."
"""

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_bot.infrastructure.db.models.lookups import (
    City,
    DriverType,
    EarningsBasis,
    PayoutMethod,
    Platform,
    RideCategory,
    SwitchFrequency,
)
from telegram_bot.infrastructure.db.seed_data import REFERENCE_DATA

_TABLE_TO_MODEL = {
    "cities": City,
    "platforms": Platform,
    "ride_categories": RideCategory,
    "switch_frequencies": SwitchFrequency,
    "earnings_basis": EarningsBasis,
    "driver_types": DriverType,
    "payout_methods": PayoutMethod,
}


async def seed_reference_data(session: AsyncSession) -> None:
    """Upsert every row in REFERENCE_DATA, keyed by `code`. Safe to call
    repeatedly (e.g. once per test run) — existing rows are left untouched."""
    for table_name, rows in REFERENCE_DATA.items():
        model = _TABLE_TO_MODEL[table_name]
        stmt = pg_insert(model).values(rows)
        stmt = stmt.on_conflict_do_nothing(index_elements=["code"])
        await session.execute(stmt)
    await session.commit()
