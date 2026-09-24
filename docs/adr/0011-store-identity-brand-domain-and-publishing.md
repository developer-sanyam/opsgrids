# ADR-0011 — A store's name is its identity, not its styling: brand, the path split, publishing, and a domain nobody can serve

**Status: Accepted 2026-09-19. Implemented in this change.** Extends `storefronts` with five
columns, adds two routes, one migration and one named-409 vocabulary. No new service, no new
permission, no new role template, no new gateway route, no new event, no change to row-level
security.

Builds on [ADR-0000](0000-phase-0-decisions.md) §5 (the permission grammar), §10 (shared-schema
tenancy, RLS, **no cross-schema foreign keys**) and "Also settled" (audit rows in the same
transaction as the change); on [ADR-0002](0002-row-level-security.md) §1 (the transaction opener
is the chokepoint) and §2 (a bypass buys **discovery only**); on
[ADR-0003](0003-surfaces-and-authorization-topology.md) §3 (authorization is server-side; a UI
gate is not a control); and on **all of** [ADR-0010](0010-delivery-serviceability-and-slots.md),
whose `storefronts` row this extends — in particular §4 (publish gating on the anonymous route),
§10 (the index-timing register), §12 (the five-minute slug cache), §13 (the failure table) and
its **errata 5, 6 and 12**, which are the direct reason this document prints its payloads.

**Supersedes nothing. Amends ADR-0010** in three places, each marked below: the store write's
payload (§4), the index-timing register (§5), and the `custom_domain` column's description (§5).

---

## Context

### The problem, stated once

**A merchant cannot set their own store name.** The name, tagline and logo text live in
`StoreDoc.brand` on the front end — inside the *theme document*, which `adoptTheme` **forks**
(`frontend/product-storefront/src/theme/store.ts:57`, `schema.ts:128`). So a merchant's own store
name is a property of the theme they happened to pick, and re-adopting a theme is an operation
that carries their identity through a copy step. The settings screen says as much out loud:
"Brand name and tagline currently come from the theme you adopted"
(`frontend/product-storefront/src/main.tsx:74`).

A store's name is not styling. It is the one string that is true regardless of how the store
looks, and it belongs on the row that says which store this is.

### What is true today, verified rather than assumed

| Fact | Evidence |
|---|---|
| The brand lives in the theme document and is forked on adopt | `frontend/product-storefront/src/theme/schema.ts:128`; `theme/store.ts:54-64` |
| The settings screen admits the brand comes from the theme | `frontend/product-storefront/src/main.tsx:74` |
| `GET /storefront/store` **collides**: the service returns the identity row, the SPA's `getStore()` expects the theme `StoreDoc` | `services/storefront/app/api/routes.py`; `frontend/product-storefront/src/api/client.ts` `getStore()`; ADR-0010 erratum 6 |
| Nothing failed, because the SPA renders from fixtures | `client.ts` — `USE_FIXTURES` defaults true in dev |
| `storefronts.custom_domain` exists, is nullable, is **read by nothing**, and its own model comment defers it to slice 2 | `services/storefront/app/models.py` before this change |
| **Nothing in this repository can serve a custom domain.** There is no `infrastructure/` directory at all — CLAUDE.md lists Helm charts and kustomize overlays that do not exist. `.github/workflows/` holds only `ci.yml`, with no deploy job. There is no ingress, no TLS termination and no host-based routing anywhere | `ls infrastructure` (absent); `.github/workflows/`; `grep` for ingress/TLS/host routing |
| Nothing is deployed. The 35 services have Dockerfiles and a local compose brings up Postgres, Redis, RabbitMQ, MinIO and Mailhog — that is the whole of it | `tools/dev-bootstrap/compose.yml` |
| A merchant's store today exists at `localhost:4400/live` and nowhere else | `frontend/product-storefront` dev server |
| `status` (`draft`/`published`/`suspended`) already governs whether the anonymous serviceability route answers at all | ADR-0010 §4; `app/api/public.py` |
| The slug→store lookup is cached in Redis for 300 s, **including misses** | `app/selectors.py`; ADR-0010 §12 |
| The buyer's postal code is stored in `localStorage` keyed **by slug** | ADR-0010 §6 |
| The platform's `IntegrityError` handler turns any unique violation into an **anonymous** 409 that names no field, deliberately | `platform_core/errors.py` |
| `suspended` is the entitlement-lapse consumer's state, and that consumer is named, unbuilt and **unowned** | ADR-0010 §4, "Still unowned" |

