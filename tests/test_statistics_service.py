"""Tests for the statistics/analytics layer, against a small synthetic
dataset with hand-computed expected values.

Dataset (all amounts in UZS, all completed unless noted):

  Survey A - Tashkent - Yandex Go only
    days=6 hours=8 trips/day=15 trips/week=90
    total_weekly_earnings=1,000,000  commission(Yandex)=20  bonus: yes, 50,000 / 50 trips
    cash=30 digital=70  category=economy(main)  driver_type=independent
    perceived_leader=Yandex Go  est_driver_count=500  internet=good
    seasonal="Business picks up a lot in summer months" -> more_in_summer

  Survey B - Tashkent - Uklon only
    days=5 hours=6 trips/day=10 trips/week=60
    total_weekly_earnings=500,000  commission(Uklon)=25  bonus: no
    cash=50 digital=50  category=comfort(main)  driver_type=fleet
    perceived_leader=Uklon  est_driver_count=None  internet=fair
    seasonal="It's about the same all year round" -> same_year_round

  Survey C - Samarkand - Yandex Go + Uklon
    days=7 hours=10 trips/day=None(skipped) trips/week=120
    total_weekly_earnings=1,500,000
    commission(Yandex)=18 commission(Uklon)=22
    bonus(Yandex): yes, 80,000 / 60 trips   bonus(Uklon): no
    cash(Yandex)=20 digital(Yandex)=80  cash(Uklon)=None(skipped)
    categories: economy+comfort on Yandex (economy main), economy on Uklon (main)
    driver_type=independent
    perceived_leader=Yandex Go  est_driver_count(Yandex)=1000 est_driver_count(Uklon)=300
    internet=excellent
    seasonal="Winter is much busier for me" -> more_in_winter

  Survey D - Samarkand - Yandex Go only
    days=6 hours=9 trips/day=12 trips/week=None(skipped)
    total_weekly_earnings=800,000  commission(Yandex)=20  bonus: no
    cash=None(skipped) digital=None(skipped)  category=economy(main)  driver_type=independent
    perceived_leader=None(skipped)  est_driver_count=None(skipped)  internet=good
    seasonal=None (skipped -> excluded from seasonality count)

  Survey E - Tashkent - Uklon only  (many fields deliberately skipped)
    days=5 hours=7 trips/day=None trips/week=None
    total_weekly_earnings=None(skipped)  commission=None(skipped)  no Bonus row at all
    cash=None digital=None  no category rows  driver_type=None (no DriverEmployment row)
    perceived_leader=None  est_driver_count=None  internet=None (no DriverExperience row)
    seasonal=None

  Survey F - DRAFT (not completed) - must not affect any stat except total_surveys
  Survey G - ABANDONED (not completed) - must not affect any stat except total_surveys
"""

from datetime import datetime, timezone

import pytest

from telegram_bot.application.statistics_service import (
    MetricSummary,
    StatisticsFilters,
    StatisticsService,
    classify_seasonality,
)
from telegram_bot.domain.enums import BonusPeriod, InternetReliability, SurveyStatus
from telegram_bot.infrastructure.db.models import (
    Bonus,
    DriverEmployment,
    DriverExperience,
    DriverType,
    Earnings,
    EarningsBasis,
    MarketIntelligence,
    Payment,
    RideCategory,
    RideCategoryUsage,
    Survey,
    SurveyPlatform,
    WorkingStats,
)

pytestmark = pytest.mark.asyncio


def _mk_survey(interviewer, city, *, code, status=SurveyStatus.COMPLETED, when=None):
    return Survey(
        human_code=code,
        interviewer_id=interviewer.id,
        city_id=city.id,
        status=status,
        survey_datetime=when or datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc) if status == SurveyStatus.COMPLETED else None,
    )


