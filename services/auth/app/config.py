"""Configuration for the auth service."""

from __future__ import annotations

from platform_core.config import ServiceSettings, settings_for


class Settings(ServiceSettings):
    service_name: str = "auth"
    db_schema: str = "auth"
    db_app_role: str | None = "opsgrids_auth"
    db_admin_role: str | None = "opsgrids_admin"
    db_outbox_role: str | None = "opsgrids_outbox"

    #: Where account-service lives. This service holds credentials; the directory — who
    #: exists and which organizations they belong to — is asked for over the internal API
    #: rather than read across a schema boundary.
    account_service_url: str = "http://localhost:8111"
    #: Where product-service lives. Its answer becomes the `ent` claim in every token, and
    #: one hop later the difference between a 200 and a 402 at the gateway.
    product_service_url: str = "http://localhost:8112"

    # --- lockout ------------------------------------------------------------------
    #: Enough attempts that a mistyped password is not punished, few enough that online
    #: guessing is hopeless. The delay is the defence; the count is just the trigger.
    max_failed_attempts: int = 5
    lockout_minutes: int = 15
    password_min_length: int = 10

    # --- resets -------------------------------------------------------------------
    password_reset_ttl_minutes: int = 30

    # --- single sign-on -----------------------------------------------------------
    #: Where the identity provider sends the browser back. **Configuration, never a
    #: parameter.** Taking a redirect target from the request — even one that "looks like
    #: ours" — is how an authorization code ends up posted to somebody else's host, and it
    #: is also the value the token exchange must repeat verbatim, so there is exactly one
    #: place it may come from.
    sso_redirect_uri: str = "http://localhost:8100/api/v1/auth/sso/callback"
    #: How long an authorization request stays open. Long enough for a person to type a
    #: password and answer an MFA prompt at the provider, short enough that a state value
    #: captured from a browser's history is already dead. The state is single-use as well —
    #: this is the ceiling, not the defence.
    sso_state_ttl_seconds: int = 600
    #: Tolerance for clock skew when validating an ID token's `exp`/`iat`. Small: the
    #: provider and this service are both NTP-disciplined, and a generous window is a
    #: replay window.
    sso_clock_skew_seconds: int = 60

    # --- sessions -----------------------------------------------------------------
    session_ttl_days: int = 30
    #: Carries the refresh token: httpOnly, path-scoped to the auth routes, and Secure
    #: outside development. Never readable by JavaScript, which is the whole point.
    refresh_cookie_name: str = "opsgrids_refresh"
    refresh_cookie_domain: str | None = None


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
