"""D3, round 2: no compliance-portal call runs while a database transaction is open.

`test_filing_transaction_boundary.py` proves it for IRN registration. This module proves it for
the four calls round 1 left on the static guard's allow-list, and for the reclaim sweep that
finishes whatever a crash between a call and its record leaves behind:

* `POST /internal/v1/einvoice/filings:cancel` — claim a `cancelling` row (reason, actor,
  audited), call `client.cancel` with no transaction, record. The portal's "already cancelled"
  is success-by-fetch.
* `POST /internal/v1/eway/bills` — claim a `submitting` row carrying the transport facts, call
  `client.generate` with no transaction under the (document, transporter/vehicle part)
  idempotency key, record. "Already generated" is success-by-lookup.
* `POST /internal/v1/eway/bills:cancel` — claim a `cancelling` row, call, record.
* `POST /internal/v1/ims/sync` — fetch the period with no transaction, then ingest (idempotent on
  the portal's record key, enforced by the database) and reconcile in one short transaction.

The sweep tests simulate the crash the only honest way: run the claim and the call, and never
the record — exactly what a process death between the two leaves.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from app import domain, selectors, services
from app.config import settings
from app.consumers import file_requested_invoice
from app.cron import FilingReclaimSweep
from app.deps import get_eway_client, get_ims_client, get_irp_client
from app.eway.base import (
    EwayAck,
    EwayAlreadyCancelled,
    EwayAlreadyGenerated,
    EwayCancelAck,
    EwayPayload,
    EwayUnavailable,
)
from app.eway.stub import StubEwayClient
from app.ims.base import ImsSupplierDocument
from app.ims.stub import StubImsClient
from app.irp.base import IrpAlreadyCancelled, IrpCancelAck, IrpUnavailable
from app.irp.stub import StubIrpClient
from app.main import app
from app.models import AuditLog, EwayBill, OutboxEvent
from app.schemas import (
    CancelEwayIn,
    CancelFilingIn,
    CustomerCreate,
    FileInvoiceIn,
    GenerateEwayIn,
    GstRegistrationUpsert,
    InvoiceCreate,
    InvoiceLineCreate,
)
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
CANCEL_FILING = "/internal/v1/einvoice/filings:cancel"
BILLS = "/internal/v1/eway/bills"
CANCEL_BILL = "/internal/v1/eway/bills:cancel"
IMS_SYNC = "/internal/v1/ims/sync"
PERIOD = "2026-08"

#: Far enough ahead that every claim written "now" is past its first re-drive deadline.
LATER = timedelta(hours=1)


@pytest.fixture(autouse=True)
async def _engine() -> Any:
    """The sweep opens its own `session_scope` and its discovery needs the **outbox** engine —
    without it `get_outbox_sessionmaker` falls back to the app role, which sees nothing under a
    tenant policy with no organization bound (the silent stall ADR-0002 warns about)."""
    cfg = settings()
    init_engine(cfg)
    init_outbox_engine(cfg)
    yield
    await dispose_outbox_engine()
    await dispose_engine()


# --------------------------------------------------------------------- fixtures


async def _consignment(session, ctx, number: str) -> Any:
    """A registered seller and one finalized B2B invoice worth 1,18,000 rupees — reportable for
    an IRN and over the e-Way threshold."""
    async with session.begin():
        await services.set_gst_registration(
            session,
            ctx,
            GstRegistrationUpsert(gstin=SELLER, legal_name="OpsGrids Industries Pvt Ltd"),
        )
        customer = await services.create_customer(
            session,
            ctx,
            CustomerCreate(
                name="Consignee",
                contact_name="Contact",
                email="ap@example.in",
                gstin=BUYER_SAME_STATE,
                city="Pune",
                state="Maharashtra",
            ),
        )
        return await services.create_invoice(
            session,
            ctx,
            InvoiceCreate(
                number=number,
                customer_id=customer.id,
                issued_date=date(2026, 8, 12),
                due_date=date(2026, 9, 11),
                tax_rate_pct=18,
                lines=[
                    InvoiceLineCreate(
                        description="Hot-rolled coil",
                        quantity=1,
                        unit_price_minor=100_000_00,
                        hsn_sac="72085110",
                    )
                ],
            ),
        )


async def _filed(session, ctx, org_id: uuid.UUID, number: str) -> Any:
    invoice = await _consignment(session, ctx, number)
    async with session.begin():
        filing = await services.file_invoice(
            session,
            ctx,
            StubIrpClient(),
            FileInvoiceIn(organization_id=org_id, invoice_id=invoice.id),
        )
    assert filing.status == "filed"
    return invoice


def _generate_in(org_id: uuid.UUID, invoice_id: uuid.UUID, **over: Any) -> GenerateEwayIn:
    fields: dict[str, Any] = {
        "organization_id": org_id,
        "invoice_id": invoice_id,
        "distance_km": 150,
        "transport_mode": "road",
        "vehicle_no": "MH12AB1234",
    }
    fields.update(over)
    return GenerateEwayIn(**fields)


def _sweep(**over: Any) -> FilingReclaimSweep:
    kwargs: dict[str, Any] = {
        "discovery_factory": get_outbox_sessionmaker(),
        "work_scope": lambda organization_id: session_scope(organization_id=organization_id),
    }
    kwargs.update(over)
    return FilingReclaimSweep(**kwargs)


async def _filings(session, org_id: uuid.UUID, invoice_id: uuid.UUID) -> list[str]:
    async with session.begin():
        rows = await selectors.filings_for_invoice(session, org_id, invoice_id)
    return [row.status for row in rows]


async def _bills(session, org_id: uuid.UUID, invoice_id: uuid.UUID) -> list[EwayBill]:
    async with session.begin():
        return await selectors.eway_bills_for_invoice(session, org_id, invoice_id)


async def _events(session, org_id: uuid.UUID, event_type: str) -> list[OutboxEvent]:
    async with session.begin():
        stmt = (
            select(OutboxEvent)
            .where(OutboxEvent.organization_id == org_id, OutboxEvent.event_type == event_type)
            .order_by(OutboxEvent.created_at)
        )
        return list((await session.execute(stmt)).scalars().all())


class _RememberingIrp:
    """An IRP that holds state the way the real one does: a second cancellation of an IRN it has
    already withdrawn is answered "already cancelled", carrying the first withdrawal."""

    name = "remembering-irp"

    def __init__(self) -> None:
        self.cancelled: dict[str, IrpCancelAck] = {}
        self.calls = 0

    def register(self, payload: Any) -> Any:  # pragma: no cover - not exercised here
        return StubIrpClient().register(payload)

    def cancel(self, *, irn: str, reason_code: str, remark: str, at: datetime) -> IrpCancelAck:
        self.calls += 1
        if irn in self.cancelled:
            raise IrpAlreadyCancelled(self.cancelled[irn])
        ack = StubIrpClient().cancel(irn=irn, reason_code=reason_code, remark=remark, at=at)
        self.cancelled[irn] = ack
        return ack


class _DownIrp:
    name = "down-irp"

    def __init__(self) -> None:
        self.calls = 0

    def register(self, payload: Any) -> Any:
        raise IrpUnavailable("connection reset by peer")

    def cancel(self, **kwargs: Any) -> Any:
        self.calls += 1
        raise IrpUnavailable("connection reset by peer")


class _RememberingEway:
    """An e-Way portal idempotent by the platform's key, as the real one is by its own: a repeat
    of a generation it already issued is answered "already generated" with that bill, and a
    repeat withdrawal "already cancelled"."""

    name = "remembering-eway"

    def __init__(self) -> None:
        self.issued: dict[str, EwayAck] = {}
        self.cancelled: dict[str, EwayCancelAck] = {}
        self.keys: list[str] = []

    def generate(self, payload: EwayPayload) -> EwayAck:
        self.keys.append(payload.idempotency_key)
        if payload.idempotency_key in self.issued:
            raise EwayAlreadyGenerated(self.issued[payload.idempotency_key])
        ack = StubEwayClient().generate(payload)
        self.issued[payload.idempotency_key] = ack
        return ack

    def cancel(
        self, *, eway_bill_no: str, reason_code: str, remark: str, at: datetime
    ) -> EwayCancelAck:
        if eway_bill_no in self.cancelled:
            raise EwayAlreadyCancelled(self.cancelled[eway_bill_no])
        ack = StubEwayClient().cancel(
            eway_bill_no=eway_bill_no, reason_code=reason_code, remark=remark, at=at
        )
        self.cancelled[eway_bill_no] = ack
        return ack


class _DownEway:
    name = "down-eway"

    def generate(self, payload: EwayPayload) -> EwayAck:
        raise EwayUnavailable("connection reset by peer")

    def cancel(self, **kwargs: Any) -> EwayCancelAck:
        raise EwayUnavailable("connection reset by peer")


# --------------------------------------------------------------------- #6 cancelling an IRN


class TestIrnCancellation:
    async def test_the_route_holds_no_transaction_while_the_portal_is_called(
        self, client, session, org_id: uuid.UUID
    ) -> None:
        invoice = await _filed(session, admin_ctx(org_id), org_id, "INV-D3-C1")
        seen: list[bool] = []

        class _Spy(_RememberingIrp):
            def cancel(self, **kwargs: Any) -> IrpCancelAck:
                seen.append(session.in_transaction())
                return super().cancel(**kwargs)

        app.dependency_overrides[get_irp_client] = lambda: _Spy()
        try:
            res = await client.post(
                CANCEL_FILING,
                headers=service_token_headers(),
                json={
                    "organization_id": str(org_id),
                    "invoice_id": str(invoice.id),
                    "reason_code": "2",
                },
            )
        finally:
            app.dependency_overrides.pop(get_irp_client, None)
        assert res.status_code == 201, res.text
        assert res.json()["status"] == "cancelled"
        assert seen == [False], "the IRP was called while the request's transaction was open"

    async def test_the_claim_is_audited_with_its_reason_and_actor(
        self, session, org_id: uuid.UUID
    ) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _filed(session, ctx, org_id, "INV-D3-C2")
        async with session.begin():
            claimed = await services.claim_irn_cancellation(
                session,
                ctx,
                CancelFilingIn(organization_id=org_id, invoice_id=invoice.id, reason_code="3"),
            )
        assert claimed.claim.status == "cancelling"
        assert claimed.claim.error_code == "3"
        assert claimed.claim.created_by == str(ctx.principal.sub)
        async with session.begin():
            audit = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.organization_id == org_id,
                        AuditLog.action == "einvoice.cancel_requested",
                    )
                )
            ).scalar_one()
        assert str(audit.actor_id) == str(ctx.principal.sub)
        assert audit.after["reason_code"] == "3"
        assert audit.after["irn"] == claimed.facts.irn

    async def test_an_outage_answers_with_the_claim_and_the_sweep_finishes_it(
        self, client, session, org_id: uuid.UUID
    ) -> None:
        invoice = await _filed(session, admin_ctx(org_id), org_id, "INV-D3-C3")
        # Read once: the 409 below rolls the shared test session back, which expires `invoice`.
        invoice_id = invoice.id
        app.dependency_overrides[get_irp_client] = lambda: _DownIrp()
        try:
            res = await client.post(
                CANCEL_FILING,
                headers=service_token_headers(),
                json={
                    "organization_id": str(org_id),
                    "invoice_id": str(invoice_id),
                    "reason_code": "2",
                },
            )
        finally:
            app.dependency_overrides.pop(get_irp_client, None)
        assert res.status_code == 201, res.text
        assert res.json()["status"] == "cancelling"

        # A second request while the first is owed is a conflict, not a second claim.
        again = await client.post(
            CANCEL_FILING,
            headers=service_token_headers(),
            json={
                "organization_id": str(org_id),
                "invoice_id": str(invoice_id),
                "reason_code": "2",
            },
        )
        assert again.status_code == 409

        portal = _RememberingIrp()
        report = await _sweep(irp_client_factory=lambda: portal).tick(now=datetime.now(UTC) + LATER)
        assert report.failed == 0
        assert portal.calls == 1
        assert await _filings(session, org_id, invoice_id) == [
            "cancelled",
            "cancelling",
            "cancelling",
            "filed",
        ]
        cancelled = await _events(session, org_id, services.EVT_INVOICE_FILING_CANCELLED)
        assert len(cancelled) == 1

    async def test_a_crash_after_the_portal_withdrew_it_is_recorded_as_cancelled(
        self, session, org_id: uuid.UUID
    ) -> None:
        """Claim, call, and **never record** — the process died with the portal's answer in hand.
        The resumed call is answered "already cancelled", which is success-by-fetch."""
        ctx = admin_ctx(org_id)
        invoice = await _filed(session, ctx, org_id, "INV-D3-C4")
        portal = _RememberingIrp()
        async with session.begin():
            claimed = await services.claim_irn_cancellation(
                session,
                ctx,
                CancelFilingIn(organization_id=org_id, invoice_id=invoice.id, reason_code="1"),
            )
        crashed = services.call_irp_cancel(portal, claimed.facts)
        assert crashed.kind == "cancelled"
        assert await _filings(session, org_id, invoice.id) == ["cancelling", "filed"]

        report = await _sweep(irp_client_factory=lambda: portal).tick(now=datetime.now(UTC) + LATER)
        assert report.failed == 0
        assert portal.calls == 2
        statuses = await _filings(session, org_id, invoice.id)
        assert statuses[0] == "cancelled"
        async with session.begin():
            newest = (await selectors.filings_for_invoice(session, org_id, invoice.id))[0]
        assert newest.error_code == "1"

    async def test_a_claim_nobody_can_finish_becomes_a_visible_failure(
        self, session, org_id: uuid.UUID
    ) -> None:
        """The poisoned-step guard, with the bounded backoff between drives: a tick before the
        next deadline does nothing, and after `MAX_COMPLIANCE_RESUMES` drives the claim becomes
        a terminal `failed` row carrying `retry_exhausted` that no later tick touches."""
        ctx = admin_ctx(org_id)
        invoice = await _filed(session, ctx, org_id, "INV-D3-C5")
        async with session.begin():
            await services.claim_irn_cancellation(
                session,
                ctx,
                CancelFilingIn(organization_id=org_id, invoice_id=invoice.id, reason_code="2"),
            )
        portal = _DownIrp()
        sweep = _sweep(irp_client_factory=lambda: portal)

        at = datetime.now(UTC) + LATER
        for drive in range(1, domain.MAX_COMPLIANCE_RESUMES):
            report = await sweep.tick(now=at)
            assert report.failed == 0
            assert portal.calls == drive
            # Inside the backoff for the claim just renewed: left alone.
            early = await sweep.tick(now=at + domain.resume_after(drive + 1) - timedelta(seconds=1))
            assert portal.calls == drive, "re-driven before its backoff had elapsed"
            assert early.failed == 0
            at += domain.resume_after(drive + 1) + timedelta(seconds=1)

        report = await sweep.tick(now=at)
        assert report.exhausted == 1
        assert portal.calls == domain.MAX_COMPLIANCE_RESUMES - 1
        async with session.begin():
            newest = (await selectors.filings_for_invoice(session, org_id, invoice.id))[0]
        assert newest.status == "failed"
        assert newest.error_code == domain.ERR_RETRY_EXHAUSTED

        after = await sweep.tick(now=at + timedelta(days=1))
        assert after.exhausted == 0
        assert portal.calls == domain.MAX_COMPLIANCE_RESUMES - 1

    async def test_two_recorders_of_one_claim_write_one_withdrawal(
        self, session, org_id: uuid.UUID
    ) -> None:
        """A request whose call outlived the sweep's lease and the sweep that resumed the same
        claim both reach the record step; the second finds the first's row."""
        ctx = admin_ctx(org_id)
        invoice = await _filed(session, ctx, org_id, "INV-D3-C6")
        async with session.begin():
            claimed = await services.claim_irn_cancellation(
                session,
                ctx,
                CancelFilingIn(organization_id=org_id, invoice_id=invoice.id, reason_code="2"),
            )
        outcome = services.call_irp_cancel(StubIrpClient(), claimed.facts)
        for _ in range(2):
            async with session.begin():
                await services.record_irn_cancellation(
                    session, ctx, claimed.facts, outcome, at=claimed.facts.at
                )
        assert await _filings(session, org_id, invoice.id) == ["cancelled", "cancelling", "filed"]
        assert len(await _events(session, org_id, services.EVT_INVOICE_FILING_CANCELLED)) == 1

    async def test_a_claim_a_request_is_holding_is_skipped_not_waited_on(
        self, session, org_id: uuid.UUID
    ) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _filed(session, ctx, org_id, "INV-D3-C7")
        async with session.begin():
            await services.claim_irn_cancellation(
                session,
                ctx,
                CancelFilingIn(organization_id=org_id, invoice_id=invoice.id, reason_code="2"),
            )
        portal = _RememberingIrp()
        async with session.begin():
            await selectors.get_invoice(session, org_id, invoice.id, for_update=True)
            report = await _sweep(irp_client_factory=lambda: portal).tick(
                now=datetime.now(UTC) + LATER
            )
        assert report.skipped >= 1
        assert portal.calls == 0


