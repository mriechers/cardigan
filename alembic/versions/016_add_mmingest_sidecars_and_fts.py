"""Add mmingest_sidecars table and FTS5 full-text search index.

Revision ID: 016
Revises: 015
Create Date: 2026-06-03

Creates two objects:

1. `mmingest_sidecars` — stores fetched sidecar content (SRT/SCC bodies)
   linked to a mmingest_files row.

2. `mmingest_sidecars_fts` — FTS5 virtual table in external-content mode
   (content=mmingest_sidecars).  body_text is stored once in the real
   table; the FTS shadow tables hold only the inverted index.

   Unindexed columns (media_id, prefix, show) are carried alongside
   ranked hits for display/filtering without joining back to the base
   table.

FTS5 virtual tables and their sync triggers must go through op.execute()
because SQLAlchemy/Alembic does not model them.

Sync triggers
-------------
Three AFTER triggers on mmingest_sidecars keep the FTS index in sync:

  trg_mmingest_sidecars_fts_insert  — on INSERT: insert into FTS
  trg_mmingest_sidecars_fts_delete  — on DELETE: delete from FTS using
                                       the special 'delete' command row
  trg_mmingest_sidecars_fts_update  — on UPDATE: delete old + insert new

The downgrade drops triggers, FTS table, and base table in reverse order.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- mmingest_sidecars base table ---
    op.create_table(
        "mmingest_sidecars",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "file_id",
            sa.Integer(),
            sa.ForeignKey("mmingest_files.id"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),  # 'srt' | 'scc'
        sa.Column("bytes", sa.Integer(), nullable=True),
        sa.Column(
            "fetched_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column("body_text", sa.Text(), nullable=True),
    )

    op.create_index("idx_mmingest_sidecars_file_id", "mmingest_sidecars", ["file_id"])
    op.create_index("idx_mmingest_sidecars_kind", "mmingest_sidecars", ["kind"])

    # --- FTS5 virtual table (external-content mode) ---
    # SQLAlchemy/Alembic cannot model virtual tables; use op.execute() throughout.
    #
    # content=mmingest_sidecars means SQLite reads body_text from the real
    # table for snippet/highlight/bm25 — it does NOT duplicate the text in
    # the FTS shadow tables.  content_rowid=id maps FTS rowids to the PK.
    op.execute(
        """
        CREATE VIRTUAL TABLE mmingest_sidecars_fts
        USING fts5(
            body_text,
            media_id   UNINDEXED,
            prefix     UNINDEXED,
            show       UNINDEXED,
            content    = 'mmingest_sidecars',
            content_rowid = 'id'
        )
        """
    )

    # --- FTS sync triggers ---
    # AFTER INSERT: index the new row
    op.execute(
        """
        CREATE TRIGGER trg_mmingest_sidecars_fts_insert
        AFTER INSERT ON mmingest_sidecars BEGIN
            INSERT INTO mmingest_sidecars_fts(
                rowid, body_text, media_id, prefix, show
            )
            SELECT
                new.id,
                new.body_text,
                mf.media_id,
                mf.prefix,
                mf.show_name
            FROM mmingest_files mf
            WHERE mf.id = new.file_id;
        END
        """
    )

    # AFTER DELETE: remove the old row from the FTS index.
    # FTS5 delete uses a sentinel row where the first column is the integer -1.
    op.execute(
        """
        CREATE TRIGGER trg_mmingest_sidecars_fts_delete
        AFTER DELETE ON mmingest_sidecars BEGIN
            INSERT INTO mmingest_sidecars_fts(
                mmingest_sidecars_fts, rowid, body_text, media_id, prefix, show
            ) VALUES (
                'delete',
                old.id,
                old.body_text,
                (SELECT media_id FROM mmingest_files WHERE id = old.file_id),
                (SELECT prefix   FROM mmingest_files WHERE id = old.file_id),
                (SELECT show_name FROM mmingest_files WHERE id = old.file_id)
            );
        END
        """
    )

    # AFTER UPDATE: delete old entry, insert new entry.
    op.execute(
        """
        CREATE TRIGGER trg_mmingest_sidecars_fts_update
        AFTER UPDATE ON mmingest_sidecars BEGIN
            INSERT INTO mmingest_sidecars_fts(
                mmingest_sidecars_fts, rowid, body_text, media_id, prefix, show
            ) VALUES (
                'delete',
                old.id,
                old.body_text,
                (SELECT media_id  FROM mmingest_files WHERE id = old.file_id),
                (SELECT prefix    FROM mmingest_files WHERE id = old.file_id),
                (SELECT show_name FROM mmingest_files WHERE id = old.file_id)
            );
            INSERT INTO mmingest_sidecars_fts(
                rowid, body_text, media_id, prefix, show
            )
            SELECT
                new.id,
                new.body_text,
                mf.media_id,
                mf.prefix,
                mf.show_name
            FROM mmingest_files mf
            WHERE mf.id = new.file_id;
        END
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_mmingest_sidecars_fts_update")
    op.execute("DROP TRIGGER IF EXISTS trg_mmingest_sidecars_fts_delete")
    op.execute("DROP TRIGGER IF EXISTS trg_mmingest_sidecars_fts_insert")
    op.execute("DROP TABLE IF EXISTS mmingest_sidecars_fts")
    op.drop_index("idx_mmingest_sidecars_kind", table_name="mmingest_sidecars")
    op.drop_index("idx_mmingest_sidecars_file_id", table_name="mmingest_sidecars")
    op.drop_table("mmingest_sidecars")
