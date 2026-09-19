import uuid

from sqlalchemy import ForeignKeyConstraint, Integer, SmallInteger, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from telegram_bot.infrastructure.db.base import Base
from telegram_bot.infrastructure.db.models.mixins import TimestampMixin


class MarketIntelligence(TimestampMixin, Base):
    """Per-platform market perception (§8), self-reported by the driver.

    `perceived_market_leader` is deliberately NOT here — it's a single
    opinion the driver holds about the whole market, not a fact about one
    platform, so it lives on Survey.perceived_market_leader_platform_id
    instead of being (potentially inconsistently) repeated on every row.
    """

    __tablename__ = "market_intelligence"

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    platform_id: Mapped[int] = mapped_column(SmallInteger, nullable=False, index=True)

    estimated_driver_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True, doc="Legacy numeric answer. New surveys use estimated_driver_count_text instead."
    )
    estimated_driver_count_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc='Free-text answer to "Are you aware of how many drivers work with the X platform?"',
    )
    competitor_rider_discounts_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    demand_promotion_spending_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    survey_platform: Mapped["SurveyPlatform"] = relationship(back_populates="market_intelligence")

    __table_args__ = (
        ForeignKeyConstraint(
            ["survey_id", "platform_id"],
            ["survey_platforms.survey_id", "survey_platforms.platform_id"],
            ondelete="CASCADE",
            name="fk_market_intelligence_survey_platform",
        ),
        UniqueConstraint(
            "survey_id", "platform_id", name="uq_market_intelligence_survey_id_platform_id"
        ),
    )
