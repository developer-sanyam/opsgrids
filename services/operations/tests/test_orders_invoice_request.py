"""Order invoicing, operations' half (INVENTORY-01; Fulfilment plan F4).

READY stages `operations.orders.order.invoice_due` (or records why no invoice is owed);
`app.consumers.order_invoice_due` resolves the order's CRM contact into finance's billed party with
no transaction open and emits `operations.orders.order.invoice_requested`; finance's
`finance.orders.invoice.issued` then sets `order.invoice_id`. Finance itself is a different
process, so its answer is faked at the one seam operations calls
(`finance_client.resolve_finance_customer`); the client's own wire behaviour is tested in
`test_orders_finance_client.py`, and the path it calls in
`tests/infra/test_internal_client_paths.py`.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app import consumers
from app.config import settings
from app.models import AuditLog, FulfilmentSettings, OutboxEvent, ProcessedEvent
from app.orders import finance_client
from app.orders import selectors as order_selectors
from platform_core.db import dispose_engine, init_engine
from platform_core.events import validate_event_schema
from tests.fulfilment_fixtures import Floor, build_floor, ready_order

pytestmark = pytest.mark.integration

INVOICE_DUE = "operations.orders.order.invoice_due"
INVOICE_REQUESTED = "operations.orders.order.invoice_requested"
CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"


@pytest.fixture
async def floor(session, org_id) -> Floor:
    return await build_floor(session, org_id, stock=100)


@pytest.fixture(autouse=True)
async def _engine():  # type: ignore[no-untyped-def]
    """The consumer opens its own `session_scope`, independent of the fixture `session`."""
    init_engine(settings())
    yield
    await dispose_engine()


class FakeFinance:
    """Stands in for finance's `customers:resolve-for-crm-contact`."""

    def __init__(self, answer: uuid.UUID | Exception | None) -> None:
        self.answer = answer
        self.calls: list[tuple[uuid.UUID, uuid.UUID]] = []

    async def __call__(
        self, cfg: Any, organization_id: uuid.UUID, crm_contact_id: uuid.UUID, **_: Any
    ) -> uuid.UUID | None:
        self.calls.append((organization_id, crm_contact_id))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def _fake_finance(monkeypatch: pytest.MonkeyPatch, answer: uuid.UUID | Exception | None):
    fake = FakeFinance(answer)
    monkeypatch.setattr(finance_client, "resolve_finance_customer", fake)
    return fake


async def _seed_settings(session, org_id: uuid.UUID, *, tax: Decimal = Decimal("18")) -> None:
    async with session.begin():
        session.add(
            FulfilmentSettings(
                organization_id=org_id,
                warehouse_id=None,
                invoice_timing="on_delivery",
                payment_defaults={},
                offer_timeout_seconds=300,
                offer_rounds=3,
                require_pickup_code=False,
                default_tax_rate_pct=tax,
            )
        )


async def _events(session, org_id: uuid.UUID, event_type: str, order_id: uuid.UUID):
    async with session.begin():
        return (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.organization_id == org_id,
                        OutboxEvent.event_type == event_type,
                        OutboxEvent.aggregate_id == order_id,
                    )
                )
            )
            .scalars()
            .all()
        )


async def _skips(session, org_id: uuid.UUID, order_id: uuid.UUID) -> list[str]:
    async with session.begin():
        rows = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.organization_id == org_id,
                        AuditLog.action == "order.invoice_request_skipped",
                        AuditLog.entity_id == order_id,
                    )
                )
            )
            .scalars()
            .all()
        )
    return [row.after["reason"] for row in rows]


def _envelope(row: OutboxEvent) -> dict[str, Any]:
    return {
        "event_id": str(row.id),
        "event_type": row.event_type,
        "organization_id": str(row.organization_id),
        "payload": dict(row.payload),
    }


async def _invoice_due(session, org_id: uuid.UUID, order_id: uuid.UUID) -> dict[str, Any]:
    (row,) = await _events(session, org_id, INVOICE_DUE, order_id)
    return _envelope(row)


