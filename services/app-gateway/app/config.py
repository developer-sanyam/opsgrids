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

    def model_post_init(self, _: object) -> None:
        """MINOR 2 of the 2026-09-24 SSO review: `auth_refresh_cors_origins` left empty on this,
        the one gateway that fronts `app.opsgrids.com`, does not fail loudly — every product
        SPA's cross-origin `/auth/refresh` and `/auth/logout` call just gets refused by CORS at
        the browser, which surfaces client-side as `refreshAccessToken()`'s generic `"error"`
        outcome, never a 403 or a log line naming the cause. A box brought up this way looks
        healthy — every same-origin call on `app.opsgrids.com` itself works fine — right up
        until someone's session on another app host stops surviving a token expiry.

        Refused in local/test/ci too if it is explicitly set to something invalid (the shape
        checks live on the shared field itself, `platform_core.config`), but only *required*
        in a real deployment: a dev box with one app running same-origin has no product SPA
        host that needs the allowance yet.
        """
        super().model_post_init(_)
        real_deployment = self.environment in ("production", "staging", "uat")
        if real_deployment and not self.auth_refresh_cors_origins:
            raise ValueError(
                "auth_refresh_cors_origins must be set in production, staging and uat: "
                "app-gateway is the one gateway product SPAs reach for a cross-origin "
                "/auth/refresh and /auth/logout, and an empty list silently refuses every "
                "one of those calls at the browser's own CORS check."
            )


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
