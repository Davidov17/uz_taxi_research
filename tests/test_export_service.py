"""Tests for the Excel/CSV export layer.

Dataset:
  Survey RICH  - Tashkent - Yandex Go - COMPLETED, every field populated
  Survey SPARSE - Tashkent - Uklon - COMPLETED, several fields deliberately
                  left blank (commission, bonus value, cash_pct, ...)
  Survey DRAFTY - Tashkent - Yandex Go - DRAFT (not completed)
"""

from datetime import datetime, timezone

import pytest

from telegram_bot.application.export_service import (
    ExportFilters,
    ExportService,
    workbook_to_bytes,
)
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
    RideCategory,
    RideCategoryUsage,
    Survey,
    SurveyPlatform,
    WorkingStats,
)

pytestmark = pytest.mark.asyncio

EXPECTED_SHEET_TITLES = [
    "Survey Summary", "Driver Platforms", "Working Statistics", "Earnings", "Bonuses",
    "Payments", "Categories", "Market Intelligence", "Driver Experience", "Screenshots",
    "Raw Answers", "Statistics",
]


@pytest.fixture
async def dataset(session, interviewer, city, platform_yandex, platform_uklon):
    net_basis = (await session.execute(EarningsBasis.__table__.select().where(EarningsBasis.code == "net"))).first()
    independent = (await session.execute(DriverType.__table__.select().where(DriverType.code == "independent"))).first()
    economy = (await session.execute(RideCategory.__table__.select().where(RideCategory.code == "economy"))).first()

    # ---- RICH: every field populated ----
    rich = Survey(
        human_code="TAS-000001", interviewer_id=interviewer.id, city_id=city.id,
        status=SurveyStatus.COMPLETED, survey_datetime=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc), total_weekly_earnings_amount=1_200_000,
        total_weekly_earnings_currency="UZS", perceived_market_leader_platform_id=platform_yandex.id,
        best_experience_platform_id=platform_yandex.id, screenshots_offered=True,
    )
    session.add(rich)
    await session.flush()
    session.add(WorkingStats(survey_id=rich.id, days_per_week=6, hours_per_day=8, trips_per_day=15, trips_per_week=90))
    sp_rich = SurveyPlatform(
        survey_id=rich.id, platform_id=platform_yandex.id, works_with_platform=True, is_exclusive=True,
        has_loyalty_program=True, loyalty_program_notes="Points for fuel discounts",
    )
    session.add(sp_rich)
    await session.flush()
    session.add(
        Earnings(
            survey_id=rich.id, platform_id=platform_yandex.id, weekly_earnings_amount=1_200_000,
            weekly_earnings_currency="UZS", commission_pct=20.5, earnings_basis_id=net_basis.id,
        )
    )
    session.add(
        Bonus(
            survey_id=rich.id, platform_id=platform_yandex.id, receives_bonuses=True,
            bonus_period=BonusPeriod.WEEKLY, required_trips=50, bonus_value_amount=75_000,
            bonus_value_currency="UZS", description="Bonus for 50+ trips in a week, paid Sunday night",
        )
    )
    session.add(
        Payment(
            survey_id=rich.id, platform_id=platform_yandex.id, cash_pct=30, digital_pct=70,
            early_cashout_available=True, cashout_fee_amount=1.5, cashout_fee_is_percentage=True,
        )
    )
    session.add(
        RideCategoryUsage(
            survey_id=rich.id, platform_id=platform_yandex.id, category_id=economy.id,
            is_main_category=True, trip_share_pct=90, category_requirements="Car under 7 years old",
        )
    )
    session.add(DriverEmployment(survey_id=rich.id, driver_type_id=independent.id))
    session.add(
        MarketIntelligence(
            survey_id=rich.id, platform_id=platform_yandex.id, estimated_driver_count=800,
            competitor_rider_discounts_notes="Competitor runs 15% off on weekends",
            demand_promotion_spending_notes="Heavy push during holidays",
        )
    )
    session.add(
        DriverExperience(
            survey_id=rich.id, internet_reliability=InternetReliability.EXCELLENT,
            improvement_suggestions="Faster payouts please",
            reason_to_join_new_platform="Better commission rate",
            seasonal_behavior_notes="Much busier in summer",
        )
    )
    session.add(
        Attachment(
            survey_id=rich.id, platform_id=platform_yandex.id, sequence_number=1,
            file_type=AttachmentFileType.SCREENSHOT, telegram_file_id="file_rich_1",
            telegram_file_unique_id="unique_rich_1", description="Current week",
        )
    )

    # ---- SPARSE: several fields deliberately blank ----
    sparse = Survey(
        human_code="TAS-000002", interviewer_id=interviewer.id, city_id=city.id,
        status=SurveyStatus.COMPLETED, survey_datetime=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc), total_weekly_earnings_amount=None,
    )
    session.add(sparse)
    await session.flush()
    session.add(WorkingStats(survey_id=sparse.id, days_per_week=5, hours_per_day=None, trips_per_day=None, trips_per_week=None))
    sp_sparse = SurveyPlatform(survey_id=sparse.id, platform_id=platform_uklon.id, works_with_platform=True)
    session.add(sp_sparse)
    await session.flush()
    session.add(
        Earnings(survey_id=sparse.id, platform_id=platform_uklon.id, weekly_earnings_amount=None, commission_pct=None)
    )
    session.add(Payment(survey_id=sparse.id, platform_id=platform_uklon.id, cash_pct=None, digital_pct=None))

    # ---- DRAFTY: not completed ----
    drafty = Survey(
        human_code="TAS-000003", interviewer_id=interviewer.id, city_id=city.id,
        status=SurveyStatus.DRAFT, survey_datetime=datetime.now(timezone.utc),
    )
    session.add(drafty)
    await session.flush()

    await session.flush()
    return {"rich": rich, "sparse": sparse, "drafty": drafty}


