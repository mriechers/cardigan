"""Add mmingest_audit_log table.

Revision ID: 018
Revises: 017
Create Date: 2026-06-05

Creates ``mmingest_audit_log`` — one row per ``/api/mmingest/*`` request,
recording who called it, what path, which media_id (if any), when, and whether
the call was allowed, denied, or made with the shared key.

Column notes
------------
consumer_id  FK to consumer_keys.id; NULL means the caller used the
             shared CARDIGAN_API_KEY (not a per-consumer key).
path         Full request path at time of the call.
media_id     Extracted from the path for asset endpoints; NULL otherwise.
ts           Populated at INSERT via server_default; can be overridden by
             the application for accurate timestamps.
outcome      'allowed'    — consumer key authenticated + scope passed
             'denied'     — consumer key authenticated but scope rejected
             'shared_key' — caller presented the CARDIGAN_API_KEY

Also adds an ``active`` column to ``consumer_keys`` to support soft-deletion
without breaking FK relationships in this table.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add ``active`` column to consumer_keys so revocation doesn't break FKs.
    op.add_column(
        "consumer_keys",
        sa.Column("active", sa.Integer(), nullable=False, server_default="1"),
    )

    op.create_table(
        "mmingest_audit_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("consumer_id", sa.Integer(), sa.ForeignKey("consumer_keys.id"), nullable=True),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("media_id", sa.Text(), nullable=True),
        sa.Column(
            "ts",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column("outcome", sa.Text(), nullable=False),
    )

    op.create_index(
        "idx_mmingest_audit_log_consumer_id",
        "mmingest_audit_log",
        ["consumer_id"],
    )
    op.create_index(
        "idx_mmingest_audit_log_ts",
        "mmingest_audit_log",
        ["ts"],
    )


def downgrade() -> None:
    op.drop_index("idx_mmingest_audit_log_ts", table_name="mmingest_audit_log")
    op.drop_index("idx_mmingest_audit_log_consumer_id", table_name="mmingest_audit_log")
    op.drop_table("mmingest_audit_log")
    op.drop_column("consumer_keys", "active")
