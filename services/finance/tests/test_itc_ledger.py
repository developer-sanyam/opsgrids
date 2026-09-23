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
from app.models import GstReturn, ImsDocument, ItcLedgerEntry, OutboxEvent
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
from platform_core.errors import BusinessRuleProblem, ConflictProblem, NotFoundProblem
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

    async def test_a_late_reject_between_generate_and_file_refuses_filing(
        self, client, session, org_id
    ) -> None:
        """Repro A from review: accept, generate August's 3B, reject, then file — filing used to
        post a utilisation for credit that reversal had already taken back, driving the IGST
        balance to -81,000. Filing is now refused instead, with the balance untouched."""
        ret, document = await self._generated(client, session, org_id)
        ret_id, document_id = ret.id, document.id
        res = await client.post(f"{QUEUE}/{document_id}/decisions", json={"action": "reject"})
        assert res.status_code == 201

        ctx = admin_ctx(org_id)
        with pytest.raises(ConflictProblem, match="regenerate"):
            async with session.begin():
                await services.file_return(
                    session, ctx, ret_id, FileReturnIn(arn="AA0708000000005")
                )

        async with session.begin():
            rows = await _ledger_rows(session, org_id)
            balance = await selectors.itc_document_net_accrued(session, org_id, document_id)
        assert [r.kind for r in rows if r.kind == "utilisation"] == []
        assert balance.total_minor == 0

    async def test_regenerating_after_a_late_reject_files_cleanly(
        self, client, session, org_id
    ) -> None:
        """The remedy the previous test's refusal points at: regenerate, then file — the fresh
        snapshot reflects the reject, so there is nothing left to utilise."""
        _, document = await self._generated(client, session, org_id)
        document_id = document.id
        await client.post(f"{QUEUE}/{document_id}/decisions", json={"action": "reject"})

        ctx = admin_ctx(org_id)
        async with session.begin():
            regenerated = await services.generate_return(
                session, ctx, GenerateReturnIn(period=PERIOD, return_type="gstr3b")
            )
            filed = await services.file_return(
                session, ctx, regenerated.id, FileReturnIn(arn="AA0708000000006")
            )
        filed_registration_id = filed.gst_registration_id

        async with session.begin():
            rows = await _ledger_rows(session, org_id)
            balance = await selectors.itc_balance(session, org_id, filed_registration_id, PERIOD)
        # Nothing was accepted by the time this was filed, so there is nothing to utilise.
        assert [r for r in rows if r.kind == "utilisation"] == []
        assert balance.total_balance_minor == 0


class TestFilingRefusesWhenTheWholeLedgerWouldGoNegative:
    """Repro from review round 3, MEDIUM 4: the pre-posting check meant to refuse a filing that
    would take a head's ITC balance negative never actually fired. It negated an already-positive
    `amount_minor` (so the comparison ran backwards), kept only the *last* of a head's several
    utilisation lines via a dict comprehension that silently overwrote the earlier ones (IGST
    commonly has three), and read the balance for `ret.period` alone, blind to anything already
    posted to a different period. Provoked here by inserting a ledger row directly at the database
    — the same "something already spent this credit" shape a real drift would leave, without
    needing a second filing or a second registration to produce it."""

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

    async def test_a_stray_posting_that_drains_the_ledger_refuses_the_filing(
        self, client, session, org_id
    ) -> None:
        ret, document = await self._generated(client, session, org_id)
        ctx = admin_ctx(org_id)
        rogue_id = uuid.uuid4()
        async with session.begin():
            session.add(
                ItcLedgerEntry(
                    organization_id=org_id,
                    created_by=str(ctx.principal.sub),
                    gst_registration_id=ret.gst_registration_id,
                    period=PERIOD,
                    source_period=PERIOD,
                    tax_head="igst",
                    kind="utilisation",
                    amount_minor=-document.igst_minor,
                    source_type="gst_return",
                    source_id=rogue_id,
                    source_ref=f"gst_return:{rogue_id}:igst",
                    posted_by=str(ctx.principal.sub),
                )
            )

        with pytest.raises(BusinessRuleProblem, match="IGST"):
            async with session.begin():
                await services.file_return(
                    session, ctx, ret.id, FileReturnIn(arn="AA0708000000010")
                )

        async with session.begin():
            rows = await _ledger_rows(session, org_id)
        # Refused before posting: the rogue row is the only `utilisation` row on the ledger.
        assert [r.kind for r in rows if r.kind == "utilisation"] == ["utilisation"]

    async def test_without_the_stray_posting_the_same_filing_succeeds(
        self, client, session, org_id
    ) -> None:
        """The control: nothing about this scenario is refused on its own merits — only the stray
        posting trips the guard."""
        ret, _ = await self._generated(client, session, org_id)
        ctx = admin_ctx(org_id)
        async with session.begin():
            filed = await services.file_return(
                session, ctx, ret.id, FileReturnIn(arn="AA0708000000011")
            )
        assert filed.status == "filed"