@pytest.fixture
async def dataset(session, interviewer, city, platform_yandex, platform_uklon):
    """`city` (from conftest) is Tashkent. Fetch Samarkand separately."""
    from telegram_bot.infrastructure.db.models import City

    samarkand = (
        await session.execute(City.__table__.select().where(City.code == "samarkand"))
    ).first()
    samarkand = await session.get(City, samarkand.id)

    net_basis = (await session.execute(EarningsBasis.__table__.select().where(EarningsBasis.code == "net"))).first()
    net_basis_id = net_basis.id

    independent = (await session.execute(DriverType.__table__.select().where(DriverType.code == "independent"))).first()
    fleet = (await session.execute(DriverType.__table__.select().where(DriverType.code == "fleet"))).first()

    economy = (await session.execute(RideCategory.__table__.select().where(RideCategory.code == "economy"))).first()
    comfort = (await session.execute(RideCategory.__table__.select().where(RideCategory.code == "comfort"))).first()

    tashkent = city

    # ---- Survey A ----
    a = _mk_survey(interviewer, tashkent, code="A")
    a.total_weekly_earnings_amount = 1_000_000
    a.perceived_market_leader_platform_id = platform_yandex.id
    session.add(a)
    await session.flush()
    session.add(WorkingStats(survey_id=a.id, days_per_week=6, hours_per_day=8, trips_per_day=15, trips_per_week=90))
    sp_a = SurveyPlatform(survey_id=a.id, platform_id=platform_yandex.id, works_with_platform=True)
    session.add(sp_a)
    await session.flush()
    session.add(Earnings(survey_id=a.id, platform_id=platform_yandex.id, weekly_earnings_amount=1_000_000, commission_pct=20, earnings_basis_id=net_basis_id))
    session.add(
        Bonus(
            survey_id=a.id, platform_id=platform_yandex.id, receives_bonuses=True,
            bonus_period=BonusPeriod.WEEKLY, required_trips=50, bonus_value_amount=50_000,
        )
    )
    session.add(Payment(survey_id=a.id, platform_id=platform_yandex.id, cash_pct=30, digital_pct=70))
    session.add(
        RideCategoryUsage(survey_id=a.id, platform_id=platform_yandex.id, category_id=economy.id, is_main_category=True)
    )
    session.add(DriverEmployment(survey_id=a.id, driver_type_id=independent.id))
    session.add(MarketIntelligence(survey_id=a.id, platform_id=platform_yandex.id, estimated_driver_count=500))
    session.add(
        DriverExperience(
            survey_id=a.id, internet_reliability=InternetReliability.GOOD,
            seasonal_behavior_notes="Business picks up a lot in summer months",
        )
    )

    # ---- Survey B ----
    b = _mk_survey(interviewer, tashkent, code="B")
    b.total_weekly_earnings_amount = 500_000
    b.perceived_market_leader_platform_id = platform_uklon.id
    session.add(b)
    await session.flush()
    session.add(WorkingStats(survey_id=b.id, days_per_week=5, hours_per_day=6, trips_per_day=10, trips_per_week=60))
    sp_b = SurveyPlatform(survey_id=b.id, platform_id=platform_uklon.id, works_with_platform=True)
    session.add(sp_b)
    await session.flush()
    session.add(Earnings(survey_id=b.id, platform_id=platform_uklon.id, weekly_earnings_amount=500_000, commission_pct=25, earnings_basis_id=net_basis_id))
    session.add(Bonus(survey_id=b.id, platform_id=platform_uklon.id, receives_bonuses=False))
    session.add(Payment(survey_id=b.id, platform_id=platform_uklon.id, cash_pct=50, digital_pct=50))
    session.add(
        RideCategoryUsage(survey_id=b.id, platform_id=platform_uklon.id, category_id=comfort.id, is_main_category=True)
    )
    session.add(DriverEmployment(survey_id=b.id, driver_type_id=fleet.id))
    session.add(
        DriverExperience(
            survey_id=b.id, internet_reliability=InternetReliability.FAIR,
            seasonal_behavior_notes="It's about the same all year round",
        )
    )

    # ---- Survey C ----
    c = _mk_survey(interviewer, samarkand, code="C")
    c.total_weekly_earnings_amount = 1_500_000
    c.perceived_market_leader_platform_id = platform_yandex.id
    session.add(c)
    await session.flush()
    session.add(WorkingStats(survey_id=c.id, days_per_week=7, hours_per_day=10, trips_per_day=None, trips_per_week=120))
    sp_c_y = SurveyPlatform(survey_id=c.id, platform_id=platform_yandex.id, works_with_platform=True)
    sp_c_u = SurveyPlatform(survey_id=c.id, platform_id=platform_uklon.id, works_with_platform=True)
    session.add_all([sp_c_y, sp_c_u])
    await session.flush()
    session.add(Earnings(survey_id=c.id, platform_id=platform_yandex.id, weekly_earnings_amount=900_000, commission_pct=18, earnings_basis_id=net_basis_id))
    session.add(Earnings(survey_id=c.id, platform_id=platform_uklon.id, weekly_earnings_amount=600_000, commission_pct=22, earnings_basis_id=net_basis_id))
    session.add(
        Bonus(
            survey_id=c.id, platform_id=platform_yandex.id, receives_bonuses=True,
            bonus_period=BonusPeriod.WEEKLY, required_trips=60, bonus_value_amount=80_000,
        )
    )
    session.add(Bonus(survey_id=c.id, platform_id=platform_uklon.id, receives_bonuses=False))
    session.add(Payment(survey_id=c.id, platform_id=platform_yandex.id, cash_pct=20, digital_pct=80))
    session.add(
        RideCategoryUsage(survey_id=c.id, platform_id=platform_yandex.id, category_id=economy.id, is_main_category=True)
    )
    session.add(
        RideCategoryUsage(survey_id=c.id, platform_id=platform_yandex.id, category_id=comfort.id, is_main_category=False)
    )
    session.add(
        RideCategoryUsage(survey_id=c.id, platform_id=platform_uklon.id, category_id=economy.id, is_main_category=True)
    )
    session.add(DriverEmployment(survey_id=c.id, driver_type_id=independent.id))
    session.add(MarketIntelligence(survey_id=c.id, platform_id=platform_yandex.id, estimated_driver_count=1000))
    session.add(MarketIntelligence(survey_id=c.id, platform_id=platform_uklon.id, estimated_driver_count=300))
    session.add(
        DriverExperience(
            survey_id=c.id, internet_reliability=InternetReliability.EXCELLENT,
            seasonal_behavior_notes="Winter is much busier for me",
        )
    )

    # ---- Survey D ----
    d = _mk_survey(interviewer, samarkand, code="D")
    d.total_weekly_earnings_amount = 800_000
    session.add(d)
    await session.flush()
    session.add(WorkingStats(survey_id=d.id, days_per_week=6, hours_per_day=9, trips_per_day=12, trips_per_week=None))
    sp_d = SurveyPlatform(survey_id=d.id, platform_id=platform_yandex.id, works_with_platform=True)
    session.add(sp_d)
    await session.flush()
    session.add(Earnings(survey_id=d.id, platform_id=platform_yandex.id, weekly_earnings_amount=800_000, commission_pct=20, earnings_basis_id=net_basis_id))
    session.add(Bonus(survey_id=d.id, platform_id=platform_yandex.id, receives_bonuses=False))
    session.add(
        RideCategoryUsage(survey_id=d.id, platform_id=platform_yandex.id, category_id=economy.id, is_main_category=True)
    )
    session.add(DriverEmployment(survey_id=d.id, driver_type_id=independent.id))
    session.add(DriverExperience(survey_id=d.id, internet_reliability=InternetReliability.GOOD))

    # ---- Survey E (deliberately sparse) ----
    e = _mk_survey(interviewer, tashkent, code="E")
    session.add(e)
    await session.flush()
    session.add(WorkingStats(survey_id=e.id, days_per_week=5, hours_per_day=7, trips_per_day=None, trips_per_week=None))
    sp_e = SurveyPlatform(survey_id=e.id, platform_id=platform_uklon.id, works_with_platform=True)
    session.add(sp_e)

    # ---- Survey F (DRAFT) / G (ABANDONED) ----
    f = _mk_survey(interviewer, tashkent, code="F", status=SurveyStatus.DRAFT)
    g = _mk_survey(interviewer, tashkent, code="G", status=SurveyStatus.ABANDONED)
    session.add_all([f, g])

    await session.flush()
    return {
        "tashkent": tashkent,
        "samarkand": samarkand,
        "yandex": platform_yandex,
        "uklon": platform_uklon,
        "independent": independent,
        "fleet": fleet,
        "surveys": {"A": a, "B": b, "C": c, "D": d, "E": e, "F": f, "G": g},
    }


