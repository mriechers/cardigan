"""Remove chat tables.

Revision ID: 009
Revises: 008
Create Date: 2026-04-30

Chat feature removed in v4.1; tables no longer needed.
"""
from typing import Sequence, Union

from alembic import op


revision: str = '009'
down_revision: Union[str, None] = '008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index('ix_chat_messages_created_at', table_name='chat_messages')
    op.drop_index('ix_chat_messages_session_id', table_name='chat_messages')
    op.drop_table('chat_messages')
    op.drop_index('ix_chat_sessions_status', table_name='chat_sessions')
    op.drop_index('ix_chat_sessions_job_id', table_name='chat_sessions')
    op.drop_table('chat_sessions')


def downgrade() -> None:
    # Chat feature removed in v4.1. No downgrade path.
    pass
