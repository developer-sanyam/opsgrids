# ADR-0013 — Self-serve products: sign up, pick a product, pay for it, and how one box runs them all

**Status: Accepted 2026-09-20 (design), building.** Builds on ADR-0004 (commercial model), ADR-0005
(dunning), ADR-0007 (permission rollout), ADR-0001 (frontend topology) and ADR-0012 (the edge).
Supersedes nothing; it wires together things those ADRs designed and this repository half-built.

---

## Context

The question that prompted this: *"If a customer only wants inventory management, what does the
initial setup look like? One organisation may want inventory, another WhatsApp automation; later
both may want both. Users should explore the platform, pick what they need, and be charged for it."*

Almost all of that is already the platform's shape, and it is worth saying plainly before deciding
anything, because the temptation is to design a "module system" that already exists:

- **An organisation is the tenant, and a product is an activation on it.** `product.activations`
  holds one row per organisation per product; auth-service folds those into the `ent` claim; the
  gateway answers **402** for any route of a product the organisation has not activated
  (`platform_core/gateway.py`). Proven on the edge on 2026-09-20 — step 2c of the end-to-end run.
- **A product's roles come from its own manifest and nothing else** (`libs/contracts/permissions/
  <product>.json`, CLAUDE.md). Activating a product installs *that* product's permissions and role
  templates into the organisation, through account-service's consumer of `core.product.activated`.
- **Inventory management is the `operations` product**: suppliers, warehouses, purchase orders,
  receiving, inventory on hand — a service with 9 routes, a manifest with three role templates
  (`OPERATIONS_ADMIN`, `OPERATIONS_MANAGER`, `OPERATIONS_USER`) and a standalone SPA
  (`frontend/product-operations`: Inventory, Purchase Orders, Suppliers, Overview).
- **WhatsApp automation is the `notification` service**: templates, provider templates, messages,
  Meta webhooks, delivery and read receipts, and customer-service's phone lookup. It is catalogued
  `is_platform`, which entitles every organisation for free.
- **The commercial machinery exists as services**: `catalog` (plans → immutable versions → prices,
  `flat | per_seat | usage`), `subscription` (trial/active/past_due/suspended, dunning cycles,
  change-plan, cancel), `billing` (invoices, credit ledger, GST rates), `metering`, `payment`
  (Razorpay provider with `charge`, `create_mandate`, `refund`; webhook ingestion). Subscription
  lifecycle events are consumed by product-service and *projected into the activation*
  (`product/app/services.py:project_subscription`) — so a subscription that lapses switches the
  product off. That direction — **subscription drives entitlement** — is ADR-0004's and stands.

What is missing is the loop a stranger walks through: there is **no way to sign up**
(organisations are created by platform staff through the admin gateway), **no screen that lists
products and starts one** (`GET /products` and `POST /subscriptions` exist; nothing calls them),
**no route that collects a payment method** (the provider can create a mandate; no API exposes it),
**no broker on the production box** (activation cannot install roles without `wire_product.py` by
hand), no email transport, and the two customer-facing apps ADR-0001 names — `opsgrids-dashboard`
and `opsgrids-admin` — were never created. Inventory is catalogued `soon`; nothing has a plan except
HRMS.

Decisions taken with the owner on 2026-09-20: **flat price per product per month**; **14-day trial,
payment method collected at the end**; **WhatsApp sold as an add-on line** on whatever the
organisation subscribes to, not as a product; **first deliverable is the full self-serve loop for
Inventory, deployed to the edge**.

---

## Decision

### 1. The unit a customer buys is a *subscription to a product plan*; activation is its shadow

"Activate Inventory" in the UI is `POST /subscriptions {product_key: "operations",
plan_version_id, trial_days: 14}`. Nothing calls `POST /products/activations` from a customer
surface any more; that route stays for staff (a grant, a demo) and for tests. The subscription
service publishes `commerce.subscription.trial_started`; product-service projects it into an
`activations` row with `entitles = true` and `trial_ends_at`; account-service installs the roles on
`core.product.activated`. Three services, two events, no request-time coupling — and a lapsed
trial or a failed renewal walks the same path backwards to a **402**. This is ADR-0004 §4 done
rather than described.

**Why not activation first, then subscription?** Because then the money is optional. An activation
with no subscription behind it is exactly the state that lets a customer use a product for free
forever, and it is the state every ad-hoc grant would drift into.

### 2. Flat per product per month, in the catalogue, versioned

One plan per sellable product, one published version, one `flat` monthly price in INR. Trials are
a *state of the subscription* (`trial_days`), never a plan. A price change is a new plan version
(ADR-0000 §9); existing subscriptions keep their pin until migrated. Seeded by migration for
`operations` (the first product sold this way); the numbers are deployment data and are set on the
box, not in the repository — the migration seeds a placeholder amount and the owner sets the real
one through the staff console (`opsgrids-internal` → Plans).

WhatsApp is an **add-on**: `catalog.features` already models plan features; an add-on is a
`SubscriptionItem` on the organisation's subscription with its own flat monthly price plus a
`usage` price per conversation, metered by notification-service into `metering`. It is *not* a
product key, so it needs no entitlement row and no manifest of its own: the routes stay under
`notification`, which stays `is_platform`, and the add-on gates the WhatsApp *channel* inside the
service (a provider account cannot be connected without the add-on item). This is slice 5, after
inventory; recorded here so nobody re-catalogues WhatsApp as a product in the meantime.

### 3. Sign up is one public route in account-service; the password is set by link

`POST /api/v1/account/signup {organisation name, slug, owner full name, owner email}` — public on
the gateway, rate-limited by address like the storefront's anonymous routes. In one transaction:
`create_organization` (status `trial`, 14 days — the *organisation* trial that already exists),
`add_member` with `ORG_OWNER` and `is_owner = true`. No password in the payload, ever: the route
publishes `core.user.signup_completed`, auth-service **does not** consume it — auth reads users
from account's directory on demand — and the owner sets their password through the existing
`password/forgot` → email → `password/reset` path, which notification-service delivers. Signup
therefore creates nothing in the auth schema, and cannot leak a credential it never held.

Duplicate email → `409` with the same sentence as a taken slug. Email verification *is* the
set-password link: an address that cannot receive it never gets a password.

Refused: a signup route in auth-service (it would have to write another service's schema), and
returning a session on signup (an unverified address with a live token).

**Which role writes the new organisation.** ADR-0002's RLS migration says bringing an
organisation into existence "runs as the admin role" because a tenant cannot name a GUC for an
id that does not exist yet. That is a convention, not a mechanism: the policy is `id =
current_org()` and the GUC is a string. Sign-up therefore generates the id first, binds the
transaction to it, and writes on the ordinary NOBYPASSRLS app role — every row it creates
(organisation, roles, membership, audit, outbox) carries that id and passes `WITH CHECK`, and
nothing else is reachable. A public route running as the BYPASSRLS admin would be the wider
blast radius, on the one endpoint that is open to the internet. The duplicate checks (slug, email)
run first on the SELECT-only directory session, because under the new tenant's binding the
policied `organizations` table cannot show a clash — it would surface as a unique violation.
notification's rules are per tenant and auth publishes credential events under the nil UUID, so
a seeded **platform tenant** (nil id) owns the reset-email rule and template; auth includes a
ready `reset_url` in the event so the template never learns a hostname.

### 4. Two customer apps, both static, both on the edge

ADR-0001's `opsgrids-dashboard` is created: sign up, sign in, the marketplace (every `available`
product with its plan price, a "Start 14-day trial" button that calls `POST /subscriptions`), the
organisation's subscriptions with trial-end dates, invoices, and one "Open" link per active
product. Customer admin (`opsgrids-admin`) is folded into it as `/admin` routes for now — a second
app is the right shape once there is a second team, and there is not.

Product apps are served **one hostname each**, exactly as ADR-0001's routing table says
(`ops.opsgrids.com` for Inventory; `storefront` already has its own). Caddy gets one `handle`
block per bundle keyed on host, all under the same on-demand TLS with the platform hostnames added
to `TLS_PLATFORM_HOSTS`. `publish-web.sh` learns a bundle name (`make edge-web APP=dashboard`),
and each app lands in `${EDGE_WEB_ROOT}/<app>/current`.

### 5. One box runs every service; a compose *profile* per sellable product

The edge compose grows from five services to the platform core plus one profile per product:

| Profile | Services | Always on? |
|---|---|---|
| *(none)* — core | caddy, postgres, redis, **rabbitmq**, auth, account (+worker, +relay), product (+worker, +relay), app-gateway, **catalog, subscription (+worker, +cron, +relay), billing (+worker, +relay), metering (+worker, +relay), payment (+worker, +relay), notification (+worker, +relay)** | yes |
| `inventory` | operations (+relay) | when the catalogue says `operations` is `available` |
| `storefront` | storefront (+cron once §6 of ADR-0012 exists) | when `storefront` is `available` |

The catalogue row's `status` is the commercial switch ("this deployment sells it"); the profile is
the operational one ("its service is running"). `preflight.sh` checks they agree: an `available`
product whose profile is not enabled is a 504 waiting to happen, and fails preflight.

Every outbox gets a relay process. `bin/relay` is added to the scaffold and to every service, so
the three services that repurposed `bin/cron` (ADR-0012 erratum 8) can publish at all.

Sizing: this is ~24 containers. The current 2 vCPU / 3.8 GB VM will not hold it; the box moves
to 4 vCPU / 8–16 GB before slice 1 goes live. That is a resize of the same VM (same reserved IP),
not a migration.

### 6. Trial end and money

Subscription-service's existing cron (the dunning sweep) gains a second job: **trials ending in 3
days** publish `commerce.subscription.trial_ending`, which notification emails; **trials that have
ended with no mandate** move to `past_due` and ADR-0005's grace window starts — the product keeps
working for 14 more days with a banner, then `suspended` → 402. A trial that ends *with* a mandate
is charged and becomes `active`.

The payment method is a **Razorpay e-mandate / UPI Autopay** collected from the dashboard: a new
`POST /payment/mandates` returns what the browser needs to open Razorpay checkout; the webhook
already ingested by `payment/app/api/webhooks.py` marks it activated. Razorpay keys are deployment
secrets in the box's `.env` and are the owner's to provide — the service is inert without them,
by design (`payment/app/config.py`).

### 7. What is deliberately not decided here

Per-seat or usage pricing for any product (flat was chosen; the catalogue supports the others
whenever a product wants them). Mobile apps. A second box. Staff-side approval of signups (any
address with a working inbox may create an organisation; abuse controls are rate limits and the
trial's own end).

#### Decisions on the items above (2026-09-24)

Each of the four is now decided. The register of every ADR question is
[open-questions-register.md](open-questions-register.md). This subsection sits here rather than
at the end of the document because the memory-headroom work (D4b) appends to the erratum below.

- **Per-seat or usage pricing — DECIDED (default; owner may revise the numbers).** Self-serve
  products stay **flat per product per month** (§2).
  - **Per-seat** pricing arrives only as a new plan version that sells **seat classes** (catalog
    already models `seat_classes` with `can_write`). Account enforces it through an assignment
    rule and role templates, never a second grant writer (ADR-0007, "Decisions" Q2). **Implemented
    by O2** after **C6**.
  - **Usage** pricing's first real line is WhatsApp conversations (§2, slice 5), **R4**.
  - New per-seat and usage versions are **sales-led first**: operator subscribe, as bundles are
    (ADR-0004 §5 addendum). Self-serve opens per version once one has been sold and billed
    correctly through a full period. Amounts are deployment data set by the owner in the staff
    console, never in the repository.
- **Mobile apps — DECIDED: no native app in this backlog.** "Mobile is another client"
  (ADR-0003 §6) needs the mobile token audience first, which is **R31**. Until a customer asks for
  a native app, the responsive web apps are the mobile experience. ADR-0001's one-app-per-product
  shape still applies when one is built.
- **A second box — DECIDED: not yet; when forced, the first split is the stateful tier.** The owner
  has refused a resize (the erratum below), so container count is the variable. **Trigger**, either
  of:
  - `preflight.sh` refuses a deploy and there is **no profile left with zero activations to
    de-list**;
  - ADR-0012 slice 4's triggers fire (an uptime clause, ~200 domains, `caddy_data` lost).

  **What to build then:** move Postgres, the largest single container (350 MB expected, 768 MB
  limit in `memory-budget.json`), to a managed Postgres or a second VM with the same image. Application containers
  stay on one box. That also gives backups a home off the application disk (B3). A second
  *application* box behind a load balancer is ADR-0012 slice 4's second option, and it is decided
  there with the numbers at the time.
- **Staff approval of signups — DECIDED: none; the abuse controls are the rate limit, the
  set-password link and the trial's end.** Signup stays open (§3). The per-IP anonymous limit
  becomes a real client-IP limit with a global ceiling on the unauthenticated surface (**R1**).
  **Trigger to revisit:** a wave of junk organizations, more than 20 a day from signups that never
  set a password. **What to build then:** a per-email-domain daily signup cap and an operator
  "suspend organization" override. It is never a manual approval queue, which would put a human on
  every legitimate customer's first minute.

## Erratum — the memory budget (2026-09-23)

**§5's sizing was wrong, and the plan built on top of it never happened.** "This is ~24
containers. The current 2 vCPU / 3.8 GB VM will not hold it; the box moves to 4 vCPU / 8–16 GB
before slice 1 goes live" — slice 1 shipped on the un-resized box anyway (2026-09-20), every
slice since has too, and the owner has since said explicitly: **no resize.** Nothing in this
document, or in any deploy since, ever refused to proceed for lack of one. The box is still
2 vCPU / 3.8 GB RAM, plus a 4 GB swapfile added after the fact when it started thrashing.

**The real container count**, counted directly off `compose.prod.yml` rather than estimated, is
44 with every profile this ADR names turned on at once (`.env.example`'s
`COMPOSE_PROFILES=inventory,storefront,hrms,crm,pulseboard,projects,support,marketing,finance,itsm`)
— not ~24. It was 46 before this same erratum folded `subscription-cron` and `approval-cron`
into their services' `bin/worker` (§"Levers", below). And this ADR's own §5 table already
undercounted slice 1: it named "catalog, subscription (+worker, +cron, +relay), billing
(+worker, +relay), metering (+worker, +relay), payment (+worker, +relay), notification (+worker,
+relay)" as core, but billing and metering were never given a worker or a relay of their own —
one `edge-relay` container drains every service's outbox (the runbook's "one relay, not one per
service", 2026-09-21) — so even slice 1's own table priced work that was never built. Sizing by
architecture-diagram arithmetic ("N services, each roughly M containers") is what produced both
errors; sizing by reading the compose file that actually deploys is `memory-budget.json`'s whole
reason to exist.

**The method, going forward.** `infrastructure/edge/memory-budget.json` prices every container
this platform can deploy at a measured (or, where nothing had ever been measured before this
erratum — caddy, rabbitmq — conservatively estimated and labelled as such) steady-state RSS, and
`infrastructure/edge/preflight.sh` sums it against the box's actual `COMPOSE_PROFILES` and its
live `/proc/meminfo`, refusing a deploy that would cross a hard budget line and warning above a
softer one. `tests/infra/test_compose_memory_budget.py` is the gate that keeps the file honest:
every container `compose.prod.yml` defines must be priced there, and every future slice that adds
one and forgets fails that test by name, in CI, before it reaches this box. Postgres, RabbitMQ,
Redis and Caddy — previously the four processes on this box with no `mem_limit` at all, meaning
an unbounded one of them is exactly what the OOM killer reaches for first when something else
spikes — each now carry an explicit `mem_limit` and `mem_reservation`.

**What "no resize" actually implies.** §5 treated the box's size as the variable that would move
to meet the container count. It will not. The only variable left is the container count itself,
which means every future slice's memory cost is a real design constraint, not an afterthought
for whoever eventually did the resize. Concretely, the levers are:

- **Per-service process merges.** A worker (`bin/worker`, a consumer) and a cron (`bin/cron`, a
  poll-and-drain sweep) are two containers today wherever a service has scheduled work, at
  ~40–50 MB idle apiece for two processes that are each idle almost all the time.
  `notification/app/worker.py` already runs two *consumers* in one process this way; this
  erratum applies the same shape to a consumer plus a sweep for `subscription` and `approval` —
  the two services where both existed, both had tests, and both tests passed unchanged after the
  merge (`services/subscription/app/worker.py`, `services/approval/app/worker.py`). `bin/cron`
  stays standalone-runnable in both for local debugging; only `bin/worker` changed. This is not
  free for every service with a cron — a service whose sweep needs its own scaling story, or
  whose worker and cron have genuinely different failure domains, should stay split — but it is
  free where nothing depended on the split, and it removed two containers here.
- **Moving crons onto a shared scheduler.** `services/scheduler` is still the empty scaffold §5's
  neighbour ADR-0006 §8 already deferred to it once. Every per-service `bin/cron` sweep
  (subscription's dunning ladder, approval's SLA escalation, storefront's domain sweep,
  operations' reconciler) is a candidate to become one job on one shared scheduler process
  instead of N poll loops each holding their own idle connection pool — the same argument that
  already collapsed 24 outbox relays into one `edge-relay` container (2026-09-21). Not done in
  this erratum: it is a scheduler service, not a compose edit, and deserves its own slice.
- **CI-built images, pulled rather than built on the box.** Every deploy today builds ~20 images
  on the box itself before recreating anything — real CPU and real disk I/O on a 2-vCPU box, at
  the exact moment it is also trying to keep serving traffic. Documented as an option, not done
  here: build and tag each service's image in CI (the same Dockerfiles, `docker buildx build
  --push` to a registry keyed by the deployed ref), then have `remote.sh` `docker compose pull`
  the changed images instead of `docker compose build`. Concretely this needs (1) a registry —
  GitHub Container Registry costs nothing extra given CI already runs there, (2) a
  `GHCR_TOKEN`/`GITHUB_TOKEN`-scoped push step added to `.github/workflows/ci.yml` on main after
  the gate passes, tagged with the commit SHA the same way `remote.sh` already records
  `last-good-ref`, and (3) `compose.prod.yml`'s `build:` blocks kept (a laptop or a box with no
  registry access must still be able to build directly) while `remote.sh` prefers `pull` when the
  configured registry has the tag. It removes a build-time CPU and memory spike from the box, not
  a steady-state cost — orthogonal to `memory-budget.json`, which only prices steady state — but
  it is the spike that turns "tight" into "briefly out of memory" during a deploy.
- **Turning off profiles with zero activations.** `COMPOSE_PROFILES` on the box today is every
  profile this ADR and its successors have shipped, regardless of whether the catalogue has ever
  sold a trial of that product. `preflight.sh`'s existing catalogue/profile agreement check
  (§5, "must agree") only ever catches a product sold with no profile running — it has nothing to
  say about the opposite waste, a profile running for a product with zero organizations ever
  activated. That check does not exist yet; it is the next thing this budget should grow, because
  it is the only lever here that costs nothing to ship (no code change to a product, no new
  service) and gives back a whole container group.

**What this does not fix.** Even with both merges landed, `.env.example`'s full profile list
prices at ~3.4 GB expected steady-state RSS against this box's 3.8 GB — inside `preflight.sh`'s
warn band, not its refuse one, which matches what the box has been doing since 2026-09-21: it
runs, and it swaps. The remaining lever that actually buys headroom, rather than redistributing
it, is turning off a profile nobody has activated — or the resize this erratum's own title says
will not happen.

---

## The plan, sliced

Each slice ends green through the full gate and deployed to the edge, or is reverted.

| # | Slice | Delivers | Blocks on |
|---|---|---|---|
| 1 | **The broker and the workers** | rabbitmq, relays and consumers for core; catalog/subscription/billing/metering/payment/notification on the box; `bin/relay` everywhere; profile `inventory` with operations; routes.prod.json; VM resized | VM resize (owner) |
| 2 | **A stranger can sign up and sign in** | `POST /account/signup`; set-password email through notification over SMTP; gateway public paths; the dashboard app's signup/sign-in/marketplace pages at `app.opsgrids.com` | SMTP credentials (owner) |
| 3 | **Inventory is a product you can start** | `operations` catalogued `available` as "OpsGrids Inventory"; a flat monthly plan; "Start trial" → subscription → activation → roles, all by events; Inventory SPA at `ops.opsgrids.com`; billing permissions added to the core manifest (audit blocker) | slice 2 |
| 4 | **The trial ends and money moves** | trial-ending mail; `POST /payment/mandates`; Razorpay checkout in the dashboard; trial→active charge; past_due/suspended per ADR-0005 | Razorpay keys (owner) |
| 5 | **WhatsApp as an add-on** | add-on item + metered conversations; the channel gated on the item | slice 4 |

The end-to-end suite from 2026-09-20 grows one journey per slice, and runs against the edge.
