"""Test helpers shared between conftest and the test modules.

Kept out of `conftest.py` because conftest is loaded by pytest rather than imported, so
importing *from* it is fragile — this module is a plain import that works the same way
everywhere.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ApprovalPolicy, ApprovalRequest, ApprovalStep
from platform_core.auth import Principal, Scope, ScopeContext
from platform_core.events import build_envelope

READ = "core.approval.request.read"
DECIDE = "core.approval.request.decide"
REQUEST_TYPE_REGULARIZATION = "hrms.time.regularization"
REQUEST_TYPE_LEAVE = "hrms.time.leave"


def principal_for(
    org_id: uuid.UUID,
    subject_ref: uuid.UUID | None,
    perms: list[str],
    *,
    locations: list[str] | None = None,
) -> Principal:
    """The principal the gateway would have forwarded.

    `subject_ref` is the caller's own employee id — what OWN scope resolves against. A `None`
    subject_ref is an authenticated caller with no employee link, who (per ADR-0006 §6) can
    decide nothing and, here, owns no chain either. `locations` is what a LOCATION-scoped grant
    compares against (`ScopeContext.assert_can_touch`) — empty by default, the same "nothing to
    match" shape a caller with no assigned location actually has.
    """
    return Principal(
        sub=uuid.uuid4(),
        organization_id=org_id,
        membership_id=uuid.uuid4(),
        subject_ref=subject_ref,
        perms=perms,
        entitlements=["core"],
        locations=locations or [],
    )


def ctx_for(
    org_id: uuid.UUID, subject_ref: uuid.UUID | None, permission: str, scope: Scope
) -> ScopeContext:
    """A scope context as `require(...)` would have built it."""
    principal = principal_for(org_id, subject_ref, [f"{permission}:{scope.value}"])
    return ScopeContext(principal=principal, scope=scope, permission=permission)


def employee_onboarded_envelope(
    *,
    event_id: uuid.UUID | None = None,
    organization_id: uuid.UUID,
    employee_id: uuid.UUID,
    reporting_manager_id: uuid.UUID | None = None,
    dotted_line_manager_id: uuid.UUID | None = None,
    status: str = "active",
    display_name: str = "Ananya Rao",
    employee_no: str = "EMP0001",
    location_code: str | None = "MUM",
    hierarchy_version: int | None = 1,
    event_type: str = "hrms.people.employee.onboarded",
) -> dict:
    """An envelope shaped the way `app/consumers.py` expects, per ADR-0006 §2 / §12.

    `hierarchy_version=None` omits the field entirely (an un-upgraded employee pod), matching
    the payload shape `services/employee/app/services.py` publishes today.
    """
    payload = {
        "organization_id": str(organization_id),
        "employee_id": str(employee_id),
        "reporting_manager_id": str(reporting_manager_id) if reporting_manager_id else None,
        "dotted_line_manager_id": (str(dotted_line_manager_id) if dotted_line_manager_id else None),
        "status": status,
        "display_name": display_name,
        "employee_no": employee_no,
        "location_code": location_code,
    }
    if hierarchy_version is not None:
        payload["hierarchy_version"] = hierarchy_version

    return {
        "event_id": str(event_id or uuid.uuid4()),
        "event_type": event_type,
        "version": 1,
        "organization_id": str(organization_id),
        "aggregate_type": "employee",
        "aggregate_id": str(employee_id),
        "occurred_at": datetime.now(UTC).isoformat(),
        "producer": "employee",
        "correlation_id": None,
        "causation_id": None,
        "actor": None,
        "schema_uri": f"opsgrids://contracts/{event_type}/v1",
        "payload": payload,
    }


async def open_request(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    source_ref: uuid.UUID | None = None,
    requester_employee_id: uuid.UUID,
    summary: dict[str, Any] | None = None,
    request_note: str | None = "Forgot to punch in",
) -> Any:
    """`open_regularization_request`, wrapped in its own transaction — the shape every slice 2
    test that needs a pending request to decide starts from."""
    from app.services import open_regularization_request

    async with session.begin():
        return await open_regularization_request(
            session,
            organization_id=org_id,
            source_ref=source_ref or uuid.uuid4(),
            requester_employee_id=requester_employee_id,
            summary=summary or {"date": "2026-09-12", "from": "absent", "to": "present"},
            request_note=request_note,
        )


async def open_leave(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    source_ref: uuid.UUID | None = None,
    requester_employee_id: uuid.UUID,
    summary: dict[str, Any] | None = None,
    request_note: str | None = "family trip",
) -> Any:
    """`open_leave_request`, wrapped in its own transaction — `open_request`'s twin for the
    second request type this service routes (HRMS leave slice 1)."""
    from app.services import open_leave_request

    async with session.begin():
        return await open_leave_request(
            session,
            organization_id=org_id,
            source_ref=source_ref or uuid.uuid4(),
            requester_employee_id=requester_employee_id,
            summary=summary
            or {
                "leave_type_id": str(uuid.uuid4()),
                "start_date": "2027-03-01",
                "end_date": "2027-03-02",
                "half_day_start": False,
                "half_day_end": False,
                "total_half_days": 4,
            },
            request_note=request_note,
        )


async def seed_person(
    session: AsyncSession,
    org_id: uuid.UUID,
    employee_id: uuid.UUID,
    *,
    reporting_manager_id: uuid.UUID | None = None,
    status: str = "active",
    display_name: str = "Someone",
    employee_no: str | None = None,
    location_code: str | None = "MUM",
    source_version: int = 1,
) -> None:
    """A `hierarchy_nodes` row, shaped the way `app/consumers.py` would have written it. Kept
    here rather than duplicated per test module (`tests/test_chain.py`'s `_seed` predates this
    and is left alone — moving it would touch a file this slice does not own the diff of)."""
    from app.services import upsert_hierarchy_node

    await upsert_hierarchy_node(
        session,
        organization_id=org_id,
        employee_id=employee_id,
        reporting_manager_id=reporting_manager_id,
        dotted_line_manager_id=None,
        status=status,
        display_name=display_name,
        employee_no=employee_no or f"EMP{str(employee_id)[:8]}",
        location_code=location_code,
        source_version=source_version,
        source_occurred_at=datetime.now(UTC),
    )


async def seed_pending_request_with_step(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    requester_employee_id: uuid.UUID,
    approver_employee_id: uuid.UUID | None,
    acting_for_employee_id: uuid.UUID | None = None,
    delegation_id: uuid.UUID | None = None,
    reason: str = "assigned",
    level: int = 1,
    opened_at: datetime | None = None,
    due_at: datetime | None = None,
    source_ref: uuid.UUID | None = None,
) -> tuple[ApprovalRequest, ApprovalStep]:
    """Insert a pending request and its one open step directly, bypassing
    `open_regularization_request`'s routing — for the SLA sweep and escalation tests, which need
    a step whose `due_at` is already in the past without waiting real minutes for one the routing
    path opened. `trg_approval_steps_append_only` only restricts an `UPDATE` to an open step (the
    predecessor's own `due_at` cannot be edited after insert, only carried forward by a new row —
    ADR-0006 §5); it does not restrict what an `INSERT` may set, which is what this relies on.

    Caller must `session.begin()`/commit around this — matches every other seeding helper here.
    """
    now = opened_at or datetime.now(UTC)
    request = ApprovalRequest(
        organization_id=org_id,
        request_type=REQUEST_TYPE_REGULARIZATION,
        source_ref=source_ref or uuid.uuid4(),
        requester_employee_id=requester_employee_id,
        summary={"date": "2026-09-12", "from": "absent", "to": "present"},
        request_note="Forgot to punch in",
        status="pending",
        opened_at=now,
    )
    session.add(request)
    await session.flush()
    step = ApprovalStep(
        organization_id=org_id,
        request_id=request.id,
        level=level,
        approver_employee_id=approver_employee_id,
        acting_for_employee_id=acting_for_employee_id,
        delegation_id=delegation_id,
        reason=reason,
        opened_at=now,
        due_at=due_at,
        outcome="open",
    )
    session.add(step)
    await session.flush()
    return request, step


async def seed_policy(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    request_type: str = REQUEST_TYPE_REGULARIZATION,
    sla_minutes: int = 15,
    enabled: bool = True,
) -> ApprovalPolicy:
    """An `approval_policies` row — no `PUT` endpoint ships before this slice's own successor, so
    tests that need a non-default SLA (the 15-minute floor, most often, to keep fixtures readable
    without arithmetic on `domain.DEFAULT_SLA_MINUTES`) write the row directly.

    Caller must `session.begin()`/commit around this — matches `seed_pending_request_with_step`.
    """
    policy = ApprovalPolicy(
        organization_id=org_id,
        request_type=request_type,
        sla_minutes=sla_minutes,
        enabled=enabled,
    )
    session.add(policy)
    await session.flush()
    return policy


def envelope_from_outbox_row(raw: dict[str, Any], *, producer: str) -> dict[str, Any]:
    """Rebuild the wire envelope from a printed outbox row (as `tests.regularization_cli` in
    attendance-service's own test tree prints it — `id`, `organization_id`, `event_type`,
    `event_version`, `aggregate_type`, `aggregate_id`, `payload`, `correlation_id`,
    `causation_id`, `actor`, `created_at`) using the platform's own `build_envelope` — the exact
    function the real outbox relay calls — so nothing about the wire shape is reimplemented here.
    Same technique as `test_cross_service_contract.py`'s `_envelope_from_outbox_row`."""
    row = SimpleNamespace(
        id=uuid.UUID(raw["id"]),
        event_type=raw["event_type"],
        event_version=raw["event_version"],
        organization_id=uuid.UUID(raw["organization_id"]),
        aggregate_type=raw["aggregate_type"],
        aggregate_id=uuid.UUID(raw["aggregate_id"]),
        created_at=datetime.fromisoformat(raw["created_at"]),
        correlation_id=raw["correlation_id"],
        causation_id=uuid.UUID(raw["causation_id"]) if raw["causation_id"] else None,
        actor=raw["actor"],
        payload=raw["payload"],
    )
    return build_envelope(row, producer=producer)


