import pytest
from sqlalchemy import select

from telegram_bot.application.question_engine import ValidationError
from telegram_bot.application.survey_session import CursorState, SurveySession, get_or_create_interviewer
from telegram_bot.domain.enums import HoursPerDayBucket, SeasonalPattern, SurveyStatus
from telegram_bot.domain.questionnaire import QUESTION_COUNT, QUESTIONS_BY_DISPLAY_NUMBER, QuestionType
from telegram_bot.infrastructure.db.models import Bonus, Earnings, Survey, SurveyAnswerOption

pytestmark = pytest.mark.asyncio

_HOUR_CODES = {b.value for b in HoursPerDayBucket}
_SEASON_CODES = {p.value for p in SeasonalPattern}

EXPECTED_CODES_IN_ORDER = [QUESTIONS_BY_DISPLAY_NUMBER[n].code for n in range(1, QUESTION_COUNT + 1)]


async def new_session(db, interviewer) -> SurveySession:
    return await SurveySession.resume(db, CursorState.initial(), interviewer)


async def full_walkthrough(session: SurveySession, force: dict | None = None) -> None:
    """Drive the session to completion (stops at the review section),
    picking the first available/valid option(s) for every question unless
    overridden via `force`. Q5 (hours_and_season) and Q9 (earnings) each
    require exactly two selections of different kinds, so they get their
    own default-picking logic rather than "just take options[0]"."""
    force = force or {}
    guard = 0
    while not session.is_review():
        guard += 1
        assert guard < 100, "walkthrough did not terminate"
        question = session.current_question()
        assert question is not None

        if question.code in force:
            value = force[question.code]
        elif question.code == "has_screenshots":
            value = False  # generic walkthroughs skip the upload loop by default
        elif question.code == "hours_and_season":
            options = await session.resolve_options(question)
            hour = next(o.value for o in options if o.value in _HOUR_CODES)
            season = next(o.value for o in options if o.value in _SEASON_CODES)
            value = [hour, season]
        elif question.code == "earnings":
            options = await session.resolve_options(question)
            range_code = next(o.value for o in options if not o.value.isdigit())
            basis_code = next(o.value for o in options if o.value.isdigit())
            value = [range_code, basis_code]
        elif question.qtype == QuestionType.SINGLE_CHOICE:
            options = await session.resolve_options(question)
            value = options[0].value
        elif question.qtype == QuestionType.MULTI_CHOICE:
            options = await session.resolve_options(question)
            value = [options[0].value]
        else:
            raise AssertionError(f"unhandled question type: {question.qtype}")

        if value is None:
            await session.skip(question)
        else:
            await session.answer(question, value)
        if question.code == "has_screenshots" and value is True:
            await session.finish_screenshot_upload()


async def test_survey_created_on_first_answer_with_generated_code(session, interviewer, city):
    s = await new_session(session, interviewer)
    q = s.current_question()
    assert q.code == "city"
    await s.answer(q, str(city.id))

    assert s.survey is not None
    assert s.survey.city_id == city.id
    assert s.survey.human_code.startswith(city.code[:3].upper())
    assert s.survey.status == SurveyStatus.DRAFT
    assert s.cursor.survey_id == str(s.survey.id)


async def test_setup_then_fifteen_questions_appear_in_fixed_order(session, interviewer, city, platform_yandex):
    """The core redesign requirement: no matter what's answered, the
    interviewer walks through exactly Q1..Q15 in the same order, with no
    conditional branch and no per-platform repeat."""
    s = await new_session(session, interviewer)
    await s.answer(s.current_question(), str(city.id))

    seen = []
    for _ in range(QUESTION_COUNT):
        q = s.current_question()
        seen.append(q.code)
        if q.code == "platforms_used":
            await s.answer(q, [str(platform_yandex.id)])
        elif q.code == "hours_and_season":
            await s.answer(q, ["7_8", "more_in_summer"])
        elif q.code == "earnings":
            options = await s.resolve_options(q)
            basis = next(o.value for o in options if o.value.isdigit())
            await s.answer(q, ["500k_1m", basis])
        elif q.qtype == QuestionType.SINGLE_CHOICE:
            options = await s.resolve_options(q)
            await s.answer(q, options[0].value)
        elif q.qtype == QuestionType.MULTI_CHOICE:
            options = await s.resolve_options(q)
            await s.answer(q, [options[0].value])
    assert seen == EXPECTED_CODES_IN_ORDER

    q = s.current_question()
    assert q.code == "has_screenshots"


