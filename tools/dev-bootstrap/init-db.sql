-- Runs once, on a fresh database volume.

CREATE EXTENSION IF NOT EXISTS postgis;      -- geo-fencing is a query, not app-side maths
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS btree_gist;   -- exclusion constraints on overlapping ranges
CREATE EXTENSION IF NOT EXISTS pg_trgm;      -- name search
CREATE EXTENSION IF NOT EXISTS citext;       -- email is case-insensitive and globally unique

-- ---------------------------------------------------------------------------
-- The roles, and the difference between them matters.
--
--   opsgrids_app        runs the services. NOBYPASSRLS, so a missing tenant filter returns
--                     nothing rather than everything. Superseded by the per-service roles
--                     below, kept so an older .env still connects.
--   opsgrids_admin      runs migrations and backfills — work that owns the schema and
--                     legitimately spans organizations. BYPASSRLS.
--   opsgrids_directory  the cross-tenant identity reads sign-in depends on. BYPASSRLS,
--                     SELECT-only on a named handful of `account` tables.
--   opsgrids_product_directory
--                     the cross-tenant entitlements read auth-service makes while minting a
--                     token — resolving an organization's activations before any tenant is
--                     bound. BYPASSRLS, SELECT-only on `product.activations` and
--                     `product.products`. Its own role rather than opsgrids_directory because a
--                     bypass reader is granted only its own service's named tables.
--   opsgrids_storefront_directory
--                     the anonymous store-slug lookup the buyer-facing serviceability check
--                     makes before any tenant is bound (ADR-0010). BYPASSRLS, SELECT-only on
--                     `storefront.storefronts`. Its own role for the same reason as above.
--   opsgrids_outbox     the outbox relay and the outbox-lag probe. BYPASSRLS, SELECT and
--                     UPDATE on each service's `outbox` and nothing else.
--
-- Each service also gets its own NOBYPASSRLS login role — opsgrids_account, opsgrids_auth,
-- opsgrids_product, opsgrids_employee, opsgrids_attendance, opsgrids_catalog, and one per
-- product/platform service (opsgrids_project, opsgrids_customer, opsgrids_lead, opsgrids_document, …)
-- — so one service's connection cannot read another's schema. `opsgrids_catalog` owns the
-- commerce definitional layer: its plan/price tables are global and un-policied, but its
-- `audit_logs`/`outbox` carry a tenant policy, so it too must be a role RLS can bind.
-- `opsgrids_billing` owns the financial record — invoices, invoice lines and the append-only
-- credit ledger, all tenant-policied — so it is NOBYPASSRLS too; its append-only and
-- finalize-freeze guarantees are enforced below the role, with table privileges and a trigger.
-- `opsgrids_metering` owns the append-only usage sink: its `usage_events` additionally has
-- UPDATE/DELETE revoked from it in a service migration, so append-only holds at the database.
-- `opsgrids_payment` owns the provider-facing edge — mandates, charges, and the append-only
-- `refunds` and `provider_events` (webhook inbox); it is NOBYPASSRLS too, and append-only there
-- is enforced below the role by a trigger that survives the post-migration grant re-apply.
-- `opsgrids_console` owns the operator read model: its `revenue_events` projection is likewise
-- append-only (UPDATE/DELETE revoked in a service migration), and it carries a second BYPASSRLS
-- reader, `opsgrids_console_directory`, for the cross-tenant operator read API (below).
-- `opsgrids_projects`, `opsgrids_finance`, `opsgrids_operations` and `opsgrids_itsm` are the newest
-- product-line services: gate-green skeletons for now (outbox, audit_logs, processed_events
-- only), each a NOBYPASSRLS role confined to its own schema like every other product service.
-- `opsgrids_approval` owns the routing engine (ADR-0006): a platform service, not a product, but
-- NOBYPASSRLS like every other service — every table it owns, including the read-only
-- `hierarchy_nodes` copy this slice adds, carries `organization_id` and a tenant policy.
--

-- Running a relay as a NOBYPASSRLS role without setting the tenant GUC per message is the
-- failure that stops event processing with no error at all: the outbox simply fills.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'opsgrids_app') THEN
    CREATE ROLE opsgrids_app LOGIN PASSWORD 'opsgrids' NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'opsgrids_admin') THEN
    CREATE ROLE opsgrids_admin LOGIN PASSWORD 'opsgrids' BYPASSRLS;
  END IF;
END
$$;

