# ADR-0010 — Delivery serviceability: what a buyer may ask before they sign in, and what a merchant configures behind it

**Status: Proposed 2026-09-19. Not implemented.** Creates `services/storefront` (schema `storefront`,
port 8174, product key `storefront`), four tables, one public route, seven authenticated routes,
four permissions, two role templates, two gateway route entries and one new BYPASSRLS reader role.
No new event, no new external dependency, no geocoder, no map API.

Builds on [ADR-0000](0000-phase-0-decisions.md) §3 (per-audience tokens), §5 (the permission grammar
and actions-as-data), §6 (the scope ladder), §8 (money is integer minor units), §10 (shared-schema
tenancy, RLS, **no cross-schema foreign keys**) and "Also settled" (timestamps are converted using
*the relevant location's* timezone, never the browser's); on
[ADR-0002](0002-row-level-security.md) §1 (the transaction opener is the chokepoint), §2 (the bypass
roles, and that a bypass buys **discovery only**) and §4 (fail-quiet is silent, so the application
must be loud); on [ADR-0003](0003-surfaces-and-authorization-topology.md) §3 (authorization is
server-side at the data-access boundary; a UI gate is not a control); on
[ADR-0004](0004-commercial-model.md) §5 (entitlement is the enforcement point commerce drives); on
[ADR-0008](0008-membership-employee-link.md) §3 (an event type with no consumer is a contract kept
forever in exchange for nothing); and on [ADR-0009](0009-uploaded-images-and-object-storage.md) §3
(the chokepoint sentence a later contributor is most likely to break), §12 (the index-timing
register) and its treatment of the gateway's public surface.

**Supersedes nothing.** It does create the storefront product's first service, and it deliberately
creates only the slice of it this feature needs. The store document, the buyer catalogue, the cart
and the order engine are each their own ADR, and §1 says exactly which columns here are placeholders
those ADRs will extend and which are finished.

---

## Context

The storefront's retail theme ships an announcement bar whose first two settings are
`promiseText` — default **"Delivery in 11 minutes"** — and `locationText` — default **"Sakinaka,
Andheri East, Mumbai 400072"**
(`frontend/product-storefront/src/theme/sections.retail.ts:63-75`). Both are **free text a merchant
types once**. The store tells every buyer in the country that it delivers to Andheri in eleven
minutes, because that is the string in the box.

The ask is to make that true: a buyer says where they are, and the store answers whether it delivers
there, and how.

### What is true today, verified rather than assumed

| Fact | Evidence |
|---|---|
| **There is no address model anywhere.** No `pincode`, `postal_code` or `postcode` column in any service | `grep -rn` across `services/` |
| `operations.warehouses` has `location` as a **plain `String(160)`** — no coordinates, no structure | `services/operations/app/models.py:115-128` |
| `account.locations` exists but is the organization's **own sites**, the input to the `LOCATION` scope rung — not delivery areas | ADR-0003 §3 |
| **PostGIS is installed** and `attendance` already runs a raw `ST_DWithin(...::geography, ...)`, deliberately with no `geoalchemy2` and no geometry column, because the test is one raw query | `tools/dev-bootstrap/init-db.sql:3`; `services/attendance/app/models.py:11-13`, `app/services.py:107-121` |
| **There is no cart, no checkout, no order engine and no buyer identity.** ADR-0008 established the platform has exactly two identity registries — tenant members and OpsGrids staff — and a shopper is neither | ADR-0008; `grep -rn "cart\|checkout\|order" services/` finds only purchase orders |
| **There is no storefront service.** The SPA renders from `fixtures.ts`; `client.ts` holds the store in a module-level variable a reload discards; the live paths it will call are already written as `/storefront/*` | `frontend/product-storefront/src/api/client.ts:1-11`, `:57`, `:68` |
| `/live/*` — the buyer-facing view — is behind `RequireAuth` **deliberately**: today it is a merchant previewing their own store | `frontend/product-storefront/src/main.tsx:95-104` |
| **`GatewayPolicy.public_paths` is an exact-path frozenset**, `full_path in policy.public_paths` — not a prefix match | `platform_core/gateway.py:214`, `:334` |
| `full_path` is `f"/api/{path}"` from the `{path:path}` catch-all, so it **excludes the query string** | `gateway.py:312` |
| On the public branch the gateway forwards **`x-org-slug` only, and only from a 3+-label host**; it forwards **no `x-org-id`** and performs **no entitlement check** — it returns before both | `gateway.py:334-341`, `_slug_from_host` at `:422-432` |
| `_slug_from_host` returns `None` for a two-label host and returns `shop` for `shop.merchant.com` — it is a subdomain heuristic, not a store identity | `gateway.py:428-432` |
| **The gateway now caps request bodies** — `DEFAULT_MAX_BODY_BYTES` 2 MiB, per-route override up to 32 MiB, refused before being read into memory. ADR-0009 §5's prerequisite has landed | `gateway.py:111-116`, `:141`, `:485`; `services/app-gateway/routes.json` (`documents` at 6291456) |
| `enforce_rate_limit` **fails closed**: Redis unreachable raises `UpstreamUnavailableProblem` | `platform_core/cache.py:90-104` |
| The anonymous limit is **60 requests/minute per client IP** | `gateway.py:218`, `:338` |
| `platform_core.db` already supports a **second, tiny BYPASSRLS engine** — `init_directory_engine`, `get_directory_session`, driven by `settings.db_directory_role` (pool 3+2) | `platform_core/db.py:289-341`; `config.py:93`, `:191` |
| **`get_directory_session` falls back to the ordinary session when no directory role is configured** | `db.py:327-341` |
| A service may have **its own** bypass reader on **its own** named tables — `opsgrids_product_directory`, SELECT-only on `product.activations` and `product.products`, granted in that service's `migrations/env.py` | `tools/dev-bootstrap/init-db.sql:19-25`, `:162-177`; `services/product/migrations/env.py:33-60` |
| `account`'s public `POST /api/v1/invitations/accept` is the **existing precedent** for an anonymous request that resolves its tenant on a bypass SELECT and then does every write in a tenant-bound transaction | `db.py:113-118` comment; ADR-0002 "What breaks", path 4 |
| **No `storefront` product is registered** anywhere — no row, no gateway prefix, no permission manifest | `grep -rn storefront services/ libs/contracts/` returns only ADR-0009's document-service references |
| The house answer for org reference data is a permission at **`default ORG, max ORG`** | `services/operations/app/catalogue.py` (supplier, item, warehouse) |
| `install_registration` **skips role codes that already exist** — the reason ADR-0007 exists | ADR-0007 |

### The problem, stated once

**A serviceability answer must be given to somebody who has no token, no account and no session, and
it must be given before they have done anything at all.** Everything else in this ADR is downstream
of that sentence.

The platform has never had to serve such a request for tenant-scoped data. Every anonymous route
today exists to *produce* a token; each is a fixed path on `auth`, and the one that touches tenant
data (`invitations/accept`) is handed an opaque id that resolves to exactly one organization. A
storefront is different in kind: it is a tenant's data, published, read by strangers, at page-view
rates, with the tenant identified only by where the browser happens to be pointed.

Three constraints narrow this more than they first appear:

- **An anonymous request arrives with no organization.** The gateway sets `x-org-id` only after
  decoding a token, and on the public branch it returns before that. So the service must resolve its
  own tenant, and it must do so **before** any tenant-bound transaction can be opened — which means
  before RLS can protect anything.
- **`public_paths` is exact-match.** A public *prefix* is not configurable, and a public path with a
  path parameter (`/public/store/{slug}`) is not expressible at all. The query string is not part of
  `full_path`, so a variable may travel in the query or the body — never in the path. This shapes
  the whole future anonymous buyer API, not just this route.
- **There is nothing to buy.** No cart, no order, no stock. Any design that assumes a place to
  *enforce* the answer is designing against a component that does not exist.

---

## Decision

### 1. Owner: a new `services/storefront`, and this ADR builds one module of it

The storefront product gets its service now, and this ADR builds only its `delivery` module.

- Schema `storefront`, role `opsgrids_storefront` (NOBYPASSRLS), port **8174**, gateway prefix
  `/api/v1/storefront`, product key **`storefront`**, scaffolded with `make new-service`.
