"""Configuration for the scheduler service."""

from __future__ import annotations

from platform_core.config import ServiceSettings, settings_for


class Settings(ServiceSettings):
    service_name: str = "scheduler"
    db_schema: str = "scheduler"
    #: Its own NOBYPASSRLS role, granted DML on the `scheduler` schema and nothing else — so
    #: this service cannot read another product's tables, the whole point of the split.
    db_app_role: str | None = "opsgrids_scheduler"
    db_admin_role: str | None = "opsgrids_admin"
    db_outbox_role: str | None = "opsgrids_outbox"
    #: How long a `fired` row is kept before the retention pass deletes it (ADR-0015 §7). A
    #: setting rather than the module constant it started as, so an operator who needs a
    #: longer support-lookback window (or a shorter one, to shrink the table faster) changes
    #: an environment variable, not code.
    scheduled_job_retention_days: int = 7


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
