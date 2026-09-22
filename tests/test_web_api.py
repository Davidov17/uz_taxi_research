"""Tests for the admin dashboard's JSON API (src/telegram_bot/web/).

Dataset:
  Survey ONE - Tashkent - Yandex Go only - COMPLETED
    days=6 hours=8 trips/day=15 total_weekly_earnings=1,000,000 commission=20
    is_exclusive=True  receives_bonuses=True  cash_pct=30
    perceived_leader=Yandex Go  reason_to_join="Better rates"
    improvement_suggestions="Faster payouts"  category_requirements="Car under 7 years"
    one screenshot uploaded (metadata only, no local file)

  Survey TWO - Samarkand - Yandex Go + Uklon - COMPLETED
    days=5 hours=6 trips/day=10 total_weekly_earnings=500,000 commission(Yandex)=25
    receives_bonuses=False  cash_pct=50
    perceived_leader=Uklon

  Survey DRAFTY - Tashkent - DRAFT (not completed) - must not appear anywhere
"""

from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from telegram_bot.domain.enums import AttachmentFileType, SurveyStatus
from telegram_bot.infrastructure.db.models import (
    Attachment,
    Bonus,
    DriverExperience,
    Earnings,
    Payment,
    RideCategory,
    RideCategoryUsage,
    Survey,
    SurveyPlatform,
    WorkingStats,
)
from telegram_bot.web.app import app
from telegram_bot.web.deps import get_db

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def dataset(session, interviewer, city, platform_yandex, platform_uklon):
    from telegram_bot.infrastructure.db.models import City

    samarkand = (await session.execute(City.__table__.select().where(City.code == "samarkand"))).first()
    samarkand = await session.get(City, samarkand.id)
    economy = (await session.execute(RideCategory.__table__.select().where(RideCategory.code == "economy"))).first()

    one = Survey(
        human_code="TAS-000001", interviewer_id=interviewer.id, city_id=city.id,
        status=SurveyStatus.COMPLETED, survey_datetime=datetime(2026, 1, 10, tzinfo=timezone.utc),
        completed_at=datetime(2026, 1, 10, tzinfo=timezone.utc), total_weekly_earnings_amount=1_000_000,
        perceived_market_leader_platform_id=platform_yandex.id,
    )
    session.add(one)
    await session.flush()
    session.add(WorkingStats(survey_id=one.id, days_per_week=6, hours_per_day=8, trips_per_day=15, trips_per_week=90))
    sp1 = SurveyPlatform(survey_id=one.id, platform_id=platform_yandex.id, works_with_platform=True, is_exclusive=True)
    session.add(sp1)
    await session.flush()
    session.add(Earnings(survey_id=one.id, platform_id=platform_yandex.id, weekly_earnings_amount=1_000_000, commission_pct=20))
    session.add(Bonus(survey_id=one.id, platform_id=platform_yandex.id, receives_bonuses=True, required_trips=50, bonus_value_amount=50_000))
    session.add(Payment(survey_id=one.id, platform_id=platform_yandex.id, cash_pct=30, digital_pct=70))
    session.add(
        RideCategoryUsage(
            survey_id=one.id, platform_id=platform_yandex.id, category_id=economy.id,
            is_main_category=True, category_requirements="Car under 7 years",
        )
    )
    session.add(
        DriverExperience(
            survey_id=one.id, reason_to_join_new_platform="Better rates",
            improvement_suggestions="Faster payouts",
        )
    )
    session.add(
        Attachment(
            survey_id=one.id, platform_id=platform_yandex.id, sequence_number=1,
            file_type=AttachmentFileType.SCREENSHOT, telegram_file_id="file_1", telegram_file_unique_id="unique_1",
        )
    )

    two = Survey(
        human_code="SAM-000001", interviewer_id=interviewer.id, city_id=samarkand.id,
        status=SurveyStatus.COMPLETED, survey_datetime=datetime(2026, 2, 15, tzinfo=timezone.utc),
        completed_at=datetime(2026, 2, 15, tzinfo=timezone.utc), total_weekly_earnings_amount=500_000,
        perceived_market_leader_platform_id=platform_uklon.id,
    )
    session.add(two)
    await session.flush()
    session.add(WorkingStats(survey_id=two.id, days_per_week=5, hours_per_day=6, trips_per_day=10, trips_per_week=60))
    sp2y = SurveyPlatform(survey_id=two.id, platform_id=platform_yandex.id, works_with_platform=True)
    sp2u = SurveyPlatform(survey_id=two.id, platform_id=platform_uklon.id, works_with_platform=True)
    session.add_all([sp2y, sp2u])
    await session.flush()
    session.add(Earnings(survey_id=two.id, platform_id=platform_yandex.id, weekly_earnings_amount=500_000, commission_pct=25))
    session.add(Bonus(survey_id=two.id, platform_id=platform_yandex.id, receives_bonuses=False))
    session.add(Payment(survey_id=two.id, platform_id=platform_yandex.id, cash_pct=50, digital_pct=50))

    drafty = Survey(
        human_code="TAS-000002", interviewer_id=interviewer.id, city_id=city.id,
        status=SurveyStatus.DRAFT, survey_datetime=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )
    session.add(drafty)

    await session.flush()
    return {"one": one, "two": two, "drafty": drafty, "tashkent": city, "samarkand": samarkand}


