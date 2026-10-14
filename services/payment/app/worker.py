"""The worker process: the charge consumers, the stuck-charge sweep and the charge poller, side by
side.

The stuck-charge sweep (`app/cron.py`) is what resumes a charge whose process died between
calling the provider and recording what it said (D3). The consumers mark their event processed
in the *claim* transaction, so the queue never retries such a charge — the sweep is the only
thing that does, and it therefore has to run wherever payment runs. It does so here, inside
`payment-worker`, rather than in a `payment-cron` container of its own: no new container, no
change to the memory budget, the same fold `subscription`, `account` and `approval` made
(ADR-0013's memory-budget erratum).

The two loops are supervised (`platform_core.supervise`): either one returning or raising ends the
process non-zero so the orchestrator restarts it, and the sweep's own per-tick guard
(`StuckChargeSweep.run`) means a failing tick backs off instead of ending anything — a bad pass
never takes the consumers down with it.

`bin/cron` (`python -m app.cron`) still runs the sweep alone for local debugging.
"""

from __future__ import annotations

import asyncio

from app import consumers
from app.checkout.cron import build_poller
from app.collections.cron import build_expiry_sweep
from app.config import announce_test_mode, settings
from app.cron import build_sweep
from platform_core.db import (
    assert_app_role,
    assert_outbox_role,
    get_outbox_engine,
    init_engine,
    init_outbox_engine,
)
from platform_core.logging import get_logger
from platform_core.supervise import run_supervised

log = get_logger("payment.worker")


async def main() -> None:
    cfg = settings()
    # The `split-workers` rollback runs the poller here, so test mode is announced here too.
    announce_test_mode(cfg)
    engine = init_engine(cfg)
    # Consumers run NOBYPASSRLS: the per-message tenant GUC only means something if row-level
    # security can bind this role (ADR-0002).
    await assert_app_role(engine, environment=cfg.environment)
    init_outbox_engine(cfg)
    # The sweep's discovery is cross-tenant and must bypass; on the app role it would see nothing
    # and a charge orphaned by a crash would sit `created` forever.
    await assert_outbox_role(get_outbox_engine(), environment=cfg.environment)

    sweep = build_sweep()
    expiry = build_expiry_sweep()
    poller = build_poller()
    log.info(
        "payment.worker.starting",
        queue="payment.events",
        sweeps=["stuck_charge", "collection_expiry", "charge_poller"],
    )
    await run_supervised(
        consumer=consumers.consumer.run(),
        stuck_charge=sweep.run(),
        collection_expiry=expiry.run(),
        charge_poller=poller.run(),
    )


if __name__ == "__main__":
    asyncio.run(main())