# --------------------------------------------------------------------- #7 raising an e-Way bill


class TestEwayGeneration:
    async def test_the_route_holds_no_transaction_while_the_portal_is_called(
        self, client, session, org_id: uuid.UUID
    ) -> None:
        invoice = await _consignment(session, admin_ctx(org_id), "INV-D3-E1")
        seen: list[bool] = []

        class _Spy(_RememberingEway):
            def generate(self, payload: EwayPayload) -> EwayAck:
                seen.append(session.in_transaction())
                return super().generate(payload)

        app.dependency_overrides[get_eway_client] = lambda: _Spy()
        try:
            res = await client.post(
                BILLS,
                headers=service_token_headers(),
                json=_generate_in(org_id, invoice.id).model_dump(mode="json"),
            )
        finally:
            app.dependency_overrides.pop(get_eway_client, None)
        assert res.status_code == 201, res.text
        assert res.json()["status"] == "generated"
        assert seen == [False], "the e-Way portal was called while a transaction was open"

    async def test_a_crash_after_the_portal_issued_the_bill_records_that_bill(
        self, session, org_id: uuid.UUID
    ) -> None:
        """Claim, call, never record. The sweep re-sends under the **same** idempotency key, the
        portal answers "already generated" with the bill it issued, and that bill — not a second
        one — is what the ledger records."""
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-D3-E2")
        portal = _RememberingEway()
        async with session.begin():
            facts = await services.claim_eway_generation(
                session, ctx, _generate_in(org_id, invoice.id)
            )
        crashed = services.call_eway_generate(portal, facts.payload)
        assert crashed.kind == "generated" and crashed.ack is not None
        assert [b.status for b in await _bills(session, org_id, invoice.id)] == ["submitting"]

        report = await _sweep(eway_client_factory=lambda: portal).tick(
            now=datetime.now(UTC) + LATER
        )
        assert report.failed == 0
        assert len(portal.keys) == 2
        assert portal.keys[0] == portal.keys[1] == facts.payload.idempotency_key

        bills = await _bills(session, org_id, invoice.id)
        assert [b.status for b in bills] == ["generated", "submitting", "submitting"]
        assert bills[0].eway_bill_no == crashed.ack.eway_bill_no
        assert bills[0].valid_until == domain.eway_valid_until(
            crashed.ack.valid_from, bills[0].distance_km
        )
        assert len(await _events(session, org_id, services.EVT_EWAY_GENERATED)) == 1

    async def test_an_outage_is_pending_and_the_sweep_raises_it_later(
        self, client, session, org_id: uuid.UUID
    ) -> None:
        invoice = await _consignment(session, admin_ctx(org_id), "INV-D3-E3")
        app.dependency_overrides[get_eway_client] = lambda: _DownEway()
        try:
            res = await client.post(
                BILLS,
                headers=service_token_headers(),
                json=_generate_in(org_id, invoice.id).model_dump(mode="json"),
            )
        finally:
            app.dependency_overrides.pop(get_eway_client, None)
        assert res.status_code == 201, res.text
        assert res.json()["status"] == "pending"

        portal = _RememberingEway()
        report = await _sweep(eway_client_factory=lambda: portal).tick(
            now=datetime.now(UTC) + LATER
        )
        assert report.failed == 0
        bills = await _bills(session, org_id, invoice.id)
        assert [b.status for b in bills] == ["generated", "submitting", "pending", "submitting"]
        assert bills[0].vehicle_no == "MH12AB1234"

    async def test_a_generation_in_flight_refuses_a_second(
        self, client, session, org_id: uuid.UUID
    ) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-D3-E4")
        async with session.begin():
            await services.claim_eway_generation(session, ctx, _generate_in(org_id, invoice.id))
        res = await client.post(
            BILLS,
            headers=service_token_headers(),
            json=_generate_in(org_id, invoice.id, vehicle_no="MH12ZZ9999").model_dump(mode="json"),
        )
        assert res.status_code == 409
        assert "already in progress" in res.json()["detail"]

    async def test_a_generation_nobody_can_finish_becomes_a_visible_failure(
        self, session, org_id: uuid.UUID
    ) -> None:
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, "INV-D3-E5")
        async with session.begin():
            await services.generate_eway_bill(
                session, ctx, _DownEway(), _generate_in(org_id, invoice.id)
            )
        sweep = _sweep(eway_client_factory=lambda: _DownEway())
        at = datetime.now(UTC) + LATER
        exhausted = 0
        for _ in range(domain.MAX_COMPLIANCE_RESUMES + 2):
            report = await sweep.tick(now=at)
            assert report.failed == 0
            exhausted += report.exhausted
            at += domain.FILING_RETRY_CEILING + domain.FILING_RECLAIM_AFTER
        assert exhausted == 1
        bills = await _bills(session, org_id, invoice.id)
        assert bills[0].status == "failed"
        assert bills[0].error_code == domain.ERR_RETRY_EXHAUSTED
        assert sum(1 for b in bills if b.status == "submitting") == domain.MAX_COMPLIANCE_RESUMES
        failures = await _events(session, org_id, services.EVT_EWAY_FAILED)
        assert failures[-1].payload["retryable"] is False


