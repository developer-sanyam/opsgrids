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

import asyncio
import os
import subprocess
import sys
import time
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
#: Creates the tables; *its* downgrade is the destructive one.
TABLES_REV = "b7d41e92c05a"  # pragma: allowlist secret
ALLOW = {"OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE": "1"}
NEW_TABLES = ("lots", "rolls", "roll_events", "lot_genealogy")
TRUNCATE = (
    "count_lines",
    "count_sessions",
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


def alembic(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  (fixed argv, sys.executable, no shell)
        [sys.executable, "-m", "alembic", *args],
        cwd=SERVICE_DIR,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **env} if env else None,
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


async def current_revision(conn) -> str:  # type: ignore[no-untyped-def]
    row = await conn.execute(text(f"SELECT version_num FROM {SCHEMA}.alembic_version"))
    return str(row.scalar_one())


FK_NAME = "'^fk_inventory_(ledger|balances|discrepancies)_org_(lot|roll)$'"


async def test_foreign_keys_are_validated_and_the_big_indexes_are_valid(admin) -> None:
    """Revision 1 adds the keys NOT VALID; revision 2 validates them and builds the indexes
    CONCURRENTLY — afterwards nothing may be left unvalidated or invalid."""
    assert alembic("upgrade", "head").returncode == 0
    async with admin.begin() as conn:
        keys = (
            await conn.execute(
                text(
                    "SELECT conname, convalidated FROM pg_constraint c "
                    "JOIN pg_namespace n ON n.oid = c.connamespace "
                    f"WHERE n.nspname = :s AND conname ~ {FK_NAME}"
                ),
                {"s": SCHEMA},
            )
        ).all()
        assert len(keys) == 6 and all(valid for _, valid in keys), keys
        indexes = (
            await conn.execute(
                text(
                    "SELECT c.relname, i.indisvalid FROM pg_index i "
                    "JOIN pg_class c ON c.oid = i.indexrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = :s "
                    "AND c.relname ~ '^ix_inventory_(ledger|balances|moves)_org_"
                    "(created|location_created|lot_created|roll_created|location|lot|roll|occurred)$'"
                ),
                {"s": SCHEMA},
            )
        ).all()
        assert len(indexes) == 8 and all(valid for _, valid in indexes), indexes


async def test_the_first_revision_leaves_the_keys_not_valid_for_the_second_to_validate(
    admin,
) -> None:
    assert alembic("upgrade", TABLES_REV).returncode == 0
    async with admin.begin() as conn:
        assert await current_revision(conn) == TABLES_REV
        valid = (
            await conn.execute(
                text(
                    "SELECT bool_or(convalidated) FROM pg_constraint c "
                    "JOIN pg_namespace n ON n.oid = c.connamespace "
                    f"WHERE n.nspname = :s AND conname ~ {FK_NAME}"
                ),
                {"s": SCHEMA},
            )
        ).scalar_one()
        assert valid is False
    assert alembic("upgrade", "head").returncode == 0


async def test_an_interrupted_second_revision_resumes_and_rebuilds_an_invalid_index(
    admin,
) -> None:
    # What an interrupted CREATE INDEX CONCURRENTLY leaves behind: an INVALID index of that name.
    name = f"{SCHEMA}.ix_inventory_balances_org_lot"
    async with admin.begin() as conn:
        await seed_old_shape(conn, uuid.uuid4())  # four balances sharing one organization_id
    assert alembic("upgrade", TABLES_REV).returncode == 0
    async with admin.connect() as conn:
        auto = await conn.execution_options(isolation_level="AUTOCOMMIT")
        # A unique build over duplicate values fails half-way and leaves the index INVALID.
        with pytest.raises(Exception, match="could not create unique index"):
            await auto.execute(
                text(
                    f"CREATE UNIQUE INDEX CONCURRENTLY ix_inventory_balances_org_lot "
                    f"ON {SCHEMA}.inventory_balances (organization_id)"
                )
            )
    async with admin.begin() as conn:
        broken = await conn.execute(
            text(f"SELECT NOT indisvalid FROM pg_index WHERE indexrelid = '{name}'::regclass")
        )
        assert broken.scalar_one() is True
    assert alembic("upgrade", "head").returncode == 0
    async with admin.begin() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT i.indisvalid, pg_get_indexdef(i.indexrelid) FROM pg_index i "
                    f"WHERE i.indexrelid = '{name}'::regclass"
                )
            )
        ).one()
        assert row[0] is True and "lot_id" in row[1]


