import uuid

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from telegram_bot.infrastructure.db.base import Base
from telegram_bot.infrastructure.db.models.mixins import TimestampMixin


class Earnings(TimestampMixin, Base):
    """Per-platform economics (§4). Scoped to one (survey, platform) pair via
    a composite FK into survey_platforms — this is what "repeats" the
    earnings question set for every platform a driver reports.

    Note: the driver's combined weekly income across *all* platforms lives
    on Survey.total_weekly_earnings_amount, not here, since it is not a
    per-platform fact and storing it on every row would just invite it to
    drift out of sync across a driver's platforms.
    """

    __tablename__ = "earnings"

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    platform_id: Mapped[int] = mapped_column(SmallInteger, nullable=False, index=True)

    weekly_earnings_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    weekly_earnings_currency: Mapped[str | None] = mapped_column(String(3), nullable=True, default="UZS")
    earnings_basis_id: Mapped[int | None] = mapped_column(
        ForeignKey("earnings_basis.id", ondelete="RESTRICT"), nullable=True
    )
    commission_pct: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)

    survey_platform: Mapped["SurveyPlatform"] = relationship(back_populates="earnings")
    earnings_basis: Mapped["EarningsBasis | None"] = relationship()

    __table_args__ = (
        ForeignKeyConstraint(
            ["survey_id", "platform_id"],
            ["survey_platforms.survey_id", "survey_platforms.platform_id"],
            ondelete="CASCADE",
            name="fk_earnings_survey_platform",
        ),
        UniqueConstraint("survey_id", "platform_id", name="uq_earnings_survey_id_platform_id"),
        CheckConstraint(
            "commission_pct IS NULL OR commission_pct BETWEEN 0 AND 100", name="commission_pct_range"
        ),
        CheckConstraint(
            "weekly_earnings_amount IS NULL OR weekly_earnings_amount >= 0",
            name="weekly_earnings_non_negative",
        ),
    )
