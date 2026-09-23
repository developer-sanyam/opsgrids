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
sells a customer a 404.

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
from app.models import SCHEMA  # noqa: E402

#: The products this suite's journeys activate. `crm` for the CRM journey (two services, two
#: admin roles); `hrms` for the Wave 0 exit journey (employee + attendance); `finance` for the
#: bundle journey — the trio's third member, and product-service will not project a `soon` one.
PRODUCTS = ("crm", "hrms", "finance")


async def main() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    cfg = settings()
    engine = create_async_engine(
        cfg.admin_dsn,
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
    )
    async with engine.begin() as conn:
        # The sources go with the activations: a leftover source from a previous run would be
        # ORed back into an activation this run expects to be off.
        await conn.execute(
            text(
                f'TRUNCATE "{SCHEMA}"."activations", "{SCHEMA}"."activation_sources", '
                f'"{SCHEMA}"."outbox" CASCADE'
            )
        )
        # The only interpolation is `SCHEMA`, a constant this service defines; the product
        # keys are bound.
        await conn.execute(
            text(
                f'UPDATE "{SCHEMA}"."products" SET status = \'available\' '  # noqa: S608
                "WHERE key = ANY(:keys)"
            ),
            {"keys": list(PRODUCTS)},
        )

    await engine.dispose()
    print(f"{', '.join(PRODUCTS)} activatable; activations and outbox cleared")


asyncio.run(main())
