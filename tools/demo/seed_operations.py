"""Seed the Operations product for the demo org, mirroring the SPA fixtures.

Run **inside `services/operations`** as a subprocess (every service exposes a top-level `app`, so
one process can import only one of them — the same constraint the other seeders live under).

Mirrors `services/operations/tests/conftest.py`: connects as the NOBYPASSRLS app role and binds
the tenant GUC on every transaction (the GUC is transaction-local), so writes go through the same
row-level-security path a real request does, and through the real `services.*` write paths — so
warehouses, suppliers, purchase orders (with their line items) and inventory all land with an
audit row and an outbox event, exactly as the live product produces them.

The dataset is the SPA's `frontend/product-operations/src/api/fixtures.ts`, one-for-one: the same
4 warehouses, 12 suppliers, 15 purchase orders (25 lines) and 18 inventory items, cross-linked by
supplier code and warehouse name, so the live screens look like the fixtures the user approved.

Idempotent: a code / number / sku that already exists is skipped, so re-running never duplicates.

Usage: seed_operations.py <operations_service_dir> <org_id> <admin_user_id> <admin_membership_id>
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import date
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from platform_core.auth import ScopeContext


def inr(rupees: int) -> int:
    """Rupees → paise (integer minor units), matching the SPA's `inr()`."""
    return rupees * 100


# ------------------------------------------------------------------ warehouses (code, name, loc)

WAREHOUSES: list[tuple[str, str, str]] = [
    ("WH-BHI", "Bhiwandi DC", "Bhiwandi"),
    ("WH-PUN", "Pune Plant", "Pune"),
    ("WH-CHE", "Chennai Hub", "Chennai"),
    ("WH-BLR", "Bengaluru Store", "Bengaluru"),
]


# ------------------------------------------------------------------ suppliers
# (code, name, category, status, lead_time_days, on_time_pct, spend_rupees, open_pos,
#  contact_name, contact_email, location, since)

SUPPLIERS: list[tuple[str, str, str, str, int, float, int, int, str, str, str, str]] = [
    (
        "SUP-MER",
        "Meridian Metals",
        "raw_materials",
        "active",
        12,
        96.4,
        41_20_00_000,
        3,
        "Rajesh Pillai",
        "rajesh@meridianmetals.example",
        "Jamshedpur",
        "2019-04-02",
    ),
    (
        "SUP-IRC",
        "Ironclad Alloys",
        "raw_materials",
        "active",
        18,
        91.2,
        33_80_00_000,
        2,
        "Neha Kulkarni",
        "neha@ironcladalloys.example",
        "Raipur",
        "2020-08-11",
    ),
    (
        "SUP-APX",
        "Apex Packaging",
        "packaging",
        "active",
        6,
        98.1,
        8_64_00_000,
        1,
        "Farhan Qureshi",
        "farhan@apexpack.example",
        "Vapi",
        "2021-01-19",
    ),
    (
        "SUP-VLT",
        "Volt Components",
        "components",
        "active",
        21,
        88.7,
        19_45_00_000,
        4,
        "Karthik Iyer",
        "karthik@voltcomponents.example",
        "Bengaluru",
        "2020-03-27",
    ),
    (
        "SUP-NMB",
        "Nimbus Fasteners",
        "components",
        "on_hold",
        15,
        79.5,
        6_12_00_000,
        0,
        "Sunita Rao",
        "sunita@nimbusfast.example",
        "Ludhiana",
        "2022-06-05",
    ),
    (
        "SUP-STL",
        "Sterling MRO Supplies",
        "mro",
        "active",
        4,
        94.9,
        3_28_00_000,
        2,
        "Amit Deshmukh",
        "amit@sterlingmro.example",
        "Pune",
        "2021-11-14",
    ),
    (
        "SUP-PLY",
        "Polymer Works",
        "raw_materials",
        "active",
        10,
        92.6,
        14_90_00_000,
        1,
        "Divya Menon",
        "divya@polymerworks.example",
        "Ahmedabad",
        "2019-09-30",
    ),
    (
        "SUP-CRL",
        "Crestline Logistics",
        "logistics",
        "active",
        3,
        90.3,
        5_74_00_000,
        2,
        "Vikram Sethi",
        "vikram@crestlinelog.example",
        "Mumbai",
        "2020-12-08",
    ),
    (
        "SUP-PRC",
        "Precision Castings Co",
        "components",
        "active",
        24,
        85.4,
        11_36_00_000,
        1,
        "Lakshmi Nair",
        "lakshmi@precisioncast.example",
        "Coimbatore",
        "2018-07-22",
    ),
    (
        "SUP-GRL",
        "GreenLeaf Consumables",
        "mro",
        "pending",
        8,
        83.0,
        92_00_000,
        0,
        "Tejas Bhatt",
        "tejas@greenleaf.example",
        "Nashik",
        "2024-02-16",
    ),
    (
        "SUP-BRF",
        "BlueRiver Freight",
        "logistics",
        "active",
        5,
        87.8,
        4_16_00_000,
        1,
        "Ishita Banerjee",
        "ishita@blueriverfreight.example",
        "Kolkata",
        "2021-05-03",
    ),
    (
        "SUP-ANV",
        "Anvil Industrial Services",
        "services",
        "active",
        14,
        89.6,
        7_08_00_000,
        0,
        "Mohammed Sayeed",
        "mohammed@anvilservices.example",
        "Hyderabad",
        "2022-10-01",
    ),
]


