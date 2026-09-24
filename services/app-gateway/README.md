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

- **Public paths** (sign-in, password reset, sign-up, the storefront buyer calls, the Engage
  unsubscribe link) — per *caller* address, `ANONYMOUS_RATE_LIMIT_PER_MINUTE` (60), and a ceiling
  over **all** of them combined, `ANONYMOUS_GLOBAL_RATE_LIMIT_PER_MINUTE` (6000; `0` disables). The
  per-address check runs first, so one address can spend at most its own allowance of the ceiling.
- **Provider webhooks** (`WEBHOOK_PUBLIC_PATHS` in `app/main.py`: Razorpay `POST`, Meta's WhatsApp
  `GET`/`POST`) — no token, exact path and method, per-address and per-path buckets
  (`WEBHOOK_RATE_LIMIT_PER_MINUTE`, `WEBHOOK_GLOBAL_RATE_LIMIT_PER_MINUTE`), a body cap set on the
  route, cookies/`Authorization`/`Origin` dropped inbound and `Set-Cookie`/`Access-Control-*`
  dropped outbound. Authenticity is the owning service's HMAC check over the raw body.
- **Everything else** needs a bearer token.

**The caller's address** is `X-Forwarded-For`, believed only when the connecting peer is inside
`TRUSTED_PROXY_CIDRS` and read from the right (the first address that is not a trusted proxy);
from any other peer the header is ignored. Unset means "trust nobody", so the limiter keys on the
socket peer — right in development, wrong behind Caddy, where the peer is always Caddy and every
caller shares one bucket. `compose.prod.yml` sets it for the edge; `0.0.0.0/0` and malformed
entries stop the process booting. See `infrastructure/edge/RUNBOOK.md`, "Rate limiting behind
Caddy".

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
