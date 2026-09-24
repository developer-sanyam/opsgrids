"""Configuration for the finance service."""

from __future__ import annotations

from pydantic import model_validator

from app.domain import FILING_RECLAIM_AFTER
from platform_core.config import ServiceSettings, settings_for


class Settings(ServiceSettings):
    service_name: str = "finance"
    db_schema: str = "finance"
    #: Its own NOBYPASSRLS role, granted DML on the `finance` schema and nothing else — so
    #: this service cannot read another product's tables, the whole point of the split.
    db_app_role: str | None = "opsgrids_finance"
    db_admin_role: str | None = "opsgrids_admin"
    db_outbox_role: str | None = "opsgrids_outbox"

    #: How long one synchronous compliance-portal call (IRP, e-Way, IMS) may take before the
    #: caller stops waiting for it (D3). The call runs on a worker thread
    #: (`services.off_loop`), never on the event loop; a call that outlives this is treated as an
    #: outage — the claim stands and the reclaim sweep finishes it — and the thread is left to
    #: finish on its own, because a thread cannot be cancelled. Comfortably under the reclaim
    #: sweep's own `FILING_RECLAIM_AFTER`, so a timed-out call is never still running when the
    #: sweep decides its claim is stuck.
    portal_call_timeout_seconds: float = 60.0

    @model_validator(mode="after")
    def _claims_outlive_the_call(self) -> Settings:
        """A claim becomes re-drivable `FILING_RECLAIM_AFTER` after it was made; the portal call
        it covers must be long over by then, twice over plus a minute — otherwise an abandoned
        call can still be answering when the sweep re-drives the same claim (D3)."""
        floor = 2 * self.portal_call_timeout_seconds + 60
        if FILING_RECLAIM_AFTER.total_seconds() < floor:
            raise ValueError(
                "portal_call_timeout_seconds is too long for the reclaim sweep: "
                f"2 * {self.portal_call_timeout_seconds:g} + 60 exceeds FILING_RECLAIM_AFTER "
                f"({FILING_RECLAIM_AFTER.total_seconds():g}s)"
            )
        return self

    #: The filing-reclaim sweep's poll interval, now that it runs inside `finance-worker`
    #: (`app/worker.py`) rather than a container of its own.
    filing_reclaim_poll_interval_seconds: float = 60.0

    #: Runs this service's worker loops — the filing consumer and the filing-reclaim sweep
    #: (`app/worker.py`) — as supervised background tasks inside the API process instead of the
    #: separate `finance-worker` container (the memory-headroom follow-up that folds every
    #: service's remaining worker in). Off by default everywhere except the edge box's own
    #: `.env`: `bin/worker` still runs `app.worker` standalone unchanged, and setting this to
    #: `false` while adding `split-workers` to `EDGE_PROFILES` is the rollback (RUNBOOK.md).
    run_consumers_in_api: bool = False


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
