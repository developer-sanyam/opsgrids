"""Writes for leave.

Every function here follows employee- and attendance-service's shape: change the row, record
the audit entry, stage the event — **all inside one transaction**. `get_tenant_session` opens
that transaction and binds it to the caller's organization before the route body runs, and does
not commit for the route: a crash between the state change and the event cannot leave one
without the other, and a rolled-back write never leaves an audit row claiming it happened.

Events are staged into the outbox, never published inline (ADR-0000). Leave requests route
through the approval engine exactly the way attendance's regularisation requests do (ADR-0006):
this service publishes `.requested`, approval opens and routes the request, approval publishes
`.opened`/`.decided`, and this service's own consumer (`app/consumers.py`) applies the outcome
and posts the ledger consumption — never a synchronous call in either direction.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain, selectors
from app.models import (
    AuditLog,
    EmployeeDirectory,
    LeaveLedgerEntry,
    LeaveRequest,
    LeaveSettings,
    LeaveType,
    OutboxEvent,
)
from app.schemas import (
    LeaveBalanceGrantIn,
    LeaveCancelIn,
    LeaveRequestIn,
    LeaveSettingsIn,
    LeaveTypeIn,
    LeaveTypePatch,
)
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.db import constraint_name
from platform_core.errors import BusinessRuleProblem, ConflictProblem, NotFoundProblem
from platform_core.events import publish_event

#: `hrms.time.*` because these are the product's own, not the platform's.
EVT_LEAVE_TYPE_CREATED = "hrms.time.leave_type.created"
EVT_LEAVE_TYPE_UPDATED = "hrms.time.leave_type.updated"
EVT_LEAVE_TYPE_DELETED = "hrms.time.leave_type.deleted"
EVT_LEAVE_BALANCE_GRANTED = "hrms.time.leave_balance.granted"
EVT_LEAVE_SETTINGS_UPDATED = "hrms.time.leave_settings.updated"
#: ADR-0006 — the approval engine's request/decide pair. `.requested` is what approval opens a
#: request from; `.applied` is leave's return leg, closing the loop the way
#: `hrms.time.regularization.applied` does for attendance (ADR-0006 §12).
EVT_LEAVE_REQUESTED = "hrms.time.leave.requested"
EVT_LEAVE_APPLIED = "hrms.time.leave.applied"
EVT_LEAVE_CANCELLED = "hrms.time.leave.cancelled"
#: The request type approval routes this product's leave requests under (ADR-0006 §12,
#: `services/approval/app/services.py`'s `REQUEST_TYPE_LEAVE`).
REQUEST_TYPE_LEAVE = "hrms.time.leave"

DEFAULT_TIMEZONE = "Asia/Kolkata"


def _actor(ctx: ScopeContext) -> dict[str, Any]:
    return {
        "type": "user",
        "id": str(ctx.principal.sub),
        "membership_id": str(ctx.principal.membership_id) if ctx.principal.membership_id else None,
    }


def _consumer_actor() -> dict[str, Any]:
    return {"type": "consumer", "id": "leave.consumer"}


def _subject_employee(ctx: ScopeContext) -> uuid.UUID:
    """The employee a self-service write belongs to — the caller's own subject reference. A
    caller whose token carries no `subject_ref` is authenticated but is not an employee of this
    organization, the same business rule `attendance.services._subject_employee` states."""
    subject = ctx.principal.subject_ref
    if subject is None:
        raise BusinessRuleProblem("Only an employee can apply for their own leave")
    return subject


async def resolve_timezone(session: AsyncSession, org_id: uuid.UUID) -> str:
    """The timezone `domain.today_in` resolves "today" against for this organization. A missing
    `LeaveSettings` row means the default (`DEFAULT_TIMEZONE`) — the same "missing row is the
    default, created on first PUT" shape `approval.approval_policies` uses (ADR-0006 §10)."""
    row = await selectors.get_leave_settings(session, org_id)
    return row.timezone if row is not None else DEFAULT_TIMEZONE


