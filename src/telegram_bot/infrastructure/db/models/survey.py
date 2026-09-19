import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from telegram_bot.domain.enums import BestExperienceNote, Language, SurveyStatus
from telegram_bot.infrastructure.db.base import Base
from telegram_bot.infrastructure.db.models.mixins import TimestampMixin


class Survey(TimestampMixin, Base):
    """The root record of one driver interview.

    Holds only survey-identity fields (§1 of the spec) plus two driver-level
    aggregates that don't belong to any single platform and would otherwise
    have to be duplicated across every SurveyPlatform row:
      - total_weekly_earnings: the driver's combined income across all
        platforms/sources for the week (vs. Earnings.weekly_earnings_amount,
        which is scoped to one platform).
      - perceived_market_leader: a single opinion the driver holds, not a
        per-platform fact.
    Everything else (working stats, per-platform economics, bonuses,
    payments, ride categories, market intelligence, employment, and
    driver-experience commentary) lives in its own table — see the sibling
    modules in this package.
    """

    __tablename__ = "surveys"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    human_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    language: Mapped[Language] = mapped_column(
        Enum(Language, name="survey_language", native_enum=True),
        nullable=False,
        default=Language.EN,
        server_default=Language.EN.value,
        doc="Interviewer's chosen language, selected before Question 1 and fixed for the survey's lifetime.",
    )

    interviewer_id: Mapped[int] = mapped_column(
        ForeignKey("interviewers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    city_id: Mapped[int] = mapped_column(
        ForeignKey("cities.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    target_platform_id: Mapped[int | None] = mapped_column(
        ForeignKey("platforms.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    perceived_market_leader_platform_id: Mapped[int | None] = mapped_column(
        ForeignKey("platforms.id", ondelete="RESTRICT"), nullable=True
    )
    best_experience_platform_id: Mapped[int | None] = mapped_column(
        ForeignKey("platforms.id", ondelete="RESTRICT"),
        nullable=True,
        doc="Which app gives the driver the best experience — a driver preference, distinct from perceived_market_leader (biggest, not best).",
    )
    best_experience_note: Mapped[BestExperienceNote | None] = mapped_column(
        Enum(BestExperienceNote, name="best_experience_note", native_enum=True),
        nullable=True,
        doc=(
            "Set instead of best_experience_platform_id when the driver answered "
            '"All are about the same" or "Don\'t know" rather than naming a platform.'
        ),
    )

    status: Mapped[SurveyStatus] = mapped_column(
        Enum(SurveyStatus, name="survey_status", native_enum=True),
        nullable=False,
        default=SurveyStatus.DRAFT,
        index=True,
    )

    survey_datetime: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
        doc="When the interview actually took place (field time, not row-insert time).",
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    total_weekly_earnings_amount: Mapped[float | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    total_weekly_earnings_currency: Mapped[str | None] = mapped_column(
        String(3), nullable=True, default="UZS"
    )
    screenshots_offered: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        doc="Whether the driver said they had statistics screenshots they were willing to share.",
    )
    market_leader_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc=(
            "Free-text answer to \"Who is the market leader?\" (open text in the current "
            "questionnaire). perceived_market_leader_platform_id is kept for older surveys "
            "that answered this as a platform pick rather than free text."
        ),
    )

    interviewer: Mapped["Interviewer"] = relationship(back_populates="surveys")
    city: Mapped["City"] = relationship(back_populates="surveys")
    target_platform: Mapped["Platform | None"] = relationship(foreign_keys=[target_platform_id])
    perceived_market_leader_platform: Mapped["Platform | None"] = relationship(
        foreign_keys=[perceived_market_leader_platform_id]
    )
    best_experience_platform: Mapped["Platform | None"] = relationship(
        foreign_keys=[best_experience_platform_id]
    )

    survey_platforms: Mapped[list["SurveyPlatform"]] = relationship(
        back_populates="survey", cascade="all, delete-orphan"
    )
    working_stats: Mapped["WorkingStats | None"] = relationship(
        back_populates="survey", cascade="all, delete-orphan", uselist=False
    )
    driver_employment: Mapped["DriverEmployment | None"] = relationship(
        back_populates="survey", cascade="all, delete-orphan", uselist=False
    )
    driver_experience: Mapped["DriverExperience | None"] = relationship(
        back_populates="survey", cascade="all, delete-orphan", uselist=False
    )
    attachments: Mapped[list["Attachment"]] = relationship(
        back_populates="survey", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "completed_at IS NULL OR started_at IS NULL OR completed_at >= started_at",
            name="completed_after_started",
        ),
    )