async def _service(session) -> StatisticsService:
    return StatisticsService(session)


# ---- GENERAL ----------------------------------------------------------------


async def test_general_total_vs_completed_counts(session, dataset):
    svc = await _service(session)
    report = await svc.generate_report()
    assert report.general.total_surveys == 7  # A-G
    assert report.general.completed_surveys == 5  # A-E


async def test_general_surveys_by_city(session, dataset):
    svc = await _service(session)
    report = await svc.generate_report()
    assert report.general.surveys_by_city.counts == {"Tashkent": 3, "Samarkand": 2}
    assert report.general.surveys_by_city.total == 5


async def test_general_surveys_by_platform(session, dataset):
    svc = await _service(session)
    report = await svc.generate_report()
    # A, C, D use Yandex (3); B, C, E use Uklon (3) -- C counts under both
    assert report.general.surveys_by_platform.counts == {"Yandex Go": 3, "Uklon": 3}


# ---- WORKING ------------------------------------------------------------------


async def test_working_pattern_excludes_missing_values(session, dataset):
    svc = await _service(session)
    report = await svc.generate_report()
    trips_week = report.working_pattern.trips_per_week
    # Only A(90), B(60), C(120) have a value; D and E skipped it.
    assert trips_week.count == 3
    assert trips_week.average == pytest.approx(90.0)
    assert trips_week.median == pytest.approx(90.0)
    assert trips_week.minimum == 60
    assert trips_week.maximum == 120

    trips_day = report.working_pattern.trips_per_day
    # A(15), B(10), D(12) have a value; C and E skipped it.
    assert trips_day.count == 3
    assert trips_day.average == pytest.approx((15 + 10 + 12) / 3)

    days = report.working_pattern.days_per_week
    assert days.count == 5  # every survey answered this
    assert days.average == pytest.approx((6 + 5 + 7 + 6 + 5) / 5)


