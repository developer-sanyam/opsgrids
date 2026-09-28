# ruff: noqa: S608 — every f-string here interpolates only the SCHEMA constant; values are bound.
"""`670a53a2c597` (widen `locations.kind`), run for real against pre-F1 data.

Drives alembic down to the revision right before this one, seeds a warehouse with locations in
the pre-fulfilment `zone | rack | bin` vocabulary, upgrades to head and checks the column now
accepts the full ADR-0024 §5 vocabulary (including `pack_station`, 12 characters — the reason the
column had to widen at all) without disturbing the rows already there. Then proves the downgrade
refuses while a new-vocabulary kind is in use, and restores cleanly once it is gone.
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
BEFORE = "7899dcdf7534"  # alembic revision, right before this one  # pragma: allowlist secret
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
    """A warehouse and its locations exactly as the pre-F1 schema held them: `kind` in
    `zone | rack | bin` only, `String(10)`."""
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
    ids = {name: uuid.uuid4() for name in ("zone", "rack", "bin")}
    for name, loc_id in ids.items():
        await conn.execute(
            text(
                f"""
                INSERT INTO {SCHEMA}.locations
                    (id, organization_id, warehouse_id, parent_id, kind, code, name, is_default,
                     created_at, updated_at)
                VALUES (:id, :org, :wh, NULL, :kind, :code, :name, false, now(), now())
                """
            ),
            {"id": loc_id, "org": org, "wh": wh, "kind": name, "code": name.upper(), "name": name},
        )
    return {"warehouse_id": wh, **ids}


async def test_upgrade_widens_kind_and_keeps_existing_rows_unchanged(admin) -> None:
    org = uuid.uuid4()
    async with admin.begin() as conn:
        ids = await seed_old_shape(conn, org)

    up = alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]

    async with admin.begin() as conn:
        rows = dict(
            (
                await conn.execute(
                    text(f"SELECT id, kind FROM {SCHEMA}.locations WHERE organization_id = :o"),
                    {"o": org},
                )
            ).all()
        )
        assert rows[ids["zone"]] == "zone"
        assert rows[ids["rack"]] == "rack"
        assert rows[ids["bin"]] == "bin"

    # The column now fits `pack_station` (12 characters) and every other new kind — its own
    # transaction, committed, so the next check's expected failure cannot abort it.
    async with admin.begin() as conn:
        for kind in (
            "aisle",
            "shelf",
            "staging",
            "pack_station",
            "dock",
            "receiving",
            "returns",
            "quarantine",
        ):
            new_id = uuid.uuid4()
            await conn.execute(
                text(
                    f"""
                    INSERT INTO {SCHEMA}.locations
                        (id, organization_id, warehouse_id, parent_id, kind, code, name,
                         is_default, created_at, updated_at)
                    VALUES (:id, :org, :wh, NULL, :kind, :code, :kind, false, now(), now())
                    """
                ),
                {"id": new_id, "org": org, "wh": ids["warehouse_id"], "kind": kind, "code": kind},
            )

    # An unknown kind is still refused by the (recreated) CHECK constraint. Its own connection
    # and transaction: a constraint violation aborts the transaction it happened in, so this
    # must not share one with anything asserted afterwards.
    with pytest.raises(Exception, match="kind_known"):
        async with admin.begin() as conn:
            await conn.execute(
                text(
                    f"""
                    INSERT INTO {SCHEMA}.locations
                        (id, organization_id, warehouse_id, parent_id, kind, code, name,
                         is_default, created_at, updated_at)
                    VALUES (gen_random_uuid(), :org, :wh, NULL, 'floor', 'FLOOR-1', 'floor',
                            false, now(), now())
                    """
                ),
                {"org": org, "wh": ids["warehouse_id"]},
            )


async def test_downgrade_refuses_a_new_vocabulary_kind_then_restores_cleanly(admin) -> None:
    org = uuid.uuid4()
    async with admin.begin() as conn:
        ids = await seed_old_shape(conn, org)
    up = alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]

    async with admin.begin() as conn:
        await conn.execute(
            text(f"UPDATE {SCHEMA}.locations SET kind = 'staging' WHERE id = :id"),
            {"id": ids["zone"]},
        )

    blocked = alembic("downgrade", BEFORE)
    assert blocked.returncode != 0
    assert "zone|rack|bin" in blocked.stderr or "kind" in blocked.stderr

    async with admin.begin() as conn:
        await conn.execute(
            text(f"UPDATE {SCHEMA}.locations SET kind = 'zone' WHERE id = :id"),
            {"id": ids["zone"]},
        )

    clean = alembic("downgrade", BEFORE)
    assert clean.returncode == 0, clean.stderr[-2000:]

    async with admin.begin() as conn:
        # The column is back to its pre-F1 width and vocabulary.
        col = (
            await conn.execute(
                text(
                    "SELECT character_maximum_length FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = 'locations' AND column_name = 'kind'"
                ),
                {"s": SCHEMA},
            )
        ).scalar_one()
        assert col == 10

    # Its own connection and transaction: a constraint violation aborts whatever transaction it
    # happened in, so this must not share one with the assertions before or after it.
    with pytest.raises(Exception, match="kind_known"):
        async with admin.begin() as conn:
            await conn.execute(
                text(
                    f"""
                    INSERT INTO {SCHEMA}.locations
                        (id, organization_id, warehouse_id, parent_id, kind, code, name,
                         is_default, created_at, updated_at)
                    VALUES (gen_random_uuid(), :org, :wh, NULL, 'staging', 'ST-1', 'staging',
                            false, now(), now())
                    """
                ),
                {"org": org, "wh": ids["warehouse_id"]},
            )

    async with admin.begin() as conn:
        # `bin`/`rack`/`zone` survived the round trip untouched.
        rows = dict(
            (
                await conn.execute(
                    text(f"SELECT id, kind FROM {SCHEMA}.locations WHERE organization_id = :o"),
                    {"o": org},
                )
            ).all()
        )
        assert rows[ids["zone"]] == "zone"
        assert rows[ids["rack"]] == "rack"
        assert rows[ids["bin"]] == "bin"
