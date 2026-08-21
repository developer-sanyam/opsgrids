# ADR-0002 — Row-level security: making tenant isolation enforced rather than promised

**Status: Proposed. Nothing here is implemented. Awaiting review before any code is written.**

Supersedes nothing. Refines decision 9 of [ADR-0000](0000-phase-0-decisions.md), which chose
shared-schema tenancy with `organization_id` plus RLS but did not say how RLS is enforced.

---

## Context

ADR-0000 §9, `README.md` and `CLAUDE.md` all state that tenant isolation rests on two
mechanisms: every query filtering on `organization_id`, **and** Postgres row-level security
underneath as "the net under the tightrope". The first exists and is applied with discipline.
The second does not exist at all.

That much was already known. What the investigation for this ADR found is worse, and it
changes what the work is. **There are three independent reasons RLS is currently inert, and
fixing only the obvious one produces a system that looks isolated and is not.**

### Verified against the running database and the source

| # | Finding | Evidence |
|---|---|---|
| 1 | **0 of 28 tables** have `rowsecurity` enabled | `pg_tables` on the dev database: `account` 15 tables, `auth` 13, none with RLS |
| 2 | The application connects as a **superuser** | `.env` → `postgresql+asyncpg://atmora:...`; `pg_roles` shows `atmora` is `rolsuper=t, rolbypassrls=t`. **Superusers bypass RLS unconditionally — `FORCE` cannot override this** |
| 3 | Every table is **owned by the connecting role** | all 28 owned by `atmora`. A table owner bypasses its own policies unless the table is `FORCE ROW LEVEL SECURITY` |
| 4 | The purpose-built roles are **unused and unprivileged** | `atmora_app` (NOBYPASSRLS) and `atmora_admin` (BYPASSRLS) exist in `init-db.sql:23,26`, but the only grant either holds is `GRANT CONNECT` (`:31`). No `USAGE`, no table privileges |
| 5 | **The tenant GUC is never set by anything** | `set_tenant` (`platform_core/db.py:98`) is called only by `session_scope` (`db.py:134`), and `session_scope` has no callers in any service |
| 6 | The GUC **name differs between code and docs** | code: `app.organization_id` (`db.py:36`). ADR-0000 `:121` and `CLAUDE.md:28`: `app.org_id` |
| 7 | `processed_events` has **no `organization_id`** | `event_id`, `event_type`, `processed_at` only (`platform_core/events.py:63-72`) |
| 8 | `roles.organization_id` is **nullable**, NULL = platform template | `services/account/app/models.py:376` |
| 9 | A **fourth** cross-tenant path exists, and it writes | `POST /api/v1/invitations/accept` (`routes.py:326-345`) takes no principal — public by design — and creates a user, a membership and role grants |

Findings 2, 3 and 5 are the important ones, and they compound:

- Because of **5**, switching RLS on with the application unchanged returns **zero rows for
  every tenant query**. Total outage.
- Because of **2 and 3**, switching RLS on *without* also moving off the superuser is a
  **silent no-op**. Policies exist, tests pass, review looks clean, nothing is enforced.

The second is the more dangerous outcome. A no-op RLS layer is worse than no RLS layer,
because the application-level filter in `selectors.py` is currently the *only* defence and
everyone knows it. Once a decorative net is installed, people start trusting it.

### The failure mode this exists to prevent

If the GUC were set per-connection rather than per-transaction, a pooled connection carries
one tenant's id into the next request that forgets to set it. The result is **HTTP 200, a
well-formed body, and another customer's rows** — no error, no log line, nothing in a DLQ.
`set_tenant` already does this correctly (`set_config(..., true)`, transaction-local), and
that property must survive every change below.

---

## Decision

### 1. The chokepoint is the transaction opener, not the session provider

`get_session` (`db.py:110`) yields a session with **no transaction** on purpose: service
functions open `async with session.begin()` so that the state change, the audit row and the
outbox row commit together. A transaction-local GUC must be set *inside that same
transaction*, so a dependency that sets it before yielding cannot work — the `set_config`
would autobegin a transaction and the route's own `session.begin()` would then raise.

Therefore: **introduce a tenant-bound transaction dependency that takes the session and the
`Principal`, opens the transaction, issues `set_tenant` as its first statement, and yields.**
Routes drop their own `session.begin()`. This is a mechanical edit across the write routes in
`services/account/app/api/routes.py`.

Rejected alternative: keep `get_session` and add `set_tenant` calls in service functions. That
is the convention-not-enforcement pattern we already have, one layer down.

**Consequence to accept now:** `services/account/tests/conftest.py:136` overrides `get_session`
with the test's own session. If the chokepoint lives inside the thing tests override, **every
test bypasses it and the suite passes while production is unprotected.** Tests must override
*through* the chokepoint, not around it. This is a design constraint, not a test cleanup task.