class TestTheEwayIdempotencyKey:
    """Pure: the key a generation is idempotent by."""

    def _key(self, **over: Any) -> str:
        fields: dict[str, Any] = {
            "invoice_id": uuid.UUID(int=7),
            "generation": 1,
            "transport_mode": "road",
            "vehicle_no": "MH12AB1234",
            "transporter_id": None,
            "transport_doc_no": None,
            "transport_doc_date": None,
            "distance_km": 150,
            "over_dimensional": False,
        }
        fields.update(over)
        return domain.eway_idempotency_key(**fields)

    def test_the_same_consignment_and_part_b_is_one_key(self) -> None:
        assert self._key() == self._key()
        assert self._key(vehicle_no="mh12 ab1234") == self._key()

    def test_a_different_vehicle_is_a_different_request(self) -> None:
        assert self._key(vehicle_no="MH12ZZ9999") != self._key()
        assert self._key(transporter_id="27AABCT1234F1Z5") != self._key()

    def test_a_re_raised_bill_is_a_different_generation(self) -> None:
        assert self._key(generation=2) != self._key()

    def test_the_validity_inputs_are_part_of_the_request(self) -> None:
        """Distance and over-dimensional cargo are what validity is computed from; a corrected
        distance must not be answered with a bill raised for the old one."""
        assert self._key(distance_km=151) != self._key()
        assert self._key(over_dimensional=True) != self._key()

    def test_transport_references_compare_case_and_space_insensitively(self) -> None:
        assert self._key(transport_doc_no="lr 1234") == self._key(transport_doc_no="LR1234")
        assert self._key(transporter_id=" 27aabct1234f1z5") == self._key(
            transporter_id="27AABCT1234F1Z5"
        )

    def test_generations_are_one_based(self) -> None:
        with pytest.raises(ValueError):
            self._key(generation=0)


