"""Add validation_result column to jobs table."""

revision = "011"
down_revision = "010"

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column("jobs", sa.Column("validation_result", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("jobs", "validation_result")
