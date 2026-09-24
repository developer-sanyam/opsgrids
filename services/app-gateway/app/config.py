"""Configuration for the product gateway.

`db_schema` is set to `public` only to satisfy the base settings — **this service owns no
tables and never opens a database connection.** Everything it needs is in the token or in
Redis.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import NoDecode

from platform_core.config import ServiceSettings, settings_for
from platform_core.gateway import parse_provider_cidrs, parse_trusted_proxies


class Settings(ServiceSettings):
    service_name: str = "app-gateway"
    db_schema: str = "public"

    #: Where the route table lives. A file rather than a constant, so launching a product
    #: is a deployment artefact rather than an edit to the core gateway.
    routes_file: str = "routes.json"

    #: Tokens must name this audience. One minted for the operator console is not accepted
    #: here, and vice versa.
    audience: str = "app"

    #: Resolving permissions per request is what lets tokens stay small and lets a
    #: revocation take effect on the next call rather than at token expiry.
    account_service_url: str = "http://localhost:8111"

    #: Every limit below is bounds-checked, at boot. A per-address limit of `0` is not "off", it
    #: refuses every request (the counter is incremented first, and one is over zero), so those
    #: are `ge=1`; the shared ceilings are `ge=0` because `0` is documented there as "no ceiling".
    #: An *empty* variable is "not configured" (`env_ignore_empty`), which is how
    #: `compose.prod.yml` passes a knob the box's `.env` does not set — it falls back to the
    #: default here and does not disable anything.
    rate_limit_per_minute: int = Field(300, ge=1)
    #: Sign-in and password reset are reachable without a token, so they are limited by
    #: address and much harder.
    anonymous_rate_limit_per_minute: int = Field(60, ge=1)
    #: A ceiling over the **credential** endpoints together — sign-in, one-time codes, password
    #: reset, sign-up, accepting an invitation (`CREDENTIAL_PUBLIC_PATHS` in `app/main.py`) — on
    #: top of the per-address limit above (`0` disables it). The per-address limit stops one
    #: address; this stops a crowd of them, each politely inside its own allowance, from guessing
    #: passwords and codes. A 429 from it names itself as the shared limit, with a `Retry-After`.
    #: Tune it against the `anon-global` counter, not by guessing.
    anonymous_global_rate_limit_per_minute: int = Field(6000, ge=0)
    #: The same, for every other public path (a published catalogue, an unsubscribe link, the token
    #: refresh): a separate bucket, `anon-content-global`, and ten times the size, because nothing
    #: there can be guessed and a busy day of real buyers is legitimate. Sharing one ceiling let
    #: about a hundred storefront visitors lock every merchant out of signing in. `0` disables it.
    anonymous_content_global_rate_limit_per_minute: int = Field(60000, ge=0)
    #: Provider webhooks (Razorpay, Meta) have two limits, both per (method, path, address).
    #: `webhook_failure_limit` is read before a delivery is forwarded and spent only when the
    #: service answers 400/401/403, or the gateway itself refuses the body with 408/413 — a
    #: delivery that works, that fails on our side, or that the service answers 422 (an authentic
    #: event it cannot yet attribute) costs nothing. 600, not round one's 60: a provider's egress
    #: address is shared with every other merchant on it, so the bar for "hostile" has to be
    #: higher than for any other unauthenticated caller — a third party can point their own Meta
    #: app or Razorpay webhook at our URL and make either provider send a failing request from an
    #: address our own merchants also deliver from. `webhook_provider_cidrs` exempts a known
    #: provider range from this one entirely.
    webhook_failure_limit_per_minute: int = Field(600, ge=1)
    #: Every request to a webhook path, per (method, path, address), regardless of outcome —
    #: restored per-address only, never a bucket shared across addresses (that shape is exactly
    #: what let five hostile addresses starve every other one before the failure limit existed).
    #: Applies even inside `webhook_provider_cidrs`.
    webhook_arrival_limit_per_minute: int = Field(600, ge=1)
    #: Public ranges a provider's own deliveries are known to originate from — addresses inside
    #: are exempt from `webhook_failure_limit_per_minute`'s lockout (never from the arrival limit
    #: above). Comma-separated CIDRs, refused at boot if malformed or wider than `/8` (IPv4) /
    #: `/24` (IPv6) — a sanity bound on how much of the internet one typo can exempt, not an
    #: allow-list of *where* the range may be (a provider's range is public by definition).
    #:
    #: **Empty by default, deliberately.** Razorpay's and Meta's current published ranges are not
    #: guessed into this repository — they change on the providers' own schedule, silently and
    #: with nothing here to notice, and shipping a stale guess would be worse than shipping none.
    #: Filling this in from the providers' own current documentation is the deploying owner's job
    #: (RUNBOOK, "Provider webhooks"); an edge allow-list of the same ranges at Caddy or the
    #: provider-side firewall is the stronger version of the same idea, and is also the owner's.
    webhook_provider_cidrs: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("webhook_provider_cidrs", mode="before")
    @classmethod
    def _split_and_validate_provider_cidrs(cls, v: object) -> object:
        items = [item.strip() for item in v.split(",") if item.strip()] if isinstance(v, str) else v
        if isinstance(items, list):
            parse_provider_cidrs(str(item) for item in items)
        return items

    #: How long an unauthenticated request (a webhook or a public path) may take to send its body
    #: before it is abandoned with a 408 and what it sent is released.
    unauth_body_read_timeout_seconds: float = Field(10.0, gt=0)
    #: How many unauthenticated requests one address may have in flight, and how many the process
    #: will hold in total; past either, 503 with `Retry-After: 5`. Buffered memory is bounded by
    #: the global figure times the largest body cap (3 MiB on the WhatsApp webhook).
    unauth_body_max_concurrent_per_address: int = Field(4, ge=1)
    unauth_body_max_concurrent_global: int = Field(32, ge=1)
    upstream_timeout_seconds: float = 30.0

    #: The reverse proxy (Caddy) in front of this gateway: addresses or CIDR ranges whose
    #: `X-Forwarded-For` is believed. Comma-separated in the environment.
    #:
    #: **Empty by default, and empty means "trust nobody"**: the rate limit then keys on the
    #: socket peer, which is correct when clients connect directly (development, tests) and
    #: wrong behind a proxy, where the peer is always the proxy and every anonymous caller in
    #: the world shares one bucket. `compose.prod.yml` sets it for the edge — and there an empty
    #: `.env` value becomes the RFC 1918 default rather than "nobody" (the service default
    #: applies only when the variable is absent altogether). Read only by this service — the other
    #: services see the gateway, not the caller, whatever their own settings say.
    #: `parse_trusted_proxies` refuses a malformed entry and anything outside private, loopback,
    #: link-local and unique-local space at boot. **Trusting a range trusts every host in it**:
    #: see `platform_core.gateway._resolve_client` for how the header is read and what that means.
    trusted_proxy_cidrs: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("trusted_proxy_cidrs", mode="before")
    @classmethod
    def _split_and_validate_cidrs(cls, v: object) -> object:
        # NoDecode is load-bearing here for the same reason as `cors_origins`: without it the
        # dotenv source tries to JSON-decode the list before this validator runs.
        items = [item.strip() for item in v.split(",") if item.strip()] if isinstance(v, str) else v
        if isinstance(items, list):
            # Validated for its error; the parsed networks are built once, in `app.main`.
            parse_trusted_proxies(str(item) for item in items)
        return items


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
