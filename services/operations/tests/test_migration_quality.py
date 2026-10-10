# ruff: noqa: S608 — every f-string here interpolates only the SCHEMA constant or a fixed table name
"""`a3c5e8f1b290` (quality: QC templates, inspections, results), run for real.

Downgrades to the revision right before it (the tables must vanish), upgrades to head (they must
exist, with FORCEd row-level security and the standard tenant policy that the **app** role actually
feels), proves the constraints refuse bad rows, and round-trips down and up with rows present.
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.models import SCHEMA
from platform_core.db import TENANT_GUC

pytestmark = pytest.mark.integration

SERVICE_DIR = Path(__file__).resolve().parent.parent
REVISION = "a3c5e8f1b290"  # pragma: allowlist secret
# The revision this one sits on, derived so re-parenting never touches this file.
BEFORE = f"{REVISION}-1"
TABLES = ("qc_templates", "qc_template_params", "inspections", "inspection_results")


def alembic(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  (fixed argv, sys.executable, no shell)
        [sys.executable, "-m", "alembic", *args],
        cwd=SERVICE_DIR,
        capture_output=True,
        text=True,
        check=False,
    )


def _engine(dsn: str):  # type: ignore[no-untyped-def]
    return create_async_engine(
        dsn, connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}}
    )


@pytest.fixture
async def admin():  # type: ignore[no-untyped-def]
    engine = _engine(settings().admin_dsn)
    # Always leave the schema at head, whatever a test did.
    try:
        yield engine
    finally:
        up = alembic("upgrade", "head")
        await engine.dispose()
        assert up.returncode == 0, up.stderr[-2000:]


async def _exists(conn, table: str) -> bool:  # type: ignore[no-untyped-def]
    return bool(
        (await conn.execute(text("SELECT to_regclass(:t)"), {"t": f"{SCHEMA}.{table}"})).scalar()
    )


async def _seed(conn, org: uuid.UUID) -> uuid.UUID:  # type: ignore[no-untyped-def]
    inspection = uuid.uuid4()
    await conn.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.inspections
                (id, organization_id, kind, status, item_id, location_id, reference_type,
                 quantity_milli, notes, decision_note, disposition, disposition_note,
                 disposition_reference)
            VALUES (:id, :org, 'incoming', 'PENDING', gen_random_uuid(), gen_random_uuid(), '',
                    1000, '', '', 'NONE', '', '')
            """
        ),
        {"id": inspection, "org": org},
    )
    return inspection


async def test_downgrade_removes_the_tables_and_upgrade_restores_them_with_rls(admin) -> None:
    down = alembic("downgrade", BEFORE)
    assert down.returncode == 0, down.stderr[-2000:]
    async with admin.connect() as conn:
        assert [await _exists(conn, t) for t in TABLES] == [False] * 4

    up = alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]
    async with admin.connect() as conn:
        assert [await _exists(conn, t) for t in TABLES] == [True] * 4
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
            assert tuple(flags) == (True, True), table
            policies = (
                (
                    await conn.execute(
                        text(
                            "SELECT policyname FROM pg_policies "
                            "WHERE schemaname = :s AND tablename = :t"
                        ),
                        {"s": SCHEMA, "t": table},
                    )
                )
                .scalars()
                .all()
            )
            assert policies == ["tenant_isolation"], table
            has_org = (
                await conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.columns WHERE table_schema = :s "
                        "AND table_name = :t AND column_name = 'organization_id' "
                        "AND is_nullable = 'NO'"
                    ),
                    {"s": SCHEMA, "t": table},
                )
            ).scalar()
            assert has_org == 1, table


async def test_the_app_role_only_sees_its_own_tenants_rows(admin) -> None:
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    async with admin.begin() as conn:
        await _seed(conn, org_a)
        await _seed(conn, org_b)

    app_engine = _engine(settings().app_dsn)
    try:
        async with app_engine.begin() as conn:
            await conn.execute(text(f"SELECT set_config('{TENANT_GUC}', '{org_a}', true)"))
            seen = (
                await conn.execute(text(f"SELECT organization_id FROM {SCHEMA}.inspections"))
            ).all()
            assert [r[0] for r in seen] == [org_a]
        async with app_engine.begin() as conn:  # no tenant bound at all: nothing, not everything
            assert (await conn.execute(text(f"SELECT 1 FROM {SCHEMA}.inspections"))).all() == []
        with pytest.raises(DBAPIError, match="row-level security"):
            async with app_engine.begin() as conn:
                await conn.execute(text(f"SELECT set_config('{TENANT_GUC}', '{org_a}', true)"))
                await _seed(conn, org_b)  # WITH CHECK: cannot write another tenant's row
    finally:
        await app_engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f"DELETE FROM {SCHEMA}.inspections"))


@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [
        ("status", "'DONE'", "status_known"),
        ("kind", "'outgoing'", "kind_known"),
        ("quantity_milli", "0", "quantity_positive"),
        ("accepted_milli", "2000", "accepted_within_quantity"),
        ("rejected_bucket", "'AVAILABLE'", "rejected_bucket_known"),
        ("disposition", "'DONATE'", "disposition_known"),
    ],
)
async def test_the_database_refuses_bad_inspections(
    admin, column: str, value: str, constraint: str
) -> None:
    with pytest.raises(DBAPIError, match=constraint):
        async with admin.begin() as conn:
            inspection = await _seed(conn, uuid.uuid4())
            await conn.execute(
                text(f"UPDATE {SCHEMA}.inspections SET {column} = {value} WHERE id = :id"),
                {"id": inspection},
            )


async def test_round_trip_with_rows_present(admin) -> None:
    org = uuid.uuid4()
    async with admin.begin() as conn:
        inspection = await _seed(conn, org)
        await conn.execute(
            text(
                f"""
                INSERT INTO {SCHEMA}.inspection_results
                    (id, organization_id, inspection_id, position, param_name, param_kind, unit,
                     is_critical, passed, note)
                VALUES (gen_random_uuid(), :org, :i, 0, 'GSM', 'numeric', '', false, true, '')
                """
            ),
            {"org": org, "i": inspection},
        )
    down = alembic("downgrade", BEFORE)
    assert down.returncode == 0, down.stderr[-2000:]
    up = alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]
    async with admin.connect() as conn:
        for table in TABLES:
            count = (await conn.execute(text(f"SELECT count(*) FROM {SCHEMA}.{table}"))).scalar()
            assert count == 0, table
    check = alembic("check")
    assert check.returncode == 0, check.stdout[-1000:] + check.stderr[-1000:]
