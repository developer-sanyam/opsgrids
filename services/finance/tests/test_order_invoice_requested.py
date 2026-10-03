"""Fulfilment F4 (finance slice): `operations.orders.order.invoice_requested` → an invoice, or a
refusal — ADR-0024 §1/§4.

Mirrors `test_filing_transaction_boundary.py`'s shape for exercising a consumer directly (no
broker in these tests: the envelope is built by hand and handed straight to the handler), and
`test_concurrency.py`'s shape for the one test that needs two genuinely racing connections.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app import consumers, services
from app.config import settings
from app.models import SCHEMA, AuditLog, Invoice, OutboxEvent, ProcessedEvent
from platform_core.db import (
    TENANT_GUC,
    dispose_engine,
    dispose_outbox_engine,
    init_engine,
    init_outbox_engine,
)
from tests.helpers import admin_ctx

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _engine() -> Any:
    """`invoice_requested_from_order` opens its own `session_scope`, independent of the fixture
    `session` every assertion in this file otherwise uses — same reason and same fixture as
    `test_filing_transaction_boundary.py`'s own `_engine`: the process engine has to exist for
    that to work, and the app lifespan is what does it in production."""
    cfg = settings()
    init_engine(cfg)
    init_outbox_engine(cfg)
    yield
    await dispose_outbox_engine()
    await dispose_engine()


SELLER = "27AABCA9012D1Z5"  # Maharashtra ("27")
BUYER_SAME_STATE = "27AAECO9012H1ZG"  # also Maharashtra — intra-state, CGST+SGST

#: One line, 18 % intra-state: subtotal 1,00,000 + tax 18,000 (9,000 CGST + 9,000 SGST) = 1,18,000.
LINES = [
    {
        "description": "Order item",
        "quantity": 2,
        "unit_price_minor": 50_000,
        "line_amount_minor": 100_000,
    }
]
SUBTOTAL = 100_000
TAX = 18_000
TOTAL = 118_000


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


async def _org_session(org: uuid.UUID) -> tuple[AsyncSession, AsyncEngine]:
    """A second, independent connection bound to `org` — same shape as
    `test_concurrency.py`'s own helper: the race needs two real sessions, not the one shared
    fixture session everything else in this suite uses."""
    engine = create_async_engine(settings().app_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    session = maker()

    @event.listens_for(session.sync_session, "after_begin")
    def _bind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
        connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org}', true)")

    return session, engine


async def _register(session: AsyncSession, ctx, gstin: str = SELLER) -> None:
    from app.schemas import GstRegistrationUpsert

    await services.set_gst_registration(
        session, ctx, GstRegistrationUpsert(gstin=gstin, legal_name="OpsGrids Industries Pvt Ltd")
    )


async def _customer(session: AsyncSession, ctx, *, gstin: str = BUYER_SAME_STATE):
    from app.schemas import CustomerCreate

    return await services.create_customer(
        session,
        ctx,
        CustomerCreate(
            name="Order Customer",
            contact_name="AP Desk",
            email="ap@example.in",
            gstin=gstin,
            city="Pune",
            state="Maharashtra",
        ),
    )


def _envelope(
    *,
    organization_id: uuid.UUID,
    order_id: uuid.UUID,
    customer_id: uuid.UUID,
    event_id: uuid.UUID | None = None,
    payload_organization_id: uuid.UUID | None = None,
    lines: list[dict[str, Any]] = LINES,
    expected_subtotal_minor: int = SUBTOTAL,
    expected_tax_minor: int = TAX,
    expected_total_minor: int = TOTAL,
    tax_rate_pct: float = 18,
    place_of_supply: str | None = "27",
    supply_type: str = "b2b",
    reverse_charge: bool = False,
) -> dict[str, Any]:
    return {
        "event_id": str(event_id or uuid.uuid4()),
        "event_type": consumers.EVT_INVOICE_REQUESTED,
        "organization_id": str(organization_id),
        "payload": {
            "organization_id": str(payload_organization_id or organization_id),
            "order_id": str(order_id),
            "order_number": "ORD-1",
            "customer_id": str(customer_id),
            "currency": "INR",
            "lines": lines,
            "tax_rate_pct": tax_rate_pct,
            "expected_subtotal_minor": expected_subtotal_minor,
            "expected_tax_minor": expected_tax_minor,
            "expected_total_minor": expected_total_minor,
            "place_of_supply": place_of_supply,
            "supply_type": supply_type,
            "reverse_charge": reverse_charge,
        },
    }


async def _outbox(session: AsyncSession, org_id: uuid.UUID, event_type: str) -> list[OutboxEvent]:
    stmt = select(OutboxEvent).where(
        OutboxEvent.organization_id == org_id, OutboxEvent.event_type == event_type
    )
    return list((await session.execute(stmt)).scalars().all())


class TestMatchingTotals:
    async def test_issues_the_invoice_with_audit_and_event(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx)
        order_id = uuid.uuid4()

        await consumers.invoice_requested_from_order(
            _envelope(organization_id=org_id, order_id=order_id, customer_id=customer.id)
        )

        async with session.begin():
            invoice = (
                await session.execute(select(Invoice).where(Invoice.order_id == order_id))
            ).scalar_one()
            issued = await _outbox(session, org_id, services.EVT_ORDER_INVOICE_ISSUED)
            audits = (
                (await session.execute(select(AuditLog).where(AuditLog.entity_id == invoice.id)))
                .scalars()
                .all()
            )

        assert invoice.document_type == "invoice"
        assert invoice.status == "sent"
        assert invoice.subtotal_minor == SUBTOTAL
        assert invoice.tax_minor == TAX
        assert invoice.total_minor == TOTAL
        assert invoice.cgst_minor == 9_000
        assert invoice.sgst_minor == 9_000
        assert invoice.customer_id == customer.id

        assert len(issued) == 1
        assert issued[0].payload == {
            "organization_id": str(org_id),
            "order_id": str(order_id),
            "invoice_id": str(invoice.id),
            "invoice_number": invoice.number,
            "total_minor": TOTAL,
        }

        assert len(audits) == 1
        assert audits[0].action == "invoice.issued"
        assert audits[0].after["order_id"] == str(order_id)
        assert audits[0].after["total_minor"] == TOTAL


class TestMismatchedTotals:
    async def test_rejects_without_raising_and_creates_no_invoice(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx)
        order_id = uuid.uuid4()

        # A paisa short of the truth on every one of the three totals.
        await consumers.invoice_requested_from_order(
            _envelope(
                organization_id=org_id,
                order_id=order_id,
                customer_id=customer.id,
                expected_subtotal_minor=SUBTOTAL,
                expected_tax_minor=TAX - 1,
                expected_total_minor=TOTAL - 1,
            )
        )

        async with session.begin():
            invoices = (
                (await session.execute(select(Invoice).where(Invoice.order_id == order_id)))
                .scalars()
                .all()
            )
            rejected = await _outbox(session, org_id, services.EVT_ORDER_INVOICE_REQUEST_REJECTED)

        assert invoices == []
        assert len(rejected) == 1
        payload = rejected[0].payload
        assert payload["reason"] == "tax_mismatch"
        assert payload["order_id"] == str(order_id)
        assert payload["expected_tax_minor"] == TAX - 1
        assert payload["actual_tax_minor"] == TAX
        assert payload["expected_total_minor"] == TOTAL - 1
        assert payload["actual_total_minor"] == TOTAL


class TestUnknownCustomer:
    async def test_rejected_the_same_way_a_mismatch_is(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
        order_id = uuid.uuid4()

        await consumers.invoice_requested_from_order(
            _envelope(organization_id=org_id, order_id=order_id, customer_id=uuid.uuid4())
        )

        async with session.begin():
            invoices = (
                (await session.execute(select(Invoice).where(Invoice.order_id == order_id)))
                .scalars()
                .all()
            )
            rejected = await _outbox(session, org_id, services.EVT_ORDER_INVOICE_REQUEST_REJECTED)

        assert invoices == []
        assert len(rejected) == 1
        assert rejected[0].payload["reason"] == "unknown_customer"


class TestRedeliveredEventId:
    async def test_is_a_no_op(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx)
        order_id = uuid.uuid4()
        event_id = uuid.uuid4()

        envelope = _envelope(
            organization_id=org_id, order_id=order_id, customer_id=customer.id, event_id=event_id
        )
        await consumers.invoice_requested_from_order(envelope)
        await consumers.invoice_requested_from_order(envelope)  # the exact same delivery, again

        async with session.begin():
            invoices = (
                (await session.execute(select(Invoice).where(Invoice.order_id == order_id)))
                .scalars()
                .all()
            )
            processed = (
                (
                    await session.execute(
                        select(ProcessedEvent).where(ProcessedEvent.event_id == event_id)
                    )
                )
                .scalars()
                .all()
            )
        assert len(invoices) == 1
        assert len(processed) == 1


class TestAlreadyInvoicedOrder:
    async def test_a_second_distinct_request_for_the_same_order_is_a_silent_no_op(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Not a redelivery — a *different* `event_id` for an order that already has its
        invoice (the ordinary path through `selectors.invoice_exists_for_order`, not the race).
        No second invoice, and — unlike a tax mismatch — no rejection event either: this is the
        benign short-circuit the spec asks to mirror `EVT_INVOICE_FINALIZED`'s
        `nothing_to_collect` branch for."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx)
        order_id = uuid.uuid4()

        await consumers.invoice_requested_from_order(
            _envelope(organization_id=org_id, order_id=order_id, customer_id=customer.id)
        )
        await consumers.invoice_requested_from_order(
            _envelope(organization_id=org_id, order_id=order_id, customer_id=customer.id)
        )

        async with session.begin():
            invoices = (
                (await session.execute(select(Invoice).where(Invoice.order_id == order_id)))
                .scalars()
                .all()
            )
            rejected = await _outbox(session, org_id, services.EVT_ORDER_INVOICE_REQUEST_REJECTED)
        assert len(invoices) == 1
        assert rejected == []


class TestConcurrentRequestsForTheSameOrder:
    async def test_never_both_create_an_invoice(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The race the pre-check cannot see: two deliveries for the same order, far enough
        apart in the same test to be real concurrency, close enough that both pass
        `selectors.invoice_exists_for_order` before either has committed. Exercises
        `uq_invoices_org_order_id` and the `SAVEPOINT` around it directly, at the
        `services.evaluate_order_invoice_request` level — the same level
        `test_concurrency.py` tests every other racing write at."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx)
        customer_id = customer.id
        order_id = uuid.uuid4()

        def _lines() -> list[services.OrderInvoiceLineIn]:
            return [
                services.OrderInvoiceLineIn(
                    description="Order item",
                    quantity=2,
                    unit_price_minor=50_000,
                    line_amount_minor=SUBTOTAL,
                )
            ]

        async def _request(session: AsyncSession) -> services.OrderInvoiceOutcome:
            request_ctx = services.service_ctx(org_id)
            async with session.begin():
                return await services.evaluate_order_invoice_request(
                    session,
                    request_ctx,
                    order_id=order_id,
                    order_number="ORD-RACE-1",
                    customer_id=customer_id,
                    currency="INR",
                    lines=_lines(),
                    tax_rate_pct=18,
                    expected_subtotal_minor=SUBTOTAL,
                    expected_tax_minor=TAX,
                    expected_total_minor=TOTAL,
                    place_of_supply="27",
                    supply_type="b2b",
                    reverse_charge=False,
                )

        s1, e1 = await _org_session(org_id)
        s2, e2 = await _org_session(org_id)
        try:
            results = await asyncio.gather(_request(s1), _request(s2), return_exceptions=True)
        finally:
            await s1.close()
            await s2.close()
            await e1.dispose()
            await e2.dispose()

        for result in results:
            if isinstance(result, BaseException):
                raise result
        outcomes = sorted(r.outcome for r in results)  # type: ignore[union-attr]
        assert outcomes == ["duplicate", "issued"], results

        async with session.begin():
            invoices = (
                (await session.execute(select(Invoice).where(Invoice.order_id == order_id)))
                .scalars()
                .all()
            )
        assert len(invoices) == 1


class TestTenantIsolation:
    async def test_the_guc_is_bound_from_the_envelope_per_message(
        self, session: AsyncSession, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """An envelope naming a *different* organization must never see, or bill, `org_id`'s own
        customer. If the tenant GUC were bound once for the process rather than per message, or
        leaked from an earlier call, this request would wrongly find `org_id`'s customer under
        `other_org`'s session and raise an invoice against it."""
        other_org = uuid.uuid4()
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx)  # belongs to org_id, not other_org

        await consumers.invoice_requested_from_order(
            _envelope(organization_id=other_org, order_id=uuid.uuid4(), customer_id=customer.id)
        )

        async with session.begin():
            # org_id's own view: nothing was created here, and its customer is untouched.
            own_invoices = (
                (await session.execute(select(Invoice).where(Invoice.organization_id == org_id)))
                .scalars()
                .all()
            )
        assert own_invoices == []

        async with admin_session.begin():
            other_invoices = (
                (
                    await admin_session.execute(
                        select(Invoice).where(Invoice.organization_id == other_org)
                    )
                )
                .scalars()
                .all()
            )
            other_rejections = (
                (
                    await admin_session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.organization_id == other_org,
                            OutboxEvent.event_type == services.EVT_ORDER_INVOICE_REQUEST_REJECTED,
                        )
                    )
                )
                .scalars()
                .all()
            )
        # `other_org`'s GUC made `org_id`'s customer invisible, exactly as row-level security
        # should — so this reads as an unknown customer, never as a cross-tenant invoice.
        assert other_invoices == []
        assert len(other_rejections) == 1
        assert other_rejections[0].payload["reason"] == "unknown_customer"

    async def test_an_envelope_payload_organization_mismatch_is_refused_not_trusted(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Copied from `file_requested_invoice`'s own guard: the envelope's `organization_id` is
        a column its producer's row-level security enforced; the payload's is JSON this consumer
        cannot prove nothing altered. They must agree, or the message is refused rather than
        bound to whichever one looks more convenient."""
        event_id = uuid.uuid4()
        envelope = _envelope(
            organization_id=org_id,
            order_id=uuid.uuid4(),
            customer_id=uuid.uuid4(),
            event_id=event_id,
            payload_organization_id=uuid.uuid4(),  # disagrees with the envelope's own
        )

        await consumers.invoice_requested_from_order(envelope)  # must not raise

        async with session.begin():
            processed = (
                (
                    await session.execute(
                        select(ProcessedEvent).where(ProcessedEvent.event_id == event_id)
                    )
                )
                .scalars()
                .all()
            )
            invoices = (await session.execute(select(Invoice))).scalars().all()
        # Marked processed (so a redelivery of the same malformed message is also a no-op) but
        # nothing was ever created under either organization.
        assert len(processed) == 1
        assert invoices == []
