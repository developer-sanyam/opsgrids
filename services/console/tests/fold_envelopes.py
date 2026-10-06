"""Standalone script: folds **real** `commerce.subscription.*` envelopes through console-service's
real consumer, in the order given, and prints the org's commercial snapshot as JSON.

Run as ``python -m tests.fold_envelopes --org <uuid>`` with `cwd` set to `services/console`, the
envelopes as a JSON list on stdin. It exists so subscription's own suite can prove what console
*does* with the events subscription actually stages — `import app` is ambiguous across services, so
the two talk through this subprocess's stdin and stdout and the shared Postgres cluster. See
`services/subscription/tests/test_stop_event_folds.py`, which holds the cross-service lock on this
schema for the whole run (its truncating peer is this service's `_clean` fixture).

Each envelope goes through `app.consumers.consume` with the subscription fold — the tenant-bound,
idempotent transaction the broker's handler opens — so the ordering guard (`domain._is_stale`) is
the production one. Prices are not resolved (no catalog here): every plan is a month's worth of a
fixed amount, which is all MRR needs to be seen to stop counting.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import consumers, services
from app.config import settings
from app.models import OrgCommercialState
from platform_core.db import dispose_engine, init_engine, session_scope

#: Prefixes the one result line on stdout, so the parent can find it among the service's own logs.
RESULT_MARKER = "FOLD_RESULT "

#: What the fake resolver says a plan is worth: a month's price in INR minor units.
FAKE_PRICE = (30_000_00, "INR")


async def _fake_price(_plan_version_id: str) -> tuple[int, str | None]:
    return FAKE_PRICE


async def _fold(session: AsyncSession, envelope: dict[str, Any]) -> None:
    await services.fold_subscription_event(session, envelope, resolve_price=_fake_price)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--org", required=True)
    args = parser.parse_args()
    org_id = uuid.UUID(args.org)
    envelopes: list[dict[str, Any]] = json.load(sys.stdin)

    init_engine(settings())
    try:
        for envelope in envelopes:
            await consumers.consume(envelope, _fold)
        async with session_scope(organization_id=org_id) as session:
            row = (
                await session.execute(
                    select(OrgCommercialState).where(OrgCommercialState.organization_id == org_id)
                )
            ).scalar_one_or_none()
            out: dict[str, Any] = (
                {
                    "states": {
                        key: entry.get("state") for key, entry in row.subscription_states.items()
                    },
                    "active_products": list(row.active_products),
                    "locked": row.locked,
                    "mrr_minor": row.mrr_minor,
                }
                if row is not None
                else {"states": {}, "active_products": [], "locked": False, "mrr_minor": 0}
            )
    finally:
        await dispose_engine()

    # Stdout is the channel back to the parent test process, not debug output. The marker is there
    # because the service's own logging writes to the same stream.
    print(RESULT_MARKER + json.dumps(out), flush=True)  # noqa: T201


if __name__ == "__main__":
    asyncio.run(main())