- It also gets the product's identity row — `storefronts` (§4) — because resolving "which merchant
  is this" is a prerequisite for the public route and belongs to no one else.

**Why not `services/operations`.** It owns warehouses, which look like delivery nodes and are not:
they are a *procurement* product's stocking locations, behind the `operations` entitlement. A
storefront merchant who has not bought Operations would have to, to configure delivery — and a
cross-schema read to avoid that is forbidden (ADR-0000 §10). **The cost of not reusing them is
real**: a merchant who runs both will maintain two lists of the same physical sites, and that is the
third such list in the platform after `account.locations` and `operations.warehouses`. Accepted,
named, and recorded in §8 as the thing to revisit if "a merchant's sites" ever gets a platform owner.

**Why not a separate `services/delivery`.** Two new deployables for a product with no users. Every
buyer-facing read — the store document, the catalogue, serviceability — needs the same anonymous
host-to-tenant resolution, the same public-path budget at the gateway, and the same bypass reader.
Building that twice is the cost; building it once, in the service that will own the rest of the
buyer surface anyway, is the decision. **What that gives up:** the storefront service will become
large, and "delivery" is a genuinely separable concern that could have had its own release cadence.
If the delivery module ever needs to scale or deploy independently, extracting it is a schema split
— which ADR-0000 §10's no-cross-schema-foreign-keys rule already keeps as a config change.

**Why not `services/document` or another existing platform service.** Serviceability is product data
behind a product entitlement, not a platform capability every customer has. ADR-0009 put assets on
`document` precisely because they *were* platform-wide; this is the opposite case.

**The four columns on `storefronts` that this ADR finishes** are `slug`, `status`, `currency` and
`timezone`. The store document, theme, pages and catalogue are **not** here and are not stubbed. The
later storefront-document ADR extends this table; it does not replace it.

### 2. An area is a postal code. A zone is a named set of them, and the zone carries the behaviour

A merchant types (or imports) **postal codes**. A **zone** is a named bag of postal codes plus the
behaviour that applies to all of them.

```
delivery_zones     ─┐  mode, promise, fee, minimum order
zone_postal_codes  ─┴─ one row per postal code, unique per organization
```

**One postal code maps to exactly one zone per organization** — `UNIQUE (organization_id,
postal_code)`. The lookup is therefore a single-row index hit on the hottest path in the product,
the merchant's mental model is "a postal code is in one zone", and there is no precedence rule to
explain. Which *node* serves that zone, when more than one can, is a separate question answered at
the zone→node layer (§8), not by making the postal code ambiguous.

**Why not a polygon.** A merchant cannot draw one without a map editor we would have to build, and a
buyer cannot supply a point without either a geolocation prompt on first page view (a conversion
tax, and frequently denied) or a geocoder. A geocoder is a paid external dependency with a rate
limit, a per-call cost, a latency budget and a wrong-answer mode, placed on the single hottest path
in the product. **Slice 1 adds zero external dependencies, and that is the largest single benefit of
this decision.**

**Why not a radius from a node.** Same objection — it needs the buyer's coordinates — plus it makes
the answer non-deterministic from data a merchant can read back. PostGIS is available and
`attendance` shows how to use it (raw `ST_DWithin` over `geography`, no geometry column); that
precedent is the one to follow **when a coordinate is genuinely in hand**, which at the serviceability
gate it is not.

**On "a postal code is too coarse for quick commerce" — this is a framing correction.** It conflates
two questions that have different answers and different consumers:

- *Do you deliver here at all?* — asked by a stranger, before shopping, from a pincode typed into a
  box. Postal-code resolution is exactly right, because the postal code is the only thing the buyer
  can supply and the only thing the merchant can administer without a map tool.
- *Can you get to this building in eleven minutes?* — a **promise** question, answered at order time
  from rider supply, distance and load. The incumbents in this market answer it with live ETA at
  checkout, not with a serviceability map.

Slice 1 answers the first. Conflating them would put a geocoder on every page view to make a promise
nobody can keep from static data anyway. Finer-than-postal-code resolution gets an explicit revisit
trigger in §8 and a named mechanism (a zone-level radius test that applies **only** when a coordinate
is present), not a speculative column now.

**What the merchant actually types.** In the zone editor: a zone name, a mode, and a textarea of
postal codes — one per line or comma-separated, pasted from a spreadsheet, de-duplicated and
validated on save. For anything past a few dozen, the CSV import (§9).

**Postal codes are stored normalised** — upper-cased, non-alphanumerics stripped — in `String(16)`.
Slice 1 validates India's six-digit form, in one pure function in `domain.py` keyed by country code
with exactly one entry. The column is wide enough for a UK or Canadian code so a second country is a
validator change rather than a migration; the validator is **not** generalised today, because there
is one country and a generalisation with one use case is a guess.

### 3. Serviceability is not binary: three modes, four buyer-visible states

The zone carries a `mode`:

| `mode` | What the buyer does | Example |
|---|---|---|
| `SLOTTED` | Picks a delivery slot | "Bandra — choose a 2-hour window" |
| `PROMISED` | Is told when it will arrive; picks nothing | "Nashik — by Thu 25 Sep" / "Andheri — in about 30 minutes" |
| `UNSERVED` | Is told no, deliberately | "Leh — mapped and excluded" |

And `UNSERVED` also arises with **no** zone at all — the postal code was never mapped. The response
distinguishes them with a `reason` (`no_mapping`, `excluded`, `zone_inactive`), because "we decided
not to" and "we have not got round to it" are different facts and the demand report (§7) is far more
useful when it can tell them apart.

`PROMISED` carries a `promise_kind`, and this is where the fourth buyer state comes from:

- **`DURATION`** — `promise_minutes`, e.g. 30. Renders "in about 30 minutes". This is the shape the
  shipped retail theme already advertises as free text.
- **`DATE_OFFSET`** — `promise_offset_days` plus `promise_cutoff_time` (local). Orders before the
  cutoff get today + offset; after it, one more day. Renders "by Thursday 25 September".

**The server computes the date, not the client.** ADR-0000's "Also settled" is explicit: timestamps
are converted using *the relevant location's* timezone, never the browser's. The store's timezone is
on `storefronts`; a buyer in Singapore looking at a Mumbai store must see the Mumbai answer. The
response carries a resolved ISO date or a minute count, never prose — the client owns the wording.

**Naming.** `SLOTTED` and `PROMISED` are named for what the **buyer does**, not "quick" and
"standard", because those are merchant marketing words that will change and would then be either
wrong in the enum or migrated in every tenant's data.

`SLOTTED` **cannot be set until slice 2.** Slice 1's zone writer refuses it with a message naming
why. A half-rendered slot picker in front of a real buyer is worse than a feature that is honestly
absent.

### 4. The anonymous check: one exact path, a store slug in the body, one bypass lookup

```
POST /api/v1/storefront/public/serviceability
```

**One path, added to `app-gateway`'s policy — not to `platform_core`.**
`services/app-gateway/app/main.py` becomes
`public_paths=DEFAULT_PUBLIC_PATHS | STOREFRONT_PUBLIC_PATHS`, where the second frozenset lives in
that service. Editing `DEFAULT_PUBLIC_PATHS` would put a product's buyer surface in the shared
library and — since `admin-gateway` builds its own explicit set — would encode a product concern in
the one module both gateways read. A two-line change in the gateway *service* is the right blast
radius.

**Exact match is sufficient here, and this is the constraint to design the whole buyer surface
around.** ADR-0009 could not use `public_paths` because it needed `/a/{org}/{asset}` — a variable in
the path. This route has no path variable and never will: **every anonymous storefront endpoint must
put its variables in the query string or the request body.** `full_path` is built from the catch-all
`{path:path}` and excludes the query, so `GET /api/v1/storefront/public/product?slug=…` is
expressible and `GET /api/v1/storefront/public/product/{slug}` is not. Writing this down now is most
of the value of this section: the later catalogue ADR inherits the rule instead of rediscovering it
against a route table it has already designed.

**Two route-table entries, so the entitlement skip is visible:**