def _sheet(workbook, title):
    return workbook[title]


def _headers(ws):
    return [cell.value for cell in ws[1]]


def _rows(ws):
    return [[cell.value for cell in row] for row in ws.iter_rows(min_row=2)]


def _find_row(ws, id_col_header, id_value):
    headers = _headers(ws)
    idx = headers.index(id_col_header)
    for row in _rows(ws):
        if row[idx] == id_value:
            return dict(zip(headers, row))
    return None


# ---- structural sanity ---------------------------------------------------------


async def test_workbook_has_all_twelve_sheets_in_order(session, dataset):
    service = ExportService(session)
    workbook = await service.build_workbook()
    assert workbook.sheetnames == EXPECTED_SHEET_TITLES


async def test_no_merged_cells_anywhere(session, dataset):
    service = ExportService(session)
    workbook = await service.build_workbook(ExportFilters(include_incomplete=True))
    for title in EXPECTED_SHEET_TITLES:
        ws = workbook[title]
        assert list(ws.merged_cells.ranges) == [], f"{title} has merged cells"


async def test_workbook_serializes_to_nonempty_bytes(session, dataset):
    service = ExportService(session)
    workbook = await service.build_workbook()
    data = workbook_to_bytes(workbook)
    assert isinstance(data, bytes)
    assert data[:2] == b"PK"  # xlsx is a zip archive


# ---- completed vs. incomplete ---------------------------------------------------


async def test_completed_surveys_export_correctly(session, dataset):
    service = ExportService(session)
    workbook = await service.build_workbook()
    summary = _sheet(workbook, "Survey Summary")
    row = _find_row(summary, "survey_id", "TAS-000001")
    assert row is not None
    assert row["city"] == "Tashkent"
    assert row["interviewer"] == "Test Interviewer"
    assert row["status"] == "completed"
    assert row["total_weekly_earnings_amount"] == 1_200_000
    assert row["perceived_market_leader"] == "Yandex Go"

    earnings = _sheet(workbook, "Earnings")
    erow = _find_row(earnings, "survey_id", "TAS-000001")
    assert erow["platform"] == "Yandex Go"
    assert erow["commission_pct"] == 20.5


