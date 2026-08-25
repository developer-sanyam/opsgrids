"""The demo seed produces the expected rows, and is idempotent.

Drives `tools/demo/seed_operations.py` directly against a tenant-bound session (the seed module is
importable and side-effect-free at load), so this proves the same dataset the live screens render
lands correctly and cross-links: a purchase order's lines foot to its total, an inventory item's
low-stock is derived, a supplier's spend is carried on the row.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app import domain, selectors
from platform_core.auth import Scope
from tests.helpers import ctx_for

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools" / "demo"))
import seed_operations

pytestmark = pytest.mark.integration


class TestDemoSeed:
    async def test_seed_inserts_the_full_fixture_dataset(self, session, org_id) -> None:
        ctx = ctx_for(org_id, "operations.procurement.purchase_order.write", Scope.ORG)
        async with session.begin():
            counts = await seed_operations.seed(session, ctx)
        assert counts == {
            "warehouses": 4,
            "suppliers": 12,
            "purchase_orders": 15,
            "purchase_order_lines": 25,
            "inventory": 18,
            "skipped": 0,
        }

    async def test_seed_is_idempotent(self, session, org_id) -> None:
        ctx = ctx_for(org_id, "operations.procurement.purchase_order.write", Scope.ORG)
        async with session.begin():
            await seed_operations.seed(session, ctx)
        async with session.begin():
            second = await seed_operations.seed(session, ctx)
        assert second["warehouses"] == 0
        assert second["suppliers"] == 0
        assert second["purchase_orders"] == 0
        assert second["purchase_order_lines"] == 0
        assert second["inventory"] == 0
        assert second["skipped"] == 4 + 12 + 15 + 18

    async def test_seeded_po_lines_foot_to_the_total(self, session, org_id) -> None:
        ctx = ctx_for(org_id, "operations.procurement.purchase_order.write", Scope.ORG)
        async with session.begin():
            await seed_operations.seed(session, ctx)
        po = await selectors.get_purchase_order_by_number(session, org_id, "PO-2026-0147")
        assert po is not None
        lines = await selectors.lines_for(session, org_id, po.id)
        # 500·₹1,840 + 1200·₹245 + 2000·₹62 in paise.
        expected = 500 * 1_840_00 + 1200 * 245_00 + 2000 * 62_00
        assert domain.po_total_minor(lines) == expected
        assert all(ln.amount_minor == ln.quantity * ln.unit_price_minor for ln in lines)

    async def test_seeded_low_stock_is_derived_not_stored(self, session, org_id) -> None:
        ctx = ctx_for(org_id, "operations.procurement.purchase_order.write", Scope.ORG)
        async with session.begin():
            await seed_operations.seed(session, ctx)
        # RM-ALU-7075: on_hand 6, reorder 12 → low; RM-STL-4501: 82 on 40 → healthy.
        low = await selectors.get_inventory_by_sku(session, org_id, "RM-ALU-7075")
        healthy = await selectors.get_inventory_by_sku(session, org_id, "RM-STL-4501")
        assert low is not None and healthy is not None
        assert domain.is_low_stock(low) is True
        assert domain.is_low_stock(healthy) is False

    async def test_seeded_supplier_carries_its_spend_and_open_pos(self, session, org_id) -> None:
        ctx = ctx_for(org_id, "operations.procurement.purchase_order.write", Scope.ORG)
        async with session.begin():
            await seed_operations.seed(session, ctx)
        meridian = await selectors.get_supplier_by_code(session, org_id, "SUP-MER")
        assert meridian is not None
        assert meridian.spend_minor == 41_20_00_000 * 100
        assert meridian.open_pos == 3
        assert float(meridian.on_time_pct) == pytest.approx(96.4)

    async def test_seeded_overview_reconciles(self, session, org_id) -> None:
        ctx = ctx_for(org_id, "operations.procurement.purchase_order.read", Scope.ORG)
        write = ctx_for(org_id, "operations.procurement.purchase_order.write", Scope.ORG)
        async with session.begin():
            await seed_operations.seed(session, write)

        pos = await selectors.all_purchase_orders(session, ctx)
        inventory = await selectors.all_inventory(session, org_id)
        # 15 POs seeded; open = draft(2) + approved(5) = 7.
        assert len(pos) == 15
        assert domain.open_po_count(pos) == 7
        # 18 inventory items; the low-stock ones are those at/under their reorder point.
        low_skus = {it.sku for it in domain.low_stock_items(inventory)}
        assert low_skus == {
            "RM-STL-4520",
            "RM-ALU-7075",
            "RM-CU-ROD",
            "RM-POL-PP0",
            "CMP-PCB-220",
            "CMP-CST-410",
            "PKG-WRP-011",
            "MRO-GLV-XL",
        }
