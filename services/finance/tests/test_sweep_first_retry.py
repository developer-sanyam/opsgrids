"""A failed `FilingReclaimSweep` tick retries after a short wait, not after a whole poll interval.

Backoff used to start at `poll_interval`, so a sweep configured to poll rarely waited just as long
before its first retry of a transient error (a one-second database blip). It now starts at
`min(poll_interval, max_backoff)`, the same rule `platform_core.supervise.GuardedSweep` applies.
No database: `tick()` and `_sleep()` are faked, so the clock never really runs.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.cron import FilingReclaimSweep


async def _drive(
    sweep: FilingReclaimSweep, outcomes: list[bool], monkeypatch: pytest.MonkeyPatch
) -> list[float]:
    """Run the loop through one scripted tick per entry (True = succeeds), returning every sleep."""
    sleeps: list[float] = []
    remaining = list(outcomes)

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if not remaining:
            sweep.stop()

    async def fake_tick(now: Any = None) -> Any:
        ok = remaining.pop(0)
        if not ok:
            raise RuntimeError("database restarted")
        return type("R", (), {"acted": 0, "failed": 0})()

    monkeypatch.setattr(sweep, "_sleep", fake_sleep)
    monkeypatch.setattr(sweep, "tick", fake_tick)
    await sweep.run()
    return sleeps


def _sweep(**kw: Any) -> FilingReclaimSweep:
    return FilingReclaimSweep(
        discovery_factory=None,  # type: ignore[arg-type]
        work_scope=None,  # type: ignore[arg-type]
        **kw,
    )


async def test_first_retry_is_capped_when_the_poll_interval_is_long(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sweep = _sweep(poll_interval=3600.0, max_backoff=30.0)
    assert await _drive(sweep, [False, False, False], monkeypatch) == [30.0, 30.0, 30.0]


async def test_backoff_still_doubles_up_to_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    sweep = _sweep(poll_interval=5.0, max_backoff=20.0)
    assert await _drive(sweep, [False, False, False, False], monkeypatch) == [
        5.0,
        10.0,
        20.0,
        20.0,
    ]


async def test_a_clean_tick_resets_the_streak_to_the_small_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sweep = _sweep(poll_interval=3600.0, max_backoff=30.0)
    assert await _drive(sweep, [False, True, False], monkeypatch) == [30.0, 3600.0, 30.0]
