"""Every enriched `purchase_order.*` event, built through the real write path, validated against
the JSON Schema committed for it under `libs/contracts/events/` (ADR-0020 slice I5,
`docs/insights/events.md` §6).

Same shape as `services/notification/tests/test_event_contracts.py`: drive the real
`app/services.py` function inside a real transaction, read the staged outbox row back, and check
it against the schema — never a hand-written dict that could quietly diverge from what the code
actually emits.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app import services
from app.models import OutboxEvent
from app.schemas import PurchaseOrderPatch
from platform_core.auth import Scope
from platform_core.events import validate_event_schema
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration

#: `tests`, `operations`, `services`, ROOT.
CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"

PO_WRITE = "operations.procurement.purchase_order.write"
PO_APPROVE = "operations.procurement.purchase_order.approve"


async def _last_payload(session: Any, event_type: str) -> dict[str, Any]:
    stmt = (
        select(OutboxEvent)
        .where(OutboxEvent.event_type == event_type)
        .order_by(OutboxEvent.created_at.desc())
    )
    async with session.begin():
        row = (await session.execute(stmt)).scalars().first()
        assert row is not None, f"no {event_type} event was staged"
        return dict(row.payload)


async def _validate(session: Any, event_type: str) -> None:
    payload = await _last_payload(session, event_type)
    validate_event_schema(event_type, 1, payload, contracts_root=CONTRACTS_ROOT)


async def test_po_raised_updated_approved_received_match_their_schemas(
    session, org_id, data
) -> None:
    write = ctx_for(org_id, PO_WRITE, Scope.ORG)
    approve = ctx_for(org_id, PO_APPROVE, Scope.ORG)

    async with session.begin():
        po = await services.create_purchase_order(
            session,
            write,
            number="PO-2026-9001",
            supplier_id=data["meridian"].id,
            status="draft",
            warehouse="Pune Plant",
            raised_by="Test Buyer",
            raised_by_id=data["subject_a"],
            currency="INR",
            created_date=date(2026, 9, 1),
            expected_date=date(2026, 9, 20),
            lines=[
                {
                    "sku": "RM-STL-4501",
                    "description": "Cold-rolled steel coil",
                    "quantity": 5,
                    "uom": "MT",
                    "unit_price_minor": 62_500_00,
                }
            ],
        )
    await _validate(session, services.EVT_PO_RAISED)

    async with session.begin():
        po = await services.update_purchase_order(
            session, write, po, PurchaseOrderPatch(notes="Updated for the contract test")
        )
    await _validate(session, services.EVT_PO_UPDATED)

    async with session.begin():
        po = await services.approve_purchase_order(session, approve, po)
    await _validate(session, services.EVT_PO_APPROVED)

    async with session.begin():
        await services.receive_purchase_order(session, approve, po)
    await _validate(session, services.EVT_PO_RECEIVED)