### 2. Three database roles, least privilege inside each

| Role | Used by | RLS |
|---|---|---|
| `atmora_admin` | Alembic migrations. Owns the schemas and tables | BYPASSRLS |
| `atmora_app` | Every service API process, and event consumers | **NOBYPASSRLS** |
| `atmora_directory` *(new)* | The `/internal/v1` cross-tenant reads, and nothing else | BYPASSRLS, **SELECT only**, on a named subset of `account` tables |

`atmora_directory` is deliberately **not** `atmora_admin`. The migration role owns everything
and can write anything; the directory reader must not be able to write at all. The point is
least privilege *inside* the bypass, because the risk being managed is a future route added to
that module, not the three that exist today.

The outbox relay must run **BYPASSRLS**: `OutboxRelay._drain` issues one
`SELECT ... FOR UPDATE SKIP LOCKED` across all organizations, and the purpose of that query is
to *discover* which tenants have pending work — there is no org to set beforehand. Event
consumers are the opposite: the envelope carries `organization_id`, so they run NOBYPASSRLS
with the GUC set per message. No consumers are registered yet, so this pattern can be
established before it must be retrofitted.

### 3. Policy shape

Per schema, never a shared `public` helper — a cross-schema dependency would forfeit the
later database-per-service split that ADR-0000 §9 preserves:

```sql
CREATE OR REPLACE FUNCTION account.current_org() RETURNS uuid
    LANGUAGE sql STABLE PARALLEL SAFE
AS $$ SELECT nullif(current_setting('app.organization_id', true), '')::uuid $$;
```

`nullif(…, '')` is **not** a defensive nicety, and it decides the failure mode. Verified against
the dev database: once a transaction that set the GUC locally has ended, the next transaction on
the same session reads it back as **`''`, not NULL**. The empty string is the *normal*
post-transaction state, not a hypothetical. Without `nullif`, `''::uuid` raises `invalid input
syntax for type uuid` — **loud**. With `nullif` it becomes NULL, the predicate is false, and the
query silently returns nothing. Keep `nullif` (§4 explains why fail-quiet is right here), but
make the choice knowingly rather than inheriting it from a snippet.


```sql
ALTER TABLE account.memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE account.memberships FORCE  ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON account.memberships
    FOR ALL TO atmora_app
    USING      (organization_id = account.current_org())
    WITH CHECK (organization_id = account.current_org());
```

**`WITH CHECK` is not optional.** `USING` is evaluated against existing rows and is not applied
to the new row of an INSERT. Without `WITH CHECK`, a tenant can insert a row belonging to
another organization — invisible to them, fully visible to the victim — and on UPDATE could
reassign one of its own rows into another tenant. Postgres does default `WITH CHECK` to the
`USING` expression for `FOR ALL`, which would save us here; relying on that breaks the moment
someone writes an explicit per-command policy. Write it out.

**`FORCE` is defence-in-depth, not a requirement** — an earlier draft of this ADR said it was
required, and that was wrong. Verified empirically against the dev database: a NOBYPASSRLS role
that does **not** own the table sees only its own rows with plain `ENABLE`, no `FORCE` needed
(1 of 2 probe rows); the owner sees all of them (2 of 2). In the target split `atmora_admin`
owns and `atmora_app` connects, so `atmora_app` is never the owner and RLS applies to it
regardless. `FORCE` is still worth setting, for the two futures where it bites: a table created
accidentally by the app role, which would then own it and silently bypass its own policy, and a
later decision to drop `BYPASSRLS` from `atmora_admin`. One extra statement, no cost — but it is
not what makes this work, and believing it is would misdirect debugging when a policy appears
inert.

**Policies are written per table, explicitly.** A loop over "every table with an
`organization_id` column" gets at least four tables wrong in this codebase: it would miss
`organizations` (the tenant is `id`, not `organization_id`), miss the child tables that have no
such column, and wrongly policy the auth tables discussed in §5. Classification is a security
decision and belongs in a reviewable diff. **The loop belongs in a test instead** — a CI check
that fails when a new table carrying `organization_id` has no policy and is not on an explicit,
commented exemption list. That is the durable control as products add tables.

### 4. Unset GUC yields zero rows, and the application must be loud about it

`current_setting('app.organization_id', true)` returns NULL when unset; a NULL comparison is
never true, so reads return nothing and writes are refused. Fail-closed, which is right.

But zero-rows-on-misconfiguration is *silent*, and the application already has friendly
messages that would dress it up as normal domain state — "Organization not found", "That
person is not a member of this organization", an empty page with `total: 0` and HTTP 200. An
RLS misconfiguration would be indistinguishable from ordinary empty state in the logs.

