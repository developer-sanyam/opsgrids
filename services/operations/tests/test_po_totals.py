"""Purchase-order lines foot to the order total — the money invariant, against real PostgreSQL.

A PO total is never stored: it is the sum of its lines' `amount_minor`, and each `amount_minor`
is `quantity * unit_price_minor` in integer minor units (ADR-0000 §8). These tests prove the
stored line amounts are exactly that product, and that the derived total is exactly their sum, so
the figure the UI shows can never drift from the rows behind it.
"""

from __future__ import annotations

import pytest

from app import domain, selectors

pytestmark = pytest.mark.integration


class TestLinesFootToTotal:
    async def test_each_line_amount_is_quantity_times_unit_price(
        self, session, org_id, data
    ) -> None:
        for po in (data["po_a"], data["po_b"]):
            lines = await selectors.lines_for(session, org_id, po.id)
            assert lines
            for line in lines:
                assert line.amount_minor == line.quantity * line.unit_price_minor
                assert line.amount_minor == domain.line_amount_minor(
                    line.quantity, line.unit_price_minor
                )

    async def test_po_total_is_the_sum_of_its_line_amounts(self, session, org_id, data) -> None:
        lines = await selectors.lines_for(session, org_id, data["po_a"].id)
        # 40 MT @ ₹62,500 + 25 MT @ ₹71,200 = ₹42,80,000 → 428,000,000 paise.
        expected = 40 * 62_500_00 + 25 * 71_200_00
        assert domain.po_total_minor(lines) == expected
        assert domain.po_total_minor(lines) == sum(line_amt.amount_minor for line_amt in lines)

    async def test_no_total_column_is_exposed_on_the_wire(self, client, org_id, data) -> None:
        """The wire carries line items, not a stored total — the client sums the lines."""
        from app.main import app
        from platform_core.auth import get_principal
        from tests.helpers import principal_for

        principal = principal_for(
            org_id,
            ["operations.procurement.purchase_order.read:ORG"],
            subject_ref=data["subject_a"],
        )
        app.dependency_overrides[get_principal] = lambda: principal
        try:
            response = await client.get(f"/api/v1/operations/purchase-orders/{data['po_a'].id}")
        finally:
            app.dependency_overrides.pop(get_principal, None)
        body = response.json()
        assert "total_minor" not in body
        assert "line_items" in body
        derived = sum(item["quantity"] * item["unit_price_minor"] for item in body["line_items"])
        assert derived == 40 * 62_500_00 + 25 * 71_200_00