class TestReady:
    async def test_a_ready_order_with_a_crm_buyer_queues_the_invoice(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _seed_settings(session, org_id)
        contact = uuid.uuid4()
        order_id = await ready_order(client, org_id, session, floor, qty="5", customer_id=contact)

        envelope = await _invoice_due(session, org_id, order_id)
        validate_event_schema(INVOICE_DUE, 1, envelope["payload"], contracts_root=CONTRACTS_ROOT)
        assert envelope["payload"]["crm_contact_id"] == str(contact)
        assert await _skips(session, org_id, order_id) == []

    async def test_no_saved_policy_means_no_chosen_tax_rate_so_nothing_is_invoiced(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order_id = await ready_order(
            client, org_id, session, floor, qty="5", customer_id=uuid.uuid4()
        )

        assert await _events(session, org_id, INVOICE_DUE, order_id) == []
        assert await _skips(session, org_id, order_id) == ["tax_rate_unset"]

    async def test_a_walk_in_buyer_with_no_crm_contact_is_skipped(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _seed_settings(session, org_id)
        order_id = await ready_order(client, org_id, session, floor, qty="5")

        assert await _events(session, org_id, INVOICE_DUE, order_id) == []
        assert await _skips(session, org_id, order_id) == ["no_crm_contact"]


class TestConsumer:
    async def test_ready_to_invoice_requested_to_invoice_id(
        self, client, org_id, session, floor: Floor, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _seed_settings(session, org_id, tax=Decimal("18"))
        contact = uuid.uuid4()
        finance_customer = uuid.uuid4()
        fake = _fake_finance(monkeypatch, finance_customer)
        # 5 widgets @ 500_00 = 2_500_00; 18% GST.
        order_id = await ready_order(client, org_id, session, floor, qty="5", customer_id=contact)

        await consumers.order_invoice_due(await _invoice_due(session, org_id, order_id))

        assert fake.calls == [(org_id, contact)]
        (row,) = await _events(session, org_id, INVOICE_REQUESTED, order_id)
        payload = dict(row.payload)
        validate_event_schema(INVOICE_REQUESTED, 1, payload, contracts_root=CONTRACTS_ROOT)
        assert payload["customer_id"] == str(finance_customer)
        assert payload["expected_subtotal_minor"] == 2_500_00
        assert payload["expected_tax_minor"] == 450_00
        assert payload["expected_total_minor"] == 2_950_00
        assert payload["lines"][0]["line_amount_minor"] == 2_500_00
        # Finance derives these from its own billed party; operations knows neither.
        assert "supply_type" not in payload
        assert "place_of_supply" not in payload
        assert await _skips(session, org_id, order_id) == []

        # Finance's answer closes the loop on the order.
        invoice_id = uuid.uuid4()
        await consumers.finance_invoice_issued(
            {
                "event_id": str(uuid.uuid4()),
                "event_type": "finance.orders.invoice.issued",
                "organization_id": str(org_id),
                "payload": {
                    "organization_id": str(org_id),
                    "order_id": str(order_id),
                    "invoice_id": str(invoice_id),
                    "invoice_number": "INV-0001",
                    "total_minor": payload["expected_total_minor"],
                },
            }
        )
        async with session.begin():
            order = await order_selectors.get_order(session, org_id, order_id, for_update=True)
        assert order is not None
        assert order.invoice_id == invoice_id

    async def test_redelivery_asks_finance_once_and_requests_one_invoice(
        self, client, org_id, session, floor: Floor, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _seed_settings(session, org_id)
        fake = _fake_finance(monkeypatch, uuid.uuid4())
        order_id = await ready_order(
            client, org_id, session, floor, qty="2", customer_id=uuid.uuid4()
        )
        envelope = await _invoice_due(session, org_id, order_id)

        await consumers.order_invoice_due(envelope)
        await consumers.order_invoice_due(envelope)

        assert len(fake.calls) == 1
        assert len(await _events(session, org_id, INVOICE_REQUESTED, order_id)) == 1
        async with session.begin():
            processed = (
                await session.execute(
                    select(ProcessedEvent).where(
                        ProcessedEvent.event_id == uuid.UUID(envelope["event_id"])
                    )
                )
            ).scalar_one()
        assert processed.event_type == INVOICE_DUE

    async def test_a_contact_finance_does_not_recognise_is_skipped_not_invented(
        self, client, org_id, session, floor: Floor, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _seed_settings(session, org_id)
        _fake_finance(monkeypatch, None)
        order_id = await ready_order(
            client, org_id, session, floor, qty="2", customer_id=uuid.uuid4()
        )

        await consumers.order_invoice_due(await _invoice_due(session, org_id, order_id))

        assert await _events(session, org_id, INVOICE_REQUESTED, order_id) == []
        assert await _skips(session, org_id, order_id) == ["crm_contact_not_found"]

    async def test_finance_unavailable_raises_so_the_broker_retries(
        self, client, org_id, session, floor: Floor, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _seed_settings(session, org_id)
        _fake_finance(monkeypatch, finance_client.FinanceUnavailable("down"))
        order_id = await ready_order(
            client, org_id, session, floor, qty="2", customer_id=uuid.uuid4()
        )
        envelope = await _invoice_due(session, org_id, order_id)

        with pytest.raises(finance_client.FinanceUnavailable):
            await consumers.order_invoice_due(envelope)

        assert await _events(session, org_id, INVOICE_REQUESTED, order_id) == []
        assert await _skips(session, org_id, order_id) == []
        async with session.begin():
            processed = (
                await session.execute(
                    select(ProcessedEvent).where(
                        ProcessedEvent.event_id == uuid.UUID(envelope["event_id"])
                    )
                )
            ).scalar_one_or_none()
        assert processed is None

    async def test_another_tenant_sees_nothing(
        self, client, org_id, session, floor: Floor, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An envelope naming another organization binds that tenant, where this order does not
        exist: nothing is requested for it, for either tenant."""
        await _seed_settings(session, org_id)
        _fake_finance(monkeypatch, uuid.uuid4())
        order_id = await ready_order(
            client, org_id, session, floor, qty="2", customer_id=uuid.uuid4()
        )
        envelope = await _invoice_due(session, org_id, order_id)
        other_org = uuid.uuid4()
        envelope = {
            **envelope,
            "event_id": str(uuid.uuid4()),
            "organization_id": str(other_org),
            "payload": {**envelope["payload"], "organization_id": str(other_org)},
        }

        await consumers.order_invoice_due(envelope)

        assert await _events(session, org_id, INVOICE_REQUESTED, order_id) == []
        assert await _events(session, other_org, INVOICE_REQUESTED, order_id) == []
