"""Reference/lookup tables.

Each is a small `code` + `name` table rather than a native DB enum, so new
cities, platforms, ride categories, etc. can be added with an INSERT instead
of a migration. `code` is the stable machine key used by application code;
`name` is the human-readable label shown to interviewers and in exports.
"""

from sqlalchemy import Boolean, SmallInteger, String, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from telegram_bot.infrastructure.db.base import Base


class LookupMixin:
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())


class City(LookupMixin, Base):
    __tablename__ = "cities"

    surveys: Mapped[list["Survey"]] = relationship(back_populates="city")


class Platform(LookupMixin, Base):
    """A ride-hailing platform (Yandex Go, Uklon, ...).

    `is_other` marks the single generic "Other" row used when a driver names
    a platform we don't have a dedicated code for yet; the free-text name is
    then captured on the referencing row (e.g. SurveyPlatform.platform_other_name)
    rather than requiring a schema change to onboard a new platform.
    """

    __tablename__ = "platforms"

    is_other: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class RideCategory(LookupMixin, Base):
    __tablename__ = "ride_categories"


class SwitchFrequency(LookupMixin, Base):
    """How often a driver switches between apps, e.g. never/rarely/weekly/daily."""

    __tablename__ = "switch_frequencies"

    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)


class EarningsBasis(LookupMixin, Base):
    """gross / net / unknown."""

    __tablename__ = "earnings_basis"


class DriverType(LookupMixin, Base):
    """independent / fleet / other."""

    __tablename__ = "driver_types"


class PayoutMethod(LookupMixin, Base):
    __tablename__ = "payout_methods"


__all__ = [
    "LookupMixin",
    "City",
    "Platform",
    "RideCategory",
    "SwitchFrequency",
    "EarningsBasis",
    "DriverType",
    "PayoutMethod",
]
