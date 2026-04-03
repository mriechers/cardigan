"""Add content_type column to jobs table

Revision ID: 010
Revises: 009
Create Date: 2026-04-02

Adds content_type to jobs table to store detected content classification
('full', 'short', or 'clip') for Shorts-aware pipeline routing.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '010'
down_revision: Union[str, None] = '009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'jobs',
        sa.Column('content_type', sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('jobs', 'content_type')