async def test_the_upgrade_gives_up_after_five_seconds_instead_of_queueing_behind_a_lock(
    admin,
) -> None:
    async with admin.connect() as holder:
        await holder.execute(text(f"LOCK TABLE {SCHEMA}.inventory_ledger IN ACCESS EXCLUSIVE MODE"))
        started = time.monotonic()
        blocked = await asyncio.to_thread(alembic, "upgrade", "head")
        elapsed = time.monotonic() - started
        assert blocked.returncode != 0
        assert "lock timeout" in blocked.stderr.lower(), blocked.stderr[-1500:]
        assert 3 < elapsed < 60, elapsed
        await holder.rollback()
    assert alembic("upgrade", "head").returncode == 0


async def _one_item(conn, org: uuid.UUID) -> uuid.UUID:  # type: ignore[no-untyped-def]
    row = await conn.execute(
        text(f"SELECT id FROM {SCHEMA}.inventory_items WHERE organization_id = :o LIMIT 1"),
        {"o": org},
    )
    return uuid.UUID(str(row.scalar_one()))


async def test_downgrade_refuses_while_real_lots_exist_unless_explicitly_allowed(admin) -> None:
    org = uuid.uuid4()
    async with admin.begin() as conn:
        await seed_old_shape(conn, org)
    assert alembic("upgrade", "head").returncode == 0
    # Only this migration's own placeholders so far: going down is allowed.
    assert alembic("downgrade", BEFORE).returncode == 0
    assert alembic("upgrade", "head").returncode == 0

    async with admin.begin() as conn:
        item = await _one_item(conn, org)
        await conn.execute(
            text(
                f"""
                INSERT INTO {SCHEMA}.lots (id, organization_id, inventory_item_id, lot_no,
                                           quality_status, created_by)
                VALUES (gen_random_uuid(), :o, :i, 'REAL-1', 'APPROVED', 'someone-real')
                """
            ),
            {"o": org, "i": item},
        )
    refused = alembic("downgrade", BEFORE)
    assert refused.returncode != 0
    assert "Refusing to downgrade" in refused.stderr
    assert "OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE" in refused.stderr
    async with admin.begin() as conn:
        assert await current_revision(conn) != BEFORE
        kept = await conn.execute(
            text(f"SELECT count(*) FROM {SCHEMA}.lots WHERE lot_no = 'REAL-1'")
        )
        assert kept.scalar_one() == 1
    allowed = alembic("downgrade", BEFORE, env=ALLOW)
    assert allowed.returncode == 0, allowed.stderr[-1500:]
    async with admin.begin() as conn:
        assert await current_revision(conn) == BEFORE
        gone = await conn.execute(text("SELECT to_regclass(:t)"), {"t": f"{SCHEMA}.lots"})
        assert gone.scalar() is None


async def test_downgrade_refuses_for_a_real_roll_even_without_a_real_lot(admin) -> None:
    org = uuid.uuid4()
    async with admin.begin() as conn:
        await seed_old_shape(conn, org)
    assert alembic("upgrade", "head").returncode == 0
    async with admin.begin() as conn:
        item = await _one_item(conn, org)
        await conn.execute(
            text(
                f"""
                INSERT INTO {SCHEMA}.rolls (id, organization_id, inventory_item_id, roll_no, state,
                                            primary_milli, created_by)
                VALUES (gen_random_uuid(), :o, :i, 'REAL-ROLL', 'ACTIVE', 1000, 'someone-real')
                """
            ),
            {"o": org, "i": item},
        )
    refused = alembic("downgrade", BEFORE)
    assert refused.returncode != 0 and "Refusing to downgrade" in refused.stderr


