"""Snapshot `entitled_products` onto subscriptions that predate it. A one-off deploy step.

Until bundles fanned out, a subscription entitled the one product it was bought under, and a
`suite` subscription entitled `suite` — a key no gateway route checks, so the customer was
entitled to nothing. New subscriptions snapshot their members at subscribe and change-plan
(`services.subscribe`/`change_plan`); this fills in the rows written before that, and for each
**bundle** emits `commerce.subscription.entitlements_resynced` so product-service projects the
members and account-service installs their manifests.

Why a tool and not a migration. The member list lives in catalog, in another schema. A migration
that read `catalog.features` would be a cross-schema read baked into subscription's history — the
one thing that turns a later database-per-service split from a configuration change into a
rewrite (ADR-0000 §10). So it asks catalog over the same service-token internal API a subscribe
does, and a row whose pin it cannot resolve stays NULL (read as `[product_key]`) rather than
being guessed at.

Connection discipline is the dunning sweep's: **discovery** is cross-tenant by construction — it
asks which organizations have a NULL snapshot — and runs on the admin DSN, which init-db.sql
reserves for exactly this ("migrations and backfills"). **Every write** runs on the NOBYPASSRLS
app role inside `session_scope(organization_id=...)`, one transaction per subscription, so the
snapshot, its audit row and its outbox event commit together or not at all. Re-running is safe:
a row that already has a snapshot is skipped.

Usage, from `services/subscription`, after catalog and product are deployed (see the RUNBOOK):

    uv run python -m app.backfill [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.catalog import PlanCatalog
from app.models import Subscription
from platform_core.logging import get_logger

log = get_logger("subscription.backfill")

#: A tenant-bound transaction for one organization. `session_scope` in production; a test
#: substitutes its own so the backfill runs without a process-wide engine.
WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]


@dataclass
class BackfillReport:
    """What one run did. Printed as JSON so the deploy log is the evidence."""

    considered: int = 0
    written: int = 0
    bundles: int = 0
    unresolved: int = 0
    failed: int = 0


async def pending(
    discovery_factory: async_sessionmaker[AsyncSession],
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """`(organization_id, subscription_id)` for every row with no snapshot.

    Ids only: every decision and every write happens back under the tenant binding. The bypass
    buys discovery, never authority — the dunning sweep's rule.
    """
    async with discovery_factory() as session, session.begin():
        stmt = (
            select(Subscription.organization_id, Subscription.id)
            .where(Subscription.entitled_products_snapshot.is_(None))
            .order_by(Subscription.organization_id, Subscription.id)
        )
        return [(row[0], row[1]) for row in (await session.execute(stmt)).all()]


async def backfill(
    *,
    discovery_factory: async_sessionmaker[AsyncSession],
    work_scope: WorkScope,
    catalog: PlanCatalog,
    dry_run: bool = False,
) -> BackfillReport:
    """Resync every subscription with no snapshot, one tenant-bound transaction each."""
    from app import domain, selectors, services

    report = BackfillReport()
    for organization_id, subscription_id in await pending(discovery_factory):
        report.considered += 1
        try:
            async with work_scope(organization_id) as session:
                subscription = await selectors.get_subscription(session, subscription_id)
                if subscription is None or subscription.entitled_products_snapshot is not None:
                    continue
                version = await catalog.get_plan_version(subscription.plan_version_id)
                if version is None:
                    report.unresolved += 1
                    log.warning(
                        "entitlements.backfill.unresolvable_pin",
                        subscription_id=str(subscription_id),
                        plan_version_id=str(subscription.plan_version_id),
                    )
                    continue
                members = domain.entitled_products(
                    subscription.product_key, version.feature_pairs()
                )
                is_bundle = members != [subscription.product_key]
                if dry_run:
                    log.info(
                        "entitlements.backfill.would_write",
                        subscription_id=str(subscription_id),
                        entitled_products=members,
                    )
                    report.bundles += int(is_bundle)
                    continue
                if await services.resync_entitlements(session, subscription, version):
                    report.written += 1
                    report.bundles += int(is_bundle)
        except Exception as exc:  # one bad row must not strand the rest of the fleet
            report.failed += 1
            log.error(
                "entitlements.backfill.failed",
                subscription_id=str(subscription_id),
                error=str(exc)[:300],
            )
    return report


async def main() -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.catalog import HttpPlanCatalog
    from app.config import settings
    from app.models import SCHEMA
    from platform_core.db import assert_app_role, dispose_engine, init_engine, session_scope

    parser = argparse.ArgumentParser(description="Snapshot entitled_products onto old rows.")
    parser.add_argument("--dry-run", action="store_true", help="report, write nothing")
    args = parser.parse_args()

    cfg = settings()
    engine = init_engine(cfg)
    # Writes run on the app role under a bound tenant; started on a BYPASSRLS DSN by mistake,
    # row-level security would be silently inert (ADR-0002).
    await assert_app_role(engine, environment=cfg.environment)
    discovery_engine = create_async_engine(
        cfg.admin_dsn, connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}}
    )
    catalog = HttpPlanCatalog(cfg.catalog_service_url, cfg)
    try:
        report = await backfill(
            discovery_factory=async_sessionmaker(discovery_engine, expire_on_commit=False),
            work_scope=lambda organization_id: session_scope(organization_id=organization_id),
            catalog=catalog,
            dry_run=args.dry_run,
        )
    finally:
        await catalog.aclose()
        await discovery_engine.dispose()
        await dispose_engine()
    print(json.dumps({"dry_run": args.dry_run, **asdict(report)}))  # noqa: T201 -- the deploy log


if __name__ == "__main__":
    asyncio.run(main())
