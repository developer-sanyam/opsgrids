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

**What this does not mean.** It does not mean the box got smaller or the situation got worse
today — the box was *always* costing this much; the number describing it was wrong. It explains,
better than the previous number did, why this box has needed a swapfile since before this slice:
"94% of 3.8 GB, mostly idle" should not swap; "109%" does.

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

| scenario | expected MB | % of 3800 MB reference | verdict |
|---|---|---|---|
| core only | 1964 | 52% | ok |
| **real production (all ten profiles) — what the box actually runs today** | 4149 | **109%** | over the hard line **before a single planned service is added** |
| + every `planned` backlog item (ops-monitor, CRM lead api + customer-worker, payroll, recruit, config-service, operator/staff console, analytics, marketplace, workflow) | 5217 | **137%** | over the hard line |

**There is no honest number under the soft line here.** The box is overcommitted *today*, on what
it already runs, independent of anything still on the backlog — see "Fix round 1 review" below for
the decision this forced (grandfather the existing overcommit rather than refuse every future
deploy over a fact that predates it) and what is still unresolved (more RAM or de-listing a
product, an owner decision this repository cannot make on its own).

### What this does not fix — left for the owner, or a future slice

- **`caddy`, `postgres`, `rabbitmq` and `redis` were not re-measured this slice.** They are
  estimated (caddy) or measured against their own compose comment (postgres, rabbitmq, redis)
  from before this slice existed. If a future slice re-measures them with the same isolated-image
  method used here and finds a similar gap, the projected numbers above move with it.
- **`operations-cron`, `storefront-cron` and `document-cron` have no worker to merge into** (none
  of those three services has a consumer at all today) — ADR-0015's scheduler is the named
  candidate for absorbing a pure time-triggered sweep like these, but ADR-0015 §"Deliberately out
  of scope" explicitly deferred "making scheduler platform core" until a second producer needs it
  outside the `finance` profile. Moving these three sweeps onto `scheduler` is a real design
  change (scheduler would need to run under `inventory`/`storefront`'s own profiles or move to
  core, and each sweep's actual business logic — reconciliation, TLS probing, S3 purging — would
  need to reach `scheduler`'s image somehow, unlike `edge-relay`'s generic, schema-blind outbox
  drain), not a wiring change, and was left undone rather than done in a hurry — see "Fix round 1
  review" below for why this stayed undone through a second round too.
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
hard line before a single planned service is added (see "The projected budget" above, corrected).
Decision: **grandfather, do not de-list.**
- **(a) Grandfather clause.** `grandfathered_verdict` (`infrastructure/edge/lib/memory-budget.sh`)
  downgrades an over-hard-line verdict to a loud `warn` when this deploy is not more than 1% worse
  than the last one that actually went healthy; a fresh box with no recorded last-ok keeps
  today's behaviour and still refuses. `remote.sh`'s `record_ref` writes
  `${EDGE_STATE_DIR}/edge-budget.last-ok` (via the new `record_budget`) only after `health` passes,
  using the same running-containers pricing `preflight.sh` itself now uses (below) — so a failed
  deploy never lowers the bar the next one is measured against. Tests, one per branch:
  `tests/infra/test_memory_budget.py`'s `test_ok_verdict_passes_through_unchanged`,
  `test_warn_verdict_passes_through_unchanged`, `test_a_fresh_box_with_no_last_ok_keeps_refusing`,
  `test_a_deploy_no_worse_than_last_ok_is_downgraded_to_warn`,
  `test_a_deploy_within_one_percent_worse_is_still_downgraded`,
  `test_exactly_at_the_one_percent_ceiling_is_downgraded`,
  `test_a_deploy_that_makes_it_worse_still_refuses`,
  `test_a_smaller_deploy_than_last_ok_is_downgraded`.
- **(b) Consolidate `operations-cron`/`storefront-cron`/`document-cron` onto `scheduler`, or fold
  each into an existing always-on container: not done this round.** Evaluated both options named
  in the review. Moving each sweep's actual business logic (stock-ledger reconciliation, TLS
  certificate probing, S3 orphan scanning) into `scheduler`'s own image is not the same shape
  `edge-relay`'s consolidation was: `edge-relay` works because every service's outbox has the
  *same* shape (`platform_core.edge_relay` never imports a service's own models, only a generic
  `id`/`organization_id`/`event_type`/`payload` table), so one process can drain 27 of them with
  no service-specific code at all. These three sweeps have no such common shape — folding them
  into one process means importing (or reimplementing) three services' own business logic inside
  a fourth service's image, which is a real architecture change with real correctness risk
  (cross-schema database access, new role grants, new migrations), not a wiring change achievable
  safely in this fix round. Folding into an "existing always-on container" is blocked by the same
  rule that rejected a multi-app API host in the original addendum: CLAUDE.md's `import app`
  ambiguity applies exactly as much to `app.cron` as to `app.main`. Left for a dedicated slice;
  the potential saving (three containers, ~255 MB at the corrected price) is recorded above under
  "The projected budget" and in `memory-budget.json`'s own `planned` comment.
- **(c) False premises fixed.** This document's own "only inventory sold" claim is corrected
  above (the projected-budget table and the decision-list strikethrough on auto-off).
  `tests/infra/test_planned_headroom.py`'s `REALISTIC_PROFILES` is gone — replaced by
  `_real_production_profiles`, which reads `memory-budget.json`'s own `profiles` list (all ten)
  instead of hardcoding `{"inventory"}`; its two headline tests
  (`test_current_production_over_hard_budget_is_grandfathered_not_a_fresh_regression`,
  `test_realistic_today_plus_every_planned_service_projection`) now assert the honest,
  over-100% numbers rather than a fit that never existed. RUNBOOK.md's rollout section is updated
  to say every sold product's profile must be listed explicitly (see M4 below).

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
`.env` says it should cost. `tests/infra/test_memory_budget_totals_containers.py` pins the new
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
- B1(b)'s scheduler consolidation (above) — a real architecture change, not attempted this round.
- H3's branch has no dedicated unit test — it is a single `if` in `preflight.sh` itself rather
  than logic extracted to a tested library function; extracting it would mean shelling out to
  `docker compose exec ... psql` from a test, which is exactly the "never the shared database"
  risk this whole exercise exists to avoid, or mocking `docker` itself, which would test the mock.
- `subscription`'s own missing poisoned-step guard (same shape as the one `approval` had) — not
  touched, per instruction; the billing slice's call.
