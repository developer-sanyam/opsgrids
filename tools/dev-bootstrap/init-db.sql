-- Runs once, on a fresh database volume.

CREATE EXTENSION IF NOT EXISTS postgis;      -- geo-fencing is a query, not app-side maths
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS btree_gist;   -- exclusion constraints on overlapping ranges
CREATE EXTENSION IF NOT EXISTS pg_trgm;      -- name search
CREATE EXTENSION IF NOT EXISTS citext;       -- email is case-insensitive and globally unique

-- ---------------------------------------------------------------------------
-- The roles, and the difference between them matters.
--
--   atmora_app        runs the services. NOBYPASSRLS, so a missing tenant filter returns
--                     nothing rather than everything. Superseded by the per-service roles
--                     below, kept so an older .env still connects.
--   atmora_admin      runs migrations and backfills — work that owns the schema and
--                     legitimately spans organizations. BYPASSRLS.
--   atmora_directory  the cross-tenant identity reads sign-in depends on. BYPASSRLS,
--                     SELECT-only on a named handful of `account` tables.
--   atmora_product_directory
--                     the cross-tenant entitlements read auth-service makes while minting a
--                     token — resolving an organization's activations before any tenant is
--                     bound. BYPASSRLS, SELECT-only on `product.activations` and
--                     `product.products`. Its own role rather than atmora_directory because a
--                     bypass reader is granted only its own service's named tables.
--   atmora_outbox     the outbox relay and the outbox-lag probe. BYPASSRLS, SELECT and
--                     UPDATE on each service's `outbox` and nothing else.
--
-- Each service also gets its own NOBYPASSRLS login role — atmora_account, atmora_auth,
-- atmora_product, atmora_employee, atmora_attendance, atmora_catalog, and one per
-- product/platform service (atmora_project, atmora_customer, atmora_lead, atmora_document, …)
-- — so one service's connection cannot read another's schema. `atmora_catalog` owns the
-- commerce definitional layer: its plan/price tables are global and un-policied, but its
-- `audit_logs`/`outbox` carry a tenant policy, so it too must be a role RLS can bind.
-- `atmora_billing` owns the financial record — invoices, invoice lines and the append-only
-- credit ledger, all tenant-policied — so it is NOBYPASSRLS too; its append-only and
-- finalize-freeze guarantees are enforced below the role, with table privileges and a trigger.
-- `atmora_metering` owns the append-only usage sink: its `usage_events` additionally has
-- UPDATE/DELETE revoked from it in a service migration, so append-only holds at the database.
-- `atmora_console` owns the operator read model: its `revenue_events` projection is likewise
-- append-only (UPDATE/DELETE revoked in a service migration), and it carries a second BYPASSRLS
-- reader, `atmora_console_directory`, for the cross-tenant operator read API (below).
--
-- Running a relay as a NOBYPASSRLS role without setting the tenant GUC per message is the
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
  FOREACH r IN ARRAY ARRAY[
    'atmora_account', 'atmora_auth', 'atmora_product', 'atmora_employee', 'atmora_attendance',
    -- PulseBoard
    'atmora_project', 'atmora_timesheet', 'atmora_standup', 'atmora_quotation',
    -- CRM
    'atmora_customer', 'atmora_deal', 'atmora_marketing', 'atmora_support',
    -- Sales CRM
    'atmora_lead', 'atmora_pipeline', 'atmora_activity',
    -- Platform (shared)
    'atmora_document', 'atmora_notification', 'atmora_scheduler',
    'atmora_eventbridge', 'atmora_config',
    -- Commerce
    'atmora_catalog', 'atmora_billing', 'atmora_metering', 'atmora_subscription', 'atmora_console'
  ] LOOP
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
  TO atmora_admin, atmora_account, atmora_auth, atmora_product, atmora_employee, atmora_attendance,
     atmora_project, atmora_timesheet, atmora_standup, atmora_quotation,
     atmora_customer, atmora_deal, atmora_marketing, atmora_support,
     atmora_lead, atmora_pipeline, atmora_activity,
     atmora_document, atmora_notification, atmora_scheduler,
     atmora_eventbridge, atmora_config, atmora_catalog, atmora_billing, atmora_metering, atmora_subscription, atmora_console;

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

-- The product entitlements reader (ADR-0002, "the cross-tenant reader"), one per service.
--
-- auth-service resolves an organization's activations while it mints that organization a
-- token, at a moment when there is no tenant context to bind — the token being minted is what
-- would establish one. Under row-level security the application role reads nothing there, so
-- every product's routes 402 at the gateway. The read runs on its own BYPASSRLS role instead,
-- SELECT-only on `product.activations` and `product.products` and nothing else. A separate
-- role from atmora_directory because each bypass reader is confined to its own service's
-- named tables — least privilege inside the bypass, for the route added to that module later.
-- Table grants are applied by product's `migrations/env.py`, because the tables do not exist
-- when this file runs.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atmora_product_directory') THEN
    CREATE ROLE atmora_product_directory LOGIN PASSWORD 'atmora' NOINHERIT BYPASSRLS;
  END IF;
END
$$;
GRANT CONNECT ON DATABASE atmora TO atmora_product_directory;
GRANT USAGE ON SCHEMA public, tiger, tiger_data, topology TO atmora_product_directory;

-- The operator console's cross-tenant reader (ADR-0002 §2/§3, ADR-0003 §2).
--
-- The operator console reads *across* tenants — every customer's MRR, subscriptions, usage and
-- override-audit at once — so its read API cannot carry a tenant binding and cannot run on the
-- application role. It gets its own BYPASSRLS role, SELECT-only on console's projection tables
-- (`org_commercial_state`, `revenue_events`, `usage_summary`) and its `audit_logs`, and nothing
-- else — a named, auditable bypass, not a superuser. A separate role from atmora_directory and
-- atmora_product_directory because each bypass reader is confined to its own service's named
-- tables — least privilege inside the bypass. Table grants are applied by console's
-- `migrations/env.py`, because the tables do not exist when this file runs.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atmora_console_directory') THEN
    CREATE ROLE atmora_console_directory LOGIN PASSWORD 'atmora' NOINHERIT BYPASSRLS;
  END IF;
END
$$;
GRANT CONNECT ON DATABASE atmora TO atmora_console_directory;
GRANT USAGE ON SCHEMA public, tiger, tiger_data, topology TO atmora_console_directory;

-- The outbox relay (ADR-0002 §2, "The outbox relay must run BYPASSRLS").
--
-- The relay's query exists to discover which organizations have work, so there is no tenant to
-- bind before it runs; as a NOBYPASSRLS role it selects nothing, logs nothing and loops while
-- the outbox fills. It does not get the admin role for the same reason the directory reader
-- does not: a bug in the relay must not be able to write anything but the rows it marks as sent.
-- Table grants (SELECT, UPDATE on `outbox` only) are applied by each service's
-- `migrations/env.py`, because the table does not exist when this file runs.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atmora_outbox') THEN
    CREATE ROLE atmora_outbox LOGIN PASSWORD 'atmora' NOINHERIT BYPASSRLS;
  END IF;
END
$$;
GRANT CONNECT ON DATABASE atmora TO atmora_outbox;
GRANT USAGE ON SCHEMA public, tiger, tiger_data, topology TO atmora_outbox;
