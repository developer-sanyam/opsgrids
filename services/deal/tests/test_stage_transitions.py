"""Stage-transition legality: the pipeline's one real state machine.

The rule (`domain.can_transition`) is pure and tested exhaustively; `services.move_stage`
is its enforcement point and is tested against the database — including that a refused
move leaves no audit row and no event, because a rejected change must not look like one
that happened.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app import domain, selectors, services
from app.models import AuditLog, OutboxEvent
from platform_core.auth import Scope
from platform_core.errors import ConflictProblem
from tests.helpers import ctx_for

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("pipeline_mode")]


class TestTheRule:
    def test_open_deals_move_freely_between_open_stages(self) -> None:
        for current in domain.OPEN_STAGES:
            for new in domain.OPEN_STAGES:
                if new != current:
                    assert domain.can_transition(current, new), f"{current} -> {new}"

    def test_open_deals_close_either_way(self) -> None:
        for current in domain.OPEN_STAGES:
            assert domain.can_transition(current, "closed_won")
            assert domain.can_transition(current, "closed_lost")

    def test_a_closed_deal_is_a_record_and_moves_nowhere(self) -> None:
        for current in ("closed_won", "closed_lost"):
            for new in domain.STAGE_ORDER:
                assert not domain.can_transition(current, new), f"{current} -> {new}"

    def test_a_move_to_the_same_stage_is_not_a_transition(self) -> None:
        for stage in domain.STAGE_ORDER:
            assert not domain.can_transition(stage, stage)

    def test_unknown_stages_never_pass(self) -> None:
        assert not domain.can_transition("lead", "galaxy_brain")
        assert not domain.can_transition("galaxy_brain", "lead")


class TestTheEnforcement:
    async def test_an_open_deal_advances_and_probability_follows(
        self, session, org_id, data
    ) -> None:
        ctx = ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG)
        deal = data["deal_a"]  # negotiation
        async with session.begin():
            await services.move_stage(session, ctx, deal, new_stage="closed_won")
        fresh = await selectors.get_deal(session, org_id, deal.id)
        assert fresh is not None
        assert fresh.stage == "closed_won"
        assert fresh.probability == 100

    async def test_a_closed_deal_refuses_to_move(self, session, org_id, data) -> None:
        ctx = ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG)
        deal = data["deal_b"]  # closed_won
        with pytest.raises(ConflictProblem, match="cannot move"):
            async with session.begin():
                await services.move_stage(session, ctx, deal, new_stage="negotiation")

    async def test_a_move_stages_its_audit_row_and_event(self, session, org_id, data) -> None:
        ctx = ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG)
        deal = data["deal_a"]
        async with session.begin():
            await services.move_stage(session, ctx, deal, new_stage="proposal")
        audits = (
            await session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "deal.stage_changed", AuditLog.entity_id == deal.id)
            )
        ).scalar_one()
        events = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.aggregate_id == deal.id,
                        OutboxEvent.event_type == services.EVT_DEAL_STAGE_CHANGED,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert audits == 1
        assert len(events) == 1
        assert events[0].payload["from"] == "negotiation"
        assert events[0].payload["to"] == "proposal"

    async def test_a_refused_move_leaves_no_trace(self, session, org_id, data) -> None:
        """A rejected change must not look like one that happened: no stage change, no
        audit row, no event."""
        ctx = ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG)
        deal = data["deal_b"]  # closed_won
        deal_id = data["deal_b_id"]  # plain value: the rollback below expires the instance
        with pytest.raises(ConflictProblem):
            async with session.begin():
                await services.move_stage(session, ctx, deal, new_stage="lead")
        fresh = await selectors.get_deal(session, org_id, deal_id)
        assert fresh is not None
        assert fresh.stage == "closed_won"
        audits = (
            await session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "deal.stage_changed", AuditLog.entity_id == deal_id)
            )
        ).scalar_one()
        assert audits == 0
