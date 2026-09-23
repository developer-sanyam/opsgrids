"""Two genuine writers racing the same append-only ledger.

`invoices`, `payments` and `payment_allocations` are append-only at the application layer: a
correction is a new row, never an edit. That means an over-application can never be *removed*,
only offset by more rows — so the check that keeps it from happening in the first place has to
hold under real concurrency, not just in a single-threaded test. Written against real PostgreSQL
and two independent connections, like `services/operations/tests/test_stock.py` and
`services/support/tests/test_numbering.py`: only a real server can arbitrate two transactions
genuinely racing each other for the same row lock.

Before the fix, `record_payment` and the credit-note path in `create_invoice` each read "already
applied" (`selectors.paid_by_invoice` / `selectors.credited_total_for_invoice`) with no lock on
the invoice they were checking it against. Two concurrent writers against the same invoice each
read the same figure, each passed the cap, and the invoice was paid or credited past its total —
with no way to undo either row afterwards.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app import selectors, services
from app.config import settings
from app.models import SCHEMA, Customer, GstReturn, ImsDocument, Invoice, ItcLedgerEntry
from app.schemas import (
    CustomerCreate,
    FileReturnIn,
    GenerateReturnIn,
    GstRegistrationUpsert,
    ImsDecisionCreate,
    InvoiceCreate,
    InvoiceLineCreate,
    PaymentAllocationCreate,
    PaymentCreate,
    PurchaseDocumentCreate,
)
from platform_core.db import TENANT_GUC
from platform_core.errors import BusinessRuleProblem, ConflictProblem, NotFoundProblem
from tests.helpers import admin_ctx, service_token_headers

pytestmark = pytest.mark.integration

SELLER = "27AABCA9012D1Z5"


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


async def _org_session(org: uuid.UUID) -> tuple[AsyncSession, AsyncEngine]:
    """A second, independent connection bound to `org` — the race needs two real sessions,
    not the one shared fixture session everything else in this suite uses."""
    engine = create_async_engine(settings().app_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    session = maker()

    @event.listens_for(session.sync_session, "after_begin")
    def _bind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
        connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org}', true)")

    return session, engine


async def _register(session: AsyncSession, ctx, gstin: str = SELLER) -> None:
    await services.set_gst_registration(
        session, ctx, GstRegistrationUpsert(gstin=gstin, legal_name="OpsGrids Industries Pvt Ltd")
    )


async def _customer(session: AsyncSession, ctx, *, name: str = "ACME Corp"):
    return await services.create_customer(
        session,
        ctx,
        CustomerCreate(
            name=name,
            contact_name="AP Desk",
            email="ap@example.com",
            city="Pune",
            state="Maharashtra",
        ),
    )


def _invoice_payload(customer_id: uuid.UUID, number: str, **over) -> InvoiceCreate:
    payload = {
        "number": number,
        "customer_id": customer_id,
        "issued_date": date(2026, 1, 10),
        "due_date": date(2026, 1, 25),
        "tax_rate_pct": 18,
        "lines": [InvoiceLineCreate(description="Widgets", quantity=10, unit_price_minor=100_00)],
    }
    payload.update(over)
    return InvoiceCreate(**payload)


class TestConcurrentPayments:
    async def test_two_payments_that_each_fit_alone_do_not_both_fit_together(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Invoice total is 1,18,000 paise (10 x Rs 100 + 18% GST). Two payments of 70,000 each
        fit individually but sum to more than the invoice — exactly one must be accepted."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx)
            invoice = await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-RACE-PAY-1")
            )
        invoice_id = invoice.id
        customer_id = customer.id
        assert invoice.total_minor == 118_000

        async def _pay(session: AsyncSession, reference: str) -> object:
            ctx = admin_ctx(org_id)
            async with session.begin():
                return await services.record_payment(
                    session,
                    ctx,
                    PaymentCreate(
                        reference=reference,
                        customer_id=customer_id,
                        received_date=date(2026, 1, 20),
                        method="upi",
                        allocations=[
                            PaymentAllocationCreate(invoice_id=invoice_id, amount_minor=70_000)
                        ],
                    ),
                    today=date(2026, 1, 20),
                )

        s1, e1 = await _org_session(org_id)
        s2, e2 = await _org_session(org_id)
        try:
            results = await asyncio.gather(
                _pay(s1, "PMT-RACE-1"), _pay(s2, "PMT-RACE-2"), return_exceptions=True
            )
        finally:
            await s1.close()
            await s2.close()
            await e1.dispose()
            await e2.dispose()

        failures = [r for r in results if isinstance(r, BusinessRuleProblem)]
        successes = [r for r in results if not isinstance(r, BaseException)]
        assert len(failures) == 1, results
        assert len(successes) == 1, results

        async with session.begin():
            paid = await selectors.paid_by_invoice(session, org_id, [invoice_id])
            refreshed = (
                await session.execute(select(Invoice).where(Invoice.id == invoice_id))
            ).scalar_one()
        assert paid[invoice_id] == 70_000
        assert paid[invoice_id] <= invoice.total_minor
        assert refreshed.status != "paid"

    async def test_two_payments_on_two_invoices_in_opposite_order_do_not_deadlock(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """One payment allocates [A, B]; the other allocates [B, A]. Locking in id order (not
        payload order) must serialise them rather than deadlock."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx)
            invoice_a = await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-RACE-DEADLOCK-A")
            )
            invoice_b = await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-RACE-DEADLOCK-B")
            )
        customer_id = customer.id
        ids_forward = [invoice_a.id, invoice_b.id]
        ids_reverse = [invoice_b.id, invoice_a.id]

        async def _pay(session: AsyncSession, reference: str, invoice_ids: list[uuid.UUID]):
            ctx = admin_ctx(org_id)
            async with session.begin():
                return await services.record_payment(
                    session,
                    ctx,
                    PaymentCreate(
                        reference=reference,
                        customer_id=customer_id,
                        received_date=date(2026, 1, 20),
                        method="upi",
                        allocations=[
                            PaymentAllocationCreate(invoice_id=iid, amount_minor=1_000)
                            for iid in invoice_ids
                        ],
                    ),
                    today=date(2026, 1, 20),
                )

        s1, e1 = await _org_session(org_id)
        s2, e2 = await _org_session(org_id)
        try:
            results = await asyncio.wait_for(
                asyncio.gather(
                    _pay(s1, "PMT-DEADLOCK-1", ids_forward),
                    _pay(s2, "PMT-DEADLOCK-2", ids_reverse),
                    return_exceptions=True,
                ),
                timeout=15,
            )
        finally:
            await s1.close()
            await s2.close()
            await e1.dispose()
            await e2.dispose()

        for r in results:
            if isinstance(r, BaseException):
                raise r


