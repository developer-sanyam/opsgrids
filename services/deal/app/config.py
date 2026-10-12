"""Configuration for the deal service."""

from __future__ import annotations

from typing import Literal

from pydantic import field_validator

from platform_core.config import ServiceSettings, settings_for
from platform_core.logging import get_logger

CleanupMode = Literal["off", "log_only", "on"]


class Settings(ServiceSettings):
    service_name: str = "deal"
    db_schema: str = "deal"
    #: Its own NOBYPASSRLS role, granted DML on the `deal` schema and nothing else — so
    #: this service cannot read another product's tables, the whole point of the split.
    db_app_role: str | None = "opsgrids_deal"
    db_admin_role: str | None = "opsgrids_admin"
    db_outbox_role: str | None = "opsgrids_outbox"

    #: Runs this service's event consumer (`app/consumers.py`) as a supervised background task
    #: inside the API process instead of a separate `deal-worker` container. Off by default
    #: everywhere except the edge box, whose compose sets it true for every service: so the
    #: consumer starts there at deploy, which is why the cleanup mode below defaults to
    #: `log_only`. `bin/worker` still runs `python -m app.consumers` standalone (the rollback).
    run_consumers_in_api: bool = False

    #: What the `crm.lead.conversion_aborted` handler does with an aborted attempt's orphan deal:
    #: `off` acknowledges and ignores it, `log_only` (the default) evaluates and logs the decision
    #: and writes nothing, `on` soft-deletes a provably untouched orphan. Only the exact word `on`
    #: arms the deletion; anything unrecognised reads as `log_only` (`_parse_cleanup_mode`).
    crm_conversion_cleanup_mode: CleanupMode = "log_only"

    @field_validator("crm_conversion_cleanup_mode", mode="before")
    @classmethod
    def _parse_cleanup_mode(cls, v: object) -> object:
        return parse_cleanup_mode(v)


def parse_cleanup_mode(v: object) -> CleanupMode:
    """Fail safe: `off` and `log_only` are read case-insensitively, the destructive `on` only in
    exact lower case, and anything else (a typo, `ON`) becomes `log_only` with one error line, so
    a slip never arms deletion. Kept equal to customer's copy by each service's own tests."""
    raw = v.strip() if isinstance(v, str) else ""
    if raw == "on":
        return "on"
    if raw.lower() in ("off", "log_only"):
        return "off" if raw.lower() == "off" else "log_only"
    get_logger(__name__).error("crm.conversion_cleanup.mode_unknown", fallback="log_only")
    return "log_only"


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
