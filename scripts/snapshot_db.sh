#!/usr/bin/env bash
# Daily snapshot of the cardigan-v4 dashboard DB.
# Uses SQLite's online-backup API (via Python, since sqlite3 CLI is not
# installed in the container) so it's safe while the app is writing.
#
# Usage:
#   scripts/snapshot_db.sh                # snapshot to default dir
#   CARDIGAN_SNAP_DIR=/elsewhere scripts/snapshot_db.sh

set -euo pipefail

CONTAINER="${CARDIGAN_API_CONTAINER:-cardigan-v4-api-1}"
SNAP_DIR="${CARDIGAN_SNAP_DIR:-$HOME/Developer/pbswi/cardigan-v4/.snapshots}"
DATE_TAG="$(date +%Y%m%d-%H%M%S)"
DEST="$SNAP_DIR/dashboard-$DATE_TAG.db"

mkdir -p "$SNAP_DIR"

docker exec "$CONTAINER" python3 -c "
import sqlite3
src = sqlite3.connect('/data/db/dashboard.db')
dst = sqlite3.connect('/tmp/snapshot.db')
src.backup(dst)
src.close(); dst.close()
"

docker cp "$CONTAINER:/tmp/snapshot.db" "$DEST"
docker exec "$CONTAINER" rm -f /tmp/snapshot.db

gzip -9 "$DEST"

SIZE="$(du -h "${DEST}.gz" | cut -f1)"
echo "[$(date -Iseconds)] snapshot ${DEST}.gz ($SIZE)"
