from __future__ import annotations

from telegram_bot.application.survey_session import SurveySession
from telegram_bot.domain.i18n import t
from telegram_bot.domain.questionnaire import QUESTIONS_BY_DISPLAY_NUMBER, SECTIONS_BY_INDEX, Question, localize
from telegram_bot.infrastructure.db.models import Survey


def _option_label(question: Question, value: str | None, lang: str) -> str | None:
    """Look up one of a question's own (already-localized) option labels —
    used by the review screen so its labels never drift out of sync with
    what the live questionnaire displays. Returns None for no value or an
    unrecognized one."""
    if value is None:
        return None
    for opt in (question.options or ()) + (question.extra_options or ()):
        if opt.value == value:
            return localize(opt.label, lang)
    return None


def _option_labels(question: Question, values: list[str], lang: str) -> list[str]:
    return [label for v in values if (label := _option_label(question, v, lang)) is not None]


def question_message(session: SurveySession, question: Question, lang: str) -> str:
    """"Q{n}. {question text}" for the 15 numbered questions — the number
    is part of the displayed text itself, not just a section label (see
    docs/SURVEY_SPECIFICATION.md's UI numbering requirement). The
    unnumbered setup/screenshot steps before Q1 and after Q15 fall back to
    a plain section-title header instead.
    """
    body = localize(question.text, lang)
    if question.display_number is not None:
        text = t("question_number_prefix", lang, n=question.display_number, text=body)
    else:
        section = SECTIONS_BY_INDEX[session.cursor.position.section_index]
        text = f"{localize(section.title, lang)}\n\n{body}"
    if question.help_text:
        text += f"\n\n{localize(question.help_text, lang)}"
    return text


def _kv(label: str, value: str | None, lang: str) -> str:
    shown = value if value not in (None, "") else t("review_not_answered", lang)
    return f"{label}: {shown}"


def _format_answer_for_review(session: SurveySession, question: Question, lang: str) -> str:
    """One question's stored answer, formatted the same way regardless of
    which table(s) it actually lives in — the review screen shouldn't
    have to know that, say, Q8's answer is a bucket on every selected
    platform's Earnings row while Q11's is a set of SurveyAnswerOption
    rows. Returns the localized "not answered" placeholder for anything
    left blank.
    """
    code = question.code
    not_answered = t("review_not_answered", lang)

    if code == "platforms_used":
        names = [sp.platform_other_name or sp.platform.name for sp in session.survey_platforms]
        return ", ".join(names) if names else not_answered

    if code == "switch_frequency":
        sp = session.survey_platforms[0] if session.survey_platforms else None
        freq = sp.switch_frequency if sp else None
        return freq.name if freq else not_answered

    if code == "best_experience":
        if session.survey.best_experience_platform:
            return session.survey.best_experience_platform.name
        if session.survey.best_experience_note:
            return _option_label(question, session.survey.best_experience_note.value, lang) or not_answered
        return not_answered

    if code == "days_per_week":
        ws = session.working_stats
        if ws and ws.days_per_week is not None:
            return f"{ws.days_per_week} {t('review_days_per_week', lang)}"
        return not_answered

    if code == "hours_and_season":
        ws, de = session.working_stats, session.driver_experience
        parts = []
        if ws and ws.hours_per_day_bucket:
            parts.append(_option_label(question, ws.hours_per_day_bucket.value, lang) or ws.hours_per_day_bucket.value)
        if de and de.seasonal_pattern:
            parts.append(_option_label(question, de.seasonal_pattern.value, lang) or de.seasonal_pattern.value)
        return ", ".join(parts) if parts else not_answered

    if code == "main_category":
        de = session.driver_experience
        if de and de.main_category:
            return de.main_category.name
        if "dont_know" in session.answer_options.get("main_category", []):
            return _option_label(question, "dont_know", lang) or not_answered
        return not_answered

    if code in ("trips_per_day_range", "commission_range", "bonus_type", "cash_pct"):
        values = session.answer_options.get(code, [])
        return ", ".join(_option_labels(question, values, lang)) or not_answered

    if code == "earnings":
        range_values = session.answer_options.get("earnings", [])
        labels = _option_labels(question, range_values, lang)
        sp = session.survey_platforms[0] if session.survey_platforms else None
        if sp and sp.earnings and sp.earnings.earnings_basis:
            labels.append(sp.earnings.earnings_basis.name)
        return ", ".join(labels) if labels else not_answered

    if code in ("market_awareness", "payout_methods", "driver_type_loyalty", "driver_motivation"):
        values = session.answer_options.get(code, [])
        labels = _option_labels(question, values, lang)
        return ", ".join(labels) if labels else not_answered

    return not_answered


def format_review(session: SurveySession, lang: str) -> str:
    """The structured review/confirmation screen shown once the driver has
    walked through all 15 questions — see SurveySession.is_review()/
    confirm(). Built directly from persisted data (not the FSM), so it's
    always an accurate reflection of what will actually be saved,
    including any edits made via the Edit/Back button.
    """
    survey = session.survey
    lines = [t("review_title", lang, code=survey.human_code), ""]

    lines.append(f"{t('review_city', lang)}:")
    lines.append(survey.city.name)
    lines.append("")

    lines.append(f"{t('review_platforms', lang)}:")
    for sp in session.survey_platforms:
        lines.append(f"• {sp.platform_other_name or sp.platform.name}")
    lines.append("")

    for n in range(1, 16):
        question = QUESTIONS_BY_DISPLAY_NUMBER[n]
        lines.append(f"Q{n}:")
        lines.append(_format_answer_for_review(session, question, lang))
        lines.append("")

    return "\n".join(lines).rstrip()


def format_completion(survey: Survey, lang: str) -> str:
    """The only thing shown once a survey is finished — no answer summary,
    no edit options (those are only available before confirming, on the
    review screen)."""
    return (
        f"{t('completion_title', lang)}\n\n"
        f"{t('completion_survey_id', lang)}: {survey.human_code}\n\n"
        f"{t('completion_thanks', lang)}"
    )
