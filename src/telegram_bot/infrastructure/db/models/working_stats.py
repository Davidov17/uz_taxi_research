import uuid

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Numeric, SmallInteger, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from telegram_bot.domain.enums import HoursPerDayBucket
from telegram_bot.infrastructure.db.base import Base
from telegram_bot.infrastructure.db.models.mixins import TimestampMixin


class WorkingStats(TimestampMixin, Base):
    """Driver-level working pattern (§3). One row per survey — asked once,
    not repeated per platform, since a driver's daily schedule spans
    whichever apps they have running at once."""

    __tablename__ = "working_stats"

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("surveys.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    days_per_week: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    hours_per_day: Mapped[float | None] = mapped_column(
        Numeric(4, 1),
        nullable=True,
        doc=(
            "Numeric hours-per-day, kept for existing AVG/MEDIAN statistics. The current "
            "questionnaire only offers fixed buckets (see hours_per_day_bucket); this is "
            "populated with that bucket's representative midpoint, not a value the driver "
            "typed directly."
        ),
    )
    hours_per_day_bucket: Mapped[HoursPerDayBucket | None] = mapped_column(
        Enum(HoursPerDayBucket, name="hours_per_day_bucket", native_enum=True),
        nullable=True,
        doc="The exact bucket the driver picked — see hours_per_day for a derived numeric value.",
    )
    trips_per_day: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    trips_per_week: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    trips_summary_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc=(
            'Free-text answer to "How many trips on average do you usually complete daily? '
            'Or weekly?" — the current questionnaire deliberately does not force this into '
            "the structured trips_per_day/trips_per_week fields above (kept for older surveys)."
        ),
    )

    survey: Mapped["Survey"] = relationship(back_populates="working_stats")

    __table_args__ = (
        CheckConstraint("days_per_week IS NULL OR days_per_week BETWEEN 0 AND 7", name="days_per_week_range"),
        CheckConstraint("hours_per_day IS NULL OR hours_per_day BETWEEN 0 AND 24", name="hours_per_day_range"),
        CheckConstraint("trips_per_day IS NULL OR trips_per_day >= 0", name="trips_per_day_non_negative"),
        CheckConstraint("trips_per_week IS NULL OR trips_per_week >= 0", name="trips_per_week_non_negative"),
    )
