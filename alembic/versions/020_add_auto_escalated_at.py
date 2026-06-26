"""Add auto_escalated_at column to jobs table

Revision ID: 020
Revises: 019
Create Date: 2026-06-25

Adds the escalate-once marker stamped by the QA-fail auto-escalation gate
(#243). pause_and_suggest(mark_escalated=True) writes this timestamp; without
the column, the first escalation-exhausted PAUSE on a pre-existing DB would
raise OperationalError and turn into a hard FAILED job.

The column was previously declared only in the SQLAlchemy schema (used by
metadata.create_all for fresh DBs). create_all never ALTERs an existing table,
so deployed DBs migrated via `alembic upgrade head` need this revision to gain
the column.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("auto_escalated_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "auto_escalated_at")
