#!/usr/bin/env bash
# Smoke test: snapshot script produces a gzip file that decompresses to a
# valid SQLite DB containing the expected tables.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "[test] running snapshot_db.sh with CARDIGAN_SNAP_DIR=$TMP_DIR"
CARDIGAN_SNAP_DIR="$TMP_DIR" "$REPO_ROOT/scripts/snapshot_db.sh"

SNAP_GZ="$(ls -t "$TMP_DIR"/dashboard-*.db.gz | head -1)"
[ -f "$SNAP_GZ" ] || { echo "FAIL: no snapshot file produced"; exit 1; }

gunzip -k "$SNAP_GZ"
SNAP_DB="${SNAP_GZ%.gz}"

# Must contain expected tables
TABLES="$(python3 -c "
import sqlite3
c = sqlite3.connect('$SNAP_DB').cursor()
print(' '.join(r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")))
")"

for t in jobs session_stats chat_sessions; do
    echo "$TABLES" | grep -qw "$t" || { echo "FAIL: table $t missing"; exit 1; }
done

echo "[test] OK: snapshot produced and contains expected tables"
