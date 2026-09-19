import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from telegram_bot.infrastructure.db.base import Base
from telegram_bot.infrastructure.db.models.mixins import TimestampMixin


class Payment(TimestampMixin, Base):
    """Per-platform payment mix and payout mechanics (§6)."""

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    platform_id: Mapped[int] = mapped_column(SmallInteger, nullable=False, index=True)

    cash_pct: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    digital_pct: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    payout_method_id: Mapped[int | None] = mapped_column(
        ForeignKey("payout_methods.id", ondelete="RESTRICT"),
        nullable=True,
        doc="Legacy choice-based answer. New surveys use payout_notes instead.",
    )
    payout_notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc=(
            "Free-text answer to how the platform pays out / collects cash from drivers — "
            "the current questionnaire deliberately does not force this into predefined choices."
        ),
    )
    early_cashout_available: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    cashout_fee_amount: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    cashout_fee_is_percentage: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, doc="True if cashout_fee_amount is a %, false if a flat amount."
    )

    survey_platform: Mapped["SurveyPlatform"] = relationship(back_populates="payment")
    payout_method: Mapped["PayoutMethod | None"] = relationship()

    __table_args__ = (
        ForeignKeyConstraint(
            ["survey_id", "platform_id"],
            ["survey_platforms.survey_id", "survey_platforms.platform_id"],
            ondelete="CASCADE",
            name="fk_payments_survey_platform",
        ),
        UniqueConstraint("survey_id", "platform_id", name="uq_payments_survey_id_platform_id"),
        CheckConstraint("cash_pct IS NULL OR cash_pct BETWEEN 0 AND 100", name="cash_pct_range"),
        CheckConstraint("digital_pct IS NULL OR digital_pct BETWEEN 0 AND 100", name="digital_pct_range"),
        CheckConstraint(
            "cash_pct IS NULL OR digital_pct IS NULL OR cash_pct + digital_pct <= 100",
            name="cash_plus_digital_pct_at_most_100",
        ),
        CheckConstraint(
            "cashout_fee_amount IS NULL OR cashout_fee_amount >= 0", name="cashout_fee_non_negative"
        ),
    )
