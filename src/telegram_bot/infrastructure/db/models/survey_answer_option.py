import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from telegram_bot.infrastructure.db.base import Base
from telegram_bot.infrastructure.db.models.mixins import TimestampMixin


class SurveyAnswerOption(TimestampMixin, Base):
    """One selected option for one of the 15-question flow's closed-option
    questions that have no other natural single-column home (predefined
    ranges/checklists: trips/day range, weekly-earnings range, bonus type,
    market awareness, payout methods, driver type & loyalty, driver
    motivation — see domain/questionnaire.py).

    Deliberately a plain relational child table (survey_id, question_code,
    option_code) — the same pattern already used everywhere else in this
    schema for "many rows per survey" data (SurveyPlatform,
    RideCategoryUsage) — rather than either inventing one near-identical
    table per question, or a JSON array column (Attachment.file_metadata's
    docstring already establishes this schema deliberately does not store
    survey answers as JSON). A single-choice question stores exactly one
    row here; a multi-choice question stores one row per selected option.

    `question_code` matches Question.code in questionnaire.py;
    `option_code` matches that question's Option.value — both are the
    same stable, language-independent strings used everywhere else (never
    a translated label).
    """

    __tablename__ = "survey_answer_options"

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("surveys.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    option_code: Mapped[str] = mapped_column(String(64), nullable=False)

    survey: Mapped["Survey"] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "survey_id", "question_code", "option_code", name="uq_survey_answer_options_survey_question_option"
        ),
    )
