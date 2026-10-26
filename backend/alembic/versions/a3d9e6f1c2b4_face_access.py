"""face samples and allowed people per restricted zone (P34)

Revision ID: a3d9e6f1c2b4
Revises: f2b8c3e1d5a7
Create Date: 2026-10-27 01:30:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3d9e6f1c2b4'
down_revision: Union[str, None] = 'f2b8c3e1d5a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('zones', sa.Column('allowed_user_ids', sa.JSON(), server_default='[]', nullable=False))
    op.create_table('face_samples',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('embedding', sa.LargeBinary(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_face_samples_user_id'), 'face_samples', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_face_samples_user_id'), table_name='face_samples')
    op.drop_table('face_samples')
    op.drop_column('zones', 'allowed_user_ids')