async def update_leave_settings(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: LeaveSettingsIn,
    *,
    request: Request | None = None,
) -> LeaveSettings:
    try:
        domain.zone(payload.timezone)
    except domain.InvalidTimezone as exc:
        raise BusinessRuleProblem(str(exc)) from exc

    row = await selectors.get_leave_settings(session, ctx.organization_id)
    before = {"timezone": row.timezone} if row is not None else None
    if row is None:
        row = LeaveSettings(
            organization_id=ctx.organization_id,
            created_by=str(ctx.principal.sub),
            timezone=payload.timezone,
        )
        session.add(row)
    else:
        row.timezone = payload.timezone
        row.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="leave.settings.updated",
        entity="leave_settings",
        entity_id=None,
        before=before,
        after={"timezone": row.timezone},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_LEAVE_SETTINGS_UPDATED,
        aggregate_type="leave_settings",
        aggregate_id=ctx.organization_id,
        payload={"organization_id": str(ctx.organization_id), "timezone": row.timezone},
        actor=_actor(ctx),
    )
    return row


# --------------------------------------------------------------------- the employee directory


async def upsert_directory_entry(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    employee_id: uuid.UUID,
    reporting_manager_id: uuid.UUID | None,
    status: str,
    source_version: int,
    source_occurred_at: datetime,
) -> bool:
    """Apply one employee's directory state, if and only if it is newer than what is stored —
    the identical version-gated upsert `approval.services.upsert_hierarchy_nodes_bulk` uses for
    its own hierarchy copy (ADR-0006 §2, data model §1.1), narrowed to the columns this service
    needs. Returns `True` when the row was written, `False` when the incoming state was dropped
    as stale or equal — never itself a reason to raise; a dropped stale event is the intended
    outcome (`app/consumers.py` treats both as a successfully processed message).
    """
    stmt = pg_insert(EmployeeDirectory).values(
        organization_id=organization_id,
        employee_id=employee_id,
        reporting_manager_id=reporting_manager_id,
        status=status,
        source_version=source_version,
        source_occurred_at=source_occurred_at,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[EmployeeDirectory.organization_id, EmployeeDirectory.employee_id],
        set_={
            "reporting_manager_id": stmt.excluded.reporting_manager_id,
            "status": stmt.excluded.status,
            "source_version": stmt.excluded.source_version,
            "source_occurred_at": stmt.excluded.source_occurred_at,
            # `TimestampMixin.updated_at`'s `onupdate=func.now()` is a Core/ORM `Update()`
            # default; `on_conflict_do_update`'s `set_` is taken literally and does not consult
            # it, so without this line the column would silently keep its insert-time value.
            "updated_at": func.now(),
        },
        where=EmployeeDirectory.source_version < stmt.excluded.source_version,
    )
    result = cast("CursorResult[Any]", await session.execute(stmt))
    return bool(result.rowcount or 0)


# --------------------------------------------------------------------- leave types


async def create_leave_type(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: LeaveTypeIn,
    *,
    request: Request | None = None,
) -> LeaveType:
    if await selectors.get_leave_type_by_code(session, ctx.organization_id, payload.code):
        raise ConflictProblem(f"A leave type coded {payload.code!r} already exists")

    leave_type = LeaveType(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        code=payload.code,
        name=payload.name,
        is_paid=payload.is_paid,
        annual_quota_half_days=payload.annual_quota_half_days,
        monthly_accrual_half_days=payload.monthly_accrual_half_days,
        carry_forward_cap_half_days=payload.carry_forward_cap_half_days,
    )
    session.add(leave_type)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="leave.type.created",
        entity="leave_type",
        entity_id=leave_type.id,
        after=leave_type.as_dict(exclude={"organization_id"}),
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_LEAVE_TYPE_CREATED,
        aggregate_type="leave_type",
        aggregate_id=leave_type.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "leave_type_id": str(leave_type.id),
            "code": leave_type.code,
            "is_paid": leave_type.is_paid,
        },
        actor=_actor(ctx),
    )
    return leave_type


