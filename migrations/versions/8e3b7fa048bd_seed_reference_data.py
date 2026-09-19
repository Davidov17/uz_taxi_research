"""seed reference data

Revision ID: 8e3b7fa048bd
Revises: c940fd1d2052
Create Date: 2026-09-17 09:54:28.198373

Inserts the initial rows for every lookup table (cities, platforms, ride
categories, switch frequencies, earnings basis, driver types, payout
methods). The values are intentionally inlined here — rather than imported
from telegram_bot.infrastructure.db.seed_data — so this migration keeps
producing the exact same result even if that module's contents change
later; telegram_bot.infrastructure.db.seed.seed_reference_data() is the
place to reach for if you want current defaults applied idempotently
outside of a migration (e.g. in tests).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.sql import table, column

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '8e3b7fa048bd'
down_revision: Union[str, Sequence[str], None] = 'c940fd1d2052'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CITIES = [
    {"code": "tashkent", "name": "Tashkent"},
    {"code": "samarkand", "name": "Samarkand"},
    {"code": "namangan", "name": "Namangan"},
    {"code": "andijan", "name": "Andijan"},
]

PLATFORMS = [
    {"code": "yandex_go", "name": "Yandex Go", "is_other": False},
    {"code": "uklon", "name": "Uklon", "is_other": False},
    {"code": "other", "name": "Other", "is_other": True},
]

RIDE_CATEGORIES = [
    {"code": "economy", "name": "Economy"},
    {"code": "comfort", "name": "Comfort"},
    {"code": "comfort_plus", "name": "Comfort+"},
    {"code": "business", "name": "Business"},
    {"code": "minivan", "name": "Minivan"},
    {"code": "delivery", "name": "Delivery"},
    {"code": "other", "name": "Other"},
]

SWITCH_FREQUENCIES = [
    {"code": "never", "name": "Never switches", "sort_order": 0},
    {"code": "rarely", "name": "Rarely (a few times a month)", "sort_order": 1},
    {"code": "weekly", "name": "Weekly", "sort_order": 2},
    {"code": "daily", "name": "Daily / every shift", "sort_order": 3},
]

EARNINGS_BASIS = [
    {"code": "gross", "name": "Gross (before commission/fees)"},
    {"code": "net", "name": "Net (after commission/fees)"},
    {"code": "unknown", "name": "Unknown / driver unsure"},
]

DRIVER_TYPES = [
    {"code": "independent", "name": "Independent driver"},
    {"code": "fleet", "name": "Fleet driver"},
    {"code": "other", "name": "Other"},
]

PAYOUT_METHODS = [
    {"code": "bank_card", "name": "Bank card"},
    {"code": "cash_office", "name": "Cash at office/partner point"},
    {"code": "e_wallet", "name": "E-wallet"},
    {"code": "bank_transfer", "name": "Bank transfer"},
    {"code": "other", "name": "Other"},
]

_SEED = [
    ("cities", CITIES, ("code", "name")),
    ("platforms", PLATFORMS, ("code", "name", "is_other")),
    ("ride_categories", RIDE_CATEGORIES, ("code", "name")),
    ("switch_frequencies", SWITCH_FREQUENCIES, ("code", "name", "sort_order")),
    ("earnings_basis", EARNINGS_BASIS, ("code", "name")),
    ("driver_types", DRIVER_TYPES, ("code", "name")),
    ("payout_methods", PAYOUT_METHODS, ("code", "name")),
]


def upgrade() -> None:
    for table_name, rows, columns in _SEED:
        t = table(table_name, *(column(c) for c in columns))
        op.bulk_insert(t, rows)


def downgrade() -> None:
    for table_name, rows, _columns in _SEED:
        codes = tuple(row["code"] for row in rows)
        op.execute(
            sa.text(f"DELETE FROM {table_name} WHERE code IN :codes").bindparams(
                sa.bindparam("codes", value=codes, expanding=True)
            )
        )