# ---- EARNINGS ------------------------------------------------------------------


async def test_earnings_weekly_summary_excludes_missing(session, dataset):
    svc = await _service(session)
    report = await svc.generate_report()
    weekly = report.earnings.weekly_earnings
    # A,B,C,D have a value (E skipped) -> 4 data points
    assert weekly.count == 4
    assert weekly.average == pytest.approx(950_000)
    assert weekly.median == pytest.approx(900_000)
    assert weekly.minimum == 500_000
    assert weekly.maximum == 1_500_000


async def test_earnings_by_platform(session, dataset):
    svc = await _service(session)
    report = await svc.generate_report()
    by_platform = report.earnings.earnings_by_platform
    assert set(by_platform) == {"Yandex Go", "Uklon"}
    # earnings_by_platform uses the per-platform Earnings.weekly_earnings_amount
    # (not the driver's combined total): Yandex -> A(1M) C(900k) D(800k);
    # Uklon -> B(500k) C(600k). E has no Earnings row at all.
    assert by_platform["Yandex Go"].count == 3
    assert by_platform["Yandex Go"].average == pytest.approx((1_000_000 + 900_000 + 800_000) / 3)
    assert by_platform["Uklon"].count == 2
    assert by_platform["Uklon"].average == pytest.approx((500_000 + 600_000) / 2)


async def test_earnings_by_city(session, dataset):
    svc = await _service(session)
    report = await svc.generate_report()
    by_city = report.earnings.earnings_by_city
    assert by_city["Tashkent"].count == 2  # A, B (E skipped earnings)
    assert by_city["Tashkent"].average == pytest.approx((1_000_000 + 500_000) / 2)
    assert by_city["Samarkand"].count == 2  # C, D


async def test_earnings_per_trip_and_per_hour(session, dataset):
    svc = await _service(session)
    report = await svc.generate_report()
    # per-trip needs total_weekly_earnings AND trips_per_week: A(1,000,000/90),
    # B(500,000/60), C(1,500,000/120); D and E excluded (missing one side).
    per_trip = report.earnings.earnings_per_trip
    assert per_trip.count == 3
    expected = [1_000_000 / 90, 500_000 / 60, 1_500_000 / 120]
    assert per_trip.average == pytest.approx(sum(expected) / 3)

    # per-hour needs total_weekly_earnings, hours_per_day, days_per_week:
    # A(1,000,000/(8*6)), B(500,000/(6*5)), C(1,500,000/(10*7)), D(800,000/(9*6))
    per_hour = report.earnings.earnings_per_hour
    assert per_hour.count == 4


# ---- COMMISSION ------------------------------------------------------------------


async def test_commission_overall_and_by_platform(session, dataset):
    svc = await _service(session)
    report = await svc.generate_report()
    commission = report.commission.commission_pct
    # A(20) B(25) C-Yandex(18) C-Uklon(22) D(20) = 5 observations; E excluded (no Earnings row)
    assert commission.count == 5
    assert commission.average == pytest.approx((20 + 25 + 18 + 22 + 20) / 5)
    assert commission.median == pytest.approx(20)
    assert commission.minimum == 18
    assert commission.maximum == 25

    by_platform = report.commission.commission_by_platform
    assert by_platform["Yandex Go"].count == 3  # A, C, D
    assert by_platform["Yandex Go"].average == pytest.approx((20 + 18 + 20) / 3)
    assert by_platform["Uklon"].count == 2  # B, C
    assert by_platform["Uklon"].average == pytest.approx((25 + 22) / 2)


# ---- PAYMENTS ------------------------------------------------------------------


