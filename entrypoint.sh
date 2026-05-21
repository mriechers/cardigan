#!/bin/sh
set -e

# Ensure writable directories exist
mkdir -p /data/db /data/output /data/transcripts logs

# Run database migrations if this image carries them. The api image bundles
# alembic.ini + alembic/; the worker image only ships alembic.ini and relies
# on api having already migrated the shared DB. Both conditions are required
# so a partially-built image fails fast instead of silently skipping.
# Fails loudly on error: a migration failure means image and DB are out of
# sync (e.g. image built before a new revision was added) and continuing
# would mask schema drift.
if [ -f alembic.ini ] && [ -d alembic ]; then
    echo "[entrypoint] Running database migrations..."
    alembic upgrade head
fi

exec "$@"