```json
{ "prefix": "/api/v1/storefront/public", "upstream": "http://localhost:8174" },
{ "prefix": "/api/v1/storefront", "upstream": "http://localhost:8174", "product": "storefront" }
```

Longest prefix wins, so the public entry carries **no `product` key**. The public branch of the
proxy returns before the entitlement check anyway (`gateway.py:334`), so a product key there would
be inert — and an inert guard that looks like a guard is worse than none. Splitting the entry makes
"the buyer surface is not entitlement-gated" a fact in the route table rather than a consequence of
statement ordering in the gateway.

**The tenant is resolved from a store slug in the request body, not from the Host header.**

```json
{ "store": "greenleaf", "postal_code": "400050" }
```

`_slug_from_host` is a subdomain heuristic: `None` for a two-label domain, and `shop` for
`shop.merchant.com`. A merchant on their own domain — which is the normal end state for a storefront
— would resolve to nothing or to the wrong thing. The slug is the storefront's own published
identity, known to the SPA at build or runtime config, and it works identically on
`greenleaf.opsgrids.com`, on `shop.greenleaf.in`, and in a preview iframe.

**The trade, plainly:** anyone may ask about any store, so store slugs are enumerable and a
merchant's delivery map is readable by a competitor. Accepted, because the merchant publishes that
map on their own website to every visitor — it is a shopfront sign. The anonymous rate limit (60/min
per IP) throttles bulk enumeration; it does not prevent it, and no scheme that keeps the data usable
by strangers could.

**How the tenant is actually resolved — this is the chokepoint of the feature:**

```
resolve_public_store(slug) ──► (organization_id, storefront_id)      # BYPASSRLS, SELECT-only
        │
        └──► session_scope(organization_id=…) ──► everything else    # NOBYPASSRLS, RLS applies
```

- The first step runs on a **new role, `opsgrids_storefront_directory`** — BYPASSRLS, SELECT-only,
  granted on **`storefront.storefronts` and nothing else**, in `services/storefront/migrations/env.py`,
  exactly as `services/product` grants `opsgrids_product_directory` on its two tables. A bypass
  reader is confined to its own service's named tables; it is never `opsgrids_directory`.
- It buys **discovery only** (ADR-0002 §2). It returns two UUIDs and a status. No delivery data, no
  postal codes, no writes, ever.
- Every subsequent statement — the zone lookup, the demand counter — runs on the ordinary
  NOBYPASSRLS application role inside `session_scope(organization_id=…)`, so RLS applies to the
  anonymous request exactly as it does to an authenticated one.

**The failure mode to guard against, because it is silent and specific.**
`get_directory_session` *falls back to the ordinary session when no directory role is configured*
(`db.py:334-337`). That fallback is the safe direction for account's internal routes. Here it is not
safe, it is **invisible**: with `db_directory_role` unset, the slug lookup runs on the app role with
no GUC bound, returns zero rows, and **every anonymous buyer in the world is told the store does not
deliver to them** — HTTP 200, a well-formed body, nothing in a log. This is ADR-0002 §4's fail-quiet
problem landing on the buyer surface.

So, per ADR-0002 §4's rule that the policy stays quiet and the application gets loud:

- The public route **asserts at startup** that a directory role is configured, and refuses to boot
  otherwise. Not a warning — the service does not start.
- A slug that resolves to nothing logs `storefront.public.store_unresolved` at **warning** with the
  slug, and returns **404**, distinct in both log and status from "resolved, not served".

**Publish gating.** The anonymous route returns 404 unless `storefronts.status = 'published'`. A
draft store is invisible, so a merchant maps zones and tests before any buyer can ask.

**Entitlement does not apply to the anonymous path, and cannot be made to cheaply.** The gateway
skips it; checking it in the service would put an HTTP hop to product-service on every page view.
So a merchant whose `storefront` entitlement lapses keeps answering serviceability until somebody
unpublishes them. The correct mechanism is an event consumer that flips `status` to `suspended` on
deactivation; it is **not built here and has no owner**, and that is stated rather than assumed.
Named in "Revisit when".

### 5. One selector answers every serviceability question, and the client's answer is never an input

**The chokepoint sentence, and the one a later contributor is most likely to break for convenience:**

> *Every serviceability answer — public, admin, and eventually order placement — is produced by
> `selectors.resolve_serviceability(session, organization_id, postal_code)`, inside a tenant-bound
> transaction. No caller computes it, caches it, or passes one in.*

That abstraction earns its place on **two concrete current callers** — the anonymous buyer endpoint
and the merchant's tester (§9) — not on an imagined third. The third (order placement) is named in
§8 because it is what the rule exists to protect.

**The server is authoritative. The client check is for rendering, and for nothing else.**

- The front end calls the public endpoint and renders one of four states: served-with-slots,
  served-with-a-promise, not-served, or **could not check**. It computes nothing.
- The buyer's chosen postal code is stored in the browser. **The answer is not.** It may be kept for
  one thing only — painting the chip instantly on the next page load, visibly marked stale — and it
  is re-fetched on every load regardless. This removes the entire class of "the front end believed a
  stale yes" on the read path, at the cost of one small POST per page load.

**What stops a spoofed or stale client answer letting an unserviceable order through: nothing in
this ADR, because there is no order.** Pretending otherwise would be the dishonest answer. What this
ADR fixes instead is the *rule the checkout ADR inherits*, stated now while it is free:

> The serviceability decision is **re-evaluated server-side inside the order-placement transaction**,
> through the same selector, against the postal code on the order. The client's answer is never an
> input to it. If the answer has changed, placement is refused with a 422 naming the reason. The
> decision actually applied — mode, zone id, promise, fee — is **snapshotted onto the order row**, so
> a dispute six months later is answerable and a later zone edit cannot rewrite history.

The snapshot is the same reasoning ADR-0000 §9 applies to plan versions and invoices: a decision a
customer acted on must remain reconstructable after the configuration behind it changes.

**No server-side cache of the answer in slice 1, and this is deliberate.** The lookup is one index
hit on a table of at most a few hundred thousand rows, fully cached by Postgres; a Redis round trip
is a network hop to avoid a memory read. A cache would also introduce the one failure a merchant
will not forgive — editing their map and watching the store keep the old answer for a TTL. The
host-to-org lookup **is** cached (§12), and the contrast is the point: cache the thing that crosses
an engine boundary and never changes, not the thing that is cheap and changes on purpose.

### 6. The buyer's address lives in the buyer's browser, and slice 1 stores no address at all

There is no buyer identity, so there is nowhere to hang an address book, and inventing one here
would be inventing a buyer registry in a delivery ADR.

- The buyer's postal code lives in `localStorage`, keyed per store
  (`opsgrids.storefront.<slug>.postal_code`), and travels in the request body on each check. A buyer
  who blocks storage re-enters it; nothing breaks.
- **No row is written that identifies a buyer.** No IP, no device id, no full address. A postal code
  alone is not personal data; an IP plus a postal code begins to be, and the demand counter (§7) is
  designed so it never needs one.
- A **full address is PII** the moment it exists, and ADR-0000's "Also settled" rules apply to it in
  full — encrypted at rest, masked by default, unmasking a distinct permission that writes an audit
  row. That is a constraint on the checkout ADR, inherited from here.

**What this design assumes of the buyer-identity work when it lands** — two things, and nothing
more:

1. A saved buyer address will carry a `postal_code` of this shape (normalised, `String(16)`).
2. The selector takes a postal code, not an address object. An address book therefore plugs in by
   passing `address.postal_code`; no code in this module needs to know a buyer exists.

If either turns out false, this ADR is what gets revisited — not silently adapted.

### 7. Demand capture is a daily counter, not a log

Every check increments one row:

```
serviceability_demand(organization_id, check_date, postal_code, outcome, checks)
UNIQUE (organization_id, check_date, postal_code, outcome)
INSERT … ON CONFLICT DO UPDATE SET checks = checks + 1
```

The merchant's report is "these postal codes were asked for and we said no, this many times, in
descending order". **It is the only demand signal a merchant has before they have a single order**,
and it is the strongest argument for shipping slice 1 on its own.