---

## Decision

### 1. The brand moves onto the identity row

`storefronts` gains `name` (required), `tagline` and `logo_text`. They sit beside `slug`,
`status`, `currency`, `timezone` and `custom_domain`, because they answer the same question —
*which store is this* — and none of them answers *what does it look like*.

**What this buys, concretely:** adopting a different theme cannot change or lose a merchant's
name; the delivery admin and any future admin surface can label a store without reading a theme
document; and a store that has no theme yet still has a name.

**Why `name` is required and the other two are not.** A store with no name is a page with a blank
header and a row every admin list has to invent a label for. A store with no tagline is a store
with no tagline. `logo_text` is null rather than defaulted to the name, so a later rename cannot
leave a stale wordmark nobody remembers typing — **the fallback is the renderer's**, and putting
it in the database would freeze one moment's name into a second column.

### 2. What stays in the theme document

Tokens, pages, sections, `themeKey` and `mode`. Once the brand has left, what remains genuinely
*is* a theme: it is all styling and layout, all of it copied from a theme at adopt time, all of
it safe to replace wholesale when a merchant adopts another one. That is the test this split is
drawn on — **if re-adopting a theme should be allowed to replace it, it belongs in the theme
document; if not, it belongs on the identity row.**

### 3. The path split: identity keeps `/storefront/store`, the theme document becomes `/storefront/theme`

```
GET  /api/v1/storefront/store          → StoreOut      identity   (exists, this ADR extends it)
PUT  /api/v1/storefront/store          → StoreOut      identity   (exists, this ADR extends it)
POST /api/v1/storefront/store/publish  → StoreOut      identity   (new)
POST /api/v1/storefront/store/unpublish→ StoreOut      identity   (new)

GET  /api/v1/storefront/theme          → the theme document       RESERVED, NOT BUILT
PUT  /api/v1/storefront/theme          → the theme document       RESERVED, NOT BUILT
```

**Identity does not move, and the theme document does.** Three reasons, in order of weight:

1. `/storefront/store` returning `StoreOut` is **in the published schema**
   (`libs/contracts/openapi/storefront.json`) and both the delivery admin and the merchant's
   settings surface read it. It is the side with live consumers.
2. The brand leaving the theme document is what makes `store` the wrong name for it anyway.
3. The SPA's `getStore()` is the only caller of the other side, it runs on fixtures, and a flag
   flip is what makes it live — so moving it costs one line in `client.ts` today and costs a
   migration of a live contract later.

**Why `theme` and not `store-document` or `storefront`.** After §2, the document contains exactly
the thing a merchant adopts, forks and replaces: a theme. Naming it after what it holds means the
next contributor does not have to read an ADR to know what is in it. `store-document` names its
storage, not its content, and `storefront` would collide with the product prefix.

**This ADR does not build it, and that is deliberate.** There is no store-document model in this
service, and inventing one inside a brand-and-publishing change is how a second half-specified
store document gets created. The path is reserved here so the frontend has one name to move to
and the storefront-document ADR has one name to build. `tests/test_store.py` asserts there is no
route matching it today, which is what will tell whoever builds it to come back and update this
section.

### 4. Status is a state machine with two named transitions, not a field on a form

**Amends ADR-0010 §9's route table and its `StorePut` payload: `status` is no longer on the store
write at all.**

```
draft ⇄ published          the merchant's two transitions, each its own endpoint
draft, published → suspended      the platform's, via a consumer that does not exist yet
suspended → anything              nobody's. Both merchant transitions refuse it by name.
```

