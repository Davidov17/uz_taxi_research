import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    SmallInteger,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from telegram_bot.infrastructure.db.base import Base
from telegram_bot.infrastructure.db.models.mixins import TimestampMixin


class RideCategoryUsage(TimestampMixin, Base):
    """Ride categories a driver serves on a given platform (§7). Many rows
    per (survey, platform) — a driver can run multiple categories (e.g.
    Economy and Comfort) on the same platform, so this is a proper
    many-rows child rather than a 1:1 extension like Earnings/Bonus/Payment.
    """

    __tablename__ = "ride_category_usages"

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    platform_id: Mapped[int] = mapped_column(SmallInteger, nullable=False, index=True)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("ride_categories.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    trip_share_pct: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    is_main_category: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    category_requirements: Mapped[str | None] = mapped_column(
        Text, nullable=True, doc="Raw qualitative notes on vehicle/driver requirements for this category."
    )

    survey_platform: Mapped["SurveyPlatform"] = relationship(back_populates="ride_category_usages")
    category: Mapped["RideCategory"] = relationship()

    __table_args__ = (
        ForeignKeyConstraint(
            ["survey_id", "platform_id"],
            ["survey_platforms.survey_id", "survey_platforms.platform_id"],
            ondelete="CASCADE",
            name="fk_ride_category_usages_survey_platform",
        ),
        UniqueConstraint(
            "survey_id", "platform_id", "category_id", name="uq_ride_category_usages_survey_platform_category"
        ),
        CheckConstraint(
            "trip_share_pct IS NULL OR trip_share_pct BETWEEN 0 AND 100", name="trip_share_pct_range"
        ),
    )
