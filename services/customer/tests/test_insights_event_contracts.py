"""Every `crm.book.company.*` event this service stages, validated against the JSON Schema
committed for it under `libs/contracts/events/` (ADR-0020 §6, `docs/insights/events.md` §6).

Same shape as `services/notification/tests/test_event_contracts.py`.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app import services
from app.models import OutboxEvent
from app.schemas import CompanyPatch
from platform_core.auth import Scope
from platform_core.events import validate_event_schema
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration

#: `services.py`, `tests/`, `customer`, `services`, ROOT.
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


class TestCompanyEventContracts:
    async def test_company_created_matches_its_schema(self, session, org_id) -> None:
        write = ctx_for(org_id, "crm.book.company.write", Scope.ORG)
        async with session.begin():
            rep = await services.create_owner(
                session,
                write,
                full_name="Contract Rep",
                email="contract.rep@opsgrids.example.com",
                role="AE",
                owner_id=uuid.uuid4(),
            )
            await services.create_company(session, write, name="Contract Check Co", owner_id=rep.id)
        await _validate(session, services.EVT_COMPANY_CREATED)

    async def test_company_updated_matches_its_schema(self, session, org_id, data) -> None:
        write = ctx_for(org_id, "crm.book.company.write", Scope.ORG)
        company = data["company_a"]
        async with session.begin():
            await services.update_company(session, write, company, CompanyPatch(city="Pune"))
        await _validate(session, services.EVT_COMPANY_UPDATED)

    async def test_company_deleted_matches_its_schema(self, session, org_id) -> None:
        write = ctx_for(org_id, "crm.book.company.write", Scope.ORG)
        async with session.begin():
            rep = await services.create_owner(
                session,
                write,
                full_name="Contract Rep 2",
                email="contract.rep2@opsgrids.example.com",
                role="AE",
                owner_id=uuid.uuid4(),
            )
            company = await services.create_company(
                session, write, name="Deletable Co", owner_id=rep.id
            )
        async with session.begin():
            fresh = await session.get(type(company), company.id)
            await services.delete_company(session, write, fresh)
        await _validate(session, services.EVT_COMPANY_DELETED)
