"""Read the two rows that prove an event crossed between services.

The journey asserts the *effect* of the activation event — permissions installed, roles
granted — because that is what a customer experiences. This probe reads the mechanism
underneath it, so a failure says which hop broke instead of only that the last one did:

* `product` — did the named service stage the event, and did its relay mark it published?
  An unpublished row is defect (a) exactly: the relay running as the RLS-bound app role
  selects nothing, so the row sits at `published_at IS NULL` forever while activation
  reports success. Despite the name, this side reads whichever service directory it is
  pointed at — the bundle journey (`test_bundle_journey.py`) points it at subscription's
  outbox, not product's, to watch `commerce.subscription.*` leave it.
* `account` — did account-service's consumer record having processed it? A published row
  with no matching `processed_events` entry means the broker hop or the handler is the
  problem, not the relay.

**Scoped by product, not just by organization, when the caller needs that.** `tenants` is a
session fixture: alpha and beta each activate more than one product over the life of a full
suite run (engage in `test_engage_journey.py`, crm in `test_journey.py`, both against the same
alpha). Every activation shares the one event type, `core.product.activated`. A probe that only
filtered on `organization_id` used to report "published" the moment *any* product's activation
for that org had gone out — including one staged by an earlier file — so a caller waiting on
its own, still-unpublished row would see somebody else's already-published one and stop
waiting before its own event had even reached the relay. That is a harness defect, not a
product one, and it only showed up once a second file activated a second product for an
organization another file was still using — which a file run alone never does. `product_key`
narrows the query to the one activation this call actually cares about; it is optional and
irrelevant to a caller (like the bundle journey) watching a different event type on a
different service's outbox, which has no product_key to filter on at all.

Reads only, as the migration role, from **inside the service's own directory** — one
interpreter can hold only one service's `app`.

Usage: `_probe.py <service_dir> <product|account> <organization_id> [event_type_pattern] [product_key]`

`event_type_pattern` is a SQL `LIKE` pattern, defaulting to the exact string
`core.product.activated` when omitted — every existing product-activation caller (`probe`'s
callers, and `event_probe` when it wants the default). `product_key`, only meaningful on the
`product` side, ANDs an exact match on `payload ->> 'product_key'`; omit it to watch every event
matching the pattern regardless of product (what the bundle journey does, matching
`commerce.subscription.%` on subscription's own outbox, which carries no product_key at all).
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
EVENT_TYPE_PATTERN = sys.argv[4] if len(sys.argv) > 4 else "core.product.activated"
PRODUCT_KEY = sys.argv[5] if len(sys.argv) > 5 else None

sys.path.insert(0, str(SERVICE_DIR))

from app.config import settings  # noqa: E402
from app.models import SCHEMA  # noqa: E402


async def main() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    cfg = settings()
    engine = create_async_engine(
        cfg.admin_dsn, connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}}
    )

    async with engine.connect() as conn:
        if WHICH == "product":
            where = 'WHERE organization_id = :org AND event_type LIKE :evt'  # noqa: S608
            params: dict[str, object] = {"org": ORG_ID, "evt": EVENT_TYPE_PATTERN}
            if PRODUCT_KEY is not None:
                where += " AND payload ->> 'product_key' = :product_key"
                params["product_key"] = PRODUCT_KEY
            rows = (
                await conn.execute(
                    text(
                        f"SELECT id, event_type, published_at, attempts, last_error "  # noqa: S608
                        f'FROM "{SCHEMA}"."outbox" {where}'
                    ),
                    params,
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
            # `processed_events` is the platform-wide idempotency ledger
            # (`ProcessedEventMixin`): one row per event id, with no `organization_id` and no
            # payload — by design, so a redelivery check never needs a tenant bound to run.
            # That means this cannot be scoped by organization or product at the query level;
            # the caller scopes it by intersecting against the *specific* event id(s) the
            # `product` probe above returned, never by treating "processed events of this
            # type exist at all" as evidence about the one this call is waiting for.
            rows = (
                await conn.execute(
                    text(
                        f'SELECT event_id, event_type FROM "{SCHEMA}"."processed_events" '  # noqa: S608
                        f"WHERE event_type LIKE :evt"
                    ),
                    {"evt": EVENT_TYPE_PATTERN},
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
