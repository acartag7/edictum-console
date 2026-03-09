#!/bin/sh
set -e

# Ensure postgresql+asyncpg:// driver prefix — Railway provides postgresql:// format
if [ -n "$EDICTUM_DATABASE_URL" ]; then
  export EDICTUM_DATABASE_URL=$(echo "$EDICTUM_DATABASE_URL" | sed 's|^postgresql://|postgresql+asyncpg://|;s|^postgres://|postgresql+asyncpg://|')
elif [ -n "$DATABASE_URL" ]; then
  export EDICTUM_DATABASE_URL=$(echo "$DATABASE_URL" | sed 's|^postgresql://|postgresql+asyncpg://|;s|^postgres://|postgresql+asyncpg://|')
fi

# Railway injects REDIS_URL — map to our var if needed
if [ -z "$EDICTUM_REDIS_URL" ] && [ -n "$REDIS_URL" ]; then
  export EDICTUM_REDIS_URL="$REDIS_URL"
fi

echo "Running database migrations..."
# Advisory lock (id=43) prevents concurrent migration runs across instances.
# Uses asyncpg directly (already installed) — psycopg2 is not in deps.
python3 -c "
import asyncio, os, subprocess, sys

async def run_migrations():
    import asyncpg
    url = os.environ.get('EDICTUM_DATABASE_URL', '')
    if not url:
        print('No EDICTUM_DATABASE_URL — running alembic without lock')
        sys.exit(subprocess.run(['alembic', 'upgrade', 'head']).returncode)
    # asyncpg needs postgresql:// not postgresql+asyncpg://
    dsn = url.replace('postgresql+asyncpg://', 'postgresql://')
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute('SELECT pg_advisory_lock(43)')
        print('Migration lock acquired')
        rc = subprocess.run(['alembic', 'upgrade', 'head']).returncode
        await conn.execute('SELECT pg_advisory_unlock(43)')
        print('Migration lock released')
    finally:
        await conn.close()
    sys.exit(rc)

asyncio.run(run_migrations())
"

echo "Starting edictum-console..."
exec uvicorn edictum_server.main:app --host 0.0.0.0 --port "${PORT:-8000}"
