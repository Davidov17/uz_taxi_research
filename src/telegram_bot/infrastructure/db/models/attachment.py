import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, SmallInteger, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from telegram_bot.domain.enums import AttachmentFileType
from telegram_bot.infrastructure.db.base import Base


class Attachment(Base):
    """Screenshots/files (§11), e.g. a driver's stats screen.

    `platform_id` is nullable (some attachments, like a photo of the
    vehicle, aren't tied to one platform) but plain FK — not part of the
    survey_platforms composite key — since an attachment can exist even for
    a platform the driver didn't end up answering the full question set for.
    `file_metadata` is JSONB deliberately: this column holds incidental
    Telegram/file metadata (dimensions, original filename, etc.), not
    survey answers, so keeping it schemaless here does not violate the
    "don't store answers as JSON" rule applied to the rest of the schema.
    """

    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("surveys.id", ondelete="CASCADE"), nullable=False, index=True
    )
    platform_id: Mapped[int | None] = mapped_column(
        ForeignKey("platforms.id", ondelete="RESTRICT"), nullable=True, index=True
    )

    telegram_file_id: Mapped[str] = mapped_column(String(256), nullable=False)
    telegram_file_unique_id: Mapped[str] = mapped_column(String(128), nullable=False)
    file_type: Mapped[AttachmentFileType] = mapped_column(
        Enum(AttachmentFileType, name="attachment_file_type", native_enum=True),
        nullable=False,
        default=AttachmentFileType.SCREENSHOT,
    )
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    local_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    file_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    sequence_number: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        doc="1-based upload order within this survey (\"screenshot 2 of however many\"), not a global id.",
    )
    description: Mapped[str | None] = mapped_column(
        Text, nullable=True, doc="Interviewer's free-text note on what this file shows, e.g. which period."
    )

    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    survey: Mapped["Survey"] = relationship(back_populates="attachments")
    platform: Mapped["Platform | None"] = relationship()

    __table_args__ = (
        UniqueConstraint("survey_id", "sequence_number", name="uq_attachments_survey_id_sequence_number"),
    )
