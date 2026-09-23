# ADR-0009 — Merchant-uploaded images: object storage, a reference that never expires, and who may read it

**Status: Proposed 2026-09-18. Not implemented.** Adds one table, one storage module, four routes and
two permissions to `services/document` — today a 171-line untouched scaffold from
`tools/new_service.py`. No new service, no new gateway route, no new event.

Builds on [ADR-0000](0000-phase-0-decisions.md) §5 (the permission grammar and actions-as-data), §6
(the scope ladder), §7 (the event envelope), §10 (shared-schema tenancy, RLS, **no cross-schema
foreign keys**); on [ADR-0002](0002-row-level-security.md) §1 (the transaction opener is the
chokepoint), §2 (the bypass roles and "discovery only"), §3 (policy shape) and §4 (fail-quiet is
silent, so the application must be loud); on
[ADR-0003](0003-surfaces-and-authorization-topology.md) §3 (authorization is server-side at the
data-access boundary, and a UI gate is not a control); on [ADR-0004](0004-commercial-model.md) §5
(entitlement is the only enforcement point commerce drives) and §7 (start metering now, bill later);
on [ADR-0005](0005-dunning-and-grace.md) §6 (a due-work table drained by a sweep); on
[ADR-0007](0007-permission-rollout-to-live-tenants.md) (delivering a new permission to tenants that
already exist); and on [ADR-0008](0008-membership-employee-link.md) §3 (an event type with no
consumer is a contract kept forever in exchange for nothing).

**Supersedes nothing.** It does narrow what `services/document` is: this ADR makes it the platform's
**public asset store**, not a document vault. §3 says exactly what that excludes and what would have
to change to widen it.

---

## Context

The storefront editor has an `image` setting field (`frontend/product-storefront/src/theme/sections.ts:174`,
`:238`) whose control is a URL text box (`editor/SettingControl.tsx`, `ImageControl`). A merchant can
paste an address. There is nowhere to upload to.

What is true today, verified rather than assumed:

| Fact | Evidence |
|---|---|
| **MinIO is already in the dev stack** and has been since the beginning — ports 9100 (S3) / 9101 (console), root user `opsgrids` | `tools/dev-bootstrap/compose.yml:63-77` |
| **It has no bucket and no policy.** The container runs `server /data` and nothing creates a bucket or sets an anonymous-read policy | same block; nothing in `tools/dev-bootstrap/` references `mc` beyond the healthcheck |
| **Nothing in the repo talks to it.** No `boto3`, no MinIO SDK, no S3 client, no presigned-URL helper | `grep -rni "s3_\|boto\|minio" libs/platform-core/src/` returns nothing |
| `boto3` is already *earmarked* for this service and deliberately not installed yet | `pyproject.toml:17` — `#   boto3         → document-service` |
| `.env.example` already carries `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`, `S3_REGION` — and **no `ServiceSettings` field reads any of them** | `.env.example:35-40`; `libs/platform-core/src/platform_core/config.py` has no S3 field |
| `/api/v1/documents` is **already routed**, to `http://localhost:8150`, with **no `product` key** — so it is a platform surface every customer has, like `/api/v1/members` | `services/app-gateway/routes.json:55` |
| `services/document` is the scaffold: template `models.py` (outbox, processed-events, audit only), one `/document/info` route, three template tests, **zero domain tables** | `services/document/app/`, `migrations/versions/2f9d9bd227a4_initial_document_schema.py` |
| **`GatewayPolicy.public_paths` is an exact-path set, not a prefix match** — `if full_path in policy.public_paths` | `platform_core/gateway.py:282`, `:174` |
| **The gateway buffers every request and response body whole**, despite opening the upstream with `stream=True`: `content=await request.body()` and `body = await upstream.aread()` | `platform_core/gateway.py:383-410` |
| There is **no storefront service**. The storefront SPA renders from fixtures; `client.ts` holds the store document in a module-level variable that a page reload discards | `frontend/product-storefront/src/api/client.ts:1-11`, `:57` |
| `/live/*` — the buyer-facing view — is **behind `RequireAuth` deliberately**, because today it is a merchant previewing their own store | `frontend/product-storefront/src/main.tsx:116-124` |
| `SettingValue` is `string \| number \| boolean \| null`. No objects, no arrays | `frontend/product-storefront/src/theme/schema.ts:17` |
| `core.json` is a **merge** of two sources today, `account` and `approval` | `libs/contracts/permissions/core.json`, `"sources": ["account","approval"]` |
| `metering` already ingests usage server-to-server, idempotent on `idempotency_key`, `is_billable=false` by default | `services/metering/app/api/internal.py`, `app/models.py:66-96` |
| `catalog` already models a plan feature as a capability flag **or a numeric limit**, JSONB-valued | `services/catalog/app/models.py:273-296` |
| `TenantBase` already carries `deleted_at`, and soft delete is **never filtered automatically** | `platform_core/models.py:73-92` |

### The problem this ADR exists to decide

**A stored `image_url` and a presigned URL are incompatible, and the incompatibility is silent.**

The storefront stores an image address *inside the merchant's store document*, as a section setting.
That document is long-lived merchant data. A presigned S3 URL carries an expiry in its signature. So
the obvious implementation — "upload, presign, put the presigned URL in the setting" — produces a
store whose hero image works for a week and then serves `AccessDenied` to every buyer, with nothing
in any log, no failed request on our side, and a merchant who finds out from a customer.

Whatever is stored in a section setting must stay resolvable **indefinitely**, across a move from
MinIO to AWS, across a bucket rename, and across putting a CDN in front. That is the constraint the
rest of this document is organised around.

Two further constraints narrow the option space more than they first appear:

- **A browser fetching `<img src="…">` sends no `Authorization` header** and never will. Any scheme
  that authorizes the image request per caller has to carry the credential somewhere else — a
  cookie on a different origin, a signed query parameter (which expires, see above), or a
  same-origin proxy. There is no fourth place.
- **An unauthenticated prefix cannot be expressed at the gateway today.** `public_paths` is exact
  match (`full_path in policy.public_paths`), so `/api/v1/documents/public/*` is not a thing the
  gateway can be told about without a `libs/platform-core` change. A content endpoint reachable by
  an anonymous buyer is therefore not merely expensive — it does not currently exist as an option.

---

## Decision

### 1. Public-read object storage with unguessable keys. Store images are public by design

Store images go into a bucket that is **entirely world-readable**, under keys carrying 122 bits of
entropy. The stored reference never expires because nothing about it is signed.

**This is a decision, not a leak.** A storefront is a shopfront: its hero image, its category tiles
and its product photos exist to be seen by an anonymous buyer who has never heard of us. Requiring a
credential to fetch them would be requiring a credential to see a shop window. The three
alternatives each fail on something concrete, and §"What this rejects" gives each its own entry.

What "public" precisely means here, because the word does more work than it looks:

- **Anyone holding the URL can fetch the object.** Forever, until the object is deleted. There is no
  per-request authorization, no revocation short of deletion, and no expiry.
- **Nobody can list the bucket.** The anonymous policy grants `s3:GetObject` and **not**
  `s3:ListBucket`. Without a listing, an object whose key is a v4 UUID is not reachable by
  enumeration; there is no directory to walk.
- **No client ever holds S3 credentials.** The document service holds them and is the only process
  that writes. The browser talks to our API, never to the bucket, on the write path. On the read path
  the browser talks to the bucket and never to our API.
- **There is no "unpublish".** Removing the image from a store does not stop the object being
  fetchable. §8 gives the merchant an explicit way to make it stop, and the upload control has to say
  this in words (§6).

**The merchant must be told.** The upload control states, on the control and not in a help page, that
an uploaded image is readable by anyone who has its address. A merchant who uploads a supplier price
list or a customer's ID scan into a storefront image slot has published it, and the product must not
let them discover that later.

### 2. One bucket, wholly public. The private half is named and unbuilt

`S3_PUBLIC_BUCKET` is public-read in its entirety. Nothing that is not intended to be world-readable
is ever written to it.

