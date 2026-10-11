# ruff: noqa: S608 — every f-string here interpolates only the SCHEMA constant; values are bound.
"""`c5e91a7d3b28` (requisitions, goods receipts, receipt tolerance), run for real against purchase
orders and items that predate it.

Drives alembic in a subprocess down to the revision right before this one, seeds an old-shape
purchase order that had already received 3 of 10 units and an item, up to head, and checks: the
whole-unit `received_qty` became exact `received_milli` (x1000) with nothing else changed, every
existing item has tolerance 0, the four new tables carry RLS and a tenant policy, the two receipt
tables refuse `UPDATE`/`DELETE` for the app role (and keep refusing after a re-applied grant),
the idempotency-key index is unique per organization, and the schema goes back down and up again.
Always leaves the schema at head.
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.models import SCHEMA

pytestmark = pytest.mark.integration

SERVICE_DIR = Path(__file__).resolve().parent.parent
BEFORE = "d7a3c9e5b184"  # alembic revision id  # pragma: allowlist secret
NEW_TABLES = (
    "purchase_requisitions",
    "purchase_requisition_lines",
    "goods_receipts",
    "goods_receipt_lines",
)
TRUNCATE = (
    "goods_receipt_lines",
    "goods_receipts",
    "purchase_requisition_lines",
    "purchase_requisitions",
    "purchase_order_lines",
    "purchase_orders",
    "suppliers",
    "inventory_items",
    "locations",
    "warehouses",
    "audit_logs",
    "outbox",
)


def alembic(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  (fixed argv, sys.executable, no shell)
        [sys.executable, "-m", "alembic", *args],
        cwd=SERVICE_DIR,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
async def admin():  # type: ignore[no-untyped-def]
    engine = create_async_engine(
        settings().admin_dsn,
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
    )
    async with engine.begin() as conn:
        existing = [
            t
            for t in TRUNCATE
            if (await conn.execute(text("SELECT to_regclass(:t)"), {"t": f"{SCHEMA}.{t}"})).scalar()
        ]
        await conn.execute(text(f"TRUNCATE {', '.join(f'{SCHEMA}.{t}' for t in existing)} CASCADE"))
    try:
        down = alembic("downgrade", BEFORE)
        assert down.returncode == 0, down.stderr[-2000:]
        yield engine
    finally:
        up = alembic("upgrade", "head")
        await engine.dispose()
        assert up.returncode == 0, up.stderr[-2000:]


async def seed_old_shape(conn, org: uuid.UUID) -> dict[str, uuid.UUID]:  # type: ignore[no-untyped-def]
    """A supplier, a warehouse and item, and an approved PO with two lines that had received 3 of
    10 and 0 of 5 units under the old whole-unit model."""
    ids = {n: uuid.uuid4() for n in ("wh", "loc", "item", "sup", "po", "line_a", "line_b")}
    p = {**ids, "org": org}
    await conn.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.warehouses (id, organization_id, code, name, location,
                                             created_at, updated_at)
            VALUES (:wh, :org, 'WH-MIG', 'Migration Plant', 'Pune', now(), now())
            """
        ),
        p,
    )
    await conn.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.locations (id, organization_id, warehouse_id, kind, code, name,
                                            is_default, created_at, updated_at)
            VALUES (:loc, :org, :wh, 'bin', 'DEFAULT', '', true, now(), now())
            """
        ),
        p,
    )
    await conn.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.inventory_items
                (id, organization_id, sku, name, category, warehouse, home_location_id,
                 reorder_point, uom, unit_value_minor, currency, stocked_at, hsn_code, track_lot,
                 track_roll, track_expiry, track_quality, is_active, created_at, updated_at)
            VALUES (:item, :org, 'BTN-1', 'Button', 'components', 'Migration Plant', :loc,
                    0, 'pcs', 0, 'INR', '2026-09-01', '', false, false, false, false, true, now(),
                    now())
            """
        ),
        p,
    )
    await conn.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.suppliers
                (id, organization_id, code, name, category, status, lead_time_days, on_time_pct,
                 spend_minor, currency, open_pos, contact_name, contact_email, location,
                 created_at, updated_at)
            VALUES (:sup, :org, 'SUP-1', 'Supplier', 'components', 'active', 0, 0, 0, 'INR', 1,
                    '', '', '', now(), now())
            """
        ),
        p,
    )
    await conn.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.purchase_orders
                (id, organization_id, number, supplier_id, supplier_name, category, status,
                 currency, warehouse, raised_by, created_date, expected_date, created_at,
                 updated_at)
            VALUES (:po, :org, 'PO-MIG-1', :sup, 'Supplier', 'components', 'approved', 'INR',
                    'Migration Plant', 'Tester', '2026-09-01', '2026-09-10', now(), now())
            """
        ),
        p,
    )
    for line, sku, qty, received in (("line_a", "BTN-1", 10, 3), ("line_b", "SVC-1", 5, 0)):
        await conn.execute(
            text(
                f"""
                INSERT INTO {SCHEMA}.purchase_order_lines
                    (id, organization_id, purchase_order_id, sku, description, quantity, uom,
                     unit_price_minor, amount_minor, received_qty, created_at, updated_at)
                VALUES (:id, :org, :po, :sku, '', :qty, 'pcs', 100, :amount, :received, now(),
                        now())
                """
            ),
            {
                **p,
                "id": ids[line],
                "sku": sku,
                "qty": qty,
                "amount": qty * 100,
                "received": received,
            },
        )
    return ids


