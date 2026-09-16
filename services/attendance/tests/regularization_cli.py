"""Standalone script: drives attendance-service's **real** write and consume paths for the
slice 2 attendance <-> approval round trip, and prints the resulting state as JSON.

Run as ``python -m tests.regularization_cli <subcommand> ...`` with `cwd` set to
`services/attendance` (so `app` and `tests` resolve the way attendance's own conftest does).
This exists so approval's cross-service tests
(`services/approval/tests/test_attendance_round_trip.py`) can drive attendance's *actual*
`request_regularization` write path and its *actual* `on_approval_request_decided` consumer
handler — instead of a hand-written envelope or a reimplemented state machine — without ever
importing attendance's `app` into approval's own pytest process (CLAUDE.md: "`import app` is
ambiguous across services... one process can only ever hold one"). This process holds
attendance's; the orchestrating test process holds approval's; the two talk only through this
subprocess's stdout and the shared Postgres cluster, the same boundary two real deployed
services would cross. Reused, not reinvented: this is the same shape
`services/employee/tests/emit_events.py` and `bump_employee.py` established for slice 1.

Subcommands
-----------
``raise``
    Punches in one employee (a real `check_in`) and raises a real regularization request
    (`request_regularization`) against that day. Prints the attendance id, the request id, and
    the real `hrms.time.regularization.requested` outbox row.

``override``
    HR directly regularizes an attendance record (`regularize_attendance`, ORG scope) — used to
    simulate an HR override landing between a request being raised and its decision arriving, the
    scenario that makes an in-flight request's transition illegal by the time it is applied.

``apply-decision``
    Reads a JSON array of `core.approval.request.decided` envelopes from stdin — real envelopes,
    built by the orchestrator from approval's own outbox rows via `platform_core.events
    .build_envelope`, the same function the real outbox relay calls — and feeds each one through
    attendance's real `app.consumers.on_approval_request_decided`, in order. This is what
    actually calls `apply_regularization_decision` and publishes the real
    `hrms.time.regularization.applied` outbox event(s). Prints the regularization request's and
    the attendance record's final state — including `decided_by_name`, once approval's own
    `.decided` payload carries it — plus every `.applied` row that got published, so the
    orchestrator can assert convergence under redelivery without spawning one subprocess per
    envelope.

``apply-opened``
    The `.opened` counterpart to ``apply-decision``, added alongside
    `app.consumers.on_approval_request_opened`: reads a JSON array of
    `core.approval.request.opened` envelopes from stdin and feeds each through that real handler,
    in order. `.opened` publishes no outbox event of its own (see the consumer's docstring), so
    this prints only the regularization request's resulting state — `approval_request_id`,
    `approval_opened_at`, `approver_employee_id` and `approver_name` — for the orchestrator to
    assert against.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from typing import Any

import structlog
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import selectors, services
from app.config import settings
from app.consumers import on_approval_request_decided, on_approval_request_opened
from app.models import SCHEMA, OutboxEvent
from app.schemas import CheckInIn, RegularizationRequestIn
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC, dispose_engine, init_engine
from tests.helpers import ctx_for

EVT_REGULARIZATION_APPLIED = "hrms.time.regularization.applied"


def _connect_args() -> dict[str, Any]:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


def _tenant_session(org_id: uuid.UUID) -> Any:
    engine = create_async_engine(settings().app_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    session = maker()

    @event.listens_for(session.sync_session, "after_begin")
    def _bind(_s: Any, _t: Any, connection: Any) -> None:
        connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org_id}', true)")

    return engine, session


def _outbox_row_to_json(row: OutboxEvent) -> dict[str, Any]:
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


async def _cmd_raise(args: argparse.Namespace) -> None:
    org_id = uuid.UUID(args.org)
    employee_id = uuid.UUID(args.employee)
    engine, session = _tenant_session(org_id)

    ctx_punch = ctx_for(org_id, employee_id, "hrms.time.attendance.write", Scope.OWN)
    async with session.begin():
        record = await services.check_in(
            session,
            ctx_punch,
            CheckInIn(work_mode="office", source="web", location_code=args.location),
        )

    ctx_request = ctx_for(org_id, employee_id, "hrms.time.attendance.request", Scope.OWN)
    async with session.begin():
        record = await selectors.get_attendance(session, org_id, record.id)
        assert record is not None
        reg = await services.request_regularization(
            session,
            ctx_request,
            record,
            RegularizationRequestIn(requested_status=args.requested_status, reason=args.reason),
        )

    async with session.begin():
        requested_row = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "hrms.time.regularization.requested",
                        OutboxEvent.aggregate_id == reg.id,
                    )
                )
            )
            .scalars()
            .one()
        )

    print(  # noqa: T201 -- stdout is the channel back to the parent test process
        json.dumps(
            {
                "attendance_id": str(record.id),
                "regularization_request_id": str(reg.id),
                "employee_id": str(employee_id),
                "requested_row": _outbox_row_to_json(requested_row),
            },
            default=str,
        ),
        flush=True,
    )
    await session.close()
    await engine.dispose()


async def _cmd_override(args: argparse.Namespace) -> None:
    org_id = uuid.UUID(args.org)
    attendance_id = uuid.UUID(args.attendance_id)
    engine, session = _tenant_session(org_id)

    ctx = ctx_for(org_id, uuid.uuid4(), "hrms.time.attendance.write", Scope.ORG)
    async with session.begin():
        record = await selectors.get_attendance(session, org_id, attendance_id)
        assert record is not None, "attendance record not visible to the override caller"
        record = await services.regularize_attendance(
            session, ctx, record, args.to, reason=args.reason
        )

    print(  # noqa: T201
        json.dumps({"attendance_id": str(record.id), "status": record.status}), flush=True
    )
    await session.close()
    await engine.dispose()


async def _cmd_apply_decision(args: argparse.Namespace) -> None:
    envelopes: list[dict[str, Any]] = json.loads(sys.stdin.read())
    assert envelopes, "apply-decision needs at least one envelope on stdin"

    init_engine(settings())
    try:
        for envelope in envelopes:
            await on_approval_request_decided(envelope)

        org_id = uuid.UUID(envelopes[-1]["payload"]["organization_id"])
        source_ref = uuid.UUID(envelopes[-1]["payload"]["source_ref"])
        engine, session = _tenant_session(org_id)
        try:
            async with session.begin():
                # The same lookups attendance's own `GET /attendance/{id}` route makes
                # (`app/selectors.py`) — proving the *read model* a requester's own view would
                # render, not just that a row somewhere changed.
                reg = await selectors.get_regularization(session, org_id, source_ref)

                attendance_out: dict[str, Any] | None = None
                if reg is not None:
                    record = await selectors.get_attendance(session, org_id, reg.attendance_id)
                    if record is not None:
                        attendance_out = {"id": str(record.id), "status": record.status}

                applied_rows = (
                    (
                        await session.execute(
                            select(OutboxEvent)
                            .where(
                                OutboxEvent.event_type == EVT_REGULARIZATION_APPLIED,
                                OutboxEvent.aggregate_id == source_ref,
                            )
                            .order_by(OutboxEvent.created_at)
                        )
                    )
                    .scalars()
                    .all()
                )

            print(  # noqa: T201
                json.dumps(
                    {
                        "regularization": (
                            {
                                "id": str(reg.id),
                                "status": reg.status,
                                "failure_reason": reg.failure_reason,
                                "approval_request_id": (
                                    str(reg.approval_request_id)
                                    if reg.approval_request_id
                                    else None
                                ),
                                "decided_at": (
                                    reg.decided_at.isoformat() if reg.decided_at else None
                                ),
                                "decided_by_name": reg.decided_by_name,
                            }
                            if reg is not None
                            else None
                        ),
                        "attendance": attendance_out,
                        "applied_rows": [_outbox_row_to_json(r) for r in applied_rows],
                    },
                    default=str,
                ),
                flush=True,
            )
        finally:
            await session.close()
            await engine.dispose()
    finally:
        await dispose_engine()


async def _cmd_apply_opened(args: argparse.Namespace) -> None:
    envelopes: list[dict[str, Any]] = json.loads(sys.stdin.read())
    assert envelopes, "apply-opened needs at least one envelope on stdin"

    init_engine(settings())
    try:
        for envelope in envelopes:
            await on_approval_request_opened(envelope)

        org_id = uuid.UUID(envelopes[-1]["payload"]["organization_id"])
        source_ref = uuid.UUID(envelopes[-1]["payload"]["source_ref"])
        engine, session = _tenant_session(org_id)
        try:
            async with session.begin():
                reg = await selectors.get_regularization(session, org_id, source_ref)

            print(  # noqa: T201
                json.dumps(
                    {
                        "regularization": (
                            {
                                "id": str(reg.id),
                                "status": reg.status,
                                "approval_request_id": (
                                    str(reg.approval_request_id)
                                    if reg.approval_request_id
                                    else None
                                ),
                                "approval_opened_at": (
                                    reg.approval_opened_at.isoformat()
                                    if reg.approval_opened_at
                                    else None
                                ),
                                "approver_employee_id": (
                                    str(reg.approver_employee_id)
                                    if reg.approver_employee_id
                                    else None
                                ),
                                "approver_name": reg.approver_name,
                            }
                            if reg is not None
                            else None
                        ),
                    },
                    default=str,
                ),
                flush=True,
            )
        finally:
            await session.close()
            await engine.dispose()
    finally:
        await dispose_engine()


def _quiet_stdout_logging() -> None:
    """`app.consumers` and `app.services` log through `platform_core.logging.get_logger`, which
    — unconfigured, as it is in this one-shot script — falls back to structlog's own default
    console renderer on stdout. Stdout here is the channel back to the parent test process (one
    JSON line, see each `_cmd_*` above); a log line ahead of it would corrupt that channel. This
    reconfigures structlog to render to stderr instead, leaving stdout exclusively ours."""
    structlog.configure(
        processors=[structlog.processors.JSONRenderer()],
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )


def main() -> None:
    _quiet_stdout_logging()
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p_raise = sub.add_parser("raise")
    p_raise.add_argument("--org", required=True)
    p_raise.add_argument("--employee", required=True)
    p_raise.add_argument("--location", default=None)
    p_raise.add_argument("--requested-status", default="half_day")
    p_raise.add_argument("--reason", default="left early")

    p_override = sub.add_parser("override")
    p_override.add_argument("--org", required=True)
    p_override.add_argument("--attendance-id", required=True)
    p_override.add_argument("--to", required=True)
    p_override.add_argument("--reason", default="HR override")

    sub.add_parser("apply-decision")
    sub.add_parser("apply-opened")

    args = parser.parse_args()
    if args.command == "raise":
        asyncio.run(_cmd_raise(args))
    elif args.command == "override":
        asyncio.run(_cmd_override(args))
    elif args.command == "apply-decision":
        asyncio.run(_cmd_apply_decision(args))
    elif args.command == "apply-opened":
        asyncio.run(_cmd_apply_opened(args))


if __name__ == "__main__":
    main()