The obvious alternative — one bucket with a `pub/` prefix policy and a `prv/` prefix — is **rejected**.
"Is this object public?" should be answerable by reading one bucket policy and running one
`mc anonymous get`, not by reasoning about whether a prefix condition in a policy document matches
the key that was actually written. Prefix-conditioned public access is the shape of the classic S3
disclosure incident, and the platform gains nothing from it: a second bucket is a second line in the
Terraform and a second env var.

So when private documents arrive — a payslip, a signed contract, an ID scan — they get
`S3_PRIVATE_BUCKET`, with no anonymous policy at all and a short-lived presigned **GET** issued by an
authorized API call. A presigned GET is correct *there* precisely because a payslip URL is fetched by
a click on a page that already has a session, not by an `<img>` inside a document that outlives the
session. That endpoint is not built here, and this ADR does not design it beyond naming the bucket.

**Until it exists, `services/document` is the platform's public asset store and not a document
vault.** Its API offers no way to store a private object, so the failure mode is "the feature is
missing", not "the confidential file went somewhere surprising". The README and the service docstring
must say this, because the service's *name* invites the opposite assumption.

### 3. Tenancy: the org is in the key for operations, and it is not the isolation mechanism

The key is:

```
{organization_id}/{asset_id}
```

Both are UUIDs. **No extension** — the format travels in the object's `Content-Type` metadata, which
is what S3 serves, and leaving it out makes the URL derivable from two ids alone (§6 depends on
this).

**The organization id is in the key for operations, not for isolation.** It buys three concrete
things: a prefix delete is a tenant erasure (`DeleteObjects` under one prefix, which is what a GDPR
request or an offboarding needs), a per-prefix cost report answers "what does this customer cost us
in storage", and during an incident an object's tenant is recoverable from the key alone without the
database. It discloses the tenant's UUID to anyone who sees a store image URL, which is worth
approximately nothing: the store is served from a tenant-identifying hostname anyway, and
organization ids already travel in tokens and in `X-Org-Id`.

**What stops one tenant fetching another's object: nothing, and that is the correct answer.** Every
object in this bucket is world-readable, so "tenant A fetched tenant B's object" is not a violation
of anything — it is the same event as "a member of the public fetched it". Phrasing the question as
a confidentiality question leads to the wrong design. The questions that *do* matter at the object
layer are **list, overwrite and delete**, and those are answered by three separate mechanisms:

| Risk | What prevents it |
|---|---|
| Tenant A enumerates tenant B's objects | `s3:ListBucket` is not in the anonymous policy. There is no listing to walk, and a v4 UUID is not guessable |
| Tenant A overwrites tenant B's object | No client holds credentials. Only the service writes, and it writes only keys it just generated |
| Tenant A deletes tenant B's object | Deletion goes through a row lookup in a tenant-bound transaction (below). The service never takes a caller-supplied key |
| Tenant A *reads* B's object | Not prevented. Not a violation. See above |

**Row isolation is unchanged and is where the real enforcement lives.** `document.assets` carries
`organization_id`, gets an RLS policy per ADR-0002 §3 (`ENABLE` + `FORCE`, `USING` **and**
`WITH CHECK`, written out explicitly), and every route goes through `get_tenant_session` — the
chokepoint. Reads live in `selectors.py` and call `ctx.apply()`; writes live in `services.py`.

**The chokepoint that matters for this feature, stated once:** *the object key is never accepted from
a caller.* Every route addresses an asset by its **row id**; the row is fetched under RLS; the key is
read off the row. A route that took `?key=` would be a cross-tenant delete primitive that RLS cannot
see, because RLS does not know what a bucket is. This is the single rule that a later contributor is
most likely to break for convenience, and it is the one to put in the router docstring.

### 4. The service surface

Four routes on `services/document`, under the `/api/v1/documents` prefix that already routes to 8150.
**No gateway change** — the route table needs no edit and the permission resolver needs no change,
which is a large part of why `document` is the right owner.

| Route | Guard | Notes |
|---|---|---|
| `POST /api/v1/documents/assets` | `require("core.content.asset.write", minimum=Scope.ORG)` | `multipart/form-data`, exactly one `file` part. Returns `AssetOut` |
| `GET /api/v1/documents/assets` | `require("core.content.asset.read", minimum=Scope.ORG)` | Paginated, newest first, `deleted_at IS NULL`. The editor's picker |
| `GET /api/v1/documents/assets/{id}` | `require("core.content.asset.read", minimum=Scope.ORG)` | Metadata, not bytes. This service never serves image bytes |
| `DELETE /api/v1/documents/assets/{id}` | `require("core.content.asset.write", minimum=Scope.ORG)` | Soft delete; `?purge=true` for an immediate takedown (§8) |

**Two permissions, both ORG-only, and that is deliberate.**

```
core.content.asset.read   default ORG, max ORG
core.content.asset.write  default ORG, max ORG
```

`max_scope = "ORG"` with `default_scope = "ORG"` makes ORG the only representable rung. This is the
direct application of ADR-0007 §1's amended note: **`max_scope` is a ladder ceiling, not an
allowed-set** — Account validates a grant with a single `_RANK` comparison, so a permission ceilinged
at ORG makes OWN, TEAM and LOCATION grantable whether or not this service has an opinion about them,
and a rung with no defined meaning gets improvised per endpoint. An asset library is an
organization-level resource: an image has no team and no location, and the "own uploads" reading
nobody has asked for would be the only thing OWN could mean. Setting the *default* to ORG as well
makes every rung below it unreachable rather than merely ungranted, so there is nothing to improvise.

Every route is additionally floored `minimum=Scope.ORG`, per ADR-0003 §3's rule that a permission
name without enough scope is not access. The floor is redundant given the ceiling and is written
anyway, because the ceiling is catalogue data that a later edit can widen and the floor is code in
front of the query.

**No `delete` action.** The house grammar uses `read`/`write` (`core.json` has read, write and
`decide`; attendance adds `request`), and ADR-0000 §5 makes a new verb free of migration cost — so
this is a choice, not a constraint. It is refused because nobody has asked for an upload-but-not-
remove role, a third permission is a third thing ADR-0007's reconcile has to deliver to every live
tenant, and §8's design makes an ordinary delete non-destructive. **The trade, plainly: anyone who
can upload can also take an image down.** Revisit when a customer asks for the split.

**Role templates.** `document` contributes its slice to the `core` manifest, alongside `account` and
`approval`:

- `ORG_OWNER` — both grants at ORG.
- `ORG_ADMIN` — both grants at ORG.
- `MEMBER` — **neither.** An ordinary employee has no reason to list or upload to the organization's
  asset library, and "the images are public anyway so read is harmless" is an argument for adding a
  grant that nothing uses.

There is **no storefront role**, because there is no `storefront` product registered anywhere in the
platform (`grep -rn storefront services/ tools/ libs/` is empty). When one is registered, whether
store editing gets its own role is that ADR's question, not this one's.

**This ADR requires an ADR-0007 reconcile.** Two new `core.*` permissions cannot reach any existing
tenant on their own: `install_registration` skips role codes that already exist, so `ORG_OWNER` and
`ORG_ADMIN` in every live organization would keep exactly the grants they have and every upload would
403 while a freshly created organization worked perfectly. Two ordering rules from ADR-0007 apply
verbatim and both are silent when violated: **catalogue export precedes the reconcile** (a grant
naming a permission row that does not exist is dropped without error), and **the `core` manifest is a
merge of three sources now** — assert `in-code ⊆ manifest` and never equality, which already cost one
build failure on `core` when `approval` became the second source.

### 5. Validation: the bytes decide, and the bytes are re-encoded

An upload endpoint is an obvious attack surface. Five rules, each with the specific thing it stops.

**Accepted formats: JPEG, PNG, WebP.** Nothing else.

- **SVG is refused permanently.** It is a script container. An `<img src>` of an SVG does not execute
  script, but a direct navigation to the object URL does, on a host we control and will later put a
  cookie-bearing CDN domain in front of. There is no version of "sanitise the SVG" that is boring.
- **GIF is refused for now**, because it is usually animated and §"downscale and re-encode" would
  silently flatten it to one frame. Refusing is louder than ruining. Revisit if a merchant asks.

