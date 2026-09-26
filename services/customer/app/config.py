"""Configuration for the customer service."""

from __future__ import annotations

from platform_core.config import ServiceSettings, settings_for


class Settings(ServiceSettings):
    service_name: str = "customer"
    db_schema: str = "customer"
    #: Its own NOBYPASSRLS role, granted DML on the `customer` schema and nothing else — so
    #: this service cannot read another product's tables, the whole point of the split.
    db_app_role: str | None = "opsgrids_customer"
    db_admin_role: str | None = "opsgrids_admin"
    db_outbox_role: str | None = "opsgrids_outbox"

    #: Runs this service's single event consumer (`app/consumers.py`) as a supervised background
    #: task inside the API process instead of the separate `customer-worker` container (the
    #: memory-headroom follow-up that folds every service's remaining worker in). Off by default
    #: everywhere except the edge box's own `.env`: `bin/worker` still runs `python -m
    #: app.consumers` standalone unchanged, and setting this to `false` while adding
    #: `split-workers` to `EDGE_PROFILES` is the rollback (RUNBOOK.md).
    run_consumers_in_api: bool = False

    #: The customer 360 (`GET /customers/{id}/360`) asks these services for its sections. Neither
    #: is required at boot: an unreachable upstream makes that section `null` and lists it in
    #: `unavailable`, it never fails the request.
    notification_internal_url: str = "http://localhost:8151"
    deal_internal_url: str = "http://localhost:8131"
    #: Per upstream call, and also the ceiling on the whole fan-out since the calls run
    #: concurrently. Short on purpose: a card with a hole beats a card that hangs.
    customer360_upstream_timeout_seconds: float = 2.0


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
