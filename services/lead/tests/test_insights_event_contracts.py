"""Every `crm.lead.*` event this service stages, validated against the JSON Schema committed
for it under `libs/contracts/events/` (ADR-0020 §6, `docs/insights/events.md` §6).

Same shape as `services/notification/tests/test_event_contracts.py`: the payload comes from
the real `services.py` functions the routes call, never a hand-written dict.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app import clients, services
from app.models import OutboxEvent
from app.schemas import LeadPatch
from platform_core.auth import Scope
from platform_core.events import validate_event_schema
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration

#: `services.py`, `tests/`, `lead`, `services`, ROOT.
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


class TestLeadEventContracts:
    async def test_lead_created_matches_its_schema(self, session, org_id) -> None:
        write = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        async with session.begin():
            await services.create_lead(
                session,
                write,
                full_name="Contract Check",
                company=None,
                email=None,
                phone=None,
                source="manual",
                owner_id=None,
                notes="",
            )
        await _validate(session, services.EVT_LEAD_CREATED)

    async def test_lead_updated_matches_its_schema(self, session, org_id, data) -> None:
        write = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        lead_b = data["lead_b"]
        async with session.begin():
            await services.update_lead(session, write, lead_b.id, LeadPatch(status="working"))
        await _validate(session, services.EVT_LEAD_UPDATED)

    async def test_lead_converted_matches_its_schema(
        self, session, org_id, data, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        company_id, contact_id, deal_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

        async def _fake_company(settings, organization_id, **kwargs):
            return company_id, contact_id

        async def _fake_deal(settings, organization_id, **kwargs):
            return deal_id

        monkeypatch.setattr(clients, "ensure_company_and_contact", _fake_company)
        monkeypatch.setattr(clients, "ensure_deal", _fake_deal)

        ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)
        await services.convert_lead(session, ctx, data["lead_a_id"])

        payload = await _last_payload(session, services.EVT_LEAD_CONVERTED)
        assert payload["owner_id"] is not None
        assert payload["converted_at"] is not None
        await _validate(session, services.EVT_LEAD_CONVERTED)
