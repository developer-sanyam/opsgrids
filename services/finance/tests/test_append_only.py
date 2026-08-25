"""Invoices and their lines are append-only: a correction is a new credit note, never an edit.

The guarantee is architectural — the service exposes no path that mutates a finalized document,
and a correction adds a new `credit_note` document that points back at the original with
`corrects_invoice_id`. These tests pin both halves: the original's lines are untouched after a
correction, and the HTTP surface for invoices is read-only (no PUT/PATCH/DELETE).

The same property holds for `einvoice_filings`, and there it is enforced by the database rather
than only by the absence of a route — see `TestFilingsAreAppendOnlyAtTheDatabase` in
`test_einvoice.py`, which tries UPDATE and DELETE as the app role and is refused by a trigger.
"""

from __future__ import annotations

from datetime import date

import pytest

from app import selectors, services
from app.main import app
from app.schemas import InvoiceCreate, InvoiceLineCreate
from tests.helpers import admin_ctx

pytestmark = pytest.mark.integration


class TestCorrectionsAreNewRows:
    async def test_a_credit_note_leaves_the_original_lines_untouched(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            customer = await services.create_customer(
                session,
                ctx,
                _customer(),
            )
            invoice = await services.create_invoice(
                session,
                ctx,
                InvoiceCreate(
                    number="INV-TEST-1",
                    customer_id=customer.id,
                    issued_date=date(2026, 7, 1),
                    due_date=date(2026, 7, 31),
                    tax_rate_pct=18,
                    lines=[
                        InvoiceLineCreate(description="Widgets", quantity=10, unit_price_minor=1000)
                    ],
                ),
            )
        original_line_ids = {line.id for line in invoice.lines}
        original_total = invoice.total_minor

        async with session.begin():
            credit = await services.create_invoice(
                session,
                ctx,
                InvoiceCreate(
                    number="CN-TEST-1",
                    customer_id=customer.id,
                    issued_date=date(2026, 7, 5),
                    due_date=date(2026, 7, 5),
                    tax_rate_pct=18,
                    document_type="credit_note",
                    corrects_invoice_id=invoice.id,
                    lines=[
                        InvoiceLineCreate(
                            description="Return - Widgets", quantity=-4, unit_price_minor=1000
                        )
                    ],
                ),
            )

        # The correction is a distinct document that references the original.
        assert credit.id != invoice.id
        assert credit.document_type == "credit_note"
        assert credit.corrects_invoice_id == invoice.id
        assert credit.total_minor < 0

        # The original invoice is unchanged: same lines, same total.
        refreshed = await selectors.get_invoice(session, org_id, invoice.id)
        assert refreshed is not None
        assert {line.id for line in refreshed.lines} == original_line_ids
        assert refreshed.total_minor == original_total

    async def test_the_invoice_surface_is_read_only(self) -> None:
        """No write verb is published for invoices — the only way to change the book is a new
        document. Checked against the OpenAPI contract the SPA is generated from.

        The filing history hangs off an invoice path, so it is covered by the same sweep: a
        recorded attempt is not editable through the public API either, and the internal route
        that appends one is excluded from this document entirely.
        """
        paths = app.openapi()["paths"]
        invoice_paths = {p: ops for p, ops in paths.items() if "/finance/invoices" in p}
        assert invoice_paths, "expected the invoice routes to be published"
        assert any(p.endswith("/filings") for p in invoice_paths)
        verbs = {verb for ops in invoice_paths.values() for verb in ops if verb != "parameters"}
        assert verbs == {"get"}


def _customer():
    from app.schemas import CustomerCreate

    return CustomerCreate(
        name="Append-Only Test Co",
        contact_name="Test",
        email="t@example.com",
        city="Pune",
        state="Maharashtra",
    )
