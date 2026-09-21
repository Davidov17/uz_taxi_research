"""End-to-end tests driving the real aiogram Dispatcher/Router against a
fake Bot session (no network) and the real test database, to check the
presentation layer is wired correctly on top of the already-thoroughly
tested application layer (test_question_engine.py, test_survey_session.py).
"""

from datetime import datetime, timezone

import pytest
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from sqlalchemy import select

from telegram_bot.domain.enums import SurveyStatus
from telegram_bot.infrastructure.db.models import Survey, SurveyPlatform

pytestmark = pytest.mark.asyncio


def make_user(user_id: int = 111) -> User:
    return User(id=user_id, is_bot=False, first_name="Test", username="testinterviewer")


def make_chat(chat_id: int = 111) -> Chat:
    return Chat(id=chat_id, type="private")


async def send_text(dp, bot, user, text, update_id=[0]):
    update_id[0] += 1
    msg = Message(message_id=update_id[0], date=datetime.now(timezone.utc), chat=make_chat(user.id), from_user=user, text=text)
    await dp.feed_update(bot, Update(update_id=update_id[0], message=msg))


async def send_callback(dp, bot, user, data, update_id=[1000]):
    update_id[0] += 1
    msg = Message(message_id=update_id[0], date=datetime.now(timezone.utc), chat=make_chat(user.id), from_user=user, text="prompt")
    cb = CallbackQuery(id=str(update_id[0]), from_user=user, chat_instance="ci", data=data, message=msg)
    await dp.feed_update(bot, Update(update_id=update_id[0], callback_query=cb))


async def begin_survey_in_english(dp, bot, user) -> None:
    """/start -> language picker -> English, landing on the city question.
    The shared first step of nearly every handler test below."""
    await send_text(dp, bot, user, "/start")
    await send_callback(dp, bot, user, "lang:en")


def last_sent_text(bot) -> str:
    calls = [c for c in bot.session.calls if type(c).__name__ == "SendMessage"]
    return calls[-1].text


async def test_start_shows_language_picker(dp, bot):
    user = make_user(1)
    await send_text(dp, bot, user, "/start")
    text = last_sent_text(bot)
    assert "language" in text.lower()

    calls = [c for c in bot.session.calls if type(c).__name__ == "SendMessage"]
    keyboard = calls[-1].reply_markup
    callback_data = {btn.callback_data for row in keyboard.inline_keyboard for btn in row}
    assert callback_data == {"lang:en", "lang:ru", "lang:uz"}


async def test_selecting_language_shows_city_question(dp, bot):
    user = make_user(2)
    await begin_survey_in_english(dp, bot, user)
    text = last_sent_text(bot)
    assert "city" in text.lower()


async def test_full_platform_selection_creates_survey_platform_rows(dp, bot, session, platform_yandex, platform_uklon):
    user = make_user(3)
    await begin_survey_in_english(dp, bot, user)

    # answer city (first available single-choice option)
    await send_callback(dp, bot, user, "ans:1")

    # now on platforms_used (multi-choice): toggle both platforms, then Done
    await send_callback(dp, bot, user, f"tgl:{platform_yandex.id}")
    await send_callback(dp, bot, user, f"tgl:{platform_uklon.id}")
    await send_callback(dp, bot, user, "mdone")

    result = await session.execute(select(Survey).where(Survey.interviewer.has(telegram_user_id=user.id)))
    survey = result.scalar_one()
    sps = (await session.execute(select(SurveyPlatform).where(SurveyPlatform.survey_id == survey.id))).scalars().all()
    assert {sp.platform_id for sp in sps} == {platform_yandex.id, platform_uklon.id}

    text = last_sent_text(bot)
    assert "Q2." in text  # platforms_used answered -> Q2 (switch_frequency), unconditionally


async def test_back_button_returns_to_previous_section(dp, bot):
    user = make_user(4)
    await begin_survey_in_english(dp, bot, user)
    await send_callback(dp, bot, user, "ans:1")  # city
    text_after_city = last_sent_text(bot)
    assert "Q1." in text_after_city  # now on platforms_used

    await send_callback(dp, bot, user, "back")
    text_after_back = last_sent_text(bot)
    assert "city" in text_after_back.lower()


async def test_restart_flow_abandons_survey_and_shows_language_picker(dp, bot, session):
    user = make_user(5)
    await begin_survey_in_english(dp, bot, user)
    await send_callback(dp, bot, user, "ans:1")  # city -> creates the Survey row

    result = await session.execute(select(Survey).where(Survey.interviewer.has(telegram_user_id=user.id)))
    old_survey = result.scalar_one()

    await send_text(dp, bot, user, "/restart")
    await send_callback(dp, bot, user, "restart_yes")

    await session.refresh(old_survey)
    assert old_survey.status == SurveyStatus.ABANDONED

    # a new survey means language can be selected again
    text = last_sent_text(bot)
    assert "language" in text.lower()

    await send_callback(dp, bot, user, "lang:ru")
    text_after_lang = last_sent_text(bot)
    assert "город" in text_after_lang.lower()  # Russian city question, confirming ru applied
