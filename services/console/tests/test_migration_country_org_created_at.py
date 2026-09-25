"""`c3bc1da7c0bc` (country and org created at on the commercial state), run for real.

Same shape as `test_migration_org_identity.py`: downgrade to the revision right before this one,
seed a row the way that schema would have (no `country`/`org_created_at`), prove upgrading to head
adds the columns without disturbing the row, and that downgrading back removes them cleanly.
"""

# ruff: noqa: S608 — every f-string here interpolates only the SCHEMA/TABLE constants above;
# values are always bound parameters.

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
# The org-identity migration, right before this one.
BEFORE = "3257fa97a764"  # pragma: allowlist secret
TABLE = "org_commercial_state"


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
        await conn.execute(text(f'TRUNCATE "{SCHEMA}"."{TABLE}" CASCADE'))
    try:
        down = alembic("downgrade", BEFORE)
        assert down.returncode == 0, down.stderr[-2000:]
        yield engine
    finally:
        up = alembic("upgrade", "head")
        await engine.dispose()
        assert up.returncode == 0, up.stderr[-2000:]


async def _columns(conn) -> set[str]:  # type: ignore[no-untyped-def]
    rows = (
        await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = :schema AND table_name = :table"
            ),
            {"schema": SCHEMA, "table": TABLE},
        )
    ).all()
    return {r[0] for r in rows}


class TestUpAndDown:
    async def test_upgrade_adds_the_columns_without_disturbing_existing_rows(self, admin) -> None:
        org_id = uuid.uuid4()
        async with admin.begin() as conn:
            columns_before = await _columns(conn)
            assert "country" not in columns_before
            assert "org_created_at" not in columns_before
            await conn.execute(
                text(
                    f'INSERT INTO "{SCHEMA}"."{TABLE}" '
                    "(id, organization_id, mrr_minor, active_products, "
                    "subscription_states, locked, name, slug) "
                    "VALUES (gen_random_uuid(), :org, 50000, '[]'::jsonb, '{}'::jsonb, false, "
                    "'Northwind Metals', 'northwind-metals')"
                ),
                {"org": org_id},
            )

        up = alembic("upgrade", "head")
        assert up.returncode == 0, up.stderr[-2000:]

        async with admin.begin() as conn:
            columns_after = await _columns(conn)
            assert {"country", "org_created_at"} <= columns_after
            row = (
                await conn.execute(
                    text(
                        "SELECT mrr_minor, name, country, org_created_at "
                        f'FROM "{SCHEMA}"."{TABLE}" WHERE organization_id = :org'
                    ),
                    {"org": org_id},
                )
            ).one()
            assert row.mrr_minor == 50000, "the upgrade must not touch an existing row's columns"
            assert row.name == "Northwind Metals"
            assert row.country is None
            assert row.org_created_at is None

    async def test_downgrade_removes_the_columns(self, admin) -> None:
        # `admin` already downgraded to BEFORE as its own setup; assert that state directly.
        async with admin.begin() as conn:
            columns = await _columns(conn)
        assert not ({"country", "org_created_at"} & columns)