- `POST /storefront/store/publish` — `draft → published`, or a no-op on an already published
  store. **This is the act that makes the store reachable by strangers**, and with it makes that
  merchant's delivery map readable by anyone who knows the slug (ADR-0010 §4's accepted
  disclosure).
- `POST /storefront/store/unpublish` — `published → draft`, or a no-op. The anonymous check 404s
  again, after the slug cache's five minutes.
- Both are idempotent, because a double-click on the one control in this product with
  consequences should not be an error.
- A store is **created `draft`, always**. There is no payload that can produce a published store.

**Why not a `status` field on the settings PUT.** Because then "I fixed a typo in my tagline" and
"I put my shop on the public internet" are the same request, distinguished only by whether a
dropdown happened to be on the right value when the form was submitted. Publishing deserves its
own control, its own confirmation copy and its own audit action, and the only way to guarantee
the first two is to make the second impossible to reach any other way.

**Why a merchant may never set or leave `suspended`.** It is the state the entitlement-lapse
consumer will write (ADR-0010 §4). If a merchant could clear it, the lapse mechanism would be
advisory the day somebody built it. So both transitions refuse a suspended store with 409
`store_suspended` and a sentence naming support. A suspended merchant **can** still edit their
brand and settings — suspension stops a store being served, not a merchant fixing their own
details.

### 5. The custom domain is a stated intention, and it is honest about that

**Amends ADR-0010 §10's index-timing register** (the `custom_domain` unique index moves from
slice 2 to here, because the field becomes merchant-writable here) **and the column's "slice 2
placeholder" description.**

The field ships. What it does is: **record the domain the merchant intends to use.** Nothing
else. Specifically, it does not make the store reachable at that address, because nothing in this
repository can serve a domain at all — no ingress, no TLS termination, no host-based routing, no
deployment. A merchant's store is at `localhost:4400/live`.

So the row carries the claim *and its state*:

```
custom_domain         shop.example.com      lower-cased hostname, or null
custom_domain_status  unverified            or null when there is no domain
```

**`unverified` is the only value anything can reach, and there is no route off it.** `verified`
is in the check constraint from day one — the same reasoning that puts `SLOTTED` in `ZONE_MODES`
before slice 2 can set it — so the hosting slice is a validation change and a new endpoint rather
than an ALTER on a table with live tenant rows. An honest dead end beats a field that lies.

**The hijacking risk, written where the next person will find it.** Serving a domain because
somebody typed it into a form lets one merchant claim another's domain and take their traffic —
including a domain belonging to a business that has never heard of this platform. **Verification
of control (a DNS TXT record, or an HTTP challenge) is therefore a precondition of ever serving a
request on a custom domain, not an enhancement to it.** It does not exist. Nobody has done the
DNS work, and this ADR deliberately does not start it: it is a hosting slice with no foundation
under it, and a half-built verification flow is worse than an absent one because it looks like a
control.

**Uniqueness: global, over non-null values.**
`UNIQUE (custom_domain) WHERE custom_domain IS NOT NULL`. Partial, because null is not a claim —
without the predicate every storefront that has named no domain would collide with every other
one. Global rather than per-tenant, because a domain is a global name: two rows claiming
`shop.example.com` is data the hosting slice cannot act on, whichever way it later breaks the
tie.

**What the loser sees: a named 409, never a 500 and never an anonymous one.**
`errors[0].code = "custom_domain_taken"`, with a sentence that says a domain is claimed by one
store at a time and that support settles ownership because nothing here can prove it. **Nothing
about the winning tenant is disclosed** — not its slug, not its id, not its name — which is
asserted in `tests/test_store.py`. The arbiter is the unique index itself, caught inside a
savepoint; a "is this taken?" `SELECT` first would be a cross-tenant read (row-level security
correctly hides the colliding row) *and* would still race with a concurrent claim.

