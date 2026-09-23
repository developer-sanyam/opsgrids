# ruff: noqa: S608 — every f-string here interpolates only the SCHEMA constant; values are bound.
"""The ADR-0014 data migration (`d4e8b1c93f20`), run for real against old-shape data.

Drives alembic in a subprocess (its env.py runs its own event loop) against the test database:
down to the pre-ledger revision, seed stock the way the old schema held it, up to head, and check
that not a unit, not a row id and not a movement was lost — then back down, to prove the rollback
restores the integer model exactly, and up again. Always leaves the schema at head.
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
from app.inventory.reconcile import reconcile_organization
from app.models import SCHEMA

pytestmark = pytest.mark.integration

SERVICE_DIR = Path(__file__).resolve().parent.parent
BEFORE = "a1c7fe093d42"
TABLES = (
    "inventory_discrepancies",
    "inventory_reservations",
    "inventory_balances",
    "inventory_ledger",
    "inventory_moves",
    "purchase_order_lines",
    "purchase_orders",
    "inventory_items",
    "locations",
    "warehouses",
    "suppliers",
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
            for t in TABLES
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
    """Stock exactly as the pre-ledger schema held it: an integer `on_hand` on the item and a
    `stock_movements` history that sums to it."""
    ids = {name: uuid.uuid4() for name in ("wh", "wh_gone", "alu", "ghost", "retired", "empty")}
    mv = {
        name: uuid.uuid4() for name in ("alu_open", "alu_adj", "ghost_open", "ret_open", "ret_adj")
    }
    await conn.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.warehouses (id, organization_id, code, name, location,
                                             created_at, updated_at, deleted_at)
            VALUES (:wh, :org, 'WH-PUN', 'Pune Plant', 'Pune', now(), now(), NULL),
                   (:gone, :org, 'WH-OLD', 'Old Depot', '', now(), now(), now())
            """
        ),
        {"wh": ids["wh"], "gone": ids["wh_gone"], "org": org},
    )
    items = [
        (ids["alu"], "RM-ALU-7075", "Pune Plant", 6, None),
        (ids["ghost"], "RM-GHOST", "Warehouse that never existed", 3, None),
        (ids["retired"], "RM-RETIRED", "Old Depot", 0, "now()"),
        (ids["empty"], "RM-EMPTY", "Pune Plant", 0, None),
    ]
    for item_id, sku, warehouse, on_hand, deleted in items:
        await conn.execute(
            text(
                f"""
                INSERT INTO {SCHEMA}.inventory_items
                    (id, organization_id, sku, name, category, warehouse, on_hand, reorder_point,
                     uom, unit_value_minor, currency, stocked_at, created_at, updated_at,
                     deleted_at)
                VALUES (:id, :org, :sku, :sku, 'raw_materials', :wh, :on_hand, 0, 'MT', 100,
                        'INR', '2026-08-01', now(), now(), {deleted or "NULL"})
                """
            ),
            {"id": item_id, "org": org, "sku": sku, "wh": warehouse, "on_hand": on_hand},
        )
    movements = [
        (mv["alu_open"], ids["alu"], "opening", None, 4, 4),
        (mv["alu_adj"], ids["alu"], "adjustment", "found", 2, 6),
        (mv["ghost_open"], ids["ghost"], "opening", None, 3, 3),
        (mv["ret_open"], ids["retired"], "opening", None, 5, 5),
        (mv["ret_adj"], ids["retired"], "adjustment", "consumed", -5, 0),
    ]
    for n, (mid, item_id, kind, reason, delta, after) in enumerate(movements):
        await conn.execute(
            text(
                f"""
                INSERT INTO {SCHEMA}.stock_movements
                    (id, organization_id, inventory_item_id, kind, reason, quantity_delta,
                     on_hand_after, note, occurred_on, created_at)
                VALUES (:id, :org, :item, :kind, :reason, :delta, :after, '', '2026-08-01',
                        now() + make_interval(secs => :n))
                """
            ),
            {
                "id": mid,
                "org": org,
                "item": item_id,
                "kind": kind,
                "reason": reason,
                "delta": delta,
                "after": after,
                "n": n,
            },
        )
    return {**ids, **mv}


