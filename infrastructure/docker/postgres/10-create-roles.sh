#!/usr/bin/env bash
# Runs once, when the postgres container initialises an empty data directory.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v db_name=tilltree \
  -v owner_password="$TILLTREE_OWNER_PASSWORD" \
  -v app_password="$TILLTREE_APP_PASSWORD" \
  -f /docker-entrypoint-initdb.d/create_roles.sql.tmpl
