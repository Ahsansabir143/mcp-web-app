#!/bin/sh
# Run database migrations from the repo root.
# Usage: DATABASE_URL=postgresql+asyncpg://user:pass@host/db ./scripts/migrate.sh
set -e
alembic -c migrations/alembic.ini upgrade head
