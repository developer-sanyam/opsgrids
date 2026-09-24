"""The outbox role's discovery grants, walked through every downgrade from head.

`migrations/env.py`'s `grant_message_sweep_reader` used to `GRANT SELECT` on a fixed column list
unconditionally, after **every** alembic run — including a downgrade. `retry_at` was added to
`messages` at `d5a8f1c3e7b2`, a migration later than the table itself (`bbb40f8503b3`), so
`alembic downgrade c9d4e7a2b1f3` (one step below `d5a8f1c3e7b2`) ran the grant against a table
that no longer had the column, raised `UndefinedColumn`, and did it **after its own DDL had
already committed** — the migration succeeded and the grant step failed, leaving the schema
downgraded but the grant function broken for every run after. `infrastructure/edge/RUNBOOK.md`'s
own rollback step is `alembic downgrade -1`; this is what would have hit it.

This walks alembic down from `head` to the base, one revision at a time, and back up again,
asserting after every step that the outbox role's column privileges on `messages` (and, since the
same function's shape covers it, `campaigns`) are exactly the columns that exist at that revision
— never a grant that names a column the table does not have.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.models import SCHEMA

pytestmark = pytest.mark.integration

SERVICE_DIR = Path(__file__).resolve().parent.parent

#: Oldest first. `alembic history` reversed — kept explicit rather than parsed, so a test failure
#: reads as "wrong revision" rather than "alembic's own output changed shape".
REVISIONS = (
    "bbb40f8503b3",
    "a1b2c3d4e5f6",
    "b2c3d4e5f6a7",
    "d4e5f6a7b8c9",
    "f39a16d28e5c",
    "a4b7c9e1f206",
    "b8c1d2e3f4a5",
    "6c89f77c9fda",
    "c3a1ecc45df8",
    "e7d2f4a91b3c",
    "4f999b248e7e",
    "a1c7e93f5b02",
    "b6f1c2a7d8e9",
    "c9d4e7a2b1f3",
    "d5a8f1c3e7b2",
)

#: The columns each grant names, and the revision each column arrives with. `None` means "from
#: the table's own creation" (`bbb40f8503b3`).
MESSAGES_COLUMNS = {
    "organization_id": None,
    "id": None,
    "state": None,
    "retry_at": "d5a8f1c3e7b2",
}
CAMPAIGNS_COLUMNS = {
    "organization_id": None,
    "id": None,
    "status": None,
    "paused_at": None,
    "started_at": None,
    "deleted_at": None,
}
#: The revision a table first exists at (its own creation migration) — `messages` arrives with
#: the domain schema, one migration after the initial (empty) one.
TABLE_SINCE = {"messages": "a1b2c3d4e5f6", "campaigns": "4f999b248e7e"}


def alembic(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  (fixed argv, sys.executable, no shell)
        [sys.executable, "-m", "alembic", *args],
        cwd=SERVICE_DIR,
        capture_output=True,
        text=True,
        check=False,
    )


def _expected_columns(table_columns: dict[str, str | None], since_index: int) -> set[str]:
    return {
        name
        for name, arrives_at in table_columns.items()
        if arrives_at is None or REVISIONS.index(arrives_at) <= since_index
    }


async def _granted_columns(engine, table: str) -> set[str]:  # type: ignore[no-untyped-def]
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.column_privileges "
                "WHERE table_schema = :schema AND table_name = :table "
                "AND grantee = :role AND privilege_type = 'SELECT'"
            ),
            {"schema": SCHEMA, "table": table, "role": settings().db_outbox_role},
        )
        return {r[0] for r in rows}


async def _table_exists(engine, table: str) -> bool:  # type: ignore[no-untyped-def]
    async with engine.connect() as conn:
        return bool(
            (
                await conn.execute(text("SELECT to_regclass(:name)"), {"name": f"{SCHEMA}.{table}"})
            ).scalar()
        )


async def test_every_downgrade_step_grants_only_the_columns_that_exist() -> None:
    engine = create_async_engine(settings().admin_dsn)
    try:
        # From head (already migrated by the suite's own setup) down to the base, one step each.
        for index in range(len(REVISIONS) - 1, -1, -1):
            revision = REVISIONS[index]
            result = alembic("downgrade", revision)
            assert result.returncode == 0, (
                f"downgrade to {revision} failed:\n{result.stderr[-4000:]}"
            )
            for table, columns in (
                ("messages", MESSAGES_COLUMNS),
                ("campaigns", CAMPAIGNS_COLUMNS),
            ):
                since = TABLE_SINCE[table]
                if index < REVISIONS.index(since):
                    assert not await _table_exists(engine, table)
                    continue
                expected = _expected_columns(columns, index)
                granted = await _granted_columns(engine, table)
                assert granted == expected, (
                    f"{table} at {revision}: granted {granted}, expected {expected}"
                )

        base = alembic("downgrade", "base")
        assert base.returncode == 0, base.stderr[-4000:]
        assert not await _table_exists(engine, "messages")
        assert not await _table_exists(engine, "campaigns")

        # And every step back up, same assertion, so a regression on the *upgrade* path (a
        # forgotten call, a typo'd column list) is caught the same way.
        for index in range(len(REVISIONS)):
            revision = REVISIONS[index]
            result = alembic("upgrade", revision)
            assert result.returncode == 0, f"upgrade to {revision} failed:\n{result.stderr[-4000:]}"
            for table, columns in (
                ("messages", MESSAGES_COLUMNS),
                ("campaigns", CAMPAIGNS_COLUMNS),
            ):
                since = TABLE_SINCE[table]
                if index < REVISIONS.index(since):
                    assert not await _table_exists(engine, table)
                    continue
                expected = _expected_columns(columns, index)
                granted = await _granted_columns(engine, table)
                assert granted == expected, (
                    f"{table} at {revision}: granted {granted}, expected {expected}"
                )
    finally:
        restore = alembic("upgrade", "head")
        await engine.dispose()
        assert restore.returncode == 0, restore.stderr[-4000:]
