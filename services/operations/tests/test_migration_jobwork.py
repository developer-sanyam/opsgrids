# ruff: noqa: S608 — every f-string here interpolates only the SCHEMA constant; values are bound.
"""`d7b3e5a19f62` (job work), run for real with alembic: downgrade to the revision before it,
upgrade, and check what the migration promises — four tables, each with FORCE row-level security
and a tenant policy, a trigger that freezes an issued challan, no foreign key out of the four
tables to anything but each other, the app role unable to rewrite a receipt — then that the
downgrade removes all of it, including with rows present, and the upgrade can be applied again.
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.models import SCHEMA

pytestmark = pytest.mark.integration

SERVICE_DIR = Path(__file__).resolve().parent.parent
REVISION = "d7b3e5a19f62"  # pragma: allowlist secret
# The revision this one sits on, derived so re-parenting never touches this file.
BEFORE = f"{REVISION}-1"
TABLES = ("job_work_orders", "job_work_materials", "job_work_receipts", "challans")


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
    try:
        yield engine
    finally:
        up = alembic("upgrade", "head")
        await engine.dispose()
        assert up.returncode == 0, up.stderr[-2000:]


async def exists(conn, name: str) -> bool:  # type: ignore[no-untyped-def]
    return bool(
        (await conn.execute(text("SELECT to_regclass(:t)"), {"t": f"{SCHEMA}.{name}"})).scalar()
    )


async def seed_one_of_each(conn, org: uuid.UUID) -> dict[str, uuid.UUID]:  # type: ignore[no-untyped-def]
    order, material, challan = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await conn.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.job_work_orders
                (id, organization_id, number, supplier_id, process, status, qc_required, notes,
                 version, transition_keys)
            VALUES (:o, :org, 'JW-2026-0001', :sup, 'Dyeing', 'ISSUED', false, '', 1, '{{}}')
            """
        ),
        {"o": order, "org": org, "sup": uuid.uuid4()},
    )
    await conn.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.job_work_materials
                (id, organization_id, order_id, line_no, item_id, location_id, sku, uom,
                 unit_value_minor, currency, quantity_milli, sent_milli, received_milli,
                 rejected_milli, shortage_milli)
            VALUES (:m, :org, :o, 1, :i, :l, 'FAB', 'm', 40000, 'INR', 82500, 82500, 0, 0, 0)
            """
        ),
        {"m": material, "org": org, "o": order, "i": uuid.uuid4(), "l": uuid.uuid4()},
    )
    await conn.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.challans
                (id, organization_id, order_id, challan_number, kind, supplier_id, supplier_name,
                 process, lines, total_value_minor, currency, eway_status)
            VALUES (:c, :org, :o, 'DC/26-27/0001', 'DELIVERY', :sup, 'Dyer', 'Dyeing', '[]'::jsonb,
                    33000, 'INR', 'NOT_REQUIRED')
            """
        ),
        {"c": challan, "org": org, "o": order, "sup": uuid.uuid4()},
    )
    return {"order": order, "material": material, "challan": challan}


async def test_upgrade_creates_four_tenant_isolated_tables(admin) -> None:
    down = alembic("downgrade", BEFORE)
    assert down.returncode == 0, down.stderr[-2000:]
    async with admin.connect() as conn:
        for table in TABLES:
            assert not await exists(conn, table), f"{table} survived the downgrade"
        # the trigger function goes with it
        fn = await conn.execute(
            text(
                "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = :s AND p.proname = 'challans_immutable'"
            ),
            {"s": SCHEMA},
        )
        assert fn.scalar_one() == 0

    up = alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]
    async with admin.connect() as conn:
        for table in TABLES:
            assert await exists(conn, table)
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
            assert tuple(rls) == (True, True), f"{table}: RLS must be enabled AND forced"
            policy = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM pg_policies WHERE schemaname = :s AND tablename = :t "
                        "AND policyname = 'tenant_isolation'"
                    ),
                    {"s": SCHEMA, "t": table},
                )
            ).scalar_one()
            assert policy == 1, f"{table} has no tenant_isolation policy"
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
            assert org_col == 1, f"{table} must carry a NOT NULL organization_id"