@pytest.fixture
async def client(session):
    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_db, None)


# ---- reference data ---------------------------------------------------------


async def test_list_cities(client, dataset):
    resp = await client.get("/api/cities")
    assert resp.status_code == 200
    codes = {c["code"] for c in resp.json()}
    assert {"tashkent", "samarkand", "namangan", "andijan"} <= codes


async def test_list_platforms(client, dataset):
    resp = await client.get("/api/platforms")
    assert resp.status_code == 200
    codes = {p["code"] for p in resp.json()}
    assert {"yandex_go", "uklon"} <= codes


async def test_list_languages(client):
    resp = await client.get("/api/languages")
    assert resp.status_code == 200
    codes = {lang["code"] for lang in resp.json()}
    assert codes == {"en", "ru", "uz"}


async def test_list_driver_types(client):
    resp = await client.get("/api/driver-types")
    assert resp.status_code == 200
    codes = {d["code"] for d in resp.json()}
    assert {"independent", "fleet"} <= codes


async def test_overview_filtered_by_language(client, session, dataset):
    from telegram_bot.domain.enums import Language

    dataset["one"].language = Language.RU
    dataset["two"].language = Language.EN
    await session.flush()

    resp = await client.get("/api/overview", params={"language": "ru"})
    assert resp.status_code == 200
    assert resp.json()["completed_surveys"] == 1


# ---- OVERVIEW ------------------------------------------------------------------


async def test_overview_counts(client, dataset):
    resp = await client.get("/api/overview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_surveys"] == 3
    assert body["completed_surveys"] == 2
    assert body["surveys_by_city"]["counts"] == {"Tashkent": 1, "Samarkand": 1}
    assert body["surveys_by_platform"]["counts"] == {"Yandex Go": 2, "Uklon": 1}


async def test_overview_city_filter(client, dataset):
    resp = await client.get("/api/overview", params={"city_id": dataset["samarkand"].id})
    body = resp.json()
    assert body["completed_surveys"] == 1
    assert body["surveys_by_city"]["counts"] == {"Samarkand": 1}


async def test_overview_date_filter_excludes_out_of_range(client, dataset):
    resp = await client.get("/api/overview", params={"date_from": "2026-02-01"})
    body = resp.json()
    assert body["completed_surveys"] == 1  # only survey TWO (Feb 15)


# ---- DRIVER ECONOMICS -----------------------------------------------------------


