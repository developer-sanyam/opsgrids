-- Idempotent setup for the sales-demo database, `opsgrids_demo`. Run by `make demo-db`, against
-- the compose postgres started by `make infra`. Safe to run any number of times: every statement
-- below is guarded, and nothing here drops or truncates anything. Same shape as `e2e-db.sql`;
-- a second database so the demo never touches the shared `opsgrids` (or `opsgrids_wt`).
--
-- init-db.sql only ever runs against the database the postgres entrypoint creates, so a
-- second database has to be given, by hand, what the entrypoint gave the first: the PostGIS
-- extensions, the roles/schema grants, and the database-level CONNECT/CREATE rights.

-- 1. The database itself. CREATE DATABASE cannot run inside a transaction or a DO block, so
-- this uses psql's \gexec: the SELECT only produces a CREATE DATABASE statement when
-- opsgrids_demo doesn't exist yet, and \gexec then runs whatever the query returned (nothing,
-- on every run after the first).
SELECT 'CREATE DATABASE opsgrids_demo OWNER opsgrids'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'opsgrids_demo')\gexec

-- 2. Everything below runs inside opsgrids_demo.
\c opsgrids_demo

-- The postgis image installs these into POSTGRES_DB at initdb time via 10_postgis.sh (see
-- compose.yml). opsgrids_demo didn't exist at initdb time, so nobody ever ran that for it.
-- Order matters: postgis_tiger_geocoder depends on the other three.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;
CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;
CREATE EXTENSION IF NOT EXISTS postgis_tiger_geocoder;

-- Same file the postgres container already mounts for the primary database, read from inside
-- the container so there is exactly one copy of the roles/schema-grant logic. Idempotent on
-- its own terms (IF NOT EXISTS role guards); its two `GRANT ... ON DATABASE opsgrids` lines
-- name that database explicitly and are harmless no-ops here.
\i /docker-entrypoint-initdb.d/20-init.sql

-- init-db.sql's database-level grants are hardcoded to `opsgrids` by name, so they never touch
-- opsgrids_demo. Without these, migrations fail with "permission denied for database opsgrids_demo"
-- the moment `make migrate` targets it — opsgrids_admin cannot create a schema, and no
-- opsgrids_* service role can even connect.
GRANT CREATE ON DATABASE opsgrids_demo TO opsgrids_admin;

DO $$
DECLARE
  r text;
BEGIN
  FOR r IN SELECT rolname FROM pg_roles WHERE rolname LIKE 'opsgrids\_%' ESCAPE '\' LOOP
    EXECUTE format('GRANT CONNECT ON DATABASE opsgrids_demo TO %I', r);
  END LOOP;
END
$$;
