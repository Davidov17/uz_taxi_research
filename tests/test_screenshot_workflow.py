"""Tests for the driver-statistics screenshot collection workflow:
one screenshot, multiple screenshots, no screenshots, an interrupted
upload, and a rejected wrong file type — at both the service layer
(SurveySession) and, for the Telegram-facing acceptance behaviour, the
real aiogram handlers.
"""

from datetime import datetime, timezone

import pytest
from aiogram.types import CallbackQuery, Chat, Document, Message, PhotoSize, Update, User
from sqlalchemy import select

from telegram_bot.application.survey_session import CursorState, SurveySession
from telegram_bot.domain.enums import AttachmentFileType, SurveyStatus
from telegram_bot.domain.questionnaire import SCREENSHOTS_SECTION_INDEX, QuestionType
from telegram_bot.infrastructure.db.models import Attachment
from telegram_bot.presentation.states import SECTION_TO_STATE

SCREENSHOTS_STATE = SECTION_TO_STATE[SCREENSHOTS_SECTION_INDEX]

pytestmark = pytest.mark.asyncio


async def new_session(db, interviewer) -> SurveySession:
    return await SurveySession.resume(db, CursorState.initial(), interviewer)


async def advance_to_has_screenshots(s: SurveySession, *, city, platform_ids: list[str]) -> None:
    """Drive a fresh session up to (and stop at) the has_screenshots
    question, answering everything before it with quick placeholder
    values."""
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


async def attachments_for(db, survey_id):
    result = await db.execute(select(Attachment).where(Attachment.survey_id == survey_id).order_by(Attachment.sequence_number))
    return result.scalars().all()


# ---- service-layer tests --------------------------------------------------


async def test_one_screenshot(session, interviewer, city, platform_yandex):
    s = await new_session(session, interviewer)
    await advance_to_has_screenshots(s, city=city, platform_ids=[str(platform_yandex.id)])
    await s.answer(s.current_question(), True)
    assert s.awaiting_screenshot_upload is True
    assert not s.is_review()

    await s.record_screenshot(telegram_file_id="f1", telegram_file_unique_id="u1", file_type=AttachmentFileType.SCREENSHOT)
    await s.finish_screenshot_upload()

    assert s.is_review()
    rows = await attachments_for(session, s.survey.id)
    assert len(rows) == 1
    assert rows[0].sequence_number == 1
    assert rows[0].telegram_file_id == "f1"
    assert s.survey.screenshots_offered is True


async def test_multiple_screenshots_get_sequential_numbers(session, interviewer, city, platform_yandex):
    s = await new_session(session, interviewer)
    await advance_to_has_screenshots(s, city=city, platform_ids=[str(platform_yandex.id)])
    await s.answer(s.current_question(), True)

    await s.record_screenshot(telegram_file_id="f1", telegram_file_unique_id="u1", file_type=AttachmentFileType.SCREENSHOT)
    await s.record_screenshot(
        telegram_file_id="f2", telegram_file_unique_id="u2", file_type=AttachmentFileType.DOCUMENT, mime_type="image/png"
    )
    await s.record_screenshot(telegram_file_id="f3", telegram_file_unique_id="u3", file_type=AttachmentFileType.SCREENSHOT)
    await s.finish_screenshot_upload()

    rows = await attachments_for(session, s.survey.id)
    assert [r.sequence_number for r in rows] == [1, 2, 3]
    assert [r.telegram_file_id for r in rows] == ["f1", "f2", "f3"]
    assert rows[1].file_type == AttachmentFileType.DOCUMENT
    assert rows[1].mime_type == "image/png"
    assert s.attachment_count == 3


async def test_no_screenshots_goes_straight_to_review(session, interviewer, city, platform_yandex):
    s = await new_session(session, interviewer)
    await advance_to_has_screenshots(s, city=city, platform_ids=[str(platform_yandex.id)])
    await s.answer(s.current_question(), False)

    assert s.is_review()
    assert s.survey.screenshots_offered is False
    rows = await attachments_for(session, s.survey.id)
    assert rows == []


async def test_yes_but_zero_uploads_is_allowed(session, interviewer, city, platform_yandex):
    """"Do not force a fixed number of screenshots" — saying Yes and then
    finishing without uploading anything must be a valid path too."""
    s = await new_session(session, interviewer)
    await advance_to_has_screenshots(s, city=city, platform_ids=[str(platform_yandex.id)])
    await s.answer(s.current_question(), True)
    await s.finish_screenshot_upload()

    assert s.is_review()
    assert s.survey.screenshots_offered is True
    rows = await attachments_for(session, s.survey.id)
    assert rows == []