# --------------------------------------------------------------------- #8 withdrawing an e-Way bill


class TestEwayCancellation:
    async def _generated(self, session, org_id: uuid.UUID, number: str) -> Any:
        ctx = admin_ctx(org_id)
        invoice = await _consignment(session, ctx, number)
        async with session.begin():
            bill = await services.generate_eway_bill(
                session, ctx, StubEwayClient(), _generate_in(org_id, invoice.id)
            )
        assert bill.status == "generated"
        return invoice, bill

    async def test_the_route_holds_no_transaction_while_the_portal_is_called(
        self, client, session, org_id: uuid.UUID
    ) -> None:
        invoice, _ = await self._generated(session, org_id, "INV-D3-W1")
        seen: list[bool] = []

        class _Spy(_RememberingEway):
            def cancel(self, **kwargs: Any) -> EwayCancelAck:
                seen.append(session.in_transaction())
                return super().cancel(**kwargs)

        app.dependency_overrides[get_eway_client] = lambda: _Spy()
        try:
            res = await client.post(
                CANCEL_BILL,
                headers=service_token_headers(),
                json={
                    "organization_id": str(org_id),
                    "invoice_id": str(invoice.id),
                    "reason_code": "2",
                },
            )
        finally:
            app.dependency_overrides.pop(get_eway_client, None)
        assert res.status_code == 201, res.text
        assert res.json()["status"] == "cancelled"
        assert seen == [False]

    async def test_a_crash_after_the_portal_withdrew_it_is_recorded_as_cancelled(
        self, session, org_id: uuid.UUID
    ) -> None:
        ctx = admin_ctx(org_id)
        invoice, bill = await self._generated(session, org_id, "INV-D3-W2")
        portal = _RememberingEway()
        async with session.begin():
            facts = await services.claim_eway_cancellation(
                session,
                ctx,
                CancelEwayIn(organization_id=org_id, invoice_id=invoice.id, reason_code="3"),
            )
        assert facts.eway_bill_no == bill.eway_bill_no
        assert services.call_eway_cancel(portal, facts).kind == "cancelled"

        report = await _sweep(eway_client_factory=lambda: portal).tick(
            now=datetime.now(UTC) + LATER
        )
        assert report.failed == 0
        bills = await _bills(session, org_id, invoice.id)
        assert [b.status for b in bills] == [
            "cancelled",
            "cancelling",
            "cancelling",
            "generated",
            "submitting",
        ]
        assert bills[0].error_code == "3"
        events = await _events(session, org_id, services.EVT_EWAY_CANCELLED)
        assert [e.payload["eway_bill_no"] for e in events] == [bill.eway_bill_no]

    async def test_an_outage_answers_with_the_claim_and_the_sweep_finishes_it(
        self, client, session, org_id: uuid.UUID
    ) -> None:
        invoice, _ = await self._generated(session, org_id, "INV-D3-W3")
        app.dependency_overrides[get_eway_client] = lambda: _DownEway()
        try:
            res = await client.post(
                CANCEL_BILL,
                headers=service_token_headers(),
                json={
                    "organization_id": str(org_id),
                    "invoice_id": str(invoice.id),
                    "reason_code": "2",
                },
            )
        finally:
            app.dependency_overrides.pop(get_eway_client, None)
        assert res.status_code == 201, res.text
        assert res.json()["status"] == "cancelling"

        report = await _sweep(eway_client_factory=lambda: StubEwayClient()).tick(
            now=datetime.now(UTC) + LATER
        )
        assert report.failed == 0
        assert (await _bills(session, org_id, invoice.id))[0].status == "cancelled"


