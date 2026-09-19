"""Telegram-facing survey handlers.

Every handler here is a thin translator: parse the incoming update into a
raw answer value, hand it to SurveySession, and render whatever question
SurveySession says is now current, in the interviewer's chosen language
(session.language). No question ordering, validation, or persistence
logic lives in this file — see application/survey_session.py and
application/question_engine.py for that. No translated strings live here
either — see domain/i18n.py.

Language selection happens once, before Question 1 (see cmd_start /
cb_select_language) and is not itself a questionnaire section — it has no
SurveySession position, just a small dedicated FSM state
(SurveyStates.language_select).

Once the driver has answered the last question (or finished the
screenshot step), SurveySession.is_review() becomes true and _send_current
renders the structured review/confirmation screen — no automatic
submission. The survey is only actually finished when the interviewer
taps Confirm/Submit (review_confirm), which calls session.confirm().
Edit/Back (review_edit) reuses ordinary Back navigation to return to the
last question.

The one exception to "one question, one render" is the screenshot upload
loop (Section 11): it isn't a single question/answer exchange like
everything else, so its per-file sub-steps (which platform, what period)
are tracked as small pieces of transient UI state in FSMContext data
(`screenshot_stage`, `pending_attachment_id`) rather than forced through
the generic Position-based question engine.
"""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, User
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_bot.application.question_engine import ValidationError
from telegram_bot.application.survey_session import CursorState, SurveySession, get_or_create_interviewer
from telegram_bot.domain.enums import AttachmentFileType, SurveyStatus
from telegram_bot.domain.i18n import DEFAULT_LANGUAGE, t, validation_message
from telegram_bot.domain.questionnaire import SCREENSHOTS_SECTION_INDEX, Option, QuestionType
from telegram_bot.infrastructure.config import settings
from telegram_bot.infrastructure.db.models import Attachment, Interviewer
from telegram_bot.infrastructure.storage import download_attachment
from telegram_bot.presentation.formatters import format_completion, format_review, question_message
from telegram_bot.presentation.keyboards import (
    SCREENSHOT_DESCRIPTION_PRESET_KEYS,
    confirm_kb,
    language_select_kb,
    multi_choice_kb,
    review_kb,
    screenshot_awaiting_kb,
    screenshot_continue_kb,
    screenshot_description_kb,
    screenshot_platform_kb,
    single_choice_kb,
    start_new_survey_kb,
    text_input_kb,
    yes_no_kb,
)
from telegram_bot.presentation.states import ALL_SURVEY_STATES, SECTION_TO_STATE, SurveyStates

router = Router(name="survey")

SCREENSHOTS_STATE = SECTION_TO_STATE[SCREENSHOTS_SECTION_INDEX]


# ---- session <-> FSMContext plumbing ------------------------------------


async def _interviewer_for(db: AsyncSession, user: User) -> Interviewer:
    return await get_or_create_interviewer(db, user.id, user.full_name or str(user.id))


async def _load_session(state: FSMContext, db: AsyncSession, interviewer: Interviewer) -> SurveySession:
    data = await state.get_data()
    cursor_dict = data.get("cursor")
    cursor = CursorState.from_dict(cursor_dict) if cursor_dict else CursorState.initial()
    return await SurveySession.resume(db, cursor, interviewer)


async def _save_session(state: FSMContext, session: SurveySession, *, clear_draft: bool = True) -> None:
    data = await state.get_data()
    data["cursor"] = session.cursor.to_dict()
    if clear_draft:
        data.pop("multi_draft", None)
        data.pop("screenshot_stage", None)
        data.pop("pending_attachment_id", None)
    await state.set_data(data)
    await state.set_state(SECTION_TO_STATE[session.cursor.position.section_index])


