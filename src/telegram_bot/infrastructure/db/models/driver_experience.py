import uuid

from sqlalchemy import Enum, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from telegram_bot.domain.enums import InternetReliability, SeasonalPattern
from telegram_bot.infrastructure.db.base import Base
from telegram_bot.infrastructure.db.models.mixins import TimestampMixin


class DriverExperience(TimestampMixin, Base):
    """Driver-level qualitative experience (§10). One row per survey.

    Loyalty/point programs are intentionally excluded here — they're
    captured per platform on SurveyPlatform, since a loyalty program is a
    property of one platform, not the driver's experience in general.
    """

    __tablename__ = "driver_experience"

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("surveys.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    internet_reliability: Mapped[InternetReliability | None] = mapped_column(
        Enum(InternetReliability, name="internet_reliability", native_enum=True), nullable=True
    )
    improvement_suggestions: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason_to_join_new_platform: Mapped[str | None] = mapped_column(Text, nullable=True)
    seasonal_behavior_notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Legacy free-text seasonality note. New surveys use seasonal_pattern instead.",
    )
    seasonal_pattern: Mapped[SeasonalPattern | None] = mapped_column(
        Enum(SeasonalPattern, name="seasonal_pattern", native_enum=True), nullable=True
    )
    main_category_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc='Legacy free-text answer to "Which category do you drive the most?" — new surveys use main_category_id instead.',
    )
    category_requirements_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc='Legacy free-text answer to "Which ones are the requirements to drive in each category?" — no longer asked.',
    )
    main_category_id: Mapped[int | None] = mapped_column(
        ForeignKey("ride_categories.id", ondelete="RESTRICT"),
        nullable=True,
        doc='Structured answer to "Which category do you drive the most?" (Q6) — survey-level, not per-platform, unlike the legacy ride_category_usages table.',
    )

    survey: Mapped["Survey"] = relationship(back_populates="driver_experience")
    main_category: Mapped["RideCategory | None"] = relationship()