# --------------------------------------------------------------------- #9 syncing IMS


class TestImsSync:
    async def _registered(self, session, org_id: uuid.UUID) -> None:
        async with session.begin():
            await services.set_gst_registration(
                session,
                admin_ctx(org_id),
                GstRegistrationUpsert(gstin=SELLER, legal_name="OpsGrids Industries Pvt Ltd"),
            )

    async def test_the_route_holds_no_transaction_while_the_portal_is_fetched(
        self, client, session, org_id: uuid.UUID
    ) -> None:
        await self._registered(session, org_id)
        seen: list[bool] = []

        class _Spy:
            name = "spy-ims"

            def fetch(self, *, gstin: str, period: str) -> list[ImsSupplierDocument]:
                seen.append(session.in_transaction())
                return StubImsClient().fetch(gstin=gstin, period=period)

        app.dependency_overrides[get_ims_client] = lambda: _Spy()
        try:
            res = await client.post(
                IMS_SYNC,
                headers=service_token_headers(),
                json={"organization_id": str(org_id), "period": PERIOD},
            )
        finally:
            app.dependency_overrides.pop(get_ims_client, None)
        assert res.status_code == 200, res.text
        assert res.json()["created"] == res.json()["fetched"] > 0
        assert seen == [False], "IMS was fetched while a transaction was open"

    async def test_the_ingest_is_idempotent_on_the_portals_record_key(
        self, session, org_id: uuid.UUID
    ) -> None:
        """Recording the same fetch twice — and a fetch naming one record twice — stores each
        portal record once. Enforced by the database, not by a read the fetch has made stale."""
        await self._registered(session, org_id)
        ctx = admin_ctx(org_id)
        fetched = StubImsClient().fetch(gstin=SELLER, period=PERIOD)
        doubled = [*fetched, fetched[0]]
        async with session.begin():
            first = await services.record_ims_fetch(
                session, ctx, doubled, period=PERIOD, portal="stub-ims"
            )
        async with session.begin():
            second = await services.record_ims_fetch(
                session, ctx, fetched, period=PERIOD, portal="stub-ims"
            )
        assert first.created == len(fetched)
        assert second.created == 0
        async with session.begin():
            held = await selectors.all_ims_documents(session, org_id)
        assert len(held) == len(fetched)
        announced = await _events(session, org_id, services.EVT_IMS_DOCUMENT_INGESTED)
        assert [e.payload["source_ref"] for e in announced] == [d.source_ref for d in fetched]

    async def test_two_syncs_racing_one_period_both_succeed(
        self, org_id: uuid.UUID, session
    ) -> None:
        """The fetch now happens outside any transaction, so two syncs of one period overlapping
        is ordinary. The second insert waits on the first's uncommitted rows, then skips them —
        no unique-violation 500, no duplicates."""
        await self._registered(session, org_id)
        ctx = admin_ctx(org_id)
        fetched = StubImsClient().fetch(gstin=SELLER, period=PERIOD)
        results: list[int] = []

        async def _record(delay: float) -> None:
            async with session_scope(organization_id=org_id) as s:
                result = await services.record_ims_fetch(
                    s, ctx, fetched, period=PERIOD, portal="stub-ims"
                )
                await asyncio.sleep(delay)
                results.append(result.created)

        await asyncio.gather(_record(0.3), _record(0.0))
        assert sorted(results) == [0, len(fetched)]
        async with session.begin():
            assert len(await selectors.all_ims_documents(session, org_id)) == len(fetched)

    async def test_an_outage_is_a_503_and_leaves_nothing_behind(
        self, client, session, org_id: uuid.UUID
    ) -> None:
        from app.ims.base import ImsUnavailable

        await self._registered(session, org_id)

        class _Down:
            name = "down-ims"

            def fetch(self, *, gstin: str, period: str) -> list[ImsSupplierDocument]:
                raise ImsUnavailable("timed out")

        app.dependency_overrides[get_ims_client] = lambda: _Down()
        try:
            res = await client.post(
                IMS_SYNC,
                headers=service_token_headers(),
                json={"organization_id": str(org_id), "period": PERIOD},
            )
        finally:
            app.dependency_overrides.pop(get_ims_client, None)
        assert res.status_code == 503
        async with session.begin():
            assert await selectors.all_ims_documents(session, org_id) == []
            synced = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.organization_id == org_id, AuditLog.action == "ims.synced"
                    )
                )
            ).all()
        assert synced == []


