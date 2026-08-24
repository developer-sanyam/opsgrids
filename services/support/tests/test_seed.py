"""The demo seed produces the expected rows.

Loads `tools/demo/seed_support.py` by path and runs its `run_seed` against a test tenant, so the
same data and write path the demo uses is exercised here — and proven idempotent.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app import selectors
from app.models import Agent, Ticket, TicketMessage
from platform_core.auth import Scope
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration

_SEED_PATH = Path(__file__).resolve().parents[3] / "tools" / "demo" / "seed_support.py"


def _load_seed():
    spec = importlib.util.spec_from_file_location("seed_support", _SEED_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _count(session, model, org_id: uuid.UUID) -> int:
    return (
        await session.execute(
            select(func.count()).select_from(model).where(model.organization_id == org_id)
        )
    ).scalar_one()


class TestSeed:
    async def test_seed_inserts_the_full_demo_desk(self, session, org_id) -> None:
        seed = _load_seed()
        ctx = ctx_for(org_id, "support.desk.ticket.write", Scope.ORG)

        async with session.begin():
            result = await seed.run_seed(session, ctx)

        assert result == {
            "agents": 8,
            "tickets_created": 20,
            "tickets_skipped": 0,
            "tickets_total": 20,
        }
        assert await _count(session, Agent, org_id) == 8
        assert await _count(session, Ticket, org_id) == 20
        # 20 tickets carry 45 messages in the fixtures.
        assert await _count(session, TicketMessage, org_id) == 45

        policy = await selectors.get_sla_policy(session, org_id)
        assert policy is not None
        assert len(policy.targets) == 4

    async def test_a_seeded_ticket_matches_the_fixture(self, session, org_id) -> None:
        seed = _load_seed()
        ctx = ctx_for(org_id, "support.desk.ticket.write", Scope.ORG)
        async with session.begin():
            await seed.run_seed(session, ctx)

        ticket = await selectors.get_ticket(session, org_id, "TK-4001")
        assert ticket is not None
        assert ticket.priority == "urgent"
        assert ticket.status == "open"
        assert ticket.assignee_ref == "ag-04"
        assert ticket.sla_breached is True
        assert ticket.requester_company == "Northwind Traders"
        messages = await selectors.get_messages(session, org_id, ticket.id)
        assert [m.ref for m in messages] == ["m1", "m2", "m3"]

    async def test_seed_is_idempotent(self, session, org_id) -> None:
        seed = _load_seed()
        ctx = ctx_for(org_id, "support.desk.ticket.write", Scope.ORG)
        async with session.begin():
            await seed.run_seed(session, ctx)
        async with session.begin():
            second = await seed.run_seed(session, ctx)

        assert second["tickets_created"] == 0
        assert second["tickets_skipped"] == 20
        assert await _count(session, Ticket, org_id) == 20
        # No duplicate threads either.
        assert await _count(session, TicketMessage, org_id) == 45
