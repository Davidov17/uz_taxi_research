import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from telegram_bot.infrastructure.db.models import (
    Bonus,
    Earnings,
    Payment,
    Survey,
    SurveyPlatform,
    WorkingStats,
)

pytestmark = pytest.mark.asyncio


async def test_duplicate_survey_platform_rejected(session, survey, platform_yandex):
    session.add(SurveyPlatform(survey_id=survey.id, platform_id=platform_yandex.id))
    await session.flush()

    session.add(SurveyPlatform(survey_id=survey.id, platform_id=platform_yandex.id))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_earnings_requires_matching_survey_platform_row(session, survey, platform_yandex):
    """Earnings has a composite FK into survey_platforms(survey_id, platform_id);
    inserting for a platform never added to survey_platforms must fail."""
    session.add(Earnings(survey_id=survey.id, platform_id=platform_yandex.id, commission_pct=10))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_commission_pct_out_of_range_rejected(session, survey, platform_yandex):
    session.add(SurveyPlatform(survey_id=survey.id, platform_id=platform_yandex.id))
    await session.flush()

    session.add(Earnings(survey_id=survey.id, platform_id=platform_yandex.id, commission_pct=150))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_cash_plus_digital_pct_over_100_rejected(session, survey, platform_yandex):
    session.add(SurveyPlatform(survey_id=survey.id, platform_id=platform_yandex.id))
    await session.flush()

    session.add(Payment(survey_id=survey.id, platform_id=platform_yandex.id, cash_pct=70, digital_pct=60))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_working_days_per_week_out_of_range_rejected(session, survey):
    session.add(WorkingStats(survey_id=survey.id, days_per_week=9))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_working_stats_unique_per_survey(session, survey):
    session.add(WorkingStats(survey_id=survey.id, days_per_week=5))
    await session.flush()

    session.add(WorkingStats(survey_id=survey.id, days_per_week=6))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_human_code_must_be_unique(session, interviewer, city):
    code = f"DUP-{uuid.uuid4().hex[:8]}"
    session.add(
        Survey(
            human_code=code,
            interviewer_id=interviewer.id,
            city_id=city.id,
            survey_datetime=datetime.now(timezone.utc),
        )
    )
    await session.flush()

    session.add(
        Survey(
            human_code=code,
            interviewer_id=interviewer.id,
            city_id=city.id,
            survey_datetime=datetime.now(timezone.utc),
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_deleting_survey_cascades_to_children(session, survey, platform_yandex):
    sp = SurveyPlatform(survey_id=survey.id, platform_id=platform_yandex.id)
    session.add(sp)
    await session.flush()
    session.add(Earnings(survey_id=survey.id, platform_id=platform_yandex.id, commission_pct=15))
    session.add(WorkingStats(survey_id=survey.id, days_per_week=5))
    await session.commit()

    await session.delete(survey)
    await session.commit()

    remaining_sp = await session.get(SurveyPlatform, sp.id)
    assert remaining_sp is None


async def test_cannot_delete_city_referenced_by_a_survey(session, survey, city):
    with pytest.raises(IntegrityError):
        await session.delete(city)
        await session.flush()


async def test_bonus_required_trips_cannot_be_negative(session, survey, platform_yandex):
    session.add(SurveyPlatform(survey_id=survey.id, platform_id=platform_yandex.id))
    await session.flush()

    session.add(Bonus(survey_id=survey.id, platform_id=platform_yandex.id, required_trips=-5))
    with pytest.raises(IntegrityError):
        await session.flush()