**Maximum 5 MiB per file, enforced twice.** Once against `Content-Length` before reading, and once as
a hard byte counter while reading the stream — because `Content-Length` is client-supplied and a
chunked upload does not have to send one at all. Exceeding either returns 413.

Why 5 and not 25: **the gateway buffers whole bodies in memory.** `_forward` does
`content=await request.body()` on the way in and `body = await upstream.aread()` on the way out
(`platform_core/gateway.py:383-410`), despite opening the upstream with `stream=True`. So the
in-flight cost of an upload is roughly twice its size, in gateway RAM, times concurrency. At 5 MiB
and 50 concurrent uploads that is a few hundred megabytes and survivable; at 25 MiB it is not. A
storefront image after downscaling is a few hundred kilobytes, so 5 MiB is generous for the actual
use.

> **Prerequisite, owned by `libs/platform-core`, not by this service.** The service's limit cannot
> protect the gateway: a 2 GB POST to `/api/v1/documents/assets` is buffered at the gateway *before*
> the document service is ever asked its opinion. **A request-body cap in `build_gateway` is a
> precondition for exposing this endpoint to the internet**, and it does not exist today. It is named
> here rather than assumed because it falls between two owners. Locally and in staging the risk is
> tolerable; in production it is a one-request memory exhaustion of the whole gateway.

**A file that lies about its type never gets a chance to.** The declared `Content-Type` and the
filename extension are both **ignored for the decision**. The bytes are decoded with Pillow; if they
do not decode as one of the three accepted formats, the upload is refused with 422 naming what was
accepted. The stored `content_type` is derived from what the decoder says the image is, never from
what the request claimed.

**Every image is decoded and re-encoded.** This is the rule that makes the previous one structural
rather than a check to remember:

- It **strips EXIF**, including GPS. Merchant phone photos routinely carry the coordinates of the
  merchant's warehouse, and the platform's own rule is that PII is masked by default — publishing a
  location to the open internet by accident is the same class of failure.
- It **neutralises polyglots.** A file that is simultaneously a valid JPEG and a valid HTML document
  does not survive a decode/encode round trip; the stored bytes are ours, not the uploader's.
- It **bounds the object**: downscale to a maximum of **2560 px on the long edge**, JPEG quality 85,
  PNG and WebP lossless. 2560 is more than any storefront hero needs and bounds both the buyer's
  download and the re-encode cost. The trade: a merchant cannot use this to store a print-resolution
  asset, and a re-encoded JPEG is very slightly softer than the original.

**Decompression bombs are the attack the size limit does not cover**, and they are the one that gets
forgotten. A 5 MiB PNG can decode to tens of gigabytes of pixels. `Image.MAX_IMAGE_PIXELS` must be
set **explicitly** to a real value — 50 MP, about 8000×6000 — and an image above it refused before
decoding completes. Pillow's default behaviour is a *warning*, not an error, which is worse than
either alternative.

**The merchant's filename is never used to build anything.** It is stored as `original_filename` for
display in the picker and is sanitised at render. The key is the asset UUID (§3).

### 6. The frontend contract: store a root-relative path, resolve the host at render time

`SettingValue` is `string | number | boolean | null`, so what lands in `settings` is a string. Which
string is the decision.

**Not an absolute URL.** An absolute URL bakes the hostname into merchant data. Moving MinIO → AWS,
renaming a bucket, or putting a CDN in front would then mean rewriting every merchant's store
document — a data migration over customer-authored content, to change a hostname. That is precisely
the tenth-change cost this ADR is supposed to avoid.

**Not an opaque `asset:{uuid}` either.** An opaque id needs a resolution step, and the page that
needs to resolve it is a public storefront with no session and — when the storefront service exists —
possibly no authenticated API call available at all. An indirection whose resolver is unreachable
from the place that needs it is not an indirection.

**The thing worth abstracting is the host, and only the host.** So:

- `AssetOut.public_path` is a **root-relative path**: `/a/{organization_id}/{asset_id}`. That is what
  the editor writes into the setting.
- One pure function, `resolveImageSrc(value: SettingValue): string | null`, in
  `frontend/product-storefront/src/theme/render/`, is **the single place a setting becomes an
  `<img src>`**. It passes absolute `http(s)://` values through unchanged, prefixes values starting
  `/a/` with `ASSET_BASE`, and passes everything else through. `ASSET_BASE` is runtime config:
  `http://localhost:9100/opsgrids-public` in dev, the CDN origin in production.
- **The existing URL text box stays.** Pasting an external address remains legal — merchants have
  image hosts already and removing that is a regression. Upload becomes a second way to fill the same
  field, which is why the resolver has to handle both shapes.

That helper earns its place on two current use cases, not one imagined: section `image` settings
(`sections.ts:174`, `:238`), and `Product.image_url` (`api/types.ts`), which is `string | null` today
and bundled sample art under `/img/…`. `theme/render/href.ts` is the existing precedent for "one pure
string function that every renderer calls"; this is its sibling and belongs beside it.

**What the editor does**, concretely: `ImageControl` gains an Upload button beside the URL box. It
POSTs the file to `/api/v1/documents/assets` with the signed-in merchant's own token, receives
`AssetOut`, and calls `onChange(asset.public_path)`. No new auth, no service token, no new trust
relationship — `/api/v1/documents` is a platform route with no product key, and every product SPA
already holds a token on the `app` audience.

**Adoption is not free, and it is not this ADR's to do.** Every `<img>` in the renderer has to go
through `resolveImageSrc` — `Hero.tsx`, `ImageWithText.tsx`, `ProductCard.tsx`, `CategoryTiles.tsx`,
`primitives.tsx` and the preview inside `ImageControl` itself. Until they all do, an uploaded image
renders in some sections and not others, which is the confusing failure. One helper, one test, and a
lint rule or a review habit that no component builds an `src` itself.

### 7. Quota: a hard ceiling now, metered for later, entitlement-gated when it is priced

Storage is the first thing in this platform a tenant can consume without bound. Three separate
mechanisms, and it matters that they are separate.

**A hard per-organization ceiling, enforced at upload, from configuration.** Default 2 GiB and 5 000
assets. The check is `SUM(size_bytes)` and `COUNT(*)` over non-deleted assets for the organization,
inside the same tenant-bound transaction as the insert — cheap at these row counts and correct under
concurrency because the transaction serialises against itself. Over-quota returns `BusinessRuleProblem`
(422), naming the limit and the current usage. Not 402: per ADR-0004 §5, 402 means the *subscription*
does not entitle the product, and it does — this is a domain rule inside an entitled product.

Why a ceiling before anyone is billed: metered-but-ungated means the first abusive or merely
enthusiastic tenant is discovered on the AWS invoice. The ceiling is roughly ten lines and is the only
thing between us and that. **The honest cost: a legitimately large merchant hits a wall, and there is
no per-organization override in slice 1** — raising it means raising it for everyone, via config. The
per-org override arrives with the `catalog` `Feature`, below.

**Metering is deferred, and unusually it is safe to defer.** ADR-0004 §7 says product services emit
usage from the moment they exist because *a meter reading not taken is gone*. That argument does not
hold here, and the reason is worth stating because it is the only place in the platform where it
does not: `document.assets` rows carry `size_bytes` and `created_at`, so the entire history of
`document.asset.bytes_added` is **exactly reconstructable from the table** at any later date. Nothing
is lost by not emitting today.

That safety is conditional, and it turns into a design constraint: **the purge sweep must not delete
rows.** It sets `purged_at` and leaves the row (§8). A sweep that hard-deleted would destroy the very
history that makes deferral safe, and it would do so 30 days after the fact, quietly.

**No event is published.** Per ADR-0008 §3's precedent, adding an event type with no consumer is a
contract we are obliged to keep compatible forever in exchange for nothing — `metering` ingests over
`/internal/v1/usage`, not over the bus, and the storefront service does not exist. The outbox table
is in the scaffold and stays empty until something needs it.

