-- Idempotent setup for `ops-monitor`'s own integration-test database, `opsgrids_mon`
-- (libs/platform-core/tests/test_ops_monitor_integration.py). Run by `make ops-monitor-db`,
-- against the compose postgres started by `make infra`. Safe to run any number of times: every
-- statement below is guarded, and nothing here drops or truncates anything.
--
-- Why a second database rather than the shared `opsgrids` one every other suite uses: this
-- monitor's outbox-lag check runs real queries against real `outbox` tables, and the fastest
-- way to turn "insert a stale row, expect firing" into a flake nobody can reproduce is sharing
-- rows with every other developer's `account`/`product` schemas on the same box. `opsgrids_wt`
-- (`e2e-db.sql`) already set the precedent for a second, throwaway database in the same
-- cluster; this is the identical shape for a different suite.
--
-- Same recipe as `e2e-db.sql`, and deliberately kept in step with it: create the database,
-- install the PostGIS extensions the image would have installed at initdb time (so the
-- `tiger`/`tiger_data`/`topology` schemas init-db.sql's own GRANT USAGE lines name actually
-- exist here — no hand-edited copy of that file to keep in sync), re-run the shared
-- roles/schema-grant script against it, and grant the database-level rights it only ever names
-- `opsgrids` for.

SELECT 'CREATE DATABASE opsgrids_mon OWNER opsgrids'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'opsgrids_mon')\gexec

\c opsgrids_mon

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;
CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;
CREATE EXTENSION IF NOT EXISTS postgis_tiger_geocoder;

\i /docker-entrypoint-initdb.d/20-init.sql

GRANT CREATE ON DATABASE opsgrids_mon TO opsgrids_admin;

DO $$
DECLARE
  r text;
BEGIN
  FOR r IN SELECT rolname FROM pg_roles WHERE rolname LIKE 'opsgrids\_%' ESCAPE '\' LOOP
    EXECUTE format('GRANT CONNECT ON DATABASE opsgrids_mon TO %I', r);
  END LOOP;
END
$$;
