"""The IMS reconciliation queue — the half of GST that arrives rather than leaves.

E-invoicing reports this organization's documents outward. The Invoice Management System is what
its *suppliers* reported about it, and what the organization does with each of those documents
decides how much input tax credit it may claim. Accepting an invoice for goods that never arrived
is a credit claimed wrongly; rejecting a real one costs the supplier money and the business its
credit. Both are decisions, and the product's whole position is that a program makes neither.

So these tests are mostly about restraint. The reconciler is asked to be right about *why* — an
exact match, a money difference with the delta named, a supplier nothing was bought from, a
document that arrived twice — and then asked not to act on any of it. The status of every document
stays `pending` until a person says otherwise, and what that person decided is written to a table
the application cannot rewrite.

Real PostgreSQL, the app role, the real policies and the real append-only trigger. The portal is
the deterministic stub, and a test asserts it opens no socket.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import delete, func, select, text, update

from app import domain, selectors, services
from app.ims.base import ImsSupplierDocument, ImsUnavailable
from app.ims.stub import PORTAL, StubImsClient
from app.models import ImsDecision, ImsDocument, ImsReconciliation, OutboxEvent, PurchaseDocument
from app.schemas import (
    GstRegistrationUpsert,
    ImsDecisionCreate,
    PurchaseDocumentCreate,
)
from platform_core.errors import BusinessRuleProblem, NotFoundProblem
from tests.helpers import admin_ctx, service_token_headers

pytestmark = pytest.mark.integration

SYNC = "/internal/v1/ims/sync"
QUEUE = "/api/v1/finance/ims/documents"

#: The recipient: this organization, in Maharashtra.
SELLER = "27AABCA9012D1Z5"
PERIOD = "2026-08"

#: The stub's suppliers, by the story each one tells.
MERIDIAN = "24AABCM1234F1Z3"  # bills what was ordered      -> matched
KANAKA = "33AAACK5678G1ZH"  # bills more than was ordered  -> amount_mismatch
SAHYADRI = "29AADCS3456J1Z0"  # nobody has heard of them     -> supplier_unknown
ORBIT = "27AAECO9012H1ZG"  # known, but this number is not -> no_purchase_record


# --------------------------------------------------------------------- fixtures


async def _register(session, ctx) -> None:
    await services.set_gst_registration(
        session,
        ctx,
        GstRegistrationUpsert(gstin=SELLER, legal_name="OpsGrids Industries Private Limited"),
    )


def _purchase(
    supplier_gstin: str, number: str, taxable: int, rate: float, **overrides
) -> PurchaseDocumentCreate:
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
    """A purchase book positioned so the stub's five documents produce five different reasons.

    Meridian's invoice was ordered and recorded exactly. Kanaka's was recorded for less than they
    have billed. Orbit is a known supplier, but the number they filed is not one on file. Nothing
    at all has been bought from Sahyadri.
    """
    await services.record_purchase_document(
        session, ctx, _purchase(MERIDIAN, "MT-4471", 4_50_000, 18, supplier_name="Meridian")
    )
    await services.record_purchase_document(
        session, ctx, _purchase(KANAKA, "KF-2210", 2_50_000, 12, supplier_name="Kanaka Foods")
    )
    await services.record_purchase_document(
        session, ctx, _purchase(ORBIT, "OL-8000", 90_000, 5, supplier_name="Orbit Logistics")
    )


async def _sync(client, org_id, period: str = PERIOD):
    return await client.post(
        SYNC,
        headers=service_token_headers(),
        json={"organization_id": str(org_id), "period": period},
    )


async def _by_ref(session, org_id) -> dict[str, ImsDocument]:
    rows = await selectors.all_ims_documents(session, org_id)
    return {row.source_ref: row for row in rows}


async def _reasons(session, org_id) -> dict[str, str]:
    """`source_ref` → the reason of its newest suggestion."""
    documents = await selectors.all_ims_documents(session, org_id)
    latest = await selectors.latest_reconciliations(session, org_id, [d.id for d in documents])
    return {d.source_ref: latest[d.id].reason for d in documents if d.id in latest}


# --------------------------------------------------------------------- the stub


class TestTheStubbedPortal:
    async def test_fetching_needs_no_credentials_and_opens_no_socket(self) -> None:
        """The one seam, and a test that would fail the moment somebody made the stub reach out."""
        import socket

        original = socket.socket

        def _forbidden(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("the stub IMS client must not open a socket")

        socket.socket = _forbidden  # type: ignore[assignment]
        try:
            documents = StubImsClient().fetch(gstin=SELLER, period=PERIOD)
        finally:
            socket.socket = original  # type: ignore[assignment]
        assert len(documents) == 5
        assert all(isinstance(d, ImsSupplierDocument) for d in documents)

    async def test_the_fixtures_tax_is_computed_not_typed(self) -> None:
        """Every head comes from `domain.split_gst`, so the feed foots exactly and cannot disagree
        with the reconciler about arithmetic. A Maharashtra supplier is CGST+SGST for this
        Maharashtra recipient; everybody else is IGST."""
        documents = {d.source_ref: d for d in StubImsClient().fetch(gstin=SELLER, period=PERIOD)}
        for doc in documents.values():
            assert doc.taxable_minor + doc.tax_minor == doc.total_minor
            assert not (doc.igst_minor and (doc.cgst_minor or doc.sgst_minor))

        intra = documents[f"{PERIOD}/0005"]  # Orbit, Maharashtra
        assert intra.supplier_gstin == ORBIT
        assert intra.igst_minor == 0
        assert (intra.cgst_minor, intra.sgst_minor) == (2_250, 2_250)

        inter = documents[f"{PERIOD}/0001"]  # Meridian, Gujarat
        expected = domain.split_gst(4_50_000, Decimal("18"), intra_state=False)
        assert (inter.cgst_minor, inter.sgst_minor, inter.igst_minor) == (
            0,
            0,
            expected.igst_minor,
        )

    async def test_the_recipient_state_decides_the_heads(self) -> None:
        """Not decoration: the same fixture is CGST+SGST for one recipient and IGST for another,
        which is what a real feed does and what a reconciler that only ever saw one shape would
        mis-handle."""
        gujarat_recipient = "24AAHCA6789M1Z0"
        for doc in StubImsClient().fetch(gstin=gujarat_recipient, period=PERIOD):
            if doc.supplier_gstin.startswith("24"):
                assert doc.igst_minor == 0 and doc.cgst_minor
            else:
                assert doc.igst_minor and not doc.cgst_minor

    async def test_a_malformed_period_is_refused_before_anything_is_fetched(self) -> None:
        for bad in ("2026", "2026-13", "august", "26-08"):
            with pytest.raises(ValueError):
                StubImsClient().fetch(gstin=SELLER, period=bad)


# --------------------------------------------------------------------- ingest


class TestIngest:
    async def test_a_sync_stores_the_portals_documents(self, client, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)

        res = await _sync(client, org_id)
        assert res.status_code == 200
        body = res.json()
        assert body["fetched"] == 5
        assert body["created"] == 5
        assert body["reconciled"] == 5
        assert body["portal"] == PORTAL

        async with session.begin():
            documents = await selectors.all_ims_documents(session, org_id)
        assert len(documents) == 5
        assert {d.status for d in documents} == {"pending"}
        assert {d.period for d in documents} == {PERIOD}

    async def test_re_syncing_a_period_creates_nothing(self, client, session, org_id) -> None:
        """Idempotent on the portal's own row reference. The number is the assertion: `fetched`
        stays at five and `created` falls to zero, which is what idempotency looks like from
        outside rather than a claim about it."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
        await _sync(client, org_id)
        again = (await _sync(client, org_id)).json()

        assert again["fetched"] == 5
        assert again["created"] == 0
        async with session.begin():
            total = (
                await session.execute(
                    select(func.count())
                    .select_from(ImsDocument)
                    .where(ImsDocument.organization_id == org_id)
                )
            ).scalar_one()
        assert total == 5

    async def test_two_periods_are_additive(self, client, session, org_id) -> None:
        """The portal reference carries the period, so syncing July after August adds rather than
        collides — and the queue can be worked one period at a time."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
        await _sync(client, org_id, "2026-08")
        await _sync(client, org_id, "2026-07")
        async with session.begin():
            august = await selectors.all_ims_documents(session, org_id, period="2026-08")
            july = await selectors.all_ims_documents(session, org_id, period="2026-07")
        assert len(august) == 5
        assert len(july) == 5
        assert not {d.source_ref for d in august} & {d.source_ref for d in july}

    async def test_an_organization_with_no_registration_cannot_sync(
        self, client, session, org_id
    ) -> None:
        """There is no supplier ledger to fetch without knowing whose it is."""
        res = await _sync(client, org_id)
        assert res.status_code == 422
        assert "no GST registration configured" in res.json()["detail"]
        async with session.begin():
            assert await selectors.all_ims_documents(session, org_id) == []

    async def test_a_malformed_period_is_refused(self, client, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
        res = await _sync(client, org_id, "2026-13")
        assert res.status_code == 422
        assert "does not exist" in res.json()["detail"]

    async def test_a_portal_outage_leaves_nothing_behind(self, session, org_id) -> None:
        """Unlike a filing, a failed fetch asserts nothing to anybody, so there is no attempt to
        record — and the caller must retry rather than conclude the period was empty."""

        class DownPortal:
            name = "down-ims"

            def fetch(self, *, gstin: str, period: str) -> list[ImsSupplierDocument]:
                raise ImsUnavailable("the portal did not answer in time")

        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
        async with session.begin():
            from platform_core.errors import UpstreamUnavailableProblem

            with pytest.raises(UpstreamUnavailableProblem):
                await services.ingest_ims_documents(session, ctx, DownPortal(), period=PERIOD)
        async with session.begin():
            assert await selectors.all_ims_documents(session, org_id) == []

    async def test_an_unusable_supplier_gstin_refuses_the_sync_and_names_the_row(
        self, session, org_id
    ) -> None:
        """This service stores no unverified GSTIN anywhere. Refusing the whole sync is the harsher
        of the two choices — the other being to drop the row — and the right one: silently
        dropping documents from a compliance queue is how a credit goes unclaimed with nothing on
        any screen to show for it."""

        class BadPortal:
            name = "bad-ims"

            def fetch(self, *, gstin: str, period: str) -> list[ImsSupplierDocument]:
                return [
                    ImsSupplierDocument(
                        source_ref=f"{period}/9001",
                        supplier_gstin="27AABCA9012D1Z9",  # wrong check digit
                        supplier_name="Typo Traders",
                        document_number="TT-1",
                        document_type="invoice",
                        document_date=date(2026, 8, 3),
                        period=period,
                        taxable_minor=1000,
                        cgst_minor=0,
                        sgst_minor=0,
                        igst_minor=180,
                        total_minor=1180,
                    )
                ]

        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
        async with session.begin():
            with pytest.raises(BusinessRuleProblem, match="9001"):
                await services.ingest_ims_documents(session, ctx, BadPortal(), period=PERIOD)

    async def test_each_new_document_is_announced(self, client, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
        await _sync(client, org_id)
        async with session.begin():
            events = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.organization_id == org_id,
                            OutboxEvent.event_type == services.EVT_IMS_DOCUMENT_INGESTED,
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(events) == 5
        assert {e.aggregate_type for e in events} == {"ims_document"}

    async def test_re_syncing_announces_nothing_new(self, client, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
        await _sync(client, org_id)
        await _sync(client, org_id)
        async with session.begin():
            count = (
                await session.execute(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(
                        OutboxEvent.organization_id == org_id,
                        OutboxEvent.event_type == services.EVT_IMS_DOCUMENT_INGESTED,
                    )
                )
            ).scalar_one()
        assert count == 5


# --------------------------------------------------------------------- the purchase book


class TestThePurchaseBook:
    async def test_the_tax_split_is_computed_by_the_one_tax_function(self, session, org_id) -> None:
        """A purchase document's heads are derived from its taxable value and rate with the same
        `domain.split_gst` an invoice uses. Accepting them from the caller would put a second
        implementation of the platform's one exactly-footing calculation into the purchase book."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            inter = await services.record_purchase_document(
                session, ctx, _purchase(MERIDIAN, "MT-1", 4_50_000, 18)
            )
            intra = await services.record_purchase_document(
                session, ctx, _purchase(ORBIT, "OL-1", 4_50_000, 18)
            )
        expected = domain.split_gst(4_50_000, Decimal("18"), intra_state=False)
        assert (inter.cgst_minor, inter.sgst_minor, inter.igst_minor) == (
            0,
            0,
            expected.igst_minor,
        )
        assert inter.total_minor == 4_50_000 + expected.igst_minor
        # Same state as the recipient, so CGST+SGST — and the halves sum to the whole.
        assert intra.igst_minor == 0
        assert intra.cgst_minor + intra.sgst_minor == expected.igst_minor
        assert intra.total_minor == inter.total_minor

    async def test_a_supplier_gstin_is_checksum_verified(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            with pytest.raises(BusinessRuleProblem, match="GSTIN is not valid"):
                await services.record_purchase_document(
                    session, ctx, _purchase("24AABCM1234F1Z5", "MT-2", 1000, 18)
                )

    async def test_the_same_document_twice_is_a_conflict(self, session, org_id) -> None:
        from platform_core.errors import ConflictProblem

        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            await services.record_purchase_document(
                session, ctx, _purchase(MERIDIAN, "MT-3", 1000, 18)
            )
        async with session.begin():
            with pytest.raises(ConflictProblem, match="already recorded"):
                await services.record_purchase_document(
                    session, ctx, _purchase(MERIDIAN, "MT-3", 1000, 18)
                )

    async def test_two_suppliers_may_use_the_same_number(self, session, org_id) -> None:
        """A document number is unique per supplier, not globally. Two suppliers numbering their
        invoices `INV-1` is the ordinary case rather than a collision."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            await services.record_purchase_document(
                session, ctx, _purchase(MERIDIAN, "INV-1", 1000, 18)
            )
            await services.record_purchase_document(
                session, ctx, _purchase(KANAKA, "INV-1", 1000, 18)
            )
        async with session.begin():
            book = await selectors.all_purchase_documents(session, org_id)
        assert len(book) == 2


# --------------------------------------------------------------------- the suggestions


class TestSuggestionReasons:
    """One test per reason class, against the real stub feed and a purchase book positioned to
    produce it. The reason is what an operator works from — an action alone leaves them to
    re-derive it for every row — so each is asserted with the sentence that explains it."""

    @pytest.fixture
    async def reconciled(self, client, session, org_id):
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            await _seed_purchase_book(session, ctx)
        await _sync(client, org_id)
        async with session.begin():
            return await _reasons(session, org_id)

    async def test_an_exact_match_is_suggested_for_acceptance(
        self, reconciled, session, org_id
    ) -> None:
        assert reconciled[f"{PERIOD}/0001"] == domain.REASON_MATCHED
        async with session.begin():
            documents = await _by_ref(session, org_id)
            latest = await selectors.latest_reconciliations(
                session, org_id, [documents[f"{PERIOD}/0001"].id]
            )
        suggestion = latest[documents[f"{PERIOD}/0001"].id]
        assert suggestion.suggested_action == "accept"
        assert suggestion.delta_minor == 0
        assert suggestion.matched_purchase_id is not None
        assert "MT-4471" in suggestion.detail

    async def test_a_money_difference_names_the_delta(self, reconciled, session, org_id) -> None:
        """ "Amount mismatch" on its own is not actionable. The delta is carried signed and in minor
        units, and the sentence says which way it goes — the supplier is billing more than was
        recorded, which is a conversation with them rather than a correction here."""
        assert reconciled[f"{PERIOD}/0002"] == domain.REASON_AMOUNT_MISMATCH
        async with session.begin():
            documents = await _by_ref(session, org_id)
            doc = documents[f"{PERIOD}/0002"]
            latest = await selectors.latest_reconciliations(session, org_id, [doc.id])
        suggestion = latest[doc.id]
        assert suggestion.suggested_action == "pending_action"
        # Kanaka billed 2,80,000 taxable at 12 %; the purchase order recorded 2,50,000 at 12 %.
        assert suggestion.delta_minor == doc.total_minor - (2_50_000 + 30_000)
        assert suggestion.delta_minor > 0
        assert "more" in suggestion.detail
        assert str(suggestion.delta_minor) in suggestion.detail
        assert suggestion.matched_purchase_id is not None

    async def test_an_unknown_supplier_is_suggested_for_rejection(
        self, reconciled, session, org_id
    ) -> None:
        """A document from a GSTIN nothing has ever been bought from is the classic mis-addressed
        or fraudulent filing, and it is the one reason class where `reject` is the right default."""
        assert reconciled[f"{PERIOD}/0003"] == domain.REASON_SUPPLIER_UNKNOWN
        async with session.begin():
            documents = await _by_ref(session, org_id)
            doc = documents[f"{PERIOD}/0003"]
            latest = await selectors.latest_reconciliations(session, org_id, [doc.id])
        assert doc.supplier_gstin == SAHYADRI
        assert latest[doc.id].suggested_action == "reject"
        assert latest[doc.id].matched_purchase_id is None
        assert SAHYADRI in latest[doc.id].detail

    async def test_a_second_copy_is_the_duplicate_and_the_first_is_not(
        self, reconciled, session, org_id
    ) -> None:
        """Duplicate beats match, and it is decided **before** the purchase book is consulted: a
        second copy of a document that already matched a purchase order still matches it, so a
        match-first ordering would suggest claiming the same credit twice."""
        assert reconciled[f"{PERIOD}/0004"] == domain.REASON_DUPLICATE
        assert reconciled[f"{PERIOD}/0001"] == domain.REASON_MATCHED
        async with session.begin():
            documents = await _by_ref(session, org_id)
            latest = await selectors.latest_reconciliations(
                session, org_id, [documents[f"{PERIOD}/0004"].id]
            )
        suggestion = latest[documents[f"{PERIOD}/0004"].id]
        assert suggestion.suggested_action == "reject"
        assert suggestion.duplicate_of_id == documents[f"{PERIOD}/0001"].id

    async def test_a_known_supplier_with_an_unrecorded_number_is_parked(self, reconciled) -> None:
        """Usually a timing difference rather than a problem — which is why it is parked for a
        person and not rejected."""
        assert reconciled[f"{PERIOD}/0005"] == domain.REASON_NO_PURCHASE_RECORD

    async def test_every_reason_class_is_exercised(self, reconciled) -> None:
        """A feed that only ever produced clean matches would leave four fifths of this untested
        by eye. Five documents, five reasons."""
        assert set(reconciled.values()) == domain.IMS_REASONS

    async def test_no_document_is_accepted_by_the_reconciliation(
        self, reconciled, session, org_id
    ) -> None:
        """**The restraint.** Even the exact match — where the answer is obvious — stays `pending`.
        Accepting claims the input tax credit on somebody else's invoice, and that is a decision a
        person is accountable for and a program is not."""
        async with session.begin():
            documents = await selectors.all_ims_documents(session, org_id)
            decisions = (
                await session.execute(
                    select(func.count())
                    .select_from(ImsDecision)
                    .where(ImsDecision.organization_id == org_id)
                )
            ).scalar_one()
        assert {d.status for d in documents} == {"pending"}
        assert decisions == 0

    async def test_re_running_appends_rather_than_overwrites(
        self, reconciled, session, org_id
    ) -> None:
        """A suggestion is recomputed opinion, so a run is appended and the newest wins — which is
        how "why did this row's advice change when the purchase book did" reads off the history."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            first = (
                await session.execute(
                    select(func.count())
                    .select_from(ImsReconciliation)
                    .where(ImsReconciliation.organization_id == org_id)
                )
            ).scalar_one()
            result = await services.reconcile_ims_documents(session, ctx)
            second = (
                await session.execute(
                    select(func.count())
                    .select_from(ImsReconciliation)
                    .where(ImsReconciliation.organization_id == org_id)
                )
            ).scalar_one()
        assert first == 5
        assert second == 10
        assert result.reconciled == 5

    async def test_recording_the_missing_purchase_changes_the_advice(
        self, reconciled, client, session, org_id
    ) -> None:
        """The reason the two halves are separable. The supplier's document did not change; our
        record of it did, and re-running costs no fetch."""
        ctx = admin_ctx(org_id)
        assert reconciled[f"{PERIOD}/0005"] == domain.REASON_NO_PURCHASE_RECORD
        async with session.begin():
            await services.record_purchase_document(
                session, ctx, _purchase(ORBIT, "OL-9002", 90_000, 5, supplier_name="Orbit")
            )
            await services.reconcile_ims_documents(session, ctx)
        async with session.begin():
            now = await _reasons(session, org_id)
        assert now[f"{PERIOD}/0005"] == domain.REASON_MATCHED


