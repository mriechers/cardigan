"""Add defer-and-requeue columns to jobs.

Revision ID: 019
Revises: 018
Create Date: 2026-06-12

Supports requeueing a job when a `defer_when_unavailable` backend (e.g. the
local MLX `local-dougie`) is temporarily busy, instead of failing it.

Column notes
------------
retry_after       ISO-8601 UTC string. claim_next_job skips a pending job until
                  now >= retry_after (string-compared, so the format must stay
                  ISO-8601 UTC). NULL = immediately eligible.
defer_count       Number of times this job has been deferred for capacity. Kept
                  separate from retry_count/max_retries — a busy backend is not a
                  failure and must not burn the failure-retry budget.
first_deferred_at ISO-8601 UTC string anchoring the give-up ceiling (wall-clock
                  since the first defer). NULL until the first defer.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("retry_after", sa.Text(), nullable=True))
    op.add_column(
        "jobs", sa.Column("defer_count", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("jobs", sa.Column("first_deferred_at", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "first_deferred_at")
    op.drop_column("jobs", "defer_count")
    op.drop_column("jobs", "retry_after")