async def test_payment_percentages_exclude_missing(session, dataset):
    svc = await _service(session)
    report = await svc.generate_report()
    # cash_pct answered by A(30) B(50) C-Yandex(20); D and E skipped, C-Uklon skipped
    assert report.payments.cash_pct.count == 3
    assert report.payments.cash_pct.average == pytest.approx((30 + 50 + 20) / 3)
    assert report.payments.digital_pct.count == 3


# ---- BONUSES ------------------------------------------------------------------


async def test_bonus_stats(session, dataset):
    svc = await _service(session)
    report = await svc.generate_report()
    # Bonus rows exist for A,B,C(x2),D = 5 rows; E has none.
    receives = report.bonuses.receives_bonuses
    assert receives.total == 5
    assert receives.counts == {"yes": 2, "no": 3}  # A yes, C-Yandex yes; B, C-Uklon, D no

    bonus_value = report.bonuses.bonus_value
    assert bonus_value.count == 2  # only the two "yes" rows have a value
    assert bonus_value.average == pytest.approx((50_000 + 80_000) / 2)

    threshold = report.bonuses.bonus_trip_threshold
    assert threshold.count == 2
    assert threshold.average == pytest.approx((50 + 60) / 2)


# ---- MULTI-APP ------------------------------------------------------------------


async def test_multi_app_bucket_and_combinations(session, dataset):
    svc = await _service(session)
    report = await svc.generate_report()
    bucket = report.multi_app.platform_count_bucket
    # A, B, D, E use one platform; C uses two.
    assert bucket.counts == {"one platform": 4, "multiple platforms": 1}

    combos = report.multi_app.platform_combinations
    assert combos.counts == {"Yandex Go": 2, "Uklon": 2, "Uklon, Yandex Go": 1}


async def test_exclusivity_counts_only_answered_rows(session, interviewer, city, platform_yandex, platform_uklon):
    """is_exclusive is asked only of single-platform drivers; C's two rows
    (a multi-platform driver) are left null and must not count either way."""
    from telegram_bot.application.statistics_service import StatisticsService

    exclusive_survey = Survey(
        human_code="EXC-1", interviewer_id=interviewer.id, city_id=city.id,
        status=SurveyStatus.COMPLETED, survey_datetime=datetime.now(timezone.utc), completed_at=datetime.now(timezone.utc),
    )
    non_exclusive_survey = Survey(
        human_code="EXC-2", interviewer_id=interviewer.id, city_id=city.id,
        status=SurveyStatus.COMPLETED, survey_datetime=datetime.now(timezone.utc), completed_at=datetime.now(timezone.utc),
    )
    session.add_all([exclusive_survey, non_exclusive_survey])
    await session.flush()
    session.add(SurveyPlatform(survey_id=exclusive_survey.id, platform_id=platform_yandex.id, is_exclusive=True))
    session.add(SurveyPlatform(survey_id=non_exclusive_survey.id, platform_id=platform_uklon.id, is_exclusive=False))
    await session.flush()

    svc = StatisticsService(session)
    report = await svc.generate_report()
    assert report.multi_app.exclusivity.counts == {"yes": 1, "no": 1}
    assert report.multi_app.exclusivity.total == 2


# ---- DRIVER TYPE ------------------------------------------------------------------


async def test_driver_type_distribution_excludes_survey_with_no_row(session, dataset):
    svc = await _service(session)
    report = await svc.generate_report()
    # A, C, D independent; B fleet; E has no DriverEmployment row at all.
    dist = report.driver_type.distribution
    assert dist.counts == {"Independent driver": 3, "Fleet driver": 1}
    assert dist.total == 4


# ---- CATEGORIES ------------------------------------------------------------------


async def test_category_and_main_category_distribution(session, dataset):
    svc = await _service(session)
    report = await svc.generate_report()
    # economy: A, C-Yandex, C-Yandex(comfort too), C-Uklon, D -> tally usage rows
    dist = report.categories.category_distribution
    assert dist.counts["Economy"] == 4  # A, C-Yandex, C-Uklon, D
    assert dist.counts["Comfort"] == 2  # B, C-Yandex

    main_dist = report.categories.main_category_distribution
    assert main_dist.counts["Economy"] == 4  # A, C-Yandex, C-Uklon, D
    assert main_dist.counts["Comfort"] == 1  # B


