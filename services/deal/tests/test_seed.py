"""The demo seed produces the expected rows, and is idempotent — the pipeline half.

Drives `tools/demo/seed_crm.py`'s `seed_deal` directly against a tenant-bound session (the
seed module is importable and side-effect-free at load), so this proves the same dataset
the live screens render lands correctly: the fixture counts, the stage spread the board
shows, the cross-schema references onto the book's deterministic ids, and the timeline the
Overview reads.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app import domain, selectors
from platform_core.auth import Scope
from tests.helpers import activities_for_deal, ctx_for

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools" / "demo"))
import seed_crm

pytestmark = pytest.mark.integration


class TestDemoSeedPipeline:
    async def test_seed_inserts_the_full_fixture_dataset(self, session, org_id) -> None:
        ctx = ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG)
        async with session.begin():
            counts = await seed_crm.seed_deal(session, ctx)
        assert counts == {"deals": 18, "activities": 20, "skipped": 0}

    async def test_seed_is_idempotent(self, session, org_id) -> None:
        ctx = ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG)
        async with session.begin():
            await seed_crm.seed_deal(session, ctx)
        async with session.begin():
            second = await seed_crm.seed_deal(session, ctx)
        assert second["deals"] == 0
        assert second["activities"] == 0
        assert second["skipped"] == 18 + 20

    async def test_the_board_shows_the_fixtures_stage_spread(self, session, org_id) -> None:
        ctx = ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG)
        async with session.begin():
            await seed_crm.seed_deal(session, ctx)
        read = ctx_for(org_id, "crm.pipeline.deal.read", Scope.ORG)
        deals = await selectors.all_deals(session, read)
        by_stage = dict.fromkeys(domain.STAGE_ORDER, 0)
        for d in deals:
            by_stage[d.stage] += 1
        assert by_stage == {
            "lead": 4,
            "qualified": 3,
            "proposal": 4,
            "negotiation": 4,
            "closed_won": 2,
            "closed_lost": 1,
        }

    async def test_deals_cross_link_onto_the_books_deterministic_ids(self, session, org_id) -> None:
        """The pipeline points at the account book by plain UUID; the ids must be exactly
        the ones `seed_customer` derives for the same org."""
        ctx = ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG)
        async with session.begin():
            await seed_crm.seed_deal(session, ctx)
        deal = await selectors.get_deal(session, org_id, seed_crm.demo_id(org_id, "deal-1"))
        assert deal is not None
        assert deal.name == "Fleet telematics rollout"
        assert deal.company_id == seed_crm.demo_id(org_id, "comp-1")
        assert deal.primary_contact_id == seed_crm.demo_id(org_id, "con-1")
        assert deal.owner_id == seed_crm.demo_id(org_id, "own-1")
        assert deal.value_minor == 32_000_000 * 100
        assert deal.currency == "INR"
        assert deal.stage == "negotiation"

    async def test_a_deals_timeline_lands_with_it(self, session, org_id) -> None:
        ctx = ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG)
        async with session.begin():
            await seed_crm.seed_deal(session, ctx)
        deal_id = seed_crm.demo_id(org_id, "deal-8")
        timeline = await activities_for_deal(session, org_id, deal_id)
        assert [a.subject for a in timeline] == [
            "Sent revised commercial proposal",  # 2026-08-23, newest first
            "Send MSA redlines to legal",  # 2026-08-10
        ]
        deal = await selectors.get_deal(session, org_id, deal_id)
        assert deal is not None
        assert deal.last_activity_at is not None
        assert deal.last_activity_at.isoformat() == "2026-08-23T14:25:00+00:00"

    async def test_the_summary_over_the_seed_matches_the_fixture_arithmetic(
        self, session, org_id
    ) -> None:
        """The same numbers the SPA derives from the fixtures: 15 open deals, the open
        pipeline total, and a 67% win rate over the three decided deals."""
        ctx = ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG)
        async with session.begin():
            await seed_crm.seed_deal(session, ctx)
        read = ctx_for(org_id, "crm.pipeline.deal.read", Scope.ORG)
        deals = await selectors.all_deals(session, read)
        assert domain.open_count(deals) == 15
        open_rupees = (
            32_000_000
            + 45_000_000
            + 18_000_000
            + 6_500_000
            + 27_500_000
            + 4_800_000
            + 39_000_000
            + 3_200_000
            + 14_500_000
            + 8_800_000
            + 16_000_000
            + 2_400_000
            + 11_500_000
            + 13_500_000
            + 20_500_000
        )
        assert domain.pipeline_value_minor(deals) == open_rupees * 100
        assert domain.win_rate_pct(deals) == 67
        assert domain.won_value_minor(deals) == (21_000_000 + 9_500_000) * 100
