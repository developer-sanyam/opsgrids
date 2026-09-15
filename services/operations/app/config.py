"""Configuration for the operations service."""

from __future__ import annotations

from platform_core.config import ServiceSettings, settings_for


class Settings(ServiceSettings):
    service_name: str = "operations"
    db_schema: str = "operations"
    #: Its own NOBYPASSRLS role, granted DML on the `operations` schema and nothing else — so
    #: this service cannot read another product's tables, the whole point of the split.
    db_app_role: str | None = "opsgrids_operations"
    db_admin_role: str | None = "opsgrids_admin"
    db_outbox_role: str | None = "opsgrids_outbox"


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