**When storage is priced**, it becomes a `catalog` `Feature` — `{"document.storage_bytes": N}`, which
is exactly the JSONB capability-or-limit shape `Feature` already models — read through the existing
entitlement path, and *then* exceeding it can legitimately be a 402 with an upgrade offer. That is
ADR-0004 §5's rule honoured: commerce drives the switch that already exists rather than inventing a
second one.

### 8. Deletion: soft by default, immediate takedown on request, purged by a sweep

**Implemented 2026-09-23** — see the addendum at the foot of this document for what the sweep
actually is (`services/document/app/cron.py`, not `bin/relay`) and what §9's orphan pass turned
out to need beyond what this section anticipated.

This is the one everyone forgets, and it has a structural constraint that decides it.

**The document service cannot know what references an asset, and never will.** The store document
lives in a storefront service that does not exist; when it does, it will own a different schema, and
ADR-0000 §10 forbids the cross-schema read. A reference check is not expensive here — it is
impossible by construction. Any design that assumes one is wrong.

So deletion is two verbs behind one endpoint:

**`DELETE /api/v1/documents/assets/{id}` — remove from the library.** Sets `deleted_at`. The asset
disappears from the picker and stops counting against the quota. **The object is not touched and the
public URL keeps working.** A published store that references it keeps rendering.

This is right because "delete" in a merchant's head means "take it out of my library", not "break my
live store", and those two readings differ by an outage the merchant did not ask for. The 30-day
window turns an instant silent breakage into a bounded, recoverable one, and gives undelete for free:
clear `deleted_at`, because the object never moved.

**`DELETE …?purge=true` — take it down now.** The object is deleted from the bucket immediately, the
URL starts returning 404, and any store referencing it shows a broken image. `purged_at` is set. This
exists for the case the soft path cannot serve: a merchant who has just uploaded the wrong file —
a customer's ID scan, an internal price list — into a world-readable bucket. "Wait 30 days" is not an
answer to that, and an operator ticket is not either. Same permission, explicit flag, audit row
carrying the flag.

**A sweep purges the rest**, following ADR-0005 §6's due-work pattern — here the due work *is* the
rows, no separate table needed. `bin/cron` runs it. It selects assets with
`deleted_at < now() - interval '30 days' AND purged_at IS NULL`, deletes the object, sets `purged_at`,
and **keeps the row** (§7).

Its tenancy discipline is the one from ADR-0002 §2 and it is easy to get wrong: **the discovery query
is cross-tenant and runs on the BYPASSRLS role; it buys discovery only.** It returns
`(organization_id, id)` pairs and nothing else. Every delete then runs in a
`session_scope(organization_id=…)` transaction on the ordinary NOBYPASSRLS app role. A sweep that did
its writes on the bypass session would be a cross-tenant write path created for a housekeeping job.

The sweep is idempotent in the direction that matters: an object that is already gone is success, not
an error. S3 `DeleteObject` on a missing key returns 204 anyway.

**One scheduling constraint falls out of this.** The retention window is only real if the sweep
exists. If slice 2 (the cron) does not land within 30 days of slice 1 shipping, soft-deleted objects
accumulate forever and quota accounting drifts from reality — quietly, because nothing errors. That
is a date, not a preference.

**The editor warns and does not pretend to check.** The delete confirmation says this image may be in
use on the published store and that we cannot tell. Claiming to have checked would be worse than
saying we have not.

**When the storefront service exists**, the better answer becomes available and should be taken: the
store records which asset ids it references, and the editor calls a batch
`GET /api/v1/documents/assets?ids=…` to warn "3 sections use an image you removed". That is a
storefront-side reference list plus one batch route here — deferred, with an explicit trigger, not
speculatively built.

### 9. Write order, and the orphan it deliberately chooses

**The orphan sweep named at the end of this section is implemented 2026-09-23** — see the
addendum for the credential it needed that this section did not anticipate, and for the
caution built into turning it on.

An upload is two writes that cannot be one transaction: an object into S3 and a row into Postgres.
One of them can fail after the other succeeded, and the choice of order is the choice of which
failure to have.

**Object first, then commit the row.**

- If the object write fails, the transaction rolls back, nothing is written, and the merchant sees a
  retryable 503. **No orphan row.**
- If the row insert fails after the object is written, there is an **orphan object**: unreferenced,
  world-readable at an unguessable key, costing storage.

The orphan object is strictly the less harmful failure. A row without an object is an entry in the
merchant's library whose image is permanently broken, visible, confusing and self-healing never. An
object without a row is invisible, unreachable without the key, and **sweepable** — the same cron
lists the bucket for keys older than 24 hours with no matching row and deletes them, using the same
bypass-for-discovery-then-tenant-bound-write pattern as §8.

### 10. Timeouts, retries and what happens when each dependency misbehaves

Decided here, because they are architecture and not implementation detail.

| Dependency | Slow | Failing | Wrong |
|---|---|---|---|
| **S3 / MinIO, upload** | connect 3 s, read 20 s, 2 retries with jittered backoff (boto3 `Config(retries={"max_attempts": 3, "mode": "standard"})`). The 20 s read budget is for 5 MiB, not for a round trip | Transaction rolls back, 503 `UpstreamUnavailableProblem`, nothing written, merchant's unsaved document untouched, upload retryable | A key collision is the only "wrong", and 122 bits of entropy makes it not happen. A conditional `PutObject` would make it impossible by check rather than by probability — **unverified against the MinIO version in compose**, so not required |
| **S3 / MinIO, read** | Not our request path. Buyer sees a slow image | Buyer sees a broken image. Nothing for us to degrade — there is no fallback we could serve without becoming the proxy §"What this rejects" refuses | — |
| **S3 / MinIO, purge sweep** | Sweep takes longer; it is a cron with no deadline | Row keeps `purged_at IS NULL` and is retried next run. This is why the sweep is idempotent | A delete that silently did nothing leaves a public object; the next run retries because `purged_at` is still null |
| **Postgres** | Standard service behaviour | Service is down; upload 503s | RLS misconfiguration returns zero rows *silently* (ADR-0002 §4) — the non-production GUC probe in `get_tenant_session` is what makes it loud |
| **`metering`** | Not on the request path, because nothing calls it yet (§7) | — | — |
| **CDN**, when it exists | — | Origin-serves | **A purged object may be served from cache until its TTL expires.** So the takedown path must issue a cache invalidation, or the TTL *is* the takedown latency. Named here so the CDN ADR inherits the obligation rather than discovering it |

### 11. Capacity: what saturates first, and at roughly what number

Numbers marked **(assumed)** are planning assumptions, not measurements.

- **Tenants**: ~50 storefront merchants in year one **(assumed)**.
- **Assets per tenant**: a theme has roughly 15 image slots; product photography pushes it up.
  ~60 assets typical, ~5 MiB/month growth **(assumed)**.
- **Largest realistic tenant**: a 500-line catalogue with a photo each — 500 assets, ~125 MB after
  downscaling **(assumed)**.
- **Total year one**: well under 10 GB. Storage cost is a rounding error; storage is not the
  constraint.
- **Peak upload rate**: this is a human dragging files into an editor. A burst of 20 during
  onboarding; ~2 uploads/second across the fleet at peak **(assumed)**.

**The component that saturates first is the re-encode CPU in the document API process**, not storage
and not the database. Pillow is synchronous C: a 5 MiB JPEG decode, downscale and re-encode is
roughly **150–300 ms** **(estimated, not measured)** and holds the GIL for most of it, so it blocks
the event loop for every other request the process is serving.

Two decisions follow:

- **The re-encode runs in a thread pool** (`anyio.to_thread.run_sync`). The upload route is the only
  route in this service that does CPU work, and it must not be the reason `GET /assets` is slow.
- **Roughly 4 concurrent uploads per API process** before other routes visibly degrade **(estimated
  from the figures above; measure before trusting it)**. Beyond that, the work moves off the request
  path: upload the raw bytes to a staging key, re-encode in a worker, swap the object. That is a
  real design with real complexity (a pending state the editor has to render) and it is **deferred
  with that number as its trigger**.

**The second saturation point is the gateway's whole-body buffering** (§5), which is a memory
ceiling rather than a CPU one, and which the body-size cap prerequisite addresses.