async def test_full_survey_two_platforms_persists_everything(session, interviewer, city, platform_yandex, platform_uklon):
    s = await new_session(session, interviewer)
    await full_walkthrough(
        s,
        force={
            "platforms_used": [str(platform_yandex.id), str(platform_uklon.id)],
            "bonus_type": "trip_based",
        },
    )
    assert s.is_review()
    await s.finish()

    reloaded = await session.get(Survey, s.survey.id)
    assert reloaded.status == SurveyStatus.COMPLETED
    assert reloaded.completed_at is not None

    earnings_rows = (
        await session.execute(select(Earnings).where(Earnings.survey_id == s.survey.id))
    ).scalars().all()
    assert len(earnings_rows) == 2  # one per platform, broadcast from the single Q9 answer
    assert all(e.earnings_basis_id is not None for e in earnings_rows)

    bonus_rows = (await session.execute(select(Bonus).where(Bonus.survey_id == s.survey.id))).scalars().all()
    assert len(bonus_rows) == 2
    assert all(b.receives_bonuses is True for b in bonus_rows)

    answer_option_rows = (
        await session.execute(select(SurveyAnswerOption).where(SurveyAnswerOption.survey_id == s.survey.id))
    ).scalars().all()
    codes = {row.question_code for row in answer_option_rows}
    assert "market_awareness" in codes
    assert "driver_motivation" in codes


async def test_platforms_used_is_the_only_place_platforms_are_selected(session, interviewer, city, platform_yandex, platform_uklon):
    """No per-platform loop exists any more: switch_frequency (Q2) and
    best_experience (Q3) come right after Q1, unconditionally, regardless
    of how many platforms were selected."""
    s = await new_session(session, interviewer)
    await s.answer(s.current_question(), str(city.id))
    await s.answer(s.current_question(), [str(platform_yandex.id), str(platform_uklon.id)])

    q = s.current_question()
    assert q.code == "switch_frequency"
    await s.answer(q, (await s.resolve_options(q))[0].value)

    q2 = s.current_question()
    assert q2.code == "best_experience"


async def test_bonus_type_none_records_no_bonuses(session, interviewer, city, platform_yandex):
    s = await new_session(session, interviewer)
    await full_walkthrough(s, force={"platforms_used": [str(platform_yandex.id)], "bonus_type": "none"})
    bonus = (await session.execute(select(Bonus).where(Bonus.survey_id == s.survey.id))).scalar_one()
    assert bonus.receives_bonuses is False


async def test_bonus_type_dont_know_leaves_receives_bonuses_null(session, interviewer, city, platform_yandex):
    s = await new_session(session, interviewer)
    await full_walkthrough(s, force={"platforms_used": [str(platform_yandex.id)], "bonus_type": "dont_know"})
    bonus = (await session.execute(select(Bonus).where(Bonus.survey_id == s.survey.id))).scalar_one()
    assert bonus.receives_bonuses is None


async def test_hours_and_season_requires_exactly_one_of_each(session, interviewer, city, platform_yandex):
    s = await new_session(session, interviewer)
    await s.answer(s.current_question(), str(city.id))
    await s.answer(s.current_question(), [str(platform_yandex.id)])
    switch_q = s.current_question()
    await s.answer(switch_q, (await s.resolve_options(switch_q))[0].value)
    best_q = s.current_question()
    await s.answer(best_q, (await s.resolve_options(best_q))[0].value)
    days_q = s.current_question()
    await s.answer(days_q, (await s.resolve_options(days_q))[0].value)

    q = s.current_question()
    assert q.code == "hours_and_season"
    position_before = s.cursor.position

    with pytest.raises(ValidationError):
        await s.answer(q, ["7_8"])  # missing a seasonal selection
    assert s.cursor.position == position_before

    with pytest.raises(ValidationError):
        await s.answer(q, ["7_8", "9_10", "more_in_summer"])  # two hour buckets
    assert s.cursor.position == position_before