async def test_new_15q_survey_via_survey_session_feeds_dashboard_stats(session, interviewer, city, platform_yandex):
    """The dataset fixture above simulates historical (pre-redesign) rows
    inserted directly; this test instead drives a *real* SurveySession
    walkthrough (the actual current questionnaire) and checks the resulting
    survey shows up correctly in general/working/earnings/category stats —
    i.e. the dashboard stays meaningful for new surveys, not just old ones.
    """
    from telegram_bot.application.survey_session import CursorState, SurveySession

    s = await SurveySession.resume(session, CursorState.initial(), interviewer)
    q = s.current_question()
    while not s.is_review():
        q = s.current_question()
        if q.code == "city":
            await s.answer(q, str(city.id))
        elif q.code == "target_platform":
            await s.skip(q)
        elif q.code == "platforms_used":
            await s.answer(q, [str(platform_yandex.id)])
        elif q.code == "hours_and_season":
            await s.answer(q, ["7_8", "more_in_summer"])
        elif q.code == "earnings":
            options = await s.resolve_options(q)
            basis = next(o.value for o in options if o.value.isdigit())
            await s.answer(q, ["500k_1m", basis])  # midpoint 750,000
        elif q.code == "days_per_week":
            await s.answer(q, "6")
        elif q.code == "trips_per_day_range":
            await s.answer(q, "6_10")  # midpoint 8
        elif q.qtype.value == "single_choice":
            options = await s.resolve_options(q)
            await s.answer(q, options[0].value)
        elif q.qtype.value == "multi_choice":
            options = await s.resolve_options(q)
            await s.answer(q, [options[0].value])
        elif q.code == "has_screenshots":
            await s.answer(q, False)
    await s.confirm()

    svc = await _service(session)
    report = await svc.generate_report()

    assert report.general.completed_surveys >= 1

    # Q4/Q7: trips_per_week derived (days_per_week * trips_per_day midpoint)
    assert report.working_pattern.trips_per_week.count >= 1
    assert report.working_pattern.trips_per_week.maximum == 48  # 6 * 8

    # Q9: the single earnings answer also lands on Survey.total_weekly_earnings_amount
    assert report.earnings.weekly_earnings.count >= 1

    # Q6: DriverExperience.main_category now feeds the same category stat
    # historical RideCategoryUsage rows feed (first static option = Economy).
    assert report.categories.category_distribution.counts.get("Economy", 0) >= 1


# ---- MARKET: sample vs. estimates --------------------------------------------


async def test_sample_market_internet_reliability(session, dataset):
    svc = await _service(session)
    report = await svc.generate_report()
    # A=good, B=fair, C=excellent, D=good; E has no DriverExperience row.
    reliability = report.sample_market.internet_reliability
    assert reliability.total == 4
    assert reliability.counts["good"] == 2
    assert reliability.counts["fair"] == 1
    assert reliability.counts["excellent"] == 1


async def test_driver_estimates_are_kept_separate_from_sample_stats(session, dataset):
    svc = await _service(session)
    report = await svc.generate_report()

    leader = report.driver_estimates.perceived_market_leader
    # A->Yandex, B->Uklon, C->Yandex; D and E skipped this question.
    assert leader.counts == {"Yandex Go": 2, "Uklon": 1}
    assert leader.total == 3

    est_count = report.driver_estimates.estimated_driver_count
    # A(500), C-Yandex(1000), C-Uklon(300); B/D/E skipped -> 3 observations
    assert est_count.count == 3
    assert est_count.average == pytest.approx((500 + 1000 + 300) / 3)

    # Structural guarantee: estimates and sample facts never share a section.
    assert not hasattr(report.sample_market, "perceived_market_leader")
    assert not hasattr(report.driver_estimates, "internet_reliability")


async def test_seasonality_text_classification(session, dataset):
    svc = await _service(session)
    report = await svc.generate_report()
    seasonality = report.driver_estimates.seasonality_text_classification
    # A -> more_in_summer, B -> same_year_round, C -> more_in_winter; D, E blank (excluded)
    assert seasonality.counts == {"more_in_summer": 1, "same_year_round": 1, "more_in_winter": 1}
    assert seasonality.total == 3


def test_classify_seasonality_unit_cases():
    assert classify_seasonality(None) is None
    assert classify_seasonality("") is None
    assert classify_seasonality("   ") is None
    assert classify_seasonality("Much more in summer") == "more_in_summer"
    assert classify_seasonality("Winter is quiet actually, more work then") == "more_in_winter"
    assert classify_seasonality("It's about the same all year round") == "same_year_round"
    assert classify_seasonality("Depends on the holidays I guess") == "unclassified"
    # Mentions both -> ambiguous, not silently assigned to either bucket.
    assert classify_seasonality("Busier in summer AND in winter") == "unclassified"