GRANT CONNECT ON DATABASE opsgrids TO opsgrids_app, opsgrids_admin;
-- The admin role owns the schemas, so it must be able to create them. Each service's
-- first migration issues CREATE SCHEMA; nothing else in the platform creates one.
GRANT CREATE ON DATABASE opsgrids TO opsgrids_admin;

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
    'opsgrids_account', 'opsgrids_auth', 'opsgrids_product', 'opsgrids_employee', 'opsgrids_attendance',
    -- PulseBoard
    'opsgrids_project', 'opsgrids_timesheet', 'opsgrids_standup', 'opsgrids_quotation',
    -- CRM
    'opsgrids_customer', 'opsgrids_deal', 'opsgrids_marketing', 'opsgrids_support',
    -- Sales CRM
    'opsgrids_lead', 'opsgrids_pipeline', 'opsgrids_activity',
    -- Platform (shared)
    'opsgrids_document', 'opsgrids_notification', 'opsgrids_scheduler',
    'opsgrids_eventbridge', 'opsgrids_config', 'opsgrids_approval',
    -- Commerce
    'opsgrids_catalog', 'opsgrids_billing', 'opsgrids_metering', 'opsgrids_subscription', 'opsgrids_console', 'opsgrids_payment',
    -- Product lines (skeletons)
    'opsgrids_projects', 'opsgrids_finance', 'opsgrids_operations', 'opsgrids_itsm',
    -- Storefront (ADR-0010)
    'opsgrids_storefront'
  ] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L NOBYPASSRLS', r, 'opsgrids');
    END IF;
    EXECUTE format('GRANT CONNECT ON DATABASE opsgrids TO %I', r);
  END LOOP;
END
$$;

-- PostGIS installs `tiger`, `tiger_data` and `topology`, and Alembic's reflection walks
-- every schema in the database. Without read access the non-superuser roles fail
-- `alembic check` with "permission denied for schema tiger" — a confusing error that has
-- nothing to do with the service's own tables. Reference data, no tenant rows.
GRANT USAGE ON SCHEMA public, tiger, tiger_data, topology
  TO opsgrids_admin, opsgrids_account, opsgrids_auth, opsgrids_product, opsgrids_employee, opsgrids_attendance,
     opsgrids_project, opsgrids_timesheet, opsgrids_standup, opsgrids_quotation,
     opsgrids_customer, opsgrids_deal, opsgrids_marketing, opsgrids_support,
     opsgrids_lead, opsgrids_pipeline, opsgrids_activity,
     opsgrids_document, opsgrids_notification, opsgrids_scheduler,
     opsgrids_eventbridge, opsgrids_config, opsgrids_catalog, opsgrids_billing, opsgrids_metering, opsgrids_subscription,
     opsgrids_console, opsgrids_payment, opsgrids_approval,
     opsgrids_projects, opsgrids_finance, opsgrids_operations, opsgrids_itsm,
     opsgrids_storefront;

-- The directory reader (ADR-0002, "the cross-tenant reader").
--
-- Sign-in has to resolve a person before an organization is known, so those lookups cannot
-- carry a tenant binding and cannot be served by the application role. They get their own
-- role rather than the admin one: BYPASSRLS so they can read across tenants, SELECT-only on a
-- named handful of tables so a bug in that module cannot write anything. Least privilege
-- inside the bypass, because the risk being managed is a route added there later.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'opsgrids_directory') THEN
    CREATE ROLE opsgrids_directory LOGIN PASSWORD 'opsgrids' NOINHERIT BYPASSRLS;
  END IF;
END
$$;
GRANT CONNECT ON DATABASE opsgrids TO opsgrids_directory;
GRANT USAGE ON SCHEMA public, tiger, tiger_data, topology TO opsgrids_directory;

-- The product entitlements reader (ADR-0002, "the cross-tenant reader"), one per service.
--
-- auth-service resolves an organization's activations while it mints that organization a
-- token, at a moment when there is no tenant context to bind — the token being minted is what
-- would establish one. Under row-level security the application role reads nothing there, so
-- every product's routes 402 at the gateway. The read runs on its own BYPASSRLS role instead,
-- SELECT-only on `product.activations` and `product.products` and nothing else. A separate
-- role from opsgrids_directory because each bypass reader is confined to its own service's
-- named tables — least privilege inside the bypass, for the route added to that module later.
-- Table grants are applied by product's `migrations/env.py`, because the tables do not exist
-- when this file runs.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'opsgrids_product_directory') THEN
    CREATE ROLE opsgrids_product_directory LOGIN PASSWORD 'opsgrids' NOINHERIT BYPASSRLS;
  END IF;