async def test_hours_per_day_bucket_stores_structured_value_and_numeric_midpoint(session, interviewer, city, platform_yandex):
    s = await new_session(session, interviewer)
    await s.answer(s.current_question(), str(city.id))
    await s.answer(s.current_question(), [str(platform_yandex.id)])
    switch_q = s.current_question()
    await s.answer(switch_q, (await s.resolve_options(switch_q))[0].value)
    best_q = s.current_question()
    await s.answer(best_q, (await s.resolve_options(best_q))[0].value)
    days_q = s.current_question()
    await s.answer(days_q, (await s.resolve_options(days_q))[0].value)

    q = s.current_question()
    assert q.code == "hours_and_season"
    await s.answer(q, ["7_8", "same_year_round"])

    assert s.working_stats.hours_per_day_bucket == HoursPerDayBucket.H7_8
    assert float(s.working_stats.hours_per_day) == 7.5
    assert s.driver_experience.seasonal_pattern == SeasonalPattern.SAME_YEAR_ROUND


async def test_earnings_invalid_combination_raises_and_does_not_advance(session, interviewer, city, platform_yandex):
    s = await new_session(session, interviewer)
    await s.answer(s.current_question(), str(city.id))
    await s.answer(s.current_question(), [str(platform_yandex.id)])
    for _ in range(5):  # switch_frequency, best_experience, days_per_week, hours_and_season, main_category
        q = s.current_question()
        if q.code == "hours_and_season":
            await s.answer(q, ["7_8", "same_year_round"])
        else:
            await s.answer(q, (await s.resolve_options(q))[0].value)
    trips_q = s.current_question()
    assert trips_q.code == "trips_per_day_range"
    await s.answer(trips_q, (await s.resolve_options(trips_q))[0].value)
    commission_q = s.current_question()
    assert commission_q.code == "commission_range"
    await s.answer(commission_q, (await s.resolve_options(commission_q))[0].value)

    q = s.current_question()
    assert q.code == "earnings"
    position_before = s.cursor.position
    with pytest.raises(ValidationError):
        await s.answer(q, ["500k_1m"])  # missing a basis selection
    assert s.cursor.position == position_before

    # Q8 (commission_range) already created the platform's Earnings row
    # (to store commission_pct); the invalid Q9 answer must not have
    # touched its earnings amount/basis.
    earnings_rows = (
        await session.execute(select(Earnings).where(Earnings.survey_id == s.survey.id))
    ).scalars().all()
    assert len(earnings_rows) == 1
    assert earnings_rows[0].weekly_earnings_amount is None
    assert earnings_rows[0].earnings_basis_id is None


async def test_market_awareness_requires_at_least_one_selection(session, interviewer, city, platform_yandex):
    s = await new_session(session, interviewer)
    await s.answer(s.current_question(), str(city.id))
    await s.answer(s.current_question(), [str(platform_yandex.id)])

    while s.current_question().code != "market_awareness":
        q = s.current_question()
        if q.code == "hours_and_season":
            await s.answer(q, ["7_8", "same_year_round"])
        elif q.code == "earnings":
            options = await s.resolve_options(q)
            basis = next(o.value for o in options if o.value.isdigit())
            await s.answer(q, ["500k_1m", basis])
        else:
            await s.answer(q, (await s.resolve_options(q))[0].value)

    q = s.current_question()
    position_before = s.cursor.position
    with pytest.raises(ValidationError):
        await s.answer(q, [])
    assert s.cursor.position == position_before


async def test_back_does_not_lose_previously_entered_answers(session, interviewer, city, platform_yandex):
    s = await new_session(session, interviewer)
    await s.answer(s.current_question(), str(city.id))
    await s.answer(s.current_question(), [str(platform_yandex.id)])  # platforms_used (Q1)

    switch_q = s.current_question()
    assert switch_q.code == "switch_frequency"
    switch_value = (await s.resolve_options(switch_q))[0].value
    await s.answer(switch_q, switch_value)
    assert s.survey_platforms[0].switch_frequency_id == int(switch_value)

    q = s.current_question()
    assert q.code == "best_experience"

    moved = s.go_back()
    assert moved is True
    back_q = s.current_question()
    assert back_q.code == "switch_frequency"

    # the previously given answer is still in the database, untouched by
    # simply moving backward
    assert s.survey_platforms[0].switch_frequency_id == int(switch_value)


