# ruff: noqa: S608 — every f-string here interpolates only the SCHEMA constant; values are bound.
"""`b7d41e92c05a` (lots, rolls, roll_events, lot_genealogy and the bucket foreign keys), run for
real against stock that already names lots and rolls the way Phase 0's kernel allowed.

Drives alembic in a subprocess down to the revision right before this one, seeds buckets whose
`lot_id` / `roll_id` point at nothing (there was no table to point at), up to head, and checks:
every orphan id got a `LEGACY-…` placeholder in its own organization, no quantity and no ledger
row changed, the reconciler is clean, the new foreign keys are enforced and organization-scoped,
the new tables carry RLS, and the two append-only tables really refuse `UPDATE`. Then back down —
the ledger keeps its ids as plain UUIDs — and up again. Always leaves the schema at head.
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import settings
from app.inventory.reconcile import reconcile_organization
from app.models import SCHEMA

pytestmark = pytest.mark.integration

SERVICE_DIR = Path(__file__).resolve().parent.parent
BEFORE = "665e44e243d7"  # alembic revision id  # pragma: allowlist secret
NEW_TABLES = ("lots", "rolls", "roll_events", "lot_genealogy")
TRUNCATE = (
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


async def seed_old_shape(conn, org: uuid.UUID) -> dict[str, uuid.UUID]:  # type: ignore[no-untyped-def]
    """One item with stock in four buckets: a lot and a roll that have no rows, the same lot with
    no roll, a second roll, and a bucket naming neither."""
    ids = {
        name: uuid.uuid4() for name in ("wh", "loc", "item", "move", "lot", "roll", "roll2", "run")
    }
    await conn.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.warehouses (id, organization_id, code, name, location,
                                             created_at, updated_at)
            VALUES (:wh, :org, 'WH-MIG', 'Migration Plant', 'Pune', now(), now())
            """
        ),
        {**ids, "org": org},
    )
    await conn.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.locations (id, organization_id, warehouse_id, kind, code, name,
                                            is_default, created_at, updated_at)
            VALUES (:loc, :org, :wh, 'bin', 'DEFAULT', '', true, now(), now())
            """
        ),
        {**ids, "org": org},
    )
    await conn.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.inventory_items
                (id, organization_id, sku, name, category, warehouse, home_location_id,
                 reorder_point, uom, unit_value_minor, currency, stocked_at, hsn_code, track_lot,
                 track_roll, track_expiry, track_quality, is_active, created_at, updated_at)
            VALUES (:item, :org, 'FAB-1', 'Fabric', 'raw_materials', 'Migration Plant', :loc,
                    0, 'm', 0, 'INR', '2026-09-01', '', false, false, false, false, true, now(),
                    now())
            """
        ),
        {**ids, "org": org},
    )
    await conn.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.inventory_moves (id, organization_id, kind, note, occurred_on,
                                                  created_at)
            VALUES (:move, :org, 'opening', '', '2026-09-01', now())
            """
        ),
        {**ids, "org": org},
    )
    buckets = [
        (ids["lot"], ids["roll"], 82_500),
        (ids["lot"], None, 10_000),
        (None, ids["roll2"], 40_000),
        (None, None, 5_000),
    ]
    for lot_id, roll_id, milli in buckets:
        params = {
            **ids,
            "org": org,
            "lot_id": lot_id,
            "roll_id": roll_id,
            "milli": milli,
            "ledger_id": uuid.uuid4(),
            "balance_id": uuid.uuid4(),
        }
        await conn.execute(
            text(
                f"""
                INSERT INTO {SCHEMA}.inventory_ledger
                    (id, organization_id, move_id, inventory_item_id, location_id, status, lot_id,
                     roll_id, quantity_milli, balance_after_milli, created_at)
                VALUES (:ledger_id, :org, :move, :item, :loc, 'AVAILABLE', :lot_id, :roll_id,
                        :milli, :milli, now())
                """
            ),
            params,
        )
        await conn.execute(
            text(
                f"""
                INSERT INTO {SCHEMA}.inventory_balances
                    (id, organization_id, inventory_item_id, location_id, status, lot_id, roll_id,
                     quantity_milli, reserved_milli, updated_at)
                VALUES (:balance_id, :org, :item, :loc, 'AVAILABLE', :lot_id, :roll_id, :milli, 0,
                        now())
                """
            ),
            params,
        )
    # A stale discrepancy that names the same lot and roll.
    await conn.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.inventory_discrepancies
                (id, organization_id, kind, inventory_item_id, location_id, status, lot_id, roll_id,
                 stored_milli, expected_milli, run_id, detected_at, last_seen_at, resolution_note)
            VALUES (:run, :org, 'balance', :item, :loc, 'AVAILABLE', :lot, :roll, 1, 2, :run, now(),
                    now(), '')
            """
        ),
        {**ids, "org": org},
    )
    return ids


async def counts(conn, org: uuid.UUID) -> dict[str, int]:  # type: ignore[no-untyped-def]
    out = {}
    for table in ("inventory_ledger", "inventory_balances", "inventory_discrepancies"):
        out[table] = (
            await conn.execute(
                text(f"SELECT count(*) FROM {SCHEMA}.{table} WHERE organization_id = :o"),
                {"o": org},
            )
        ).scalar_one()
    out["total_milli"] = (
        await conn.execute(
            text(
                f"SELECT COALESCE(SUM(quantity_milli), 0) FROM {SCHEMA}.inventory_ledger "
                "WHERE organization_id = :o"
            ),
            {"o": org},
        )
    ).scalar_one()
    return out


