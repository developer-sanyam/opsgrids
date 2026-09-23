"""Reads for approval. Visibility rules live here, in one place.

Every scoped query calls `ctx.apply(...)` or filters on `organization_id` explicitly (RLS is the
net, not the filter — CLAUDE.md). A list endpoint that queries directly instead of coming through
a selector is how an organization leaks.

**The chain walk is a recursive CTE, not a traversal in Python** (ADR-0006 §5, data model §4.3 /
§2 pattern 12). The rejected alternative — loading every node in the organization into a mapping
and walking it in `domain.py` — is 4,000 rows to answer a 33-row question on the hot path of a
consumer; the query returns at most 33 rows (32-rung depth cap plus the starting row) against one
indexed lookup per rung (`pk_hierarchy_nodes`).
"""

from __future__ import annotations

import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import ColumnElement, Select, select, text
from sqlalchemy import false as sa_false
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import DEFAULT_POLICY_ENABLED, DEFAULT_SLA_MINUTES, MAX_CHAIN_DEPTH, Rung
from app.models import ApprovalPolicy, ApprovalRequest, ApprovalStep, Delegation, HierarchyNode
from platform_core.auth import ScopeContext
from platform_core.db import get_scoped

#: `WITH RECURSIVE ... CYCLE ... SET is_cycle USING path` is the SQL-standard cycle clause
#: (Postgres 14+). The source (`employee.employees`) refuses cycles at write time (data model
#: §4.3's advisory-locked cycle check, owned by employee-service), but this copy is fed by two
#: independent event streams and can be transiently cyclic while an `A→B` and a `B→A` correction
#: are in flight in opposite order (ADR-0006 §5) — so the walk defends itself rather than trust
#: the source. `depth < :max_depth` in the recursive term caps the walk at `domain.MAX_CHAIN_DEPTH`
#: **rungs above the requester** — the same boundary employee's own cycle check enforces (a
#: 32-rung proposed chain is accepted, 33 refused). `depth` itself starts at 1 for the requester's
#: own row, which is not a rung, so the bound passed here is `MAX_CHAIN_DEPTH + 1`: the walk may
#: extend a row whose depth is still less than that, which lets the 32nd rung land at depth 33 —
#: matching this module's own "at most 33 rows" claim above rather than stopping one short of it.
#: A row that hits the cap is reported to the caller as depth-exceeded rather than silently
#: truncated.
_CHAIN_WALK_SQL = text(
    """
    WITH RECURSIVE up (
        employee_id, reporting_manager_id, dotted_line_manager_id, status,
        display_name, employee_no, location_code, depth
    ) AS (
        SELECT h.employee_id, h.reporting_manager_id, h.dotted_line_manager_id, h.status,
               h.display_name, h.employee_no, h.location_code, 1
          FROM approval.hierarchy_nodes h
         WHERE h.organization_id = :organization_id
           AND h.employee_id = :start_employee_id
        UNION ALL
        SELECT h.employee_id, h.reporting_manager_id, h.dotted_line_manager_id, h.status,
               h.display_name, h.employee_no, h.location_code, up.depth + 1
          FROM up
          JOIN approval.hierarchy_nodes h
            ON h.organization_id = :organization_id
           AND h.employee_id = up.reporting_manager_id
         WHERE up.depth < :max_depth
    )
    CYCLE employee_id SET is_cycle USING path
    SELECT employee_id, reporting_manager_id, dotted_line_manager_id, status,
           display_name, employee_no, location_code, depth, is_cycle
      FROM up
     ORDER BY depth
    """
)


@dataclass(frozen=True, slots=True)
class ChainWalk:
    """The chain-walk CTE's output, split into what a caller renders and what it warns about."""

    #: Depth order, starting at the subject employee's own row (depth 1).
    rungs: list[Rung]
    #: `True` when the `CYCLE` clause fired — the copy is transiently (or permanently, if the
    #: cycle check has a bug) cyclic. The caller must not treat `rungs` as complete.
    cycle_detected: bool
    #: `True` when the walk hit the `MAX_CHAIN_DEPTH`-rung cap without terminating naturally (no
    #: manager, or a node the copy has not seen). Distinct from a cycle: a genuinely 32-rung-deep
    #: chain is unroutable, not looping.
    depth_exceeded: bool


