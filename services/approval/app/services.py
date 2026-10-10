"""Writes for approval.

Business rules, audit rows and outbox rows. Nothing here opens or commits a transaction: the
consumer and the backfill each take their session from `session_scope(organization_id=...)`,
which binds the transaction to the tenant first — so the state change and its idempotency marker
commit or roll back together.

Slice 1 has exactly one write: the version-gated upsert of one `hierarchy_nodes` row (ADR-0006
§2, data model §1.1). Slice 2 adds the routing engine's three writes: opening a request
(`open_regularization_request`, ADR-0006 §4), deciding one (`decide_request`, §6-§7), and
recording the product's return leg (`apply_regularization_outcome`, §12). Slice 3 adds the
delegation CRUD surface: `create_delegation` (§5, with the re-pointing it triggers) and
`revoke_delegation` (§5, §11). Slice 4 arms the SLA clock: `open_regularization_request` and
`escalate_step` (new, §8) both now stamp a real `due_at` off `approval_policies.sla_minutes`
rather than the `None` slices 2-3 deliberately opened every step with, `escalate_step` is what
`app/cron.py`'s sweep calls once per overdue step, and `check_eligibility` widens to search a
request's step *history* rather than only its current open step, which escalation is the first
thing in this service ever to make necessary (see that function's own docstring). Slice 4 also
adds `update_policy` (§10, §11): the write half of policy administration, guarded by
`policy.write:ORG` in `app/api/routes.py` and writing only `sla_minutes`/`enabled` — never a
request's own status, which is what makes "disabling turns off escalation only" true by
construction rather than by review.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, selectors
from app.domain import pick_approver
from app.models import (
    HIERARCHY_STATUSES,
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalStep,
    AuditLog,
    Delegation,
    HierarchyNode,
    OutboxEvent,
)
from platform_core.audit import record_audit
from platform_core.errors import (
    ConflictProblem,
    ForbiddenProblem,
    NotFoundProblem,
    ValidationProblem,
)
from platform_core.events import publish_event

#: `<product>.<module>.<thing>` (ADR-0006 §12) — the request types this slice routes.
REQUEST_TYPE_REGULARIZATION = "hrms.time.regularization"
#: Leave's own request type (HRMS leave slice 1) — routed by the same generic opening/decision/
#: application machinery as regularisation, parametrized by `request_type` rather than
#: duplicated; see `_open_request`/`_apply_outcome` below.
REQUEST_TYPE_LEAVE = "hrms.time.leave"
#: Operations' inventory requests (inventory Phase 3, docs/inventory/movements.md): an
#: inter-warehouse transfer, or a stock adjustment, at or over operations' own quantity threshold.
#: Routed by the same generic machinery, up the requester's reporting line. `summary` carries only
#: structured facts (transfer number, warehouses, the largest line / the SKU and quantity).
REQUEST_TYPE_INVENTORY_TRANSFER = "operations.inventory.transfer"
REQUEST_TYPE_INVENTORY_ADJUSTMENT = "operations.inventory.adjustment"

#: Every request type approval currently knows how to route. `GET /policies` (§10, §11) iterates
#: this rather than "every request_type a PUT has ever written" or "every distinct request_type
#: `approval_requests` has ever seen": the settings screen needs every type to render at its
#: honest defaults even for a brand-new organization that has opened nothing yet and an
#: administrator who has edited nothing — the alternative (deriving the list from written rows,
#: or from routing history) would show a card only after someone else has already acted, which is
#: not what "the type is shown, on the settings screen" (screens, overview) describes. A new
#: request type is added here the same way `REQUEST_TYPE_LEAVE` was: one new module-level
#: constant, appended.
KNOWN_REQUEST_TYPES: tuple[str, ...] = (
    REQUEST_TYPE_REGULARIZATION,
    REQUEST_TYPE_LEAVE,
    REQUEST_TYPE_INVENTORY_TRANSFER,
    REQUEST_TYPE_INVENTORY_ADJUSTMENT,
)

EVT_REQUEST_OPENED = "core.approval.request.opened"
EVT_REQUEST_DECIDED = "core.approval.request.decided"
EVT_REQUEST_ESCALATED = "core.approval.request.escalated"


@dataclass(frozen=True, slots=True)
class HierarchyNodeWrite:
    """One row's worth of the version-gated upsert.

    Mirrors `upsert_hierarchy_node`'s keyword arguments exactly, minus `organization_id`, which
    `upsert_hierarchy_nodes_bulk` takes once for the whole batch rather than once per row.
    """

    employee_id: uuid.UUID
    reporting_manager_id: uuid.UUID | None
    dotted_line_manager_id: uuid.UUID | None
    status: str
    display_name: str
    employee_no: str
    location_code: str | None
    source_version: int
    source_occurred_at: datetime


async def upsert_hierarchy_node(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    employee_id: uuid.UUID,
    reporting_manager_id: uuid.UUID | None,
    dotted_line_manager_id: uuid.UUID | None,
    status: str,
    display_name: str,
    employee_no: str,
    location_code: str | None,
    source_version: int,
    source_occurred_at: datetime,
) -> bool:
    """Apply one hierarchy state, if and only if it is newer than what is stored.

    The ordering rule (ADR-0006 §2, "a version, not a timestamp"; data model §1.1): apply an
    event or a backfill row only when its `source_version` is **strictly greater** than the
    value already stored for `(organization_id, employee_id)`. Equal is a duplicate — a
    redelivered event, or a backfill page that re-covers ground the live stream already applied
    — and is dropped without error, which is what makes both the consumer and the backfill
    idempotent under replay. The caller (never this function) is responsible for refusing a row
    whose version is altogether missing, before it ever reaches here — see
    `app/consumers.py` and `app/backfill.py`.

    Returns `True` when the row was written (an insert, or an update that passed the version
    gate) and `False` when the incoming state was dropped as stale or equal. Callers use this for
    logging and for tests; it is never itself a reason to raise, because a dropped stale event is
    the intended outcome, not a failure (data model §1.1, verified: "a replayed or reordered
    event updates zero rows and the consumer treats that as success").

    A single-row call through `upsert_hierarchy_nodes_bulk` — the consumer only ever has one row
    per message, so it pays no round-trip cost for sharing the batched statement.
    """
    applied = await upsert_hierarchy_nodes_bulk(
        session,
        organization_id=organization_id,
        nodes=[
            HierarchyNodeWrite(
                employee_id=employee_id,
                reporting_manager_id=reporting_manager_id,
                dotted_line_manager_id=dotted_line_manager_id,
                status=status,
                display_name=display_name,
                employee_no=employee_no,
                location_code=location_code,
                source_version=source_version,
                source_occurred_at=source_occurred_at,
            )
        ],
    )
    return applied > 0


async def upsert_hierarchy_nodes_bulk(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    nodes: Sequence[HierarchyNodeWrite],
) -> int:
    """The same version-gated upsert as `upsert_hierarchy_node`, applied to a whole batch in one
    round trip.

    Written for the backfill's page loop (`app/backfill.py`): it used to call
    `upsert_hierarchy_node` once per item inside the page's transaction — a database round trip
    per employee inside a `for` loop, the shape CLAUDE.md calls a bug regardless of which layer
    it is in. A page is already one transaction (ADR-0006 §2.1's "one transaction per page, on
    approval's side"); batching its rows into one multi-row `INSERT ... ON CONFLICT` keeps that
    boundary exactly and changes nothing about the version gate, because PostgreSQL evaluates the
    `ON CONFLICT ... WHERE` clause per conflicting row, independently of how many other rows are
    in the same statement — one employee's stale version is dropped exactly as it would be alone.

    Returns the number of rows actually written (inserted, or updated because their version
    passed the gate): PostgreSQL's own command-tag count for `INSERT ... ON CONFLICT DO UPDATE
    ... WHERE` already excludes rows whose `WHERE` failed, so summing this across pages is
    exactly what summing `upsert_hierarchy_node`'s per-call booleans used to produce.
    """
    if not nodes:
        return 0

    for node in nodes:
        if node.status not in HIERARCHY_STATUSES:
            # A status outside the closed vocabulary would fail the table's own CHECK, but
            # refusing it here keeps a malformed payload from ever reaching the database as a
            # constraint violation that looks like a bug rather than a data problem upstream.
            raise ValueError(f"unknown hierarchy status: {node.status!r}")

    stmt = pg_insert(HierarchyNode).values(
        [
            {
                "organization_id": organization_id,
                "employee_id": node.employee_id,
                "reporting_manager_id": node.reporting_manager_id,
                "dotted_line_manager_id": node.dotted_line_manager_id,
                "status": node.status,
                "display_name": node.display_name,
                "employee_no": node.employee_no,
                "location_code": node.location_code,
                "source_version": node.source_version,
                "source_occurred_at": node.source_occurred_at,
            }
            for node in nodes
        ]
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[HierarchyNode.organization_id, HierarchyNode.employee_id],
        set_={
            "reporting_manager_id": stmt.excluded.reporting_manager_id,
            "dotted_line_manager_id": stmt.excluded.dotted_line_manager_id,
            "status": stmt.excluded.status,
            "display_name": stmt.excluded.display_name,
            "employee_no": stmt.excluded.employee_no,
            "location_code": stmt.excluded.location_code,
            "source_version": stmt.excluded.source_version,
            "source_occurred_at": stmt.excluded.source_occurred_at,
            # `TimestampMixin.updated_at`'s `onupdate=func.now()` is a Core/ORM `Update()`
            # default; `on_conflict_do_update`'s `set_` is taken literally and does not consult
            # it, so without this line the column silently keeps its insert-time value across
            # every later upsert (data model §1.1 specifies it moves on every applied change).
            "updated_at": func.now(),
        },
        # PostgreSQL does not count a conflicting row whose `WHERE` fails against the command
        # tag / rowcount — a stale or equal-version row is therefore indistinguishable, at this
        # layer, from "nothing happened", which is exactly the idempotent behaviour wanted.
        where=HierarchyNode.source_version < stmt.excluded.source_version,
    )
    # `session.execute` is typed to return the generic `Result`, but an INSERT/UPDATE statement
    # always produces a `CursorResult` at runtime — which is what actually carries `rowcount`.
    result = cast("CursorResult[Any]", await session.execute(stmt))
    return int(result.rowcount or 0)


# --------------------------------------------------------------------- opening a request (§4)


async def db_now(session: AsyncSession) -> datetime:
    """One clock, and it is the database's (ADR-0006 §8): every SLA instant and every
    delegation-activity comparison is read from PostgreSQL, once per transaction, then threaded
    through. Applied here too, even though slices 2-3 have no SLA clock, for the same reason —
    `opened_at`/`decided_at`/`closed_at` and the delegation-active check must agree with each
    other and with the mixins' own `server_default=func.now()`, or a constraint that compares an
    app-supplied instant with a database-supplied one becomes a heisenbug on clock skew.

    Public: `app/api/routes.py` reads it directly at four sites (`get_inbox`, `list_delegations`,
    `create_delegation`, `revoke_delegation`) that need "now" for a response computed outside any
    `services.py` write path — the inbox's eligibility reasoning and the delegation status tag
    both have to agree with whatever clock instant a write in the same request used, so the route
    calls this rather than reading its own, second `now()`."""
    return (await session.execute(select(func.now()))).scalar_one()


async def _sla_due_at(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    request_type: str,
    approver_present: bool,
    opened_at: datetime,
) -> datetime | None:
    """The due date a step opening *right now* should carry (ADR-0006 §4, §8, §10) — shared by
    `open_regularization_request` (routing), the re-pointing carry-through in
    `create_delegation` (which reads a predecessor's stored value rather than calling this; see
    that function), and `escalate_step` (§8), so the three points a step can open never apply the
    policy differently.

    `None` for an unassigned step regardless of policy — `ck_approval_steps_unassigned_has_no_due`
    makes that the only legal value, and there is no SLA clock for nobody to hold. `None` also
    when the type's policy has `enabled = False` (§10: "steps open with due_at = NULL... it never
    turns off approval"). Otherwise `opened_at + sla_minutes` off **the policy row that exists
    right now**, not a value captured at an earlier rung — so an SLA edit between one escalation
    and the next takes effect on the very next step that opens, matching §10's "a policy edit
    applies to steps opened after it; existing due_at values are stored and do not move". A
    missing policy row reads as the defaults (`domain.DEFAULT_SLA_MINUTES`,
    `domain.DEFAULT_POLICY_ENABLED`), exactly as `ApprovalPolicy`'s own column defaults do for a
    row this slice's `PUT` has not written yet — no policy endpoint ships in this slice, so today
    that is the *only* way a policy row exists at all.
    """
    if not approver_present:
        return None
    policy = await selectors.get_policy(session, organization_id, request_type)
    enabled = policy.enabled if policy is not None else domain.DEFAULT_POLICY_ENABLED
    if not enabled:
        return None
    sla_minutes = policy.sla_minutes if policy is not None else domain.DEFAULT_SLA_MINUTES
    return domain.compute_due_at(opened_at=opened_at, sla_minutes=sla_minutes)


@dataclass(frozen=True, slots=True)
class StepAssignment:
    """Who a step opens against, once ADR-0006 §5's delegation rule has been applied to a chosen
    candidate rung — shared by `open_regularization_request` (routing, §4) and `escalate_step`
    (escalation, §8), so the two can never apply the delegation rule differently.

    `reason` is `assigned`, `delegated` or `unassigned` — `open_regularization_request` uses it
    as-is (it matches `ApprovalStep.reason`'s own vocabulary exactly at routing); `escalate_step`
    relabels `assigned` to `escalated` before writing the step, since "assigned" does not appear
    in `ck_approval_steps_reason` for a step this function opened.
    """

    approver_id: uuid.UUID | None
    approver_name: str | None
    acting_for_id: uuid.UUID | None
    delegation_id: uuid.UUID | None
    reason: str


async def _resolve_step_assignment(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    candidate: domain.Rung | None,
    requester_employee_id: uuid.UUID,
    now: datetime,
    skip_ids: Collection[uuid.UUID] = (),
) -> StepAssignment:
    """ADR-0006 §5, applied to whichever candidate the caller's own walk picked: if `candidate`
    has an active delegation to `D`, `D` is not the requester, and `D`'s node is not `exited`, the
    step goes to `D` with `acting_for = candidate`; otherwise it goes to `candidate` with no
    delegation. `candidate=None` is "the walk found nobody" — chain-exhausted at routing, or the
    chain above the original approver ran out at escalation — and the step opens unassigned with
    nothing left to check.

    `skip_ids` is empty at routing (`open_regularization_request` never passes it: nobody has
    failed to answer anything yet, so there is nobody to keep a delegation from reaching).
    `escalate_step` passes `{original_approver_id, previous_approver_id}` — `escalate_step`'s own
    docstring promises "the delegate's manager is never the next rung" and, more generally, that
    escalation only ever widens who may act, never hands the request straight back to whoever just
    missed the deadline. Without this, a delegation *at the new rung* pointing at either id would
    silently reopen the step against the very person the walk was escalating away from: `D`, the
    new rung's chosen delegate, is checked against `skip_ids` before the step is handed to them,
    and a match falls back to the plain, undelegated candidate instead — never to `unassigned`,
    since `candidate` itself already cleared `pick_next_approver`'s own skip list and is a legal
    approver in its own right.
    """
    if candidate is None:
        return StepAssignment(None, None, None, None, "unassigned")

    delegation = await selectors.get_active_delegation_for(
        session, organization_id, candidate.employee_id, now
    )
    if (
        delegation is not None
        and delegation.delegate_employee_id != requester_employee_id
        and delegation.delegate_employee_id not in skip_ids
    ):
        delegate_node = await selectors.get_hierarchy_node(
            session, organization_id, delegation.delegate_employee_id
        )
        if delegate_node is not None and delegate_node.status != "exited":
            return StepAssignment(
                delegation.delegate_employee_id,
                delegate_node.display_name,
                candidate.employee_id,
                delegation.id,
                "delegated",
            )
    return StepAssignment(candidate.employee_id, candidate.display_name, None, None, "assigned")


async def open_regularization_request(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    source_ref: uuid.UUID,
    requester_employee_id: uuid.UUID,
    summary: dict[str, Any],
    request_note: str | None,
) -> ApprovalRequest | None:
    """Open a `hrms.time.regularization` request from `hrms.time.regularization.requested`
    (ADR-0006 §4). Thin wrapper over `_open_request`, kept as its own name and signature so every
    existing call site and test is untouched by `REQUEST_TYPE_LEAVE` landing beside it."""
    return await _open_request(
        session,
        request_type=REQUEST_TYPE_REGULARIZATION,
        organization_id=organization_id,
        source_ref=source_ref,
        requester_employee_id=requester_employee_id,
        summary=summary,
        request_note=request_note,
    )


async def open_leave_request(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    source_ref: uuid.UUID,
    requester_employee_id: uuid.UUID,
    summary: dict[str, Any],
    request_note: str | None,
) -> ApprovalRequest | None:
    """Open a `hrms.time.leave` request from `hrms.time.leave.requested` (HRMS leave slice 1) —
    the same opening rule ADR-0006 §4 states for regularisation, applied to a second request
    type through the shared `_open_request`."""
    return await _open_request(
        session,
        request_type=REQUEST_TYPE_LEAVE,
        organization_id=organization_id,
        source_ref=source_ref,
        requester_employee_id=requester_employee_id,
        summary=summary,
        request_note=request_note,
    )


INVENTORY_REQUEST_TYPES = (REQUEST_TYPE_INVENTORY_TRANSFER, REQUEST_TYPE_INVENTORY_ADJUSTMENT)


async def open_inventory_request(
    session: AsyncSession,
    *,
    request_type: str,
    organization_id: uuid.UUID,
    source_ref: uuid.UUID,
    requester_employee_id: uuid.UUID,
    summary: dict[str, Any],
    request_note: str | None,
) -> ApprovalRequest | None:
    """Open an `operations.inventory.transfer` / `operations.inventory.adjustment` request from
    operations' `.requested` event — the same opening rule ADR-0006 §4 states for every type,
    through the shared `_open_request`. Any other `request_type` is a caller bug, refused here
    rather than routed."""
    if request_type not in INVENTORY_REQUEST_TYPES:
        raise ValueError(f"{request_type!r} is not an inventory request type")
    return await _open_request(
        session,
        request_type=request_type,
        organization_id=organization_id,
        source_ref=source_ref,
        requester_employee_id=requester_employee_id,
        summary=summary,
        request_note=request_note,
    )


async def _open_request(
    session: AsyncSession,
    *,
    request_type: str,
    organization_id: uuid.UUID,
    source_ref: uuid.UUID,
    requester_employee_id: uuid.UUID,
    summary: dict[str, Any],
    request_note: str | None,
) -> ApprovalRequest | None:
    """The routing engine's one "open a request" rule (ADR-0006 §4), shared by every request
    type this service knows how to route — parametrized by `request_type` rather than
    duplicated, which is what let `open_leave_request` add a second product's request type with
    no new routing logic to get subtly out of step with the first.

    Returns `None` when the insert is a no-op: `ON CONFLICT (organization_id, request_type,
    source_ref) DO NOTHING` means a producer that re-published the same request under a new event
    id opens nothing twice (rule 1) — `processed_events` already covers redelivery of the same
    event id, one layer up in `app/consumers.py`. Returns the new request, with its one opened
    step, otherwise.
    """
    now = await db_now(session)

    insert_stmt = (
        pg_insert(ApprovalRequest)
        .values(
            organization_id=organization_id,
            request_type=request_type,
            source_ref=source_ref,
            requester_employee_id=requester_employee_id,
            summary=summary,
            request_note=request_note,
            status="pending",
            opened_at=now,
        )
        .on_conflict_do_nothing(
            index_elements=[
                ApprovalRequest.organization_id,
                ApprovalRequest.request_type,
                ApprovalRequest.source_ref,
            ]
        )
        .returning(ApprovalRequest.id)
    )
    inserted_id = (await session.execute(insert_stmt)).scalar_one_or_none()
    if inserted_id is None:
        return None
    request = await session.get(ApprovalRequest, inserted_id)
    if request is None:
        # `assert` is stripped under `-O`, which would turn this invariant violation into a
        # `NoneType has no attribute ...` a few lines down instead of a clear failure at the
        # point it actually happened — the row was just inserted in this same transaction, so its
        # absence here means the transaction/session state itself is broken, not a normal,
        # expected outcome worth a typed domain error.
        raise RuntimeError(f"approval request {inserted_id} vanished immediately after insert")

    walk = await selectors.walk_chain(session, organization_id, requester_employee_id)
    approver_rung = pick_approver(walk.rungs, requester_employee_id)

    # ADR-0006 §4 rule 3 names four distinct causes for an unassigned step: the requester has no
    # `hierarchy_nodes` row at all, the chain contains a cycle, the walk hit its 32-rung depth cap,
    # or the chain simply runs out (no manager, or every manager above is exited). Collapsing all
    # four into one `"chain_exhausted"` reason would make a cyclic copy and a merely top-of-chain
    # requester indistinguishable on the audit row — `walk`'s own `cycle_detected`/`depth_exceeded`
    # flags (the same ones `GET /chain`'s `unresolved` field reports, using the same two strings)
    # are threaded through here instead of re-derived, so the population of each cause stays
    # findable independently.
    if not walk.rungs:
        unassigned_reason = "requester_not_in_hierarchy"
    elif approver_rung is not None:
        unassigned_reason = None
    elif walk.cycle_detected:
        unassigned_reason = "cycle"
    elif walk.depth_exceeded:
        unassigned_reason = "depth_exceeded"
    else:
        unassigned_reason = "chain_exhausted"

    # `approver_rung` is a row `walk_chain` already read off `hierarchy_nodes` (the same query
    # that picked the approver in the first place), so `_resolve_step_assignment`'s delegation
    # check is the only extra I/O this needs — no second lookup for a display name already in
    # hand.
    assignment = await _resolve_step_assignment(
        session,
        organization_id=organization_id,
        candidate=approver_rung,
        requester_employee_id=requester_employee_id,
        now=now,
    )
    step_approver_id = assignment.approver_id
    step_approver_name = assignment.approver_name
    step_acting_for_id = assignment.acting_for_id
    step_delegation_id = assignment.delegation_id
    step_reason = assignment.reason

    # Slice 4 (ADR-0006 §4 rule 4, §8, §10): the policy's `sla_minutes` at the moment this step
    # opens, or `None` for an unassigned step or a disabled policy. Slices 2-3 opened every step
    # with `due_at = None` unconditionally, because no sweep existed yet to act on a deadline —
    # arming it here is what slice 4 exists to do; see `_sla_due_at`'s own docstring for why the
    # sweep does **not** retroactively adopt the `due_at = NULL` rows those slices already wrote
    # (ADR-0006 §10: "steps opened earlier keep due_at = NULL and never escalate").
    step_due_at = await _sla_due_at(
        session,
        organization_id=organization_id,
        request_type=request_type,
        approver_present=step_approver_id is not None,
        opened_at=now,
    )

    step = ApprovalStep(
        organization_id=organization_id,
        request_id=request.id,
        level=1,
        approver_employee_id=step_approver_id,
        acting_for_employee_id=step_acting_for_id,
        delegation_id=step_delegation_id,
        reason=step_reason,
        opened_at=now,
        due_at=step_due_at,
        outcome="open",
    )
    session.add(step)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=organization_id,
        actor_id=None,
        acting_as="approval.consumer",
        action="approval.request.opened",
        entity="approval_request",
        entity_id=request.id,
        after={
            "request_type": request.request_type,
            "source_ref": source_ref,
            "requester_employee_id": requester_employee_id,
            "step_reason": step_reason,
            "approver_employee_id": step_approver_id,
        },
        reason=unassigned_reason,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=organization_id,
        event_type=EVT_REQUEST_OPENED,
        aggregate_type="approval_request",
        aggregate_id=request.id,
        payload={
            "organization_id": str(organization_id),
            "request_id": str(request.id),
            "request_type": request.request_type,
            "source_ref": str(source_ref),
            "requester_employee_id": str(requester_employee_id),
            "approver_employee_id": str(step_approver_id) if step_approver_id else None,
            # Additive (data model amendment): the approver's display name off the hierarchy
            # copy, so an ordinary employee — who holds `employee.read:OWN` and so cannot look
            # the approver up themselves — can still be shown who a pending step is waiting on.
            # `None` for exactly the same two reasons `approver_employee_id` is `None`: the step
            # is unassigned, or (defensively; not reachable through this function today, since
            # `step_approver_id` is always drawn from a row already read off `hierarchy_nodes`)
            # the approver has no row in the copy.
            "approver_name": step_approver_name,
            "acting_for_employee_id": str(step_acting_for_id) if step_acting_for_id else None,
            "due_at": step_due_at.isoformat() if step_due_at else None,
        },
        actor=None,
    )
    return request


# --------------------------------------------------------------------- deciding (§6, §7)

#: The one place an incoming decision's `outcome` is turned into the step's `outcome` (which is
#: also the request's `status` — see `decide_request` below). Explicit and exhaustive, not
#: `"approved" if outcome == "approve" else "rejected"`: that shape maps *everything that is not
#: exactly "approve"* to a rejection, so a typo, a stale client, or a third outcome added to
#: `DecisionIn.outcome` (`app/schemas.py`) without a matching case here would silently reject a
#: request instead of failing loudly.
_DECISION_OUTCOMES = {"approve": "approved", "reject": "rejected"}


@dataclass(frozen=True, slots=True)
class Eligibility:
    eligible: bool
    #: Set only when eligibility came through route (a) on a delegated step (ADR-0006 §7's
    #: precise rule for `acting_for_employee_id` on the decided request).
    acting_for_employee_id: uuid.UUID | None
    #: The caller's display name off the hierarchy copy, carried out of the one `caller_node`
    #: lookup this function already makes for every route but the two early, ineligible returns
    #: below (the requester, and a request with no open step) — neither of which `decide_request`
    #: ever turns into a published event, since both raise before publishing. `None` when the
    #: caller has no row in the copy at all, which is expected and legal for a route-(c)
    #: `decide:ORG` holder (an HR admin need not be anyone's manager to be in the copy).
    decider_name: str | None = None


async def check_eligibility(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    request: ApprovalRequest,
    caller_id: uuid.UUID,
    has_org_decide: bool,
    now: datetime,
) -> Eligibility:
    """ADR-0006 §6, evaluated under the request row's lock. Rule 3 — the requester is never
    eligible, and it overrides every other route including `decide:ORG` — is checked first, ahead
    of even looking at any step.

    **Routes (a) and (b) search the request's step *lineage*** — every step with outcome `open`,
    `delegated` or `escalated` (`selectors.get_lineage_steps`) — not only the current open step.
    Through slice 3 the two were interchangeable: a `delegated`-outcome predecessor's original
    approver was already reachable through the *successor*'s `acting_for_employee_id`
    (`app/selectors.py`'s `_INBOX_CANDIDATES_CTE` docstring explains why in full), so checking
    only the open step and checking the whole lineage gave the same answer. Escalation
    (`escalate_step`, §8) breaks that: a successor step's `acting_for_employee_id` is set only
    when a delegation applies *at the new rung*, never to carry the *previous* rung's approver
    forward. §6's own consequence — "the original approver stays eligible after delegation (b),
    and after escalation (a on the escalated step)" — is only true once the search actually
    covers step history, which is what makes this slice's "the original approver can still decide
    after escalation" hold rather than merely being asserted.

    **Route (c) still reads only the current open step** — condition 5(c) is phrased over "the
    request's open step" specifically, not its lineage, and `decide:ORG` answering an *abandoned*
    historical step would mean HR could act on a step someone else already has route (a)/(b)
    eligibility for, which is not what route (c) is for.

    Routes (a), (b) and (c) all require the caller's own node not be `exited` (ADR-0006 §9's
    negative-authz case: an exited employee holding `decide:ORG` must not be able to decide, or
    read `request_note` through, a route-(c) step just because their own node is stale). Only
    route (c)'s *approver* check tolerates an exited node — that is precisely what route (c) is
    for.
    """
    if request.requester_employee_id == caller_id:
        return Eligibility(False, None)

    steps = await selectors.get_lineage_steps(session, organization_id, request.id)
    open_step = next((s for s in steps if s.outcome == "open"), None)
    if open_step is None:
        # Defensive only: a pending request always has exactly one open step by construction
        # (`uq_approval_steps_open_per_request`), so `steps` (which always includes it — `open`
        # is one of the three outcomes this lineage search matches) can only be missing it if
        # that invariant itself has broken.
        return Eligibility(False, None)

    caller_node = await selectors.get_hierarchy_node(session, organization_id, caller_id)
    # A missing node is treated as not-exited (fail open) — the same reading ADR-0006 §4 gives a
    # requester the copy has not seen yet: absence is "not caught up", not "gone".
    caller_exited = caller_node is not None and caller_node.status == "exited"
    # The one lookup above already answers "is the decider in the copy, and what is their name" —
    # every `Eligibility(True, ...)` below reuses it rather than querying `hierarchy_nodes` again
    # for the same id `decide_request` is about to publish an event about.
    decider_name = caller_node.display_name if caller_node is not None else None

    if not caller_exited:
        for step in steps:
            if step.approver_employee_id != caller_id:
                continue
            if step.delegation_id is None:
                return Eligibility(True, None, decider_name)  # route (a), plain
            delegation = await selectors.get_delegation(
                session, organization_id, step.delegation_id
            )
            if delegation is not None and domain.delegation_is_active(
                revoked_at=delegation.revoked_at,
                starts_at=delegation.starts_at,
                ends_at=delegation.ends_at,
                at=now,
            ):
                # route (a), delegated
                return Eligibility(True, step.acting_for_employee_id, decider_name)
            # Inactive delegation on *this* step: keep scanning the rest of the lineage.
            # `approver_employee_id <> acting_for_employee_id` (a table CHECK) means this step
            # itself cannot also match route (b), but an earlier or later rung might still match
            # either route independently.

        for step in steps:
            if step.acting_for_employee_id == caller_id:
                # route (b) — never activity-checked (ADR-0006 §5), on any lineage step
                return Eligibility(True, None, decider_name)

    if has_org_decide and open_step.approver_employee_id != caller_id and not caller_exited:
        if open_step.approver_employee_id is None:
            return Eligibility(True, None, decider_name)  # route (c), unassigned
        approver_node = await selectors.get_hierarchy_node(
            session, organization_id, open_step.approver_employee_id
        )
        if approver_node is not None and approver_node.status == "exited":
            return Eligibility(True, None, decider_name)  # route (c), abandoned

    return Eligibility(False, None)


async def decide_request(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    request_id: uuid.UUID,
    caller_id: uuid.UUID | None,
    has_org_decide: bool,
    outcome: str,
    comment: str | None,
) -> ApprovalRequest:
    """`POST /requests/{id}/decision` (ADR-0006 §7), one transaction:

    1. Lock the request row. 2. Re-evaluate §6 under the lock — not pending is a 409 with the
    recorded outcome; not eligible is a 403. 3. Close the one open step. 4. Record the decision.
    5. Audit, then publish `core.approval.request.decided`.
    """
    request = await selectors.get_request_for_update(session, organization_id, request_id)
    if request is None:
        # Cross-tenant and genuinely-unknown ids look identical here, on purpose — a probe must
        # not be able to tell the difference (ADR-0006 §11).
        raise NotFoundProblem("That request isn't available to you")

    if request.status != "pending":
        raise ConflictProblem(f"This request was already {request.status}")

    if caller_id is None:
        # Rule 2: a membership with no employee link decides nothing, not even an unassigned
        # step. Refused here, under the same lock and with the same 403 every other ineligible
        # caller gets, rather than at the route — so there is exactly one place this rule lives.
        raise ForbiddenProblem("Not eligible to decide this request")

    now = await db_now(session)
    eligibility = await check_eligibility(
        session,
        organization_id=organization_id,
        request=request,
        caller_id=caller_id,
        has_org_decide=has_org_decide,
        now=now,
    )
    if not eligibility.eligible:
        raise ForbiddenProblem("Not eligible to decide this request")

    step = await selectors.get_open_step(session, organization_id, request_id)
    if step is None:
        # `assert` is stripped under `-O`; guaranteed by `uq_approval_steps_open_per_request` and
        # already required by `check_eligibility` above (it would have returned ineligible
        # otherwise) — its absence here means that invariant broke, not a normal request state.
        raise RuntimeError(f"approval request {request_id} has no open step to decide")
    try:
        step.outcome = _DECISION_OUTCOMES[outcome]
    except KeyError:
        # `DecisionIn.outcome`'s `Literal["approve", "reject"]` (`app/schemas.py`) already refuses
        # anything else at the HTTP boundary, but this function is called directly by tests and
        # will be by future consumers — an explicit, exhaustive mapping means an unrecognised
        # value raises here rather than silently falling through to "rejected" the way `outcome ==
        # "approve" else "rejected"` used to (a typo, or a third outcome added to the schema
        # without a matching case here, would otherwise reject a request nobody meant to reject).
        raise ValueError(f"unknown decision outcome: {outcome!r}") from None
    step.closed_at = now

    request.status = step.outcome
    request.decided_by_employee_id = caller_id
    request.acting_for_employee_id = eligibility.acting_for_employee_id
    request.decided_at = now
    request.comment = comment

    record_audit(
        session,
        AuditLog,
        organization_id=organization_id,
        actor_id=caller_id,
        acting_as=(
            str(eligibility.acting_for_employee_id) if eligibility.acting_for_employee_id else None
        ),
        action="approval.request.decided",
        entity="approval_request",
        entity_id=request.id,
        before={"status": "pending"},
        after={"status": request.status},
        # `comment` and `request_note` never land in an audit before/after blob (ADR-0006 §3).
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=organization_id,
        event_type=EVT_REQUEST_DECIDED,
        aggregate_type="approval_request",
        aggregate_id=request.id,
        payload={
            "organization_id": str(organization_id),
            "request_id": str(request.id),
            "request_type": request.request_type,
            "source_ref": str(request.source_ref),
            "outcome": request.status,
            "decided_by_employee_id": str(caller_id),
            # Additive (data model amendment), reusing `check_eligibility`'s own `caller_node`
            # lookup (`Eligibility.decider_name`) rather than a second query for the same id.
            # `None` for a route-(c) `decide:ORG` decider who is legitimately not in the copy —
            # not a failure, and not distinguishable on the wire from any other absent name.
            "decided_by_name": eligibility.decider_name,
            # `acting_for_employee_id`'s own name is deliberately not added here: the one
            # consumer this event has (attendance's "Waiting on {Name}" line) renders the
            # decider, never the person they stood in for, and no other consumer has asked for
            # it — an unrequested field is one more thing to keep in sync with the copy for no
            # known reader. Add `acting_for_name` the same way, off `Eligibility`, if that
            # changes.
            "acting_for_employee_id": (
                str(eligibility.acting_for_employee_id)
                if eligibility.acting_for_employee_id
                else None
            ),
            "comment": comment,
            "decided_at": now.isoformat(),
        },
        actor={"employee_id": str(caller_id)},
    )
    return request


# --------------------------------------------------------------------- the product's return leg


async def apply_regularization_outcome(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    source_ref: uuid.UUID,
    outcome: str,
    failure_reason: str | None,
) -> ApprovalRequest | None:
    """`hrms.time.regularization.applied` → the write-once `application_*` trio (ADR-0006 §12).
    Thin wrapper over `_apply_outcome`, kept as its own name so every existing call site and test
    is untouched by `apply_leave_outcome` landing beside it."""
    return await _apply_outcome(
        session,
        request_type=REQUEST_TYPE_REGULARIZATION,
        organization_id=organization_id,
        source_ref=source_ref,
        outcome=outcome,
        failure_reason=failure_reason,
    )


async def apply_leave_outcome(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    source_ref: uuid.UUID,
    outcome: str,
    failure_reason: str | None,
) -> ApprovalRequest | None:
    """`hrms.time.leave.applied` → the write-once `application_*` trio (ADR-0006 §12), for the
    leave request type."""
    return await _apply_outcome(
        session,
        request_type=REQUEST_TYPE_LEAVE,
        organization_id=organization_id,
        source_ref=source_ref,
        outcome=outcome,
        failure_reason=failure_reason,
    )


async def apply_inventory_outcome(
    session: AsyncSession,
    *,
    request_type: str,
    organization_id: uuid.UUID,
    source_ref: uuid.UUID,
    outcome: str,
    failure_reason: str | None,
) -> ApprovalRequest | None:
    """Operations' `.applied` events -> the write-once `application_*` trio (ADR-0006 §12), for
    the two inventory request types."""
    if request_type not in INVENTORY_REQUEST_TYPES:
        raise ValueError(f"{request_type!r} is not an inventory request type")
    return await _apply_outcome(
        session,
        request_type=request_type,
        organization_id=organization_id,
        source_ref=source_ref,
        outcome=outcome,
        failure_reason=failure_reason,
    )


async def _apply_outcome(
    session: AsyncSession,
    *,
    request_type: str,
    organization_id: uuid.UUID,
    source_ref: uuid.UUID,
    outcome: str,
    failure_reason: str | None,
) -> ApprovalRequest | None:
    """The product's return leg (ADR-0006 §12), shared by every request type — see
    `_open_request`'s identical reasoning for why this is parametrized rather than duplicated.

    Returns `None` when there is no request to apply to yet (the `.applied` event outran
    `.requested`, which `app/consumers.py`'s idempotent handling tolerates as a log-and-drop
    rather than a retry that could reorder them further). Returns the request unchanged, without
    writing anything, when it is still `pending` (the decision has not landed here yet) or
    already carries an `application_state` (a replay) — both are legal, expected shapes
    (`ck_approval_requests_application_needs_decision` would refuse the first as an `INSERT`-time
    invariant, and `trg_approval_requests_decide_once` would refuse the second as an `UPDATE`;
    checking here turns what would otherwise be a raised exception into the ordinary no-op replay
    handling every consumer in this codebase uses).
    """
    request = await selectors.get_request_by_source(
        session, organization_id, request_type, source_ref
    )
    if request is None or request.status == "pending" or request.application_state is not None:
        return request

    now = await db_now(session)
    request.application_state = outcome
    request.applied_at = now
    request.application_failure_reason = failure_reason if outcome == "failed" else None

    record_audit(
        session,
        AuditLog,
        organization_id=organization_id,
        actor_id=None,
        acting_as="approval.consumer",
        action="approval.request.application_reported",
        entity="approval_request",
        entity_id=request.id,
        after={"application_state": outcome},
    )
    return request


async def withdraw_request(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    request_type: str,
    source_ref: uuid.UUID,
) -> ApprovalRequest | None:
    """Close a still-pending request's open step because the product withdrew the thing being
    asked about — leave's own cancellation of a request nobody has decided yet
    (`hrms.time.leave.cancelled`), so that approving or rejecting it is no longer offered to
    anyone (HRMS leave slice 1).

    **Deliberately does not touch `approval_requests.status`.** An undecided withdrawal has no
    decider, and `ck_approval_requests_decided_iff_not_pending` requires a non-pending status to
    carry one — which `ck_approval_requests_decider_not_requester` then forbids from ever being
    the requester themselves. There is no legal non-pending status this function could write
    for a self-withdrawal without a schema change this slice does not make. Closing the one open
    step instead — with the same `'rejected'` outcome a real rejection already uses — achieves
    the actual requirement without one: every eligibility route (ADR-0006 §6 (a)/(b)/(c)) requires
    an `open`/`delegated`/`escalated` *step* naming the caller, so a request whose only step is
    closed has nothing left for anyone to decide, and `decide_request` itself finds no open step
    to act on. `uq_approval_steps_open_per_request` is "at most one open step", never "at least
    one", so a pending request with zero open steps is a legal, quiet, terminal state — not a
    violated invariant. The authoritative fact of *why* nothing will ever decide it lives on the
    product's own row (`leave.leave_requests.status = 'cancelled'`), not here.

    Locks the request the same way `decide_request` does (`get_request_by_source_for_update`,
    "lock order is always request, then steps") so a withdrawal racing an in-flight decision can
    never leave the step half-closed: whichever transaction gets the lock first wins, and the
    second sees the already-changed state under its own lock and no-ops.

    Returns `None`/the request unchanged for the same no-op reasons every other consumer in this
    codebase uses: no request to withdraw yet (`.cancelled` outran `.requested`), already decided,
    or already withdrawn (no open step left) — none of these are errors.
    """
    request = await selectors.get_request_by_source_for_update(
        session, organization_id, request_type, source_ref
    )
    if request is None or request.status != "pending":
        return request

    step = await selectors.get_open_step(session, organization_id, request.id)
    if step is None:
        return request

    now = await db_now(session)
    step.outcome = "rejected"
    step.closed_at = now

    record_audit(
        session,
        AuditLog,
        organization_id=organization_id,
        actor_id=None,
        acting_as="approval.consumer",
        action="approval.request.withdrawn",
        entity="approval_request",
        entity_id=request.id,
        after={"step_id": step.id, "level": step.level},
    )
    return request


# --------------------------------------------------------------------- delegation (slice 3, §5)


def _integrity_error_constraint(exc: IntegrityError) -> str | None:
    """The name of the constraint that actually fired, when the driver surfaces one — or `None`
    when it does not (a constraint with no name, or an `IntegrityError` that is not a constraint
    violation at all).

    asyncpg's own exception carries `constraint_name` directly, but SQLAlchemy's asyncpg dialect
    re-raises it as a driver-shaped exception first (`AsyncAdapt_asyncpg_dbapi.IntegrityError`,
    raised `from` the original — see `sqlalchemy.dialects.postgresql.asyncpg._handle_exception`),
    so the structured attribute survives on `exc.orig.__cause__`, not on `exc.orig` itself. Both
    are checked, in that order, so this keeps working if a future SQLAlchemy version stops
    wrapping and starts surfacing the driver exception as `.orig` directly.
    """
    orig = exc.orig
    name = getattr(orig, "constraint_name", None)
    if name is not None:
        return str(name)
    cause = getattr(orig, "__cause__", None)
    name = getattr(cause, "constraint_name", None)
    return str(name) if name is not None else None


def _validate_delegation_window(
    *,
    delegator_employee_id: uuid.UUID,
    delegate_employee_id: uuid.UUID,
    starts_at: datetime,
    ends_at: datetime,
    now: datetime,
) -> None:
    """The field-level refusals ADR-0006 §5 states plainly enough to check before ever touching
    the database, each its own `ValidationProblem` with a `field` so the screens can attach the
    message to the control that caused it (screens §4.E's validation states) rather than a
    single undirected 422. Pure and synchronous — no I/O — so it is exercised directly by
    `tests/test_delegations.py` without a database round trip for the cases that do not need
    one.
    """
    if delegate_employee_id == delegator_employee_id:
        # ADR-0006 §5's own CHECK (`ck_delegations_not_self`) would refuse this too, but as a
        # raw `IntegrityError` the global handler turns into an undirected "conflicts with
        # something" 409 — the wrong status for a validation mistake, and with no field to
        # attach the screens' inline message to.
        raise ValidationProblem(
            "You can't delegate to yourself",
            errors=[
                {
                    "field": "delegate_employee_id",
                    "code": "self_delegation",
                    "message": "You can't delegate to yourself.",
                }
            ],
        )
    if ends_at <= starts_at:
        raise ValidationProblem(
            "End must be after the start",
            errors=[
                {
                    "field": "ends_at",
                    "code": "window_backwards",
                    "message": "End must be after the start.",
                }
            ],
        )
    if ends_at <= now:
        # ADR-0006 §5: "ends_at must be in the future" — screens §4.E's Ends field validates the
        # same rule client-side; this is the server making it a rule rather than a courtesy.
        raise ValidationProblem(
            "Pick a time in the future",
            errors=[
                {
                    "field": "ends_at",
                    "code": "window_in_the_past",
                    "message": "Pick a time in the future.",
                }
            ],
        )


async def create_delegation(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    delegator_employee_id: uuid.UUID,
    delegate_employee_id: uuid.UUID,
    starts_at: datetime,
    ends_at: datetime,
    created_by_employee_id: uuid.UUID,
) -> tuple[Delegation, int]:
    """`POST /delegations` (ADR-0006 §5), one transaction:

    1. Refuse self-delegation, an end before (or equal to) a start, and an end in the past.
    2. Refuse a delegator with no `hierarchy_nodes` row in this tenant, symmetrically with the
       delegate check below (no cross-tenant effect today — `organization_id` is bound into
       every query regardless — but an id this service has never seen for this org is unvalidated
       data all the same, and the same ADR-0006 §11 boundary-validation discipline the delegate
       check already follows applies here).
    3. Refuse a delegate whose `hierarchy_nodes` row is missing or `exited` (§5: "the delegate
       must have a hierarchy_nodes row whose status is not exited").
    4. Refuse an overlapping active delegation for this delegator with a clean 409 naming the
       conflicting range, falling back to a generic one if a concurrent create wins the race the
       opportunistic check cannot see — `ex_delegations_no_overlap` is the actual enforcement
       either way (data model §1.5).
    5. Insert the delegation, audit it.
    6. Re-point every currently open step this delegator holds directly to the new delegate
       (§5), **only** when `starts_at <= now` — a future-dated delegation re-points nothing at
       creation, because nothing re-points it when it starts either (§5, and this ADR rejects
       any re-route sweep, §9). The original approver stays eligible throughout (route b,
       `check_eligibility`); only the *current* step moves.

    Returns the new row and `repointed_count` (screens **C4**).
    """
    now = await db_now(session)
    _validate_delegation_window(
        delegator_employee_id=delegator_employee_id,
        delegate_employee_id=delegate_employee_id,
        starts_at=starts_at,
        ends_at=ends_at,
        now=now,
    )

    delegator_node = await selectors.get_hierarchy_node(
        session, organization_id, delegator_employee_id
    )
    if delegator_node is None:
        # Symmetric with the delegate check below, one field over: an id with no row in this
        # tenant's copy at all. Below-ORG callers can only ever name themselves here (the route's
        # own gate), so this is reachable only for an ORG caller naming a `delegator_employee_id`
        # the copy has never seen — a stale or cross-tenant id, never a live routing decision.
        raise ValidationProblem(
            "That delegator can't be found",
            errors=[
                {
                    "field": "delegator_employee_id",
                    "code": "delegator_not_in_tenant",
                    "message": "That delegator can't be found in this organization.",
                }
            ],
        )

    delegate_node = await selectors.get_hierarchy_node(
        session, organization_id, delegate_employee_id
    )
    if delegate_node is None or delegate_node.status == "exited":
        # `GET /delegate-candidates` already excludes both populations, so reaching here means a
        # client-supplied id the picker never offered — a raced departure, or a caller that
        # bypassed the picker entirely.
        raise ValidationProblem(
            "That colleague can't be delegated to",
            errors=[
                {
                    "field": "delegate_employee_id",
                    "code": "delegate_not_available",
                    "message": "That colleague can't be delegated to right now.",
                }
            ],
        )

    # Opportunistic, not the enforcement (`app/selectors.get_overlapping_delegation`'s own
    # docstring): reads before the insert so the common, non-racing case gets a 409 naming the
    # actual conflicting range (screens §4.E). The `except IntegrityError` below is what a race
    # this read misses still falls back to.
    overlap = await selectors.get_overlapping_delegation(
        session, organization_id, delegator_employee_id, starts_at, ends_at
    )
    if overlap is not None:
        raise ConflictProblem(
            f"You already have a delegation from {overlap.starts_at.isoformat()} to "
            f"{overlap.ends_at.isoformat()} that covers those dates."
        )

    delegation = Delegation(
        organization_id=organization_id,
        delegator_employee_id=delegator_employee_id,
        delegate_employee_id=delegate_employee_id,
        starts_at=starts_at,
        ends_at=ends_at,
        created_by_employee_id=created_by_employee_id,
    )
    session.add(delegation)
    try:
        await session.flush()
    except IntegrityError as exc:
        # `flush()` drains every pending object in the session, not only this one, so an
        # `IntegrityError` here is not necessarily *this* insert's overlap — reporting every
        # integrity failure as "you already have a delegation that covers those dates" would
        # misreport an unrelated constraint violation as this specific, wrong business rule. Only
        # `ex_delegations_no_overlap` firing gets the overlap message; anything else re-raises,
        # which the platform's own `IntegrityError` handler (`platform_core/errors.py`) turns
        # into a generic, non-leaking 409 — the same shape every other unanticipated constraint
        # violation in this service gets.
        if _integrity_error_constraint(exc) != "ex_delegations_no_overlap":
            raise
        # The race the pre-check above cannot see (the same shape `billing/app/services.py`'s
        # credit-reversal race uses): another transaction created an overlapping delegation
        # between our read and our insert, and the exclusion constraint refused ours — a clean
        # 409, not the raw `IntegrityError` the global handler would otherwise turn into a less
        # specific one.
        raise ConflictProblem("You already have a delegation that covers those dates.") from exc

    record_audit(
        session,
        AuditLog,
        organization_id=organization_id,
        actor_id=created_by_employee_id,
        action="approval.delegation.created",
        entity="delegation",
        entity_id=delegation.id,
        after={
            "delegator_employee_id": str(delegator_employee_id),
            "delegate_employee_id": str(delegate_employee_id),
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
        },
    )

    repointed_count = 0
    if starts_at <= now:
        repointable = await selectors.get_repointable_open_steps(
            session, organization_id, delegator_employee_id, delegate_employee_id
        )
        new_steps: list[ApprovalStep] = []
        for step in repointable:
            # Append-only (`trg_approval_steps_append_only`): the only permitted update to an
            # open step is closing it, which is exactly what re-pointing does before inserting
            # its successor — rerouting is never an edit (data model §1.4).
            step.outcome = "delegated"
            step.closed_at = now
            new_step = ApprovalStep(
                organization_id=organization_id,
                request_id=step.request_id,
                level=step.level,
                approver_employee_id=delegate_employee_id,
                acting_for_employee_id=delegator_employee_id,
                delegation_id=delegation.id,
                reason="delegated",
                opened_at=now,
                # "the predecessor's due_at carried over unchanged... re-pointing does not
                # restart the SLA clock; if it did, creating and revoking delegations would be a
                # way to never escalate" (ADR-0006 §5). This line predates slice 4 unchanged: it
                # was already written to carry the predecessor's stored value straight through
                # rather than recompute one, so it needed no edit the moment `step.due_at` started
                # holding a real timestamp instead of always `None` — recomputing here would be
                # exactly the bug §5 calls out, a second, silent way to reset the clock.
                due_at=step.due_at,
                outcome="open",
            )
            session.add(new_step)
            new_steps.append(new_step)
        repointed_count = len(new_steps)
        if new_steps:
            # One flush for the whole batch so every successor's id is assigned before the audit
            # loop below needs it, rather than a round trip per step.
            await session.flush()
            for new_step in new_steps:
                # One audit row per re-pointed step (ADR-0006 §13: "approval.step.delegated (one
                # per re-pointed step)").
                record_audit(
                    session,
                    AuditLog,
                    organization_id=organization_id,
                    actor_id=created_by_employee_id,
                    action="approval.step.delegated",
                    entity="approval_step",
                    entity_id=new_step.id,
                    after={
                        "request_id": str(new_step.request_id),
                        "level": new_step.level,
                        "approver_employee_id": str(delegate_employee_id),
                        "acting_for_employee_id": str(delegator_employee_id),
                    },
                    reason="delegation_created",
                )

    return delegation, repointed_count


async def revoke_delegation(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    delegation_id: uuid.UUID,
    caller_id: uuid.UUID,
    is_org: bool,
) -> Delegation:
    """`POST /delegations/{id}/revoke` (ADR-0006 §5, §11). Revoking is the only mutation
    `trg_delegations_revoke_only` permits, and it changes nothing else about the row or about
    any step it already re-pointed: "Revocation and expiry never re-route... What it changes is
    eligibility" (§5) — no step here is touched, closed, or reopened.

    Below ORG (read as OWN, `app/catalogue.py`'s collapse), only the delegator may revoke — the
    delegate can *see* the row (`GET /delegations`' OWN reading matches either party) but may
    not act on it, the same 403-after-404 split `decide_request` draws for a caller who already
    knows a row exists (ADR-0006 §11): a bystander who is neither party gets **404** (cannot see
    it at all), a delegate who is not the delegator gets **403** (sees it, may not revoke it).

    Idempotent in the sense `decide_request`'s "already decided" is: a second revoke is refused
    with a clean **409** naming what happened — never the trigger's raw exception, and never a
    silent no-op that could hide a client retrying against a delegation someone else already
    revoked (screens §4.E: "This was already revoked.").
    """
    delegation = await selectors.get_delegation(session, organization_id, delegation_id)
    if delegation is None:
        raise NotFoundProblem("That delegation isn't available to you")
    if not is_org and caller_id not in (
        delegation.delegator_employee_id,
        delegation.delegate_employee_id,
    ):
        # Cross-tenant, unknown, and "neither party, below ORG" all look identical here, on
        # purpose (ADR-0006 §11's probe-resistance rule).
        raise NotFoundProblem("That delegation isn't available to you")
    if not is_org and caller_id != delegation.delegator_employee_id:
        raise ForbiddenProblem("Only the delegator may revoke this delegation")

    if delegation.revoked_at is not None:
        raise ConflictProblem("This delegation was already revoked")

    now = await db_now(session)
    delegation.revoked_at = now
    delegation.revoked_by_employee_id = caller_id

    record_audit(
        session,
        AuditLog,
        organization_id=organization_id,
        actor_id=caller_id,
        action="approval.delegation.revoked",
        entity="delegation",
        entity_id=delegation.id,
        before={"revoked_at": None},
        after={"revoked_at": now.isoformat()},
    )
    return delegation


# --------------------------------------------------------------------- SLA escalation (slice 4, §8)


@dataclass(frozen=True, slots=True)
class EscalationResult:
    """What `escalate_step` did with one due step — returned rather than raised for every
    "nothing to do" outcome, so `app/cron.py`'s per-step loop counts a skip instead of treating a
    race, a beaten-to-it decision, or a not-actually-due-yet re-check as an error."""

    #: `False` for every re-check failure below (a concurrent sweeper or a decision already holds
    #: the lock, the request is no longer pending, the step is no longer open/assigned/due under
    #: this transaction's own later clock read). `True` once the successor step is written.
    escalated: bool
    #: Only meaningful when `escalated` is `True`: whether the chain was exhausted and the
    #: successor opened unassigned (ADR-0006 §8: "an exhausted chain becomes the unassigned step
    #: that decide:ORG holders answer").
    unassigned: bool = False


async def escalate_step(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    request_id: uuid.UUID,
) -> EscalationResult:
    """One escalation (ADR-0006 §8), in the caller's own tenant-bound transaction —
    `app/cron.py`'s sweep calls this once per due step discovered, each in its own
    `session_scope(organization_id=...)`.

    1. Lock the request `FOR UPDATE SKIP LOCKED` (`selectors.get_request_for_escalation`) — held
       means a decision or another replica's escalation is mid-flight, so skip rather than wait;
       the sweep is a background loop with up to 100 candidates a tick, not a queue behind one
       contended row. Lock order is request, then steps, matching §7's "the decision path and the
       sweep alike" — the two paths can never deadlock against each other.
    2. Re-read the open step and re-check `request.status == 'pending'`,
       `approver_employee_id is not None`, `due_at is not None`, `due_at <= now()` (this
       transaction's own, deliberately later clock read — ADR-0006 §8's "the re-check under the
       lock must ask whether the step is *still* due now"). Any failure is a skip. This re-check
       under the lock is what makes concurrent sweepers idempotent: the loser of a race finds the
       step already `escalated` (or already decided, or no longer due) and skips it, rather than
       escalating it a second time.
    3. Close the step (`outcome = 'escalated'`). The walk starts from the step's **original**
       approver — `acting_for_employee_id` if the step itself was delegated, else
       `approver_employee_id` — never from the delegate: "the delegate's manager is never the
       next rung, because the delegate is not in the requester's line" (§8). Next approver =
       nearest non-exited manager above the original approver, skipping the requester
       (`domain.pick_next_approver`) — same skip rule §5's routing walk uses, generalised to more
       than one id to skip.
    4. Apply delegation to that candidate (`_resolve_step_assignment`, shared with routing,
       `skip_ids={original_approver_id, previous_approver_id}`) and insert the successor:
       `level + 1`, `reason = 'escalated'` (or `'delegated'` when a delegation applied at this
       rung), `due_at` re-armed off the *current* policy (`_sla_due_at`) — the SLA clock restarts
       at every rung, unlike delegation re-pointing, which deliberately does not (§5). The
       `skip_ids` is what keeps this step 3's own docstring true beyond the walk itself: the walk
       already refuses to *land on* the person who was just asked, but a delegation active at the
       new rung could otherwise still hand the step straight back to them (or to the original
       approver, if this step was itself opened through a delegation) — `_resolve_step_assignment`
       falls back to the plain, undelegated candidate rather than resolve the delegate in that
       case, never to unassigned. A chain that has run out produces `candidate = None`, which
       `_resolve_step_assignment` turns into the unassigned terminus: no approver, no due date, and
       `ck_approval_steps_unassigned_iff_no_approver`/`..._unassigned_has_no_due` make that the
       only representable shape for it. Unassigned steps never escalate again — the ladder
       terminates by construction, not by a flag this function has to remember to check.
    5. Audit row `approval.step.escalated` (`acting_as = "approval.sla_sweep"`, `reason =
       "sla_elapsed"` — ADR-0006 §13); publish `core.approval.request.escalated`.

    Nothing here ever decides the request. Escalation only ever widens who may answer it — the
    original approver's own step (whatever outcome it now carries) still satisfies
    `check_eligibility`'s route (a)/(b) lineage search, so they can still act on the request
    directly by id even though this function has moved the *open* step on.
    """
    request = await selectors.get_request_for_escalation(session, organization_id, request_id)
    if request is None:
        return EscalationResult(escalated=False)
    if request.status != "pending":
        return EscalationResult(escalated=False)

    step = await selectors.get_open_step(session, organization_id, request_id)
    if step is None or step.approver_employee_id is None or step.due_at is None:
        return EscalationResult(escalated=False)

    now = await db_now(session)
    if step.due_at > now:
        # Re-armed since discovery (another replica escalated it) or genuinely not due yet under
        # this transaction's own, later clock read.
        return EscalationResult(escalated=False)
    if step.level >= domain.MAX_CHAIN_DEPTH + 1:
        # Defensive, not expected to be reachable: `ck_approval_steps_level_range` refuses a 34th
        # rung outright, and a step already at the ceiling has nowhere this function could send
        # it. Skipped rather than left to raise `IntegrityError` out of a background sweep tick.
        # `MAX_CHAIN_DEPTH + 1` (33), not a bare literal, so this bound and the chain walk's own
        # (`app/selectors.py`'s `_CHAIN_WALK_SQL`) can never drift on what "the ceiling" means.
        return EscalationResult(escalated=False)

    original_approver_id = step.acting_for_employee_id or step.approver_employee_id
    previous_approver_id = step.approver_employee_id
    step.outcome = "escalated"
    step.closed_at = now

    walk = await selectors.walk_chain(session, organization_id, original_approver_id)
    candidate = domain.pick_next_approver(
        walk.rungs, skip_ids=(original_approver_id, request.requester_employee_id)
    )
    assignment = await _resolve_step_assignment(
        session,
        organization_id=organization_id,
        candidate=candidate,
        requester_employee_id=request.requester_employee_id,
        now=now,
        # Escalation only ever widens who may act (this function's own docstring) — a delegation
        # at the new rung must not be allowed to hand the step straight back to the person who
        # just missed the deadline (`previous_approver_id`) or to the original approver it was
        # already escalated away from once before (`original_approver_id`, when those two differ
        # — a delegated step's `approver_employee_id` is the delegate, not the original).
        skip_ids={original_approver_id, previous_approver_id},
    )
    # `_resolve_step_assignment` speaks the routing vocabulary (`assigned`/`delegated`/
    # `unassigned`) — `assigned` does not appear in `ck_approval_steps_reason` for a step this
    # function opens, so it is relabelled `escalated` here; `delegated`/`unassigned` already match.
    step_reason = "escalated" if assignment.reason == "assigned" else assignment.reason

    due_at = await _sla_due_at(
        session,
        organization_id=organization_id,
        request_type=request.request_type,
        approver_present=assignment.approver_id is not None,
        opened_at=now,
    )

    new_step = ApprovalStep(
        organization_id=organization_id,
        request_id=request.id,
        level=step.level + 1,
        approver_employee_id=assignment.approver_id,
        acting_for_employee_id=assignment.acting_for_id,
        delegation_id=assignment.delegation_id,
        reason=step_reason,
        opened_at=now,
        due_at=due_at,
        outcome="open",
    )
    session.add(new_step)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=organization_id,
        actor_id=None,
        acting_as="approval.sla_sweep",
        action="approval.step.escalated",
        entity="approval_step",
        entity_id=new_step.id,
        before={"approver_employee_id": str(previous_approver_id)},
        after={
            "level": new_step.level,
            "reason": step_reason,
            "approver_employee_id": (
                str(assignment.approver_id) if assignment.approver_id else None
            ),
        },
        reason="sla_elapsed",
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=organization_id,
        event_type=EVT_REQUEST_ESCALATED,
        aggregate_type="approval_request",
        aggregate_id=request.id,
        payload={
            "organization_id": str(organization_id),
            "request_id": str(request.id),
            "request_type": request.request_type,
            "source_ref": str(request.source_ref),
            "step_id": str(new_step.id),
            "level": new_step.level,
            "from_approver_employee_id": str(previous_approver_id),
            "to_approver_employee_id": (
                str(assignment.approver_id) if assignment.approver_id else None
            ),
            "acting_for_employee_id": (
                str(assignment.acting_for_id) if assignment.acting_for_id else None
            ),
            "due_at": due_at.isoformat() if due_at else None,
        },
        actor=None,
    )
    return EscalationResult(escalated=True, unassigned=assignment.approver_id is None)


# ------------------------------------------------------------- policy administration (slice 4, §10)


async def update_policy(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    request_type: str,
    sla_minutes: int,
    enabled: bool,
    actor_id: uuid.UUID | None,
) -> selectors.PolicyView:
    """`PUT /policies/{request_type}` (ADR-0006 §10, §11): create the row on first write, update
    it in place otherwise — "the row is created on first `PUT`". Only `KNOWN_REQUEST_TYPES` may
    be configured, refused as a field-level 422 rather than a silently-orphaned row `GET
    /policies` would never show back (that endpoint iterates the same tuple).

    **This function writes exactly the two columns ADR-0006 §10 says `approval_policies`
    governs, and nothing else** — it never touches `approval_requests` or `approval_steps`, never
    decides anything, and never reads whether any request of this type is even pending. That is
    what makes "disabling turns off escalation only, never approval" true by construction: there
    is no code path here that could auto-approve anything even if a bug tried, because this
    function has no access to a request to approve.

    `sla_minutes`'s 15-minute-to-365-day bounds are `ApprovalPolicy`'s own CHECK
    (`ck_approval_policies_sla_minutes_range`, ADR-0006 §8's floor). `app/schemas.PolicyUpdateIn`
    enforces the identical bounds at the boundary, so a sub-floor or over-ceiling value is a
    field-level 422 (`platform_core.errors.ValidationProblem`, naming `sla_minutes`) before this
    function is ever called — the database CHECK stays as defence in depth (§8: "enforced by the
    policy CHECK rather than by the API", so no future write path can bypass it), not as the only
    place the bound is checked, so a violation here would still be an unexpected 409 rather than
    the clean 422 a caller going through the documented endpoint always gets.
    """
    if request_type not in KNOWN_REQUEST_TYPES:
        raise ValidationProblem(
            "That request type is not one approval routes",
            errors=[
                {
                    "field": "request_type",
                    "code": "unknown_request_type",
                    "message": f"{request_type!r} is not a request type approval can configure",
                }
            ],
        )

    before = await selectors.get_policy(session, organization_id, request_type)
    before_values = (
        {"sla_minutes": before.sla_minutes, "enabled": before.enabled}
        if before is not None
        else {"sla_minutes": domain.DEFAULT_SLA_MINUTES, "enabled": domain.DEFAULT_POLICY_ENABLED}
    )

    stmt = pg_insert(ApprovalPolicy).values(
        organization_id=organization_id,
        request_type=request_type,
        sla_minutes=sla_minutes,
        enabled=enabled,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[ApprovalPolicy.organization_id, ApprovalPolicy.request_type],
        set_={
            "sla_minutes": sla_minutes,
            "enabled": enabled,
            # `TimestampMixin.updated_at`'s `onupdate=func.now()` is an ORM/Core `Update()`
            # default that `on_conflict_do_update`'s `set_` never consults — the exact trap
            # `upsert_hierarchy_nodes_bulk` documents above — so without this line a second `PUT`
            # would silently freeze `updated_at` at the row's first-write instant forever.
            "updated_at": func.now(),
        },
    )
    await session.execute(stmt)

    record_audit(
        session,
        AuditLog,
        organization_id=organization_id,
        actor_id=actor_id,
        action="approval.policy.updated",
        entity="approval_policy",
        entity_id=None,
        before={"request_type": request_type, **before_values},
        after={"request_type": request_type, "sla_minutes": sla_minutes, "enabled": enabled},
    )
    return selectors.PolicyView(request_type=request_type, sla_minutes=sla_minutes, enabled=enabled)
