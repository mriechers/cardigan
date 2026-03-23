#!/bin/sh
set -e

# Ensure writable directories exist
mkdir -p /data/db /data/output /data/transcripts logs

# Run database migrations if alembic is configured
if [ -f alembic.ini ]; then
    echo "[entrypoint] Running database migrations..."
    alembic upgrade head || echo "[entrypoint] WARNING: Alembic migration failed (may be OK on first run)"
fi

exec "$@"
