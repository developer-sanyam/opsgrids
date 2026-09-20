"""Run one pass of the real SLA escalation sweep. Run **inside `services/approval`**.

This is `approval/app/cron.py`'s own `main()`, minus the `while True`: the same
`EscalationSweep`, built with the same two session factories, asserting the same two database
roles. Discovery runs on the BYPASSRLS outbox role because it is cross-tenant and has no tenant
to bind; every write runs on the NOBYPASSRLS app role inside `session_scope(organization_id=…)`,
under row-level security. Nothing is stubbed and nothing is patched — the locks, the
`FOR UPDATE SKIP LOCKED` re-check, the audit row and the outbox row are all the production ones.

**Why a single `tick(now=…)` rather than the sleeping loop.** An SLA is measured in minutes —
the floor `ApprovalPolicy` will accept is fifteen — and a suite that waits out even the floor is
a suite people skip. `tick`'s `now` parameter is the seam `app/cron.py` documents for exactly
this ("`now` exists **only** for tests… a test may pass a synthetic instant to exercise an
SLA-minutes-long ladder in milliseconds against real rows and real locks, without sleeping or
patching a process-wide clock"), and it is deliberately narrow: it changes only what the
**discovery** read compares `due_at` against, and discovery never writes a row. Every write the
tick leads to still takes its instant from `services.db_now` inside `escalate_step`'s own
transaction.

**The instant is read from the database, never from this process.** `--minutes-ahead` is added to
`now()` *by PostgreSQL* (`SELECT now() + make_interval(mins => :m)`), so the synthetic instant and
the `due_at` it is compared against come from the same clock. A container whose wall clock has
drifted from the database's — which has already cost this project a debugging session — cannot
make this sweep fire early or late.

**The candidate list is reported, not just the counts.** Discovery is platform-wide by
construction — it exists to find out *which organizations* have overdue steps — so its counts
include every tenant in the database, including requests left behind by earlier runs of this
suite, whose fifteen-minute deadlines have since genuinely elapsed. A test that asserted on
`considered` or `escalated` would therefore pass or fail depending on how recently the suite last
ran, which is the definition of flaky. So this prints the `(organization_id, request_id)` pairs
discovery returned and lets the caller assert about **its own** request.

The list is read from `selectors.due_escalation_steps` — the same function `tick` calls, on the
same session factory and the same role — immediately before the tick, for reporting only. Running
that query twice is cheap and keeps `EscalationSweep` untouched; reaching inside it to capture the
list would mean reimplementing `tick`.

Prints one JSON object: what the pass did, the candidates it saw, and the two instants it used, so
a failing assertion can say whether the sweep never found the step or found it and skipped it.

Usage: `_sweep.py <service_dir> <minutes_ahead>`
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

SERVICE_DIR = Path(sys.argv[1]).resolve()
MINUTES_AHEAD = int(sys.argv[2])

sys.path.insert(0, str(SERVICE_DIR))

from app.config import settings  # noqa: E402
from app.cron import EscalationSweep  # noqa: E402
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

    from app import selectors

    cfg = settings()
    engine = init_engine(cfg)
    await assert_app_role(engine, environment=cfg.environment)
    init_outbox_engine(cfg)
    await assert_outbox_role(get_outbox_engine(), environment=cfg.environment)

    factory = get_outbox_sessionmaker()
    sweep = EscalationSweep(
        discovery_factory=factory,
        work_scope=lambda organization_id: session_scope(organization_id=organization_id),
    )

    async with factory() as session, session.begin():
        real_now, at = (
            await session.execute(
                text("SELECT now(), now() + make_interval(mins => :m)"),
                {"m": MINUTES_AHEAD},
            )
        ).one()
        candidates = await selectors.due_escalation_steps(
            session, now=at, per_org_cap=20, limit=100
        )

    report = await sweep.tick(now=at)

    print(
        json.dumps(
            {
                "db_now": real_now.isoformat(),
                "swept_at": at.isoformat(),
                "candidates": [
                    {"organization_id": str(org_id), "request_id": str(request_id)}
                    for org_id, request_id in candidates
                ],
                "considered": report.considered,
                "escalated": report.escalated,
                "unassigned": report.unassigned,
                "skipped": report.skipped,
                "failed": report.failed,
            }
        )
    )


asyncio.run(main())
