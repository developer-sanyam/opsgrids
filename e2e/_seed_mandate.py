"""Give one organization an active, bank-confirmed mandate. Run **inside `services/payment`**.

A seeder rather than an HTTP call because payment has no route that creates a mandate yet —
ADR-0013 slice 4's `POST /payment/mandates` and Razorpay checkout are still to come, and in
production a mandate becomes `active` only by the bank confirming it over the webhook. This writes
the row that confirmation would leave behind (`state = active`, `activated_at` set), on the admin
role, so the billing-run journey can prove the *collectable* branch of trial end: `trial →
active`, not `trial → past_due`.

Prints one JSON object. Usage: `_seed_mandate.py <service_dir> <org_id> <currency> <cap_minor>`
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

SERVICE_DIR = Path(sys.argv[1]).resolve()
ORG_ID = uuid.UUID(sys.argv[2])
CURRENCY = sys.argv[3]
CAP_MINOR = int(sys.argv[4])

sys.path.insert(0, str(SERVICE_DIR))

from app.config import settings  # noqa: E402
from app.models import MANDATE_ACTIVE, SCHEMA, Mandate  # noqa: E402


async def main() -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(
        settings().admin_dsn,
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
    )
    async with async_sessionmaker(engine, expire_on_commit=False)() as session, session.begin():
        mandate = Mandate(
            organization_id=ORG_ID,
            provider="razorpay",
            provider_mandate_id=f"mandate_e2e_{uuid.uuid4().hex[:12]}",
            state=MANDATE_ACTIVE,
            max_amount_minor=CAP_MINOR,
            currency=CURRENCY,
            activated_at=datetime.now(UTC),
        )
        session.add(mandate)
        await session.flush()
        result = {"mandate_id": str(mandate.id)}
    await engine.dispose()
    print(json.dumps(result))


asyncio.run(main())
