"""Configuration for the subscription service."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import model_validator

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
    #: The cutover: the instant billing goes live, set by the operator to the moment they turn the
    #: run on, with an explicit offset (`2026-10-01T09:00:00+05:30`). **Required when the run is
    #: enabled** — the worker refuses to start the run's loop without it (final review, fix 3).
    #: No recurring charge for a period that began before it is ever invoiced: such periods roll
    #: or convert `skipped` (`pre_cutover`), so an existing subscription starts paying at its first
    #: period after the cutover and a trial that ended before it converts with no back charge; and
    #: no usage from before it is billed. Set it once and never move it later — rows already billed
    #: are not revisited.
    #:
    #: Accepted syntax, exactly: `YYYY-MM-DDTHH:MM[:SS[.ffffff]]` then `Z` or `±HH:MM` — e.g.
    #: `2026-10-01T09:00:00+05:30` or `2026-10-01T03:30:00Z`. Anything else is **refused softly**
    #: (second post-review fixup): the value becomes None and the reason lands in
    #: `billing_run_not_before_invalid`, so the settings still load — the payment consumer and the
    #: dunning sweep in the same worker keep running — and `billing_run_loop` refuses the run. A
    #: hard validation error here would crash-loop the whole worker; a lenient one would let
    #: pydantic read `0` or `2026` as epoch seconds (1970) and back-bill everything.
    billing_run_not_before: datetime | None = None
    #: Why the configured cutover was refused, when it was. Written by the screen below only.
    billing_run_not_before_invalid: str | None = None

    #: Runs this service's worker loops — the payment-event consumer, the dunning sweep and the
    #: billing run (`app/worker.py`) — as supervised background tasks inside the API process
    #: instead of the separate `subscription-worker` container (the memory-headroom follow-up
    #: that folds every service's remaining worker in). Off by default everywhere except the
    #: edge box's own `.env`: `bin/worker` still runs `app.worker` standalone unchanged, and
    #: setting this to `false` while adding `split-workers` to `EDGE_PROFILES` is the rollback
    #: (RUNBOOK.md).
    run_consumers_in_api: bool = False

    @model_validator(mode="before")
    @classmethod
    def _screen_cutover(cls, data: Any) -> Any:
        if not isinstance(data, dict) or "billing_run_not_before" not in data:
            return data
        instant, problem = parse_cutover(data["billing_run_not_before"])
        return {
            **data,
            "billing_run_not_before": instant,
            "billing_run_not_before_invalid": problem,
        }


#: The one shape `BILLING_RUN_NOT_BEFORE` may take (preflight checks the same pattern).
CUTOVER_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$")


def parse_cutover(raw: Any) -> tuple[datetime | None, str | None]:
    """`(instant, None)` for an ISO instant with an offset, `(None, None)` for unset or empty
    (compose interpolates an unset variable to ""), and `(None, why)` for anything else."""
    if raw is None or raw == "":
        return None, None
    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            return None, f"BILLING_RUN_NOT_BEFORE {raw.isoformat()!r} has no UTC offset"
        return raw, None
    shown = repr(raw)[:60]
    expected = (
        "an ISO instant with a UTC offset, YYYY-MM-DDTHH:MM:SS±HH:MM "
        "(e.g. 2026-10-01T09:00:00+05:30 or 2026-10-01T03:30:00Z)"
    )
    if not isinstance(raw, str) or not CUTOVER_PATTERN.match(raw):
        return None, f"BILLING_RUN_NOT_BEFORE={shown} is not {expected}"
    try:
        return datetime.fromisoformat(raw), None
    except ValueError:
        return None, f"BILLING_RUN_NOT_BEFORE={shown} is not a real instant; expected {expected}"


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