# ------------------------------------------------------------------ purchase orders
# Each: number, supplier_code, status, warehouse, raised_by, created, expected, received, notes,
#       lines = [(sku, description, qty, uom, price_rupees, received_qty), ...]

PURCHASE_ORDERS: list[dict[str, Any]] = [
    {
        "number": "PO-2026-0148",
        "supplier": "SUP-MER",
        "status": "approved",
        "warehouse": "Pune Plant",
        "raised_by": "Devansh Rao",
        "created": "2026-08-06",
        "expected": "2026-08-22",
        "received": None,
        "notes": "Priority run for the Q3 casting line.",
        "lines": [
            ("RM-STL-4501", "Cold-rolled steel coil, 1.2mm", 40, "MT", 62_500, 0),
            ("RM-STL-4520", "Galvanised steel sheet, 0.8mm", 25, "MT", 71_200, 0),
        ],
    },
    {
        "number": "PO-2026-0147",
        "supplier": "SUP-VLT",
        "status": "approved",
        "warehouse": "Bengaluru Store",
        "raised_by": "Priya Nair",
        "created": "2026-08-09",
        "expected": "2026-08-30",
        "received": None,
        "notes": None,
        "lines": [
            ("CMP-PCB-220", "Control PCB assembly, rev C", 500, "pcs", 1_840, 0),
            ("CMP-REL-014", "24V industrial relay", 1200, "pcs", 245, 0),
            ("CMP-CON-088", "16-pin ribbon connector", 2000, "pcs", 62, 0),
        ],
    },
    {
        "number": "PO-2026-0146",
        "supplier": "SUP-APX",
        "status": "received",
        "warehouse": "Bhiwandi DC",
        "raised_by": "Devansh Rao",
        "created": "2026-07-28",
        "expected": "2026-08-05",
        "received": "2026-08-04",
        "notes": "Delivered a day early, full quantity.",
        "lines": [
            ("PKG-BOX-330", "Double-wall export carton, 600x400x400", 5000, "pcs", 78, 5000),
            ("PKG-WRP-011", "Stretch wrap film, 500mm", 300, "rolls", 640, 300),
        ],
    },
    {
        "number": "PO-2026-0145",
        "supplier": "SUP-IRC",
        "status": "approved",
        "warehouse": "Pune Plant",
        "raised_by": "Karthik Iyer",
        "created": "2026-08-01",
        "expected": "2026-08-19",
        "received": None,
        "notes": "Follow up — supplier flagged a mill delay.",
        "lines": [
            ("RM-ALU-7075", "Aluminium billet, 7075-T6", 18, "MT", 2_18_000, 0),
        ],
    },
    {
        "number": "PO-2026-0144",
        "supplier": "SUP-PLY",
        "status": "received",
        "warehouse": "Chennai Hub",
        "raised_by": "Priya Nair",
        "created": "2026-07-20",
        "expected": "2026-07-30",
        "received": "2026-07-31",
        "notes": None,
        "lines": [
            ("RM-POL-ABS", "ABS polymer granules, natural", 12, "MT", 1_34_000, 12),
            ("RM-POL-PP0", "Polypropylene homopolymer", 8, "MT", 1_12_000, 8),
        ],
    },
    {
        "number": "PO-2026-0143",
        "supplier": "SUP-STL",
        "status": "received",
        "warehouse": "Bengaluru Store",
        "raised_by": "Amit Deshmukh",
        "created": "2026-08-10",
        "expected": "2026-08-16",
        "received": "2026-08-15",
        "notes": None,
        "lines": [
            ("MRO-LUB-220", "Industrial gear oil, ISO 220", 60, "L", 385, 60),
            ("MRO-GLV-XL", "Cut-resistant gloves, XL", 400, "pairs", 129, 400),
            ("MRO-FLT-008", "Hydraulic filter cartridge", 80, "pcs", 640, 80),
        ],
    },
    {
        "number": "PO-2026-0142",
        "supplier": "SUP-PRC",
        "status": "closed",
        "warehouse": "Pune Plant",
        "raised_by": "Devansh Rao",
        "created": "2026-06-30",
        "expected": "2026-07-24",
        "received": "2026-07-23",
        "notes": "Closed after QC sign-off.",
        "lines": [
            ("CMP-CST-410", "Gearbox housing casting, GH-410", 300, "pcs", 3_150, 300),
        ],
    },
    {
        "number": "PO-2026-0141",
        "supplier": "SUP-CRL",
        "status": "approved",
        "warehouse": "Bhiwandi DC",
        "raised_by": "Vikram Sethi",
        "created": "2026-08-12",
        "expected": "2026-08-21",
        "received": None,
        "notes": "Inbound freight for the Meridian coil shipment.",
        "lines": [
            ("LOG-FRT-FTL", "Full-truckload freight, Jamshedpur → Pune", 4, "trips", 46_000, 0),
        ],
    },
    {
        "number": "PO-2026-0140",
        "supplier": "SUP-VLT",
        "status": "received",
        "warehouse": "Bengaluru Store",
        "raised_by": "Priya Nair",
        "created": "2026-07-15",
        "expected": "2026-08-04",
        "received": "2026-08-06",
        "notes": "Two days late — partial short-ship reconciled.",
        "lines": [
            ("CMP-SEN-330", "Proximity sensor, M18", 800, "pcs", 410, 800),
            ("CMP-TRM-090", "DIN-rail terminal block", 3000, "pcs", 28, 3000),
        ],
    },
    {
        "number": "PO-2026-0139",
        "supplier": "SUP-APX",
        "status": "draft",
        "warehouse": "Chennai Hub",
        "raised_by": "Devansh Rao",
        "created": "2026-08-20",
        "expected": "2026-09-02",
        "received": None,
        "notes": "Awaiting budget approval from the plant head.",
        "lines": [
            ("PKG-PAL-004", "Euro pallet, heat-treated", 600, "pcs", 720, 0),
            ("PKG-STR-020", "Steel strapping coil, 19mm", 120, "rolls", 1_240, 0),
        ],
    },
    {
        "number": "PO-2026-0138",
        "supplier": "SUP-MER",
        "status": "received",
        "warehouse": "Pune Plant",
        "raised_by": "Karthik Iyer",
        "created": "2026-07-02",
        "expected": "2026-07-16",
        "received": "2026-07-16",
        "notes": None,
        "lines": [
            ("RM-STL-4501", "Cold-rolled steel coil, 1.2mm", 55, "MT", 61_800, 55),
        ],
    },
    {
        "number": "PO-2026-0137",
        "supplier": "SUP-BRF",
        "status": "approved",
        "warehouse": "Bhiwandi DC",
        "raised_by": "Vikram Sethi",
        "created": "2026-08-14",
        "expected": "2026-08-18",
        "received": None,
        "notes": "Overdue — driver stuck at the interstate check-post.",
        "lines": [
            ("LOG-FRT-LTL", "Part-load freight, Kolkata → Bhiwandi", 6, "trips", 18_500, 0),
        ],
    },
    {
        "number": "PO-2026-0136",
        "supplier": "SUP-IRC",
        "status": "closed",
        "warehouse": "Pune Plant",
        "raised_by": "Devansh Rao",
        "created": "2026-06-10",
        "expected": "2026-07-01",
        "received": "2026-06-29",
        "notes": "Closed and invoiced.",
        "lines": [
            ("RM-ALU-6061", "Aluminium billet, 6061-T6", 22, "MT", 1_96_000, 22),
            ("RM-CU-ROD", "Copper rod, 12mm", 6, "MT", 7_84_000, 6),
        ],
    },
    {
        "number": "PO-2026-0135",
        "supplier": "SUP-ANV",
        "status": "received",
        "warehouse": "Chennai Hub",
        "raised_by": "Amit Deshmukh",
        "created": "2026-07-25",
        "expected": "2026-08-10",
        "received": "2026-08-09",
        "notes": "Preventive maintenance on the press line.",
        "lines": [
            ("SRV-MNT-PRS", "Hydraulic press overhaul, 400T", 1, "job", 4_85_000, 1),
        ],
    },
    {
        "number": "PO-2026-0134",
        "supplier": "SUP-PLY",
        "status": "draft",
        "warehouse": "Chennai Hub",
        "raised_by": "Priya Nair",
        "created": "2026-08-21",
        "expected": "2026-09-08",
        "received": None,
        "notes": None,
        "lines": [
            ("RM-POL-PC0", "Polycarbonate granules, clear", 10, "MT", 2_05_000, 0),
        ],
    },
]