# --------------------------------------------------------------------- the sweep's own contract


class TestStalePendingFilings:
    async def _pending(self, client, session, org_id: uuid.UUID, number: str) -> Any:
        invoice = await _consignment(session, admin_ctx(org_id), number)
        app.dependency_overrides[get_irp_client] = lambda: _DownIrp()
        try:
            res = await client.post(
                FILINGS,
                headers=service_token_headers(),
                json={"organization_id": str(org_id), "invoice_id": str(invoice.id)},
            )
        finally:
            app.dependency_overrides.pop(get_irp_client, None)
        assert res.json()["status"] == "pending"
        return invoice

    async def _reclaims(self, session, org_id: uuid.UUID) -> list[OutboxEvent]:
        return [
            e
            for e in await _events(session, org_id, services.EVT_INVOICE_FILING_REQUESTED)
            if e.payload["trigger"] == "reclaim"
        ]

    async def test_a_pending_row_whose_retry_was_lost_is_requeued_once_and_then_filed(
        self, client, session, org_id: uuid.UUID
    ) -> None:
        invoice = await self._pending(client, session, org_id, "INV-D3-P1")
        sweep = _sweep()

        # Inside the retry's own delay plus grace: the scheduled retry may still be coming.
        early = datetime.now(UTC) + domain.pending_redrive_after(1) - timedelta(seconds=30)
        await sweep.tick(now=early)
        assert await self._reclaims(session, org_id) == []

        later = datetime.now(UTC) + LATER
        await sweep.tick(now=later)
        reclaims = await self._reclaims(session, org_id)
        assert len(reclaims) == 1
        assert reclaims[0].payload["attempt"] == 2

        # The lease: the re-drive is queued and not yet answered, so the next tick leaves it.
        await sweep.tick(now=later)
        assert len(await self._reclaims(session, org_id)) == 1

        await file_requested_invoice(
            {
                "event_id": str(reclaims[0].id),
                "event_type": reclaims[0].event_type,
                "organization_id": str(reclaims[0].organization_id),
                "payload": reclaims[0].payload,
            }
        )
        assert (await _filings(session, org_id, invoice.id))[0] == "filed"

    async def test_the_lease_expires_if_the_requeued_request_was_lost(
        self, client, session, admin_session, org_id: uuid.UUID
    ) -> None:
        await self._pending(client, session, org_id, "INV-D3-P2")
        sweep = _sweep()
        later = datetime.now(UTC) + LATER
        await sweep.tick(now=later)
        assert len(await self._reclaims(session, org_id)) == 1

        async with admin_session.begin():
            await admin_session.execute(
                update(OutboxEvent)
                .where(OutboxEvent.organization_id == org_id)
                .values(created_at=text("now() - interval '1 hour'"))
            )
        await sweep.tick(now=later)
        assert len(await self._reclaims(session, org_id)) == 2


