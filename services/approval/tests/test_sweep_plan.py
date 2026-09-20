"""The SLA sweep's discovery query and the stuck-open liveness probes (data model §2.2, §3.3;
ADR-0006 §8, §15): both cross-tenant queries must be served by their own partial index — no
sequential scan of `approval_steps` — and both must actually be **runnable** by the outbox role,
column grant and all. `enable_seqscan = off` is the standard way to make a cost-based planner's
*scan method* deterministic on a small seeded table (the same technique `test_inbox_plan.py` and
the delegation/team-arm plan tests use); without it Postgres correctly prefers a sequential scan
of a handful of rows over any index, which would say nothing about whether the query is even
indexable.

Run as `opsgrids_outbox` throughout — the role the sweep and the probes actually run on — rather
than the admin role, so a broken grant fails these tests the same way it fails the process.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.selectors import _DUE_ESCALATION_STEPS_SQL
from app.services import db_now
from tests.helpers import seed_pending_request_with_step, seed_person

pytestmark = pytest.mark.integration

#: Probe A (data model §2.2): the primary stuck-request alarm — oldest unassigned open step.
#: Transcribed verbatim from the data model rather than imported, because it is an operator's own
#: query, never called from application code in this slice.
_PROBE_A_SQL = text(
    """
    SELECT s.organization_id, s.request_id, s.id, s.opened_at
      FROM approval.approval_steps s
     WHERE s.outcome = 'open' AND s.due_at IS NULL AND s.approver_employee_id IS NULL
     ORDER BY s.opened_at
     LIMIT 1
    """
)

#: Probe C (data model §2.2): the escalation-disabled backlog — oldest assigned open step with no
#: due date, the population a `enabled = false` policy produces.
_PROBE_C_SQL = text(
    """
    SELECT s.organization_id, s.request_id, s.id, s.opened_at
      FROM approval.approval_steps s
     WHERE s.outcome = 'open' AND s.due_at IS NULL AND s.approver_employee_id IS NOT NULL
     ORDER BY s.opened_at
     LIMIT 1
    """
)


async def _explain(session: AsyncSession, stmt: Any, params: dict) -> str:
    await session.execute(text("SET LOCAL enable_seqscan = off"))
    rows = (await session.execute(text("EXPLAIN " + stmt.text), params)).scalars().all()
    return "\n".join(rows)


def _discovery_sessionmaker(discovery_engine: Any) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(discovery_engine, expire_on_commit=False)


class TestSweepDiscoveryQueryPlan:
    async def test_due_escalation_steps_is_index_backed_with_no_seq_scan(
        self, session: AsyncSession, sweep_engines: tuple[Any, Any], org_id: uuid.UUID
    ) -> None:
        requester, manager = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            now = await db_now(session)
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
            await seed_pending_request_with_step(
                session,
                org_id,
                requester_employee_id=requester,
                approver_employee_id=manager,
                opened_at=now - timedelta(minutes=20),
                due_at=now - timedelta(minutes=5),
            )

        discovery_engine, _work_engine = sweep_engines
        maker = _discovery_sessionmaker(discovery_engine)
        async with maker() as discovery_session, discovery_session.begin():
            plan = await _explain(
                discovery_session,
                _DUE_ESCALATION_STEPS_SQL,
                {"now": now, "per_org_cap": 20, "limit": 100},
            )

        assert "Seq Scan" not in plan
        # `due_organizations` (`SELECT DISTINCT organization_id`) is served by the plain `(due_at)`
        # index; the per-organization `LATERAL` needs the one leading with `organization_id` —
        # both must appear, or one half of the query fell back to a scan the other half's index
        # cannot serve (the slice-4 review finding this migration and query shape fix).
        assert "ix_approval_steps_sla_due_by_org" in plan
        assert "ix_approval_steps_sla_due" in plan


class TestStuckOpenProbesAreRunnableByTheOutboxRole:
    async def test_probe_a_the_primary_unassigned_backlog_alarm_runs_and_uses_the_index(
        self, session: AsyncSession, sweep_engines: tuple[Any, Any], org_id: uuid.UUID
    ) -> None:
        """The seven-column grant (`migrations/env.py`'s `grant_sla_sweep_reader`) is what makes
        this runnable at all — `opened_at` is the column this amendment added, and without it the
        outbox role is refused `42501` on exactly this probe (data model §3.3)."""
        requester = uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, requester, reporting_manager_id=None)
            await seed_pending_request_with_step(
                session,
                org_id,
                requester_employee_id=requester,
                approver_employee_id=None,
                reason="unassigned",
                due_at=None,
            )

        discovery_engine, _work_engine = sweep_engines
        maker = _discovery_sessionmaker(discovery_engine)
        async with maker() as discovery_session, discovery_session.begin():
            plan = await _explain(discovery_session, _PROBE_A_SQL, {})

        assert "Seq Scan" not in plan
        assert "ix_approval_steps_stuck_open" in plan

    async def test_probe_c_the_escalation_disabled_backlog_runs_and_uses_the_same_index(
        self, session: AsyncSession, sweep_engines: tuple[Any, Any], org_id: uuid.UUID
    ) -> None:
        requester, manager = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            now = await db_now(session)
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
            # `enabled = false` shape: assigned, open, no due date at all.
            await seed_pending_request_with_step(
                session,
                org_id,
                requester_employee_id=requester,
                approver_employee_id=manager,
                opened_at=now,
                due_at=None,
            )

        discovery_engine, _work_engine = sweep_engines
        maker = _discovery_sessionmaker(discovery_engine)
        async with maker() as discovery_session, discovery_session.begin():
            plan = await _explain(discovery_session, _PROBE_C_SQL, {})

        assert "Seq Scan" not in plan
        assert "ix_approval_steps_stuck_open" in plan