async def get_hierarchy_node(
    session: AsyncSession, organization_id: uuid.UUID, employee_id: uuid.UUID
) -> Rung | None:
    """One employee's row on the copy, or `None` if it has never seen one for them."""
    row = (
        (
            await session.execute(
                text(
                    """
                SELECT employee_id, reporting_manager_id, dotted_line_manager_id, status,
                       display_name, employee_no, location_code
                  FROM approval.hierarchy_nodes
                 WHERE organization_id = :organization_id AND employee_id = :employee_id
                """
                ),
                {"organization_id": str(organization_id), "employee_id": str(employee_id)},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    return Rung(
        employee_id=row["employee_id"],
        reporting_manager_id=row["reporting_manager_id"],
        dotted_line_manager_id=row["dotted_line_manager_id"],
        status=row["status"],
        display_name=row["display_name"],
        employee_no=row["employee_no"],
        location_code=row["location_code"],
        depth=1,
    )


async def walk_chain(
    session: AsyncSession, organization_id: uuid.UUID, start_employee_id: uuid.UUID
) -> ChainWalk:
    """The recursive walk upward from `start_employee_id`, at most 33 rows.

    An empty result (the subject has no `hierarchy_nodes` row at all — the backfill has not
    reached this organization, or an `onboarded` event is lagging) is a normal, expected shape:
    the caller renders it as "no chain yet", not as an error (ADR-0006 §4's "a requester missing
    from the copy is not retried" applies equally to reading the chain).
    """
    rows = (
        (
            await session.execute(
                _CHAIN_WALK_SQL,
                {
                    "organization_id": str(organization_id),
                    "start_employee_id": str(start_employee_id),
                    # +1: `depth` counts the requester's own row (depth 1), which is not a rung,
                    # so allowing `MAX_CHAIN_DEPTH` rungs above it means the last one may reach
                    # depth `MAX_CHAIN_DEPTH + 1` — see the comment on `_CHAIN_WALK_SQL`.
                    "max_depth": MAX_CHAIN_DEPTH + 1,
                },
            )
        )
        .mappings()
        .all()
    )

    rungs = [
        Rung(
            employee_id=row["employee_id"],
            reporting_manager_id=row["reporting_manager_id"],
            dotted_line_manager_id=row["dotted_line_manager_id"],
            status=row["status"],
            display_name=row["display_name"],
            employee_no=row["employee_no"],
            location_code=row["location_code"],
            depth=row["depth"],
        )
        for row in rows
    ]
    cycle_detected = any(row["is_cycle"] for row in rows)
    # The recursive term's guard means the last row the walk can ever add has depth
    # `MAX_CHAIN_DEPTH + 1` (the requester's own row at depth 1, plus up to `MAX_CHAIN_DEPTH`
    # rungs — see `_CHAIN_WALK_SQL`'s comment). If that row still names a `reporting_manager_id`,
    # the walk *might* have somewhere left to go — but naming one is not proof by itself: a
    # dangling pointer (a manager whose own row has not reached the copy yet, `HierarchyNode`'s
    # docstring calls this a normal, expected state) would make the recursive JOIN fail to extend
    # the walk regardless of the depth cap, and lands here looking identical to a genuinely
    # deeper chain unless the pointer's target is actually checked. So the last rung's manager
    # must be confirmed to resolve in the copy before this is reported as depth-exceeded rather
    # than "the chain simply ends here, unassigned" — one extra indexed lookup, paid only in this
    # already-rare edge case.
    depth_exceeded = False
    if (
        not cycle_detected
        and rungs
        and rungs[-1].depth >= MAX_CHAIN_DEPTH + 1
        and rungs[-1].reporting_manager_id is not None
    ):
        top_manager = await get_hierarchy_node(
            session, organization_id, rungs[-1].reporting_manager_id
        )
        depth_exceeded = top_manager is not None

    return ChainWalk(rungs=rungs, cycle_detected=cycle_detected, depth_exceeded=depth_exceeded)


# --------------------------------------------------------------------- routing reads (slice 2)


@dataclass(frozen=True, slots=True)
class PersonInfo:
    """One `hierarchy_nodes` row, shaped for name resolution rather than the chain walk."""

    employee_id: uuid.UUID
    display_name: str
    employee_no: str
    location_code: str | None
    status: str


async def get_hierarchy_people(
    session: AsyncSession, organization_id: uuid.UUID, employee_ids: Collection[uuid.UUID | None]
) -> dict[uuid.UUID, PersonInfo]:
    """Batch name/status resolution for a set of ids, one round trip.

    The N+1 guard every list endpoint that returns a `*_name` beside more than one id per row
    needs (ADR-0006 §11): a caller resolving the requester's name and the approver's name on 25
    inbox rows one lookup at a time is 50 round trips for one page. `None`s in the input (an
    optional `approver_employee_id`, an unset `acting_for_employee_id`) are dropped rather than
    queried for.
    """
    ids = {e for e in employee_ids if e is not None}
    if not ids:
        return {}
    stmt = select(HierarchyNode).where(
        HierarchyNode.organization_id == organization_id, HierarchyNode.employee_id.in_(ids)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return {
        row.employee_id: PersonInfo(
            employee_id=row.employee_id,
            display_name=row.display_name,
            employee_no=row.employee_no,
            location_code=row.location_code,
            status=row.status,
        )
        for row in rows
    }


async def get_request(
    session: AsyncSession, organization_id: uuid.UUID, request_id: uuid.UUID
) -> ApprovalRequest | None:
    """Org-scoped fetch by id, no lock — `get_scoped` is the platform's chokepoint for "does this
    id belong to this tenant", which is what keeps a cross-organization id a 404 rather than a
    500 or, worse, a 200 with someone else's row."""
    # `get_scoped` is typed `Any` (it is shared across every service's model hierarchy); the cast
    # is this function's own contract, not a claim about what the platform helper returns.
    return cast(
        "ApprovalRequest | None",
        await get_scoped(session, ApprovalRequest, request_id, organization_id),
    )


async def get_request_for_update(
    session: AsyncSession, organization_id: uuid.UUID, request_id: uuid.UUID
) -> ApprovalRequest | None:
    """The decision path's lock (ADR-0006 §7: "lock order is always request, then steps").
    `SELECT ... FOR UPDATE` inside the caller's transaction; the caller re-checks `status` under
    the lock rather than trusting this read, which is what makes "first decision wins" true under
    concurrency rather than merely under a single request.

    Filters `deleted_at IS NULL`, matching `get_request` (via `get_scoped`, which drops a
    soft-deleted row the same way it drops a cross-tenant one): without it, a soft-deleted request
    would be invisible to `GET /requests/{id}` — a 404 — yet still reachable and decidable through
    `POST .../decision`, one read path refusing what the write path would happily act on."""
    stmt = (
        select(ApprovalRequest)
        .where(
            ApprovalRequest.organization_id == organization_id,
            ApprovalRequest.id == request_id,
            ApprovalRequest.deleted_at.is_(None),
        )
        .with_for_update()
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_request_for_escalation(
    session: AsyncSession, organization_id: uuid.UUID, request_id: uuid.UUID
) -> ApprovalRequest | None:
    """The sweep's own lock (ADR-0006 §8): `SELECT ... FOR UPDATE SKIP LOCKED`, unlike
    `get_request_for_update`'s blocking form the decision path uses. A step and a decision race
    the same request row (§7: "lock order is always request, then steps, in the decision path and
    the sweep alike"), and the sweep is a background loop with 500 candidates a tick — it must
    move past a row someone else already holds rather than queue behind it. `None` covers both
    "another replica's escalation is mid-flight" and "a decision landed and holds the lock right
    now"; the caller treats either as a skip, not an error.

    Filters `deleted_at IS NULL`, matching `get_request_for_update` — a soft-deleted request must
    not be escalated any more than it can be decided.
    """
    stmt = (
        select(ApprovalRequest)
        .where(
            ApprovalRequest.organization_id == organization_id,
            ApprovalRequest.id == request_id,
            ApprovalRequest.deleted_at.is_(None),
        )
        .with_for_update(skip_locked=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_request_by_source(
    session: AsyncSession, organization_id: uuid.UUID, request_type: str, source_ref: uuid.UUID
) -> ApprovalRequest | None:
    """`(organization_id, request_type, source_ref)` — the join back to the product's own row
    (ADR-0006 §3), and what makes opening a request idempotent under redelivery."""
    stmt = select(ApprovalRequest).where(
        ApprovalRequest.organization_id == organization_id,
        ApprovalRequest.request_type == request_type,
        ApprovalRequest.source_ref == source_ref,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_request_by_source_for_update(
    session: AsyncSession, organization_id: uuid.UUID, request_type: str, source_ref: uuid.UUID
) -> ApprovalRequest | None:
    """The same lookup as `get_request_by_source`, `SELECT ... FOR UPDATE` — the lock
    `withdraw_request` takes so a product's withdrawal (leave's own cancellation of a request
    nobody has decided yet) and `decide_request` can never both act on the same request: whichever
    transaction gets here first serialises the other, which then re-checks `status` under its own
    lock exactly the way `decide_request` already does for a concurrent second decision."""
    stmt = (
        select(ApprovalRequest)
        .where(
            ApprovalRequest.organization_id == organization_id,
            ApprovalRequest.request_type == request_type,
            ApprovalRequest.source_ref == source_ref,
        )
        .with_for_update()
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_open_step(
    session: AsyncSession, organization_id: uuid.UUID, request_id: uuid.UUID
) -> ApprovalStep | None:
    """The one open step a pending request always has (`uq_approval_steps_open_per_request`).
    `None` for a decided request — it has none (ADR-0006 §3)."""
    stmt = select(ApprovalStep).where(
        ApprovalStep.organization_id == organization_id,
        ApprovalStep.request_id == request_id,
        ApprovalStep.outcome == "open",
    )
    return (await session.execute(stmt)).scalar_one_or_none()


#: Outcomes that still confer eligibility on the step's approver/represented original (ADR-0006
#: §6 condition 5's "some step of the request with outcome open, delegated or escalated"). Not
#: `approved`/`rejected` — a request carrying either is decided, and `check_eligibility`'s caller
#: already refuses a non-`pending` request before this ever runs.
_ELIGIBILITY_OUTCOMES = ("open", "delegated", "escalated")


async def get_lineage_steps(
    session: AsyncSession, organization_id: uuid.UUID, request_id: uuid.UUID
) -> list[ApprovalStep]:
    """Every step of this request whose outcome still confers eligibility (ADR-0006 §6 condition
    5), ordered by `level`.

    Through slice 3, `get_open_step` alone was sufficient for `check_eligibility`'s routes (a) and
    (b): a `delegated`-outcome predecessor's original approver is already reachable through the
    *successor*'s `acting_for_employee_id` (`_INBOX_CANDIDATES_CTE`'s own docstring explains why
    in full), so checking only the current open step and checking every live step in the request's
    history produced the same answer. Escalation breaks that equivalence — a successor step's
    `acting_for_employee_id` is set only when a delegation applies *at the new rung*, never to
    carry the *previous* rung's approver forward — so "the original approver stays eligible ...
    and after escalation (a on the escalated step)" (§6) requires actually looking at the step
    history rather than only the current open step.

    Bounded by construction: at most 33 rows per request (`ck_approval_steps_level_range`), served
    by `ix_approval_steps_org_request` (pattern 7, the same index `list_steps`/request-detail
    already uses) — no new index for this slice's own query.
    """
    stmt = (
        select(ApprovalStep)
        .where(
            ApprovalStep.organization_id == organization_id,
            ApprovalStep.request_id == request_id,
            ApprovalStep.outcome.in_(_ELIGIBILITY_OUTCOMES),
        )
        .order_by(ApprovalStep.level)
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_open_steps_for_requests(
    session: AsyncSession, organization_id: uuid.UUID, request_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, ApprovalStep]:
    """The same lookup as `get_open_step`, batched across a page of requests — the N+1 guard for
    `GET /requests`, which needs "who is this waiting on" for every pending row on the page."""
    if not request_ids:
        return {}
    stmt = select(ApprovalStep).where(
        ApprovalStep.organization_id == organization_id,
        ApprovalStep.request_id.in_(request_ids),
        ApprovalStep.outcome == "open",
    )
    rows = (await session.execute(stmt)).scalars().all()
    return {row.request_id: row for row in rows}


async def list_steps(
    session: AsyncSession, organization_id: uuid.UUID, request_id: uuid.UUID
) -> list[ApprovalStep]:
    """Every step of a request, opened order — the detail screen's history (ADR-0006 §11)."""
    stmt = (
        select(ApprovalStep)
        .where(
            ApprovalStep.organization_id == organization_id,
            ApprovalStep.request_id == request_id,
        )
        .order_by(ApprovalStep.opened_at)
    )
    return list((await session.execute(stmt)).scalars().all())


async def caller_is_on_any_step(
    session: AsyncSession, organization_id: uuid.UUID, request_id: uuid.UUID, employee_id: uuid.UUID
) -> bool:
    """Whether `employee_id` appears as `approver` or `acting_for` on **any** step of this
    request, any outcome — the detail read's OWN-scope widening (ADR-0006 §11, amended): "anyone
    who appears on a step of this request... any outcome" reaches a past decider, a past
    delegate, or anyone escalated past, not only whoever is eligible right now."""
    stmt = (
        select(ApprovalStep.id)
        .where(
            ApprovalStep.organization_id == organization_id,
            ApprovalStep.request_id == request_id,
            (ApprovalStep.approver_employee_id == employee_id)
            | (ApprovalStep.acting_for_employee_id == employee_id),
        )
        .limit(1)
    )
    return (await session.execute(stmt)).first() is not None


async def get_delegation(
    session: AsyncSession, organization_id: uuid.UUID, delegation_id: uuid.UUID
) -> Delegation | None:
    return cast(
        "Delegation | None", await get_scoped(session, Delegation, delegation_id, organization_id)
    )


async def get_active_delegation_for(
    session: AsyncSession,
    organization_id: uuid.UUID,
    delegator_employee_id: uuid.UUID,
    at: datetime,
) -> Delegation | None:
    """The delegation active for `delegator_employee_id` at instant `at`, if any (ADR-0006 §5) —
    used when a step opens, to decide whether the chosen approver's delegate should get it
    instead. At most one can ever exist: `ex_delegations_no_overlap` forbids two non-revoked
    windows for the same delegator from overlapping."""
    stmt = select(Delegation).where(
        Delegation.organization_id == organization_id,
        Delegation.delegator_employee_id == delegator_employee_id,
        Delegation.revoked_at.is_(None),
        Delegation.starts_at <= at,
        Delegation.ends_at > at,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_policy(
    session: AsyncSession, organization_id: uuid.UUID, request_type: str
) -> ApprovalPolicy | None:
    """The SLA policy row for one request type, or `None` when nothing has ever been written for
    it — ADR-0006 §10's "a missing row means the defaults" (`enabled = True`, `sla_minutes =
    1440`; `app/domain.DEFAULT_POLICY_ENABLED`/`DEFAULT_SLA_MINUTES`). Natural composite PK
    lookup, one row at most (`app/models.ApprovalPolicy`)."""
    stmt = select(ApprovalPolicy).where(
        ApprovalPolicy.organization_id == organization_id,
        ApprovalPolicy.request_type == request_type,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


@dataclass(frozen=True, slots=True)
class PolicyView:
    """One `GET /policies` row (ADR-0006 §10, §11): the written row's values where one exists,
    `domain.py`'s own defaults otherwise — never a fabricated `ApprovalPolicy` row. Distinct from
    the model itself so a read can never be mistaken for something a `PUT` actually wrote."""

    request_type: str
    sla_minutes: int
    enabled: bool


async def list_policies(
    session: AsyncSession, organization_id: uuid.UUID, request_types: Collection[str]
) -> list[PolicyView]:
    """Every request type in `request_types`, in that order, each resolved through the same
    default-fallback `get_policy` gives a single one (ADR-0006 §10) — one round trip
    (`request_type.in_(...)`) rather than N single-row lookups, the N+1 guard the settings screen
    needs the moment a second request type exists (`app/services.KNOWN_REQUEST_TYPES` is one
    today). A type nobody has ever written a row for still comes back, at the honest defaults,
    rather than being silently absent from the response."""
    types = list(dict.fromkeys(request_types))  # de-duplicate, keep the given order
    if not types:
        return []
    stmt = select(ApprovalPolicy).where(
        ApprovalPolicy.organization_id == organization_id,
        ApprovalPolicy.request_type.in_(types),
    )
    written = {row.request_type: row for row in (await session.execute(stmt)).scalars().all()}
    return [
        PolicyView(
            request_type=t,
            sla_minutes=written[t].sla_minutes if t in written else DEFAULT_SLA_MINUTES,
            enabled=written[t].enabled if t in written else DEFAULT_POLICY_ENABLED,
        )
        for t in types
    ]


def requests_stmt(ctx: ScopeContext, *, mine_only: bool = False) -> Select[Any]:
    """`GET /requests` (ADR-0006 §11): OWN reaches requests the caller raised, ORG reaches every
    request in the organization, TEAM reaches requests raised by the caller's direct reports, and
    LOCATION reaches requests raised by anyone at one of the caller's locations. `ctx.apply` is
    the enforcement point.

    `core.approval.request.read`'s `max_scope` is ORG (see `tests/test_chain.py`'s own note on
    this, for `/chain`): the ladder is one ceiling, so a TEAM or LOCATION grant is legal here and
    must be honoured for real rather than refused, the same fix slice 1 made for `/chain` via
    `assert_can_touch`. Unlike `/chain`'s single subject, a list needs the comparison *in the
    query* (CLAUDE.md: "every list endpoint injects scope into the query") — `ApprovalRequest`
    itself carries no manager or location column, so this joins the requester's row on the
    hierarchy copy to reach `reporting_manager_id`/`location_code`, the same columns `/chain`
    compares against. The join is `LEFT OUTER`, not `INNER`: a requester the copy has never seen
    (`open_regularization_request`'s `requester_not_in_hierarchy` case) must still be visible to
    OWN and ORG, and simply fails TEAM/LOCATION closed the way `/chain` does for the same reason —
    an inner join would silently drop those rows from every scope, not only the two that need to
    compare against them.

    `mine_only` is the "My requests" screen's own filter (`?requester=me`), layered *on top of*
    scope rather than replacing it: a TEAM, LOCATION or ORG reader is entitled to see other
    people's requests through this same endpoint (screens also describe an HR/ORG "all requests"
    view), so scope alone must not be read as "this is my list" — an ORG-scoped caller with no
    filter would otherwise see the entire organization's requests presented as "mine". `ctx.apply`
    still runs first and is still what a bystander cannot bypass; this only narrows further, the
    same way an additional `WHERE` narrows any other filter on this endpoint (`request_type`,
    `status`, were either ever added). A caller with no `subject_ref` asking for `mine_only` is
    the same "nothing to own with" shape `ScopeContext.apply`'s own `OWN` branch already handles —
    matches nothing rather than rendering `col == NULL`, which would quietly return everyone's.

    The route pages this with `platform_core.pagination.paginate` and batches in the current
    approver per row itself (`get_open_steps_for_requests`) rather than joining that in here, so
    the join this function does add stays the one the scope check itself needs."""
    stmt = (
        select(ApprovalRequest)
        .outerjoin(
            HierarchyNode,
            (HierarchyNode.organization_id == ApprovalRequest.organization_id)
            & (HierarchyNode.employee_id == ApprovalRequest.requester_employee_id),
        )
        .where(ApprovalRequest.organization_id == ctx.organization_id)
    )
    stmt = ctx.apply(
        stmt,
        owner_col=ApprovalRequest.requester_employee_id,
        team_col=HierarchyNode.reporting_manager_id,
        location_col=HierarchyNode.location_code,
    )
    if mine_only:
        if ctx.subject_ref is None:
            stmt = stmt.where(sa_false())
        else:
            stmt = stmt.where(ApprovalRequest.requester_employee_id == ctx.subject_ref)
    return stmt.order_by(ApprovalRequest.opened_at.desc())


@dataclass(frozen=True, slots=True)
class InboxRow:
    """One row of the inbox's steps-first UNION (data model §2.1) — a pending request the caller
    is eligible to decide, paired with the fields needed to derive `inbox_reason` (ADR-0006 §6,
    §11's amended response shape)."""

    request_id: uuid.UUID
    request_type: str
    source_ref: uuid.UUID
    requester_employee_id: uuid.UUID
    summary: dict[str, Any]
    request_note: str | None
    opened_at: datetime
    approver_employee_id: uuid.UUID | None
    acting_for_employee_id: uuid.UUID | None
    reason: str
    #: The rung the open step's `reason = 'escalated'` moved up *from* — `None` for every other
    #: reason, and for an escalated step whose predecessor is off the end of `candidate_steps`'
    #: self-join (there never is one; level 1 has no level 0). Feeds `domain.inbox_reason_for`'s
    #: `escalated_to_me` "from" (ADR-0006 §11, screens §2.3) — see that function's own docstring
    #: for why the open step alone cannot answer this.
    predecessor_approver_employee_id: uuid.UUID | None
    #: The deadline off the step this row matched through — the *current* open step's, always,
    #: never a closed predecessor's (see `_INBOX_CANDIDATES_CTE`'s own docstring on why the
    #: `escalated_away` branches project `cur.due_at`, not `hs.due_at`). `None` for a step opened
    #: before slice 4 or one that is unassigned; never computed here.
    due_at: datetime | None


#: The steps-first shape data model §2.1 measured at 27ms against the EXISTS-driven alternative's
#: 313ms: driven from `approval_steps`, joined to the *pending* slice of `approval_requests`,
#: rather than a subquery that walks every request an approver has ever touched. The
#: delegation-active check is its own `UNION` branch so the common branch — the large majority of
#: rows, which carry no delegation at all — never joins `delegations` (data model §2.1's own
#: instruction: "splitting the delegation-active check into its own UNION branch keeps the common
#: branch... free of the join entirely").
#:
#: Four eligibility routes live here, matching ADR-0006 §6 exactly:
#:   - route (a), undelegated: `approver_employee_id = caller`, `delegation_id IS NULL`.
#:   - route (a), delegated: `approver_employee_id = caller` **via the second branch**, which
#:     alone checks the delegation is active now — route (a)'s one activity requirement.
#:   - route (b): `acting_for_employee_id = caller` — never activity-checked; the original
#:     approver stays eligible even through a delegation later revoked (ADR-0006 §5).
#:   - route (c): `decide:ORG` and the open step is unassigned, **or** assigned to an approver
#:     whose node is now `exited` — excluding the case where the caller *is* that exited
#:     approver, so an exited approver cannot use their own ORG grant to decide their own step
#:     (ADR-0006 §9's negative-authz case).
#: Routes (a), (b) **and (c)** all require the caller's own node not be `exited` (ADR-0006 §6,
#: amended; ADR-0006 §9) — `:caller_active` is computed once by the caller of `list_inbox`, not
#: re-derived per row. Route (c) needs this exactly as much as (a)/(b): an exited employee still
#: holding `decide:ORG` (permission revocation lags an `exited` status landing on the copy) must
#: not see, or decide, an unassigned or abandoned step either — including the `request_note` this
#: same query returns alongside it.
#:
#: Only `outcome = 'open'` steps are considered for the *live* routes above. Re-pointing
#: (`app/services.create_delegation`, slice 3) does now produce closed, `delegated`-outcome steps
#: — the predecessor a re-pointed request leaves behind — so it is no longer true that nothing in
#: this slice's write path produces that outcome. It remains true that only an *open* step can
#: make a request eligible *through those two branches*: a closed `delegated` step is history,
#: not a live candidate. Both routes a closed `delegated` predecessor could otherwise have fed are
#: already covered without it: route (a) "delegated to me" is served by the *successor* step
#: re-pointing opened (`approver_employee_id` = the new delegate, still `outcome = 'open'`), and
#: route (b) "delegated away" — the original approver's own continuing eligibility — is served by
#: that same successor's `acting_for_employee_id` naming the delegator, not by anything read off
#: the predecessor it replaced.
#:
#: **`escalated_away` (slice 4) is the one route a live open step genuinely cannot serve**, and it
#: needs two more branches rather than a tweak to the first two: ADR-0006 §6's "the original
#: approver stays eligible ... after escalation (a on the escalated step)" is eligibility through
#: the caller's own **now-closed**, `escalated`-outcome step — a step `check_eligibility`
#: (`app/services.py`) already walks via `get_lineage_steps`, which is exactly the asymmetry this
#: CTE closed: a caller could already *decide* a request that had escalated away from them by
#: knowing its id, but `GET /inbox` never listed it, because every branch above reads only the
#: open step. The two new branches below read `outcome = 'escalated'` steps instead of `open`
#: ones, matching routes (a)-undelegated/(b) and (a)-delegated exactly as the first two branches
#: do for the live step, and both project the *current* open step's `approver_employee_id` as
#: this row's "approver" and the caller's own id as its "acting_for" — not because either is
#: literally true of the historical step, but because that is precisely the (approver, acting_for,
#: reason) triple `domain.inbox_reason_for` needs to derive `escalated_away` with the right "from"
#: (the request's *current* holder, screens §2.3: "now with {from_name}") without a sixth
#: parameter threading a different shape through a function every other branch already calls the
#: same way. `cur.approver_employee_id IS DISTINCT FROM :caller AND cur.acting_for_employee_id IS
#: DISTINCT FROM :caller` excludes the (rare, cycle-shaped) case where the caller is *also* the
#: step's current live holder — the live branches above already cover that with a stronger reason
#: (`assigned`/`escalated_to_me`/`delegated_to_me`), and without the exclusion `matched` could
#: return the same request twice for the same caller under a hierarchy loop, which `UNION`'s
#: dedup cannot catch when the two branches project different tuples.
#:
#: `escalated_to_me` needed no new branch — the live open step's own `reason = 'escalated'` was
#: already reachable through the first (undelegated) branch the moment a step could actually carry
#: that value, which slice 4's sweep is the first thing in this service ever to write
#: (`escalate_step` never sets `delegation_id` on a step whose `reason` is `'escalated'` — the two
#: are mutually exclusive by `ck_approval_steps_delegated_iff_delegation`, so the *delegated*
#: branch below it can never produce that reason either). What slice 4 *does* add for it is the
#: `predecessor_approver_employee_id` self-join in the first branch only (the only branch that can
#: ever produce `reason = 'escalated'`): the open step's own columns name who holds the request
#: now, never who held it a rung ago, and "the step moved up to you because {from_name} didn't
#: answer in time" (screens §2.3) needs that previous rung. One extra join on `level - 1` within
#: the same request answers it in the same round trip rather than a second query per row.
#:
#: `prev.outcome = 'escalated'` on that same join is load-bearing, not decorative: delegation
#: re-pointing (`app/services.create_delegation`) inserts its successor **at the same level** as
#: the step it replaces (§5 — re-pointing is not a rung), so a step that was re-pointed and *then*
#: escalated has two candidate predecessors at `level - 1` in the plain `level = s.level - 1` join
#: — the closed, `delegated`-outcome original and, if it too was re-pointed, another one before
#: it. Both are real rows, so `UNION` cannot dedup them: the two tuples they produce differ only in
#: `predecessor_approver_employee_id`, which `UNION` treats as two distinct rows, and the same
#: request would appear twice in one caller's inbox with two different "from" names. Exactly one
#: step at `level - 1` can ever have escalated a request up to `level` — `escalate_step` closes the
#: step it escalates with `outcome = 'escalated'`, never `'delegated'` — so filtering on that
#: outcome is what picks the one predecessor that is actually the "from" of an `escalated_to_me`
#: reason, not merely *a* step that once occupied the rung below.
#:
#: One CTE literal, concatenated with two different tails (count vs. page) rather than kept as two
#: near-identical copies — so every branch is written once and both statements pick it up, instead
#: of being a one-copy-only bug waiting to happen. Every value that varies is still a bind
#: parameter (`:org`, `:caller`, ...), never interpolated into the text — this is string
#: concatenation of two complete, literal fragments, not dynamic SQL construction.
_INBOX_CANDIDATES_CTE = """
    WITH candidate_steps AS (
        -- Every branch below aliases every projected column, including the ones a bare column
        -- reference would already have named correctly on its own: `mappings()` (this CTE's only
        -- Python-side reader, `list_inbox`) keys off the **first** branch's names regardless of
        -- what the other three call theirs, which is exactly what makes a silent reorder possible
        -- in branches 3/4 below — two adjacent `uuid`-typed columns swapping position type-checks
        -- cleanly and changes nothing PostgreSQL would ever refuse, only which employee id a
        -- caller sees themselves as (`approver` vs. `acting_for`). Explicit aliases on every
        -- column, in every branch, are what turn that into something a reviewer (or a diff) can
        -- actually catch: an alias that no longer matches its position reads as wrong on sight,
        -- even though PostgreSQL itself only ever looks at the first branch's names.
        SELECT s.request_id AS request_id,
               s.approver_employee_id AS approver_employee_id,
               s.acting_for_employee_id AS acting_for_employee_id,
               s.reason AS reason,
               prev.approver_employee_id AS predecessor_approver_employee_id,
               s.due_at AS due_at
          FROM approval.approval_steps s
          LEFT JOIN approval.approval_steps prev
            ON prev.organization_id = s.organization_id
           AND prev.request_id = s.request_id
           AND prev.level = s.level - 1
           AND prev.outcome = 'escalated'
         WHERE s.organization_id = :org AND s.outcome = 'open'
           AND (
                (s.delegation_id IS NULL AND :caller_active AND s.approver_employee_id = :caller)
             OR (:caller_active AND s.acting_for_employee_id = :caller)
             OR (:caller_active AND :is_org AND s.approver_employee_id IS NULL)
             OR (
                    :caller_active
                AND :is_org
                AND s.approver_employee_id IS DISTINCT FROM :caller
                AND EXISTS (
                        SELECT 1 FROM approval.hierarchy_nodes h
                         WHERE h.organization_id = s.organization_id
                           AND h.employee_id = s.approver_employee_id
                           AND h.status = 'exited'
                    )
                )
           )

        UNION

        SELECT s.request_id AS request_id,
               s.approver_employee_id AS approver_employee_id,
               s.acting_for_employee_id AS acting_for_employee_id,
               s.reason AS reason,
               NULL::uuid AS predecessor_approver_employee_id,
               s.due_at AS due_at
          FROM approval.approval_steps s
          JOIN approval.delegations d
            ON d.organization_id = s.organization_id AND d.id = s.delegation_id
         WHERE s.organization_id = :org AND s.outcome = 'open'
           AND s.delegation_id IS NOT NULL AND :caller_active AND s.approver_employee_id = :caller
           AND d.revoked_at IS NULL AND d.starts_at <= :now AND :now < d.ends_at

        UNION

        -- escalated_away, routes (a)-undelegated and (b): the caller was the approver (with no
        -- delegation on that historical step) or the represented original of a step that has
        -- since escalated away, on a request still pending. "approver" here is the *current*
        -- open step's holder (the "now with" name), "acting_for" is the caller's own id, and
        -- "reason" is the literal 'escalated' sentinel `domain.inbox_reason_for` reads for it —
        -- see this CTE's own docstring for why those three values, not the historical step's.
        -- `cur.due_at`, not `hs.due_at`: the closed, escalated-away `hs` step's own deadline
        -- already lapsed and stopped being anyone's due date the moment it escalated — the row's
        -- `due_at` is the *current* open step's, matching every other field this branch reports
        -- for it (`approver_employee_id`, `reason`).
        SELECT hs.request_id AS request_id,
               cur.approver_employee_id AS approver_employee_id,
               CAST(:caller AS uuid) AS acting_for_employee_id,
               'escalated' AS reason,
               NULL::uuid AS predecessor_approver_employee_id,
               cur.due_at AS due_at
          FROM approval.approval_steps hs
          JOIN approval.approval_steps cur
            ON cur.organization_id = hs.organization_id
           AND cur.request_id = hs.request_id
           AND cur.outcome = 'open'
         WHERE hs.organization_id = :org AND hs.outcome = 'escalated'
           AND :caller_active
           AND cur.approver_employee_id IS DISTINCT FROM :caller
           AND cur.acting_for_employee_id IS DISTINCT FROM :caller
           AND (
                (hs.delegation_id IS NULL AND hs.approver_employee_id = :caller)
             OR hs.acting_for_employee_id = :caller
           )

        UNION

        -- escalated_away, route (a)-delegated: the caller was reached through a delegation still
        -- active now on the historical escalated step. Same projection as the branch above,
        -- `cur.due_at` for the same reason.
        SELECT hs.request_id AS request_id,
               cur.approver_employee_id AS approver_employee_id,
               CAST(:caller AS uuid) AS acting_for_employee_id,
               'escalated' AS reason,
               NULL::uuid AS predecessor_approver_employee_id,
               cur.due_at AS due_at
          FROM approval.approval_steps hs
          JOIN approval.delegations d
            ON d.organization_id = hs.organization_id AND d.id = hs.delegation_id
          JOIN approval.approval_steps cur
            ON cur.organization_id = hs.organization_id
           AND cur.request_id = hs.request_id
           AND cur.outcome = 'open'
         WHERE hs.organization_id = :org AND hs.outcome = 'escalated'
           AND hs.delegation_id IS NOT NULL AND hs.approver_employee_id = :caller
           AND :caller_active
           AND cur.approver_employee_id IS DISTINCT FROM :caller
           AND cur.acting_for_employee_id IS DISTINCT FROM :caller
           AND d.revoked_at IS NULL AND d.starts_at <= :now AND :now < d.ends_at
    ),
    matched AS (
        SELECT r.id, r.request_type, r.source_ref, r.requester_employee_id, r.summary,
               r.request_note, r.opened_at,
               cs.approver_employee_id, cs.acting_for_employee_id, cs.reason,
               cs.predecessor_approver_employee_id, cs.due_at
          FROM candidate_steps cs
          JOIN approval.approval_requests r
            ON r.organization_id = :org AND r.id = cs.request_id AND r.status = 'pending'
         WHERE r.requester_employee_id <> :caller
    )
    """

# S608 pattern-matches on string concatenation feeding a query-shaped string, which is exactly
# the tell for building SQL out of untrusted parts — but both operands here are module-level
# literals with no interpolated value anywhere in either (every value that varies is still a bind
# parameter, `:org`/`:caller`/...), which is the property the rule exists to catch the absence of.
_INBOX_COUNT_SQL = text(_INBOX_CANDIDATES_CTE + "SELECT count(*) FROM matched")  # noqa: S608

_INBOX_PAGE_SQL = text(
    _INBOX_CANDIDATES_CTE  # noqa: S608
    + "SELECT * FROM matched ORDER BY opened_at ASC LIMIT :limit OFFSET :offset"
)


async def list_inbox(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    caller_id: uuid.UUID,
    caller_active: bool,
    is_org_decider: bool,
    now: datetime,
    limit: int,
    offset: int,
) -> tuple[list[InboxRow], int]:
    """`GET /inbox` (ADR-0006 §11): pending requests the caller may decide, oldest first (screens
    §4.A's default sort — "the oldest is the one about to breach"). See `_INBOX_PAGE_SQL`
    for the eligibility routes this implements and why the query is shaped this way.
    """
    params: dict[str, Any] = {
        "org": str(organization_id),
        "caller": str(caller_id),
        "caller_active": caller_active,
        "is_org": is_org_decider,
        "now": now,
    }
    total = (await session.execute(_INBOX_COUNT_SQL, params)).scalar_one()
    rows = (
        (await session.execute(_INBOX_PAGE_SQL, {**params, "limit": limit, "offset": offset}))
        .mappings()
        .all()
    )
    items = [
        InboxRow(
            request_id=row["id"],
            request_type=row["request_type"],
            source_ref=row["source_ref"],
            requester_employee_id=row["requester_employee_id"],
            summary=row["summary"],
            request_note=row["request_note"],
            opened_at=row["opened_at"],
            approver_employee_id=row["approver_employee_id"],
            acting_for_employee_id=row["acting_for_employee_id"],
            reason=row["reason"],
            predecessor_approver_employee_id=row["predecessor_approver_employee_id"],
            due_at=row["due_at"],
        )
        for row in rows
    ]
    return items, int(total)


# --------------------------------------------------------------------- delegation (slice 3)


def _either_party(employee_id: uuid.UUID) -> ColumnElement[bool]:
    """ "Is a party to this delegation, in either direction" — pattern 11's own `OR` across the
    two employee columns, written once so the scope predicate and the `employee_id` filter can
    never drift into two different readings of the same phrase."""
    return (Delegation.delegator_employee_id == employee_id) | (
        Delegation.delegate_employee_id == employee_id
    )


def delegations_stmt(
    *,
    organization_id: uuid.UUID,
    caller_id: uuid.UUID | None,
    is_org: bool,
    employee_id: uuid.UUID | None = None,
) -> Select[Any]:
    """`GET /delegations` (ADR-0006 §11): ORG reaches every delegation in the organization;
    anything below ORG reads as OWN — `delegation.write` never gains TEAM or LOCATION (the
    catalogue's own "collapse below ORG" rule, `app/catalogue.py`) — and reaches a row the
    caller is *either party* to, delegator or delegate (data model §2 pattern 11).

    That "either party" reading is an `OR` across two columns `ScopeContext.apply()` has no
    shape for (it takes one `owner_col`), so this bypasses it and checks `ctx.scope is
    Scope.ORG` directly — the same pattern `app/api/routes.py`'s `get_inbox` and
    `decide_request` already use for `decide`'s identical collapse.

    A caller with no `subject_ref` below ORG has nothing to match either column against —
    matches nothing rather than rendering `col == NULL`, the same "nothing to own with" guard
    `ScopeContext.apply()`'s own OWN branch uses.

    `employee_id` is the ORG screen's "filtered to a person" state (screens §4.E), and it is the
    *same* "either party" predicate applied to a **different** person: rows where the named
    employee is the delegator or the delegate. It is layered **on top of** the scope predicate
    with `AND`, never in place of it — exactly the way `requests_stmt`'s `mine_only` layers on
    top of `ctx.apply()`. Below ORG that makes the result the intersection of "rows I am a party
    to" and "rows they are a party to", which is a subset of what the caller's unfiltered page
    already returns; there is nothing for the route to refuse (see `list_delegations`' own
    docstring for the full reasoning, and why this differs from `POST /delegations`'
    `delegator_employee_id`, which *is* refused at the route).

    Because the count `platform_core.pagination.paginate` takes is derived from this same
    statement, `total` is the filtered total — which is the entire point of the parameter. The
    UI previously filtered an already-loaded page client-side while still rendering the server's
    unfiltered `total`, so the paging footer disagreed with the rows on screen.

    No new index: measured at 94,560 delegations across 281 organizations (one at 24,000) and the
    filtered arm is served by the pattern-11a pair `ix_delegations_org_delegator_starts` /
    `ix_delegations_org_delegate_starts` as a planner `BitmapOr` — the same access path the OWN
    arm already uses, because it is literally the same predicate shape. See
    `tests/test_delegations_plan.py` for the pinned plans.
    """
    stmt = select(Delegation).where(
        Delegation.organization_id == organization_id, Delegation.deleted_at.is_(None)
    )
    if not is_org:
        scope_predicate: ColumnElement[bool] = (
            sa_false() if caller_id is None else _either_party(caller_id)
        )
        stmt = stmt.where(scope_predicate)
    if employee_id is not None:
        stmt = stmt.where(_either_party(employee_id))
    # Pattern 11 (data model §2): "by starts_at DESC" — most recent first, matching the create
    # response's own row landing at the top of the "Active"/"Scheduled" section it lands in
    # (screens §4.E's "the new row appears first").
    return stmt.order_by(Delegation.starts_at.desc())


async def get_overlapping_delegation(
    session: AsyncSession,
    organization_id: uuid.UUID,
    delegator_employee_id: uuid.UUID,
    starts_at: datetime,
    ends_at: datetime,
) -> Delegation | None:
    """Any non-revoked delegation for this delegator whose half-open window overlaps
    `[starts_at, ends_at)` — the same test `ex_delegations_no_overlap` (data model §1.5)
    enforces atomically at insert time, read here opportunistically first so a conflict can be
    reported with the *actual* conflicting range (screens §4.E: "with the conflicting range
    named from problem.detail") rather than a bare 409.

    This is **not** the concurrency control. No lock is taken, and the GiST exclusion
    constraint remains the only thing that is: `app/services.create_delegation` still wraps its
    insert in `except IntegrityError`, which is what a race this read misses still falls back
    to (data model §1.5: "no advisory lock or SELECT ... FOR UPDATE dance is needed in
    services.py — the constraint is atomic under concurrency, which a read-then-insert check is
    not"). `s1 < e2 AND s2 < e1` is the half-open-range overlap test the exclusion constraint's
    own `tstzrange(...,  '[)') WITH &&` compiles to.
    """
    stmt = select(Delegation).where(
        Delegation.organization_id == organization_id,
        Delegation.delegator_employee_id == delegator_employee_id,
        Delegation.revoked_at.is_(None),
        Delegation.starts_at < ends_at,
        Delegation.ends_at > starts_at,
    )
    return (await session.execute(stmt)).scalars().first()


async def get_repointable_open_steps(
    session: AsyncSession,
    organization_id: uuid.UUID,
    delegator_employee_id: uuid.UUID,
    delegate_employee_id: uuid.UUID,
) -> list[ApprovalStep]:
    """Every step ADR-0006 §5's re-pointing rule closes and replaces when a new delegation is
    created: the open step of a pending request, directly assigned to the delegator
    (`acting_for_employee_id IS NULL` — a step already reached through someone *else's*
    delegation is left alone, matching §5's "not transitive"), excluding a request whose
    requester is the delegate — the same "delegate is the requester" rule step-open already
    enforces (§5), so re-pointing can never hand a request to the person who raised it.

    `outcome = 'open'` already implies the owning request is pending (the invariant
    `uq_approval_steps_open_per_request` enforces, data model §1.4: "a decided request has
    none"), but the requester comparison still needs the request row, hence the join.

    **No dedicated index** (data model §2 pattern 9: "delegation creation is rare... an index to
    serve it would be paid for on every step insert"), the same call the data model already
    made for this exact query — this reuses `ix_approval_steps_org_approver`'s leading columns
    for the scan.

    `deleted_at IS NULL` on both sides, matching `get_request`/`get_request_for_update`'s own
    reading of `ApprovalRequest` (via `get_scoped`, and its own explicit filter respectively) —
    a soft-deleted request or step is invisible everywhere else this service reads one, and
    re-pointing must not be the one write path that quietly acts on a row every read path
    already treats as gone.
    """
    stmt = (
        select(ApprovalStep)
        .join(
            ApprovalRequest,
            (ApprovalRequest.organization_id == ApprovalStep.organization_id)
            & (ApprovalRequest.id == ApprovalStep.request_id),
        )
        .where(
            ApprovalStep.organization_id == organization_id,
            ApprovalStep.outcome == "open",
            ApprovalStep.approver_employee_id == delegator_employee_id,
            ApprovalStep.acting_for_employee_id.is_(None),
            ApprovalStep.deleted_at.is_(None),
            ApprovalRequest.requester_employee_id != delegate_employee_id,
            ApprovalRequest.deleted_at.is_(None),
        )
    )
    return list((await session.execute(stmt)).scalars().all())


@dataclass(frozen=True, slots=True)
class DelegateCandidate:
    """One `GET /delegate-candidates` row — a `hierarchy_nodes` projection, not the ORM row
    itself, so the endpoint cannot accidentally widen what it returns by adding a column to the
    query without a matching field here."""

    employee_id: uuid.UUID
    display_name: str
    employee_no: str


def _like_prefix(term: str) -> str:
    """Escape a caller-supplied search term for use as a `LIKE ... ESCAPE '\\'` prefix.

    Without this, someone searching for a literal `%` or `_` in a name — "50% Corp", "j_doe" —
    would have that character read as a wildcard instead of the literal they typed, silently
    widening rather than narrowing their search. Backslash is escaped first, or escaping a
    literal `%` as `\\%` would itself introduce a second, unintended escape sequence.
    """
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


#: `GET /delegate-candidates` (ADR-0006 §11, data model §2.2a pattern 16). Written as the plain
#: `OR` form the data model specifies rather than a hand-rolled `UNION` of the two branches: the
#: planner turns the `OR` into a `BitmapOr` over `ix_hierarchy_nodes_org_display_name` and
#: `ix_hierarchy_nodes_org_employee_no` (853 buffers, measured) — which *beats* the `UNION`
#: form's 2,189 buffers, "the opposite of the inbox's lesson in §2.1, and the reason each of
#: these is measured rather than reasoned about." `lower(...)` on both sides matches the two
#: partial indexes' own functional expression exactly, or the planner cannot use them.
#: `employee_id IS DISTINCT FROM :exclude` reads as "true for every row" when `:exclude` binds
#: SQL `NULL` (a caller with no `subject_ref`), which is the same "nothing to exclude" shape a
#: plain `!=` cannot express — `!= NULL` is `NULL`, not `TRUE`, and would silently exclude
#: nothing *or* everything depending on how the caller's driver renders it, rather than
#: deterministically excluding nothing.
_DELEGATE_CANDIDATES_SQL = text(
    """
    SELECT employee_id, display_name, employee_no
      FROM approval.hierarchy_nodes
     WHERE organization_id = :org
       AND status <> 'exited'
       AND employee_id IS DISTINCT FROM :exclude
       AND (
                lower(display_name) LIKE :prefix ESCAPE '\\'
             OR lower(employee_no) LIKE :prefix ESCAPE '\\'
           )
     ORDER BY display_name
     LIMIT :limit
    """
)


async def search_delegate_candidates(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    exclude_employee_id: uuid.UUID | None,
    q: str,
    limit: int,
) -> list[DelegateCandidate]:
    """The delegation picker's source (ADR-0006 §11, screens §4.E): non-exited
    `hierarchy_nodes` whose name or staff number starts with `q`, excluding the caller,
    capped at `limit`. See `_DELEGATE_CANDIDATES_SQL` for the query shape and why it is
    written the way it is.

    The route already normalises `q` before its own length check (`StringConstraints(
    strip_whitespace=True, min_length=2)`), so a whitespace-only or whitespace-padded
    one-character query never reaches here over HTTP. This function refuses an empty
    post-strip term anyway, as its own boundary rather than one only the route enforces: a
    bare `%` prefix (what `_like_prefix("")` would build) matches every non-exited employee in
    the tenant, which is exactly the staff-directory enumeration `q`'s length rule exists to
    close — a second caller of this selector must not be able to reopen it by skipping the
    route's own check.
    """
    term = q.strip().lower()
    if not term:
        return []
    rows = (
        (
            await session.execute(
                _DELEGATE_CANDIDATES_SQL,
                {
                    "org": str(organization_id),
                    "exclude": str(exclude_employee_id) if exclude_employee_id else None,
                    "prefix": _like_prefix(term),
                    "limit": limit,
                },
            )
        )
        .mappings()
        .all()
    )
    return [
        DelegateCandidate(
            employee_id=row["employee_id"],
            display_name=row["display_name"],
            employee_no=row["employee_no"],
        )
        for row in rows
    ]


# --------------------------------------------------------------------- the SLA sweep (slice 4)

#: ADR-0006 §8, data model §2 patterns 3/3b: every open, assigned, overdue step, capped so one
#: tenant cannot occupy a whole tick.
#:
#: Runs on the sweep's BYPASSRLS session (`get_outbox_sessionmaker`, `app/cron.py`) — exactly the
#: shape `subscription/app/cron.py`'s `due_dunning_cycles` and the outbox relay itself document
#: (ADR-0002's split, applied a third time): it exists to discover *which* organizations have
#: overdue steps, so there is no tenant to bind while asking, and as the NOBYPASSRLS app role it
#: would return zero rows, log nothing, and every overdue step in the platform would sit
#: unescalated forever. Nothing here decides or writes anything; every action happens back on the
#: app role inside `session_scope(organization_id=...)`, under the tenant policy.
#:
#: **The cap is applied per organization, before any platform-wide ordering or limit is taken** —
#: this is the property a slice-then-cap shape (take the 500 oldest steps platform-wide, *then*
#: cap each tenant to 20 of whatever it already got) does not actually deliver: a single tenant
#: with 500+ overdue steps fills the initial slice entirely on its own, so every other tenant's
#: `row_number()` never gets a chance to run at all — the sweep starves every other organization
#: silently, still logging a healthy `considered=500`. Two stages instead:
#:   1. `due_organizations` — `SELECT DISTINCT organization_id` over every step matching the
#:      predicate, so *which* tenants have overdue work is known before any tenant's rows are
#:      fetched, ordering or limit applied.
#:   2. `CROSS JOIN LATERAL`, once per organization found above: the `per_org_cap` (default 20)
#:      oldest overdue steps *for that organization alone*, `due_at`-ordered — evaluated
#:      independently per tenant, so a tenant with 500 overdue steps and a tenant with one each
#:      contribute their own oldest rows, not a shared, globally-ordered pool one of them can fill.
#:      The outer `SELECT` then takes the tick's own action budget of `limit` (default 100) off the
#:      combined, `due_at`-ordered result — so the oldest overdue steps platform-wide are still
#:      acted on first, subject to no tenant supplying more than its own per-organization share of
#:      *that* ordering, not of a pool a single large tenant could already have exhausted.
#:
#: Served by `ix_approval_steps_sla_due_by_org` — `(organization_id, due_at)` with the same partial
#: predicate — which is what makes both stages index-only: `ix_approval_steps_sla_due`'s `due_at`
#: alone (no leading `organization_id`) cannot serve the per-organization `LATERAL` without a
#: re-scan per tenant, and cannot serve `DISTINCT organization_id` as an index-only unique scan
#: either. See the migration that adds it (`app/selectors.py`'s docstring is not the place plans
#: are measured — the migration's own docstring carries the before/after `EXPLAIN`).
_DUE_ESCALATION_STEPS_SQL = text(
    """
    WITH due_organizations AS (
        SELECT DISTINCT organization_id
          FROM approval.approval_steps
         WHERE outcome = 'open' AND approver_employee_id IS NOT NULL AND due_at <= :now
    )
    SELECT top.organization_id AS organization_id, top.request_id AS request_id
      FROM due_organizations o
      CROSS JOIN LATERAL (
              SELECT s.organization_id, s.request_id, s.due_at
                FROM approval.approval_steps s
               WHERE s.organization_id = o.organization_id
                 AND s.outcome = 'open'
                 AND s.approver_employee_id IS NOT NULL
                 AND s.due_at <= :now
               ORDER BY s.due_at
               LIMIT :per_org_cap
           ) AS top
     ORDER BY top.due_at
     LIMIT :limit
    """
)


async def due_escalation_steps(
    session: AsyncSession,
    *,
    now: datetime,
    per_org_cap: int = 20,
    limit: int = 100,
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """`(organization_id, request_id)` for every step the sweep should act on this tick. See
    `_DUE_ESCALATION_STEPS_SQL` for the query shape and the fairness reasoning.

    `now` is read once by the caller, in the discovery transaction, and passed in — the sweep's
    "one clock, and it is the database's" (ADR-0006 §8): discovery evaluates `due_at <= now()` in
    the database, and each action transaction (`app/services.escalate_step`) reads its own,
    deliberately later `now()` when it re-checks the step under the lock.

    No `step_id` in the result: `app/cron.py`'s `_escalate` calls `app/services.escalate_step`
    with only `(organization_id, request_id)` — it re-finds the request's own open step itself,
    under the lock, rather than trusting discovery's now-possibly-stale id (the same "re-check
    under the lock" discipline `escalate_step`'s own docstring describes for `due_at`) — so a
    discovered-but-unused id would be dead weight on every row this query returns.
    """
    rows = (
        (
            await session.execute(
                _DUE_ESCALATION_STEPS_SQL,
                {"now": now, "per_org_cap": per_org_cap, "limit": limit},
            )
        )
        .mappings()
        .all()
    )
    return [(row["organization_id"], row["request_id"]) for row in rows]
