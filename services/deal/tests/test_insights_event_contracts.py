"""Every `crm.pipeline.deal.*` event this service stages, validated against the JSON Schema
committed for it under `libs/contracts/events/` (ADR-0020 §6, `docs/insights/events.md` §6).

Same shape as `services/notification/tests/test_event_contracts.py`: the payload here comes
from the real `services.py` functions the routes call, never a hand-written dict, so a real
transaction is what proves the schema describes what actually lands on the outbox — not what a
docstring claims it does. Once this passes, the schema's own `$comment` ("ahead of the
producer") is removed in the same commit (already done here), because the schema is no longer
ahead of what `deal/app/services.py` emits.
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app import services
from app.models import OutboxEvent
from app.schemas import DealPatch
from platform_core.auth import Scope
from platform_core.events import validate_event_schema
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration

#: `services.py`, `tests/`, `deal`, `services`, ROOT.
CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"


async def _last_payload(session: Any, event_type: str) -> dict[str, Any]:
    stmt = (
        select(OutboxEvent)
        .where(OutboxEvent.event_type == event_type)
        .order_by(OutboxEvent.created_at.desc())
    )
    row = (await session.execute(stmt)).scalars().first()
    assert row is not None, f"no {event_type} event was staged"
    return dict(row.payload)


async def _validate(session: Any, event_type: str, *, version: int = 1) -> None:
    payload = await _last_payload(session, event_type)
    validate_event_schema(event_type, version, payload, contracts_root=CONTRACTS_ROOT)


class TestDealEventContracts:
    async def test_deal_created_matches_its_schema(self, session, org_id) -> None:
        ctx = ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG)
        async with session.begin():
            await services.create_deal(
                session,
                ctx,
                name="Contract-checked deal",
                company_id=uuid.uuid4(),
                owner_id=uuid.uuid4(),
                value_minor=500_00,
                close_date=date(2026, 12, 1),
            )
        await _validate(session, services.EVT_DEAL_CREATED)

    async def test_deal_created_closed_at_set_for_a_deal_opened_closed(
        self, session, org_id
    ) -> None:
        """The one case `closed_at` is non-null on `created`: a deal opened directly in a
        closed stage (an import, say)."""
        ctx = ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG)
        async with session.begin():
            await services.create_deal(
                session,
                ctx,
                name="Imported, already won",
                company_id=uuid.uuid4(),
                owner_id=uuid.uuid4(),
                value_minor=1_000_00,
                close_date=date(2026, 1, 1),
                stage="closed_won",
            )
        payload = await _last_payload(session, services.EVT_DEAL_CREATED)
        assert payload["closed_at"] is not None
        await _validate(session, services.EVT_DEAL_CREATED)

    async def test_deal_updated_matches_its_schema(self, session, org_id, data) -> None:
        ctx = ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG)
        deal = data["deal_a"]
        async with session.begin():
            await services.update_deal(
                session, ctx, deal, DealPatch(value_minor=deal.value_minor + 100_00)
            )
        await _validate(session, services.EVT_DEAL_UPDATED)

    async def test_deal_stage_changed_matches_its_schema(self, session, org_id, data) -> None:
        ctx = ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG)
        deal = data["deal_a"]
        async with session.begin():
            await services.move_stage(session, ctx, deal, new_stage="closed_won")
        payload = await _last_payload(session, services.EVT_DEAL_STAGE_CHANGED)
        assert payload["closed_at"] is not None
        await _validate(session, services.EVT_DEAL_STAGE_CHANGED)

    async def test_deal_deleted_matches_its_schema(self, session, org_id, data) -> None:
        ctx = ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG)
        deal = data["deal_a"]
        async with session.begin():
            await services.delete_deal(session, ctx, deal)
        await _validate(session, services.EVT_DEAL_DELETED)
