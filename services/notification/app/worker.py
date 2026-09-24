"""The worker process: the rules engine, the dispatcher, the campaign send driver and the
dispatch recovery sweep, side by side.

This service has two consumers rather than the usual one — `notification.events` folds inbound
events into messages, and `notification.dispatch` sends them — and they are run together here so
the platform keeps the three entrypoints CLAUDE.md fixes (`api`, `worker`, `cron`) instead of
growing a fourth deployment for one service.

They are still two queues with two bindings and two prefetch windows, so a backlog on one does
not stall the other. Both are I/O-bound and cooperative: a slow provider is awaited, not spun on,
so a dispatcher waiting on SMTP does not stop a ticket event being folded.

**The campaign driver joined this group in ADR-0013's memory-budget erratum (2026-09-23).**
Two containers used to run this service's background work — `notification-worker` on the two
consumers above and `notification-cron` on `app.cron`'s `CampaignDriver` — at 192 MB apiece for
processes that are each idle almost all the time, the same shape `subscription-worker` and
`approval-worker` already collapsed. `CampaignDriver` gained its own poisoned-step guard
(`platform_core.supervise.GuardedSweep`, the same shape `scheduler.app.cron.Sweep` uses,
ADR-0015) as part of this move: sharing a process means an uncaught exception in the driver's
discovery query would otherwise take the event consumer and the dispatcher down with it, not
just the campaign sweep.

**`run_supervised`, not a plain `asyncio.gather` (memory-headroom slice, 2026-09-23 fix round).**
Plain `gather` propagates the first exception but does not cancel the other two loops — they
keep running, orphaned, against a process that is already on its way down. `run_supervised`
(`platform_core.supervise`) cancels every other loop the instant any one of them returns or
raises, so there is never a moment with one loop dead and another still touching a connection
pool this process is about to tear down.

**The dispatch recovery sweep is the fourth loop** (NOTIF-A). It is what makes a transient send
failure retry and a dispatcher that died mid-send recover (`app/dispatcher.py`'s docstring has
the whole argument): without it a message whose first attempt failed is `pending` for ever. It
has to live with the dispatcher — a worker that runs the consumer and not the sweep looks healthy
and quietly drops every message its provider fumbles once — so it is a loop of this process, not
a container of its own. It uses `GuardedSweep` for the same reason the campaign driver does.

`app/cron.py` keeps its own `main()` and remains directly runnable (`python -m app.cron`) for
debugging the campaign driver in isolation on a laptop; `bin/cron` still points at it. Only
`bin/worker` moved. `python -m app.consumers` and `python -m app.dispatcher` are likewise still
independently runnable — that is why they keep their own `main()`.
"""

from __future__ import annotations

import asyncio

from app import consumers, dispatcher
from app.config import settings
from app.cron import CampaignDriver, DispatchRecovery
from app.targets import make_resolver as make_target_resolver
from app.transports import CAMPAIGN_SMTP_EXECUTOR
from app.transports import build as build_transports
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
from platform_core.supervise import run_supervised

log = get_logger("notification.worker")


async def main() -> None:
    cfg = settings()
    engine = init_engine(cfg)
    # Every consumer and the driver's own writes run NOBYPASSRLS: the per-message tenant GUC
    # only means something if row-level security can bind this role. Started on the admin or
    # outbox DSN by mistake, RLS would be silently inert — refuse outside local (ADR-0002).
    await assert_app_role(engine, environment=cfg.environment)
    init_outbox_engine(cfg)
    # Discovery is cross-tenant and must bypass. Without the split it runs as the app role, sees
    # no rows, and every campaign in the platform stays `sending` forever — the outbox relay's
    # own silent stall, here (app/cron.py's own module docstring).
    await assert_outbox_role(get_outbox_engine(), environment=cfg.environment)

    consumers.register(make_target_resolver(cfg))
    # Raises rather than downgrading to `log` when a real transport is named without
    # credentials, so a misconfigured deploy fails here instead of going quiet.
    dispatcher.register(build_transports(cfg), cfg)

    driver = CampaignDriver(
        discovery_factory=get_outbox_sessionmaker(),
        work_scope=lambda organization_id: session_scope(organization_id=organization_id),
        batch_size=cfg.engage_campaign_batch_size,
        lease_seconds=cfg.engage_campaign_claim_lease_seconds,
        pause_retry_seconds=cfg.engage_campaign_pause_retry_seconds,
        poll_interval=cfg.engage_campaign_poll_interval_seconds,
    )

    recovery = DispatchRecovery(
        discovery_factory=get_outbox_sessionmaker(),
        work_scope=lambda organization_id: session_scope(organization_id=organization_id),
        max_attempts=cfg.dispatch_max_attempts,
        retry_base_seconds=cfg.dispatch_retry_base_seconds,
        retry_max_seconds=cfg.dispatch_retry_max_seconds,
        requeue_lease_seconds=cfg.dispatch_requeue_lease_seconds,
        poll_interval=cfg.dispatch_recovery_poll_seconds,
        limit=cfg.dispatch_recovery_batch,
    )

    log.info(
        "notification.worker.starting",
        queues=["notification.events", "notification.dispatch"],
        sweeps=["campaign_driver", "dispatch_recovery"],
    )
    try:
        # Any one of these returning or raising ends the group; `run_supervised` cancels the
        # other two before this raises, so the orchestrator restarts a process with no orphaned
        # loop still running against it (platform_core.supervise's own docstring has the full
        # argument).
        await run_supervised(
            events=consumers.consumer.run(),
            dispatch=dispatcher.consumer.run(),
            campaign_driver=driver.run(),
            dispatch_recovery=recovery.run(),
        )
    finally:
        # `cancel_futures=True` drops anything still queued (nothing should be, since
        # `run_supervised` already cancelled `campaign_driver` above); `wait=False` does not
        # block process exit on an SMTP call already in flight on one of the pool's two
        # threads — the orchestrator is about to restart this container regardless, and a
        # send genuinely mid-flight when that happens is exactly the "crash mid-batch"
        # window `app/cron.py`'s own module docstring already describes: the claim's lease
        # expires, the next tick reclaims the batch, and a message whose provider call had
        # actually completed just before the cut gets sent again. That duplicate-send window
        # is not new here — it existed for `notification-cron` as its own container before
        # this slice folded it into this process — this executor does not widen it.
        CAMPAIGN_SMTP_EXECUTOR.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    asyncio.run(main())
