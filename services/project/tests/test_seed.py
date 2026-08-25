"""The demo seed produces the expected rows, and is idempotent.

Drives `tools/demo/seed_pulseboard.py`'s project stage directly against a tenant-bound session
(the seed module is importable and side-effect-free at load), so this proves the same dataset
the live screens render lands correctly and cross-links: an engagement's owner ref, a card's
assignee ref, the deterministic person UUID the timesheet and standup services share.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app import selectors
from platform_core.auth import Scope
from tests.helpers import ctx_for

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools" / "demo"))
import seed_pulseboard

pytestmark = pytest.mark.integration


class TestDemoSeed:
    async def test_seed_inserts_the_full_fixture_dataset(self, session, org_id) -> None:
        ctx = ctx_for(org_id, "pulseboard.engagements.project.write", Scope.ORG)
        async with session.begin():
            counts = await seed_pulseboard.seed_project(session, ctx)
        assert counts == {"people": 12, "projects": 14, "tasks": 22, "skipped": 0}

    async def test_seed_is_idempotent(self, session, org_id) -> None:
        ctx = ctx_for(org_id, "pulseboard.engagements.project.write", Scope.ORG)
        async with session.begin():
            await seed_pulseboard.seed_project(session, ctx)
        async with session.begin():
            second = await seed_pulseboard.seed_project(session, ctx)
        assert second["people"] == 0
        assert second["projects"] == 0
        assert second["tasks"] == 0
        assert second["skipped"] == 12 + 14 + 22

    async def test_seeded_project_mirrors_the_fixture(self, session, org_id) -> None:
        ctx = ctx_for(org_id, "pulseboard.engagements.project.write", Scope.ORG)
        async with session.begin():
            await seed_pulseboard.seed_project(session, ctx)
        project = await selectors.get_project(session, org_id, "PB-11")
        assert project is not None
        assert project.name == "Cobalt Insights Dashboard"
        assert project.status == "at_risk"
        # The fixtures' over-budget engagement: spend passed budget, in integer US cents.
        assert project.budget_minor == 13_000_000
        assert project.spent_minor == 14_100_000
        assert project.currency == "USD"
        people = await selectors.people_by_id(session, org_id)
        assert selectors.ref_of(people, project.owner_id) == "p11"

    async def test_seeded_person_uuid_is_deterministic(self, session, org_id) -> None:
        """The UUID the timesheet and standup stages derive matches the roster row."""
        ctx = ctx_for(org_id, "pulseboard.engagements.project.write", Scope.ORG)
        async with session.begin():
            await seed_pulseboard.seed_project(session, ctx)
        person = await selectors.get_person_by_ref(session, org_id, "p07")
        assert person is not None
        assert person.id == seed_pulseboard.person_uuid(org_id, "p07")
        assert person.name == "Sofia Rossi"
        assert person.capacity_hours == 34

    async def test_seeded_board_covers_every_stage(self, session, org_id) -> None:
        ctx = ctx_for(org_id, "pulseboard.board.task.read", Scope.ORG)
        write = ctx_for(org_id, "pulseboard.engagements.project.write", Scope.ORG)
        async with session.begin():
            await seed_pulseboard.seed_project(session, write)
        tasks = await selectors.all_tasks(session, ctx)
        by_stage = {
            stage: [t for t in tasks if t.stage == stage]
            for stage in ("backlog", "in_progress", "review", "done")
        }
        assert len(by_stage["backlog"]) == 6
        assert len(by_stage["in_progress"]) == 6
        assert len(by_stage["review"]) == 5
        assert len(by_stage["done"]) == 5
        people = await selectors.people_by_id(session, org_id)
        spike = next(t for t in tasks if t.key == "PB-258")
        assert selectors.ref_of(people, spike.assignee_id) == "p04"
        assert spike.tags == ["research", "mobile"]