class TestSequentialFilingOrder:
    """Repro B from review: generating September while August sits generated-but-unfiled let both
    periods utilise the same brought-forward credit. Both generate and file now refuse to move
    past an earlier period that has not been filed."""

    async def _generated_august(self, client, session, org_id):
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
        return ret

    async def test_generating_september_before_filing_august_is_refused(
        self, client, session, org_id
    ) -> None:
        await self._generated_august(client, session, org_id)
        ctx = admin_ctx(org_id)
        with pytest.raises(BusinessRuleProblem, match=PERIOD):
            async with session.begin():
                await services.generate_return(
                    session, ctx, GenerateReturnIn(period="2026-09", return_type="gstr3b")
                )

    async def test_filing_september_before_filing_august_is_refused(
        self, client, session, org_id
    ) -> None:
        """Forces the state the generate-time guard would normally prevent (a September row
        already generated) directly at the database, so the *file*-time guard is what is on
        trial here, as defence in depth rather than the only line of defence."""
        august = await self._generated_august(client, session, org_id)
        ctx = admin_ctx(org_id)
        async with session.begin():
            september = GstReturn(
                organization_id=org_id,
                created_by=str(ctx.principal.sub),
                gst_registration_id=august.gst_registration_id,
                gstin=august.gstin,
                state_code=august.state_code,
                period="2026-09",
                return_type="gstr3b",
                status="generated",
                sections=august.sections,
                total_taxable_minor=0,
                total_cgst_minor=0,
                total_sgst_minor=0,
                total_igst_minor=0,
            )
            session.add(september)
            await session.flush()
            september_id = september.id

        with pytest.raises(BusinessRuleProblem, match=PERIOD):
            async with session.begin():
                await services.file_return(
                    session, ctx, september_id, FileReturnIn(arn="AA0709000000001")
                )

    async def test_filing_in_order_succeeds(self, client, session, org_id) -> None:
        ret = await self._generated_august(client, session, org_id)
        ctx = admin_ctx(org_id)
        async with session.begin():
            filed = await services.file_return(
                session, ctx, ret.id, FileReturnIn(arn="AA0708000000007")
            )
        assert filed.status == "filed"
        async with session.begin():
            second = await services.generate_return(
                session, ctx, GenerateReturnIn(period="2026-09", return_type="gstr3b")
            )
        assert second.period == "2026-09"


