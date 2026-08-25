"""Read the two rows that prove an event crossed between services.

The journey asserts the *effect* of the activation event — permissions installed, roles
granted — because that is what a customer experiences. This probe reads the mechanism
underneath it, so a failure says which hop broke instead of only that the last one did:

* `product` — did product-service stage the event, and did its relay mark it published?
  An unpublished row is defect (a) exactly: the relay running as the RLS-bound app role
  selects nothing, so the row sits at `published_at IS NULL` forever while activation
  reports success.
* `account` — did account-service's consumer record having processed it? A published row
  with no matching `processed_events` entry means the broker hop or the handler is the
  problem, not the relay.

Reads only, as the migration role, from **inside the service's own directory** — one
interpreter can hold only one service's `app`.

Usage: `_probe.py <service_dir> <product|account> <organization_id>`
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

SERVICE_DIR = Path(sys.argv[1]).resolve()
WHICH = sys.argv[2]
ORG_ID = uuid.UUID(sys.argv[3])

sys.path.insert(0, str(SERVICE_DIR))

from app.config import settings  # noqa: E402
from app.models import SCHEMA  # noqa: E402

EVENT_TYPE = "core.product.activated"


async def main() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    cfg = settings()
    engine = create_async_engine(
        cfg.admin_dsn, connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}}
    )

    async with engine.connect() as conn:
        if WHICH == "product":
            rows = (
                await conn.execute(
                    text(
                        f"SELECT id, event_type, published_at, attempts, last_error "  # noqa: S608
                        f'FROM "{SCHEMA}"."outbox" '
                        f"WHERE organization_id = :org AND event_type = :evt"
                    ),
                    {"org": ORG_ID, "evt": EVENT_TYPE},
                )
            ).mappings()
            staged = [dict(r) for r in rows]
            result = {
                "staged": len(staged),
                "published": sum(1 for r in staged if r["published_at"] is not None),
                "rows": [
                    {
                        "id": str(r["id"]),
                        "published": r["published_at"] is not None,
                        "attempts": r["attempts"],
                        "last_error": r["last_error"],
                    }
                    for r in staged
                ],
            }
        else:
            rows = (
                await conn.execute(
                    text(
                        f'SELECT event_id, event_type FROM "{SCHEMA}"."processed_events" '  # noqa: S608
                        f"WHERE event_type = :evt"
                    ),
                    {"evt": EVENT_TYPE},
                )
            ).mappings()
            processed = [dict(r) for r in rows]
            result = {
                "processed": len(processed),
                "event_ids": [str(r["event_id"]) for r in processed],
            }

    await engine.dispose()
    print(json.dumps(result, default=str))


asyncio.run(main())
