"""`3257fa97a764` (org identity on the commercial state), run for real.

Adds three nullable columns to `org_commercial_state` — `name`, `slug`, `identity_synced_at` — the
`app/consumers.py` org-identity fold and the one-off `app/backfill.py` write into. This drives
alembic down to the revision right before this one, seeds a row the way the pre-migration schema
would have (no identity columns at all), proves upgrading to head adds the columns without
disturbing the row, and that downgrading back removes them cleanly.
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
# The append-only revenue_events migration, right before this one.
BEFORE = "d2e1f3a5b7c9"  # pragma: allowlist secret
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
    async def test_upgrade_adds_the_identity_columns_without_disturbing_existing_rows(
        self, admin
    ) -> None:
        org_id = uuid.uuid4()
        async with admin.begin() as conn:
            columns_before = await _columns(conn)
            assert "name" not in columns_before
            assert "slug" not in columns_before
            assert "identity_synced_at" not in columns_before
            await conn.execute(
                text(
                    f'INSERT INTO "{SCHEMA}"."{TABLE}" '
                    "(id, organization_id, mrr_minor, active_products, "
                    "subscription_states, locked) "
                    "VALUES (gen_random_uuid(), :org, 50000, '[]'::jsonb, '{}'::jsonb, false)"
                ),
                {"org": org_id},
            )

        up = alembic("upgrade", "head")
        assert up.returncode == 0, up.stderr[-2000:]

        async with admin.begin() as conn:
            columns_after = await _columns(conn)
            assert {"name", "slug", "identity_synced_at"} <= columns_after
            row = (
                await conn.execute(
                    text(
                        "SELECT mrr_minor, name, slug, identity_synced_at "
                        f'FROM "{SCHEMA}"."{TABLE}" WHERE organization_id = :org'
                    ),
                    {"org": org_id},
                )
            ).one()
            assert row.mrr_minor == 50000, "the upgrade must not touch an existing row's columns"
            assert row.name is None
            assert row.slug is None
            assert row.identity_synced_at is None

    async def test_downgrade_removes_the_identity_columns(self, admin) -> None:
        # `admin` already downgraded to BEFORE as its own setup; assert that state directly.
        async with admin.begin() as conn:
            columns = await _columns(conn)
        assert not ({"name", "slug", "identity_synced_at"} & columns)
