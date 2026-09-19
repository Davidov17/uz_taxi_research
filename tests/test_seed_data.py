import pytest
from sqlalchemy import select

from telegram_bot.infrastructure.db.models import (
    City,
    DriverType,
    EarningsBasis,
    Platform,
    PayoutMethod,
    RideCategory,
    SwitchFrequency,
)
from telegram_bot.infrastructure.db.seed import seed_reference_data
from telegram_bot.infrastructure.db.seed_data import REFERENCE_DATA

pytestmark = pytest.mark.asyncio


async def _codes(session, model):
    result = await session.execute(select(model.code))
    return set(result.scalars().all())


async def test_cities_seeded(session):
    assert await _codes(session, City) == {row["code"] for row in REFERENCE_DATA["cities"]}


async def test_target_cities_present(session):
    codes = await _codes(session, City)
    for expected in ("tashkent", "samarkand", "namangan", "andijan"):
        assert expected in codes


async def test_platforms_seeded_with_other_flag(session):
    result = await session.execute(select(Platform.code, Platform.is_other))
    by_code = dict(result.all())
    assert by_code == {"yandex_go": False, "uklon": False, "other": True}


async def test_ride_categories_seeded(session):
    assert await _codes(session, RideCategory) == {row["code"] for row in REFERENCE_DATA["ride_categories"]}


async def test_switch_frequencies_seeded_in_order(session):
    result = await session.execute(select(SwitchFrequency.code).order_by(SwitchFrequency.sort_order))
    assert result.scalars().all() == [
        "never", "rarely", "weekly", "daily",
        "never_same_app", "sometimes_pick_hours", "once_a_day", "few_times_a_day",
    ]


async def test_legacy_switch_frequencies_retired_current_ones_active(session):
    """migrations/versions/da9dcac30c59 retires the previous questionnaire's
    four switch-frequency options (is_active=false) in favor of four new
    ones matching the current wording — old survey answers keep a valid
    foreign key, but new surveys are only ever offered the current four."""
    result = await session.execute(select(SwitchFrequency.code, SwitchFrequency.is_active))
    by_code = dict(result.all())
    for legacy_code in ("never", "rarely", "weekly", "daily"):
        assert by_code[legacy_code] is False
    for current_code in ("never_same_app", "sometimes_pick_hours", "once_a_day", "few_times_a_day"):
        assert by_code[current_code] is True


async def test_earnings_basis_seeded(session):
    assert await _codes(session, EarningsBasis) == {"gross", "net", "unknown"}


async def test_driver_types_seeded(session):
    assert await _codes(session, DriverType) == {"independent", "fleet", "other"}


async def test_payout_methods_seeded(session):
    assert await _codes(session, PayoutMethod) == {row["code"] for row in REFERENCE_DATA["payout_methods"]}


async def test_lookup_rows_default_active(session):
    result = await session.execute(select(City.is_active))
    assert all(result.scalars().all())


async def test_seed_reference_data_helper_is_idempotent(session):
    """telegram_bot.infrastructure.db.seed.seed_reference_data is the
    reusable upsert used outside of migrations (e.g. a future admin reseed
    command); calling it again against already-seeded data must not error
    or duplicate rows."""
    await seed_reference_data(session)
    await seed_reference_data(session)

    result = await session.execute(select(City.code))
    codes = result.scalars().all()
    assert sorted(codes) == sorted(row["code"] for row in REFERENCE_DATA["cities"])
    assert len(codes) == len(set(codes))
