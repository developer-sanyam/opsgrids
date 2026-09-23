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

---

## Addendum — memory headroom, measured (2026-09-23)

The previous erratum named the levers; this slice pulled two of them, measured the box for real
instead of estimating it, and found the estimate had been wrong in a way that mattered.

### The measurement

**Method.** For each of the two container *kinds* `memory-budget.json` prices (`api`,
`background`), the real production Dockerfile image was built (`docker build`, the same
Dockerfile `compose.prod.yml` uses) and run against an **isolated** Postgres/Redis/RabbitMQ — a
fresh `docker network`, fresh containers, `tools/dev-bootstrap/init-db.sql` applied to a
database created for the purpose (never the shared development database, and never a database
one of these processes could see another session's real traffic through). The container was
warmed with a few dozen health-check probes, then `docker exec <container> cat /proc/1/status`
was read for `VmRSS` once the number stopped moving. `account` (built as both the `api` image and
`python -m app.consumers` for `background`) and `product` (`python -m platform_core.edge_relay`,
`background`, draining all 27 schemas at once) were the two services measured — chosen because
one is a small, ordinary service and the other is the platform's single busiest background
process, bracketing the range.

| kind | previous `expected_rss_mb` | measured | new `expected_rss_mb` | verdict |
|---|---|---|---|---|
| `api` (account) | 85 (range 70-100, never independently confirmed) | 92-95 MB VmRSS | 85 (unchanged) | within range — not wildly off |
| `background` (account-worker, one consumer) | 42 (range 35-50, a comment's guess, never measured) | 88 MB VmRSS | 85 | **wildly off — corrected** |
| `background` (edge-relay, 27 schemas) | 42 (same guess) | 93 MB VmRSS | 85 | **wildly off — corrected** |
| `caddy`, `postgres`, `rabbitmq`, `redis` | unchanged | not remeasured this slice | unchanged | out of scope — see "left undone" |

**Why background was wrong, and why the two measured values agree with each other.** A consumer
or a relay imports almost the same module graph an API does — SQLAlchemy's async engine,
pydantic-settings, structlog, cryptography (PII), aio-pika — because that graph is where the
service's actual business logic (`services.py`, `selectors.py`, `models.py`) lives, and a worker
needs that code exactly as much as the API does. The only thing it does *not* import is the
ASGI/uvicorn stack, which turns out to be a smaller share of the total than the "consumers are
lighter" assumption implied. The old 35-50 MB figure traces to a compose comment ("idle consumers
at ~35 MB each") that was itself never measured — it was carried forward from before any of this
platform's services existed in their current, dependency-heavy form. This single correction — not
a new container, not a new product, just pricing an existing container kind at what it actually
costs — moved the full-"every profile"-at-once total from ~3589 MB (94%) to ~4149 MB (109%): a
combination that was already, on paper, passing preflight's hard line is not one that should have
been.

> **Corrected by the review's fix round 2 (2026-09-24) — the 4149 MB / "109% today" figure is the
> price of *this release's compose file*, not of what the box runs today.** A read-only look at the
> production VM (MemTotal 3862 MB, about 2 GB of swap in use, 40 containers running under
> `com.docker.compose.project=opsgrids-edge`) priced the containers actually running at **3494 MB —
> 90%** *(round 3 corrects this: that figure left out two running containers the budget had no
> entry for; by the file's own rule they are priced as `background`, which makes it 3664 MB /
> 94.9% at those prices, and 4044 MB / 104.7% at the corrected ones — see "Fix round 3")*. The 4149 MB figure is what that same box would run once *this release's* compose file
> (47 services, ten profiles) is deployed: the extra ~655 MB is `document`, `document-cron`,
> `minio`, `leave`, `leave-worker`, `scheduler`, `operations-cron` and `storefront-cron`, less the
> `approval-cron` and `subscription-cron` that earlier merges removed. **This release creates the
> overcommit; history did not.** See "Fix round 2 review" below for what was done about it.

**What this does not mean.** It does not mean the correction made the box smaller or the
situation worse on its own — the price of a `background` container was always ~85 MB; the number
describing it was wrong. It explains, better than the previous number did, why a box running
~90% of its RAM in mostly-idle Python processes has been swapping: "94% of 3.8 GB, mostly idle"
should not swap; ~90% of 3862 MB plus a busy Postgres does.

### The decision

1. **Correct `kinds.background` from 42 → 85 MB**, matching `kinds.api`, with the measurement
   above cited in `memory-budget.json` itself. Not "wildly off in a way that helps" — the honest
   number is worse news, and reporting it is the point of this file existing.
2. **Merge `notification-cron` into `notification-worker`**, the same shape ADR-0013's original
   erratum already used for `subscription` and `approval`: `app/cron.py`'s `CampaignDriver`
   joined the `asyncio.gather` in `app/worker.py`, and gained the same poisoned-step guard
   `scheduler.app.cron.Sweep.run()` uses (ADR-0015 review finding) — required here, and not
   optional the way it might look in isolation, because sharing a `gather` means an uncaught
   exception in the driver's discovery query would otherwise take the event consumer and the
   dispatcher down with it too. One fewer container; ~85 MB back at the corrected price.
3. ~~**Zero-activation profile auto-off**, implemented rather than left as a lever on paper.~~
   **Corrected by the review's fix round (2026-09-24, H2) — this was never actually a lever.**
   The version described here computed `COMPOSE_PROFILES` from `product.activations` when neither
   it nor `EDGE_PROFILES` was set. The review caught the flaw this text missed: `docker compose
   up` does not stop a container whose profile is no longer in `COMPOSE_PROFILES` — it only
   starts what a *newly enabled* profile adds. A computed value here never turned anything off; it
   only changed what this script *believed* was running, which is a worse failure mode than
   leaving the box alone. See "Fix round 1 review" below for what replaced it: `EDGE_PROFILES`/
   `COMPOSE_PROFILES` stay fully explicit, and the activations query became a pure advisory that
   never mutates either.
4. **`.env.example`'s `COMPOSE_PROFILES` ships empty**, not "every product at once." The old
   default was already wrong before this slice (§5's own sizing was wrong, per the previous
   erratum); it is now actively unsafe to hand a fresh box, because the corrected numbers put
   that combination over the hard budget line outright.
   `tests/infra/test_compose_memory_budget.py::test_every_sellable_profile_at_once_is_refused_
   not_silently_allowed` asserts this is refused on purpose — a regression there is not a bug in
   the test, it is a reason to revisit whether `.env.example` should default wider again.
5. **`host.soft_budget_percent` raised 80 → 85**, to express the backlog's own 15%-margin target
   as the number preflight actually warns against, and **`preflight.sh` reads both thresholds
   from `memory-budget.json` instead of a second, hardcoded copy** — the previous hardcoded `80
   100` would have silently stopped matching the file it claimed to be reading from the moment
   this line changed.
6. **A `planned` key in `memory-budget.json`** prices backlog items that have no code and no
   `compose.prod.yml` entry yet, at their kind's measured price, so headroom for what is coming
   is a number instead of a feeling. `preflight.sh` reports it informationally (never gates a
   deploy on it — a placeholder must not be able to fail a real deploy);
   `tests/infra/test_planned_headroom.py` is the gate that keeps the projection honest.

### The projected budget

**Corrected by the review's fix round (2026-09-24, B1(c)) — the premise below was wrong.** This
section originally priced against "core plus `inventory` alone," on the premise that Inventory
was the only product this platform had ever sold. The coordinator's own review corrected that:
**production runs all ten profiles** — every product this platform catalogues `available` — on
the box's own explicit `COMPOSE_PROFILES`. The corrected numbers, against what the box actually
runs:

| scenario (as of fix round 1; superseded by the round-2 table below) | expected MB | % of 3800 MB reference | verdict |
|---|---|---|---|
| core only | 1964 | 52% | ok |
| **all ten profiles on this release's compose file** (round 1 called this "what the box actually runs today" — it is not; see round 2) | 4149 | **109%** | over the hard line **before a single planned service is added** |
| + every `planned` backlog item (ops-monitor, CRM lead api + customer-worker, payroll, recruit, config-service, operator/staff console, analytics, marketplace, workflow) | 5217 | **137%** | over the hard line |

**There is no honest number under the soft line here.** Ten profiles on this release's compose file
are over the hard line, independent of anything still on the backlog. Round 1 answered that by
grandfathering the overcommit; round 2 found the premise ("the box already runs this") false and
replaced the grandfather clause with an explicit owner ceiling — see "Fix round 2 review" below for
the numbers as of that round (3894 MB after consolidation; **superseded by round 3's 4581 MB on the
merged tree with corrected prices**) and the decision that remains the owner's: more RAM, de-listing a product, or accepting the overcommit in
writing.

### What this does not fix — left for the owner, or a future slice

- **`caddy`, `postgres`, `rabbitmq` and `redis` were not re-measured this slice.** They are
  estimated (caddy) or measured against their own compose comment (postgres, rabbitmq, redis)
  from before this slice existed. If a future slice re-measures them with the same isolated-image
  method used here and finds a similar gap, the projected numbers above move with it.
- **~~`operations-cron`, `storefront-cron` and `document-cron` have no worker to merge into~~
  — resolved in fix round 2: each sweep now runs inside its own service's API container** (see
  "Fix round 2 review"). What follows is the round-0/1 analysis, kept as the record of why the
  *scheduler* route was rejected. None of those three services had a consumer to merge into.
  ADR-0015's scheduler is the named candidate for absorbing a pure time-triggered sweep like
  these, but ADR-0015 §"Deliberately out of scope" explicitly deferred "making scheduler platform
  core" until a second producer needs it outside the `finance` profile. Moving these three sweeps
  onto `scheduler` is a real design change (scheduler would need to run under
  `inventory`/`storefront`'s own profiles or move to core, and each sweep's actual business logic
  — reconciliation, TLS probing, S3 purging — would need to reach `scheduler`'s image somehow,
  unlike `edge-relay`'s generic, schema-blind outbox drain), not a wiring change.
- **CI-built, registry-pulled images** (the previous erratum's own suggestion) would remove the
  build-time CPU/memory spike a deploy causes on this box, which is orthogonal to the steady-state
  numbers above but is the spike that turns "tight" into "briefly out of memory" during a deploy.
  Still not done; still real.
- **`services/lead`, `services/customer` (its worker), `services/deal`, `services/subscription`,
  `services/billing` and `services/account`** are out of scope for this slice by instruction —
  `subscription` explicitly (the billing slice owns it and routes changes through its own
  process); the others simply had no reason to be touched this round. One concrete, in-scope-
  shaped follow-up for whoever owns `customer`/`deal` next: neither has a worker at all today —
  when Q3's `customer-worker` (already priced in `planned`) lands, give it the campaign-driver
  treatment from day one (one background container using `platform_core.supervise.GuardedSweep`
  from the start, not a worker-plus-cron pair to merge away later). `approval`'s own
  `EscalationSweep.run()` (merged into its worker by the *previous* erratum, before either round
  identified the poisoned-step guard pattern) was fixed in the review's fix round — see below.
  `subscription`'s `DunningSweep.run()` still has the identical gap and was deliberately not
  touched: if it does not already catch an exception out of `tick()` the way
  `scheduler.app.cron.Sweep.run()`, `notification.app.cron.CampaignDriver` and (now)
  `approval.app.cron.EscalationSweep` do, a discovery failure in it would currently take
  `subscription-worker`'s payment-event consumer down with it — the billing slice's own call to
  make, using `platform_core.supervise.GuardedSweep`.
- **`ops-monitor`** (slice/ops-monitor, e812d44, in review) is priced in `planned` at the
  coordinator-supplied 48 MB and is not otherwise touched by this slice — its own code and compose
  block belong to that slice, not this one.

---

## Fix round 1 review (2026-09-24)

The coordinator's review of the addendum above found the "84% projected" conclusion rested on a
wrong premise, one real correctness gap the addendum's own merges introduced, and a lever
("zero-activation profile auto-off") that never actually worked. Findings, by label:

**B1 — production runs all ten profiles; corrected numbers refuse every deploy.** The addendum's
own "84% is the honest number" assumed Inventory was the only product sold. It is not: production
sells everything this platform catalogues `available`, on the box's own explicit
`COMPOSE_PROFILES`, and the corrected `kinds.background` price alone puts that at 109% — over the
hard line before a single planned service is added (see "The projected budget" above).
Decision at the time: **grandfather, do not de-list.** *Round 2 reversed the grandfather half of
this — read (a) as history, and "Fix round 2 review" below for what replaced it. The "109%" was
also mislabelled "what the box actually runs today": it is what this release's compose file would
run; the box runs ~3494 MB (90%) — round 3: 3664 MB / 94.9% at those prices once the two
unpriced `*-cron` containers are counted, 4044 MB / 104.7% at the corrected ones.*
- **(a) ~~Grandfather clause.~~ Removed in round 2 (B2).** Round 1 shipped
  `grandfathered_verdict` (`infrastructure/edge/lib/memory-budget.sh`), which downgraded an
  over-hard-line verdict to a `warn` when the deploy was not more than 1% worse than the last one
  that went healthy, using `${EDGE_STATE_DIR}/edge-budget.last-ok` as the bar. The re-review
  showed why that is wrong: on the real box there is no `last-ok` file (so the clause never
  applied and the first deploy refused), and if there had been one, *whichever deploy happened to
  succeed last* — not a person — would have set the ceiling, with a 1% creep per deploy. The
  function and its eight tests are gone; the replacement is the explicit, static owner ceiling
  `EDGE_BUDGET_ACCEPTED_MB` (below). `last-ok` is still written, and is informational only.
- **(b) ~~Consolidate `operations-cron`/`storefront-cron`/`document-cron`: not done this round.~~
  Done in round 2**, by a different route than the two round 1 evaluated and rejected
  (moving the sweeps into `scheduler`, or a multi-app host): each sweep now runs as a supervised
  background task **inside its own service's API container**, so no service imports another's
  code and CLAUDE.md's `import app` ambiguity never arises. See round 2 below.
- **(c) False premises fixed.** This document's own "only inventory sold" claim is corrected
  above (the projected-budget table and the decision-list strikethrough on auto-off).
  `tests/infra/test_planned_headroom.py`'s `REALISTIC_PROFILES` is gone — replaced by
  `_real_production_profiles`, which reads `memory-budget.json`'s own `profiles` list (all ten)
  instead of hardcoding `{"inventory"}`; its two headline tests
  (`test_current_production_is_at_the_hard_line_so_the_owner_decision_is_required` — renamed in
  round 2 from `..._is_grandfathered_not_a_fresh_regression` —
  `test_realistic_today_plus_every_planned_service_projection`) assert the honest numbers rather
  than a fit that never existed. RUNBOOK.md's rollout section is updated to say every sold
  product's profile must be listed explicitly (see M4 below).

**H2 — the zero-activation "auto-off" never actually stopped a container.** `docker compose up`
starts what an enabled profile adds; it does not stop what a disabled one used to run (an
"orphan," in compose's own vocabulary, is a service removed from the *file*, not one merely
filtered out by profile). The addendum's `resolve_profiles` (`remote.sh`) and its computed-
COMPOSE_PROFILES fallback in `preflight.sh` therefore never changed what was running — only what
these scripts *believed* was running. Decision: **removed entirely.** `EDGE_PROFILES`/
`COMPOSE_PROFILES` are now always explicit, resolved by one shared, pure function,
`resolve_declared_profiles` (`infrastructure/edge/lib/active-profiles.sh`) — EDGE_PROFILES wins
when set, COMPOSE_PROFILES otherwise, never a computed third value — used identically by
`preflight.sh` and `remote.sh`'s own `resolve_profiles`, so the two can never quietly disagree
about what "declared" means (tested by `tests/infra/test_active_profiles.py`'s
`TestResolveDeclaredProfiles`). The activations query survives as a **pure advisory** with two
distinct signals: a product with a real activation whose profile is not enabled now **refuses**
the deploy outright (a paying customer would lose their product); a profile enabled with zero
activations for a product the catalogue does not even sell is **reported as information**, never
forced. `preflight.sh` also now prices the memory budget by the containers **actually running**
(`docker ps --filter label=com.docker.compose.project=opsgrids-edge`), not by the declared profile
list, for the same reason the auto-off never worked: a box carrying orphaned containers from a
profile someone believes they turned off must still be priced for what it costs, not for what
`.env` says it should cost. *(Round 2, B1, refined this: pricing only what runs today prices the
past — a deploy that adds a service was gated on a set that did not contain it. The gate now
prices the post-deploy set, a union of the running containers the new compose still defines and
whatever the declared profiles configure. See below.)* `tests/infra/test_memory_budget_totals_containers.py` pins the new
`expected_total_for_names` function this uses, including
`test_orphaned_container_from_a_disabled_profile_still_counts` — the exact scenario this finding
is about.

**H3 — an activations query failure must not read as zero activations.** Fixed alongside H2:
`preflight.sh` now checks the query's own exit code before doing anything with its result: a
non-zero exit prints a `warn` naming the psql exit code and skips the advisory entirely, rather
than treating an empty (because the query never ran) result as "nothing is activated" — which,
under H2's new refuse-on-unserved-customer rule, could otherwise turn a transient database hiccup
into a false refusal, or worse, a false "safe to trim this profile" info line. No dedicated unit
test (the branch is a single `if [ "$activations_rc" -ne 0 ]` in `preflight.sh` itself, not a
library function extracted for it — see "left undone" below).

**M4 — every sold product's profile must be listed explicitly.** A direct consequence of H2:
RUNBOOK.md's memory-budget section documents `EDGE_PROFILES`/`COMPOSE_PROFILES` as always
explicit, and its rollout steps no longer describe an auto-computed value.

**H5 + M6 — `platform_core.supervise`: a real supervision primitive, not two more `asyncio.gather`
calls.** New module, `libs/platform-core/src/platform_core/supervise.py`:
`run_supervised(**named_coroutines)` runs every coroutine as a task and, the instant any one of
them finishes — by returning *or* raising — cancels every other task and awaits its cancellation
before raising (the first task's own exception, or `LoopExited` naming which loop merely
returned). Plain `asyncio.gather` propagates the first exception but does not cancel the others,
which keep running as orphaned tasks against a process already tearing down.
`GuardedSweep`, the same module, generalises `scheduler.app.cron.Sweep.run()`'s poisoned-step
guard: a `tick()` that raises is logged and backed off exponentially (capped, reset on the next
clean tick) rather than propagating; `asyncio.CancelledError` is never caught. Adopted in
`notification` (`app/worker.py`'s three loops now run through `run_supervised`; `CampaignDriver`
now extends `GuardedSweep` instead of carrying its own copy of the guard) and `approval`
(`app/worker.py` likewise; `EscalationSweep` now extends `GuardedSweep`, fixing the exact
unguarded-discovery gap the review named at `app/cron.py:118-119` and `:147-157` — a
DB-restart-shaped exception from `due_escalation_steps` used to propagate straight out of the old
inline loop and, since the previous erratum's own merge, would have taken the hierarchy/
regularization consumer down with it too). **`subscription` was deliberately not touched** — the
billing slice owns it. Tests: `libs/platform-core/tests/test_supervise.py` (9 tests: a returning
coroutine ends the group and cancels the rest; a raising one propagates its own exception and
cancels the rest, without a sibling's own teardown exception masking it; `GuardedSweep`'s
tick-failure backoff, its cap, and that `CancelledError` is never swallowed; the heartbeat file is
written on both success and failure and a missing heartbeat directory does not kill the loop);
`services/approval/tests/test_sweep_run.py` (3, the same guard shape
`services/scheduler/tests/test_sweep_run.py` already pinned, now proven for `EscalationSweep`
too); `services/notification/tests/test_cron_run.py` (3, updated for the shared `GuardedSweep`);
`services/notification/tests/test_worker_wiring.py` and
`services/approval/tests/test_worker_wiring.py` (1 each) — the "worker.main runs all loops" wiring
tests the review asked for, proving `main()` passes every loop into `run_supervised` without
needing a real database or broker (every DB-role assertion is faked to a no-op).

**M7 — a dedicated thread pool for campaign SMTP so password resets are never starved.**
`transports.CAMPAIGN_SMTP_EXECUTOR` (`services/notification/app/transports.py`), a two-thread
`ThreadPoolExecutor`. `SmtpTransport` takes an optional `executor` — `None` (the process's default
pool, what `asyncio.to_thread` itself uses) unless a caller passes one.
`services/notification/app/services.py:campaign_transport` is the **only** caller that passes
`CAMPAIGN_SMTP_EXECUTOR`; `send_test`'s own interactive, one-off send stays on the default pool on
purpose, so a large campaign already in flight can never make an operator's "send a test" button
hang behind it, and the platform's own shared transport (built once in `app/worker.py`, used by
the dispatcher for password resets and trial-ending mail) is unaffected because it is never given
an executor at all. Docstrings on `CAMPAIGN_SMTP_EXECUTOR`, `SmtpTransport.__init__`,
`_transport_for_connection` and `campaign_transport` explain why, for the next person who wonders
whether two threads is a typo. Tests (`services/notification/tests/test_transports.py`):
`test_send_many_uses_the_process_default_executor_when_none_is_given`,
`test_send_many_uses_the_executor_it_was_given`,
`test_campaign_smtp_executor_is_a_small_dedicated_pool`,
`test_campaign_transport_uses_the_dedicated_executor`.

**M8 — `planned_total_mb` and the real production set; account-worker's merged sweep needs no new
container.** Covered under B1(c) above for the profile-set correction. Separately,
`memory-budget.json`'s own `planned.$comment` now notes that account-worker's incoming merged
sweep and subscription-worker's incoming "billing-run" step are **not** priced in `planned` — both
land inside an already-priced, already-running container, so pricing them again would double-
count. If either of those merges later needs a bigger `mem_limit` for the container it lands in,
that is a `containers`/`mem_limit_mb` edit when it happens, not a `planned` placeholder now.

**L9 — replaced a tautological test; added real coverage.**
`tests/infra/test_active_profiles.py`'s old `test_a_profile_named_after_its_own_key` asserted
`profile_for(key) == key` against a list of names typed into the test itself — proving the test's
own list agreed with itself, nothing about `profile_for` or `active-profiles.sh`. Replaced by
`TestProfileForAndKeyForProfileCoverExactlyTheSameProfiles`, which walks `memory-budget.json`'s
own `profiles` list (the actual, current set) and proves `profile_for`/`key_for_profile` round-
trip for every one of them — the two mappings can now never quietly drift apart the way two
independently hand-maintained `case` statements eventually would.

**L10 — preflight's reverse mapping now comes from the shared library.** `preflight.sh` used to
keep its own inline `case "$profile" in inventory) key=operations ;; ...` — the literal inverse of
`lib/active-profiles.sh`'s `profile_for`, hand-copied. That case statement is gone; `preflight.sh`
now calls the new `key_for_profile` (same file), and L9's round-trip test is what keeps the two
directions honest going forward.

**L11 — the stale comment at `compose.prod.yml`'s background fuse.** Fixed alongside the
`kinds.background` correction itself (the same edit that changed 42 MB to 85 MB in
`memory-budget.json` also updated the comment on `x-background`'s `mem_limit: 192m` that still
said "35-50 MB" and "4-5x that").

**Left genuinely undone, and why:**
- ~~B1(b)'s scheduler consolidation~~ — done in round 2 by folding each sweep into its own API
  container instead (below).
- H3's branch has no dedicated unit test — it is a single `if` in `preflight.sh` itself rather
  than logic extracted to a tested library function; extracting it would mean shelling out to
  `docker compose exec ... psql` from a test, which is exactly the "never the shared database"
  risk this whole exercise exists to avoid, or mocking `docker` itself, which would test the mock.
- `subscription`'s own missing poisoned-step guard (same shape as the one `approval` had) — not
  touched, per instruction; the billing slice's call.

---

## Fix round 2 review (2026-09-24)

> **Numbers in this section are as of round 2 (pre-merge tree, `kinds` 85 / 85 / 48) and are
> superseded by "Fix round 3" below: the merge added ops-monitor, `lead` and `customer-worker`,
> and the kinds were re-priced from measured images. The reasoning here stands; the figures do
> not. Where a figure is wrong on its own terms it is corrected inline.**

The re-review of round 1 (`8179f5f`) came back NEEDS-FIXES with two blockers, and it came with
something this document had been missing: a read-only look at the real box. Everything below is
priced against those facts, not against the reference number in `memory-budget.json`.

**What the box actually is.** MemTotal 3862 MB (the file's `mem_total_mb_reference` is now 3862;
it said 3800), about 2 GB of swap already in use, 40 containers running under the compose project
`opsgrids-edge`, `.env` with `COMPOSE_PROFILES` listing all ten profiles and no `EDGE_PROFILES`,
and no `edge-budget.last-ok` in the state dir. Priced at `memory-budget.json`'s numbers, the
running set was reported as **3494 MB — 90%**. *(Corrected in round 3: that sum silently
excluded `approval-cron` and `subscription-cron`, which were running and had no budget entry.
The file's own M7 rule prices such a container as `kinds.background`, never as free: 3664 MB —
94.9% at the old prices.)*

**The correction this whole section turns on: this release creates the overcommit.** Rounds 0 and
1 both said the box was already over its hard line ("109% today"). It was not — at the prices then
in the file it was at ~90-95%. The
109% was the price of *this release's compose file* (47 services, ten profiles, 4149 MB): the extra
~655 MB is `document`, `document-cron`, `minio`, `leave`, `leave-worker`, `scheduler`,
`operations-cron` and `storefront-cron`, less the `approval-cron` and `subscription-cron` that
earlier merges removed. A grandfather clause ("do not make it worse than last time") was therefore
an answer to a question the box was not asking; the honest question is "should this release be
allowed to take a ~95% box to over 100%?", and that is the owner's to answer. *(Round 3: at
prices measured from the real images the box was already at 104.7% — see below — which is what
the ~2 GB of swap in use was saying. That does not rescue the grandfather clause; it makes the
owner's decision more, not less, necessary.)*

### Numbers, after this round (superseded — see "Fix round 3" for the current ones)

Post-deploy pricing of the real production compose (`infrastructure/edge/compose.prod.yml`, 44
services with all ten profiles) against MemTotal 3862 MB. "Cumulative" turns profiles on in the
order `memory-budget.json` lists them; "alone" is core plus that one profile.

| profiles enabled | alone (MB / %) | cumulative (MB / %) |
|---|---|---|
| core only | 1964 / 50% | 1964 / 50% |
| storefront | 2194 / 56% | 2194 / 56% |
| inventory | 2049 / 53% | 2279 / 59% |
| hrms | 2559 / 66% | 2874 / 74% |
| crm | 2134 / 55% | 3044 / 78% |
| pulseboard | 2219 / 57% | 3299 / 85% |
| projects | 2049 / 53% | 3384 / 87% |
| support | 2049 / 53% | 3469 / 89% |
| marketing | 2049 / 53% | 3554 / 92% |
| finance | 2219 / 57% | 3809 / 98% |
| **itsm — all ten** | 2049 / 53% | **3894 / 100.8%** |
| + every `planned` backlog item (+1068) | | 4962 / 128% |

Round 2's consolidation (below) is worth 3 x 85 = **255 MB** at the round-2 price (4149 MB before,
3894 MB after; 3 x 90 = 270 MB at round 3's). The ten-profile set was still fractionally over the
100% hard line and about 16 points over the 85% soft line, and the planned backlog does not fit on this box at any profile count near ten. No
change in this repository closes that gap; what the repository can do is refuse to let it be closed
by accident. That is B2.

### B1 — the gate priced the past. Fixed: it prices the box the deploy leaves behind.

`preflight.sh` used to price `docker ps`'s running containers. A deploy that *adds* a profile (or
whose new compose file adds a service) was therefore judged on a set that did not contain what it
was about to start. The gate now prices the **post-deploy set**: `(running containers that the new
compose file still defines) union (services the declared profiles configure)`, skipping one-shot
jobs. Running-but-deleted services (`approval-cron`, `subscription-cron`) drop out — `up
--remove-orphans` removes them — while a running container from a profile that was *removed from
`.env`* stays priced, because `docker compose up` does not stop it (H2). The computation lives in
`lib/memory_budget_totals.py` (`--post-deploy`) behind `post_deploy_compute` in
`lib/memory-budget.sh`, and `remote.sh record-budget` calls the same function so what is recorded
and what was gated can never be priced two ways.
Tests: `tests/infra/test_memory_budget_totals_containers.py::TestPostDeployContainerNames`
(newly declared profile priced with nothing running; running orphan still in the new compose
survives; deleted service does not; CLI parity) and, running the real `preflight.sh` and
`remote.sh` against a fake `docker` on `PATH`, `tests/infra/test_preflight_memory.py::
test_a_deploy_adding_a_profile_is_priced_above_the_running_set`,
`test_an_orphan_from_a_disabled_profile_is_still_priced`,
`test_a_running_container_whose_service_was_deleted_is_not_priced`,
`test_a_fresh_box_with_nothing_running_is_priced_from_its_declared_profiles` and
`test_record_budget_prices_the_box_the_same_way_the_gate_does`.

### B2 + H3 + H4 — a ratchet that nobody chose. Fixed: an explicit owner ceiling.

Round 1's `grandfathered_verdict` compared a deploy against `edge-budget.last-ok`. On the real box
that file does not exist (so the clause never applied) and, had it existed, the last deploy to
succeed — not a person — would have set the bar, with a per-deploy 1% creep. **Removed, with its
eight tests.** The replacement is a static, human-set number:

- New optional key `EDGE_BUDGET_ACCEPTED_MB` in `.env` (`.env.example` ships it empty; preflight
  allows it, like `EDGE_PROFILES`, to be absent from a box's existing `.env`).
- Up to the soft line: ok. Up to the hard line: warn. Over the hard line: **refuse — unless** the
  key is set to a whole number and the post-deploy total is at or under it, in which case preflight
  prints a loud warning that names the overcommit percentage and the accepted value and proceeds.
  A deploy asking for even one MB more than the accepted number refuses again; a non-numeric value
  is a preflight failure, not a silent ignore.
- The number never moves by itself. Raising it is a diff to `.env`, which is the audit trail.
- `edge-budget.last-ok` is still written by `record-budget` after a healthy deploy and preflight
  still prints it, but **informationally only** — no verdict reads it. That also settles the
  recovery question: after a failed health check nothing needs resetting.
- RUNBOOK's memory section now opens with the owner decision (resize the VM, de-list a product, or
  accept the overcommit at a stated number) and asks for it **before the first deploy of this
  release**, with the pre-deploy key list that includes `EDGE_PROFILES` and
  `EDGE_BUDGET_ACCEPTED_MB` as knowingly-absent-allowed keys.

Tests: `tests/infra/test_memory_budget.py` (the seven `apply_accepted_ceiling` cases, including
`test_the_ceiling_is_static_not_derived_from_expected_mb` and
`test_no_accepted_ceiling_set_keeps_refusing`), and against the real script
`tests/infra/test_preflight_memory.py::test_over_the_hard_line_with_no_ceiling_refuses`,
`test_over_the_hard_line_but_at_or_under_the_accepted_ceiling_warns_and_names_the_overcommit`,
`test_over_the_accepted_ceiling_still_refuses`,
`test_a_non_numeric_ceiling_is_a_failure_not_a_silent_ignore`,
`test_last_ok_is_informational_only_and_never_grandfathers_a_deploy`,
`test_recovery_after_a_failed_health_check_needs_nothing_special`,
`test_under_the_soft_line_is_ok_whatever_the_ceiling`. Round 1's H3 note said the activations-query
branch had no test because testing it meant a real database or mocking `docker`; the fake-`docker`
harness above is the middle route, and it now pins
`test_a_failing_activations_query_is_a_warning_never_read_as_zero` and
`test_a_real_activation_with_no_service_enabled_is_refused`.
`tests/infra/test_planned_headroom.py::test_runbook_ceiling_example_is_the_number_preflight_would_
print` keeps the RUNBOOK's worked example (`EDGE_BUDGET_ACCEPTED_MB=3894`) from drifting from the
priced total.

**The decision this leaves open, plainly (round-2 figures; round 3's are 4581 MB / 118.6%).** Ten
profiles was 3894 MB on a 3862 MB box after the consolidation — a box that already swaps would
run at 100.8%. The three options are in the
RUNBOOK. More RAM is the only one that adds headroom rather than redistributing it, but the
erratum above recorded that the owner would not resize this box; that was decided at ~90%, before
this release added ~400 MB net, so the RUNBOOK asks again rather than assuming. The planned
backlog (+1068 MB) does not fit on this box under any of the three. **And the price list was
low:** the API images measured at 94-102 MiB against `kinds.api`'s 85 MB — which round 3 acted on
(re-priced to 98), and which the RUNBOOK still asks the owner to cross-check against `free -m` on
the box before writing the ceiling down.

### Consolidation — `operations-cron`, `storefront-cron`, `document-cron` folded into their APIs

Round 1 rejected moving these sweeps into `scheduler` (three services' business logic inside a
fourth's image) and a multi-app host (`import app` ambiguity). Round 2's decision avoids both:
**each sweep runs inside its own service's API container**, as a supervised background task
started from the FastAPI lifespan. No service imports another's code, and no `app` namespace
collides.

- New helpers in `platform_core.supervise`: `start_background_sweep(coro)` and
  `stop_background_sweep(task)` (cancel and await; a `None` task is a no-op). Deliberately *not*
  `run_supervised`: a sweep exiting must not take an API down with it.
- `create_app` already accepts `on_startup`/`on_shutdown`; each service's `main.py` uses them.
  Each sweep is a `GuardedSweep` (or, for storefront's `DomainSweep`, gained the same
  whole-tick guard and capped backoff), so a failing tick backs off and logs instead of
  propagating.
- Gated per service by `RUN_SWEEPS_IN_API` (`Settings.run_sweeps_in_api`, default `false`
  everywhere; `compose.prod.yml` sets `${RUN_SWEEPS_IN_API:-true}` on the three API services).
  `bin/cron` is kept — it is the manual entrypoint. *(Round-2 text said setting the flag to `false`
  "puts a sweep back in a container of its own": wrong — no `*-cron` service exists to catch it, so
  `false` **stops** the sweep. Preflight warns when it is not `true`.)*
- **Idempotent and lease-safe, verified rather than assumed.** Storefront's domain sweep and
  document's purge sweep claim rows with `SELECT ... FOR UPDATE SKIP LOCKED`; operations'
  reconciler is read-mostly and appends (append-only ledger rule), so two overlapping runs —
  the API's own copy and a by-hand `bin/cron` — cannot double-process. (The edge runs exactly one
  API replica and `up` recreates a container rather than overlapping two, so a rolling restart is
  not a scenario here; only a rollback to a ref that still has the `*-cron` services briefly
  overlaps.) The role checks the `*-cron` entrypoints made move with the sweep:
  operations and document call `assert_outbox_role` at API startup, storefront asserts its
  directory role.
- **Security exception, kept deliberately: document's orphan scan does not run in the API.**
  ADR-0009 §3 says the document API must never hold the credential that can list the bucket (it
  would let a compromised API enumerate other tenants' keys). The purge sweep therefore runs in the
  API with `orphan_enabled=False`, unconditionally; the orphan pass stays a manual, operator-run
  job: `docker compose run --rm --no-deps document ./bin/cron` (RUNBOOK, "The purge sweep"). This
  costs nothing in memory — the orphan scan was never continuous.
- Deleted from `compose.prod.yml` and `memory-budget.json`: the three `*-cron` services and their
  entries (the RUNBOOK's backup-restore stop/start lists and the storefront/document sections were
  updated to match).
- Saving: 3 x 85 MB = **255 MB** in the budget at round 2's price (4149 -> 3894 MB, table above).
- **Measured, not assumed:** the three real production images (`docker build` of each service's
  Dockerfile) were run against an isolated Postgres/Redis/RabbitMQ (`d4b2_iso`, ports 55442 /
  63992 / 56742 — never the shared dev stack) with compose's `MALLOC_ARENA_MAX=2`, trim threshold
  and pool sizes, `RUN_SWEEPS_IN_API` off and on, 40 health probes, `/proc/1/status` `VmRSS`:

  | image | sweep off (kB) | sweep on (kB) | difference |
  |---|---|---|---|
  | `operations` | 96,984 | 96,516 | -468 (noise) |
  | `storefront` | 99,996 | 101,040 | +1,044 |
  | `document` | 104,260 | 104,336 | +76 |

  The sweep costs an API container about 1 MB at most (it imports nothing the API does not
  already hold), so the ~255 MB is genuinely given back rather than moved. The `operations` and
  `storefront` containers logged `inventory.reconcile.pass` / `storefront.domain.sweep.pass` from
  inside the API process, against the isolated database, on their first tick.
- **What the same run says about the price list.** These three images idled at ~95-104 MB (94-102
  MiB), above `kinds.api`'s 85 MB planning figure and in line with the 92-95 MB `account` showed in
  the original measurement. `kinds.api` was not changed in this round: a laptop's RSS is not the
  box's, the reviewer asked about something else, and re-pricing 27 containers should be its own
  decision made against `docker stats` on the real box. But an owner deciding on
  `EDGE_BUDGET_ACCEPTED_MB` should know the projection may understate by up to ~270 MB.
  *(Round 3 re-priced `kinds.api` to 98 and quantified the effect; see below.)*

Tests: `libs/platform-core/tests/test_supervise.py::TestBackgroundSweep` (start then stop cancels
cleanly; stopping a never-started task is a no-op; a sweep that finished on its own is not an
error to stop); per service, `tests/test_main_sweep_wiring.py` in `operations`, `storefront` and
`document` — `test_sweep_is_not_started_when_the_setting_is_off`,
`test_sweep_starts_as_a_background_task_when_the_setting_is_on`,
`test_a_sweep_that_fails_every_tick_does_not_propagate_out_of_startup`;
`services/storefront/tests/test_domain_sweep_run.py` (the new guard: a raising tick, the capped
backoff, recovery, `CancelledError` not swallowed); and on the compose side
`tests/infra/test_sweeps_in_api.py` (the three `*-cron` services stay gone, each API container is
told `RUN_SWEEPS_IN_API=true`, each Settings default is `false`, `bin/cron` still exists).

### Smaller findings

- **M5 — `EDGE_PROFILES` (and the new ceiling key) allowed absent.** A box's existing `.env` has
  no `EDGE_PROFILES` line; preflight reported it missing and would have failed every deploy.
  `preflight.sh`'s required-key check now knows an `optional_absent` set. Tests:
  `test_edge_profiles_and_the_accepted_ceiling_may_be_absent_from_an_existing_env`,
  `test_any_other_absent_key_is_still_reported_missing`.
- **M6 — `.env.example` comments rewritten.** They still described the removed auto-off. Now:
  empty `COMPOSE_PROFILES` means core only, the list is hand-written, `EDGE_PROFILES` overrides it.
- **M7 — unknown running containers.** A container with no `memory-budget.json` entry used to be
  priced at zero. It is priced as `kinds.background` and named in a preflight warning. Tests:
  `test_a_container_the_budget_does_not_price_is_priced_as_background_not_skipped`,
  `test_unpriced_container_names_names_the_gap`,
  `tests/infra/test_preflight_memory.py::test_a_service_with_no_budget_entry_is_priced_as_
background_and_named`.
- **L8 — `record_budget`.** Every skip path (cannot price the box, unwritable state dir) now warns
  instead of returning quietly, and the file is written to a temp name and `mv`'d into place so a
  crash mid-write cannot leave a truncated number that a later reader misparses. Tests:
  `test_record_budget_says_so_when_it_cannot_price_the_box`,
  `test_record_budget_says_so_when_the_state_dir_is_unwritable`,
  `test_record_budget_writes_atomically_and_leaves_no_temp_file`.
- **L9 — `GuardedSweep`.** `on_tick_success` now runs inside the same guard as `tick()` (a raising
  success hook used to escape and kill the loop). `consecutive_failures` is a public counter and is
  in the log line. The heartbeat file uses wall-clock `time.time()` (it used a monotonic clock,
  meaningless to another process) and is written as JSON; it stays opt-in and **is not wired to
  any container healthcheck** — documented in the module rather than half-built here. Tests:
  `test_a_raising_on_tick_success_is_caught_by_the_same_guard`,
  `test_consecutive_failures_counts_up_and_resets_on_success`,
  `test_heartbeat_reports_the_failure_streak`.
- **L11 — campaign SMTP executor teardown.** `notification`'s worker now calls
  `CAMPAIGN_SMTP_EXECUTOR.shutdown(wait=False, cancel_futures=True)` in a `finally` around
  `run_supervised`, so threads mid-send do not hold the process open after a cancel. A send
  interrupted this way may be re-attempted by the next process — at-least-once, as documented in
  the worker's comment. Tests: `services/notification/tests/test_worker_wiring.py` (shutdown
  asserted on both the clean path and `test_campaign_smtp_executor_is_shut_down_even_when_run_
supervised_raises`).
- **L12 — activations advisory.** The "a product has active organizations but no service running"
  refusal counted every row in `product.activations`, including suspended ones and the `e2e-…`
  test organizations the smoke suite leaves behind, so a suspended tenant could block a deploy.
  It now counts only `status in ('trial','active')` and excludes `account.organizations` slugs
  like `e2e-%`. Test: `test_the_activations_query_counts_only_live_activations_and_skips_e2e_
orgs`.

### Still not done, and why

- **The owner decision itself.** Resize, de-list, or accept — this repository can only make the
  decision visible and make the "accept" path explicit. Until `.env` has a considered
  `EDGE_BUDGET_ACCEPTED_MB` (or the box is bigger), the first deploy of this release refuses, on
  purpose.
- **The planned backlog does not fit.** 4962 MB projected (128%). Consolidation is now exhausted for
  the cheap cases; the remaining levers are more RAM, the memory-limited datastore tuning
  (postgres/rabbitmq/redis were never re-measured), or fewer products per box.
- **`document`'s orphan scan is manual** (security, above).
- **The `GuardedSweep` heartbeat is not wired to a healthcheck** (L9, above) — a sweep that stops
  ticking inside a live API is visible today only through its log lines
  (`*.sweep.started`, `*.tick_failed`).
- **`subscription`'s own `DunningSweep`** — unchanged and still the billing slice's call.


---

## Fix round 3 review (2026-09-24) — the final round

The re-review of round 2 (`c159a3e`) came back NEEDS-FIXES: two HIGH, four MEDIUM and a tail of
lows, on a branch that had to be merged again first (`origin/release/backlog-complete` had moved
to `1706135`: ops-monitor, the CRM `lead` service and `customer-worker`, ADR-0017/0018/0019). This
section records what changed and, above all, **what the numbers are now**, because two of the
previous rounds' headline numbers turned out to be wrong in the optimistic direction.

**Merge.** Four conflicts, resolved as the review's map said: `remote.sh` kept both sets of `source`
lines; `memory-budget.json` kept the corrected `background` text and took the release's
`ops_monitor` kind with its stale "background's 42 MB" and "~94%/~98%" claims replaced by a
measurement; `compose.prod.yml` kept the header without a service count; the RUNBOOK's routine
table took ops-monitor's rows and its "`storefront-cron` alive" became a
`storefront.domain.sweep.started` check. The release's own edits to this ADR (its §7 decisions)
merged without conflict and stand.

### The numbers, corrected — and re-priced from measurement (MEDIUM 4a, 4b, 4c; HIGH 2)

Three things were wrong in what the owner was being told, all in the same direction.

**(a) "Running today = 3494 MB / 90%" excluded two running containers.** The read-only look at the
box found 40 containers; the budget had no entry for `approval-cron` and `subscription-cron`,
and the sum skipped them, against the file's own M7 rule (an unpriced container is priced as
`kinds.background`, never as free). Priced by that rule: **3664 MB — 94.9%** at the round-2
prices. The production ref's compose (`47143a0`), ten profiles, is exactly those 40 services, so
this is reproducible from the repository rather than from anyone's memory of the box.

**(b) The price list was measured on one image.** `kinds.api` was 85, "confirmed" by a single
92-95 MB reading of `account`. Round 2 measured three more and noticed the gap and then left it;
round 3 acts on it. Ten measurements of seven API images, production Dockerfiles built from the
merged tree, run against an isolated Postgres/Redis/RabbitMQ with compose's `MALLOC_ARENA_MAX=2`,
trim threshold and pool sizes, warmed with 40 in-container `/health` probes, `VmRSS` read from
`/proc/1/status` after ~40 s idle (MiB = kB / 1024):

| kind | image | VmRSS (MiB) |
|---|---|---|
| api | account | 97.1 |
| api | lead | 92.2 |
| api | operations | 95.5 (round 2 run: 94.7) |
| api | storefront | 99.2 (97.7) |
| api | document | 102.3 (101.8) |
| api | notification | 100.6 |
| api | customer | 91.7 |
| background | account-worker | 89.6 |
| background | notification-worker | 92.1 |
| background | customer-worker | 84.6 |
| background | edge-relay (all 28 schemas) | 91.4 |
| ops_monitor | ops-monitor (`product` image, `python -m platform_core.ops_monitor`) | 92.3 |

`kinds.api` **85 -> 98** (mean 97.3 over ten readings, rounded up; max 102.3),
`kinds.background` **85 -> 90** (mean 89.4, rounded up), `kinds.ops_monitor` **48 -> 93**
(measured; slice/ops-monitor's figure was "a shade heavier than a plain background consumer" and
it is not lighter than one — it imports the same module graph plus an HTTP server). Its 128m
`mem_limit`, chosen to keep a total under the hard line at a price of 48, now leaves ~36 MiB over
idle (1.4x): flagged for that container's owner, not edited here.

**These are laptop figures.** The images ran on an arm64 Docker Desktop VM; the box is amd64. The
same code and the same allocator settings, so the difference should be small, but it is a
difference nobody has measured. The RUNBOOK asks the owner to read `free -m` and `docker stats` on
the box before writing a ceiling, and the ceiling is a number the owner reads back off preflight,
not one this repository can vouch for.

The measured effect of the re-pricing on the ten-profile release: 28 API containers x 13 MiB, 12
background x 5 MiB and ops-monitor +45 = **+469 MB** — the "up to ~270 MB understatement" round 2
guessed at was itself an understatement, because it counted only the 27 API containers of the
pre-merge tree and stopped at the API kind.

**(c) All the numbers, on the merged tree with the re-priced kinds**, against MemTotal 3862 MB
(soft line 3282, hard line 3862, cap 4827):

| profiles enabled | this profile adds | cumulative | of MemTotal |
|---|---|---|---|
| core | — | 2217 | 57.4% |
| + storefront | 256 | 2473 | 64.0% |
| + inventory | 98 | 2571 | 66.6% |
| + hrms | 662 | 3233 | 83.7% |
| + crm | 384 | 3617 | 93.7% |
| + pulseboard | 294 | 3911 | 101.3% |
| + projects | 98 | 4009 | 103.8% |
| + support | 98 | 4107 | 106.3% |
| + marketing | 98 | 4205 | 108.9% |
| + finance | 278 | 4483 | 116.1% |
| **+ itsm — all ten** | 98 | **4581** | **118.6% — 719 MB over MemTotal** |
| + every `planned` backlog item (+956) | | 5537 | 143.4% |

| reference point | MB | % |
|---|---|---|
| the running set today at the production ref, round-2 prices, unpriced crons excluded (the "3494") | 3494 | 90.5% |
| the same set, unpriced crons priced as `background` (M7) | 3664 | 94.9% |
| the same set at the corrected prices | 4044 | 104.7% (182 MB over) |
| this release's merge target at the *old* prices (ops-monitor 48, lead 85, customer-worker 85) | 4112 | 106.5% |
| **this release, corrected prices, ten profiles** | **4581** | **118.6%** |

The release adds eight containers (customer-worker, document, lead, leave, leave-worker, minio,
ops-monitor, scheduler: 717 MB) and removes two (`approval-cron`, `subscription-cron`: 180 MB):
**net +537 MB, 104.7% -> 118.6%.** The `planned` list lost its three entries that are real
containers now (ops-monitor, `lead-api` = `lead`, `customer-worker`), which had been priced twice
in every projection; 956 MB of genuinely-unbuilt backlog remains.

**What this means for the decision, stated once and plainly.** With realistic prices the ten
profiles need **~118.6% of a 3862 MB box** — the box is already over its hard line today (104.7%,
which is what ~2 GB of swap in use means) and this release takes it 537 MB further. The owner's
options are the same three as round 2, with real numbers attached (the RUNBOOK carries them):
**accept a deliberate overcommit ceiling** (`EDGE_BUDGET_ACCEPTED_MB=4581`), **de-list products**
(719 MB to get under the hard line: `hrms` is 662 of it, plus any one 98 MB profile; 1299 MB to
get under the soft line), or **resize** (an 8 GB VM puts the ten near 58% and the whole planned
backlog near 70%). The earlier erratum's "the owner will not resize" was decided before any of
these numbers existed and is asked again rather than assumed. Every RUNBOOK and ADR figure is
generated from the merged tree, and `test_runbook_ceiling_example_is_the_number_preflight_would_
print` fails when the RUNBOOK's worked example stops being what preflight would print.

### 1 HIGH — the gate failed open. Fixed: it fails closed, and prices restarting and paused containers

`post_deploy_compute` (`lib/memory-budget.sh`) sent compose's stderr to /dev/null and ignored its
exit status: a compose failure (a missing `.env` key, a syntax error, a missing binary) produced an
empty post-deploy set and a green "ok memory budget … 0M (0%)". It now returns non-zero with
`POST_DEPLOY_ERROR` naming the reason when `docker ps` fails, either `docker compose config
--services` fails or comes back empty (a valid compose file always has core services, so empty is
a failure, not "nothing to price"); compose's own stderr stays visible; and preflight turns it into
`FAIL memory budget could not be priced — <why>`. The one remaining fail-open in the same gate —
an unreadable `MemTotal` used to warn and skip — is a FAIL too. `record-budget` reports the reason
in its own "not recorded" line. `docker ps` now includes `status=restarting` and `status=paused`: a
crash-looping container is resident between crashes and a paused one keeps every page. "Every
profile" for the running-set intersection is compose's own `COMPOSE_PROFILES='*'` rather than a
list in the JSON, so a profile added to compose and forgotten in the budget cannot fall out of it.
Tests (`tests/infra/test_preflight_memory.py`, real `preflight.sh`/`remote.sh` against a fake
`docker`): `test_a_compose_config_failure_fails_the_memory_row_instead_of_pricing_zero` (also
asserts compose's own error is visible), `test_an_empty_service_list_is_a_failure_not_a_zero_
megabyte_box`, `test_docker_ps_failing_is_a_failure_because_the_running_set_is_unknown`,
`test_an_unreadable_memtotal_is_a_failure_not_a_skipped_check`,
`test_record_budget_reports_why_it_could_not_price_a_broken_compose_file`,
`test_restarting_and_paused_containers_are_priced_and_exited_ones_are_not`.

### 5 MEDIUM — the ceiling had no upper bound. Fixed: `host.max_accepted_percent` = 125

`EDGE_BUDGET_ACCEPTED_MB` above 125% of MemTotal (4827 MB here) is a FAILED row naming the cap and
is ignored for the verdict (a `38940` for `3894` typo would otherwise be a blank cheque); a total
above the cap is refused even with a ceiling; a ceiling above the printed total prints
`info … is NM above this deploy's total` (the ceiling is a decision record — exactly the printed
post-deploy total for the commit being deployed, a rule now in the RUNBOOK and `.env.example`); a
ceiling on a deploy that is under the hard line prints `set but not needed`; and an overcommit is
printed as `719M over MemTotal (118.6%)` rather than a floored `118%` (a floored percent read
"100%" for a box that was 32 MB over). Tests: `test_a_ceiling_above_the_cap_is_a_failure_naming_
the_cap`, `test_a_total_over_the_cap_is_refused_and_says_no_ceiling_can_cover_it`,
`test_a_ceiling_exactly_at_the_cap_is_accepted`, `test_slack_between_the_ceiling_and_the_total_is_
reported`, `test_a_ceiling_at_exactly_the_total_reports_no_slack`, `test_a_ceiling_this_deploy_
does_not_need_is_called_out`, and the unit cases in `test_memory_budget.py` (`apply_accepted_
ceiling` with a cap; `describe_budget_usage`).

### 3 MEDIUM — the document purge sweep spun with no sleep. Fixed: only progress earns an immediate re-run

`PurgeSweep.run()` slept only when a tick *considered nothing*; one asset whose object-store delete
failed every time made it re-run at once, forever (~740 ticks a second) — in a loop that now shares
an event loop and a 3+2 connection pool with the upload API. It now re-runs immediately only when
something was purged or removed (a backlog larger than `batch_size` still drains back to back);
found-but-no-progress backs off exactly like an idle tick, doubling to the cap. A tick where every
attempt failed also counts as a failure for the gauges below, so an always-failing asset shows as a
climbing `sweep_consecutive_failures` instead of a healthy-looking loop. Tests (`services/document/
tests/test_purge_sweep_run.py`): `test_a_permanently_failing_asset_sleeps_between_ticks_instead_of_
spinning`, `test_rows_another_sweeper_keeps_locking_also_back_off`, `test_progress_earns_an_
immediate_rerun_and_stopping_progress_backs_off`, `test_an_idle_tick_backs_off_as_it_always_did`,
`test_a_dry_run_orphan_report_is_not_progress`, `test_health_counts_a_tick_where_every_attempt_
failed_and_clears_on_progress`.

### 6 MEDIUM — an in-API sweep was invisible when dead or failing. Fixed: an error log and three gauges

`start_background_sweep` now attaches a done-callback: a sweep task that ends without being
cancelled — it raised, or a bug made it return — is logged at **error** as
`supervise.background_sweep_exited`, and its `sweep_alive` gauge drops to 0. Each sweep
(`GuardedSweep` subclasses, storefront's `DomainSweep`, document's `PurgeSweep`) records a
`SweepHealth`, and every API's own `/metrics` exposes `sweep_alive`, `sweep_consecutive_failures`
and `sweep_last_success_timestamp_seconds`, labelled `sweep="inventory.reconcile"`,
`"storefront.domain.sweep"`, `"document.sweep"` (the log-event prefix, so a metric and the line that
explains it read the same). Names are in the RUNBOOK for the ops-monitor follow-up — nothing reads
them yet, and ops_monitor's code is deliberately untouched. Tests: `test_supervise.py::
TestSweepHealth`, `::TestBackgroundSweepIsLoudWhenItDies` (raises -> error log + not alive;
merely returns -> error; a clean stop -> no error; stopping a dead sweep does not raise at
shutdown), `test_app_metrics.py::test_an_in_process_sweep_reports_its_three_gauges`,
`::test_an_api_with_no_sweep_exposes_none`, and each service's wiring test (alive while running,
not alive after stop).

### 7 LOW-MED — a daily sweep waited a day after its first failure. Fixed

`GuardedSweep`'s backoff began at `poll_interval` and was only capped after the first sleep: a
86,400 s sweep with the default 300 s cap waited 24 hours after one failed tick. It now starts (and
resets) at `min(poll_interval, max_backoff)`; storefront's `DomainSweep` and document's `PurgeSweep`
got the same. Tests: `test_supervise.py::TestFirstBackoffNeverExceedsTheCap` (both the first retry
and the reset after a clean tick), `test_domain_sweep_run.py::TestFirstBackoffNeverExceedsTheCap`,
`test_purge_sweep_run.py::test_a_raising_tick_first_retries_after_the_cap_not_after_the_poll_
interval`.

### 8 LOW — the in-API sweeps held a request-pool connection across network calls. Fixed: a bulkhead

It was straightforward, so it was done rather than deferred: `platform_core.db.create_bulkhead_
engine` (a private application-role engine, **one connection, no overflow**, same DSN, same
tenant-GUC reset as the process engine) and `bulkhead_scope` (`session_scope`'s shape on that
engine). Each of the three services builds its sweep on it and disposes it at shutdown. The problem
it removes: storefront's domain claim holds its `FOR UPDATE` transaction across probes of up to
~15 s a domain, and document's purge holds its transaction across the object-store delete — the
"network I/O inside a transaction" pattern D3 (#14, #16) is removing at the source — while the API's
request pool is 3 + 2. With the bulkhead a slow probe can neither take one of those five nor be
starved by buyer traffic. Cost: one more Postgres connection per API process that runs a sweep,
opened lazily, plus an engine object (no measurable RSS: the sweep-off/on readings above differed by
~1 MiB). **Sequential use only** — a pool of one would deadlock a scope that nested a second on the
same engine; no sweep does, and a test pins that a second scope waits rather than growing the pool.
D3's priority is unchanged: this contains the blast radius, it does not remove the pattern.
Tests: `libs/platform-core/tests/test_bulkhead.py` (pool of one with no overflow; app role;
tenant GUC bound per scope and not leaked; a second scope waits), and each service's wiring test
(the sweep is built on the bulkhead's scope; the engine is disposed at shutdown).

### 9 LOW — `remote.sh` exported `COMPOSE_PROFILES` only when `EDGE_PROFILES` was set. Fixed

It now always exports what `resolve_profiles` resolved, so a stray `COMPOSE_PROFILES` in an
operator's shell can no longer beat `--env-file` and make `record-budget` price a different box
than preflight judged. Test: `test_remote_sh_always_exports_the_profile_list_it_resolved`.

### 10 LOW docs — stale statements

RUNBOOK: `--remove-orphans` on the box removes **only `approval-cron` and `subscription-cron`**
(`notification-cron`, `operations-cron` and `document-cron` never reached it, and `storefront-cron`
sat behind the unused `sweep` profile); rollback to `47143a0` recreates those two and removes the
eight this release added; **`RUN_SWEEPS_IN_API=false` STOPS the sweeps** (no cron service is left
to catch them) and preflight warns when it is not `true`; the "rolling restart" scenario is
removed. Comments: `services/document/app/config.py`, `storage.py` (no longer say the orphan
credential is held by `document-cron`), `compose.prod.yml` (`x-service-common`, `x-background`,
ops-monitor's `storefront-cron` references), `docs/architecture.md` (which services run their cron
inside the API), `docs/backlog.md`, ADR-0009 and ADR-0012 pointers. Tests: `test_the_in_api_sweeps_
being_switched_off_is_warned_about`, `test_the_in_api_sweeps_default_is_reported_ok`.

### 11 LOW — the smaller items

- **Suspended activations count.** Round 2's L12 dropped them on the reasoning that nobody is served
  for one; `product.project_subscription` flips a suspended activation back to `active` when a
  payment lands, onto a service this box would not be running. The advisory counts `trial`,
  `active` and `suspended` (still excluding `e2e-%` organizations). Test: `test_the_activations_
  query_counts_suspended_and_skips_e2e_orgs`.
- **`COMPOSE_PROFILES=*`.** Valid for compose and for ops-monitor; a literal `*` matches no profile
  name, so every sold product read as unserved and a healthy deploy was refused. Preflight and
  `remote.sh` expand it from `docker compose config --profiles` (`expand_profile_wildcard`, tested
  including that it is never glob-expanded into filenames). Tests: `test_a_wildcard_profile_list_is_
  expanded_not_read_as_no_products`, `test_a_wildcard_is_expanded_by_remote_sh_too`,
  `test_active_profiles.py::TestExpandProfileWildcard`.
- **`document`'s `memswap_limit`** said 320m against a 256m `mem_limit` — 64 MB of swap allowed
  under a comment that said none, left over from before the erratum lowered the shared limit. Now
  256m, and `test_a_memswap_limit_that_says_no_swap_really_means_no_swap` keeps any service from
  drifting the same way.
- **The APIs must not refuse to start over a sweep's precondition.** `operations` and `document`
  used to let a failed `assert_outbox_role` abort the API's own startup. It now skips only the
  sweep, logs `<sweep>.not_started` at error and leaves the gauges saying it never ran. Tests:
  `test_a_failed_outbox_role_precondition_skips_only_the_sweep` (operations and document).
  Storefront's own directory-role assertion is the *API's* precondition (the anonymous route
  depends on it) and is unchanged.
- **Ledger note, not edited here:** preflight's activation advisory excludes organizations whose
  slug starts `e2e-`, on the strength of `e2e/conftest.py`. Nothing in account/auth reserves that
  prefix, so a real customer who registers `e2e-acme` would be invisible to the "you are about to
  take a paying customer's product away" refusal. The reservation belongs in account/auth (out of
  this slice's scope); until then the exclusion is a known small hole.

### Two deploy blockers found by the environment audit, fixed in the same push

- **The Caddyfile site list matched the catch-all as an empty name.** `preflight.sh`'s `sed -n
  's#^https://\([a-z0-9.-]*\) {$#\1#p'` matched `https:// {` (the custom-domain on-demand-TLS
  catch-all) as `""`, so every preflight run failed with "the Caddyfile serves  but
  TLS_PLATFORM_HOSTS does not list it", whatever `.env` said. `\{1,\}` instead of `*`. Tests
  extract the exact expression from `preflight.sh`, run it against the real Caddyfile and assert no
  empty name and that every site it does produce is in the example `TLS_PLATFORM_HOSTS` — **none is
  unlisted, so nothing needed exempting**. (The test helper that built the scratch `.env` used the
  same `*` and had been quietly listing an empty host, which is how the suite stayed green.)