async def update_leave_type(
    session: AsyncSession,
    ctx: ScopeContext,
    leave_type: LeaveType,
    payload: LeaveTypePatch,
    *,
    request: Request | None = None,
) -> LeaveType:
    before = leave_type.as_dict(exclude={"organization_id"})
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(leave_type, field, value)
    leave_type.updated_by = str(ctx.principal.sub)
    # Flush, then refresh: the flush's `UPDATE` expires `updated_at` (its `onupdate=func.now()`
    # is server-computed, so the ORM does not know its new value without asking) — the exact
    # "flush expires a server-computed column that a narrower refresh would leave expired"
    # `employee.services.update_employee`'s own comment describes. Left expired, `as_dict()`'s
    # plain `getattr` loop below triggers an implicit lazy load outside of any `await`, which an
    # async session cannot service and raises `MissingGreenlet` — not a validation error, a 500,
    # and only on the update path, since `create_leave_type`'s INSERT gets its defaults back for
    # free via `eager_defaults`'s automatic `RETURNING`.
    await session.flush()
    await session.refresh(leave_type)

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="leave.type.updated",
        entity="leave_type",
        entity_id=leave_type.id,
        before=before,
        after=leave_type.as_dict(exclude={"organization_id"}),
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_LEAVE_TYPE_UPDATED,
        aggregate_type="leave_type",
        aggregate_id=leave_type.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "leave_type_id": str(leave_type.id),
            "changed": sorted(changes),
        },
        actor=_actor(ctx),
    )
    return leave_type


async def delete_leave_type(
    session: AsyncSession,
    ctx: ScopeContext,
    leave_type: LeaveType,
    *,
    request: Request | None = None,
) -> None:
    """Soft delete — the same shape `attendance.services.delete_shift` uses for the identical
    reference-data reason: `code` is freed by the partial unique index the moment `deleted_at` is
    set, so a retired type is free to be recreated."""
    leave_type.deleted_at = datetime.now(UTC)
    leave_type.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="leave.type.deleted",
        entity="leave_type",
        entity_id=leave_type.id,
        before={"deleted_at": None},
        after={"deleted_at": leave_type.deleted_at},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_LEAVE_TYPE_DELETED,
        aggregate_type="leave_type",
        aggregate_id=leave_type.id,
        payload={"organization_id": str(ctx.organization_id), "leave_type_id": str(leave_type.id)},
        actor=_actor(ctx),
    )


# --------------------------------------------------------------------- balances (the ledger)


async def grant_leave_balance(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: LeaveBalanceGrantIn,
    *,
    request: Request | None = None,
) -> LeaveLedgerEntry:
    """Post a credit to one employee's ledger — a grant, an accrual, or a plain adjustment.

    Idempotent on `idempotency_key`: a retried call with the same key returns the row the first
    call posted rather than posting a second credit (CLAUDE.md's idempotency rule, the same
    "unique constraint plus a friendly pre-check" shape `services/payment` uses for a charge).
    """
    if payload.idempotency_key is not None:
        existing = await selectors.get_ledger_entry_by_idempotency_key(
            session, ctx.organization_id, payload.idempotency_key
        )
        if existing is not None:
            return existing

    leave_type = await selectors.get_leave_type(session, ctx.organization_id, payload.leave_type_id)
    if leave_type is None:
        raise NotFoundProblem("That leave type is not part of this organization")

    entry = LeaveLedgerEntry(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        employee_id=payload.employee_id,
        leave_type_id=leave_type.id,
        entry_type=payload.entry_type,
        delta_half_days=payload.half_days,
        idempotency_key=payload.idempotency_key,
        note=payload.note,
    )
    try:
        # A SAVEPOINT, not a bare `session.add` + `flush()`: once a statement inside a
        # transaction raises, Postgres marks the *whole* transaction aborted until a ROLLBACK
        # (or a ROLLBACK TO SAVEPOINT) runs — every later statement on it fails with "current
        # transaction is aborted", including the very SELECT below that is supposed to recover
        # from this. `begin_nested()` is that ROLLBACK TO SAVEPOINT: the failed INSERT undoes
        # cleanly and the outer transaction (and the request's own tenant binding) stays usable.
        async with session.begin_nested():
            session.add(entry)
            await session.flush()
    except IntegrityError as exc:
        if constraint_name(exc) != "uq_leave_ledger_entries_org_idempotency_key":
            raise
        # The race the pre-check above cannot see: two concurrent calls with the same key.
        existing = await selectors.get_ledger_entry_by_idempotency_key(
            session,
            ctx.organization_id,
            payload.idempotency_key,  # type: ignore[arg-type]
        )
        if existing is None:  # pragma: no cover - defensive, the unique constraint guarantees it
            raise ConflictProblem("That idempotency key is already in use") from exc
        return existing

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="leave.balance.granted",
        entity="leave_ledger_entry",
        entity_id=entry.id,
        after={
            "employee_id": entry.employee_id,
            "leave_type_id": entry.leave_type_id,
            "entry_type": entry.entry_type,
            "delta_half_days": entry.delta_half_days,
        },
        reason=entry.note,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_LEAVE_BALANCE_GRANTED,
        aggregate_type="leave_ledger_entry",
        aggregate_id=entry.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "employee_id": str(entry.employee_id),
            "leave_type_id": str(entry.leave_type_id),
            "entry_type": entry.entry_type,
            "delta_half_days": entry.delta_half_days,
        },
        actor=_actor(ctx),
    )
    return entry


