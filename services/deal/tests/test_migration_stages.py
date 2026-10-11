# ruff: noqa: S608 — every f-string here interpolates only the SCHEMA constant; values are bound.
"""Deal's migration chain, run for real: `e94d7a2c5f18` → `a1c9e2f7b3d4` → `f3b8d1a65c02`.

`a1c9e2f7b3d4` ("add closed_at") is adopted **byte for byte** from the insights branch, so that
branch's rebase onto this one is a no-op; `f3b8d1a65c02` creates the two pipeline tables. These
tests prove the adoption is exact, the chain has one head and one order, the backfill recovers
what the audit trail can and guesses nothing, the downgrade refuses to destroy a configured
pipeline, and the migration connection gives up after five seconds rather than queue behind a
long session while the old containers still serve.

Like every migration test in this repo they drive alembic in a subprocess against the configured
database and restore head afterwards, so run them against a throwaway database.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings
from app.models import SCHEMA
from app.pipeline import DEFAULT_PIPELINE
from tests.pipeline_support import insert_deal, install_pipeline

pytestmark = pytest.mark.integration

SERVICE_DIR = Path(__file__).resolve().parent.parent
VERSIONS = SERVICE_DIR / "migrations" / "versions"

RLS_REVISION = "e94d7a2c5f18"  # pragma: allowlist secret
CLOSED_AT_REVISION = "a1c9e2f7b3d4"  # pragma: allowlist secret
STAGES_REVISION = "f3b8d1a65c02"  # pragma: allowlist secret
#: The git blob of `a1c9e2f7b3d4_add_closed_at.py` on the insights branch (`a4b248f1`).
ADOPTED_BLOB_SHA1 = "be9f7b9f66102b70346d178c8a84299912a5245d"  # pragma: allowlist secret

TABLES = (
    "activities",
    "deals",
    "pipeline_stages",
    "pipelines",
    "audit_logs",
    "outbox",
    "processed_events",
)


def alembic(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  (fixed argv, sys.executable, no shell)
        [sys.executable, "-m", "alembic", *args],
        cwd=SERVICE_DIR,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **(env or {})},
    )


def script_directory() -> ScriptDirectory:
    config = Config(str(SERVICE_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(SERVICE_DIR / "migrations"))
    return ScriptDirectory.from_config(config)


async def wipe(engine: AsyncEngine) -> None:
    """Empty whichever of this service's tables exist at the revision the database is at."""
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


async def revision_of(conn: AsyncConnection) -> str:
    return (
        await conn.execute(text(f"SELECT version_num FROM {SCHEMA}.alembic_version"))
    ).scalar_one()


async def table_exists(conn: AsyncConnection, name: str) -> bool:
    found = (await conn.execute(text("SELECT to_regclass(:t)"), {"t": f"{SCHEMA}.{name}"})).scalar()
    return found is not None


async def has_closed_at(conn: AsyncConnection) -> bool:
    return (
        await conn.execute(
            text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_schema = :s AND table_name = 'deals' AND column_name = 'closed_at'"
            ),
            {"s": SCHEMA},
        )
    ).scalar_one() == 1


@pytest.fixture
async def admin() -> AsyncIterator[AsyncEngine]:
    """The migration role's engine, at head with every table empty. Whatever a test does to the
    schema, head is restored — and the tables emptied again — before the next test."""
    engine = create_async_engine(
        settings().admin_dsn,
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
    )
    up = alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]
    await wipe(engine)
    try:
        yield engine
    finally:
        restored = alembic("upgrade", "head")
        await wipe(engine)
        await engine.dispose()
        assert restored.returncode == 0, restored.stderr[-2000:]


@pytest.fixture
async def at_rls_revision(admin: AsyncEngine) -> AsyncEngine:
    """The database as it was before this release: the RLS revision, no `closed_at`, no stages."""
    down = alembic("downgrade", RLS_REVISION)
    assert down.returncode == 0, down.stderr[-2000:]
    return admin


# --------------------------------------------------------------------------- the chain (static)


def test_the_adopted_closed_at_revision_is_byte_identical_to_the_insights_blob() -> None:
    """`git hash-object` of the file, computed the way git does: `blob <length>\\0<content>`.
    Any edit — a comment, a `SET LOCAL`, a re-parent — fails this, which is the point: the
    insights branch carries the same file, and an identical blob is what lets it rebase onto this
    release without a conflict."""
    content = (VERSIONS / "a1c9e2f7b3d4_add_closed_at.py").read_bytes()
    blob = hashlib.sha1(b"blob %d\0" % len(content) + content, usedforsecurity=False).hexdigest()
    assert blob == ADOPTED_BLOB_SHA1


