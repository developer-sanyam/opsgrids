"""`app.consumers.finance_invoice_issued` — the operations-side half of ADR-0025 §1's billing
gate (Fulfilment plan F4): the only way `Order.invoice_id` (the fact `check_billing_gate` reads)
ever gets set, since operations never writes finance's own tables.

Mirrors `test_fulfilment_order_cancelled_consumer.py`'s own shape exactly: a wrong-tenant
envelope touches nothing, redelivery is idempotent through the existing `ProcessedEvent` ledger
(the plan's own "does not create a second invoice request" exit criterion, proven here on the
genuinely new consumer code this slice adds), and the happy path records the fact.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app import consumers
from app.config import settings
from app.models import Order, ProcessedEvent
from app.orders import selectors as order_selectors
from platform_core.db import dispose_engine, init_engine
from tests.fulfilment_fixtures import Floor, accept_order, build_floor

pytestmark = pytest.mark.integration


@pytest.fixture
async def floor(session, org_id) -> Floor:
    return await build_floor(session, org_id, stock=100)


@pytest.fixture(autouse=True)
async def _engine():  # type: ignore[no-untyped-def]
    """`finance_invoice_issued` opens its own `session_scope`, independent of the fixture
    `session` this file otherwise uses — same reason and shape as
    `test_fulfilment_order_cancelled_consumer.py::_engine`."""
    init_engine(settings())
    yield
    await dispose_engine()


def _issued_envelope(
    *, organization_id: uuid.UUID, order_id: uuid.UUID, invoice_id: uuid.UUID
) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": "finance.orders.invoice.issued",
        "organization_id": str(organization_id),
        "payload": {
            "organization_id": str(organization_id),
            "order_id": str(order_id),
            "invoice_id": str(invoice_id),
            "invoice_number": "INV-2026-0001",
            "total_minor": 50000,
        },
    }


async def _order(session, org_id: uuid.UUID, order_id: uuid.UUID) -> Order:
    """`for_update=True` matters here beyond locking: it is what makes `get_order` pass
    `populate_existing=True` (that function's own docstring), forcing a refresh of the `Order`
    Python object `accept_order` already put in this session's identity map — the consumer under
    test writes through its own, independent `session_scope` (`_engine`'s own docstring), so
    without this a plain read would silently return the pre-consumer, stale cached object."""
    async with session.begin():
        row = await order_selectors.get_order(session, org_id, order_id, for_update=True)
        assert row is not None
    return row


class TestHappyPath:
    async def test_records_the_invoice_id_fact(self, org_id, session, floor: Floor) -> None:
        order = await accept_order(
            session,
            org_id,
            floor.shop,
            lines=[{"inventory_item_id": floor.shop.widget, "quantity": "1"}],
        )
        invoice_id = uuid.uuid4()

        await consumers.finance_invoice_issued(
            _issued_envelope(organization_id=org_id, order_id=order.id, invoice_id=invoice_id)
        )

        after = await _order(session, org_id, order.id)
        assert after.invoice_id == invoice_id

    async def test_an_unknown_order_is_a_visible_no_op_not_a_crash(self, org_id, session) -> None:
        # No order created at all — a redelivery racing a compensating cancellation, or a bad
        # payload, must not raise (which would dead-letter after the broker's one retry).
        await consumers.finance_invoice_issued(
            _issued_envelope(organization_id=org_id, order_id=uuid.uuid4(), invoice_id=uuid.uuid4())
        )


class TestIdempotency:
    async def test_a_redelivered_event_does_not_double_process(
        self, org_id, session, floor: Floor
    ) -> None:
        order = await accept_order(
            session,
            org_id,
            floor.shop,
            lines=[{"inventory_item_id": floor.shop.widget, "quantity": "1"}],
        )
        invoice_id = uuid.uuid4()
        envelope = _issued_envelope(
            organization_id=org_id, order_id=order.id, invoice_id=invoice_id
        )

        await consumers.finance_invoice_issued(envelope)
        # The exact same delivery again — must not raise and must not touch the row twice.
        await consumers.finance_invoice_issued(envelope)

        after = await _order(session, org_id, order.id)
        assert after.invoice_id == invoice_id

        async with session.begin():
            processed = (
                (
                    await session.execute(
                        select(ProcessedEvent).where(
                            ProcessedEvent.event_id == uuid.UUID(envelope["event_id"])
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(processed) == 1  # recorded once, not once per delivery

    async def test_a_second_different_event_for_an_already_invoiced_order_does_not_overwrite(
        self, org_id, session, floor: Floor
    ) -> None:
        """The domain-level half of idempotency (`record_invoice_issued`'s own docstring): a
        *different* event id delivering a *different* invoice id for an order that already has
        one is still a no-op — an invoice is immutable once issued (ADR-0000 §9)."""
        order = await accept_order(
            session,
            org_id,
            floor.shop,
            lines=[{"inventory_item_id": floor.shop.widget, "quantity": "1"}],
        )
        first_invoice = uuid.uuid4()
        await consumers.finance_invoice_issued(
            _issued_envelope(organization_id=org_id, order_id=order.id, invoice_id=first_invoice)
        )

        second_invoice = uuid.uuid4()
        await consumers.finance_invoice_issued(
            _issued_envelope(organization_id=org_id, order_id=order.id, invoice_id=second_invoice)
        )

        after = await _order(session, org_id, order.id)
        assert after.invoice_id == first_invoice


class TestWrongTenant:
    async def test_an_envelope_for_a_different_organization_touches_nothing(
        self, org_id, session, floor: Floor
    ) -> None:
        order = await accept_order(
            session,
            org_id,
            floor.shop,
            lines=[{"inventory_item_id": floor.shop.widget, "quantity": "1"}],
        )
        wrong_org = uuid.uuid4()

        await consumers.finance_invoice_issued(
            _issued_envelope(organization_id=wrong_org, order_id=order.id, invoice_id=uuid.uuid4())
        )

        after = await _order(session, org_id, order.id)
        assert after.invoice_id is None  # untouched


class TestOrganizationMismatch:
    async def test_a_payload_naming_a_different_org_than_the_envelope_is_refused(
        self, org_id, session, floor: Floor
    ) -> None:
        """The envelope's own `organization_id` is what binds the tenant GUC — a payload that
        disagrees with it (tampered, or a producer bug) is never trusted, the same guard
        `services/finance/app/consumers.py::invoice_requested_from_order` documents for the
        identical cross-service shape."""
        order = await accept_order(
            session,
            org_id,
            floor.shop,
            lines=[{"inventory_item_id": floor.shop.widget, "quantity": "1"}],
        )
        envelope = _issued_envelope(
            organization_id=org_id, order_id=order.id, invoice_id=uuid.uuid4()
        )
        envelope["payload"]["organization_id"] = str(uuid.uuid4())  # disagrees with the envelope

        await consumers.finance_invoice_issued(envelope)

        after = await _order(session, org_id, order.id)
        assert after.invoice_id is None