- **`.env` is sourced as shell, so an unquoted value with whitespace ran the rest as a command.**
  `EDGE_PROFILES=inventory, storefront` died with exit 127 and no FAILED footer. A pre-check on the
  raw file, before it is sourced, now fails with a row naming the key and exits before anything is
  read or run; quoted values, a trailing space, an inline ` # comment` and a `#` inside a value are
  not flagged, and neither are CRLF line endings. Tests: `test_an_unquoted_value_with_whitespace_is_
  a_named_failure_not_exit_127`, `test_nothing_in_a_bad_env_line_is_executed`,
  `test_quoted_values_a_trailing_space_and_inline_comments_are_not_flagged` and three more.

### Deploy guidance for this release (RUNBOOK, "Rolling out this release on the box")

`up` is not staged and the merge lands ~+540 MB at once on a box that is already swapping (~+450 at
the old prices). So: `EDGE_UP_PARALLEL_LIMIT=2` for this one deploy (a new optional `.env` line —
ssh carries no environment, so an exported variable would never arrive; `remote.sh up` reads it,
default 4, anything that is not a positive whole number falls back to 4; tested), a quiet window,
`vmstat 5` in a second session. **There is no automatic rollback**: `health` gives up after 600 s
with the containers as they are and no `record_ref`; the rollback is `make edge-rollback`. The
key table lists all 19 keys `.env.example` gained since the production ref.

### Still not done, and why

- **The owner decision** — unchanged, and now with numbers that cannot be read as "nearly fits".
  Until `.env` has a considered `EDGE_BUDGET_ACCEPTED_MB` (or the box is bigger, or a product
  de-listed), the first deploy refuses, on purpose.
- **The prices are laptop figures** and the datastores (postgres 350, rabbitmq 150, redis 40, caddy
  64, minio 60) were never re-measured; postgres is the one most likely to move.
- **ops-monitor's `mem_limit` (128m) against 92.3 MiB idle** — for that container's owner. Its
  `checks.py` docstrings still describe `operations-cron`'s liveness as uncovered; the `sweep_alive`
  gauge is what would cover it now.
- **D3's #14/#16** (network I/O out of transactions) — the bulkhead contains it, the fix is theirs.
- **The `e2e-` slug reservation** (ledger note above), the `GuardedSweep` heartbeat file (still
  opt-in and unwired; the gauges are the visible signal), and `subscription`'s own `DunningSweep`
  guard (billing's, untouched as instructed).
