"""Flip a product's launch status — the one write `products` ever needs outside a migration.

    docker compose exec product python -m app.launch <product_key> available|soon

Launch status is deployment data, not a property of the code (which products a running
deployment is actually selling), so it belongs to an operator running a command against a real
deployment, not to a migration and not to a hand-run `UPDATE product.products SET status = ...`
against the database directly. The latter is exactly what this replaces: it left no audit trail,
and it could set anything, including a `status` nothing else in the schema treats as meaningful.

Goes through `app.services.set_launch_status`, the same service layer an HTTP route would call,
so refusing an unknown product key, refusing a status outside `LAUNCH_STATUSES`, and writing the
audit row are all real service-layer behaviour a test can exercise directly — not logic that
lives only in this script.

Runs as the ordinary NOBYPASSRLS app role (`assert_app_role`), not the admin role: `products`
itself carries no tenant policy and needs no GUC, but this service's `audit_logs` does, and the
app role is the one every other write in this service already goes through
`session_scope(organization_id=...)` on. There is no organization a launch flip belongs to, so
the audit row is stamped with `PLATFORM_ORG`, exactly as `set_launch_status` documents.
"""

from __future__ import annotations

import argparse
import asyncio

from app.config import settings
from app.models import PLATFORM_ORG
from app.services import LAUNCH_STATUSES, set_launch_status
from platform_core.db import assert_app_role, init_engine, session_scope
from platform_core.errors import ProblemError
from platform_core.logging import get_logger

log = get_logger("product.launch")


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.launch", description="Flip a product's launch status"
    )
    parser.add_argument("product_key", help="the catalogue key, e.g. crm, hrms")
    parser.add_argument("status", choices=LAUNCH_STATUSES)
    args = parser.parse_args(argv)

    cfg = settings()
    engine = init_engine(cfg)
    await assert_app_role(engine, environment=cfg.environment)

    try:
        async with session_scope(organization_id=PLATFORM_ORG) as session:
            product = await set_launch_status(
                session,
                product_key=args.product_key,
                status=args.status,
                acting_as="operator:cli",
            )
    except ProblemError as exc:
        log.error(
            "product.launch.refused",
            product_key=args.product_key,
            status=args.status,
            error=str(exc),
        )
        return 1

    log.info("product.launch.set", product_key=args.product_key, status=product.status)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
