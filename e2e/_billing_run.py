"""Run one pass of the real billing run. Run **inside `services/subscription`**.

This is `app/worker.py`'s `BillingRunSweep`, minus the `while True`: the same two engines, the same
role assertions, and the **real** HTTP clients — so the pass calls the running billing-service to
price and finalize the invoice, and the running payment-service to ask whether a mandate can
collect it. Nothing is stubbed; `BILLING_SERVICE_URL`, `PAYMENT_SERVICE_URL` and the shared
`INTERNAL_SERVICE_TOKEN` come from the harness, exactly as compose passes them on the box.

**Why a single `tick(now=…)`.** A trial is fourteen days long. `now` is the seam `tick` documents
for this: it moves only what discovery and `due_period` compare stored timestamps against. It is
read from PostgreSQL (`now() + make_interval(secs => :s)`), so the synthetic instant and the
stored `trial_ends_at` come from one clock.

Discovery is platform-wide, so the counts include every tenant in the database; the caller
asserts about its own subscription from the database, not from these counts.

Prints one JSON object. Usage: `_billing_run.py <service_dir> <seconds_ahead>`
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

SERVICE_DIR = Path(sys.argv[1]).resolve()
SECONDS_AHEAD = int(sys.argv[2])

sys.path.insert(0, str(SERVICE_DIR))

from app.billing_run import BillingRunSweep, HttpBilling, HttpPayment  # noqa: E402
from app.config import settings  # noqa: E402
from platform_core.db import (  # noqa: E402
    assert_app_role,
    assert_outbox_role,
    get_outbox_engine,
    get_outbox_sessionmaker,
    init_engine,
    init_outbox_engine,
    session_scope,
)


async def main() -> None:
    from sqlalchemy import text

    cfg = settings()
    engine = init_engine(cfg)
    await assert_app_role(engine, environment=cfg.environment)
    init_outbox_engine(cfg)
    await assert_outbox_role(get_outbox_engine(), environment=cfg.environment)

    factory = get_outbox_sessionmaker()
    sweep = BillingRunSweep(
        discovery_factory=factory,
        work_scope=lambda organization_id: session_scope(organization_id=organization_id),
        billing=HttpBilling(cfg.billing_service_url, cfg),
        payment=HttpPayment(cfg.payment_service_url, cfg),
        batch_size=500,
        # The cutover the worker would run with (`BILLING_RUN_NOT_BEFORE`, from the harness).
        not_before=cfg.billing_run_not_before,
    )
    async with factory() as session, session.begin():
        at = (
            await session.execute(
                text("SELECT now() + make_interval(secs => :s)"), {"s": SECONDS_AHEAD}
            )
        ).scalar_one()

    report = await sweep.tick(now=at)
    print(
        json.dumps(
            {
                "swept_at": at.isoformat(),
                "considered": report.considered,
                "issued": report.issued,
                "activated": report.activated,
                "past_due": report.past_due,
                "renewed": report.renewed,
                "failed": report.failed,
                "invoices": [str(i) for i in report.invoices],
            }
        )
    )


asyncio.run(main())