class TestTheSuggestionRules:
    """The pure function on its own, where the cases that are awkward to stage end to end are
    cheap: an empty book, a tie between two candidate purchase documents, and the financial-year
    boundary that decides whether two identically numbered documents are the same one."""

    def _inbound(self, **overrides: Any) -> domain.InboundDocument:
        base: dict[str, Any] = {
            "id": uuid.uuid4(),
            "source_ref": "2026-08/0001",
            "supplier_gstin": MERIDIAN,
            "document_type": "invoice",
            "document_number": "MT-1",
            "document_date": date(2026, 8, 4),
            "taxable_minor": 100_000,
            "total_minor": 118_000,
            "first_seen": datetime(2026, 9, 1, tzinfo=UTC),
        }
        base.update(overrides)
        return domain.InboundDocument(**base)

    def _record(self, **overrides: Any) -> domain.PurchaseRecord:
        base: dict[str, Any] = {
            "id": uuid.uuid4(),
            "supplier_gstin": MERIDIAN,
            "document_type": "invoice",
            "document_number": "MT-1",
            "document_date": date(2026, 8, 1),
            "taxable_minor": 100_000,
            "total_minor": 118_000,
        }
        base.update(overrides)
        return domain.PurchaseRecord(**base)

    def test_an_empty_purchase_book_makes_every_supplier_unknown(self) -> None:
        doc = self._inbound()
        assert (
            domain.suggest_action(doc, purchases=[], siblings=[doc]).reason
            == domain.REASON_SUPPLIER_UNKNOWN
        )

    def test_the_document_number_is_matched_case_and_space_insensitively(self) -> None:
        """A supplier writing `mt 1` and a clerk typing `MT-1` are not a mismatch worth a human's
        afternoon. Hyphens are kept — they are part of a number — but case and spacing are not."""
        doc = self._inbound(document_number=" mt-1 ")
        suggestion = domain.suggest_action(doc, purchases=[self._record()], siblings=[doc])
        assert suggestion.reason == domain.REASON_MATCHED

    def test_the_same_number_in_a_different_financial_year_is_a_different_document(self) -> None:
        """Numbers restart each April, so `MT-1` in 2026-27 and `MT-1` in 2025-26 are two
        documents. Matching them would accept a credit twice a year apart."""
        doc = self._inbound(document_date=date(2026, 8, 4))
        old = self._record(document_date=date(2026, 3, 30))  # FY 2025-26
        assert (
            domain.suggest_action(doc, purchases=[old], siblings=[doc]).reason
            == domain.REASON_NO_PURCHASE_RECORD
        )

    def test_a_mismatch_picks_the_nearest_candidate(self) -> None:
        """When several recorded documents share the number, the delta shown is the smallest one
        the operator has to explain."""
        doc = self._inbound(total_minor=120_000)
        near = self._record(total_minor=119_000)
        far = self._record(total_minor=50_000)
        suggestion = domain.suggest_action(doc, purchases=[far, near], siblings=[doc])
        assert suggestion.reason == domain.REASON_AMOUNT_MISMATCH
        assert suggestion.delta_minor == 1_000
        assert suggestion.matched_purchase_id == near.id

    def test_a_lower_supplier_total_is_reported_as_less(self) -> None:
        doc = self._inbound(total_minor=100_000)
        suggestion = domain.suggest_action(doc, purchases=[self._record()], siblings=[doc])
        assert suggestion.delta_minor == -18_000
        assert "less" in suggestion.detail

    def test_the_duplicate_verdict_is_stable_across_runs(self) -> None:
        """Two copies fetched in the same sync share a `first_seen` to the microsecond, so the
        portal's own reference breaks the tie — and gives the same answer every time rather than
        one that depends on a random row id."""
        seen = datetime(2026, 9, 1, tzinfo=UTC)
        first = self._inbound(source_ref="2026-08/0001", first_seen=seen)
        second = self._inbound(source_ref="2026-08/0004", first_seen=seen)
        book = [self._record()]
        assert (
            domain.suggest_action(first, purchases=book, siblings=[first, second]).reason
            == domain.REASON_MATCHED
        )
        for _ in range(5):
            verdict = domain.suggest_action(second, purchases=book, siblings=[second, first])
            assert verdict.reason == domain.REASON_DUPLICATE
            assert verdict.duplicate_of_id == first.id


