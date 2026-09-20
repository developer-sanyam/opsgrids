"""`GET /delegations`'s query plan (data model §2, table rows 11a/11b): a slice-3 review found
both scopes running a full `Seq Scan` of the whole multi-tenant `delegations` table — neither
`deleted_at` nor `starts_at` led an index, and the unique on `(organization_id, id)` cannot
serve `ORDER BY starts_at DESC`. `640aba68b036` adds the three indexes this module verifies are
actually used: `ix_delegations_org_starts` for ORG, and the `ix_delegations_org_delegator_starts`
/ `ix_delegations_org_delegate_starts` pair (a planner `BitmapOr`) for OWN's "delegator or
delegate" `OR`.

Same technique as `tests/test_inbox_plan.py` and `tests/test_delegate_candidates_plan.py`:
`enable_seqscan = off` makes the planner's *scan method* choice deterministic on a small seeded
table, where a sequential scan of a handful of rows would otherwise correctly be the cheaper
plan regardless of which indexes exist — so what these tests pin is "index-backed, never a
sequential scan of the whole table", not the exact buffer/latency ratio the data model measured
at fleet scale (94,704 delegations / 281 organizations), which is a structural gap only visible
at that size.

`selectors.delegations_stmt` is a SQLAlchemy Core `Select`, not a raw `text()` constant like the
other two plan modules' queries — so `EXPLAIN` here runs against the statement compiled with
`literal_binds=True` (values rendered inline rather than as bind parameters), which avoids
juggling the asyncpg dialect's own parameter style inside a hand-built `EXPLAIN` string.

**`?employee_id=` adds no index, and that was measured rather than assumed.** The filter is the
same "delegator = X OR delegate = X" predicate the OWN arm already issues, only for a different
person, so the pattern-11a pair above already serves it — there is no new column pair, no new
sort key and no new leading column to index. Measured on a seeded table of 94,560 delegations
across 281 organizations with one organization at 24,000 rows (`EXPLAIN (ANALYZE, BUFFERS)`,
after `ANALYZE`, page size 25):

    ORG, no filter (shipped)      Index Scan Backward ix_delegations_org_starts      4 buffers
    ORG, ?employee_id=            BitmapOr over the 11a pair -> Sort               16 buffers
    ORG, ?employee_id= (count)    BitmapOr over the 11a pair                       16 buffers
    ORG, no filter  (count)       Bitmap Heap Scan ix_delegations_org_starts       458 buffers
    below-ORG, own id             BitmapOr over the 11a pair -> Sort               16 buffers
    below-ORG, foreign id         BitmapOr over the 11a pair -> Sort               16 buffers

The filtered count is the one that matters, because it is the count the paging footer was
previously lying about: 16 buffers against the unfiltered count's 458 — the filter makes the
endpoint *cheaper*, it does not add a scan. The adversarial shape was measured too (a subject
with 200 delegations whose `starts_at` are the oldest rows in a 24,000-row organization, so an
ordered backward scan would have to filter through the whole organization before finding the
first match): the planner still chooses the `BitmapOr` and a `top-N heapsort`, 13 buffers.
Nothing here justifies a fourth index on a table every delegation write pays for."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors
from app.models import Delegation
from tests.helpers import seed_person

pytestmark = pytest.mark.integration


async def _explain(session: AsyncSession, stmt: Select[Any]) -> str:
    await session.execute(text("SET LOCAL enable_seqscan = off"))
    compiled = stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    rows = (await session.execute(text("EXPLAIN " + str(compiled)))).scalars().all()
    return "\n".join(rows)


async def _seed_delegation(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    delegator_employee_id: uuid.UUID,
    delegate_employee_id: uuid.UUID,
) -> None:
    """A bare `Delegation` row, bypassing `services.create_delegation` — these tests only need
    rows on the table for the planner to have something to choose an access path over, not the
    service's own business rules or re-pointing side effects."""
    now = datetime.now(UTC)
    session.add(
        Delegation(
            organization_id=org_id,
            delegator_employee_id=delegator_employee_id,
            delegate_employee_id=delegate_employee_id,
            starts_at=now,
            ends_at=now + timedelta(days=1),
            created_by_employee_id=delegator_employee_id,
        )
    )


