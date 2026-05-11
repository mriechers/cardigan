"""Add app_version column to jobs, session_stats, chat_sessions

Revision ID: 011
Revises: 010
Create Date: 2026-05-04

Adds a nullable app_version TEXT column to the three cost-bearing tables so
that rows can be attributed to the Cardigan version (e.g., "v2.1", "v4.1")
that produced them. Existing rows remain NULL until backfilled.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '011'
down_revision: Union[str, None] = '010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('jobs',           sa.Column('app_version', sa.Text(), nullable=True))
    op.add_column('session_stats',  sa.Column('app_version', sa.Text(), nullable=True))
    op.add_column('chat_sessions',  sa.Column('app_version', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('chat_sessions', 'app_version')
    op.drop_column('session_stats', 'app_version')
    op.drop_column('jobs',          'app_version')
