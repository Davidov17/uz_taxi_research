import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from telegram_bot.infrastructure.db.base import Base
from telegram_bot.infrastructure.db.models.mixins import TimestampMixin


class DriverEmployment(TimestampMixin, Base):
    """Independent / fleet / other (§9). One row per survey."""

    __tablename__ = "driver_employment"

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("surveys.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    driver_type_id: Mapped[int | None] = mapped_column(
        ForeignKey("driver_types.id", ondelete="RESTRICT"),
        nullable=True,
        doc="Legacy choice-based answer. New surveys use employment_text instead.",
    )
    fleet_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    employment_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc=(
            'Free-text answer to "Are you working with a fleet company or are you an '
            'independent driver?" — the current questionnaire asks this as open text; '
            "see application/statistics_service.classify_driver_type for the heuristic "
            "used to keep the fleet-vs-independent dashboard breakdown working from it."
        ),
    )

    survey: Mapped["Survey"] = relationship(back_populates="driver_employment")
    driver_type: Mapped["DriverType | None"] = relationship()
