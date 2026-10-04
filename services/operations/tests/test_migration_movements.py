# ruff: noqa: S608 — every f-string here interpolates only the SCHEMA constant; values are bound.
"""`c5a82f6d3b19` (inventory Phase 3: transfers, adjustment requests, control settings and the
reservation-expiry columns), run for real through alembic in a subprocess.

Checks: up leaves every new table under ENABLE + FORCE RLS with the tenant policy; the append-only
transition trail really refuses `UPDATE`; the CHECK and foreign-key constraints hold (including the
organization-scoped lot key); the outbox role can read exactly the reservation columns the expiry
sweep's discovery needs; down **refuses** while stock is in transit rather than stranding it, and
otherwise removes everything it added without touching a reservation; and up again works. Always
leaves the schema at head.
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
BEFORE = "c5e91a7d3b28"  # alembic revision id  # pragma: allowlist secret
REVISION = "c5a82f6d3b19"  # pragma: allowlist secret
NEW_TABLES = (
    "inventory_control_settings",
    "stock_transfers",
    "stock_transfer_lines",
    "stock_transfer_transitions",
    "adjustment_requests",
)
RESERVATION_COLUMNS = ("expires_at", "expired_at", "note")
TRUNCATE = (
    "stock_transfer_transitions",
    "stock_transfer_lines",
    "stock_transfers",
    "adjustment_requests",
    "inventory_control_settings",
    "inventory_reservations",
    "inventory_balances",
    "inventory_ledger",
    "inventory_moves",
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
    up = alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(f'{SCHEMA}.{t}' for t in TRUNCATE)} CASCADE"))
    try:
        yield engine
    finally:
        up = alembic("upgrade", "head")
        await engine.dispose()
        assert up.returncode == 0, up.stderr[-2000:]


async def seed(conn, org: uuid.UUID) -> dict[str, uuid.UUID]:  # type: ignore[no-untyped-def]
    """Two warehouses with a bin each, and one item."""
    ids = {n: uuid.uuid4() for n in ("wh_a", "wh_b", "loc_a", "loc_b", "item")}
    for wh, loc, code in (("wh_a", "loc_a", "A"), ("wh_b", "loc_b", "B")):
        await conn.execute(
            text(
                f"""
                INSERT INTO {SCHEMA}.warehouses (id, organization_id, code, name, location,
                                                 created_at, updated_at)
                VALUES (:wh, :org, :code, :code, '', now(), now())
                """
            ),
            {"wh": ids[wh], "org": org, "code": f"WH-{code}"},
        )
        await conn.execute(
            text(
                f"""
                INSERT INTO {SCHEMA}.locations (id, organization_id, warehouse_id, kind, code,
                                                name, is_default, created_at, updated_at)
                VALUES (:loc, :org, :wh, 'bin', 'DEFAULT', '', true, now(), now())
                """
            ),
            {"loc": ids[loc], "wh": ids[wh], "org": org},
        )
    await conn.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.inventory_items
                (id, organization_id, sku, name, category, warehouse, home_location_id,
                 reorder_point, uom, unit_value_minor, currency, stocked_at, hsn_code, track_lot,
                 track_roll, track_expiry, track_quality, is_active, created_at, updated_at)
            VALUES (:item, :org, 'DYE', 'Dye', 'raw_materials', 'WH-A', :loc_a, 0, 'kg', 0, 'INR',
                    '2026-09-01', '', false, false, false, false, true, now(), now())
            """
        ),
        {**ids, "org": org},
    )
    return ids


async def transfer(conn, org, ids, *, number="TRF-000001", dispatched=0, received=0, shortage=0):  # type: ignore[no-untyped-def]
    tid = uuid.uuid4()
    await conn.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.stock_transfers
                (id, organization_id, number, from_warehouse_id, to_warehouse_id, status, note,
                 decision_comment, cancel_reason, created_at, updated_at)
            VALUES (:id, :org, :number, :a, :b, 'IN_TRANSIT', '', '', '', now(), now())
            """
        ),
        {"id": tid, "org": org, "number": number, "a": ids["wh_a"], "b": ids["wh_b"]},
    )
    await conn.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.stock_transfer_lines
                (id, organization_id, transfer_id, line_no, inventory_item_id, from_location_id,
                 quantity_milli, dispatched_milli, received_milli, shortage_milli)
            VALUES (gen_random_uuid(), :org, :id, 1, :item, :loc_a, 40000, :d, :r, :s)
            """
        ),
        {
            "id": tid,
            "org": org,
            "item": ids["item"],
            "loc_a": ids["loc_a"],
            "d": dispatched,
            "r": received,
            "s": shortage,
        },
    )
    return tid