# ------------------------------------------------------------------ inventory
# (sku, name, category, warehouse, on_hand, reorder_point, uom, unit_value_rupees, stocked_at)

INVENTORY: list[tuple[str, str, str, str, int, int, str, int, str]] = [
    (
        "RM-STL-4501",
        "Cold-rolled steel coil, 1.2mm",
        "raw_materials",
        "Pune Plant",
        82,
        40,
        "MT",
        62_400,
        "2026-08-23",
    ),
    (
        "RM-STL-4520",
        "Galvanised steel sheet, 0.8mm",
        "raw_materials",
        "Pune Plant",
        21,
        30,
        "MT",
        71_000,
        "2026-08-23",
    ),
    (
        "RM-ALU-7075",
        "Aluminium billet, 7075-T6",
        "raw_materials",
        "Pune Plant",
        6,
        12,
        "MT",
        2_18_000,
        "2026-08-22",
    ),
    (
        "RM-ALU-6061",
        "Aluminium billet, 6061-T6",
        "raw_materials",
        "Pune Plant",
        34,
        15,
        "MT",
        1_96_000,
        "2026-08-21",
    ),
    (
        "RM-CU-ROD",
        "Copper rod, 12mm",
        "raw_materials",
        "Chennai Hub",
        3,
        5,
        "MT",
        7_84_000,
        "2026-08-20",
    ),
    (
        "RM-POL-ABS",
        "ABS polymer granules, natural",
        "raw_materials",
        "Chennai Hub",
        19,
        10,
        "MT",
        1_34_000,
        "2026-08-22",
    ),
    (
        "RM-POL-PP0",
        "Polypropylene homopolymer",
        "raw_materials",
        "Chennai Hub",
        7,
        8,
        "MT",
        1_12_000,
        "2026-08-19",
    ),
    (
        "CMP-PCB-220",
        "Control PCB assembly, rev C",
        "components",
        "Bengaluru Store",
        260,
        300,
        "pcs",
        1_840,
        "2026-08-23",
    ),
    (
        "CMP-REL-014",
        "24V industrial relay",
        "components",
        "Bengaluru Store",
        4200,
        1500,
        "pcs",
        245,
        "2026-08-23",
    ),
    (
        "CMP-SEN-330",
        "Proximity sensor, M18",
        "components",
        "Bengaluru Store",
        640,
        400,
        "pcs",
        410,
        "2026-08-22",
    ),
    (
        "CMP-CST-410",
        "Gearbox housing casting, GH-410",
        "components",
        "Pune Plant",
        40,
        120,
        "pcs",
        3_150,
        "2026-08-21",
    ),
    (
        "CMP-CON-088",
        "16-pin ribbon connector",
        "components",
        "Bengaluru Store",
        8800,
        3000,
        "pcs",
        62,
        "2026-08-20",
    ),
    (
        "PKG-BOX-330",
        "Double-wall export carton",
        "packaging",
        "Bhiwandi DC",
        12400,
        5000,
        "pcs",
        78,
        "2026-08-23",
    ),
    (
        "PKG-WRP-011",
        "Stretch wrap film, 500mm",
        "packaging",
        "Bhiwandi DC",
        210,
        250,
        "rolls",
        640,
        "2026-08-22",
    ),
    (
        "PKG-PAL-004",
        "Euro pallet, heat-treated",
        "packaging",
        "Bhiwandi DC",
        1350,
        500,
        "pcs",
        720,
        "2026-08-19",
    ),
    (
        "MRO-LUB-220",
        "Industrial gear oil, ISO 220",
        "mro",
        "Bengaluru Store",
        180,
        60,
        "L",
        385,
        "2026-08-21",
    ),
    (
        "MRO-GLV-XL",
        "Cut-resistant gloves, XL",
        "mro",
        "Bengaluru Store",
        90,
        200,
        "pairs",
        129,
        "2026-08-20",
    ),
    (
        "MRO-FLT-008",
        "Hydraulic filter cartridge",
        "mro",
        "Pune Plant",
        240,
        80,
        "pcs",
        640,
        "2026-08-18",
    ),
]