def regularization_requested_envelope(
    *,
    event_id: uuid.UUID | None = None,
    organization_id: uuid.UUID,
    regularization_request_id: uuid.UUID,
    attendance_id: uuid.UUID | None = None,
    employee_id: uuid.UUID,
    requested_status: str = "present",
    current_status: str = "absent",
    reason: str = "Forgot to punch in",
    date: str = "2026-09-12",
    event_type: str = "hrms.time.regularization.requested",
) -> dict[str, Any]:
    """An envelope shaped exactly the way `services/attendance/app/services.py`'s
    `request_regularization` publishes it — verified against that code, not assumed, as this
    slice was built (ADR-0006 §12)."""
    payload = {
        "organization_id": str(organization_id),
        "regularization_request_id": str(regularization_request_id),
        "attendance_id": str(attendance_id or uuid.uuid4()),
        "employee_id": str(employee_id),
        "requested_status": requested_status,
        "current_status": current_status,
        "reason": reason,
        "date": date,
    }
    return {
        "event_id": str(event_id or uuid.uuid4()),
        "event_type": event_type,
        "version": 1,
        "organization_id": str(organization_id),
        "aggregate_type": "regularization_request",
        "aggregate_id": str(regularization_request_id),
        "occurred_at": datetime.now(UTC).isoformat(),
        "producer": "attendance",
        "correlation_id": None,
        "causation_id": None,
        "actor": None,
        "schema_uri": f"opsgrids://contracts/{event_type}/v1",
        "payload": payload,
    }


