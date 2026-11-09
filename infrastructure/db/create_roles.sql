-- Creates the Tillfree database and its roles. Idempotent. Run as a PostgreSQL superuser:
--
--   psql -d postgres -v ON_ERROR_STOP=1 \
--        -v db_name=tillfree -v owner_password=... -v app_password=... \
--        -f infrastructure/db/create_roles.sql
--
-- tillfree_owner  owns the schema; used ONLY to run migrations (and by the test suite,
--                 which needs CREATEDB). Not a superuser, so FORCE ROW LEVEL SECURITY
--                 applies to it too.
-- tillfree_app    used by the API and workers at runtime: DML only, no DDL,
--                 no BYPASSRLS. See docs/architecture/03-multi-tenancy.md §4.
--
-- In production, omit CREATEDB from tillfree_owner and provision through your
-- infrastructure tooling; this script is for local development and CI.

SELECT format('CREATE ROLE tillfree_owner LOGIN PASSWORD %L', :'owner_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tillfree_owner') \gexec
SELECT format('CREATE ROLE tillfree_app LOGIN PASSWORD %L', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tillfree_app') \gexec

ALTER ROLE tillfree_owner NOSUPERUSER NOBYPASSRLS CREATEDB;
ALTER ROLE tillfree_app NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;

SELECT format('CREATE DATABASE %I OWNER tillfree_owner', :'db_name')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db_name') \gexec

\connect :db_name

REVOKE ALL ON DATABASE :"db_name" FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE :"db_name" TO tillfree_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO tillfree_app;

-- Tables and sequences created later by migrations (as tillfree_owner) are usable by the app.
ALTER DEFAULT PRIVILEGES FOR ROLE tillfree_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO tillfree_app;
ALTER DEFAULT PRIVILEGES FOR ROLE tillfree_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO tillfree_app;
-- Objects that already exist (re-running after migrations).
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO tillfree_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO tillfree_app;
