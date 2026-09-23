"""Put product-service's registry into the state this journey needs.

Run **inside `services/product`** as a subprocess, for the same reason as every other seeder
here: one interpreter, one service's `app`.

Two things, and the second needs justifying.

**Clear this organization's activations and outbox.** Both are per-run state. A leftover
activation would entitle a product the journey expects to be refused, and a leftover outbox
row would let the relay step pass on an event from a previous run. `products` itself is
global catalogue data seeded by a migration and is deliberately *not* truncated — emptying it
would leave nothing to activate.

**Promote `crm` and `hrms` from "soon" to "available".** `services.activate` refuses a product
whose registry status is `soon` — rightly, since activating something with no service behind it
sells a customer a 404. Driven through `python -m app.launch` (P6) — the real operator command,
not a hand-run `UPDATE` — so this suite exercises the same path a deploy does and gets the same
audit row and the same refusal for a key the catalogue does not know.

CRM is the only product in the catalogue that spans two services (customer-service owns the
account book, deal-service owns the pipeline) and therefore the only one shipping **two** admin
role templates. That is exactly the shape of the defect the CRM journey exists to catch:
activation used to grant only the last admin template parsed, so CRM's owners could read
contacts and not deals.

HRMS spans employee-service and attendance-service, and the Wave 0 exit journey
(`test_approval_journey.py`) cannot start without it: an attendance correction is the one
request type approval routes, and `/api/v1/employees` and `/api/v1/attendance` are both gated on
the `hrms` entitlement at the gateway. Both services are built, both are booted by this suite,
and both contribute to `libs/contracts/permissions/hrms.json` — so this flips a launch flag, it
does not fabricate a capability.

Launch status is deployment data — which products this deployment is selling — not a property
of the code. Both services are built and both have manifests, so flipping the flag in this
suite's own database exercises a real path rather than fabricating one. It is confined to
`opsgrids_wt`, and `make gate`'s migration round-trip re-runs the seed migration, which puts
the committed value back.

Usage: `_seed_product.py <service_dir>`
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

SERVICE_DIR = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(SERVICE_DIR))

from app.config import settings  # noqa: E402
from app.launch import main as launch_main  # noqa: E402
from app.models import PLATFORM_ORG, SCHEMA  # noqa: E402

#: The products this suite's journeys activate. `crm` for the CRM journey (two services, two
#: admin roles); `hrms` for the Wave 0 exit journey (employee + attendance).
PRODUCTS = ("crm", "hrms")


async def main() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    cfg = settings()
    engine = create_async_engine(
        cfg.admin_dsn,
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
    )
    async with engine.begin() as conn:
        await conn.execute(text(f'TRUNCATE "{SCHEMA}"."activations", "{SCHEMA}"."outbox" CASCADE'))
        # `audit_logs` is tenant-policied even for a platform-sentinel row (ADR-0000 §10), so
        # a prior run's launch-status audit rows are cleared here too — otherwise they would
        # be the one row this seeder leaves behind indefinitely across every future run.
        await conn.execute(
            text(f'DELETE FROM "{SCHEMA}"."audit_logs" WHERE organization_id = :org'),  # noqa: S608
            {"org": PLATFORM_ORG},
        )
    await engine.dispose()

    # The real operator path (P6), not a hand-run UPDATE: `python -m app.launch <key> available`
    # per product, in this same process — `app.launch.main` takes argv directly rather than
    # this seeder shelling out to `python -m app.launch` again, which would be a second
    # interpreter importing the same `app` package this one already holds.
    for key in PRODUCTS:
        code = await launch_main([key, "available"])
        if code != 0:
            raise RuntimeError(f"app.launch refused to promote {key!r} to available")

    print(f"{', '.join(PRODUCTS)} activatable; activations and outbox cleared")


asyncio.run(main())
