"""Put product-service's registry into the state this journey needs.

Run **inside `services/product`** as a subprocess, for the same reason as every other seeder
here: one interpreter, one service's `app`.

Two things, and the second needs justifying.

**Clear this organization's activations and outbox.** Both are per-run state. A leftover
activation would entitle a product the journey expects to be refused, and a leftover outbox
row would let the relay step pass on an event from a previous run. `products` itself is
global catalogue data seeded by a migration and is deliberately *not* truncated — emptying it
would leave nothing to activate.

**Promote `crm` from "soon" to "available".** `services.activate` refuses a product whose
registry status is `soon` — rightly, since activating something with no service behind it
sells a customer a 404. But CRM is the only product in the catalogue that spans two services
(customer-service owns the account book, deal-service owns the pipeline) and therefore the
only one shipping **two** admin role templates. That is exactly the shape of the defect this
journey exists to catch: activation used to grant only the last admin template parsed, so
CRM's owners could read contacts and not deals.

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

#: The product whose activation this journey drives. Two services, two admin roles.
PRODUCT = "crm"


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
        # The only interpolation is `SCHEMA`, a constant this service defines; the product
        # key is bound.
        await conn.execute(
            text(f'UPDATE "{SCHEMA}"."products" SET status = \'available\' WHERE key = :key'),  # noqa: S608
            {"key": PRODUCT},
        )

    await engine.dispose()
    print(f"{PRODUCT} is activatable; activations and outbox cleared")


asyncio.run(main())
