# ruff: noqa: S608 — every f-string here interpolates only the SCHEMA constant; values are bound.
"""`d9a4c6e1b3f7` (item / lot / roll barcodes and `inventory_import_runs`), run for real.

Drives alembic in a subprocess down to the revision right before it with stock in place, up to
head, and checks: existing rows keep their stock and get a `NULL` barcode, the barcode indexes
are unique per organization (items ignoring soft-deleted rows), the new table carries RLS and
refuses `UPDATE`/`DELETE` for the app role, and the downgrade drops everything it added without
touching a single ledger row. Always leaves the schema at head.
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.models import SCHEMA

pytestmark = pytest.mark.integration

SERVICE_DIR = Path(__file__).resolve().parent.parent
BEFORE = "c5a82f6d3b19"  # pragma: allowlist secret
THIS = "d9a4c6e1b3f7"  # pragma: allowlist secret
#: The barcode unique indexes are built CONCURRENTLY here, not in THIS.
FINISH = "e1b6d4a8c273"  # pragma: allowlist secret
TRUNCATE = (
    "inventory_import_runs",
    "roll_events",
    "lot_genealogy",
    "inventory_discrepancies",
    "inventory_reservations",
    "inventory_balances",
    "inventory_ledger",
    "inventory_moves",
    "rolls",
    "lots",
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


async def columns(conn, table: str) -> set[str]:  # type: ignore[no-untyped-def]
    rows = await conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = :s AND table_name = :t"
        ),
        {"s": SCHEMA, "t": table},
    )
    return {r[0] for r in rows}


async def indexes(conn) -> set[str]:  # type: ignore[no-untyped-def]
    rows = await conn.execute(
        text("SELECT indexname FROM pg_indexes WHERE schemaname = :s"), {"s": SCHEMA}
    )
    return {r[0] for r in rows}


async def seed_item(conn, org: uuid.UUID, sku: str) -> dict[str, uuid.UUID]:  # type: ignore[no-untyped-def]
    ids = {k: uuid.uuid4() for k in ("wh", "loc", "item", "move")}
    p = {**ids, "org": org, "sku": sku, "code": f"WH-{sku}"}
    await conn.execute(
        text(
            f"INSERT INTO {SCHEMA}.warehouses (id, organization_id, code, name, location, "
            "created_at, updated_at) VALUES (:wh, :org, :code, :code, 'Pune', now(), now())"
        ),
        p,
    )
    await conn.execute(
        text(
            f"INSERT INTO {SCHEMA}.locations (id, organization_id, warehouse_id, kind, code, "
            "name, is_default, created_at, updated_at) "
            "VALUES (:loc, :org, :wh, 'bin', 'DEFAULT', '', true, now(), now())"
        ),
        p,
    )
    await conn.execute(
        text(
            f"INSERT INTO {SCHEMA}.inventory_items (id, organization_id, sku, name, category, "
            "warehouse, home_location_id, reorder_point, uom, unit_value_minor, currency, "
            "stocked_at, hsn_code, track_lot, track_roll, track_expiry, track_quality, is_active, "
            "created_at, updated_at) VALUES (:item, :org, :sku, 'Fabric', 'raw_materials', :code, "
            ":loc, 0, 'm', 0, 'INR', '2026-09-01', '', false, false, false, false, true, now(), "
            "now())"
        ),
        p,
    )
    await conn.execute(
        text(
            f"INSERT INTO {SCHEMA}.inventory_moves (id, organization_id, kind, note, occurred_on, "
            "created_at) VALUES (:move, :org, 'opening', '', '2026-09-01', now())"
        ),
        p,
    )
    await conn.execute(
        text(
            f"INSERT INTO {SCHEMA}.inventory_ledger (id, organization_id, move_id, "
            "inventory_item_id, location_id, status, quantity_milli, balance_after_milli, "
            "created_at) VALUES (gen_random_uuid(), :org, :move, :item, :loc, 'AVAILABLE', "
            "82500, 82500, now())"
        ),
        p,
    )
    await conn.execute(
        text(
            f"INSERT INTO {SCHEMA}.inventory_balances (id, organization_id, inventory_item_id, "
            "location_id, status, quantity_milli, reserved_milli, updated_at) VALUES "
            "(gen_random_uuid(), :org, :item, :loc, 'AVAILABLE', 82500, 0, now())"
        ),
        p,
    )
    return ids


async def ledger_total(conn, org: uuid.UUID) -> tuple[int, int]:  # type: ignore[no-untyped-def]
    row = (
        await conn.execute(
            text(
                f"SELECT count(*), COALESCE(sum(quantity_milli), 0) FROM {SCHEMA}.inventory_ledger "
                "WHERE organization_id = :o"
            ),
            {"o": org},
        )
    ).one()
    return (row[0], int(row[1]))


async def test_the_schema_before_this_revision_has_none_of_it(admin) -> None:
    async with admin.begin() as conn:
        for table in ("inventory_items", "lots", "rolls"):
            assert "barcode" not in await columns(conn, table)
        assert not await columns(conn, "inventory_import_runs")


async def test_up_keeps_every_row_and_stock_and_down_removes_only_what_it_added(admin) -> None:
    org = uuid.uuid4()
    async with admin.begin() as conn:
        await seed_item(conn, org, "FAB-1")
        before = await ledger_total(conn, org)

    up = alembic("upgrade", FINISH)
    assert up.returncode == 0, up.stderr[-2000:]
    async with admin.begin() as conn:
        assert await ledger_total(conn, org) == before
        for table in ("inventory_items", "lots", "rolls"):
            assert "barcode" in await columns(conn, table)
        assert (
            await conn.execute(
                text(f"SELECT barcode FROM {SCHEMA}.inventory_items WHERE organization_id = :o"),
                {"o": org},
            )
        ).scalar_one() is None
        names = await indexes(conn)
        assert {
            "uq_inventory_items_org_barcode",
            "uq_lots_org_barcode",
            "uq_rolls_org_barcode",
            "uq_inventory_import_runs_org_key",
        } <= names

    down = alembic("downgrade", BEFORE)
    assert down.returncode == 0, down.stderr[-2000:]
    async with admin.begin() as conn:
        assert await ledger_total(conn, org) == before
        for table in ("inventory_items", "lots", "rolls"):
            assert "barcode" not in await columns(conn, table)
        assert not await columns(conn, "inventory_import_runs")
        added = {
            n
            for n in await indexes(conn)
            if ("barcode" in n and "locations" not in n) or "import_runs" in n
        }
        assert not added

    again = alembic("upgrade", "head")
    assert again.returncode == 0, again.stderr[-2000:]


async def test_barcodes_are_unique_per_organization_and_items_ignore_deleted(admin) -> None:
    up = alembic("upgrade", FINISH)
    assert up.returncode == 0, up.stderr[-2000:]
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    async with admin.begin() as conn:
        a = await seed_item(conn, org_a, "FAB-1")
        b = await seed_item(conn, org_b, "FAB-1")
        await conn.execute(
            text(f"UPDATE {SCHEMA}.inventory_items SET barcode = 'EAN-1' WHERE id = :i"),
            {"i": a["item"]},
        )
        # Another organization may use the same code.
        await conn.execute(
            text(f"UPDATE {SCHEMA}.inventory_items SET barcode = 'EAN-1' WHERE id = :i"),
            {"i": b["item"]},
        )
    # Same organization may not.
    with pytest.raises(IntegrityError):
        async with admin.begin() as conn:
            c = await seed_item(conn, org_a, "FAB-2")
            await conn.execute(
                text(f"UPDATE {SCHEMA}.inventory_items SET barcode = 'EAN-1' WHERE id = :i"),
                {"i": c["item"]},
            )
    # A soft-deleted item frees its code.
    async with admin.begin() as conn:
        await conn.execute(
            text(f"UPDATE {SCHEMA}.inventory_items SET deleted_at = now() WHERE id = :i"),
            {"i": a["item"]},
        )
        d = await seed_item(conn, org_a, "FAB-3")
        await conn.execute(
            text(f"UPDATE {SCHEMA}.inventory_items SET barcode = 'EAN-1' WHERE id = :i"),
            {"i": d["item"]},
        )

    # Lots and rolls: unique per organization where set.
    async with admin.begin() as conn:
        for table, no_col, extra_cols, extra_vals in (
            ("lots", "lot_no", "quality_status", "'PENDING'"),
            ("rolls", "roll_no", "state, primary_milli", "'ACTIVE', 1000"),
        ):
            params = {"item": b["item"], "org": org_b}
            await conn.execute(
                text(
                    f"INSERT INTO {SCHEMA}.{table} (id, organization_id, inventory_item_id, "
                    f"{no_col}, barcode, {extra_cols}) VALUES (gen_random_uuid(), :org, :item, "
                    f"'N-1', 'SUP-1', {extra_vals})"
                ),
                params,
            )
    for table, no_col, extra_cols, extra_vals in (
        ("lots", "lot_no", "quality_status", "'PENDING'"),
        ("rolls", "roll_no", "state, primary_milli", "'ACTIVE', 1000"),
    ):
        with pytest.raises(IntegrityError):
            async with admin.begin() as conn:
                await conn.execute(
                    text(
                        f"INSERT INTO {SCHEMA}.{table} (id, organization_id, inventory_item_id, "
                        f"{no_col}, barcode, {extra_cols}) VALUES (gen_random_uuid(), :org, "
                        f":item, 'N-2', 'SUP-1', {extra_vals})"
                    ),
                    {"item": b["item"], "org": org_b},
                )


async def test_import_runs_carry_rls_and_are_append_only(admin) -> None:
    up = alembic("upgrade", THIS)
    assert up.returncode == 0, up.stderr[-2000:]
    app_role = settings().db_app_role
    async with admin.begin() as conn:
        rls = (
            await conn.execute(
                text(
                    "SELECT c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = :s AND c.relname = 'inventory_import_runs'"
                ),
                {"s": SCHEMA},
            )
        ).one()
        assert tuple(rls) == (True, True)
        assert (
            await conn.execute(
                text(
                    "SELECT count(*) FROM pg_policies WHERE schemaname = :s "
                    "AND tablename = 'inventory_import_runs' AND policyname = 'tenant_isolation'"
                ),
                {"s": SCHEMA},
            )
        ).scalar_one() == 1
        for privilege, expected in (
            ("SELECT", True),
            ("INSERT", True),
            ("UPDATE", False),
            ("DELETE", False),
        ):
            held = (
                await conn.execute(
                    text("SELECT has_table_privilege(:r, :t, :p)"),
                    {"r": app_role, "t": f"{SCHEMA}.inventory_import_runs", "p": privilege},
                )
            ).scalar_one()
            assert held is expected, privilege

    # And the revoke survives a grant re-application: upgrading to head re-runs env.py's grants.
    head = alembic("upgrade", "head")
    assert head.returncode == 0, head.stderr[-2000:]
    async with admin.begin() as conn:
        assert (
            await conn.execute(
                text("SELECT has_table_privilege(:r, :t, 'UPDATE')"),
                {"r": app_role, "t": f"{SCHEMA}.inventory_import_runs"},
            )
        ).scalar_one() is False


async def test_the_app_role_cannot_rewrite_a_run(admin) -> None:
    up = alembic("upgrade", THIS)
    assert up.returncode == 0, up.stderr[-2000:]
    org = uuid.uuid4()
    app_engine = create_async_engine(
        settings().app_dsn, connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}}
    )
    try:
        async with app_engine.begin() as conn:
            await conn.execute(text(f"SELECT set_config('app.organization_id', '{org}', true)"))
            await conn.execute(
                text(
                    f"INSERT INTO {SCHEMA}.inventory_import_runs (id, organization_id, "
                    "idempotency_key, request_hash, entity, on_duplicate, row_count, "
                    "created_count, skipped_count, summary) VALUES (gen_random_uuid(), :o, 'k', "
                    "'h', 'items', 'fail', 1, 1, 0, '{}'::jsonb)"
                ),
                {"o": org},
            )
        with pytest.raises(ProgrammingError):
            async with app_engine.begin() as conn:
                await conn.execute(text(f"SELECT set_config('app.organization_id', '{org}', true)"))
                await conn.execute(text(f"UPDATE {SCHEMA}.inventory_import_runs SET row_count = 2"))
    finally:
        await app_engine.dispose()


async def test_the_downgrade_refuses_to_forget_barcodes_people_typed(admin) -> None:
    up = alembic("upgrade", FINISH)
    assert up.returncode == 0, up.stderr[-2000:]
    async with admin.begin() as conn:
        ids = await seed_item(conn, uuid.uuid4(), "FAB-9")
        await conn.execute(
            text(f"UPDATE {SCHEMA}.inventory_items SET barcode = 'EAN-9' WHERE id = :i"),
            {"i": ids["item"]},
        )
    down = alembic("downgrade", BEFORE)
    assert down.returncode != 0 and "Refusing to downgrade" in down.stderr
    async with admin.begin() as conn:
        assert "barcode" in await columns(conn, "inventory_items")
        await conn.execute(
            text(f"UPDATE {SCHEMA}.inventory_items SET barcode = NULL WHERE id = :i"),
            {"i": ids["item"]},
        )
