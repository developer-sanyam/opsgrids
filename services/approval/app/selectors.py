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

from sqlalchemy import Select, select, text
from sqlalchemy import false as sa_false
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import MAX_CHAIN_DEPTH, Rung
from app.models import ApprovalRequest, ApprovalStep, Delegation, HierarchyNode
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
#: Only `outcome = 'open'` steps are considered. In slices 2-3 that is every step that can ever
#: make a request eligible: nothing in this slice's write path ever produces a `delegated` or
#: `escalated` **outcome** (that requires re-pointing on `POST /delegations`, slice 3, or the SLA
#: sweep, slice 4), so a pending request's single open step already carries everything routes
#: (a)/(b) need — including "delegated to me" and "delegated away", both derivable from the open
#: step's own `approver_employee_id`/`acting_for_employee_id` when a delegation applied at open
#: (ADR-0006 §5). The one route this simplification does not yet serve is `escalated_away`
#: (ADR-0006 §6: eligible via the caller's own **now-closed** `escalated`-outcome step) — schemas
#: §2.3 confirms it "cannot occur before slice 4", so it is deferred with the sweep that would be
#: the only thing to ever produce it.
#: One CTE literal, concatenated with two different tails (count vs. page) rather than kept as two
#: near-identical copies — so a future branch (`escalated_away`, slice 4) is added once and both
#: statements pick it up, instead of being a one-copy-only bug waiting to happen. Every value that
#: varies is still a bind parameter (`:org`, `:caller`, ...), never interpolated into the text —
#: this is string concatenation of two complete, literal fragments, not dynamic SQL construction.
_INBOX_CANDIDATES_CTE = """
    WITH candidate_steps AS (
        SELECT s.request_id, s.approver_employee_id, s.acting_for_employee_id, s.reason
          FROM approval.approval_steps s
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

        SELECT s.request_id, s.approver_employee_id, s.acting_for_employee_id, s.reason
          FROM approval.approval_steps s
          JOIN approval.delegations d
            ON d.organization_id = s.organization_id AND d.id = s.delegation_id
         WHERE s.organization_id = :org AND s.outcome = 'open'
           AND s.delegation_id IS NOT NULL AND :caller_active AND s.approver_employee_id = :caller
           AND d.revoked_at IS NULL AND d.starts_at <= :now AND :now < d.ends_at
    ),
    matched AS (
        SELECT r.id, r.request_type, r.source_ref, r.requester_employee_id, r.summary,
               r.request_note, r.opened_at,
               cs.approver_employee_id, cs.acting_for_employee_id, cs.reason
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
        )
        for row in rows
    ]
    return items, int(total)
