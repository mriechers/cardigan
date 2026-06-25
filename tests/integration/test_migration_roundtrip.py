"""Migration round-trip: `upgrade head` then `downgrade base` must both succeed.

Regression test for #206: migration 011's downgrade dropped a column from
chat_sessions, but 012's downgrade is a no-op (the table is never recreated),
so `alembic downgrade base` failed at 011 with "no such table: chat_sessions".
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _alembic(args: list[str], db_path: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_PATH": db_path}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def test_full_upgrade_then_downgrade_to_base_succeeds():
    """#206: a full upgrade head -> downgrade base round-trip must not fail."""
    fd, db_path = tempfile.mkstemp(suffix="_migration_roundtrip.db")
    os.close(fd)
    try:
        up = _alembic(["upgrade", "head"], db_path)
        assert up.returncode == 0, f"upgrade head failed:\n{up.stdout}\n{up.stderr}"

        down = _alembic(["downgrade", "base"], db_path)
        assert down.returncode == 0, f"downgrade base failed:\n{down.stdout}\n{down.stderr}"
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass
