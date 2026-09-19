"""Tests for the review/confirmation submission flow: walking off the end
of the questionnaire shows a structured review screen (not an automatic
submission), the survey only becomes COMPLETED when the interviewer taps
Confirm/Submit, Edit/Back returns to the questionnaire for corrections,
and duplicate-submission handling stays idempotent either way.
"""

import re
from datetime import datetime, timezone

import pytest
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from sqlalchemy import select

from telegram_bot.application.survey_session import CursorState, SurveySession, get_or_create_interviewer
from telegram_bot.domain.enums import SurveyStatus
from telegram_bot.domain.questionnaire import SCREENSHOTS_SECTION_INDEX, QuestionType
from telegram_bot.infrastructure.db.models import Survey
from telegram_bot.presentation.states import SECTION_TO_STATE

SCREENSHOTS_STATE = SECTION_TO_STATE[SCREENSHOTS_SECTION_INDEX]

pytestmark = pytest.mark.asyncio


async def new_session(db, interviewer) -> SurveySession:
    return await SurveySession.resume(db, CursorState.initial(), interviewer)


async def advance_to_has_screenshots(s: SurveySession, *, city, platform_ids: list[str]) -> None:
    """Drive a fresh session up to (and stop at) has_screenshots — the
    last question before the review screen (whether answered Yes, leading
    into the upload loop, or No, going straight to review)."""
    for _ in range(300):
        q = s.current_question()
        if q is None or q.code == "has_screenshots":
            return
        if q.code == "city":
            await s.answer(q, str(city.id))
        elif q.code == "platforms_used":
            await s.answer(q, platform_ids)
        elif q.code == "hours_and_season":
            await s.answer(q, ["7_8", "same_year_round"])
        elif q.code == "earnings":
            options = await s.resolve_options(q)
            basis = next(o.value for o in options if o.value.isdigit())
            await s.answer(q, ["500k_1m", basis])
        elif not q.required:
            await s.skip(q)
        elif q.qtype == QuestionType.SINGLE_CHOICE:
            options = await s.resolve_options(q)
            await s.answer(q, options[0].value)
        elif q.qtype == QuestionType.MULTI_CHOICE:
            options = await s.resolve_options(q)
            await s.answer(q, [options[0].value])
        elif q.qtype == QuestionType.YES_NO:
            await s.answer(q, False)
        else:
            await s.answer(q, "1")
    raise AssertionError("did not reach has_screenshots")


# ---- 1-6: DRAFT -> (review, still DRAFT) -> COMPLETED lifecycle -----------


async def test_new_survey_starts_as_draft(session, interviewer, city):
    s = await new_session(session, interviewer)
    await s.answer(s.current_question(), str(city.id))
    assert s.survey.status == SurveyStatus.DRAFT


async def test_reaching_end_of_questionnaire_shows_review_not_completion(
    session, interviewer, city, platform_yandex
):
    """Walking off the end of the questionnaire must NOT submit on its
    own — only confirm() does (see the review/confirmation requirement)."""
    s = await new_session(session, interviewer)
    await advance_to_has_screenshots(s, city=city, platform_ids=[str(platform_yandex.id)])
    await s.answer(s.current_question(), False)  # has_screenshots -> final step

    assert s.is_review()
    assert s.survey.status == SurveyStatus.DRAFT
    assert s.survey.completed_at is None


async def test_confirm_completes_the_survey(session, interviewer, city, platform_yandex):
    s = await new_session(session, interviewer)
    await advance_to_has_screenshots(s, city=city, platform_ids=[str(platform_yandex.id)])
    await s.answer(s.current_question(), False)
    assert s.survey.status == SurveyStatus.DRAFT

    await s.confirm()
    assert s.survey.status == SurveyStatus.COMPLETED


async def test_final_step_via_screenshot_upload_path_also_only_completes_on_confirm(
    session, interviewer, city, platform_yandex
):
    s = await new_session(session, interviewer)
    await advance_to_has_screenshots(s, city=city, platform_ids=[str(platform_yandex.id)])
    await s.answer(s.current_question(), True)
    assert s.survey.status == SurveyStatus.DRAFT  # not yet — still uploading

    await s.finish_screenshot_upload()  # reaches review
    assert s.is_review()
    assert s.survey.status == SurveyStatus.DRAFT  # still not submitted

    await s.confirm()
    assert s.survey.status == SurveyStatus.COMPLETED


async def test_completed_at_is_stored_only_after_confirm(session, interviewer, city, platform_yandex):
    s = await new_session(session, interviewer)
    await advance_to_has_screenshots(s, city=city, platform_ids=[str(platform_yandex.id)])
    await s.answer(s.current_question(), False)
    assert s.survey.completed_at is None

    before = datetime.now(timezone.utc)
    await s.confirm()
    after = datetime.now(timezone.utc)

    assert s.survey.completed_at is not None
    assert before <= s.survey.completed_at <= after