# --------------------------------------------------------------------- decisions


class TestDecisions:
    @pytest.fixture
    async def queued(self, client, session, org_id):
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            await _seed_purchase_book(session, ctx)
        await _sync(client, org_id)
        async with session.begin():
            return await _by_ref(session, org_id)

    async def test_a_decision_moves_the_status_and_records_who_and_when(
        self, queued, client, session, org_id
    ) -> None:
        document = queued[f"{PERIOD}/0001"]
        res = await client.post(
            f"{QUEUE}/{document.id}/decisions",
            json={"action": "accept", "note": "Goods received against PO 4471"},
        )
        assert res.status_code == 201
        body = res.json()
        assert body["action"] == "accept"
        assert body["decided_by"]
        assert body["decided_at"]
        # What the platform advised at the time, recorded beside what the person chose.
        assert body["suggested_action"] == "accept"
        assert body["suggestion_reason"] == domain.REASON_MATCHED

        async with session.begin():
            refreshed = await selectors.get_ims_document(session, org_id, document.id)
        assert refreshed is not None
        assert refreshed.status == "accepted"

    async def test_the_status_projection_agrees_with_the_newest_decision(
        self, queued, client, session, org_id
    ) -> None:
        """`ims_documents.status` is a projection, kept as a column because the queue's whole
        access path is "every pending document for August" and that is not a query built from a
        lateral join on every list page. This is what keeps the shortcut honest."""
        document = queued[f"{PERIOD}/0002"]
        for action, expected in (
            ("pending_action", "pending_action"),
            ("reject", "rejected"),
            ("accept", "accepted"),
        ):
            res = await client.post(f"{QUEUE}/{document.id}/decisions", json={"action": action})
            assert res.status_code == 201
            async with session.begin():
                refreshed = await selectors.get_ims_document(session, org_id, document.id)
                history = await selectors.decisions_for_document(session, org_id, document.id)
            assert refreshed is not None
            assert refreshed.status == expected
            assert history[0].action == action

    async def test_a_change_of_mind_is_a_further_row(self, queued, client, session, org_id) -> None:
        """Never an edit. The question asked later is not "what does it say now" but "who decided
        this, when, and what were they shown" — and the superseded decision stays visible with the
        correction after it."""
        document = queued[f"{PERIOD}/0003"]
        await client.post(f"{QUEUE}/{document.id}/decisions", json={"action": "reject"})
        await client.post(
            f"{QUEUE}/{document.id}/decisions",
            json={"action": "accept", "note": "Confirmed with the supplier"},
        )
        async with session.begin():
            history = await selectors.decisions_for_document(session, org_id, document.id)
        assert [d.action for d in history] == ["accept", "reject"]
        assert history[0].note == "Confirmed with the supplier"

    async def test_disagreeing_with_the_suggestion_is_recorded_as_such(
        self, queued, client, session, org_id
    ) -> None:
        """The number worth watching: a suggestion nobody follows is a suggestion that should be
        changed, and that is only knowable if the disagreement is written down."""
        document = queued[f"{PERIOD}/0003"]  # suggested `reject`
        await client.post(f"{QUEUE}/{document.id}/decisions", json={"action": "accept"})
        async with session.begin():
            events = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.organization_id == org_id,
                            OutboxEvent.event_type == services.EVT_IMS_DOCUMENT_DECIDED,
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(events) == 1
        payload = events[0].payload
        assert payload["action"] == "accept"
        assert payload["suggested_action"] == "reject"
        assert payload["followed_suggestion"] is False

    async def test_an_unknown_action_is_refused(self, queued, client) -> None:
        document = queued[f"{PERIOD}/0001"]
        res = await client.post(f"{QUEUE}/{document.id}/decisions", json={"action": "maybe"})
        assert res.status_code == 422

    async def test_deciding_someone_elses_document_is_a_404(self, client) -> None:
        res = await client.post(f"{QUEUE}/{uuid.uuid4()}/decisions", json={"action": "accept"})
        assert res.status_code == 404

    async def test_the_service_refuses_an_unknown_document(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            with pytest.raises(NotFoundProblem):
                await services.record_ims_decision(
                    session, ctx, uuid.uuid4(), ImsDecisionCreate(action="accept")
                )


class TestDecisionsAreAppendOnlyAtTheDatabase:
    """The guarantee, checked where it is made rather than where it is convenient.

    A `REVOKE UPDATE, DELETE` would not have held: `apply_schema_grants` re-grants DML on every
    table at the end of every migration run. The trigger is independent of privileges, and these
    tests run as the same NOBYPASSRLS app role the service uses — so what they prove is what
    production has.
    """

    async def _decided(self, client, session, org_id) -> uuid.UUID:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
        await _sync(client, org_id)
        async with session.begin():
            documents = await _by_ref(session, org_id)
        document = documents[f"{PERIOD}/0001"]
        await client.post(f"{QUEUE}/{document.id}/decisions", json={"action": "accept"})
        async with session.begin():
            history = await selectors.decisions_for_document(session, org_id, document.id)
        return history[0].id

    async def test_an_update_is_refused_by_the_database(self, client, session, org_id) -> None:
        decision_id = await self._decided(client, session, org_id)
        with pytest.raises(Exception, match="append-only"):
            async with session.begin():
                await session.execute(
                    update(ImsDecision).where(ImsDecision.id == decision_id).values(action="reject")
                )

    async def test_a_delete_is_refused_by_the_database(self, client, session, org_id) -> None:
        decision_id = await self._decided(client, session, org_id)
        with pytest.raises(Exception, match="append-only"):
            async with session.begin():
                await session.execute(delete(ImsDecision).where(ImsDecision.id == decision_id))

    async def test_a_soft_delete_is_refused_too(self, client, session, org_id) -> None:
        """`deleted_at` is on the row because every tenant table carries it. Setting it is still an
        UPDATE, so the trigger refuses that route to erasure as well."""
        decision_id = await self._decided(client, session, org_id)
        with pytest.raises(Exception, match="append-only"):
            async with session.begin():
                await session.execute(
                    update(ImsDecision)
                    .where(ImsDecision.id == decision_id)
                    .values(deleted_at=datetime.now(UTC))
                )

    async def test_the_trigger_is_installed_on_the_table(self, session) -> None:
        """A guard against the guard: if the migration stopped creating the trigger, the tests
        above would pass for the wrong reason only if something else refused the write — this
        asserts the actual mechanism is present."""
        async with session.begin():
            found = (
                (
                    await session.execute(
                        text(
                            "SELECT tgname FROM pg_trigger t "
                            "JOIN pg_class c ON c.oid = t.tgrelid "
                            "JOIN pg_namespace n ON n.oid = c.relnamespace "
                            "WHERE n.nspname = 'finance' AND c.relname = 'ims_decisions' "
                            "AND NOT t.tgisinternal"
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert "trg_ims_decisions_append_only" in found

    async def test_suggestions_are_deliberately_not_append_only(
        self, client, session, org_id
    ) -> None:
        """Opinion, not evidence. Freezing a recomputed suggestion buys an auditor nothing and
        would only mean a stale one could never be superseded."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
        await _sync(client, org_id)
        async with session.begin():
            documents = await selectors.all_ims_documents(session, org_id)
            latest = await selectors.latest_reconciliations(session, org_id, [documents[0].id])
            row_id = latest[documents[0].id].id
        async with session.begin():
            await session.execute(
                update(ImsReconciliation)
                .where(ImsReconciliation.id == row_id)
                .values(detail="recomputed")
            )
        async with session.begin():
            row = await session.get(ImsReconciliation, row_id)
        assert row is not None
        assert row.detail == "recomputed"


# --------------------------------------------------------------------- the queue endpoints


class TestTheQueueEndpoints:
    @pytest.fixture
    async def queued(self, client, session, org_id):
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            await _seed_purchase_book(session, ctx)
        await _sync(client, org_id)
        async with session.begin():
            return await _by_ref(session, org_id)

    async def test_the_queue_lists_every_document_with_its_suggestion(self, queued, client) -> None:
        res = await client.get(QUEUE, params={"size": 50})
        assert res.status_code == 200
        body = res.json()
        assert body["total"] == 5
        by_ref = {row["source_ref"]: row for row in body["items"]}
        assert by_ref[f"{PERIOD}/0001"]["suggestion"]["reason"] == domain.REASON_MATCHED
        assert by_ref[f"{PERIOD}/0001"]["decision"] is None
        assert by_ref[f"{PERIOD}/0001"]["status"] == "pending"
        # Oldest first: a queue is worked forward, toward the deadline.
        assert [row["document_date"] for row in body["items"]] == sorted(
            row["document_date"] for row in body["items"]
        )

    async def test_the_queue_filters_by_reason(self, queued, client) -> None:
        res = await client.get(QUEUE, params={"reason": domain.REASON_DUPLICATE})
        assert res.status_code == 200
        items = res.json()["items"]
        assert [row["source_ref"] for row in items] == [f"{PERIOD}/0004"]

    async def test_the_queue_filters_by_status_supplier_and_period(self, queued, client) -> None:
        document = queued[f"{PERIOD}/0001"]
        await client.post(f"{QUEUE}/{document.id}/decisions", json={"action": "accept"})

        pending = await client.get(QUEUE, params={"status": "pending", "size": 50})
        assert pending.json()["total"] == 4
        accepted = await client.get(QUEUE, params={"status": "accepted"})
        assert [row["source_ref"] for row in accepted.json()["items"]] == [f"{PERIOD}/0001"]

        by_supplier = await client.get(QUEUE, params={"supplier_gstin": MERIDIAN, "size": 50})
        assert by_supplier.json()["total"] == 2
        wrong_period = await client.get(QUEUE, params={"period": "2026-07"})
        assert wrong_period.json()["total"] == 0

    async def test_the_queue_searches_by_supplier_and_number(self, queued, client) -> None:
        res = await client.get(QUEUE, params={"q": "kanaka"})
        assert [row["source_ref"] for row in res.json()["items"]] == [f"{PERIOD}/0002"]
        by_number = await client.get(QUEUE, params={"q": "zr-0088"})
        assert [row["source_ref"] for row in by_number.json()["items"]] == [f"{PERIOD}/0003"]

    async def test_the_detail_carries_the_matched_purchase_document(self, queued, client) -> None:
        """The evidence, not a verdict. An operator asked to accept somebody else's claim needs to
        see what it was matched to."""
        res = await client.get(f"{QUEUE}/{queued[f'{PERIOD}/0001'].id}")
        assert res.status_code == 200
        body = res.json()
        assert body["document"]["source_ref"] == f"{PERIOD}/0001"
        assert body["matched_purchase"]["number"] == "MT-4471"
        assert body["matched_purchase"]["total_minor"] == body["document"]["total_minor"]
        assert body["duplicate_of"] is None
        assert body["decisions"] == []

    async def test_the_detail_carries_the_document_a_duplicate_duplicates(
        self, queued, client
    ) -> None:
        res = await client.get(f"{QUEUE}/{queued[f'{PERIOD}/0004'].id}")
        body = res.json()
        assert body["document"]["suggestion"]["reason"] == domain.REASON_DUPLICATE
        assert body["duplicate_of"]["source_ref"] == f"{PERIOD}/0001"

    async def test_the_detail_carries_no_evidence_when_there_is_none(self, queued, client) -> None:
        """The absence is an answer too: nothing has ever been bought from this supplier, and the
        screen has to be able to say so rather than showing an empty panel."""
        res = await client.get(f"{QUEUE}/{queued[f'{PERIOD}/0003'].id}")
        body = res.json()
        assert body["document"]["suggestion"]["reason"] == domain.REASON_SUPPLIER_UNKNOWN
        assert body["matched_purchase"] is None
        assert body["duplicate_of"] is None

    async def test_the_detail_carries_the_decision_history(self, queued, client) -> None:
        document = queued[f"{PERIOD}/0002"]
        await client.post(f"{QUEUE}/{document.id}/decisions", json={"action": "pending_action"})
        await client.post(f"{QUEUE}/{document.id}/decisions", json={"action": "accept"})
        body = (await client.get(f"{QUEUE}/{document.id}")).json()
        assert [d["action"] for d in body["decisions"]] == ["accept", "pending_action"]
        assert body["document"]["decision"]["action"] == "accept"

    async def test_an_unknown_document_is_a_404(self, client) -> None:
        res = await client.get(f"{QUEUE}/{uuid.uuid4()}")
        assert res.status_code == 404


# --------------------------------------------------------------------- tenancy


class TestCrossTenantIsolation:
    async def test_another_tenant_sees_none_of_the_queue(self, client, session, org_id) -> None:
        """A supplier list is as sensitive as an invoice book, and arguably more: it names who a
        competitor buys from. Asserted through the app role, so a policy regression fails here
        rather than passing silently under a bypassing connection."""
        ctx = admin_ctx(org_id)
        async with session.begin():
            await _register(session, ctx)
            await _seed_purchase_book(session, ctx)
        await _sync(client, org_id)
        async with session.begin():
            documents = await selectors.all_ims_documents(session, org_id)
        assert len(documents) == 5
        mine = documents[0].id

        stranger = uuid.uuid4()
        async with session.begin():
            assert await selectors.all_ims_documents(session, stranger) == []
            assert await selectors.all_purchase_documents(session, stranger) == []
            assert await selectors.get_ims_document(session, stranger, mine) is None
            assert await selectors.latest_reconciliations(session, stranger, [mine]) == {}
            assert await selectors.latest_decisions(session, stranger, [mine]) == {}
            assert await selectors.decisions_for_document(session, stranger, mine) == []

    async def test_a_document_cannot_be_written_into_another_tenant(self, session, org_id) -> None:
        """`WITH CHECK`, not just `USING`. Without it a tenant could plant a supplier document in
        somebody else's queue — invisible to them, fully visible to the victim."""
        from sqlalchemy import event
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.config import settings
        from app.models import SCHEMA
        from platform_core.db import TENANT_GUC

        other = uuid.uuid4()
        engine = create_async_engine(
            settings().app_dsn,
            connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
        )
        maker = async_sessionmaker(engine, expire_on_commit=False)
        s = maker()

        @event.listens_for(s.sync_session, "after_begin")
        def _bind(_session, _transaction, connection) -> None:  # type: ignore[no-untyped-def]
            connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{other}', true)")

        try:
            with pytest.raises(Exception, match="row-level security"):
                async with s.begin():
                    s.add(
                        ImsDocument(
                            organization_id=org_id,  # not this session's tenant
                            source_ref="planted/0001",
                            supplier_gstin=MERIDIAN,
                            document_number="X-1",
                            document_date=date(2026, 8, 1),
                            period=PERIOD,
                            taxable_minor=1,
                            total_minor=1,
                        )
                    )
                    await s.flush()
        finally:
            await s.close()
            await engine.dispose()

    async def test_a_purchase_document_cannot_be_written_into_another_tenant(
        self, session, org_id
    ) -> None:
        from sqlalchemy import event
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.config import settings
        from app.models import SCHEMA
        from platform_core.db import TENANT_GUC

        other = uuid.uuid4()
        engine = create_async_engine(
            settings().app_dsn,
            connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
        )
        maker = async_sessionmaker(engine, expire_on_commit=False)
        s = maker()

        @event.listens_for(s.sync_session, "after_begin")
        def _bind(_session, _transaction, connection) -> None:  # type: ignore[no-untyped-def]
            connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{other}', true)")

        try:
            with pytest.raises(Exception, match="row-level security"):
                async with s.begin():
                    s.add(
                        PurchaseDocument(
                            organization_id=org_id,
                            supplier_gstin=MERIDIAN,
                            number="X-1",
                            document_date=date(2026, 8, 1),
                            taxable_minor=1,
                            total_minor=1,
                        )
                    )
                    await s.flush()
        finally:
            await s.close()
            await engine.dispose()