def test_classify_seasonality_russian():
    assert classify_seasonality("Летом заказов намного больше") == "more_in_summer"
    assert classify_seasonality("Зимой работы гораздо больше") == "more_in_winter"
    assert classify_seasonality("Круглый год примерно одинаково") == "same_year_round"
    assert classify_seasonality("Зависит от праздников, не знаю") == "unclassified"
    # mentions both seasons -> ambiguous, not guessed
    assert classify_seasonality("Летом и зимой почти нет разницы, но чуть больше зимой") == "unclassified"


def test_classify_seasonality_uzbek():
    assert classify_seasonality("Yozda buyurtmalar ancha ko'p bo'ladi") == "more_in_summer"
    assert classify_seasonality("Qishda ancha ko'proq ishlayman") == "more_in_winter"
    assert classify_seasonality("Yil davomida bir xil, o'zgarmaydi") == "same_year_round"
    assert classify_seasonality("Bilmadim, bayramlarga bog'liq") == "unclassified"
    assert classify_seasonality("Yozda ham, qishda ham ko'p ishlayman") == "unclassified"


# ---- FILTERS ------------------------------------------------------------------


async def test_filter_by_city(session, dataset):
    svc = await _service(session)
    report = await svc.generate_report(StatisticsFilters(city_id=dataset["samarkand"].id))
    assert report.general.completed_surveys == 2  # C, D
    assert report.general.surveys_by_city.counts == {"Samarkand": 2}
    assert report.working_pattern.days_per_week.count == 2


async def test_filter_by_platform_scopes_all_metrics(session, dataset):
    svc = await _service(session)
    report = await svc.generate_report(StatisticsFilters(platform_id=dataset["uklon"].id))
    # Uklon used by B, C, E
    assert report.general.completed_surveys == 3
    # commission scoped to Uklon rows only: B(25), C-Uklon(22)
    assert report.commission.commission_pct.count == 2
    assert set(report.commission.commission_by_platform) == {"Uklon"}


async def test_filter_by_date_range_excludes_out_of_range_surveys(session, dataset, interviewer, city):
    from datetime import timedelta

    old = _mk_survey(interviewer, city, code="OLD", when=datetime.now(timezone.utc) - timedelta(days=400))
    old.total_weekly_earnings_amount = 999_999
    session.add(old)
    await session.flush()

    svc = await _service(session)
    recent_only = StatisticsFilters(date_from=(datetime.now(timezone.utc) - timedelta(days=30)).date())
    report = await svc.generate_report(recent_only)
    assert report.general.completed_surveys == 5  # A-E, not OLD


async def test_filter_by_driver_type(session, dataset):
    svc = await _service(session)
    report = await svc.generate_report(StatisticsFilters(driver_type_id=dataset["fleet"].id))
    assert report.general.completed_surveys == 1  # B only
    assert report.driver_type.distribution.counts == {"Fleet driver": 1}


async def test_no_data_in_scope_returns_empty_summaries_not_errors(session, interviewer, city):
    """No surveys at all for this filter combination -> everything should
    be a clean zero/empty result, not a crash or a division-by-zero."""
    svc = await _service(session)
    report = await svc.generate_report(StatisticsFilters(city_id=city.id + 30000))  # within SMALLINT range, no such city
    assert report.general.completed_surveys == 0
    assert report.working_pattern.days_per_week == MetricSummary.empty()
    assert report.general.surveys_by_city.counts == {}


# ---- current questionnaire additions: language, hours bucket, driver type/seasonal text --


async def test_language_filter_scopes_to_matching_surveys_only(session, interviewer, city):
    from telegram_bot.domain.enums import Language

    en_survey = _mk_survey(interviewer, city, code="LANG-EN")
    en_survey.language = Language.EN
    ru_survey = _mk_survey(interviewer, city, code="LANG-RU")
    ru_survey.language = Language.RU
    session.add_all([en_survey, ru_survey])
    await session.flush()

    svc = await _service(session)
    report = await svc.generate_report(StatisticsFilters(language="ru"))
    assert report.general.completed_surveys == 1

    report_en = await svc.generate_report(StatisticsFilters(language="en"))
    assert report_en.general.completed_surveys == 1

    report_all = await svc.generate_report()
    assert report_all.general.completed_surveys == 2


