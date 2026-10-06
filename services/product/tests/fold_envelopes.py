"""Standalone script: folds **real** `commerce.subscription.*` envelopes through product-service's
real consumer, in the order given, and prints what the gate would read afterwards as JSON.

Run as ``python -m tests.fold_envelopes --org <uuid>`` with `cwd` set to `services/product` (so
`app` and `tests` resolve the way product's own conftest does), the envelopes as a JSON list on
stdin. It exists so subscription's own suite can prove what product *does* with the events
subscription actually stages — `import app` is ambiguous across services, one process can only
hold one, so the two talk through this subprocess's stdin and stdout and the shared Postgres
cluster, the same boundary two deployed services cross. See
`services/subscription/tests/test_stop_event_folds.py`, which holds the cross-service lock on this
schema for the whole run (its truncating peer is this service's `session` fixture).

Each envelope goes through `app.consumers.project_subscription_event` — the handler the broker
calls, with the per-message tenant binding and the processed-events ledger — so the ordering guard
that drops an event tied with the one before it is the production one, not a re-implementation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import selectors
from app.config import settings
from app.consumers import project_subscription_event
from app.models import SCHEMA
from platform_core.db import TENANT_GUC, dispose_engine, init_engine

#: Prefixes the one result line on stdout, so the parent can find it among the service's own logs.
RESULT_MARKER = "FOLD_RESULT "


async def _read(org_id: uuid.UUID) -> dict[str, Any]:
    """What the organization has: activations, entitlements and sources, read as the app role."""
    engine = create_async_engine(
        settings().app_dsn, connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}}
    )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session, session.begin():
        await session.execute(
            text(f"SELECT set_config('{TENANT_GUC}', :org, true)"), {"org": str(org_id)}
        )
        activations = await selectors.list_activations(session, org_id)
        entitlements = await selectors.entitlements_for(session, org_id)
        out: dict[str, Any] = {
            "activations": {
                a.product_key: {
                    "status": a.status,
                    "entitles": a.entitles,
                    "deactivated_at": a.deactivated_at.isoformat() if a.deactivated_at else None,
                }
                for a in activations
            },
            "entitlements": entitlements,
            "sources": [],
        }
        for a in activations:
            for s in await selectors.sources_for(session, org_id, a.product_key):
                out["sources"].append(
                    {
                        "product_key": s.product_key,
                        "source_product_key": s.source_product_key,
                        "status": s.status,
                        "is_subscription_managed": s.is_subscription_managed,
                    }
                )
    await engine.dispose()
    return out


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--org", required=True)
    args = parser.parse_args()
    org_id = uuid.UUID(args.org)
    envelopes: list[dict[str, Any]] = json.load(sys.stdin)

    init_engine(settings())
    try:
        for envelope in envelopes:
            await project_subscription_event(envelope)
    finally:
        await dispose_engine()

    # Stdout is the channel back to the parent test process, not debug output. The marker is there
    # because the service's own logging writes to the same stream.
    print(RESULT_MARKER + json.dumps(await _read(org_id)), flush=True)  # noqa: T201


if __name__ == "__main__":
    asyncio.run(main())