async def _send_current(bot: Bot, chat_id: int, session: SurveySession, state: FSMContext) -> None:
    lang = session.language

    if session.awaiting_screenshot_upload:
        await _send_screenshot_upload_prompt(bot, chat_id, session, state)
        return

    if session.is_review():
        if session.survey.status == SurveyStatus.COMPLETED:
            # finish() is idempotent, so a stale tap / resumed session /
            # Telegram retry after a real Confirm always lands here and
            # shows the same survey ID rather than erroring or re-showing
            # an editable review screen for an already-submitted survey.
            await bot.send_message(chat_id, format_completion(session.survey, lang), reply_markup=start_new_survey_kb(lang))
        else:
            await bot.send_message(chat_id, format_review(session, lang), reply_markup=review_kb(lang))
        return

    question = session.current_question()
    text = question_message(session, question, lang)
    can_back = session.can_go_back()
    allow_skip = not question.required

    if question.qtype == QuestionType.SINGLE_CHOICE:
        options = await session.resolve_options(question, lang)
        kb = single_choice_kb(options, lang, allow_back=can_back, allow_skip=allow_skip)
    elif question.qtype == QuestionType.MULTI_CHOICE:
        options = await session.resolve_options(question, lang)
        selected = session.previous_multi_selection(question)
        await state.update_data(multi_draft=sorted(selected))
        kb = multi_choice_kb(options, selected=selected, lang=lang, allow_back=can_back)
    elif question.qtype == QuestionType.YES_NO:
        kb = yes_no_kb(lang, allow_back=can_back, allow_skip=allow_skip)
    else:
        kb = text_input_kb(lang, allow_back=can_back, allow_skip=allow_skip)

    await bot.send_message(chat_id, text, reply_markup=kb)


async def _advance_and_render(session: SurveySession, state: FSMContext, bot: Bot, chat_id: int) -> None:
    await _save_session(state, session)
    await _send_current(bot, chat_id, session, state)


async def _prompt_language_selection(bot: Bot, chat_id: int, state: FSMContext) -> None:
    await state.set_data({})
    await state.set_state(SurveyStates.language_select)
    await bot.send_message(chat_id, t("select_language_prompt", DEFAULT_LANGUAGE), reply_markup=language_select_kb())


# ---- entry points ---------------------------------------------------------


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, db: AsyncSession) -> None:
    interviewer = await _interviewer_for(db, message.from_user)
    data = await state.get_data()
    cursor_dict = data.get("cursor")
    if cursor_dict and cursor_dict.get("survey_id"):
        session = await SurveySession.resume(db, CursorState.from_dict(cursor_dict), interviewer)
        await message.answer(t("survey_in_progress", session.language))
        await _send_current(message.bot, message.chat.id, session, state)
        return
    await _prompt_language_selection(message.bot, message.chat.id, state)


@router.callback_query(StateFilter(SurveyStates.language_select), F.data.startswith("lang:"))
async def cb_select_language(callback: CallbackQuery, state: FSMContext, db: AsyncSession) -> None:
    lang = callback.data.removeprefix("lang:")
    interviewer = await _interviewer_for(db, callback.from_user)
    session = await SurveySession.resume(db, CursorState.initial(language=lang), interviewer)
    await callback.answer()
    await _advance_and_render(session, state, callback.bot, callback.message.chat.id)


@router.message(Command("restart"))
async def cmd_restart(message: Message, state: FSMContext, db: AsyncSession) -> None:
    data = await state.get_data()
    if not (data.get("cursor") or {}).get("survey_id"):
        await message.answer(t("no_survey_in_progress", DEFAULT_LANGUAGE))
        return
    interviewer = await _interviewer_for(db, message.from_user)
    session = await _load_session(state, db, interviewer)
    lang = session.language
    await message.answer(
        t("restart_confirm_prompt", lang),
        reply_markup=confirm_kb("restart_yes", "restart_no", yes_text=t("restart_yes", lang), no_text=t("restart_no", lang)),
    )


@router.callback_query(StateFilter(*ALL_SURVEY_STATES), F.data == "restart_ask")
async def cb_restart_ask(callback: CallbackQuery, state: FSMContext, db: AsyncSession) -> None:
    interviewer = await _interviewer_for(db, callback.from_user)
    session = await _load_session(state, db, interviewer)
    lang = session.language
    await callback.answer()
    await callback.message.answer(
        t("restart_confirm_prompt", lang),
        reply_markup=confirm_kb("restart_yes", "restart_no", yes_text=t("restart_yes", lang), no_text=t("restart_no", lang)),
    )