**A row per check was rejected**, and for a security reason rather than a storage one: the public
endpoint is an **unauthenticated write**, so a row-per-check table is a free way for any stranger to
grow a merchant's table without bound, throttled only by a per-IP rate limit that a botnet does not
respect. A counter is bounded by (days × distinct postal codes × outcomes) regardless of traffic —
a few hundred rows per merchant per day at the absolute worst, and typically a few dozen.

**The counter is written in the same transaction as the read, on purpose.** The row returned and the
row counted are then the same decision by construction; there is no "recorded served, returned
unserved" drift to debug. If the upsert fails, the whole request fails with a 503 — which the client
renders as *could not check*, never as *not served* (§13). A best-effort counter in its own
transaction was considered and rejected: it doubles the transactions on the hottest path to protect
against a failure mode (an upsert failing alone on a four-column table) that does not occur
independently.

**Retention: 400 days, dropped by a sweep** following ADR-0005 §6's due-work pattern — 400 rather
than 365 so a merchant can compare this September with last September. The sweep's discovery query
is cross-tenant on the bypass role and its deletes are tenant-bound, the same discipline as
ADR-0009 §8.

### 8. Nodes, slots and who serves whom — the shape, and why none of it is in slice 1

**Nothing here is built in slice 1.** It is specified because the slice boundaries only make sense
if the destination is visible, and because two of these decisions constrain slice 1's schema.

**A delivery node** (slice 2) is `delivery_nodes(code, name, opening hours per weekday,
warehouse_code)` in the `storefront` schema. `warehouse_code` is a **free-text label for the
merchant's own reconciliation** with `operations.warehouses` — no foreign key, no sync, no read
across the schema boundary (ADR-0000 §10). **Coordinates are not included**: with no radius test and
no ETA engine, a latitude and longitude would be two columns nothing reads. They arrive with the
first thing that needs them, and `attendance`'s pattern — plain `Numeric(9,6)`, no geometry column,
a raw `ST_DWithin` in `services.py` — is the one to copy when it does.

**Node-to-zone is many-to-many with a priority** (slice 2): `zone_nodes(zone_id, node_id, priority)`.
**Which node serves an address: the first node in the zone's priority order that is open now and not
blacked out.** Not the nearest — we have no buyer coordinates and, per §2, will not manufacture
them. Merchant-authored priority is deterministic, explicable and readable back, which at this scale
beats a distance computation built on a geocode we would have to buy.

**A slot** (slice 2) is derived, not stored: `slot_templates(node_id, weekday, start_time, end_time,
capacity, cutoff_minutes)` plus `blackout_dates(node_id, date, reason)`, expanded over a rolling
horizon at request time. **Nothing is materialised until something books**, because a materialised
slot table with no booking engine is a table nobody reads and a cron nobody needs.

**What stops two buyers taking the last slot — decided here, built in slice 3.** A single counter
row per `(node_id, slot_date, template_id)` with `capacity` and `taken`, claimed by one statement:

```sql
UPDATE storefront.slot_days SET taken = taken + 1
 WHERE id = :id AND organization_id = :org AND taken < capacity
```

Zero rows affected means the slot went; one means it is yours. No lock is held across an HTTP round
trip, and the claim happens **inside the order-placement transaction**, not when the buyer clicks.

**There is no hold, and that is a decision.** A hold that survives browsing is a distributed lease
with an expiry sweep, and it is the wrong first thing to build: at this scale, the buyer who loses
a slot between selecting and paying is rare, and re-picking is a sentence of copy. A hold arrives
when a real merchant reports real losses.

**Slot reservation and stock reservation are the same problem**, and the platform has neither. They
should get **one** answer — one counter-row mechanism, claimed in the order transaction — not two
that drift. Whichever ADR arrives first owns the mechanism; this one records the claim so the second
does not invent a parallel scheme.

**Fees and minimum order value are stored and displayed in slice 1, and enforced in slice 4.**
`delivery_fee_minor`, `free_delivery_above_minor` and `min_order_minor` are `BIGINT` minor units
against the storefront's ISO currency (ADR-0000 §8). A card saying "Yes — ₹29 delivery, free over
₹499" is a materially better answer than "Yes", and it is three columns. **The rule that must not be
broken:** these are **display values**, and the pricing engine recomputes from its own state at
checkout. This service's numbers are never the ones charged. Two sources of truth for money is how
a customer gets billed something the page did not say, so when the cart engine lands, one of these
two must stop being authoritative — and it is this one.

### 9. Authorization: four permissions on a new product, and no ADR-0007 reconcile

```
storefront.store.settings.read     default ORG, max ORG
storefront.store.settings.write    default ORG, max ORG
storefront.delivery.zone.read      default ORG, max ORG
storefront.delivery.zone.write     default ORG, max ORG
```

