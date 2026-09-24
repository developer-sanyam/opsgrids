# app-gateway — the product surface

Every request from a product UI or a mobile app enters here. The gateway verifies the
token once, establishes who is asking and for which organization, decides whether that
organization has bought the product being asked for, and proxies to the owning service.

**It owns no tables and opens no database connection.** Everything it needs is in the
token or in Redis. `db_schema` is set to `public` only because the base settings require a
value.

```bash
make run SERVICE=app-gateway PORT=8100
```

There is no worker and no relay: with no tables there is no outbox to drain and no
projection to maintain.

## The order of checks

Cheapest and most conclusive first, which is also the order that leaks the least:

| # | Check | Failure |
|---|---|---|
| 1 | Route exists | `404` — a request to nowhere is not worth authenticating |
| 2 | Token verifies (signature, issuer, **audience**, expiry, type) | `401` |
| 3 | Token carries an organization | `401` — there is no default org; guessing is how tenants read each other's data |
| 4 | Permission version is current | `401 token-stale` — a distinct type, so the client refreshes rather than showing a login screen |
| 5 | Product is entitled | `402` — **not 403.** The caller's permissions are fine; the subscription is not, so the client can offer an upgrade |
| 6 | Rate limit | `429` with `Retry-After` |
| 7 | Proxy | `503` if the upstream is unreachable — a connection error never reaches the caller |

Rate limiting comes *after* authentication so it counts against a person rather than an
address, which is what makes a shared office NAT usable. Public paths are limited by IP
instead.

## Unauthenticated traffic

Three kinds, each with its own buckets (Redis, one-minute windows, failing closed):

- **Public paths** — per *caller* address, `ANONYMOUS_RATE_LIMIT_PER_MINUTE` (60), and a ceiling
  over all callers together that depends on the path. **Credential** endpoints (sign-in, MFA
  verify, password forgot/reset, sign-up, accept-invitation) share `anon-global`
  (`ANONYMOUS_GLOBAL_RATE_LIMIT_PER_MINUTE`, 6000); every other public path (the storefront buyer
  calls, the unsubscribe link, session refresh, SSO) shares `anon-content-global`
  (`ANONYMOUS_CONTENT_GLOBAL_RATE_LIMIT_PER_MINUTE`, 60000). `0` disables a ceiling. Separate so a
  crowd of storefront visitors cannot lock out sign-in and the reverse. Which path is which is
  declared in `app/main.py` (`CREDENTIAL_PUBLIC_PATHS`, `CONTENT_PUBLIC_PATHS`) and a test fails
  when one is on neither. The per-address check runs first, so one address can spend at most its
  own allowance of a ceiling.
- **Provider webhooks** (`WEBHOOK_PUBLIC_PATHS` in `app/main.py`: Razorpay `POST`, Meta's WhatsApp
  `GET`/`POST`) — no token, exact path and method, and **one limit: rejected deliveries per
  address** (`WEBHOOK_FAILURE_LIMIT_PER_MINUTE`, 60). The bucket is read before a delivery is
  forwarded and spent only when the service answers 400/401/403/422; a success or a failure on our
  side costs nothing and no bucket is shared between addresses, so no stranger can make a genuine
  delivery fail. The body is capped on the route, cookies/`Authorization`/`Origin`/`Referer` are
  dropped before the request is forwarded, and `Set-Cookie`/`Access-Control-*` from the service are
  dropped on the way back. The gateway's own CORS middleware still wraps the route, so an allowed
  first-party `Origin` (providers send none) would be answered with that origin's CORS headers.
  Authenticity is the owning service's HMAC check over the raw body.
- **Everything else** needs a bearer token.

**Bodies from strangers are bounded.** The gateway buffers a body whole, so for every
unauthenticated request it buffers (public paths *and* webhooks; never an authenticated upload) the
body must arrive within `UNAUTH_BODY_READ_TIMEOUT_SECONDS` (10, else 408), and one address may have
`UNAUTH_BODY_MAX_CONCURRENT_PER_ADDRESS` (4) requests in flight, the process
`UNAUTH_BODY_MAX_CONCURRENT_GLOBAL` (32) — past either, 503 with `Retry-After: 5` before a byte is
read. Slots are held until the response is on its way and released on every way out. Every limit is
bounds-checked at boot: a per-address limit of 0 would refuse everyone, so it stops the process.

**The caller's address** is `X-Forwarded-For`, believed only when the connecting peer is inside
`TRUSTED_PROXY_CIDRS` and read from the right (the first address that is not a trusted proxy);
from any other peer the header is ignored. The gateway then forwards exactly one
`X-Forwarded-For` — the address it resolved — on every request, so the address a service audits is
the address that was rate limited. Unset means "trust nobody", so the limiter keys on the socket
peer and the header is forwarded as received — right in development, wrong behind Caddy, where the
peer is always Caddy and every caller shares one bucket. Entries must be private, loopback,
link-local or unique-local ranges (anything else stops the process booting). `compose.prod.yml`
sets it for the edge, defaulting to the three private ranges, which trusts **every container on the
Docker networks**, not just Caddy; the runbook narrows it to Caddy's own address. See
`infrastructure/edge/RUNBOOK.md`, "Rate limiting behind Caddy".

## Headers

Inbound `X-*` identity headers are **stripped** before the gateway writes its own. Without
that, a caller could simply claim to be somebody else and the internal token the gateway
adds would vouch for it — see `SPOOFABLE` in `platform_core.gateway`. This is the single
most important behaviour in this service, and it has a test named after it.

Forwarded downstream, all lower-case so that writing one always *replaces* a
caller-supplied value rather than appending to it:

`x-user-id` · `x-org-id` · `x-membership-id` · `x-session-id` · `x-audience` · `x-roles` ·
`x-entitlements` · `x-locations` · `x-amr` · `x-permission-version` · `x-perms` ·
`x-request-id` · `x-correlation-id` · `x-internal-token`

`x-org-slug` is added on public paths only, parsed from `Host`. Only the gateway parses
`Host`; a service that trusted it directly would be trusting a client-supplied value.

## Permissions are resolved here, not carried

The token carries **no permission list**. Five products × ~40 permissions is 5–6 KB of
header — past common proxy limits, and it fails as a silent 500. Instead the gateway asks
account-service for the caller's grants and caches them under a key containing the
permission version, so a grant change invalidates every entry for that organization by
construction rather than by waiting for a TTL.

If that lookup fails the request is **refused with 503**, never forwarded. Forwarding
would hand the caller whatever a downstream service does when `x-perms` is absent, which
is not a decision any single service should be making alone.

## Routes

`routes.json`. A file rather than a constant, so launching a product is a deployment
artefact rather than an edit to the core gateway — step 2 of the "adding product N"
checklist. Longest prefix wins, so `/api/v1/employees/salary` can belong to a different
service than `/api/v1/employees`.

A route with no `product` is a platform route every customer has, whatever they bought.