**The cost of uniqueness, stated plainly: an unverified claim reserves the name.** A merchant who
types a domain they do not own blocks the merchant who does, until somebody releases it. That is
a real denial vector and it is accepted for now on three grounds: nothing is served, so the block
costs the true owner nothing today; the claim is audited with an actor and a timestamp, so
support can release it; and the rule the hosting slice inherits is written here — **a verified
claim supersedes an unverified one.** Releasing a claim (`custom_domain: null`) frees it
immediately, which `tests/test_store.py` proves.

**Shape validation: yes, and nothing more.** A hostname, lower-cased, trailing dot stripped, at
least two labels, each label 1–63 characters of letters, digits and hyphens without leading or
trailing hyphens, 253 characters overall. Refused: a scheme, a path, a port, credentials, a
wildcard, a single label, an IP address, and any non-ASCII character — the last with a message
naming punycode, because silently storing `köln.de` would store a form no `Host` header will ever
carry. The same rule is a check constraint, so a backfill cannot walk past it.

**One list of refusals is not about shape: `RESERVED_DOMAIN_SUFFIXES`.** `opsgrids.com`,
`opsgrids.dev`, `opsgrids.local` and `localhost` cannot be claimed. Since nothing verifies
ownership, the one claim that must not be storable is the platform's own — an unverified row
naming `api.opsgrids.com` is a squat that becomes live routing the day somebody builds host-based
routing and trusts this column.

**Changing the domain resets the status to `unverified`; clearing it nulls both.** A verification
attaches to a value, not to a row. Writing that now costs a line; discovering it later costs a
merchant verifying one name and serving another.

**What is deliberately not here: a verification token column.** The coordinator's brief asked for
room for one, and this is that room — the status enum already carries `verified`, and adding a
nullable token column when the flow that issues it exists is an additive migration with no
backfill and no downtime. A nullable column that nothing writes is the speculative column
ADR-0010 §2 refuses; the *state* is different, because the state is load-bearing today: it is
what tells a merchant the field changed nothing.

### 6. Brand text: trimmed, bounded, single-line — and **not** escaped here

| Field | Limit | Required | Empty after trimming |
|---|---|---|---|
| `name` | 80 | yes | 422 `name_required` |
| `tagline` | 160 | no | stored as `null` |
| `logo_text` | 12 | no | stored as `null` |

The numbers are the renderer's rather than round: the header wordmark takes
`logoText.slice(0, 3)` and the favicon takes two characters
(`frontend/product-storefront/src/theme/render/StoreRenderer.tsx:203`,
`routes/live/liveRoutes.ts:89`), so twelve is generous for a wordmark and small enough that it
cannot become a sentence. Each limit is stated in three places — the column, the schema (so it
reaches the OpenAPI document as `maxLength` and a form can enforce it) and the domain rule (the
net behind both).

**Whitespace is trimmed and collapsed before anything else happens**, including before the length
is checked: a name of exactly 80 characters pasted with a trailing space is a name of 80
characters, and refusing it with "at most 80" while the merchant is looking at 78 letters is
arguing with their clipboard. Runs of whitespace — including a pasted newline or tab from a
spreadsheet cell — become one ordinary space, because all three fields are single-line strings.
**Empty after trimming is not a value:** for `name` it is a named refusal, for the other two it
is `null`, so that "cleared" and "never set" are one state rather than two that render
identically and compare differently.

**Control characters and Unicode bidirectional overrides are refused** (the latter is a
display-spoofing trick, not a shop name). That is the whole of the sanitising this service does.

> **Output encoding is the renderer's job, and this service does not do it.** A name containing
> `<b>` or `&` is stored and returned verbatim — `tests/test_store.py` pins that. Escaping here
> would double-escape the first time anything else read the column, and it would still not
> protect a client that injected the value as HTML. **The frontend unit owns escaping these three
> fields at render time.** This paragraph exists so that ownership is written down rather than
> assumed by both sides.

### 7. The slug cannot change

A slug is chosen once, at creation, and is then immutable. A change is 409 `slug_immutable`.

