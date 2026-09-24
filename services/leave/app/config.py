"""Configuration for the leave service."""

from __future__ import annotations

from platform_core.config import ServiceSettings, settings_for


class Settings(ServiceSettings):
    service_name: str = "leave"
    db_schema: str = "leave"
    #: Its own NOBYPASSRLS role, granted DML on the `leave` schema and nothing else — so this
    #: service cannot read another product's tables, the whole point of the split.
    db_app_role: str | None = "opsgrids_leave"
    db_admin_role: str | None = "opsgrids_admin"
    db_outbox_role: str | None = "opsgrids_outbox"

    #: Runs this service's single event consumer (`app/consumers.py`) as a supervised background
    #: task inside the API process instead of the separate `leave-worker` container (the
    #: memory-headroom follow-up that folds every service's remaining worker in). Off by default
    #: everywhere except the edge box's own `.env`: `bin/worker` still runs `python -m
    #: app.consumers` standalone unchanged, and setting this to `false` while adding
    #: `split-workers` to `EDGE_PROFILES` is the rollback (RUNBOOK.md).
    run_consumers_in_api: bool = False


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
