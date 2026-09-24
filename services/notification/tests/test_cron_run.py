"""`CampaignDriver.run()`'s guard against a `tick()` that raises (ADR-0013's memory-budget
erratum, 2026-09-23 — the same poisoned-step guard `scheduler.app.cron.Sweep.run()` uses,
ADR-0015 review finding, added here because `app/worker.py` now shares one `asyncio.gather`
between this driver and both event consumers: an uncaught exception from discovery must not take
the whole process down with it).

No real broker or database: `tick()` is faked and `asyncio.sleep` is replaced with a recorder —
`services/scheduler/tests/test_sweep_run.py`'s own shape, mirrored on purpose.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.cron import CampaignDriver, SweepReport


def _driver(**overrides: Any) -> CampaignDriver:
    return CampaignDriver(
        discovery_factory=None,  # type: ignore[arg-type]  # tick() is faked; never dereferenced
        work_scope=None,  # type: ignore[arg-type]
        **overrides,
    )


async def _run_and_collect(
    driver: CampaignDriver,
    monkeypatch: pytest.MonkeyPatch,
    tick_results: list[SweepReport | Exception],
) -> list[float]:
    """Drives `driver.run()` through one scripted `tick()` outcome per pass, then stops it.

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
            driver.stop()
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(driver, "tick", fake_tick)

    await driver.run()
    return sleeps


class TestTickFailureIsCaughtAndBackedOff:
    async def test_a_raising_tick_does_not_kill_the_process(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A discovery failure — a DB restart, a deadlock — must not propagate: this driver
        shares an `asyncio.gather` with both event consumers (`app/worker.py`), so an uncaught
        exception here would take those down too."""
        driver = _driver(poll_interval=5.0, max_backoff_seconds=60.0)
        sleeps = await _run_and_collect(
            driver,
            monkeypatch,
            [RuntimeError("db gone"), RuntimeError("db gone"), SweepReport()],
        )
        # Exponential from poll_interval, doubling on each consecutive failure; the clean tick
        # after resets to the ordinary poll_interval sleep.
        assert sleeps == [5.0, 10.0, 5.0]

    async def test_backoff_is_capped_at_max_backoff_seconds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        driver = _driver(poll_interval=5.0, max_backoff_seconds=15.0)
        sleeps = await _run_and_collect(
            driver,
            monkeypatch,
            [
                RuntimeError("boom"),
                RuntimeError("boom"),
                RuntimeError("boom"),
                SweepReport(),
            ],
        )
        # 5, 10, 15 (capped — would be 20 uncapped), then the clean tick's ordinary sleep.
        assert sleeps == [5.0, 10.0, 15.0, 5.0]

    async def test_a_clean_tick_after_failures_resumes_the_ordinary_poll_interval(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        driver = _driver(poll_interval=5.0, max_backoff_seconds=60.0)
        sleeps = await _run_and_collect(
            driver,
            monkeypatch,
            [RuntimeError("boom"), SweepReport(), SweepReport()],
        )
        assert sleeps == [5.0, 5.0, 5.0]
