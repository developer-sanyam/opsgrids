"""Give an organization one company and one deal — its CRM, in miniature.

CRM spans two services: customer-service owns the account book, deal-service owns the
pipeline. Every service exposes a top-level `app`, so this file re-invokes itself once per
service (`--child`), each child running inside that service's directory — the same shape
`tools/demo/seed_crm.py` uses, and the reason the demo seeder is not simply reused here: this
journey wants two tenants with a handful of rows it names, not the ninety-row demo fixture.

Both halves write through the real `services.create_*` paths as the **NOBYPASSRLS app role**
with the tenant GUC bound per transaction, exactly as a request does. Seeding with the admin
role would put rows in that the policies would never have accepted, and the cross-tenant step
later in the journey would then be asserting against data row-level security had no part in
creating.

The deal's `company_id` is a plain UUID pointing at a row in another schema — never a foreign
key (ADR-0000 §10). The child derives it deterministically from the organization id so the
pipeline lands on the book's row without the two processes talking to each other.

Usage:
    _seed_crm.py <customer_service_dir> <org_id> <user_id> <membership_id> <label> <reset|keep>
    _seed_crm.py --child <service> <service_dir> <org_id> <user_id> <membership_id> <label> <mode>
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

#: What the journey reads back through the gateway. Distinct per tenant, so an assertion that
#: alpha sees exactly its own row cannot pass by coincidence.
FIXTURES = {
    "alpha": {"company": "Alpha Anvils Ltd", "deal": "Alpha forge retooling", "value": 4_500_000},
    "beta": {"company": "Beta Bearings Ltd", "deal": "Beta spindle contract", "value": 2_750_000},
}


def seed_id(org_id: uuid.UUID, key: str) -> uuid.UUID:
    """The deterministic id for a fixture key — the same on both sides of the boundary."""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"atmora-e2e-crm:{org_id}:{key}")


async def _child(
    service: str,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    membership_id: uuid.UUID,
    label: str,
    mode: str,
) -> None:
    """Run one service's half. The service directory is already on `sys.path`."""
    from sqlalchemy import event, text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app import services  # type: ignore[import-not-found]
    from app.config import settings  # type: ignore[import-not-found]
    from app.models import SCHEMA, Base  # type: ignore[import-not-found]
    from platform_core.auth import Principal, Scope, ScopeContext
    from platform_core.db import TENANT_GUC

    fixture = FIXTURES[label]
    cfg = settings()
    connect_args = {"server_settings": {"search_path": f"{SCHEMA},public"}}

    # One reset for the whole run, before the first tenant is seeded. As the admin role,
    # because TRUNCATE is deliberately withheld from the application roles.
    if mode == "reset":
        admin = create_async_engine(cfg.admin_dsn, connect_args=connect_args)
        tables = ", ".join(f'"{SCHEMA}"."{t.name}"' for t in reversed(Base.metadata.sorted_tables))
        async with admin.begin() as conn:
            await conn.execute(text(f"TRUNCATE {tables} CASCADE"))
        await admin.dispose()

    permission = "crm.book.company.write" if service == "customer" else "crm.pipeline.deal.write"
    engine = create_async_engine(cfg.app_dsn, connect_args=connect_args)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    ctx = ScopeContext(
        principal=Principal(
            sub=user_id,
            organization_id=org_id,
            membership_id=membership_id,
            perms=[f"{permission}:ORG"],
            entitlements=["crm"],
        ),
        scope=Scope.ORG,
        permission=permission,
    )

    async with maker() as session:

        @event.listens_for(session.sync_session, "after_begin")
        def _bind(_s: Any, _t: Any, connection: Any) -> None:
            # Transaction-local (the third argument): the pool reuses connections across
            # requests, and a session-level GUC is a cross-tenant leak.
            connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org_id}', true)")

        async with session.begin():
            if service == "customer":
                await services.create_owner(
                    session,
                    ctx,
                    full_name=f"{label.title()} Rep",
                    email=f"rep-{label}-{org_id.hex[:8]}@example.com",
                    role="Account executive",
                    owner_id=seed_id(org_id, "owner"),
                )
                await services.create_company(
                    session,
                    ctx,
                    name=fixture["company"],
                    owner_id=seed_id(org_id, "owner"),
                    industry="Manufacturing",
                    city="Pune",
                    country="IN",
                    status="customer",
                    company_id=seed_id(org_id, "company"),
                )
            else:
                await services.create_deal(
                    session,
                    ctx,
                    name=fixture["deal"],
                    company_id=seed_id(org_id, "company"),
                    owner_id=seed_id(org_id, "owner"),
                    value_minor=int(fixture["value"]),
                    close_date=date(2026, 12, 31),
                    stage="proposal",
                    last_activity_at=datetime.now(UTC),
                    deal_id=seed_id(org_id, "deal"),
                )

    await engine.dispose()
    print(f"{service}: seeded {label}")


def _run_child() -> None:
    service, service_dir = sys.argv[2], Path(sys.argv[3]).resolve()
    sys.path.insert(0, str(service_dir))
    asyncio.run(
        _child(
            service,
            uuid.UUID(sys.argv[4]),
            uuid.UUID(sys.argv[5]),
            uuid.UUID(sys.argv[6]),
            sys.argv[7],
            sys.argv[8],
        )
    )


def _run_parent() -> None:
    """Book first, so the pipeline's cross-schema reference lands on a row that exists."""
    root = Path(sys.argv[1]).resolve().parent.parent
    org, user, membership, label, mode = sys.argv[2:7]
    for service in ("customer", "deal"):
        service_dir = root / "services" / service
        subprocess.run(
            [
                sys.executable,
                __file__,
                "--child",
                service,
                str(service_dir),
                org,
                user,
                membership,
                label,
                mode,
            ],
            cwd=service_dir,
            check=True,
        )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        _run_child()
    else:
        _run_parent()
