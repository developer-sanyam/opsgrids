"""Configuration for the quotation service."""

from __future__ import annotations

from platform_core.config import ServiceSettings, settings_for


class Settings(ServiceSettings):
    service_name: str = "quotation"
    db_schema: str = "quotation"
    #: Its own NOBYPASSRLS role, granted DML on the `quotation` schema and nothing else — so
    #: this service cannot read another product's tables, the whole point of the split.
    db_app_role: str | None = "opsgrids_quotation"
    db_admin_role: str | None = "opsgrids_admin"
    db_outbox_role: str | None = "opsgrids_outbox"


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
