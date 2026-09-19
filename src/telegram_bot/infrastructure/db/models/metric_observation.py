import uuid

from sqlalchemy import Enum, ForeignKey, Index, Numeric, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from telegram_bot.domain.enums import AnswerSource, MetricCode
from telegram_bot.infrastructure.db.base import Base
from telegram_bot.infrastructure.db.models.mixins import TimestampMixin


class MetricObservation(TimestampMixin, Base):
    """One sourced value for one of the brief's dual-source "main research
    metrics" (trips, online hours, earnings, ASP, tips, bonus/supply spend,
    commission, distance).

    Deliberately NOT a column on Earnings/WorkingStats/etc.: the brief
    requires that a driver's verbal estimate and a number read off their
    app screen both be kept, never overwriting one with the other ("do not
    mix them"). A row here is one (metric, source) pair, so a verbal and a
    screenshot value for the same metric coexist as two rows instead of
    competing for one column.

    platform_id is nullable: some of these metrics are per-app (weekly
    earnings by platform, commission, ASP, tips, distance) and some are
    driver-level totals (trips per driver, online hours, total weekly
    earnings) — NULL means "across all platforms."
    """

    __tablename__ = "metric_observations"

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("surveys.id", ondelete="CASCADE"), nullable=False, index=True
    )
    platform_id: Mapped[int | None] = mapped_column(
        ForeignKey("platforms.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    metric_code: Mapped[MetricCode] = mapped_column(
        Enum(MetricCode, name="metric_code", native_enum=True), nullable=False
    )
    source: Mapped[AnswerSource] = mapped_column(
        Enum(AnswerSource, name="answer_source", native_enum=True), nullable=False
    )
    value_numeric: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    attachment_id: Mapped[int | None] = mapped_column(
        ForeignKey("attachments.id", ondelete="SET NULL"),
        nullable=True,
        doc="The screenshot this value was read from, when source=screenshot.",
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    survey: Mapped["Survey"] = relationship()
    platform: Mapped["Platform | None"] = relationship()
    attachment: Mapped["Attachment | None"] = relationship()

    __table_args__ = (
        # NULL platform_id means "not distinct" under a plain unique
        # constraint in Postgres (multiple NULLs are allowed), which would
        # let duplicate survey-level rows slip in — so survey-level and
        # per-platform uniqueness are each enforced with their own partial
        # unique index instead of one constraint.
        Index(
            "uq_metric_obs_platform_scoped",
            "survey_id",
            "platform_id",
            "metric_code",
            "source",
            unique=True,
            postgresql_where=text("platform_id IS NOT NULL"),
        ),
        Index(
            "uq_metric_obs_survey_scoped",
            "survey_id",
            "metric_code",
            "source",
            unique=True,
            postgresql_where=text("platform_id IS NULL"),
        ),
    )
