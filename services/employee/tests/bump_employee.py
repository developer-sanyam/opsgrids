"""Standalone script: bumps an **existing** employee's hierarchy state through employee's real
write path, and prints the resulting `hrms.people.employee.updated` outbox row as JSON.

Companion to `tests/emit_events.py`, which only ever creates a fresh employee. This one targets
an employee id that already exists — typically one seeded by `tests/serve_hierarchy.py` for a
live server a cross-service test is racing a backfill against — so a test can produce a second,
real, higher `hierarchy_version` for a row the live server has already answered a page about.

Run as ``python -m tests.bump_employee --org <uuid> --employee <uuid> --location <code>`` with
`cwd` set to `services/employee`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from typing import Any

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import selectors, services
from app.config import settings
from app.models import SCHEMA, OutboxEvent
from app.schemas import EmployeePatch
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC
from tests.helpers import ctx_for


def _connect_args() -> dict[str, Any]:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


def _row_to_json(row: OutboxEvent) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "organization_id": str(row.organization_id),
        "event_type": row.event_type,
        "event_version": row.event_version,
        "aggregate_type": row.aggregate_type,
        "aggregate_id": str(row.aggregate_id),
        "payload": row.payload,
        "correlation_id": row.correlation_id,
        "causation_id": str(row.causation_id) if row.causation_id else None,
        "actor": row.actor,
        "created_at": row.created_at.isoformat(),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--org", required=True)
    parser.add_argument("--employee", required=True)
    parser.add_argument("--location", default="BLR")
    args = parser.parse_args()
    org_id = uuid.UUID(args.org)
    employee_id = uuid.UUID(args.employee)

    cfg = settings()
    engine = create_async_engine(cfg.app_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    session = maker()

    @event.listens_for(session.sync_session, "after_begin")
    def _bind(_s: Any, _t: Any, connection: Any) -> None:
        connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org_id}', true)")

    async with session.begin():
        employee = await selectors.get_employee(session, org_id, employee_id)
        assert employee is not None, f"{employee_id} not found in org {org_id}"
        ctx = ctx_for(org_id, employee, "hrms.people.employee.write", Scope.ORG)
        await services.update_employee(
            session, ctx, employee, EmployeePatch(location_code=args.location)
        )

    async with session.begin():
        row = (
            (
                await session.execute(
                    select(OutboxEvent)
                    .where(
                        OutboxEvent.aggregate_id == employee_id,
                        OutboxEvent.event_type == "hrms.people.employee.updated",
                    )
                    .order_by(OutboxEvent.created_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .one()
        )

    # Stdout is the channel back to the parent test process, not debug output.
    print(json.dumps(_row_to_json(row), default=str), flush=True)  # noqa: T201

    await session.close()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