So the policy stays quiet and **the application gets loud**: outside production, assert that
`current_setting(TENANT_GUC)` equals the principal's organization id, once per request. One
round trip converts the worst silent failure into an immediate one.

The same probe sharpened the chokepoint: **the GUC does not survive into a later transaction on
the same session.** That is the transaction-local design working as intended, but it means the
GUC must be set inside *every* transaction, not once per request. Any service function or
fixture that opens two sequential `session.begin()` blocks needs it twice. This is the concrete
reason the chokepoint must be the transaction opener (§1) rather than anything that runs once at
request entry — and the reason the existing fixtures, which open three separate transactions,
cannot be repaired with a single call.

### 5. Table classification

**Tenant-scoped, get a policy** — `account`: `organizations` (on `id`), `org_domains`,
`org_units`, `locations`, `memberships`, `invitations`, `audit_logs`, `outbox`.

**Children with no org column** — `role_permissions`, `membership_roles`. Un-policied, a
session that guesses a `role_id` reads another tenant's grant set. Use an `EXISTS` policy
against the parent; existing unique indexes cover it exactly. Denormalise only if it profiles
badly.

**Global, must NOT get an org policy:**

- `permissions` — the platform's vocabulary, not a customer's. Every org reads the whole
  catalogue by design.
- `users` — global identity by explicit design; email is unique platform-wide so one person can
  hold memberships in several organizations. Read by `/internal/v1/users/by-email` *before any
  organization exists*. A membership-`EXISTS` policy is definable and more secure, but it breaks
  `add_member` (Postgres applies SELECT policies to `INSERT ... RETURNING`, and the row must
  exist before the membership that would make it visible). **Recommendation: leave un-policied**
  and compensate with a test asserting no route selects `users` without joining `memberships`.
  Revisit when a product service needs a user list.
- `processed_events` — finding 7: no org column. If policied on anything, `already_processed`
  returns nothing for every lookup and **every redelivered event is reprocessed**, silently.
  Exempt it deliberately and record why.

**`roles` is the ambiguous one** (finding 8). NULL means platform template, so a naive policy
hides every template and new organizations get no roles. Either implement templates properly
and use `USING (organization_id = current_org() OR organization_id IS NULL)` with
`WITH CHECK (organization_id = current_org())` — read templates, never write one — **or** make
the column `NOT NULL` and take the simple policy. Analysis indicates no NULL row is ever
created today, which argues for the second. *This is an open question for review.*

### 6. auth-service is largely out of scope, and that is the finding

Ten of its twelve tables are pre-organization by design: `credentials`, `mfa_factors`,
`mfa_recovery_codes`, `password_reset_tokens`, `external_identities` are all user-keyed and
read at sign-in before any org is chosen; `sessions.active_org_id` is *mutable* and org
switching is what mutates it, so a policy on it would break switching and single logout;
`signing_keys` back an unauthenticated JWKS endpoint. Only `org_idp_configs` fits cleanly.

**Enable RLS on `org_idp_configs`; explicitly exempt the rest and say so here.** Isolation for
those tables comes from `user_id` scoping in application code — a different control, which
should be named rather than pretended into RLS.

Two blockers if this were ignored: the nil-UUID sentinel (`uuid.UUID(int=0)`, 6 occurrences in
`services/auth/app/services.py`) is written as `organization_id` for pre-org audit and outbox
rows, so a `WITH CHECK` policy would **break password reset**; and `refresh_tokens.organization_id`
is legitimately NULL in the pre-org window.

**The real gap in `auth` is privilege isolation, not RLS.** With one shared `atmora_app`, the
account service's connection can `SELECT * FROM auth.credentials`. Per-service login roles
(`atmora_account`, `atmora_auth`), each with `USAGE` on only its own schema, closes a hole
wider than the missing RLS. *Recommended, and cheapest to do now.*

### 7. Reconcile the GUC name

Adopt the code's `app.organization_id`; correct ADR-0000 `:121` and `CLAUDE.md:28`. A migration
author following the ADR today would write policies against a GUC nothing sets — which fails
silently, per §4.

---

## Migration sequence

**The first three steps contain no RLS at all, and steps 0–2 must land and be verified before a
single `CREATE POLICY` is written.** Adding policies while connected as a superuser produces a
change that is invisible in dev, CI, review and production alike, and every subsequent test of
the mechanism is a false pass.

0. **Fix ownership and grants.** Schemas and tables to `atmora_admin`; `USAGE` + DML to
   `atmora_app` with `ALTER DEFAULT PRIVILEGES`. Deliberately withhold `TRUNCATE`, and withhold
   `UPDATE`/`DELETE` on `audit_logs` — table privileges, not RLS, are the right tool for
   append-only. Split per-service roles here while it is cheap (§6).