class TestFilingPeriodsBackwardsDoubleUtilises:
    """Repro from review round 3, BLOCKER 2. August is never generated: an organization is allowed
    to onboard mid-period with no earlier rows at all, so `gstr3b_pending_periods_before` sees
    nothing to block September on. September is generated and filed first, spending August's
    brought-forward credit against a real liability — brought-forward is read straight off the
    ledger, not off any particular return, so August having no return of its own yet does not stop
    September from seeing its credit. Generating August's own return *afterward* must now be
    refused, because doing so would let August's return utilise that same credit a second time."""

    async def _filed_september_on_augusts_credit(self, client, session, org_id):
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
            customer = await services.create_customer(session, ctx, _customer_payload())
            await services.create_invoice(
                session,
                ctx,
                InvoiceCreate(
                    number="INV-SEP-1",
                    customer_id=customer.id,
                    issued_date=date(2026, 9, 12),
                    due_date=date(2026, 10, 11),
                    tax_rate_pct=18,
                    lines=[
                        InvoiceLineCreate(
                            description="Hot-rolled coil",
                            quantity=1,
                            unit_price_minor=10_00_000,
                            hsn_sac="72085110",
                        )
                    ],
                ),
            )
        async with session.begin():
            september = await services.generate_return(
                session, ctx, GenerateReturnIn(period="2026-09", return_type="gstr3b")
            )
        async with session.begin():
            filed = await services.file_return(
                session, ctx, september.id, FileReturnIn(arn="AA0709000000099")
            )
        return document, filed

    async def test_generating_august_after_september_is_filed_is_refused(
        self, client, session, org_id
    ) -> None:
        await self._filed_september_on_augusts_credit(client, session, org_id)
        ctx = admin_ctx(org_id)
        with pytest.raises(BusinessRuleProblem, match="2026-09"):
            async with session.begin():
                await services.generate_return(
                    session, ctx, GenerateReturnIn(period=PERIOD, return_type="gstr3b")
                )

    async def test_filing_august_after_september_is_filed_is_refused(
        self, client, session, org_id
    ) -> None:
        """Forces the state the generate-time guard would normally prevent (an August row already
        generated before September existed) directly at the database, so the *file*-time guard is
        what is on trial here, as defence in depth rather than the only line of defence."""
        _, september = await self._filed_september_on_augusts_credit(client, session, org_id)
        ctx = admin_ctx(org_id)
        async with session.begin():
            august = GstReturn(
                organization_id=org_id,
                created_by=str(ctx.principal.sub),
                gst_registration_id=september.gst_registration_id,
                gstin=september.gstin,
                state_code=september.state_code,
                period=PERIOD,
                return_type="gstr3b",
                status="generated",
                sections=september.sections,
                total_taxable_minor=0,
                total_cgst_minor=0,
                total_sgst_minor=0,
                total_igst_minor=0,
            )
            session.add(august)
            await session.flush()
            august_id = august.id

        with pytest.raises(BusinessRuleProblem, match="2026-09"):
            async with session.begin():
                await services.file_return(
                    session, ctx, august_id, FileReturnIn(arn="AA0708000000099")
                )


class TestExpenseCategoryChangeOnAnAlreadyAcceptedDocument:
    """An operator may reconsider *why* a document was accepted without ever un-accepting it —
    and the ledger has to follow the new category exactly, in both directions."""

    @pytest.fixture
    async def accepted(self, client, session, org_id):
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

    async def test_re_classifying_as_blocked_reverses_the_credit(
        self, accepted, client, session, org_id
    ) -> None:
        res = await client.post(
            f"{QUEUE}/{accepted.id}/decisions",
            json={"action": "accept", "expense_category": "motor_vehicle"},
        )
        assert res.status_code == 201

        async with session.begin():
            rows = await _ledger_rows(session, org_id)
            balance = await selectors.itc_document_net_accrued(session, org_id, accepted.id)
        assert sorted(r.kind for r in rows) == ["accrual", "reversal"]
        assert balance.total_minor == 0

    async def test_re_classifying_back_to_general_re_accrues(
        self, accepted, client, session, org_id
    ) -> None:
        await client.post(
            f"{QUEUE}/{accepted.id}/decisions",
            json={"action": "accept", "expense_category": "motor_vehicle"},
        )
        res = await client.post(
            f"{QUEUE}/{accepted.id}/decisions",
            json={"action": "accept", "expense_category": "general"},
        )
        assert res.status_code == 201

        async with session.begin():
            rows = await _ledger_rows(session, org_id)
            balance = await selectors.itc_document_net_accrued(session, org_id, accepted.id)
        assert sorted(r.kind for r in rows) == ["accrual", "accrual", "reversal"]
        assert balance.igst_minor == accepted.igst_minor

    async def test_re_accepting_under_the_same_category_posts_nothing(
        self, accepted, client, session, org_id
    ) -> None:
        res = await client.post(
            f"{QUEUE}/{accepted.id}/decisions",
            json={"action": "accept", "expense_category": "general"},
        )
        assert res.status_code == 201
        async with session.begin():
            rows = await _ledger_rows(session, org_id)
        assert len(rows) == 1
        assert rows[0].kind == "accrual"