async def test_whole_unit_receipts_become_exact_milli_and_nothing_else_changes(admin) -> None:
    org = uuid.uuid4()
    async with admin.begin() as conn:
        ids = await seed_old_shape(conn, org)

    up = alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]

    async with admin.begin() as conn:
        lines = {
            r.id: r
            for r in (
                await conn.execute(
                    text(
                        f"SELECT id, quantity, received_qty, received_milli, short_closed "
                        f"FROM {SCHEMA}.purchase_order_lines WHERE organization_id = :o"
                    ),
                    {"o": org},
                )
            ).all()
        }
        a, b = lines[ids["line_a"]], lines[ids["line_b"]]
        assert (a.quantity, a.received_qty, a.received_milli, a.short_closed) == (
            10,
            3,
            3_000,
            False,
        )
        assert (b.quantity, b.received_qty, b.received_milli, b.short_closed) == (5, 0, 0, False)
        item = (
            await conn.execute(
                text(
                    f"SELECT receipt_tolerance_bp FROM {SCHEMA}.inventory_items "
                    "WHERE organization_id = :o"
                ),
                {"o": org},
            )
        ).one()
        assert item.receipt_tolerance_bp == 0
        status = (
            await conn.execute(
                text(f"SELECT status FROM {SCHEMA}.purchase_orders WHERE organization_id = :o"),
                {"o": org},
            )
        ).scalar_one()
        assert status == "approved"  # a PO is not moved to partially_received retroactively


async def test_new_tables_carry_rls_a_policy_and_the_receipts_are_append_only(admin) -> None:
    up = alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]
    app_role = settings().db_app_role
    async with admin.begin() as conn:
        for table in NEW_TABLES:
            rls = (
                await conn.execute(
                    text(
                        "SELECT c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = :s AND c.relname = :t"
                    ),
                    {"s": SCHEMA, "t": table},
                )
            ).one()
            assert tuple(rls) == (True, True), table
            policy = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM pg_policies WHERE schemaname = :s "
                        "AND tablename = :t AND policyname = 'tenant_isolation'"
                    ),
                    {"s": SCHEMA, "t": table},
                )
            ).scalar_one()
            assert policy == 1, table
            org_col = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM information_schema.columns WHERE table_schema = :s "
                        "AND table_name = :t AND column_name = 'organization_id' "
                        "AND is_nullable = 'NO'"
                    ),
                    {"s": SCHEMA, "t": table},
                )
            ).scalar_one()
            assert org_col == 1, table
        for table in ("goods_receipts", "goods_receipt_lines"):
            for privilege, expected in (
                ("SELECT", True),
                ("INSERT", True),
                ("UPDATE", False),
                ("DELETE", False),
            ):
                granted = (
                    await conn.execute(
                        text("SELECT has_table_privilege(:r, :t, :p)"),
                        {"r": app_role, "t": f"{SCHEMA}.{table}", "p": privilege},
                    )
                ).scalar_one()
                assert granted is expected, (table, privilege)
        for table in ("purchase_requisitions", "purchase_requisition_lines"):
            assert (
                await conn.execute(
                    text("SELECT has_table_privilege(:r, :t, 'UPDATE')"),
                    {"r": app_role, "t": f"{SCHEMA}.{table}"},
                )
            ).scalar_one()
    # re-running the migration's grant pass (a no-op upgrade) must not hand UPDATE back
    again = alembic("upgrade", "head")
    assert again.returncode == 0
    async with admin.begin() as conn:
        assert not (
            await conn.execute(
                text("SELECT has_table_privilege(:r, :t, 'UPDATE')"),
                {"r": app_role, "t": f"{SCHEMA}.goods_receipts"},
            )
        ).scalar_one()


