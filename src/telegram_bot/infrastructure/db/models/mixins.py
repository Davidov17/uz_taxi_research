from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column


class TimestampMixin:
    """created_at / updated_at for any table that represents a mutable record.

    updated_at is maintained by the database itself (ON UPDATE trigger-less
    default via SQLAlchemy's `onupdate`, plus a matching server-side default
    on insert) so it stays correct even if a caller forgets to set it.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
