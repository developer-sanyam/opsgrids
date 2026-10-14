"""A failed payment sweep tick retries after a short wait, not after a whole poll interval.

Covers `StuckChargeSweep`, `ChargePoller` and `CollectionExpirySweep`. Backoff used to start at
`poll_interval`, so a sweep configured to poll rarely waited just as long before its first retry
of a transient error. It now starts at `min(poll_interval, max_backoff)`, the rule
`platform_core.supervise.GuardedSweep` applies. No database: `tick()` and `_sleep()` are faked.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest

from app.checkout.cron import ChargePoller
from app.collections.cron import CollectionExpirySweep
from app.cron import StuckChargeSweep

Factory = Callable[..., Any]


def _stuck(**kw: Any) -> Any:
    return StuckChargeSweep(discovery_factory=None, work_scope=None, **kw)  # type: ignore[arg-type]


def _poller(**kw: Any) -> Any:
    return ChargePoller(discovery_factory=None, work_scope=None, **kw)  # type: ignore[arg-type]


def _expiry(**kw: Any) -> Any:
    return CollectionExpirySweep(
        discovery_factory=None,  # type: ignore[arg-type]
        work_scope=None,  # type: ignore[arg-type]
        ttl=timedelta(hours=1),
        **kw,
    )


FACTORIES = [
    pytest.param(_stuck, id="stuck-charge-sweep"),
    pytest.param(_poller, id="charge-poller"),
    pytest.param(_expiry, id="collection-expiry-sweep"),
]


async def _drive(sweep: Any, outcomes: list[bool], monkeypatch: pytest.MonkeyPatch) -> list[float]:
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
        # Every report field the loops read is zero: a quiet pass.
        return type("R", (), {"__getattr__": lambda self, name: 0})()

    monkeypatch.setattr(sweep, "_sleep", fake_sleep)
    monkeypatch.setattr(sweep, "tick", fake_tick)
    await sweep.run()
    return sleeps


@pytest.mark.parametrize("make", FACTORIES)
async def test_first_retry_is_capped_when_the_poll_interval_is_long(
    make: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    sweep = make(poll_interval=3600.0, max_backoff=30.0)
    assert await _drive(sweep, [False, False, False], monkeypatch) == [30.0, 30.0, 30.0]


@pytest.mark.parametrize("make", FACTORIES)
async def test_backoff_still_doubles_up_to_the_cap(
    make: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    sweep = make(poll_interval=5.0, max_backoff=20.0)
    assert await _drive(sweep, [False, False, False, False], monkeypatch) == [
        5.0,
        10.0,
        20.0,
        20.0,
    ]


@pytest.mark.parametrize("make", FACTORIES)
async def test_a_clean_tick_resets_the_streak_to_the_small_wait(
    make: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    sweep = make(poll_interval=3600.0, max_backoff=30.0)
    assert await _drive(sweep, [False, True, False], monkeypatch) == [30.0, 3600.0, 30.0]