async def _lock_balance(
    session: AsyncSession,
    organization_id: uuid.UUID,
    employee_id: uuid.UUID,
    leave_type_id: uuid.UUID,
) -> None:
    """Serialises every balance-affecting read-then-write for one `(employee, leave type)`.

    There is no balance *row* to `SELECT ... FOR UPDATE` — a balance is a derived `SUM`, never a
    stored value (CLAUDE.md's append-only rule) — so this takes a transaction-scoped advisory
    lock instead, the exact shape `employee-service`'s own reporting-line cycle check uses for
    the identical class of problem (ADR-0006 §2: "the check takes an advisory lock first"):
    without it, two requests for the same employee and type approved at the same instant can each
    read a balance that individually looks sufficient and both post a consumption, overdrawing
    it — a read-then-write race no row lock on either *request* (they are two different rows)
    would ever catch. Held until the caller's transaction ends, which is what makes the second
    caller's own balance read happen only after the first one's consumption has committed.
    """
    key = f"leave.balance:{organization_id}:{employee_id}:{leave_type_id}"
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
    )


async def _post_ledger_entry(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    employee_id: uuid.UUID,
    leave_type_id: uuid.UUID,
    entry_type: str,
    delta_half_days: int,
    leave_request_id: uuid.UUID,
    note: str | None,
    created_by: str,
) -> LeaveLedgerEntry:
    """A consumption or a reversal — always tied to the request that caused it. Not exposed as
    its own endpoint: the only two writers are `apply_leave_decision` (consumption) and
    `cancel_leave_request` (reversal)."""
    entry = LeaveLedgerEntry(
        organization_id=organization_id,
        created_by=created_by,
        employee_id=employee_id,
        leave_type_id=leave_type_id,
        entry_type=entry_type,
        delta_half_days=delta_half_days,
        leave_request_id=leave_request_id,
        note=note,
    )
    session.add(entry)
    await session.flush()
    return entry


# --------------------------------------------------------------------- leave requests