**ORG is the only representable rung, and that is deliberate.** Per ADR-0007 §1's amended note,
`max_scope` is a **ladder ceiling, not an allowed-set** — account validates a grant with one `_RANK`
comparison, so a permission ceilinged at ORG makes OWN, TEAM and LOCATION grantable whether or not
this service has an opinion about them, and an undefined rung gets improvised per endpoint. A
delivery zone has no owner, no team and no location reading; setting the **default** to ORG as well
makes every lower rung unreachable rather than merely ungranted. This matches the house answer for
org reference data (`operations`' supplier, item and warehouse specs).

Every route is additionally floored `minimum=Scope.ORG` (ADR-0003 §3: a permission name without
enough scope is not access). Redundant given the ceiling, written anyway, because the ceiling is
catalogue data a later edit can widen and the floor is code in front of the query.

**The demand report is folded into `zone.read`**, not given its own permission. Nobody has asked for
a read-the-demand-but-not-the-zones role, and a fifth permission is a fifth thing to deliver. The
trade, plainly: anyone who can see the zones can see which postal codes were turned away.

**Role templates** — `STOREFRONT_ADMIN` (all four at ORG) and `STOREFRONT_STAFF` (both reads at ORG,
`is_default=True`). Both are **new role codes on a new product**, so `install_registration` installs
them normally and **this ADR needs no ADR-0007 reconcile** — the direct contrast with ADR-0009,
whose two `core.*` permissions could not reach a single existing tenant without one. Putting these
permissions on a new product rather than on `core` is what buys that, and it is a reason for the
choice rather than a happy accident.

**Contracts**: a new `libs/contracts/permissions/storefront.json` with `"sources": ["storefront"]`.
The assertion is **`in-code ⊆ manifest`, never equality** — equality passes only while a product has
one contributing service and fails the moment a second lands, which has already cost one build
failure on `core`.

**The authenticated surface** (all `minimum=Scope.ORG`, all through `get_tenant_session`, every one
with a negative-authz test per ADR-0003 §3):

| Route | Guard | Notes |
|---|---|---|
| `GET/PUT /api/v1/storefront/store` | `store.settings.read` / `.write` | slug, status, currency, timezone |
| `GET/POST /api/v1/storefront/delivery/zones` | `zone.read` / `zone.write` | list, create |
| `PATCH/DELETE /api/v1/storefront/delivery/zones/{id}` | `zone.write` | Delete refuses while postal codes are mapped |
| `GET/PUT /api/v1/storefront/delivery/zones/{id}/postal-codes` | `zone.read` / `zone.write` | The textarea path |
| `POST /api/v1/storefront/delivery/postal-codes/import` | `zone.write` | CSV, §below |
| `POST /api/v1/storefront/delivery/check` | `zone.read` | **The tester.** Same selector, same response shape, plus the matched zone id |
| `GET /api/v1/storefront/delivery/demand` | `zone.read` | The out-of-zone report |

**The tester earns its place at about twenty lines** and is the only way a merchant verifies that a
19,000-row import did what they meant. It is the second caller that justifies §5's selector.

**Bulk import.** `text/csv`, two columns `postal_code,zone_code`, one whole-file transaction.

- **A duplicate postal code in the file is refused**, naming the line numbers — detected in pure
  `domain.py` before any SQL. Last-wins on a file the merchant cannot read back is the silent wrong
  answer, and silent wrong answers on a 19,000-row file are found by a customer, not by the merchant.
- **Two modes.** `merge` (upsert, the default) and `replace` (delete every mapping for the
  organization, then insert). `replace` is what a merchant re-uploading their master file means, and
  without it their **removals never take effect** — a zone they shrank keeps delivering.
- **All or nothing.** A half-applied import is worse than a failed one, because the merchant cannot
  tell which half.
- **One audit row for the import**, carrying counters (rows read, inserted, updated, deleted,
  rejected) — not 19,000 rows. Same shape as ADR-0008's reconcile report: counters in the audit row,
  offending line numbers in the response body.
- **Size arithmetic**: ~19,000 rows × ~12 bytes ≈ **230 KB**, comfortably inside the gateway's 2 MiB
  default, so **no `max_body_bytes` override is needed**. If a merchant's file carries extra columns,
  the override is the lever and it is named here so nobody reaches for chunking first.
- **Time**: a 19,000-row batched upsert should run in one to three seconds against the gateway's
  30-second upstream timeout. If a file ever approaches that, the import becomes a job with a status
  row — deferred, with that number as the trigger.

### 10. Data model, and the index-timing register

Four tables, all `Base, TenantBase` (`id`, `organization_id`, `created_at`, `updated_at`,
`deleted_at`), all `ENABLE` + `FORCE ROW LEVEL SECURITY` with `USING` **and** `WITH CHECK` written
out explicitly (ADR-0002 §3). Foreign keys **within** the schema only.

**`storefronts`** — the product's identity row. `slug`, `status` (`draft`|`published`|`suspended`),
`currency` (ISO-4217), `timezone` (IANA), `custom_domain` (nullable, slice 2).

**`delivery_zones`** — `code` (unique per org), `name`, `mode`, `promise_kind`, `promise_minutes`,
`promise_offset_days`, `promise_cutoff_time`, `delivery_fee_minor`, `free_delivery_above_minor`,
`min_order_minor`, `is_active`, `buyer_note`.

**`zone_postal_codes`** — `zone_id` (in-schema FK), `postal_code`.

**`serviceability_demand`** — `check_date`, `postal_code`, `outcome`, `checks`.

**Index timing — every index appears here with the slice it lands in, and the ones deliberately
rejected are listed rather than omitted.** This register exists because ADR-0006 §15 records two
incidents caused by its absence, the second of which was an engineer resolving a *silence* in a
timing table by analogy and reaching a competent, wrong conclusion.

| Index | Query | Slice |
|---|---|---|
| `zone_postal_codes` **UNIQUE `(organization_id, postal_code)`** | **The hot path.** Every serviceability check, and the import's conflict target | **1**, with the table |
| `zone_postal_codes (organization_id, zone_id)` | The admin's "show me this zone's postal codes", and the `replace` import's delete | **1** |
| `delivery_zones` **UNIQUE `(organization_id, code)`** | Import resolves `zone_code`; the merchant's handle | **1** |
| `storefronts` **UNIQUE `(slug)`** — **no leading `organization_id`, deliberately** | The public slug lookup runs on the bypass role **before any tenant is known**. This is the one index in the design that does not lead with the tenant column, and it is the reason the table needs its own bypass reader | **1** |
| `serviceability_demand` **UNIQUE `(organization_id, check_date, postal_code, outcome)`** | The upsert's conflict target | **1** |
| `storefronts` **UNIQUE `(custom_domain) WHERE custom_domain IS NOT NULL`** | Custom-domain resolution | **2**, with custom domains |
| `slot_days (organization_id, node_id, slot_date)` | Slot availability and the capacity claim | **3**, with booking |
| `serviceability_demand (organization_id, check_date DESC)` | The demand report | **Rejected.** The unique index's `(organization_id, check_date)` prefix already serves the report's range scan. A second index would be write amplification on the hottest write in the service |
| `zone_postal_codes` trigram on `postal_code` | "Search postal codes" in the admin | **Rejected.** A merchant looks up an exact code and the unique index answers it. Revisit only if partial-prefix search is actually asked for |

### 11. Tenancy: what a postal-code lookup costs with RLS on

Every table carries `organization_id` and a forced policy, so the planner ANDs
`organization_id = storefront.current_org()` into every query. An index that did not lead with the
tenant column would scan the whole platform's rows and discard almost all of them — the rule the
`attendance` model states in a comment and this design follows literally.

The hot path, in full, for one anonymous check:

1. `SELECT id, organization_id, status, currency, timezone FROM storefront.storefronts WHERE slug = :slug`
   — bypass role, one index hit, **cached in Redis for 300 s** (§12).
2. `set_config('app.organization_id', …, true)` — transaction-local, per ADR-0000 §10's second trap.
3. `SELECT … FROM zone_postal_codes JOIN delivery_zones … WHERE organization_id = :org AND postal_code = :pc`
   — one unique-index probe plus one primary-key lookup.
4. The demand upsert — one unique-index probe, one insert or one update.

At the planned scale the whole of `zone_postal_codes` is in shared buffers: 50 merchants × the
absolute worst case of 19,000 postal codes each is **950,000 rows**, roughly 60 MB of table and 40 MB
of index. A realistic merchant maps a few hundred codes, not the whole country, so the real figure is
two orders of magnitude smaller. **The read is not the constraint. The write in step 4 is** (§12).

### 12. Capacity, and what saturates first

Numbers marked **(assumed)** are planning assumptions, not measurements.

- **Tenants**: ~50 storefront merchants in year one **(assumed)**.
- **Buyers per tenant**: ~1,000 sessions/day, ~1.2 checks per session (one on entry, occasionally one
  on change) **(assumed)** → **~60,000 checks/day fleet-wide, ~0.7/s average**.
- **Peak**: evening concentration and a promotion spike, ~20/s fleet-wide **(assumed)**.
- **Largest realistic tenant**: a metro grocery chain — 12 zones, ~400 postal codes, 3 nodes, ~8,000
  checks/day **(assumed)**.
- **Data growth per tenant per month**: the zone map is written once and edited rarely — kilobytes.
  The demand counter is (distinct postal codes checked × outcomes) per day, ~50–300 rows/day → **~5
  MB/tenant/year**. Bounded by the postal-code space, not by traffic, which is the whole point of §7.

**The component that saturates first is the demand-counter upsert**, and specifically **one hot row**:
`(organization_id, today, '400050', 'SERVED')` for a merchant whose traffic concentrates in one
postal code. Every check for that code serialises on that row's lock for the duration of the
transaction. Postgres will sustain this to roughly **a few hundred writes per second on a single
row** before contention dominates **(estimated, not measured — measure before trusting it)**. At 20/s
fleet-wide there are three orders of magnitude of headroom.

The named fix, deferred with that trigger: buffer counts in Redis and flush them to Postgres once a
minute from `bin/cron`. That trades exactness for throughput and loses at most a minute of counts on
a Redis failure — acceptable for a demand signal, which is why the mechanism is chosen now and not
built now.

**The second saturation point is the directory engine's pool** — `pool_size=3, max_overflow=2`
(`db.py:302-308`), i.e. **five connections**, on the path of every anonymous request. That is sized
for account's three internal read endpoints, not for buyer traffic. **This is why the slug lookup is
cached in Redis for 300 seconds**: the cache turns a per-request database round trip into a
per-5-minutes-per-slug one, and it is worth caching precisely because it crosses an engine boundary
and changes approximately never. A cold cache under a burst is the failure to watch; if it bites,
the fix is a larger dedicated pool for this service, not a bigger cache.

**Buyer read load never touches the storefront service's authenticated surface at all.** The public
route shares a process with the merchant's admin routes today; if buyer traffic ever degrades the
admin experience, the split is `bin/api` versus a second entrypoint serving only the public router —
deferred, with "admin latency complaints correlate with buyer traffic" as the trigger.

### 13. Timeouts, retries, and what happens when each dependency misbehaves

Decided here, because they are architecture and not implementation detail.

**The rule that governs the whole table, and the most important sentence in this ADR for the buyer:**

> **A check that fails renders as "we couldn't check", never as "we don't deliver here".** Unknown is
> not no. The server never returns an `UNSERVED` outcome it did not compute; `UNKNOWN` exists only as
> a client-side state produced by a transport failure. Telling a buyer in a served area that the shop
> does not reach them is a lost order and a support ticket, and it is the failure a naive
> `catch → not serviceable` produces by default.

| Dependency | Slow | Failing | Wrong |
|---|---|---|---|
| **Postgres, application role** | Public route budget **2 s** end to end, then 503. The chip shows "couldn't check" with a retry | 503 `UpstreamUnavailableProblem`. Nothing recorded, nothing rendered as unserved | RLS misconfiguration returns zero rows **silently** (ADR-0002 §4). The non-production GUC probe in `get_tenant_session` is what makes it loud |
| **Postgres, `opsgrids_storefront_directory`** | Pool exhaustion (5 connections) is the specific failure. Redis cache absorbs the steady state | 503, same rendering | **Role unset → silent fallback to the app role → zero rows → every buyer told "not served".** Guarded by a **startup assertion that refuses to boot** (§4). This is the single worst failure in the design |
| **Redis, rate limiter** | — | `enforce_rate_limit` **fails closed** (`cache.py:90-104`), so the gateway 503s the anonymous request. Correct — an open limiter on a public endpoint is an amplifier. Consequence: **a Redis outage takes the buyer-facing check down entirely**, and the client must render that as "couldn't check" | — |
| **Redis, slug cache** | Miss falls through to the directory query. Never fatal | Same | A stale entry for up to 300 s after a slug or status change. A newly published store may be 404 for five minutes. Accepted; the publish UI says so |
| **The buyer's `localStorage`** | — | Buyer re-enters their postal code each visit | — |
| **The merchant's zone map** | — | — | **The dangerous one: nothing detects a wrong map.** A postal code mapped to the wrong zone returns a confident answer. The tester and the demand report are the only two things that make it visible, which is why both are in slice 1 rather than deferred as tooling |
| **Geocoder / maps API** | — | — | **Does not exist.** Slice 1 has no external dependency beyond Postgres and Redis, and that is the strongest practical argument for §2 |

**Retries**: none anywhere on this path. Every operation is a single short database statement; a
retry on a 2-second budget adds latency to a request the buyer is watching, and the client's own
retry button is the honest place for it.

### 14. Audit

Through `platform_core.audit.record_audit`, in the same transaction as the change (ADR-0000, "Also
settled"):

- `delivery.zone.created` / `.updated` / `.deleted` — `before`/`after` on mode, promise, fees.
- `delivery.postal_codes.imported` — **one row per import**, carrying the mode (`merge`/`replace`)
  and the five counters. The mode is on the row because a `replace` is a destructive act and a
  `merge` is not, and the trail has to distinguish them.
- `storefront.published` / `.unpublished` — a status change is the act that makes a store visible to
  the internet.

**No audit row per serviceability check.** It is an anonymous read of published data at page-view
rates; auditing it would bury every real entry. **No `record_sensitive_read`** either — a delivery
map is public by design, and calling it sensitive would dilute the signal on the paths where it
matters (ADR-0009 §14's reasoning).

**No event is published.** ADR-0008 §3's precedent: an event type with no consumer is a contract kept
compatible forever in exchange for nothing. The scaffold's outbox table stays empty until the order
engine needs something from it.

### 15. The frontend contract

**Additive, and it does not migrate a single merchant's stored data.**

- The announcement bar gains one setting, `serviceabilityEnabled` (toggle, **default off**). Off, the
  existing `locationLabel` / `locationText` chip renders exactly as it does today. On, the chip
  becomes a control that prompts for a postal code and renders the answer.
- **Precedence, because two promises on one screen is a real ambiguity**: once a buyer has an answer,
  the zone's promise replaces the store-wide `promiseText` for that buyer. With no answer yet,
  `promiseText` shows. The merchant's copy is the default, the zone's answer is the truth.
- One module, `src/delivery/`, owning the call, the four render states and the `localStorage` key.
  The four states are **served-with-slots** (slice 2), **served-with-a-promise**, **not served**, and
  **couldn't check** — and the fourth is a first-class state with its own copy, not an error toast.
- `/live/*` stays behind `RequireAuth` in slice 1. The serviceability call is public and works
  without a session, but the store around it is still a merchant preview. Unbolting `RequireAuth` is
  the storefront-document ADR's decision, not this one's — and this ADR's public endpoint is
  deliberately usable the day it is.

### 16. Dev/prod parity and bootstrap

| Thing | Where | Note |
|---|---|---|
| `opsgrids_storefront` (NOBYPASSRLS) | `tools/dev-bootstrap/init-db.sql` | Like every other service role |
| `opsgrids_storefront_directory` (BYPASSRLS, SELECT-only) | same | Grants applied in `services/storefront/migrations/env.py`, guarded by `to_regclass`, re-applied every migration — the `services/product` pattern verbatim |
| `STOREFRONT_DB_DIRECTORY_ROLE` | `.env.example` | **Unset is a silent outage** (§4, §13). The startup assertion is what makes it loud |
| Two gateway route entries | `services/app-gateway/routes.json` | §4 |
| `STOREFRONT_PUBLIC_PATHS` | `services/app-gateway/app/main.py` | Unioned into `DEFAULT_PUBLIC_PATHS`; **not** added to `platform_core` |
| A `storefront` product row + activation | `tools/demo/activate_product.py` | Otherwise every authenticated route 402s while the public one works — a confusing pair of symptoms |
| `libs/contracts/permissions/storefront.json` | export before install | A grant naming a permission row that does not exist is **silently dropped** by `install_registration` |

---

## What this rejects

- **Polygons, or a radius from a node, as the slice-1 area definition.** Both need the buyer's
  coordinates, which means a geolocation prompt or a geocoder: a paid external dependency with a
  rate limit and a wrong-answer mode, on the hottest path in the product, to answer a question a
  postal code already answers. The radius returns, gated on a coordinate actually being present, when
  something produces one (§8).
- **A postal code mapping to several zones with a precedence rule.** It makes the hot lookup
  multi-row, makes the merchant's map unreadable back to them, and answers a question ("which node")
  at the wrong layer. Node priority answers it, at the zone→node layer, where a merchant can see it.
- **Serviceable as a boolean.** The user's own description already contains three outcomes; a boolean
  would have forced "next-day courier to Nashik" to be expressed as a second flag beside it within a
  month.
- **Resolving the tenant from the Host header.** `_slug_from_host` returns `None` for a two-label
  domain and `shop` for `shop.merchant.com` — a subdomain heuristic, not a store identity, and every
  storefront's end state is a custom domain.
- **Editing `DEFAULT_PUBLIC_PATHS` in `platform_core`.** It puts one product's buyer surface in the
  library both gateways read.
- **A public path with a path parameter.** Not expressible: `public_paths` is exact-match. Variables
  go in the query string or the body, and this is a standing constraint on the buyer API, not a
  workaround for this route.
- **Serving the anonymous check on the application role with no tenant bound.** It returns zero rows
  silently and tells every buyer the store does not deliver to them. This is the specific shape of
  ADR-0002 §4's fail-quiet problem on a customer-facing surface.
- **Making the bypass reader `opsgrids_directory`.** Each bypass reader is confined to its own
  service's named tables; `opsgrids_product_directory` is the precedent and the reason is in
  `init-db.sql:177`.
- **Un-policying `storefronts` instead of adding a bypass role.** An exemption would let the *whole*
  application role read every tenant's storefront row, which is precisely the hole a bug in an
  unrelated module would find. A role that holds SELECT on one table is narrower and auditable.
- **Caching the serviceability answer server-side.** One index hit is cheaper than a Redis round
  trip, and a TTL is the mechanism by which a merchant edits their map and watches nothing happen.
  The *slug* lookup is cached, and the difference — it crosses an engine boundary and never changes —
  is the rule.
- **A row per serviceability check.** An unauthenticated write with no bound, on a public endpoint. A
  daily counter is bounded by the postal-code space and answers the merchant's actual question
  better.
- **Storing the buyer's answer in the browser and trusting it.** The postal code is stored; the answer
  is re-fetched every load. A client-side check that is trusted is not a check.
- **Storing a buyer address, or a buyer identity, in this service.** That is a different ADR with a
  different owner, and inventing a shadow registry here is how two of them end up existing.
- **Materialising slot rows before anything books them.** A table nobody reads and a cron nobody
  needs.
- **A slot hold with an expiry sweep.** A distributed lease to solve a collision that, at this scale,
  is a sentence of copy.
- **Reusing `operations.warehouses` as delivery nodes.** Forbidden by ADR-0000 §10 and wrong on
  entitlement: a storefront merchant would have to buy Operations to configure delivery.
- **Emitting `storefront.serviceability.checked` or any other event.** No consumer. ADR-0008 §3.
- **A fifth permission for the demand report** (§9), and **`OWN`/`TEAM`/`LOCATION` rungs on any of
  the four** — left representable-but-undefined they get improvised per endpoint, the trap ADR-0007
  §1 records.
- **`is_dangerous` as a control anywhere in this design.** Zero references in `libs/platform-core`;
  it changes no authorization outcome (ADR-0000 §5's note). What gates these routes is the ORG floor
  and the audit row.
- **A bulk import that lets the last duplicate win**, or one that applies half a file.

## Consequences

- **A buyer can ask "do you deliver to me?" before signing in, and get a real answer.** That works
  with no cart, no checkout, no orders and no buyer identity — which is what makes slice 1 shippable
  on its own.
- **The platform gains its first anonymous read of tenant data, and with it a new bypass reader.**
  `opsgrids_storefront_directory` is narrow (one table, SELECT only) but it is a real cross-tenant
  surface, and it inherits ADR-0002's obligations: the route-blast-radius test, and vigilance that no
  second query is ever added to it for convenience.
- **The worst failure in the design is a missing environment variable.** With
  `STOREFRONT_DB_DIRECTORY_ROLE` unset, `get_directory_session` falls back silently and every buyer
  is told the store does not deliver to them — HTTP 200, no error, no log. A startup assertion is the
  only thing standing between that and production, and it is load-bearing, not defensive polish.
- **A Redis outage takes the public serviceability check down**, because the gateway's rate limiter
  fails closed. That is the right trade for a public endpoint, and it means the client's
  "couldn't check" state is exercised in ordinary operations, not only in theory.
- **The anonymous path is not entitlement-gated, and cannot cheaply be.** A merchant whose
  subscription lapses keeps answering serviceability until somebody unpublishes them. The event
  consumer that would fix it is named and **unowned**.
- **Store slugs and delivery maps are enumerable by anyone.** Judged as costing nothing — a merchant
  publishes both on their own site — but it is a deliberate disclosure and it is not reversible once
  slugs are in circulation.
- **A merchant now maintains a third list of their physical sites**, after `account.locations` and
  `operations.warehouses`, and nothing reconciles them. `warehouse_code` is a label, not a link. This
  is the clearest structural cost in the design and it is the price of ADR-0000 §10.
- **Two sources of truth for a delivery fee exist from slice 1 to slice 4**: this service's display
  value and, later, the pricing engine's. One of them must stop being authoritative when the cart
  lands, and it is this one. Until then a merchant can make the card lie by editing the zone and not
  the pricing rules — which do not yet exist, which is why it is tolerable and dated.
- **The demand counter is a write on the read path**, so the buyer-facing check can never be a
  cacheable GET and can never be served from a CDN. Accepted deliberately: the demand signal is worth
  more than the cache at this scale, and a per-merchant per-postal-code answer is close to uncacheable
  anyway.
- **A wrong zone map produces a confident wrong answer and nothing detects it.** The tester and the
  demand report are the only instruments, which is why both ship in slice 1 rather than being
  deferred as internal tooling.
- **Slice 1 puts a real, working, honest check in front of a buyer whose store document does not
  persist.** The storefront SPA still renders from fixtures and `/live/*` is still behind
  `RequireAuth`. The serviceability answer is durable and the store around it is not — the same shape
  of half-finished that ADR-0009 §"Consequences" recorded, and a reason not to read slice 1 as more
  finished than it is.
- **`SLOTTED` is in the enum from day one but refused by the writer until slice 2.** A merchant will
  see the mode named in the UI and be told it is coming. Shipping it half-rendered would be worse;
  hiding it and adding it later would make slice 2 a schema change instead of a validation change.
- **Every future anonymous buyer endpoint now has a shape it must fit**: a fixed path, variables in
  the query or the body, tenant resolved from a body-carried slug on the bypass reader. That is a
  constraint the catalogue and store-document ADRs inherit — a cost if one of them needs REST-shaped
  paths, and a saving if none do.

## Revisit when

- **The cart and order engine land.** Then §5's inherited rule becomes code: the check is re-run
  inside the placement transaction, the decision is snapshotted onto the order, and §8's counter-row
  claim is built. This is the largest single revisit and it is the one the whole design is shaped to
  make cheap.
- **Buyer identity lands.** Then §6's two assumptions are tested: an address carries a `postal_code`
  of this shape, and the selector still takes a postal code rather than an address. If either is
  false, this ADR is superseded rather than adapted.
- **A merchant asks for sub-postal-code precision** — typically a large pincode straddling a river or
  a highway. Then a zone gains an optional radius or polygon that applies **only when a coordinate is
  present**, `attendance`'s raw `ST_DWithin` over `geography` is the mechanism, and the postal-code
  answer remains the fallback for the anonymous buyer who has given no fix.
- **A merchant's import approaches the 30-second upstream timeout or the 2 MiB body cap.** Then the
  import becomes a job with a status row, or the route takes a `max_body_bytes` override — in that
  order, and both are named so nobody reaches for chunking first.
- **Demand-counter contention appears** — roughly a few hundred writes per second on one hot
  `(org, date, postal_code)` row. Then counts buffer in Redis and flush per minute from `bin/cron`,
  trading a minute of exactness for throughput.
- **The directory pool (5 connections) saturates under a cold cache.** Then this service gets its own
  larger dedicated pool rather than a longer cache TTL, because the TTL is also the merchant's
  publish latency.
- **A storefront entitlement lapses on a published store** and the store keeps answering. Then the
  deactivation consumer that flips `status` to `suspended` gets built, and gets an owner.
- **Buyer traffic degrades the merchant's admin latency.** Then the public router moves to its own
  entrypoint against the same schema — one more process, no schema change.
- **A second country's storefront appears.** Then §2's single-entry validator gains its second entry.
  The column is already wide enough; nothing migrates.
- **`GatewayPolicy.public_paths` gains prefix matching.** Then the "variables in the query, never the
  path" constraint relaxes, and the buyer API's shape becomes a free choice rather than a forced one.
  Until then it is forced, and designing as if it were not is how a route table gets written twice.
- **A customer asks to separate reading the demand report from editing zones.** Then §9's four
  permissions become five, with ADR-0007's full rollout — which this ADR avoided only because the
  product is new.

---

## The plan, sliced

The sequencing is as much the deliverable as the design. Each slice is shippable and each one is
useful on its own.

### Slice 1 — the gate. *Nothing blocks it.*

**Ships:** `services/storefront` scaffolded; the `opsgrids_storefront` and
`opsgrids_storefront_directory` roles; four tables with policies and the slice-1 indexes; the four
permissions, two role templates and the `storefront.json` manifest; two gateway route entries and the
public-path union; `resolve_serviceability`; the public `POST /public/serviceability`; the merchant's
zone editor, postal-code textarea, CSV import, tester and demand report; the frontend module with its
four states behind a default-off toggle; the startup assertion on the directory role.

**Modes available:** `PROMISED` (both `DURATION` and `DATE_OFFSET`) and `UNSERVED`. `SLOTTED` is
refused by the writer with a message naming slice 2.

**The smallest thing that puts a real check in front of a real buyer**, and it is genuinely useful
alone: today the store tells everyone in the country it delivers in eleven minutes. After slice 1 it
tells the truth, tells a merchant which postal codes it turned away, and does it with no new external
dependency. A buyer being told "we don't deliver to 422001 yet — we'll let you know" before they fill
a basket is a better experience than the same sentence at checkout, and the merchant gets an expansion
map out of it before they have a single order.

### Slice 2 — nodes, slots and `SLOTTED`. *Nothing blocks it.*

Adds `delivery_nodes` (code, name, weekday hours, `warehouse_code` label — **no coordinates**),
`zone_nodes` with priority, `slot_templates`, `blackout_dates`; slot availability **computed** from
templates minus blackouts over a rolling horizon; node selection by priority among open nodes;
`SLOTTED` unlocked; the frontend's slot-picker state. Custom domains and their unique index land here
too. **Nothing books anything** — a buyer sees which windows exist, and capacity is displayed, not
claimed.

### Slice 3 — booking. **Blocked on the cart and order engine.**

`slot_days` counter rows and the conditional-`UPDATE` claim, executed **inside the order-placement
transaction**; the serviceability re-check at placement; the decision snapshot on the order row. This
is where §5's inherited rule becomes code. It cannot ship earlier because there is no transaction to
put it in — and building a standalone booking API first would create exactly the second mechanism §8
exists to prevent.

### Slice 4 — fees and minimum order value enforced. **Blocked on cart pricing.**

The stored fee, free-delivery threshold and minimum order stop being display values and become inputs
to a pricing engine that owns the total. One of the two sources of truth for money stops being
authoritative here, and it is this one.

### Slice 5 — the buyer's address book. **Blocked on buyer identity.**

A saved address per buyer, PII-encrypted and masked per ADR-0000, its `postal_code` fed to the same
selector. This service gains nothing but a second caller; if it needs more than that, §6's
assumptions were wrong and this ADR is superseded.

### Slice 6 — sub-postal-code precision. **Blocked on a real merchant asking.**

An optional radius or polygon per zone, applied only when a coordinate is present, with the
postal-code answer as the fallback. Explicitly not built on speculation.

---

## Errata — found during slice 1 implementation (2026-09-19)

**1. §16 names an environment variable that does not exist, and setting it would cause a
second silent cross-service outage.**

The ADR said to set `STOREFRONT_DB_DIRECTORY_ROLE`. `ServiceSettings` declares no
`env_prefix`, so pydantic reads the unprefixed `DB_DIRECTORY_ROLE` — and this repo's single
local `.env` is read by *every* service. Setting it there would have silently rebound
**product-service's** directory reader to storefront's role: a different service, a different
surface, no error and no log. This is the same failure shape as the `get_directory_session`
fallback the ADR was written to close, one layer out.

Corrected: `db_directory_role` is pinned as a default in `services/storefront/app/config.py`
(the `services/product` pattern verbatim), and `.env.example` documents the trap rather than
setting the key. **Any future service using the directory-role pattern must do the same.**

**2. §10's index register does not list `uq_storefronts_organization_id`.**

`GET/PUT /storefront/store` is singular, so "one storefront per organization" must be a
constraint — otherwise a second row is invisible to that route and a merchant is shown
settings they did not edit. The register enumerates *access paths*; this is a *correctness*
constraint. A multi-store ADR drops it.

**3. The `SLOTTED` refusal cannot come from the type.**

"The writer refuses `SLOTTED` with a message naming why" conflicts with typing `mode` as
`PROMISED|UNSERVED` — a narrow `Literal` yields pydantic's "Input should be 'PROMISED' or
'UNSERVED'", which names nothing and helps nobody. The schema accepts all three modes;
`domain.assert_mode_is_available` refuses with the sentence. Slice 2 deletes one line.

**4. "Unknown is not no" was made structural, not documentary.**

Translating a domain error in the route would have put the platform's first `except` on
precisely the path where one must never appear. Instead `domain.Rejected` subclasses
`ValidationProblem` — a rejection *is* a 422 — and `app/api/public.py` contains no `try` and
no `except`. Two tests enforce it: one parses the module's AST and fails on any `Try` or
`ExceptHandler`, the other fails if the string `UNSERVED` appears in that file.

**Still unowned:** the entitlement-lapse consumer that flips `status` to `suspended`. Until it
exists, a lapsed merchant keeps answering serviceability. Needs an owner before slice 2.

### Errata, second pass — found by the two frontend units (2026-09-19)

**5. This ADR prints no response body, and that cost a rebuild.** Two units built against the
prose and derived two different shapes; the backend shipped a third. The merchant half reworked
itself onto `libs/contracts/openapi/storefront.json` when that landed, the buyer half did not,
and the drift was caught by review rather than by a test. **The generated OpenAPI document is the
contract. An ADR that describes a wire format without printing it is an invitation to guess** —
future ADRs print the payload or name the schema component.

**6. `GET /storefront/store` collides with an existing route.** The schema returns `StoreOut`
(id, slug, status, currency, timezone, custom_domain, timestamps); `src/api/client.ts:getStore()`
already calls that exact path expecting the theme `StoreDoc`. Both frontend units hit this
independently. One must move. The delivery module reads only the four columns this ADR finishes,
so it is not the side that should change.

**7. `ImportResultOut.rejected` is a count, but section 9 promised line numbers.** A merchant
cannot act on "7 rejected". The import screen states that the server returned a count and sources
actionable line numbers from the client's own pre-flight plan — honest, but the server should
carry them.

**8. `GET /delivery/demand` returns raw daily counter rows and paginates; section 7 reads as a
range aggregate.** The client walks up to 10 pages (2,000 rows) and sums, then declares itself
truncated rather than naming the wrong busiest postal code. A server-side GROUP BY over the range
removes both the walk and the cap, and is the difference between a report a merchant can trust at
12 zones and one they can trust at 300.

**9. There is no endpoint for the whole postal-code map.** The conflict preview needs
postal-code to zone across the organisation and assembles it with one GET per zone. Acceptable at
this ADR's own worst case of 12 zones; a `GET /delivery/postal-codes` collapses N requests into one.

**10. Nothing in the storefront app mounts a `PermissionProvider`**, so `usePermissionStatus`
returns `unknown` for every code. The delivery admin treats `unknown` as render-and-let-the-server-
answer, and `denied` as hide — because hiding on `unknown` would hide the entire delivery admin
from a merchant holding every grant. Correct given the constraint, but `denied` can never arrive
until `src/auth/AuthProvider.tsx` mounts the provider. **Authorization is server-side regardless;
this only affects whether the UI hides a control it was going to be refused anyway.**

### Errata, third pass — found during reconciliation (2026-09-19)

**11. Section 15 sends the reader to the wrong module.** It says `src/delivery/` owns the buyer's
call, the four render states and the localStorage key. It does not: `src/delivery/` is the
*merchant admin*. The buyer's half is `src/lib/serviceability.ts` plus
`src/theme/render/DeliveryCheck.tsx`. **This misdirection is roughly how the contract drift
happened** — corrected here so the next contributor is not sent to the wrong place.

**12. Section 3 should state what broke us.** `mode` is **required and never null**, and
`UNSERVED` covers all three refusal reasons. The opposite assumption — that an unmapped code
returns `mode: null` — is exactly what the buyer half guessed, and a client that switches on
`mode` cannot tell "we have not reached your area yet" from "we deliberately excluded it".
Classify on `serviceable`, then branch on `reason`.

**13. `src/delivery/types.ts` over-declares required-ness.** `PromiseOut.minutes`/`date`,
`FeesOut.free_delivery_above_minor`/`min_order_minor` and the `MerchantServiceabilityOut`
nullables are declared required, but the schema's `required` lists are only `kind`,
`currency` + `delivery_fee_minor`, and the four on the buyer's shape. A body omitting
`min_order_minor` (server-side default 0) would typecheck-lie. The contract test added for the
buyer half is the pattern to extend here.

**14. The buyer's field is India-only.** The schema accepts a 24-character postal code and any
slug matching `^[a-z0-9][a-z0-9-]{1,62}$`; the buyer field caps at 10 and validates the six-digit
Indian form. Deliberate — mirror, not replace — but a non-India storefront would be refused
*client-side* before the server saw it. Name this when slice 2 widens beyond India.

**15. `SLOTTED` is in the schema enum but the zone writer refuses it in slice 1**, so the
"Delivery windows are shown at checkout." path should be unreachable live today. Worth confirming
against the service rather than assuming.

**Copy decision worth review:** the refusal headline keeps "…yet" only for `no_mapping` and the
unrecognised case. `excluded` drops it, because **"yet" for an area the merchant deliberately
excluded is a promise nobody intends to keep**; `zone_inactive` reads "right now". A zone's own
`buyer_note` still overrides all three.