async def test_hours_per_day_bucket_breakdown(session, interviewer, city):
    from telegram_bot.domain.enums import HoursPerDayBucket

    s1 = _mk_survey(interviewer, city, code="HRS-1")
    s2 = _mk_survey(interviewer, city, code="HRS-2")
    session.add_all([s1, s2])
    await session.flush()
    session.add(WorkingStats(survey_id=s1.id, hours_per_day_bucket=HoursPerDayBucket.H7_8, hours_per_day=7.5))
    session.add(WorkingStats(survey_id=s2.id, hours_per_day_bucket=HoursPerDayBucket.H7_8, hours_per_day=7.5))
    await session.flush()

    svc = await _service(session)
    report = await svc.generate_report()
    assert report.working_pattern.hours_per_day_bucket.counts == {"7_8": 2}


async def test_driver_type_employment_text_classification():
    from telegram_bot.application.statistics_service import classify_driver_type

    assert classify_driver_type(None) is None
    assert classify_driver_type("") is None
    assert classify_driver_type("I work with a fleet company") == "fleet"
    assert classify_driver_type("I'm an independent driver, my own car") == "independent"
    assert classify_driver_type("not sure what that means") == "unclassified"
    # mentions both -> ambiguous, not guessed
    assert classify_driver_type("Used to be independent, now with a fleet") == "unclassified"


async def test_driver_type_employment_text_classification_russian():
    from telegram_bot.application.statistics_service import classify_driver_type

    assert classify_driver_type("Работаю от автопарка") == "fleet"
    assert classify_driver_type("Вожу в таксопарке") == "fleet"
    assert classify_driver_type("Работаю самостоятельно, собственный автомобиль") == "independent"
    assert classify_driver_type("Не знаю, как ответить") == "unclassified"


async def test_driver_type_employment_text_classification_uzbek():
    from telegram_bot.application.statistics_service import classify_driver_type

    assert classify_driver_type("Avtopark orqali ishlayman") == "fleet"
    assert classify_driver_type("Men mustaqil haydovchiman, o'z mashinasi bilan ishlayman") == "independent"
    # apostrophe-variant spellings of "o'z" must be recognized the same way
    assert classify_driver_type("Men mustaqilman, o’z avtomobili bor") == "independent"
    assert classify_driver_type("Aniq bilmayman") == "unclassified"


async def test_driver_type_stats_classifies_employment_text(session, interviewer, city):
    s1 = _mk_survey(interviewer, city, code="EMP-1")
    s2 = _mk_survey(interviewer, city, code="EMP-2")
    session.add_all([s1, s2])
    await session.flush()
    session.add(DriverEmployment(survey_id=s1.id, employment_text="I drive for a fleet company"))
    session.add(DriverEmployment(survey_id=s2.id, employment_text="Independent, using my own car"))
    await session.flush()

    svc = await _service(session)
    report = await svc.generate_report()
    assert report.driver_type.employment_text_classification.counts == {"fleet": 1, "independent": 1}


async def test_seasonal_pattern_structured_breakdown(session, interviewer, city):
    from telegram_bot.domain.enums import SeasonalPattern

    s1 = _mk_survey(interviewer, city, code="SEAS-1")
    s2 = _mk_survey(interviewer, city, code="SEAS-2")
    session.add_all([s1, s2])
    await session.flush()
    session.add(DriverExperience(survey_id=s1.id, seasonal_pattern=SeasonalPattern.MORE_IN_SUMMER))
    session.add(DriverExperience(survey_id=s2.id, seasonal_pattern=SeasonalPattern.SAME_YEAR_ROUND))
    await session.flush()

    svc = await _service(session)
    report = await svc.generate_report()
    assert report.driver_estimates.seasonal_pattern.counts == {"more_in_summer": 1, "same_year_round": 1}


async def test_structured_seasonal_pattern_is_not_overridden_by_text_heuristic(session, interviewer, city):
    """The structured answer must stay authoritative: even when a survey's
    legacy free-text note would heuristically classify differently, the
    structured seasonal_pattern field reports exactly what was selected,
    untouched, and the two breakdowns are computed completely independently."""
    from telegram_bot.domain.enums import SeasonalPattern

    s = _mk_survey(interviewer, city, code="SEAS-BOTH")
    session.add(s)
    await session.flush()
    session.add(
        DriverExperience(
            survey_id=s.id,
            seasonal_pattern=SeasonalPattern.MORE_IN_WINTER,
            seasonal_behavior_notes="Business picks up a lot in summer months",  # contradicts the structured answer
        )
    )
    await session.flush()

    svc = await _service(session)
    report = await svc.generate_report()
    assert report.driver_estimates.seasonal_pattern.counts == {"more_in_winter": 1}
    assert report.driver_estimates.seasonality_text_classification.counts == {"more_in_summer": 1}
