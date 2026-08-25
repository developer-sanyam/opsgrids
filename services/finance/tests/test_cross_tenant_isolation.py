"""Row-level security, exercised as the app role — the isolation the whole platform rests on.

Seed a book into org A, then bind a fresh transaction to org B and confirm none of A's rows are
reachable: not through a list, not through a paid-amount rollup, and not by naming A's invoice id
directly. Run as the **app** role (NOBYPASSRLS), so a policy regression fails here rather than
passing silently under a bypassing connection.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import selectors, services
from app.config import settings
from app.ims.stub import StubImsClient
from app.irp.stub import StubIrpClient
from app.models import (
    SCHEMA,
    EInvoiceFiling,
    GstRegistration,
    GstReturn,
    ImsDecision,
    ImsDocument,
    ImsReconciliation,
    Invoice,
    PurchaseDocument,
)
from app.schemas import (
    FileInvoiceIn,
    GstRegistrationUpsert,
    ImsDecisionCreate,
    PurchaseDocumentCreate,
)
from platform_core.db import TENANT_GUC
from tests.helpers import admin_ctx

pytestmark = pytest.mark.integration


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


async def _session_bound_to(org: uuid.UUID):
    """A fresh app-role session bound to `org` on every transaction."""
    engine = create_async_engine(settings().app_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    s = maker()

    @event.listens_for(s.sync_session, "after_begin")
    def _bind(_session, _transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org}', true)")

    return engine, s


class TestCrossTenantIsolation:
    async def test_org_b_cannot_see_org_a_book(self, session, org_id, book) -> None:
        """`session`/`org_id` is org A, seeded by `book`. Org B is a different tenant entirely."""
        org_a = org_id
        org_b = uuid.uuid4()

        # Sanity: org A really does have the book.
        ctx_a = admin_ctx(org_a)
        async with session.begin():
            a_rows = (await session.execute(selectors.invoices_stmt(ctx_a))).scalars().all()
        assert len(a_rows) == 21
        a_invoice_id = a_rows[0].id

        engine, s_b = await _session_bound_to(org_b)
        try:
            ctx_b = admin_ctx(org_b)
            async with s_b.begin():
                # 1) The list is empty for org B.
                b_rows = (await s_b.execute(selectors.invoices_stmt(ctx_b))).scalars().all()
                assert b_rows == []

                # 2) A raw count under RLS sees zero of A's rows.
                count = (await s_b.execute(select(func.count()).select_from(Invoice))).scalar_one()
                assert count == 0

                # 3) Naming A's invoice id directly resolves to nothing for org B.
                got = await selectors.get_invoice(s_b, org_b, a_invoice_id)
                assert got is None

                # 4) The paid-amount rollup returns nothing for A's ids under B's tenant.
                paid = await selectors.paid_by_invoice(s_b, org_b, [a_invoice_id])
                assert paid == {}
        finally:
            await s_b.close()
            await engine.dispose()

    async def test_org_b_cannot_see_org_a_gst_identity_or_filings(
        self, session, org_id, book
    ) -> None:
        """The compliance tables are under the same policy as the rest of the book, and must be.

        A GSTIN is a company's tax identity and a filing history is the evidence of its reporting;
        either leaking across a tenant boundary is worse than an invoice doing so. The seed
        configures org A's registration and files one of its documents, then org B is asked for
        both — by list, by primary key, and through the rollup the invoice read model uses.
        """
        org_a = org_id
        org_b = uuid.uuid4()
        ctx_a = admin_ctx(org_a)

        async with session.begin():
            registration = await selectors.get_gst_registration(session, org_a)
            assert registration is not None
            a_registration_id = registration.id

            invoice = next(
                inv
                for inv in await selectors.all_invoices(session, org_a)
                if inv.supply_type == "b2b" and inv.document_type == "invoice"
            )
            a_invoice_id = invoice.id
            filing = await services.file_invoice(
                session,
                ctx_a,
                StubIrpClient(),
                FileInvoiceIn(organization_id=org_a, invoice_id=a_invoice_id),
            )
            a_filing_id = filing.id
            a_irn = filing.irn

        engine, s_b = await _session_bound_to(org_b)
        try:
            async with s_b.begin():
                # 1) Org B has no registration, and cannot borrow org A's.
                assert await selectors.get_gst_registration(s_b, org_b) is None
                assert await selectors.all_gst_registrations(s_b, org_b) == []
                assert (
                    await s_b.execute(select(func.count()).select_from(GstRegistration))
                ).scalar_one() == 0

                # 2) Naming org A's registration id directly resolves to nothing.
                assert (
                    await s_b.execute(
                        select(GstRegistration).where(GstRegistration.id == a_registration_id)
                    )
                ).scalar_one_or_none() is None

                # 3) The filing history of org A's invoice is empty for org B — by invoice id...
                assert await selectors.filings_for_invoice(s_b, org_b, a_invoice_id) == []
                # ...and through the rollup the list and detail read models both use.
                latest, counts = await selectors.latest_filings(s_b, org_b, [a_invoice_id])
                assert (latest, counts) == ({}, {})

                # 4) Not by primary key, and not by the IRN either.
                assert (
                    await s_b.execute(
                        select(EInvoiceFiling).where(EInvoiceFiling.id == a_filing_id)
                    )
                ).scalar_one_or_none() is None
                assert (
                    await s_b.execute(select(EInvoiceFiling).where(EInvoiceFiling.irn == a_irn))
                ).scalar_one_or_none() is None
        finally:
            await s_b.close()
            await engine.dispose()

    async def test_org_b_cannot_see_org_a_supplier_queue(self, session, org_id) -> None:
        """The IMS tables under the same policy as the rest of the book.

        A supplier queue is arguably the most sensitive thing in this schema: it names who a
        business buys from, at what price, and what it decided to claim credit on. Org A syncs a
        period and decides one document; org B is then asked for all four tables — by list, by
        primary key, and through the two rollups the queue's read models use.
        """
        org_a = org_id
        org_b = uuid.uuid4()
        ctx_a = admin_ctx(org_a)

        async with session.begin():
            await services.set_gst_registration(
                session,
                ctx_a,
                GstRegistrationUpsert(
                    gstin="27AABCA9012D1Z5", legal_name="Atmora Industries Private Limited"
                ),
            )
            await services.record_purchase_document(
                session,
                ctx_a,
                PurchaseDocumentCreate(
                    supplier_gstin="24AABCM1234F1Z3",
                    number="MT-4471",
                    document_date=date(2026, 8, 1),
                    taxable_minor=4_50_000,
                    tax_rate_pct=18,
                    supplier_name="Meridian Textiles",
                ),
            )
            await services.ingest_ims_documents(session, ctx_a, StubImsClient(), period="2026-08")
            await services.reconcile_ims_documents(session, ctx_a, period="2026-08")

        async with session.begin():
            documents = await selectors.all_ims_documents(session, org_a)
            a_document_id = documents[0].id
            decision = await services.record_ims_decision(
                session, ctx_a, a_document_id, ImsDecisionCreate(action="accept")
            )
            a_decision_id = decision.id
            a_purchase = (await selectors.all_purchase_documents(session, org_a))[0].id
        assert len(documents) == 5

        engine, s_b = await _session_bound_to(org_b)
        try:
            async with s_b.begin():
                # 1) Nothing in any of the four tables is visible to org B.
                for model in (ImsDocument, ImsReconciliation, ImsDecision, PurchaseDocument):
                    count = (
                        await s_b.execute(select(func.count()).select_from(model))
                    ).scalar_one()
                    assert count == 0, model.__tablename__

                # 2) Naming org A's rows by primary key resolves to nothing.
                assert await selectors.get_ims_document(s_b, org_b, a_document_id) is None
                assert await selectors.get_purchase_document(s_b, org_b, a_purchase) is None
                assert (
                    await s_b.execute(select(ImsDecision).where(ImsDecision.id == a_decision_id))
                ).scalar_one_or_none() is None

                # 3) And through the rollups the list and detail read models both use.
                assert await selectors.latest_reconciliations(s_b, org_b, [a_document_id]) == {}
                assert await selectors.latest_decisions(s_b, org_b, [a_document_id]) == {}
                assert await selectors.decisions_for_document(s_b, org_b, a_document_id) == []
        finally:
            await s_b.close()
            await engine.dispose()

    async def test_a_filing_cannot_be_written_into_another_tenant(self, session, org_id) -> None:
        """`WITH CHECK`, not just `USING`. Without it a tenant could insert a row belonging to
        somebody else — invisible to them, fully visible to the victim."""
        org_b = uuid.uuid4()
        engine, s_b = await _session_bound_to(org_b)
        try:
            with pytest.raises(Exception, match="row-level security"):
                async with s_b.begin():
                    s_b.add(
                        GstRegistration(
                            organization_id=org_id,  # not org B's own
                            gstin="27AABCA9012D1Z5",
                            state_code="27",
                            legal_name="Planted By Another Tenant",
                        )
                    )
                    await s_b.flush()
        finally:
            await s_b.close()
            await engine.dispose()

    async def test_every_new_compliance_table_carries_a_forced_policy(self, session) -> None:
        """A structural check rather than a per-table one, and it earns its place: the failure it
        catches is a *new* table shipped without a policy, which no test written against the
        tables that already exist would ever notice.

        FORCE as well as ENABLE, because the tables are owned by the migration role and a plain
        ENABLE leaves the owner exempt from its own policy.
        """
        async with session.begin():
            rows = (
                await session.execute(
                    text(
                        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity"
                        " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                        " WHERE n.nspname = 'finance' AND c.relkind = 'r'"
                        "   AND c.relname IN ('gst_returns', 'gst_return_transitions',"
                        "                     'eway_bills', 'invoice_withholdings')"
                    )
                )
            ).all()
        assert len(rows) == 4
        for name, enabled, forced in rows:
            assert enabled is True, name
            assert forced is True, name

    async def test_a_return_cannot_be_written_into_another_tenant(self, session, org_id) -> None:
        """The same `WITH CHECK` claim as the filing above, on the newest tenant table. Planting a
        return in somebody else's organization would put figures they never assembled in front of
        an auditor as their own."""
        org_b = uuid.uuid4()
        engine, s_b = await _session_bound_to(org_b)
        try:
            async with session.begin():
                registration = await services.set_gst_registration(
                    session,
                    admin_ctx(org_id),
                    GstRegistrationUpsert(
                        gstin="27AABCA9012D1Z5", legal_name="Atmora Industries Private Limited"
                    ),
                )
                registration_id = registration.id

            with pytest.raises(Exception, match="row-level security"):
                async with s_b.begin():
                    s_b.add(
                        GstReturn(
                            organization_id=org_id,  # not org B's own
                            gst_registration_id=registration_id,
                            gstin="27AABCA9012D1Z5",
                            state_code="27",
                            period="2026-08",
                            return_type="gstr1",
                            status="draft",
                        )
                    )
                    await s_b.flush()
        finally:
            await s_b.close()
            await engine.dispose()

    async def test_the_suite_runs_as_a_nobypassrls_role(self, session) -> None:
        """A false-pass guard: if these tests ran as a bypassing role, every isolation assertion
        above would pass for the wrong reason."""
        async with session.begin():
            row = (
                await session.execute(
                    text("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
                )
            ).scalar_one()
        assert row is False