The reasons compound rather than merely add up: the slug is in every link a merchant has shared;
it is what an anonymous check names in its **request body** (ADR-0010 §4), so every buyer's app
asks for it by name; it keys the buyer's `localStorage` (`opsgrids.storefront.<slug>.postal_code`,
ADR-0010 §6); and the slug→store lookup is cached for five minutes including misses, so a rename
404s every buyer for that long *and then* 404s them permanently on any link that already exists.
And because slugs are globally unique and enumerable, a freed slug can be claimed by somebody
else, who inherits the traffic.

None of that is visible from a settings form, which is why the refusal is code rather than a
warning dialog. A rename is a support operation, and the message says so.

**The payload still carries `slug`**, and must send the stored value back unchanged. A
full-replacement payload with a field it silently ignores is worse than one that refuses: the
merchant who edits the box would otherwise believe they had renamed their store.

### 8. The wire — the actual bodies, printed

**ADR-0010's erratum 5 records that describing a response without printing it cost a rebuild.**
`libs/contracts/openapi/storefront.json` is the contract; this is the same thing in prose so the
two can be compared.

#### `GET /api/v1/storefront/store` → `200 StoreOut`

Permission `storefront.store.settings.read`, floored at ORG, behind the `storefront` entitlement.
404 when the organization has no storefront yet.

```json
{
  "id": "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0",
  "slug": "greenleaf",
  "name": "Greenleaf Grocers",
  "tagline": "Fresh, in eleven minutes",
  "logo_text": "GL",
  "status": "published",
  "currency": "INR",
  "timezone": "Asia/Kolkata",
  "custom_domain": "shop.greenleaf.in",
  "custom_domain_status": "unverified",
  "created_at": "2026-09-19T06:12:41.115332Z",
  "updated_at": "2026-09-19T09:03:07.884210Z"
}
```

Every key above is **always present**; `tagline`, `logo_text`, `custom_domain` and
`custom_domain_status` are the four that may be `null`. `status` is one of `draft` | `published` |
`suspended`. `custom_domain_status` is `unverified` | `verified` | `null`, and **is `unverified`
or `null` in every response this slice can produce**.

#### `PUT /api/v1/storefront/store` → `200 StoreOut`

Permission `storefront.store.settings.write`, floored at ORG. Creates when absent, replaces when
present. Unknown fields are refused (`extra="forbid"`).

```json
{
  "slug": "greenleaf",
  "name": "Greenleaf Grocers",
  "tagline": "Fresh, in eleven minutes",
  "logo_text": "GL",
  "currency": "INR",
  "timezone": "Asia/Kolkata",
  "custom_domain": "shop.greenleaf.in"
}
```

`slug` and `name` are required. `tagline`, `logo_text` and `custom_domain` accept `null` or `""`
to clear. `currency` defaults to `"INR"`, `timezone` to `"Asia/Kolkata"`. **`status` is not a
field**; sending it is a 422 `extra_forbidden`.

#### `POST /api/v1/storefront/store/publish` and `.../unpublish` → `200 StoreOut`

Permission `storefront.store.settings.write`, floored at ORG. **No request body.** The response
is the same `StoreOut` with `status` moved. 404 when there is no storefront.

#### Every failure, by status and `errors[0].code`

The body is always RFC 7807 problem+json (`platform_core.errors`): `type`, `title`, `status`,
`detail`, `instance`, `request_id`, and `errors: [{field, code, message}]`.

