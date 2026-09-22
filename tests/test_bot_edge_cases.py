"""Closes gaps left by the rest of the suite: handler-level coverage for
`skip`, `bonuses`, and free-text validation (`msg_text_answer`) — none of
which had a direct test driving the real aiogram Dispatcher before this
file, even though the underlying parsing/branching logic was already
well covered at the service layer (test_question_engine.py,
test_survey_session.py). Also covers a general "duplicate Telegram
update" edge case beyond the screenshot-specific one in
test_screenshot_workflow.py.
"""

from datetime import datetime, timezone

import pytest
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from telegram_bot.application.survey_session import CursorState, SurveySession, get_or_create_interviewer
from telegram_bot.domain.questionnaire import QuestionType
from telegram_bot.presentation.states import SECTION_TO_STATE

pytestmark = pytest.mark.asyncio

_uid = [30000]


async def new_session(db, interviewer) -> SurveySession:
    return await SurveySession.resume(db, CursorState.initial(), interviewer)


async def advance_service_session_to(
    s: SurveySession, *, city, platform_ids: list[str], target_code: str, overrides: dict | None = None
):
    """Drive SurveySession forward with generic placeholder answers,
    stopping right before `target_code` so a test can exercise that one
    step through the real handlers instead. Same rationale as the
    equivalent helpers in test_screenshot_workflow.py /
    test_submission_flow.py: hand-counting button taps through a dozen
    sections is fragile against questionnaire changes; driving the
    service layer to a known point and only using real Telegram updates
    for the step under test is not. `overrides` forces a specific answer
    for a question code encountered along the way (e.g. receives_bonuses
    -> True, to reach a bonus-detail question that's conditioned on it).
    """
    overrides = overrides or {}
    for _ in range(300):
        q = s.current_question()
        if q is None or q.code == target_code:
            return
        if q.code == "city":
            await s.answer(q, str(city.id))
        elif q.code == "platforms_used":
            await s.answer(q, platform_ids)
        elif q.code in overrides:
            await s.answer(q, overrides[q.code])
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
    raise AssertionError(f"did not reach {target_code!r}")


async def seed_dispatcher_at(
    dp, bot, db, user: User, *, city, platform_ids: list[str], target_code: str, overrides: dict | None = None
):
    """Drive to `target_code` at the service layer, then seed the
    dispatcher's FSM storage so the next real update is handled by the
    actual aiogram handlers, landing exactly on that question."""
    interviewer = await get_or_create_interviewer(db, user.id, user.first_name)
    s = await new_session(db, interviewer)
    await advance_service_session_to(s, city=city, platform_ids=platform_ids, target_code=target_code, overrides=overrides)
    await db.flush()

    key = StorageKey(bot_id=bot.id, chat_id=user.id, user_id=user.id)
    await dp.storage.set_data(key, {"cursor": s.cursor.to_dict()})
    await dp.storage.set_state(key, SECTION_TO_STATE[s.cursor.position.section_index])
    return s


def make_user(user_id: int) -> User:
    return User(id=user_id, is_bot=False, first_name="Test")


def make_chat(chat_id: int) -> Chat:
    return Chat(id=chat_id, type="private")


async def send_text(dp, bot, user, text):
    _uid[0] += 1
    msg = Message(message_id=_uid[0], date=datetime.now(timezone.utc), chat=make_chat(user.id), from_user=user, text=text)
    await dp.feed_update(bot, Update(update_id=_uid[0], message=msg))


async def send_callback(dp, bot, user, data):
    _uid[0] += 1
    msg = Message(message_id=_uid[0], date=datetime.now(timezone.utc), chat=make_chat(user.id), from_user=user, text="prompt")
    cb = CallbackQuery(id=str(_uid[0]), from_user=user, chat_instance="ci", data=data, message=msg)
    await dp.feed_update(bot, Update(update_id=_uid[0], callback_query=cb))


def last_text(bot) -> str:
    calls = [c for c in bot.session.calls if type(c).__name__ == "SendMessage"]
    return calls[-1].text


def sent_texts_since(bot, count_before) -> list[str]:
    calls = [c for c in bot.session.calls if type(c).__name__ == "SendMessage"]
    return [c.text for c in calls[count_before:]]


def sent_message_count(bot) -> int:
    return len([c for c in bot.session.calls if type(c).__name__ == "SendMessage"])


# ---- BOT TESTS: skip -------------------------------------------------------------
#
# There's no longer any optional/skippable question in the live flow
# (target_platform, the only one, was removed) — "skip succeeds" has no
# live question left to test against, so only the rejection path below
# (skip on a required question) remains exercisable.


