#!/bin/sh
set -e

# Create a dedicated app user with ownership of the edictum database.
# Falls back to POSTGRES_PASSWORD if POSTGRES_APP_PASSWORD is not set,
# preserving backward compatibility.
APP_PASSWORD="${POSTGRES_APP_PASSWORD:-$POSTGRES_PASSWORD}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE USER edictum WITH PASSWORD '$APP_PASSWORD';
    GRANT ALL PRIVILEGES ON DATABASE $POSTGRES_DB TO edictum;
    ALTER DATABASE $POSTGRES_DB OWNER TO edictum;
EOSQL
