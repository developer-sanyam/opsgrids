"""`PurgeSweep.run()`'s pacing (memory-headroom slice, review round 3, MEDIUM).

The loop used to sleep only when a tick *considered nothing*. A tick that found work and got
nowhere — one asset whose object-store delete fails every time, or rows another sweeper's lock
keeps skipping — re-ran immediately, with no sleep, forever: ~740 ticks a second of discovery
queries and failing deletes, in a loop that now shares an event loop and a connection pool with the
API's own request traffic. Progress (something actually purged or removed) is the only thing that
earns an immediate re-run.

No real database or store: `tick()` is faked and `asyncio.sleep` is replaced with a recorder — the
same "fake the loop, drive the real code" shape `services/storefront/tests/test_domain_sweep_run.py`
uses.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest

from app.cron import PurgeSweep, SweepReport
from platform_core.supervise import reset_sweep_health


@pytest.fixture(autouse=True)
def _clean_sweep_registry():  # type: ignore[no-untyped-def]
    reset_sweep_health()
    yield
    reset_sweep_health()


def _sweep(**overrides: Any) -> PurgeSweep:
    return PurgeSweep(
        discovery_factory=None,  # type: ignore[arg-type]  # tick() is faked; never dereferenced
        work_scope=None,  # type: ignore[arg-type]
        store=None,  # type: ignore[arg-type]
        scanner=None,  # type: ignore[arg-type]
        retention=timedelta(days=30),
        **overrides,
    )


async def _run(
    sweep: PurgeSweep,
    monkeypatch: pytest.MonkeyPatch,
    ticks: list[SweepReport | Exception],
) -> list[float]:
    """Runs `sweep` until the scripted ticks are exhausted; returns every sleep it asked for."""
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    remaining = list(ticks)

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


FAILING = SweepReport(considered=1, failed=1)
LOCKED_BY_ANOTHER_SWEEPER = SweepReport(considered=1, skipped=1)
IDLE = SweepReport()
PROGRESS = SweepReport(considered=3, purged=3)


async def test_a_permanently_failing_asset_sleeps_between_ticks_instead_of_spinning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sweep = _sweep(poll_interval=300.0, max_idle_interval=1800.0)
    sleeps = await _run(sweep, monkeypatch, [FAILING] * 5)
    # One sleep per tick (the old loop made none), doubling like an idle tick and capped.
    assert sleeps == [300.0, 600.0, 1200.0, 1800.0, 1800.0]


async def test_rows_another_sweeper_keeps_locking_also_back_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sweep = _sweep(poll_interval=300.0, max_idle_interval=1800.0)
    sleeps = await _run(sweep, monkeypatch, [LOCKED_BY_ANOTHER_SWEEPER] * 3)
    assert sleeps == [300.0, 600.0, 1200.0]


async def test_progress_earns_an_immediate_rerun_and_stopping_progress_backs_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A backlog larger than `batch_size` drains back to back; the moment a tick makes no
    progress the loop sleeps, starting again from the poll interval."""
    sweep = _sweep(poll_interval=300.0, max_idle_interval=1800.0)
    sleeps = await _run(sweep, monkeypatch, [PROGRESS, PROGRESS, FAILING, PROGRESS, IDLE])
    assert sleeps == [300.0, 300.0]


async def test_an_idle_tick_backs_off_as_it_always_did(monkeypatch: pytest.MonkeyPatch) -> None:
    sweep = _sweep(poll_interval=300.0, max_idle_interval=1800.0)
    sleeps = await _run(sweep, monkeypatch, [IDLE, IDLE, IDLE])
    assert sleeps == [300.0, 600.0, 1200.0]


async def test_a_dry_run_orphan_report_is_not_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    """`orphan_would_delete` is a report, not a deletion: nothing changed, so an immediate re-run
    would find the same objects again."""
    sweep = _sweep(poll_interval=300.0, max_idle_interval=1800.0)
    sleeps = await _run(
        sweep, monkeypatch, [SweepReport(orphan_considered=2, orphan_would_delete=2)]
    )
    assert sleeps == [300.0]


async def test_a_raising_tick_first_retries_after_the_cap_not_after_the_poll_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sweep = _sweep(poll_interval=86_400.0, max_backoff=300.0, max_idle_interval=86_400.0)
    sleeps = await _run(sweep, monkeypatch, [RuntimeError("blip"), IDLE])
    assert sleeps == [300.0, 86_400.0]


async def test_health_counts_a_tick_where_every_attempt_failed_and_clears_on_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`sweep_consecutive_failures` must climb for the always-failing asset — that is the alert."""
    sweep = _sweep(poll_interval=300.0)
    streaks: list[int] = []
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        streaks.append(sweep.health.consecutive_failures)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    ticks = [FAILING, FAILING, SweepReport(considered=2, purged=1, failed=1), IDLE]

    async def fake_tick(now: Any = None) -> SweepReport:
        result = ticks.pop(0)
        if not ticks:
            sweep.stop()
        return result

    monkeypatch.setattr(sweep, "tick", fake_tick)
    await sweep.run()
    assert streaks == [1, 2, 0]  # progress (a purge) makes the third tick a success, no sleep
    assert sweep.health.consecutive_failures == 0
    assert sweep.health.last_success_ts > 1_700_000_000
    assert sweep.health.name == "document.sweep"