@router.callback_query(F.data == "restart_yes")
async def cb_restart_yes(callback: CallbackQuery, state: FSMContext, db: AsyncSession) -> None:
    interviewer = await _interviewer_for(db, callback.from_user)
    session = await _load_session(state, db, interviewer)
    lang = session.language
    await session.restart()
    await callback.answer(t("restart_started", lang))
    await _prompt_language_selection(callback.bot, callback.message.chat.id, state)


@router.callback_query(F.data == "restart_no")
async def cb_restart_no(callback: CallbackQuery, state: FSMContext, db: AsyncSession) -> None:
    interviewer = await _interviewer_for(db, callback.from_user)
    session = await _load_session(state, db, interviewer)
    await callback.answer(t("restart_continuing", session.language))
    await _send_current(callback.bot, callback.message.chat.id, session, state)


# ---- answering --------------------------------------------------------


@router.callback_query(StateFilter(*ALL_SURVEY_STATES), F.data.startswith("ans:"))
async def cb_answer(callback: CallbackQuery, state: FSMContext, db: AsyncSession) -> None:
    interviewer = await _interviewer_for(db, callback.from_user)
    session = await _load_session(state, db, interviewer)
    lang = session.language
    question = session.current_question()
    if question is None or question.qtype not in (QuestionType.SINGLE_CHOICE, QuestionType.YES_NO):
        await callback.answer(t("stale_step_notice", lang))
        await _send_current(callback.bot, callback.message.chat.id, session, state)
        return

    raw = callback.data.removeprefix("ans:")
    value = {"true": True, "false": False}[raw] if question.qtype == QuestionType.YES_NO else raw

    try:
        await session.answer(question, value)
    except ValidationError as exc:
        await callback.answer(validation_message(exc.code, lang, **exc.params), show_alert=True)
        return
    if question.code == "has_screenshots" and value is True:
        await state.update_data(screenshot_stage="awaiting_file")
    await callback.answer()
    await _advance_and_render(session, state, callback.bot, callback.message.chat.id)


@router.callback_query(StateFilter(*ALL_SURVEY_STATES), F.data.startswith("tgl:"))
async def cb_toggle(callback: CallbackQuery, state: FSMContext, db: AsyncSession) -> None:
    interviewer = await _interviewer_for(db, callback.from_user)
    session = await _load_session(state, db, interviewer)
    lang = session.language
    question = session.current_question()
    if question is None or question.qtype != QuestionType.MULTI_CHOICE:
        await callback.answer(t("stale_step_notice", lang))
        await _send_current(callback.bot, callback.message.chat.id, session, state)
        return

    value = callback.data.removeprefix("tgl:")
    data = await state.get_data()
    draft = set(data.get("multi_draft") or [])
    if value in draft:
        draft.discard(value)
    else:
        draft.add(value)
    await state.update_data(multi_draft=sorted(draft))

    options = await session.resolve_options(question, lang)
    kb = multi_choice_kb(options, selected=draft, lang=lang, allow_back=session.can_go_back())
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=kb)


@router.callback_query(StateFilter(*ALL_SURVEY_STATES), F.data == "mdone")
async def cb_multi_done(callback: CallbackQuery, state: FSMContext, db: AsyncSession) -> None:
    interviewer = await _interviewer_for(db, callback.from_user)
    session = await _load_session(state, db, interviewer)
    lang = session.language
    question = session.current_question()
    if question is None or question.qtype != QuestionType.MULTI_CHOICE:
        await callback.answer(t("stale_step_notice", lang))
        await _send_current(callback.bot, callback.message.chat.id, session, state)
        return

    data = await state.get_data()
    draft = list(data.get("multi_draft") or [])
    try:
        await session.answer(question, draft)
    except ValidationError as exc:
        await callback.answer(validation_message(exc.code, lang, **exc.params), show_alert=True)
        return
    await callback.answer()
    await _advance_and_render(session, state, callback.bot, callback.message.chat.id)


@router.callback_query(StateFilter(*ALL_SURVEY_STATES), F.data == "back")
async def cb_back(callback: CallbackQuery, state: FSMContext, db: AsyncSession) -> None:
    interviewer = await _interviewer_for(db, callback.from_user)
    session = await _load_session(state, db, interviewer)
    session.go_back()
    await callback.answer()
    await _advance_and_render(session, state, callback.bot, callback.message.chat.id)


