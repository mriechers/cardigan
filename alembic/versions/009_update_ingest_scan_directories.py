"""Update ingest scanner to scan from root directory

Revision ID: 009
Revises: 008
Create Date: 2026-03-31

Changes the ingest.directories config from a curated list of known
directories (["/misc/", "/SCC2SRT/", "/wisconsinlife/"]) to scan from
root (["/"]) with recursive subdirectory traversal. This ensures new
directories like /IWP/ are automatically discovered.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import json

revision: str = '009'
down_revision: Union[str, None] = '008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_DIRECTORIES = ["/misc/", "/SCC2SRT/", "/wisconsinlife/"]
NEW_DIRECTORIES = ["/"]


def upgrade() -> None:
    # Update ingest.directories to scan from root
    op.execute(
        sa.text(
            "UPDATE config SET value = :new_val, updated_at = CURRENT_TIMESTAMP "
            "WHERE key = 'ingest.directories'"
        ).bindparams(new_val=json.dumps(NEW_DIRECTORIES))
    )

    # Update scan_time from midnight to 7 AM
    op.execute(
        sa.text(
            "UPDATE config SET value = '07:00', updated_at = CURRENT_TIMESTAMP "
            "WHERE key = 'ingest.scan_time'"
        )
    )


def downgrade() -> None:
    # Restore original curated directory list
    op.execute(
        sa.text(
            "UPDATE config SET value = :old_val, updated_at = CURRENT_TIMESTAMP "
            "WHERE key = 'ingest.directories'"
        ).bindparams(old_val=json.dumps(OLD_DIRECTORIES))
    )

    # Restore midnight scan time
    op.execute(
        sa.text(
            "UPDATE config SET value = '00:00', updated_at = CURRENT_TIMESTAMP "
            "WHERE key = 'ingest.scan_time'"
        )
    )
