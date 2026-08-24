"""Seed the support desk for the demo org — 8 agents, 20 tickets with their threads, and the
SLA policy. The data lives in `support_fixtures.json` beside this script and mirrors
`frontend/product-support/src/api/fixtures.ts` name-for-name, so the live screens look like the
fixtures the user already approved.

Run **inside `services/support`** as a subprocess (like `tools/demo/seed_employee.py`): it
connects as the NOBYPASSRLS app role and binds the tenant GUC on every transaction (the GUC is
transaction-local), so writes go through the same row-level-security path a real request does.
Agents and the policy are upserted; a ticket whose number already exists is skipped — so
re-running does not duplicate.

Usage: seed_support.py <support_service_dir> <org_id> <admin_user_id> <admin_membership_id>

Point `DATABASE_URL` at the intended database before running — the demo targets `atmora_wt`
in this workspace, never the shared `atmora`.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

FIXTURES_PATH = Path(__file__).resolve().parent / "support_fixtures.json"


def load_fixtures() -> dict[str, Any]:
    """The demo desk, with ISO timestamps parsed into aware datetimes."""
    data: dict[str, Any] = json.loads(FIXTURES_PATH.read_text())
    for ticket in data["tickets"]:
        for key in ("created_at", "updated_at", "first_response_at", "sla_due"):
            ticket[key] = datetime.fromisoformat(ticket[key]) if ticket[key] else None
        for message in ticket["messages"]:
            message["at"] = datetime.fromisoformat(message["at"])
    return data


async def run_seed(session: Any, ctx: Any) -> dict[str, int]:
    """Insert the demo desk into the caller's transaction, idempotently. Returns counts.

    The caller owns the transaction (so tests and the CLI wrap it the same way). Uses the
    service's own write path, so every row goes through the tenant chokepoint a request does.
    """
    from app import (  # type: ignore[import-not-found]  # loaded from the service dir
        selectors,
        services,
    )

    data = load_fixtures()

    for agent in data["agents"]:
        await services.upsert_agent(session, ctx, **agent)

    policy = data["sla_policy"]
    await services.upsert_sla_policy(
        session,
        ctx,
        name=policy["name"],
        business_hours=policy["business_hours"],
        coverage=policy["coverage"],
        targets=policy["targets"],
    )

    created = 0
    skipped = 0
    for ticket in data["tickets"]:
        if await selectors.get_ticket(session, ctx.organization_id, ticket["number"]):
            skipped += 1
            continue
        await services.create_ticket(
            session,
            ctx,
            number=ticket["number"],
            subject=ticket["subject"],
            requester=ticket["requester"],
            priority=ticket["priority"],
            status=ticket["status"],
            channel=ticket["channel"],
            assignee_ref=ticket["assignee_ref"],
            tags=ticket["tags"],
            created_at=ticket["created_at"],
            updated_at=ticket["updated_at"],
            first_response_at=ticket["first_response_at"],
            sla_due=ticket["sla_due"],
            sla_breached=ticket["sla_breached"],
            satisfaction=ticket["satisfaction"],
            messages=ticket["messages"],
        )
        created += 1

    return {
        "agents": len(data["agents"]),
        "tickets_created": created,
        "tickets_skipped": skipped,
        "tickets_total": len(data["tickets"]),
    }


def _main() -> None:
    import asyncio
    import sys

    service_dir = Path(sys.argv[1]).resolve()
    org_id = uuid.UUID(sys.argv[2])
    admin_user_id = uuid.UUID(sys.argv[3])
    admin_membership_id = uuid.UUID(sys.argv[4])
    sys.path.insert(0, str(service_dir))

    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import settings  # type: ignore[import-not-found]  # loaded from the service dir
    from app.models import SCHEMA  # type: ignore[import-not-found]  # loaded from the service dir
    from platform_core.auth import Principal, Scope, ScopeContext
    from platform_core.db import TENANT_GUC

    def _ctx() -> ScopeContext:
        principal = Principal(
            sub=admin_user_id,
            organization_id=org_id,
            membership_id=admin_membership_id,
            perms=["support.desk.ticket.write:ORG", "support.desk.ticket.read:ORG"],
            entitlements=["support"],
        )
        return ScopeContext(
            principal=principal, scope=Scope.ORG, permission="support.desk.ticket.write"
        )

    async def _run() -> None:
        cfg = settings()
        connect_args = {"server_settings": {"search_path": f"{SCHEMA},public"}}
        engine = create_async_engine(cfg.app_dsn, connect_args=connect_args)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:

            @event.listens_for(session.sync_session, "after_begin")
            def _bind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
                connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org_id}', true)")

            async with session.begin():
                result = await run_seed(session, _ctx())
        await engine.dispose()
        print(json.dumps(result))

    asyncio.run(_run())


if __name__ == "__main__":
    _main()
