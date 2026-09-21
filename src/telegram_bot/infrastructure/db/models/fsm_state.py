from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from telegram_bot.infrastructure.db.base import Base
from telegram_bot.infrastructure.db.models.mixins import TimestampMixin


class FsmState(TimestampMixin, Base):
    """Persisted aiogram FSM state/data, one row per stringified StorageKey
    (see infrastructure/fsm_storage.py's PostgresStorage).

    Unlike survey answers (see SurveyAnswerOption's docstring, which
    explicitly avoids JSON so responses stay queryable), this table is a
    serialization cache for aiogram's own session blob — never queried or
    reported on, so JSONB here doesn't conflict with that convention. It
    exists so an interviewer's exact in-progress question position
    survives a bot process restart, essential on a host that spins the
    process down on inactivity (e.g. a free-tier web host) and a real
    improvement over the previous in-memory-only storage everywhere else.
    """

    __tablename__ = "fsm_states"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    state: Mapped[str | None] = mapped_column(String(255), nullable=True)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
