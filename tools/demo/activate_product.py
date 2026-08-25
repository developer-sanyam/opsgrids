"""Activate a product for an organization. Run **inside `services/product`** as a subprocess.

The org's entitlement — the difference between a 200 and a 402 at the gateway — is a single
`activations` row whose `entitles` property is true. `product.services.activate` refuses a
product whose catalogue status is still `soon`, so this upserts the activation row directly on
the admin (BYPASSRLS) engine. Purely additive: one row, never touches the global `products`
catalogue.

The activation row alone flips the 402 gate; the product's *permissions* reach the
organization separately, when account-service consumes `core.product.activated` — which this
script bypasses along with the outbox. After activating this way, run
`tools/wire_product.py <org_id> <product_key>` from the repository root so the catalogue and
role templates land too.

Usage: activate_product.py <product_service_dir> <org_id> <product_key> [activated_by_membership_id]

Point `DATABASE_URL` at the intended database before running — the demo targets `atmora_wt`
in this workspace, never the shared `atmora`.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path


def _main() -> None:
    import asyncio
    import sys

    service_dir = Path(sys.argv[1]).resolve()
    org_id = uuid.UUID(sys.argv[2])
    product_key = sys.argv[3]
    activated_by = uuid.UUID(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4] else None

    sys.path.insert(0, str(service_dir))

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app import selectors  # type: ignore[import-not-found]  # loaded from the service dir
    from app.config import settings  # type: ignore[import-not-found]
    from app.models import SCHEMA, Activation  # type: ignore[import-not-found]

    async def _run() -> None:
        cfg = settings()
        connect_args = {"server_settings": {"search_path": f"{SCHEMA},public"}}
        engine = create_async_engine(cfg.admin_dsn, connect_args=connect_args)

        async with async_sessionmaker(engine, expire_on_commit=False)() as session, session.begin():
            existing = await selectors.get_activation(session, org_id, product_key)
            if existing is not None:
                existing.status = "active"
                existing.deactivated_at = None
                activation = existing
            else:
                activation = Activation(
                    organization_id=org_id,
                    product_key=product_key,
                    status="active",
                    activated_by=activated_by,
                )
                session.add(activation)
            await session.flush()
            result = {
                "org_id": str(org_id),
                "product_key": product_key,
                "status": activation.status,
                "entitles": activation.entitles,
            }

        await engine.dispose()
        print(json.dumps(result))

    asyncio.run(_run())


if __name__ == "__main__":
    _main()
