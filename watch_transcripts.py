#!/usr/bin/env python3
"""Watch transcripts folder and auto-queue new files.

Usage:
    python watch_transcripts.py [--once]

Options:
    --once    Queue all unprocessed files once and exit (no watching)
"""

import sys
import time
from pathlib import Path

import httpx

TRANSCRIPTS_DIR = Path("transcripts")
API_BASE = "http://localhost:8000"
POLL_INTERVAL = 5  # seconds


def get_queued_files() -> set:
    """Get set of transcript files already in queue (any status)."""
    queued = set()
    try:
        # Get all jobs from API (paginated response)
        for status in ["pending", "in_progress", "completed", "failed", "paused", "cancelled"]:
            response = httpx.get(f"{API_BASE}/api/queue/", params={"status": status, "page_size": 1000}, timeout=10)
            if response.status_code == 200:
                data = response.json()
                # API returns paginated response: { jobs: [...], total: X, ... }
                jobs = data.get("jobs", [])
                for job in jobs:
                    queued.add(job.get("transcript_file", ""))
    except Exception as e:
        print(f"[Watch] Error fetching queue: {e}")
    return queued


def get_transcript_files() -> list:
    """Get all transcript files in the transcripts folder."""
    files = []
    if TRANSCRIPTS_DIR.exists():
        for f in TRANSCRIPTS_DIR.iterdir():
            if f.is_file() and f.suffix in [".txt", ".srt"] and not f.name.startswith("."):
                files.append(f.name)
    return sorted(files)


def queue_file(filename: str, force: bool = False) -> bool:
    """Queue a transcript file for processing.

    Args:
        filename: Transcript filename to queue
        force: Bypass duplicate detection (default: False)

    Returns:
        True if queued successfully, False otherwise
    """
    # Generate project name from filename
    project_name = Path(filename).stem
    # Clean up common suffixes
    for suffix in ["_ForClaude", "_forclaude", "_transcript"]:
        if project_name.endswith(suffix):
            project_name = project_name[: -len(suffix)]

    try:
        # Build URL with force parameter if needed
        url = f"{API_BASE}/api/queue/"
        if force:
            url += "?force=true"

        response = httpx.post(
            url,
            json={
                "project_name": project_name,
                "transcript_file": filename,
            },
            timeout=10,
        )
        if response.status_code in [200, 201]:
            job = response.json()
            print(f"[Queue] {filename} -> Job {job.get('id')} ({project_name})")
            return True
        elif response.status_code == 409:
            # Duplicate detected
            data = response.json().get("detail", {})
            existing_id = data.get("existing_job_id", "?")
            existing_status = data.get("existing_status", "?")
            print(f"[Skip] {filename} -> Already exists as Job {existing_id} ({existing_status})")
            return False
        else:
            print(f"[Queue] Failed to queue {filename}: {response.status_code}")
            return False
    except Exception as e:
        print(f"[Queue] Error queueing {filename}: {e}")
        return False


def run_once():
    """Queue all unprocessed files once."""
    print(f"[Watch] Scanning {TRANSCRIPTS_DIR} for unprocessed transcripts...")

    queued = get_queued_files()
    files = get_transcript_files()

    new_files = [f for f in files if f not in queued]

    if not new_files:
        print("[Watch] No new files to queue.")
        return

    print(f"[Watch] Found {len(new_files)} new file(s) to queue:")
    for f in new_files:
        queue_file(f)


def watch_loop():
    """Watch for new files continuously."""
    print(f"[Watch] Watching {TRANSCRIPTS_DIR} for new transcripts...")
    print("[Watch] Press Ctrl+C to stop")

    seen_files = get_queued_files() | set(get_transcript_files())

    while True:
        try:
            current_files = set(get_transcript_files())
            new_files = current_files - seen_files

            for f in new_files:
                print(f"[Watch] New file detected: {f}")
                queue_file(f)

            seen_files = seen_files | current_files
            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            print("\n[Watch] Stopped.")
            break


def main():
    if "--once" in sys.argv:
        run_once()
    else:
        # First queue any existing unprocessed files
        run_once()
        print()
        # Then watch for new ones
        watch_loop()


if __name__ == "__main__":
    main()