| Status | `code` | Field | When |
|---|---|---|---|
| 422 | `extra_forbidden` | the unknown key | A field the payload does not define — including `status` |
| 422 | `missing` | `slug` / `name` | A required field is absent |
| 422 | `too_long` | `name`, `tagline`, `logo_text` | Over the limit **after** trimming — this is what a form sees |
| 422 | `string_too_long` | `custom_domain` | Longer than the column |
| 422 | `string_pattern_mismatch` | `slug` / `currency` | Not `^[a-z0-9][a-z0-9-]{1,62}$` / not `^[A-Z]{3}$` |
| 422 | `name_required` | `name` | Blank after trimming |
| 422 | `name_invalid` / `tagline_invalid` / `logo_text_invalid` | that field | Control characters or a bidi override |
| 422 | `name_too_long` / `tagline_too_long` / `logo_text_too_long` | that field | The domain's own net behind `too_long`; unreachable over HTTP today |
| 422 | `custom_domain_invalid` | `custom_domain` | Not a hostname — scheme, path, port, wildcard, single label, IP, non-ASCII |
| 422 | `custom_domain_too_long` | `custom_domain` | Over 253 characters |
| 422 | `custom_domain_reserved` | `custom_domain` | A platform-owned name |
| 422 | `timezone_invalid` | `timezone` | Not a known IANA zone |
| 409 | `slug_immutable` | `slug` | A different slug on an existing store |
| 409 | `slug_taken` | `slug` | Another organization holds that slug |
| 409 | `custom_domain_taken` | `custom_domain` | Another organization has claimed that domain |
| 409 | `storefront_exists` | `slug` | A concurrent create; this organization already has one |
| 409 | `store_suspended` | `status` | Publish or unpublish on a suspended store |
| 404 | — | — | No storefront for this organization (GET, publish, unpublish) |
| 403 / 401 / 402 | — | — | Permission, credential, entitlement — the platform's usual three |

The 409s carry a distinct `type` URI (`…/slug-immutable`, `…/custom-domain-taken`, …) as well as
the code, so a client may branch on either.

### 9. Data model and the migration

```
storefronts
  + name                  String(80)   NOT NULL
  + tagline               String(160)  NULL
  + logo_text             String(12)   NULL
  + custom_domain_status  String(16)   NULL
  ~ custom_domain         String(255)  NULL — now written by the merchant

  + UNIQUE (custom_domain) WHERE custom_domain IS NOT NULL
  + CHECK name is non-blank and equal to its own btrim
  + CHECK tagline / logo_text are null or non-blank and trimmed
  + CHECK custom_domain is null or a lower-cased hostname of ≥2 labels
  + CHECK (domain null ⇔ status null) and status ∈ ('unverified','verified')
```

**`name` is added in three steps** — nullable, backfilled from `slug`, then `NOT NULL`. `ADD
COLUMN … NOT NULL` with no default fails on a table with rows, and a server default would leave
every existing store named after a value nobody typed and nobody can distinguish from one
somebody did. The slug is the only identity an existing row has, it already matches the
non-blank/trimmed constraint, and a merchant sees it in the name box and edits it.

Row-level security is **unchanged**: `storefronts` already carries `tenant_isolation` with
`USING` and `WITH CHECK`, and adding columns to a policied table changes nothing about the
policy. No cross-schema foreign key is added; money is untouched.

The migration's `downgrade()` drops the five columns and loses a store's brand and domain claim —
there is nowhere else to put them. That is the honest cost of rolling this back, and it is why
the brand's move is an ADR rather than a refactor.

### 10. Authorization and audit

**No new permission.** All four routes are the existing
`storefront.store.settings.read` / `.write`, floored at `Scope.ORG`, behind
`require_product("storefront")`. Publishing stays on `.write` rather than getting a fifth
permission: it is the store's own setting with the largest consequence, not a different job, and
nobody has asked for an edit-the-brand-but-not-publish role. **The trade, plainly: whoever can
rename the store can put it in front of the internet.** The permission's description says so.

Every route has a negative-authorization test (no token → 401, every other permission → 403, a
narrower grant → 403, no entitlement → 402) and a cross-tenant test.

Audit actions, all in the same transaction as the change:

| Action | When |
|---|---|
| `storefront.created` / `storefront.updated` | The settings write, with `before`/`after` over all nine identity fields |
| `storefront.custom_domain.claimed` / `.released` | **Its own row** whenever the domain changes, carrying `verified: false` and `served: false` as facts of the moment |
| `storefront.published` / `storefront.unpublished` | A transition, and only a real one — an idempotent no-op writes nothing |

