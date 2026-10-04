# ruff: noqa: S608, E501 — every f-string here interpolates only the SCHEMA constant or a table name from
# this module's own tuple; values are bound.
"""`d3b9f2a7c614` (manufacturing), run for real: up, down, up again.

Drives alembic in a subprocess down to the revision right before this one and asserts nothing of
manufacturing is left (tables, trigger functions); upgrades and asserts what must exist — every
table with forced row-level security and its tenant policy, the three immutability triggers, and
UPDATE/DELETE revoked from the app role on exactly the append-only tables; then seeds a published
BOM and a production order through a bypassing role, proves the app role sees only its own
tenant's rows, and downgrades again over that data (the downgrade is destructive by design: these
tables are new in this revision) before restoring head.
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import settings
from app.models import SCHEMA
from platform_core.db import TENANT_GUC

pytestmark = pytest.mark.integration

SERVICE_DIR = Path(__file__).resolve().parent.parent
BEFORE = "665e44e243d7"  # alembic revision right before this one  # pragma: allowlist secret
APP_ROLE = "opsgrids_operations"

TABLES = (
    "boms",
    "bom_versions",
    "bom_components",
    "bom_substitutes",
    "production_orders",
    "production_materials",
    "production_picks",
    "production_outputs",
    "production_wastage",
    "production_genealogy",
    "wastage_thresholds",
    "manufacturing_requests",
)
APPEND_ONLY = ("production_outputs", "production_wastage", "production_genealogy")
FUNCTIONS = ("guard_bom_version", "guard_bom_component", "guard_bom_substitute")


def alembic(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  (fixed argv, sys.executable, no shell)
        [sys.executable, "-m", "alembic", *args],
        cwd=SERVICE_DIR,
        capture_output=True,
        text=True,
        check=False,
    )


async def exists(conn, name: str) -> bool:  # type: ignore[no-untyped-def]
    return bool(
        (await conn.execute(text("SELECT to_regclass(:t)"), {"t": f"{SCHEMA}.{name}"})).scalar()
    )


async def function_exists(conn, name: str) -> bool:  # type: ignore[no-untyped-def]
    return bool(
        (
            await conn.execute(
                text(
                    "SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = :s AND p.proname = :f"
                ),
                {"s": SCHEMA, "f": name},
            )
        ).scalar()
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


async def seed(engine: AsyncEngine, org_a: uuid.UUID, org_b: uuid.UUID) -> uuid.UUID:
    """A published BOM, one component and an order for org A, and a BOM for org B."""
    ids = {
        k: uuid.uuid4() for k in ("bom", "ver", "comp", "order", "item", "loc", "bom_b", "ver_b")
    }
    async with engine.begin() as conn:
        for org, bom, ver in ((org_a, ids["bom"], ids["ver"]), (org_b, ids["bom_b"], ids["ver_b"])):
            await conn.execute(
                text(
                    f"INSERT INTO {SCHEMA}.boms (id, organization_id, code, name, item_id, "
                    "description, is_active) VALUES (:id, :org, :code, 'b', gen_random_uuid(), '', true)"
                ),
                {"id": bom, "org": org, "code": f"B-{bom.hex[:6]}"},
            )
            await conn.execute(
                text(
                    f"INSERT INTO {SCHEMA}.bom_versions (id, organization_id, bom_id, version_no, "
                    "status, output_quantity_milli, notes) "
                    "VALUES (:id, :org, :bom, 1, 'DRAFT', 1000, '')"
                ),
                {"id": ver, "org": org, "bom": bom},
            )
        await conn.execute(
            text(
                f"INSERT INTO {SCHEMA}.bom_components (id, organization_id, bom_version_id, "
                "line_no, component_item_id, quantity_milli, scrap_bps, notes) "
                "VALUES (:id, :org, :ver, 1, :item, 1000, 250, '')"
            ),
            {"id": ids["comp"], "org": org_a, "ver": ids["ver"], "item": ids["item"]},
        )
        await conn.execute(
            text(f"UPDATE {SCHEMA}.bom_versions SET status = 'PUBLISHED' WHERE id = :id"),
            {"id": ids["ver"]},
        )
        await conn.execute(
            text(
                f"INSERT INTO {SCHEMA}.production_orders (id, organization_id, seq, number, bom_id, "
                "bom_version_id, item_id, status, planned_quantity_milli, produced_quantity_milli, "
                "output_location_id, resolved_on, cancel_reason, notes) "
                "VALUES (:id, :org, 1, 'MO-00001', :bom, :ver, :item, 'DRAFT', 5000, 0, :loc, "
                "current_date, '', '')"
            ),
            {
                "id": ids["order"],
                "org": org_a,
                "bom": ids["bom"],
                "ver": ids["ver"],
                "item": ids["item"],
                "loc": ids["loc"],
            },
        )
    return ids["bom"]


class TestMigration:
    async def test_down_removes_everything_and_up_restores_it_exactly(self, admin) -> None:  # type: ignore[no-untyped-def]
        down = alembic("downgrade", BEFORE)
        assert down.returncode == 0, down.stderr[-2000:]
        async with admin.connect() as conn:
            for table in TABLES:
                assert not await exists(conn, table), f"{table} survived the downgrade"
            for function in FUNCTIONS:
                assert not await function_exists(conn, function), f"{function} survived"
            # What was there before is untouched.
            assert await exists(conn, "channels") and await exists(conn, "inventory_ledger")

        up = alembic("upgrade", "head")
        assert up.returncode == 0, up.stderr[-2000:]
        async with admin.connect() as conn:
            for table in TABLES:
                assert await exists(conn, table), f"{table} missing after upgrade"
            for function in FUNCTIONS:
                assert await function_exists(conn, function), f"{function} missing after upgrade"
        check = alembic("check")
        assert check.returncode == 0, check.stdout[-2000:] + check.stderr[-2000:]

    async def test_every_table_is_row_level_secured_and_carries_the_org(self, admin) -> None:  # type: ignore[no-untyped-def]
        async with admin.connect() as conn:
            for table in TABLES:
                flags = (
                    await conn.execute(
                        text(
                            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class c "
                            "JOIN pg_namespace n ON n.oid = c.relnamespace "
                            "WHERE n.nspname = :s AND c.relname = :t"
                        ),
                        {"s": SCHEMA, "t": table},
                    )
                ).one()
                assert tuple(flags) == (True, True), f"{table}: RLS not enabled and forced"
                policy = (
                    await conn.execute(
                        text(
                            "SELECT qual, with_check FROM pg_policies "
                            "WHERE schemaname = :s AND tablename = :t AND policyname = 'tenant_isolation'"
                        ),
                        {"s": SCHEMA, "t": table},
                    )
                ).one_or_none()
                assert policy is not None, f"{table}: no tenant_isolation policy"
                assert "organization_id" in policy.qual and "organization_id" in policy.with_check
                column = (
                    await conn.execute(
                        text(
                            "SELECT is_nullable FROM information_schema.columns WHERE "
                            "table_schema = :s AND table_name = :t AND column_name = 'organization_id'"
                        ),
                        {"s": SCHEMA, "t": table},
                    )
                ).scalar()
                assert column == "NO", f"{table}: organization_id must be NOT NULL"

    async def test_no_foreign_key_to_lots_rolls_items_or_locations(self, admin) -> None:  # type: ignore[no-untyped-def]
        """`item_id`/`location_id`/`lot_id`/`roll_id` are plain indexed-where-it-matters UUIDs; the
        only foreign keys manufacturing owns point at its own tables."""
        async with admin.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT c.relname AS child, p.relname AS parent, pn.nspname AS parent_schema "
                        "FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid "
                        "JOIN pg_namespace cn ON cn.oid = c.relnamespace "
                        "JOIN pg_class p ON p.oid = k.confrelid "
                        "JOIN pg_namespace pn ON pn.oid = p.relnamespace "
                        "WHERE k.contype = 'f' AND cn.nspname = :s AND c.relname = ANY(:t)"
                    ),
                    {"s": SCHEMA, "t": list(TABLES)},
                )
            ).all()
        assert rows, "expected intra-manufacturing foreign keys"
        for child, parent, parent_schema in rows:
            assert parent_schema == SCHEMA and parent in TABLES, f"{child} -> {parent}"

    async def test_append_only_tables_cannot_be_updated_or_deleted_by_the_app_role(
        self, admin
    ) -> None:  # type: ignore[no-untyped-def]
        async with admin.connect() as conn:
            for table in TABLES:
                for privilege in ("UPDATE", "DELETE"):
                    granted = (
                        await conn.execute(
                            text("SELECT has_table_privilege(:r, :t, :p)"),
                            {"r": APP_ROLE, "t": f"{SCHEMA}.{table}", "p": privilege},
                        )
                    ).scalar()
                    assert granted is (table not in APPEND_ONLY), (table, privilege, granted)
                assert (
                    await conn.execute(
                        text("SELECT has_table_privilege(:r, :t, 'INSERT')"),
                        {"r": APP_ROLE, "t": f"{SCHEMA}.{table}"},
                    )
                ).scalar()

    async def test_the_triggers_exist_and_the_app_role_only_sees_its_own_tenant(
        self, admin
    ) -> None:  # type: ignore[no-untyped-def]
        org_a, org_b = uuid.uuid4(), uuid.uuid4()
        bom_a = await seed(admin, org_a, org_b)
        async with admin.connect() as conn:
            triggers = {
                r[0]
                for r in (
                    await conn.execute(
                        text(
                            "SELECT tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                            "JOIN pg_namespace n ON n.oid = c.relnamespace "
                            "WHERE n.nspname = :s AND NOT t.tgisinternal"
                        ),
                        {"s": SCHEMA},
                    )
                ).all()
            }
        assert set(FUNCTIONS) <= triggers

        app = create_async_engine(
            settings().app_dsn,
            connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
        )
        try:
            for org, expected_boms in ((org_a, 1), (org_b, 1)):
                async with app.begin() as conn:
                    await conn.execute(
                        text(f"SELECT set_config('{TENANT_GUC}', :o, true)"), {"o": str(org)}
                    )
                    boms = (await conn.execute(text(f"SELECT id FROM {SCHEMA}.boms"))).all()
                    orders = (
                        await conn.execute(text(f"SELECT id FROM {SCHEMA}.production_orders"))
                    ).all()
                    assert len(boms) == expected_boms
                    assert len(orders) == (1 if org == org_a else 0)
                    assert (bom_a in {r[0] for r in boms}) is (org == org_a)
            # No tenant bound: nothing at all.
            async with app.begin() as conn:
                assert (await conn.execute(text(f"SELECT 1 FROM {SCHEMA}.boms"))).all() == []
        finally:
            await app.dispose()

    async def test_downgrade_over_data_and_up_again_round_trips(self, admin) -> None:  # type: ignore[no-untyped-def]
        await seed(admin, uuid.uuid4(), uuid.uuid4())
        down = alembic("downgrade", BEFORE)
        assert down.returncode == 0, down.stderr[-2000:]
        async with admin.connect() as conn:
            assert not await exists(conn, "boms")
        up = alembic("upgrade", "head")
        assert up.returncode == 0, up.stderr[-2000:]
        async with admin.connect() as conn:
            count = (await conn.execute(text(f"SELECT count(*) FROM {SCHEMA}.boms"))).scalar()
            assert count == 0  # new tables: a downgrade discards what they held
        # And once more, from head: base -> head must work too (what `make migration-roundtrip` does
        # for the whole service is covered by CI; this proves this revision in isolation).
        again = alembic("downgrade", BEFORE)
        assert again.returncode == 0, again.stderr[-2000:]
        assert alembic("upgrade", "head").returncode == 0