async def test_interrupted_upload_preserves_already_uploaded_screenshots(session, interviewer, city, platform_yandex):
    """Restarting mid-upload must not lose what was already saved — it
    survives on the abandoned survey; the new survey starts clean."""
    s = await new_session(session, interviewer)
    await advance_to_has_screenshots(s, city=city, platform_ids=[str(platform_yandex.id)])
    await s.answer(s.current_question(), True)
    await s.record_screenshot(telegram_file_id="f1", telegram_file_unique_id="u1", file_type=AttachmentFileType.SCREENSHOT)
    interrupted_survey_id = s.survey.id

    fresh = await s.restart()

    old_rows = await attachments_for(session, interrupted_survey_id)
    assert len(old_rows) == 1  # not deleted

    old_survey_result = await session.execute(select(Attachment.survey_id).where(Attachment.id == old_rows[0].id))
    assert old_survey_result.scalar_one() == interrupted_survey_id

    from telegram_bot.infrastructure.db.models import Survey

    old_survey = await session.get(Survey, interrupted_survey_id)
    assert old_survey.status == SurveyStatus.ABANDONED

    assert fresh.survey is None
    assert fresh.attachment_count == 0


async def test_platform_tagging_for_multi_platform_survey(session, interviewer, city, platform_yandex, platform_uklon):
    s = await new_session(session, interviewer)
    await advance_to_has_screenshots(s, city=city, platform_ids=[str(platform_yandex.id), str(platform_uklon.id)])
    await s.answer(s.current_question(), True)

    tagged = await s.record_screenshot(
        telegram_file_id="f1", telegram_file_unique_id="u1", file_type=AttachmentFileType.SCREENSHOT, platform_id=platform_uklon.id
    )
    untagged = await s.record_screenshot(telegram_file_id="f2", telegram_file_unique_id="u2", file_type=AttachmentFileType.SCREENSHOT)

    assert tagged.platform_id == platform_uklon.id
    assert untagged.platform_id is None


async def test_description_is_stored_verbatim(session, interviewer, city, platform_yandex):
    s = await new_session(session, interviewer)
    await advance_to_has_screenshots(s, city=city, platform_ids=[str(platform_yandex.id)])
    await s.answer(s.current_question(), True)

    attachment = await s.record_screenshot(
        telegram_file_id="f1", telegram_file_unique_id="u1", file_type=AttachmentFileType.SCREENSHOT, description="Previous week"
    )
    assert attachment.description == "Previous week"


# ---- handler-level tests (real Telegram message types, fake network) -----
# `bot`, `dp`, and DB wiring come from conftest.py — shared across test
# modules since `survey_router` is a singleton that can only ever be
# attached to one Dispatcher.


def make_user(user_id: int) -> User:
    return User(id=user_id, is_bot=False, first_name="Test")


def make_chat(chat_id: int) -> Chat:
    return Chat(id=chat_id, type="private")


_uid = [0]


async def send_text(dp, bot, user, text):
    _uid[0] += 1
    msg = Message(message_id=_uid[0], date=datetime.now(timezone.utc), chat=make_chat(user.id), from_user=user, text=text)
    await dp.feed_update(bot, Update(update_id=_uid[0], message=msg))


async def send_callback(dp, bot, user, data):
    _uid[0] += 1
    msg = Message(message_id=_uid[0], date=datetime.now(timezone.utc), chat=make_chat(user.id), from_user=user, text="prompt")
    cb = CallbackQuery(id=str(_uid[0]), from_user=user, chat_instance="ci", data=data, message=msg)
    await dp.feed_update(bot, Update(update_id=_uid[0], callback_query=cb))


async def send_photo(dp, bot, user, file_id="photo_file", file_unique_id="photo_unique"):
    _uid[0] += 1
    photo = [PhotoSize(file_id=file_id, file_unique_id=file_unique_id, width=100, height=100, file_size=1000)]
    msg = Message(message_id=_uid[0], date=datetime.now(timezone.utc), chat=make_chat(user.id), from_user=user, photo=photo)
    await dp.feed_update(bot, Update(update_id=_uid[0], message=msg))


async def send_document(dp, bot, user, *, mime_type, file_id="doc_file", file_unique_id="doc_unique"):
    _uid[0] += 1
    doc = Document(file_id=file_id, file_unique_id=file_unique_id, mime_type=mime_type, file_name="stats.png")
    msg = Message(message_id=_uid[0], date=datetime.now(timezone.utc), chat=make_chat(user.id), from_user=user, document=doc)
    await dp.feed_update(bot, Update(update_id=_uid[0], message=msg))


def last_text(bot) -> str:
    calls = [c for c in bot.session.calls if type(c).__name__ == "SendMessage"]
    return calls[-1].text


