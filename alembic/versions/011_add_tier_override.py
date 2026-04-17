"""Add tier_override column to jobs table

Revision ID: 011
Revises: 010
Create Date: 2026-04-16

Adds nullable tier_override integer to jobs table to support per-job model tier
selection at queue time. Null means auto-routing; 0=cheapskate, 1=default,
2=big-brain, 3=pinned.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '011'
down_revision: Union[str, None] = '010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'jobs',
        sa.Column('tier_override', sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('jobs', 'tier_override')
