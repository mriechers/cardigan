"""Add mmingest_crawl_runs telemetry table.

Revision ID: 021
Revises: 020
Create Date: 2026-06-29

Creates ``mmingest_crawl_runs`` — one row per delta-walk pass recording when it
started/finished, its terminal status, and the counts from the IndexerRun
summary.  This gives ``GET /api/mmingest/status`` a queryable last-run signal so
a crawl that stalls or fails no longer hides behind an empty index with no
external trace (issue: mmingest index empty on prod, 2026-06-29).

Column notes
------------
started_at         When run_delta_walk recorded the run start (UTC ISO).
finished_at        When the run reached a terminal state; NULL while running.
status             'running'   — a pass is in flight (finished_at IS NULL)
                   'completed' — run_once() returned normally
                   'suppressed'— crawl paused (pause-window RuntimeError)
                   'failed'    — run_once() raised; see ``error``
files_seen         IndexerRun.files_seen (new/changed work items this pass).
files_new          IndexerRun.files_new.
sidecars_fetched   IndexerRun.sidecars_fetched.
sidecars_persisted IndexerRun.sidecars_persisted.
fts_parity_delta   IndexerRun.fts_parity_delta (0 = in sync; NULL = unchecked).
elapsed_seconds    Wall-clock duration of the pass.
error              repr() of the exception for failed runs; NULL otherwise.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "021"
down_revision: Union[str, None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mmingest_crawl_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("files_seen", sa.Integer(), nullable=True),
        sa.Column("files_new", sa.Integer(), nullable=True),
        sa.Column("sidecars_fetched", sa.Integer(), nullable=True),
        sa.Column("sidecars_persisted", sa.Integer(), nullable=True),
        sa.Column("fts_parity_delta", sa.Integer(), nullable=True),
        sa.Column("elapsed_seconds", sa.Float(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )

    op.create_index(
        "idx_mmingest_crawl_runs_started_at",
        "mmingest_crawl_runs",
        ["started_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_mmingest_crawl_runs_started_at", table_name="mmingest_crawl_runs")
    op.drop_table("mmingest_crawl_runs")
