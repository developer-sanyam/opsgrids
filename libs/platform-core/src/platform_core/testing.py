"""Test-only support that needs the real cluster. Nothing in a request, consumer, relay or
cron path imports this module — it exists for `tests/` trees, and a service that has no
cross-service test pays nothing for it.

Why it is here at all
---------------------
`make test-services` fans the per-service pytest runs out across cores (`tools/fan-out.sh`).
That is safe because a service's fixtures truncate only its own schema — *except* where a test
deliberately reaches across a service boundary. Two modules do:

* `services/approval/tests/test_attendance_round_trip.py` drives attendance's real write and
  consume paths (through `services/attendance/tests/regularization_cli.py`), so it writes and
  reads rows in the **attendance** schema.
* `services/approval/tests/test_cross_service_contract.py` drives employee's real write path
  and its real `/internal/v1/hierarchy` route (through `services/employee/tests/emit_events.py`,
  `bump_employee.py` and `serve_hierarchy.py`), so it writes and reads rows in the **employee**
  schema.

Meanwhile `services/attendance/tests/conftest.py` and `services/employee/tests/conftest.py`
open each of their own tests with a `TRUNCATE` of that same schema. Run the two suites at the
same time and each one deletes rows the other is halfway through using: the observed shape is
`TypeError: 'NoneType' object is not subscriptable`, `IndexError: list index out of range`, a
subprocess helper exiting non-zero — and, from the truncating side, a genuine
`DeadlockDetectedError` as the `TRUNCATE`'s `AccessExclusiveLock` meets the other process's
open read.

`cross_service_schema_lock` is the mutual exclusion that fixes it. Both sides name the *schema*
they are contending for rather than a bare number, so the lock says what it protects, and each
pair serialises only against itself — the other ~31 services never take it and stay parallel.

If a new service ever grows a test that reaches into a sibling's schema, it takes this lock on
that sibling's schema, and the sibling's truncating fixture takes it too. Nothing detects the
omission; it shows up as a flake in whichever suite happens to lose the race.
"""

from __future__ import annotations

import os
import zlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from sqlalchemy import make_url, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

#: The namespace half of every advisory lock taken here: the ASCII bytes ``XSVC``
#: ("cross-service") read as a big-endian int32, i.e. ``int.from_bytes(b"XSVC", "big")``.
#: Postgres' two-argument ``pg_advisory_lock(int4, int4)`` keys the whole family under it, so
#: anything else in this cluster that reaches for an advisory lock — application code, a
#: migration, a developer at a psql prompt — cannot collide with these by accident.
CROSS_SERVICE_LOCK_NAMESPACE = 0x58535643

#: How long to wait for the peer suite before giving up. A cross-service test spawns several
#: subprocesses and can legitimately hold the lock for tens of seconds, so this is generous;
#: it exists only so that a lock leaked by something outside this helper fails the job with an
#: explanation instead of hanging CI until the runner's own timeout kills it.
DEFAULT_LOCK_TIMEOUT_S = 180.0

#: SQLSTATE 55P03, `lock_not_available` — what `lock_timeout` raises when the wait runs out.
_LOCK_NOT_AVAILABLE = "55P03"

#: The database `make infra` creates and every developer's stack shares, which a test that
#: truncates tables must never be pointed at by accident.
SHARED_DEV_DATABASE = "opsgrids"


def is_shared_dev_database(dsn: str) -> bool:
    """Whether `dsn` names the one database a destructive test must refuse to touch.

    That is the developer stack's shared `opsgrids`. The CI workflow's database is *also* called
    `opsgrids`, because the workflow's postgres service container is created with that name — but
    it is the job's own throwaway container, created for this run and discarded with it, so there
    is nothing shared there to protect. A guard that compared the name alone failed every test
    behind it on every CI run, which says nothing about the code under test.

    The exemption keys on `GITHUB_ACTIONS=true`, the variable GitHub's runners set. Nothing stops
    a developer setting it too — in a shell, or in the `.env` the Makefile exports into every test
    process — and that would lift the guard against their own shared database. This is a guard
    against accidents (a stray DSN, a forgotten override), not a security boundary, and it makes
    no attempt to prove it is running on a runner. `ENVIRONMENT=ci` is deliberately *not* the key:
    developers set that by hand to reproduce CI, which is exactly when the guard must still hold.
    """
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return False
    return make_url(dsn).database == SHARED_DEV_DATABASE


#: Locks this *process* currently holds, key -> nesting depth. Two purposes, both about not
#: deadlocking against ourselves: a re-entrant acquisition of a lock we already hold is a no-op
#: rather than a second wait on a second connection (which would block forever), and locks must
#: be acquired in ascending key order, so two processes that ever do need two of these cannot
#: build a cycle. See `_check_ordering`.
#:
#: Per process, deliberately, and it assumes one logical holder at a time — which is what a
#: pytest process is, since it runs one test at a time. Two *concurrent tasks* in one process
#: asking for the same lock would both be let through rather than one waiting on the other.
#: That is the wrong tool for that job; this one is for excluding other processes.
_held: dict[int, int] = {}