**Buyer-side read load never touches us at all.** That is not a happy accident; it is the strongest
argument for §1 over a proxy endpoint. At 50 merchants × 1 000 page views/day × 8 images, a proxy
would put **400 000 requests/day** through the gateway and a Python process sized for a handful of
uploads — three orders of magnitude more traffic than the write path, on a service that would then be
on the critical path of every buyer's first impression.

### 12. Data model, and the index-timing register

One table. `Asset(Base, TenantBase)` — `TenantBase` supplies `id`, `organization_id`, `created_at`,
`updated_at`, `deleted_at`.

| Column | Notes |
|---|---|
| `storage_key` | `{organization_id}/{asset_id}`. Kept after purge for forensics |
| `content_type` | Derived from the decoder, never from the request (§5) |
| `size_bytes` | Of the **stored** object, after re-encode. This is what quota and any future meter count |
| `width`, `height` | Post-downscale. The editor uses them to avoid layout shift |
| `checksum_sha256` | Of the stored bytes. For integrity checks and a possible future dedupe |
| `original_filename` | Display only. Never used to build a key or a path |
| `uploaded_by_user_id` | Who. For the picker and for audit context — **not** a scope input (§4) |
| `purged_at` | Set by the sweep or by `?purge=true`. The row survives (§7) |

**Index timing — every index this model names appears here with a slice, and one deliberately
rejected is marked `Rejected` rather than omitted.** This table exists because ADR-0006 §15 records
two incidents caused by its absence, the second of which was an engineer resolving a *silence* in a
timing table by analogy and reaching a competent, wrong conclusion.

| Index | Query | Slice |
|---|---|---|
| `(organization_id, created_at DESC) WHERE deleted_at IS NULL` | The library listing — `GET /assets`, the editor's picker. The only hot read | **1**, with the route |
| `(deleted_at) WHERE deleted_at IS NOT NULL AND purged_at IS NULL` | The purge sweep's cross-tenant discovery query. Deliberately has no `organization_id` leading column: this query runs on the bypass role across all tenants | **2**, with the cron |
| `(organization_id, checksum_sha256) WHERE deleted_at IS NULL` | Dedupe on re-upload of an identical file | **Rejected.** Cross-tenant dedupe is impossible (separate objects under separate keys), and within one tenant re-uploading the same photo twice is rare and costs a few hundred kilobytes. Revisit only if a real tenant's storage is meaningfully duplicate |

### 13. Dev/prod parity: exactly which variables differ

Names only. No values that are secret anywhere appear in the repo — the MinIO development credentials
are already in `tools/dev-bootstrap/compose.yml` and are not secrets.

| Variable | MinIO (dev) | AWS (prod) | Why it differs |
|---|---|---|---|
| `S3_ENDPOINT_URL` | `http://localhost:9100` | **unset** | boto3 derives the AWS endpoint from the region. Setting it in production pins a hostname for no reason |
| `S3_REGION` | `us-east-1` | the real region | MinIO ignores it; boto3 requires one |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | the compose root credentials | **unset** | See below. This is the important one |
| `S3_PUBLIC_BUCKET` | `opsgrids-public` | the real bucket | — |
| `S3_FORCE_PATH_STYLE` | `true` | `false` | MinIO serves path-style (`host/bucket/key`) unless wildcard DNS is configured; AWS prefers virtual-hosted style |
| `PUBLIC_ASSET_BASE_URL` | `http://localhost:9100/opsgrids-public` | the CDN origin | What §6's `ASSET_BASE` resolves to. Changing it moves every image with no data migration — the payoff of §6 |

**The settings must tolerate absent credentials, and that is a parity requirement rather than a
convenience.** If `S3_ACCESS_KEY` is required, production gets a long-lived static IAM key by
construction, because nobody will restructure the settings under deadline. Leaving it optional lets
boto3 fall back to its default credential chain — instance profile, IRSA, or a local profile — which
is how production should authenticate. Making it optional costs one `| None`; making it required
costs a rotation policy forever.

**`.env.example` changes**: rename `S3_BUCKET` → `S3_PUBLIC_BUCKET`, add `S3_FORCE_PATH_STYLE`, add
`PUBLIC_ASSET_BASE_URL`. `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_REGION` already
exist and stay.

**Bootstrap, which does not exist and without which slice 1 fails at runtime with an opaque error:**

- *Dev*: `tools/dev-bootstrap` creates `opsgrids-public` and sets an anonymous download policy — an
  `mc` one-shot container in `compose.yml` alongside the MinIO service, so `make infra` leaves the
  stack usable.
- *Prod*: the same two facts as a bucket policy granting `s3:GetObject` to `*`, with
  `BlockPublicAccess` disabled **for that bucket only**. There is no `infrastructure/` directory in
  this worktree despite CLAUDE.md naming one, so this has no home yet and needs an owner.

### 14. Audit

Every write path writes an audit row through `platform_core.audit.record_audit`, in the same
transaction, per ADR-0000's "Also settled":

- `asset.uploaded` — `after` carries `storage_key`, `content_type`, `size_bytes`, `width`, `height`,
  `original_filename`.
- `asset.removed` — `before`/`after` on `deleted_at`, and **the `purge` flag**, because an immediate
  takedown and a soft removal are different acts and the trail has to distinguish them.

No `record_sensitive_read`: the assets in this bucket are public by design, so listing them is not a
sensitive read and calling it one would dilute the signal on the paths where it matters.

---

## What this rejects

- **Presigned URLs stored in merchant data.** The failure this ADR exists to prevent: the signature
  expires, every image a merchant placed serves `AccessDenied`, nothing fails on our side, and the
  merchant learns from a customer. A longer expiry moves the date; it does not remove it.
- **A proxy / content endpoint on the document service that authorizes per request.** Not merely
  expensive — **not currently expressible**. `GatewayPolicy.public_paths` is an exact-path set
  (`gateway.py:282`), so an unauthenticated content *prefix* cannot be configured without a
  `libs/platform-core` change; and a browser's `<img src>` sends no bearer token, so an authenticated
  endpoint 401s for the anonymous buyer the storefront exists to serve. Even granting both fixes, it
  would route every image on every page view through the gateway's whole-body buffer and a Python
  process (§11), and re-create the expiry problem the moment anyone reached for a signed cookie. It
  is the right design for **private** documents later (§2), where every one of these objections
  disappears.
- **Storing an opaque document id and resolving it at render time.** The right instinct aimed at the
  wrong target. What changes over the next three years is the *host* — MinIO, then AWS, then a CDN —
  not the key layout, which is our own data. §6 abstracts the host and keeps resolution a pure string
  function, so the public storefront needs no API call to render an image. An opaque id needs a
  resolver reachable from a sessionless public page, which is the one place it is hardest to have.
- **A CDN in slice 1.** It is the right thing and it is not first: it adds a deploy piece, a cache
  invalidation obligation on the takedown path (§10), and an origin configuration, in exchange for
  latency nobody is complaining about at 50 merchants. §6 makes adding it a config change, which is
  the whole point of doing §6 now and the CDN later.
- **A bucket with a public `pub/` prefix and a private `prv/` prefix** (§2). Prefix-conditioned public
  access is the shape of the classic S3 disclosure incident, and a second bucket costs one env var.
- **Presigned `PUT` direct from the browser to the bucket.** It would take the bytes off our request
  path, which is genuinely attractive (§11). It also makes §5 impossible: nothing decodes the file,
  nothing re-encodes it, nothing strips EXIF, and the client writes whatever bytes it likes under a
  key we handed it. Revisit if and when the re-encode moves to a worker, at which point the staging
  key can be presigned and validation still happens before the object becomes public.
- **`is_dangerous` as a control on the upload or delete permission.** It has zero references in
  `libs/platform-core` and changes no authorization outcome (ADR-0000 §5's note, added after ADR-0008
  had to retract exactly this claim). What gates these routes is the ORG floor and the audit row.
- **A third `core.content.asset.delete` permission** (§4).
- **`OWN`, `TEAM` or `LOCATION` rungs on the asset permissions** (§4). Left representable-but-
  undefined, they get improvised per endpoint — the trap ADR-0007 §1 records.
