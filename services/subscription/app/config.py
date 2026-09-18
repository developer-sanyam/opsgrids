"""Configuration for the subscription service."""

from __future__ import annotations

from platform_core.config import ServiceSettings, settings_for


class Settings(ServiceSettings):
    service_name: str = "subscription"
    db_schema: str = "subscription"
    #: Its own NOBYPASSRLS role, granted DML on the `subscription` schema and nothing else.
    #: Every table here is tenant-scoped, so the API must connect as a role RLS can bind
    #: (ADR-0002 finding 2) — and the projection consumer downstream in product-service relies
    #: on the same discipline: a write only passes the tenant policy when the GUC is set.
    db_app_role: str | None = "opsgrids_subscription"
    db_admin_role: str | None = "opsgrids_admin"
    #: The outbox relay's role: BYPASSRLS, SELECT+UPDATE on `outbox` only. subscription emits
    #: the `commerce.subscription.*` lifecycle stream that product-service projects to Activation.
    db_outbox_role: str | None = "opsgrids_outbox"

    #: Where the catalog service answers the pin lookup. A subscribe/change-plan resolves its
    #: `plan_version_id` against catalog over the service-token internal API before pinning it —
    #: catalog is the authority for whether a version exists, is published and is visible here.
    catalog_service_url: str = "http://localhost:8164"


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
