"""The finance reads, against real PostgreSQL with the demo book seeded.

These prove the live endpoints return exactly the shapes the SPA renders and that the derived
figures — an invoice's paid-so-far, the customer receivable positions, the overview rollups —
foot the way `derive.ts` computes them, so flipping the SPA off fixtures is a clean swap.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app import domain, selectors
from tests.helpers import admin_ctx

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------- the seed


class TestSeed:
    async def test_seed_produces_the_expected_rows(self, book) -> None:
        assert book["created_customers"] == 12
        assert book["created_invoices"] == 21  # 19 invoices + 2 credit notes
        assert book["created_payments"] == 9

    async def test_seed_is_idempotent(self, session, ctx, book) -> None:
        """A second run skips everything rather than duplicating."""
        from seed_finance import seed_finance

        async with session.begin():
            again = await seed_finance(session, ctx)
        assert again["created_customers"] == 0
        assert again["created_invoices"] == 0
        assert again["created_payments"] == 0
        assert again["skipped_invoices"] == 21


# --------------------------------------------------------------------- invoices


class TestInvoices:
    async def test_list_returns_every_document(self, client, book) -> None:
        res = await client.get("/api/v1/finance/invoices?size=100")
        assert res.status_code == 200
        body = res.json()
        assert body["total"] == 21
        assert len(body["items"]) == 21
        # Contract shape the SPA reads.
        first = body["items"][0]
        assert {
            "id",
            "number",
            "document_type",
            "customer_id",
            "status",
            "lines",
            "subtotal_minor",
            "tax_minor",
            "total_minor",
            "amount_paid_minor",
        } <= first.keys()

    async def test_a_paid_invoice_foots_and_shows_full_payment(self, session, org_id, book) -> None:
        """INV-2026-0130: 1800 x Rs 265 @ 18% = ₹5,62,860, fully paid."""
        ctx = admin_ctx(org_id)
        stmt = selectors.invoices_stmt(ctx)
        rows = (await session.execute(stmt)).scalars().all()
        inv = next(r for r in rows if r.number == "INV-2026-0130")
        paid = await selectors.paid_by_invoice(session, org_id, [inv.id])
        out = selectors.to_invoice_out(inv, paid.get(inv.id, 0))
        assert out.subtotal_minor == 47_700_000
        assert out.tax_minor == 8_586_000
        assert out.total_minor == 56_286_000
        assert out.amount_paid_minor == 56_286_000
        assert out.status == "paid"
        # Lines foot to the subtotal exactly.
        assert sum(line.amount_minor for line in out.lines) == out.subtotal_minor

    async def test_search_matches_number_and_customer_name(self, client, book) -> None:
        by_number = await client.get("/api/v1/finance/invoices?q=INV-2026-0120")
        assert by_number.json()["total"] == 1
        by_customer = await client.get("/api/v1/finance/invoices?q=meridian")
        # Meridian Textiles has two invoices (0130, 0142).
        assert by_customer.json()["total"] == 2

    async def test_status_filter(self, client, book) -> None:
        draft = await client.get("/api/v1/finance/invoices?status=draft")
        assert draft.json()["total"] == 1
        void = await client.get("/api/v1/finance/invoices?status=void")
        assert void.json()["total"] == 1

    async def test_amount_sort(self, client, book) -> None:
        res = await client.get("/api/v1/finance/invoices?sort=amount_desc&size=100")
        totals = [i["total_minor"] for i in res.json()["items"]]
        assert totals == sorted(totals, reverse=True)

    async def test_detail_and_credit_note_correction(self, client, session, org_id, book) -> None:
        ctx = admin_ctx(org_id)
        # Commit this read so the shared session has no open transaction when the client request
        # opens its own (the tenant chokepoint begins the request's transaction).
        async with session.begin():
            rows = (await session.execute(selectors.invoices_stmt(ctx))).scalars().all()
        cn = next(r for r in rows if r.number == "CN-2026-0012")
        res = await client.get(f"/api/v1/finance/invoices/{cn.id}")
        assert res.status_code == 200
        body = res.json()
        assert body["document_type"] == "credit_note"
        assert body["total_minor"] < 0  # a credit note carries a negative total
        assert body["corrects_invoice_id"] is not None

    async def test_detail_unknown_id_is_404(self, client, book) -> None:
        import uuid

        res = await client.get(f"/api/v1/finance/invoices/{uuid.uuid4()}")
        assert res.status_code == 404


# --------------------------------------------------------------------- payments


class TestPayments:
    async def test_list_resolves_allocations_to_invoice_numbers(self, client, book) -> None:
        res = await client.get("/api/v1/finance/payments?size=100")
        assert res.status_code == 200
        body = res.json()
        assert body["total"] == 9
        # Newest first.
        dates = [p["received_date"] for p in body["items"]]
        assert dates == sorted(dates, reverse=True)
        alloc = body["items"][0]["allocations"][0]
        assert {"invoice_id", "invoice_number", "amount_minor"} == alloc.keys()
        assert alloc["invoice_number"].startswith("INV-")


# --------------------------------------------------------------------- customers


class TestCustomers:
    async def test_summaries_roll_up_receivables_and_standing(self, client, book) -> None:
        res = await client.get("/api/v1/finance/customers?size=100")
        assert res.status_code == 200
        body = res.json()
        assert body["total"] == 12
        row = body["items"][0]
        assert {
            "customer",
            "receivables_minor",
            "overdue_minor",
            "invoice_count",
            "last_invoice_date",
            "standing",
        } == row.keys()
        assert row["standing"] in ("overdue", "open", "clear")
        # Default sort is receivables desc.
        receivables = [c["receivables_minor"] for c in body["items"]]
        assert receivables == sorted(receivables, reverse=True)

    async def test_a_fully_paid_customer_is_clear(self, client, book) -> None:
        # Orbit Logistics (cust-03) has INV-0137 (paid) and INV-0151 (overdue, unpaid).
        res = await client.get("/api/v1/finance/customers?q=meridian")
        row = res.json()["items"][0]
        # Meridian's two invoices (0130, 0142) are both fully paid -> clear, nothing outstanding.
        assert row["customer"]["name"].startswith("Meridian")
        assert row["receivables_minor"] == 0
        assert row["standing"] == "clear"


# --------------------------------------------------------------------- overview & ledger


class TestOverview:
    async def test_overview_shape_and_invariants(self, client, book) -> None:
        """No injected clock: the endpoint anchors to the real date, not a frozen one, so
        `period_label` is whatever month the suite happens to run in and `revenue` for it is
        honestly whatever the seeded (2026, static) book issued in that real month — usually
        zero, since the demo book's dates do not track the calendar. The KPIs that do not depend
        on "this period" — receivables, overdue, aging, attention — still foot."""
        res = await client.get("/api/v1/finance/overview")
        assert res.status_code == 200
        body = res.json()
        assert body["period_label"] == datetime.now(UTC).date().strftime("%B %Y")

        kpis = {k["key"]: k for k in body["kpis"]}
        assert set(kpis) == {"revenue", "receivables", "overdue", "cash"}
        # The cash balance is a booked constant, flagged as representative on the wire.
        assert kpis["cash"]["value_minor"] == domain.CASH_BALANCE_MINOR
        assert kpis["cash"]["sample"] is True
        assert kpis["revenue"]["sample"] is False
        assert kpis["revenue"]["value_minor"] >= 0
        assert kpis["overdue"]["value_minor"] > 0
        assert all(k["trend_sample"] is True for k in kpis.values())

        # AR aging sums back to the outstanding-receivables KPI — the internal invariant.
        assert len(body["aging"]) == 5
        assert sum(b["amount_minor"] for b in body["aging"]) == kpis["receivables"]["value_minor"]

        assert len(body["cashflow"]) == 6
        assert body["cashflow_sample"] is True

        # Attention: overdue invoices only, largest outstanding first.
        attention = body["attention"]
        assert attention and all(inv["status"] == "overdue" for inv in attention)
        outstanding = [inv["total_minor"] - inv["amount_paid_minor"] for inv in attention]
        assert outstanding == sorted(outstanding, reverse=True)

    async def test_overview_with_an_injected_clock_matches_the_seeded_month(
        self, session, org_id, book
    ) -> None:
        """With the clock pinned to the demo book's own era, `revenue` is real and positive —
        proving the figure is genuinely derived from this tenant's invoices, not a constant, and
        that the read-time derivation works correctly when "today" falls inside the seeded data.
        """
        ctx = admin_ctx(org_id)
        summary = await selectors.get_overview(session, ctx, today=date(2026, 8, 24))
        assert summary.period_label == "August 2026"
        kpis = {k.key: k for k in summary.kpis}
        assert kpis["revenue"].value_minor > 0


class TestLedger:
    async def test_ledger_returns_the_trial_balance(self, client, book) -> None:
        res = await client.get("/api/v1/finance/ledger")
        assert res.status_code == 200
        body = res.json()
        assert body["period_label"].startswith("FY 2026")
        assert len(body["accounts"]) == len(domain.LEDGER_ACCOUNTS)
        codes = {a["code"] for a in body["accounts"]}
        assert "4000" in codes and "5000" in codes
        # There is no ledger table behind this report — always flagged, never served as fact.
        assert body["sample"] is True
        assert body["as_of"] == datetime.now(UTC).date().isoformat()
