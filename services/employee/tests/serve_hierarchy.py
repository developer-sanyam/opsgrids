"""Standalone script: seeds two organizations through employee's **real** write path, then
serves the **real** FastAPI app (`app.main:app`) over a real TCP port, so approval's
cross-service tests can drive employee's actual `/internal/v1/hierarchy` endpoint — the exact
route `services/employee/app/api/internal.py` exposes — across a real process boundary, the
same one the deployed backfill crosses.

Run as::

    python -m tests.serve_hierarchy --org-a <uuid> --org-b <uuid> \
        --count-a <n> --count-b <n> --port <port>

with `cwd` set to `services/employee`. Prints exactly one JSON line to stdout once seeding is
complete and the server is about to start listening — the parent must read that line (and only
that line) before issuing any request against `--port`. Runs until it receives SIGTERM.

Two organizations, not one, because the cross-tenant question a single-org run cannot answer is
exactly the one that matters here: does employee's endpoint — and everything approval's backfill
does with what it returns — ever let one organization's page carry another's rows.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from typing import Any

import uvicorn
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import services
from app.config import settings
from app.main import app
from app.models import SCHEMA, Employee
from app.schemas import EmployeeIn
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC
from tests.helpers import ctx_for


def _connect_args() -> dict[str, Any]:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


async def _seed(org_id: uuid.UUID, count: int, prefix: str) -> list[dict[str, str]]:
    cfg = settings()
    engine = create_async_engine(cfg.app_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    session = maker()

    @event.listens_for(session.sync_session, "after_begin")
    def _bind(_s: Any, _t: Any, connection: Any) -> None:
        connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org_id}', true)")

    ctx = ctx_for(org_id, Employee(id=uuid.uuid4()), "hrms.people.employee.write", Scope.ORG)
    seeded: list[dict[str, str]] = []
    async with session.begin():
        for i in range(count):
            employee = await services.create_employee(
                session,
                ctx,
                EmployeeIn(
                    employee_no=f"{prefix}{i:04d}",
                    full_name=f"{prefix} Person {i}",
                    location_code="MUM",
                ),
            )
            seeded.append({"employee_id": str(employee.id), "employee_no": employee.employee_no})

    await session.close()
    await engine.dispose()
    return seeded


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--org-a", required=True)
    parser.add_argument("--org-b", required=True)
    parser.add_argument("--count-a", type=int, default=5)
    parser.add_argument("--count-b", type=int, default=2)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()

    org_a = uuid.UUID(args.org_a)
    org_b = uuid.UUID(args.org_b)

    seeded_a = await _seed(org_a, args.count_a, "AORG")
    seeded_b = await _seed(org_b, args.count_b, "BORG")

    # Exactly one line, read by the parent before it sends any request.
    print(json.dumps({"ready": True, "org_a": seeded_a, "org_b": seeded_b}), flush=True)  # noqa: T201

    config = uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
