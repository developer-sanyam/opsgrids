"""Configuration for the catalog service."""

from __future__ import annotations

from platform_core.config import ServiceSettings, settings_for


class Settings(ServiceSettings):
    service_name: str = "catalog"
    db_schema: str = "catalog"
    #: Its own NOBYPASSRLS role, granted DML on the `catalog` schema and nothing else. The
    #: definitional tables are global and un-policied, but `audit_logs` and `outbox` carry a
    #: tenant policy, so the API must still connect as a role RLS can bind (ADR-0002 finding 2).
    db_app_role: str | None = "atmora_catalog"
    db_admin_role: str | None = "atmora_admin"
    #: The outbox relay's role: BYPASSRLS, SELECT+UPDATE on `outbox` only. Kept from the
    #: scaffold — catalog emits `catalog.plan_version.published` and `catalog.plan.retired`.
    db_outbox_role: str | None = "atmora_outbox"


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