class TestClaimPeriodRedirection:
    async def test_a_decision_after_the_periods_return_is_filed_lands_in_the_next_open_period(
        self, client, session, org_id
    ) -> None:
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
            await services.file_return(session, ctx, ret.id, FileReturnIn(arn="AA0708000000008"))

        # August is now filed. Rejecting the document is a decision *about* August's document,
        # but August's return has already gone out — the reversal must land in September instead.
        res = await client.post(f"{QUEUE}/{document.id}/decisions", json={"action": "reject"})
        assert res.status_code == 201

        async with session.begin():
            rows = await _ledger_rows(session, org_id)
        reversal = next(r for r in rows if r.kind == "reversal")
        assert reversal.source_period == PERIOD
        assert reversal.period == "2026-09"


class TestClaimPeriodJumpsPastAnyFiledPeriodNotJustTheDocumentsOwn:
    async def test_a_late_decision_on_a_document_from_before_the_latest_filed_period_lands_after_it(
        self, client, session, org_id
    ) -> None:
        """Repro from review round 3, HIGH 3: the old claim-period walk started at the document's
        *own* period and stepped forward one period at a time asking "is this filed yet", stopping
        the moment it found one that was not. A document reported for July, decided on only after
        *August* has already been filed, is itself never filed — so the old walk stopped at July
        and never advanced past it, and that posting would never appear in any GSTR-3B's Table 4A:
        August is filed and behind it, and September's own Table 4A never asks about July. Claim
        period is now `max(source_period, next_period(latest filed period for the registration))`,
        so this jumps straight to the first period actually still open, in one query rather than a
        walk."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            await _book_with_output_liability(session, ctx)
            ret = await services.generate_return(
                session, ctx, GenerateReturnIn(period=PERIOD, return_type="gstr3b")
            )
            await services.file_return(session, ctx, ret.id, FileReturnIn(arn="AA0708000000012"))

        # August is filed with nothing yet accepted. A July document — reported for a period
        # *before* August — is decided on only now, after August has already gone out.
        async with session.begin():
            document = ImsDocument(
                organization_id=org_id,
                created_by=str(ctx.principal.sub),
                source_ref="2026-07/late-0001",
                supplier_gstin=MERIDIAN,
                supplier_name="Meridian",
                document_number="MT-JUL-1",
                document_type="invoice",
                document_date=date(2026, 7, 15),
                period="2026-07",
                taxable_minor=4_50_000,
                igst_minor=81_000,
                total_minor=5_31_000,
            )
            session.add(document)
            await session.flush()
            document_id = document.id

        async with session.begin():
            await services.record_ims_decision(
                session, ctx, document_id, ImsDecisionCreate(action="accept")
            )

        async with session.begin():
            rows = await _ledger_rows(session, org_id)
        accrual = next(r for r in rows if r.kind == "accrual")
        assert accrual.source_period == "2026-07"
        assert accrual.period == "2026-09"


class TestCreditNoteReducesCredit:
    async def test_accepting_a_supplier_credit_note_posts_a_negative_entry(
        self, session, org_id
    ) -> None:
        """A credit note reduces what a supplier billed, so accepting one must move the ledger
        down, never up. Posted as a `reversal` — `amount_minor`'s sign is what the kind answers
        for, not whether anything preceded it: the CHECK constraint at the database requires a
        negative amount be a `reversal` or a `utilisation`, never an `accrual`."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            document = ImsDocument(
                organization_id=org_id,
                created_by=str(ctx.principal.sub),
                source_ref=f"{PERIOD}/credit-note-0001",
                supplier_gstin=MERIDIAN,
                supplier_name="Meridian",
                document_number="MT-CN-1",
                document_type="credit_note",
                document_date=date(2026, 8, 15),
                period=PERIOD,
                taxable_minor=50_000,
                igst_minor=9_000,
                total_minor=59_000,
            )
            session.add(document)
            await session.flush()
            document_id = document.id

        async with session.begin():
            await services.record_ims_decision(
                session, ctx, document_id, ImsDecisionCreate(action="accept")
            )

        async with session.begin():
            rows = await _ledger_rows(session, org_id)
            balance = await selectors.itc_document_net_accrued(session, org_id, document_id)
        assert len(rows) == 1
        assert rows[0].kind == "reversal"
        assert rows[0].amount_minor == -9_000
        assert balance.igst_minor == -9_000


