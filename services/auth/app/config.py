"""Configuration for the auth service."""

from __future__ import annotations

from platform_core.config import ServiceSettings, settings_for


class Settings(ServiceSettings):
    service_name: str = "auth"
    db_schema: str = "auth"
    db_app_role: str | None = "atmora_auth"
    db_admin_role: str | None = "atmora_admin"
    db_outbox_role: str | None = "atmora_outbox"

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

    # --- sessions -----------------------------------------------------------------
    session_ttl_days: int = 30
    #: Carries the refresh token: httpOnly, path-scoped to the auth routes, and Secure
    #: outside development. Never readable by JavaScript, which is the whole point.
    refresh_cookie_name: str = "atmora_refresh"
    refresh_cookie_domain: str | None = None


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
