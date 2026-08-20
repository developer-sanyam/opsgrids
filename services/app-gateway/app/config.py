"""Configuration for the product gateway.

`db_schema` is set to `public` only to satisfy the base settings — **this service owns no
tables and never opens a database connection.** Everything it needs is in the token or in
Redis.
"""

from __future__ import annotations

from platform_core.config import ServiceSettings, settings_for


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
    upstream_timeout_seconds: float = 30.0


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