END
$$;
GRANT CONNECT ON DATABASE opsgrids TO opsgrids_product_directory;
GRANT USAGE ON SCHEMA public, tiger, tiger_data, topology TO opsgrids_product_directory;

-- The operator console's cross-tenant reader (ADR-0002 §2/§3, ADR-0003 §2).
--
-- The operator console reads *across* tenants — every customer's MRR, subscriptions, usage and
-- override-audit at once — so its read API cannot carry a tenant binding and cannot run on the
-- application role. It gets its own BYPASSRLS role, SELECT-only on console's projection tables
-- (`org_commercial_state`, `revenue_events`, `usage_summary`) and its `audit_logs`, and nothing
-- else — a named, auditable bypass, not a superuser. A separate role from opsgrids_directory and
-- opsgrids_product_directory because each bypass reader is confined to its own service's named
-- tables — least privilege inside the bypass. Table grants are applied by console's
-- `migrations/env.py`, because the tables do not exist when this file runs.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'opsgrids_console_directory') THEN
    CREATE ROLE opsgrids_console_directory LOGIN PASSWORD 'opsgrids' NOINHERIT BYPASSRLS;
  END IF;
  -- notification's bypass reader. Meta posts every organization's WhatsApp webhooks to one
  -- URL, so the service has to resolve *which* tenant from the payload's `phone_number_id`
  -- before a tenant is known — a lookup row-level security cannot express. Confined by the
  -- migration to SELECT on `notification.provider_accounts` alone, like every other bypass
  -- reader here: least privilege inside the bypass, because the risk being managed is a route
  -- added to that module later.
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'opsgrids_notification_directory') THEN
    CREATE ROLE opsgrids_notification_directory LOGIN PASSWORD 'opsgrids' NOINHERIT BYPASSRLS;
  END IF;
END
$$;
GRANT CONNECT ON DATABASE opsgrids TO opsgrids_console_directory;
GRANT CONNECT ON DATABASE opsgrids TO opsgrids_notification_directory;
GRANT USAGE ON SCHEMA public, tiger, tiger_data, topology TO opsgrids_notification_directory;
GRANT USAGE ON SCHEMA public, tiger, tiger_data, topology TO opsgrids_console_directory;

-- The storefront's public reader (ADR-0010 §4, ADR-0002 §2).
--
-- The platform's first anonymous read of tenant data. A buyer with no token, no account and no
-- session asks whether a store delivers to their postal code; the store is named by a slug in
-- the request body, and the organization behind that slug has to be resolved *before* any
-- tenant-bound transaction can be opened -- which is to say before row-level security can
-- protect anything. On the application role that lookup matches zero rows, silently, and every
-- buyer in the world is told the store does not deliver to them.
--
-- BYPASSRLS, and SELECT-only on `storefront.storefronts` and nothing else: it returns two ids
-- and a status, and every statement after it runs tenant-bound on opsgrids_storefront. A separate
-- role from opsgrids_directory, opsgrids_product_directory and opsgrids_console_directory because
-- each bypass reader is confined to its own service's named tables -- least privilege inside the
-- bypass. Table grants are applied by storefront's `migrations/env.py`, because the table does
-- not exist when this file runs.
--
-- **Leaving this role unconfigured is the worst failure in ADR-0010's design**, because it is
-- silent: `get_directory_session` falls back to the ordinary session, the answer is a well-formed
-- HTTP 200 saying "not served", and nothing is logged. storefront-service asserts the role at
-- startup and refuses to boot without it.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'opsgrids_storefront_directory') THEN
    CREATE ROLE opsgrids_storefront_directory LOGIN PASSWORD 'opsgrids' NOINHERIT BYPASSRLS;
  END IF;
END
$$;
GRANT CONNECT ON DATABASE opsgrids TO opsgrids_storefront_directory;
GRANT USAGE ON SCHEMA public, tiger, tiger_data, topology TO opsgrids_storefront_directory;

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
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'opsgrids_outbox') THEN
    CREATE ROLE opsgrids_outbox LOGIN PASSWORD 'opsgrids' NOINHERIT BYPASSRLS;
  END IF;
END
$$;
GRANT CONNECT ON DATABASE opsgrids TO opsgrids_outbox;
GRANT USAGE ON SCHEMA public, tiger, tiger_data, topology TO opsgrids_outbox;