async def test_go_back_at_first_question_returns_false(session, interviewer):
    s = await new_session(session, interviewer)
    assert s.go_back() is False


async def test_restart_abandons_current_survey_and_starts_fresh(session, interviewer, city):
    s = await new_session(session, interviewer)
    await s.answer(s.current_question(), str(city.id))
    old_survey_id = s.survey.id

    fresh = await s.restart()

    old = await session.get(Survey, old_survey_id)
    assert old.status == SurveyStatus.ABANDONED
    assert fresh.survey is None
    assert fresh.cursor.survey_id is None
    assert fresh.current_question().code == "city"


async def test_default_walkthrough_uploads_no_screenshots(session, interviewer, city, platform_yandex):
    """See test_screenshot_workflow.py for the dedicated upload-loop tests
    (one/multiple/no screenshots, interrupted upload, wrong file type)."""
    s = await new_session(session, interviewer)
    await full_walkthrough(s, force={"platforms_used": [str(platform_yandex.id)]})

    from telegram_bot.infrastructure.db.models import Attachment

    attachments = (
        await session.execute(select(Attachment).where(Attachment.survey_id == s.survey.id))
    ).scalars().all()
    assert attachments == []
    assert s.survey.screenshots_offered is False


async def test_reaching_review_does_not_auto_submit(session, interviewer, city, platform_yandex):
    """The questionnaire's review/confirmation screen requirement: walking
    off the end of the questionnaire must NOT finish the survey by
    itself — only confirm() does that."""
    s = await new_session(session, interviewer)
    await full_walkthrough(s, force={"platforms_used": [str(platform_yandex.id)]})
    assert s.is_review()
    assert s.survey.status == SurveyStatus.DRAFT
    assert s.survey.completed_at is None


async def test_confirm_finishes_the_survey(session, interviewer, city, platform_yandex):
    s = await new_session(session, interviewer)
    await full_walkthrough(s, force={"platforms_used": [str(platform_yandex.id)]})
    await s.confirm()
    assert s.survey.status == SurveyStatus.COMPLETED
    assert s.survey.completed_at is not None


async def test_confirm_is_idempotent_like_finish(session, interviewer, city, platform_yandex):
    s = await new_session(session, interviewer)
    await full_walkthrough(s, force={"platforms_used": [str(platform_yandex.id)]})
    await s.confirm()
    first_completed_at = s.survey.completed_at
    await s.confirm()
    assert s.survey.completed_at == first_completed_at


async def test_go_back_from_review_returns_to_last_question(session, interviewer, city, platform_yandex):
    s = await new_session(session, interviewer)
    await full_walkthrough(s, force={"platforms_used": [str(platform_yandex.id)]})
    assert s.is_review()
    moved = s.go_back()
    assert moved is True
    assert not s.is_review()
    assert s.current_question().code == "has_screenshots"


async def test_language_defaults_to_english(session, interviewer, city):
    s = await new_session(session, interviewer)
    assert s.language == "en"
    await s.answer(s.current_question(), str(city.id))
    assert s.survey.language.value == "en"


async def test_set_language_before_survey_created_persists_once_created(session, interviewer, city):
    s = await new_session(session, interviewer)
    s.set_language("ru")
    assert s.language == "ru"
    await s.answer(s.current_question(), str(city.id))  # creates the Survey row
    assert s.survey.language.value == "ru"
    assert s.language == "ru"


async def test_language_persists_across_resume(session, interviewer, city):
    s = await new_session(session, interviewer)
    s.set_language("uz")
    await s.answer(s.current_question(), str(city.id))
    survey_id = s.survey.id

    resumed = await SurveySession.resume(session, CursorState(survey_id=str(survey_id), section_index=1, platform_pos=None, question_index=1), interviewer)
    assert resumed.language == "uz"


async def test_get_or_create_interviewer_is_idempotent(session):
    a = await get_or_create_interviewer(session, 999999, "Alice")
    b = await get_or_create_interviewer(session, 999999, "Alice Again")
    assert a.id == b.id
    assert b.full_name == "Alice"  # not overwritten on second call