@router.callback_query(StateFilter(*ALL_SURVEY_STATES), F.data == "skip")
async def cb_skip(callback: CallbackQuery, state: FSMContext, db: AsyncSession) -> None:
    interviewer = await _interviewer_for(db, callback.from_user)
    session = await _load_session(state, db, interviewer)
    lang = session.language
    question = session.current_question()
    if question is None:
        await callback.answer(t("stale_step_notice", lang))
        await _send_current(callback.bot, callback.message.chat.id, session, state)
        return
    try:
        await session.skip(question)
    except ValidationError as exc:
        await callback.answer(validation_message(exc.code, lang, **exc.params), show_alert=True)
        return
    await callback.answer()
    await _advance_and_render(session, state, callback.bot, callback.message.chat.id)


@router.message(StateFilter(*ALL_SURVEY_STATES), F.text)
async def msg_text_answer(message: Message, state: FSMContext, db: AsyncSession) -> None:
    data = await state.get_data()
    if data.get("screenshot_stage") == "awaiting_description":
        await _finish_screenshot_description(message.bot, message.chat.id, db, state, message.from_user, message.text.strip())
        return

    interviewer = await _interviewer_for(db, message.from_user)
    session = await _load_session(state, db, interviewer)
    lang = session.language
    question = session.current_question()
    if question is None:
        await _send_current(message.bot, message.chat.id, session, state)
        return
    if question.qtype not in (
        QuestionType.NUMBER,
        QuestionType.PERCENTAGE,
        QuestionType.CURRENCY,
        QuestionType.TEXT,
    ):
        await message.answer(t("please_use_buttons", lang))
        return

    try:
        await session.answer(question, message.text)
    except ValidationError as exc:
        await message.answer(f"{validation_message(exc.code, lang, **exc.params)}\n\n{t('please_try_again', lang)}")
        return
    await _advance_and_render(session, state, message.bot, message.chat.id)


# ---- review / confirmation ------------------------------------------------


@router.callback_query(StateFilter(*ALL_SURVEY_STATES), F.data == "review_confirm")
async def cb_review_confirm(callback: CallbackQuery, state: FSMContext, db: AsyncSession) -> None:
    interviewer = await _interviewer_for(db, callback.from_user)
    session = await _load_session(state, db, interviewer)
    lang = session.language
    if not session.is_review():
        await callback.answer(t("stale_step_notice", lang))
        await _send_current(callback.bot, callback.message.chat.id, session, state)
        return
    await session.confirm()
    await callback.answer()
    await _advance_and_render(session, state, callback.bot, callback.message.chat.id)


@router.callback_query(StateFilter(*ALL_SURVEY_STATES), F.data == "review_edit")
async def cb_review_edit(callback: CallbackQuery, state: FSMContext, db: AsyncSession) -> None:
    interviewer = await _interviewer_for(db, callback.from_user)
    session = await _load_session(state, db, interviewer)
    lang = session.language
    if not session.is_review() or session.survey.status == SurveyStatus.COMPLETED:
        await callback.answer(t("stale_step_notice", lang))
        await _send_current(callback.bot, callback.message.chat.id, session, state)
        return
    session.go_back()
    await callback.answer()
    await _advance_and_render(session, state, callback.bot, callback.message.chat.id)


# ---- screenshot upload loop (Section 11) --------------------------------


async def _send_screenshot_upload_prompt(bot: Bot, chat_id: int, session: SurveySession, state: FSMContext) -> None:
    lang = session.language
    data = await state.get_data()
    stage = data.get("screenshot_stage", "awaiting_file")

    if stage == "awaiting_platform":
        options = [Option(str(sp.platform_id), sp.platform_other_name or sp.platform.name) for sp in session.survey_platforms]
        await bot.send_message(chat_id, t("screenshot_platform_prompt", lang), reply_markup=screenshot_platform_kb(options, lang))
        return

    if stage == "awaiting_description":
        await bot.send_message(chat_id, t("screenshot_description_prompt", lang), reply_markup=screenshot_description_kb(lang))
        return

    count = session.attachment_count
    progress = t("screenshot_progress_suffix", lang, count=count) if count else ""
    text = t("screenshot_upload_prompt", lang, progress=progress)
    await bot.send_message(chat_id, text, reply_markup=screenshot_awaiting_kb(lang))


