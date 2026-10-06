"""Audit every sellable bundle against the product registry. A deploy check; exits non-zero.

Catalog validates a bundle's members when the draft is authored, but the registry moves after
that: a member's launch is rolled back to `soon`, a product is folded into the platform, a
bundle is authored before the rule existed. This re-runs `domain.bundle_member_problems` over
every published or grandfathered version that declares `bundled_products` — and flags a version
of a registered bundle product that declares none (ADR-0030 §3) — and says what it finds:

* on a plan that is still **active** (offered) a problem is an **error** — that rung is being
  sold with a member the registry cannot vouch for — and the run exits 1;
* on a **retired** plan it is a **warning**: nobody can buy it any more, but somebody may still
  be pinned to it, and product-service is already refusing to project the bad member for them.

Read-only. It lives in catalog rather than in subscription's backfill because this is where the
versions and the rule both live — subscription can only see the versions somebody is pinned to.

Usage, from `services/catalog`, once product-service is up (see the RUNBOOK):

    uv run python -m app.bundle_audit
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict, dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import domain
from app.models import (
    FEATURE_BUNDLED_PRODUCTS,
    PLAN_RETIRED,
    VERSION_GRANDFATHERED,
    VERSION_PUBLISHED,
    Plan,
    PlanVersion,
)
from app.registry import ProductRegistry


@dataclass
class Finding:
    plan: str
    version_no: int
    plan_version_id: str
    problems: list[str]


@dataclass
class AuditReport:
    audited: int = 0
    errors: list[Finding] = field(default_factory=list)
    warnings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


async def audit(session: AsyncSession, registry: ProductRegistry) -> AuditReport:
    """Every sellable bundle version, judged against the registry as it is now."""
    products = await registry.products()
    stmt = (
        select(PlanVersion, Plan)
        .join(Plan, Plan.id == PlanVersion.plan_id)
        .where(PlanVersion.state.in_((VERSION_PUBLISHED, VERSION_GRANDFATHERED)))
        .options(selectinload(PlanVersion.features))
        .order_by(Plan.product_key, Plan.code, PlanVersion.version_no)
    )
    report = AuditReport()
    for version, plan in (await session.execute(stmt)).all():
        declared = {f.feature_key: f.value for f in version.features}
        entry = products.get(plan.product_key)
        if FEATURE_BUNDLED_PRODUCTS in declared:
            report.audited += 1
            problems = domain.bundle_member_problems(
                entry, plan.product_key, declared[FEATURE_BUNDLED_PRODUCTS], products
            )
        elif entry is not None and entry.is_bundle:
            # A bundle product's version that names no members entitles only the bundle's own
            # key, which routes nowhere (ADR-0030 §3) — frozen before publish refused it.
            report.audited += 1
            problems = [
                f"{plan.product_key!r} is a bundle, but this version declares no bundled_products"
            ]
        else:
            continue
        if not problems:
            continue
        finding = Finding(
            plan=f"{plan.product_key}/{plan.code}",
            version_no=version.version_no,
            plan_version_id=str(version.id),
            problems=problems,
        )
        (report.warnings if plan.state == PLAN_RETIRED else report.errors).append(finding)
    return report


async def main() -> int:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import settings
    from app.models import SCHEMA
    from app.registry import HttpProductRegistry

    cfg = settings()
    engine = create_async_engine(
        cfg.app_dsn, connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}}
    )
    registry = HttpProductRegistry(cfg.product_service_url, cfg)
    try:
        async with async_sessionmaker(engine)() as session, session.begin():
            report = await audit(session, registry)
    finally:
        await registry.aclose()
        await engine.dispose()
    print(json.dumps({"ok": report.ok, **asdict(report)}, indent=2))  # noqa: T201 -- the deploy log
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