async def test_unique_human_readable_survey_id_is_generated(session, interviewer, city, platform_yandex):
    """human_code is assigned the moment the Survey row is created (on the
    city answer), independent of confirmation."""
    s = await new_session(session, interviewer)
    await advance_to_has_screenshots(s, city=city, platform_ids=[str(platform_yandex.id)])

    assert re.fullmatch(r"[A-Z]{3}-\d{6}", s.survey.human_code)

    s2 = await new_session(session, interviewer)
    await advance_to_has_screenshots(s2, city=city, platform_ids=[str(platform_yandex.id)])

    assert s2.survey.human_code != s.survey.human_code
    assert s.survey.human_code.startswith(city.code[:3].upper())


# ---- 7-10: what the driver actually sees -----------------------------------


async def test_review_screen_is_shown_before_completion(dp, bot, session, city, platform_yandex):
    user = User(id=20001, is_bot=False, first_name="Driver")
    survey = await _seed_session_at_has_screenshots(dp, bot, session, user, city, platform_yandex)

    await _send_callback(dp, bot, user, "ans:false")

    text = _last_text(bot)
    assert survey.human_code in text  # the review screen's own "SURVEY #..." heading
    assert "completed successfully" not in text.lower()  # not the completion message yet

    from telegram_bot.domain.enums import SurveyStatus

    await session.refresh(survey)
    assert survey.status == SurveyStatus.DRAFT


async def test_review_screen_offers_confirm_and_edit_buttons(dp, bot, session, city, platform_yandex):
    user = User(id=20002, is_bot=False, first_name="Driver")
    await _seed_session_at_has_screenshots(dp, bot, session, user, city, platform_yandex)

    await _send_callback(dp, bot, user, "ans:false")

    calls = [c for c in bot.session.calls if type(c).__name__ == "SendMessage"]
    keyboard = calls[-1].reply_markup
    callback_data = {btn.callback_data for row in keyboard.inline_keyboard for btn in row}
    assert callback_data == {"review_confirm", "review_edit"}


async def test_completion_message_appears_only_after_confirm(dp, bot, session, city, platform_yandex):
    user = User(id=20003, is_bot=False, first_name="Driver")
    survey = await _seed_session_at_has_screenshots(dp, bot, session, user, city, platform_yandex)

    await _send_callback(dp, bot, user, "ans:false")
    assert "completed successfully" not in _last_text(bot).lower()

    await _send_callback(dp, bot, user, "review_confirm")
    text = _last_text(bot)
    assert "completed successfully" in text.lower()
    assert survey.human_code in text
    assert "Survey ID" in text


async def test_edit_button_returns_to_last_question_not_completion(dp, bot, session, city, platform_yandex):
    user = User(id=20004, is_bot=False, first_name="Driver")
    await _seed_session_at_has_screenshots(dp, bot, session, user, city, platform_yandex)

    await _send_callback(dp, bot, user, "ans:false")  # -> review
    await _send_callback(dp, bot, user, "review_edit")

    text = _last_text(bot)
    assert "completed successfully" not in text.lower()
    assert "screenshots" in text.lower()  # back on has_screenshots, the last real question


# ---- 11-12: idempotency and immutability ------------------------------------


async def test_duplicate_confirm_does_not_create_another_survey_or_change_completed_at(
    session, interviewer, city, platform_yandex
):
    s = await new_session(session, interviewer)
    await advance_to_has_screenshots(s, city=city, platform_ids=[str(platform_yandex.id)])
    await s.answer(s.current_question(), False)
    await s.confirm()
    survey_id = s.survey.id
    first_completed_at = s.survey.completed_at
    first_human_code = s.survey.human_code

    # Simulate a retried/duplicate confirm trigger, both on the same
    # session object and on one resumed fresh from the same cursor.
    await s.confirm()
    resumed = await SurveySession.resume(session, s.cursor, interviewer)
    await resumed.confirm()

    count = (
        await session.execute(select(Survey).where(Survey.interviewer_id == interviewer.id))
    ).scalars().all()
    assert len(count) == 1
    assert count[0].id == survey_id
    assert count[0].completed_at == first_completed_at
    assert count[0].human_code == first_human_code


async def test_duplicate_confirm_tap_via_handler_shows_existing_survey_id(dp, bot, session, city, platform_yandex):
    user = User(id=20005, is_bot=False, first_name="Driver")
    survey = await _seed_session_at_has_screenshots(dp, bot, session, user, city, platform_yandex)

    await _send_callback(dp, bot, user, "ans:false")  # -> review
    await _send_callback(dp, bot, user, "review_confirm")
    first_text = _last_text(bot)

    # A duplicate tap of the same (now stale) Confirm button — e.g. a
    # Telegram retry — must show the same result, not error or resubmit.
    await _send_callback(dp, bot, user, "review_confirm")
    second_text = _last_text(bot)

    assert survey.human_code in first_text
    assert survey.human_code in second_text

    surveys = (
        await session.execute(select(Survey).where(Survey.interviewer.has(telegram_user_id=user.id)))
    ).scalars().all()
    assert len(surveys) == 1