def regularization_applied_envelope(
    *,
    event_id: uuid.UUID | None = None,
    organization_id: uuid.UUID,
    regularization_request_id: uuid.UUID,
    approval_request_id: uuid.UUID | None = None,
    outcome: str = "applied",
    failure_reason: str | None = None,
    event_type: str = "hrms.time.regularization.applied",
) -> dict[str, Any]:
    """Shaped the way `_publish_regularization_applied` publishes it (verified, ADR-0006 §12)."""
    payload: dict[str, Any] = {
        "organization_id": str(organization_id),
        "regularization_request_id": str(regularization_request_id),
        "approval_request_id": str(approval_request_id) if approval_request_id else None,
        "request_type": REQUEST_TYPE_REGULARIZATION,
        "outcome": outcome,
    }
    if failure_reason is not None:
        payload["failure_reason"] = failure_reason
    return {
        "event_id": str(event_id or uuid.uuid4()),
        "event_type": event_type,
        "version": 1,
        "organization_id": str(organization_id),
        "aggregate_type": "regularization_request",
        "aggregate_id": str(regularization_request_id),
        "occurred_at": datetime.now(UTC).isoformat(),
        "producer": "attendance",
        "correlation_id": None,
        "causation_id": None,
        "actor": None,
        "schema_uri": f"opsgrids://contracts/{event_type}/v1",
        "payload": payload,
    }


