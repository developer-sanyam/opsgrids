"""`create_bulkhead_engine` / `bulkhead_scope` — the private, fixed-size engine background work
sharing an API process uses instead of the API's own request pool (memory-headroom slice, review
round 3, LOW; raised from one connection to two by the worker-folding follow-up, which put more
than one background loop — a consumer alongside a sweep, in several services — on the same
bulkhead).

Background work that shares an API process with request traffic used to check its per-tenant
transactions out of the same 3 + 2 connections the requests use, and some of those transactions
hold their connection across network calls (storefront's domain probes, document's object-store
delete) or across whatever a folded consumer's handler does.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from platform_core.config import ServiceSettings
from platform_core.db import bulkhead_scope, create_bulkhead_engine


class _Settings(ServiceSettings):
    service_name: str = "probe"
    db_schema: str = "public"


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    engine = create_bulkhead_engine(_Settings(db_pool_size=3, db_max_overflow=2), name="sweep")
    yield engine
    await engine.dispose()


def test_the_bulkhead_is_two_connections_with_no_overflow_whatever_the_request_pool_is() -> None:
    """3 + 2 is the API's request pool on the edge; the bulkhead's is exactly two, and never
    grows — that is the whole point of a bulkhead (it can neither take a request's connection nor
    be starved by one)."""
    settings = _Settings(db_pool_size=3, db_max_overflow=2)
    engine = create_bulkhead_engine(settings, name="sweep")
    try:
        assert engine.pool.size() == 2  # type: ignore[attr-defined]
        assert engine.pool._max_overflow == 0  # type: ignore[attr-defined]
    finally:
        asyncio.run(engine.dispose())


def test_the_bulkhead_connects_as_the_application_role_not_a_bypass_role() -> None:
    settings = _Settings(db_app_role="opsgrids_probe_app", db_outbox_role="opsgrids_outbox")
    engine = create_bulkhead_engine(settings, name="sweep")
    try:
        assert engine.url.username == "opsgrids_probe_app"
    finally:
        asyncio.run(engine.dispose())


@pytest.mark.integration
async def test_the_scope_binds_the_tenant_for_the_transaction_and_not_beyond(
    engine: AsyncEngine,
) -> None:
    """Same contract as `session_scope(organization_id=...)`: the GUC is bound first, is the
    scope's organization while inside, and does not leak to the next scope on the same (only)
    connection — the pool reuses it."""
    scope = bulkhead_scope(engine)
    first, second = uuid.uuid4(), uuid.uuid4()
    async with scope(first) as session:
        bound = (
            await session.execute(text("select current_setting('app.organization_id')"))
        ).scalar()
    assert bound == str(first)
    async with scope(second) as session:
        bound = (
            await session.execute(text("select current_setting('app.organization_id')"))
        ).scalar()
    assert bound == str(second)


@pytest.mark.integration
async def test_a_third_scope_waits_for_the_first_two_rather_than_taking_another_connection(
    engine: AsyncEngine,
) -> None:
    """Two connections means two concurrent scopes and no more: with both held, a third scope on
    the same bulkhead cannot get a connection — proving it never quietly grows past two."""
    scope = bulkhead_scope(engine)
    async with scope(uuid.uuid4()) as first_held, scope(uuid.uuid4()) as second_held:
        await first_held.execute(text("select 1"))
        await second_held.execute(text("select 1"))
        third_started = asyncio.Event()

        async def contender() -> None:
            third_started.set()
            async with scope(uuid.uuid4()) as other:
                await other.execute(text("select 1"))

        task = asyncio.ensure_future(contender())
        await third_started.wait()
        done, _ = await asyncio.wait({task}, timeout=0.5)
        assert not done, "a third scope got a connection while the first two still held both"
    await asyncio.wait_for(task, timeout=5)
