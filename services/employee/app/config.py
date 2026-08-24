"""Configuration for the employee service."""

from __future__ import annotations

from platform_core.config import ServiceSettings, settings_for


class Settings(ServiceSettings):
    service_name: str = "employee"
    db_schema: str = "employee"
    #: Its own NOBYPASSRLS role, granted DML on the `employee` schema and nothing else — so
    #: this service cannot read another product's tables, the whole point of the split.
    db_app_role: str | None = "atmora_employee"
    db_admin_role: str | None = "atmora_admin"
    db_outbox_role: str | None = "atmora_outbox"


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
