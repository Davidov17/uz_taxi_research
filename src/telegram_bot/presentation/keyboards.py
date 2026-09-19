"""Reusable inline keyboard builders — one per question component type.

Callback data is kept to short, consistent prefixes so a single set of
router filters in handlers/survey.py can dispatch every question type:
  lang:<code>               language selection (before Question 1)
  ans:<value>              single-choice / yes-no answer
  tgl:<value>               multi-choice toggle
  mdone                     multi-choice "Done"
  back                      go to the previous question
  skip                      skip the current (optional) question
  restart_ask / restart_yes / restart_no   mid-survey discard-and-restart confirmation
  screenshot_platform:<id|none>            tag an uploaded file's platform
  screenshot_desc:<key> / screenshot_desc_skip   tag an uploaded file's period/description
  screenshot_more / screenshot_finish      continue or end the upload loop
  review_confirm / review_edit             review screen: submit, or go back and edit
  new_survey                start a new survey after a completed submission

Button *labels* are localized (see domain/i18n.t); callback_data values
never are — they're the stable, language-independent wire format the
handlers dispatch on.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from telegram_bot.domain.i18n import LANGUAGE_NAMES, t
from telegram_bot.domain.questionnaire import Option


def language_select_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=f"lang:{code}")] for code, label in LANGUAGE_NAMES.items()]
    )


def _nav_row(lang: str, *, allow_back: bool, allow_skip: bool) -> list[InlineKeyboardButton]:
    row = []
    if allow_back:
        row.append(InlineKeyboardButton(text=t("back", lang), callback_data="back"))
    if allow_skip:
        row.append(InlineKeyboardButton(text=t("skip", lang), callback_data="skip"))
    return row


def single_choice_kb(options: list[Option], lang: str, *, allow_back: bool, allow_skip: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=opt.label, callback_data=f"ans:{opt.value}")] for opt in options]
    nav = _nav_row(lang, allow_back=allow_back, allow_skip=allow_skip)
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def multi_choice_kb(options: list[Option], selected: set[str], lang: str, *, allow_back: bool) -> InlineKeyboardMarkup:
    rows = []
    for opt in options:
        mark = "☑" if opt.value in selected else "☐"
        rows.append([InlineKeyboardButton(text=f"{mark} {opt.label}", callback_data=f"tgl:{opt.value}")])
    done_row = [InlineKeyboardButton(text=t("done", lang), callback_data="mdone")]
    if allow_back:
        done_row.insert(0, InlineKeyboardButton(text=t("back", lang), callback_data="back"))
    rows.append(done_row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def yes_no_kb(lang: str, *, allow_back: bool, allow_skip: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text=t("yes", lang), callback_data="ans:true"),
            InlineKeyboardButton(text=t("no", lang), callback_data="ans:false"),
        ]
    ]
    nav = _nav_row(lang, allow_back=allow_back, allow_skip=allow_skip)
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def text_input_kb(lang: str, *, allow_back: bool, allow_skip: bool) -> InlineKeyboardMarkup | None:
    """Numeric/currency/percentage/free-text questions are answered by
    sending a message, but Back/Skip are still offered as inline buttons
    under the prompt."""
    nav = _nav_row(lang, allow_back=allow_back, allow_skip=allow_skip)
    return InlineKeyboardMarkup(inline_keyboard=[nav]) if nav else None


def screenshot_awaiting_kb(lang: str) -> InlineKeyboardMarkup:
    """Shown while waiting for the interviewer to send a photo/document, in
    case they want to end the upload loop without sending (any)one."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t("finish_uploading", lang), callback_data="screenshot_finish")]]
    )


def screenshot_continue_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("upload_another_screenshot", lang), callback_data="screenshot_more")],
            [InlineKeyboardButton(text=t("finish_uploading", lang), callback_data="screenshot_finish")],
        ]
    )


def screenshot_platform_kb(options: list[Option], lang: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=opt.label, callback_data=f"screenshot_platform:{opt.value}")] for opt in options]
    rows.append([InlineKeyboardButton(text=t("not_platform_specific", lang), callback_data="screenshot_platform:none")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# The brief's screenshot categories: current week, previous week, previous
# 4 weeks/month, or other. Stored as free text in Attachment.description
# (there's no dedicated "period" column) — these buttons are a shortcut for
# typing it out, not a separate structured field. Keys are stable
# (language-independent); labels are resolved per-language at render time.
SCREENSHOT_DESCRIPTION_PRESET_KEYS = {
    "current_week": "screenshot_period_current_week",
    "previous_week": "screenshot_period_previous_week",
    "previous_month": "screenshot_period_previous_month",
    "other": "screenshot_period_other",
}


def screenshot_description_kb(lang: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=t(i18n_key, lang), callback_data=f"screenshot_desc:{key}")]
        for key, i18n_key in SCREENSHOT_DESCRIPTION_PRESET_KEYS.items()
    ]
    rows.append([InlineKeyboardButton(text=t("skip", lang), callback_data="screenshot_desc_skip")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_kb(yes_callback: str, no_callback: str, *, yes_text: str, no_text: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=yes_text, callback_data=yes_callback),
                InlineKeyboardButton(text=no_text, callback_data=no_callback),
            ]
        ]
    )


def start_new_survey_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t("start_new_survey", lang), callback_data="new_survey")]]
    )


def review_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("confirm_submit", lang), callback_data="review_confirm")],
            [InlineKeyboardButton(text=t("edit_back", lang), callback_data="review_edit")],
        ]
    )
