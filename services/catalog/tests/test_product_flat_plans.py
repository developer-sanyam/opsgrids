"""The flat monthly plan seeded for every non-platform product (`10e6b62d9ac7`).

Unlike the rest of this suite, this reaches past `services.py` and drives the migration itself:
`platform_session` truncates the definitional tables before every test (conftest.py), which is
right for tests that build their own plans but says nothing about what a fresh database actually
ends up with. Proving that requires running the migration and reading the result back — so this
module downgrades and upgrades the real thing, against the migration's own ids and amounts,
rather than a copy invented for the test (the same reasoning `test_packaging.py` gives for
loading `f78c86fcec78` by path instead of restating its numbers).

Session-scoped and serial: it is the one place in this service that moves the schema's migration
head, and it leaves the database at `head` again whether it passes or fails.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.models import SCHEMA

pytestmark = pytest.mark.integration

_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations" / "versions"


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


def _seed_module() -> ModuleType:
    """The flat-plan seed migration, imported by path — a migrations directory is not a package,
    and the point is to assert on the migration's own ids and amounts, not a restatement of
    them."""
    path = _MIGRATIONS_DIR / "10e6b62d9ac7_product_flat_plans.py"
    spec = importlib.util.spec_from_file_location("seed_product_flat_plans", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _alembic_config() -> Config:
    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    return cfg


def _sync(coro):  # type: ignore[no-untyped-def]
    """Run one query outside pytest-asyncio's loop.

    `alembic.command.upgrade`/`downgrade` call `asyncio.run()` themselves (`migrations/env.py`),
    so this module's test is a plain sync function rather than `async def` — nesting an
    `asyncio.run()` inside a loop pytest-asyncio already started would raise. The database check
    in between two alembic calls needs its own short-lived loop for the same reason.
    """
    return asyncio.run(coro)


async def _rows_for(plan_ids: list[str]) -> list[tuple]:
    """(product_key, code, plan_id, version_id, version_state, price_id, price_kind,
    amount_minor, currency, unit) for each of `plan_ids` that still exists — the join is on the
    migration's own ids, so a row present at all already proves the plan/version/price triple it
    seeded is intact."""
    engine = create_async_engine(settings().admin_dsn, connect_args=_connect_args())
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    """
                    SELECT p.product_key, p.code, p.id, v.id, v.state,
                           pr.id, pr.kind, pr.amount_minor, pr.currency, pr.unit
                    FROM catalog.plans p
                    LEFT JOIN catalog.plan_versions v ON v.plan_id = p.id
                    LEFT JOIN catalog.prices pr ON pr.plan_version_id = v.id
                    WHERE p.id = ANY(CAST(:plan_ids AS uuid[]))
                    """
                ),
                {"plan_ids": plan_ids},
            )
            return list(result.all())
    finally:
        await engine.dispose()


async def _plan_count(plan_ids: list[str]) -> int:
    engine = create_async_engine(settings().admin_dsn, connect_args=_connect_args())
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT count(*) FROM catalog.plans WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": plan_ids},
            )
            return result.scalar_one()
    finally:
        await engine.dispose()


def test_every_non_platform_product_gets_one_published_flat_inr_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Stepping down from head passes through `b88a833020dc`, whose downgrade refuses while its
    # list-price versions exist (it cannot see subscription's pins); on a test database deleting
    # them is the point.
    monkeypatch.setenv("OPSGRIDS_ALLOW_LIST_PRICE_DOWNGRADE", "1")
    seed = _seed_module()
    cfg = _alembic_config()
    plan_ids = [ids[0] for ids in seed.IDS.values()]

    # Baseline: whatever ran before this test, this migration's rows are out.
    command.downgrade(cfg, seed.down_revision)
    try:
        assert _sync(_plan_count(plan_ids)) == 0, "downgrade must remove every seeded plan"

        command.upgrade(cfg, seed.revision)
        rows = _sync(_rows_for(plan_ids))

        by_product = {row[0]: row for row in rows}
        assert set(by_product) == {product_key for product_key, *_ in seed.PRODUCTS}, (
            "every non-platform product in the seed must have exactly one row"
        )

        for product_key, code, _name, amount_minor, _sort_order in seed.PRODUCTS:
            plan_id, version_id, price_id = seed.IDS[product_key]
            (
                _pk,
                seeded_code,
                seeded_plan,
                seeded_version,
                state,
                price,
                kind,
                amount,
                currency,
                unit,
            ) = by_product[product_key]
            assert seeded_code == code
            assert str(seeded_plan) == plan_id
            assert str(seeded_version) == version_id
            assert state == "published", f"{product_key}'s version must be published"
            assert str(price) == price_id
            assert kind == "flat", f"{product_key}'s price must be flat, not per-seat or usage"
            assert amount == amount_minor
            assert currency == "INR"
            assert unit is None, "a flat price has no unit"

        # Exactly one version and one price per plan — no duplicate rows from a second run.
        counts: dict[str, int] = {}
        for row in rows:
            counts[row[0]] = counts.get(row[0], 0) + 1
        assert all(n == 1 for n in counts.values()), "each product must have exactly one row"

        # Idempotent: running the upgrade again must not create a second version or price.
        command.upgrade(cfg, seed.revision)
        rows_again = _sync(_rows_for(plan_ids))
        assert len(rows_again) == len(rows), "a second upgrade must not duplicate any row"
    finally:
        command.downgrade(cfg, seed.down_revision)
        assert _sync(_plan_count(plan_ids)) == 0, "downgrade must remove every seeded plan"
        command.upgrade(cfg, "head")
