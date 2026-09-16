"""Outbox relay for approval. Runs as its own process.

Runs as `opsgrids_outbox`: BYPASSRLS, because it drains every organization's events in one loop
and under row-level security the app role would silently see nothing; privileged to read and mark
rows in `outbox` and nothing else, because the admin role owns the whole schema and a bug here
must not be able to write anything but the rows it marks as sent.

Nothing publishes through the outbox yet in this slice — the hierarchy consumer only *writes*
`hierarchy_nodes`, it does not emit an event of its own, and the `core.approval.request.*` events
land in slice 2. This process exists now because every service runs the same four processes from
its first migration (ADR-0006 §1, "the same shape as subscription"), and an outbox relay with an
empty table to drain is a correct, idle process rather than a missing one.
"""

from __future__ import annotations

import asyncio

from app.config import settings
from app.models import OutboxEvent
from platform_core.db import (
    assert_outbox_role,
    get_outbox_engine,
    get_outbox_sessionmaker,
    init_engine,
    init_outbox_engine,
)
from platform_core.events import OutboxRelay


async def main() -> None:
    cfg = settings()
    init_outbox_engine(cfg)
    if not cfg.db_outbox_role:
        # No split role configured: `get_outbox_sessionmaker` and `get_outbox_engine` fall back
        # to the app engine (`init_outbox_engine` already logs `outbox.role.not_split` for this
        # case), so it has to exist here. Tolerated only so an unsplit deployment still starts —
        # this is the "silent-stall" configuration those functions' own docstrings warn about.
        init_engine(cfg)
    # When `db_outbox_role` *is* configured (every real deployment, ADR-0006 §14), this process
    # never touches `HierarchyNode` or anything else on the app role — `OutboxRelay` only ever
    # drains through `get_outbox_sessionmaker()`. Calling `init_engine` unconditionally used to
    # open the far larger app-role pool (10 + 5 by default) for a process that would never use a
    # single connection from it, which is most of the gap between the ~68 connections a replica
    # set measured and the ~35 the ADR's table budgets: skipping it here is what keeps this
    # process at its documented 2 + 2.
    await assert_outbox_role(get_outbox_engine(), environment=cfg.environment)
    relay = OutboxRelay(
        amqp_url=cfg.rabbitmq_url,
        outbox_cls=OutboxEvent,
        session_factory=get_outbox_sessionmaker(),
        producer=cfg.service_name,
    )
    await relay.run()


if __name__ == "__main__":
    asyncio.run(main())