async def test_new_tables_carry_rls_and_the_trail_is_append_only(admin) -> None:
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
        for privilege, expected in (
            ("SELECT", True),
            ("INSERT", True),
            ("UPDATE", False),
            ("DELETE", False),
        ):
            granted = (
                await conn.execute(
                    text("SELECT has_table_privilege(:r, :t, :p)"),
                    {"r": app_role, "t": f"{SCHEMA}.stock_transfer_transitions", "p": privilege},
                )
            ).scalar_one()
            assert granted is expected, privilege
        # The sweep's discovery reads exactly these reservation columns, and no others.
        outbox = settings().db_outbox_role
        for column, expected in (
            ("organization_id", True),
            ("status", True),
            ("expires_at", True),
            ("note", False),
            ("quantity_milli", False),
        ):
            granted = (
                await conn.execute(
                    text(
                        "SELECT has_column_privilege(:r, 'operations.inventory_reservations', "
                        ":c, 'SELECT')"
                    ),
                    {"r": outbox, "c": column},
                )
            ).scalar_one()
            assert granted is expected, column


async def test_the_constraints_hold(admin) -> None:
    org = uuid.uuid4()
    async with admin.begin() as conn:
        ids = await seed(conn, org)

    async def refused(sql: str, **params) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(IntegrityError):
            async with admin.begin() as conn:
                await conn.execute(text(sql), {"org": org, **ids, **params})

    # A transfer between a warehouse and itself.
    await refused(
        f"""
        INSERT INTO {SCHEMA}.stock_transfers (id, organization_id, number, from_warehouse_id,
            to_warehouse_id, status, note, decision_comment, cancel_reason, created_at, updated_at)
        VALUES (gen_random_uuid(), :org, 'X', :wh_a, :wh_a, 'DRAFT', '', '', '', now(), now())
        """
    )
    # An unknown state.
    await refused(
        f"""
        INSERT INTO {SCHEMA}.stock_transfers (id, organization_id, number, from_warehouse_id,
            to_warehouse_id, status, note, decision_comment, cancel_reason, created_at, updated_at)
        VALUES (gen_random_uuid(), :org, 'X', :wh_a, :wh_b, 'SHIPPED', '', '', '', now(), now())
        """
    )
    async with admin.begin() as conn:
        good = await transfer(conn, org, ids, dispatched=40_000)
    # Number unique per organization.
    with pytest.raises(IntegrityError):
        async with admin.begin() as conn:
            await transfer(conn, org, ids, number="TRF-000001")
    # received + shortage may never exceed dispatched, which may never exceed the request.
    for received, shortage, dispatched in ((30_000, 20_000, 40_000), (0, 0, 50_000)):
        with pytest.raises(IntegrityError):
            async with admin.begin() as conn:
                await transfer(
                    conn,
                    org,
                    ids,
                    number=f"TRF-{received}-{dispatched}",
                    dispatched=dispatched,
                    received=received,
                    shortage=shortage,
                )
    del good
    # An adjustment that needs approval must carry its evidence.
    await refused(
        f"""
        INSERT INTO {SCHEMA}.adjustment_requests (id, organization_id, number, inventory_item_id,
            location_id, stock_status, quantity_delta_milli, reason, note, occurred_on, status,
            approval_required, decision_comment, requested_by, failure_reason, created_at,
            updated_at)
        VALUES (gen_random_uuid(), :org, 'ADJ-1', :item, :loc_a, 'AVAILABLE', -1000, 'lost', '',
                '2026-10-01', 'PENDING_APPROVAL', true, '', 'u', '', now(), now())
        """
    )
    # A zero adjustment, and an unknown reason.
    for delta, reason in ((0, "lost"), (-1000, "whim")):
        await refused(
            f"""
            INSERT INTO {SCHEMA}.adjustment_requests (id, organization_id, number,
                inventory_item_id, location_id, stock_status, quantity_delta_milli, reason, note,
                occurred_on, status, approval_required, decision_comment, requested_by,
                failure_reason, created_at, updated_at)
            VALUES (gen_random_uuid(), :org, 'ADJ-2', :item, :loc_a, 'AVAILABLE', :delta, :reason,
                    '', '2026-10-01', 'APPLIED', false, '', 'u', '', now(), now())
            """,
            delta=delta,
            reason=reason,
        )
    # Another tenant's lot cannot be named — the organization-scoped key.
    await refused(
        f"""
        INSERT INTO {SCHEMA}.adjustment_requests (id, organization_id, number, inventory_item_id,
            location_id, stock_status, lot_id, quantity_delta_milli, reason, note, occurred_on,
            status, approval_required, decision_comment, requested_by, failure_reason,
            created_at, updated_at)
        VALUES (gen_random_uuid(), :org, 'ADJ-3', :item, :loc_a, 'AVAILABLE', gen_random_uuid(),
                -1000, 'lost', '', '2026-10-01', 'APPLIED', false, '', 'u', '', now(), now())
        """
    )
    # A threshold of zero is no threshold; the counters start at one.
    await refused(
        f"""
        INSERT INTO {SCHEMA}.inventory_control_settings (id, organization_id,
            transfer_approval_threshold_milli)
        VALUES (gen_random_uuid(), :org, 0)
        """
    )


