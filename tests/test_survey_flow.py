import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from telegram_bot.domain.enums import AttachmentFileType, BonusPeriod, InternetReliability, SurveyStatus
from telegram_bot.infrastructure.db.models import (
    Attachment,
    Bonus,
    DriverEmployment,
    DriverExperience,
    DriverType,
    Earnings,
    EarningsBasis,
    MarketIntelligence,
    Payment,
    PayoutMethod,
    RideCategory,
    RideCategoryUsage,
    Survey,
    SurveyPlatform,
    SwitchFrequency,
    WorkingStats,
)

pytestmark = pytest.mark.asyncio


async def test_survey_gets_a_unique_uuid_and_human_code(session, survey: Survey):
    assert isinstance(survey.id, uuid.UUID)
    assert survey.human_code
    assert survey.status == SurveyStatus.DRAFT


async def test_survey_defaults_and_timestamps(session, survey: Survey):
    fetched = await session.get(Survey, survey.id)
    assert fetched.created_at is not None
    assert fetched.updated_at is not None


async def test_interviewer_can_run_multiple_surveys(session, interviewer, city):
    s1 = Survey(
        human_code=f"A-{uuid.uuid4().hex[:8]}",
        interviewer_id=interviewer.id,
        city_id=city.id,
        survey_datetime=datetime.now(timezone.utc),
    )
    s2 = Survey(
        human_code=f"B-{uuid.uuid4().hex[:8]}",
        interviewer_id=interviewer.id,
        city_id=city.id,
        survey_datetime=datetime.now(timezone.utc),
    )
    session.add_all([s1, s2])
    await session.flush()

    result = await session.execute(select(Survey).where(Survey.interviewer_id == interviewer.id))
    assert len(result.scalars().all()) == 2


