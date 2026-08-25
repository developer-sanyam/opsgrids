"""Seed the finance book for the demo org — customers, invoices, credit notes and payments.

Mirrors `frontend/product-finance/src/api/fixtures.ts` and `src/data/customers.ts` exactly: the
same 12 customers, 21 documents (19 invoices + 2 credit notes) and 9 payments, with the same
numbers, amounts, tax rates, statuses and allocations. So when the SPA is flipped from fixtures
to live (`VITE_USE_FIXTURES=false`) the screens show the data the user already approved.

Like `tools/demo/seed_employee.py`, it connects as the NOBYPASSRLS app role and binds the tenant
GUC on every transaction (the GUC is transaction-local), so every write goes through the same
row-level-security path a real request does — and through the service's own `services.*` write
functions, so totals foot and audit + outbox rows are written exactly as in production.

The book is GST-complete: the org's own registration is seeded first (Maharashtra), every customer
GSTIN is checksum-valid, and every line carries an HSN or SAC code — so the demo documents are
documents that can actually be filed with the Invoice Registration Portal, and the CGST/SGST vs
IGST split is exercised on real data rather than only in tests.

Idempotent: a GST registration, customer name, invoice number or payment reference that already
exists in the org is reused/skipped, so re-running does not duplicate.

Usage: seed_finance.py <finance_service_dir> <org_id> <admin_user_id> <admin_membership_id>
       DATABASE_URL must point at atmora_wt (never the shared atmora).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.auth import ScopeContext

# --------------------------------------------------------------------- fixture data
# Rupee figures are written for legibility and converted to paise (minor units) on the way in.

REFERENCE_TODAY = date(2026, 8, 24)  # the fixtures' "today" — statuses are frozen against it

#: The demo organization's own GST registration. Maharashtra, so the two Maharashtra customers
#: (Orbit Logistics in Pune, Deccan AgriTech in Nagpur) are intra-state supplies taxed CGST+SGST
#: and everyone else is inter-state IGST — both halves of the split are visible in the demo book
#: rather than one of them being theoretical.
#:
#: Every GSTIN in this file is **checksum-valid**: `services.create_customer` parses each one
#: before storing it, so an invented number fails the seed rather than sitting in the demo data
#: looking real. They were computed with `domain.gstin_check_digit`.
SELLER_GSTIN = "27AABCA9012D1Z5"
SELLER_LEGAL_NAME = "Atmora Industries Private Limited"
SELLER_TRADE_NAME = "Atmora"


@dataclass(frozen=True)
class CustomerSeed:
    key: str
    name: str
    contact_name: str
    email: str
    gstin: str | None
    city: str
    state: str


CUSTOMERS: list[CustomerSeed] = [
    CustomerSeed(
        "cust-01",
        "Meridian Textiles Pvt Ltd",
        "Harish Patel",
        "accounts@meridiantex.in",
        "24AABCM1234F1Z3",
        "Surat",
        "Gujarat",
    ),
    CustomerSeed(
        "cust-02",
        "Kanaka Foods Ltd",
        "Latha Subramaniam",
        "finance@kanakafoods.in",
        "33AAACK5678G1ZH",
        "Chennai",
        "Tamil Nadu",
    ),
    CustomerSeed(
        "cust-03",
        "Orbit Logistics",
        "Devendra Joshi",
        "billing@orbitlogistics.in",
        "27AAECO9012H1ZG",
        "Pune",
        "Maharashtra",
    ),
    CustomerSeed(
        "cust-04",
        "Sundara Electricals",
        "Ravi Gopal",
        "ap@sundaraelec.in",
        "29AADCS3456J1Z0",
        "Bengaluru",
        "Karnataka",
    ),
    CustomerSeed(
        "cust-05",
        "Nimbus Cloud Services",
        "Ayesha Khan",
        "ap@nimbuscloud.in",
        "36AAFCN7890K1ZS",
        "Hyderabad",
        "Telangana",
    ),
    CustomerSeed(
        "cust-06",
        "Trishul Steel Works",
        "Manoj Agrawal",
        "accounts@trishulsteel.in",
        "22AAGCT2345L1ZC",
        "Raipur",
        "Chhattisgarh",
    ),
    CustomerSeed(
        "cust-07",
        "Anand Pharma",
        "Sunita Rao",
        "finance@anandpharma.in",
        "24AAHCA6789M1Z0",
        "Ahmedabad",
        "Gujarat",
    ),
    CustomerSeed(
        "cust-08",
        "Coastal Traders LLP",
        "Thomas Kurian",
        "billing@coastaltraders.in",
        "32AAJFC0123N1ZR",
        "Kochi",
        "Kerala",
    ),
    CustomerSeed(
        "cust-09",
        "Vega Interiors",
        "Priyanka Sethi",
        "accounts@vegainteriors.in",
        "06AAKCV4567P1ZG",
        "Gurugram",
        "Haryana",
    ),
    CustomerSeed(
        "cust-10",
        "Lotus Hospitality Group",
        "Rajat Chauhan",
        "ap@lotushospitality.in",
        "08AALCL8901Q1ZP",
        "Jaipur",
        "Rajasthan",
    ),
    CustomerSeed(
        "cust-11",
        "Deccan AgriTech",
        "Vaishnavi Kulkarni",
        "finance@deccanagritech.in",
        "27AAMCD2345R1Z0",
        "Nagpur",
        "Maharashtra",
    ),
    CustomerSeed(
        "cust-12",
        "Apex Motors",
        "Iqbal Ahmed",
        "accounts@apexmotors.in",
        "07AANCA6789S1ZE",
        "New Delhi",
        "Delhi",
    ),
]


@dataclass(frozen=True)
class LineSeed:
    description: str
    quantity: float
    rate: float  # rupees per unit
    #: HSN (goods) or SAC (services), six digits — the length mandatory above ₹5 crore turnover,
    #: which is the same threshold e-invoicing binds at. Without one the portal refuses the line,
    #: so a demo book with no codes would be a demo book that cannot be filed.
    hsn: str = ""


@dataclass(frozen=True)
class InvoiceSeed:
    number: str
    customer_key: str
    issued_date: str
    due_date: str
    tax_rate_pct: float
    lines: list[LineSeed]
    document_type: str = "invoice"
    status_override: str | None = None
    corrects: str | None = None
    notes: str | None = None


INVOICES: list[InvoiceSeed] = [
    InvoiceSeed(
        "INV-2026-0120",
        "cust-06",
        "2026-03-10",
        "2026-04-09",
        18,
        [LineSeed("MS plates 10mm (tonnes)", 18, 58000, "720851")],
        notes="Site delivery, Raipur plant. Payment overdue — third reminder issued.",
    ),
    InvoiceSeed(
        "INV-2026-0122",
        "cust-08",
        "2026-05-01",
        "2026-05-31",
        18,
        [LineSeed("Teak wood logs, seasoned (cft)", 300, 3200, "440349")],
    ),
    InvoiceSeed(
        "INV-2026-0130",
        "cust-01",
        "2026-04-15",
        "2026-05-15",
        18,
        [LineSeed("Cotton yarn — 30s count (kg)", 1800, 265, "520512")],
    ),
    InvoiceSeed(
        "INV-2026-0137",
        "cust-03",
        "2026-04-28",
        "2026-05-28",
        18,
        [LineSeed("Line-haul freight — Pune⇄Mumbai (trips)", 30, 18000, "996511")],
    ),
    InvoiceSeed(
        "INV-2026-0142",
        "cust-01",
        "2026-05-02",
        "2026-06-01",
        18,
        [
            LineSeed("Cotton yarn — 40s count (kg)", 2400, 285, "520512"),
            LineSeed("Dyeing & finishing charges", 1, 65000, "998821"),
        ],
    ),
    InvoiceSeed(
        "INV-2026-0145",
        "cust-04",
        "2026-05-20",
        "2026-06-19",
        18,
        [LineSeed("Ceiling fan — BLDC (units)", 700, 2350, "841451")],
    ),
    InvoiceSeed(
        "INV-2026-0148",
        "cust-02",
        "2026-06-10",
        "2026-07-10",
        5,
        [
            LineSeed("Packaged spices — bulk (cartons)", 320, 1250, "091099"),
            LineSeed("Cold storage handling", 1, 28000, "996799"),
        ],
    ),
    InvoiceSeed(
        "INV-2026-0151",
        "cust-03",
        "2026-06-18",
        "2026-07-18",
        18,
        [
            LineSeed("Line-haul freight — Pune⇄Delhi (trips)", 42, 18500, "996511"),
            LineSeed("Fuel surcharge", 1, 54000, "996511"),
        ],
    ),
    InvoiceSeed(
        "INV-2026-0155",
        "cust-04",
        "2026-07-01",
        "2026-07-31",
        18,
        [
            LineSeed("LED panel 40W (units)", 1500, 640, "940540"),
            LineSeed("Installation labour (site-days)", 12, 4200, "995461"),
        ],
        notes="Partly paid. Credit note CN-2026-0008 issued for 30 returned panels.",
    ),
    InvoiceSeed(
        "INV-2026-0158",
        "cust-02",
        "2026-07-14",
        "2026-08-13",
        5,
        [LineSeed("Edible oil — refined (litres)", 6000, 145, "151790")],
    ),
    InvoiceSeed(
        "INV-2026-0160",
        "cust-05",
        "2026-07-20",
        "2026-08-19",
        18,
        [
            LineSeed("Managed hosting — August (nodes)", 24, 8500, "998315"),
            LineSeed("Support retainer", 1, 75000, "998313"),
        ],
    ),
    InvoiceSeed(
        "INV-2026-0163",
        "cust-06",
        "2026-07-28",
        "2026-08-27",
        18,
        [
            LineSeed("TMT bars Fe500 (tonnes)", 60, 62000, "721420"),
            LineSeed("Cutting & bending", 1, 145000, "998873"),
        ],
    ),
    InvoiceSeed(
        "INV-2026-0166",
        "cust-07",
        "2026-08-02",
        "2026-09-01",
        12,
        [
            LineSeed("API — Paracetamol grade (kg)", 800, 1450, "292429"),
            LineSeed("QC batch testing", 6, 18000, "998346"),
        ],
        notes="Advance of ₹3,00,000 received against this invoice.",
    ),
    InvoiceSeed(
        "INV-2026-0169",
        "cust-08",
        "2026-08-05",
        "2026-09-04",
        18,
        [
            LineSeed("Marine plywood 18mm (sheets)", 900, 2100, "441231"),
            LineSeed("Freight to yard", 1, 42000, "996511"),
        ],
        notes="Credit note CN-2026-0012 issued for 40 damaged sheets.",
    ),
    InvoiceSeed(
        "INV-2026-0170",
        "cust-09",
        "2026-08-06",
        "2026-09-05",
        18,
        [LineSeed("Reception desk — custom (units)", 2, 145000, "940330")],
        status_override="void",
        notes="Voided — superseded by INV-2026-0172 after a spec change.",
    ),
    InvoiceSeed(
        "INV-2026-0172",
        "cust-09",
        "2026-08-08",
        "2026-09-07",
        18,
        [
            LineSeed("Modular workstation (units)", 48, 22500, "940330"),
            LineSeed("On-site assembly", 1, 96000, "995466"),
        ],
    ),
    InvoiceSeed(
        "INV-2026-0175",
        "cust-10",
        "2026-08-12",
        "2026-09-11",
        18,
        [
            LineSeed("Banquet catering (per plate)", 850, 1250, "996334"),
            LineSeed("Décor & floral", 1, 185000, "998596"),
        ],
        status_override="draft",
        notes="Draft — awaiting final headcount from the events team.",
    ),
    InvoiceSeed(
        "INV-2026-0178",
        "cust-11",
        "2026-08-15",
        "2026-09-14",
        5,
        [
            LineSeed("Drip irrigation kit (acres)", 120, 7800, "842482"),
            LineSeed("Agronomy advisory", 1, 60000, "998611"),
        ],
        notes="Part payment of ₹2,00,000 received on account.",
    ),
    InvoiceSeed(
        "INV-2026-0181",
        "cust-12",
        "2026-08-18",
        "2026-09-17",
        28,
        [
            LineSeed("Spare parts — brake assembly (sets)", 220, 3400, "870830"),
            LineSeed("Logistics & handling", 1, 36000, "996511"),
        ],
    ),
    # Credit notes — append-only corrections, never edits to the original invoice.
    InvoiceSeed(
        "CN-2026-0008",
        "cust-04",
        "2026-07-22",
        "2026-07-22",
        18,
        [LineSeed("Return — LED panel 40W (units)", -30, 640, "940540")],
        document_type="credit_note",
        corrects="INV-2026-0155",
        notes="Correction: 30 LED panels returned as faulty.",
    ),
    InvoiceSeed(
        "CN-2026-0012",
        "cust-08",
        "2026-08-14",
        "2026-08-14",
        18,
        [LineSeed("Return — Marine plywood 18mm (sheets)", -40, 2100, "441231")],
        document_type="credit_note",
        corrects="INV-2026-0169",
        notes="Correction: 40 marine plywood sheets damaged in transit.",
    ),
]


@dataclass(frozen=True)
class AllocSeed:
    number: str
    amount: float  # rupees applied to that invoice


@dataclass(frozen=True)
class PaymentSeed:
    reference: str
    customer_key: str
    received_date: str
    method: str
    allocations: list[AllocSeed]
    note: str | None = None


PAYMENTS: list[PaymentSeed] = [
    PaymentSeed(
        "PMT-2026-0288",
        "cust-01",
        "2026-05-14",
        "bank_transfer",
        [AllocSeed("INV-2026-0130", 562860)],
        note="NEFT · UTR AXIS0288",
    ),
    PaymentSeed(
        "PMT-2026-0295",
        "cust-03",
        "2026-05-27",
        "cheque",
        [AllocSeed("INV-2026-0137", 637200)],
        note="Cheque 114520 · HDFC",
    ),
    PaymentSeed(
        "PMT-2026-0301",
        "cust-01",
        "2026-06-01",
        "bank_transfer",
        [AllocSeed("INV-2026-0142", 883820)],
        note="RTGS · UTR ICIC0301",
    ),
    PaymentSeed(
        "PMT-2026-0305",
        "cust-04",
        "2026-06-18",
        "bank_transfer",
        [AllocSeed("INV-2026-0145", 1941100)],
        note="NEFT · UTR SBIN0305",
    ),
    PaymentSeed(
        "PMT-2026-0331",
        "cust-02",
        "2026-07-09",
        "upi",
        [AllocSeed("INV-2026-0148", 449400)],
        note="UPI · kanaka@okhdfcbank",
    ),
    PaymentSeed(
        "PMT-2026-0352",
        "cust-04",
        "2026-08-10",
        "bank_transfer",
        [AllocSeed("INV-2026-0155", 500000)],
        note="NEFT · part settlement",
    ),
    PaymentSeed(
        "PMT-2026-0357",
        "cust-08",
        "2026-08-20",
        "upi",
        [AllocSeed("INV-2026-0169", 800000)],
        note="UPI · coastal@okicici",
    ),
    PaymentSeed(
        "PMT-2026-0360",
        "cust-07",
        "2026-08-22",
        "card",
        [AllocSeed("INV-2026-0166", 300000)],
        note="Corporate card · advance",
    ),
    PaymentSeed(
        "PMT-2026-0364",
        "cust-11",
        "2026-08-23",
        "cash",
        [AllocSeed("INV-2026-0178", 200000)],
        note="Cash · receipt 0364",
    ),
]

#: What the seed should produce, asserted by the test and printed on completion.
EXPECTED_COUNTS = {
    "customers": len(CUSTOMERS),
    "invoices": len(INVOICES),
    "payments": len(PAYMENTS),
}


def _paise(rupees: float) -> int:
    return round(rupees * 100)


def _paid_by_number() -> dict[str, int]:
    paid: dict[str, int] = {}
    for payment in PAYMENTS:
        for alloc in payment.allocations:
            paid[alloc.number] = paid.get(alloc.number, 0) + _paise(alloc.amount)
    return paid


def _expected_status(seed: InvoiceSeed, paid: int) -> str:
    """Replicate the fixtures' deriveStatus, frozen against REFERENCE_TODAY.

    The tax is summed **per line**, matching what `services.create_invoice` now does, so this
    prediction and the row it predicts cannot disagree by a paisa on an odd rate.
    """
    if seed.status_override:
        return seed.status_override
    if seed.document_type == "credit_note":
        return "sent"
    amounts = [round(line.quantity * _paise(line.rate)) for line in seed.lines]
    subtotal = sum(amounts)
    tax = sum(round(amount * seed.tax_rate_pct / 100) for amount in amounts)
    total = subtotal + tax
    if paid >= total:
        return "paid"
    return "overdue" if date.fromisoformat(seed.due_date) < REFERENCE_TODAY else "sent"


# --------------------------------------------------------------------- the seed


async def seed_finance(session: AsyncSession, ctx: ScopeContext) -> dict[str, int]:
    """Insert the demo book through the service's own write path. Caller owns the transaction.

    Returns a summary of what was created vs skipped. Importable so a test can drive it against a
    tenant-bound session; `main()` wires the engine and GUC for the CLI/subprocess path.
    """
    from sqlalchemy import func, select

    # `app` resolves only with the finance service dir on the path (inserted by `__main__`, or
    # already present in the test process), so these are lazy and unresolvable to a tools-wide
    # type check.
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

    org_id = ctx.organization_id
    created = {"customers": 0, "invoices": 0, "payments": 0, "registrations": 0}
    skipped = {"customers": 0, "invoices": 0, "payments": 0, "registrations": 0}

    # 0) The seller's own GST registration, **first**: every invoice below reads it to decide its
    #    place of supply and whether the tax is CGST+SGST or IGST. Seeded after the fact, the
    #    documents would already be frozen with no seller identity.
    if await selectors.get_gst_registration(session, org_id) is None:
        await services.set_gst_registration(
            session,
            ctx,
            GstRegistrationUpsert(
                gstin=SELLER_GSTIN,
                legal_name=SELLER_LEGAL_NAME,
                trade_name=SELLER_TRADE_NAME,
            ),
        )
        created["registrations"] += 1
    else:
        skipped["registrations"] += 1

    # 1) Customers — reuse by name so a re-run does not duplicate.
    customer_ids: dict[str, uuid.UUID] = {}
    for cust in CUSTOMERS:
        existing = (
            await session.execute(
                select(Customer.id).where(
                    Customer.organization_id == org_id,
                    func.lower(Customer.name) == cust.name.lower(),
                    Customer.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            customer_ids[cust.key] = existing
            skipped["customers"] += 1
            continue
        customer = await services.create_customer(
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
        customer_ids[cust.key] = customer.id
        created["customers"] += 1

    paid_by_number = _paid_by_number()

    # 2) Invoices and credit notes — ordinary invoices first so a credit note can reference one.
    invoice_ids: dict[str, uuid.UUID] = {}
    ordered = sorted(INVOICES, key=lambda s: s.document_type == "credit_note")
    for inv_seed in ordered:
        if await selectors.number_exists(session, org_id, inv_seed.number):
            existing_inv = (
                await session.execute(
                    select(Invoice.id).where(
                        Invoice.organization_id == org_id,
                        Invoice.number == inv_seed.number,
                    )
                )
            ).scalar_one()
            invoice_ids[inv_seed.number] = existing_inv
            skipped["invoices"] += 1
            continue
        status = _expected_status(inv_seed, paid_by_number.get(inv_seed.number, 0))
        invoice = await services.create_invoice(
            session,
            ctx,
            InvoiceCreate(
                number=inv_seed.number,
                customer_id=customer_ids[inv_seed.customer_key],
                issued_date=date.fromisoformat(inv_seed.issued_date),
                due_date=date.fromisoformat(inv_seed.due_date),
                tax_rate_pct=inv_seed.tax_rate_pct,
                lines=[
                    InvoiceLineCreate(
                        description=line.description,
                        quantity=line.quantity,
                        unit_price_minor=_paise(line.rate),
                        hsn_sac=line.hsn or None,
                    )
                    for line in inv_seed.lines
                ],
                document_type=inv_seed.document_type,
                status=status,
                notes=inv_seed.notes,
                corrects_invoice_id=(
                    invoice_ids.get(inv_seed.corrects) if inv_seed.corrects else None
                ),
            ),
        )
        invoice_ids[inv_seed.number] = invoice.id
        created["invoices"] += 1

    # 3) Payments — split across the invoices they settle.
    for payment in PAYMENTS:
        if await selectors.reference_exists(session, org_id, payment.reference):
            skipped["payments"] += 1
            continue
        await services.record_payment(
            session,
            ctx,
            PaymentCreate(
                reference=payment.reference,
                customer_id=customer_ids[payment.customer_key],
                received_date=date.fromisoformat(payment.received_date),
                method=payment.method,
                allocations=[
                    PaymentAllocationCreate(
                        invoice_id=invoice_ids[alloc.number],
                        amount_minor=_paise(alloc.amount),
                    )
                    for alloc in payment.allocations
                ],
                note=payment.note,
            ),
        )
        created["payments"] += 1

    return {
        "created_registrations": created["registrations"],
        "created_customers": created["customers"],
        "created_invoices": created["invoices"],
        "created_payments": created["payments"],
        "skipped_registrations": skipped["registrations"],
        "skipped_customers": skipped["customers"],
        "skipped_invoices": skipped["invoices"],
        "skipped_payments": skipped["payments"],
    }


async def _main() -> None:
    import json
    import sys

    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import settings  # type: ignore[import-not-found]
    from app.models import SCHEMA
    from platform_core.auth import Principal, Scope
    from platform_core.db import TENANT_GUC

    org_id = uuid.UUID(sys.argv[2])
    admin_user_id = uuid.UUID(sys.argv[3])
    admin_membership_id = uuid.UUID(sys.argv[4])

    ctx = ScopeContext(
        principal=Principal(
            sub=admin_user_id,
            organization_id=org_id,
            membership_id=admin_membership_id,
            perms=["finance.billing.invoice.read:ORG"],
            entitlements=["finance"],
        ),
        scope=Scope.ORG,
        permission="finance.billing.invoice.read",
    )

    cfg = settings()
    connect_args = {"server_settings": {"search_path": f"{SCHEMA},public"}}
    engine = create_async_engine(cfg.app_dsn, connect_args=connect_args)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as session:

        @event.listens_for(session.sync_session, "after_begin")
        def _bind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
            connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org_id}', true)")

        async with session.begin():
            summary = await seed_finance(session, ctx)

    await engine.dispose()
    print(json.dumps({**summary, "expected": EXPECTED_COUNTS}))


if __name__ == "__main__":
    import asyncio
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
    asyncio.run(_main())