1. **Split the DSN.** `ServiceSettings` gains a migration URL (`atmora_admin`) distinct from the
   app URL (`atmora_app`); `migrations/env.py` uses the former. Both read the same setting today.
2. **Wire the chokepoint** (§1) and ship it *before* any policy exists. It is a no-op against a
   database with RLS off, so it lands independently and is proven by asserting the GUC is set
   inside a request.
3. **Audit the data** — NULL `organization_id` on tables to be policied; nil-UUID sentinel rows;
   orphaned rows that would become permanently invisible.
4. **Policies per schema**, `account` first, `auth` last and minimal, with a `downgrade()`.
5. **Fix the test fixtures** — see below.

Migrations themselves do not break: DDL is not subject to RLS, and data migrations run as
`atmora_admin` with BYPASSRLS. The failure mode to watch is *ownership*, not RLS — if a table
ends up owned by anything else, `ALTER TABLE` fails.

## What breaks

**Four cross-tenant paths**, all of which must be handled before policies land:

1. `/internal/v1/memberships/{id}/grants` — called by **both gateways on every authenticated
   request**. Under RLS with no carve-out it 404s, the gateway resolves an empty grant set, and
   **every authenticated request 403s**. Worse, the empty result is cached for 60s, so the
   failure outlives the fix.
2. `/internal/v1/users/by-email` — 404 → auth maps it to `None` → **nobody can sign in, and the
   error message says the password was wrong.**
3. `/internal/v1/users/{id}`.
4. `POST /api/v1/invitations/accept` (finding 9) — public, and it **writes**. Fails closed on
   its first SELECT with a message identical to the one a forged token gets.

Paths 1–3 move to the `atmora_directory` session. Path 4 gets the better pattern, which should
become the model: **bypass for the lookup only, then narrow** — read the invitation on the
bypass session, take its `organization_id`, then open a tenant session and do every write under
policy. The bypass touches one row of one table; the writes stay enforced.

`services/account/app/api/internal.py:11-13` claims these are "the only three endpoints in the
platform that read across tenants". That is now known to be false and must be corrected — it is
the kind of claim someone will rely on.

**Tests.** `services/account/tests/conftest.py` breaks in several distinct ways: it builds its
own engine from the app DSN (which, if left as the superuser, makes every RLS assertion a false
pass — the worst outcome); it `TRUNCATE`s, which is an owner privilege deliberately withheld
from `atmora_app`; it yields a session with no GUC; and the `org` fixture creates an
organization, which no tenant GUC can name before it exists. The fixture graph has to invert:
`org` on the admin engine first, then a tenant session bound to it.

**Relay stall.** If the relay were ever run NOBYPASSRLS it selects zero rows, sleeps, and loops
forever. `attempts` never increments, no error is logged, the process looks healthy, and the
outbox grows without bound. `/health/ready` only does `SELECT 1`. **Add an outbox-lag probe
before RLS lands, so there is a baseline** — this is worth doing regardless of this ADR.

## Rollback

Three levers, cheapest first — the whole change is DDL plus a connection string, so no rows are
touched:

1. `ALTER ROLE atmora_app BYPASSRLS;` — one statement, no lock, service restored instantly with
   policies intact for diagnosis. Whoever is on call must know this exists.
2. Point `DATABASE_URL` back at `atmora_admin` — config change and restart, no database change.
3. `alembic downgrade` — drops policies; brief `ACCESS EXCLUSIVE` per table.

Making `roles.organization_id` NOT NULL is the one part that is not trivially reversible; it
goes in its own migration.

---

## Open questions for review

1. **`roles.organization_id`** — implement platform templates properly, or drop the nullability
   and take the simple policy? (§5)
2. **Per-service database roles** (`atmora_account`, `atmora_auth`) — do this now, or accept
   that account-service can read `auth.credentials` for the time being? (§6)
3. **`users`** — accept the un-policied recommendation with a compensating test, or take the
   membership-`EXISTS` policy and route user-creating writes through a bypass session? (§5)
4. **Sequencing** — steps 0–2 are a prerequisite with no user-visible payoff and real risk of
   being mistaken for the whole job. Land them as their own slice, or as one slice with the
   policies?

## Consequences

- Isolation stops depending on every developer remembering a filter, forever.
- A bypass surface is created deliberately (`atmora_directory`) that did not exist before. It is
  narrower than the superuser everything uses today, but it is a named target and needs the
  route-blast-radius test and audit logging that `record_sensitive_read` already exists for and
  nothing calls.
- The cheap emergency rollback (`ALTER ROLE ... BYPASSRLS`) is also a permanent silent disable
  if someone runs it and forgets. It should be alerted on.
- The generated cross-org acceptance suite that the delivery plan calls the acceptance gate
  becomes meaningful for the first time. Until RLS is real, that suite can only test the
  application filter.