- **A reference count on the asset row, maintained by a `referenced`/`dereferenced` event pair.** Two
  writers and a drift, for a storefront service that does not exist. §8 explains why the check is
  impossible rather than merely costly.
- **Hard delete on `DELETE`.** Breaks a live store instantly and silently, for a merchant whose
  intent was housekeeping (§8).
- **A sweep that hard-deletes rows.** It would destroy the history that makes deferring metering safe
  (§7), 30 days after the fact and quietly.
- **Emitting `core.document.asset.uploaded` with no consumer.** ADR-0008 §3's precedent: a contract
  kept compatible forever in exchange for nothing. The usage history is exactly reconstructable from
  the table, which is the specific reason this deferral is safe here and is not safe generally.
- **SVG uploads** (§5), now or later.
- **Accepting an object key from a caller on any route** (§3). It is a cross-tenant delete primitive
  that RLS cannot see.

## Consequences

- **A merchant can upload an image and the reference never breaks.** No expiry, no renewal, no
  background job keeping URLs alive. That is the single property this ADR was written to get, and
  everything else is downstream of it.
- **Store images are world-readable, and there is no way to make an individual one private later.**
  Visibility is a property of the bucket, and the bucket is the public one. A merchant who uploads
  something confidential has published it; §8's `?purge=true` is the only remedy and it is after the
  fact. The upload control saying so in words is a product requirement, not polish.
- **This service becomes the platform's public asset store, under a name that suggests a document
  vault.** That mismatch is the seam to watch. It is managed by the API offering no private path at
  all — the failure mode is a missing feature, not a misplaced payslip — and by saying so in the
  README and the module docstring. It is not eliminated.
- **This ADR requires an ADR-0007 reconcile, and ADR-0008 did not.** Two new `core.*` permissions
  cannot reach a single existing tenant without it, and the symptom is a 403 on every upload in every
  existing organization while a newly created organization works perfectly. That is a deploy step
  with an owner, not a migration.
- **The organization id appears in every public image URL.** Judged as costing nothing, because the
  store is served from a tenant-identifying hostname anyway — but it is a deliberate disclosure and
  it is irreversible for objects already written, because a key is immutable and merchant documents
  reference it.
- **The re-encode puts CPU on an API request path for the first time in this platform.** Every other
  service's request path is I/O. The thread pool keeps it off the event loop; it does not keep it off
  the box, and it makes this service's capacity a function of image size rather than of request
  count.
- **The gateway becomes a memory amplification point for a body it cannot stream.** `_forward`
  buffers whole bodies, so the service's 5 MiB limit protects the service and not the gateway. The
  `build_gateway` body cap (§5) is a precondition owned elsewhere, and this feature should not be
  exposed to the internet before it lands.
- **Real storage can exceed the configured quota** by up to one retention window's worth of
  deletions, because a soft-deleted asset stops counting immediately and its object survives 30 days
  (§8). Bought deliberately: the alternative is a merchant who deletes images to free space and
  watches nothing happen for a month.
- **A merchant who hits the quota needs a config change that affects everyone**, because slice 1 has
  no per-organization override (§7). Acceptable at 50 tenants; it is the first thing to hurt at 500,
  and the `catalog` `Feature` is the named fix.
- **Orphan objects are a normal, expected state** (§9) rather than an anomaly, and the reconcile pass
  is what keeps them bounded. Nothing alerts on one; the sweep just removes it.
- **The frontend gains a chokepoint it does not have today.** Every `<img>` in the renderer must go
  through `resolveImageSrc`, and until all of them do, an uploaded image renders in some sections and
  not others. Six components, one helper, one test.
- **The three engineers currently in `frontend/product-storefront/` inherit §6 as work**, and §6 was
  written to be additive: the resolver handles the absolute URLs the app already stores, so it can
  land before any upload exists and change nothing.
- **Slice 1 delivers a durable image into a store document that is not durable.** There is no
  storefront service; `client.ts` holds the store in a module-level variable that a reload discards.
  So "upload an image and see it on the store" is true within a session and the asset outlives the
  store that references it — which is an argument for the asset library being independent of the
  store, and a reason not to read slice 1 as more finished than it is.

## Revisit when

- **The first genuinely private document arrives** — a payslip, a contract, an ID scan. Then §2's
  named-but-unbuilt `S3_PRIVATE_BUCKET` is built, with a short-lived presigned GET behind an
  authorized route, and the rejection of a proxy/content endpoint is re-read: every objection to it
  in §"What this rejects" is about *anonymous* fetches, and none of them apply there.
- **The storefront service exists.** Then §8's reference warning becomes possible — the store records
  which asset ids it uses, and a batch `GET /assets?ids=` lets the editor say which sections would
  break. Until then the delete confirmation says we cannot tell, because we cannot.
- **Concurrent uploads per API process pass roughly four**, or upload latency becomes a complaint.
  Then the re-encode moves off the request path to a worker with a staging key, and presigned `PUT`
  becomes reconsiderable because validation would still happen before the object goes public (§11).
- **A CDN lands in front of the bucket.** Then `PUBLIC_ASSET_BASE_URL` changes and nothing else does —
  but that ADR inherits §10's obligation to invalidate on takedown, or the cache TTL silently becomes
  the real takedown latency.
- **Storage is priced.** Then §7's ceiling becomes a `catalog` `Feature` read through entitlement,
  over-quota becomes a 402 with an upgrade offer, and `document.asset.bytes_added` is backfilled from
  the table — which is only possible because the sweep keeps rows.
- **A customer asks for an upload-but-not-remove role.** Then §4's two permissions become three, with
  ADR-0007's full rollout.
- **A merchant needs an animated image, or a vector logo.** GIF and SVG are refused for different
  reasons (§5) and only the GIF refusal is negotiable; the SVG one is not, and the answer there is a
  server-side rasterisation, not an exception.
- **`build_gateway` gains a request-body cap**, or a streaming forward path. The first is a
  precondition (§5); the second would remove the memory amplification and let the 5 MiB limit be
  reconsidered on its own merits rather than on the gateway's.
- **A key collision is ever observed, or MinIO's conditional-write support is verified.** Then the
  `PutObject` precondition in §10 moves from "not required" to "required", and the guarantee stops
  being probabilistic.

## Addendum (2026-09-23) — MinIO on the edge VM, and a header a public bucket cannot be trusted with

§13's "Bootstrap, Prod" said the production half of this ADR "has no home yet and needs an
owner." It now does: `infrastructure/edge/` (ADR-0012), `minio` and `document` behind the
`storefront` compose profile, `minio-init` running the same anonymous-`GetObject`-only bucket
policy `tools/dev-bootstrap/compose.yml` uses in development. This addendum records what that
deploy actually needed beyond §13's dev/prod table, because one of the three findings below is a
stored-XSS-to-account-takeover path and the fix is a Caddy configuration decision this ADR never
anticipated, not an application change.

**MinIO gets no host port; Caddy is a plain reverse proxy in front of it, and that is still the
right shape.** §"What this rejects" already refuses an *authorizing* content proxy — a service
that decides per request whether to serve an object. What runs on the edge is not that: it is an
unauthenticated `reverse_proxy minio:9000` for `GET`/`HEAD` on `/opsgrids-public/*`, with no
application logic and no session ever consulted, because the bucket behind it already grants
`s3:GetObject` to `*` and nothing else. There is nothing for a proxy to authorize that MinIO's own
anonymous policy has not already decided. This is acceptable precisely because §1's decision — the
bucket is world-readable by design — has not changed; a proxy in front of a *private* bucket would
be a different and refused shape.