async def test_driver_economics_shape_and_values(client, dataset):
    resp = await client.get("/api/driver-economics")
    assert resp.status_code == 200
    body = resp.json()
    for key in ("weekly_earnings", "trips_per_day", "hours_per_day", "earnings_per_hour", "earnings_per_trip", "commission_pct"):
        assert key in body
        for stat_key in ("count", "average", "median", "minimum", "maximum"):
            assert stat_key in body[key]
    assert body["weekly_earnings"]["count"] == 2
    assert body["weekly_earnings"]["average"] == pytest.approx(750_000)
    assert body["commission_pct"]["count"] == 2
    assert body["commission_pct"]["average"] == pytest.approx(22.5)


async def test_driver_economics_includes_driver_profile_fields(client, dataset):
    """The additive fields for the current questionnaire: structured hours
    bucket, structured + heuristic seasonal pattern, structured + heuristic
    driver type, and perceived market leader — all reused from the same
    /driver-economics endpoint rather than a new one."""
    resp = await client.get("/api/driver-economics")
    assert resp.status_code == 200
    body = resp.json()
    for key in (
        "hours_per_day_bucket", "seasonal_pattern", "seasonality_text_classification",
        "driver_type_distribution", "driver_type_employment_text", "perceived_market_leader",
    ):
        assert key in body
        assert "counts" in body[key] and "total" in body[key]
    # dataset fixture sets perceived_market_leader_platform_id on both surveys
    assert body["perceived_market_leader"]["counts"] == {"Yandex Go": 1, "Uklon": 1}


async def test_driver_economics_filtered_by_language(client, session, dataset):
    from telegram_bot.domain.enums import Language

    dataset["one"].language = Language.UZ
    await session.flush()

    resp = await client.get("/api/driver-economics", params={"language": "uz"})
    assert resp.status_code == 200
    assert resp.json()["weekly_earnings"]["count"] == 1


# ---- PLATFORM -------------------------------------------------------------------


