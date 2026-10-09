#!/bin/bash
set -e

# Migrations only run once Alembic is configured (HU-02)
if [ -f alembic.ini ]; then
    echo "Running database migrations..."
    alembic upgrade head
    echo "Migrations completed"
else
    echo "alembic.ini not found, skipping migrations"
fi

# Hand over to the container command (uvicorn); without exec the container would exit here
exec "$@"