**What was missing, and it is a real finding, not a hardening nicety: a public-bucket reverse
proxy cannot be a *bare* one.** MinIO honours `?response-content-type=` (and the sibling
`response-*` overrides) on an anonymous `GetObject` — a caller may ask MinIO to lie about the
`Content-Type` of an object it did not write. `services/document` never issues such a query
itself, but nothing stops a browser from appending one to a URL it already has, and every object
in this bucket is reachable by anyone who has the URL (§1). Combined with the original Caddy
route, which imported into *every* site this Caddyfile serves — `app.opsgrids.com`,
`ops.opsgrids.com`, every other platform hostname, not only the one `PUBLIC_ASSET_BASE_URL` names
— the consequence was concrete: upload an HTML/JS payload through the ordinary, authorized upload
endpoint (§4's five validation rules bound *image* formats, not arbitrary bytes rejected outright
as non-images — see the honest residual below), then fetch it back as
`/opsgrids-public/<org>/<asset>?response-content-type=text/html` on `app.opsgrids.com`, where the
`.opsgrids.com`-scoped refresh cookie (ADR-0001) is live. That is stored XSS with a direct path to
session theft and account takeover, reachable without ever touching an authenticated route.

**The fix, entirely in `infrastructure/edge/caddy/Caddyfile`, no application code:**

1. **The route now lives only on `EDGE_HOST`'s own site block**, not in the shared snippet every
   platform hostname imports. `PUBLIC_ASSET_BASE_URL` already names exactly one host (§13); the
   Caddyfile now enforces that it is the *only* host this path resolves on, so a payload served
   back with a lying content type has no platform cookie to steal even if every other layer below
   failed.
2. **The query string is stripped before the request reaches MinIO** (`rewrite * {path}?`) — the
   trap named in the Caddy documentation and easy to get backwards: `rewrite * {path}` alone does
   **not** drop the incoming query, only a rewrite target ending in a bare `?` does. This closes
   `response-content-type` and every other `response-*` override at the edge, not by trusting
   MinIO to have no more of them.
3. **Response hardening on top of both**: `Content-Security-Policy: default-src 'none'; sandbox`
   and `X-Content-Type-Options: nosniff` on every response this route serves, `Cache-Control:
   public, max-age=86400` (objects are content-addressed UUID keys, written once, ADR-0009 §3 —
   the honest trade-off is that a `?purge=true` takedown, §8, does not invalidate a cache that
   already has the object, so the real takedown latency for an already-cached response is bounded
   by this TTL, not by the API call), `request_header -Authorization` (no session ever belongs on
   this path; stripping it is one less thing an operator error downstream could turn into a
   leak), and `-Server` (MinIO's own version banner is not this platform's to publish). None of
   these is the boundary on its own — the host restriction and the query strip are — but each is
   free and each is the layer that still holds if a future MinIO version adds an override nobody
   here has read about yet.

**The honest residual, not closed by this addendum.** §5's validation refuses SVG permanently and
GIF for now, and every accepted upload is decoded and re-encoded by Pillow before it is ever
written — a polyglot does not survive that round trip (§5, "neutralises polyglots"). The
`evilasset` payload used to verify this fix was written directly into a scratch MinIO bucket, not
through `POST /api/v1/documents/assets` — proving the Caddy-layer fix independently of whether
`process_upload` would have accepted the bytes in the first place, which it would not (Pillow has
no HTML/script decoder, so `UnidentifiedImageError` refuses it with 422 before any bytes reach
S3). The Caddy hardening above exists anyway, for the object that gets into the bucket by some
path this ADR did not anticipate — a future service sharing the bucket, an operator's `mc cp`, a
bug in `process_upload` — because "the application validates it" and "the edge cannot serve it as
HTML regardless" are two different guarantees and only the second one is a boundary a public
bucket can actually stand behind.

**Verified against the pinned images, not assumed**: `caddy:2.10.0-alpine` serving the production
Caddyfile (`caddy validate`, clean) and, in a scratch compose project against a real
`quay.io/minio/minio` with the anonymous-`GetObject` policy `minio-init` applies, the following
held simultaneously — `?response-content-type=text/html` on a non-image object returns
`Content-Type: application/octet-stream` (MinIO's own default) with the CSP and `nosniff` headers
present and no `Server` header; a plain `GET` of a real object still returns its true content
type; a bucket-root listing still returns `403 AccessDenied`; and a `..`-traversal probe and a
`POST` both miss the route entirely and hit the same 404 fallback as before this change, so
nothing about the isolation properties §3 depends on moved.

**The longer-term plan, named here so it is a decision rather than a thing that happens by
default:** the durable fix is a cookieless asset domain entirely outside `.opsgrids.com` — a
second-level domain with no cookie ever set on it, so an object served with any content type on
that host has nothing to steal regardless of what MinIO, a future proxy, or a future contributor
gets wrong. That is the CDN §"What this rejects" already deferred ("A CDN in slice 1"), and this
addendum is the concrete argument for not deferring the *domain* choice along with it: when a CDN
does arrive, its origin should be that cookieless domain, not a `stores.opsgrids.com` or
`opsgrids.com` subdomain, and `PUBLIC_ASSET_BASE_URL` (§6, §13) is already the one config value
that changing costs nothing else — the payoff §6 was built for, spent here.

## Addendum (2026-09-23) — §8 shipped: the purge sweep, and the orphan pass it made possible

§8's own deadline was thirty days after the document service reached the edge, and the edge
addendum above records that as the same date this one carries. This addendum is that slice:
`services/document/app/cron.py`, wired to `bin/cron` in place of the scaffold's `app/relay`,
and a second container, `document-cron`, in `infrastructure/edge/compose.prod.yml`.

**§8 is implemented as designed, with the discipline the ADR named.** Discovery — which
organizations have assets whose `deleted_at` is past `ASSET_PURGE_RETENTION_DAYS` (a setting,
default 30, not the module constant §8 implied) — runs cross-tenant on the BYPASSRLS outbox
role, reading exactly four columns a new column-level grant names
(`organization_id, id, deleted_at, purged_at`; `migrations/env.py`'s
`grant_purge_sweep_reader`, the same shape `approval`'s and `scheduler`'s own sweep readers
use). Every write — the object delete, then `purged_at` — runs in
`session_scope(organization_id=...)` on the ordinary app role, under `SELECT ... FOR UPDATE
SKIP LOCKED`, re-checking the retention window against the write transaction's own, later
clock read rather than trusting discovery's. The partial index §12 named for "slice 2, with
the cron" — `(deleted_at) WHERE deleted_at IS NOT NULL AND purged_at IS NULL` — ships in this
slice's own migration, not the table's. The row survives, as §7 requires: the sweep sets
`purged_at` and nothing here ever deletes a row.

**§9's orphan sweep is also implemented, not merely designed** — the ADR's own text said this
needed "a separate list-capable MinIO credential" and stopped there. It is built, judged safe
to automate rather than left as a dry-run-only report, for a reason worth stating plainly: the
one way an automated bucket scan could delete something it should not is racing the write path
described in this very section — an object whose `PutObject` succeeded but whose row `INSERT`
has not committed yet. That race is bounded by a single request's duration, not by anything a
human would call "a while", so a candidate has to sit unreferenced for `orphan_sweep_min_age_hours`
(default 24, the number §9 itself named) before it is even considered. Two further backstops sit
on top of that judgement, deliberately redundant with it rather than a substitute for it:
`orphan_sweep_max_deletes_per_tick` caps how much damage one bad tick can do regardless of how
wrong discovery turns out to be, and `orphan_sweep_dry_run` — on by default even after
`orphan_sweep_enabled` is set — means the very first thing a newly turned-on sweep does is log
what it *would* remove, never remove it, until an operator reads a run or two and turns the
second flag off deliberately.

**The credential §9 asked for, built exactly as narrow as it needs to be.** A second MinIO user,
created idempotently by `minio-init` alongside the existing write-only one:
`s3:ListBucket` on the bucket plus `s3:DeleteObject` on its objects, nothing else, and handed
to **`document-cron` only** — the `document` API container's own environment carries no trace
of it. `services/document/app/storage.py`'s `ObjectStore` (the write path) and `OrphanScanner`
(the sweep's own class, used only from `app/cron.py`) are two separate boto3 clients for exactly
this reason: nothing on the request path must ever be capable of `ListBucket`, because that
capability is the one thing ADR-0009 §3's whole tenancy argument depends on nobody having.

**What discovery actually checks, precisely, because "has no row" needs to survive every state
a row can be in.** The orphan pass's own read (`selectors.existing_asset_keys`, on the same
column-grant-limited bypass connection §8's sweep uses) asks whether **any** row exists for a
listed key's `(organization_id, id)` — live, soft-deleted, or already purged. A soft-deleted or
purged asset's object is never in the bucket by the time this pass would see it (§8's own sweep,
or `?purge=true`, already removed it), so the distinction is moot in practice, but the query is
written to be correct rather than merely lucky: it would still refuse to touch a hypothetical
future state where a row survives with no live object, because it asks about the row's
existence, never its `deleted_at`.

**Verified, not assumed**: the full test suite (`services/document/tests/test_cron.py`,
`test_orphan_sweep.py`) drives the sweep against real PostgreSQL — cross-tenant discovery
finding both organizations' own due work, the retention window's boundary, an already-purged
row never rediscovered, a missing object counting as success, a storage failure leaving
`purged_at` null for the next tick, two concurrent sweepers agreeing on the total, and the
discovery role failing loudly (`42501`) on any column outside its grant — and one test in
`test_storage_minio.py` runs the orphan pass against a genuine MinIO bucket with a real
`ListObjectsV2` and a real `DeleteObject`, proving the credential shape works and not only that
the application logic does.

**What is deliberately still off.** `orphan_sweep_enabled` defaults to `false` in
`services/document/app/config.py` and stays `false` in `infrastructure/edge/compose.prod.yml`
until an operator sets `DOCUMENT_S3_ORPHAN_ACCESS_KEY`/`_SECRET_KEY` and flips it, per the
runbook's own new section. §8's row-purge sweep carries no such flag and is on unconditionally
once `document-cron` is deployed — its failure mode (a soft-deleted object staying public a
little longer) is bounded and already-accepted by §7; the orphan pass's failure mode (deleting
an object a request-in-flight might still need) is the one this addendum's caution is about,
even though the 24-hour margin makes it very unlikely in practice.