async def test_the_idempotency_key_is_unique_per_organization_and_null_keys_are_free(
    admin,
) -> None:
    org, other = uuid.uuid4(), uuid.uuid4()
    async with admin.begin() as conn:
        ids = await seed_old_shape(conn, org)
    assert alembic("upgrade", "head").returncode == 0

    async def insert_receipt(conn, organization: uuid.UUID, number: str, key: str | None) -> None:  # type: ignore[no-untyped-def]
        await conn.execute(
            text(
                f"""
                INSERT INTO {SCHEMA}.goods_receipts
                    (id, organization_id, number, purchase_order_id, supplier_id, received_on,
                     status, idempotency_key, request_hash)
                VALUES (gen_random_uuid(), :org, :number, :po, :sup, '2026-09-02', 'POSTED',
                        CAST(:key AS text),
                        CASE WHEN CAST(:key AS text) IS NULL THEN NULL ELSE 'h' END)
                """
            ),
            {"org": organization, "number": number, "po": ids["po"], "sup": ids["sup"], "key": key},
        )

    async with admin.begin() as conn:
        await insert_receipt(conn, org, "GRN-1", "k")
        await insert_receipt(conn, org, "GRN-2", None)
        await insert_receipt(conn, org, "GRN-3", None)  # many keyless receipts are fine
        await insert_receipt(conn, other, "GRN-1", "k")  # the same key in another tenant is fine
    with pytest.raises(IntegrityError):
        async with admin.begin() as conn:
            await insert_receipt(conn, org, "GRN-4", "k")
    with pytest.raises(IntegrityError):
        async with admin.begin() as conn:
            await insert_receipt(conn, org, "GRN-1", None)  # a number is unique per organization


async def test_an_empty_database_migrates_up_and_down(admin) -> None:
    assert alembic("upgrade", "head").returncode == 0
    assert alembic("downgrade", BEFORE).returncode == 0
    assert alembic("upgrade", "head").returncode == 0


async def test_downgrade_drops_the_new_shape_and_keeps_the_old_data(admin) -> None:
    org = uuid.uuid4()
    async with admin.begin() as conn:
        ids = await seed_old_shape(conn, org)
    assert alembic("upgrade", "head").returncode == 0
    down = alembic("downgrade", BEFORE)
    assert down.returncode == 0, down.stderr[-2000:]
    async with admin.begin() as conn:
        for table in NEW_TABLES:
            exists = (
                await conn.execute(text("SELECT to_regclass(:t)"), {"t": f"{SCHEMA}.{table}"})
            ).scalar()
            assert exists is None, table
        columns = {
            r[0]
            for r in (
                await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns WHERE "
                        "table_schema = :s AND table_name = 'purchase_order_lines'"
                    ),
                    {"s": SCHEMA},
                )
            ).all()
        }
        assert "received_milli" not in columns and "short_closed" not in columns
        kept = (
            await conn.execute(
                text(f"SELECT received_qty FROM {SCHEMA}.purchase_order_lines WHERE id = :id"),
                {"id": ids["line_a"]},
            )
        ).scalar_one()
        assert kept == 3
    assert alembic("upgrade", "head").returncode == 0
    async with admin.begin() as conn:
        assert (
            await conn.execute(
                text(f"SELECT received_milli FROM {SCHEMA}.purchase_order_lines WHERE id = :id"),
                {"id": ids["line_a"]},
            )
        ).scalar_one() == 3_000
