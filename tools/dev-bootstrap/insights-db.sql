-- Idempotent setup for insights' own scratch database, `opsgrids_insights`. Run by
-- `make insights-db`, against the compose postgres started by `make infra`. Safe to run any
-- number of times: every statement below is guarded, and nothing here drops or truncates
-- anything.
--
-- Why this exists: ADR-0020's I1 slice is new schema-and-RLS work — a migration up/down and an
-- RLS-isolation suite are exactly the kind of thing worth trying against real Postgres before
-- trusting the automated suite alone, and never against the shared `opsgrids` database everyone
-- else's dev work and manual testing also lands in (several worktrees share one Postgres
-- container). Same shape as `e2e-db.sql`/`opsgrids_wt`, `ops-monitor-db.sql`/`opsgrids_mon` and
-- `backfill-db.sql`/`opsgrids_backfill`: a second database in the same cluster nothing else
-- creates.
--
-- Deliberately **self-contained** rather than `\i`-including
-- `/docker-entrypoint-initdb.d/20-init.sql` the way its three siblings do: that path is a bind
-- mount resolved to whichever worktree's `tools/dev-bootstrap/init-db.sql` happened to start the
-- container, which is not necessarily this checkout's copy on a box running several worktrees
-- against one Postgres. `opsgrids_insights` is the one role this file needs that a much older
-- container might not have, so it is created here directly; `CREATE ROLE` is a cluster-level
-- statement; issuing it while connected to this database is exactly as durable as issuing it
-- against `opsgrids`.

SELECT 'CREATE DATABASE opsgrids_insights OWNER opsgrids'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'opsgrids_insights')\gexec

\c opsgrids_insights

-- The postgis image installs these into POSTGRES_DB at initdb time; opsgrids_insights didn't
-- exist then, so nobody ever ran that for it. Order matters: postgis_tiger_geocoder depends on
-- the other three. Alembic's reflection walks every schema in the database, so without these
-- `alembic check`/migrations fail with "permission denied for schema tiger" — unrelated to
-- insights' own tables.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;
CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;
CREATE EXTENSION IF NOT EXISTS postgis_tiger_geocoder;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'opsgrids_insights') THEN
    CREATE ROLE opsgrids_insights LOGIN PASSWORD 'opsgrids' NOBYPASSRLS;
  END IF;
END
$$;

GRANT CONNECT ON DATABASE opsgrids_insights TO opsgrids_insights;
-- init-db.sql's database-level grants are hardcoded to `opsgrids` by name, so they never touch
-- opsgrids_insights. Without this, migrations fail with "permission denied for database
-- opsgrids_insights" the moment `alembic upgrade head` targets it — opsgrids_admin cannot
-- create a schema, and no opsgrids_* service role can even connect.
GRANT CREATE ON DATABASE opsgrids_insights TO opsgrids_admin;
-- Alembic's reflection walks every schema in the database (init-db.sql's own comment on this
-- exact grant) — opsgrids_admin runs every migration, so it needs this here too, not only the
-- app role.
GRANT USAGE ON SCHEMA public, tiger, tiger_data, topology TO opsgrids_insights, opsgrids_admin;

DO $$
DECLARE
  r text;
BEGIN
  FOR r IN SELECT rolname FROM pg_roles WHERE rolname LIKE 'opsgrids\_%' ESCAPE '\' LOOP
    EXECUTE format('GRANT CONNECT ON DATABASE opsgrids_insights TO %I', r);
  END LOOP;
END
$$;
