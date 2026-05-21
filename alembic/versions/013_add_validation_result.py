"""Add validation_result column to jobs table.

Renumbered from '011' during v4.1/sprint-2 rebase onto main: '011' was taken
by add_app_version, '012' by remove_chat_tables (both already merged via Sprint 1).
"""

revision = "013"
down_revision = "012"

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column("jobs", sa.Column("validation_result", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("jobs", "validation_result")
