"""`EscalationSweep.run()`'s guard against a `tick()` that raises (memory-headroom slice,
2026-09-23 fix round — the poisoned-step guard `scheduler.app.cron.Sweep.run()` and
`notification.app.cron.CampaignDriver` use, via `platform_core.supervise.GuardedSweep`, added
here because `app/worker.py` shares one supervised group between this sweep and the hierarchy/
regularization consumer: an uncaught exception from discovery must not take that consumer down).

No real broker or database: `tick()` is faked and `asyncio.sleep` is replaced with a recorder —
`services/scheduler/tests/test_sweep_run.py`'s own shape, mirrored on purpose.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.cron import EscalationSweep, SweepReport


def _sweep(**overrides: Any) -> EscalationSweep:
    return EscalationSweep(
        discovery_factory=None,  # type: ignore[arg-type]  # tick() is faked; never dereferenced
        work_scope=None,  # type: ignore[arg-type]
        **overrides,
    )


async def _run_and_collect(
    sweep: EscalationSweep,
    monkeypatch: pytest.MonkeyPatch,
    tick_results: list[SweepReport | Exception],
) -> list[float]:
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


class TestTickFailureIsCaughtAndBackedOff:
    async def test_a_raising_tick_does_not_kill_the_process(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A discovery failure — a DB restart, a deadlock — must not propagate: this sweep
        shares a supervised group with the hierarchy/regularization consumer (`app/worker.py`),
        so an uncaught exception here would take that consumer down too."""
        sweep = _sweep(poll_interval=5.0)
        sleeps = await _run_and_collect(
            sweep,
            monkeypatch,
            [RuntimeError("db gone"), RuntimeError("db gone"), SweepReport()],
        )
        assert sleeps == [5.0, 10.0, 5.0]

    async def test_a_clean_tick_after_failures_resumes_the_ordinary_poll_interval(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sweep = _sweep(poll_interval=5.0)
        sleeps = await _run_and_collect(
            sweep,
            monkeypatch,
            [RuntimeError("boom"), SweepReport(), SweepReport()],
        )
        assert sleeps == [5.0, 5.0, 5.0]

    async def test_cancelled_error_is_not_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sweep = _sweep(poll_interval=5.0)

        async def fake_tick(now: Any = None) -> SweepReport:
            raise asyncio.CancelledError()

        monkeypatch.setattr(sweep, "tick", fake_tick)
        with pytest.raises(asyncio.CancelledError):
            await sweep.run()
