"""Configuration for the activity service."""

from __future__ import annotations

from platform_core.config import ServiceSettings, settings_for


class Settings(ServiceSettings):
    service_name: str = "activity"
    db_schema: str = "activity"
    #: Its own NOBYPASSRLS role, granted DML on the `activity` schema and nothing else — so
    #: this service cannot read another product's tables, the whole point of the split.
    db_app_role: str | None = "opsgrids_activity"
    db_admin_role: str | None = "opsgrids_admin"
    db_outbox_role: str | None = "opsgrids_outbox"


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
