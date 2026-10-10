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

Two kinds, each with its own buckets (Redis, one-minute windows, failing closed); everything else
needs a bearer token.

- **Public paths** (sign-in, password reset, sign-up, the storefront buyer calls, the Engage
  unsubscribe link) — per address, `ANONYMOUS_RATE_LIMIT_PER_MINUTE` (60).
- **Provider webhooks** (`WEBHOOK_PUBLIC_PATHS` in `app/main.py`: Meta's WhatsApp callback,
  `/api/v1/webhooks/whatsapp`, `GET` and `POST`) — no token, **exact path and method** (a `PUT` or
  `DELETE` falls through to the bearer check, a sub-path or `..` walk is a different path), and
  their own buckets: per address, `WEBHOOK_RATE_LIMIT_PER_MINUTE` (600), and per path across all
  addresses, `WEBHOOK_GLOBAL_RATE_LIMIT_PER_MINUTE` (3000; `0` disables), so a sign-in flood and a
  webhook flood cannot starve each other. The per-address check runs first, so one address spends
  at most its own allowance of the shared one. Cookies, `Authorization`, `Origin` and identity
  headers are dropped on the way in, `Set-Cookie` and `Access-Control-*` on the way out, and no
  `x-org-slug` is derived from the `Host`. Authenticity is notification's HMAC over the raw body,
  which is forwarded untouched under the route's own `max_body_bytes` (3 MiB, in both route
  tables). Razorpay's `/api/v1/webhooks/razorpay` is routed in production but is **not** public
  here; that is a separate decision.

`tests/infra/test_route_table_parity.py` holds the policy, both route tables, the body cap and
notification's mount together: add a webhook route without deciding, and the build fails.

## Headers

Inbound `X-*` identity headers are **stripped** before the gateway writes its own. Without
that, a caller could simply claim to be somebody else and the internal token the gateway
adds would vouch for it — see `SPOOFABLE` in `platform_core.gateway`. This is the single
most important behaviour in this service, and it has a test named after it.

Forwarded downstream, all lower-case so that writing one always *replaces* a
caller-supplied value rather than appending to it:

`x-user-id` · `x-org-id` · `x-membership-id` · `x-session-id` · `x-audience` · `x-roles` ·
`x-entitlements` · `x-locations` · `x-amr` · `x-permission-version` · `x-perms` ·
`x-client-ip` · `x-request-id` · `x-correlation-id` · `x-internal-token`

`x-org-slug` is added on public paths only, parsed from `Host`. Only the gateway parses
`Host`; a service that trusted it directly would be trusting a client-supplied value.

`x-internal-token` is a 60-second hop token that names the forwarded `uid`/`org` and binds a
SHA-256 of every identity header above plus `x-client-ip` (`FORWARDED_HEADERS`), so a captured
token cannot be replayed with an escalated `x-perms` or `x-audience`. Public paths carry one
too, naming nobody, so the visitor's address (`x-client-ip`, resolved from the trusted proxy's
`X-Forwarded-For`) is believable for an anonymous request such as a password reset.

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
