"""The `operations.orders.order.invoice_requested` trigger (`app.orders.services
.request_invoice_for_order`, called from `_maybe_ready_order` via `handle_order_ready` — Fulfilment
plan F4).

Two things this slice's own report is explicit about:

1. **Today, in this build, the trigger is a visibly-logged no-op** — `_resolve_finance_customer_id`
   always returns `None` (the unresolved customer_id gap; see that function's own docstring), so
   no event is ever published; an audit row says why, on the order's own trail.
2. **The payload-building code is real and ready**, not aspirational — `TestPayloadShape` proves
   it by monkeypatching only the one unresolved seam (`_resolve_finance_customer_id`) and checking
   the emitted payload against the exact JSON Schema finance's already-deployed consumer expects
   (`libs/contracts/events/operations.orders.order.invoice_requested/v1.schema.json`).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import AuditLog, FulfilmentSettings, OutboxEvent
from app.orders import services as order_services
from platform_core.events import validate_event_schema
from tests.fulfilment_fixtures import Floor, build_floor, ready_order

pytestmark = pytest.mark.integration

INVOICE_REQUESTED = "operations.orders.order.invoice_requested"
#: `test_x.py`, `tests`, `operations`, `services`, ROOT — the same depth
#: `services/notification/tests/test_event_contracts.py` computes it at.
CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"


@pytest.fixture
async def floor(session, org_id) -> Floor:
    return await build_floor(session, org_id, stock=100)


async def _seed_settings(
    session, org_id: uuid.UUID, *, default_tax_rate_pct: Decimal = Decimal("0")
) -> None:
    async with session.begin():
        session.add(
            FulfilmentSettings(
                organization_id=org_id,
                warehouse_id=None,
                invoice_timing="on_accept",
                payment_defaults={},
                offer_timeout_seconds=300,
                offer_rounds=3,
                require_pickup_code=False,
                default_tax_rate_pct=default_tax_rate_pct,
            )
        )


class TestSkippedForNowGap:
    async def test_no_event_is_published(self, client, org_id, session, floor: Floor) -> None:
        order_id = await ready_order(client, org_id, session, floor, qty="5")

        async with session.begin():
            rows = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.organization_id == org_id,
                            OutboxEvent.event_type == INVOICE_REQUESTED,
                            OutboxEvent.aggregate_id == order_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert rows == []

    async def test_the_gap_is_recorded_on_the_orders_own_audit_trail(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order_id = await ready_order(client, org_id, session, floor, qty="5")

        async with session.begin():
            row = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.organization_id == org_id,
                        AuditLog.action == "order.invoice_request_skipped",
                        AuditLog.entity_id == order_id,
                    )
                )
            ).scalar_one()
        assert row.after["reason"] == "no_finance_customer_mapping"


class TestPayloadShape:
    """The one unresolved seam is patched to prove the rest of the payload-building code is
    real: correct field shapes, and numbers finance's own recomputation would agree with."""

    async def test_the_payload_matches_the_committed_contract(
        self, client, org_id, session, floor: Floor, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        finance_customer_id = uuid.uuid4()
        monkeypatch.setattr(
            order_services, "_resolve_finance_customer_id", lambda order: finance_customer_id
        )
        await _seed_settings(session, org_id, default_tax_rate_pct=Decimal("18"))

        # 5 widgets @ 500_00 minor each = 2_500_00 subtotal; 18% GST, rounded half-up.
        order_id = await ready_order(client, org_id, session, floor, qty="5")

        async with session.begin():
            row = (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.organization_id == org_id,
                        OutboxEvent.event_type == INVOICE_REQUESTED,
                        OutboxEvent.aggregate_id == order_id,
                    )
                )
            ).scalar_one()
        payload = dict(row.payload)

        validate_event_schema(INVOICE_REQUESTED, 1, payload, contracts_root=CONTRACTS_ROOT)

        assert payload["organization_id"] == str(org_id)
        assert payload["order_id"] == str(order_id)
        assert payload["customer_id"] == str(finance_customer_id)
        assert payload["currency"] == "INR"
        assert payload["tax_rate_pct"] == 18.0
        assert payload["expected_subtotal_minor"] == 2_500_00
        assert payload["expected_tax_minor"] == 450_00  # 18% of 2_500_00, exact
        assert payload["expected_total_minor"] == 2_950_00
        assert payload["supply_type"] == "b2b"
        assert payload["reverse_charge"] is False
        assert "place_of_supply" not in payload or payload["place_of_supply"] is None
        assert len(payload["lines"]) == 1
        line = payload["lines"][0]
        assert line["sku"] == "WGT-001"
        assert line["quantity"] == 5.0
        assert line["unit_price_minor"] == 500_00
        assert line["line_amount_minor"] == 2_500_00

    async def test_an_audit_row_names_the_customer_and_total(
        self, client, org_id, session, floor: Floor, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        finance_customer_id = uuid.uuid4()
        monkeypatch.setattr(
            order_services, "_resolve_finance_customer_id", lambda order: finance_customer_id
        )
        await _seed_settings(session, org_id, default_tax_rate_pct=Decimal("0"))

        order_id = await ready_order(client, org_id, session, floor, qty="2")

        async with session.begin():
            row = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.organization_id == org_id,
                        AuditLog.action == "order.invoice_requested",
                        AuditLog.entity_id == order_id,
                    )
                )
            ).scalar_one()
        assert row.after["customer_id"] == str(finance_customer_id)
        assert row.after["expected_total_minor"] == 1_000_00
