"""conversation outcome and duration (P32)

Revision ID: f2b8c3e1d5a7
Revises: e7c1a9d24b10
Create Date: 2026-10-27 00:40:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2b8c3e1d5a7'
down_revision: Union[str, None] = 'e7c1a9d24b10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('conversations', sa.Column('outcome', sa.String(length=16), nullable=True))
    op.add_column('conversations', sa.Column('duration_s', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('conversations', 'duration_s')
    op.drop_column('conversations', 'outcome')
