"""Pure navigation over the questionnaire graph: no DB, no Telegram.

A `Position` identifies exactly one on-screen question: which section
(1..16), which platform iteration (for the sections that repeat per
platform), and which question within that section's *currently visible*
list. `next_position`/`prev_position` are the whole FSM transition table —
they only need a callback that resolves the answers/context available at a
given position (`answers_view_fn`) and how many platforms are in the loop.

Forward and backward traversal are deliberately written as mirror images of
each other (`_advance_section` / `_retreat_section`) so that walking
forward then back always lands back where you started, and empty sections
(all their questions conditioned away) are transparently skipped in both
directions.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from telegram_bot.domain.questionnaire import (
    PLATFORM_LOOP_SECTIONS,
    SECTIONS_BY_INDEX,
    Question,
    Section,
    visible_questions,
)

AnswersViewFn = Callable[["Position"], dict[str, Any]]

# None when the current questionnaire has no per-platform repeat at all
# (PLATFORM_LOOP_SECTIONS == ()) — _advance_section/_retreat_section below
# treat that as "there is no platform loop to insert/skip."
FIRST_PLATFORM_SECTION = min(PLATFORM_LOOP_SECTIONS) if PLATFORM_LOOP_SECTIONS else None
LAST_PLATFORM_SECTION = max(PLATFORM_LOOP_SECTIONS) if PLATFORM_LOOP_SECTIONS else None
REVIEW_SECTION_INDEX = max(SECTIONS_BY_INDEX)


class ValidationError(ValueError):
    """Carries a stable `code` (+ optional formatting `params`) alongside
    the English default message, so the presentation layer can render a
    localized message (see presentation/i18n.validation_message) without
    this module knowing anything about languages. `str(exc)` alone still
    gives a sensible English message — useful for logs/tests that don't
    care about localization.
    """

    def __init__(self, message: str, *, code: str = "generic", **params: Any) -> None:
        super().__init__(message)
        self.code = code
        self.params = params


@dataclass(frozen=True)
class Position:
    section_index: int
    platform_pos: int | None  # index into the survey's platform list; None outside the platform loop
    question_index: int  # index into visible_questions(section, view) at this position


def start_position() -> Position:
    return Position(section_index=1, platform_pos=None, question_index=0)


def section_for(position: Position) -> Section:
    return SECTIONS_BY_INDEX[position.section_index]


def is_platform_section(section_index: int) -> bool:
    return section_index in PLATFORM_LOOP_SECTIONS


def visible_questions_at(position: Position, answers_view_fn: AnswersViewFn) -> list[Question]:
    section = section_for(position)
    return visible_questions(section, answers_view_fn(position))


def current_question(position: Position, answers_view_fn: AnswersViewFn) -> Question | None:
    qs = visible_questions_at(position, answers_view_fn)
    if position.question_index >= len(qs):
        return None
    return qs[position.question_index]


def _advance_section(position: Position, platform_count: int) -> Position | None:
    idx = position.section_index
    if is_platform_section(idx):
        if idx < LAST_PLATFORM_SECTION:
            return Position(idx + 1, position.platform_pos, 0)
        # idx == LAST_PLATFORM_SECTION: end of this platform's loop pass
        if position.platform_pos is not None and position.platform_pos + 1 < platform_count:
            return Position(FIRST_PLATFORM_SECTION, position.platform_pos + 1, 0)
        return Position(LAST_PLATFORM_SECTION + 1, None, 0)
    if idx >= REVIEW_SECTION_INDEX:
        return None
    next_idx = idx + 1
    if FIRST_PLATFORM_SECTION is not None and next_idx == FIRST_PLATFORM_SECTION:
        if platform_count == 0:
            return Position(LAST_PLATFORM_SECTION + 1, None, 0)
        return Position(FIRST_PLATFORM_SECTION, 0, 0)
    return Position(next_idx, None, 0)


def _retreat_section(position: Position, platform_count: int) -> Position | None:
    idx = position.section_index
    if is_platform_section(idx):
        if idx > FIRST_PLATFORM_SECTION:
            return Position(idx - 1, position.platform_pos, 0)
        # idx == FIRST_PLATFORM_SECTION: start of this platform's loop pass
        if position.platform_pos:  # > 0
            return Position(LAST_PLATFORM_SECTION, position.platform_pos - 1, 0)
        return Position(FIRST_PLATFORM_SECTION - 1, None, 0)
    if idx <= 1:
        return None
    prev_idx = idx - 1
    if LAST_PLATFORM_SECTION is not None and prev_idx == LAST_PLATFORM_SECTION:
        if platform_count == 0:
            return Position(FIRST_PLATFORM_SECTION - 1, None, 0)
        return Position(LAST_PLATFORM_SECTION, platform_count - 1, 0)
    return Position(prev_idx, None, 0)


def next_position(position: Position, answers_view_fn: AnswersViewFn, platform_count: int) -> Position | None:
    """The position right after `position`, skipping any section whose
    questions are all conditioned away. A freshly-landed section is
    returned as-is (its first visible question) — it must NOT be run back
    through the "+1 within section" check, or a section with exactly one
    visible question gets skipped entirely.
    """
    qs = visible_questions_at(position, answers_view_fn)
    if position.question_index + 1 < len(qs):
        return replace(position, question_index=position.question_index + 1)

    candidate = _advance_section(position, platform_count)
    while candidate is not None and candidate.section_index < REVIEW_SECTION_INDEX:
        if visible_questions_at(candidate, answers_view_fn):
            return candidate
        candidate = _advance_section(candidate, platform_count)
    return candidate  # None, or the terminal review section


def prev_position(position: Position, answers_view_fn: AnswersViewFn, platform_count: int) -> Position | None:
    """Mirror of next_position: the position right before `position`."""
    if position.question_index > 0:
        return replace(position, question_index=position.question_index - 1)

    candidate = _retreat_section(position, platform_count)
    while candidate is not None:
        qs = visible_questions_at(candidate, answers_view_fn)
        if qs:
            return replace(candidate, question_index=len(qs) - 1)
        candidate = _retreat_section(candidate, platform_count)
    return None


# ---- Input validation -------------------------------------------------


def parse_number(raw: str, *, min_value: float | None = None, max_value: float | None = None) -> float:
    cleaned = raw.strip().replace(",", "").replace(" ", "")
    if not cleaned:
        raise ValidationError("Please send a number.", code="empty")
    try:
        value = float(cleaned)
    except ValueError as exc:
        raise ValidationError(
            "That doesn't look like a number — please try again.", code="not_a_number"
        ) from exc
    if min_value is not None and value < min_value:
        raise ValidationError(f"Value must be at least {min_value:g}.", code="too_low", min_value=min_value)
    if max_value is not None and value > max_value:
        raise ValidationError(f"Value must be at most {max_value:g}.", code="too_high", max_value=max_value)
    return value


def parse_percentage(raw: str) -> float:
    return parse_number(raw, min_value=0, max_value=100)


def parse_currency(raw: str, *, min_value: float = 0) -> float:
    return parse_number(raw, min_value=min_value)