class TestNonPrimaryRegistrationIsRefusedForGstr3b:
    async def test_generating_for_a_non_primary_registration_is_refused(
        self, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx, gstin=SELLER)
            secondary = await services.set_gst_registration(
                session,
                ctx,
                GstRegistrationUpsert(
                    gstin="24AABCM1234F1Z3",
                    legal_name="OpsGrids Industries — Gujarat Branch",
                    is_primary=False,
                ),
            )
        ctx = admin_ctx(org_id)
        with pytest.raises(BusinessRuleProblem, match="primary"):
            async with session.begin():
                await services.generate_return(
                    session,
                    ctx,
                    GenerateReturnIn(
                        period=PERIOD, return_type="gstr3b", gst_registration_id=secondary.id
                    ),
                )


class TestItcLedgerCheckConstraintsAtTheDatabase:
    async def _registration_id(self, session, org_id) -> uuid.UUID:
        ctx = admin_ctx(org_id)
        async with session.begin():
            registration = await _register(session, ctx)
        return registration.id

    async def _base_kwargs(self, session, org_id) -> dict[str, Any]:
        return {
            "organization_id": org_id,
            "gst_registration_id": await self._registration_id(session, org_id),
            "period": PERIOD,
            "source_period": PERIOD,
            "tax_head": "igst",
            "kind": "accrual",
            "amount_minor": 100,
            "source_type": "ims_decision",
            "source_id": uuid.uuid4(),
            "source_ref": f"ims_decision:{uuid.uuid4()}:igst",
            "posted_by": "test",
        }

    async def test_an_unknown_tax_head_is_refused(self, session, org_id) -> None:
        kwargs = await self._base_kwargs(session, org_id)
        kwargs["tax_head"] = "vat"
        with pytest.raises(Exception, match="ck_itc_ledger_entries_tax_head"):
            async with session.begin():
                session.add(ItcLedgerEntry(**kwargs))
                await session.flush()

    async def test_an_unknown_kind_is_refused(self, session, org_id) -> None:
        kwargs = await self._base_kwargs(session, org_id)
        kwargs["kind"] = "adjustment"
        # An unknown `kind` necessarily also fails to satisfy either branch of the sign
        # constraint, so either name is a correct refusal — which one Postgres reports first is
        # not this test's business.
        with pytest.raises(Exception, match=r"ck_itc_ledger_entries_(kind|amount_sign)"):
            async with session.begin():
                session.add(ItcLedgerEntry(**kwargs))
                await session.flush()

    async def test_an_unknown_source_type_is_refused(self, session, org_id) -> None:
        kwargs = await self._base_kwargs(session, org_id)
        kwargs["source_type"] = "manual"
        with pytest.raises(Exception, match="ck_itc_ledger_entries_source_type"):
            async with session.begin():
                session.add(ItcLedgerEntry(**kwargs))
                await session.flush()

    async def test_a_positive_reversal_is_refused(self, session, org_id) -> None:
        kwargs = await self._base_kwargs(session, org_id)
        kwargs["kind"] = "reversal"
        kwargs["amount_minor"] = 100  # must be negative
        with pytest.raises(Exception, match="ck_itc_ledger_entries_amount_sign"):
            async with session.begin():
                session.add(ItcLedgerEntry(**kwargs))
                await session.flush()

    async def test_a_negative_accrual_is_refused(self, session, org_id) -> None:
        kwargs = await self._base_kwargs(session, org_id)
        kwargs["amount_minor"] = -100  # accrual must be positive
        with pytest.raises(Exception, match="ck_itc_ledger_entries_amount_sign"):
            async with session.begin():
                session.add(ItcLedgerEntry(**kwargs))
                await session.flush()


