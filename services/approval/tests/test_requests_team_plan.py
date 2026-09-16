"""`GET /requests` at TEAM scope's query plan (data model §2, table row 19): `requests_stmt`'s
TEAM arm (`ctx.apply(team_col=HierarchyNode.reporting_manager_id, ...)`) filters `hierarchy_nodes`
by `reporting_manager_id = caller`, then joins each match's `employee_id` into
`ix_approval_requests_org_requester_opened`. `ix_hierarchy_nodes_org_manager` is the index that
serves the `hierarchy_nodes` side — named in the data model and in two other modules' own
comments since slice 2, but never actually created until `640aba68b036` (a gap an architect
review caught while this slice's other index findings were being fixed).

Same technique and the same caveat as `tests/test_inbox_plan.py` and
`tests/test_delegate_candidates_plan.py`: `enable_seqscan = off` makes the planner's scan
*method* deterministic on a small seeded table; the buffer/latency ratio the data model measured
(0.081 ms / 89 buffers against 2.87 ms / 550 unindexed, at 20,000 nodes) is a fleet-scale
property this module does not attempt to reproduce.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import Select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors
from platform_core.auth import Scope
from tests.helpers import ctx_for, open_request, seed_person

pytestmark = pytest.mark.integration


async def _explain(session: AsyncSession, stmt: Select[Any]) -> str:
    await session.execute(text("SET LOCAL enable_seqscan = off"))
    compiled = stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    rows = (await session.execute(text("EXPLAIN " + str(compiled)))).scalars().all()
    return "\n".join(rows)


class TestRequestsTeamScopePlan:
    async def test_the_team_arm_is_index_backed_with_no_seq_scan(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        manager, report = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, report, reporting_manager_id=manager)
        request = await open_request(session, org_id, requester_employee_id=report)
        assert request is not None

        ctx = ctx_for(org_id, manager, "core.approval.request.read", Scope.TEAM)
        stmt = selectors.requests_stmt(ctx)
        async with session.begin():
            plan = await _explain(session, stmt)

        assert "Seq Scan" not in plan
        # The `hierarchy_nodes` side of the join — the index under test, previously named only
        # in prose (see the module docstring), and confirmed to be the one the planner actually
        # picks here: an `Index Only Scan` keyed on `(organization_id, reporting_manager_id)`.
        assert "ix_hierarchy_nodes_org_manager" in plan
        # The `approval_requests` side is always index-backed too, but *which* of its two
        # org-leading indexes drives the join is not deterministic at this fixture's size —
        # verified directly: with one request in the table, the planner drives from
        # `ix_approval_requests_org_opened` (cheaper here, since it also serves `ORDER BY
        # opened_at DESC` directly) rather than probing `ix_approval_requests_org_requester_opened`
        # once per matched report, which is the shape pattern 19's own 89-buffer measurement
        # describes at 20,000 nodes. Both are legitimate, already-shipped (slice 2) access paths
        # for this side of the join; only the `hierarchy_nodes` side is this migration's own
        # claim to verify.
        assert (
            "ix_approval_requests_org_opened" in plan
            or "ix_approval_requests_org_requester_opened" in plan
        )
