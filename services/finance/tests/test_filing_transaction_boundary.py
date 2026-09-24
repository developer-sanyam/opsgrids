"""D3: the IRP call must not run while a database transaction is open.

Filing an e-invoice used to run `client.register(...)` inside the one transaction that read the
invoice, decided to file it, and appended the ledger row — the manual route's request transaction,
or the consumer's `session_scope`. These tests prove the restructured shape instead: **claim**
(`services.claim_manual_filing` / `claim_autofile_attempt`, no portal call), **call**
(`services.call_irp_register`, no session at all), **record** (`services.record_manual_filing` /
`record_autofile_result`). A process that dies between the call and the record leaves no filing
row behind — nothing was ever written for the attempt — and `app/cron.py`'s filing-reclaim sweep
is what notices and resumes it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text

from app import selectors, services
from app.config import settings
from app.consumers import file_requested_invoice
from app.cron import FilingReclaimSweep
from app.deps import get_irp_client
from app.irp.base import IrpAck, IrpInvoicePayload
from app.irp.stub import StubIrpClient
from app.main import app
from app.models import OutboxEvent
from platform_core.db import (
    dispose_engine,
    dispose_outbox_engine,
    get_outbox_sessionmaker,
    init_engine,
    init_outbox_engine,
    session_scope,
)
from tests.helpers import admin_ctx, service_token_headers

pytestmark = pytest.mark.integration

SELLER = "27AABCA9012D1Z5"
BUYER_SAME_STATE = "27AAECO9012H1ZG"
FILINGS = "/internal/v1/einvoice/filings"


@pytest.fixture(autouse=True)
async def _engine() -> Any:
    """The consumer and the sweep both open their own `session_scope`, which needs the process
    engine initialised — the app lifespan does this in production. The sweep's discovery also
    needs the **outbox** engine: without it, `get_outbox_sessionmaker` silently falls back to the
    app role, which sees zero rows under a tenant policy with no organization bound — exactly the
    silent-stall shape ADR-0002 warns every sweep in this platform about."""
    cfg = settings()
    init_engine(cfg)
    init_outbox_engine(cfg)
    yield
    await dispose_outbox_engine()
    await dispose_engine()


async def _register(session, ctx, gstin: str = SELLER) -> None:
    from app.schemas import GstRegistrationUpsert

    await services.set_gst_registration(
        session, ctx, GstRegistrationUpsert(gstin=gstin, legal_name="OpsGrids Industries Pvt Ltd")
    )


async def _customer(session, ctx):
    from app.schemas import CustomerCreate

    return await services.create_customer(
        session,
        ctx,
        CustomerCreate(
            name="Buyer",
            contact_name="Contact",
            email="ap@example.in",
            gstin=BUYER_SAME_STATE,
            city="Pune",
            state="Maharashtra",
        ),
    )


async def _finalize(session, ctx, number: str):
    from app.schemas import InvoiceCreate, InvoiceLineCreate

    async with session.begin():
        await _register(session, ctx)
        customer = await _customer(session, ctx)
        return await services.create_invoice(
            session,
            ctx,
            InvoiceCreate(
                number=number,
                customer_id=customer.id,
                issued_date=datetime(2026, 7, 1, tzinfo=UTC).date(),
                due_date=datetime(2026, 7, 31, tzinfo=UTC).date(),
                tax_rate_pct=18,
                lines=[
                    InvoiceLineCreate(
                        description="Cotton yarn",
                        quantity=100,
                        unit_price_minor=25_000,
                        hsn_sac="520512",
                    )
                ],
            ),
        )


def _envelope(row: OutboxEvent) -> dict[str, Any]:
    return {
        "event_id": str(row.id),
        "event_type": row.event_type,
        "organization_id": str(row.organization_id),
        "payload": row.payload,
    }


async def _requests(session, org_id: uuid.UUID) -> list[OutboxEvent]:
    stmt = (
        select(OutboxEvent)
        .where(
            OutboxEvent.organization_id == org_id,
            OutboxEvent.event_type == services.EVT_INVOICE_FILING_REQUESTED,
        )
        .order_by(OutboxEvent.created_at)
    )
    return list((await session.execute(stmt)).scalars().all())


class TestNoTransactionDuringTheIrpCall:
    async def test_the_internal_route_holds_no_transaction_while_the_portal_is_called(
        self,
        client,
        session,
        org_id: uuid.UUID,
    ) -> None:
        invoice = await _finalize(session, admin_ctx(org_id), "INV-NOTXN-1")

        seen_in_transaction: list[bool] = []
        real_register = StubIrpClient().register

        class _SpyClient:
            name = "spy-irp"

            def register(self, payload: IrpInvoicePayload) -> IrpAck:
                seen_in_transaction.append(session.in_transaction())
                return real_register(payload)

            def cancel(self, **kwargs: Any) -> Any:
                raise NotImplementedError

        app.dependency_overrides[get_irp_client] = lambda: _SpyClient()
        try:
            response = await client.post(
                FILINGS,
                headers=service_token_headers(),
                json={"organization_id": str(org_id), "invoice_id": str(invoice.id)},
            )
        finally:
            app.dependency_overrides.pop(get_irp_client, None)
        assert response.status_code == 201, response.text
        assert seen_in_transaction == [False], (
            "the portal was called while a transaction was open on the request's session"
        )

    async def test_the_consumer_holds_no_transaction_while_the_portal_is_called(
        self, session, org_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        invoice = await _finalize(session, admin_ctx(org_id), "INV-NOTXN-2")
        async with session.begin():
            requests = await _requests(session, org_id)
        assert len(requests) == 1
        envelope = _envelope(requests[0])

        open_scopes = 0
        real_register = StubIrpClient().register

        from contextlib import asynccontextmanager

        import app.consumers as consumers_module

        @asynccontextmanager
        async def _tracking_session_scope(organization_id: uuid.UUID | None = None):
            nonlocal open_scopes
            open_scopes += 1
            try:
                async with session_scope(organization_id=organization_id) as s:
                    yield s
            finally:
                open_scopes -= 1

        monkeypatch.setattr(consumers_module, "session_scope", _tracking_session_scope)

        seen_open_scopes: list[int] = []

        class _SpyClient:
            name = "spy-irp"

            def register(self, payload: IrpInvoicePayload) -> IrpAck:
                seen_open_scopes.append(open_scopes)
                return real_register(payload)

            def cancel(self, **kwargs: Any) -> Any:
                raise NotImplementedError

        monkeypatch.setattr(consumers_module, "get_irp_client", lambda: _SpyClient())

        await file_requested_invoice(envelope)

        assert seen_open_scopes == [0], (
            "the portal was called while a consumer session_scope transaction was open"
        )

        async with session.begin():
            filings = await selectors.filings_for_invoice(session, org_id, invoice.id)
        assert filings and filings[0].status == "filed"


class TestCrashBetweenCallAndRecord:
    async def test_an_invoice_with_no_filing_row_at_all_is_resumed_by_the_sweep(
        self, session, admin_session, org_id: uuid.UUID
    ) -> None:
        """Simulate the crash directly: claim and call the portal, but never call
        `record_autofile_result` — exactly what a process death between the two leaves behind:
        no row at all. The sweep must find the invoice, re-arm the queue, and delivering that
        message must file it for real."""
        ctx = admin_ctx(org_id)
        invoice = await _finalize(session, ctx, "INV-CRASH-1")

        async with session.begin():
            claim = await services.claim_autofile_attempt(session, ctx, invoice.id, attempt=1)
        assert claim.outcome is None
        assert claim.irp_payload is not None
        services.call_irp_register(StubIrpClient(), claim.irp_payload)  # the crashed call

        async with session.begin():
            filings = await selectors.filings_for_invoice(session, org_id, invoice.id)
        assert filings == [], "the claim must not have written a row"
        # The finalize request was delivered (the consumer's claim above is what took it), so the
        # relay had published it; a request still queued would leave the invoice to its delivery.
        async with admin_session.begin():
            await admin_session.execute(
                text("UPDATE finance.outbox SET published_at = now() WHERE organization_id = :o"),
                {"o": org_id},
            )

        sweep = FilingReclaimSweep(
            discovery_factory=get_outbox_sessionmaker(),
            work_scope=lambda organization_id: session_scope(organization_id=organization_id),
            reclaim_after=timedelta(seconds=0),
        )
        report = await sweep.tick(now=datetime.now(UTC) + timedelta(minutes=10))
        assert report.considered == 1
        assert report.resumed == 1
        assert report.failed == 0

        async with session.begin():
            requests = await _requests(session, org_id)
        # One from `_finalize` (never delivered in this test) and one the sweep just staged.
        assert len(requests) == 2
        envelope = _envelope(requests[-1])
        await file_requested_invoice(envelope)

        async with session.begin():
            filings = await selectors.filings_for_invoice(session, org_id, invoice.id)
        assert filings and filings[0].status == "filed"

    async def test_a_healthy_in_flight_invoice_is_left_alone(
        self, session, org_id: uuid.UUID
    ) -> None:
        """The poisoned-step guard: an invoice finalized moments ago has no filing row yet
        either, and must not be reclaimed just because its own first attempt has not landed."""
        ctx = admin_ctx(org_id)
        await _finalize(session, ctx, "INV-CRASH-2")

        sweep = FilingReclaimSweep(
            discovery_factory=get_outbox_sessionmaker(),
            work_scope=lambda organization_id: session_scope(organization_id=organization_id),
            reclaim_after=timedelta(minutes=5),
        )
        report = await sweep.tick()
        assert report.considered == 0
        assert report.resumed == 0

    async def test_a_filed_invoice_is_never_reclaimed(self, session, org_id: uuid.UUID) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _finalize(session, ctx, "INV-CRASH-3")
        async with session.begin():
            requests = await _requests(session, org_id)
        envelope = _envelope(requests[0])
        await file_requested_invoice(envelope)

        async with session.begin():
            filings = await selectors.filings_for_invoice(session, org_id, invoice.id)
        assert filings and filings[0].status == "filed"

        sweep = FilingReclaimSweep(
            discovery_factory=get_outbox_sessionmaker(),
            work_scope=lambda organization_id: session_scope(organization_id=organization_id),
            reclaim_after=timedelta(seconds=0),
        )
        report = await sweep.tick(now=datetime.now(UTC) + timedelta(minutes=10))
        assert report.considered == 0
