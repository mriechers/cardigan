#!/usr/bin/env python3
"""Backfill missing transcript metrics (word_count, duration_minutes) for existing jobs.

This script finds jobs with missing metrics, locates their transcript files,
calculates the metrics, and updates the database.

Usage (from project root):
    python -m scripts.backfill_transcript_metrics [--dry-run]
"""

import sqlite3
import sys
from pathlib import Path

from api.services.utils import calculate_transcript_metrics


def find_transcript_file(media_id: str, project_path: str, transcript_file: str) -> Path | None:
    """Try to find transcript file in various locations."""
    base_dir = Path(__file__).parent.parent

    # Possible locations to check
    locations = [
        # Original location from job record
        base_dir / project_path / transcript_file if project_path and transcript_file else None,
        # Archive folder with various extensions
        base_dir / "transcripts" / "archive" / f"{media_id}.txt",
        base_dir / "transcripts" / "archive" / f"{media_id}.srt",
        base_dir / "transcripts" / "archive" / f"{media_id}_ForClaude.txt",
        # Current transcripts folder
        base_dir / "transcripts" / f"{media_id}.txt",
        base_dir / "transcripts" / f"{media_id}.srt",
        # OUTPUT folder - check for any transcript-like file
        base_dir / "OUTPUT" / media_id / f"{media_id}_ForClaude.txt",
        base_dir / "OUTPUT" / media_id / "transcript.txt",
    ]

    # Also check for partial matches in archive (handles _REV suffixes)
    archive_dir = base_dir / "transcripts" / "archive"
    if archive_dir.exists():
        for f in archive_dir.iterdir():
            if media_id in f.name:
                locations.append(f)

    for loc in locations:
        if loc and loc.exists():
            return loc

    return None


def extract_text_from_srt(content: str) -> str:
    """Extract plain text from SRT subtitle format."""
    lines = content.split("\n")
    text_lines = []

    for line in lines:
        line = line.strip()
        # Skip empty lines, sequence numbers, and timestamps
        if not line:
            continue
        if line.isdigit():
            continue
        if "-->" in line:
            continue
        text_lines.append(line)

    return " ".join(text_lines)


def backfill_metrics(dry_run: bool = False):
    """Main backfill function."""
    db_path = Path(__file__).parent.parent / "dashboard.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Find jobs with missing metrics
    cursor.execute("""
        SELECT id, media_id, project_path, transcript_file, duration_minutes, word_count
        FROM jobs
        WHERE duration_minutes IS NULL OR word_count IS NULL
        ORDER BY id
    """)

    jobs = cursor.fetchall()
    print(f"Found {len(jobs)} jobs with missing metrics\n")

    updated = 0
    skipped = 0
    not_found = []

    for job in jobs:
        job_id = job["id"]
        media_id = job["media_id"] or f"job_{job_id}"
        project_path = job["project_path"]
        transcript_file = job["transcript_file"]

        # Try to find transcript
        transcript_path = find_transcript_file(media_id, project_path, transcript_file)

        if not transcript_path:
            not_found.append((job_id, media_id))
            skipped += 1
            continue

        # Read and process transcript
        try:
            # Try UTF-8 first, fall back to latin-1 for older files
            try:
                content = transcript_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                content = transcript_path.read_text(encoding="latin-1")

            # Handle SRT format
            if transcript_path.suffix.lower() == ".srt":
                content = extract_text_from_srt(content)

            # Calculate metrics
            metrics = calculate_transcript_metrics(content)
            word_count = metrics["word_count"]
            duration_minutes = metrics["estimated_duration_minutes"]

            print(f"Job {job_id:3d} ({media_id}): {word_count:,} words, {duration_minutes:.2f} min")
            print(f"         Source: {transcript_path.name}")

            if not dry_run:
                cursor.execute(
                    """
                    UPDATE jobs
                    SET word_count = ?, duration_minutes = ?
                    WHERE id = ?
                """,
                    (word_count, duration_minutes, job_id),
                )

            updated += 1

        except Exception as e:
            print(f"Job {job_id:3d} ({media_id}): ERROR - {e}")
            skipped += 1

    if not dry_run:
        conn.commit()

    conn.close()

    # Summary
    print(f"\n{'='*60}")
    print(f"SUMMARY {'(DRY RUN)' if dry_run else ''}")
    print(f"{'='*60}")
    print(f"Updated: {updated}")
    print(f"Skipped: {skipped}")

    if not_found:
        print(f"\nTranscripts not found for {len(not_found)} jobs:")
        for job_id, media_id in not_found[:10]:
            print(f"  - Job {job_id}: {media_id}")
        if len(not_found) > 10:
            print(f"  ... and {len(not_found) - 10} more")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("DRY RUN MODE - No changes will be made\n")
    backfill_metrics(dry_run=dry_run)
