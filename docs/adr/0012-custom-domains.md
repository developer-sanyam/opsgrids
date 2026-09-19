# ADR-0012 — Custom domains: putting a merchant's store on a URL they own, and the smallest hosting that can serve one

**Status: Proposed 2026-09-19. Not implemented.** Creates `infrastructure/` — which `CLAUDE.md`
describes and this repository does not have — containing one Docker Compose file and one Caddyfile
for **one Linux VM**. Adds one table, one public route, four merchant routes, one internal route,
one `bin/cron` sweep, one Vite entry point and one Python dependency (`dnspython`). Adds **no**
permission, **no** event, **no** index on `storefronts`, and **no** grant to any bypass role.

Builds on [ADR-0000](0000-phase-0-decisions.md) §10 (shared-schema tenancy, RLS, no cross-schema
foreign keys) and "Also settled" (audit inside the transaction); on
[ADR-0001](0001-frontend-topology.md) (each app is static output behind its own domain; the gateway
does not serve frontends; the refresh cookie is scoped to `.opsgrids.com`) — and **extends its
routing table with a class of host it does not contemplate**, see §2; on
[ADR-0002](0002-row-level-security.md) §2 (a bypass buys **discovery only**) and §4 (the policy is
quiet, so the application must be loud); on [ADR-0005](0005-dunning-and-grace.md) §6 (a due-work
table drained by a sweep); on [ADR-0009](0009-uploaded-images-and-object-storage.md) §6 (resolve the
host at render time, store a root-relative path); on
[ADR-0010](0010-delivery-serviceability-and-slots.md) §4 (the bypass-reader chokepoint, the
exact-match public path, the 300-second slug cache) and §13 ("unknown is not no"); and on
**ADR-0011** (store identity and brand), which is being written concurrently and whose
`storefronts.custom_domain` / `custom_domain_status` columns this ADR is the other half of.

**Supersedes nothing. Amends two things, both explicitly:**

1. **ADR-0010 §4's "never the Host header"** — narrowed, not overturned. §2 below.
2. **ADR-0011's `CUSTOM_DOMAIN_STATUSES = ("unverified", "verified")`** — widened to five values.
   ADR-0011's own column comment predicts this ("the hosting slice that adds DNS verification is
   then a validation change and a new endpoint, not a migration against a table with live tenant
   rows"), and §6 below is that change. There are zero live rows, so it is a check-constraint
   edit and nothing else.

**One coordination point with ADR-0011 is named rather than assumed**: §11's
`POST /public/store-context` is the **only** public representation of ADR-0011's brand columns, and
the storefront-document ADR must extend that payload rather than add a second one. If ADR-0011
lands its own public route, this ADR's §11 is what changes.

---

## Context

A merchant must be able to point their own domain at their storefront and have it go live. Nothing
in this repository is deployed, and serving a domain requires a deployment. That was said plainly to
the user and the user chose it anyway, so the minimum hosting required to serve one domain is in
scope here and is not re-opened below.

### What is true today, verified rather than assumed

| Fact | Evidence |
|---|---|
| **There is no `infrastructure/` directory at all**, although `CLAUDE.md` lists it as holding Helm charts and kustomize overlays | `ls` at the repository root |
| `.github/workflows/` contains **only `ci.yml`**. There is no deploy job anywhere | `ls .github/workflows/` |
| **No ingress, no TLS termination, no host-based routing** exists in the repository | `grep` across `services/`, `libs/`, `tools/` |
| 35 services have Dockerfiles; `tools/dev-bootstrap/compose.yml` brings up Postgres+PostGIS, Redis, RabbitMQ, MinIO and MailHog. **Services are containerised; nothing is deployed** | `find . -name Dockerfile`; `tools/dev-bootstrap/compose.yml` |
| Each service image is single-service by construction — `import app` is ambiguous across services, so one image holds one service's code on top of a shared `.venv` layer | `services/storefront/Dockerfile` |
| **`storefronts.custom_domain` is now merchant-writable**, `String(255)`, globally unique when non-null (`uq_storefronts_custom_domain`, partial), shape-checked in the database, and **read by nothing** | ADR-0011, `services/storefront/app/models.py` |
| **`storefronts.custom_domain_status`** is `unverified \| verified`, null iff `custom_domain` is null, and **only `unverified` is reachable**; `verified` is reserved for "the hosting slice" | same |
| ADR-0010 §10's register put `UNIQUE (custom_domain) WHERE NOT NULL` in slice 2. **ADR-0011 already landed it.** This ADR adds no index to `storefronts` | `models.py` `__table_args__` |
| The store is named in the **request body**, deliberately. `_slug_from_host` is a subdomain heuristic: `None` for a two-label host, `shop` for `shop.merchant.com` | `gateway.py:422-432`; ADR-0010 §4 |
| **`GatewayPolicy.public_paths` is an exact-path frozenset** and `full_path` excludes the query string, so a public endpoint's variables must travel in the query or the body, never the path | `gateway.py:312`, `:334` |
| On the public branch the gateway forwards `x-org-slug` only (and only from a 3+-label host), sends **no `x-org-id`**, and **skips the entitlement check entirely** | `gateway.py:334-341` |
| `enforce_rate_limit` **fails closed**; the anonymous limit is **60/min per client IP** | `cache.py:90-104`; `gateway.py:218` |
| The anonymous tenant-resolution problem is already solved once: a BYPASSRLS **SELECT-only** role (`opsgrids_storefront_directory`) resolves slug → org on **one table**, and every later statement runs tenant-bound | `selectors.resolve_public_store`; ADR-0010 §4 |
| **`get_directory_session` silently falls back to an ordinary session when its role is unset**, which is why storefront asserts at startup and refuses to boot | `db.py:334-337`; `services/storefront/app/config.py` |
| The directory engine pool is **five connections** (`pool_size=3, max_overflow=2`) | `db.py:302` |
| The storefront SPA is a **static Vite build** that learns its store from a module-level variable and `VITE_USE_FIXTURES`; `/live/*` is behind `RequireAuth` deliberately | `frontend/product-storefront/src/api/client.ts`, `src/main.tsx:95-104` |
| The Vite dev server already proxies `/api` to the gateway so the browser makes **same-origin** calls and CORS never applies | `frontend/product-storefront/vite.config.ts` |
| `cors_origins` defaults to a single localhost origin and is a comma-separated list | `platform_core/config.py:155` |
| The refresh cookie is scoped to `.opsgrids.com`; the access token lives in memory | ADR-0001 |
| `services/storefront/bin/cron` runs `python -m app.relay` — the scaffold default. A service with real scheduled work points it at `app.cron` instead | `services/storefront/bin/cron`; `services/subscription/bin/cron` |
| `make secrets` scans **`git ls-files`** against `.secrets.baseline` | `Makefile:305-308` |
| Problem+json bodies carry `type`, `title`, `status`, `detail`, `instance`, `request_id`, and optionally `errors`. `PROBLEM_BASE` is `https://errors.opsgrids.dev` | `platform_core/errors.py:20`, `:43-54` |

### The problem, stated once

**A stranger's browser resolves a name the platform does not own, negotiates TLS for it with a
certificate the platform must have obtained without a human, and must then be shown exactly one
merchant's store — and never anyone else's, and never one that used to be there.**

Everything below is downstream of that sentence. The three hard parts are, in order of how badly
they fail: who is allowed to have a certificate for a name (§3, §5), what happens to a name after
the merchant stops owning it or stops paying (§7), and where any of this runs at all (§1).

---

## Three framings I am rejecting before designing against them

**1. "The SPA's answer is harder because it is cached bytes."** It is not, and treating it as a
caching problem is how it becomes one. The bundle should be **byte-identical for every merchant**
and should learn its host from `window.location.hostname`, which is free, correct, and available
before the first request. Cached bytes are only a problem if the store is baked into the build —
which would mean a build and a deploy per merchant, and a rebuild of all of them on every code
change. The real cost is one round trip before first paint, which is a latency problem with a
latency answer (§2). `index.html` is served `no-cache`; `/assets/*` is fingerprinted and immutable.

**2. "TXT or CNAME."** A false choice — the design needs **both records, doing different jobs**, and
conflating them is precisely what produces subdomain takeover. A TXT record proves *ownership*; an
A or CNAME record carries *traffic*. A design that treats "your DNS points at us" as authorization
hands the domain to whoever inherits a stale record (§7). **Pointing DNS at us is routing. It is
never authorization.** That sentence is the load-bearing one in this ADR.

**3. ADR-0010 §4's "never the Host header".** Narrowed. The objection there is to
`_slug_from_host` — a subdomain heuristic living in the gateway, which returns `None` for
`merchant.com` and `shop` for `shop.merchant.com`. That objection stands and this ADR does not use
that function. But the stronger reading — "the host is client-supplied and therefore untrustworthy"
— does not survive contact with this feature, and more importantly **does not need to**: in this
design the host is only a *lookup key into published data*. The security boundary is the state check
at the origin (§7), not the provenance of the header. A visitor who lies about the host gets shown a
different published store, which they could have reached by typing its slug. Nothing is protected by
the host being honest, so nothing is lost by it not being.

---

## Decision

### 1. Where this deploys: one Linux VM, Docker Compose, and Caddy. Not Kubernetes

**The target, concretely.** One x86 Linux VM — Hetzner CX32 (4 vCPU / 8 GB / 80 GB, ≈ €7.60/month)
or a DigitalOcean 4 GB droplet (≈ $24/month) — running Ubuntu 24.04 LTS, Docker Engine and Compose
v2, with a **reserved (floating) IPv4 address attached**. Everything runs on that one box:
Caddy, Postgres+PostGIS, Redis, and five service containers — `auth`, `account`, `product`,
`app-gateway`, `storefront`. The other thirty services are not deployed, and that is the point.

Memory arithmetic, because "will it fit" is the first question: Postgres 1 GB, Redis 128 MB, five
uvicorn processes at ~250 MB each ≈ 1.25 GB, Caddy 64 MB. **About 2.5 GB of 8 GB.** There is room
for the rest of the platform later, or for nothing.

**`infrastructure/edge/` is created**, containing `compose.prod.yml`, `Caddyfile`, `.env.example`
and `RUNBOOK.md`. **This contradicts a line in `CLAUDE.md`** — which says `infrastructure/` holds
Helm charts and kustomize overlays — and the contradiction is deliberate and needs a human to accept
it: there is no Kubernetes cluster, there is no one to run one, and proposing a control plane for a
product with no users is exactly what ADR discipline exists to prevent. `CLAUDE.md`'s line should be
amended to describe what is there.

**Why one VM and not a managed platform (Render, Fly, Railway).** Each of those solves custom
domains for you and would be a legitimate choice. They are rejected for one reason: **the platform
is 35 services, one Postgres cluster with a schema per service, and RabbitMQ.** A managed platform
prices and shapes itself around one service per app; thirty-five of them is either thirty-five
billable services or a single container running one service, which is what we would end up doing
anyway. A VM is the shape the repository already has. **What that gives up:** every operational
concern — patching, disk, restarts, backups — becomes ours, and the answer to "why is the site down"
starts with `ssh`.