def _date(s: str) -> date:
    return date.fromisoformat(s)


async def seed(session: AsyncSession, ctx: ScopeContext) -> dict[str, int]:
    """Insert the full Operations demo dataset through the real service write paths. Idempotent.

    Importable and side-effect-free at module load, so tests can drive it directly. Runs inside a
    transaction the caller opens.
    """
    from app import selectors, services  # type: ignore[import-not-found]
    from app.inventory import selectors as inv_selectors  # type: ignore[import-not-found]
    from app.inventory import services as inv_services

    counts = {
        "warehouses": 0,
        "suppliers": 0,
        "purchase_orders": 0,
        "purchase_order_lines": 0,
        "inventory": 0,
        "skipped": 0,
    }

    for code, name, location in WAREHOUSES:
        if await inv_selectors.get_warehouse_by_code(session, ctx.organization_id, code):
            counts["skipped"] += 1
            continue
        await inv_services.create_warehouse(session, ctx, code=code, name=name, location=location)
        counts["warehouses"] += 1

    for (
        code,
        name,
        category,
        status,
        lead,
        on_time,
        spend,
        open_pos,
        contact,
        email,
        location,
        since,
    ) in SUPPLIERS:
        if await selectors.get_supplier_by_code(session, ctx.organization_id, code):
            counts["skipped"] += 1
            continue
        await services.create_supplier(
            session,
            ctx,
            code=code,
            name=name,
            category=category,
            status=status,
            lead_time_days=lead,
            on_time_pct=on_time,
            spend_minor=inr(spend),
            currency="INR",
            open_pos=open_pos,
            contact_name=contact,
            contact_email=email,
            location=location,
            since=_date(since),
        )
        counts["suppliers"] += 1

    for spec in PURCHASE_ORDERS:
        if await selectors.get_purchase_order_by_number(
            session, ctx.organization_id, spec["number"]
        ):
            counts["skipped"] += 1
            continue
        lines = [
            {
                "sku": sku,
                "description": description,
                "quantity": qty,
                "uom": uom,
                "unit_price_minor": inr(price),
                "received_qty": recv,
            }
            for (sku, description, qty, uom, price, recv) in spec["lines"]
        ]
        await services.create_purchase_order(
            session,
            ctx,
            number=spec["number"],
            supplier_code=spec["supplier"],
            status=spec["status"],
            warehouse=spec["warehouse"],
            raised_by=spec["raised_by"],
            currency="INR",
            created_date=_date(spec["created"]),
            expected_date=_date(spec["expected"]),
            received_date=_date(spec["received"]) if spec["received"] else None,
            notes=spec["notes"],
            lines=lines,
        )
        counts["purchase_orders"] += 1
        counts["purchase_order_lines"] += len(lines)

    for sku, name, category, warehouse, on_hand, reorder, uom, unit_value, stocked in INVENTORY:
        if await inv_selectors.get_inventory_by_sku(session, ctx.organization_id, sku):
            counts["skipped"] += 1
            continue
        await inv_services.create_inventory_item(
            session,
            ctx,
            sku=sku,
            name=name,
            category=category,
            warehouse=warehouse,
            on_hand=on_hand,
            reorder_point=reorder,
            uom=uom,
            unit_value_minor=inr(unit_value),
            currency="INR",
            stocked_at=_date(stocked),
        )
        counts["inventory"] += 1

    return counts


