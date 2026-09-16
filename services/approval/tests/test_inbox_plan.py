"""The inbox query's shape (data model §2.1): driven from `approval_steps`, not an `EXISTS` over
`approval_requests` — measured there at 27ms against the rejected shape's 313ms. `EXPLAIN` on a
handful of seeded rows cannot reproduce that ratio (the gap is structural and only visible once
the table is large; on a near-empty table several org-leading indexes cost the same few
milli-cost-units and the planner's pick among them is not meaningfully predictable), so this pins
the property the ratio actually depends on: **no sequential scan of either table**, and the
pending side served by the partial `ix_approval_requests_org_pending` — the index data model
§2.1 calls out as "what makes the pending side cheap". `enable_seqscan` is turned off first,
which is the standard way to make a cost-based planner's *scan method* deterministic on a small
test table; without it, Postgres correctly prefers a sequential scan of a two-row table over any
index, and the test would say nothing about whether the query is even indexable.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.selectors import _INBOX_PAGE_SQL
from tests.helpers import open_request, seed_person

pytestmark = pytest.mark.integration


async def _explain(session: AsyncSession, params: dict) -> str:
    await session.execute(text("SET LOCAL enable_seqscan = off"))
    rows = (await session.execute(text("EXPLAIN " + _INBOX_PAGE_SQL.text), params)).scalars().all()
    return "\n".join(rows)


class TestInboxQueryPlan:
    async def test_the_direct_approver_route_is_index_backed_with_no_seq_scan(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        manager, requester = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        async with session.begin():
            plan = await _explain(
                session,
                {
                    "org": str(org_id),
                    "caller": str(manager),
                    "caller_active": True,
                    "is_org": False,
                    "now": datetime.now(UTC),
                    "limit": 25,
                    "offset": 0,
                },
            )

        assert "Seq Scan" not in plan
        # The pending side of the join is the partial index data model §2.1 names explicitly.
        assert "ix_approval_requests_org_pending" in plan
        # The step side is reached through `uq_approval_steps_open_per_request` — the same
        # partial-unique index that enforces "one open step per request" doubles as this lookup's
        # own access path (data model §2.1's `ApprovalStep.__table_args__` comment: "pattern 8").
        # A generic "Index Scan using" assertion would still pass if a later change silently
        # swapped this for a different, worse index on `approval_steps`.
        assert "uq_approval_steps_open_per_request" in plan

    async def test_the_unassigned_org_route_is_index_backed_with_no_seq_scan(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester = uuid.uuid4()
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None  # no hierarchy row -> unassigned

        async with session.begin():
            plan = await _explain(
                session,
                {
                    "org": str(org_id),
                    "caller": str(uuid.uuid4()),
                    "caller_active": True,
                    "is_org": True,
                    "now": datetime.now(UTC),
                    "limit": 25,
                    "offset": 0,
                },
            )

        assert "Seq Scan" not in plan
        assert "ix_approval_requests_org_pending" in plan
