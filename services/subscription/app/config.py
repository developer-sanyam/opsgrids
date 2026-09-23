"""Configuration for the subscription service."""

from __future__ import annotations

from platform_core.config import ServiceSettings, settings_for


class Settings(ServiceSettings):
    service_name: str = "subscription"
    db_schema: str = "subscription"
    #: Its own NOBYPASSRLS role, granted DML on the `subscription` schema and nothing else.
    #: Every table here is tenant-scoped, so the API must connect as a role RLS can bind
    #: (ADR-0002 finding 2) — and the projection consumer downstream in product-service relies
    #: on the same discipline: a write only passes the tenant policy when the GUC is set.
    db_app_role: str | None = "opsgrids_subscription"
    db_admin_role: str | None = "opsgrids_admin"
    #: The outbox relay's role: BYPASSRLS, SELECT+UPDATE on `outbox` only. subscription emits
    #: the `commerce.subscription.*` lifecycle stream that product-service projects to Activation.
    db_outbox_role: str | None = "opsgrids_outbox"

    #: Where the catalog service answers the pin lookup. A subscribe/change-plan resolves its
    #: `plan_version_id` against catalog over the service-token internal API before pinning it —
    #: catalog is the authority for whether a version exists, is published and is visible here.
    catalog_service_url: str = "http://localhost:8164"

    #: Where product-service answers the registry read — asked only when a bundle is sold, to
    #: refuse members the registry no longer vouches for (`app/registry.py`).
    product_service_url: str = "http://localhost:8112"

    # --- the billing run (ADR-0005 addendum, 2026-09-23) -----------------------------------
    #: Where billing answers `POST /internal/v1/subscription-invoices` — the run's one write.
    billing_service_url: str = "http://localhost:8160"
    #: Where payment answers the trial-end mandate question.
    payment_service_url: str = "http://localhost:8163"
    #: Seconds between billing-run passes. A trial that ended converts within one interval; the
    #: `/health/billing-run` probe calls a subscription overdue after `billing_run_overdue_seconds`.
    billing_run_poll_interval: float = 300.0
    billing_run_overdue_seconds: int = 3600
    #: The run's kill switch — **off by default** (review fix 6). A first deploy onto a box with
    #: history converts every expired trial and bills every overdue period on its first passes,
    #: so it is turned on deliberately, after reading `python -m app.billing_run --dry-run`
    #: (the runbook's procedure). Off, the worker logs `billing_run.disabled` at warning and
    #: `/health/billing-run`'s `overdue` climbs.
    billing_run_enabled: bool = False


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