async def drive_to_has_screenshots(dp, bot, user, session, city, platform_yandex):
    """Get a survey to the has_screenshots question without hand-replaying
    15 sections of button taps (fragile against questionnaire changes):
    drive SurveySession directly to that point, then seed the dispatcher's
    own FSM storage with the resulting cursor so the *next* update is
    handled by the real handlers — which is what these tests actually
    need to exercise (Telegram photo/document acceptance).
    """
    from aiogram.fsm.storage.base import StorageKey

    from telegram_bot.application.survey_session import get_or_create_interviewer
    from telegram_bot.infrastructure.db.models import Survey

    interviewer = await get_or_create_interviewer(session, user.id, user.first_name)
    s = await new_session(session, interviewer)
    await advance_to_has_screenshots(s, city=city, platform_ids=[str(platform_yandex.id)])
    await session.flush()

    key = StorageKey(bot_id=bot.id, chat_id=user.id, user_id=user.id)
    await dp.storage.set_data(key, {"cursor": s.cursor.to_dict()})
    await dp.storage.set_state(key, SCREENSHOTS_STATE)

    return await session.get(Survey, s.survey.id)


async def test_handler_wrong_file_type_rejected(dp, bot, session, city, platform_yandex):
    user = make_user(9001)
    survey = await drive_to_has_screenshots(dp, bot, user, session, city, platform_yandex)
    await send_callback(dp, bot, user, "ans:true")  # has_screenshots

    await send_document(dp, bot, user, mime_type="application/pdf")

    text = last_text(bot)
    assert "isn't supported" in text or "not supported" in text.lower()

    rows = await attachments_for(session, survey.id)
    assert rows == []


async def test_handler_one_screenshot_via_photo(dp, bot, session, city, platform_yandex):
    user = make_user(9002)
    survey = await drive_to_has_screenshots(dp, bot, user, session, city, platform_yandex)
    await send_callback(dp, bot, user, "ans:true")

    await send_photo(dp, bot, user)
    await send_callback(dp, bot, user, "screenshot_desc_skip")  # single platform -> no platform step
    await send_callback(dp, bot, user, "screenshot_finish")

    rows = await attachments_for(session, survey.id)
    assert len(rows) == 1
    assert rows[0].file_type == AttachmentFileType.SCREENSHOT
    assert rows[0].platform_id == platform_yandex.id  # auto-tagged, single platform


async def test_handler_multiple_screenshots_photo_and_document(dp, bot, session, city, platform_yandex):
    user = make_user(9003)
    survey = await drive_to_has_screenshots(dp, bot, user, session, city, platform_yandex)
    await send_callback(dp, bot, user, "ans:true")

    await send_photo(dp, bot, user, file_id="p1", file_unique_id="pu1")
    await send_callback(dp, bot, user, "screenshot_desc:current_week")
    await send_document(dp, bot, user, mime_type="image/jpeg", file_id="d1", file_unique_id="du1")
    await send_callback(dp, bot, user, "screenshot_desc:previous_week")
    await send_callback(dp, bot, user, "screenshot_finish")

    rows = await attachments_for(session, survey.id)
    assert len(rows) == 2
    assert rows[0].file_type == AttachmentFileType.SCREENSHOT
    assert rows[0].description == "Current week"
    assert rows[1].file_type == AttachmentFileType.DOCUMENT
    assert rows[1].mime_type == "image/jpeg"
    assert rows[1].description == "Previous week"


async def test_handler_no_screenshots(dp, bot, session, city, platform_yandex):
    user = make_user(9004)
    survey = await drive_to_has_screenshots(dp, bot, user, session, city, platform_yandex)
    await send_callback(dp, bot, user, "ans:false")

    text = last_text(bot)
    # straight to the review/confirmation screen — no auto-submission
    assert survey.human_code in text

    calls = [c for c in bot.session.calls if type(c).__name__ == "SendMessage"]
    keyboard = calls[-1].reply_markup
    button_labels = {btn.text for row in keyboard.inline_keyboard for btn in row}
    assert any("Confirm" in label for label in button_labels)

    from telegram_bot.domain.enums import SurveyStatus

    await session.refresh(survey)
    assert survey.status == SurveyStatus.DRAFT

    rows = await attachments_for(session, survey.id)
    assert rows == []


async def test_handler_interrupted_upload_second_file_before_finishing_step(dp, bot, session, city, platform_yandex):
    """Sending a second photo before answering the description step for
    the first one must not silently drop or corrupt the first upload."""
    user = make_user(9005)
    survey = await drive_to_has_screenshots(dp, bot, user, session, city, platform_yandex)
    await send_callback(dp, bot, user, "ans:true")

    await send_photo(dp, bot, user, file_id="first", file_unique_id="first_u")
    await send_photo(dp, bot, user, file_id="second", file_unique_id="second_u")  # interrupts before description saved

    text = last_text(bot)
    assert "finish the previous step" in text.lower()

    rows = await attachments_for(session, survey.id)
    assert len(rows) == 1
    assert rows[0].telegram_file_id == "first"

    # completing the step properly then still works
    await send_callback(dp, bot, user, "screenshot_desc_skip")
    await send_callback(dp, bot, user, "screenshot_finish")
    rows = await attachments_for(session, survey.id)
    assert len(rows) == 1