async def test_no_foreign_key_leaves_the_four_tables(admin) -> None:
    async with admin.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT cn.nspname || '.' || c.relname AS child,
                           pn.nspname || '.' || p.relname AS parent
                    FROM pg_constraint k
                    JOIN pg_class c ON c.oid = k.conrelid
                    JOIN pg_namespace cn ON cn.oid = c.relnamespace
                    JOIN pg_class p ON p.oid = k.confrelid
                    JOIN pg_namespace pn ON pn.oid = p.relnamespace
                    WHERE k.contype = 'f'
                      AND cn.nspname = 'operations'
                      AND c.relname IN (
                          'job_work_orders', 'job_work_materials',
                          'job_work_receipts', 'challans')
                    """
                )
            )
        ).all()
    own = {f"{SCHEMA}.{t}" for t in TABLES}
    assert rows, "expected the intra-module foreign keys"
    for child, parent in rows:
        assert parent in own, f"{child} has a foreign key to {parent}"
    # and in particular none to items, locations, suppliers, lots or rolls
    parents = {parent for _child, parent in rows}
    assert not parents & {
        f"{SCHEMA}.{t}" for t in ("inventory_items", "locations", "suppliers", "warehouses")
    }


async def test_the_issued_challan_is_frozen_below_the_application(admin) -> None:
    org = uuid.uuid4()
    async with admin.begin() as conn:
        ids = await seed_one_of_each(conn, org)
    for change in ("challan_number = 'X'", "total_value_minor = 1", "lines = '[{}]'::jsonb"):
        async with admin.connect() as conn:
            with pytest.raises(Exception, match="immutable"):
                await conn.execute(
                    text(f"UPDATE {SCHEMA}.challans SET {change} WHERE id = :c"),
                    {"c": ids["challan"]},
                )
    async with admin.begin() as conn:
        await conn.execute(
            text(
                f"UPDATE {SCHEMA}.challans SET eway_status = 'REQUESTED', distance_km = 90 "
                "WHERE id = :c"
            ),
            {"c": ids["challan"]},
        )
    async with admin.connect() as conn:
        with pytest.raises(Exception, match="cannot be deleted"):
            await conn.execute(
                text(f"DELETE FROM {SCHEMA}.challans WHERE id = :c"), {"c": ids["challan"]}
            )


async def test_check_constraints_hold_the_reconciliation_identity(admin) -> None:
    org = uuid.uuid4()
    async with admin.begin() as conn:
        ids = await seed_one_of_each(conn, org)
    async with admin.connect() as conn:
        with pytest.raises(Exception, match="accounted_within_sent"):
            await conn.execute(
                text(
                    f"UPDATE {SCHEMA}.job_work_materials SET received_milli = 82500, "
                    "shortage_milli = 1 WHERE id = :m"
                ),
                {"m": ids["material"]},
            )
    async with admin.begin() as conn:  # exactly equal is fine
        await conn.execute(
            text(
                f"UPDATE {SCHEMA}.job_work_materials SET received_milli = 70000, "
                "rejected_milli = 10000, shortage_milli = 2500 WHERE id = :m"
            ),
            {"m": ids["material"]},
        )


async def test_the_app_role_cannot_rewrite_a_receipt(admin) -> None:
    app_engine = create_async_engine(
        settings().app_dsn, connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}}
    )
    try:
        async with app_engine.connect() as conn:
            for statement in (
                f"UPDATE {SCHEMA}.job_work_receipts SET received_milli = 1",
                f"DELETE FROM {SCHEMA}.job_work_receipts",
            ):
                with pytest.raises(Exception, match="permission denied"):
                    await conn.execute(text(statement))
                await conn.rollback()
    finally:
        await app_engine.dispose()


async def test_downgrade_with_data_present_then_upgrade_again(admin) -> None:
    org = uuid.uuid4()
    async with admin.begin() as conn:
        await seed_one_of_each(conn, org)
    down = alembic("downgrade", BEFORE)
    assert down.returncode == 0, down.stderr[-2000:]
    async with admin.connect() as conn:
        for table in TABLES:
            assert not await exists(conn, table)
    up = alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]
    async with admin.connect() as conn:
        count = (
            await conn.execute(text(f"SELECT count(*) FROM {SCHEMA}.job_work_orders"))
        ).scalar_one()
        assert count == 0  # job work is new in this revision: nothing to preserve on the way down
    check = alembic("check")
    assert check.returncode == 0, (check.stdout + check.stderr)[-2000:]
    # Back at head (later revisions sit on top of this one); the job_work_orders count above
    # proves this revision's tables were re-created.
    head = alembic("heads").stdout.split()[0]
    current = alembic("current")
    assert head in current.stdout + current.stderr
