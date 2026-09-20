"""`cross_service_schema_lock` — the mutual exclusion that keeps `make test-services`
parallel and deterministic at the same time.

This is the guard on a fix for a defect that was invisible precisely because nothing tested
it: the fan-out ran every service's suite at once on the stated premise that a suite only ever
touches its own schema, and two suites deliberately do not. Everything asserted here is a
property the fix rests on — it excludes, it releases even when the guarded block blows up, it
waits rather than failing fast, it stops waiting rather than hanging CI, and it cannot deadlock
against itself.

The peer suite is stood in for by a second connection holding the *raw* advisory lock, which is
exactly what the other process holds. Nothing here needs the named schema to exist: an advisory
lock is a number in the lock manager, not a grant on an object.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from platform_core.config import ServiceSettings
from platform_core.testing import (
    CROSS_SERVICE_LOCK_NAMESPACE,
    cross_service_schema_lock,
    schema_lock_key,
)

pytestmark = pytest.mark.integration


class _Settings(ServiceSettings):
    service_name: str = "probe"
    db_schema: str = "public"


def _dsn() -> str:
    return _Settings(db_admin_role="opsgrids_admin").admin_dsn


def _unique_schema() -> str:
    """A name no real suite contends for, so this file can run beside them."""
    return f"probe_{uuid.uuid4().hex}"


class _Peer:
    """The other service's test process, as far as the lock manager can tell: one connection
    holding the raw advisory lock until it is told to let go."""

    def __init__(self, schema: str) -> None:
        self._key = schema_lock_key(schema)
        self._engine = create_async_engine(_dsn(), poolclass=NullPool, isolation_level="AUTOCOMMIT")
        self._connection: AsyncConnection | None = None

    async def take(self) -> None:
        self._connection = await self._engine.connect()
        await self._connection.execute(
            text("SELECT pg_advisory_lock(:ns, :key)"),
            {"ns": CROSS_SERVICE_LOCK_NAMESPACE, "key": self._key},
        )

    async def release(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None
        await self._engine.dispose()


async def _is_locked(schema: str) -> bool:
    """Ask Postgres, not our own bookkeeping, whether the lock is held by anyone."""
    engine = create_async_engine(_dsn(), poolclass=NullPool, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            taken = (
                await connection.execute(
                    text("SELECT pg_try_advisory_lock(:ns, :key)"),
                    {"ns": CROSS_SERVICE_LOCK_NAMESPACE, "key": schema_lock_key(schema)},
                )
            ).scalar_one()
            if taken:
                await connection.execute(
                    text("SELECT pg_advisory_unlock(:ns, :key)"),
                    {"ns": CROSS_SERVICE_LOCK_NAMESPACE, "key": schema_lock_key(schema)},
                )
            return not bool(taken)
    finally:
        await engine.dispose()


class TestTheKey:
    def test_the_key_is_derived_from_the_schema_name_and_nothing_else(self) -> None:
        """Both sides of a collision compute it independently — the service that owns the
        schema and the sibling suite reaching into it — so it must depend only on the name."""
        assert schema_lock_key("attendance") == schema_lock_key("attendance")
        assert schema_lock_key("attendance") != schema_lock_key("employee")

    def test_the_key_fits_the_int4_postgres_actually_takes(self) -> None:
        for schema in ("attendance", "employee", "approval", "a" * 200, ""):
            assert 0 <= schema_lock_key(schema) <= 0x7FFF_FFFF


class TestItExcludes:
    async def test_a_lock_another_process_holds_is_not_handed_out(self) -> None:
        schema = _unique_schema()
        peer = _Peer(schema)
        await peer.take()
        try:
            with pytest.raises(TimeoutError):
                async with cross_service_schema_lock(_dsn(), schema, holder="probe", timeout_s=0.4):
                    pytest.fail("acquired a lock the peer suite is holding")
        finally:
            await peer.release()

    async def test_it_waits_for_the_peer_rather_than_failing_fast(self) -> None:
        """A busy peer is normal, not an error: the wait has to end in the lock, not in a
        failure, or the suites would go red whenever they happened to overlap."""
        schema = _unique_schema()
        peer = _Peer(schema)
        await peer.take()

        async def _let_go_shortly() -> None:
            await asyncio.sleep(0.3)
            await peer.release()

        releaser = asyncio.create_task(_let_go_shortly())
        async with cross_service_schema_lock(_dsn(), schema, holder="probe", timeout_s=30):
            assert await _is_locked(schema), "the block is running without holding the lock"
        await releaser

    async def test_the_wait_is_bounded_and_the_message_names_this_collision(self) -> None:
        """A wait that never ends is a hung CI job, which is worse than a red one — so the
        wait is bounded, and what it says has to be enough to find the other half."""
        schema = _unique_schema()
        peer = _Peer(schema)
        await peer.take()
        try:
            with pytest.raises(TimeoutError) as caught:
                async with cross_service_schema_lock(
                    _dsn(), schema, holder="services/probe/tests", timeout_s=0.4
                ):
                    pytest.fail("acquired a lock the peer suite is holding")
        finally:
            await peer.release()

        message = str(caught.value)
        assert schema in message, "the message must name the schema being contended for"
        assert "services/probe/tests" in message, "the message must name who was waiting"
        assert str(schema_lock_key(schema)) in message, "the message must name the lock"


class TestItAlwaysReleases:
    async def test_the_lock_is_released_when_the_guarded_block_raises(self) -> None:
        """The property that stops one red test becoming a hung suite."""
        schema = _unique_schema()
        with pytest.raises(ZeroDivisionError):
            async with cross_service_schema_lock(_dsn(), schema, holder="probe"):
                raise ZeroDivisionError
        assert not await _is_locked(schema)

    async def test_the_lock_is_released_on_a_clean_exit(self) -> None:
        schema = _unique_schema()
        async with cross_service_schema_lock(_dsn(), schema, holder="probe"):
            assert await _is_locked(schema)
        assert not await _is_locked(schema)


class TestItCannotDeadlockAgainstItself:
    async def test_re_entering_the_same_lock_is_not_a_second_wait(self) -> None:
        """A second acquisition on a second connection would wait on the first forever. The
        inner block is a no-op, and the lock survives it — only the outermost exit drops it."""
        schema = _unique_schema()
        async with cross_service_schema_lock(_dsn(), schema, holder="probe", timeout_s=2):
            async with cross_service_schema_lock(_dsn(), schema, holder="probe", timeout_s=2):
                assert await _is_locked(schema)
            assert await _is_locked(schema), "the inner exit released the outer block's lock"
        assert not await _is_locked(schema)

    async def test_taking_two_locks_out_of_order_is_refused_rather_than_risked(self) -> None:
        """Two locks held at once is how two processes build a cycle. Ascending key order
        makes that impossible, and taking them the other way round is caught here rather than
        by Postgres' deadlock detector at three in the morning."""
        low, high = sorted((_unique_schema(), _unique_schema()), key=schema_lock_key)
        async with cross_service_schema_lock(_dsn(), high, holder="probe"):
            with pytest.raises(RuntimeError, match="ascending key order"):
                async with cross_service_schema_lock(_dsn(), low, holder="probe"):
                    pytest.fail("took a lower-keyed lock while holding a higher one")
        assert not await _is_locked(high)
        assert not await _is_locked(low)

    async def test_ascending_order_is_allowed(self) -> None:
        low, high = sorted((_unique_schema(), _unique_schema()), key=schema_lock_key)
        async with cross_service_schema_lock(_dsn(), low, holder="probe"):
            assert await _is_locked(low)
            async with cross_service_schema_lock(_dsn(), high, holder="probe"):
                assert await _is_locked(high)
        assert not await _is_locked(low)
        assert not await _is_locked(high)
