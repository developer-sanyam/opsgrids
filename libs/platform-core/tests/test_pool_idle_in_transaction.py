"""A connection returned to the pool must be `idle`, never `idle in transaction`.

Production, 2026-10-15: every service held exactly one app-role connection in `idle in
transaction`, for as long as it had been up. The cause was the pool's `reset` listener
(`RESET app.organization_id`): asyncpg's adapter opens a transaction on the first statement, and
the listener runs after the connection has already been rolled back, so nothing ever closed the
transaction it opened. An idle-in-transaction session holds a snapshot (blocking vacuum) and,
under any `idle_in_transaction_session_timeout`, gets killed.
"""

from __future__ import annotations

import asyncpg  # type: ignore[import-untyped]
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from platform_core.config import ServiceSettings
from platform_core.db import TENANT_GUC, create_bulkhead_engine, init_engine


class _Settings(ServiceSettings):
    service_name: str = "idle_probe"
    db_schema: str = "public"


async def _states(settings: _Settings, application_name: str) -> list[str]:
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    mon = await asyncpg.connect(dsn)
    try:
        rows = await mon.fetch(
            "SELECT state FROM pg_stat_activity WHERE application_name = $1", application_name
        )
    finally:
        await mon.close()
    return [r["state"] for r in rows]


async def _assert_pooled_connection_idle(
    engine: AsyncEngine, settings: _Settings, app: str
) -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    # A pure read, ended by close (not commit): the shape of a readiness probe or a GET.
    async with engine.connect() as conn:
        await conn.execute(text(f"SELECT current_setting('{TENANT_GUC}', true)"))
    states = await _states(settings, app)
    assert states, "the pooled connection should still be open"
    assert set(states) == {"idle"}, states


@pytest.mark.asyncio
async def test_request_engine_connection_is_idle_not_in_transaction_after_checkin() -> None:
    settings = _Settings()
    engine = init_engine(settings)
    try:
        await _assert_pooled_connection_idle(engine, settings, "idle_probe")
    finally:
        from platform_core.db import dispose_engine

        await dispose_engine()


@pytest.mark.asyncio
async def test_bulkhead_engine_connection_is_idle_not_in_transaction_after_checkin() -> None:
    settings = _Settings()
    engine = create_bulkhead_engine(settings, name="sweep")
    try:
        await _assert_pooled_connection_idle(engine, settings, "idle_probe-sweep")
    finally:
        await engine.dispose()
