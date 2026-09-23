"""`DomainSweep.run()`'s guard against a `tick()` that raises (memory-headroom slice, fix round
2): this sweep now also runs as a supervised background task inside the `storefront` API
process itself, not only its own `storefront-cron` container, so an uncaught discovery failure
must back off and retry rather than take the API down with it.

No real database: `tick()` is faked and `asyncio.wait_for` is replaced with a recorder — the
same "fake the loop, drive the real supervision code" shape
`libs/platform-core/tests/test_outbox_relay_idle_backoff.py` and
`services/approval/tests/test_sweep_run.py` use for their own sweeps.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.cron import DomainSweep, SweepReport


def _sweep(**overrides: Any) -> DomainSweep:
    return DomainSweep(
        discovery_factory=None,  # type: ignore[arg-type]  # tick() is faked; never dereferenced
        work_scope=None,  # type: ignore[arg-type]
        **overrides,
    )


async def _run_and_collect(
    sweep: DomainSweep,
    monkeypatch: pytest.MonkeyPatch,
    tick_results: list[SweepReport | Exception],
) -> list[float]:
    waits: list[float] = []

    async def fake_wait_for(aw: Any, timeout: float) -> None:  # noqa: ASYNC109 - stands in for asyncio.wait_for's own signature
        waits.append(timeout)
        aw.close()  # dispose of the un-awaited `self._stop_event.wait()` coroutine cleanly
        # Nothing ever sets the event before the timeout in this test: `wait_for` always raises
        # `TimeoutError`, which `run()`'s own `with suppress(TimeoutError):` swallows — the loop
        # then re-checks `self._stop_event.is_set()`, which `fake_tick` below has already set
        # directly once the script is exhausted.
        raise TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    remaining = list(tick_results)

    async def fake_tick(now: Any = None) -> SweepReport:
        result = remaining.pop(0)
        if not remaining:
            sweep.stop()
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(sweep, "tick", fake_tick)

    await sweep.run()
    return waits


class TestTickFailureIsCaughtAndBackedOff:
    async def test_a_raising_tick_does_not_kill_the_process(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sweep = _sweep(poll_interval=5.0, max_backoff=60.0)
        waits = await _run_and_collect(
            sweep,
            monkeypatch,
            [RuntimeError("db gone"), RuntimeError("db gone"), SweepReport()],
        )
        assert waits == [5.0, 10.0, 5.0]

    async def test_backoff_is_capped_at_max_backoff(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sweep = _sweep(poll_interval=5.0, max_backoff=15.0)
        waits = await _run_and_collect(
            sweep,
            monkeypatch,
            [RuntimeError("x"), RuntimeError("x"), RuntimeError("x"), SweepReport()],
        )
        assert waits == [5.0, 10.0, 15.0, 5.0]

    async def test_a_clean_tick_after_failures_resumes_the_ordinary_poll_interval(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sweep = _sweep(poll_interval=5.0, max_backoff=60.0)
        waits = await _run_and_collect(
            sweep,
            monkeypatch,
            [RuntimeError("boom"), SweepReport(), SweepReport()],
        )
        assert waits == [5.0, 5.0, 5.0]

    async def test_cancelled_error_is_not_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sweep = _sweep(poll_interval=5.0)

        async def fake_tick(now: Any = None) -> SweepReport:
            raise asyncio.CancelledError()

        monkeypatch.setattr(sweep, "tick", fake_tick)
        with pytest.raises(asyncio.CancelledError):
            await sweep.run()
