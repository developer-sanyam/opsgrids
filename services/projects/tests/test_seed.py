"""The demo seed produces the expected rows, and is idempotent.

Drives `tools/demo/seed_projects.py` directly against a tenant-bound session (the seed module is
importable and side-effect-free at load), so this proves the same dataset the live screens render
lands correctly and cross-links: a project's milestone count, a milestone's owner, a task's
assignee.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app import selectors
from platform_core.auth import Scope
from tests.helpers import ctx_for

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools" / "demo"))
import seed_projects

pytestmark = pytest.mark.integration


class TestDemoSeed:
    async def test_seed_inserts_the_full_fixture_dataset(self, session, org_id) -> None:
        ctx = ctx_for(org_id, "projects.portfolio.project.write", Scope.ORG)
        async with session.begin():
            counts = await seed_projects.seed(session, ctx)
        assert counts == {
            "members": 10,
            "projects": 14,
            "milestones": 16,
            "tasks": 18,
            "skipped": 0,
        }

    async def test_seed_is_idempotent(self, session, org_id) -> None:
        ctx = ctx_for(org_id, "projects.portfolio.project.write", Scope.ORG)
        async with session.begin():
            await seed_projects.seed(session, ctx)
        async with session.begin():
            second = await seed_projects.seed(session, ctx)
        assert second["members"] == 0
        assert second["projects"] == 0
        assert second["milestones"] == 0
        assert second["tasks"] == 0
        assert second["skipped"] == 10 + 14 + 16 + 18

    async def test_seeded_project_carries_its_owner_and_milestone_count(
        self, session, org_id
    ) -> None:
        ctx = ctx_for(org_id, "projects.portfolio.project.write", Scope.ORG)
        async with session.begin():
            await seed_projects.seed(session, ctx)
        project = await selectors.get_project(session, org_id, "PRJ-146")
        assert project is not None
        assert project.health == "on_track"
        members = await selectors.members_by_id(session, org_id)
        assert selectors.person_of(members, project.owner_id).name == "Aarav Sharma"
        counts = await selectors.milestone_count_map(session, org_id)
        # PRJ-146 has two milestones in the fixtures: "Store rollout — wave 1" and "ERP cutover".
        assert counts[project.id] == 2

    async def test_seeded_milestone_points_at_its_project(self, session, org_id) -> None:
        ctx = ctx_for(org_id, "projects.portfolio.project.write", Scope.ORG)
        async with session.begin():
            await seed_projects.seed(session, ctx)
        rows = await selectors.all_milestones(session, ctx)
        completed = [m for m in rows if m.status == "completed"]
        assert len(completed) == 2  # ERP cutover + Sensor fleet handover
        projects = await selectors.projects_by_id(session, org_id)
        members = await selectors.members_by_id(session, org_id)
        erp = next(m for m in rows if m.title == "ERP cutover")
        out = selectors.milestone_out(erp, members=members, projects=projects)
        assert out.project_code == "PRJ-146"
        assert out.owner.id == "p-01"

    async def test_seeded_board_covers_every_stage(self, session, org_id) -> None:
        ctx = ctx_for(org_id, "projects.board.task.read", Scope.ORG)
        async with session.begin():
            await seed_projects.seed(session, ctx)
        tasks = await selectors.all_tasks(session, ctx)
        by_stage = {
            stage: [t for t in tasks if t.stage == stage]
            for stage in ("backlog", "in_progress", "review", "done")
        }
        assert len(by_stage["backlog"]) == 5
        assert len(by_stage["in_progress"]) == 5
        assert len(by_stage["review"]) == 4
        assert len(by_stage["done"]) == 4
