"""Add chat persistence tables

Revision ID: 008
Revises: 007
Create Date: 2026-01-28

Adds tables for chat session persistence with cost tracking:
- chat_sessions: Track conversation sessions per job
- chat_messages: Store message history with cost attribution

This enables:
- Per-session and per-message cost tracking for viability assessment
- Conversation persistence across page refreshes
- Cost comparison between embedded chat and automated phases
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '008'
down_revision: Union[str, None] = '007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create chat_sessions table
    op.create_table(
        'chat_sessions',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('job_id', sa.Integer(), sa.ForeignKey('jobs.id'), nullable=False),
        sa.Column('project_name', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.current_timestamp()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.current_timestamp()),
        sa.Column('total_tokens', sa.Integer(), server_default='0'),
        sa.Column('total_cost', sa.Float(), server_default='0.0'),
        sa.Column('message_count', sa.Integer(), server_default='0'),
        sa.Column('status', sa.Text(), server_default='active'),
        sa.Column('model', sa.Text(), nullable=True),  # Primary model used in session
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_chat_sessions_job_id', 'chat_sessions', ['job_id'])
    op.create_index('ix_chat_sessions_status', 'chat_sessions', ['status'])

    # Create chat_messages table
    op.create_table(
        'chat_messages',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('session_id', sa.Text(), sa.ForeignKey('chat_sessions.id'), nullable=False),
        sa.Column('role', sa.Text(), nullable=False),  # user, assistant, system
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.current_timestamp()),
        sa.Column('tokens', sa.Integer(), nullable=True),  # Token count (assistant messages)
        sa.Column('cost', sa.Float(), nullable=True),  # Cost in USD (assistant messages)
        sa.Column('model', sa.Text(), nullable=True),  # Model used (assistant messages)
        sa.Column('duration_ms', sa.Integer(), nullable=True),  # Response time (assistant messages)
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_chat_messages_session_id', 'chat_messages', ['session_id'])
    op.create_index('ix_chat_messages_created_at', 'chat_messages', ['created_at'])


def downgrade() -> None:
    # 012 (remove chat tables) has a no-op downgrade, so chat_messages /
    # chat_sessions may already be gone when we downgrade through 008. Guard on
    # existence so `alembic downgrade base` doesn't crash (#206). Dropping a
    # table also drops its indexes, so explicit drop_index calls aren't needed.
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table('chat_messages'):
        op.drop_table('chat_messages')
    if insp.has_table('chat_sessions'):
        op.drop_table('chat_sessions')
