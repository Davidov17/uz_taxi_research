"""questionnaire replacement: language + new open-text/structured fields

Revision ID: da9dcac30c59
Revises: 27e4b2cb7328
Create Date: 2026-09-18 12:00:00.000000

Adds:
  - surveys.language (native enum, NOT NULL default 'en') — multilingual
    support; every existing row backfills to 'en' via server_default.
  - A handful of nullable text/enum columns for questions the new
    questionnaire asks as open text or fixed buckets where the old
    questionnaire used a different shape (numeric, single-choice, or a
    lookup FK). The old columns are kept as-is for historical surveys —
    nothing is dropped, nothing existing is renamed.
  - Two new switch_frequency lookup rows are NOT enough on their own
    (lookup tables are seeded by infrastructure/db/seed.py's idempotent
    upsert, not by migrations) — the new "how often do you switch"
    options are added there, and this migration flips the four old
    switch_frequency rows to is_active=false so they stop being offered
    to new surveys while old surveys keep a valid foreign key.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import column, table


# revision identifiers, used by Alembic.
revision: str = 'da9dcac30c59'
down_revision: Union[str, Sequence[str], None] = '27e4b2cb7328'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NEW_ENUMS = {
    "survey_language": ("EN", "RU", "UZ"),
    "hours_per_day_bucket": ("H1_2", "H3_4", "H5_6", "H7_8", "H9_10", "H11_12", "H12_PLUS"),
    "seasonal_pattern": ("SAME_YEAR_ROUND", "MORE_IN_SUMMER", "MORE_IN_WINTER"),
}

NEW_SWITCH_FREQUENCIES = [
    {"code": "never_same_app", "name": "Never, I always use the same app", "sort_order": 10},
    {"code": "sometimes_pick_hours", "name": "Sometimes (e.g. pick hours)", "sort_order": 11},
    {"code": "once_a_day", "name": "Once a day", "sort_order": 12},
    {"code": "few_times_a_day", "name": "A few times a day", "sort_order": 13},
]


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    for name, labels in NEW_ENUMS.items():
        sa.Enum(*labels, name=name).create(bind, checkfirst=True)

    op.add_column(
        'surveys',
        sa.Column(
            'language',
            sa.Enum('EN', 'RU', 'UZ', name='survey_language', create_type=False),
            nullable=False,
            server_default='EN',
        ),
    )
    op.add_column('surveys', sa.Column('market_leader_text', sa.Text(), nullable=True))

    op.add_column(
        'working_stats',
        sa.Column(
            'hours_per_day_bucket',
            sa.Enum('H1_2', 'H3_4', 'H5_6', 'H7_8', 'H9_10', 'H11_12', 'H12_PLUS', name='hours_per_day_bucket', create_type=False),
            nullable=True,
        ),
    )
    op.add_column('working_stats', sa.Column('trips_summary_text', sa.Text(), nullable=True))

    op.add_column(
        'driver_experience',
        sa.Column(
            'seasonal_pattern',
            sa.Enum('SAME_YEAR_ROUND', 'MORE_IN_SUMMER', 'MORE_IN_WINTER', name='seasonal_pattern', create_type=False),
            nullable=True,
        ),
    )
    op.add_column('driver_experience', sa.Column('main_category_text', sa.Text(), nullable=True))
    op.add_column('driver_experience', sa.Column('category_requirements_text', sa.Text(), nullable=True))

    op.add_column('driver_employment', sa.Column('employment_text', sa.Text(), nullable=True))
    op.add_column('market_intelligence', sa.Column('estimated_driver_count_text', sa.Text(), nullable=True))
    op.add_column('payments', sa.Column('payout_notes', sa.Text(), nullable=True))

    switch_frequencies_t = table("switch_frequencies", column("code"), column("name"), column("sort_order"))
    op.bulk_insert(switch_frequencies_t, NEW_SWITCH_FREQUENCIES)
    op.execute(
        "UPDATE switch_frequencies SET is_active = false "
        "WHERE code IN ('never', 'rarely', 'weekly', 'daily')"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        "UPDATE switch_frequencies SET is_active = true "
        "WHERE code IN ('never', 'rarely', 'weekly', 'daily')"
    )
    op.execute(
        sa.text("DELETE FROM switch_frequencies WHERE code IN :codes").bindparams(
            sa.bindparam("codes", value=tuple(r["code"] for r in NEW_SWITCH_FREQUENCIES), expanding=True)
        )
    )

    op.drop_column('payments', 'payout_notes')
    op.drop_column('market_intelligence', 'estimated_driver_count_text')
    op.drop_column('driver_employment', 'employment_text')

    op.drop_column('driver_experience', 'category_requirements_text')
    op.drop_column('driver_experience', 'main_category_text')
    op.drop_column('driver_experience', 'seasonal_pattern')

    op.drop_column('working_stats', 'trips_summary_text')
    op.drop_column('working_stats', 'hours_per_day_bucket')

    op.drop_column('surveys', 'market_leader_text')
    op.drop_column('surveys', 'language')

    # autogenerate does not emit DROP TYPE for native Postgres enums.
    for enum_name in NEW_ENUMS:
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
