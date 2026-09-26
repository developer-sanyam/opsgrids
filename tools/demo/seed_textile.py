"""Seed a textile-trading demo org across four services: Operations, Finance, Marketing and
Notification (Engage).

The scenario is a Surat fabric and yarn trader selling to garment makers and retailers — the
shape of business the platform is shown to in a textile sales demo. Everything here is fictional:
company names, people, `.example` e-mail addresses, and GSTINs that pass the checksum but belong
to nobody.

Run as `seed_textile.py <repo_root> <org_id> <admin_user_id> <admin_membership_id>`. Like
`seed_crm.py`, that parent re-invokes this file once per service (`--child <service>`), because
every service exposes a top-level `app` package and one process can import only one of them. Each
child connects as the NOBYPASSRLS app role and binds the tenant GUC on every transaction (the GUC
is transaction-local), so writes go through the same row-level-security path a request does — and
through the real `services.*` write paths, so every row lands with its audit row and outbox
event. Purchase orders are approved and received through the real PO transitions, so received
stock arrives as ledger `receipt` moves carrying the PO's unit cost; opening stock is a ledger
`opening` move; the two adjustments and the count are ledger moves too.

What the platform does and does not do with this data is written down in
`docs/demo/textile-demo.md`. Two things worth knowing before reading the code:

* **Quantities** are whole units here (metres, kilograms, pieces, rolls), because the item-level
  API takes whole units. The ledger itself stores milli-units.
* **Money** is integer paise with an ISO currency. Rupee figures below are for legibility only.

Dates are relative to the day the seed first runs, so "overdue" and "expected next week" stay
true whenever the demo is loaded. Idempotent: a code, SKU, PO number, invoice number, payment
reference, campaign name, template code or contact address that already exists is skipped, and
adjustments and the reservation carry idempotency keys, so a second run changes nothing.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from platform_core.auth import ScopeContext

SERVICES = ("operations", "finance", "marketing", "notification")

#: The two org-wide identifiers the reservation and the notification rules hang off, derived so a
#: second run computes the same values.
_NS = uuid.uuid5(uuid.NAMESPACE_URL, "opsgrids-demo-textile")


def _ago(today: date, days: int) -> date:
    return today - timedelta(days=days)


def _ahead(today: date, days: int) -> date:
    return today + timedelta(days=days)


# =============================================================================== OPERATIONS

WAREHOUSES: list[tuple[str, str, str]] = [
    ("WH-SUR", "Surat Fabric Godown", "Surat, Gujarat"),
    ("WH-BHW", "Bhiwandi Distribution Centre", "Bhiwandi, Maharashtra"),
    ("WH-TPR", "Tiruppur Yarn & Trims Store", "Tiruppur, Tamil Nadu"),
]

# (code, name, parent code or None)
ITEM_CATEGORIES: list[tuple[str, str, str | None]] = [
    ("FABRIC", "Fabrics", None),
    ("FAB-COT", "Cotton fabrics", "FABRIC"),
    ("FAB-DEN", "Denim", "FABRIC"),
    ("FAB-SYN", "Synthetic & blended fabrics", "FABRIC"),
    ("FAB-SLK", "Silk", "FABRIC"),
    ("YARN", "Yarn", None),
    ("TRIM", "Trims & accessories", None),
    ("PACK", "Packing", None),
]

# (code, name, category, status, lead_time_days, on_time_pct, lifetime spend in rupees,
#  contact_name, contact_email, location, since)
# `spend` is spend *before* the purchase orders below; approving a PO adds its total on top.
SUPPLIERS: list[tuple[str, str, str, str, int, float, int, str, str, str, str]] = [
    (
        "SUP-SAR",
        "Sarthak Textile Mills",
        "raw_materials",
        "active",
        9,
        94.2,
        38_40_000,
        "Jignesh Desai",
        "jignesh@sarthaktextiles.example",
        "Surat",
        "2021-06-14",
    ),
    (
        "SUP-KAL",
        "Kalyani Synthetics",
        "raw_materials",
        "active",
        7,
        91.5,
        27_10_000,
        "Meena Shah",
        "meena@kalyanisynthetics.example",
        "Surat",
        "2020-11-02",
    ),
    (
        "SUP-AAR",
        "Aarav Denim Works",
        "raw_materials",
        "active",
        14,
        89.8,
        52_75_000,
        "Kunal Trivedi",
        "kunal@aaravdenim.example",
        "Ahmedabad",
        "2019-08-21",
    ),
    (
        "SUP-BAL",
        "Shree Balaji Silk Mills",
        "raw_materials",
        "active",
        16,
        86.3,
        9_60_000,
        "Ramesh Gowda",
        "ramesh@balajisilk.example",
        "Bengaluru",
        "2022-03-09",
    ),
    (
        "SUP-TYT",
        "Tiruppur Yarn Traders",
        "raw_materials",
        "active",
        5,
        97.0,
        61_20_000,
        "Senthil Kumar",
        "senthil@tiruppuryarn.example",
        "Tiruppur",
        "2018-05-30",
    ),
    (
        "SUP-LTZ",
        "Ludhiana Trims & Zippers",
        "components",
        "active",
        12,
        92.4,
        6_85_000,
        "Harpreet Singh",
        "harpreet@ludhianatrims.example",
        "Ludhiana",
        "2021-01-18",
    ),
    (
        "SUP-PKG",
        "Vapi Poly Packaging",
        "packaging",
        "active",
        6,
        95.1,
        2_20_000,
        "Nilesh Patil",
        "nilesh@vapipoly.example",
        "Vapi",
        "2022-09-12",
    ),
    (
        "SUP-NAV",
        "Navsari Rayon Prints",
        "raw_materials",
        "on_hold",
        11,
        78.6,
        4_05_000,
        "Bhavesh Modi",
        "bhavesh@navsarirayon.example",
        "Navsari",
        "2023-02-27",
    ),
]


@dataclass(frozen=True)
class ItemSeed:
    sku: str
    name: str
    category: str  # item_categories code
    warehouse: str  # warehouse name
    uom: str  # m | kg | pcs | roll
    opening: int  # whole units, posted as a ledger `opening` move
    reorder_point: int
    unit_rupees: float  # stock value per uom
    hsn: str
    attrs: dict[str, int | str] = field(default_factory=dict)
    variants: tuple[tuple[str, str], ...] = ()
    legacy_category: str = "raw_materials"


SUR, BHW, TPR = (w[1] for w in WAREHOUSES)

ITEMS: list[ItemSeed] = [
    # ---- cotton fabrics (Surat)
    ItemSeed(
        "COT-POP-58-WHT",
        'Cotton poplin 58" optical white',
        "FAB-COT",
        SUR,
        "m",
        2850,
        1500,
        92,
        "5208",
        {
            "gsm": 120,
            "width_mm": 1473,
            "composition": "100% cotton",
            "colour": "Optical white",
            "weave": "Poplin",
        },
    ),
    ItemSeed(
        "COT-POP-58-SKY",
        'Cotton poplin 58" sky blue',
        "FAB-COT",
        SUR,
        "m",
        1620,
        1500,
        96,
        "5208",
        {
            "gsm": 120,
            "width_mm": 1473,
            "composition": "100% cotton",
            "colour": "Sky blue",
            "weave": "Poplin",
        },
    ),
    ItemSeed(
        "COT-VOL-44-PRT",
        'Cotton voile printed floral 44"',
        "FAB-COT",
        SUR,
        "m",
        2260,
        1200,
        81,
        "5208",
        {
            "gsm": 85,
            "width_mm": 1118,
            "composition": "100% cotton",
            "colour": "Assorted floral prints",
            "weave": "Voile",
        },
        variants=(
            ("COT-VOL-44-PRT-D11", "Design 11 - marigold floral"),
            ("COT-VOL-44-PRT-D12", "Design 12 - indigo buta"),
            ("COT-VOL-44-PRT-D13", "Design 13 - pastel stripe"),
        ),
    ),
    # The one item that is visibly short: 640 m on hand against a 1,200 m reorder point, with an
    # approved purchase order (PO-2026-0305) on its way.
    ItemSeed(
        "COT-TWL-58-KHK",
        'Cotton twill 58" khaki 240 GSM',
        "FAB-COT",
        SUR,
        "m",
        640,
        1200,
        158,
        "5209",
        {
            "gsm": 240,
            "width_mm": 1473,
            "composition": "100% cotton",
            "colour": "Khaki",
            "weave": "3/1 twill",
        },
    ),
    ItemSeed(
        "GRY-POP-100-RL",
        "Greige poplin, 100 m roll",
        "FAB-COT",
        SUR,
        "roll",
        64,
        30,
        8600,
        "5208",
        {
            "gsm": 118,
            "width_mm": 1473,
            "roll_length_m": 100,
            "composition": "100% cotton",
            "weave": "Poplin (unprocessed)",
        },
    ),
    # ---- denim (Surat)
    ItemSeed(
        "DEN-58-IND-10",
        'Denim 58" indigo 10 oz',
        "FAB-DEN",
        SUR,
        "m",
        6400,
        2500,
        204,
        "5209",
        {
            "gsm": 340,
            "width_mm": 1473,
            "composition": "100% cotton",
            "colour": "Indigo",
            "weave": "3/1 right-hand twill",
        },
    ),
    ItemSeed(
        "DEN-58-BLK-8",
        'Denim 58" black stretch 8 oz',
        "FAB-DEN",
        SUR,
        "m",
        2785,
        2000,
        236,
        "5209",
        {
            "gsm": 270,
            "width_mm": 1473,
            "composition": "98% cotton, 2% elastane",
            "colour": "Black",
            "weave": "3/1 twill, stretch",
        },
    ),
    # ---- synthetic and blended fabrics (Bhiwandi)
    ItemSeed(
        "RAY-44-PRT",
        'Rayon 14 kg printed 44"',
        "FAB-SYN",
        BHW,
        "m",
        4200,
        3000,
        60,
        "5516",
        {
            "gsm": 110,
            "width_mm": 1118,
            "composition": "100% viscose rayon",
            "colour": "Assorted prints",
            "weave": "Plain",
        },
        variants=(
            ("RAY-44-PRT-D101", "Design 101 - paisley"),
            ("RAY-44-PRT-D102", "Design 102 - tropical"),
            ("RAY-44-PRT-D103", "Design 103 - geometric"),
        ),
    ),
    ItemSeed(
        "RAY-44-MRN",
        'Rayon solid dyed 44" maroon',
        "FAB-SYN",
        BHW,
        "m",
        3550,
        2000,
        58,
        "5516",
        {
            "gsm": 110,
            "width_mm": 1118,
            "composition": "100% viscose rayon",
            "colour": "Maroon",
            "weave": "Plain",
        },
    ),
    ItemSeed(
        "GEO-44-BLK",
        'Georgette 44" black',
        "FAB-SYN",
        BHW,
        "m",
        2600,
        2000,
        68,
        "5407",
        {
            "gsm": 65,
            "width_mm": 1118,
            "composition": "100% polyester",
            "colour": "Black",
            "weave": "Georgette crepe",
        },
    ),
    ItemSeed(
        "GEO-44-PCH",
        'Georgette 44" peach',
        "FAB-SYN",
        BHW,
        "m",
        2450,
        1500,
        70,
        "5407",
        {
            "gsm": 65,
            "width_mm": 1118,
            "composition": "100% polyester",
            "colour": "Peach",
            "weave": "Georgette crepe",
        },
    ),
    ItemSeed(
        "PCT-SHT-58-WHT",
        'Poly-cotton shirting 58" 65/35 white',
        "FAB-SYN",
        BHW,
        "m",
        4400,
        2000,
        86,
        "5513",
        {
            "gsm": 110,
            "width_mm": 1473,
            "composition": "65% polyester, 35% cotton",
            "colour": "White",
            "weave": "Plain",
        },
    ),
    ItemSeed(
        "POL-SAT-44-NVY",
        'Polyester satin 44" navy',
        "FAB-SYN",
        BHW,
        "m",
        3300,
        1500,
        63,
        "5407",
        {
            "gsm": 80,
            "width_mm": 1118,
            "composition": "100% polyester",
            "colour": "Navy",
            "weave": "Satin",
        },
    ),
    # ---- silk (Surat)
    ItemSeed(
        "SLK-DUP-44-GLD",
        'Dupion silk 44" antique gold',
        "FAB-SLK",
        SUR,
        "m",
        620,
        300,
        610,
        "5007",
        {
            "gsm": 90,
            "width_mm": 1118,
            "composition": "100% mulberry silk",
            "colour": "Antique gold",
            "weave": "Dupion",
        },
    ),
    # ---- yarn (Tiruppur), kilograms
    ItemSeed(
        "YRN-CTN-30S-CMB",
        "Cotton combed yarn 30s, raw white",
        "YARN",
        TPR,
        "kg",
        3700,
        2000,
        252,
        "5205",
        {"count_ne": 30, "composition": "100% combed cotton", "colour": "Raw white"},
    ),
    ItemSeed(
        "YRN-CTN-40S-CRD",
        "Cotton carded yarn 40s, raw white",
        "YARN",
        TPR,
        "kg",
        2150,
        1500,
        272,
        "5205",
        {"count_ne": 40, "composition": "100% carded cotton", "colour": "Raw white"},
    ),
    ItemSeed(
        "YRN-VIS-30S",
        "Viscose spun yarn 30s",
        "YARN",
        TPR,
        "kg",
        1850,
        800,
        228,
        "5510",
        {"count_ne": 30, "composition": "100% viscose"},
    ),
    ItemSeed(
        "YRN-PES-150D",
        "Polyester textured yarn 150D/48F",
        "YARN",
        TPR,
        "kg",
        2400,
        1000,
        112,
        "5402",
        {"denier": 150, "composition": "100% polyester"},
    ),
    # ---- trims (Tiruppur), pieces and rolls
    ItemSeed(
        "BTN-POLY-18L-WHT",
        "Polyester shirt buttons 18L white",
        "TRIM",
        TPR,
        "pcs",
        18000,
        20000,
        0.8,
        "9606",
        {"size_line": 18, "colour": "White"},
        legacy_category="components",
    ),
    ItemSeed(
        "ZIP-NYL-3-7IN-BLK",
        'Nylon zipper #3, 7" black',
        "TRIM",
        TPR,
        "pcs",
        4500,
        5000,
        6.2,
        "9607",
        {"colour": "Black", "length_mm": 178},
        variants=(
            ("ZIP-NYL-3-6IN-BLK", 'Nylon zipper #3, 6" black'),
            ("ZIP-NYL-3-9IN-BLK", 'Nylon zipper #3, 9" black'),
        ),
        legacy_category="components",
    ),
    ItemSeed(
        "LBL-WVN-MAIN",
        "Woven main label, satin, 50 x 25 mm",
        "TRIM",
        TPR,
        "pcs",
        25000,
        10000,
        1.2,
        "5807",
        {"width_mm": 50},
        legacy_category="components",
    ),
    ItemSeed(
        "THR-POL-5000",
        "Polyester sewing thread, 5000 m cone",
        "TRIM",
        TPR,
        "pcs",
        1850,
        800,
        74,
        "5401",
        {"composition": "100% polyester"},
        legacy_category="components",
    ),
    ItemSeed(
        "INT-FUS-90-RL",
        "Fusible interlining 90 cm, 100 m roll",
        "TRIM",
        TPR,
        "roll",
        42,
        20,
        2850,
        "5903",
        {"width_mm": 900, "roll_length_m": 100},
        legacy_category="components",
    ),
    ItemSeed(
        "ELS-25-50-RL",
        "Woven elastic 25 mm, 50 m roll",
        "TRIM",
        TPR,
        "roll",
        88,
        40,
        1150,
        "5806",
        {"width_mm": 25, "roll_length_m": 50},
        legacy_category="components",
    ),
    ItemSeed(
        "PKG-POLY-12x16",
        'Garment poly bag 12" x 16"',
        "PACK",
        BHW,
        "pcs",
        40000,
        15000,
        1.1,
        "3923",
        {},
        legacy_category="packaging",
    ),
]


@dataclass(frozen=True)
class PoSeed:
    number: str
    supplier: str
    warehouse: str
    status: str  # draft | approved | received
    created_ago: int
    expected_offset: int  # days from today; negative = already due
    received_ago: int | None
    raised_by: str
    notes: str | None
    lines: tuple[tuple[str, int, float], ...]  # (sku, quantity, unit rupees)


PURCHASE_ORDERS: list[PoSeed] = [
    PoSeed(
        "PO-2026-0301",
        "SUP-SAR",
        SUR,
        "received",
        34,
        -24,
        25,
        "Hitesh Demo",
        "Restock of the poplin range ahead of the wedding-season shirting orders.",
        (("COT-POP-58-WHT", 2000, 92), ("COT-POP-58-SKY", 1500, 96)),
    ),
    PoSeed(
        "PO-2026-0302",
        "SUP-KAL",
        BHW,
        "received",
        28,
        -19,
        18,
        "Hitesh Demo",
        None,
        (("GEO-44-BLK", 2500, 68), ("RAY-44-PRT", 4000, 60)),
    ),
    PoSeed(
        "PO-2026-0303",
        "SUP-TYT",
        TPR,
        "received",
        20,
        -12,
        12,
        "Priya Desai",
        "Delivered a day early.",
        (("YRN-CTN-30S-CMB", 1500, 250), ("YRN-CTN-40S-CRD", 1000, 270)),
    ),
    PoSeed(
        "PO-2026-0304",
        "SUP-LTZ",
        TPR,
        "received",
        14,
        -7,
        6,
        "Priya Desai",
        "Delivered against the supplier challan.",
        (("BTN-POLY-18L-WHT", 30000, 0.8), ("ZIP-NYL-3-7IN-BLK", 8000, 6.2)),
    ),
    PoSeed(
        "PO-2026-0305",
        "SUP-SAR",
        SUR,
        "approved",
        6,
        5,
        None,
        "Hitesh Demo",
        "Restocks the khaki twill, which is below its reorder point.",
        (("COT-TWL-58-KHK", 3000, 158),),
    ),
    PoSeed(
        "PO-2026-0306",
        "SUP-AAR",
        SUR,
        "approved",
        4,
        9,
        None,
        "Hitesh Demo",
        None,
        (("DEN-58-IND-10", 5000, 204),),
    ),
    PoSeed(
        "PO-2026-0307",
        "SUP-BAL",
        SUR,
        "approved",
        21,
        -3,
        None,
        "Priya Desai",
        "Overdue - mill says the dupion lot is still at the twisting stage.",
        (("SLK-DUP-44-GLD", 400, 610),),
    ),
    PoSeed(
        "PO-2026-0308",
        "SUP-KAL",
        BHW,
        "draft",
        1,
        12,
        None,
        "Priya Desai",
        "Awaiting approval from Hitesh.",
        (("POL-SAT-44-NVY", 3000, 63), ("GEO-44-PCH", 2500, 70)),
    ),
]

# (sku, kind, delta or counted, reason, note, days ago, idempotency key)
ADJUSTMENTS: list[tuple[str, str, int, str | None, str, int, str]] = [
    (
        "DEN-58-BLK-8",
        "adjustment",
        -35,
        "damaged",
        "Water damage on the outer layers of one roll, written off.",
        9,
        "textile-demo:adj:den-blk-damage",
    ),
    (
        "COT-VOL-44-PRT",
        "count",
        2248,
        None,
        "Monthly cycle count, Surat godown: 12 m short of the book figure.",
        5,
        "textile-demo:count:vol-cycle",
    ),
]

#: One reservation: 1,800 m of indigo denim promised to Bharat Denim Apparels' open order. There
#: is no reservation-create endpoint or screen yet, so this goes through the ledger kernel's own
#: `reserve()` — the same function a future endpoint would call.
RESERVATION = ("DEN-58-IND-10", 1800, "textile-demo:reserve:bharat-denim")

EXPECTED_OPERATIONS = {
    "warehouses": len(WAREHOUSES),
    "suppliers": len(SUPPLIERS),
    "item_categories": len(ITEM_CATEGORIES),
    "items": len(ITEMS),
    "variants": sum(len(i.variants) for i in ITEMS),
    "attributes": sum(len(i.attrs) for i in ITEMS),
    "purchase_orders": len(PURCHASE_ORDERS),
    "adjustments": len(ADJUSTMENTS),
    "reservations": 1,
}


def paise(rupees: float) -> int:
    return round(rupees * 100)


async def seed_operations(
    session: AsyncSession, ctx: ScopeContext, today: date | None = None
) -> dict[str, int]:
    """Warehouses, suppliers, categories, items (with attributes and variants), opening stock,
    purchase orders driven through approve/receive, two stock adjustments and one reservation."""
    from app import selectors, services  # type: ignore[import-not-found]
    from app.inventory import ledger  # type: ignore[import-not-found]
    from app.inventory import selectors as inv_selectors
    from app.inventory import services as inv_services
    from app.schemas import ItemAttributeIn  # type: ignore[import-not-found]

    today = today or date.today()
    org = ctx.organization_id
    counts = dict.fromkeys(EXPECTED_OPERATIONS, 0)

    for code, name, location in WAREHOUSES:
        if await inv_selectors.get_warehouse_by_code(session, org, code):
            continue
        await inv_services.create_warehouse(session, ctx, code=code, name=name, location=location)
        counts["warehouses"] += 1

    # Suppliers start with every one of their POs counted as open; receiving a PO takes it off.
    po_count = {s.supplier: 0 for s in PURCHASE_ORDERS}
    for spec in PURCHASE_ORDERS:
        po_count[spec.supplier] += 1
    for (
        code,
        name,
        category,
        status,
        lead,
        on_time,
        spend,
        contact,
        email,
        location,
        since,
    ) in SUPPLIERS:
        if await selectors.get_supplier_by_code(session, org, code):
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
            spend_minor=paise(spend),
            currency="INR",
            open_pos=po_count.get(code, 0),
            contact_name=contact,
            contact_email=email,
            location=location,
            since=date.fromisoformat(since),
        )
        counts["suppliers"] += 1

    category_ids: dict[str, str] = {}
    for code, name, parent in ITEM_CATEGORIES:
        existing = await inv_selectors.get_item_category_by_code(session, org, code)
        if existing is not None:
            category_ids[code] = str(existing.id)
            continue
        created = await inv_services.create_item_category(
            session,
            ctx,
            code=code,
            name=name,
            parent_id=category_ids[parent] if parent else None,
        )
        category_ids[code] = str(created.id)
        counts["item_categories"] += 1

    opening_date = _ago(today, 60)
    for spec in ITEMS:
        if await inv_selectors.get_inventory_by_sku(session, org, spec.sku):
            continue
        item = await inv_services.create_inventory_item(
            session,
            ctx,
            sku=spec.sku,
            name=spec.name,
            category=spec.legacy_category,
            warehouse=spec.warehouse,
            on_hand=spec.opening,
            reorder_point=spec.reorder_point,
            uom=spec.uom,
            unit_value_minor=paise(spec.unit_rupees),
            currency="INR",
            stocked_at=opening_date,
            item_category_id=category_ids[spec.category],
            hsn_code=spec.hsn,
        )
        counts["items"] += 1
        for key, value in spec.attrs.items():
            payload = (
                ItemAttributeIn(value_type="number", value_number=value)
                if isinstance(value, int)
                else ItemAttributeIn(value_type="text", value_text=value)
            )
            await inv_services.set_item_attribute(session, ctx, item, key, payload)
            counts["attributes"] += 1
        for sku, name in spec.variants:
            await inv_services.create_item_variant(session, ctx, item, sku=sku, name=name)
            counts["variants"] += 1

    # Purchase orders go through the real transitions: raised as a draft, approved (which adds the
    # PO total to the supplier's lifetime spend), and — for the received ones — received, which
    # posts a ledger `receipt` per line at the PO's unit cost.
    for po in PURCHASE_ORDERS:
        if await selectors.get_purchase_order_by_number(session, org, po.number):
            continue
        created = await services.create_purchase_order(
            session,
            ctx,
            number=po.number,
            supplier_code=po.supplier,
            status="draft",
            warehouse=po.warehouse,
            raised_by=po.raised_by,
            currency="INR",
            created_date=_ago(today, po.created_ago),
            expected_date=_ahead(today, po.expected_offset),
            notes=po.notes,
            lines=[
                {
                    "sku": sku,
                    "description": next(i.name for i in ITEMS if i.sku == sku),
                    "quantity": qty,
                    "uom": next(i.uom for i in ITEMS if i.sku == sku),
                    "unit_price_minor": paise(rate),
                    "received_qty": 0,
                }
                for sku, qty, rate in po.lines
            ],
        )
        if po.status in ("approved", "received"):
            await services.approve_purchase_order(session, ctx, created)
        if po.status == "received":
            assert po.received_ago is not None
            await services.receive_purchase_order(
                session, ctx, created, received_date=_ago(today, po.received_ago)
            )
        counts["purchase_orders"] += 1

    for sku, kind, amount, reason, note, days_ago, key in ADJUSTMENTS:
        item = await inv_selectors.get_inventory_by_sku(session, org, sku, for_update=True)
        assert item is not None
        before = await _ledger_rows(session, org, item.id)
        await inv_services.adjust_stock(
            session,
            ctx,
            item,
            quantity_delta=amount if kind == "adjustment" else None,
            counted_on_hand=amount if kind == "count" else None,
            reason=reason,
            note=note,
            occurred_on=_ago(today, days_ago),
            idempotency_key=key,
        )
        if await _ledger_rows(session, org, item.id) > before:
            counts["adjustments"] += 1

    sku, quantity, key = RESERVATION
    item = await inv_selectors.get_inventory_by_sku(session, org, sku, for_update=True)
    assert item is not None and item.home_location_id is not None
    reservation = await ledger.reserve(
        session,
        ctx,
        item_id=item.id,
        location_id=item.home_location_id,
        quantity_milli=quantity * 1000,
        reference_type="sales_order",
        reference_id=uuid.uuid5(_NS, "sales-order:bharat-denim-open-order"),
        idempotency_key=key,
    )
    if not reservation.replayed:
        counts["reservations"] += 1

    return counts


async def _ledger_rows(session: AsyncSession, org: uuid.UUID, item_id: uuid.UUID) -> int:
    from sqlalchemy import func, select

    from app.models import LedgerEntry  # type: ignore[import-not-found]

    stmt = select(func.count()).where(
        LedgerEntry.organization_id == org, LedgerEntry.inventory_item_id == item_id
    )
    return int((await session.execute(stmt)).scalar_one())


# ================================================================================= FINANCE

#: Gujarat (24). Customers in Gujarat are intra-state supplies (CGST + SGST); everyone else is
#: inter-state (IGST); the SEZ unit is IGST even though it sits in Gujarat, because a supply to an
#: SEZ is inter-state by statute. Every GSTIN below passes `domain.parse_gstin`'s checksum —
#: `services.create_customer` refuses one that does not — and belongs to no real taxpayer.
SELLER_GSTIN = "24AAECD4187K1ZG"
SELLER_LEGAL_NAME = "Demo Textiles Private Limited"
SELLER_TRADE_NAME = "Demo Textiles"


@dataclass(frozen=True)
class CustomerSeed:
    key: str
    name: str
    contact_name: str
    email: str
    gstin: str
    city: str
    state: str
    segment: str


CUSTOMERS: list[CustomerSeed] = [
    CustomerSeed(
        "ganesh",
        "Shree Ganesh Garments",
        "Harish Patel",
        "accounts@shreeganeshgarments.example",
        "24AAAFS2216G1ZQ",
        "Surat",
        "Gujarat",
        "Garment maker",
    ),
    CustomerSeed(
        "patel",
        "Patel Cloth Emporium",
        "Dinesh Patel",
        "accounts@patelcloth.example",
        "24AAQFP6402B1ZJ",
        "Ahmedabad",
        "Gujarat",
        "Fabric retailer",
    ),
    CustomerSeed(
        "vardhman",
        "Vardhman Fashion Exports Pvt Ltd",
        "Latha Subramaniam",
        "finance@vardhmanfashion.example",
        "33AACCV8310L1ZA",
        "Tiruppur",
        "Tamil Nadu",
        "Knitwear manufacturer",
    ),
    CustomerSeed(
        "rangoli",
        "Rangoli Ethnic Wear LLP",
        "Kavita Sharma",
        "billing@rangoliethnic.example",
        "08AAJFR5529D1Z4",
        "Jaipur",
        "Rajasthan",
        "Ethnic-wear brand",
    ),
    CustomerSeed(
        "metro",
        "Metro Menswear Retail Pvt Ltd",
        "Sanjay Kulkarni",
        "ap@metromenswear.example",
        "27AABCM7741Q1ZU",
        "Mumbai",
        "Maharashtra",
        "Menswear retailer",
    ),
    CustomerSeed(
        "delhihub",
        "Delhi Fabric Hub",
        "Rakesh Arora",
        "accounts@delhifabrichub.example",
        "07AAGFD3308H1ZQ",
        "New Delhi",
        "Delhi",
        "Wholesale trader",
    ),
    CustomerSeed(
        "bharat",
        "Bharat Denim Apparels",
        "Ayesha Khan",
        "finance@bharatdenim.example",
        "29AAECB9962N1ZT",
        "Bengaluru",
        "Karnataka",
        "Jeans manufacturer",
    ),
    CustomerSeed(
        "seabreeze",
        "Sea Breeze Apparel (SEZ unit)",
        "Nirav Mehta",
        "accounts@seabreezeapparel.example",
        "24AAGCS1175M1Z5",
        "Surat",
        "Gujarat",
        "Garment exporter, SEZ unit",
    ),
]

FABRIC_RATE = 5.0  # illustrative; confirm current slabs with the tenant's CA
TRIM_RATE = 18.0


@dataclass(frozen=True)
class LineSeed:
    description: str
    quantity: float
    rate_rupees: float  # per unit
    hsn: str
    tax_rate_pct: float = FABRIC_RATE


@dataclass(frozen=True)
class InvoiceSeed:
    number: str
    customer: str
    issued_ago: int
    terms_days: int
    lines: tuple[LineSeed, ...]
    supply_type: str | None = None
    place_of_supply: str | None = None
    document_type: str = "invoice"
    status: str | None = None
    corrects: str | None = None
    notes: str | None = None


INVOICES: list[InvoiceSeed] = [
    InvoiceSeed(
        "INV-2026-0501",
        "ganesh",
        58,
        30,
        (
            LineSeed('Cotton poplin 58" optical white (m)', 1200, 118, "5208"),
            LineSeed('Cotton poplin 58" sky blue (m)', 800, 122, "5208"),
        ),
        notes="Intra-state supply, Surat to Surat: CGST + SGST.",
    ),
    InvoiceSeed(
        "INV-2026-0502",
        "vardhman",
        49,
        30,
        (LineSeed("Cotton combed yarn 30s (kg)", 900, 289, "5205"),),
        notes="Payment overdue - second reminder sent.",
    ),
    InvoiceSeed(
        "INV-2026-0503",
        "rangoli",
        40,
        30,
        (
            LineSeed('Georgette 44" assorted (m)', 1500, 88, "5407"),
            LineSeed('Dupion silk 44" antique gold (m)', 150, 760, "5007"),
        ),
        notes="Part payment received by cheque.",
    ),
    InvoiceSeed(
        "INV-2026-0504",
        "metro",
        30,
        30,
        (
            LineSeed('Poly-cotton shirting 58" 65/35 (m)', 2500, 112, "5513"),
            LineSeed("Woven main labels (pcs)", 5000, 1.9, "5807", TRIM_RATE),
        ),
        notes="Two rates on one invoice: fabric and trims are taxed separately per line.",
    ),
    InvoiceSeed(
        "INV-2026-0505",
        "bharat",
        22,
        30,
        (LineSeed('Denim 58" indigo 10 oz (m)', 3000, 262, "5209"),),
        notes="50% advance received. 1,800 m of this is reserved from stock.",
    ),
    InvoiceSeed(
        "INV-2026-0506",
        "patel",
        15,
        30,
        (LineSeed('Rayon 14 kg printed 44" (m)', 2200, 78, "5516"),),
        notes="Intra-state supply, Surat to Ahmedabad: CGST + SGST.",
    ),
    InvoiceSeed(
        "INV-2026-0507",
        "delhihub",
        75,
        30,
        (
            LineSeed('Polyester satin 44" navy (m)', 3000, 86, "5407"),
            LineSeed('Georgette 44" black (m)', 900, 90, "5407"),
        ),
        notes="Payment overdue - third reminder sent.",
    ),
    InvoiceSeed(
        "INV-2026-0508",
        "seabreeze",
        9,
        30,
        (
            LineSeed('Cotton twill 58" khaki 240 GSM (m)', 1800, 214, "5209"),
            LineSeed("Polyester shirt buttons 18L (pcs)", 20000, 1.15, "9606", TRIM_RATE),
        ),
        supply_type="sez",
        place_of_supply="24",
        notes="SEZ supply: IGST even though the buyer is in Gujarat, as the statute requires.",
    ),
    InvoiceSeed(
        "INV-2026-0509",
        "ganesh",
        1,
        30,
        (LineSeed('Denim 58" black stretch 8 oz (m)', 1000, 298, "5209"),),
        status="draft",
        notes="Draft - waiting on the buyer to confirm the shade card.",
    ),
    InvoiceSeed(
        "CN-2026-0501",
        "ganesh",
        30,
        0,
        (LineSeed('Return - cotton poplin 58" optical white (m)', -60, 118, "5208"),),
        document_type="credit_note",
        corrects="INV-2026-0501",
        notes="60 m returned with a weaving fault. A new document, never an edit to the invoice.",
    ),
]


@dataclass(frozen=True)
class PaymentSeed:
    reference: str
    customer: str
    received_ago: int
    method: str
    invoice: str
    fraction: float  # of that invoice's total
    note: str


PAYMENTS: list[PaymentSeed] = [
    PaymentSeed("PMT-2026-0701", "ganesh", 41, "bank_transfer", "INV-2026-0501", 1.0, "NEFT"),
    PaymentSeed("PMT-2026-0702", "rangoli", 22, "cheque", "INV-2026-0503", 0.6, "Cheque, part"),
    PaymentSeed("PMT-2026-0703", "bharat", 17, "bank_transfer", "INV-2026-0505", 0.5, "RTGS adv."),
]

EXPECTED_FINANCE = {
    "registrations": 1,
    "customers": len(CUSTOMERS),
    "invoices": len(INVOICES),
    "payments": len(PAYMENTS),
}


async def seed_finance(
    session: AsyncSession, ctx: ScopeContext, today: date | None = None
) -> dict[str, int]:
    """The seller's GST registration, customers, GST invoices (one credit note, one draft), and
    payments allocated to them through `services.record_payment`."""
    from sqlalchemy import func, select

    from app import selectors, services  # type: ignore[import-not-found]
    from app.models import Customer, Invoice  # type: ignore[import-not-found]
    from app.schemas import (  # type: ignore[import-not-found]
        CustomerCreate,
        GstRegistrationUpsert,
        InvoiceCreate,
        InvoiceLineCreate,
        PaymentAllocationCreate,
        PaymentCreate,
    )

    today = today or date.today()
    org = ctx.organization_id
    counts = dict.fromkeys(EXPECTED_FINANCE, 0)

    # First, always: every invoice reads the seller's registration to choose CGST+SGST or IGST.
    if await selectors.get_gst_registration(session, org) is None:
        await services.set_gst_registration(
            session,
            ctx,
            GstRegistrationUpsert(
                gstin=SELLER_GSTIN,
                legal_name=SELLER_LEGAL_NAME,
                trade_name=SELLER_TRADE_NAME,
            ),
        )
        counts["registrations"] += 1

    customer_ids: dict[str, uuid.UUID] = {}
    for cust in CUSTOMERS:
        found = (
            await session.execute(
                select(Customer.id).where(
                    Customer.organization_id == org,
                    func.lower(Customer.name) == cust.name.lower(),
                    Customer.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if found is not None:
            customer_ids[cust.key] = found
            continue
        created = await services.create_customer(
            session,
            ctx,
            CustomerCreate(
                name=cust.name,
                contact_name=cust.contact_name,
                email=cust.email,
                gstin=cust.gstin,
                city=cust.city,
                state=cust.state,
            ),
        )
        customer_ids[cust.key] = created.id
        counts["customers"] += 1

    invoice_ids: dict[str, uuid.UUID] = {}
    # Invoices before credit notes: a credit note must name an invoice that already exists.
    for spec in sorted(INVOICES, key=lambda s: s.document_type == "credit_note"):
        if await selectors.number_exists(session, org, spec.number):
            invoice_ids[spec.number] = (
                await session.execute(
                    select(Invoice.id).where(
                        Invoice.organization_id == org, Invoice.number == spec.number
                    )
                )
            ).scalar_one()
            continue
        issued = _ago(today, spec.issued_ago)
        invoice = await services.create_invoice(
            session,
            ctx,
            InvoiceCreate(
                number=spec.number,
                customer_id=customer_ids[spec.customer],
                issued_date=issued,
                due_date=issued + timedelta(days=spec.terms_days),
                tax_rate_pct=FABRIC_RATE,
                lines=[
                    InvoiceLineCreate(
                        description=line.description,
                        quantity=line.quantity,
                        unit_price_minor=paise(line.rate_rupees),
                        hsn_sac=line.hsn,
                        tax_rate_pct=line.tax_rate_pct,
                    )
                    for line in spec.lines
                ],
                document_type=spec.document_type,
                status=spec.status,
                notes=spec.notes,
                supply_type=spec.supply_type,
                place_of_supply=spec.place_of_supply,
                corrects_invoice_id=invoice_ids.get(spec.corrects) if spec.corrects else None,
            ),
        )
        invoice_ids[spec.number] = invoice.id
        counts["invoices"] += 1

    for pay in PAYMENTS:
        if await selectors.reference_exists(session, org, pay.reference):
            continue
        total = (
            await session.execute(
                select(Invoice.total_minor).where(Invoice.id == invoice_ids[pay.invoice])
            )
        ).scalar_one()
        await services.record_payment(
            session,
            ctx,
            PaymentCreate(
                reference=pay.reference,
                customer_id=customer_ids[pay.customer],
                received_date=_ago(today, pay.received_ago),
                method=pay.method,
                allocations=[
                    PaymentAllocationCreate(
                        invoice_id=invoice_ids[pay.invoice],
                        amount_minor=round(total * pay.fraction),
                    )
                ],
                note=pay.note,
            ),
        )
        counts["payments"] += 1

    return counts


# =============================================================================== MARKETING

CAMPAIGN_CURRENCY = "INR"


def lakh(n: float) -> int:
    """Lakh of rupees → paise: 1 lakh = ₹1,00,000 = 10,000,000 paise."""
    return round(n * 10_000_000)


# (name, size, criteria, defined_ago_days)
AUDIENCES: list[tuple[str, int, str, int]] = [
    (
        "Surat garment manufacturers",
        6400,
        "Facebook/Instagram interest audience: apparel manufacturing, wholesale fabric; "
        "Surat, Ahmedabad, Tiruppur",
        140,
    ),
    (
        "Boutique & designer owners",
        9800,
        "Instagram: independent boutiques, ethnic-wear designers, 1-20 staff; India",
        110,
    ),
    ("Existing buyers (customer list)", 412, "Uploaded customer list, matched on phone", 95),
    ("Lookalike 1% of existing buyers", 21000, "Meta lookalike seeded from the buyer list", 80),
    ("Catalogue viewers, last 30 days", 3150, "Retargeting: viewed the catalogue on the site", 45),
    ("Gulf apparel importers", 2700, "UAE/Oman wholesale apparel buyers, English-first", 30),
]

# (name, channel, status, budget, spent, revenue, pipeline, leads, conversions, goal,
#  audience, start_ago, end_ago or None, owner) - money in lakh of rupees.
# Facebook and Instagram have no channel of their own: `social` is the closest allowed value, so
# the Meta campaigns are named "Meta | ..." to be told apart from other social spend.
CAMPAIGNS: list[
    tuple[str, str, str, float, float, float, float, int, int, str, str, int, int | None, str]
] = [
    (
        "Meta | Wholesale lead ads - Surat garment makers",
        "social",
        "live",
        6.0,
        4.85,
        38,
        62,
        412,
        21,
        "Wholesale enquiries",
        "Surat garment manufacturers",
        75,
        None,
        "Meghna Rao",
    ),
    (
        "Meta | Instagram Reels - new denim range",
        "social",
        "live",
        4.0,
        3.1,
        22,
        34,
        265,
        11,
        "Denim enquiries",
        "Boutique & designer owners",
        50,
        None,
        "Meghna Rao",
    ),
    (
        "Meta | Click-to-WhatsApp - fabric enquiries",
        "social",
        "live",
        3.5,
        2.7,
        31,
        41,
        338,
        19,
        "WhatsApp conversations",
        "Surat garment manufacturers",
        60,
        None,
        "Arjun Bhatt",
    ),
    (
        "Meta | Retargeting - catalogue viewers",
        "social",
        "live",
        2.0,
        1.55,
        27,
        29,
        144,
        14,
        "Recover interest",
        "Catalogue viewers, last 30 days",
        40,
        None,
        "Arjun Bhatt",
    ),
    (
        "Meta | Lookalike - repeat buyers",
        "social",
        "paused",
        3.0,
        2.4,
        12,
        18,
        96,
        5,
        "New-buyer acquisition",
        "Lookalike 1% of existing buyers",
        85,
        None,
        "Meghna Rao",
    ),
    (
        "Meta | Festive season - saree & dupion push",
        "social",
        "ended",
        5.0,
        4.95,
        44,
        52,
        305,
        17,
        "Festive-season orders",
        "Boutique & designer owners",
        150,
        95,
        "Meghna Rao",
    ),
    (
        "Meta | Instagram Reels test - georgette",
        "social",
        "draft",
        1.0,
        0,
        0,
        0,
        0,
        0,
        "Awareness experiment",
        "Boutique & designer owners",
        -5,
        None,
        "Arjun Bhatt",
    ),
    (
        "Google Search - fabric wholesale Surat",
        "search",
        "live",
        3.0,
        2.35,
        17,
        26,
        128,
        9,
        "Inbound wholesale enquiries",
        "Surat garment manufacturers",
        120,
        None,
        "Meghna Rao",
    ),
    (
        "Surat Textile Expo stall",
        "events",
        "ended",
        4.5,
        4.3,
        29,
        48,
        96,
        8,
        "Trade-show leads",
        "Surat garment manufacturers",
        130,
        112,
        "Arjun Bhatt",
    ),
    (
        "Catalogue mailer - Gulf importers",
        "email",
        "live",
        0.6,
        0.42,
        6,
        11,
        58,
        3,
        "Export enquiries",
        "Gulf apparel importers",
        35,
        None,
        "Arjun Bhatt",
    ),
]

# Eight months ending this month: (revenue, spend) in lakh, spend climbing, revenue faster.
REVENUE_SERIES: list[tuple[float, float]] = [
    (58, 6.1),
    (64, 6.9),
    (61, 6.4),
    (72, 8.2),
    (79, 9.0),
    (88, 10.4),
    (97, 11.1),
    (104, 12.0),
]

EXPECTED_MARKETING = {
    "audiences": len(AUDIENCES),
    "campaigns": len(CAMPAIGNS),
    "revenue_points": len(REVENUE_SERIES),
}


def _month_labels(today: date, n: int) -> list[str]:
    labels: list[str] = []
    year, month = today.year, today.month
    for _ in range(n):
        labels.append(date(year, month, 1).strftime("%b"))
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    return list(reversed(labels))


async def seed_marketing(
    session: AsyncSession, ctx: ScopeContext, today: date | None = None
) -> dict[str, int]:
    """Audiences, campaigns and the monthly revenue-vs-spend series. This is a manual tracker:
    the figures are entered, not pulled from Meta."""
    from sqlalchemy import select

    from app import selectors, services  # type: ignore[import-not-found]
    from app.models import Campaign  # type: ignore[import-not-found]
    from app.schemas import AudienceIn, CampaignIn  # type: ignore[import-not-found]

    today = today or date.today()
    org = ctx.organization_id
    counts = dict.fromkeys(EXPECTED_MARKETING, 0)

    audience_ids: dict[str, uuid.UUID] = {}
    for name, size, criteria, ago in AUDIENCES:
        existing = await selectors.get_audience_by_name(session, org, name)
        if existing is not None:
            audience_ids[name] = existing.id
            continue
        created = await services.create_audience(
            session,
            ctx,
            AudienceIn(name=name, size=size, criteria=criteria, created_at=_ago(today, ago)),
        )
        audience_ids[name] = created.id
        counts["audiences"] += 1

    known = set(
        (await session.execute(select(Campaign.name).where(Campaign.organization_id == org)))
        .scalars()
        .all()
    )
    for (
        name,
        channel,
        status,
        budget,
        spent,
        revenue,
        pipeline,
        leads,
        conversions,
        goal,
        audience,
        start_ago,
        end_ago,
        owner,
    ) in CAMPAIGNS:
        if name in known:
            continue
        await services.create_campaign(
            session,
            ctx,
            CampaignIn(
                name=name,
                channel=channel,
                status=status,
                currency=CAMPAIGN_CURRENCY,
                budget_minor=lakh(budget),
                spent_minor=lakh(spent),
                revenue_minor=lakh(revenue),
                pipeline_minor=lakh(pipeline),
                leads=leads,
                conversions=conversions,
                goal=goal,
                audience_id=audience_ids[audience],
                start_date=_ago(today, start_ago),
                end_date=_ago(today, end_ago) if end_ago is not None else None,
                owner=owner,
            ),
        )
        counts["campaigns"] += 1

    labels = _month_labels(today, len(REVENUE_SERIES))
    for position, (label, (revenue, spend)) in enumerate(zip(labels, REVENUE_SERIES, strict=True)):
        await services.upsert_revenue_point(
            session,
            ctx,
            position=position,
            label=label,
            revenue_minor=lakh(revenue),
            spend_minor=lakh(spend),
            currency=CAMPAIGN_CURRENCY,
        )
        counts["revenue_points"] += 1

    return counts


# ============================================================================ NOTIFICATION

# Templates of our own (rendered here, sent as free text). Email only: a WhatsApp message outside
# a 24-hour customer window must be a Meta-approved template (`provider_templates`, below).
# Placeholders resolve against the event payload, so they name real payload fields.
EMAIL_TEMPLATES: list[tuple[str, str, str, str]] = [
    (
        "invoice_issued_internal",
        "Invoice {{number}} issued",
        "Invoice {{number}} was issued to customer {{customer_id}}.\n"
        "Total: {{total_minor}} paise ({{currency}}). Supply type: {{supply_type}}.",
        "Accounts alert when a GST invoice is issued. Internal recipients only: the event does "
        "not carry the customer's email address.",
    ),
    (
        "payment_received_internal",
        "Payment {{reference}} received",
        "Payment {{reference}} of {{amount_minor}} paise was recorded against customer "
        "{{customer_id}}.",
        "Accounts alert when a payment is recorded.",
    ),
    (
        "po_received_store",
        "Purchase order {{number}} received",
        "Purchase order {{number}} was received on {{received_date}}. Stock has been posted.",
        "Store-keeper alert when a purchase order is received into the godown.",
    ),
]

# (code, provider_name, category, status, variables [(key, position)], preview)
# Registered as a local mirror of what Meta holds. None of these has been submitted to Meta, so
# none is `approved`; the status_reason says so. Sending needs a WABA and Meta's approval.
WHATSAPP_TEMPLATES: list[tuple[str, str, str, str, list[tuple[str, int]], str]] = [
    (
        "order_confirmation",
        "order_confirmation",
        "utility",
        "draft",
        [("customer_name", 0), ("order_number", 1), ("promised_date", 2)],
        "Hi {{1}}, we have confirmed order {{2}}. Dispatch is planned for {{3}}.",
    ),
    (
        "payment_reminder",
        "invoice_payment_reminder",
        "utility",
        "pending",
        [("customer_name", 0), ("number", 1), ("amount_due", 2), ("due_date", 3)],
        "Hi {{1}}, invoice {{2}} for {{3}} was due on {{4}}. Please share the payment reference.",
    ),
    (
        "dispatch_update",
        "dispatch_update",
        "utility",
        "draft",
        [("customer_name", 0), ("order_number", 1), ("transporter", 2), ("lr_number", 3)],
        "Hi {{1}}, order {{2}} has left our godown with {{3}}, LR {{4}}.",
    ),
    (
        "invoice_issued_alert",
        "invoice_issued_alert",
        "utility",
        "draft",
        [("number", 0), ("supply_type", 1)],
        "Invoice {{1}} was issued ({{2}}).",
    ),
]

WHATSAPP_STATUS_REASON = "Demo data - not submitted to Meta"

#: The owner's own numbers, never a customer's: no event the platform publishes today carries a
#: customer's phone or a CRM contact id, so an automatic customer-facing WhatsApp message has
#: nothing to address. A fixed internal recipient is what a rule can honestly do now.
INTERNAL_EMAIL = "accounts@demo-textiles.example"
STORE_EMAIL = "godown.surat@demo-textiles.example"
INTERNAL_WHATSAPP = "+910000000000"  # obviously not a real number

# (code, event_type, channel, template_code, provider_template_code, literal, category, dedupe)
RULES: list[tuple[str, str, str, str | None, str | None, str, str, str | None]] = [
    (
        "email_accounts_invoice_issued",
        "finance.billing.invoice.issued",
        "email",
        "invoice_issued_internal",
        None,
        INTERNAL_EMAIL,
        "billing",
        "number",
    ),
    (
        "email_accounts_payment_received",
        "finance.billing.payment.received",
        "email",
        "payment_received_internal",
        None,
        INTERNAL_EMAIL,
        "billing",
        "reference",
    ),
    (
        "email_store_po_received",
        "operations.procurement.purchase_order.received",
        "email",
        "po_received_store",
        None,
        STORE_EMAIL,
        "operational",
        "number",
    ),
    (
        "whatsapp_owner_invoice_issued",
        "finance.billing.invoice.issued",
        "whatsapp",
        None,
        "invoice_issued_alert",
        INTERNAL_WHATSAPP,
        "utility",
        "number",
    ),
]

# (email, name, company, city, segment, lists)
CONTACTS: list[tuple[str, str, str, str, str, tuple[str, ...]]] = [
    (
        "harish@shreeganeshgarments.example",
        "Harish Patel",
        "Shree Ganesh Garments",
        "Surat",
        "Garment maker",
        ("Garment makers",),
    ),
    (
        "ayesha@bharatdenim.example",
        "Ayesha Khan",
        "Bharat Denim Apparels",
        "Bengaluru",
        "Jeans manufacturer",
        ("Garment makers",),
    ),
    (
        "latha@vardhmanfashion.example",
        "Latha Subramaniam",
        "Vardhman Fashion Exports",
        "Tiruppur",
        "Knitwear manufacturer",
        ("Garment makers",),
    ),
    (
        "nirav@seabreezeapparel.example",
        "Nirav Mehta",
        "Sea Breeze Apparel",
        "Surat",
        "Garment exporter",
        ("Garment makers", "Export & SEZ buyers"),
    ),
    (
        "dinesh@patelcloth.example",
        "Dinesh Patel",
        "Patel Cloth Emporium",
        "Ahmedabad",
        "Fabric retailer",
        ("Retail & boutique buyers",),
    ),
    (
        "sanjay@metromenswear.example",
        "Sanjay Kulkarni",
        "Metro Menswear Retail",
        "Mumbai",
        "Menswear retailer",
        ("Retail & boutique buyers",),
    ),
    (
        "kavita@rangoliethnic.example",
        "Kavita Sharma",
        "Rangoli Ethnic Wear",
        "Jaipur",
        "Ethnic-wear brand",
        ("Retail & boutique buyers",),
    ),
    (
        "rakesh@delhifabrichub.example",
        "Rakesh Arora",
        "Delhi Fabric Hub",
        "New Delhi",
        "Wholesale trader",
        ("Retail & boutique buyers",),
    ),
    (
        "buyer@gulfapparel.example",
        "Omar Al Farsi",
        "Gulf Apparel Trading",
        "Dubai",
        "Importer",
        ("Export & SEZ buyers",),
    ),
    (
        "sourcing@lumina-fashion.example",
        "Reem Haddad",
        "Lumina Fashion FZE",
        "Sharjah",
        "Importer",
        ("Export & SEZ buyers",),
    ),
]

LISTS: list[tuple[str, str]] = [
    ("Garment makers", "Manufacturers who buy fabric, yarn and trims by the roll or lot."),
    ("Retail & boutique buyers", "Retailers and brands who buy by the metre."),
    ("Export & SEZ buyers", "SEZ units and overseas importers."),
]

EXPECTED_NOTIFICATION = {
    "email_templates": len(EMAIL_TEMPLATES),
    "whatsapp_templates": len(WHATSAPP_TEMPLATES),
    "rules": len(RULES),
    "contacts": len(CONTACTS),
    "lists": len(LISTS),
}


async def seed_notification(
    session: AsyncSession, ctx: ScopeContext, today: date | None = None
) -> dict[str, int]:
    """Email templates, WhatsApp template registry rows, automation rules bound to real routing
    keys, Engage contacts and lists. Connects nothing: no WhatsApp account, no SMTP identity."""
    from app import selectors, services  # type: ignore[import-not-found]
    from app.services import _normalize_email, _sealed_email  # type: ignore[import-not-found]

    org = ctx.organization_id
    counts = dict.fromkeys(EXPECTED_NOTIFICATION, 0)

    for code, subject, body, description in EMAIL_TEMPLATES:
        if await selectors.get_template(session, org, code, "email") is not None:
            continue
        await services.upsert_template(
            session,
            ctx,
            code=code,
            channel="email",
            subject=subject,
            body=body,
            description=description,
        )
        counts["email_templates"] += 1

    for code, provider_name, category, status, variables, preview in WHATSAPP_TEMPLATES:
        if await selectors.get_provider_template(session, org, code, "whatsapp") is not None:
            continue
        await services.upsert_provider_template(
            session,
            ctx,
            code=code,
            channel="whatsapp",
            provider_name=provider_name,
            language="en",
            category=category,
            parameter_format="positional",
            status=status,
            status_reason=WHATSAPP_STATUS_REASON,
            variables=[{"key": key, "component": "body", "position": pos} for key, pos in variables],
            preview_text=preview,
        )
        counts["whatsapp_templates"] += 1

    for code, event, channel, template, provider, literal, category, dedupe in RULES:
        if await selectors.get_rule(session, org, code) is not None:
            continue
        await services.upsert_rule(
            session,
            ctx,
            code=code,
            event_type=event,
            channel=channel,
            template_code=template,
            provider_template_code=provider,
            recipient_literal=literal,
            category=category,
            dedupe_key_path=dedupe,
        )
        counts["rules"] += 1

    list_ids: dict[str, uuid.UUID] = {}
    for name, description in LISTS:
        existing = await selectors.get_list_by_name(session, org, name)
        if existing is not None:
            list_ids[name] = existing.id
            continue
        created = await services.create_list(session, ctx, name=name, description=description)
        list_ids[name] = created.id
        counts["lists"] += 1

    for email, name, company, city, segment, lists in CONTACTS:
        _, _, index = _sealed_email(_normalize_email(email))
        found = await selectors.contacts_by_index(session, org, [index])
        if found:
            contact_id = found[index].id
        else:
            contact = await services.create_contact(
                session,
                ctx,
                email=email,
                name=name,
                attributes={"company": company, "city": city, "segment": segment},
            )
            contact_id = contact.id
            counts["contacts"] += 1
        for list_name in lists:
            await services.change_list_members(
                session, ctx, list_id=list_ids[list_name], contact_ids=[contact_id], add=True
            )

    return counts


# ========================================================================= standalone entry

_SEEDERS: dict[str, Any] = {
    "operations": seed_operations,
    "finance": seed_finance,
    "marketing": seed_marketing,
    "notification": seed_notification,
}

# The permission a real caller would need for the writes each service's seeder makes. The
# services check permissions at the route, not in `services.*`, so this only makes the demo
# principal read like one that holds it.
_PERMISSION = {
    "operations": "operations.procurement.purchase_order.write",
    "finance": "finance.billing.invoice.write",
    "marketing": "marketing.analytics.campaign.write",
    "notification": "engage.audience.contact.write",
}
_ENTITLEMENT = {
    "operations": "operations",
    "finance": "finance",
    "marketing": "marketing",
    "notification": "engage",
}


async def _child(
    service: str, org_id: uuid.UUID, admin_user_id: uuid.UUID, admin_membership_id: uuid.UUID
) -> None:
    """Run one service's half. The service directory is already on sys.path."""
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import settings  # type: ignore[import-not-found]
    from app.models import SCHEMA  # type: ignore[import-not-found]
    from platform_core.auth import Principal, Scope, ScopeContext
    from platform_core.db import TENANT_GUC

    permission = _PERMISSION[service]
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
            perms=[f"{permission}:ORG"],
            entitlements=[_ENTITLEMENT[service]],
        ),
        scope=Scope.ORG,
        permission=permission,
    )

    async with maker() as session:

        @event.listens_for(session.sync_session, "after_begin")
        def _bind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
            connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org_id}', true)")

        async with session.begin():
            counts = await _SEEDERS[service](session, ctx)

    await engine.dispose()
    print(json.dumps({service: counts}))


def _run_child() -> None:
    """`--child <service> <service_dir> <org> <user> <membership>` — the per-process half."""
    from pathlib import Path

    service = sys.argv[2]
    sys.path.insert(0, str(Path(sys.argv[3]).resolve()))
    asyncio.run(
        _child(service, uuid.UUID(sys.argv[4]), uuid.UUID(sys.argv[5]), uuid.UUID(sys.argv[6]))
    )


def _run_parent() -> None:
    """`<repo_root> <org> <user> <membership>` — one subprocess per service."""
    from pathlib import Path

    root = Path(sys.argv[1]).resolve()
    org, user, membership = sys.argv[2], sys.argv[3], sys.argv[4]
    for service in SERVICES:
        service_dir = root / "services" / service
        subprocess.run(  # noqa: S603 - this interpreter, this file, paths we chose
            [sys.executable, __file__, "--child", service, str(service_dir), org, user, membership],
            cwd=service_dir,
            check=True,
        )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        _run_child()
    else:
        _run_parent()
