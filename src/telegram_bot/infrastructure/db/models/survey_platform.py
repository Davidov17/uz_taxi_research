import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from telegram_bot.infrastructure.db.base import Base
from telegram_bot.infrastructure.db.models.mixins import TimestampMixin


class SurveyPlatform(TimestampMixin, Base):
    """The driver/platform relationship (§2) — one row per platform a driver
    uses (or was asked about) in a given survey. This is the hub that every
    platform-scoped section (earnings, bonuses, payments, ride categories,
    market intelligence) hangs off of via a composite FK on (survey_id,
    platform_id), which is what lets a question set repeat cleanly for
    however many platforms a driver reports.

    Loyalty/points programs (§10) are captured here rather than on
    DriverExperience because they're a property of the driver's relationship
    with *one specific platform*, not the driver's experience overall.
    """

    __tablename__ = "survey_platforms"

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("surveys.id", ondelete="CASCADE"), nullable=False
    )
    platform_id: Mapped[int] = mapped_column(
        ForeignKey("platforms.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    platform_other_name: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        doc="Free-text platform name; populated when platform.is_other = true.",
    )

    works_with_platform: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_exclusive: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, doc="True if the driver works this platform exclusively."
    )
    switch_frequency_id: Mapped[int | None] = mapped_column(
        ForeignKey("switch_frequencies.id", ondelete="RESTRICT"), nullable=True
    )
    experience_with_platform_months: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    has_loyalty_program: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    loyalty_program_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    survey: Mapped["Survey"] = relationship(back_populates="survey_platforms")
    platform: Mapped["Platform"] = relationship(foreign_keys=[platform_id])
    switch_frequency: Mapped["SwitchFrequency | None"] = relationship()

    earnings: Mapped["Earnings | None"] = relationship(
        back_populates="survey_platform", cascade="all, delete-orphan", uselist=False
    )
    bonus: Mapped["Bonus | None"] = relationship(
        back_populates="survey_platform", cascade="all, delete-orphan", uselist=False
    )
    payment: Mapped["Payment | None"] = relationship(
        back_populates="survey_platform", cascade="all, delete-orphan", uselist=False
    )
    ride_category_usages: Mapped[list["RideCategoryUsage"]] = relationship(
        back_populates="survey_platform", cascade="all, delete-orphan"
    )
    market_intelligence: Mapped["MarketIntelligence | None"] = relationship(
        back_populates="survey_platform", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (
        UniqueConstraint("survey_id", "platform_id", name="uq_survey_platforms_survey_id_platform_id"),
        CheckConstraint(
            "experience_with_platform_months IS NULL OR experience_with_platform_months >= 0",
            name="experience_months_non_negative",
        ),
    )