async def seed_second_item_sharing_ids(  # type: ignore[no-untyped-def]
    conn, org: uuid.UUID, ids: dict[str, uuid.UUID]
) -> uuid.UUID:
    """A second item whose buckets, in the same organization, name the *same* lot and roll ids as
    the first item's — plus open count lines naming them."""
    item2, session_id, ledger_id, balance_id, line_id, move2 = (uuid.uuid4() for _ in range(6))
    await conn.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.inventory_items
                (id, organization_id, sku, name, category, warehouse, home_location_id,
                 reorder_point, uom, unit_value_minor, currency, stocked_at, hsn_code, track_lot,
                 track_roll, track_expiry, track_quality, is_active, created_at, updated_at)
            VALUES (:item2, :org, 'FAB-2', 'Fabric two', 'raw_materials', 'Migration Plant', :loc,
                    0, 'm', 0, 'INR', '2026-09-01', '', false, false, false, false, true, now(),
                    now())
            """
        ),
        {"item2": item2, "org": org, "loc": ids["loc"]},
    )
    await conn.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.inventory_moves (id, organization_id, kind, note, occurred_on,
                                                  created_at)
            VALUES (:move2, :org, 'opening', '', '2026-09-01', now())
            """
        ),
        {"move2": move2, "org": org},
    )
    params = {
        "org": org,
        "move": move2,
        "item": item2,
        "loc": ids["loc"],
        "lot_id": ids["lot"],
        "roll_id": ids["roll"],
        "milli": 7_000,
        "ledger_id": ledger_id,
        "balance_id": balance_id,
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
    await conn.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.count_sessions (id, organization_id, warehouse_id, scope_kind,
                                                 status, label, created_at, updated_at)
            VALUES (:sid, :org, :wh, 'WAREHOUSE', 'ACTIVE', 'legacy', now(), now())
            """
        ),
        {"sid": session_id, "org": org, "wh": ids["wh"]},
    )
    for item_id, line in ((item2, line_id), (ids["item"], uuid.uuid4())):
        await conn.execute(
            text(
                f"""
                INSERT INTO {SCHEMA}.count_lines
                    (id, organization_id, session_id, inventory_item_id, location_id, status,
                     lot_id, roll_id, is_unlisted, expected_milli, review_status, review_note,
                     created_at, updated_at)
                VALUES (:line, :org, :sid, :item, :loc, 'QUALITY_HOLD', :lot_id, :roll_id, false,
                        0, 'NONE', '', now(), now())
                """
            ),
            {**params, "sid": session_id, "item": item_id, "line": line},
        )
    return item2


async def _ledger_per_item(conn, org: uuid.UUID) -> dict[uuid.UUID, int]:  # type: ignore[no-untyped-def]
    rows = await conn.execute(
        text(
            f"SELECT inventory_item_id, SUM(quantity_milli) FROM {SCHEMA}.inventory_ledger "
            "WHERE organization_id = :o GROUP BY 1"
        ),
        {"o": org},
    )
    return {row[0]: int(row[1]) for row in rows.all()}


async def test_ids_shared_by_two_items_get_a_placeholder_per_item_and_strand_nothing(admin) -> None:
    org = uuid.uuid4()
    async with admin.begin() as conn:
        ids = await seed_old_shape(conn, org)
        item2 = await seed_second_item_sharing_ids(conn, org, ids)
        before = await counts(conn, org)
        per_item_before = await _ledger_per_item(conn, org)

    up = alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]

    async with admin.begin() as conn:
        assert await counts(conn, org) == before
        assert await _ledger_per_item(conn, org) == per_item_before  # nothing moved between items

        # One placeholder per (item, id), each attached to its own item.
        lot_items = (
            await conn.execute(
                text(
                    f"SELECT inventory_item_id FROM {SCHEMA}.lots WHERE organization_id = :o "
                    "AND lot_no LIKE 'LEGACY-%'"
                ),
                {"o": org},
            )
        ).scalars()
        assert sorted(map(str, lot_items)) == sorted(map(str, [ids["item"], item2]))
        rolls = (
            await conn.execute(
                text(
                    f"SELECT primary_milli FROM {SCHEMA}.rolls "
                    "WHERE organization_id = :o ORDER BY primary_milli"
                ),
                {"o": org},
            )
        ).scalars()
        # roll2 (item one: 40), the shared roll for item two (7), and for item one (82.5)
        assert list(rolls) == [7_000, 40_000, 82_500]

        # The point: no bucket names a lot or roll that belongs to a different item.
        for table in ("inventory_ledger", "inventory_balances", "count_lines"):
            mismatched = (
                await conn.execute(
                    text(
                        f"""
                        SELECT count(*) FROM {SCHEMA}.{table} t
                          LEFT JOIN {SCHEMA}.lots l
                                 ON l.organization_id = t.organization_id AND l.id = t.lot_id
                          LEFT JOIN {SCHEMA}.rolls r
                                 ON r.organization_id = t.organization_id AND r.id = t.roll_id
                         WHERE t.organization_id = :o
                           AND ((t.lot_id IS NOT NULL
                                 AND (l.id IS NULL OR l.inventory_item_id <> t.inventory_item_id))
                             OR (t.roll_id IS NOT NULL
                                 AND (r.id IS NULL OR r.inventory_item_id <> t.inventory_item_id)))
                        """
                    ),
                    {"o": org},
                )
            ).scalar_one()
            assert mismatched == 0, table
        async with AsyncSession(bind=conn) as session:
            report = await reconcile_organization(session, org)
        assert report.clean, report.findings
