"""cameras edited on the dashboard keep their name and address when re-seeded

Revision ID: b7e2c4a9d1f3
Revises: a3d9e6f1c2b4
Create Date: 2026-10-27 05:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7e2c4a9d1f3'
down_revision: Union[str, None] = 'a3d9e6f1c2b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('cameras', sa.Column('edited_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('cameras', 'edited_at')
