"""Standalone script: drives employee-service's **real** write path and prints the resulting
outbox rows as JSON, one per line, in commit order.

Run as ``python -m tests.emit_events --org <uuid>`` with `cwd` set to `services/employee` (so
`app` and `tests` resolve the way employee's own conftest does). This exists so approval's
cross-service tests can assert against employee's *actual* publish path
(`services/employee/app/services.py`) instead of a hand-written envelope, without importing
employee's `app` into approval's own pytest process — CLAUDE.md: "`import app` is ambiguous
across services... one process can only ever hold one." This process holds employee's;
approval's test process holds approval's, and the two talk only through this subprocess's
stdout and the shared Postgres cluster, the same boundary two real deployed services would
cross.

Four real writes against one employee, in order, so the printed sequence carries a real,
monotonic `hierarchy_version` run (0, 1, 2, 3):

    1. onboard                      -> hrms.people.employee.onboarded  (version 0)
    2. update location_code         -> hrms.people.employee.updated    (version 1)
    3. update full_name             -> hrms.people.employee.updated    (version 2)
    4. transition to "active"       -> hrms.people.employee.transitioned (version 3)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from typing import Any

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import services
from app.config import settings
from app.models import SCHEMA, Employee, OutboxEvent
from app.schemas import EmployeeIn, EmployeePatch
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
    parser.add_argument("--employee-no", default="XSVC001")
    args = parser.parse_args()
    org_id = uuid.UUID(args.org)

    cfg = settings()
    engine = create_async_engine(cfg.app_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    session = maker()

    @event.listens_for(session.sync_session, "after_begin")
    def _bind(_s: Any, _t: Any, connection: Any) -> None:
        connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org_id}', true)")

    ctx = ctx_for(org_id, Employee(id=uuid.uuid4()), "hrms.people.employee.write", Scope.ORG)

    async with session.begin():
        employee = await services.create_employee(
            session,
            ctx,
            EmployeeIn(employee_no=args.employee_no, full_name="Cross Service Employee"),
        )

    async with session.begin():
        await services.update_employee(session, ctx, employee, EmployeePatch(location_code="DEL"))

    async with session.begin():
        await services.update_employee(
            session, ctx, employee, EmployeePatch(full_name="Cross Service Employee Renamed")
        )

    async with session.begin():
        await services.transition_status(session, ctx, employee, "active")

    async with session.begin():
        rows = (
            (
                await session.execute(
                    select(OutboxEvent)
                    .where(OutboxEvent.aggregate_id == employee.id)
                    .order_by(OutboxEvent.created_at)
                )
            )
            .scalars()
            .all()
        )

    # Stdout is the channel back to the parent test process, not debug output.
    for row in rows:
        print(json.dumps(_row_to_json(row), default=str), flush=True)  # noqa: T201

    await session.close()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
