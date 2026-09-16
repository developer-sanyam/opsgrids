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
`revoke_delegation` (§5, §11).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
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

#: `<product>.<module>.<thing>` (ADR-0006 §12) — the one request type this slice routes.
REQUEST_TYPE_REGULARIZATION = "hrms.time.regularization"

EVT_REQUEST_OPENED = "core.approval.request.opened"
EVT_REQUEST_DECIDED = "core.approval.request.decided"


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
    (ADR-0006 §4), inside the caller's (already tenant-bound) transaction.

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
            request_type=REQUEST_TYPE_REGULARIZATION,
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

    step_approver_id: uuid.UUID | None = None
    step_approver_name: str | None = None
    step_acting_for_id: uuid.UUID | None = None
    step_delegation_id: uuid.UUID | None = None
    step_reason = "unassigned"

    if approver_rung is not None:
        step_approver_id = approver_rung.employee_id
        # `approver_rung` is a row `walk_chain` already read off `hierarchy_nodes` (the same
        # query that picked the approver in the first place), so its `display_name` is this
        # step's name at no extra round trip. Null only if the step ends up unassigned, which is
        # exactly when `approver_rung` is `None` and this whole branch is skipped.
        step_approver_name = approver_rung.display_name
        step_reason = "assigned"
        # Delegation applied at open (ADR-0006 §5): the chosen approver's active delegation, if
        # any, takes the step instead — unless the delegate is the requester (no delegation for
        # this step: rule keeps a delegation from silently changing who approves) or the
        # delegate's own node is exited.
        delegation = await selectors.get_active_delegation_for(
            session, organization_id, approver_rung.employee_id, now
        )
        if delegation is not None and delegation.delegate_employee_id != requester_employee_id:
            delegate_node = await selectors.get_hierarchy_node(
                session, organization_id, delegation.delegate_employee_id
            )
            if delegate_node is not None and delegate_node.status != "exited":
                step_approver_id = delegation.delegate_employee_id
                # Same reuse, one rung later: `delegate_node` is already the lookup this branch
                # needed to decide whether the delegate is usable at all — its `display_name`
                # replaces the manager's rather than adding a second query for it.
                step_approver_name = delegate_node.display_name
                step_acting_for_id = approver_rung.employee_id
                step_delegation_id = delegation.id
                step_reason = "delegated"

    step = ApprovalStep(
        organization_id=organization_id,
        request_id=request.id,
        level=1,
        approver_employee_id=step_approver_id,
        acting_for_employee_id=step_acting_for_id,
        delegation_id=step_delegation_id,
        reason=step_reason,
        opened_at=now,
        # Slices 2-3: no SLA clock exists yet, so every step opens with no due date at all
        # (ADR-0006 §10 amended, §8). `approval_policies.sla_minutes` is written and floored, but
        # nothing reads it before slice 4's sweep.
        due_at=None,
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
            "due_at": None,
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
    of even looking at the step. Every remaining route reads the request's one open step: in
    slices 2-3 that is always sufficient (`app/selectors.py`'s `_INBOX_PAGE_SQL` docstring
    explains why in full — nothing yet produces a `delegated`/`escalated` **outcome** step, so
    there is no history to also search).

    Routes (a), (b) and (c) all require the caller's own node not be `exited` (ADR-0006 §9's
    negative-authz case: an exited employee holding `decide:ORG` must not be able to decide, or
    read `request_note` through, a route-(c) step just because their own node is stale). Only
    route (c)'s *approver* check tolerates an exited node — that is precisely what route (c) is
    for.
    """
    if request.requester_employee_id == caller_id:
        return Eligibility(False, None)

    step = await selectors.get_open_step(session, organization_id, request.id)
    if step is None:
        # Defensive only: a pending request always has exactly one open step by construction
        # (`uq_approval_steps_open_per_request`).
        return Eligibility(False, None)

    caller_node = await selectors.get_hierarchy_node(session, organization_id, caller_id)
    # A missing node is treated as not-exited (fail open) — the same reading ADR-0006 §4 gives a
    # requester the copy has not seen yet: absence is "not caught up", not "gone".
    caller_exited = caller_node is not None and caller_node.status == "exited"
    # The one lookup above already answers "is the decider in the copy, and what is their name" —
    # every `Eligibility(True, ...)` below reuses it rather than querying `hierarchy_nodes` again
    # for the same id `decide_request` is about to publish an event about.
    decider_name = caller_node.display_name if caller_node is not None else None

    if step.approver_employee_id == caller_id and not caller_exited:
        if step.delegation_id is None:
            return Eligibility(True, None, decider_name)  # route (a), plain
        delegation = await selectors.get_delegation(session, organization_id, step.delegation_id)
        if delegation is not None and domain.delegation_is_active(
            revoked_at=delegation.revoked_at,
            starts_at=delegation.starts_at,
            ends_at=delegation.ends_at,
            at=now,
        ):
            # route (a), delegated
            return Eligibility(True, step.acting_for_employee_id, decider_name)
        # Inactive delegation: falls through. `approver_employee_id <> acting_for_employee_id`
        # (a table CHECK) means this same caller cannot also match route (b) below on this step.

    if step.acting_for_employee_id == caller_id and not caller_exited:
        # route (b) — never activity-checked (ADR-0006 §5)
        return Eligibility(True, None, decider_name)

    if has_org_decide and step.approver_employee_id != caller_id and not caller_exited:
        if step.approver_employee_id is None:
            return Eligibility(True, None, decider_name)  # route (c), unassigned
        approver_node = await selectors.get_hierarchy_node(
            session, organization_id, step.approver_employee_id
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
        session, organization_id, REQUEST_TYPE_REGULARIZATION, source_ref
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
                # restart the SLA clock" (ADR-0006 §5). Always `None` in this slice regardless
                # (ADR-0006 §10 amended) — carried through rather than hardcoded so slice 4's
                # armed due dates re-point correctly without this line changing.
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
