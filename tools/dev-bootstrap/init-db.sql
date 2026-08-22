-- Runs once, on a fresh database volume.

CREATE EXTENSION IF NOT EXISTS postgis;      -- geo-fencing is a query, not app-side maths
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS btree_gist;   -- exclusion constraints on overlapping ranges
CREATE EXTENSION IF NOT EXISTS pg_trgm;      -- name search
CREATE EXTENSION IF NOT EXISTS citext;       -- email is case-insensitive and globally unique

-- ---------------------------------------------------------------------------
-- Two roles, and the difference between them matters.
--
--   atmora_app    runs the services. NOBYPASSRLS, so a missing tenant filter returns
--                 nothing rather than everything.
--   atmora_admin  runs migrations, outbox relays, fan-out consumers and backfills —
--                 work that legitimately spans organizations. BYPASSRLS.
--
-- Running a relay as atmora_app without setting the tenant GUC per message is the
-- failure that stops event processing with no error at all: the outbox simply fills.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atmora_app') THEN
    CREATE ROLE atmora_app LOGIN PASSWORD 'atmora' NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atmora_admin') THEN
    CREATE ROLE atmora_admin LOGIN PASSWORD 'atmora' BYPASSRLS;
  END IF;
END
$$;

GRANT CONNECT ON DATABASE atmora TO atmora_app, atmora_admin;
-- The admin role owns the schemas, so it must be able to create them. Each service's
-- first migration issues CREATE SCHEMA; nothing else in the platform creates one.
GRANT CREATE ON DATABASE atmora TO atmora_admin;

-- One schema per service. No foreign key may cross a schema boundary — that rule is what
-- keeps a later database-per-service split a config change rather than a rewrite.
-- Schemas are created by each service's first migration, not here, so that adding a
-- service does not mean editing this file.

-- ---------------------------------------------------------------------------
-- Per-service application roles (ADR-0002, decision 2).
--
-- One shared app role would let account-service run `SELECT * FROM auth.credentials`.
-- auth-service exists precisely so credentials live away from the directory, and a single
-- shared login undoes that at the database layer while the application layer still looks
-- correct. Each service therefore connects as its own NOBYPASSRLS role, granted usage on
-- its own schema and nothing else.
--
-- Grants on tables are NOT here: schemas and tables are created by each service's first
-- migration, so this file runs before they exist. `migrations/env.py` applies them after
-- every migration run, which also means a new service cannot forget to.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
  r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['atmora_account', 'atmora_auth'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L NOBYPASSRLS', r, 'atmora');
    END IF;
    EXECUTE format('GRANT CONNECT ON DATABASE atmora TO %I', r);
  END LOOP;
END
$$;

-- PostGIS installs `tiger`, `tiger_data` and `topology`, and Alembic's reflection walks
-- every schema in the database. Without read access the non-superuser roles fail
-- `alembic check` with "permission denied for schema tiger" — a confusing error that has
-- nothing to do with the service's own tables. Reference data, no tenant rows.
GRANT USAGE ON SCHEMA public, tiger, tiger_data, topology
  TO atmora_admin, atmora_account, atmora_auth;

-- The directory reader (ADR-0002, "the cross-tenant reader").
--
-- Sign-in has to resolve a person before an organization is known, so those lookups cannot
-- carry a tenant binding and cannot be served by the application role. They get their own
-- role rather than the admin one: BYPASSRLS so they can read across tenants, SELECT-only on a
-- named handful of tables so a bug in that module cannot write anything. Least privilege
-- inside the bypass, because the risk being managed is a route added there later.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atmora_directory') THEN
    CREATE ROLE atmora_directory LOGIN PASSWORD 'atmora' NOINHERIT BYPASSRLS;
  END IF;
END
$$;
GRANT CONNECT ON DATABASE atmora TO atmora_directory;
GRANT USAGE ON SCHEMA public, tiger, tiger_data, topology TO atmora_directory;
