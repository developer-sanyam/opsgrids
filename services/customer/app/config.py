"""Configuration for the customer service."""

from __future__ import annotations

from typing import Literal

from pydantic import field_validator

from platform_core.config import ServiceSettings, settings_for
from platform_core.logging import get_logger

CleanupMode = Literal["off", "log_only", "on"]


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
    #: The aborted-conversion cleanup's references probe (`clients.deal_references`). Longer than
    #: the 360's: it runs on a consumer, not under a person waiting, and a timeout redelivers.
    deal_timeout_seconds: float = 3.0

    #: What the `crm.lead.conversion_aborted` handler does with an aborted attempt's orphan contact
    #: and company: `off` acknowledges and ignores it, `log_only` (the default) evaluates, asks
    #: deal's references probe and logs the decision, writing nothing; `on` soft-deletes records
    #: provably created by that attempt and untouched since. The edge box runs this consumer in the
    #: API process, so it starts at deploy: only the exact word `on` arms the deletion.
    crm_conversion_cleanup_mode: CleanupMode = "log_only"

    @field_validator("crm_conversion_cleanup_mode", mode="before")
    @classmethod
    def _parse_cleanup_mode(cls, v: object) -> object:
        return parse_cleanup_mode(v)

    #: How many real phone numbers / emails one user may reveal in one organization
    #: (`services.enforce_reveal_budget`): `reveal_burst_limit` per `reveal_burst_window_seconds`
    #: and `reveal_daily_limit` per UTC day. With no step-up in front of a reveal (ADR-0023
    #: addendum), without this a single session could open every contact the gateway's own 300
    #: requests a minute allowed. The budget is shared with lead-service's lead reveal (same
    #: Redis keys): it bounds how much PII one person can read, not how often one route is
    #: called. Raise it only with a reason.
    #: Sized for a rep working a call list (owner decision 2026-10-15, ADR-0023 addendum): about 200
    #: records a day, each with a phone and possibly an email, in bursts of ~30 records in ten
    #: minutes, with headroom for contact and lead reveals sharing the counters. It still stops a
    #: scrape: 60 per ten minutes and 600 per day is nowhere near a bulk export, and every reveal
    #: writes an audit row. The Redis key names are unchanged; only these limits moved (they are
    #: script arguments, not part of the key).
    reveal_burst_limit: int = 60
    reveal_burst_window_seconds: int = 600
    reveal_daily_limit: int = 600


def parse_cleanup_mode(v: object) -> CleanupMode:
    """Fail safe: `off` and `log_only` are read case-insensitively, the destructive `on` only in
    exact lower case, and anything else (a typo, `ON`) becomes `log_only` with one error line, so
    a slip never arms deletion. Kept equal to deal's copy by each service's own tests."""
    raw = v.strip() if isinstance(v, str) else ""
    if raw == "on":
        return "on"
    if raw.lower() in ("off", "log_only"):
        return "off" if raw.lower() == "off" else "log_only"
    get_logger(__name__).error("crm.conversion_cleanup.mode_unknown", fallback="log_only")
    return "log_only"


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