async def test_completed_survey_remains_unchanged_after_starting_a_new_one(
    session, interviewer, city, platform_yandex
):
    s = await new_session(session, interviewer)
    await advance_to_has_screenshots(s, city=city, platform_ids=[str(platform_yandex.id)])
    await s.answer(s.current_question(), False)
    await s.confirm()
    old_id, old_code, old_completed_at, old_status = (
        s.survey.id,
        s.survey.human_code,
        s.survey.completed_at,
        s.survey.status,
    )

    fresh = await s.restart()
    await fresh.answer(fresh.current_question(), str(city.id))  # start filling in the new survey

    old = await session.get(Survey, old_id)
    assert old.id == old_id
    assert old.human_code == old_code
    assert old.completed_at == old_completed_at
    assert old.status == old_status == SurveyStatus.COMPLETED


# ---- 13-14: Start New Survey -------------------------------------------------


async def test_start_new_survey_creates_a_new_draft(session, interviewer, city, platform_yandex):
    s = await new_session(session, interviewer)
    await advance_to_has_screenshots(s, city=city, platform_ids=[str(platform_yandex.id)])
    await s.answer(s.current_question(), False)
    await s.confirm()
    old_survey_id = s.survey.id

    fresh = await s.restart()
    assert fresh.survey is None  # no survey row yet — created on first answer, as usual

    await fresh.answer(fresh.current_question(), str(city.id))
    assert fresh.survey is not None
    assert fresh.survey.id != old_survey_id
    assert fresh.survey.status == SurveyStatus.DRAFT


async def test_new_survey_starts_from_the_first_question_and_allows_reselecting_language(
    session, interviewer, city, platform_yandex
):
    s = await new_session(session, interviewer)
    s.set_language("ru")
    await advance_to_has_screenshots(s, city=city, platform_ids=[str(platform_yandex.id)])
    await s.answer(s.current_question(), False)
    await s.confirm()

    fresh = await s.restart()
    assert fresh.current_question().code == "city"
    # restart() clears the cursor's language back to unset, so the
    # presentation layer shows the language picker again for the new survey
    assert fresh.cursor.language is None


async def test_start_new_survey_via_handler_shows_language_picker_then_city(
    dp, bot, session, city, platform_yandex
):
    user = User(id=20006, is_bot=False, first_name="Driver")
    await _seed_session_at_has_screenshots(dp, bot, session, user, city, platform_yandex)
    await _send_callback(dp, bot, user, "ans:false")
    await _send_callback(dp, bot, user, "review_confirm")

    await _send_callback(dp, bot, user, "new_survey")
    text = _last_text(bot)
    assert "language" in text.lower()

    await _send_callback(dp, bot, user, "lang:en")
    text2 = _last_text(bot)
    assert "city" in text2.lower()


# ---- 15-16: nothing else broke -----------------------------------------------
#
# "still works" is verified by the rest of the suite (test_survey_flow.py,
# test_metric_observations.py, test_statistics_service.py,
# test_export_service.py, test_web_api.py, etc.) continuing to pass against
# the same schema and query patterns a stats/export/dashboard feature would
# use; see the full `pytest` run in the final report.


# ---- shared handler-test helpers --------------------------------------------


async def _seed_session_at_has_screenshots(dp, bot, db, user: User, city, platform_yandex) -> Survey:
    """Drive SurveySession directly to has_screenshots (fast, not fragile
    against questionnaire changes — see test_screenshot_workflow.py for the
    rationale), then seed the dispatcher's own FSM storage so the next real
    update is handled by the actual aiogram handlers.
    """
    interviewer = await get_or_create_interviewer(db, user.id, user.first_name)
    s = await new_session(db, interviewer)
    s.set_language("en")
    await advance_to_has_screenshots(s, city=city, platform_ids=[str(platform_yandex.id)])
    await db.flush()

    key = StorageKey(bot_id=bot.id, chat_id=user.id, user_id=user.id)
    await dp.storage.set_data(key, {"cursor": s.cursor.to_dict()})
    await dp.storage.set_state(key, SCREENSHOTS_STATE)

    return await db.get(Survey, s.survey.id)


_uid = [20000]


async def _send_callback(dp, bot, user: User, data: str) -> None:
    _uid[0] += 1
    msg = Message(message_id=_uid[0], date=datetime.now(timezone.utc), chat=Chat(id=user.id, type="private"), from_user=user, text="prompt")
    cb = CallbackQuery(id=str(_uid[0]), from_user=user, chat_instance="ci", data=data, message=msg)
    await dp.feed_update(bot, Update(update_id=_uid[0], callback_query=cb))


def _last_text(bot) -> str:
    calls = [c for c in bot.session.calls if type(c).__name__ == "SendMessage"]
    return calls[-1].text
