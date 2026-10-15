-- Idempotent setup for the repository-tooling database tests, `opsgrids_tooling`. Run by
-- `make tooling-db`, against the compose postgres started by `make infra` (or, in CI and
-- against any other cluster, from the runner with `-v init_sql=tools/dev-bootstrap/init-db.sql`).
-- Safe to run any number of times: every statement below is guarded, and nothing here drops or
-- truncates anything.
--
-- Why this exists: four modules in tests/infra drive tools that DELETE organisations and GPS
-- captures (tools/purge_test_orgs.py, tools/purge_putaway_captures.py) and the runbook queries
-- written against the same tables. They need every service migrated into a database and the
-- owner role (`opsgrids_admin`), and they must never touch the shared `opsgrids` database. So
-- they get their own, the same shape as `e2e-db.sql`/`opsgrids_wt`, `ops-monitor-db.sql`/
-- `opsgrids_mon` and `backfill-db.sql`/`opsgrids_backfill`. One database serves all four
-- modules: each test builds its own random organisations and removes them, and pytest runs the
-- modules one after another.

-- CREATE DATABASE cannot run in a transaction or DO block, hence \gexec.
SELECT 'CREATE DATABASE opsgrids_tooling OWNER opsgrids'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'opsgrids_tooling')\gexec

\c opsgrids_tooling

-- Order matters: postgis_tiger_geocoder depends on the other three.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;
CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;
CREATE EXTENSION IF NOT EXISTS postgis_tiger_geocoder;

-- init-db.sql grants on `tiger_data`. The postgis image's extension scripts leave it behind; a
-- host-installed PostGIS (a throwaway cluster per docs/testing.md) does not, and the grant fails.
CREATE SCHEMA IF NOT EXISTS tiger_data;

-- The roles/schema-grant logic, in one copy. Inside the compose container it is mounted at the
-- default below; from a runner or host, pass -v init_sql=tools/dev-bootstrap/init-db.sql.
\if :{?init_sql}
\else
\set init_sql /docker-entrypoint-initdb.d/20-init.sql
\endif
\i :init_sql

-- init-db.sql's database-level grants name `opsgrids` explicitly; these are the ones for here.
GRANT CREATE ON DATABASE opsgrids_tooling TO opsgrids_admin;

DO $$
DECLARE
  r text;
BEGIN
  FOR r IN SELECT rolname FROM pg_roles WHERE rolname LIKE 'opsgrids\_%' ESCAPE '\' LOOP
    EXECUTE format('GRANT CONNECT ON DATABASE opsgrids_tooling TO %I', r);
  END LOOP;
END
$$;