The domain claim gets its own row because it is the one field here two organizations can contest,
and support adjudicating one should not have to read it out of a generic update's `after` blob.

---

## What this rejects

- **Leaving the brand in the theme document.** It makes a merchant's identity a property of the
  theme they picked, and `adoptTheme` forks it — so a re-adopt carries the name through a copy
  step it has no business being in.
- **Moving the identity row off `/storefront/store`.** It is in the published schema and has two
  live consumers; the theme document has one, and it is running on fixtures.
- **Building the theme document here.** It is the storefront-document ADR's, and inventing a
  second half-specified one inside a brand change is how two of them end up existing.
- **`status` on the settings payload.** Then publishing is a side effect of saving a tagline.
- **Letting a merchant set or clear `suspended`.** It would make the entitlement-lapse mechanism
  advisory before it is even built.
- **A mutable slug.** Every published link, every buyer's stored postal code and a five-minute
  cache all key on it, and a freed slug is claimable by somebody else.
- **A `custom_domain` that implies the store is reachable.** Nothing in this repository serves
  one. A field that reads as configuration but is inert is worse than an absent field, because a
  merchant will point their DNS at nothing and wait.
- **Ownership verification, DNS polling or certificate issuance in this slice.** Real work,
  belonging to a hosting slice with no foundation. Named, not started, and not foreclosed.
- **Per-tenant uniqueness for a custom domain**, which would let two tenants claim one global
  name and hand the tie-break to whoever writes the hosting slice.
- **A pre-flight "is this domain taken?" SELECT.** It is a cross-tenant read, and it races.
- **An anonymous 409 for an expectable collision.** The platform's handler is right to refuse to
  name a constraint; it is wrong as the answer to "which of my fields is the problem".
- **HTML-escaping brand text in this service.** Output encoding belongs to the renderer;
  escaping here double-escapes every other reader and protects nobody.
- **A `custom_domain_verification_token` column nothing writes.** The status enum is the room;
  the token arrives with the flow that issues it, additively.

## Consequences

- **A merchant can set their own store name, and a theme change cannot take it away.** That is
  the whole point, and it is one migration.
- **The path collision is settled in the contract rather than in two frontend units' heads.**
  `libs/contracts/openapi/storefront.json` says `/storefront/store` is `StoreOut`; `/storefront/theme`
  is named here and served by nobody, so the SPA's `getStore()` must move and stay on fixtures
  until the storefront-document ADR lands.
- **Publishing is now a deliberate act with a name, an audit row and a control of its own.** The
  cost: two more endpoints, and a frontend that must call one of them rather than PUT a field.
- **A merchant can claim a custom domain and nothing happens.** That is the honest state of the
  platform, and the response says so on every read. The risk this leaves is that somebody reads
  "unverified" as "pending" and waits; the field description and the UI copy are the only
  mitigations, and the UI copy is the frontend unit's.
- **An unverified claim reserves a global name.** A squatter can block the true owner until
  support intervenes or the claim is released. Accepted while nothing is served; the rule that
  settles it later — a verified claim supersedes an unverified one — is written above rather than
  left to whoever hits it first.
- **Nothing in this platform verifies domain ownership, and until something does, no request may
  ever be served on a `custom_domain` value.** If that sentence is ignored, one merchant serves
  another's domain. It is the single most important line in this ADR.
- **A slug is forever.** Support has no tool to change one; the first request for a rename will
  need one, and it will need to think about the abandoned name.
- **The brand's move is not reversible by a downgrade.** Rolling this migration back loses names,
  taglines, logo text and domain claims.