async def test_incomplete_surveys_excluded_by_default(session, dataset):
    service = ExportService(session)
    workbook = await service.build_workbook()
    summary = _sheet(workbook, "Survey Summary")
    ids = [row[0] for row in _rows(summary)]
    assert "TAS-000003" not in ids
    assert set(ids) == {"TAS-000001", "TAS-000002"}


async def test_incomplete_surveys_included_when_requested(session, dataset):
    service = ExportService(session)
    workbook = await service.build_workbook(ExportFilters(include_incomplete=True))
    summary = _sheet(workbook, "Survey Summary")
    ids = [row[0] for row in _rows(summary)]
    assert "TAS-000003" in ids
    row = _find_row(summary, "survey_id", "TAS-000003")
    assert row["status"] == "draft"


# ---- missing values / numeric types ---------------------------------------------


async def test_missing_values_remain_blank_not_zero_or_placeholder(session, dataset):
    service = ExportService(session)
    workbook = await service.build_workbook()

    summary_row = _find_row(_sheet(workbook, "Survey Summary"), "survey_id", "TAS-000002")
    assert summary_row["total_weekly_earnings_amount"] is None

    earnings_row = _find_row(_sheet(workbook, "Earnings"), "survey_id", "TAS-000002")
    assert earnings_row["weekly_earnings_amount"] is None
    assert earnings_row["commission_pct"] is None

    working_row = _find_row(_sheet(workbook, "Working Statistics"), "survey_id", "TAS-000002")
    assert working_row["hours_per_day"] is None
    assert working_row["trips_per_day"] is None
    # A field that WAS answered on the same row must not also be blanked.
    assert working_row["days_per_week"] == 5

    payments_row = _find_row(_sheet(workbook, "Payments"), "survey_id", "TAS-000002")
    assert payments_row["cash_pct"] is None
    assert payments_row["digital_pct"] is None


async def test_percentages_remain_numeric(session, dataset):
    service = ExportService(session)
    workbook = await service.build_workbook()
    earnings_row = _find_row(_sheet(workbook, "Earnings"), "survey_id", "TAS-000001")
    assert isinstance(earnings_row["commission_pct"], (int, float))
    assert not isinstance(earnings_row["commission_pct"], str)

    payments_row = _find_row(_sheet(workbook, "Payments"), "survey_id", "TAS-000001")
    assert isinstance(payments_row["cash_pct"], (int, float))
    assert isinstance(payments_row["digital_pct"], (int, float))

    categories_row = _find_row(_sheet(workbook, "Categories"), "survey_id", "TAS-000001")
    assert isinstance(categories_row["trip_share_pct"], (int, float))


async def test_currency_remains_numeric(session, dataset):
    service = ExportService(session)
    workbook = await service.build_workbook()
    summary_row = _find_row(_sheet(workbook, "Survey Summary"), "survey_id", "TAS-000001")
    assert isinstance(summary_row["total_weekly_earnings_amount"], (int, float))
    assert not isinstance(summary_row["total_weekly_earnings_amount"], str)

    earnings_row = _find_row(_sheet(workbook, "Earnings"), "survey_id", "TAS-000001")
    assert isinstance(earnings_row["weekly_earnings_amount"], (int, float))

    bonuses_row = _find_row(_sheet(workbook, "Bonuses"), "survey_id", "TAS-000001")
    assert isinstance(bonuses_row["bonus_value_amount"], (int, float))


# ---- content of the other sheets --------------------------------------------------