async def _handle_screenshot_file(
    message: Message,
    state: FSMContext,
    db: AsyncSession,
    *,
    telegram_file_id: str,
    telegram_file_unique_id: str,
    file_type: AttachmentFileType,
    mime_type: str | None,
) -> None:
    interviewer = await _interviewer_for(db, message.from_user)
    session = await _load_session(state, db, interviewer)
    lang = session.language
    if not session.awaiting_screenshot_upload:
        return  # stray file outside the upload loop; ignore

    data = await state.get_data()
    stage = data.get("screenshot_stage", "awaiting_file")
    if stage != "awaiting_file":
        await message.answer(t("screenshot_finish_previous_step", lang))
        return

    platform_id = session.survey_platforms[0].platform_id if session.platform_count == 1 else None
    attachment = await session.record_screenshot(
        telegram_file_id=telegram_file_id,
        telegram_file_unique_id=telegram_file_unique_id,
        file_type=file_type,
        mime_type=mime_type,
        platform_id=platform_id,
    )
    local_path = await download_attachment(
        message.bot,
        storage_dir=settings.screenshot_storage_dir,
        survey_id=session.survey.id,
        attachment_id=attachment.id,
        telegram_file_id=telegram_file_id,
        mime_type=mime_type,
    )
    if local_path is not None:
        attachment.local_path = local_path
    await _save_session(state, session, clear_draft=False)

    if session.platform_count > 1:
        await state.update_data(screenshot_stage="awaiting_platform", pending_attachment_id=attachment.id)
        options = [Option(str(sp.platform_id), sp.platform_other_name or sp.platform.name) for sp in session.survey_platforms]
        await message.answer(t("screenshot_platform_prompt", lang), reply_markup=screenshot_platform_kb(options, lang))
    else:
        await state.update_data(screenshot_stage="awaiting_description", pending_attachment_id=attachment.id)
        await message.answer(t("screenshot_description_prompt", lang), reply_markup=screenshot_description_kb(lang))


@router.message(StateFilter(SCREENSHOTS_STATE), F.photo)
async def msg_screenshot_photo(message: Message, state: FSMContext, db: AsyncSession) -> None:
    largest = message.photo[-1]
    await _handle_screenshot_file(
        message,
        state,
        db,
        telegram_file_id=largest.file_id,
        telegram_file_unique_id=largest.file_unique_id,
        file_type=AttachmentFileType.SCREENSHOT,
        mime_type=None,
    )


@router.message(StateFilter(SCREENSHOTS_STATE), F.document)
async def msg_screenshot_document(message: Message, state: FSMContext, db: AsyncSession) -> None:
    document = message.document
    mime_type = document.mime_type
    interviewer = await _interviewer_for(db, message.from_user)
    session = await _load_session(state, db, interviewer)
    if not mime_type or not mime_type.startswith("image/"):
        await message.answer(t("screenshot_wrong_type", session.language))
        return
    await _handle_screenshot_file(
        message,
        state,
        db,
        telegram_file_id=document.file_id,
        telegram_file_unique_id=document.file_unique_id,
        file_type=AttachmentFileType.DOCUMENT,
        mime_type=mime_type,
    )


@router.callback_query(StateFilter(*ALL_SURVEY_STATES), F.data.startswith("screenshot_platform:"))
async def cb_screenshot_platform(callback: CallbackQuery, state: FSMContext, db: AsyncSession) -> None:
    data = await state.get_data()
    if data.get("screenshot_stage") != "awaiting_platform":
        await callback.answer()
        return

    interviewer = await _interviewer_for(db, callback.from_user)
    session = await _load_session(state, db, interviewer)
    lang = session.language

    raw = callback.data.removeprefix("screenshot_platform:")
    attachment_id = data.get("pending_attachment_id")
    if attachment_id is not None:
        attachment = await db.get(Attachment, attachment_id)
        if attachment is not None:
            attachment.platform_id = None if raw == "none" else int(raw)

    await state.update_data(screenshot_stage="awaiting_description")
    await callback.answer()
    await callback.message.answer(t("screenshot_description_prompt", lang), reply_markup=screenshot_description_kb(lang))


