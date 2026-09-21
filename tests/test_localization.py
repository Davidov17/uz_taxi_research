"""Dedicated multilingual-support tests: language selection (all three
languages), persistence across resume/back, localized question rendering,
localized answer options, and localized navigation buttons.

Complements the language coverage already exercised incidentally in
test_handlers.py (English happy path), test_survey_session.py (the
service-layer language property/set_language), and test_submission_flow.py
(re-selecting a language for a new survey) — this file is the single place
that systematically checks all three languages side by side.
"""

from datetime import datetime, timezone

import pytest
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from telegram_bot.application.survey_session import CursorState, SurveySession, get_or_create_interviewer

pytestmark = pytest.mark.asyncio

_uid = [40000]


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


def last_message(bot):
    calls = [c for c in bot.session.calls if type(c).__name__ == "SendMessage"]
    return calls[-1]


# ---- 1-3: language selection, all three languages --------------------------


@pytest.mark.parametrize(
    ("lang_code", "expected_snippet"),
    [
        ("en", "which city"),
        ("ru", "каком городе"),  # "в каком городе" fragment
        ("uz", "shahar"),
    ],
)
async def test_selecting_each_language_renders_city_question_in_that_language(dp, bot, lang_code, expected_snippet):
    user = make_user(41000 + hash(lang_code) % 1000)
    await send_text(dp, bot, user, "/start")
    await send_callback(dp, bot, user, f"lang:{lang_code}")
    text = last_message(bot).text.lower()
    assert expected_snippet.lower() in text


# ---- 4: language persistence -----------------------------------------------


async def test_language_persists_when_resuming_a_draft_via_start(dp, bot, session):
    user = make_user(41010)
    await send_text(dp, bot, user, "/start")
    await send_callback(dp, bot, user, "lang:ru")
    await send_callback(dp, bot, user, "ans:1")  # city (first option) -> creates the Survey row

    # simulate the bot process restarting: re-send /start with the same
    # user/chat, resuming the draft rather than showing the language picker
    await send_text(dp, bot, user, "/start")
    text = last_message(bot).text
    assert "выберите язык" not in text.lower()  # not stuck on the language picker
    # the re-rendered current question (platforms_used, Q1) is still in Russian
    assert "приложени" in text.lower()


async def test_language_persists_across_back_navigation(dp, bot):
    user = make_user(41011)
    await send_text(dp, bot, user, "/start")
    await send_callback(dp, bot, user, "lang:uz")
    await send_callback(dp, bot, user, "ans:1")  # city
    await send_callback(dp, bot, user, "back")
    text = last_message(bot).text.lower()
    assert "shahar" in text  # still Uzbek after navigating back to the city question


# ---- 5: question rendering already covered above; 6: localized options ----


async def test_localized_answer_options_switch_frequency(dp, bot, session, city, platform_yandex, platform_uklon):
    user = make_user(41020)
    await send_text(dp, bot, user, "/start")
    await send_callback(dp, bot, user, "lang:ru")
    await send_callback(dp, bot, user, "ans:1")  # city
    await send_callback(dp, bot, user, "skip")  # target_platform
    await send_callback(dp, bot, user, f"tgl:{platform_yandex.id}")
    await send_callback(dp, bot, user, f"tgl:{platform_uklon.id}")
    await send_callback(dp, bot, user, "mdone")  # -> switch_frequency (multiple platforms)

    keyboard = last_message(bot).reply_markup
    labels = [btn.text for row in keyboard.inline_keyboard for btn in row if btn.callback_data.startswith("ans:")]
    assert "Никогда, всегда использую одно приложение" in labels


# ---- 9: localized navigation buttons ---------------------------------------


@pytest.mark.parametrize(
    ("lang_code", "back_label", "skip_label"),
    [
        ("en", "← Back", "Skip"),
        ("ru", "← Назад", "Пропустить"),
        ("uz", "← Orqaga", "O'tkazib yuborish"),
    ],
)
async def test_navigation_buttons_localized(dp, bot, lang_code, back_label, skip_label):
    user = make_user(41030 + hash(lang_code) % 1000)
    await send_text(dp, bot, user, "/start")
    await send_callback(dp, bot, user, f"lang:{lang_code}")
    await send_callback(dp, bot, user, "ans:1")  # city -> platforms_used (required, has Back but no Skip)

    keyboard = last_message(bot).reply_markup
    labels = {btn.text for row in keyboard.inline_keyboard for btn in row}
    assert back_label in labels

    # No question in the current flat questionnaire is optional any more
    # (target_platform, the only one, was removed), so Skip's label can't
    # be exercised through a live question — check the keyboard builder
    # directly instead, the same function the live flow would call for
    # a required=False question if one existed.
    from telegram_bot.domain.questionnaire import Option
    from telegram_bot.presentation.keyboards import single_choice_kb

    kb = single_choice_kb([Option("1", "x")], lang_code, allow_back=True, allow_skip=True)
    skip_labels = {btn.text for row in kb.inline_keyboard for btn in row}
    assert skip_label in skip_labels


# ---- service-layer: language selection stored on the Survey row -----------


async def test_language_selection_written_to_survey_row(session, interviewer, city):
    s = await SurveySession.resume(session, CursorState.initial(language="uz"), interviewer)
    await s.answer(s.current_question(), str(city.id))
    assert s.survey.language.value == "uz"
