"""Add mmingest_files master asset table.

Revision ID: 015
Revises: 014
Create Date: 2026-06-03

Creates `mmingest_files` — the canonical record for every asset (MP4,
SRT, SCC, images) discovered on the mmingest server.  This is the
foundation table for the mmingest search-index feature (Sprint 1A).

The existing `available_files` table is intentionally left in place for
back-compat during the transition; no data is migrated here.

Column notes
------------
prefix_category  enum-like text: 'broadcast' | 'non-broadcast' | 'unknown'
                 Derived from the URL prefix by the crawler.
status           workflow states: new | queued | indexed | no_match |
                 ignored | missing | error
hd               INTEGER treated as boolean (1=HD, 0=SD, NULL=unknown)
revision_date    ISO date string extracted from the filename, e.g. "2024-01"
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mmingest_files",

        # Primary key
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),

        # File location
        sa.Column("remote_url", sa.Text(), nullable=False, unique=True),
        sa.Column("directory_path", sa.Text(), nullable=True),
        sa.Column("filename", sa.Text(), nullable=False),

        # Parsed metadata — common fields
        sa.Column("media_id", sa.Text(), nullable=True),
        sa.Column("prefix", sa.Text(), nullable=True),
        sa.Column("prefix_category", sa.Text(), nullable=False, server_default="unknown"),
        sa.Column("show_name", sa.Text(), nullable=True),
        sa.Column("season", sa.Text(), nullable=True),
        sa.Column("episode", sa.Text(), nullable=True),
        sa.Column("hd", sa.Integer(), nullable=True),  # boolean: 1=HD, 0=SD
        sa.Column("revision_date", sa.Text(), nullable=True),

        # File type and size
        sa.Column("file_type", sa.Text(), nullable=False),  # mp4 | srt | scc | image | other
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),

        # Remote server HTTP metadata
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("content_type", sa.Text(), nullable=True),
        sa.Column("remote_modified_at", sa.DateTime(), nullable=True),

        # Tracking timestamps
        sa.Column("first_seen_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.current_timestamp()),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.current_timestamp()),

        # Workflow status
        sa.Column("status", sa.Text(), nullable=False, server_default="new"),

        # Linking
        sa.Column("airtable_record_id", sa.Text(), nullable=True),
    )

    # Indexes for common crawler + search queries
    op.create_index("idx_mmingest_files_media_id", "mmingest_files", ["media_id"])
    op.create_index("idx_mmingest_files_file_type", "mmingest_files", ["file_type"])
    op.create_index("idx_mmingest_files_status", "mmingest_files", ["status"])
    op.create_index("idx_mmingest_files_prefix", "mmingest_files", ["prefix"])
    op.create_index("idx_mmingest_files_first_seen", "mmingest_files", ["first_seen_at"])


def downgrade() -> None:
    op.drop_index("idx_mmingest_files_first_seen", table_name="mmingest_files")
    op.drop_index("idx_mmingest_files_prefix", table_name="mmingest_files")
    op.drop_index("idx_mmingest_files_status", table_name="mmingest_files")
    op.drop_index("idx_mmingest_files_file_type", table_name="mmingest_files")
    op.drop_index("idx_mmingest_files_media_id", table_name="mmingest_files")
    op.drop_table("mmingest_files")
