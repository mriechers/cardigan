#!/bin/sh
set -e

# Run Alembic migrations before starting the API server.
# The DATABASE_PATH env var controls where the SQLite file lives;
# alembic.ini uses a hardcoded relative path, so we override it here.
echo "Running database migrations..."
alembic upgrade head

echo "Starting API server..."
exec "$@"
