"""Read what the billing run left in billing's or payment's schema. Run **inside that service**.

The journey asserts the customer-visible effect (the subscription is `active`) over HTTP; this
reads the money underneath it, so a failure says which hop broke:

* `billing` — the organization's invoices: number, total, currency, the subscription and the
  period they bill, the billing-run key — and whether `commerce.invoice.finalized` left the
  outbox (the relay hop).
* `payment` — the organization's charges: which invoice, how much, the platform idempotency key,
  and the state the stubbed provider left them in (the consumer hop).

Reads only, on the migration role. Prints one JSON object.

Usage: `_commerce_probe.py <service_dir> <billing|payment> <organization_id>`
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


async def main() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(
        settings().admin_dsn,
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
    )
    async with engine.connect() as conn:
        if WHICH == "billing":
            invoices = [
                dict(r)
                for r in (
                    await conn.execute(
                        text(
                            "SELECT id, number, subscription_id, state, currency, total_minor, "
                            "period_start, period_end, idempotency_key FROM billing.invoices "
                            "WHERE organization_id = :org ORDER BY period_start"
                        ),
                        {"org": ORG_ID},
                    )
                ).mappings()
            ]
            published = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM billing.outbox WHERE organization_id = :org "
                        "AND event_type = 'commerce.invoice.finalized' "
                        "AND published_at IS NOT NULL"
                    ),
                    {"org": ORG_ID},
                )
            ).scalar_one()
            result = {"invoices": invoices, "finalized_published": int(published)}
        else:
            charges = [
                dict(r)
                for r in (
                    await conn.execute(
                        text(
                            "SELECT id, invoice_id, subscription_id, product_key, amount_minor, "
                            "currency, state, idempotency_key FROM payment.charges "
                            "WHERE organization_id = :org ORDER BY created_at"
                        ),
                        {"org": ORG_ID},
                    )
                ).mappings()
            ]
            result = {"charges": charges}
    await engine.dispose()
    print(json.dumps(result, default=str))


asyncio.run(main())
