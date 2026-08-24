"""The demo seed produces the expected rows, and is idempotent.

Drives `tools/demo/seed_itsm.py` directly against a tenant-bound session (the seed module is
importable and side-effect-free at load), so this proves the same dataset the live screens render
lands correctly and cross-links: an incident's worklog thread, an asset's open incidents, a
request's catalog item.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app import selectors
from platform_core.auth import Scope
from tests.helpers import ctx_for

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools" / "demo"))
import seed_itsm

pytestmark = pytest.mark.integration


class TestDemoSeed:
    async def test_seed_inserts_the_full_fixture_dataset(self, session, org_id) -> None:
        ctx = ctx_for(org_id, "itsm.service.incident.write", Scope.ORG)
        async with session.begin():
            counts = await seed_itsm.seed(session, ctx)
        assert counts == {
            "assets": 18,
            "catalog": 10,
            "incidents": 16,
            "requests": 15,
            "skipped": 0,
        }

    async def test_seed_is_idempotent(self, session, org_id) -> None:
        ctx = ctx_for(org_id, "itsm.service.incident.write", Scope.ORG)
        async with session.begin():
            await seed_itsm.seed(session, ctx)
        async with session.begin():
            second = await seed_itsm.seed(session, ctx)
        assert second["assets"] == 0
        assert second["incidents"] == 0
        assert second["requests"] == 0
        assert second["skipped"] == 18 + 10 + 16 + 15

    async def test_seeded_incident_carries_its_worklog_thread(self, session, org_id) -> None:
        ctx = ctx_for(org_id, "itsm.service.incident.write", Scope.ORG)
        async with session.begin():
            await seed_itsm.seed(session, ctx)
        incident = await selectors.get_incident(session, org_id, "INC-4821")
        assert incident is not None
        assert incident.priority == "P1"
        assert incident.sla_state == "breached"
        assert incident.sla_breached is True
        thread = await selectors.worklogs_for(session, org_id, incident.id)
        assert len(thread) == 5
        assert thread[0].author == "Priya Nair"

    async def test_open_incidents_are_cross_linked_to_their_asset(self, session, org_id) -> None:
        ctx = ctx_for(org_id, "itsm.service.incident.write", Scope.ORG)
        async with session.begin():
            await seed_itsm.seed(session, ctx)
        switch = await selectors.get_asset(session, org_id, "AST-1006")
        assert switch is not None
        open_map = await selectors.open_incident_numbers(session, org_id)
        # INC-4821 is open against the switch; INC-4812 (also the switch) is resolved, so excluded.
        assert open_map.get(switch.id) == ["INC-4821"]

    async def test_seeded_request_points_at_its_catalog_item(self, session, org_id) -> None:
        ctx = ctx_for(org_id, "itsm.service.incident.write", Scope.ORG)
        async with session.begin():
            await seed_itsm.seed(session, ctx)
        req = await selectors.get_request(session, org_id, "REQ-2051")
        assert req is not None
        assert req.catalog_item_code == "CAT-01"
        assert req.category == "onboarding"
        names = await selectors.catalog_name_map(session, org_id)
        assert names["CAT-01"] == "New joiner onboarding"