async def test_skip_required_question_is_rejected(dp, bot, session, city, platform_yandex):
    """days_per_week is required; the Skip button shouldn't even be shown,
    but a stray "skip" callback (e.g. a stale button) must be rejected, not
    silently accepted. The handler rejects it via an alert callback answer
    rather than a new chat message, so no question was advanced or
    re-sent — confirmed here by asserting no new SendMessage happened."""
    user = make_user(31002)
    await seed_dispatcher_at(
        dp, bot, session, user, city=city, platform_ids=[str(platform_yandex.id)], target_code="days_per_week"
    )
    before = sent_message_count(bot)
    await send_callback(dp, bot, user, "skip")
    assert sent_message_count(bot) == before

    alerts = [c for c in bot.session.calls if type(c).__name__ == "AnswerCallbackQuery"]
    assert alerts and alerts[-1].show_alert is True


# ---- days_per_week: SINGLE_CHOICE (1 day .. 7 days) ------------------------------


async def test_days_per_week_renders_all_seven_options(dp, bot, session, city, platform_yandex):
    user = make_user(31010)
    await seed_dispatcher_at(
        dp, bot, session, user, city=city, platform_ids=[str(platform_yandex.id)], target_code="days_per_week"
    )
    # seeding only sets FSM state; send /start to trigger an actual render
    # of the current question ("continuing where you left off" + the
    # question itself) without answering or mutating anything.
    await send_text(dp, bot, user, "/start")
    calls = [c for c in bot.session.calls if type(c).__name__ == "SendMessage"]
    keyboard = calls[-1].reply_markup
    option_rows = [row for row in keyboard.inline_keyboard if row[0].callback_data.startswith("ans:")]
    assert len(option_rows) == 7

    values = [row[0].callback_data.removeprefix("ans:") for row in option_rows]
    labels = [row[0].text for row in option_rows]
    assert values == ["1", "2", "3", "4", "5", "6", "7"]
    assert labels == ["1 day", "2 days", "3 days", "4 days", "5 days", "6 days", "7 days"]

    # required question: no Skip button alongside the options
    assert not any(
        btn.callback_data == "skip" for row in keyboard.inline_keyboard for btn in row
    )


async def test_days_per_week_selecting_option_advances_to_next_question(dp, bot, session, city, platform_yandex):
    user = make_user(31011)
    await seed_dispatcher_at(
        dp, bot, session, user, city=city, platform_ids=[str(platform_yandex.id)], target_code="days_per_week"
    )
    await send_callback(dp, bot, user, "ans:5")
    text = last_text(bot)
    assert "hours on average" in text.lower()


async def test_days_per_week_stored_value_is_numeric(dp, bot, session, city, platform_yandex):
    user = make_user(31012)
    await seed_dispatcher_at(
        dp, bot, session, user, city=city, platform_ids=[str(platform_yandex.id)], target_code="days_per_week"
    )
    await send_callback(dp, bot, user, "ans:3")

    from sqlalchemy import select

    from telegram_bot.infrastructure.db.models import WorkingStats

    row = (await session.execute(select(WorkingStats))).scalar_one()
    assert row.days_per_week == 3
    assert isinstance(row.days_per_week, int)


async def test_days_per_week_each_option_stores_its_own_value(dp, bot, session, city, platform_yandex):
    """All 7 options round-trip to the right stored value, not just one."""
    for n in range(1, 8):
        user = make_user(31020 + n)
        await seed_dispatcher_at(
            dp, bot, session, user, city=city, platform_ids=[str(platform_yandex.id)], target_code="days_per_week"
        )
        await send_callback(dp, bot, user, f"ans:{n}")

    from sqlalchemy import select

    from telegram_bot.infrastructure.db.models import WorkingStats

    rows = (await session.execute(select(WorkingStats))).scalars().all()
    assert sorted(row.days_per_week for row in rows) == [1, 2, 3, 4, 5, 6, 7]


async def test_days_per_week_still_rejects_skip(dp, bot, session, city, platform_yandex):
    """Duplicate of test_skip_required_question_is_rejected's assertion,
    kept local to this section so "existing validation continues to work"
    for days_per_week specifically is covered even if that other test is
    ever moved/renamed."""
    user = make_user(31013)
    await seed_dispatcher_at(
        dp, bot, session, user, city=city, platform_ids=[str(platform_yandex.id)], target_code="days_per_week"
    )
    before = sent_message_count(bot)
    await send_callback(dp, bot, user, "skip")
    assert sent_message_count(bot) == before

    from sqlalchemy import select

    from telegram_bot.infrastructure.db.models import WorkingStats

    rows = (await session.execute(select(WorkingStats))).scalars().all()
    assert all(row.days_per_week is None for row in rows)


