-- Idempotent setup for the e2e suite's worktree database, `opsgrids_wt`
-- (e2e/conftest.py's DATABASE_URL). Run by `make e2e-db`, against the compose postgres
-- started by `make infra`. Safe to run any number of times: every statement below is
-- guarded, and nothing here drops or truncates anything.
--
-- Why this exists rather than living entirely in init-db.sql: init-db.sql runs once, on a
-- fresh volume, against whatever database POSTGRES_DB names — `opsgrids`. `opsgrids_wt` is a
-- second database in the same cluster, created after the fact, and Postgres has no "also run
-- this for every future database" hook. So this script does, by hand, what the entrypoint did
-- automatically for `opsgrids`: create the database, install the PostGIS extensions the image
-- would otherwise have installed at initdb time, re-run init-db.sql's roles/schema grants
-- against it, and then grant the database-level rights init-db.sql only ever names `opsgrids`
-- for.

-- 1. The database itself. CREATE DATABASE cannot run inside a transaction or a DO block, so
-- this uses psql's \gexec: the SELECT only produces a CREATE DATABASE statement when
-- opsgrids_wt doesn't exist yet, and \gexec then runs whatever the query returned (nothing,
-- on every run after the first).
SELECT 'CREATE DATABASE opsgrids_wt OWNER opsgrids'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'opsgrids_wt')\gexec

-- 2. Everything below runs inside opsgrids_wt.
\c opsgrids_wt

-- The postgis image installs these into POSTGRES_DB at initdb time via 10_postgis.sh (see
-- compose.yml). opsgrids_wt didn't exist at initdb time, so nobody ever ran that for it.
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
-- opsgrids_wt. Without these, migrations fail with "permission denied for database opsgrids_wt"
-- the moment `make migrate` targets it — opsgrids_admin cannot create a schema, and no
-- opsgrids_* service role can even connect.
GRANT CREATE ON DATABASE opsgrids_wt TO opsgrids_admin;

DO $$
DECLARE
  r text;
BEGIN
  FOR r IN SELECT rolname FROM pg_roles WHERE rolname LIKE 'opsgrids\_%' ESCAPE '\' LOOP
    EXECUTE format('GRANT CONNECT ON DATABASE opsgrids_wt TO %I', r);
  END LOOP;
END
$$;
