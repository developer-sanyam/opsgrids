"""The delegate-candidate picker's query plan (data model §2.2a pattern 16): the `OR` across
`display_name` and `employee_no` must be served by `ix_hierarchy_nodes_org_display_name` and/or
`ix_hierarchy_nodes_org_employee_no` — the two indexes `7108bbc9ecc6` adds — never by a
sequential scan of `hierarchy_nodes`. Same technique as `tests/test_inbox_plan.py`:
`enable_seqscan = off` makes the planner's *scan method* choice deterministic on a small seeded
table, where a handful of rows would otherwise make a sequential scan the (correctly) cheaper
plan regardless of which indexes exist.

Verified empirically (not merely asserted) that a two-row fixture does **not** reproduce the
`BitmapOr` of both indexes the data model measures at 50,000 rows: with both partial indexes
sharing the same leading `organization_id` column and next to nothing to filter, the planner
picks *one* of them and applies the `OR` as a row filter — a legitimate, cheaper plan at this
size, and the exact caveat `test_inbox_plan.py`'s own docstring already names ("several
org-leading indexes cost the same few milli-cost-units and the planner's pick among them is not
meaningfully predictable"). So this pins what the ratio actually depends on — an index-backed
plan, from the pair these queries need, with no sequential scan — not which specific one a small
fixture happens to prefer.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.selectors import _DELEGATE_CANDIDATES_SQL, _like_prefix
from tests.helpers import seed_person

pytestmark = pytest.mark.integration


async def _explain(session: AsyncSession, params: dict) -> str:
    await session.execute(text("SET LOCAL enable_seqscan = off"))
    rows = (
        (await session.execute(text("EXPLAIN " + _DELEGATE_CANDIDATES_SQL.text), params))
        .scalars()
        .all()
    )
    return "\n".join(rows)


class TestDelegateCandidatesQueryPlan:
    async def test_the_name_or_employee_no_search_is_index_backed_with_no_seq_scan(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        caller = uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, caller)
            await seed_person(session, org_id, uuid.uuid4(), display_name="Priya Nair")

        async with session.begin():
            plan = await _explain(
                session,
                {
                    "org": str(org_id),
                    "exclude": str(caller),
                    "prefix": _like_prefix("pri"),
                    "limit": 20,
                },
            )

        assert "Seq Scan" not in plan
        # At least one of the two partial functional indexes is what actually serves the scan —
        # which one is not deterministic at this row count (see the module docstring), so this
        # does not assert a `BitmapOr` of both.
        assert (
            "ix_hierarchy_nodes_org_display_name" in plan
            or "ix_hierarchy_nodes_org_employee_no" in plan
        )

    async def test_a_caller_with_no_subject_ref_still_gets_an_indexed_plan(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """`exclude IS NULL` (the "no subject_ref" shape) must not force a plan change — the
        `IS DISTINCT FROM` comparison is still over a bind parameter, never a reason to fall back
        to scanning the table."""
        async with session.begin():
            await seed_person(session, org_id, uuid.uuid4(), display_name="Priya Nair")

        async with session.begin():
            plan = await _explain(
                session,
                {
                    "org": str(org_id),
                    "exclude": None,
                    "prefix": _like_prefix("pri"),
                    "limit": 20,
                },
            )

        assert "Seq Scan" not in plan
        # Same assertion the first test makes: a `Seq Scan` absence alone would also pass on a
        # primary-key plan (there is no PK on this query's `WHERE`, but nothing above pins that
        # the *index* actually serving it is one of the two this module exists to verify), so
        # this names the pair explicitly rather than trusting "not a sequential scan" to imply
        # "one of ours".
        assert (
            "ix_hierarchy_nodes_org_display_name" in plan
            or "ix_hierarchy_nodes_org_employee_no" in plan
        )