async def test_platform_stats_shape(client, dataset):
    resp = await client.get("/api/platform-stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["platform_usage"]["counts"] == {"Yandex Go": 2, "Uklon": 1}
    assert body["multi_app"]["counts"] == {"one platform": 1, "multiple platforms": 1}
    assert body["exclusivity"]["counts"] == {"yes": 1}
    assert body["receives_bonuses"]["counts"] == {"yes": 1, "no": 1}
    assert body["cash_pct"]["count"] == 2


# ---- EXECUTIVE SUMMARY / QUESTIONNAIRE STATS (Q12/Q13) ---------------------------
#
# `dataset` builds its two surveys by hand (old-style rows, no
# survey_answer_options), so Q12/Q13 breakdowns are empty for it — these
# tests check the endpoint's shape and the fields `dataset` *does* cover
# (respondent count, most-used platform, screenshot share); a real
# driver_motivation/driver_type_loyalty value is exercised via a small
# extra survey, same pattern as test_qualitative_feeds_new_fields.


async def test_executive_summary_shape_and_values(client, dataset):
    resp = await client.get("/api/executive-summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_respondents"] == 2
    assert body["most_used_platform"] == "Yandex Go"  # used by both surveys
    assert body["multi_platform_pct"] == 50.0  # survey TWO only
    assert body["screenshot_share_pct"] == 50.0  # survey ONE only
    assert body["switch_willingness_pct"] == 0.0  # fixture sets no switch_frequency at all
    assert body["top_improvement_request"] is None  # fixture sets no driver_motivation answers


async def test_questionnaire_stats_empty_for_fixture_without_answer_options(client, dataset):
    resp = await client.get("/api/questionnaire-stats")
    assert resp.status_code == 200
    body = resp.json()
    for key in ("platform_usage", "platform_count_distribution", "driver_motivation", "employment_relationship", "loyalty_program"):
        assert key in body
    assert body["driver_motivation"]["counts"] == {}
    assert body["platform_usage"]["counts"] == {"Yandex Go": 2, "Uklon": 1}


async def test_questionnaire_stats_reflects_real_answer_options(client, session, interviewer, city, platform_yandex, dataset):
    from telegram_bot.infrastructure.db.models import SurveyAnswerOption

    s = Survey(
        human_code="Q12Q13-001", interviewer_id=interviewer.id, city_id=city.id,
        status=SurveyStatus.COMPLETED, survey_datetime=datetime(2026, 3, 1, tzinfo=timezone.utc),
        completed_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )
    session.add(s)
    await session.flush()
    session.add_all([
        SurveyAnswerOption(survey_id=s.id, question_code="driver_motivation", option_code="higher_earnings"),
        SurveyAnswerOption(survey_id=s.id, question_code="driver_type_loyalty", option_code="independent"),
    ])
    await session.flush()

    resp = await client.get("/api/questionnaire-stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["driver_motivation"]["counts"] == {"higher_earnings": 1}
    assert body["employment_relationship"]["counts"] == {"Independent": 1}


async def test_driver_type_filter_scopes_executive_summary(client, session, dataset):
    """StatisticsFilters.driver_type_id existed but DashboardFilters never
    passed it through — a real gap fixed alongside this redesign."""
    from telegram_bot.infrastructure.db.models import DriverEmployment, DriverType

    independent = (
        await session.execute(DriverType.__table__.select().where(DriverType.code == "independent"))
    ).first()
    session.add(DriverEmployment(survey_id=dataset["one"].id, driver_type_id=independent.id))
    await session.flush()

    resp = await client.get("/api/executive-summary", params={"driver_type_id": independent.id})
    assert resp.status_code == 200
    assert resp.json()["total_respondents"] == 1  # only survey ONE has this driver_type


# ---- SCREENSHOTS GALLERY ----------------------------------------------------------


async def test_screenshots_gallery_shape_and_summary(client, dataset):
    resp = await client.get("/api/screenshots")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1  # only survey ONE's screenshot
    assert body["summary"]["submitted"] == 1
    assert body["summary"]["total_respondents"] == 2
    assert body["summary"]["pct"] == 50.0
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["survey_id"] == "TAS-000001"
    assert item["city"] == "Tashkent"
    assert item["platform"] == "Yandex Go"
    assert item["cached_locally"] is False  # dataset never sets local_path
    assert body["by_platform"]["counts"] == {"Yandex Go": 1}
    assert body["by_city"]["counts"] == {"Tashkent": 1}


async def test_screenshots_gallery_pagination(client, dataset):
    resp = await client.get("/api/screenshots", params={"page": 0, "page_size": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert body["page"] == 0
    assert body["page_size"] == 1
    assert body["total"] == 1
    assert len(body["items"]) == 1

    resp_page1 = await client.get("/api/screenshots", params={"page": 1, "page_size": 1})
    assert resp_page1.json()["items"] == []


async def test_screenshots_gallery_city_filter(client, dataset):
    resp = await client.get("/api/screenshots", params={"city_id": dataset["samarkand"].id})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0  # survey TWO (Samarkand) has no screenshots
    assert body["summary"]["submitted"] == 0


# ---- QUALITATIVE -----------------------------------------------------------------


async def test_qualitative_feeds(client, dataset):
    resp = await client.get("/api/qualitative")
    assert resp.status_code == 200
    body = resp.json()
    leader_platforms = {r["text"] for r in body["market_leader_responses"]}
    assert leader_platforms == {"Yandex Go", "Uklon"}
    reasons = {r["text"] for r in body["reasons_to_join_new_platform"]}
    assert reasons == {"Better rates"}
    improvements = {r["text"] for r in body["improvement_suggestions"]}
    assert improvements == {"Faster payouts"}
    requirements = {r["text"] for r in body["category_requirements"]}
    assert requirements == {"Car under 7 years"}


async def test_qualitative_feeds_new_fields(client, session, interviewer, city, platform_yandex):
    """employment_responses / bonus_descriptions / payout_notes — the
    qualitative feeds added for the current questionnaire's open-text
    answers, reusing the existing QualitativeResponse shape and dashboard
    presentation pattern (see renderQualList in app.js)."""
    from datetime import datetime, timezone

    from telegram_bot.domain.enums import SurveyStatus
    from telegram_bot.infrastructure.db.models import (
        Bonus,
        DriverEmployment,
        Payment,
        Survey,
        SurveyPlatform,
    )

    s = Survey(
        human_code="QUAL-000001", interviewer_id=interviewer.id, city_id=city.id,
        status=SurveyStatus.COMPLETED, survey_datetime=datetime(2026, 2, 1, tzinfo=timezone.utc),
        completed_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    session.add(s)
    await session.flush()
    session.add(DriverEmployment(survey_id=s.id, employment_text="Independent, my own car"))
    sp = SurveyPlatform(survey_id=s.id, platform_id=platform_yandex.id, works_with_platform=True)
    session.add(sp)
    await session.flush()
    session.add(Bonus(survey_id=s.id, platform_id=platform_yandex.id, description="50000 UZS per 50 trips"))
    session.add(Payment(survey_id=s.id, platform_id=platform_yandex.id, payout_notes="Weekly bank transfer"))
    await session.flush()

    resp = await client.get("/api/qualitative")
    assert resp.status_code == 200
    body = resp.json()
    assert {r["text"] for r in body["employment_responses"]} >= {"Independent, my own car"}
    assert {r["text"] for r in body["bonus_descriptions"]} >= {"50000 UZS per 50 trips"}
    assert {r["text"] for r in body["payout_notes"]} >= {"Weekly bank transfer"}


# ---- RAW DATA ------------------------------------------------------------------


async def test_list_surveys_pagination_and_excludes_drafts(client, dataset):
    resp = await client.get("/api/surveys", params={"limit": 1, "offset": 0})
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 1

    resp_all = await client.get("/api/surveys", params={"limit": 50})
    ids = {item["survey_id"] for item in resp_all.json()["items"]}
    assert ids == {"TAS-000001", "SAM-000001"}
    assert "TAS-000002" not in ids  # draft excluded


async def test_survey_detail_for_completed_survey(client, dataset):
    resp = await client.get("/api/surveys/TAS-000001")
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["survey_id"] == "TAS-000001"
    assert body["summary"]["city"] == "Tashkent"
    assert len(body["earnings"]) == 1
    assert body["earnings"][0]["commission_pct"] == 20
    assert len(body["screenshots"]) == 1


async def test_survey_detail_404_for_draft_or_unknown(client, dataset):
    resp = await client.get("/api/surveys/TAS-000002")  # exists but is a DRAFT
    assert resp.status_code == 404

    resp2 = await client.get("/api/surveys/NOT-REAL")
    assert resp2.status_code == 404


async def test_survey_screenshots_endpoint(client, dataset):
    resp = await client.get("/api/surveys/TAS-000001/screenshots")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["telegram_file_id"] == "file_1"


async def test_screenshot_image_not_cached_returns_404_with_explanation(client, dataset, session):
    attachment = (
        await session.execute(Attachment.__table__.select().where(Attachment.telegram_file_id == "file_1"))
    ).first()
    resp = await client.get(f"/api/screenshots/{attachment.id}/image")
    assert resp.status_code == 404
    assert "file_1" in resp.json()["detail"]


# ---- EXPORT -----------------------------------------------------------------------


async def test_export_excel_returns_valid_xlsx(client, dataset):
    resp = await client.get("/api/export/excel")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert resp.content[:2] == b"PK"


async def test_export_csv_returns_matching_data(client, dataset):
    resp = await client.get("/api/export/csv/earnings")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    lines = resp.text.strip().splitlines()
    assert lines[0].startswith("survey_id,city,survey_date,platform")
    assert any("TAS-000001" in line for line in lines)


async def test_export_csv_unknown_sheet_returns_404(client, dataset):
    resp = await client.get("/api/export/csv/not_a_sheet")
    assert resp.status_code == 404


async def test_export_excel_include_incomplete(client, dataset):
    resp_default = await client.get("/api/export/excel")
    resp_all = await client.get("/api/export/excel", params={"include_incomplete": "true"})
    # both succeed; the include_incomplete workbook should be a different
    # (larger) payload since it has an extra row in Survey Summary.
    assert resp_default.status_code == 200
    assert resp_all.status_code == 200
