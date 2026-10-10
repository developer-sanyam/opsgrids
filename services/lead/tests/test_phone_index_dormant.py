"""`leads.phone_index` is dormant: the column exists, nothing computes it and nothing reads it.

It was written on every capture with a phone and read by no selector and no index, so each lead
minted a never-used equality value under the platform-wide blind-index key. R1.2b stops computing it
and maps the column `deferred`, so no statement this service issues names it. The column stays:
phone-based duplicate detection (R3.7) resumes writing it, with an owner-run backfill for the leads
captured in between. If R3.7 drops phone dedupe instead, one change removes the attribute and drops
the column; it is safe only because the running code no longer references the column.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

from app import selectors, services
from platform_core.auth import Scope
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration


def test_select_lead_does_not_name_phone_index() -> None:
    """The compiled statements the service reads leads with. `email_index` is asserted present so
    the check is not vacuous: its sibling column is still selected."""
    ctx = ctx_for(uuid.uuid4(), "crm.lead.lead.read", Scope.ORG)
    list_sql = str(selectors.leads_stmt(ctx).compile(dialect=postgresql.dialect()))
    assert "email_index" in list_sql
    assert "phone_index" not in list_sql


async def test_a_lead_created_with_a_phone_stores_no_index(session: Any, org_id: uuid.UUID) -> None:
    ctx = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
    async with session.begin():
        lead = await services.create_lead(
            session,
            ctx,
            full_name="Dormant Index",
            company=None,
            email=None,
            phone="+919876543210",
            source="manual",
            owner_id=uuid.uuid4(),
            notes="",
        )
    async with session.begin():
        stored = (
            await session.execute(
                text("SELECT phone_index, phone_masked, phone_enc FROM lead.leads WHERE id = :id"),
                {"id": lead.id},
            )
        ).one()
    assert stored.phone_index is None
    # The phone itself is still captured, masked and sealed: only the unused index is gone.
    assert stored.phone_masked is not None
    assert stored.phone_enc is not None


async def test_create_lead_never_computes_a_phone_blind_index(
    session: Any, org_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    kinds: list[str] = []
    real = services.blind_index

    def spy(plaintext: str, *, kind: str, settings: Any) -> str:
        kinds.append(kind)
        return real(plaintext, kind=kind, settings=settings)

    monkeypatch.setattr(services, "blind_index", spy)
    ctx = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
    async with session.begin():
        await services.create_lead(
            session,
            ctx,
            full_name="Both Details",
            company=None,
            email="both.details@example.com",
            phone="+919876543211",
            source="manual",
            owner_id=uuid.uuid4(),
            notes="",
        )
    assert kinds == ["email"]
