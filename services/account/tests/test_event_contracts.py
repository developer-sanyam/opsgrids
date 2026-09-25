"""`core.org.created` and `core.org.updated`, built through the real functions that publish
them, validated against the JSON Schema committed for each under `libs/contracts/events/`
(ADR-0020 §6, events.md §6).

Same shape as `services/notification/tests/test_event_contracts.py`: real PostgreSQL as the app
role, the payload read back from the staged `outbox` row exactly as a consumer would, never a
hand-written dict that could quietly diverge from what `app/services.py` actually emits.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.models import Organization, OutboxEvent
from app.schemas import OrganizationPatch
from platform_core.auth import Scope
from platform_core.events import validate_event_schema
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration

#: `tests/`, `account`, `services`, ROOT.
CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"


async def _last_payload(session: AsyncSession, event_type: str) -> dict[str, Any]:
    stmt = (
        select(OutboxEvent)
        .where(OutboxEvent.event_type == event_type)
        .order_by(OutboxEvent.created_at.desc())
    )
    row = (await session.execute(stmt)).scalars().first()
    assert row is not None, f"no {event_type} event was staged"
    return dict(row.payload)


async def _validate(session: AsyncSession, event_type: str, *, version: int = 1) -> None:
    payload = await _last_payload(session, event_type)
    validate_event_schema(event_type, version, payload, contracts_root=CONTRACTS_ROOT)


async def test_org_created_matches_its_schema(
    admin_session: AsyncSession, org: Organization
) -> None:
    """`org` (the fixture) already ran `create_organization` on the admin engine before this
    test starts, so the staged event is right there — no second organization to create just to
    exercise the same code path again."""
    await _validate(admin_session, services.EVT_ORG_CREATED)


async def test_org_updated_matches_its_schema(
    session: AsyncSession, org: Organization, people
) -> None:
    ctx = ctx_for(org.id, people["manager"], "core.identity.org.write", Scope.ORG)
    async with session.begin():
        row = await selectors.get_organization(session, org.id)
        assert row is not None
        await services.update_organization(
            session, ctx, row, OrganizationPatch(name="Acme Renamed")
        )
    await _validate(session, services.EVT_ORG_UPDATED)


async def test_org_updated_carries_timezone_currency_and_fiscal_year_even_when_untouched(
    session: AsyncSession, org: Organization, people
) -> None:
    """The one field group ADR-0020 §6 requires present on *every* update, not only when
    `changed` names it — unlike `name`/`country`, which stay `null` off a patch that never
    touched them."""
    ctx = ctx_for(org.id, people["manager"], "core.identity.org.write", Scope.ORG)
    async with session.begin():
        row = await selectors.get_organization(session, org.id)
        assert row is not None
        await services.update_organization(
            session, ctx, row, OrganizationPatch(legal_name="Acme Technologies Pvt Ltd")
        )
    payload = await _last_payload(session, services.EVT_ORG_UPDATED)
    assert payload["name"] is None
    assert payload["timezone"] == "Asia/Kolkata"
    assert payload["currency"] == "INR"
    assert payload["fiscal_year_start_month"] == 4
