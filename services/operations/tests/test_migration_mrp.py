# ruff: noqa: S608, E501 — every f-string here interpolates only the SCHEMA constant or a table name from
# this module's own tuple; values are bound.
"""`e7a4c1d9b352` (MRP), run for real: up, down, up again.

Drives alembic in a subprocess down to the revision right before this one (the manufacturing
migration) and asserts nothing of MRP is left; upgrades and asserts what must exist — every table
with forced row-level security and its tenant policy, no foreign key out of MRP's own tables, the
queued-run discovery grant for the outbox role; seeds a run with a shortage and its pegging for two
tenants through a bypassing role, proves the app role sees only its own tenant's rows, and
downgrades again over that data (destructive by design: the tables are new in this revision).
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
BEFORE = (
    "d3b9f2a7c614"  # the manufacturing migration, right before this one  # pragma: allowlist secret
)
APP_ROLE = "opsgrids_operations"
TABLES = ("mrp_runs", "mrp_demands", "mrp_shortages", "mrp_shortage_lines")


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


async def seed(engine: AsyncEngine, org: uuid.UUID, number: int) -> uuid.UUID:
    run, shortage = uuid.uuid4(), uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                f"INSERT INTO {SCHEMA}.mrp_runs (id, organization_id, seq, number, status, execution, "
                "plan_date, params, notes, demand_count, shortage_count, items_planned, error) "
                "VALUES (:id, :org, :seq, :num, 'COMPLETED', 'SYNC', current_date, '{}', '', 1, 1, 1, '')"
            ),
            {"id": run, "org": org, "seq": number, "num": f"MRP-{number:05d}"},
        )
        await conn.execute(
            text(
                f"INSERT INTO {SCHEMA}.mrp_demands (id, organization_id, run_id, source_type, source_ref, "
                "item_id, requested_milli, reserved_milli, quantity_milli, note) "
                "VALUES (gen_random_uuid(), :org, :run, 'MANUAL', 'manual', gen_random_uuid(), 5000, 0, 5000, '')"
            ),
            {"org": org, "run": run},
        )
        await conn.execute(
            text(
                f"INSERT INTO {SCHEMA}.mrp_shortages (id, organization_id, run_id, item_id, action, level, "
                "gross_milli, on_hand_milli, reserved_milli, open_po_milli, open_production_milli, "
                "shortage_milli) VALUES (:id, :org, :run, gen_random_uuid(), 'BUY', 0, 5000, 0, 0, 0, 0, 5000)"
            ),
            {"id": shortage, "org": org, "run": run},
        )
        await conn.execute(
            text(
                f"INSERT INTO {SCHEMA}.mrp_shortage_lines (id, organization_id, run_id, shortage_id, "
                "source_type, source_ref, quantity_milli) "
                "VALUES (gen_random_uuid(), :org, :run, :s, 'MANUAL', 'manual', 5000)"
            ),
            {"org": org, "run": run, "s": shortage},
        )
    return run


class TestMigration:
    async def test_down_removes_everything_and_up_restores_it_exactly(self, admin) -> None:  # type: ignore[no-untyped-def]
        down = alembic("downgrade", BEFORE)
        assert down.returncode == 0, down.stderr[-2000:]
        async with admin.connect() as conn:
            for table in TABLES:
                assert not await exists(conn, table), f"{table} survived the downgrade"
            # What was there before is untouched.
            assert await exists(conn, "boms") and await exists(conn, "manufacturing_requests")
            heads = (
                await conn.execute(text(f"SELECT version_num FROM {SCHEMA}.alembic_version"))
            ).all()
            assert [r[0] for r in heads] == [BEFORE]
        up = alembic("upgrade", "head")
        assert up.returncode == 0, up.stderr[-2000:]
        async with admin.connect() as conn:
            for table in TABLES:
                assert await exists(conn, table), f"{table} missing after upgrade"
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

    async def test_foreign_keys_stay_inside_mrp(self, admin) -> None:  # type: ignore[no-untyped-def]
        """`item_id`, source ids and the requisition columns are plain UUIDs; the only foreign keys
        MRP owns point at its own tables."""
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
        assert rows, "expected intra-MRP foreign keys"
        for child, parent, parent_schema in rows:
            assert parent_schema == SCHEMA and parent in TABLES, f"{child} -> {parent}"

    async def test_the_outbox_role_can_discover_queued_runs_and_nothing_more(self, admin) -> None:  # type: ignore[no-untyped-def]
        outbox = create_async_engine(
            settings().outbox_dsn,
            connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
        )
        try:
            async with outbox.connect() as conn:
                await conn.execute(
                    text(
                        f"SELECT DISTINCT organization_id FROM {SCHEMA}.mrp_runs WHERE status = 'QUEUED'"
                    )
                )
                for column in ("params", "notes", "result_hash"):
                    with pytest.raises(Exception, match="permission denied"):
                        await conn.execute(text(f"SELECT {column} FROM {SCHEMA}.mrp_runs"))
                    await conn.rollback()
                with pytest.raises(Exception, match="permission denied"):
                    await conn.execute(text(f"SELECT 1 FROM {SCHEMA}.mrp_shortages"))
        finally:
            await outbox.dispose()

    async def test_the_app_role_only_sees_its_own_tenant(self, admin) -> None:  # type: ignore[no-untyped-def]
        org_a, org_b = uuid.uuid4(), uuid.uuid4()
        run_a = await seed(admin, org_a, 1)
        run_b = await seed(admin, org_b, 1)
        app = create_async_engine(
            settings().app_dsn,
            connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
        )
        try:
            for org, mine, theirs in ((org_a, run_a, run_b), (org_b, run_b, run_a)):
                async with app.begin() as conn:
                    await conn.execute(
                        text(f"SELECT set_config('{TENANT_GUC}', :o, true)"), {"o": str(org)}
                    )
                    for table in TABLES:
                        rows = (
                            await conn.execute(
                                text(f"SELECT organization_id FROM {SCHEMA}.{table}")
                            )
                        ).all()
                        assert rows and {r[0] for r in rows} == {org}, table
                    ids = {
                        r[0]
                        for r in (
                            await conn.execute(text(f"SELECT id FROM {SCHEMA}.mrp_runs"))
                        ).all()
                    }
                    assert ids == {mine} and theirs not in ids
            # No tenant bound: nothing at all, on any table.
            async with app.begin() as conn:
                for table in TABLES:
                    assert (await conn.execute(text(f"SELECT 1 FROM {SCHEMA}.{table}"))).all() == []
            # And a write for another tenant is refused by the policy's WITH CHECK.
            with pytest.raises(Exception, match="row-level security"):
                async with app.begin() as conn:
                    await conn.execute(
                        text(f"SELECT set_config('{TENANT_GUC}', :o, true)"), {"o": str(org_a)}
                    )
                    await conn.execute(
                        text(
                            f"INSERT INTO {SCHEMA}.mrp_runs (id, organization_id, seq, number, status, "
                            "execution, plan_date, params, notes, demand_count, shortage_count, "
                            "items_planned, error) VALUES (gen_random_uuid(), :other, 9, 'MRP-X', "
                            "'QUEUED', 'SYNC', current_date, '{}', '', 0, 0, 0, '')"
                        ),
                        {"other": org_b},
                    )
        finally:
            await app.dispose()

    async def test_check_constraints_hold_below_the_service(self, admin) -> None:  # type: ignore[no-untyped-def]
        org = uuid.uuid4()
        run = await seed(admin, org, 1)
        for sql in (
            f"UPDATE {SCHEMA}.mrp_runs SET status = 'DONE' WHERE id = :r",
            f"UPDATE {SCHEMA}.mrp_demands SET quantity_milli = 1 WHERE run_id = :r",
            f"UPDATE {SCHEMA}.mrp_shortages SET shortage_milli = 0 WHERE run_id = :r",
            f"UPDATE {SCHEMA}.mrp_shortages SET action = 'SELL' WHERE run_id = :r",
            f"UPDATE {SCHEMA}.mrp_shortage_lines SET quantity_milli = 0 WHERE run_id = :r",
        ):
            with pytest.raises(Exception, match="violates check constraint"):
                async with admin.begin() as conn:
                    await conn.execute(text(sql), {"r": run})

    async def test_downgrade_over_data_and_up_again_round_trips(self, admin) -> None:  # type: ignore[no-untyped-def]
        await seed(admin, uuid.uuid4(), 1)
        down = alembic("downgrade", BEFORE)
        assert down.returncode == 0, down.stderr[-2000:]
        async with admin.connect() as conn:
            assert not await exists(conn, "mrp_runs")
        up = alembic("upgrade", "head")
        assert up.returncode == 0, up.stderr[-2000:]
        async with admin.connect() as conn:
            count = (await conn.execute(text(f"SELECT count(*) FROM {SCHEMA}.mrp_runs"))).scalar()
            assert count == 0  # new tables: a downgrade discards what they held
        again = alembic("downgrade", BEFORE)
        assert again.returncode == 0, again.stderr[-2000:]
        assert alembic("upgrade", "head").returncode == 0