- **Done: the frontend no longer holds a `brand` key anywhere.** `StoreDoc` (`theme/schema.ts`)
  has no `brand` field, `adoptTheme` no longer takes or forks one, and every screen that draws a
  store's chrome — the buyer route, the console preview, the theme editor, the buyer bundle's
  `ShopFront` — reads name/tagline/logo text from the identity row (`getStoreBrand` /
  `brandFromIdentity`), falling back to a theme's own suggestion only before an identity exists.
  The editor's theme panel shows the brand read-only with a link to `/settings`, which is the only
  place it is written. The residue this bullet used to describe is gone.

## Revisit when

- **The storefront-document ADR lands.** It builds `/api/v1/storefront/theme`, drops `brand` from
  `StoreDoc`, and deletes the reservation test in `tests/test_store.py`.
- **Anything can actually serve a custom domain** — an ingress, TLS termination, host-based
  routing. Then, and not before, verification becomes mandatory work: a token, a DNS TXT or HTTP
  challenge, a `verified` transition, the `verified-supersedes-unverified` tie-break, and a route
  that resolves a `Host` header to a store. Until all of that exists, §5's rule holds.
- **A merchant asks to rename a slug.** Then §7 is revisited with a redirect table and a
  reservation of the old name, not by relaxing the rule.
- **Somebody asks for an edit-the-brand-but-not-publish role.** Then `store.settings.write` splits
  and ADR-0007's rollout applies.
- **A second store per organization is wanted.** Then `uq_storefronts_organization_id` goes, the
  singular routes become collection routes, and the brand goes with the store rather than with
  the organization — which is already where this ADR put it.
- **The entitlement-lapse consumer finds an owner.** It writes `suspended`, and §4's refusals are
  what make that state mean something.

---

## Decisions on open questions (2026-09-24)

Other ADRs have since settled most of this ADR's "Revisit when" list. The storefront-document work
now persists `theme_documents` behind `/storefront/theme`. ADR-0012 built domain serving and
verification. The entitlement-lapse consumer has an owner (ADR-0010's decisions section, backlog
R14). One item waits deliberately on demand and is recorded here as **NOT-TRIGGERED**. The
register of every ADR question is [open-questions-register.md](open-questions-register.md).

### A second store per organization — **NOT-TRIGGERED**

**Trigger:** an organization says in writing that it needs a **second live storefront**, such as a
second brand or a second country. Two slugs for one store is not the trigger; that is a slug
rename, covered by §7's "Revisit when". Today `uq_storefronts_organization_id` enforces one store
per organization, and every storefront route is singular.

**What to build when it fires. It supersedes §4's and ADR-0010 erratum 2's single-store premise,
so it ships with its own ADR:**
- **Drop `uq_storefronts_organization_id`.** Add `storefronts.is_primary` (a partial unique index,
  at most one per organization), backfilled `true` for the existing store.
- **Re-key every per-store table from the organization to the store.** `delivery_zones`,
  `zone_postal_codes`, `serviceability_demand`, `theme_documents`, `categories`, `products`,
  `product_variants` and `enquiries` each gain a `NOT NULL storefront_id` (an in-schema FK),
  backfilled from the single store. `custom_domain_claims` already carries one. Each unique
  constraint becomes `(organization_id, storefront_id, …)`: `uq_delivery_zones_org_code`,
  `uq_zone_postal_codes_org_postal_code`, the demand counter's upsert key, and
  `uq_custom_domain_claims_organization_id`, which becomes unique per store. The index-timing
  register is re-issued with those.
- **Authenticated routes become collections**: `/storefront/stores` and
  `/storefront/stores/{id}/…`. Authenticated routes may carry path variables. The singular routes
  stay for one release as aliases for the primary store, then go.
- **The anonymous surface does not change shape.** Every public call already names the store by
  slug in the body (ADR-0010 §4), and the host resolver already returns one store.
- **Entitlement stays per organization.** The number of stores sold is a catalog plan-feature limit
  (`storefront.stores`, C6's declarations). Creating a store past the limit is a 402.
- **The brand already sits on the store row (§1)**, so it moves with the store and nothing more is
  needed.

This is a schema re-key across nine tables with live tenant rows. That is exactly why it waits for
a real merchant rather than being built on speculation.
