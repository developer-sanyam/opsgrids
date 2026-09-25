"""Configuration for the insights service (ADR-0020).

insights is a **leaf**: no service ever calls it, and it reads no other schema directly. Its
three background loops (the event consumer, the hourly snapshot sweep and the backfill runner,
`app/main.py`) call other services only over HTTP, through their `/internal/v1` export routes,
under a short-lived service token this process mints itself (`platform_core.internal_auth`).
"""

from __future__ import annotations

from platform_core.config import ServiceSettings, settings_for


class Settings(ServiceSettings):
    service_name: str = "insights"
    db_schema: str = "insights"
    #: Its own NOBYPASSRLS role, granted DML on the `insights` schema and nothing else. Every
    #: fact write — from an event, an export page, or the sweep — runs through it with the
    #: tenant GUC bound per transaction, exactly like every other tenant-scoped service.
    db_app_role: str | None = "opsgrids_insights"
    db_admin_role: str | None = "opsgrids_admin"
    #: BYPASSRLS, but granted only the narrow, **column-level** SELECT
    #: `docs/insights/data-model.md` §1 names: `org_profile(organization_id, timezone)` and
    #: `backfill_runs(id, organization_id, entity, status, next_attempt_at, finished_at)`. This
    #: is what lets the backfill runner and the snapshot sweep *discover* which organizations
    #: have work — there is no tenant to bind while asking that question — without ever being
    #: able to read a fact. Kept from the scaffold's `db_outbox_role` field name because it is
    #: the same role (`opsgrids_outbox`) the platform already uses for exactly this split
    #: (CLAUDE.md); insights just widens what it may additionally SELECT.
    db_outbox_role: str | None = "opsgrids_outbox"

    #: Runs the event consumer, the hourly snapshot sweep and the backfill runner as one
    #: supervised background task inside this API process (`app/main.py`) instead of the
    #: separate `insights-worker` container — the same `RUN_CONSUMERS_IN_API` switch every
    #: other folded service reads (ADR-0020 §1: "three loops in one process"). Off by default;
    #: the edge box's own `.env` turns it on. `bin/worker` (`app/worker.py`) still runs all
    #: three loops standalone, unchanged, for local development and as the `split-workers`
    #: rollback.
    run_consumers_in_api: bool = False

    #: account-service's internal API, for the organization export the backfill runner and the
    #: snapshot sweep discover tenants from (`GET /internal/v1/organizations/export`,
    #: ADR-0020 §3, §6.2). Deliberately `/internal/v1`, never the public surface: this is a
    #: service-to-service call under a service token, addressed to account by name, not a
    #: request made on a caller's behalf. Same default port every other service dials account
    #: on in development (`services/account/README.md`).
    account_service_url: str = "http://localhost:8111"
    #: deal-service's internal API, for the `deal.deals` export the backfill runner drains and
    #: the `crm.pipeline.deal.*` fold's own tenant is bound against nothing further —
    #: `docs/insights/api.md` §6.1 (I3).
    deal_service_url: str = "http://localhost:8131"
    #: lead-service's internal API, for the `lead.leads` export (I4).
    lead_service_url: str = "http://localhost:8110"
    #: customer-service's internal API, for the `customer.companies` export (I4).
    customer_service_url: str = "http://localhost:8130"

    #: Paging and pacing for the backfill runner's export reads (ADR-0020 §3): "pages of 500,
    #: with a 100 ms pause between pages ... timeouts are 5 s to connect and 30 s to read, with
    #: 3 retries at 2/8/30 s."
    backfill_page_size: int = 500
    backfill_page_pause_seconds: float = 0.1
    backfill_connect_timeout_seconds: float = 5.0
    backfill_read_timeout_seconds: float = 30.0
    #: How often the runner re-exports an (organization, entity) pair with no reason to think it
    #: is stale — the self-healing weekly reconcile (ADR-0020 §3). Also how long a completed run
    #: is honoured before the discovery step enqueues another one.
    backfill_reconcile_after_days: int = 7
    #: How long the discovery-vs-drain loop sleeps between passes when it has no cross-tenant
    #: enumeration to do — the drain side runs far more often (`backfill_drain_poll_seconds`).
    backfill_discovery_poll_seconds: float = 3600.0
    backfill_drain_poll_seconds: float = 5.0

    #: The hourly snapshot sweep (ADR-0020 §2, metrics.md §8): "runs hourly at :05."
    snapshot_sweep_poll_seconds: float = 3600.0


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
