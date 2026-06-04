"""Add consumer_keys table for API key authentication.

Revision ID: 017
Revises: 016
Create Date: 2026-06-03

Creates `consumer_keys` — stores hashed API keys and their associated
scopes for authenticating mmingest search consumers.

Column notes
------------
key_hash   SHA-256 hex digest of the raw API key.  The raw key is never
           stored; consumers present the raw key and the API hashes it
           for lookup.
label      Human-readable name for the key (e.g. "search-frontend-prod").
scopes     CSV of granted scope strings, e.g.
           "mmingest:read,mmingest:stream".
created_at Populated at INSERT via server_default.
last_used_at Updated by the application on each successful auth; nullable
             until the key has been used at least once.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "consumer_keys",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("scopes", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
    )

    op.create_index("idx_consumer_keys_key_hash", "consumer_keys", ["key_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("idx_consumer_keys_key_hash", table_name="consumer_keys")
    op.drop_table("consumer_keys")
