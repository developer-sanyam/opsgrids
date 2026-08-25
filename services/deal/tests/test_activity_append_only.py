"""The activity timeline is append-only, and the database enforces it.

Appending is normal; rewriting history is not. A `BEFORE UPDATE OR DELETE` trigger refuses
both, so the timeline is a record of what happened rather than a draft — a guarantee that
survives the app role being re-granted DML after every migration.

Appending also drives the staleness clock: a deal's `last_activity_at` advances with every
touch, and only forward — the Overview's stale/attention reading depends on it.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import text

from app import selectors, services
from platform_core.auth import Scope
from tests.conftest import DEMO_NOW
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration


class TestActivityAppendOnly:
    async def test_appending_grows_the_timeline(self, session, org_id, data) -> None:
        """The ordinary path works: a logged touch joins the deal's timeline."""
        ctx = ctx_for(org_id, "crm.pipeline.activity.write", Scope.ORG)
        deal = data["deal_a"]
        async with session.begin():
            before = len(await selectors.activities_for_deal(session, org_id, deal.id))
            await services.log_activity(
                session,
                ctx,
                activity_type="email",
                subject="Sent the pilot proposal",
                deal_id=deal.id,
                owner_id=data["subject_a"],
                direction="outbound",
            )
        after = await selectors.activities_for_deal(session, org_id, deal.id)
        assert len(after) == before + 1
        assert after[0].subject == "Sent the pilot proposal"

    async def test_updating_an_activity_is_refused_by_the_database(
        self, session, org_id, data
    ) -> None:
        target = data["act_a_id"]
        with pytest.raises(Exception, match="append-only"):
            async with session.begin():
                await session.execute(
                    text("UPDATE deal.activities SET subject = 'tampered' WHERE id = :id"),
                    {"id": target},
                )

    async def test_deleting_an_activity_is_refused_by_the_database(
        self, session, org_id, data
    ) -> None:
        target = data["act_a_id"]
        with pytest.raises(Exception, match="append-only"):
            async with session.begin():
                await session.execute(
                    text("DELETE FROM deal.activities WHERE id = :id"), {"id": target}
                )


class TestStalenessClock:
    async def test_logging_advances_the_deals_last_activity(self, session, org_id, data) -> None:
        ctx = ctx_for(org_id, "crm.pipeline.activity.write", Scope.ORG)
        deal = data["deal_a"]
        later = DEMO_NOW + timedelta(days=1)
        async with session.begin():
            await services.log_activity(
                session,
                ctx,
                activity_type="call",
                subject="Follow-up",
                deal_id=deal.id,
                owner_id=data["subject_a"],
                occurred_at=later,
                direction="outbound",
            )
        fresh = await selectors.get_deal(session, org_id, deal.id)
        assert fresh is not None
        assert fresh.last_activity_at == later

    async def test_a_backfilled_touch_never_winds_the_clock_backwards(
        self, session, org_id, data
    ) -> None:
        """Monotonic: importing an old email must not make a fresh deal look stale."""
        ctx = ctx_for(org_id, "crm.pipeline.activity.write", Scope.ORG)
        deal = data["deal_a"]
        earlier = DEMO_NOW - timedelta(days=30)
        async with session.begin():
            await services.log_activity(
                session,
                ctx,
                activity_type="email",
                subject="Backfilled thread",
                deal_id=deal.id,
                owner_id=data["subject_a"],
                occurred_at=earlier,
            )
        fresh = await selectors.get_deal(session, org_id, deal.id)
        assert fresh is not None
        assert fresh.last_activity_at == DEMO_NOW  # the fixture's latest touch, unchanged