# ------------------------------------------------------------------ standalone entry point


def _bootstrap() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Parse argv and put the service dir on the import path — sync, before the event loop."""
    from pathlib import Path

    service_dir = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(service_dir))
    return uuid.UUID(sys.argv[2]), uuid.UUID(sys.argv[3]), uuid.UUID(sys.argv[4])


async def _main(
    org_id: uuid.UUID, admin_user_id: uuid.UUID, admin_membership_id: uuid.UUID
) -> None:
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import settings  # type: ignore[import-not-found]
    from app.models import SCHEMA  # type: ignore[import-not-found]
    from platform_core.auth import Principal, Scope, ScopeContext
    from platform_core.db import TENANT_GUC

    cfg = settings()
    engine = create_async_engine(
        cfg.app_dsn, connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}}
    )
    maker = async_sessionmaker(engine, expire_on_commit=False)

    ctx = ScopeContext(
        principal=Principal(
            sub=admin_user_id,
            organization_id=org_id,
            membership_id=admin_membership_id,
            perms=["operations.procurement.purchase_order.write:ORG"],
            entitlements=["operations"],
        ),
        scope=Scope.ORG,
        permission="operations.procurement.purchase_order.write",
    )

    async with maker() as session:

        @event.listens_for(session.sync_session, "after_begin")
        def _bind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
            connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org_id}', true)")

        async with session.begin():
            counts = await seed(session, ctx)

    await engine.dispose()
    print(json.dumps(counts))


if __name__ == "__main__":
    _org, _user, _membership = _bootstrap()
    asyncio.run(_main(_org, _user, _membership))
