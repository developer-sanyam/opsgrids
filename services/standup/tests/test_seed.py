"""The demo seed produces the expected rows, and is idempotent.

Drives `tools/demo/seed_pulseboard.py`'s standup stage directly against a tenant-bound session
(the seed module is importable and side-effect-free at load), so this proves the same wall the
live Standups screen renders lands correctly: nine check-ins for today, three of them flagged
with blockers, carrying the deterministic person UUID the project service's roster shares.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

from app import selectors
from platform_core.auth import Scope
from tests.helpers import ctx_for

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools" / "demo"))
import seed_pulseboard

pytestmark = pytest.mark.integration

TODAY = date.today()


class TestDemoSeed:
    async def test_seed_inserts_todays_wall(self, session, org_id) -> None:
        ctx = ctx_for(org_id, "pulseboard.standup.entry.write", Scope.ORG)
        async with session.begin():
            counts = await seed_pulseboard.seed_standup(session, ctx, today=TODAY)
        assert counts == {"standups": 9, "skipped": 0}

    async def test_seed_is_idempotent(self, session, org_id) -> None:
        ctx = ctx_for(org_id, "pulseboard.standup.entry.write", Scope.ORG)
        async with session.begin():
            await seed_pulseboard.seed_standup(session, ctx, today=TODAY)
        async with session.begin():
            second = await seed_pulseboard.seed_standup(session, ctx, today=TODAY)
        assert second == {"standups": 0, "skipped": 9}

    async def test_seeded_wall_mirrors_the_fixture(self, session, org_id) -> None:
        ctx = ctx_for(org_id, "pulseboard.standup.entry.write", Scope.ORG)
        async with session.begin():
            await seed_pulseboard.seed_standup(session, ctx, today=TODAY)

        read = ctx_for(org_id, "pulseboard.standup.entry.read", Scope.ORG)
        rows = await selectors.entries_for(session, read, TODAY)
        assert [e.person_ref for e in rows] == [
            "p02",
            "p03",
            "p04",
            "p05",
            "p06",
            "p08",
            "p09",
            "p10",
            "p11",
        ]
        blocked = [e.person_ref for e in rows if e.blockers is not None]
        assert blocked == ["p02", "p06", "p11"]
        meridian = next(e for e in rows if e.person_ref == "p11")
        assert meridian.blockers is not None
        assert "Meridian warehouse" in meridian.blockers
        assert meridian.person_id == seed_pulseboard.person_uuid(org_id, "p11")
