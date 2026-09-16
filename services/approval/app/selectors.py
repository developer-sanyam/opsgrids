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
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import MAX_CHAIN_DEPTH, Rung

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
