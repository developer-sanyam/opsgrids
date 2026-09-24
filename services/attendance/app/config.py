"""Configuration for the attendance service."""

from __future__ import annotations

from platform_core.config import ServiceSettings, settings_for


class Settings(ServiceSettings):
    service_name: str = "attendance"
    db_schema: str = "attendance"
    #: Its own NOBYPASSRLS role, granted DML on the `attendance` schema and nothing else — so
    #: this service cannot read another product's tables, the whole point of the split.
    db_app_role: str | None = "opsgrids_attendance"
    db_admin_role: str | None = "opsgrids_admin"
    db_outbox_role: str | None = "opsgrids_outbox"

    #: Where employee-service answers the TEAM backfill's internal hierarchy read (ADR-0006
    #: §2.1). Service-token authenticated, never reachable from outside the cluster, and read by
    #: the one-shot `app.backfill` command only — never on a request path.
    employee_service_url: str = "http://localhost:8120"


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
