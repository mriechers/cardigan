"""Add media-job columns for audio upload mode.

Revision ID: 022
Revises: 021
Create Date: 2026-07-15

Audio upload mode lets editors submit audio/video instead of a finished
transcript. Such jobs carry a prepended ``transcription`` phase and pause in
the new ``awaiting_review`` status (plain Text column — no CHECK constraint)
until the editor approves the corrected transcript.

Column notes
------------
job_type    'transcript' — classic flow, transcript_file supplied at creation.
            'media'      — audio upload flow; transcript_file stays '' until
                           the transcript review is approved.
media_file  Extracted-audio filename relative to MEDIA_DIR; NULL for
            transcript jobs.
intake      JSON from the upload form: {"speakers": [...], "context_terms":
            [...], "glossary_terms_added": int, "language": "en"}. Feeds the
            WhisperX initial_prompt and pre-fills the review speaker map.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "022"
down_revision: Union[str, None] = "021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("job_type", sa.Text(), nullable=False, server_default="transcript"))
    op.add_column("jobs", sa.Column("media_file", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("intake", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "intake")
    op.drop_column("jobs", "media_file")
    op.drop_column("jobs", "job_type")