async def test_upgrade_preserves_every_unit_and_row_and_downgrade_restores_it(admin) -> None:
    org = uuid.uuid4()
    async with admin.begin() as conn:
        ids = await seed_old_shape(conn, org)

    up = alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]

    async with admin.begin() as conn:
        on_hand = dict(
            (
                await conn.execute(
                    text(
                        f"""
                        SELECT i.sku, COALESCE(SUM(b.quantity_milli), 0)
                          FROM {SCHEMA}.inventory_items i
                          LEFT JOIN {SCHEMA}.inventory_balances b ON b.inventory_item_id = i.id
                         GROUP BY i.sku
                        """
                    )
                )
            ).all()
        )
        assert on_hand == {"RM-ALU-7075": 6000, "RM-GHOST": 3000, "RM-RETIRED": 0, "RM-EMPTY": 0}

        # Ledger rows kept their ids, gained milli-unit quantities and a move header each.
        ledger = {
            row.id: row
            for row in await conn.execute(
                text(
                    f"""
                    SELECT g.id, g.move_id, g.quantity_milli, g.balance_after_milli, g.status,
                           m.kind, m.reason
                      FROM {SCHEMA}.inventory_ledger g
                      JOIN {SCHEMA}.inventory_moves m ON m.id = g.move_id
                    """
                )
            )
        }
        assert set(ledger) == {
            ids[k] for k in ("alu_open", "alu_adj", "ghost_open", "ret_open", "ret_adj")
        }
        adj = ledger[ids["alu_adj"]]
        assert (adj.move_id, adj.quantity_milli, adj.balance_after_milli) == (
            ids["alu_adj"],
            2000,
            6000,
        )
        assert (adj.kind, adj.reason, adj.status) == ("adjustment", "found", "AVAILABLE")

        # The orphan's stock went to a named UNASSIGNED warehouse, not nowhere.
        ghost_home = (
            await conn.execute(
                text(
                    f"""
                    SELECT w.code, i.warehouse FROM {SCHEMA}.inventory_items i
                      JOIN {SCHEMA}.locations l ON l.id = i.home_location_id
                      JOIN {SCHEMA}.warehouses w ON w.id = l.warehouse_id
                     WHERE i.id = :i
                    """
                ),
                {"i": ids["ghost"]},
            )
        ).one()
        assert tuple(ghost_home) == ("UNASSIGNED", "Unassigned stock")

        # The soft-deleted item's history points at the soft-deleted warehouse's (deleted) bin.
        retired_bin_deleted = (
            await conn.execute(
                text(
                    f"""
                    SELECT l.deleted_at IS NOT NULL FROM {SCHEMA}.inventory_ledger g
                      JOIN {SCHEMA}.locations l ON l.id = g.location_id
                     WHERE g.id = :m
                    """
                ),
                {"m": ids["ret_open"]},
            )
        ).scalar_one()
        assert retired_bin_deleted is True

        # `on_hand` is no longer stored anywhere.
        assert (
            await conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.columns WHERE table_schema = :s "
                    "AND table_name = 'inventory_items' AND column_name = 'on_hand'"
                ),
                {"s": SCHEMA},
            )
        ).scalar_one() == 0

    async with admin.connect() as conn:
        from sqlalchemy.ext.asyncio import AsyncSession

        async with AsyncSession(bind=conn) as session, session.begin():
            report = await reconcile_organization(session, org)
        assert report.clean, report.findings

    # And back: the integer model, with the same ids and the same numbers.
    down = alembic("downgrade", BEFORE)
    assert down.returncode == 0, down.stderr[-2000:]
    async with admin.begin() as conn:
        restored = dict(
            (await conn.execute(text(f"SELECT sku, on_hand FROM {SCHEMA}.inventory_items"))).all()
        )
        assert restored == {"RM-ALU-7075": 6, "RM-GHOST": 3, "RM-RETIRED": 0, "RM-EMPTY": 0}
        movements = {
            row.id: (row.kind, row.quantity_delta, row.on_hand_after)
            for row in await conn.execute(
                text(
                    f"SELECT id, kind, quantity_delta, on_hand_after FROM {SCHEMA}.stock_movements"
                )
            )
        }
        assert movements[ids["alu_adj"]] == ("adjustment", 2, 6)
        assert len(movements) == 5


async def test_upgrade_refuses_when_on_hand_disagrees_with_its_movements(admin) -> None:
    org = uuid.uuid4()
    async with admin.begin() as conn:
        await seed_old_shape(conn, org)
        # A cached balance with no movement behind it: two numbers that should agree, don't.
        await conn.execute(
            text(f"UPDATE {SCHEMA}.inventory_items SET on_hand = 10 WHERE sku = 'RM-EMPTY'")
        )

    refused = alembic("upgrade", "head")
    assert refused.returncode != 0
    assert "disagrees with SUM(stock_movements.quantity_delta)" in refused.stderr
    assert "RM-EMPTY" in refused.stderr

    # Nothing half-applied: still the old shape, and fixable in place.
    async with admin.begin() as conn:
        assert (
            await conn.execute(text(f"SELECT version_num FROM {SCHEMA}.alembic_version"))
        ).scalar_one() == BEFORE
        await conn.execute(
            text(f"UPDATE {SCHEMA}.inventory_items SET on_hand = 0 WHERE sku = 'RM-EMPTY'")
        )


