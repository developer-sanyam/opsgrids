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

-- One schema per service. No foreign key may cross a schema boundary — that rule is what
-- keeps a later database-per-service split a config change rather than a rewrite.
-- Schemas are created by each service's first migration, not here, so that adding a
-- service does not mean editing this file.