async def test_down_refuses_while_stock_is_in_transit_then_succeeds(admin) -> None:
    org = uuid.uuid4()
    async with admin.begin() as conn:
        ids = await seed(conn, org)
        await transfer(conn, org, ids, dispatched=40_000, received=15_000)

    refused = alembic("downgrade", BEFORE)
    assert refused.returncode != 0
    assert "still hold stock in transit" in refused.stderr
    async with admin.begin() as conn:
        version = (
            await conn.execute(text(f"SELECT version_num FROM {SCHEMA}.alembic_version"))
        ).scalar_one()
        assert version == REVISION  # nothing was half-applied

    # Once the line is settled (received + short = dispatched) the downgrade goes through.
    async with admin.begin() as conn:
        await conn.execute(text(f"UPDATE {SCHEMA}.stock_transfer_lines SET shortage_milli = 25000"))
    down = alembic("downgrade", BEFORE)
    assert down.returncode == 0, down.stderr[-2000:]
    async with admin.begin() as conn:
        for table in NEW_TABLES:
            assert (
                await conn.execute(text("SELECT to_regclass(:t)"), {"t": f"{SCHEMA}.{table}"})
            ).scalar() is None, table
        for column in RESERVATION_COLUMNS:
            assert (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM information_schema.columns WHERE table_schema = :s "
                        "AND table_name = 'inventory_reservations' AND column_name = :c"
                    ),
                    {"s": SCHEMA, "c": column},
                )
            ).scalar_one() == 0, column
        version = (
            await conn.execute(text(f"SELECT version_num FROM {SCHEMA}.alembic_version"))
        ).scalar_one()
        assert version == BEFORE

    again = alembic("upgrade", "head")
    assert again.returncode == 0, again.stderr[-2000:]


async def test_reservations_survive_a_round_trip(admin) -> None:
    org = uuid.uuid4()
    reservation = uuid.uuid4()
    async with admin.begin() as conn:
        ids = await seed(conn, org)
        balance = uuid.uuid4()
        await conn.execute(
            text(
                f"""
                INSERT INTO {SCHEMA}.inventory_balances
                    (id, organization_id, inventory_item_id, location_id, status, quantity_milli,
                     reserved_milli, updated_at)
                VALUES (:b, :org, :item, :loc_a, 'AVAILABLE', 100000, 30000, now())
                """
            ),
            {"b": balance, "org": org, **ids},
        )
        await conn.execute(
            text(
                f"""
                INSERT INTO {SCHEMA}.inventory_reservations
                    (id, organization_id, balance_id, inventory_item_id, quantity_milli,
                     consumed_milli, released_milli, transferred_milli, status, expires_at, note,
                     created_at)
                VALUES (:r, :org, :b, :item, 30000, 0, 0, 0, 'active', now() + interval '1 day',
                        'hold', now())
                """
            ),
            {"r": reservation, "b": balance, "org": org, **ids},
        )
    assert alembic("downgrade", BEFORE).returncode == 0
    async with admin.begin() as conn:
        row = (
            await conn.execute(
                text(
                    f"SELECT quantity_milli, status FROM {SCHEMA}.inventory_reservations "
                    "WHERE id = :r"
                ),
                {"r": reservation},
            )
        ).one()
        assert tuple(row) == (30_000, "active")
    assert alembic("upgrade", "head").returncode == 0
    async with admin.begin() as conn:
        row = (
            await conn.execute(
                text(f"SELECT expires_at, note FROM {SCHEMA}.inventory_reservations WHERE id = :r"),
                {"r": reservation},
            )
        ).one()
        # The upgrade adds the columns empty: no expiry, an empty note. Nothing else moved.
        assert row.expires_at is None and row.note == ""
