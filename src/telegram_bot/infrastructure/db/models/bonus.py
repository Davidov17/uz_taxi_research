import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKeyConstraint,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from telegram_bot.domain.enums import BonusPeriod
from telegram_bot.infrastructure.db.base import Base
from telegram_bot.infrastructure.db.models.mixins import TimestampMixin


class Bonus(TimestampMixin, Base):
    """Per-platform bonus structure (§5). `description` preserves the
    driver's own qualitative explanation verbatim, in addition to the
    structured threshold/value fields."""

    __tablename__ = "bonuses"

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    platform_id: Mapped[int] = mapped_column(SmallInteger, nullable=False, index=True)

    receives_bonuses: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    bonus_period: Mapped[BonusPeriod | None] = mapped_column(
        Enum(BonusPeriod, name="bonus_period", native_enum=True), nullable=True
    )
    required_trips: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    bonus_value_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    bonus_value_currency: Mapped[str | None] = mapped_column(String(3), nullable=True, default="UZS")
    description: Mapped[str | None] = mapped_column(
        Text, nullable=True, doc="Raw, verbatim qualitative answer describing the bonus."
    )

    survey_platform: Mapped["SurveyPlatform"] = relationship(back_populates="bonus")

    __table_args__ = (
        ForeignKeyConstraint(
            ["survey_id", "platform_id"],
            ["survey_platforms.survey_id", "survey_platforms.platform_id"],
            ondelete="CASCADE",
            name="fk_bonuses_survey_platform",
        ),
        UniqueConstraint("survey_id", "platform_id", name="uq_bonuses_survey_id_platform_id"),
        CheckConstraint("required_trips IS NULL OR required_trips >= 0", name="required_trips_non_negative"),
        CheckConstraint(
            "bonus_value_amount IS NULL OR bonus_value_amount >= 0", name="bonus_value_non_negative"
        ),
    )