class TestConcurrentCreditNotes:
    async def test_two_credit_notes_that_each_fit_alone_do_not_both_fit_together(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Invoice total is 1,18,000 paise. Two credit notes of -82,600 each (7 units returned,
        18% GST) fit individually but sum to more correctable than the invoice holds."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx)
            invoice = await services.create_invoice(
                session, ctx, _invoice_payload(customer.id, "INV-RACE-CN-1")
            )
        invoice_id = invoice.id
        customer_id = customer.id
        assert invoice.total_minor == 118_000

        async def _credit(session: AsyncSession, number: str):
            ctx = admin_ctx(org_id)
            async with session.begin():
                return await services.create_invoice(
                    session,
                    ctx,
                    _invoice_payload(
                        customer_id,
                        number,
                        document_type="credit_note",
                        corrects_invoice_id=invoice_id,
                        lines=[
                            InvoiceLineCreate(
                                description="Return", quantity=-7, unit_price_minor=100_00
                            )
                        ],
                    ),
                )

        s1, e1 = await _org_session(org_id)
        s2, e2 = await _org_session(org_id)
        try:
            results = await asyncio.gather(
                _credit(s1, "CN-RACE-1"), _credit(s2, "CN-RACE-2"), return_exceptions=True
            )
        finally:
            await s1.close()
            await s2.close()
            await e1.dispose()
            await e2.dispose()

        failures = [r for r in results if isinstance(r, BusinessRuleProblem)]
        successes = [r for r in results if not isinstance(r, BaseException)]
        assert len(failures) == 1, results
        assert len(successes) == 1, results

        async with session.begin():
            credited = await selectors.credited_total_for_invoice(session, org_id, invoice_id)
        assert credited == 82_600
        assert credited <= invoice.total_minor


class TestConcurrentCustomerArchive:
    async def test_archive_never_wins_against_a_new_invoice_for_the_same_customer(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """An archive that races a new invoice for the same customer must not both succeed —
        that would leave an archived customer with live billing history naming them."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await _customer(session, ctx, name="Race Customer")
        customer_id = customer.id

        async def _archive(session: AsyncSession):
            ctx = admin_ctx(org_id)
            async with session.begin():
                fetched = await selectors.get_customer(
                    session, org_id, customer_id, for_update=True
                )
                assert fetched is not None
                await services.archive_customer(session, ctx, fetched)

        async def _create_invoice(session: AsyncSession):
            ctx = admin_ctx(org_id)
            async with session.begin():
                return await services.create_invoice(
                    session, ctx, _invoice_payload(customer_id, "INV-RACE-ARCHIVE-1")
                )

        s1, e1 = await _org_session(org_id)
        s2, e2 = await _org_session(org_id)
        try:
            results = await asyncio.gather(
                _archive(s1), _create_invoice(s2), return_exceptions=True
            )
        finally:
            await s1.close()
            await s2.close()
            await e1.dispose()
            await e2.dispose()

        archive_result, invoice_result = results
        archive_failed = isinstance(archive_result, (ConflictProblem, NotFoundProblem))
        invoice_failed = isinstance(invoice_result, (ConflictProblem, NotFoundProblem))
        # Never both succeed, and never both fail — one side has to win.
        assert archive_failed != invoice_failed, results

        async with session.begin():
            refreshed = (
                await session.execute(select(Customer).where(Customer.id == customer_id))
            ).scalar_one()
            has_invoice = await selectors.customer_has_references(session, org_id, customer_id)
        if refreshed.deleted_at is not None:
            assert not has_invoice


# --------------------------------------------------------------------- IMS decisions
#
# Before the fix, `record_ims_decision` read the document with a plain `SELECT` and derived
# `before_status` from that unlocked read. Two concurrent decisions on the same document each read
# the same `before_status`, each computed a posting of their own, and the ledger accrued (or
# reversed) the same document twice.

MERIDIAN = "24AABCM1234F1Z3"  # Gujarat, inter-state from the Maharashtra seller below — IGST.


async def _ims_register(session: AsyncSession, ctx) -> None:
    await services.set_gst_registration(
        session, ctx, GstRegistrationUpsert(gstin=SELLER, legal_name="OpsGrids Industries Pvt Ltd")
    )


async def _ims_seed_and_sync(client, org_id: uuid.UUID, session: AsyncSession, ctx) -> uuid.UUID:
    """One accepted-purchase-matched document, synced from the stub portal — real IMS ingest, the
    same path `test_ims.py` exercises, so the race is against the real accrual code and not a
    hand-built fixture row."""
    async with session.begin():
        await services.record_purchase_document(
            session,
            ctx,
            PurchaseDocumentCreate(
                supplier_gstin=MERIDIAN,
                number="MT-4471",
                document_date=date(2026, 8, 1),
                taxable_minor=4_50_000,
                tax_rate_pct=18,
                supplier_name="Meridian",
            ),
        )
    res = await client.post(
        "/internal/v1/ims/sync",
        headers=service_token_headers(),
        json={"organization_id": str(org_id), "period": "2026-08"},
    )
    assert res.status_code == 200
    async with session.begin():
        stmt = select(ImsDocument).where(
            ImsDocument.organization_id == org_id, ImsDocument.source_ref == "2026-08/0001"
        )
        document = (await session.execute(stmt)).scalar_one()
    return document.id


async def _itc_rows(session: AsyncSession, org_id: uuid.UUID) -> list[ItcLedgerEntry]:
    stmt = select(ItcLedgerEntry).where(ItcLedgerEntry.organization_id == org_id)
    return list((await session.execute(stmt)).scalars().all())


class TestConcurrentImsDecisions:
    async def test_two_concurrent_accepts_accrue_only_once(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _ims_register(session, ctx)
        document_id = await _ims_seed_and_sync(client, org_id, session, ctx)

        async def _accept(session: AsyncSession):
            ctx = admin_ctx(org_id)
            async with session.begin():
                return await services.record_ims_decision(
                    session, ctx, document_id, ImsDecisionCreate(action="accept")
                )

        s1, e1 = await _org_session(org_id)
        s2, e2 = await _org_session(org_id)
        try:
            results = await asyncio.gather(_accept(s1), _accept(s2), return_exceptions=True)
        finally:
            await s1.close()
            await s2.close()
            await e1.dispose()
            await e2.dispose()

        for result in results:
            if isinstance(result, BaseException):
                raise result

        async with session.begin():
            rows = await _itc_rows(session, org_id)
            balance = await selectors.itc_document_net_accrued(session, org_id, document_id)
        accruals = [r for r in rows if r.kind == "accrual"]
        assert len(accruals) == 1, rows
        assert balance.igst_minor == accruals[0].amount_minor
        assert balance.igst_minor != 2 * accruals[0].amount_minor

    async def test_two_concurrent_rejects_after_acceptance_reverse_only_once(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _ims_register(session, ctx)
        document_id = await _ims_seed_and_sync(client, org_id, session, ctx)
        async with session.begin():
            await services.record_ims_decision(
                session, ctx, document_id, ImsDecisionCreate(action="accept")
            )

        async def _reject(session: AsyncSession):
            ctx = admin_ctx(org_id)
            async with session.begin():
                return await services.record_ims_decision(
                    session, ctx, document_id, ImsDecisionCreate(action="reject")
                )

        s1, e1 = await _org_session(org_id)
        s2, e2 = await _org_session(org_id)
        try:
            results = await asyncio.gather(_reject(s1), _reject(s2), return_exceptions=True)
        finally:
            await s1.close()
            await s2.close()
            await e1.dispose()
            await e2.dispose()

        for result in results:
            if isinstance(result, BaseException):
                raise result

        async with session.begin():
            rows = await _itc_rows(session, org_id)
            balance = await selectors.itc_document_net_accrued(session, org_id, document_id)
        reversals = [r for r in rows if r.kind == "reversal"]
        assert len(reversals) == 1, rows
        assert balance.total_minor == 0


class TestConcurrentDecisionAndFiling:
    """Repro from review round 3, BLOCKER 1. `record_ims_decision` used to resolve the
    registration with a plain, unlocked read before asking `selectors.claim_period_for` (née
    `services._claim_period_for`) whether the document's own period was already filed. A decision
    racing a filing for that exact period could read "not filed yet" a moment before the filing
    committed, post its reversal straight into the period the filing was about to freeze, and take
    that period's IGST balance to -81,000 — after the fact, in a return already gone to the
    government.

    `record_ims_decision` now locks the same registration row `FOR UPDATE` before computing the
    claim period, the same row `file_return` locks before it recomputes and posts. Two-way race,
    two safe outcomes depending only on which transaction reaches the lock first — never a third:

    * The filing wins the lock: it commits first, so by the time the decision's claim period is
      computed, the period is genuinely filed and the reversal is correctly redirected to the next
      one open.
    * The decision wins the lock: it posts into the still-open period first, so the filing's own
      recompute-against-the-ledger check (guard 4 of `file_return`) sees a ledger that has moved
      since generation and refuses with a 409 — the return is never actually filed with a
      reversal landing inside it.

    Never: a reversal recorded inside a period this test also observes as `filed`.
    """

    async def test_a_decision_racing_a_filing_never_reverses_into_the_filed_period(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _ims_register(session, ctx)
        document_id = await _ims_seed_and_sync(client, org_id, session, ctx)
        async with session.begin():
            await services.record_ims_decision(
                session, ctx, document_id, ImsDecisionCreate(action="accept")
            )
            ret = await services.generate_return(
                session, ctx, GenerateReturnIn(period="2026-08", return_type="gstr3b")
            )
        ret_id = ret.id

        async def _file(session: AsyncSession):
            ctx = admin_ctx(org_id)
            async with session.begin():
                return await services.file_return(
                    session, ctx, ret_id, FileReturnIn(arn="AA0708000000900")
                )

        async def _reject(session: AsyncSession):
            ctx = admin_ctx(org_id)
            async with session.begin():
                return await services.record_ims_decision(
                    session, ctx, document_id, ImsDecisionCreate(action="reject")
                )

        s1, e1 = await _org_session(org_id)
        s2, e2 = await _org_session(org_id)
        try:
            results = await asyncio.gather(_file(s1), _reject(s2), return_exceptions=True)
        finally:
            await s1.close()
            await s2.close()
            await e1.dispose()
            await e2.dispose()

        file_result, decision_result = results
        # The decision itself is never refused by this race — only the filing can be, and only
        # with the specific conflict `file_return`'s own ledger-drift guard raises.
        if isinstance(decision_result, BaseException):
            raise decision_result
        if isinstance(file_result, BaseException) and not isinstance(file_result, ConflictProblem):
            raise file_result

        # `ret` and the ledger rows this session already knows about were loaded before the race —
        # `expire_all` forces a real reload rather than the identity map's stale, pre-race copies.
        session.expire_all()
        async with session.begin():
            rows = await _itc_rows(session, org_id)
            refreshed = (
                await session.execute(select(GstReturn).where(GstReturn.id == ret_id))
            ).scalar_one()
        reversal = next(r for r in rows if r.kind == "reversal")
        if refreshed.status == "filed":
            # The filing won the race: the decision's claim period must have been computed
            # *after* seeing the filing as done, and redirected forward past it.
            assert reversal.period != "2026-08"
        else:
            # The decision won the race and posted into the still-open period; the filing then
            # correctly refused rather than freezing a period whose ledger had just moved.
            assert isinstance(file_result, ConflictProblem)
            assert reversal.period == "2026-08"
        # Whichever branch ran, an accept fully reversed nets the document back to zero — never
        # negative, which is exactly the shape `-81,000` would have taken.
        balance = await selectors.itc_document_net_accrued(session, org_id, document_id)
        assert balance.total_minor == 0