class TestOutwardReverseChargeIsExcludedFromLiability:
    async def test_a_reverse_charge_invoice_does_not_add_to_table_6_1_liability(
        self, session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            customer = await services.create_customer(session, ctx, _customer_payload())
            await services.create_invoice(
                session,
                ctx,
                InvoiceCreate(
                    number="INV-RCM-1",
                    customer_id=customer.id,
                    issued_date=date(2026, 8, 12),
                    due_date=date(2026, 9, 11),
                    tax_rate_pct=18,
                    reverse_charge=True,
                    lines=[
                        InvoiceLineCreate(
                            description="Legal services under reverse charge",
                            quantity=1,
                            unit_price_minor=10_00_000,
                            hsn_sac="998211",
                        )
                    ],
                ),
            )
            ret = await services.generate_return(
                session, ctx, GenerateReturnIn(period=PERIOD, return_type="gstr3b")
            )
            detail = await selectors.return_detail(session, org_id, ret)

        sections = detail.sections
        assert sections is not None
        # The taxable *value* is bucketed away from 3.1(a) regardless of tax; the tax heads
        # themselves are already zero at the invoice level (`create_invoice` charges nothing on a
        # reverse-charge supply — the recipient self-assesses it), so the exclusion here is a
        # safety net for the value bucketing rather than a redirection of a real tax figure.
        assert sections.outward.taxable_supplies.total_minor == 0
        assert sections.payment.liability.total_minor == 0
        assert sections.outward.outward_reverse_charge_taxable_minor == 10_00_000
        assert sections.outward.outward_reverse_charge.total_minor == 0
        # Still reported in the header's taxable total, matching what GSTR-1 would declare for the
        # period — only the tax collected (zero, here) ever differs from a normal supply.
        assert ret.total_taxable_minor == 10_00_000
        assert ret.total_cgst_minor == 0
        assert ret.total_sgst_minor == 0


class TestReconciliationWithARicherBook:
    """A real cross-check rather than a tautology: a book with a credit note, an export and
    aggregated B2C-small supplies, none of which land in the same Table 3.1 row, still reconciles
    to the paisa against GSTR-1's totals for the same period."""

    async def test_credit_notes_exports_and_b2c_small_all_reconcile(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            registered = await services.create_customer(
                session, ctx, _customer_payload(name="Registered Buyer")
            )
            retail = await services.create_customer(
                session,
                ctx,
                _customer_payload(name="Counter Sale", gstin=None, city="Pune"),
            )
            invoice = await services.create_invoice(
                session, ctx, _invoice_payload(registered.id, "INV-RICH-1", 5_00_000)
            )
            await services.create_invoice(
                session,
                ctx,
                InvoiceCreate(
                    number="CN-RICH-1",
                    customer_id=registered.id,
                    issued_date=date(2026, 8, 20),
                    due_date=date(2026, 9, 19),
                    document_type="credit_note",
                    corrects_invoice_id=invoice.id,
                    tax_rate_pct=18,
                    lines=[
                        InvoiceLineCreate(
                            description="Return", quantity=-1, unit_price_minor=1_00_000
                        )
                    ],
                ),
            )
            await services.create_invoice(
                session,
                ctx,
                InvoiceCreate(
                    number="EXP-RICH-1",
                    customer_id=retail.id,
                    issued_date=date(2026, 8, 22),
                    due_date=date(2026, 9, 21),
                    supply_type="export",
                    tax_rate_pct=0,
                    lines=[
                        InvoiceLineCreate(
                            description="Exported goods", quantity=1, unit_price_minor=2_00_000
                        )
                    ],
                ),
            )
            await services.create_invoice(
                session,
                ctx,
                InvoiceCreate(
                    number="B2C-RICH-1",
                    customer_id=retail.id,
                    issued_date=date(2026, 8, 23),
                    due_date=date(2026, 9, 22),
                    tax_rate_pct=18,
                    lines=[
                        InvoiceLineCreate(
                            description="Counter sale", quantity=1, unit_price_minor=10_000
                        )
                    ],
                ),
            )
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
        # Not all zero, and not all the same section — a real cross-foot, not a tautology.
        assert gstr1.total_taxable_minor == 5_00_000 - 1_00_000 + 2_00_000 + 10_000


class TestItcBalanceCrossTenant404:
    async def test_another_organizations_registration_id_is_not_found(
        self, client, session, admin_session, org_id
    ) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)

        other_org = uuid.uuid4()
        async with admin_session.begin():
            other_registration = await services.set_gst_registration(
                admin_session,
                admin_ctx(other_org),
                GstRegistrationUpsert(gstin=MERIDIAN, legal_name="Another Organization"),
            )

        res = await client.get(
            f"{LEDGER}/balance",
            params={"period": PERIOD, "gst_registration_id": str(other_registration.id)},
        )
        assert res.status_code == 404

        with pytest.raises(NotFoundProblem):
            async with session.begin():
                await services.registration_for(session, ctx, other_registration.id)