def test_the_deal_chain_has_one_head_and_orders_the_three_revisions() -> None:
    """One head, and `e94d7a2c5f18` → `a1c9e2f7b3d4` → `f3b8d1a65c02` in that order along the
    chain. Written to stay true as later releases extend the chain (R2.6 adds a revision after
    `f3b8d1a65c02`): it fails if any *other* revision is parented on `e94d7a2c5f18`, which would
    leave two heads that the byte-pinned adopted file could not be re-parented to repair."""
    script = script_directory()
    assert len(script.get_heads()) == 1, script.get_heads()
    oldest_first = [
        rev.revision for rev in reversed(list(script.iterate_revisions("heads", "base")))
    ]
    positions = [oldest_first.index(r) for r in (RLS_REVISION, CLOSED_AT_REVISION, STAGES_REVISION)]
    assert positions == sorted(positions)
    rls = script.get_revision(RLS_REVISION)
    assert rls is not None
    assert set(rls.nextrev) == {CLOSED_AT_REVISION}
    stages = script.get_revision(STAGES_REVISION)
    assert stages is not None
    assert stages.down_revision == CLOSED_AT_REVISION


def test_the_deal_migration_session_runs_under_a_5s_lock_timeout() -> None:
    """The bound comes from `env.py`, not from a revision: the pinned file cannot carry a
    `SET LOCAL`, and Alembic runs every pending revision in one transaction, so a bound written
    in a later revision would run after the statement it needed to bound."""
    tree = ast.parse((SERVICE_DIR / "migrations" / "env.py").read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "async_engine_from_config"
    ]
    assert len(calls) == 1
    connect_args = next(kw.value for kw in calls[0].keywords if kw.arg == "connect_args")
    assert ast.literal_eval(connect_args) == {"server_settings": {"lock_timeout": "5s"}}


# --------------------------------------------------------------------------- upgrade


async def seed_deal(
    conn: AsyncConnection, org: uuid.UUID, *, stage: str, audit: list[tuple[str, str, datetime]]
) -> uuid.UUID:
    """A deal row as it exists at the RLS revision (no `closed_at` column yet), with the audit
    rows deal's own write path would have left."""
    deal_id = uuid.uuid4()
    await conn.execute(
        text(
            f"INSERT INTO {SCHEMA}.deals (id, organization_id, name, company_id, value_minor, "
            "currency, stage, owner_id, probability, close_date, source, created_at, updated_at) "
            "VALUES (:id, :org, :name, :company, 100, 'INR', :stage, :owner, 10, "
            "'2026-12-31', '', now(), now())"
        ),
        {
            "id": deal_id,
            "org": org,
            "name": f"Deal {deal_id.hex[:8]}",
            "company": uuid.uuid4(),
            "stage": stage,
            "owner": uuid.uuid4(),
        },
    )
    for action, after_stage, at in audit:
        await conn.execute(
            text(
                f"INSERT INTO {SCHEMA}.audit_logs (id, organization_id, action, entity, "
                "entity_id, after, at) VALUES (:id, :org, :action, 'deal', :deal, "
                "CAST(:after AS jsonb), :at)"
            ),
            {
                "id": uuid.uuid4(),
                "org": org,
                "action": action,
                "deal": deal_id,
                "after": json.dumps({"stage": after_stage}),
                "at": at,
            },
        )
    return deal_id


async def test_upgrade_from_the_rls_revision_adds_closed_at_backfilled_from_audit_and_both_tables(
    at_rls_revision: AsyncEngine,
) -> None:
    engine = at_rls_revision
    org = uuid.uuid4()
    opened = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
    won_at = opened + timedelta(days=3)
    lost_at = opened + timedelta(days=5)
    async with engine.begin() as conn:
        assert not await has_closed_at(conn)
        won = await seed_deal(
            conn,
            org,
            stage="closed_won",
            audit=[("deal.created", "lead", opened), ("deal.stage_changed", "closed_won", won_at)],
        )
        created_closed = await seed_deal(
            conn, org, stage="closed_lost", audit=[("deal.created", "closed_lost", lost_at)]
        )
        still_open = await seed_deal(
            conn,
            org,
            stage="negotiation",
            audit=[("deal.created", "lead", opened), ("deal.stage_changed", "negotiation", won_at)],
        )
        unaudited = await seed_deal(conn, org, stage="closed_won", audit=[])

    up = alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]

    async with engine.connect() as conn:
        assert await revision_of(conn) == STAGES_REVISION
        assert await has_closed_at(conn)
        assert await table_exists(conn, "pipelines")
        assert await table_exists(conn, "pipeline_stages")
        closed_at = dict(
            (
                await conn.execute(
                    text(f"SELECT id, closed_at FROM {SCHEMA}.deals WHERE organization_id = :org"),
                    {"org": org},
                )
            )
            .tuples()
            .all()
        )
    # Recovered from the deal's own audit trail where it says when ...
    assert closed_at[won] == won_at
    assert closed_at[created_closed] == lost_at
    # ... and never guessed where it does not.
    assert closed_at[still_open] is None
    assert closed_at[unaudited] is None