async def _finish_screenshot_description(
    bot: Bot, chat_id: int, db: AsyncSession, state: FSMContext, user: User, description: str | None
) -> None:
    data = await state.get_data()
    attachment_id = data.get("pending_attachment_id")
    if attachment_id is not None and description:
        attachment = await db.get(Attachment, attachment_id)
        if attachment is not None:
            attachment.description = description

    interviewer = await _interviewer_for(db, user)
    session = await _load_session(state, db, interviewer)
    lang = session.language
    await state.update_data(screenshot_stage="awaiting_file", pending_attachment_id=None)
    await bot.send_message(
        chat_id,
        t("screenshot_saved", lang, count=session.attachment_count),
        reply_markup=screenshot_continue_kb(lang),
    )


@router.callback_query(StateFilter(*ALL_SURVEY_STATES), F.data.startswith("screenshot_desc:"))
async def cb_screenshot_description_preset(callback: CallbackQuery, state: FSMContext, db: AsyncSession) -> None:
    data = await state.get_data()
    if data.get("screenshot_stage") != "awaiting_description":
        await callback.answer()
        return
    interviewer = await _interviewer_for(db, callback.from_user)
    session = await _load_session(state, db, interviewer)
    key = callback.data.removeprefix("screenshot_desc:")
    i18n_key = SCREENSHOT_DESCRIPTION_PRESET_KEYS.get(key)
    label = t(i18n_key, session.language) if i18n_key else key
    await callback.answer()
    await _finish_screenshot_description(callback.bot, callback.message.chat.id, db, state, callback.from_user, label)


@router.callback_query(StateFilter(*ALL_SURVEY_STATES), F.data == "screenshot_desc_skip")
async def cb_screenshot_description_skip(callback: CallbackQuery, state: FSMContext, db: AsyncSession) -> None:
    data = await state.get_data()
    if data.get("screenshot_stage") != "awaiting_description":
        await callback.answer()
        return
    await callback.answer()
    await _finish_screenshot_description(callback.bot, callback.message.chat.id, db, state, callback.from_user, None)


@router.callback_query(StateFilter(*ALL_SURVEY_STATES), F.data == "screenshot_more")
async def cb_screenshot_more(callback: CallbackQuery, state: FSMContext, db: AsyncSession) -> None:
    data = await state.get_data()
    if data.get("screenshot_stage") not in (None, "awaiting_file"):
        await callback.answer()
        return
    await state.update_data(screenshot_stage="awaiting_file")
    await callback.answer()
    interviewer = await _interviewer_for(db, callback.from_user)
    session = await _load_session(state, db, interviewer)
    await _send_current(callback.bot, callback.message.chat.id, session, state)


@router.callback_query(StateFilter(*ALL_SURVEY_STATES), F.data == "screenshot_finish")
async def cb_screenshot_finish(callback: CallbackQuery, state: FSMContext, db: AsyncSession) -> None:
    interviewer = await _interviewer_for(db, callback.from_user)
    session = await _load_session(state, db, interviewer)
    if not session.awaiting_screenshot_upload:
        await callback.answer()
        await _send_current(callback.bot, callback.message.chat.id, session, state)
        return
    await session.finish_screenshot_upload()
    await callback.answer()
    await _advance_and_render(session, state, callback.bot, callback.message.chat.id)


# ---- start new survey (after completion) --------------------------------


@router.callback_query(StateFilter(*ALL_SURVEY_STATES), F.data == "new_survey")
async def cb_start_new_survey(callback: CallbackQuery, state: FSMContext, db: AsyncSession) -> None:
    interviewer = await _interviewer_for(db, callback.from_user)
    session = await _load_session(state, db, interviewer)
    await session.restart()  # no-op on the just-completed survey; it's left untouched
    await callback.answer()
    await _prompt_language_selection(callback.bot, callback.message.chat.id, state)