async def test_a_live_item_naming_only_a_soft_deleted_warehouse_is_redirected_by_its_actual_name(
    admin,
) -> None:
    """A live item whose `warehouse` string names nothing but a soft-deleted warehouse is exactly
    as stranded as one naming nothing at all: it must be redirected to UNASSIGNED, not silently
    pointed at a bin under a warehouse `post_move`'s own location lookup will never find again
    (review finding: soft-deleted warehouses must be excluded from the redirect decision).

    A soft-deleted item making the same match is left alone — coherent history, not a stranded
    item — and the redirect uses the org's *own* UNASSIGNED warehouse's real name rather than a
    hardcoded string, for an org that already had one under a name of its choosing.
    """
    org = uuid.uuid4()
    old_depot, misc, live_orphan, dead_orphan = (uuid.uuid4() for _ in range(4))
    async with admin.begin() as conn:
        await conn.execute(
            text(
                f"""
                INSERT INTO {SCHEMA}.warehouses
                    (id, organization_id, code, name, location, created_at, updated_at, deleted_at)
                VALUES (:old, :org, 'WH-OLD', 'Old Depot', '', now(), now(), now()),
                       (:misc, :org, 'UNASSIGNED', 'Miscellaneous Stock', '', now(), now(), NULL)
                """
            ),
            {"old": old_depot, "misc": misc, "org": org},
        )
        for item_id, sku, on_hand, deleted in (
            (live_orphan, "RM-LIVE-ORPHAN", 5, None),
            (dead_orphan, "RM-DEAD-ORPHAN", 3, "now()"),
        ):
            await conn.execute(
                text(
                    f"""
                    INSERT INTO {SCHEMA}.inventory_items
                        (id, organization_id, sku, name, category, warehouse, on_hand,
                         reorder_point, uom, unit_value_minor, currency, stocked_at, created_at,
                         updated_at, deleted_at)
                    VALUES (:id, :org, :sku, :sku, 'raw_materials', 'Old Depot', :on_hand, 0,
                            'MT', 100, 'INR', '2026-08-01', now(), now(), {deleted or "NULL"})
                    """
                ),
                {"id": item_id, "org": org, "sku": sku, "on_hand": on_hand},
            )
        for n, (item_id, delta) in enumerate(((live_orphan, 5), (dead_orphan, 3))):
            await conn.execute(
                text(
                    f"""
                    INSERT INTO {SCHEMA}.stock_movements
                        (id, organization_id, inventory_item_id, kind, reason, quantity_delta,
                         on_hand_after, note, occurred_on, created_at)
                    VALUES (gen_random_uuid(), :org, :item, 'opening', NULL, :delta, :delta, '',
                            '2026-08-01', now() + make_interval(secs => :n))
                    """
                ),
                {"org": org, "item": item_id, "delta": delta, "n": n},
            )

    up = alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]

    async with admin.begin() as conn:
        live_home = (
            await conn.execute(
                text(
                    f"""
                    SELECT w.code, w.name, w.deleted_at IS NULL, i.warehouse
                      FROM {SCHEMA}.inventory_items i
                      JOIN {SCHEMA}.locations l ON l.id = i.home_location_id
                      JOIN {SCHEMA}.warehouses w ON w.id = l.warehouse_id
                     WHERE i.id = :i
                    """
                ),
                {"i": live_orphan},
            )
        ).one()
        assert tuple(live_home) == (
            "UNASSIGNED",
            "Miscellaneous Stock",
            True,
            "Miscellaneous Stock",
        )

        dead_home = (
            await conn.execute(
                text(
                    f"""
                    SELECT w.code, w.deleted_at IS NOT NULL, i.warehouse
                      FROM {SCHEMA}.inventory_items i
                      JOIN {SCHEMA}.locations l ON l.id = i.home_location_id
                      JOIN {SCHEMA}.warehouses w ON w.id = l.warehouse_id
                     WHERE i.id = :i
                    """
                ),
                {"i": dead_orphan},
            )
        ).one()
        assert tuple(dead_home) == ("WH-OLD", True, "Old Depot")

        # No second UNASSIGNED warehouse was invented: the org's own is reused.
        assert (
            await conn.execute(
                text(
                    f"SELECT count(*) FROM {SCHEMA}.warehouses "
                    "WHERE organization_id = :org AND code = 'UNASSIGNED'"
                ),
                {"org": org},
            )
        ).scalar_one() == 1

    down = alembic("downgrade", BEFORE)
    assert down.returncode == 0, down.stderr[-2000:]


async def test_downgrade_keeps_an_unassigned_warehouse_a_live_item_still_names(admin) -> None:
    """The `UNASSIGNED` warehouse the upgrade invents for an orphaned item must survive the
    downgrade while that item still names it — checking only purchase orders (the original code)
    would delete it out from under a live item, leaving `inventory_items.warehouse` naming
    nothing (review finding)."""
    org = uuid.uuid4()
    async with admin.begin() as conn:
        ids = await seed_old_shape(conn, org)  # RM-GHOST is redirected to UNASSIGNED on upgrade

    up = alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]

    down = alembic("downgrade", BEFORE)
    assert down.returncode == 0, down.stderr[-2000:]

    async with admin.begin() as conn:
        assert (
            await conn.execute(
                text(f"SELECT warehouse FROM {SCHEMA}.inventory_items WHERE id = :i"),
                {"i": ids["ghost"]},
            )
        ).scalar_one() == "Unassigned stock"
        assert (
            await conn.execute(
                text(
                    f"SELECT count(*) FROM {SCHEMA}.warehouses "
                    "WHERE organization_id = :org AND code = 'UNASSIGNED'"
                ),
                {"org": org},
            )
        ).scalar_one() == 1
