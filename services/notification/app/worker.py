"""The worker process: the rules engine and the dispatcher, side by side.

This service has two consumers rather than the usual one — `notification.events` folds inbound
events into messages, and `notification.dispatch` sends them — and they are run together here so
the platform keeps the three entrypoints CLAUDE.md fixes (`api`, `worker`, `cron`) instead of
growing a fourth deployment for one service.

They are still two queues with two bindings and two prefetch windows, so a backlog on one does
not stall the other. Both are I/O-bound and cooperative: a slow provider is awaited, not spun on,
so a dispatcher waiting on SMTP does not stop a ticket event being folded.

If one ever needs its own scaling story, `python -m app.consumers` and `python -m app.dispatcher`
are each independently runnable — that is why they keep their own `main()`.
"""

from __future__ import annotations

import asyncio

from app import consumers, dispatcher
from app.config import settings
from app.targets import make_resolver as make_target_resolver
from app.transports import build as build_transports
from platform_core.db import assert_app_role, init_engine
from platform_core.logging import get_logger

log = get_logger("notification.worker")


async def main() -> None:
    cfg = settings()
    engine = init_engine(cfg)
    # Both consumers run NOBYPASSRLS: the per-message tenant GUC only means something if
    # row-level security can bind this role. Started on the admin or outbox DSN by mistake,
    # RLS would be silently inert — refuse outside local (ADR-0002).
    await assert_app_role(engine, environment=cfg.environment)

    consumers.register(make_target_resolver(cfg))
    # Raises rather than downgrading to `log` when a real transport is named without
    # credentials, so a misconfigured deploy fails here instead of going quiet.
    dispatcher.register(build_transports(cfg), cfg)

    log.info(
        "notification.worker.starting", queues=["notification.events", "notification.dispatch"]
    )
    # `gather` rather than sequential awaits: either consumer returning is a failure, and the
    # first exception should bring the process down so the orchestrator restarts it, rather
    # than leaving one queue draining and the other silently dead.
    await asyncio.gather(consumers.consumer.run(), dispatcher.consumer.run())


if __name__ == "__main__":
    asyncio.run(main())
