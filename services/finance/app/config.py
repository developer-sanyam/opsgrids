"""Configuration for the finance service."""

from __future__ import annotations

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

    #: The filing-reclaim sweep's poll interval, now that it runs inside `finance-worker`
    #: (`app/worker.py`) rather than a container of its own.
    filing_reclaim_poll_interval_seconds: float = 60.0


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