async def test_days_per_week_back_navigation_still_works(dp, bot, session, city, platform_yandex):
    """Navigation (Back) must still work for this question after switching
    it from NUMBER to SINGLE_CHOICE."""
    user = make_user(31014)
    await seed_dispatcher_at(
        dp, bot, session, user, city=city, platform_ids=[str(platform_yandex.id)], target_code="days_per_week"
    )
    await send_callback(dp, bot, user, "ans:4")
    assert "hours on average" in last_text(bot).lower()

    await send_callback(dp, bot, user, "back")
    text = last_text(bot)
    assert "days per week" in text.lower()

    calls = [c for c in bot.session.calls if type(c).__name__ == "SendMessage"]
    keyboard = calls[-1].reply_markup
    option_rows = [row for row in keyboard.inline_keyboard if row[0].callback_data.startswith("ans:")]
    assert len(option_rows) == 7


# ---- BOT TESTS: bonuses -----------------------------------------------------------


async def test_bonus_type_yes_stores_receives_bonuses_and_advances_via_handler(dp, bot, session, city, platform_yandex):
    """bonus_type (Q10) has no conditional follow-up any more — choosing a
    "yes" option just records receives_bonuses=True on the platform's Bonus
    row and moves straight on to Q11 (cash_pct)."""
    user = make_user(31003)
    await seed_dispatcher_at(
        dp, bot, session, user, city=city, platform_ids=[str(platform_yandex.id)], target_code="bonus_type"
    )
    await send_callback(dp, bot, user, "ans:trip_based")
    text = last_text(bot)
    assert "q11." in text.lower()

    from sqlalchemy import select

    from telegram_bot.infrastructure.db.models import Bonus

    row = (await session.execute(select(Bonus))).scalar_one()
    assert row.receives_bonuses is True


async def test_bonus_type_none_stores_no_bonuses_via_handler(dp, bot, session, city, platform_yandex):
    user = make_user(31004)
    await seed_dispatcher_at(
        dp, bot, session, user, city=city, platform_ids=[str(platform_yandex.id)], target_code="bonus_type"
    )
    await send_callback(dp, bot, user, "ans:none")
    text = last_text(bot)
    assert "q11." in text.lower()

    from sqlalchemy import select

    from telegram_bot.infrastructure.db.models import Bonus

    row = (await session.execute(select(Bonus))).scalar_one()
    assert row.receives_bonuses is False


# ---- EDGE CASES: invalid / out-of-range free-text answers -----------------------
#
# NOTE: the 15-question redesign replaced every free-text NUMBER/
# PERCENTAGE/CURRENCY question (required_trips, commission_pct,
# weekly_earnings_amount, ...) with predefined-range buttons — see
# docs/SURVEY_SPECIFICATION.md's "every question should use options"
# requirement. No question in the current questionnaire has qtype NUMBER,
# PERCENTAGE, or CURRENCY any more, so the handler's msg_text_answer
# validation branch for those types has no live caller to exercise through
# a real Update; parse_number/parse_percentage/parse_currency themselves
# are still covered directly in test_question_engine.py. Flagged as a
# remaining limitation in the final report rather than silently dropped.


# ---- EDGE CASES: duplicate/repeated Telegram updates (general, not screenshot-specific) --
#
# General "duplicate stale button tap" protection is exercised elsewhere
# too, in a still-live mechanism — see test_submission_flow.py's
# test_duplicate_confirm_tap_via_handler_shows_existing_survey_id — and
# for skip specifically:


async def test_double_tap_skip_is_rejected_both_times(dp, bot, session, city, platform_yandex):
    """Two consecutive stale/duplicate "skip" taps on a required question
    must both be safely rejected (alert, no new message), never crash or
    silently advance past it."""
    user = make_user(31009)
    await seed_dispatcher_at(
        dp, bot, session, user, city=city, platform_ids=[str(platform_yandex.id)], target_code="switch_frequency"
    )
    before = sent_message_count(bot)

    await send_callback(dp, bot, user, "skip")
    await send_callback(dp, bot, user, "skip")

    assert sent_message_count(bot) == before
    alerts = [c for c in bot.session.calls if type(c).__name__ == "AnswerCallbackQuery"]
    assert len(alerts) == 2
    assert all(a.show_alert is True for a in alerts)
