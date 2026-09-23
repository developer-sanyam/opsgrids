"""The ITC ledger and GSTR-3B against real PostgreSQL.

Two things this proves that `test_itc.py`'s pure functions cannot: that a decision on a real
document posts a real, append-only ledger row inside the same transaction, and that filing a real
GSTR-3B consumes exactly the credit its own frozen snapshot declared — not a figure recomputed
against a book that may have moved since generation.

Real PostgreSQL, the app role, the real policies and the real append-only trigger, the same shape
`test_ims.py` and `test_gstr1.py` already run in.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import pytest
from sqlalchemy import delete, select, text, update

from app import selectors, services
from app.models import ItcLedgerEntry, OutboxEvent
from app.schemas import (
    CustomerCreate,
    FileReturnIn,
    GenerateReturnIn,
    GstRegistrationUpsert,
    ImsDecisionCreate,
    InvoiceCreate,
    InvoiceLineCreate,
    PurchaseDocumentCreate,
)
from platform_core.errors import BusinessRuleProblem, ConflictProblem
from tests.helpers import admin_ctx, service_token_headers

pytestmark = pytest.mark.integration

SYNC = "/internal/v1/ims/sync"
QUEUE = "/api/v1/finance/ims/documents"
RETURNS = "/api/v1/finance/gst/returns"
LEDGER = "/api/v1/finance/gst/itc-ledger"

#: The recipient: this organization, in Maharashtra — same seller every other GST test uses.
SELLER = "27AABCA9012D1Z5"
PERIOD = "2026-08"

#: The stub portal's suppliers. See `app/ims/stub.py`; `test_ims.py` names the same four.
MERIDIAN = "24AABCM1234F1Z3"  # Gujarat, inter-state -> IGST. Documents 0001 and 0004.
KANAKA = "33AAACK5678G1ZH"  # Tamil Nadu, inter-state -> IGST. Document 0002.
SAHYADRI = "29AADCS3456J1Z0"  # Karnataka, inter-state -> IGST. Document 0003.
ORBIT = "27AAECO9012H1ZG"  # Maharashtra — same as the seller -> CGST+SGST. Document 0005.

BUYER_SAME_STATE = "27AAECO9012H1ZG"


# --------------------------------------------------------------------- fixtures


async def _register(session, ctx, gstin: str = SELLER):
    return await services.set_gst_registration(
        session, ctx, GstRegistrationUpsert(gstin=gstin, legal_name="OpsGrids Industries Pvt Ltd")
    )


def _purchase(supplier_gstin: str, number: str, taxable: int, rate: float, **overrides):
    payload: dict[str, Any] = {
        "supplier_gstin": supplier_gstin,
        "number": number,
        "document_date": date(2026, 8, 1),
        "taxable_minor": taxable,
        "tax_rate_pct": rate,
        "supplier_name": "Supplier",
    }
    payload.update(overrides)
    return PurchaseDocumentCreate(**payload)


async def _seed_purchase_book(session, ctx) -> None:
    """Positioned so Meridian matches exactly — the document this file's accrual tests decide."""
    await services.record_purchase_document(
        session, ctx, _purchase(MERIDIAN, "MT-4471", 4_50_000, 18, supplier_name="Meridian")
    )
    await services.record_purchase_document(
        session, ctx, _purchase(ORBIT, "OL-9002", 90_000, 5, supplier_name="Orbit Logistics")
    )


async def _sync(client, org_id, period: str = PERIOD):
    return await client.post(
        SYNC,
        headers=service_token_headers(),
        json={"organization_id": str(org_id), "period": period},
    )


async def _by_ref(session, org_id) -> dict[str, Any]:
    rows = await selectors.all_ims_documents(session, org_id)
    return {row.source_ref: row for row in rows}


async def _ledger_rows(session, org_id) -> list[ItcLedgerEntry]:
    stmt = select(ItcLedgerEntry).where(ItcLedgerEntry.organization_id == org_id)
    return list((await session.execute(stmt)).scalars().all())


def _customer_payload(**overrides) -> CustomerCreate:
    fields: dict[str, Any] = {
        "name": "Buyer",
        "contact_name": "Contact",
        "email": "ap@example.in",
        "gstin": BUYER_SAME_STATE,
        "city": "Pune",
        "state": "Maharashtra",
    }
    fields.update(overrides)
    return CustomerCreate(**fields)