async def test_full_survey_across_two_platforms(session, survey, platform_yandex, platform_uklon):
    """End-to-end: one survey, two platforms, every child section populated,
    then re-read from the DB and assert everything links back correctly."""

    working_stats = WorkingStats(
        survey_id=survey.id, days_per_week=6, hours_per_day=9.5, trips_per_day=18, trips_per_week=108
    )
    session.add(working_stats)

    switch_freq = (
        await session.execute(select(SwitchFrequency).where(SwitchFrequency.code == "daily"))
    ).scalar_one()
    driver_type = (
        await session.execute(select(DriverType).where(DriverType.code == "independent"))
    ).scalar_one()
    earnings_basis = (
        await session.execute(select(EarningsBasis).where(EarningsBasis.code == "net"))
    ).scalar_one()
    payout_method = (
        await session.execute(select(PayoutMethod).where(PayoutMethod.code == "bank_card"))
    ).scalar_one()
    category = (
        await session.execute(select(RideCategory).where(RideCategory.code == "economy"))
    ).scalar_one()

    session.add(DriverEmployment(survey_id=survey.id, driver_type_id=driver_type.id))
    session.add(
        DriverExperience(
            survey_id=survey.id,
            internet_reliability=InternetReliability.GOOD,
            improvement_suggestions="Faster payouts",
            reason_to_join_new_platform="Higher commission on this platform",
            seasonal_behavior_notes="Fewer trips in summer",
        )
    )

    for seq, platform in enumerate((platform_yandex, platform_uklon), start=1):
        sp = SurveyPlatform(
            survey_id=survey.id,
            platform_id=platform.id,
            works_with_platform=True,
            is_exclusive=False,
            switch_frequency_id=switch_freq.id,
            experience_with_platform_months=14,
            has_loyalty_program=True,
            loyalty_program_notes="Points redeemable for fuel",
        )
        session.add(sp)
        await session.flush()

        session.add(
            Earnings(
                survey_id=survey.id,
                platform_id=platform.id,
                weekly_earnings_amount=1_500_000,
                earnings_basis_id=earnings_basis.id,
                commission_pct=18.5,
            )
        )
        session.add(
            Bonus(
                survey_id=survey.id,
                platform_id=platform.id,
                receives_bonuses=True,
                bonus_period=BonusPeriod.WEEKLY,
                required_trips=50,
                bonus_value_amount=100_000,
                description="Extra bonus if 50+ trips completed before Sunday",
            )
        )
        session.add(
            Payment(
                survey_id=survey.id,
                platform_id=platform.id,
                cash_pct=30,
                digital_pct=70,
                payout_method_id=payout_method.id,
                early_cashout_available=True,
                cashout_fee_amount=2.0,
                cashout_fee_is_percentage=True,
            )
        )
        session.add(
            RideCategoryUsage(
                survey_id=survey.id,
                platform_id=platform.id,
                category_id=category.id,
                trip_share_pct=80,
                is_main_category=True,
                category_requirements="Car younger than 7 years",
            )
        )
        session.add(
            MarketIntelligence(
                survey_id=survey.id,
                platform_id=platform.id,
                estimated_driver_count=5000,
                competitor_rider_discounts_notes="Competitor runs 20% off promos on weekends",
                demand_promotion_spending_notes="Heavy push during Navruz holiday",
            )
        )
        session.add(
            Attachment(
                survey_id=survey.id,
                platform_id=platform.id,
                sequence_number=seq,
                telegram_file_id=f"file_{platform.code}",
                telegram_file_unique_id=f"unique_{platform.code}",
                file_type=AttachmentFileType.SCREENSHOT,
            )
        )

    survey.status = SurveyStatus.COMPLETED
    survey.completed_at = datetime.now(timezone.utc)
    await session.commit()

    # Re-read from a clean query, not the identity map, to prove it persisted.
    result = await session.execute(
        select(Survey)
        .where(Survey.id == survey.id)
        .options(
            selectinload(Survey.survey_platforms).selectinload(SurveyPlatform.earnings),
            selectinload(Survey.survey_platforms).selectinload(SurveyPlatform.bonus),
            selectinload(Survey.survey_platforms).selectinload(SurveyPlatform.payment),
            selectinload(Survey.survey_platforms).selectinload(SurveyPlatform.ride_category_usages),
            selectinload(Survey.survey_platforms).selectinload(SurveyPlatform.market_intelligence),
            selectinload(Survey.working_stats),
            selectinload(Survey.driver_employment),
            selectinload(Survey.driver_experience),
            selectinload(Survey.attachments),
        )
    )
    reloaded = result.scalar_one()

    assert reloaded.status == SurveyStatus.COMPLETED
    assert reloaded.working_stats.trips_per_week == 108
    assert reloaded.driver_employment.driver_type_id == driver_type.id
    assert reloaded.driver_experience.internet_reliability == InternetReliability.GOOD
    assert len(reloaded.survey_platforms) == 2
    assert len(reloaded.attachments) == 2

    for sp in reloaded.survey_platforms:
        assert sp.earnings.commission_pct == 18.5
        assert sp.bonus.required_trips == 50
        assert sp.payment.cash_pct + sp.payment.digital_pct == 100
        assert len(sp.ride_category_usages) == 1
        assert sp.market_intelligence.estimated_driver_count == 5000


async def test_raw_qualitative_text_preserved_verbatim(session, survey, platform_yandex):
    sp = SurveyPlatform(survey_id=survey.id, platform_id=platform_yandex.id, works_with_platform=True)
    session.add(sp)
    await session.flush()

    verbatim = "  Driver said: \"only if it rains, otherwise never — depends on mood\"  "
    session.add(
        Bonus(survey_id=survey.id, platform_id=platform_yandex.id, description=verbatim)
    )
    await session.commit()

    stored = (
        await session.execute(
            select(Bonus.description).where(
                Bonus.survey_id == survey.id, Bonus.platform_id == platform_yandex.id
            )
        )
    ).scalar_one()
    assert stored == verbatim
