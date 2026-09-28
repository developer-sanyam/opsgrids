"""Fixtures for operations-service tests.

Real PostgreSQL rather than SQLite (the schema leans on row-level security), the **app** role
rather than the superuser (so RLS is visible here instead of only in production — a suite run as
a bypassing role makes every isolation assertion a false pass), and the tenant bound on every
`begin()` because the GUC is transaction-local. This mirrors exactly what `get_tenant_session`
does per request.

`organization_id` is a plain UUID, never a foreign key across a schema boundary, so a tenant needs
no account-service row to exist — a bare uuid is a whole tenant.

The `data` fixture seeds a small, coherent operations dataset through the real service write
paths: two suppliers, two purchase orders with lines (raised by two known subjects so OWN scope
has something to narrow to), and inventory including a low-stock item.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# The service directory is the import root: every service exposes a top-level `app`, so only one
# may be on the path in a given process.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import services
from app.config import settings
from app.inventory import services as inv_services
from app.main import app
from app.models import SCHEMA
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC, get_session
from tests.helpers import ctx_for

TENANT_TABLES = (
    "package_lines",
    "packages",
    "packaging_materials",
    "scan_events",
    "pick_exceptions",
    "assignment_offers",
    "pick_task_lines",
    "pick_tasks",
    "worker_position_samples",
    "worker_presence",
    "worker_zone_access",
    "worker_skills",
    "workers",
    "order_transitions",
    "order_lines",
    "orders",
    "fulfilment_settings",
    "purchase_order_lines",
    "purchase_orders",
    "inventory_discrepancies",
    "inventory_reservations",
    "inventory_balances",
    "inventory_ledger",
    "inventory_moves",
    "uom_conversions",
    "item_variants",
    "item_attributes",
    "inventory_items",
    "item_categories",
    "uoms",
    "walk_edges",
    "walk_nodes",
    "location_placements",
    "layout_versions",
    "locations",
    "warehouse_floors",
    "warehouses",
    "suppliers",
    "audit_logs",
    "outbox",
    "processed_events",
)

# Two known subjects, so OWN scope has an owner to match and an outsider to refuse.
SUBJECT_A = uuid.uuid4()
SUBJECT_B = uuid.uuid4()


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


@pytest.fixture
def org_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest_asyncio.fixture
async def session(org_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """A session as the **app** role, with every transaction bound to `org_id`."""
    cfg = settings()
    tables = ", ".join(f'"{SCHEMA}"."{t}"' for t in TENANT_TABLES)

    admin = create_async_engine(cfg.admin_dsn, connect_args=_connect_args())
    async with admin.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    await admin.dispose()

    engine = create_async_engine(cfg.app_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:

        @event.listens_for(s.sync_session, "after_begin")
        def _bind(_session, _transaction, connection) -> None:  # type: ignore[no-untyped-def]
            connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org_id}', true)")

        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def admin_session() -> AsyncIterator[AsyncSession]:
    """A BYPASSRLS session, for seeding rows that belong to *another* tenant."""
    engine = create_async_engine(settings().admin_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def data(session: AsyncSession, org_id: uuid.UUID) -> dict:
    """A small operations dataset seeded through the real write paths."""
    write = ctx_for(org_id, "operations.procurement.purchase_order.write", Scope.ORG)

    async with session.begin():
        await inv_services.create_warehouse(
            session, write, code="WH-PUN", name="Pune Plant", location="Pune"
        )
        await inv_services.create_warehouse(
            session, write, code="WH-BLR", name="Bengaluru Store", location="Bengaluru"
        )

        meridian = await services.create_supplier(
            session,
            write,
            code="SUP-MER",
            name="Meridian Metals",
            category="raw_materials",
            status="active",
            lead_time_days=12,
            on_time_pct=96.4,
            spend_minor=41_20_00_000_00,
            currency="INR",
            open_pos=3,
            contact_name="Rajesh Pillai",
            contact_email="rajesh@meridianmetals.example",
            location="Jamshedpur",
            since=date(2019, 4, 2),
        )
        volt = await services.create_supplier(
            session,
            write,
            code="SUP-VLT",
            name="Volt Components",
            category="components",
            status="active",
            lead_time_days=21,
            on_time_pct=88.7,
            spend_minor=19_45_00_000_00,
            currency="INR",
            open_pos=4,
            contact_name="Karthik Iyer",
            contact_email="karthik@voltcomponents.example",
            location="Bengaluru",
            since=date(2020, 3, 27),
        )

        # An open PO raised by SUBJECT_A, past its expected date (so it counts as late).
        po_a = await services.create_purchase_order(
            session,
            write,
            number="PO-2026-0148",
            supplier_id=meridian.id,
            status="approved",
            warehouse="Pune Plant",
            raised_by="Devansh Rao",
            raised_by_id=SUBJECT_A,
            currency="INR",
            created_date=date(2026, 8, 6),
            expected_date=date(2026, 8, 22),
            received_date=None,
            notes="Priority run for the Q3 casting line.",
            lines=[
                {
                    "sku": "RM-STL-4501",
                    "description": "Cold-rolled steel coil, 1.2mm",
                    "quantity": 40,
                    "uom": "MT",
                    "unit_price_minor": 62_500_00,
                    "received_qty": 0,
                },
                {
                    "sku": "RM-STL-4520",
                    "description": "Galvanised steel sheet, 0.8mm",
                    "quantity": 25,
                    "uom": "MT",
                    "unit_price_minor": 71_200_00,
                    "received_qty": 0,
                },
            ],
        )
        # A received PO raised by SUBJECT_B.
        po_b = await services.create_purchase_order(
            session,
            write,
            number="PO-2026-0140",
            supplier_id=volt.id,
            status="received",
            warehouse="Bengaluru Store",
            raised_by="Priya Nair",
            raised_by_id=SUBJECT_B,
            currency="INR",
            created_date=date(2026, 7, 15),
            expected_date=date(2026, 8, 4),
            received_date=date(2026, 8, 6),
            notes="Two days late — partial short-ship reconciled.",
            lines=[
                {
                    "sku": "CMP-SEN-330",
                    "description": "Proximity sensor, M18",
                    "quantity": 800,
                    "uom": "pcs",
                    "unit_price_minor": 410_00,
                    "received_qty": 800,
                },
            ],
        )

        # One low-stock item (below reorder point) and one healthy one.
        low_stock_item = await inv_services.create_inventory_item(
            session,
            write,
            sku="RM-ALU-7075",
            name="Aluminium billet, 7075-T6",
            category="raw_materials",
            warehouse="Pune Plant",
            on_hand=6,
            reorder_point=12,
            uom="MT",
            unit_value_minor=2_18_000_00,
            currency="INR",
            stocked_at=date(2026, 8, 22),
        )
        healthy_item = await inv_services.create_inventory_item(
            session,
            write,
            sku="CMP-REL-014",
            name="24V industrial relay",
            category="components",
            warehouse="Bengaluru Store",
            on_hand=4200,
            reorder_point=1500,
            uom="pcs",
            unit_value_minor=245_00,
            currency="INR",
            stocked_at=date(2026, 8, 23),
        )

    return {
        "meridian": meridian,
        "volt": volt,
        "po_a": po_a,
        "po_b": po_b,
        "subject_a": SUBJECT_A,
        "subject_b": SUBJECT_B,
        "low_stock_item": low_stock_item,
        "healthy_item": healthy_item,
    }


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """HTTP client sharing the test's tenant-bound session, so writes are visible to asserts."""

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _session_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
