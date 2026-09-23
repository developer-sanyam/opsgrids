# ruff: noqa: S608 — every f-string here interpolates only the SCHEMA constant; values are bound.
"""`37e17a153b64` (uoms + uom_conversions), run for real against pre-Phase-1 data.

Drives alembic in a subprocess down to the revision right before this one, seeds items the way
the free-text-`uom` schema held them — a code the seed registry knows, a case variant of one, two
spellings of a unit the registry does not know, and a blank one — up to head, and checks that
every item resolved to a `uoms` row (or none, for the blank one), that case variants of an unknown
unit collapsed into one custom row, and that no item's `uom` string changed. Then downgrades (and
back up again) to prove the refusal and the restore.
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
BEFORE = "46e1eade7b18"  # alembic revision id  # pragma: allowlist secret
TABLES = (
    "uom_conversions",
    "uoms",
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
    """Items exactly as the pre-uoms schema held them: a free-text `uom`, nothing else."""
    wh = uuid.uuid4()
    await conn.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.warehouses (id, organization_id, code, name, location,
                                             created_at, updated_at)
            VALUES (:wh, :org, 'WH-PUN', 'Pune Plant', 'Pune', now(), now())
            """
        ),
        {"wh": wh, "org": org},
    )
    ids = {
        name: uuid.uuid4()
        for name in (
            "kilo",
            "pieces_upper",
            "tonnes",
            "tonnes_lower",
            "meter_alias",
            "spaced",
            "blank",
        )
    }
    items = [
        (ids["kilo"], "RM-KILO", "kg"),  # matches a seed code exactly
        (ids["pieces_upper"], "RM-PCS", "PCS"),  # matches a seed code case-insensitively
        (ids["tonnes"], "RM-MT-1", "MT"),  # the registry does not know "MT"
        (ids["tonnes_lower"], "RM-MT-2", "mt"),  # same unknown unit, different case
        (ids["meter_alias"], "RM-MTR", "Meter"),  # an alias, not the code itself
        (ids["spaced"], "RM-SPACED", " kg "),  # same seed code, stray whitespace
        (ids["blank"], "RM-BLANK", ""),  # no unit at all
    ]
    for item_id, sku, uom in items:
        await conn.execute(
            text(
                f"""
                INSERT INTO {SCHEMA}.inventory_items
                    (id, organization_id, sku, name, category, warehouse, home_location_id,
                     reorder_point, uom, unit_value_minor, currency, stocked_at,
                     created_at, updated_at)
                VALUES (:id, :org, :sku, :sku, 'raw_materials', 'Pune Plant',
                        (SELECT id FROM {SCHEMA}.locations WHERE warehouse_id = :wh LIMIT 1),
                        0, :uom, 0, 'INR', '2026-09-01', now(), now())
                """
            ),
            {"id": item_id, "org": org, "sku": sku, "uom": uom, "wh": wh},
        )
    return ids


async def test_every_item_resolves_a_uom_and_nothing_is_lost(admin) -> None:
    org = uuid.uuid4()
    async with admin.begin() as conn:
        ids = await seed_old_shape(conn, org)

    up = alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]

    async with admin.begin() as conn:
        # The registry codes were seeded system rows; "kg" and "PCS" (case-insensitively)
        # resolve straight to them.
        rows = {
            r.sku: (r.code, r.is_system)
            for r in (
                await conn.execute(
                    text(
                        f"""
                        SELECT i.sku, u.code, u.is_system
                          FROM {SCHEMA}.inventory_items i
                          LEFT JOIN {SCHEMA}.uoms u ON u.id = i.uom_id
                         WHERE i.organization_id = :org
                        """
                    ),
                    {"org": org},
                )
            ).all()
        }
        assert rows == {
            "RM-KILO": ("kg", True),
            "RM-PCS": ("pcs", True),
            "RM-MT-1": ("mt", False),
            "RM-MT-2": ("mt", False),
            "RM-MTR": ("m", True),  # "Meter" is an alias of the seeded "m", not its own row
            "RM-SPACED": ("kg", True),  # " kg " folds to the same seeded row as "kg"
            "RM-BLANK": (None, None),
        }
        # The two unknown-unit spellings collapsed onto the *same* custom row.
        mt_uom_ids = (
            (
                await conn.execute(
                    text(
                        f"""
                    SELECT DISTINCT i.uom_id FROM {SCHEMA}.inventory_items i
                     WHERE i.id IN (:a, :b)
                    """
                    ),
                    {"a": ids["tonnes"], "b": ids["tonnes_lower"]},
                )
            )
            .scalars()
            .all()
        )
        assert len(mt_uom_ids) == 1

        # Nothing about what the item displays changed — `uom` is untouched.
        original = dict(
            (
                await conn.execute(
                    text(
                        f"SELECT sku, uom FROM {SCHEMA}.inventory_items WHERE organization_id = :o"
                    ),
                    {"o": org},
                )
            ).all()
        )
        assert original == {
            "RM-KILO": "kg",
            "RM-PCS": "PCS",
            "RM-MT-1": "MT",
            "RM-MT-2": "mt",
            "RM-MTR": "Meter",
            "RM-SPACED": " kg ",
            "RM-BLANK": "",
        }

        # The full seed registry landed for this organization, not just the codes items used.
        seeded = (
            await conn.execute(
                text(
                    f"SELECT count(*) FROM {SCHEMA}.uoms WHERE organization_id = :o AND is_system"
                ),
                {"o": org},
            )
        ).scalar_one()
        assert seeded == 19  # every entry in the fixed registry


async def test_downgrade_refuses_while_a_conversion_exists_then_restores_cleanly(admin) -> None:
    org = uuid.uuid4()
    async with admin.begin() as conn:
        await seed_old_shape(conn, org)
    up = alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]

    async with admin.begin() as conn:
        item_id, uom_id = (
            await conn.execute(
                text(
                    f"""
                    SELECT i.id, u.id FROM {SCHEMA}.inventory_items i
                      JOIN {SCHEMA}.uoms u
                        ON u.organization_id = i.organization_id AND u.code = 'box'
                     WHERE i.organization_id = :o AND i.sku = 'RM-KILO'
                    """
                ),
                {"o": org},
            )
        ).one()
        await conn.execute(
            text(
                f"""
                INSERT INTO {SCHEMA}.uom_conversions
                    (id, organization_id, inventory_item_id, uom_id, numerator, denominator,
                     created_at)
                VALUES (gen_random_uuid(), :org, :item, :uom, 25, 1, now())
                """
            ),
            {"org": org, "item": item_id, "uom": uom_id},
        )

    blocked = alembic("downgrade", BEFORE)
    assert blocked.returncode != 0
    assert "uom_conversions" in blocked.stderr

    async with admin.begin() as conn:
        await conn.execute(
            text(f"DELETE FROM {SCHEMA}.uom_conversions WHERE organization_id = :o"), {"o": org}
        )

    clean = alembic("downgrade", BEFORE)
    assert clean.returncode == 0, clean.stderr[-2000:]
    async with admin.begin() as conn:
        assert (await conn.execute(text("SELECT to_regclass('operations.uoms')"))).scalar() is None
        # `uom` survived the round trip untouched.
        uom = (
            await conn.execute(
                text(
                    f"SELECT uom FROM {SCHEMA}.inventory_items "
                    "WHERE organization_id = :o AND sku = 'RM-KILO'"
                ),
                {"o": org},
            )
        ).scalar_one()
        assert uom == "kg"
