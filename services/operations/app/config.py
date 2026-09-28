"""Configuration for the operations service."""

from __future__ import annotations

from platform_core.config import ServiceSettings, settings_for


class Settings(ServiceSettings):
    service_name: str = "operations"
    db_schema: str = "operations"
    #: Its own NOBYPASSRLS role, granted DML on the `operations` schema and nothing else — so
    #: this service cannot read another product's tables, the whole point of the split.
    db_app_role: str | None = "opsgrids_operations"
    db_admin_role: str | None = "opsgrids_admin"
    db_outbox_role: str | None = "opsgrids_outbox"
    #: How often `bin/cron` reconciles every organization's ledger against its balances.
    reconcile_interval_seconds: float = 86_400
    #: Runs the reconcile sweep as a supervised background task inside the API process itself
    #: instead of `operations-cron`'s own container (the memory-headroom slice's fix round 2:
    #: a background process costs about as much as an API one — see memory-budget.json's own
    #: `kinds.background` note — so three standalone sweep containers with no worker to merge
    #: into were pure overhead once that was measured). Off by default everywhere except the
    #: edge box's own `.env`: `bin/cron` still runs it standalone for local debugging, and a
    #: second copy running in both places at once would double-reconcile (safe, since
    #: `reconcile_organization` is read-mostly and idempotent per run — see `app/main.py`'s own
    #: comment — but pointless). The edge runs exactly one `operations` API replica, so this
    #: flag being on there is never a double-run either.
    run_sweeps_in_api: bool = False
    #: Runs this service's event consumers (`app/consumers.py` — the fulfilment offer-expiry
    #: sweep and the order-cancellation cascade, ADR-0025 §3/§1) as a supervised background task
    #: inside the API process, the same worker-folding shape eleven other services already use
    #: (MAJOR 2, security review: `operations` shipped F3's two consumers with no container and
    #: no in-API flag to run them, so neither ever fired in production regardless of how correct
    #: the handler code was). Off by default everywhere except the edge box's own `.env`:
    #: `bin/worker` (`python -m app.consumers`) still runs standalone unchanged, and setting this
    #: to `false` while adding `split-workers` to `EDGE_PROFILES` is the rollback (RUNBOOK.md).
    run_consumers_in_api: bool = False


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