async def apply_for_leave(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: LeaveRequestIn,
    *,
    request: Request | None = None,
) -> LeaveRequest:
    """Apply for leave (self-service). Validates the range, the overlap rule, and — for a paid
    type — the balance, then opens a `pending` request and publishes `hrms.time.leave.requested`,
    the event approval's consumer opens a `hrms.time.leave` request from (ADR-0006 §4).

    **Weekends only, no holiday calendar in this slice.** `domain.count_half_days` excludes
    Saturday and Sunday and nothing else — a documented, deliberate gap (the task's own stated
    scope): a holiday calendar is real product surface (per-location public holidays, at least),
    not a field on this call, and adding one later changes only `domain.count_half_days`'s
    weekend set into a calendar lookup, not this function's shape.
    """
    employee_id = _subject_employee(ctx)

    leave_type = await selectors.get_leave_type(session, ctx.organization_id, payload.leave_type_id)
    if leave_type is None or not leave_type.active:
        raise NotFoundProblem("That leave type is not part of this organization")

    try:
        total_half_days = domain.count_half_days(
            payload.start_date,
            payload.end_date,
            half_day_start=payload.half_day_start,
            half_day_end=payload.half_day_end,
        )
    except domain.InvalidRange as exc:
        raise BusinessRuleProblem(str(exc)) from exc

    if await selectors.has_overlapping_request(
        session, ctx.organization_id, employee_id, payload.start_date, payload.end_date
    ):
        raise ConflictProblem(
            "You already have a pending or approved leave request that overlaps these dates"
        )

    if leave_type.is_paid:
        # A friendly pre-check only — applying posts nothing to the ledger, so two concurrent
        # applications can both legitimately see a sufficient balance and both become `pending`.
        # No lock is needed here for that reason: the authoritative check, where a lock actually
        # matters, is `apply_leave_decision`'s, at the moment a consumption is posted (see
        # `_lock_balance`). A request that was never going to be approvable simply fails there
        # instead, cleanly, as `failed` rather than `approved`.
        balance = await selectors.balance_half_days(
            session, ctx.organization_id, employee_id, leave_type.id
        )
        if balance < total_half_days:
            raise BusinessRuleProblem(
                f"Insufficient balance for {leave_type.code}: {balance} half-day(s) available, "
                f"{total_half_days} requested"
            )

    directory = await selectors.get_directory_entry(session, ctx.organization_id, employee_id)
    reporting_manager_id = directory.reporting_manager_id if directory is not None else None

    req = LeaveRequest(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        employee_id=employee_id,
        leave_type_id=leave_type.id,
        start_date=payload.start_date,
        end_date=payload.end_date,
        half_day_start=payload.half_day_start,
        half_day_end=payload.half_day_end,
        total_half_days=total_half_days,
        reason=payload.reason,
        status="pending",
        reporting_manager_id=reporting_manager_id,
    )
    session.add(req)
    try:
        await session.flush()
    except IntegrityError as exc:
        if constraint_name(exc) != "ex_leave_requests_no_overlap":
            raise
        # The race the pre-check above cannot see: another transaction inserted an overlapping
        # request between our read and our insert (the same shape
        # `approval.services.create_delegation` uses for its own overlap exclusion constraint).
        raise ConflictProblem(
            "You already have a pending or approved leave request that overlaps these dates"
        ) from exc

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="leave.request.applied",
        entity="leave_request",
        entity_id=req.id,
        after={
            "leave_type_id": req.leave_type_id,
            "start_date": req.start_date,
            "end_date": req.end_date,
            "total_half_days": req.total_half_days,
        },
        reason=req.reason,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_LEAVE_REQUESTED,
        aggregate_type="leave_request",
        aggregate_id=req.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "leave_request_id": str(req.id),
            "employee_id": str(employee_id),
            "leave_type_id": str(leave_type.id),
            "start_date": req.start_date.isoformat(),
            "end_date": req.end_date.isoformat(),
            "half_day_start": req.half_day_start,
            "half_day_end": req.half_day_end,
            "total_half_days": req.total_half_days,
            "reason": req.reason,
        },
        actor=_actor(ctx),
    )
    return req


def _publish_leave_applied(
    session: AsyncSession, req: LeaveRequest, *, outcome: str, failure_reason: str | None = None
) -> None:
    """Leave's return leg (ADR-0006 §12). Published for every decision this service consumes —
    an approval and a rejection are both `applied`; only a transition that has since become
    illegal is `failed`. Never raises: approval's own alarm on `application_state` finds the
    population where this leg never runs, not a redelivery loop here."""
    payload: dict[str, Any] = {
        "organization_id": str(req.organization_id),
        "leave_request_id": str(req.id),
        "approval_request_id": (str(req.approval_request_id) if req.approval_request_id else None),
        "request_type": REQUEST_TYPE_LEAVE,
        "outcome": outcome,
    }
    if failure_reason is not None:
        payload["failure_reason"] = failure_reason
    publish_event(
        session,
        OutboxEvent,
        organization_id=req.organization_id,
        event_type=EVT_LEAVE_APPLIED,
        aggregate_type="leave_request",
        aggregate_id=req.id,
        payload=payload,
        actor=_consumer_actor(),
    )


async def apply_leave_opened(
    session: AsyncSession,
    req: LeaveRequest,
    *,
    approval_request_id: uuid.UUID,
    approver_employee_id: uuid.UUID | None,
    approver_name: str | None,
    opened_at: datetime,
) -> LeaveRequest:
    """Apply `core.approval.request.opened` to a still-`pending` leave request. Called only from
    `app/consumers.py`, which has already checked `req.status == "pending"` — a replay, or
    `.opened` arriving after `.decided`, never reaches here. Same shape as
    `attendance.services.apply_regularization_opened`; see that function's docstring."""
    if req.approval_request_id is None:
        req.approval_request_id = approval_request_id
    req.approval_opened_at = opened_at
    req.approver_employee_id = approver_employee_id
    req.approver_name = approver_name

    record_audit(
        session,
        AuditLog,
        organization_id=req.organization_id,
        actor_id=None,
        acting_as="leave.consumer",
        action="leave.request.opened",
        entity="leave_request",
        entity_id=req.id,
        after={
            "approval_request_id": req.approval_request_id,
            "approver_employee_id": approver_employee_id,
            "unassigned": approver_employee_id is None,
        },
    )
    return req