def _invoice_payload(customer_id, number: str, taxable: int) -> InvoiceCreate:
    return InvoiceCreate(
        number=number,
        customer_id=customer_id,
        issued_date=date(2026, 8, 12),
        due_date=date(2026, 9, 11),
        tax_rate_pct=18,
        lines=[
            InvoiceLineCreate(
                description="Hot-rolled coil",
                quantity=1,
                unit_price_minor=taxable,
                hsn_sac="72085110",
            )
        ],
    )


# --------------------------------------------------------------------- accrual and reversal


class TestAccrualOnAcceptance:
    @pytest.fixture
    async def queued(self, client, session, org_id):
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            await _seed_purchase_book(session, ctx)
        await _sync(client, org_id)
        async with session.begin():
            return await _by_ref(session, org_id)

    async def test_accepting_an_inter_state_document_accrues_igst(
        self, queued, client, session, org_id
    ) -> None:
        document = queued[f"{PERIOD}/0001"]  # Meridian: matched, IGST
        assert document.igst_minor > 0
        assert document.cgst_minor == 0

        res = await client.post(
            f"{QUEUE}/{document.id}/decisions",
            json={"action": "accept", "note": "Goods received against PO 4471"},
        )
        assert res.status_code == 201

        async with session.begin():
            rows = await _ledger_rows(session, org_id)
        assert len(rows) == 1
        entry = rows[0]
        assert entry.kind == "accrual"
        assert entry.tax_head == "igst"
        assert entry.amount_minor == document.igst_minor
        assert entry.source_type == "ims_decision"
        assert entry.period == PERIOD

    async def test_accepting_an_intra_state_document_accrues_cgst_and_sgst(
        self, queued, client, session, org_id
    ) -> None:
        document = queued[f"{PERIOD}/0005"]  # Orbit: the one intra-state document
        assert document.cgst_minor > 0 and document.sgst_minor > 0

        res = await client.post(f"{QUEUE}/{document.id}/decisions", json={"action": "accept"})
        assert res.status_code == 201

        async with session.begin():
            rows = await _ledger_rows(session, org_id)
        heads = {row.tax_head: row.amount_minor for row in rows}
        assert heads == {"cgst": document.cgst_minor, "sgst": document.sgst_minor}
        assert all(row.kind == "accrual" for row in rows)

    async def test_a_blocked_category_accrues_nothing(
        self, queued, client, session, org_id
    ) -> None:
        document = queued[f"{PERIOD}/0001"]
        res = await client.post(
            f"{QUEUE}/{document.id}/decisions",
            json={"action": "accept", "expense_category": "motor_vehicle"},
        )
        assert res.status_code == 201
        assert res.json()["expense_category"] == "motor_vehicle"

        async with session.begin():
            rows = await _ledger_rows(session, org_id)
        assert rows == []

    async def test_an_unknown_category_is_refused(self, queued, client) -> None:
        document = queued[f"{PERIOD}/0001"]
        res = await client.post(
            f"{QUEUE}/{document.id}/decisions",
            json={"action": "accept", "expense_category": "not-a-real-category"},
        )
        assert res.status_code == 422

    async def test_rejecting_reverses_exactly_what_was_accrued(
        self, queued, client, session, org_id
    ) -> None:
        document = queued[f"{PERIOD}/0001"]
        await client.post(f"{QUEUE}/{document.id}/decisions", json={"action": "accept"})
        res = await client.post(f"{QUEUE}/{document.id}/decisions", json={"action": "reject"})
        assert res.status_code == 201

        async with session.begin():
            rows = await _ledger_rows(session, org_id)
            balance = await selectors.itc_document_net_accrued(session, org_id, document.id)
        kinds = sorted(row.kind for row in rows)
        assert kinds == ["accrual", "reversal"]
        accrual = next(r for r in rows if r.kind == "accrual")
        reversal = next(r for r in rows if r.kind == "reversal")
        assert reversal.amount_minor == -accrual.amount_minor
        assert balance.total_minor == 0

    async def test_moving_between_two_non_accepted_statuses_posts_nothing(
        self, queued, client, session, org_id
    ) -> None:
        document = queued[f"{PERIOD}/0002"]
        await client.post(f"{QUEUE}/{document.id}/decisions", json={"action": "pending_action"})
        res = await client.post(f"{QUEUE}/{document.id}/decisions", json={"action": "reject"})
        assert res.status_code == 201
        async with session.begin():
            rows = await _ledger_rows(session, org_id)
        assert rows == []

    async def test_re_accepting_after_a_rejection_accrues_again(
        self, queued, client, session, org_id
    ) -> None:
        """A document may legitimately flip more than once; each transition is its own row, keyed
        on the decision that made it, so re-acceptance is not blocked by the earlier accrual's
        idempotency key."""
        document = queued[f"{PERIOD}/0001"]
        await client.post(f"{QUEUE}/{document.id}/decisions", json={"action": "accept"})
        await client.post(f"{QUEUE}/{document.id}/decisions", json={"action": "reject"})
        res = await client.post(f"{QUEUE}/{document.id}/decisions", json={"action": "accept"})
        assert res.status_code == 201

        async with session.begin():
            rows = await _ledger_rows(session, org_id)
            balance = await selectors.itc_document_net_accrued(session, org_id, document.id)
        assert sorted(row.kind for row in rows) == ["accrual", "accrual", "reversal"]
        assert balance.igst_minor == document.igst_minor

    async def test_the_posting_announces_itself(self, queued, client, session, org_id) -> None:
        document = queued[f"{PERIOD}/0001"]
        await client.post(f"{QUEUE}/{document.id}/decisions", json={"action": "accept"})
        async with session.begin():
            events = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.organization_id == org_id,
                            OutboxEvent.event_type == "finance.compliance.itc.accrued",
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(events) == 1
        assert events[0].payload["entries"][0]["tax_head"] == "igst"


