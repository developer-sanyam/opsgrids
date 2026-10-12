"""Configuration for the operations service."""

from __future__ import annotations

from typing import Literal

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
    #: How often `bin/cron` checks every organization for low stock, a delayed pick task or a
    #: delayed shipment (fulfilment plan §4; F12) — far more often than the nightly reconciliation,
    #: since these are conditions an operator should hear about within minutes.
    alert_interval_seconds: float = 900
    #: How often the job-work overdue sweep (Inventory plan Phase 6, `app.jobwork.sweep`) looks for
    #: orders past their due date with material still at the vendor. Each order is announced once
    #: (`overdue_notified_at`), so this only bounds how late the announcement can be.
    jobwork_overdue_interval_seconds: float = 3600
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
    #: An MRP run over more open demand lines than this is queued for the worker sweep
    #: (`app.mrp.sweep`) instead of running inside the request (inventory plan Phase 7).
    mrp_sync_max_demands: int = 200
    #: How often the MRP worker sweep looks for queued runs.
    mrp_sweep_interval_seconds: float = 30
    #: Runs this service's event consumers (`app/consumers.py` — the fulfilment offer-expiry
    #: sweep and the order-cancellation cascade, ADR-0025 §3/§1) as a supervised background task
    #: inside the API process, the same worker-folding shape eleven other services already use
    #: (MAJOR 2, security review: `operations` shipped F3's two consumers with no container and
    #: no in-API flag to run them, so neither ever fired in production regardless of how correct
    #: the handler code was). Off by default everywhere except the edge box's own `.env`:
    #: `bin/worker` (`python -m app.consumers`) still runs standalone unchanged, and setting this
    #: to `false` while adding `split-workers` to `EDGE_PROFILES` is the rollback (RUNBOOK.md).
    run_consumers_in_api: bool = False
    #: How often `bin/cron` (or the in-API fold, `run_sweeps_in_api`) polls every connected
    #: channel for new orders and pushes moved inventory (Fulfilment plan F11,
    #: `app.channels.sweep.ChannelSyncSweep`). Short relative to the daily reconcile sweep — a
    #: real channel integration is polled on the order of minutes, not once a day.
    channel_sync_interval_seconds: float = 300
    #: How often the reservation-expiry sweep (inventory Phase 3,
    #: `app.movements.sweep.ReservationExpirySweep`) releases reservations past their
    #: `expires_at` — the same cadence as the channel sweep: a lapsed hold is freed within
    #: minutes, and a pass with nothing due is one indexed query.
    reservation_expiry_interval_seconds: float = 300
    #: The self-hosted sandbox marketplace's base URL (`app.channels.mock_marketplace`) —
    #: platform configuration, not a value a tenant supplies (`app.channels.mock_provider`'s own
    #: module docstring: the same "fixed, operator-configured base" reasoning
    #: `whatsapp_provider.py` gives for skipping `EgressGuard`). Defaults to the in-process
    #: sandbox's own dev port; `bin/mock_marketplace` binds there by default too.
    mock_marketplace_api_base: str = "http://localhost:8811"

    #: Where `services/payment`'s internal API answers (tenant collections, ADR-0024 A4): an
    #: order's online payment is opened, checked, cancelled and refunded there, never here —
    #: operations holds no payment-provider secret and no payment state, only the order's own
    #: `paid_at` fact, which arrives by event. Local `make run` port by default; the edge's
    #: compose file points it at `http://payment:8000`.
    payment_service_url: str = "http://localhost:8163"
    #: How long one call to payment may take. payment bounds its own provider call at 15 s
    #: (`collections_call_timeout_seconds`); this stays under the gateway's 30 s so a slow
    #: provider is a clean 503 here rather than the gateway's own timeout.
    payment_call_timeout_seconds: float = 25.0

    #: Where `services/finance`'s internal API answers: the order-invoice consumer
    #: (`app.consumers.order_invoice_due`) resolves an order's CRM contact into finance's own
    #: billed party there before asking for the invoice. Local `make run` port by default; the
    #: edge's compose file points it at `http://finance:8000`.
    finance_internal_url: str = "http://localhost:8171"
    finance_timeout_seconds: float = 10.0
    #: The box-wide switch for order invoicing (INVENTORY-01). `off`: READY records a skip and
    #: queues nothing. `log_only` (default): the billed party is resolved and the request finance
    #: would get is audited, but nothing is sent. `live`: the invoice is requested. In every mode an
    #: organization must also have opted in (`fulfilment_settings.auto_invoice_orders`).
    order_invoicing_mode: Literal["off", "log_only", "live"] = "log_only"


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