# --------------------------------------------------------------------------- downgrade


def sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def test_downgrade_is_clean_when_the_tables_are_empty(admin: AsyncEngine) -> None:
    down = alembic("downgrade", CLOSED_AT_REVISION)
    assert down.returncode == 0, down.stderr[-2000:]
    async with admin.connect() as conn:
        assert await revision_of(conn) == CLOSED_AT_REVISION
        assert not await table_exists(conn, "pipelines")
        assert not await table_exists(conn, "pipeline_stages")
        assert await has_closed_at(conn), "only the stages revision was undone"


async def test_downgrade_is_clean_after_an_untouched_materialisation(admin: AsyncEngine) -> None:
    """The six default rows, unchanged, are not an organization's configuration: nothing is lost
    by dropping them, and the code that wrote them reads the same six from its own constants."""
    async with sessions(admin)() as db:
        await install_pipeline(db, uuid.uuid4(), DEFAULT_PIPELINE.stages)
    down = alembic("downgrade", CLOSED_AT_REVISION)
    assert down.returncode == 0, down.stderr[-2000:]
    async with admin.connect() as conn:
        assert not await table_exists(conn, "pipeline_stages")


async def test_downgrade_refuses_when_a_custom_stage_exists(admin: AsyncEngine) -> None:
    async with sessions(admin)() as db:
        await install_pipeline(db, uuid.uuid4())  # discovery, scoping, quote, won, lost
    down = alembic("downgrade", CLOSED_AT_REVISION)
    assert down.returncode != 0
    assert "Refusing to downgrade" in down.stderr
    assert "customised stage row" in down.stderr
    async with admin.connect() as conn:
        assert await revision_of(conn) == STAGES_REVISION
        assert await table_exists(conn, "pipeline_stages")


async def test_downgrade_refuses_when_a_default_stage_was_renamed(admin: AsyncEngine) -> None:
    """One edited label is a configuration: the six rows are no longer "untouched"."""
    org = uuid.uuid4()
    async with sessions(admin)() as db:
        await install_pipeline(db, org, DEFAULT_PIPELINE.stages)
    async with admin.begin() as conn:
        await conn.execute(
            text(f"UPDATE {SCHEMA}.pipeline_stages SET label = 'Prospect' WHERE key = 'lead'")
        )
    down = alembic("downgrade", CLOSED_AT_REVISION)
    assert down.returncode != 0
    assert "Refusing to downgrade" in down.stderr


async def test_downgrade_refuses_when_a_deal_sits_in_a_custom_stage(admin: AsyncEngine) -> None:
    async with sessions(admin)() as db:
        await insert_deal(db, uuid.uuid4(), stage="discovery")
    down = alembic("downgrade", CLOSED_AT_REVISION)
    assert down.returncode != 0
    assert "Refusing to downgrade" in down.stderr
    assert "stage the previous code cannot name" in down.stderr
    async with admin.connect() as conn:
        assert await revision_of(conn) == STAGES_REVISION


async def test_downgrade_with_the_override_drops_everything(admin: AsyncEngine) -> None:
    async with sessions(admin)() as db:
        await install_pipeline(db, uuid.uuid4())
    down = alembic(
        "downgrade", CLOSED_AT_REVISION, env={"OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE": "1"}
    )
    assert down.returncode == 0, down.stderr[-2000:]
    async with admin.connect() as conn:
        assert not await table_exists(conn, "pipelines")
        assert not await table_exists(conn, "pipeline_stages")


# --------------------------------------------------------------------------- the lock bound


async def test_the_adopted_revision_fails_fast_behind_a_held_lock(
    at_rls_revision: AsyncEngine,
) -> None:
    """The adopted `ADD COLUMN` needs `ACCESS EXCLUSIVE` on `deal.deals`. Behind a session that
    holds any lock on it, that request would queue — and every later read and write of the table
    would queue behind it, while the old code still serves. Under the session `lock_timeout` it
    fails instead, applies nothing, and a re-run once the holder is gone succeeds."""
    engine = at_rls_revision
    holder = await engine.connect()
    held = await holder.begin()
    try:
        await holder.execute(text(f"LOCK TABLE {SCHEMA}.deals IN ACCESS SHARE MODE"))
        started = time.monotonic()
        blocked = alembic("upgrade", "head")
        elapsed = time.monotonic() - started

        assert blocked.returncode != 0
        assert "canceling statement due to lock timeout" in blocked.stderr
        assert elapsed < 30, f"the migration waited {elapsed:.0f}s instead of failing after ~5s"
        async with engine.connect() as probe:
            assert await revision_of(probe) == RLS_REVISION
            assert not await has_closed_at(probe)
    finally:
        await held.rollback()
        await holder.close()

    rerun = alembic("upgrade", "head")
    assert rerun.returncode == 0, rerun.stderr[-2000:]
    async with engine.connect() as conn:
        assert await has_closed_at(conn)
