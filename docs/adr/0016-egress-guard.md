# ADR-0016 — The egress guard: fetching a URL a tenant typed in

Status: **accepted** · Date: 2026-09-23

Builds on [ADR-0000](0000-phase-0-decisions.md) §10 (tenant isolation is a shared-schema
property, enforced server-side) and the platform-wide rule that authorization and input
validation happen at the boundary, never trusted to a caller.

## Context

`auth-service` lets an organization admin holding `core.identity.sso.write` configure OIDC
single sign-on, including a `discovery_url`. Saving that configuration fetches the URL
server-side (`services.save_sso_config` → `IdpClient.discover`), and the discovery document it
returns is trusted for `authorization_endpoint`, `token_endpoint` and `jwks_uri` — which are
then fetched server-side too, on every sign-in.

A URL an admin supplies and a service later fetches, from inside the docker `back` network, is
server-side request forgery (SSRF) waiting to be found. From `auth-service`'s container that
network reaches `http://account:8000/internal/...`, `redis:6379`, and — if this ever runs on a
cloud VM without a network policy blocking it — the instance metadata endpoint at
`169.254.169.254`. Nothing about a discovery URL needs to look suspicious to be dangerous: a
plausible-looking `https://idp.customer.example/.well-known/openid-configuration` that resolves,
at request time, to an internal address is exactly as effective as an obviously bad one, and a
schema-time hostname check cannot see that — DNS is what decides the address, and DNS is not
required to answer the same way twice (rebinding).

## Decision

`libs/platform-core/src/platform_core/egress.py` (`EgressGuard`) is the one door any service
uses to fetch a URL it did not choose. It is not wired in automatically — a caller constructs
one and calls `.request(...)` — but every current admin-supplied-URL fetch in the platform goes
through it:

- `HttpIdpClient.discover`, `.fetch_jwks` and `.exchange_code` in `services/auth/app/oidc.py`
  (discovery, JWKS, and the token endpoint a discovery document names).

A grep across the platform for `httpx`/`requests` calls against a tenant-configurable URL found
nothing else at the time of writing: every other outbound call in the codebase (account,
catalog, customer, price and target resolution, the WhatsApp Cloud transport, the gateway's own
proxying) targets a URL built from this platform's own service configuration, not from a value
an organization typed into a form. `authorization_endpoint` is used to build a redirect for the
*browser*, not fetched by this service, so it does not go through the guard — but its shape
(`https`, no userinfo) is still checked at write time, alongside the three that are fetched, in
case a future change starts resolving it server-side too.

The guard, in order:

1. **Scheme.** `https` only, refusing `http` unless `ServiceSettings.egress_allow_insecure_http`
   says otherwise — and that setting is itself refused by `model_post_init` outside
   `local`/`test`/`ci`, so it cannot leak into a real deployment.
2. **No userinfo, no bare IP host** (both refusable by name, both default-refused).
3. **Resolve once, validate the address, pin the connection to it.** The hostname is resolved a
   single time; every resolved address is checked against loopback, RFC1918, link-local
   (`169.254.169.254` lives here), carrier-grade NAT, IPv6 unique-local, multicast, unspecified,
   and the IPv4-mapped IPv6 form of any of those; and the actual HTTP connection is forced to
   the one address that passed, via a custom transport that rewrites only the connection target
   — the `Host` header and the TLS SNI still name the real hostname. This is what makes DNS
   rebinding — a public answer for the check, a private one moments later for the connect —
   structurally impossible rather than merely unlikely: there is no second lookup for a
   rebinding answer to land in.
4. **No redirects**, a response size cap, and a short timeout.

`schemas.SsoConfigIn` calls the guard's shape check (`check_url_shape`) synchronously on
`discovery_url`, `authorization_endpoint`, `token_endpoint` and `jwks_uri`, so an obviously bad
URL (wrong scheme, embedded credentials, a bare IP) is refused at 422 when it is typed, not
discovered on the next sign-in attempt. The address-class and rebinding checks need DNS and stay
at fetch time, in the guard.

**Fetched content is never echoed in an error.** `save_sso_config`'s issuer-mismatch check used
to put the discovery document's claimed issuer directly into the 422 body; it now logs that
comparison and returns a fixed message, on the same principle the rest of the SSO flow already
follows for provider responses (`oidc.py`'s module docstring).

## Consequences

- Every service that later fetches an admin-supplied URL (a webhook target, a partner metadata
  URL) should use `EgressGuard` rather than `httpx` directly. Nothing enforces this by
  construction — there is no lint rule for "this URL came from a tenant" — so it is a review
  point, not a guarantee.
- `HttpIdpClient` no longer holds one long-lived `httpx.AsyncClient`; each request opens and
  closes its own connection, because the pinned address is only valid for that one vetted
  fetch. Discovery is fetched once per save and JWKS/token-exchange once or twice per sign-in,
  so the lost connection reuse is not a meaningful cost here — a caller with a hot path should
  measure before assuming the same trade-off holds.
- `egress_allow_insecure_http` is a new `ServiceSettings` field (default `false`), documented in
  `.env.example`; it exists for a local IdP fixture with no certificate and nowhere else.

## What this does not do

- It does not vouch for the *content* fetched — a legitimate public IdP that is itself
  compromised is outside this guard's remit; `oidc.verify_id_token` is what still validates the
  token it returns.
- It does not replace the discovery-issuer check (`found != issuer`), which stays: adopting
  whatever a discovery document claims would make that check circular.

## Decisions on open questions (2026-09-24)

### Q1. "Nothing enforces this by construction — it is a review point" — **DECIDED: a classification test, landed with the next tenant-URL fetch**

**Decision.** A static test in `tests/infra/` walks every service's `app/` for code that builds
an outbound HTTP client or request: `httpx.AsyncClient(`, `httpx.Client(`, `httpx.request(` and
`requests.`. It fails unless every such site is listed in a checked-in allowlist, each entry
tagged either
- `platform-configured` — the URL comes from this platform's own settings, or
- `egress-guard` — the site calls `EgressGuard.request`.

A new call site fails CI until someone classifies it. So "did this URL come from a tenant?" is
asked once per call site, in review, with the answer written down. It is the same shape as the
network-in-transaction guard with its allowlist added by D3. The first new tenant-supplied URL on
the backlog is **Q8**'s outbound customer webhooks. They must fetch through `EgressGuard`, and Q8
lands this test.

**Why.** A lint rule cannot tell where a URL came from. A per-call-site classification can, and it
turns this ADR's "review point" into a check that is recorded and cannot be skipped.

**Rejected.** Routing every outbound call through `EgressGuard`: platform-internal calls to
`http://account:8000` would be refused as private addresses, which is the guard working correctly.
A runtime monkeypatch of `httpx`: it changes library behaviour for every call, invisibly.

**Consequences.** **Implemented with Q8** (eventbridge outbound webhooks). If Q8 slips, this test
is still the first thing any new tenant-URL fetch must land with. The register lists it.