async def apply_leave_decision(
    session: AsyncSession,
    req: LeaveRequest,
    *,
    outcome: str,
    approval_request_id: uuid.UUID,
    decided_at: datetime,
    comment: str | None = None,
    decided_by_name: str | None = None,
) -> LeaveRequest:
    """Apply `core.approval.request.decided` to a still-`pending` leave request (ADR-0006 §12).
    Called only from `app/consumers.py`, which has already validated `outcome` is `"approved"`
    or `"rejected"` — anything else is refused there, before this function is ever reached, the
    same "explicit, exhaustive mapping" discipline `approval.services.decide_request` states for
    its own `_DECISION_OUTCOMES`. A third value reaching here anyway is a caller bug, not a
    message this function tries to interpret — it raises rather than silently treating "anything
    that is not 'rejected'" as an approval.

    * **`rejected`** moves the request to `rejected` and posts nothing to the ledger.
    * **`approved`** re-checks the balance for a paid type before posting the consumption — the
      balance may have moved since the request was raised (another request approved first, an
      adjustment posted). If it is no longer sufficient, this does **not** raise: raising would
      redeliver once and then dead-letter a fact that is not a processing error. It records
      `failed` instead, with a reason, mirroring
      `attendance.services.apply_regularization_decision`'s identical "the transition that was
      legal at request time may no longer be" handling. A leave type retired (soft-deleted)
      after the request was raised gets the same `failed` treatment, for the same reason — never
      silently approved with no balance check just because the type record is gone.

    Either branch publishes `hrms.time.leave.applied` from this same transaction.

    **Every read this function needs happens before it mutates `req`.** SQLAlchemy autoflushes
    the session before any `SELECT`, and `ck_leave_requests_decided_iff_approved_or_rejected`
    means a half-written `req` — `decided_at` set, `status` still `'pending'` — is briefly an
    illegal row. Reading `leave_type`/the balance *before* touching `req` at all keeps every
    intermediate state of this object either fully `pending` or fully decided, never caught
    mid-flush by its own CHECK constraint.
    """
    if outcome not in ("approved", "rejected"):
        raise ValueError(f"apply_leave_decision: unrecognised outcome {outcome!r}")

    leave_type = None
    balance = None
    type_retired = False
    if outcome == "approved":
        # Locked before either read below: two requests for the same employee and type decided
        # at the same instant must not both see a balance that looks sufficient — see
        # `_lock_balance`'s own docstring for why a row lock on `req` alone cannot catch this
        # (the two requests are different rows).
        await _lock_balance(session, req.organization_id, req.employee_id, req.leave_type_id)
        leave_type = await selectors.get_leave_type(session, req.organization_id, req.leave_type_id)
        type_retired = leave_type is None
        if leave_type is not None and leave_type.is_paid:
            balance = await selectors.balance_half_days(
                session, req.organization_id, req.employee_id, req.leave_type_id
            )

    req.approval_request_id = approval_request_id
    req.decided_at = decided_at
    req.decision_comment = comment
    req.decided_by_name = decided_by_name

    if outcome == "rejected":
        req.status = "rejected"
        record_audit(
            session,
            AuditLog,
            organization_id=req.organization_id,
            actor_id=None,
            acting_as="leave.consumer",
            action="leave.request.rejected",
            entity="leave_request",
            entity_id=req.id,
            before={"status": "pending"},
            after={"status": "rejected", "decided_by_name": decided_by_name},
            reason=comment,
        )
        _publish_leave_applied(session, req, outcome="applied")
        return req

    balance_now_insufficient = (
        leave_type is not None
        and leave_type.is_paid
        and balance is not None
        and balance < req.total_half_days
    )
    if type_retired or balance_now_insufficient:
        req.status = "failed"
        req.failure_reason = (
            "The leave type was retired after this request was raised."
            if type_retired
            else (
                f"Balance has since dropped to {balance} half-day(s); "
                f"{req.total_half_days} were requested."
            )
        )[:500]
        record_audit(
            session,
            AuditLog,
            organization_id=req.organization_id,
            actor_id=None,
            acting_as="leave.consumer",
            action="leave.request.failed",
            entity="leave_request",
            entity_id=req.id,
            before={"status": "pending"},
            after={"status": "failed", "failure_reason": req.failure_reason},
            reason=comment,
        )
        _publish_leave_applied(session, req, outcome="failed", failure_reason=req.failure_reason)
        return req

    req.status = "approved"
    entry = await _post_ledger_entry(
        session,
        organization_id=req.organization_id,
        employee_id=req.employee_id,
        leave_type_id=req.leave_type_id,
        entry_type="consumption",
        delta_half_days=-req.total_half_days,
        leave_request_id=req.id,
        note=f"Approved leave request {req.id}",
        created_by="leave.consumer",
    )
    record_audit(
        session,
        AuditLog,
        organization_id=req.organization_id,
        actor_id=None,
        acting_as="leave.consumer",
        action="leave.request.approved",
        entity="leave_request",
        entity_id=req.id,
        before={"status": "pending"},
        after={
            "status": "approved",
            "decided_by_name": decided_by_name,
            "ledger_entry_id": entry.id,
        },
        reason=comment,
    )
    _publish_leave_applied(session, req, outcome="applied")
    return req


