"""conversations with Friday (P31)

Revision ID: e7c1a9d24b10
Revises: d493bd538d3e
Create Date: 2026-10-27 00:05:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7c1a9d24b10'
down_revision: Union[str, None] = 'd493bd538d3e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('conversations',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('channel', sa.String(length=8), nullable=False),
    sa.Column('person', sa.String(length=120), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('call_id', sa.Integer(), nullable=True),
    sa.Column('incident_id', sa.Integer(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('recording_path', sa.String(length=255), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['call_id'], ['calls.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['incident_id'], ['incidents.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('call_id')
    )
    op.create_index(op.f('ix_conversations_started_at'), 'conversations', ['started_at'], unique=False)
    op.create_table('conversation_turns',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('conversation_id', sa.Integer(), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('speaker', sa.String(length=8), nullable=False),
    sa.Column('text', sa.String(length=1000), nullable=False),
    sa.Column('action', sa.String(length=32), nullable=True),
    sa.Column('audio_path', sa.String(length=255), nullable=True),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_conversation_turns_conversation_id'), 'conversation_turns', ['conversation_id'],
                    unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_conversation_turns_conversation_id'), table_name='conversation_turns')
    op.drop_table('conversation_turns')
    op.drop_index(op.f('ix_conversations_started_at'), table_name='conversations')
    op.drop_table('conversations')
