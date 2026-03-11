#!/bin/sh
set -e

# Create a dedicated app user with ownership of the edictum database.
# Falls back to POSTGRES_PASSWORD if POSTGRES_APP_PASSWORD is not set,
# preserving backward compatibility.
APP_PASSWORD="${POSTGRES_APP_PASSWORD:-$POSTGRES_PASSWORD}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -v "app_password=$APP_PASSWORD" <<-'EOSQL'
    CREATE USER edictum WITH ENCRYPTED PASSWORD :'app_password';
    GRANT ALL PRIVILEGES ON DATABASE edictum TO edictum;
    ALTER DATABASE edictum OWNER TO edictum;
EOSQL