def leave_requested_envelope(
    *,
    event_id: uuid.UUID | None = None,
    organization_id: uuid.UUID,
    leave_request_id: uuid.UUID,
    employee_id: uuid.UUID,
    leave_type_id: uuid.UUID | None = None,
    start_date: str = "2027-03-01",
    end_date: str = "2027-03-02",
    half_day_start: bool = False,
    half_day_end: bool = False,
    total_half_days: int = 4,
    reason: str = "family trip",
    event_type: str = "hrms.time.leave.requested",
    payload_organization_id: uuid.UUID | str | None = None,
) -> dict[str, Any]:
    """An envelope shaped exactly the way `services/leave/app/services.py::apply_for_leave`
    publishes it — verified against that code, not assumed, the same discipline
    `regularization_requested_envelope` states for its own source. `payload_organization_id`
    lets a test build the one shape production never does — an envelope and payload that
    disagree — without hand-rolling the whole envelope."""
    payload = {
        "organization_id": str(
            payload_organization_id if payload_organization_id is not None else organization_id
        ),
        "leave_request_id": str(leave_request_id),
        "employee_id": str(employee_id),
        "leave_type_id": str(leave_type_id or uuid.uuid4()),
        "start_date": start_date,
        "end_date": end_date,
        "half_day_start": half_day_start,
        "half_day_end": half_day_end,
        "total_half_days": total_half_days,
        "reason": reason,
    }
    return {
        "event_id": str(event_id or uuid.uuid4()),
        "event_type": event_type,
        "version": 1,
        "organization_id": str(organization_id),
        "aggregate_type": "leave_request",
        "aggregate_id": str(leave_request_id),
        "occurred_at": datetime.now(UTC).isoformat(),
        "producer": "leave",
        "correlation_id": None,
        "causation_id": None,
        "actor": None,
        "schema_uri": f"opsgrids://contracts/{event_type}/v1",
        "payload": payload,
    }


def leave_applied_envelope(
    *,
    event_id: uuid.UUID | None = None,
    organization_id: uuid.UUID,
    leave_request_id: uuid.UUID,
    approval_request_id: uuid.UUID | None = None,
    outcome: str = "applied",
    failure_reason: str | None = None,
    event_type: str = "hrms.time.leave.applied",
    payload_organization_id: uuid.UUID | str | None = None,
) -> dict[str, Any]:
    """Shaped the way `_publish_leave_applied` publishes it (verified, ADR-0006 §12)."""
    payload: dict[str, Any] = {
        "organization_id": str(
            payload_organization_id if payload_organization_id is not None else organization_id
        ),
        "leave_request_id": str(leave_request_id),
        "approval_request_id": str(approval_request_id) if approval_request_id else None,
        "request_type": REQUEST_TYPE_LEAVE,
        "outcome": outcome,
    }
    if failure_reason is not None:
        payload["failure_reason"] = failure_reason
    return {
        "event_id": str(event_id or uuid.uuid4()),
        "event_type": event_type,
        "version": 1,
        "organization_id": str(organization_id),
        "aggregate_type": "leave_request",
        "aggregate_id": str(leave_request_id),
        "occurred_at": datetime.now(UTC).isoformat(),
        "producer": "leave",
        "correlation_id": None,
        "causation_id": None,
        "actor": None,
        "schema_uri": f"opsgrids://contracts/{event_type}/v1",
        "payload": payload,
    }


def leave_cancelled_envelope(
    *,
    event_id: uuid.UUID | None = None,
    organization_id: uuid.UUID,
    leave_request_id: uuid.UUID,
    employee_id: uuid.UUID | None = None,
    was_status: str = "pending",
    event_type: str = "hrms.time.leave.cancelled",
    payload_organization_id: uuid.UUID | str | None = None,
) -> dict[str, Any]:
    """Shaped the way `cancel_leave_request` publishes it (verified, ADR-0006 §12)."""
    payload = {
        "organization_id": str(
            payload_organization_id if payload_organization_id is not None else organization_id
        ),
        "leave_request_id": str(leave_request_id),
        "employee_id": str(employee_id or uuid.uuid4()),
        "was_status": was_status,
    }
    return {
        "event_id": str(event_id or uuid.uuid4()),
        "event_type": event_type,
        "version": 1,
        "organization_id": str(organization_id),
        "aggregate_type": "leave_request",
        "aggregate_id": str(leave_request_id),
        "occurred_at": datetime.now(UTC).isoformat(),
        "producer": "leave",
        "correlation_id": None,
        "causation_id": None,
        "actor": None,
        "schema_uri": f"opsgrids://contracts/{event_type}/v1",
        "payload": payload,
    }