async def test_orphan_ids_get_placeholders_and_nothing_else_changes(admin) -> None:
    org = uuid.uuid4()
    async with admin.begin() as conn:
        ids = await seed_old_shape(conn, org)
        before = await counts(conn, org)

    up = alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]

    async with admin.begin() as conn:
        assert await counts(conn, org) == before  # not a row, not a milli-unit moved

        lot = (
            await conn.execute(
                text(
                    f"SELECT lot_no, quality_status, inventory_item_id FROM {SCHEMA}.lots "
                    "WHERE id = :id AND organization_id = :o"
                ),
                {"id": ids["lot"], "o": org},
            )
        ).one()
        assert lot.lot_no.startswith("LEGACY-") and lot.inventory_item_id == ids["item"]
        rolls = {
            r.id: r
            for r in (
                await conn.execute(
                    text(
                        f"SELECT id, roll_no, primary_milli, lot_id, state FROM {SCHEMA}.rolls "
                        "WHERE organization_id = :o"
                    ),
                    {"o": org},
                )
            ).all()
        }
        assert set(rolls) == {ids["roll"], ids["roll2"]}
        # The placeholder's length is what was ever received into it.
        assert (rolls[ids["roll"]].primary_milli, rolls[ids["roll2"]].primary_milli) == (
            82_500,
            40_000,
        )
        assert all(r.state == "ACTIVE" and r.roll_no.startswith("LEGACY-") for r in rolls.values())

        async with AsyncSession(bind=conn) as session:
            report = await reconcile_organization(session, org)
        assert report.clean, report.findings

    # The new keys are real: a bucket cannot name a lot that does not exist, or another tenant's.
    other_org = uuid.uuid4()
    other_lot = uuid.uuid4()
    async with admin.begin() as conn:
        await conn.execute(
            text(
                f"""
                INSERT INTO {SCHEMA}.lots (id, organization_id, inventory_item_id, lot_no,
                                           quality_status)
                SELECT :lot, :other, i.id, 'THEIRS', 'APPROVED'
                  FROM {SCHEMA}.inventory_items i WHERE i.organization_id = :o LIMIT 1
                """
            ),
            {"lot": other_lot, "other": other_org, "o": org},
        )
    for bad_lot in (uuid.uuid4(), other_lot):
        with pytest.raises(IntegrityError):
            async with admin.begin() as conn:
                await conn.execute(
                    text(
                        f"""
                        INSERT INTO {SCHEMA}.inventory_balances
                            (id, organization_id, inventory_item_id, location_id, status, lot_id,
                             quantity_milli, reserved_milli, updated_at)
                        VALUES (gen_random_uuid(), :org, :item, :loc, 'QUALITY_HOLD', :lot, 0, 0,
                                now())
                        """
                    ),
                    {"org": org, "item": ids["item"], "loc": ids["loc"], "lot": bad_lot},
                )


async def test_new_tables_carry_rls_and_the_log_tables_are_append_only(admin) -> None:
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

        for table in ("roll_events", "lot_genealogy"):
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
        for table in ("lots", "rolls"):
            assert (
                await conn.execute(
                    text("SELECT has_table_privilege(:r, :t, 'UPDATE')"),
                    {"r": app_role, "t": f"{SCHEMA}.{table}"},
                )
            ).scalar_one()


async def test_downgrade_keeps_the_ledger_and_the_upgrade_can_be_repeated(admin) -> None:
    org = uuid.uuid4()
    async with admin.begin() as conn:
        ids = await seed_old_shape(conn, org)
        before = await counts(conn, org)

    assert alembic("upgrade", "head").returncode == 0
    down = alembic("downgrade", BEFORE)
    assert down.returncode == 0, down.stderr[-2000:]

    async with admin.begin() as conn:
        for table in NEW_TABLES:
            exists = (
                await conn.execute(text("SELECT to_regclass(:t)"), {"t": f"{SCHEMA}.{table}"})
            ).scalar()
            assert exists is None, table
        assert await counts(conn, org) == before
        # The ids are still on the ledger — they are plain UUIDs again.
        assert (
            await conn.execute(
                text(
                    f"SELECT count(*) FROM {SCHEMA}.inventory_ledger "
                    "WHERE organization_id = :o AND roll_id = :r"
                ),
                {"o": org, "r": ids["roll"]},
            )
        ).scalar_one() == 1

    again = alembic("upgrade", "head")
    assert again.returncode == 0, again.stderr[-2000:]
    async with admin.begin() as conn:
        assert (
            await conn.execute(
                text(f"SELECT count(*) FROM {SCHEMA}.rolls WHERE organization_id = :o"), {"o": org}
            )
        ).scalar_one() == 2
        assert await counts(conn, org) == before


async def test_an_empty_database_migrates_up_and_down(admin) -> None:
    assert alembic("upgrade", "head").returncode == 0
    assert alembic("downgrade", BEFORE).returncode == 0
    assert alembic("upgrade", "head").returncode == 0
