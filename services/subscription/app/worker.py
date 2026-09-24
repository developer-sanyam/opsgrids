"""The worker process: the payment-event consumer, the dunning sweep and the billing run.

Two containers used to run this service's background work — `subscription-worker` on
`app.consumers` and `subscription-cron` on `app.cron` — at 192 MB apiece (ADR-0013's
memory-budget erratum, 2026-09-23) for two processes that are each idle almost all the time.
They are combined here the same way `notification/app/worker.py` already combines its two
consumers: one asyncio process, two independent loops, `gather`ed so either one raising brings
the whole process down for the orchestrator to restart, rather than leaving the other running
alone against a half-dead deploy.

The consumer and the sweep have never shared a database role or a schedule assumption, and
combining them changes neither: the consumer runs NOBYPASSRLS per event
(`app.consumers.main`'s own role assertion, unchanged); the sweep still splits BYPASSRLS
discovery from NOBYPASSRLS action every `poll_interval` (`app.cron.DunningSweep`, unchanged).
This file only starts both in one process instead of two.

The billing run (`app.billing_run.BillingRunSweep`, ADR-0005 addendum 2026-09-23) is the third
loop, added here rather than as a container of its own because the box's memory budget is spent
(ADR-0013's erratum): it is idle between passes exactly like the dunning sweep, uses the same two
engines, and makes its billing and payment calls with no transaction open.

`app/cron.py` keeps its own `main()` and remains directly runnable (`python -m app.cron`) for
debugging the sweep in isolation on a laptop; `bin/cron` still points at it. Only `bin/worker`
moved.
"""

from __future__ import annotations

import asyncio

from app import consumers
from app.billing_run import BillingRunSweep, HttpBilling, HttpPayment, billing_run_loop
from app.config import settings
from app.cron import DunningSweep
from app.supervise import supervise
from platform_core.db import (
    assert_app_role,
    assert_outbox_role,
    get_outbox_engine,
    get_outbox_sessionmaker,
    init_engine,
    init_outbox_engine,
    session_scope,
)
from platform_core.logging import get_logger

log = get_logger("subscription.worker")


async def main() -> None:
    cfg = settings()
    engine = init_engine(cfg)
    # Consumers run NOBYPASSRLS: the per-message tenant GUC only means something if row-level
    # security can bind this role. Started on the admin or outbox DSN by mistake, RLS would be
    # silently inert — refuse outside local (ADR-0002).
    await assert_app_role(engine, environment=cfg.environment)
    init_outbox_engine(cfg)
    # Discovery is cross-tenant and must bypass. Without the split it runs as the app role, sees
    # no rows, and every past-due customer in the platform keeps their product for free forever
    # (app/cron.py's own module docstring).
    await assert_outbox_role(get_outbox_engine(), environment=cfg.environment)

    sweep = DunningSweep(
        discovery_factory=get_outbox_sessionmaker(),
        work_scope=lambda organization_id: session_scope(organization_id=organization_id),
    )

    billing_run = BillingRunSweep(
        discovery_factory=get_outbox_sessionmaker(),
        work_scope=lambda organization_id: session_scope(organization_id=organization_id),
        billing=HttpBilling(cfg.billing_service_url, cfg),
        payment=HttpPayment(cfg.payment_service_url, cfg),
        poll_interval=cfg.billing_run_poll_interval,
        not_before=cfg.billing_run_not_before,
    )

    log.info(
        "subscription.worker.starting",
        queue="subscription.events",
        sweeps=["dunning", "billing_run"],
    )
    # Supervised, not `gather`ed: any loop that raises **or returns** — a consumer whose iterator
    # ended is as dead as one that crashed — cancels the others and exits non-zero, so the
    # orchestrator restarts the process instead of leaving a zombie worker with a loop missing.
    # Each sweep also retries its own failing passes with backoff (app/supervise.py), so a
    # poisoned row is not a crash loop that starves the consumer.
    loops = {"payment_consumer": consumers.consumer.run(), "dunning_sweep": sweep.run()}
    # Off unless enabled, and refused without a cutover (`billing_run_loop`, final review fix 3).
    run_loop = billing_run_loop(cfg, billing_run)
    if run_loop is not None:
        loops["billing_run"] = run_loop
    await supervise(loops)


if __name__ == "__main__":
    asyncio.run(main())
