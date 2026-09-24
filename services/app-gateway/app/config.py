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
from platform_core.gateway import parse_trusted_proxies


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

    rate_limit_per_minute: int = 300
    #: Sign-in and password reset are reachable without a token, so they are limited by
    #: address and much harder.
    anonymous_rate_limit_per_minute: int = 60
    #: A ceiling over **every** unauthenticated request together, on top of the per-address
    #: limit above (`0` disables it). The per-address limit stops one address; this stops a
    #: crowd of them, each politely inside its own allowance, from guessing passwords and codes
    #: at the one surface anybody can reach. Sized above what this deployment's real anonymous
    #: traffic (sign-ins, sign-ups, storefront buyers) reaches and below what the box can serve;
    #: a 429 from it names itself as the shared limit, with a `Retry-After`. Tune it against the
    #: `anon-global` counter, not by guessing.
    anonymous_global_rate_limit_per_minute: int = 6000
    #: Provider webhooks (Razorpay, Meta) are counted in their own buckets, per address and per
    #: path, so neither a sign-in flood nor a webhook flood starves the other. Generous by
    #: design: a provider delivers from a handful of addresses, bursts, and retries on a 429 —
    #: a limit that bites turns into redelivery, not into a lost event.
    webhook_rate_limit_per_minute: int = 600
    webhook_global_rate_limit_per_minute: int = 3000
    upstream_timeout_seconds: float = 30.0

    #: The reverse proxy (Caddy) in front of this gateway: addresses or CIDR ranges whose
    #: `X-Forwarded-For` is believed. Comma-separated in the environment.
    #:
    #: **Empty by default, and empty means "trust nobody"**: the rate limit then keys on the
    #: socket peer, which is correct when clients connect directly (development, tests) and
    #: wrong behind a proxy, where the peer is always the proxy and every anonymous caller in
    #: the world shares one bucket. `compose.prod.yml` sets it for the edge. Read only by this
    #: service — the other services see the gateway, not the caller, whatever their own
    #: settings say. `parse_trusted_proxies` refuses a malformed entry and a catch-all
    #: (`0.0.0.0/0`) at boot; see `platform_core.gateway.client_limiter_key` for how the header
    #: is read.
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
