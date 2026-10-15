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

    #: Attendance days roll over at local midnight in this zone. shortcut: one zone for every
    #: organization (the org's own timezone lives in account's schema); make it per-org when a
    #: non-India customer needs it. Matches account's organization default.
    org_timezone: str = "Asia/Kolkata"

    #: Runs this service's single event consumer (`app/consumers.py`) as a supervised background
    #: task inside the API process instead of the separate `attendance-worker` container (the
    #: memory-headroom follow-up that folds every service's remaining worker in). Off by default
    #: everywhere except the edge box's own `.env`: `bin/worker` still runs `python -m
    #: app.consumers` standalone unchanged, and setting this to `false` while adding
    #: `split-workers` to `EDGE_PROFILES` is the rollback (RUNBOOK.md).
    run_consumers_in_api: bool = False


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