class TestItcLedgerEndpoints:
    @pytest.fixture
    async def accrued(self, client, session, org_id):
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            await _seed_purchase_book(session, ctx)
        await _sync(client, org_id)
        async with session.begin():
            documents = await _by_ref(session, org_id)
        document = documents[f"{PERIOD}/0001"]
        await client.post(f"{QUEUE}/{document.id}/decisions", json={"action": "accept"})
        return document

    async def test_the_ledger_lists_the_posting(self, accrued, client) -> None:
        res = await client.get(LEDGER)
        assert res.status_code == 200
        body = res.json()
        assert body["total"] == 1
        assert body["items"][0]["kind"] == "accrual"
        assert body["items"][0]["tax_head"] == "igst"

    async def test_the_ledger_filters_by_kind_and_period(self, accrued, client) -> None:
        res = await client.get(LEDGER, params={"kind": "reversal"})
        assert res.json()["total"] == 0
        res = await client.get(LEDGER, params={"kind": "accrual", "period": PERIOD})
        assert res.json()["total"] == 1
        res = await client.get(LEDGER, params={"period": "2026-01"})
        assert res.json()["total"] == 0

    async def test_the_balance_reflects_the_accrual(self, accrued, client) -> None:
        res = await client.get(f"{LEDGER}/balance", params={"period": PERIOD})
        assert res.status_code == 200
        body = res.json()
        assert body["period"] == PERIOD
        igst = next(h for h in body["heads"] if h["tax_head"] == "igst")
        assert igst["accrued_minor"] == accrued.igst_minor
        assert igst["balance_minor"] == accrued.igst_minor
        assert body["total_balance_minor"] == accrued.igst_minor

    async def test_the_balance_before_any_posting_is_zero(self, client, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
        res = await client.get(f"{LEDGER}/balance", params={"period": PERIOD})
        assert res.status_code == 200
        assert res.json()["total_balance_minor"] == 0


class TestItcLedgerIsAppendOnlyAtTheDatabase:
    @pytest.fixture
    async def posted(self, client, session, org_id) -> uuid.UUID:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            await _seed_purchase_book(session, ctx)
        await _sync(client, org_id)
        async with session.begin():
            documents = await _by_ref(session, org_id)
        document = documents[f"{PERIOD}/0001"]
        await client.post(f"{QUEUE}/{document.id}/decisions", json={"action": "accept"})
        async with session.begin():
            row = (await _ledger_rows(session, org_id))[0]
        return row.id

    async def test_an_update_is_refused_by_the_database(self, posted, session) -> None:
        with pytest.raises(Exception, match="append-only"):
            async with session.begin():
                await session.execute(
                    update(ItcLedgerEntry)
                    .where(ItcLedgerEntry.id == posted)
                    .values(amount_minor=999)
                )

    async def test_a_delete_is_refused_by_the_database(self, posted, session) -> None:
        with pytest.raises(Exception, match="append-only"):
            async with session.begin():
                await session.execute(delete(ItcLedgerEntry).where(ItcLedgerEntry.id == posted))

    async def test_the_trigger_is_installed_on_the_table(self, session) -> None:
        async with session.begin():
            found = (
                (
                    await session.execute(
                        text(
                            "SELECT tgname FROM pg_trigger t"
                            " JOIN pg_class c ON c.oid = t.tgrelid"
                            " JOIN pg_namespace n ON n.oid = c.relnamespace"
                            " WHERE n.nspname = 'finance' AND c.relname = 'itc_ledger_entries'"
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert "trg_itc_ledger_entries_append_only" in found


class TestCrossTenantIsolation:
    async def test_another_tenants_postings_are_invisible(
        self, client, session, admin_session, org_id
    ) -> None:
        """The other tenant's ledger is posted through the admin (BYPASSRLS) session — a real IMS
        decision, so a real accrual — then read for through the app role bound to *this* org.
        Nothing comes back, and this org's own balance is unaffected."""
        other_org = uuid.uuid4()
        ctx = admin_ctx(org_id)
        async with session.begin():
            registration = await _register(session, ctx)
            await _seed_purchase_book(session, ctx)
        await _sync(client, org_id)
        async with session.begin():
            documents = await _by_ref(session, org_id)
        document = documents[f"{PERIOD}/0001"]
        await client.post(f"{QUEUE}/{document.id}/decisions", json={"action": "accept"})

        other_ctx = admin_ctx(other_org)
        async with admin_session.begin():
            await _register(admin_session, other_ctx)
            await _seed_purchase_book(admin_session, other_ctx)

        # Sync and decide as the other org, through the same client — its own request, its own
        # transaction, bound to its own organization by the tenant chokepoint on the internal
        # route and by the principal override on the decision route.
        await client.post(
            SYNC,
            headers=service_token_headers(),
            json={"organization_id": str(other_org), "period": PERIOD},
        )
        async with admin_session.begin():
            their_documents = await _by_ref(admin_session, other_org)
        their_document = their_documents[f"{PERIOD}/0001"]
        async with admin_session.begin():
            await services.record_ims_decision(
                admin_session,
                other_ctx,
                their_document.id,
                ImsDecisionCreate(action="accept"),
            )

        async with session.begin():
            visible = list((await session.execute(selectors.itc_ledger_stmt(ctx))).scalars().all())
            balance = await selectors.itc_balance(session, org_id, registration.id, PERIOD)
        assert {row.organization_id for row in visible} == {org_id}
        assert balance.total_balance_minor == document.igst_minor

        res = await client.get(LEDGER)
        assert res.status_code == 200
        assert res.json()["total"] == 1


# --------------------------------------------------------------------- GSTR-3B


async def _book_with_output_liability(session, ctx) -> dict[str, Any]:
    """One B2B invoice, intra-state, so Table 3.1 has a real CGST+SGST liability to net against
    the ITC ledger."""
    customer = await services.create_customer(session, ctx, _customer_payload())
    invoice = await services.create_invoice(
        session, ctx, _invoice_payload(customer.id, "INV-B-1", 10_00_000)
    )
    return {"customer": customer, "invoice": invoice}


class TestGeneratingAGstr3bReturn:
    async def _accepted_meridian(self, client, session, org_id):
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            await _seed_purchase_book(session, ctx)
            await _book_with_output_liability(session, ctx)
        await _sync(client, org_id)
        async with session.begin():
            documents = await _by_ref(session, org_id)
        document = documents[f"{PERIOD}/0001"]
        await client.post(f"{QUEUE}/{document.id}/decisions", json={"action": "accept"})
        return document

    async def test_table_3_1_reconciles_with_gstr1_for_the_same_period(
        self, client, session, org_id
    ) -> None:
        await self._accepted_meridian(client, session, org_id)
        ctx = admin_ctx(org_id)
        async with session.begin():
            gstr1 = await services.generate_return(
                session, ctx, GenerateReturnIn(period=PERIOD, return_type="gstr1")
            )
            gstr3b = await services.generate_return(
                session, ctx, GenerateReturnIn(period=PERIOD, return_type="gstr3b")
            )
        assert gstr3b.total_taxable_minor == gstr1.total_taxable_minor
        assert gstr3b.total_cgst_minor == gstr1.total_cgst_minor
        assert gstr3b.total_sgst_minor == gstr1.total_sgst_minor
        assert gstr3b.total_igst_minor == gstr1.total_igst_minor

    async def test_the_snapshot_carries_table_4_and_table_6_1(
        self, client, session, org_id
    ) -> None:
        document = await self._accepted_meridian(client, session, org_id)
        ctx = admin_ctx(org_id)
        async with session.begin():
            ret = await services.generate_return(
                session, ctx, GenerateReturnIn(period=PERIOD, return_type="gstr3b")
            )
            detail = await selectors.return_detail(session, org_id, ret)

        sections = detail.sections
        assert sections is not None
        # 18% intra-state on 10,00,000 = 90,000 CGST + 90,000 SGST.
        assert sections.outward.taxable_supplies.cgst_minor == 90_000
        assert sections.outward.taxable_supplies.sgst_minor == 90_000
        assert sections.itc.available.igst_minor == document.igst_minor
        # IGST credit spills to CGST then SGST liability, in that order.
        heads = {(line.head, line.against) for line in sections.payment.utilisation}
        assert ("igst", "cgst") in heads or ("igst", "sgst") in heads
        assert sections.payment.liability.cgst_minor == 90_000
        assert sections.payment.liability.sgst_minor == 90_000

    async def test_generating_twice_before_filing_re_assembles(
        self, client, session, org_id
    ) -> None:
        await self._accepted_meridian(client, session, org_id)
        ctx = admin_ctx(org_id)
        async with session.begin():
            first = await services.generate_return(
                session, ctx, GenerateReturnIn(period=PERIOD, return_type="gstr3b")
            )
        async with session.begin():
            second = await services.generate_return(
                session, ctx, GenerateReturnIn(period=PERIOD, return_type="gstr3b")
            )
        assert second.id == first.id
        async with session.begin():
            trail = await selectors.return_transitions(session, org_id, second.id)
        assert [t.to_status for t in trail] == ["generated", "generated"]

    async def test_diffing_a_gstr3b_is_refused(self, client, session, org_id) -> None:
        await self._accepted_meridian(client, session, org_id)
        ctx = admin_ctx(org_id)
        async with session.begin():
            ret = await services.generate_return(
                session, ctx, GenerateReturnIn(period=PERIOD, return_type="gstr3b")
            )
            ret_id = ret.id
        with pytest.raises(BusinessRuleProblem, match="gstr1"):
            async with session.begin():
                await services.diff_return(session, ctx, ret_id)

        res = await client.get(f"{RETURNS}/{ret_id}/diff")
        assert res.status_code == 422


class TestFilingAGstr3bReturn:
    async def _generated(self, client, session, org_id):
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            await _seed_purchase_book(session, ctx)
            await _book_with_output_liability(session, ctx)
        await _sync(client, org_id)
        async with session.begin():
            documents = await _by_ref(session, org_id)
        document = documents[f"{PERIOD}/0001"]
        await client.post(f"{QUEUE}/{document.id}/decisions", json={"action": "accept"})
        async with session.begin():
            ret = await services.generate_return(
                session, ctx, GenerateReturnIn(period=PERIOD, return_type="gstr3b")
            )
        return ret, document

    async def test_filing_posts_utilisation_to_the_ledger(self, client, session, org_id) -> None:
        ret, document = await self._generated(client, session, org_id)
        ctx = admin_ctx(org_id)
        async with session.begin():
            filed = await services.file_return(
                session, ctx, ret.id, FileReturnIn(arn="AA0708000000001")
            )

        async with session.begin():
            rows = await _ledger_rows(session, org_id)
            balance = await selectors.itc_balance(
                session, org_id, filed.gst_registration_id, PERIOD
            )
        utilisation_rows = [r for r in rows if r.kind == "utilisation"]
        assert utilisation_rows
        assert all(r.amount_minor < 0 for r in utilisation_rows)
        assert sum(-r.amount_minor for r in utilisation_rows) == document.igst_minor
        # The utilised credit no longer stands as a balance.
        assert balance.total_balance_minor == 0

    async def test_filing_announces_the_utilisation(self, client, session, org_id) -> None:
        ret, _ = await self._generated(client, session, org_id)
        ctx = admin_ctx(org_id)
        async with session.begin():
            await services.file_return(session, ctx, ret.id, FileReturnIn(arn="AA0708000000002"))
            events = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.organization_id == org_id,
                            OutboxEvent.event_type == "finance.compliance.itc.utilised",
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(events) == 1

    async def test_filing_twice_is_refused_and_posts_nothing_twice(
        self, client, session, org_id
    ) -> None:
        ret, _ = await self._generated(client, session, org_id)
        ctx = admin_ctx(org_id)
        async with session.begin():
            await services.file_return(session, ctx, ret.id, FileReturnIn(arn="AA0708000000003"))
        with pytest.raises(ConflictProblem):
            async with session.begin():
                await services.file_return(
                    session, ctx, ret.id, FileReturnIn(arn="AA0708000000004")
                )
        async with session.begin():
            rows = await _ledger_rows(session, org_id)
        utilisation_rows = [r for r in rows if r.kind == "utilisation"]
        assert len(utilisation_rows) == len({r.tax_head for r in utilisation_rows})
