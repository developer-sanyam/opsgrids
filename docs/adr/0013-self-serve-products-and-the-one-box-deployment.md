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