async def test_screenshots_sheet_contains_attachment_metadata(session, dataset):
    service = ExportService(session)
    workbook = await service.build_workbook()
    row = _find_row(_sheet(workbook, "Screenshots"), "survey_id", "TAS-000001")
    assert row is not None
    assert row["telegram_file_id"] == "file_rich_1"
    assert row["sequence_number"] == 1
    assert row["description"] == "Current week"
    assert row["file_type"] == "screenshot"


async def test_raw_answers_sheet_has_verbatim_text_and_excludes_blank(session, dataset):
    service = ExportService(session)
    workbook = await service.build_workbook()
    raw = _sheet(workbook, "Raw Answers")
    rows = _rows(raw)
    texts = {r[5] for r in rows if r[0] == "TAS-000001"}
    assert "Faster payouts please" in texts
    assert "Bonus for 50+ trips in a week, paid Sunday night" in texts
    assert "Competitor runs 15% off on weekends" in texts
    # sparse survey answered nothing qualitative -> contributes no rows
    assert not any(r[0] == "TAS-000002" for r in rows)


async def test_statistics_sheet_contains_aggregates(session, dataset):
    service = ExportService(session)
    workbook = await service.build_workbook()
    stats = _sheet(workbook, "Statistics")
    rows = _rows(stats)
    metrics = {(r[0], r[1]) for r in rows}
    assert ("general", "total_surveys") in metrics
    assert ("general", "completed_surveys") in metrics
    total_row = next(r for r in rows if r[1] == "total_surveys")
    completed_row = next(r for r in rows if r[1] == "completed_surveys")
    assert completed_row[3] == 2  # rich + sparse
    assert total_row[3] == 3  # StatisticsService.total_surveys counts every status: rich, sparse, drafty

    # driver estimates are clearly labeled and never mixed into sample_market
    sections = {r[0] for r in rows}
    assert any("NOT verified market data" in s for s in sections)


# ---- CSV export ------------------------------------------------------------------


async def test_csv_export_matches_workbook_data(session, dataset):
    service = ExportService(session)
    csv_text = await service.build_csv("earnings")
    lines = csv_text.strip().splitlines()
    assert lines[0] == "survey_id,city,survey_date,platform,weekly_earnings_amount,currency,earnings_basis,commission_pct"
    assert any(line.startswith("TAS-000001,Tashkent,") and "20.5" in line for line in lines[1:])


async def test_csv_export_blanks_missing_values(session, dataset):
    service = ExportService(session)
    csv_text = await service.build_csv("earnings")
    reader_lines = csv_text.strip().splitlines()
    sparse_line = next(line for line in reader_lines if line.startswith("TAS-000002,"))
    fields = sparse_line.split(",")
    # weekly_earnings_amount and commission_pct are the 5th and 8th columns (1-indexed)
    assert fields[4] == ""  # weekly_earnings_amount
    assert fields[7] == ""  # commission_pct
    assert "None" not in sparse_line


async def test_csv_export_unknown_sheet_key_raises(session, dataset):
    service = ExportService(session)
    with pytest.raises(ValueError):
        await service.build_csv("not_a_real_sheet")


# ---- filters ------------------------------------------------------------------


async def test_export_respects_city_filter(session, dataset, interviewer, platform_yandex):
    from telegram_bot.infrastructure.db.models import City

    samarkand = (await session.execute(City.__table__.select().where(City.code == "samarkand"))).first()
    other_city_survey = Survey(
        human_code="SAM-000001", interviewer_id=interviewer.id, city_id=samarkand.id,
        status=SurveyStatus.COMPLETED, survey_datetime=datetime.now(timezone.utc), completed_at=datetime.now(timezone.utc),
    )
    session.add(other_city_survey)
    await session.flush()

    service = ExportService(session)
    workbook = await service.build_workbook(ExportFilters(city_id=samarkand.id))
    summary = _sheet(workbook, "Survey Summary")
    ids = [row[0] for row in _rows(summary)]
    assert ids == ["SAM-000001"]