async def cancel_leave_request(
    session: AsyncSession,
    ctx: ScopeContext,
    req: LeaveRequest,
    payload: LeaveCancelIn,
    *,
    now: datetime | None = None,
    request: Request | None = None,
) -> LeaveRequest:
    """Cancel a `pending` or `approved` leave request. Two routes call this: the self-service
    withdrawal (`POST /leave/requests/{id}/cancel`, `hrms.time.leave_request.write`) and HR's
    documented override (`POST /leave/requests/{id}/force-cancel`,
    `hrms.time.leave_request.manage` at ORG — the same "product's own escape hatch, audited in
    its own domain" shape ADR-0006 §6 describes for attendance's direct regularize endpoint).
    Whether the caller may reach *this* request at all is the route's job, not this function's —
    the same "object-level check happens before the service is ever called" shape
    `attendance.api.routes.request_regularization`'s `_assert_requesting_own_record` uses; this
    function only enforces the rules that hold regardless of who is calling.

    A `pending` request is simply withdrawn — nothing was ever posted to the ledger. An
    `approved` request posts a **reversal** (CLAUDE.md's append-only rule: never edit the
    consumption row, post its exact opposite), except once the leave has already run its course:
    cancelling a request whose `end_date` has already passed, in the **organization's own
    timezone** (`domain.today_in`, resolved from `LeaveSettings`, never a bare
    `datetime.now(UTC).date()` — see `app/models.py`'s module docstring on the timezone defect
    this rebuild does not repeat), is refused. That boundary is exactly where a UTC-naive "today"
    would answer wrongly for an organization whose timezone is not UTC: near local midnight, a
    UTC calendar day and the organization's own can disagree about whether the leave is "still
    running" or "already over".
    """
    if req.status not in ("pending", "approved"):
        raise BusinessRuleProblem(f"A {req.status} leave request cannot be cancelled")

    resolved_now = now if now is not None else datetime.now(UTC)
    if req.status == "approved":
        timezone = await resolve_timezone(session, ctx.organization_id)
        today = domain.today_in(timezone, now=resolved_now)
        if req.end_date < today:
            raise BusinessRuleProblem(
                "This leave has already ended and cannot be withdrawn; ask HR to adjust the "
                "balance directly if a correction is needed."
            )

    before_status = req.status
    req.status = "cancelled"
    req.cancelled_at = resolved_now
    req.cancellation_reason = payload.reason
    req.updated_by = str(ctx.principal.sub)

    reversal_entry_id: uuid.UUID | None = None
    if before_status == "approved":
        entry = await _post_ledger_entry(
            session,
            organization_id=ctx.organization_id,
            employee_id=req.employee_id,
            leave_type_id=req.leave_type_id,
            entry_type="reversal",
            delta_half_days=req.total_half_days,
            leave_request_id=req.id,
            note=f"Cancelled leave request {req.id}",
            created_by=str(ctx.principal.sub),
        )
        reversal_entry_id = entry.id

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="leave.request.cancelled",
        entity="leave_request",
        entity_id=req.id,
        before={"status": before_status},
        after={"status": "cancelled", "reversal_entry_id": reversal_entry_id},
        reason=payload.reason,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_LEAVE_CANCELLED,
        aggregate_type="leave_request",
        aggregate_id=req.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "leave_request_id": str(req.id),
            "employee_id": str(req.employee_id),
            "was_status": before_status,
        },
        actor=_actor(ctx),
    )
    return req
