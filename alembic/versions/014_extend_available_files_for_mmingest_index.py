"""Extend available_files table for mmingest index probing.

Revision ID: 014
Revises: 013
Create Date: 2026-06-03

Adds four columns to `available_files` that the mmingest crawler will
populate during HEAD-probe passes:

  etag          — ETag value from the remote server's Last-Modified/ETag
                  response headers; used for change-detection without
                  re-downloading the full body.
  content_type  — MIME type returned by the server (e.g. video/mp4,
                  text/plain; charset=utf-8).
  last_head_at  — Timestamp of the most recent successful HEAD request;
                  used to schedule re-probes and detect stale entries.
  probe_status  — Lifecycle flag for the probing workflow:
                    'unprobed'  default; never been HEAD-checked
                    'probed'    HEAD completed successfully
                    'error'     HEAD returned non-200 or connection failed
                    'skipped'   intentionally excluded from probing

The `available_files` table is retained as-is for back-compat during the
mmingest transition; it is NOT converted into the mmingest_files table.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("available_files", sa.Column("etag", sa.Text(), nullable=True))
    op.add_column("available_files", sa.Column("content_type", sa.Text(), nullable=True))
    op.add_column("available_files", sa.Column("last_head_at", sa.DateTime(), nullable=True))
    op.add_column(
        "available_files",
        sa.Column("probe_status", sa.Text(), nullable=False, server_default="unprobed"),
    )


def downgrade() -> None:
    op.drop_column("available_files", "probe_status")
    op.drop_column("available_files", "last_head_at")
    op.drop_column("available_files", "content_type")
    op.drop_column("available_files", "etag")