---

## Decisions on open questions (2026-09-24)

This ADR named four things and built none of them. Each is decided below. One prerequisite has
since been resolved: **the gateway body cap in §5 has landed**. `DEFAULT_MAX_BODY_BYTES` is 2 MiB,
enforced before a body is read (`platform_core/gateway.py`), and `/api/v1/documents` raises its
own ceiling to 6 MiB in both route tables. The other "Revisit when" items (re-encoding in a
worker, GIF, a delete-only permission, conditional `PutObject`) stay demand triggers. The
register of every ADR question is [open-questions-register.md](open-questions-register.md).

### Q1. The private half (§2): payslips, contracts, ID scans — **DECIDED: a private bucket, the owning product authorizes, the document service presigns**

**Decision.** `S3_PRIVATE_BUCKET` has **no anonymous policy at all**. Private objects get their own
table in `document`, `private_objects` (RLS, `organization_id`, `owner_service`,
`owner_aggregate_id`, `content_type`, `size_bytes`, `checksum_sha256`), under keys
`{organization_id}/{owner_service}/{object_id}`. They are never in `assets` and never in the
public bucket.

**The product that owns the document authorizes reads, never the document service.** Payroll
decides who may read a payslip; the scope is payroll's own, for example OWN = the employee's own
payslip. The flow:
1. The product's API checks scope.
2. It writes the sensitive-read audit row. A payslip is PII, so every unmasking read is audited
   (CLAUDE.md).
3. It asks document's internal route for a presigned GET:
   `POST /internal/v1/private-objects/{id}:presign`, with a service token carrying the `org`
   claim and a per-route caller allowlist (the ADR-0008 §8 posture).

The presigned URL lives for **60 seconds** and carries
`response-content-disposition=attachment`. It is fetched through a **dedicated Caddy route on
`EDGE_HOST`** (`/opsgrids-private/*`). Unlike the public route, it **preserves the query
string**, because the SigV4 signature lives there, and preserves `Host`, because the presigner
signs for that public URL. Any added `response-*` override breaks the signature rather than being
honoured. The route sends `Cache-Control: private, no-store`,
`Content-Security-Policy: default-src 'none'; sandbox` and `nosniff`. Uploads come from the product
server-side, for example payroll rendering a PDF, over an internal route with the same posture.
There is no browser upload path to the private bucket in this decision.

**Why.** §2 named the bucket and a short-lived presigned GET. The only open question was who
decides access, and only the product knows what a payslip's scope means. Every objection in §"What
this rejects" to authorizing per request is about anonymous `<img>` fetches, and none of them
applies to a click from a signed-in page.

**Rejected.** Private objects as a `visibility` flag in the public bucket: that is §2's
prefix-policy incident with a column instead of a prefix. A generic `document.file.read`
permission: it cannot express "my own payslip". Streaming the bytes through the gateway: it
buffers whole response bodies, and nothing needs it once presigning exists.

**Consequences.** It is one table, two internal routes, one bucket, one Caddy route and one
`minio-init` user that can put and get objects in the private bucket and never list them.
**Implemented by R19** (private bucket and presigned GET), which D1's payslips depend on.

### Q2. Per-organization quota and storage metering (§7) — **DECIDED: a catalog feature and a daily usage snapshot, once storage is priced**

**Decision.** The configured ceilings stay (2 GiB and 5,000 assets) until storage is priced. When
it is, the limit becomes the catalog plan feature `document.storage_bytes` (declared through C6's
plan feature declarations). Document reads it from the organization's entitlement snapshot, and
going over it returns **402** with an upgrade offer (ADR-0004 §5). Metering then receives a
**daily** `document.storage.byte_days` usage event per organization, computed from `assets`
(counting soft-deleted assets that have not been purged, because their bytes still exist). The
history before that day is backfilled from the same table, which is possible only because the
sweep keeps rows (§7, §8).

**Rejected.** An override column per organization in document: a commercial promise belongs in a
plan version (ADR-0004 §2). Metering on every upload and delete: a daily snapshot is exact to the
day and is what a price per GB-month needs.

**Consequences.** **Implemented by R19** (per-organization quota and storage metering) together
with **C6**. The default numbers are **DECIDED (default; owner may revise)**.

### Q3. The reference warning (§8) — **DECIDED: computed in the editor from the theme document; one batch read here**

**Decision.** No reference table and no events. Only `resolveImageSrc` turns a setting into an
`<img src>` (§6), and every uploaded value is a parseable `/a/{org}/{asset}` path. So the store
editor already holds every asset reference in the theme document it has loaded (the storefront now
persists `theme_documents`). On a delete, the editor counts the sections that reference the asset.
On opening, it calls a new batch **`GET /api/v1/documents/assets?ids=…`** (at most 100 ids,
tenant-scoped, returning `deleted_at` and `purged_at`) and flags sections whose image has been
removed or purged.

**Rejected.** A storefront-side references table maintained on every save: it is derivable from the
document it would be derived from. A `referenced`/`dereferenced` event pair: already rejected in
§"What this rejects".

**Consequences.** One route here and two editor checks. **Implemented by R19** (batch
`GET /assets?ids=`).

### Q4. A cookieless asset domain (addendum 1's "longer-term plan") — **DECIDED (default; owner may revise): move off `opsgrids.com` when a CDN arrives or a second writer shares the bucket**

**Decision.** `PUBLIC_ASSET_BASE_URL` moves to a **dedicated registrable domain outside
`opsgrids.com` on which nothing ever sets a cookie**. It happens the first time either of two
things occurs:
- a CDN is put in front of the public bucket;
- a second service writes to the public bucket.

The owner registers and names the domain; the name is a commercial choice and is not made here.
The move is a configuration change and one Caddy site block, with **no data migration**, which is
§6's payoff. Until then, assets stay on `EDGE_HOST` behind addendum 1's hardening: host-only
route, query strip, CSP sandbox, `nosniff`.

**Why.** Addendum 1 found that a public bucket served under the platform's cookie domain is one
MinIO feature away from stored XSS. The durable fix is a host with nothing to steal, and it costs
nothing to decide now.

**Consequences.** **Owner action**: register the domain when the trigger fires. No ledger item is
needed until then.
