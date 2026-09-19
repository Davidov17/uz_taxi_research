"""simplify questionnaire to 15 flat questions

Revision ID: 7bad4d9abe38
Revises: da9dcac30c59
Create Date: 2026-09-19 09:00:00.000000

Adds the handful of new nullable fields the 15-flat-question redesign
needs, none of which have an existing column to reuse:
  - surveys.best_experience_note: the two non-platform answers to Q3
    ("All are about the same" / "Don't know").
  - driver_experience.main_category_id: Q6's structured single-choice
    answer, survey-level (the legacy ride_category_usages table is
    per-platform, which no longer fits since there is no per-platform
    loop in the interviewer flow any more).
  - survey_answer_options: one small relational child table (survey_id,
    question_code, option_code) backing every other new closed-option
    question that has no other natural single-column home (Q7, Q9's
    range, Q10's detail, Q11, Q13, Q14, Q15) — the same pattern already
    used for SurveyPlatform/RideCategoryUsage, not a JSON blob.

Nothing existing is dropped, renamed, or backfilled — every column and
row from the previous (26-question, per-platform-loop) questionnaire
stays exactly as it was, for historical surveys.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7bad4d9abe38'
down_revision: Union[str, Sequence[str], None] = 'da9dcac30c59'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    sa.Enum("SAME", "DONT_KNOW", name="best_experience_note").create(bind, checkfirst=True)

    op.add_column(
        'surveys',
        sa.Column(
            'best_experience_note',
            sa.Enum('SAME', 'DONT_KNOW', name='best_experience_note', create_type=False),
            nullable=True,
        ),
    )
    op.add_column('driver_experience', sa.Column('main_category_id', sa.SmallInteger(), nullable=True))
    op.create_foreign_key(
        op.f('fk_driver_experience_main_category_id_ride_categories'),
        'driver_experience', 'ride_categories', ['main_category_id'], ['id'], ondelete='RESTRICT',
    )

    op.create_table(
        'survey_answer_options',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('survey_id', sa.UUID(), nullable=False),
        sa.Column('question_code', sa.String(length=64), nullable=False),
        sa.Column('option_code', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(
            ['survey_id'], ['surveys.id'],
            name=op.f('fk_survey_answer_options_survey_id_surveys'), ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_survey_answer_options')),
    )
    op.create_index(
        op.f('ix_survey_answer_options_survey_id'), 'survey_answer_options', ['survey_id'], unique=False
    )
    op.create_index(
        op.f('ix_survey_answer_options_question_code'), 'survey_answer_options', ['question_code'], unique=False
    )
    op.create_unique_constraint(
        'uq_survey_answer_options_survey_question_option',
        'survey_answer_options', ['survey_id', 'question_code', 'option_code'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        'uq_survey_answer_options_survey_question_option', 'survey_answer_options', type_='unique'
    )
    op.drop_index(op.f('ix_survey_answer_options_question_code'), table_name='survey_answer_options')
    op.drop_index(op.f('ix_survey_answer_options_survey_id'), table_name='survey_answer_options')
    op.drop_table('survey_answer_options')

    op.drop_constraint(
        op.f('fk_driver_experience_main_category_id_ride_categories'), 'driver_experience', type_='foreignkey'
    )
    op.drop_column('driver_experience', 'main_category_id')
    op.drop_column('surveys', 'best_experience_note')

    # autogenerate does not emit DROP TYPE for native Postgres enums.
    op.execute("DROP TYPE IF EXISTS best_experience_note")