class TestDiscoveryIsColumnScoped:
    """The outbox role reads identifiers, statuses and timestamps on the compliance ledgers —
    never an amount, a GSTIN, an IRN or a vehicle number (`migrations/env.py`)."""

    @pytest.mark.parametrize(
        ("table", "column"),
        [
            ("invoices", "total_minor"),
            ("invoices", "buyer_gstin"),
            ("einvoice_filings", "irn"),
            ("einvoice_filings", "error"),
            ("eway_bills", "vehicle_no"),
            ("eway_bills", "eway_bill_no"),
        ],
    )
    async def test_content_columns_are_refused(self, table: str, column: str) -> None:
        async with get_outbox_sessionmaker()() as s:
            with pytest.raises(DBAPIError, match="permission denied"):
                async with s.begin():
                    # Both names come from the parametrize list above, never from input.
                    await s.execute(text(f'SELECT {column} FROM finance."{table}" LIMIT 1'))  # noqa: S608

    async def test_the_discovery_columns_are_readable(self) -> None:
        now = datetime.now(UTC)
        async with get_outbox_sessionmaker()() as s, s.begin():
            await selectors.due_filing_reclaims(s, older_than=now)
            await selectors.due_stale_filings(s, floors={"pending": now, "cancelling": now})
            await selectors.due_stale_eway(
                s, floors={"submitting": now, "pending": now, "cancelling": now}
            )


class TestLeadingClaimAttempts:
    def test_a_cancellation_chain_counts_its_claims(self) -> None:
        assert (
            domain.leading_claim_attempts(
                ["cancelling", "cancelling", "filed"],
                claim="cancelling",
                chain=frozenset({"cancelling"}),
            )
            == 2
        )

    def test_a_generation_chain_counts_only_its_claims(self) -> None:
        chain = frozenset({"submitting", "pending"})
        statuses = ["pending", "submitting", "pending", "submitting", "cancelled", "submitting"]
        assert domain.leading_claim_attempts(statuses, claim="submitting", chain=chain) == 2

    def test_the_backoff_grows_and_is_capped(self) -> None:
        waits = [domain.resume_after(n) for n in range(1, 10)]
        assert waits[0] == domain.FILING_RECLAIM_AFTER
        assert waits == sorted(waits)
        assert waits[-1] == (
            domain.FILING_RECLAIM_AFTER + domain.FILING_RETRY_CEILING - domain.FILING_RETRY_BASE
        )
