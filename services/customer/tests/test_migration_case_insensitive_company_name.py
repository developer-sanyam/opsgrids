# ruff: noqa: S608 — every f-string here interpolates only the SCHEMA constant; values are bound.
"""`e230c025337e` (case-insensitive company name lookup), run for real against a database that
already holds two companies whose names differ only by case.

The first version of this migration replaced `uq_companies_org_name` with a *unique* index on
`lower(name)` — which fails `alembic upgrade` outright on exactly this data, because Postgres
cannot build a unique index over rows that already violate it. This drives alembic down to the
revision right before this one, seeds "Acme Ltd" and "ACME LTD" for the same organization, and
proves upgrading to head now succeeds (both rows survive, untouched), and that downgrading back
is clean.
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
BEFORE = "c74f180ecf16"  # revision before e230c025337e  # pragma: allowlist secret
TABLES = ("companies", "contacts", "owners", "audit_logs", "outbox")


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
        if existing:
            await conn.execute(
                text(f"TRUNCATE {', '.join(f'{SCHEMA}.{t}' for t in existing)} CASCADE")
            )
    try:
        down = alembic("downgrade", BEFORE)
        assert down.returncode == 0, down.stderr[-2000:]
        yield engine
    finally:
        up = alembic("upgrade", "head")
        await engine.dispose()
        assert up.returncode == 0, up.stderr[-2000:]


async def seed_case_variant_duplicate(conn, org: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:  # type: ignore[no-untyped-def]
    """Two companies, same organization, names differing only by case — legal under the
    original case-*sensitive* `uq_companies_org_name`, and exactly the data a unique
    `lower(name)` index cannot be built over."""
    owner = uuid.uuid4()
    await conn.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.owners (id, organization_id, full_name, email, role, is_active,
                                          created_at, updated_at)
            VALUES (:id, :org, 'Priya Sharma', 'priya@example.com', '', true, now(), now())
            """
        ),
        {"id": owner, "org": org},
    )
    first, second = uuid.uuid4(), uuid.uuid4()
    for company_id, name in ((first, "Acme Ltd"), (second, "ACME LTD")):
        await conn.execute(
            text(
                f"""
                INSERT INTO {SCHEMA}.companies
                    (id, organization_id, name, domain, industry, size_band, city, country,
                     status, owner_id, annual_revenue_minor, currency, created_at, updated_at)
                VALUES (:id, :org, :name, '', '', '', '', '', 'prospect', :owner, 0, 'INR',
                        now(), now())
                """
            ),
            {"id": company_id, "org": org, "name": name, "owner": owner},
        )
    return first, second


async def test_upgrade_succeeds_over_an_existing_case_variant_duplicate(admin) -> None:
    org = uuid.uuid4()
    async with admin.begin() as conn:
        first, second = await seed_case_variant_duplicate(conn, org)

    up = alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]

    async with admin.begin() as conn:
        names = (
            (
                await conn.execute(
                    text(f"SELECT name FROM {SCHEMA}.companies WHERE id IN (:a, :b)"),
                    {"a": first, "b": second},
                )
            )
            .scalars()
            .all()
        )
        ci_index = (
            await conn.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE schemaname = :schema AND indexname = 'ix_companies_org_name_ci'"
                ),
                {"schema": SCHEMA},
            )
        ).scalar_one_or_none()
        original_constraint = (
            await conn.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE schemaname = :schema AND indexname = 'uq_companies_org_name'"
                ),
                {"schema": SCHEMA},
            )
        ).scalar_one_or_none()

    # Both rows survive, untouched -- this migration never rewrites data.
    assert set(names) == {"Acme Ltd", "ACME LTD"}
    # The new index exists and is *not* unique.
    assert ci_index is not None
    assert "UNIQUE" not in ci_index
    # The same expression the lookup compares on both sides (stored names are never trimmed).
    assert "lower(" in ci_index and "btrim(" in ci_index
    # The original case-sensitive constraint is untouched, not dropped and recreated.
    assert original_constraint is not None
    assert "UNIQUE" in original_constraint


async def test_downgrade_is_clean(admin) -> None:
    org = uuid.uuid4()
    async with admin.begin() as conn:
        await seed_case_variant_duplicate(conn, org)

    up = alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]

    down = alembic("downgrade", BEFORE)
    assert down.returncode == 0, down.stderr[-2000:]

    async with admin.begin() as conn:
        ci_index = (
            await conn.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE schemaname = :schema AND indexname = 'ix_companies_org_name_ci'"
                ),
                {"schema": SCHEMA},
            )
        ).scalar_one_or_none()
    assert ci_index is None

    # Re-upgrade so the fixture's own teardown (which re-upgrades to head) is a no-op, and so
    # this test leaves the schema in the same state it found it in either way.
    up_again = alembic("upgrade", "head")
    assert up_again.returncode == 0, up_again.stderr[-2000:]
