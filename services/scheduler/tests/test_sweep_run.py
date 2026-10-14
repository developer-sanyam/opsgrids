"""`Sweep.run()`'s idle and failure backoff, and its guard against a `tick()` that raises
(ADR-0015 review fixes).

No real broker or database: `tick()` is faked, and `asyncio.sleep` is replaced with a recorder
— the same shape `libs/platform-core/tests/test_outbox_relay_idle_backoff.py` uses for
`OutboxRelay.run()`, which this mirrors on purpose (same idle-backoff contract, same reason).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.cron import Sweep, SweepReport


def _sweep(**overrides: Any) -> Sweep:
    return Sweep(
        discovery_factory=None,  # type: ignore[arg-type]  # tick() is faked; never dereferenced
        work_scope=None,  # type: ignore[arg-type]
        **overrides,
    )


async def _run_and_collect(
    sweep: Sweep,
    monkeypatch: pytest.MonkeyPatch,
    tick_results: list[SweepReport | Exception],
) -> list[float]:
    """Drives `sweep.run()` through one scripted `tick()` outcome per pass, then stops it.

    An `Exception` instance in `tick_results` is raised from `tick()` instead of returned —
    proving `run()` catches it, backs off, and keeps going rather than propagating it.
    """
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

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
    return sleeps


class TestIdleBackoff:
    async def test_consecutive_empty_ticks_double_and_cap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sweep = _sweep()  # defaults: poll_interval=5.0, max_idle_interval=30.0
        sleeps = await _run_and_collect(sweep, monkeypatch, [SweepReport()] * 5)
        assert sleeps == [5.0, 10.0, 20.0, 30.0, 30.0]

    async def test_a_productive_tick_resets_the_idle_interval_and_does_not_sleep(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sweep = _sweep()
        sleeps = await _run_and_collect(
            sweep,
            monkeypatch,
            [SweepReport(), SweepReport(), SweepReport(considered=3, fired=3), SweepReport()],
        )
        # Idle climbs for the first two empty ticks; the productive one sleeps not at all
        # (a backlog drains at full speed, the same trade `OutboxRelay.run()` makes); idle
        # restarts cold on the next empty tick.
        assert sleeps == [5.0, 10.0, 5.0]

    async def test_a_purge_only_tick_counts_as_productive(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sweep = _sweep()
        sleeps = await _run_and_collect(
            sweep, monkeypatch, [SweepReport(), SweepReport(purged=4), SweepReport()]
        )
        assert sleeps == [5.0, 5.0]

    async def test_max_idle_interval_below_poll_interval_is_a_no_op(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sweep = _sweep(poll_interval=10.0, max_idle_interval=1.0)
        sleeps = await _run_and_collect(sweep, monkeypatch, [SweepReport()] * 3)
        assert sleeps == [10.0, 10.0, 10.0]


class TestTickFailureIsCaughtAndBackedOff:
    async def test_a_raising_tick_does_not_kill_the_process(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A discovery failure — a DB restart, a deadlock — must not propagate: this process
        shares an `asyncio.gather` with the event consumer (`app/worker.py`), so an uncaught
        exception here would take that down too."""
        sweep = _sweep(max_backoff=60.0)
        sleeps = await _run_and_collect(
            sweep,
            monkeypatch,
            [RuntimeError("db gone"), RuntimeError("db gone"), SweepReport()],
        )
        # Exponential from poll_interval, doubling on each consecutive failure; the clean
        # tick after resets to the idle path.
        assert sleeps == [5.0, 10.0, 5.0]

    async def test_failure_backoff_is_independent_of_idle_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sweep = _sweep(max_backoff=60.0)
        # One clean empty tick grows idle first, then a run of failures.
        sleeps = await _run_and_collect(
            sweep,
            monkeypatch,
            [SweepReport(), RuntimeError("boom"), RuntimeError("boom"), SweepReport()],
        )
        # sleeps[0] is the idle sleep from the first tick, which also grows idle to 10; the
        # failure backoff then climbs from poll_interval independently of idle state; the
        # final clean tick is empty too, so it resumes the idle ladder exactly where the
        # first tick left it (failures neither grow nor reset idle).
        assert sleeps == [5.0, 5.0, 10.0, 10.0]

    async def test_first_retry_is_capped_when_the_poll_interval_exceeds_the_cap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Backoff starts at min(poll_interval, max_backoff), not at the poll interval: a long
        poll interval must not make the first retry of a transient error equally long."""
        sweep = _sweep(poll_interval=3600.0, max_backoff=30.0)
        sleeps = await _run_and_collect(
            sweep, monkeypatch, [RuntimeError("blip"), RuntimeError("blip"), SweepReport()]
        )
        assert sleeps == [30.0, 30.0, 3600.0]
