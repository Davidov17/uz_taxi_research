"""rename survey_status in_progress to draft

Revision ID: 27e4b2cb7328
Revises: 681abea3b826
Create Date: 2026-09-17 11:17:34.267865

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '27e4b2cb7328'
down_revision: Union[str, Sequence[str], None] = '681abea3b826'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE survey_status RENAME VALUE 'IN_PROGRESS' TO 'DRAFT'")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER TYPE survey_status RENAME VALUE 'DRAFT' TO 'IN_PROGRESS'")