**Why not Kubernetes.** A managed control plane is $70/month before a single node, cert-manager plus
an ingress controller is two more components with their own failure modes, and the per-merchant
certificate story on ingress-nginx is either one `Ingress` object per domain (a Kubernetes API write
in the middle of a merchant's onboarding) or a wildcard we cannot have. **What that gives up:**
horizontal scale, rolling deploys and self-healing. Named as slice 4's trigger, not pre-built.

**Why Caddy and not nginx + certbot.** Caddy's `on_demand_tls` with an `ask` endpoint is the exact
shape of this problem: a certificate is obtained at handshake time for a name we were asked about,
and our own HTTP endpoint is the gate. nginx + certbot needs a certificate to exist *before* the
first request, which means a config write and a reload per merchant — a deploy step inside a
merchant's onboarding flow, and a reload that can fail with half a config. **What that gives up:**
nginx is more widely understood and has more operational literature. Caddy's automatic HTTPS is the
one thing it is famous for, and it is the one thing we need, so the trade is worth it. Caddy also
falls back from Let's Encrypt to ZeroSSL automatically, which gives two certificate authorities for
no configuration and no cost.

**Deploy, in slice 1, is `make deploy`**: an SSH invocation that runs `git pull && docker compose
-f infrastructure/edge/compose.prod.yml up -d --build` on the box, from an engineer's laptop with
their own key. **No image registry, no CI deploy job, no GitHub secret.** Building on the box costs
five to ten minutes of CPU that briefly competes with serving; at 50 merchants that is acceptable
and it removes an entire class of credential from the repository. The registry and the CI job are
slice 2, with "a deploy has degraded serving" as the trigger.

**The reserved IP is the most important line in this section.** The apex-domain answer (§4) is an A
record to a literal address, so **that address becomes part of the platform's public contract**.
A floating IP costs a few dollars a month and means replacing, resizing or rebuilding the VM never
requires fifty merchants to edit DNS. Skipping it is the cheapest mistake in this design to make and
the most expensive one to unmake.

**DNS for the platform zone.** Three records, set once by hand:

| Name | Type | Value | Why |
|---|---|---|---|
| `edge.opsgrids.com` | A | the reserved IP | What subdomain merchants CNAME to |
| `*.stores.opsgrids.com` | A | the reserved IP | Every platform-hosted store, one record |
| `opsgrids.com`, `app.`, `api.`, `auth.` | — | unchanged, ADR-0001 | Not this ADR's |

**`*.stores.opsgrids.com`, not `*.opsgrids.com`.** ADR-0001 allocates `crm.opsgrids.com`,
`hr.opsgrids.com` and friends to product applications. A merchant whose slug is `crm` would
otherwise take a product's hostname. A separate label removes the collision entirely for one DNS
record, and it means the platform-hosted name for a store is mechanically `<slug>.stores.opsgrids.com`
with no reserved-word list to maintain.

**Backups.** Nightly `pg_dump` to an **off-box** object store, plus the provider's VM snapshot. A
backup on the same disk as the database is not a backup, and MinIO on the same VM is the same disk.

**Honest statement of what this costs the moment it works:** once one real merchant's real domain
points at that IP, the VM is production. It cannot be rebooted for convenience, and a `docker
compose up --build` that fails leaves a merchant's shop down. There is no second box.

### 2. How a request for `merchant.com` reaches the right store

```
browser ──► merchant.com  (A → reserved IP)
             │
             ▼
           Caddy on the edge VM
             │  TLS: on-demand issuance, gated by §5's ask endpoint
             │
             ├── /api/*   ──► reverse_proxy app-gateway:8100   (Host preserved)
             └── everything else ──► the buyer bundle on disk, SPA fallback to index.html
```

**Both the page and its API calls are same-origin.** The browser on `merchant.com` calls
`merchant.com/api/v1/storefront/...`, which Caddy proxies to the gateway on the internal network.
This is the same arrangement `vite.config.ts` already uses in development, and it is chosen for one
concrete reason: **the alternative makes `cors_origins` grow a row per merchant domain.** That list
is process configuration read at boot, so every new custom domain would be a config change and a
restart of every service — a restart in the middle of a merchant's onboarding. The wildcard escape
hatch does not exist either: `Access-Control-Allow-Origin: *` is illegal with credentials and would
still be wrong. **Same-origin means custom domains add no CORS surface at all.**

This is an **extension of ADR-0001's routing table**, which says every application talks to
`api.opsgrids.com`. That remains true for every *authenticated product application*. The buyer
storefront is a new surface class — anonymous, on a host the platform does not own — and it is the
one surface for which "call the shared API host" is the wrong answer. Recorded here so it is a
decision and not drift.

**Nothing on a custom domain is authenticated, and that is enforced by physics.** ADR-0001 scopes
the refresh cookie to `.opsgrids.com`, so it is simply not sent to `merchant.com`. A merchant cannot
sign in to their admin on their own domain, and a buyer has no identity to carry. This is the
correct outcome — a merchant's session must never travel to a host they can put arbitrary
JavaScript next to — and it has a consequence slice 1 must pay for: **ADR-0010 §15 leaves `/live/*`
behind `RequireAuth`, and a `RequireAuth` on a custom domain is a redirect to a sign-in page that
cannot help.**

So slice 1 builds **a second Vite entry point**: `frontend/product-storefront/buyer.html` →
`main.buyer.tsx` → `dist-buyer/`, containing the theme renderer, ADR-0010's serviceability module
and the bootstrap below, and **no auth code at all**. The edge serves only that bundle. The merchant
admin stays where it is. **Why two entries rather than a runtime conditional:** shipping the auth
client, the org switcher and the admin routes to every anonymous shopper is dead weight on the
first-paint path and an XSS surface with nothing to gain. **What it gives up:** two build outputs to
keep from drifting, mitigated by both importing the same theme package and by the buyer entry being
a strict subset.

**How the bundle learns its store — one call, no baked config.**

```
POST /api/v1/storefront/public/store-context
{ "host": "merchant.com" }
```

The host comes from `window.location.hostname`. The bundle is identical for every merchant. The
response is cached in `sessionStorage` for the tab, so it is **one call per session, not per page
view** — which matters, because the anonymous rate limit is 60/min per client IP and a shared NAT
(an office, a mall's wifi) is a single IP.

**ADR-0010's wire format does not change.** `store-context` returns the slug, and every subsequent
call — including `POST /public/serviceability`, whose body is `{"store": ..., "postal_code": ...}`
with `extra="forbid"` — is made exactly as it is today. This is the main reason the host is resolved
in a bootstrap call rather than by adding a `host` field to every public endpoint.

**Host resolution — the chokepoint, and it needs no new grant:**

```
resolve_public_store_by_host(host) ──► PublicStore | None      # BYPASSRLS, SELECT-only
        │
        └──► session_scope(organization_id=…) ──► everything else
```

```
normalise(host)            # lower-case, strip port, strip trailing dot, U-label → A-label (punycode)
if host endswith ".stores.opsgrids.com":
    return resolve_public_store(host[: -len(suffix)])          # ADR-0010's existing function
partner = "www." + host  if not host.startswith("www.")  else host[4:]
SELECT id, organization_id, slug, status, currency, timezone, name, tagline, logo_text,
       custom_domain, custom_domain_status
  FROM storefront.storefronts
 WHERE custom_domain IN (:host, :partner)
   AND custom_domain_status IN ('verified', 'live', 'failing')
   AND deleted_at IS NULL
```

**Both branches read `storefront.storefronts` and nothing else.** ADR-0010 §4's chokepoint sentence
— *"this function is the entire surface of `opsgrids_storefront_directory`; the role holds SELECT on
`storefront.storefronts` and on nothing else"* — survives ADR-0012 intact. That is a direct
consequence of ADR-0011 putting the domain on `storefronts` rather than in a side table, and it is
worth naming as the single best thing that decision bought.

**Cached in Redis for 300 s under `storefront:host:{host}`**, negatives included, exactly as
ADR-0010 caches `storefront:slug:{slug}` and for the same reason: it crosses an engine boundary and
changes approximately never, and the directory pool is five connections on the path of every
anonymous request. **Every state transition in §6 deletes the host key and the slug key in the same
transaction that writes the state.** Without that, removing a domain leaves it serving for five
minutes.

**The rate-limit cost of the second call, stated:** two anonymous requests per session instead of
one. At ADR-0010 §12's planning numbers (~1,000 sessions/day/merchant, 50 merchants) that is
+50,000 requests/day fleet-wide, ~0.6/s. Nothing. The number to watch is the per-IP limit under NAT,
not the fleet total.

**The `www` pair is served as a unit.** If the claim is `merchant.com`, `www.merchant.com` resolves
to the same store and 308s to it; if the claim is `www.merchant.com`, the apex does. The rule is
purely syntactic — strip or prepend `www.` — with **no Public Suffix List**, because a PSL is a
bundled list that goes stale and a dependency that updates on someone else's schedule. The residual:
verification proved control of the claimed name, not literally of its `www` sibling, and the two are
under the same zone operator in every case that occurs in practice. Named, small, accepted.

**A second gateway public path.** `STOREFRONT_PUBLIC_PATHS` in `services/app-gateway/app/main.py`
becomes two entries. Still in the gateway *service*, still not in `platform_core` — ADR-0010 §4's
reason is unchanged.

### 3. Verification: a TXT record with a per-claim token, checked before anything is served

```
_opsgrids-challenge.merchant.com.   TXT   "opsgrids-domain-verification=<32-hex-token>"
```

The token is `secrets.token_hex(16)` — 32 hex characters, 128 bits — minted per claim and stored on
`custom_domain_claims.verification_token`.

**Why TXT and not "check that the CNAME points at us".** Three reasons, and the third is the one
that decides it:

1. **A CNAME is illegal at a zone apex** (§4), so a traffic-record proof cannot work for the case
   most merchants want. The alternative proof at apex is an A record to our IP — and our IP is
   *shared*, so "an A record points at the platform" proves the merchant can reach us, not that this
   merchant owns the name.
2. **It lets a merchant verify before cutting over.** Adding a TXT record to a live domain changes
   nothing about the live site. Repointing an A record takes the merchant's existing site down the
   moment they do it. Verification-then-cutover is the only ordering that does not require a
   merchant to accept downtime on a guess, and it is why §6 has a distinct `verified` state.
3. **It is what keeps a dangling record from becoming a takeover** (§7). A stale CNAME pointing at
   `edge.opsgrids.com` is something anyone can inherit; a TXT record under the merchant's own zone
   is not.

**What stops one merchant claiming another's domain:**

- **DNS control is the proof.** Only a party who can write records in `merchant.com` can place the
  challenge. There is no second path — no email approval, no file upload, no support override that
  skips it.
- **ADR-0011's `uq_storefronts_custom_domain` makes the claim exclusive platform-wide.** A second
  organization naming the same domain gets a 409 `custom_domain_taken` at write time, not a second
  ambiguous row. This ADR needs no `blocked` state because ADR-0011 already made the collision
  impossible to represent.
- **The writer also refuses a claim whose `www` partner is claimed by a different organization**,
  with the same 409. Without this, two organizations could hold `merchant.com` and
  `www.merchant.com` and §2's `IN (:host, :partner)` would return two rows for either. The resolver
  additionally prefers the exact match and logs `storefront.domain.ambiguous_host` at error if it
  ever sees two — a condition the constraint makes unreachable, logged because unreachable
  conditions are how one finds out the constraint was dropped.

**An unverified claim expires after 14 days, and expiry releases it.** ADR-0011's unique index means
an unverified claim on `bigbrand.com` blocks it for everyone forever; that is a squatting vector
ADR-0011 could not have seen, because nothing was served. The sweep clears `custom_domain` and
`custom_domain_status` back to null on an expired claim and writes an audit row. The merchant sees
"we never saw the DNS record, so the claim has been released — you can start again", and starting
again mints a **new** token. **Fourteen days rather than twenty-four hours** because a merchant's
DNS is very often in the hands of a web agency who answers next week.

**The TXT record must stay in place for the life of the domain**, and is re-checked daily. This is
the design's answer to "the merchant sold the domain" and its early warning for renewal failure
(§5). It is also the most likely source of a false alarm, so §6 requires **seven consecutive daily
failures** before a live domain is taken off the air.

**The token is not a credential.** It grants no access to anything; it only proves whoever published
it controls that zone. It is returned to the owning organization over an authenticated,
tenant-bound, permission-gated route and is visible to `STOREFRONT_STAFF`. It must still never be
logged (§14).

### 4. The apex problem: an A record to a documented address, and an honest recommendation not to

A CNAME cannot coexist with the SOA and NS records at a zone apex (RFC 1034 §3.6.2), so
`merchant.com` cannot CNAME to `edge.opsgrids.com`. Most merchants will want the apex anyway.

**What a merchant with an apex domain actually does, in order of what we tell them:**

1. **If their DNS provider offers ALIAS / ANAME / CNAME-flattening** (Cloudflare, Route 53,
   DNSimple, and increasingly others): an ALIAS at the apex to `edge.opsgrids.com`. This is the
   right answer and it insulates them from a platform IP change. It is offered **first**, and the
   response body names it (§11).
2. **Otherwise: an A record at the apex to the reserved IP**, printed as a literal in the merchant's
   UI, plus a second A record for `www`. This works everywhere and is what most merchants will do.
3. **We recommend `www.merchant.com` or `shop.merchant.com` as the claimed name**, with the apex as
   the redirecting partner, because a subdomain can CNAME and therefore never pins our address.

**The cost of option 2, stated plainly because it is permanent:** an apex merchant has our IP
address in their zone file. Changing that address means asking every one of them to edit DNS, and
some of them will not, and their shop will go down. The reserved IP (§1) is what makes that cost
theoretical instead of scheduled. **Subdomain merchants pay nothing for an IP change; apex merchants
pay everything.** That asymmetry is why the UI recommends a subdomain and does not merely permit one.

**The Cloudflare trap, named because it will happen.** A merchant who proxies the record through
Cloudflare (orange cloud) terminates TLS at Cloudflare with Cloudflare's certificate and originates
to us over 443 — where we have no certificate for that name and cannot get one, because the ACME
HTTP-01 challenge on port 80 never reaches us. The symptom is an error page that says nothing useful
and a support ticket that takes an afternoon. **The instructions must say "DNS only / grey cloud"
in the record table itself, not in a help article**, and §6's routing check should detect a
Cloudflare-owned address and say so by name rather than "your DNS is wrong".

**IPv6.** `edge.opsgrids.com` and `*.stores.opsgrids.com` publish AAAA records. Merchants are asked
for A only — one more record to explain buys nothing while every eyeball network has IPv4.

### 5. Certificates: ACME HTTP-01, on demand, gated by one endpoint. The platform holds no key material

**HTTP-01, not DNS-01, and the reason is that we do not have the merchant's DNS credentials and must
never ask for them.** DNS-01 for a customer domain requires either their API token — an
unconditional no — or that they CNAME `_acme-challenge.merchant.com` to a zone we control, which is
a second record to explain, a second thing to get wrong, and a second thing that can lapse. HTTP-01
requires only that the name resolves to us on port 80, which is the exact state the merchant has
already created by following §4.

**We control `opsgrids.com`, so we could use DNS-01 for `*.stores.opsgrids.com` — and deliberately
do not.** HTTP-01 handles per-store subdomains on demand just as well, and choosing it everywhere
means **the platform holds no DNS provider credential at all**. Removing an entire class of secret
is worth more than the wildcard certificate it would have bought. **What it gives up:** a wildcard
would survive a total loss of certificate storage as one re-issue instead of N; §12 prices that.

**Issuance is on demand, and one endpoint decides:**

```
GET http://storefront:8000/api/v1/storefront/internal/tls-authorize?domain=merchant.com

HTTP/1.1 200 OK            Caddy may obtain a certificate for this name
Content-Length: 0

HTTP/1.1 403 Forbidden     Caddy must not
Content-Length: 0
```

Caddy treats any non-2xx as deny, so **an exception here is a 500 and a 500 is a deny.** That is the
correct direction: a broken authorizer stops *new* issuance and does nothing to certificates already
in storage, so **an outage of the storefront service cannot take a live merchant's TLS down.**

The endpoint answers `resolve_public_store_by_host(domain) is not None` — the same function, the
same Redis cache, the same bypass reader. **Two concrete callers justify that function: this and
§2's bootstrap.** It is not a third abstraction.

**It is reached on the container network, not through the gateway.** Caddy calls the storefront
container directly. It is deliberately *not* added to `public_paths`, for two reasons: the gateway's
anonymous limiter fails closed, so a Redis outage would otherwise stop all certificate issuance; and
the public-path budget is a list a human reads. `routes.json`'s existing
`{"prefix": "/api/v1/storefront", "product": "storefront"}` entry does match this path, so it *is*
reachable from the internet — behind a bearer token and an entitlement check, which makes it a
401 to everyone who matters. The residual disclosure if it were ever opened is nil: **every
certificate we issue is published in Certificate Transparency logs**, so the set of hostnames we
serve is already public by construction.

**Where key material lives.** In Caddy's storage — the ACME account key, every certificate, every
private key, every OCSP staple — on a named Docker volume (`caddy_data`, mode 0700, owned by the
Caddy user) on the edge VM. **Not in the repository, not in the image, not in Postgres, not in an
environment variable, and not in any backup the application can read.** The architectural decision
is *to have no key-management story*: nothing in `services/` ever sees a private key, so there is no
rotation procedure, no envelope encryption, and no code path to get wrong.

The volume is included in the VM snapshot. **Losing it is survivable and expensive:** Caddy re-issues
on the next handshake for each name, which means every merchant's first request after the loss is
slow and the whole fleet re-issues at once. That is the rate-limit event (§12).

**Renewal** is Caddy's, at roughly two-thirds of the certificate's lifetime — about 30 days before
expiry for a 90-day certificate — over HTTP-01 again, with no human involved. Renewal fails when the
merchant has moved their DNS away, and **§6's daily routing check detects that about thirty days
before the browser does.** That is the whole value of keeping the checks running after a domain goes
live.

**What happens when issuance fails at 3am.** The honest answer, in three parts:

1. **For a name that already has a certificate: nothing.** Caddy serves from storage and retries
   renewal on its own schedule for thirty days. This is the common case and it is genuinely fine.
2. **For a name that does not: the buyer gets a browser TLS error**, and this is the one failure in
   the design we cannot dress up — there is no HTTP response to put a friendly page in. This is the
   defining weakness of on-demand TLS and it is why §6 promotes a domain to `live` only after we
   have *observed* a working handshake, and why the merchant's UI must never say "live" on the
   strength of DNS alone.
3. **Nobody is paged.** Slice 1 has no alerting. The compensating controls are the daily probe
   writing a real reason onto the row (§6) and the merchant's screen showing it. **This is stated as
   a gap, not as a mitigation.** The pager is slice 2 and its first alert is "a certificate is
   within 14 days of expiry and the last renewal failed".

**Abuse.** Without a gate, anyone who points ten thousand hostnames at our IP burns our ACME account
against Let's Encrypt's failed-validation limits. The `ask` endpoint is that gate and it is the
reason on-demand TLS is safe to enable at all. Caddy's older `interval`/`burst` on-demand throttles
are not relied on; **`ask` is the only control, and it must be verified against the Caddy version
pinned in `compose.prod.yml`** (§17).

### 6. The state machine a merchant sees, and the failure states specifically

**The governing rule of this section, and the analogue of ADR-0010 §13's "unknown is not no":**

> **A check that cannot run never takes a domain off the air.** The sweep is a *detector*. Serving
> depends on the stored state and on Caddy's stored certificates, never on a probe having succeeded
> recently. A cron container that is down, a DNS resolver that is unreachable, a sweep that has not
> run since Friday — none of these may make a merchant's shop stop working. The worst a broken
> detector may do is fail to promote a new domain and fail to notice a broken one.

**`CUSTOM_DOMAIN_STATUSES` widens from two values to five.** ADR-0011's `unverified` and `verified`
keep their exact meanings.

| State | What is true | Certificate authorized (`ask` → 200) | Served by the origin |
|---|---|---|---|
| `unverified` | Claimed. The TXT record has never been seen | no | no |
| `verified` | TXT seen. Traffic may or may not point here yet | **yes** | yes |
| `live` | TXT seen, traffic resolves here, and a TLS handshake for this name succeeded | yes | yes |
| `failing` | Was `verified`/`live`; at least one check has failed recently. **Still served** | yes | yes |
| `suspended` | Seven consecutive days failing, or the store is not `published` | **no** | **no** — 410 |

No claim at all is `custom_domain IS NULL AND custom_domain_status IS NULL`, which ADR-0011 already
enforces with a check constraint. An expired claim (§3) returns to that, rather than becoming a
sixth state — **an expired claim is not a state, it is the absence of one.**

**`verified` authorizes the certificate; a successful handshake promotes to `live`.** This ordering
is forced — we cannot observe a certificate before we permit one — and it is also the right merchant
experience: "we have confirmed you own this" and "your shop is answering on it" are different
sentences and a merchant needs both.

**Three probes, each answering one merchant-visible question.** They are separate because a single
"is it working" boolean is exactly the pending-forever failure the user named.

| Probe | What it does | What it proves | On failure |
|---|---|---|---|
| **Ownership** | TXT lookup at `_opsgrids-challenge.<host>` against two public resolvers, 5 s each | The merchant controls the zone | `txt_not_found` / `txt_mismatch`, with what was actually returned |
| **Routing** | A/AAAA/CNAME lookup for `<host>`, compared with the reserved IP and `edge.opsgrids.com` | Traffic will reach us | `points_elsewhere` (naming the address found and, when recognised, its operator), `no_record`, `proxied` |
| **Certificate** | A TLS handshake to the **Caddy container on the internal network with SNI set to `<host>`** | We can serve that name; records `not_after` | `handshake_failed`, `expiring_soon` |

The certificate probe deliberately connects to our own edge rather than out through the internet:
it removes any dependence on NAT hairpinning, it tests the thing we control separately from the
thing the merchant controls, and **it doubles as the warm-up** — the first handshake for a newly
verified name is what triggers on-demand issuance, so the merchant's first real visitor gets a warm
certificate instead of a two-to-five-second wait.

**Every failure shows what we looked for and what we found.** This is the entire answer to
"'Pending' that never resolves with no reason shown":

```
Ownership check failed 3 minutes ago. Next check at 11:12.
  We looked for a TXT record at: _opsgrids-challenge.merchant.com
  We expected the value:         opsgrids-domain-verification=<32-hex-token>
  What we found:                 v=spf1 include:_spf.google.com ~all
```

The row carries `last_error_code`, `last_error_detail` (truncated to 500 characters), `last_checked_at`
and **`next_check_at`, which is shown to the merchant** — so "pending" always has a clock on it.

**Cadence, and a "Check now" button.** Backoff while unverified: every 60 s for fifteen minutes, then
every 5 minutes for an hour, then every 30 minutes, then hourly, until the claim expires at 14 days.
Once `verified` or `live`: **daily**. `POST .../domain/check` runs all three probes synchronously and
is rate-limited to **one call per 30 seconds per organization** — the merchant has just pasted a
record and wants an answer now, and that button is the single highest-value piece of this feature's
UX for ten lines of code.

**Failure tolerance before a live domain goes dark: seven consecutive daily failures.** Generous on
purpose. DNS providers have outages, resolvers return SERVFAIL, and taking a merchant's shop down
because a probe was wrong is strictly worse than serving a domain whose TXT record vanished
yesterday. `failing` is visible to the merchant from the first failure and warns them with a date.

**The sweep is ADR-0005 §6's due-work pattern**, and `bin/cron` must be repointed from
`python -m app.relay` to `python -m app.cron` — the scaffold default that
`services/subscription/bin/cron` already documents having had to change. **Getting this wrong means
no domain is ever checked and nothing errors**, which is the same silent-nothing failure shape as
ADR-0010's directory role.

**The sweep discovers organizations, not domains, and that is what keeps the bypass role narrow:**

```
# on opsgrids_storefront_directory — SELECT, one table, unchanged
SELECT DISTINCT organization_id FROM storefront.storefronts WHERE custom_domain IS NOT NULL

# then, per organization, on the ordinary app role
async with session_scope(organization_id=org):   # ≤ 1 claim row per tenant
    ...probe, write, audit...
```

At ≤50 merchants this is ≤50 small transactions per tick and needs **no new grant on any bypass
role**. It does not scale to ten thousand tenants; the trigger and the fix (a grant, or its own
role) are in "Revisit when". The loop wakes every 60 s and processes at most 20 due domains per
tick, so a slow resolver cannot make the tick unbounded.

### 7. Removal, lapse, and why a dangling record cannot become a takeover

**The vector, stated concretely.** Merchant A points `shop.a.com` at `edge.opsgrids.com` and later
abandons the record or leaves the platform. If the platform served whatever arrived on that name to
whoever asked for it, merchant B could claim `shop.a.com`, receive A's traffic, read cookies scoped
to `.a.com`, and obtain a publicly trusted certificate for A's domain. That is subdomain takeover and
it is the reason this feature cannot be a text box.

**Two independent gates, and the second is the security boundary:**

1. **Issuance** — `ask` (§5). Prevents a stranger obtaining a certificate on our address and
   prevents ACME abuse. Consulted once per new name.
2. **Origin resolution** — `resolve_public_store_by_host` (§2), on **every request, forever.** A
   host that resolves to no store, or to a store in `suspended`, is answered by the origin with
   **410 Gone** and a plain page: *"This store is no longer available at this address."*

Gate 2 is what makes takeover impossible: **content served on a dangling name is always ours, never
another merchant's.** And a would-be claimant still has to publish a TXT record in a zone they do
not control (§3), so the dangling record buys them nothing to begin with.

**Choosing a 410 over a TLS error is deliberate.** Letting the certificate lapse would produce
`ERR_SSL_*`, which is the worst page on the internet to show a merchant's former customers, and it
would leave the takeover question answered only by gate 1. Serving our own 410 is better UX and a
stronger boundary at the same time.

**What each of the four events does:**

| Event | What happens |
|---|---|
| **Merchant deletes the domain** | `custom_domain` and `custom_domain_status` → null in one transaction; the claim row is deleted; the Redis host and slug keys are deleted; audit `storefront.domain.released`. From the next request the host 410s. The certificate stays in Caddy's storage |
| **Merchant points DNS away** | Routing probe fails → `failing`, visible immediately with the address found. Seven days later → `suspended`, `ask` starts refusing, renewal stops, the certificate expires within 90 days. Buyers get whatever the merchant's new DNS points at, which is what the merchant asked for |
| **Merchant removes the TXT record** | Identical path, via the ownership probe. This is the "the domain was sold" case |
| **Merchant stops paying** | Covered below — and it is the weakest part of this ADR |

**Certificate cleanup on removal is deliberately not done in slice 1.** The certificate remains in
Caddy's storage until it expires, which is harmless because the origin refuses and because the
hostname is already public in Certificate Transparency. Deleting it via Caddy's admin API is slice 3.
**I am not confident** that Caddy re-consults `ask` when *renewing* an on-demand certificate (§17);
if it does not, a removed domain's certificate renews indefinitely. The cost of being wrong is a
certificate we pay nothing for, for a name we serve a 410 on. Acceptable, and the reason gate 2 is
unconditional rather than trusting gate 1.

**The entitlement lapse — ADR-0010 named it unowned, and custom domains make it worse, not better.**
A merchant whose `storefront` entitlement lapses today keeps answering serviceability checks. With
custom domains they keep a **branded, certificated shop on their own domain** and we keep renewing
certificates for a customer who is not paying. The gateway's public branch skips entitlement and
cannot cheaply be made not to (ADR-0010 §4).

This ADR does **two** things about it and does not pretend to do a third:

- **A working manual kill switch exists today.** §6 keys both `ask` and origin resolution on
  `storefronts.status = 'published'` as well as on the domain state, so ADR-0011's
  `POST /storefront/store/unpublish` takes a lapsed merchant's custom domain off the air, over an
  audited route, immediately. Support can act.
- **The automatic consumer is named as a slice-3 prerequisite with an owner required before any
  merchant is billed**, not as a nice-to-have. It sets `status = 'suspended'` on product
  deactivation. It is not built here because the event contract does not exist in
  `libs/contracts/` (which today holds only `openapi/` and `permissions/`), and inventing an event
  name I cannot verify is how the next ADR inherits a fiction.

### 8. Slug versus domain: the domain is canonical, both serve, and slice 1 does not redirect

**The custom domain is canonical the moment it is `live`.** The merchant chose it; it is their brand;
and two hosts serving identical content split search ranking between them.

**What happens to the platform host:** `<slug>.stores.opsgrids.com` **keeps working, permanently.**
It is not 404'd and it is not retired. Three reasons: links, QR codes and printed material outlive
DNS changes; the merchant's own bookmark is usually the slug host; and **it is the fallback when the
custom domain breaks.** A design that retires the slug host makes a DNS mistake unrecoverable except
by us.

**Does it redirect? In slice 1, no — and this is the weakest answer in this ADR.** The canonical
signal is a `<link rel="canonical" href="https://merchant.com/...">` injected by the buyer bundle
from `store-context`'s `canonical_host`, plus `is_canonical: false` so the app can also show a
one-line banner. A `<link rel="canonical">` is a hint; a 308 is an instruction, and search engines
treat them differently. For a merchant with real SEO ambitions this is materially worse.

**Why not a 308 in slice 1:** the redirect has to be decided per host, and the only components that
see the host before the bundle loads are Caddy (which would need a generated host map and a config
reload on every domain change — a write from the application into the edge's configuration, which is
the coupling this whole design avoids) and the origin (which would mean proxying the document
request to Python and putting `index.html` inside a service image). Both are real work for an SEO
improvement on a product with no merchants. **Slice 3 adds the 308 at the edge, at the same time as
certificate cleanup, because both need the admin-API coupling and it should be paid for once.**

**Where canonicality actually bites today, and it is free:** the merchant's share links, the "view
your store" button, order confirmation emails and any QR code the platform generates all render
`canonical_host`. That is where the links a customer keeps come from, and it costs one field.

**A redirect that must not happen:** when the domain is `suspended` or `failing`, the platform host
must not push buyers toward a broken name. `is_canonical` is computed from the live state, so a
suspended domain makes the platform host canonical again automatically. The fallback only works if
it is not also redirecting.

### 9. Data model: one new table, one index, and why there is only one

**`custom_domain_claims`** — `Base, TenantBase` (`id`, `organization_id`, `created_at`,
`updated_at`, `deleted_at`), `ENABLE` + `FORCE ROW LEVEL SECURITY` with `USING` **and** `WITH CHECK`
written out (ADR-0002 §3). Foreign keys within the schema only.

| Column | Type | Notes |
|---|---|---|
| `storefront_id` | UUID FK → `storefront.storefronts.id`, `ON DELETE CASCADE` | In-schema, therefore allowed |
| `verification_token` | `String(64)` | `secrets.token_hex(16)`. Never logged |
| `token_issued_at` | `TIMESTAMPTZ` | |
| `claim_expires_at` | `TIMESTAMPTZ` | `token_issued_at + 14 days`, while `unverified` |
| `verified_at`, `live_since` | `TIMESTAMPTZ NULL` | |
| `last_checked_at`, `next_check_at` | `TIMESTAMPTZ` | `next_check_at` is the due-work column and is shown to the merchant |
| `consecutive_failures` | `INTEGER NOT NULL DEFAULT 0` | Seven ends a live domain |
| `ownership_state`, `routing_state`, `certificate_state` | `String(16)` | `pending \| ok \| failing` |
| `last_error_code` | `String(40) NULL` | The enum the UI switches on |
| `last_error_detail` | `String(500) NULL` | **What we actually saw.** Truncated |
| `tls_not_after`, `tls_last_ok_at` | `TIMESTAMPTZ NULL` | From the handshake probe |

**Why a second table rather than more columns on `storefronts`.** Two concrete reasons, both about
write locality: `storefronts` is the row every anonymous request reads and the row the 300-second
Redis cache is built from, and a failed probe would otherwise bump its `updated_at` every hour; and
three further ADRs are queued to extend `storefronts` (the store document, the catalogue, pages), so
ten operational columns there is ten columns of noise on the platform's most-read table. **What it
gives up:** a state transition writes two tables in one transaction, and a reader has to know that
`custom_domain_status` is the state and the claim row is the evidence. That split is stated in the
table's docstring because a future contributor will otherwise add a second status column.

**Index-timing register.** ADR-0006 §15 records two incidents caused by this register's absence, one
of them an engineer resolving a *silence* by analogy and reaching a competent, wrong conclusion. So
the rejected entries are listed, not omitted.

| Index | Query | Slice |
|---|---|---|
| `custom_domain_claims` **UNIQUE `(organization_id)`** | One claim per organization; the merchant's read; the sweep's per-tenant read | **1**, with the table |
| `storefronts` `uq_storefronts_custom_domain` (partial unique) | §2's host lookup — **already exists**, landed by ADR-0011. ADR-0010 §10's register put it in slice 2; it arrived early | **landed** |
| `storefronts UNIQUE (slug)` | `<slug>.stores.opsgrids.com` resolution, via ADR-0010's existing function | **landed** |
| `custom_domain_claims (next_check_at)` | The sweep's due-work scan | **Rejected.** The sweep discovers *organizations* from `storefronts` and then reads at most one claim row per tenant, so the unique index answers it. A global `next_check_at` index would also have to not lead with `organization_id`, which would mean widening the bypass reader to a second table — the chokepoint ADR-0010 §4 exists to keep narrow |
| `custom_domain_claims (custom_domain_status)` | "Show me all failing domains" | **Rejected.** Low cardinality on a table with one row per tenant. At the point there are enough tenants for this to matter, the sweep's discovery query is the thing that needs fixing, not this |

**One index on a new table is unusual enough to be worth the sentence:** this table has one row per
organization and will hold, at ADR-0010 §12's planning numbers, about fifty rows. Designing indexes
for it is designing for a table that fits in one page.

### 10. Authorization: no new permission, and that is a decision with a price

Domain management is folded into ADR-0010 §9's existing **`storefront.store.settings.read`** and
**`storefront.store.settings.write`**, both `default ORG, max ORG`, every route additionally floored
`minimum=Scope.ORG`.

**Why not a fifth and sixth permission.** ADR-0010 avoided an ADR-0007 fleet reconcile because
`STOREFRONT_ADMIN` and `STOREFRONT_STAFF` were *new* role codes on a *new* product. They are not new
any more. `install_registration` **skips any role code that already exists**, so a new permission
added to an existing template would never reach a tenant and nothing would error (ADR-0007's reason
to exist). A new permission therefore now costs a full ADR-0007 rollout. **Buying a permission
separation nobody has asked for, at the price of a fleet reconcile, on a product with no users, is
the wrong trade.**

**What that gives up, plainly:** a merchant cannot grant "edit the store's currency and timezone"
without also granting "connect and disconnect the domain the business runs on". That is a real
separation a real customer may want, and the trigger to build it — with ADR-0007's full rollout — is
in "Revisit when".

**`STOREFRONT_STAFF` holds `store.settings.read`, so staff can see the verification token.** The
token proves control of a zone and grants nothing; someone who can read it still cannot publish it
without the merchant's DNS. Accepted.

**Server-side, at the data-access boundary, as always.** Every domain route runs through
`get_tenant_session`, every read goes through `selectors` and calls `ctx.apply()`, every write
through `services`, and every route gets a negative-authz test (ADR-0003 §3). The merchant UI hiding
the domain screen is not a control — and per ADR-0010's erratum 10 the storefront app does not mount
a `PermissionProvider` at all, so it cannot hide anything reliably yet regardless.

### 11. The wire format — every body printed

ADR-0010's erratum 5 records that describing a wire format without printing it cost a full rebuild:
two units built against the prose and derived two different shapes, and the service shipped a third.
**The generated `libs/contracts/openapi/storefront.json` is the contract; these are the bodies it
must contain.**

#### `GET /api/v1/storefront/store/domain` — `store.settings.read`

No claim — **200**:

```json
{
  "custom_domain": null,
  "status": null,
  "platform_host": "greenleaf.stores.opsgrids.com",
  "canonical_host": "greenleaf.stores.opsgrids.com",
  "verification": null,
  "dns": null,
  "checks": null
}
```

Claimed, unverified — **200**:

```json
{
  "custom_domain": "merchant.com",
  "status": "unverified",
  "platform_host": "greenleaf.stores.opsgrids.com",
  "canonical_host": "greenleaf.stores.opsgrids.com",
  "verification": {
    "record_type": "TXT",
    "name": "_opsgrids-challenge.merchant.com",
    "value": "opsgrids-domain-verification=<32-hex-token>",
    "issued_at": "2026-09-19T11:02:14Z",
    "claim_expires_at": "2026-10-03T11:02:14Z"
  },
  "dns": {
    "apex": true,
    "preferred": {
      "record_type": "ALIAS",
      "name": "merchant.com",
      "value": "edge.opsgrids.com",
      "note": "If your DNS provider offers ALIAS, ANAME or CNAME flattening, use this. It keeps working if our address changes."
    },
    "fallback": [
      { "record_type": "A", "name": "merchant.com", "value": "198.51.100.7" },
      { "record_type": "A", "name": "www.merchant.com", "value": "198.51.100.7" }
    ],
    "proxy_warning": "If you use Cloudflare, set these records to DNS only (grey cloud). A proxied record stops us issuing your certificate.",
    "also_served": ["www.merchant.com"]
  },
  "checks": {
    "ownership": {
      "state": "failing",
      "last_checked_at": "2026-09-19T11:07:02Z",
      "next_check_at": "2026-09-19T11:12:02Z",
      "code": "txt_not_found",
      "detail": "No TXT record at _opsgrids-challenge.merchant.com. Both resolvers returned NXDOMAIN."
    },
    "routing": {
      "state": "pending",
      "last_checked_at": null,
      "next_check_at": null,
      "code": null,
      "detail": null
    },
    "certificate": {
      "state": "pending",
      "last_checked_at": null,
      "next_check_at": null,
      "code": null,
      "detail": null,
      "not_after": null
    }
  }
}
```

Verified but not yet routed — the state a merchant sits in during cutover, and the one the whole
design exists to make legible — **200** (`checks` only):

```json
{
  "ownership": {
    "state": "ok",
    "last_checked_at": "2026-09-19T11:12:04Z",
    "next_check_at": "2026-09-20T11:12:04Z",
    "code": null,
    "detail": "Found opsgrids-domain-verification=<32-hex-token>"
  },
  "routing": {
    "state": "failing",
    "last_checked_at": "2026-09-19T11:12:05Z",
    "next_check_at": "2026-09-19T11:17:05Z",
    "code": "points_elsewhere",
    "detail": "merchant.com resolves to 203.0.113.9. It needs to resolve to 198.51.100.7."
  },
  "certificate": {
    "state": "pending",
    "last_checked_at": null,
    "next_check_at": null,
    "code": null,
    "detail": null,
    "not_after": null
  }
}
```

Live — **200** (`status` and `checks` only; `verification` is still present and still required):

```json
{
  "status": "live",
  "canonical_host": "merchant.com",
  "checks": {
    "ownership": {
      "state": "ok",
      "last_checked_at": "2026-09-19T03:14:00Z",
      "next_check_at": "2026-09-20T03:14:00Z",
      "code": null,
      "detail": "Found opsgrids-domain-verification=<32-hex-token>"
    },
    "routing": {
      "state": "ok",
      "last_checked_at": "2026-09-19T03:14:01Z",
      "next_check_at": "2026-09-20T03:14:01Z",
      "code": null,
      "detail": "merchant.com resolves to 198.51.100.7"
    },
    "certificate": {
      "state": "ok",
      "last_checked_at": "2026-09-19T03:14:02Z",
      "next_check_at": "2026-09-20T03:14:02Z",
      "code": null,
      "detail": "Issued by Let's Encrypt",
      "not_after": "2026-12-14T09:31:00Z"
    }
  }
}
```

**`status` here and `custom_domain_status` on ADR-0011's `StoreOut` read the same column**, so the
settings screen and the domain screen cannot disagree.

#### `PUT /api/v1/storefront/store/domain` — `store.settings.write`

```json
{ "custom_domain": "merchant.com" }
```

**200** — the `GET` body, in `unverified`, with a freshly minted token. Replacing an existing claim
releases the old one and mints a new token; the response is the same shape.

**409** — claimed elsewhere (ADR-0011's `custom_domain_taken`, extended to cover the `www` partner):

```json
{
  "type": "https://errors.opsgrids.dev/conflict",
  "title": "Conflict",
  "status": 409,
  "detail": "merchant.com is already connected to another OpsGrids store. If it is yours, disconnect it there first, or contact support.",
  "instance": "/api/v1/storefront/store/domain",
  "request_id": "01JBQ8H2K7YT3N9V4W6ZC1RXDP"
}
```

**422** — not a hostname, or a hostname we refuse:

```json
{
  "type": "https://errors.opsgrids.dev/validation",
  "title": "Validation failed",
  "status": 422,
  "detail": "That is not a domain name we can serve.",
  "instance": "/api/v1/storefront/store/domain",
  "request_id": "01JBQ8H2K7YT3N9V4W6ZC1RXDP",
  "errors": [
    { "field": "custom_domain", "code": "reserved_suffix", "message": "opsgrids.com and its subdomains cannot be claimed." }
  ]
}
```

The refusals: a scheme, a port or a path; fewer than two labels; anything under `opsgrids.com` or
`opsgrids.dev`; and a name that does not survive `idna` encoding. **A U-label is converted to its
A-label before validation** — `münchen-shop.de` is stored as `xn--mnchen-shop-thb.de` — because
ADR-0011's `custom_domain_shape` check constraint is ASCII-only and a merchant cannot be expected to
punycode their own domain (§17).

#### `POST /api/v1/storefront/store/domain/check` — `store.settings.write`

No request body. Runs all three probes synchronously and returns the `GET` body.

**429** — the button's own limit:

```json
{
  "type": "https://errors.opsgrids.dev/rate-limited",
  "title": "Too many requests",
  "status": 429,
  "detail": "Checks are limited to one every 30 seconds. DNS changes usually take a few minutes to appear.",
  "instance": "/api/v1/storefront/store/domain/check",
  "request_id": "01JBQ8H2K7YT3N9V4W6ZC1RXDP"
}
```

#### `DELETE /api/v1/storefront/store/domain` — `store.settings.write`

No body. **204 No Content.** Idempotent: deleting when there is no claim is also a 204.

#### `POST /api/v1/storefront/public/store-context` — anonymous

```json
{ "host": "merchant.com" }
```

**200**:

```json
{
  "store": "greenleaf",
  "name": "Greenleaf Organics",
  "tagline": "Fresh from the farm, daily",
  "logo_text": "GLO",
  "currency": "INR",
  "timezone": "Asia/Kolkata",
  "canonical_host": "merchant.com",
  "is_canonical": true,
  "checked_at": "2026-09-19T11:14:22Z"
}
```

`store` is the slug and is what every subsequent call — including ADR-0010's
`POST /public/serviceability` — puts in its `store` field. **This payload is the only public
representation of ADR-0011's brand columns**; the storefront-document ADR extends it rather than
adding a second one (see the coordination note at the top).

**404** — no store at that host, or the store is not `published`, or the domain is `suspended`:

```json
{
  "type": "https://errors.opsgrids.dev/not-found",
  "title": "Not found",
  "status": 404,
  "detail": "No published store at this address.",
  "instance": "/api/v1/storefront/public/store-context",
  "request_id": "01JBQ8H2K7YT3N9V4W6ZC1RXDP"
}
```

**422** — `host` missing or not a hostname. Distinct from 404, because "that is not a hostname" and
"nothing is published there" are different facts, exactly as ADR-0010 separates a malformed postal
code from an unserved one.

#### `GET /api/v1/storefront/internal/tls-authorize?domain=…` — Caddy only

```
HTTP/1.1 200 OK
Content-Length: 0
```

```
HTTP/1.1 403 Forbidden
Content-Length: 0
```

Caddy reads the status and nothing else. `domain` is the query-parameter name **because that is what
Caddy appends**; it is not ours to choose.

#### What the edge serves on a host that resolves to nothing

**410 Gone**, `text/html`, a static page from Caddy's own file server — not a JSON problem document,
because the requester is a person's browser and not a client of ours.

### 12. Capacity, and what saturates first

Numbers marked **(assumed)** are planning assumptions, not measurements.

- **Merchants with a custom domain**: ~50 in year one **(assumed)**, ~1.0 claims each (one per
  store, ADR-0011's constraint) plus one `www` partner served without a row. **`custom_domain_claims`
  holds about fifty rows.**
- **Buyer traffic** inherits ADR-0010 §12: ~1,000 sessions/day/merchant, ~20 req/s fleet-wide at
  peak **(assumed)**. Custom domains add **one bootstrap call per session**, not per page view.
- **Probe load**: 50 domains × 3 probes/day = 150 DNS queries and 50 TLS handshakes per day.
  Irrelevant.
- **Data growth**: one row per merchant, edited rarely. Bytes.
- **Bandwidth**: a ~300 KB fingerprinted bundle, served once per buyer per deploy. At 50,000
  sessions/day fleet-wide with normal caching, well inside any VM's included transfer.

**What saturates first is not a query — it is the single VM.** Every restart, every `--build`, every
kernel upgrade is a full outage of every merchant's shop. **The trigger to move is not a number, it
is an event: the first merchant who asks about uptime in writing.** That is honest and it is what
will actually happen.

**The second saturation point is the directory engine's five connections** (`db.py:302`), already
named by ADR-0010 §12, and this ADR puts a second anonymous call on it. **The 300-second host cache
is what keeps it off that pool**; a cold cache under a burst is the failure to watch, and the fix is
a larger dedicated pool for this service rather than a longer TTL, because the TTL is also the
merchant's publish latency.

**The third is Let's Encrypt.** The limits that matter are roughly **300 new orders per account per
three hours** and **5 duplicate certificates per exact name per week** — *these figures changed
during 2025 and must be re-read before being relied on* (§17). At 50 domains, none of them bind.
They bind in exactly one scenario: **total loss of `caddy_data`**, which re-issues every certificate
at once. At 50 domains that is a few minutes; at 500 it is staggered outage measured in hours. The
trigger for moving certificate storage off one box's disk is therefore "more than ~250 domains", and
it is the same trigger as slice 4.

**The fourth is the anonymous rate limit under NAT** — 60/min per client IP, fails closed. Two calls
per session keeps a browsing buyer far from it; a shared office or mall IP with many simultaneous
buyers is where it bites. The lever is `anonymous_rate_limit_per_minute` on the gateway service, and
the trigger is clustered "couldn't check" reports from one merchant.

### 13. Timeouts, retries, and what each dependency does when it misbehaves

**The rule that governs this table:**

> **A check that cannot run never takes a domain off the air** (§6). And, inherited from ADR-0010
> §13: a buyer-facing failure renders as "something went wrong", never as "this store does not
> exist". A 410 is only ever served because a state machine said so, never because a lookup failed.

| Dependency | Slow | Failing | Wrong |
|---|---|---|---|
| **Caddy** | Handshake queueing; on-demand issuance adds 2–5 s to the *first* request for a name | **Total outage of every merchant's shop.** One process, one box. This is the design's single largest risk and it is the price of §1 | A misconfigured `Caddyfile` deployed successfully is the same as failing, and there is no staging environment to catch it. `caddy validate` runs in `make deploy` before restart |
| **ACME (Let's Encrypt → ZeroSSL)** | Issuance takes seconds; the buyer waits | Existing certificates keep serving; **new** domains cannot go live. Caddy falls back to the second CA automatically | A rate-limit refusal looks identical to a validation failure in the log. The certificate probe's `not_after` is the instrument that tells them apart |
| **`ask` endpoint / storefront service** | Caddy's own timeout; issuance is skipped and retried | **Fails closed for new issuance only.** Existing certificates are served from storage. A storefront outage cannot take live TLS down | A wrong `yes` issues a certificate for a name we do not serve — harmless, gate 2 refuses the content |
| **Postgres, app role** | Public route budget **2 s**, then 503. The buyer sees a retry, never a 410 | 503. Nothing is de-activated; the state machine is stored, not computed | RLS misconfiguration returns zero rows silently (ADR-0002 §4); the non-production GUC probe is what makes it loud |
| **Postgres, `opsgrids_storefront_directory`** | Pool exhaustion (5 connections). The host cache absorbs the steady state | 503 to the buyer. **No domain changes state** | **Role unset → silent fallback → zero rows → every custom domain 410s and every certificate request is denied.** The existing startup assertion (ADR-0010 §4) is what prevents it, and it now protects twice as much |
| **Redis, host/slug cache** | Miss falls through to one indexed query. Never fatal | Same | A stale entry for up to 300 s: a newly verified domain may be refused a certificate, and a just-removed domain may still serve, for five minutes. Both accepted; both are shorter than any verification |
| **Redis, rate limiter** | — | **Fails closed** (`cache.py:90-104`): the buyer surface 503s wholesale. This is why `tls-authorize` does **not** go through the gateway — otherwise a Redis outage would also stop all certificate issuance | — |
| **DNS resolution (the probes)** | 5 s per query, 2 resolvers, then a failure. `consecutive_failures + 1` | The domain goes `failing`, visible to the merchant; **seven days** before anything is taken off the air | **The dangerous one: a negatively cached answer.** A merchant who just added the record keeps failing for the negative-cache TTL. Mitigated by querying two public resolvers directly rather than the VM's, by never caching a negative answer ourselves, and by the "Check now" copy saying so |
| **The merchant's DNS provider** | — | Probes fail; §6's seven-day tolerance covers a provider outage | A provider that silently ignores underscore labels makes verification impossible with a message that looks like the merchant's mistake (§17) |
| **The cron container** | Probes are late; nothing else | **Nothing is ever checked and nothing errors.** No domain is promoted, no lapse is detected, every live domain keeps working. `bin/cron` pointing at `app.relay` produces exactly this | — |

**Retries: none inside a probe** beyond the second resolver, and none on the buyer path. Every
buyer-path operation is one short indexed query; a retry adds latency to a request someone is
watching. The probe's retry *is* its schedule — `next_check_at` with backoff — which is why the
schedule is stored rather than held in the loop.

### 14. Secrets

| Thing | Where it lives | Rule |
|---|---|---|
| **ACME account key, every certificate and private key** | Caddy storage, named volume `caddy_data` on the edge VM, mode 0700 | Never in git, never in the image, never in Postgres, never in an env var. **No service ever reads one.** `make secrets` scans `git ls-files` and will therefore never see them — **that is the control working, not a gap**, and it also means `make secrets` gives no assurance whatsoever about this material |
| **Verification tokens** | `custom_domain_claims.verification_token` in Postgres | Returned only over the tenant-bound, permission-gated route. **Never logged** — not in the probe's failure detail, not in an audit row's `after`, not in a test fixture. `detect-secrets` will flag a committed 32-hex string as high-entropy, and **that finding must be fixed, never baselined** |
| **`infrastructure/edge/.env`** | The VM only | `.gitignore`d. Generated by hand from `.env.example` on first deploy. Holds the database password, the auth signing key material's location, and the S3 credentials |
| **`Caddyfile` and `compose.prod.yml`** | Committed | They must contain **no** secret. The ACME contact email is not a secret; every other value is an `${ENV_VAR}` reference |
| **Deploy access** | An engineer's own SSH key, in their agent | No GitHub Actions secret in slice 1. That is the main reason slice 1 builds on the box |

### 15. Audit

Through `platform_core.audit.record_audit`, in the same transaction as the change (ADR-0000, "Also
settled"):

- `storefront.domain.claimed` — `before`/`after` on `custom_domain`. **The token is not in the row.**
- `storefront.domain.verified`, `.activated`, `.failing`, `.suspended`, `.released` — the state
  transitions, with the triggering `last_error_code`. Sweep-driven transitions carry a system actor.
- `storefront.domain.checked` — **only for the merchant's explicit "Check now"**, because that is a
  human act on a rate-limited route. **No audit row per sweep probe**: 150 probes a day of a
  published fact would bury every real entry, the same reasoning as ADR-0010 §14's refusal to audit
  serviceability checks.

**No `record_sensitive_read`.** A domain name is published in DNS and in Certificate Transparency;
calling it sensitive would dilute the signal on the paths where it matters (ADR-0009 §14).

**No event is published.** ADR-0008 §3's precedent — an event type with no consumer is a contract
kept compatible forever in exchange for nothing. The storefront outbox stays empty.

### 16. Dev/prod parity and bootstrap

| Thing | Where | Note |
|---|---|---|
| `infrastructure/edge/compose.prod.yml` | new | Postgres+PostGIS 16-3.4, Redis 7 — **the same images `tools/dev-bootstrap/compose.yml` uses**, because parity is not optional when the domain leans on PostGIS, `SKIP LOCKED` and `ON CONFLICT` |
| `infrastructure/edge/Caddyfile` | new | Pinned Caddy version. `caddy validate` runs in `make deploy` before anything restarts |
| `infrastructure/edge/RUNBOOK.md` | new | Restore from snapshot; what to do when `caddy_data` is lost; how to take one merchant off the air |
| `caddy` service in `tools/dev-bootstrap/compose.yml` | **not added** | Local development stays on Vite's proxy. A merchant domain cannot be verified against `localhost`, so a local Caddy would exercise nothing the tests do not |
| `services/storefront/bin/cron` → `python -m app.cron` | change | **The scaffold points it at the relay.** Left as-is, no domain is ever checked and nothing errors |
| `EDGE_RESERVED_IP`, `EDGE_HOST`, `PLATFORM_STORE_SUFFIX` | `services/storefront/app/config.py` | **Pinned as defaults in the service's own `Settings`, not set in `.env`** — ADR-0010's erratum 1: `ServiceSettings` declares no `env_prefix`, so an unprefixed key in the repository's single `.env` is read by every service |
| `dnspython` | `pyproject.toml` | ISC licence; passes `make licenses`. The one new runtime dependency |
| Second `STOREFRONT_PUBLIC_PATHS` entry | `services/app-gateway/app/main.py` | `/api/v1/storefront/public/store-context`. **Not** `platform_core` |
| `dist-buyer/` | `.gitignore` | The second Vite output |
| A test asserting `routes.json` has no `/api/v1/storefront/internal` entry | `services/app-gateway/tests/` | The internal route is protected today only by the catch-all's token check; a well-meant explicit entry could change that |

### 17. What I am not confident about

Named rather than smoothed over.

1. **Whether Caddy re-consults `ask` when *renewing* an on-demand certificate.** I believe recent
   versions do; I have not verified it against a pinned version. If it does not, a removed domain's
   certificate renews indefinitely. §7's gate 2 is unconditional precisely so that being wrong here
   costs nothing but a certificate.
2. **The exact `on_demand_tls` options in the Caddy version we pin.** `interval`/`burst` have been
   deprecated in favour of `ask`. The Caddyfile in slice 1 must be validated against the pinned
   version, not written from memory.
3. **Whether a TLS handshake from a container to the Caddy container with SNI overridden to
   `merchant.com` reliably triggers on-demand issuance**, and whether `httpx` exposes the SNI
   override cleanly. If it does not, the fallback is a raw `ssl.SSLContext` over `asyncio` — more
   code, same behaviour — and the warm-up degrades to "the first real visitor waits".
4. **Let's Encrypt's current rate limits.** The figures in §12 are from memory and the limits changed
   during 2025. They must be re-read before anyone relies on the headroom calculation.
5. **Whether merchants' registrars will reliably let them create a TXT record at an underscore
   label.** Some cheap control panels mangle them. If that turns out to be common, the fallback is a
   TXT at the apex itself, which is uglier and sits next to SPF and DMARC.
6. **Internationalised domains.** ADR-0011's `custom_domain_shape` constraint is ASCII-only, so a
   merchant must submit punycode or the writer must convert. §11 specifies conversion in the writer;
   I have not checked that `idna` round-trips every form a merchant might paste, and a wrong
   conversion stores a domain nobody can reach.
7. **Whether one VM's Docker network and a single Caddy process survive a merchant pointing genuinely
   high-traffic domain at us.** No measurement exists, and §12's numbers are ADR-0010's assumptions
   carried forward.
8. **ADR-0011 is not finished as this is written.** Its model changes are in the working tree and its
   ADR document does not exist yet. Every column reference here is to the tree as of 2026-09-19. If
   ADR-0011 moves `custom_domain` off `storefronts`, §2's "no new bypass grant" result is the first
   thing to fall.

### 18. Third-party dependencies and what they cost, monthly

The user is paying for this.

| Item | Recurring | Notes |
|---|---|---|
| Edge VM (Hetzner CX32 / DO 4 GB) | **€7.60 – $24** | One box, everything on it |
| Reserved IPv4 | **$1 – $5** | Non-negotiable; §1 explains why |
| Provider snapshots | **$2 – $5** | ~20% of VM cost |
| Off-box backup storage | **~$1** | `pg_dump` at this size is megabytes |
| `opsgrids.com` registration | **~$1/mo** (~$12–15/yr) | |
| DNS hosting (Cloudflare, DNS-only) | **$0** | The platform zone must **not** be proxied, or Caddy never sees the handshake |
| Let's Encrypt + ZeroSSL fallback | **$0** | Two CAs, no configuration, no account with either |
| `dnspython` | **$0** | ISC licence |
| **Total** | **roughly $15 – $35 / month** | Plus the engineer-hours of owning a VM, which is the real cost and is not zero |

**The paid alternative, named with its trigger.** **Cloudflare for SaaS** ($2/month + $0.10 per
custom hostname per month — 200 domains ≈ $22/month) removes ACME, renewal, certificate storage, the
`ask` endpoint's abuse role and the single-IP apex liability, and gives a global anycast edge
instead of one box. **Take it when the number of custom domains passes ~200, or at the first uptime
commitment in a contract, whichever comes first.** It is not slice 1 because it is a vendor lock on
the buyer's request path bought before there is a buyer, and because the design above is what makes
adopting it a change to one component rather than a rewrite.

---

## What this rejects

- **A Kubernetes cluster, Helm charts, or kustomize overlays.** `CLAUDE.md` describes them;
  nothing has ever existed. A control plane is $70/month before a node, three more failure modes, and
  a per-merchant certificate story that is worse than Caddy's. §1 names the trigger.
- **A managed application platform (Render, Fly, Railway).** They solve custom domains well and are
  shaped for one service per app. This repository is thirty-five.
- **nginx + certbot.** Needs a certificate before the first request, which means a config write and a
  reload inside a merchant's onboarding, and a reload that can fail with half a config applied.
- **Baking the store into the SPA build.** A build and a deploy per merchant, and a rebuild of all of
  them on every code change. The bundle is byte-identical for every host; the host comes from
  `window.location.hostname`.
- **Serving the buyer bundle from `store.opsgrids.com` and calling `api.opsgrids.com`.** It makes
  `cors_origins` grow a row per merchant domain — process configuration read at boot, so every new
  domain becomes a restart of every service. The wildcard escape hatch is illegal with credentials.
- **Treating "your DNS points at us" as proof of ownership.** It is the subdomain-takeover primitive.
  A CNAME can be inherited; a TXT record in the merchant's own zone cannot.
- **Verification by email to `admin@` or by a file at `/.well-known/`.** The first is a mailbox we do
  not control and a support path that skips DNS; the second cannot be served on a domain we do not
  yet serve.
- **DNS-01 for merchant domains.** It needs the merchant's DNS credentials, or a second delegated
  record to explain and to lapse.
- **DNS-01 for `*.opsgrids.com` either.** It would buy a wildcard and cost the platform a DNS
  provider credential. Removing a class of secret is worth more than the wildcard.
- **Putting the `ask` endpoint behind the gateway.** The gateway's anonymous limiter fails closed, so
  a Redis outage would stop all certificate issuance.
- **A `blocked` state for a contested hostname.** ADR-0011's partial unique index makes the collision
  impossible to represent; a state for it would be a state nothing can reach.
- **Letting an unverified claim hold a hostname forever.** ADR-0011's unique index would make that a
  squatting vector. Claims expire at 14 days and expiry releases the name.
- **Taking a domain off the air on the first failed check.** DNS providers have outages; seven
  consecutive daily failures is the threshold, and `failing` is visible from the first.
- **Letting a removed domain's certificate lapse into a browser TLS error.** A 410 served by us is
  better UX *and* a stronger boundary, because it means the content on a dangling name is always
  ours.
- **A global `next_check_at` index on `custom_domain_claims`.** It would not lead with
  `organization_id`, which would mean widening `opsgrids_storefront_directory` to a second table —
  the chokepoint ADR-0010 §4 exists to keep at one.
- **Two new permissions for domain management.** `STOREFRONT_ADMIN` and `STOREFRONT_STAFF` now exist,
  and `install_registration` skips existing role codes, so a new permission costs an ADR-0007 fleet
  reconcile. Nobody has asked for the separation.
- **Multiple domains per store.** ADR-0011 fixed it at one column with one unique index. The `www`
  partner is served without a second row. A real multi-domain design supersedes that decision rather
  than working around it.
- **A Public Suffix List dependency** for the apex/`www` rule. A bundled list that goes stale, to
  make a syntactic rule marginally smarter.
- **A CDN in slice 1.** Same reasoning as ADR-0009's: it adds a deploy piece, a cache-invalidation
  obligation and a bill, to solve a bandwidth problem nobody has.
- **A 308 from the platform host to the custom domain in slice 1.** It needs either generated Caddy
  config written by the application, or `index.html` inside a service image. Slice 3, with
  certificate cleanup, because both need the same coupling.
- **Retiring the platform host once a custom domain is live.** It is the fallback that makes a DNS
  mistake recoverable, and it is in every link a merchant has already shared.
- **Any authenticated surface on a custom domain.** ADR-0001's refresh cookie is scoped to
  `.opsgrids.com` and must stay there. A merchant's session must never travel to a host they can put
  arbitrary JavaScript next to.
- **Inventing an event name for the entitlement-lapse consumer.** `libs/contracts/` holds `openapi/`
  and `permissions/` and no event schemas. A fiction here becomes a contract the next ADR inherits.

## Consequences

- **A merchant can put their store on their own domain, and the platform can serve it.** That
  required creating the deployment this repository has never had, and the deployment is one VM.
- **There is now a production.** Once one real domain points at that IP, the box cannot be rebooted
  for convenience and a failed `--build` leaves a shop down. Every other consequence in this list is
  smaller than that one.
- **The single VM is the design's largest risk and it is deliberate.** Any restart is a full outage
  of every merchant's shop. §12 makes the trigger an event rather than a metric, because that is what
  will actually happen.
- **ADR-0010 §4's chokepoint survives intact.** `opsgrids_storefront_directory` still holds SELECT on
  `storefront.storefronts` and nothing else, because ADR-0011 put the domain on that table and
  because the sweep discovers organizations rather than domains. Both of those must be defended, not
  re-derived, the next time something needs a cross-tenant read.
- **The startup assertion on `db_directory_role` now protects twice as much.** With it unset, every
  custom domain 410s *and* every certificate request is denied — a whole-fleet outage from a missing
  default, with HTTP 200s and no log, exactly as ADR-0010 described for serviceability.
- **`bin/cron` pointing at the relay is a new instance of the same silent-nothing failure.** Nothing
  is checked, nothing is promoted, nothing errors.
- **Apex merchants have our IP address in their zone file, permanently.** The reserved IP is what
  keeps that from being a scheduled migration. The UI recommends a subdomain for this reason and not
  merely as a preference.
- **The buyer path gains a second anonymous call.** Once per session, not per page view, and
  ADR-0010's wire format is unchanged — which is the payoff of resolving the host once in a bootstrap
  rather than adding a `host` field to every public endpoint.
- **A second Vite entry point exists**, and the two bundles can drift. The buyer entry contains no
  auth code at all, which is the point.
- **Two failure modes are now visible to a buyer that were not before**: a browser TLS error, which
  we cannot dress up, and a 410, which we can. The state machine exists to make the first rare and
  the second deliberate.
- **Nobody is paged when issuance fails.** The compensating controls are a real reason on the
  merchant's screen and a daily probe. That is a gap, not a mitigation, and it is slice 2.
- **The entitlement lapse is now materially worse and now has a manual answer.** A lapsed merchant
  keeps a branded, certificated shop, and we keep renewing certificates for them. `unpublish` takes
  it off the air over an audited route. The automatic consumer is a slice-3 prerequisite and still
  needs an owner.
- **Certificate Transparency publishes every merchant domain we serve**, so the list of our customers
  and their domains is public. Unavoidable for any publicly trusted certificate; recorded so nobody
  promises otherwise in a contract.
- **A five-minute window exists** after a removal or a state change during which the Redis host cache
  may still serve. Shorter than any verification, so it cannot be used to take a domain; long enough
  to confuse a merchant testing a removal, and the UI should say so.
- **`<link rel="canonical">` is a weaker SEO signal than a 308**, and slice 1 ships the weaker one.
  For a merchant whose traffic comes from search, that is a real cost with a dated fix.
- **`CLAUDE.md` now describes an `infrastructure/` that exists and contains something other than what
  it says.** That line needs a human to amend it.

## Revisit when

- **A merchant asks about uptime in a contract.** Then §1's one VM is no longer the answer, and the
  choice is a managed edge (Cloudflare for SaaS) plus a second application box, in that order —
  because the edge is the single point of failure that matters and it is the one that can be bought.
- **Custom domains pass roughly 200.** Then Cloudflare for SaaS is cheaper than the operational risk,
  and certificate storage on one box's disk becomes the thing that can lose a fleet.
- **`caddy_data` is lost once.** Then certificate storage moves off local disk before it can happen
  twice — Caddy's Postgres or Redis storage modules are third-party plugins and must be evaluated as
  such.
- **A merchant needs a second domain on one store** (a country domain, a rebrand with both live).
  Then ADR-0011's single `custom_domain` column is superseded by a table, and §2's `IN (:host,
  :partner)` becomes a plain lookup. Named as a supersession, not an adaptation.
- **A customer asks to separate "connect a domain" from "edit store settings".** Then §10 becomes two
  more permissions **with ADR-0007's full rollout**, because the role templates now exist.
- **The tenant count passes roughly 1,000.** Then §6's sweep — one transaction per tenant — becomes
  the bottleneck, and its discovery query needs a grant on `custom_domain_claims` or its own role.
  Until then, not widening the bypass reader is worth more than the loop.
- **Underscore-label TXT records turn out to be widely unsupported by merchants' registrars.** Then
  verification moves to a TXT at the apex with a prefixed value.
- **A merchant's domain is high-traffic enough to matter.** Then §12's assumed numbers get replaced
  by measurements, and the first thing to measure is Caddy's connection handling, not Postgres.
- **The storefront-document ADR lands.** Then §11's `store-context` payload is extended by it rather
  than duplicated, and `/live/*`'s `RequireAuth` question (ADR-0010 §15) is settled there — this ADR
  routes around it with a second bundle rather than pre-empting it.
- **`GatewayPolicy.public_paths` gains prefix matching.** Then the two exact public paths could
  collapse into a prefix, and the "variables in the query or the body" constraint relaxes.
- **A second country's merchant appears.** Then §11's `idna` handling is exercised for real, and
  ADR-0010 §2's India-only postal-code validator gains its second entry at the same time.

---

## The plan, sliced

Each slice is shippable. Each one's "does not do" list is as much of the deliverable as its "ships"
list, because the thing that goes wrong with hosting work is discovering in slice 3 that slice 1
implied something nobody wrote down.

### Slice 1 — one real store, one real URL. *Nothing blocks it.*

**The smallest thing that puts a merchant's store on a domain they own.** After slice 1, a merchant
types `merchant.com`, adds two DNS records, watches three checks turn green with real reasons when
they do not, and their shop answers on their domain over HTTPS with a certificate nobody issued by
hand.

**Ships:**

- `infrastructure/edge/` — `compose.prod.yml` (Caddy, Postgres+PostGIS, Redis, `auth`, `account`,
  `product`, `app-gateway`, `storefront`), `Caddyfile` with `on_demand_tls { ask … }`, `.env.example`,
  `RUNBOOK.md`; `make deploy` over SSH with `caddy validate` first.
- One VM with a **reserved IP**; `edge.opsgrids.com`, `*.stores.opsgrids.com` (A + AAAA); nightly
  `pg_dump` off-box.
- `custom_domain_claims` with its policies and its one index; `CUSTOM_DOMAIN_STATUSES` widened to
  five values.
- The three probes, the state machine, and the sweep in `app/cron.py`; **`bin/cron` repointed**.
- `PUT / GET / DELETE /storefront/store/domain` and `POST /storefront/store/domain/check`, on the
  existing `store.settings.*` permissions, each with a negative-authz test.
- `GET /storefront/internal/tls-authorize`, reachable from Caddy on the container network.
- `POST /public/store-context` and the second `STOREFRONT_PUBLIC_PATHS` entry;
  `resolve_public_store_by_host` with the `www` pair, the 300-second host cache, and cache
  invalidation on every transition.
- Punycode normalisation and the reserved-suffix refusal in `domain.py`.
- The second Vite entry (`buyer.html` → `dist-buyer/`) with no auth code; the bootstrap call, the
  `sessionStorage` cache, the canonical link tag; the merchant's domain screen with the record table,
  the three check rows and the "Check now" button.
- The 410 page at the edge.

**Does not do:** no image registry and no CI deploy job (builds happen on the box); no Kubernetes;
no CDN; no alerting and **no pager**; no second domain per store; no 308 from the platform host; no
certificate deletion on removal; no automatic entitlement-lapse suspension (`unpublish` is the manual
lever); no authenticated surface on a custom domain; no Public Suffix List; no email or in-app
notification when a domain changes state — the merchant finds out by looking; no local Caddy in
`tools/dev-bootstrap`; no IPv6 record asked of merchants; no staging environment.

### Slice 2 — the deploy pipeline and the pager. *Nothing blocks it.*

Images built by CI and pushed to GHCR; a deploy job with the SSH key as a repository secret; health
gating on the rollout; log shipping off the box; and the first two alerts that matter — **a domain
has been `failing` for 24 hours**, and **a certificate is inside 14 days of expiry with a failed
renewal**. This is the slice that converts §5's "nobody is paged" from a gap into a decision.

**Does not do:** no second box; no HA; no blue/green; no autoscaling.

### Slice 3 — removal, redirects, and the money. **Blocked on an owner for the lapse consumer.**

Certificate deletion from Caddy storage on removal via the admin API; the 308 from
`<slug>.stores.opsgrids.com` to the canonical host at the edge (both need the same admin-API
coupling, so they are paid for once); the product-deactivation consumer that sets
`status = 'suspended'`, which finally closes the gap ADR-0010 §4 named and left unowned.

**Does not do:** no per-merchant edge configuration beyond the redirect map; no custom error pages
per merchant.

### Slice 4 — move the edge. **Blocked on ~200 domains or an uptime commitment.**

Either Cloudflare for SaaS in front (which retires the `ask` endpoint's abuse role, on-demand
issuance, renewal and certificate storage, and replaces the apex A record with theirs), or a second
application box behind a load balancer with certificate storage moved off local disk. **The decision
between them is made with the numbers that exist at the time**, not now.

**Does not do:** not a rewrite of anything in slice 1 — the origin's state check, the state machine
and the probes are all unchanged by either choice, and that is the point of §7's two-gate split.

### Slice 5 — more than one domain per store. **Blocked on a merchant asking.**

Supersedes ADR-0011's single `custom_domain` column with a table; the `www` partner becomes a real
row; a primary/redirect role per row. Explicitly a supersession, so that ADR-0011's reasoning is
retired on the record rather than worked around.

**Does not do:** not built on speculation. One merchant with a country domain is the trigger.

---

## Errata — found while building slice 1 (2026-09-19)

**1. The worked punycode example was wrong.** §11 printed `münchen-shop.de` as
`xn--mnchen-shop-ekb.de`. The real IDNA2008/UTS-46 encoding is `xn--mnchen-shop-thb.de`,
confirmed against the `idna` library. It was hand-written rather than generated, and corrected
in place. **Anyone who had copied it into a fixture would have had a test that passed against
the wrong string** — the merchant domain route is where that would have landed.

**2. Two stores can claim opposite halves of one `www` pair.** `merchant.com` and
`www.merchant.com` are separately unique, so the uniqueness constraint permits one organisation
to hold each. §2 does not address it. A `.one_or_none()` here would have raised — and **a 500 on
this path renders as an absent store, which is the one outcome this design exists to prevent**.
The lookup therefore orders exact-host-first with `LIMIT 2`, serves the exact match
deterministically, and logs `storefront.public.host_claimed_twice`.

**3. The custom-domain branch is unreachable through any API today**, because ADR-0011's check
constraint allows only `unverified | verified` and nothing moves it. The lookup filter is
already written as §2 specifies (`verified`, `live`, `failing`), so the slice that lands the
state machine changes no code on this path. Consequence: `canonical_host` cannot return a custom
domain in production yet, which correctly leaves the platform host canonical per §8.

**4. The cached public row widened.** A rolling deploy will meet cache entries written before
the brand columns existed; a short record is treated as a miss rather than served as a
brand-less store. No key version and no ops step.

**5. The anonymous path is not entitlement-gated and cannot cheaply be.** A lapsed merchant
keeps answering here. The consumer that would close it is still unowned — named in ADR-0010 and
again in slice 3.

### Errata, fourth pass — architectural review of the shipped edge (2026-09-20)

**6. The on-demand TLS `ask` endpoint was never built.** The Caddyfile calls
`{$TLS_ASK_URL}` and compose points it at
`storefront:8000/api/v1/storefront/internal/tls-authorize`. No route serves that path;
`services/storefront/app/api/` holds `routes.py` and `public.py` only. Caddy treats a non-2xx
as "no", so **no hostname can obtain a certificate — not a custom domain, and not
`<slug>.stores.opsgrids.com`.** The box would serve HTTPS redirects to an endpoint that cannot
complete a handshake: a total outage the moment DNS points at it, and one `make edge-validate`
cannot catch because the Caddyfile is syntactically valid. Slice 1 is not deployable until this
route exists and answers `200` for every platform subdomain of a published store.

**7. Slice 1's edge deploys no broker, and that silently breaks product activation.** §15
reasoned that storefront publishes nothing anyone consumes, which was true of storefront and
false of the box: `product` is one of the five deployed services, `product.activate()`
publishes `core.product.activated`, and `account`'s consumer of that event is the only thing
that installs a product's roles. The runbook now documents the manual `tools/wire_product.py`
step this forces, but the correct fix is a broker plus `account`'s worker on the box.

**8. `approval`, `subscription` and `storefront` cannot publish even with a broker.** The
scaffold's three entrypoints are `bin/api`, `bin/worker`, `bin/cron`; the scaffold points
`bin/cron` at the relay. Those three services correctly repointed `bin/cron` at a real sweep —
which leaves `app/relay.py` with no entrypoint at all. A fourth entrypoint, `bin/relay`, belongs
in the template.

**9. The entitlement-lapse consumer has now been deferred by three ADRs** (0010 §4, 0011, this
one). Three deferrals is a decision not to build it. `storefront/app/consumers.py` registers zero
handlers; the fix is one handler binding `core.product.deactivated` and setting `status` to
`suspended`, a state the CHECK constraint already allows. Until then a lapsed merchant keeps
answering serviceability to the public internet and, once §5 works, keeps a certificate we
renew on their behalf.

**10. Connection-pool arithmetic on the one-VM edge exceeds Postgres's default.** Five services
× (10 pool + 5 overflow) plus two directory engines plus every outbox engine reaches ~105
connections against `max_connections = 100`. The first symptom is `FATAL: sorry, too many
clients` on `auth` — nobody can log in. Set `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` in compose or
raise `max_connections` with `shared_buffers` sized to match.

**11. The reserved-IP premise (§1, §4) is unverified against the actual provider.** DNS is at
Hostinger. Hostinger VPS does not offer floating IPs. If the box lands there, the address every
merchant types into their apex A record changes when the VM is rebuilt — unrecoverable without
contacting each of them. Confirm the provider before the first real domain points anywhere, and
commit the intended zone as a file under `infrastructure/edge/` that `preflight.sh` diffs
against live DNS.
