"""Configuration for the itsm service."""

from __future__ import annotations

from platform_core.config import ServiceSettings, settings_for


class Settings(ServiceSettings):
    service_name: str = "itsm"
    db_schema: str = "itsm"
    #: Its own NOBYPASSRLS role, granted DML on the `itsm` schema and nothing else — so
    #: this service cannot read another product's tables, the whole point of the split.
    db_app_role: str | None = "atmora_itsm"
    db_admin_role: str | None = "atmora_admin"
    db_outbox_role: str | None = "atmora_outbox"


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
