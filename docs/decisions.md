# Decisions

An index of the architecture decision records, and the short version of the first six. The ADRs
themselves are the authority — this page exists so you can find the right one quickly.

**Every question an ADR left open is decided or recorded as waiting on demand** in
[adr/open-questions-register.md](adr/open-questions-register.md). That page links each question
to the ADR section that decides it and to the backlog item that builds it. Numbers are allocated in
order and never reused. The next free number is **0041** (0027 is allocated to the inventory Phase 9
work, which has not merged; 0038 and 0039 are left for the other branches in flight when 0040 was written,
each of which adds an ADR). This table was last brought up to date at 0019, apart from 0028–0033
and 0035–0037 and 0040 below; [`adr/`](adr/) is the complete list.

| ADR | Title | Header status | What exists today |
|---|---|---|---|
| [0000](adr/0000-phase-0-decisions.md) | Phase 0 — eleven irreversible decisions | Accepted | In force |
| [0001](adr/0001-frontend-topology.md) | One standalone app per surface | Accepted | Built; the marketing site moved out (amendment) |
| [0002](adr/0002-row-level-security.md) | Row-level security | Accepted 2026-08-22 | Built: forced RLS policies across every service's migrations; header's "not yet implemented" predates it |
| [0003](adr/0003-surfaces-and-authorization-topology.md) | Surfaces and authorization topology | Accepted | In force; the operator-roles addendum is in flight with the staff console (Q1) |
| [0004](adr/0004-commercial-model.md) | Commercial model — plans, subscriptions, billing | Accepted 2026-08-24 (design) | Built: catalog, subscription, billing, metering, payment; the billing run is in flight (R2); the tax direction is decided and waits on O6 |
| [0005](adr/0005-dunning-and-grace.md) | Dunning and the grace window | Accepted | Built; the billing-run addendum is in flight (R2) |
| [0006](adr/0006-approval-engine.md) | The approval engine | Proposed (header) | Built in slices (`services/approval`) |
| [0007](adr/0007-permission-rollout-to-live-tenants.md) | Delivering a new permission to live tenants | Proposed (header) | Built: `tools/reconcile_permissions.py`, treated as accepted |
| [0008](adr/0008-membership-employee-link.md) | Who writes `memberships.employee_id` | Proposed, implemented the same day | Built; the linking screen is not (register row 16) |
| [0009](adr/0009-uploaded-images-and-object-storage.md) | Uploaded images and object storage | Proposed (header) | Built: document service, the edge MinIO, purge and orphan sweeps (addenda) |
| [0010](adr/0010-delivery-serviceability-and-slots.md) | Delivery serviceability and slots | Proposed (header) | Slice 1 built (`services/storefront`); slice 2 is R16 |
| [0011](adr/0011-store-identity-brand-domain-and-publishing.md) | Store identity, brand, publishing | Accepted | Built |
| [0012](adr/0012-custom-domains.md) | Custom domains and the one-box edge | Proposed (header) | Slice 1 built and deployed; slice 3 is R14 and R15 |
| [0013](adr/0013-self-serve-products-and-the-one-box-deployment.md) | Self-serve products and the one-box deployment | Accepted (design), building | Slices 1–3 built; 4 is R3, 5 is R4; the memory addendum is in flight (D4b) |
| [0014](adr/0014-inventory-and-manufacturing.md) | Inventory and manufacturing | Accepted | Phases 0 and 1 built; 2–10 are R30 |
| [0015](adr/0015-scheduler-and-not-before-delivery.md) | The scheduler and `not_before` delivery | Accepted | Built |
| [0016](adr/0016-egress-guard.md) | The egress guard | Accepted | Built |
| [0017](adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md) | CRM: `lead` joins `crm`; `pipeline` and `activity` removed | Accepted | Built (Q3, merged 2026-09-24) |
| [0018](adr/0018-domain-verification-lifecycle.md) | Email-domain verification lifecycle | Accepted | Built (P1) |
| [0019](adr/0019-statutory-portal-credentials.md) | Statutory portal credentials live in finance | Accepted 2026-09-24 | Not built; B5 (5b) |
| [0028](adr/0028-putaway-gps-captures.md) | Storage put-away: GPS captures are audit evidence, kept 30 days | Accepted 2026-10-05 | Built on `feat/warehouse-map-putaway`, not deployed; the admin-run purge is `tools/purge_putaway_captures.py`. The reveal's one-time-code requirement was added in review and is an open owner decision (keep or revert) |
| [0029](adr/0029-staff-plan-control.md) | Staff plan control: what staff may change, and what every change is made of | Accepted 2026-10-06 | Built on `feat/admin-plan-switch` (hardening and plan switching), not deployed; cancel is a later release, add-product and extend-trial are ADR-0031 and the Plans page is ADR-0030. Amends ADR-0005 addendum §4 (a deferred change's event is labelled by direction) |
| [0030](adr/0030-catalog-operator-writes.md) | Catalog operator surface: role-split reads, reasoned idempotent writes, withdraw | Accepted 2026-10-06 | Built on `feat/admin-plans-page` (the console Plans & prices page), not deployed. Retire and withdraw do not yet stop a pin by id or a scheduled change; both are deferred subscription work |
| [0031](adr/0031-staff-add-product-and-trial-extension.md) | Staff Add product and Extend trial | Proposed 2026-10-06 (for owner sign-off) | Built on `feat/admin-add-product-trial`, not deployed. New event `commerce.subscription.trial_extended`; deploy its consumers first (RUNBOOK) |
| [0032](adr/0032-payment-provider-seam-and-checkout.md) | Payment provider seam: Razorpay and Cashfree, one customer-present checkout, no mandate debit yet | Accepted 2026-10-06 | Built on `feat/payments-phase-a`, not merged or deployed: every switch defaults to the stub (`PAYMENT_PROVIDER`, `PAYMENT_WIRE`, `CASHFREE_ENV`), webhooks stay private and the platform polls; amends the ADR-0004 §6 addendum. Nothing is verified against a provider until the owner runs `tools/payments_smoke_*.py` |
| [0033](adr/0033-merchant-connect-collections-seam.md) | Merchant connect: a standalone collections seam, and Cashfree as a second provider | Proposed 2026-10-06 | Built on `feat/merchant-cashfree` (on top of payments Phase A, ADR-0032), not deployed; Cashfree's wire stays `stub` until the owner's sandbox run |
| [0035](adr/0035-list-prices-2026-10-06.md) | List prices 2026-10-06: INR 30,000 a month, INR 3,00,000 a year (the year ten months' price), for every product | Accepted 2026-10-06 (owner decision; both numbers confirmed by the owner, the INR 25,000-a-month reading rejected) | Built on `feat/list-prices-30k-month-3l-year`, not deployed: catalog migration `b88a833020dc` publishes a yearly and a monthly version of each of the eleven public flat plans (refusing on an open public draft); the customer subscribe write refuses a superseded version; console counts a yearly price as a twelfth in MRR; publishing refuses a version superseded on arrival; existing subscriptions keep their pinned price until staff switch them; monthly-to-yearly is not possible yet (a cancelled subscription cannot be replaced) |
| [0036](adr/0036-removing-test-organisations.md) | Removing test organisations: an owner-run tool, never an in-app delete | Accepted 2026-10-06 (owner request) | Built on `feat/purge-test-orgs-tool`, not merged or deployed: `tools/purge_test_orgs.py` lists every organisation with a test-data hint and deletes only the ids the owner names, one transaction each, after `--apply`, a typed count, a backup attestation and (on a live database) its own flag; run by the owner on the box (RUNBOOK "Removing test organisations"), never by CI, a deploy or an agent |
| [0037](adr/0037-stop-events-and-the-hand-activation-gate.md) | Stop events and the hand-activation gate | Proposed 2026-10-06 (for owner sign-off) | Built on `fix/subscription-stop-events-and-activation-gate`, not merged or deployed. No migration, no event change: a stop that finds a lapsed pending change applies it without announcing it, so product and console can no longer drop the stop; `POST /products/activations` (and a live subscription's `DELETE`) answers 409 `subscription_managed` for a product a subscription has fed, in any status. Read-only audit query for orgs that already hand-activated: RUNBOOK |
| [0040](adr/0040-faster-ci.md) | Faster CI: service tests sharded by item, a tuned throwaway database, static checks in parallel | Accepted 2026-10-06 (owner request) | Built on `ci/faster-pipeline`, not merged. `tools/ci_shards.py` cuts a heavy service (`operations`: 19 min alone) into parts by recorded per-file duration and deals the items to six shards, never two parts of one service to one shard (so never one database); the CI Postgres runs without fsync; the static job is three jobs and every row reports; migrations are one job; the frontend is a static job plus four legs; docs-only changes skip the database-backed jobs; a push to main is never cancelled; the deploy plan does nothing when the box is already past the commit. First sharded run 13.0 min against 32.5 (measured); the second round is unmeasured because Actions refused to start jobs |

Several header status lines (0002, 0004, 0006, 0007, 0009, 0010, 0012) were written before the
work they describe shipped. The "What exists today" column is the current fact. Correcting the
headers themselves belongs to the stale-docs sweep (R35).

---

## ADR-0000 — the eleven that cannot be revisited casually

These were settled before service code existed, because each is cheap now and expensive or
impossible later.

1. **A user is global; membership is per-organization.** Retrofitting multi-org onto a per-org
   user is the largest migration available.
2. **EdDSA + JWKS; only auth-service mints.** Changing the signing scheme after tokens exist is
   a forced global logout.
3. **Per-audience tokens.** A product token must not work on the operator surface.
4. **No permission list in the token; a `pv` claim instead.** Adding a claim later is a forced
   global logout. A fat token exceeds proxy header limits and fails as a silent 500.
5. **Permission grammar `product.module.resource.action:SCOPE`.** Once customers author custom
   roles, renaming this is a migration of *customer* data.
6. **Event envelope v1 with correlation and causation.** Consumers built on a thin envelope can
   never be traced afterwards.
7. **Money in minor units.** `Numeric(_, 2)` breaks on JPY and KWD and on exact proration, and
   is unfixable once invoices exist.
8. **Plan versions immutable; subscriptions pinned to a version.** Otherwise one price edit
   re-prices every customer and makes past invoices unreconstructable.
9. **Shared schema + `organization_id` + RLS.** Never a database per tenant. Determines whether
   isolation is a query concern or connection routing.
10. **UUID preservation for migrated PulseBoard data.** The difference between one column and
    ninety-one models.
11. **PII key management and rotation.** Rotating with encrypted rows already in place is a
    project, not a task.

## ADR-0001 — one app per surface

Each product and each console is a standalone frontend rather than one monolithic SPA with
routes. Next.js where SEO and static rendering matter (marketing), Vite + React for signed-in
apps. Shared code lives in `opsgrids-ui`, `opsgrids-client` and `opsgrids-config`.

The cost is real — more build config, a risk of visual drift — and the mitigation is the shared
packages. Both are recorded in the ADR.

## ADR-0002 — row-level security (accepted; since built)

Documents that RLS is inert for three independent reasons: no policies exist, the application
connects as a superuser (which bypasses RLS unconditionally), and the tenant GUC is never set
because `set_tenant` has no callers.

The decision that matters for reviewers: **enabling policies alone is a silent no-op** — it
looks done, tests pass, nothing is enforced — which is worse than the status quo, because the
query filter is currently the only defence and everyone knows it.

Four questions were left open at first and are now answered, two of them by experiment: drop the
nullable column that stood for platform role templates, split the database role per service now,
leave `users` un-policied because Postgres applies a SELECT policy to `INSERT ... RETURNING`, and
land the prerequisites as a slice containing no policies at all.

## ADR-0003 — surfaces and authorization topology

There are three viewers — operator (OpsGrids staff, cross-tenant), organization admin (their own
org), employee (their own data) — and they are separated by **two different mechanisms**, which
this ADR refuses to conflate. Operator-vs-customer is an **audience + gateway** boundary: the
operator console is its own app on `admin-gateway` with a separate token `aud`, and staff routes
answer **404, not 403**, so the surface will not confirm its own existence to a product token.
Org-admin-vs-employee is **not a separate app** — it is the permission **scope**
(`OWN/TEAM/LOCATION/ORG`) from the grammar, enforced server-side (RLS + `ScopeContext` + a
negative-authz test per endpoint); the same product SPA renders an admin's whole org and an
employee's own record from identical code. A UI gate hides a control; it does not protect data.
Refines ADR-0001: one SPA per **product** (not per service), shared `opsgrids-ui`/`opsgrids-client`/
`opsgrids-config`, an entitlement-driven shell (`opsgrids-dashboard`), and mobile as one more client
on the mobile audience over the same APIs.

## ADR-0004 — commercial model (accepted design; the five services since built)

Commerce is five services split by what each owns and how fast it changes: `catalog` (immutable
plan versions, features, prices), `subscription` (the customer lifecycle state machine, where
entitlement is decided), `metering` (append-only usage — emitted now with `is_billable=false` so
it need not be backfilled later), `billing` (append-only invoices, proration, the credit ledger),
and `payment` (**Razorpay first** — the more constrained provider). **Per-customer custom pricing
is free**, because ADR-0000 §9 already gives it: a bespoke plan version pinned to one
subscription prices one customer without re-pricing anyone else or breaking past invoices. Grants,
discounts and comp-access-with-expiry are **append-only credit-ledger entries** — balance is
`SUM`, "who is on comp and when it lapses" is a query, and money is integer minor units with no
floats. A trial is a subscription **state**, conversion is a transition, and every transition is
an event the operator console reads for revenue, churn and usage. Commerce invents no new
enforcement point: it drives the existing `product.activations` row → `ent` claim → gateway 402.

## ADR-0005 — dunning and the grace window

Answers the first thing ADR-0004 left Open, and replaces the MVP default it left in the code: a
failed payment used to suspend **immediately**. It now opens a **14-day grace window** and a ladder
of **three retries at T+3d, T+7d and T+12d**, all anchored to the stored instant the first failure
arrived — never `now + interval`, so a sweep that runs late does not push the ladder out.

The two loads-bearing meanings: **`past_due` keeps entitling** (a grace window that takes the
product away is a suspension with a nicer name, and makes the retries theatre), and **suspension is
a scheduled act at the window's close**, not a reaction to a failure. The last attempt sits two days
*inside* the window on purpose, so "your access ends on Thursday" is a sentence the platform can
send while it is still true. A captured payment recovers to `active` from either rung — `past_due`
or `suspended` — and **nothing is ever cancelled by a timer**, because `cancelled` is terminal and a
machine must not take an irreversible commercial act on a schedule.

The spacing is set by the rails, not by taste: Razorpay mandate caps, a pre-debit notice a clear day
ahead, multi-day eMandate activation, and **no native idempotency key** — which means a retry must
carry a *new* platform key per attempt, or it is deduplicated into the charge that already failed
and debits nobody. Scheduling is a **due-work table drained by a sweep** (`bin/cron`'s first real
job), not the empty `scheduler` scaffold and not a broker delay: the schedule stays queryable,
correctable and backed up with the money it defends.

---

## Decisions recorded outside an ADR

Not everything warrants an ADR, but these shape daily work and are easy to trip over:

- **Commit messages name no AI assistant** — no generated-by footers, no co-author trailers.
- **The `.claude/` build harness is local tooling and is not tracked.** It stays on disk; the
  backlog it used to carry now lives in [backlog.md](backlog.md).
- **Tests run against real PostgreSQL, never SQLite.** The schema leans on `citext`, partial
  unique indexes and JSONB; a test passing on SQLite proves nothing about production.
- **Each service is run, tested and typechecked in its own process,** because every service
  exposes a top-level `app` package and only one can be importable at a time.