def schema_lock_key(schema: str) -> int:
    """The advisory-lock key for one Postgres schema, derived from its name.

    CRC32 of the name, masked to a non-negative int32 so it fits `pg_advisory_lock(int4, int4)`.
    Derived rather than assigned so that both sides of a collision — the service that owns the
    schema and the sibling suite that reaches into it — compute the same key from the one thing
    they already agree on, with no shared table of magic numbers to drift.
    """
    return int(zlib.crc32(schema.encode()) & 0x7FFF_FFFF)


def _check_ordering(key: int, schema: str) -> None:
    if _held and key < max(_held):
        held = ", ".join(str(k) for k in sorted(_held))
        raise RuntimeError(
            f"cross-service test lock for schema {schema!r} (key {key}) requested while this "
            f"process already holds {held}. These must be taken in ascending key order or two "
            f"suites can deadlock on each other; acquire them in one nested block, outermost "
            f"first, or release the one you hold before taking this one."
        )


@asynccontextmanager
async def cross_service_schema_lock(
    dsn: str,
    schema: str,
    *,
    holder: str,
    timeout_s: float = DEFAULT_LOCK_TIMEOUT_S,
) -> AsyncIterator[None]:
    """Serialise this block against any other test process contending for `schema`.

    Session-level (`pg_advisory_lock`), not transaction-level: the callers hold it across many
    transactions and across subprocesses that open their own connections, so a lock that ended
    at the next `COMMIT` would protect nothing.

    Released reliably. The lock lives on a connection this context manager owns and nothing
    else uses, taken from a `NullPool` engine, and both the `pg_advisory_unlock` and the close
    are in `finally`. A failing test, a raising fixture and a hard `KeyboardInterrupt` all end
    the connection, and Postgres drops a session-level advisory lock when its session goes —
    a leaked lock would turn one red test into a hung suite, which is worse.

    Waits rather than fails fast, but not forever: `lock_timeout` bounds the wait and the
    resulting error names this collision, which service is waiting, and where to read about it.

    Args:
        dsn: any DSN into the shared cluster. Advisory locks are per *database*, not per schema
            or role, so the waiting service's own app DSN is the right thing to pass.
        schema: the schema being contended for — `"attendance"`, `"employee"`.
        holder: who is waiting, for the timeout message. A suite name, e.g.
            `"services/approval/tests/test_attendance_round_trip.py"`.
        timeout_s: bound on the wait.
    """
    key = schema_lock_key(schema)
    _check_ordering(key, schema)

    if key in _held:  # already ours; re-entering is not a second wait
        _held[key] += 1
        try:
            yield
        finally:
            _held[key] -= 1
        return

    # AUTOCOMMIT, so `SET lock_timeout` is a *session* setting that survives rather than a
    # transaction-scoped one that reverts, and so this connection does not sit idle-in-
    # transaction — holding a snapshot open — for however long the guarded block runs.
    engine = create_async_engine(dsn, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    try:
        connection = await engine.connect()
        try:
            # Milliseconds, and an integer: `SET lock_timeout` takes no float.
            await connection.execute(text(f"SET lock_timeout = {int(timeout_s * 1000)}"))
            try:
                await connection.execute(
                    text("SELECT pg_advisory_lock(:ns, :key)"),
                    {"ns": CROSS_SERVICE_LOCK_NAMESPACE, "key": key},
                )
            except DBAPIError as exc:
                if getattr(exc.orig, "sqlstate", None) != _LOCK_NOT_AVAILABLE:
                    raise
                raise TimeoutError(
                    f"{holder} waited {timeout_s:g}s for the cross-service test lock on schema "
                    f"{schema!r} (advisory key {CROSS_SERVICE_LOCK_NAMESPACE}/{key}) and gave "
                    f"up. That lock is held while one suite drives another service's schema; "
                    f"whoever holds it should have released it when its test ended. See "
                    f"`platform_core.testing` and the `test-services` comment in the Makefile."
                ) from exc

            _held[key] = 1
            try:
                yield
            finally:
                _held.pop(key, None)
                # Suppressed, because closing the connection below releases the lock anyway and
                # this is the tidy path, not the guarantee. If the block failed in a way that
                # also broke the connection, an exception raised here would replace the real
                # failure with a plumbing one — the test would report the wrong cause.
                with suppress(Exception):
                    await connection.execute(
                        text("SELECT pg_advisory_unlock(:ns, :key)"),
                        {"ns": CROSS_SERVICE_LOCK_NAMESPACE, "key": key},
                    )
        finally:
            await connection.close()
    finally:
        await engine.dispose()