class TestDelegationsOrgScopePlan:
    async def test_org_scope_is_index_backed_with_no_seq_scan(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        manager, delegate = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, delegate)
            await _seed_delegation(
                session, org_id, delegator_employee_id=manager, delegate_employee_id=delegate
            )

        stmt = selectors.delegations_stmt(organization_id=org_id, caller_id=None, is_org=True)
        async with session.begin():
            plan = await _explain(session, stmt)

        assert "Seq Scan" not in plan
        assert "ix_delegations_org_starts" in plan


class TestDelegationsOwnScopePlan:
    async def test_own_scope_either_party_is_index_backed_with_no_seq_scan(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        manager, delegate = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, delegate)
            await _seed_delegation(
                session, org_id, delegator_employee_id=manager, delegate_employee_id=delegate
            )

        stmt = selectors.delegations_stmt(organization_id=org_id, caller_id=manager, is_org=False)
        async with session.begin():
            plan = await _explain(session, stmt)

        assert "Seq Scan" not in plan
        # Verified (not merely asserted, the same discipline `test_delegate_candidates_plan.py`
        # documents): without `ANALYZE` statistics, a few hundred seeded rows are not enough to
        # make the planner reliably prefer the `ix_delegations_org_delegator_starts` /
        # `ix_delegations_org_delegate_starts` `BitmapOr` pattern 11a names over
        # `ix_delegations_org_starts` — all three lead with `organization_id`, cost within the
        # same few cost-units at this size, and the data model's own 8-buffer-vs-624 ratio for
        # this pattern is a fleet-scale (24,000-row) property, not one a unit-test fixture
        # reproduces. So this pins what a small fixture *can* prove: the plan is served by one
        # of the three indexes this migration adds, not by `uq_delegations_org_id` (the
        # pre-existing index this pattern's own review finding named as unable to serve the
        # sort) and not by a sequential scan.
        assert (
            "ix_delegations_org_starts" in plan
            or "ix_delegations_org_delegator_starts" in plan
            or "ix_delegations_org_delegate_starts" in plan
        )
        assert "uq_delegations_org_id" not in plan


class TestDelegationsEmployeeFilterPlan:
    """`?employee_id=` reuses the pattern-11a pair rather than earning an index of its own — see
    this module's own docstring for the fleet-scale numbers that call was made on. What a small
    fixture can still pin is the structural property: the filtered arm is index-backed, at both
    scopes, and never falls back to a sequential scan of the whole multi-tenant table."""

    async def test_org_scope_with_the_filter_is_index_backed_with_no_seq_scan(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        manager, delegate = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, delegate)
            await _seed_delegation(
                session, org_id, delegator_employee_id=manager, delegate_employee_id=delegate
            )

        stmt = selectors.delegations_stmt(
            organization_id=org_id, caller_id=None, is_org=True, employee_id=manager
        )
        async with session.begin():
            plan = await _explain(session, stmt)

        assert "Seq Scan" not in plan
        assert (
            "ix_delegations_org_starts" in plan
            or "ix_delegations_org_delegator_starts" in plan
            or "ix_delegations_org_delegate_starts" in plan
        )
        assert "uq_delegations_org_id" not in plan

    async def test_own_scope_with_the_filter_is_index_backed_with_no_seq_scan(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The two-`OR` conjunction — the scope predicate `AND` the filter — is the shape a
        below-ORG caller naming a third party produces, and the one most likely to defeat the
        planner's `BitmapOr` if either side were written differently."""
        manager, delegate = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, delegate)
            await _seed_delegation(
                session, org_id, delegator_employee_id=manager, delegate_employee_id=delegate
            )

        stmt = selectors.delegations_stmt(
            organization_id=org_id, caller_id=manager, is_org=False, employee_id=delegate
        )
        async with session.begin():
            plan = await _explain(session, stmt)

        assert "Seq Scan" not in plan
        assert (
            "ix_delegations_org_starts" in plan
            or "ix_delegations_org_delegator_starts" in plan
            or "ix_delegations_org_delegate_starts" in plan
        )
        assert "uq_delegations_org_id" not in plan
