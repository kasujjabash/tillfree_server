#!/usr/bin/env bash
# Runs once, when the postgres container initialises an empty data directory.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v db_name=tillfree \
  -v owner_password="$TILLFREE_OWNER_PASSWORD" \
  -v app_password="$TILLFREE_APP_PASSWORD" \
  -f /docker-entrypoint-initdb.d/create_roles.sql.tmpl
