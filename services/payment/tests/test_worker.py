"""`payment-worker` runs the charge consumers and the stuck-charge sweep together, supervised (D3
round 3). The sweep had no container of its own, so it ran nowhere; it now runs in the worker, and
these tests pin the three properties that make that safe:

* `app.worker.main` starts **both** loops;
* either loop **returning** ends the process (non-zero, so the orchestrator restarts it);
* a sweep tick that **raises** backs off and tries again — it never ends the sweep, and so never
  takes the consumers down with it.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

import app.worker as worker
from app.collections.cron import CollectionExpirySweep
from app.cron import StuckChargeSweep
from app.supervise import LoopExited, run_supervised


def _idle_sweep(**over: Any) -> StuckChargeSweep:
    kwargs: dict[str, Any] = {
        "discovery_factory": None,
        "work_scope": None,
        "provider_factory": lambda: None,
    }
    kwargs.update(over)
    return StuckChargeSweep(**kwargs)


def _idle_expiry_sweep(**over: Any) -> CollectionExpirySweep:
    kwargs: dict[str, Any] = {
        "discovery_factory": None,
        "work_scope": None,
        "provider_factory": lambda: None,
        "ttl": timedelta(days=1),
    }
    kwargs.update(over)
    return CollectionExpirySweep(**kwargs)


@pytest.fixture
def _no_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """`main` asserts the app and outbox roles against a live database before it starts anything;
    these tests are about what it starts."""

    async def _ok(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(worker, "init_engine", lambda cfg: object())
    monkeypatch.setattr(worker, "init_outbox_engine", lambda cfg: None)
    monkeypatch.setattr(worker, "get_outbox_engine", lambda: object())
    monkeypatch.setattr(worker, "assert_app_role", _ok)
    monkeypatch.setattr(worker, "assert_outbox_role", _ok)
    monkeypatch.setattr(worker, "build_sweep", _idle_sweep)
    monkeypatch.setattr(worker, "build_expiry_sweep", _idle_expiry_sweep)


@pytest.mark.usefixtures("_no_database")
async def test_the_worker_runs_the_consumers_and_the_stuck_charge_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    consumer_started = asyncio.Event()
    sweep_started = asyncio.Event()
    expiry_started = asyncio.Event()

    async def _consume() -> None:
        consumer_started.set()
        await asyncio.Event().wait()

    async def _sweep(self: StuckChargeSweep) -> None:
        sweep_started.set()
        await asyncio.Event().wait()

    async def _expiry(self: CollectionExpirySweep) -> None:
        expiry_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(worker.consumers.consumer, "run", _consume)
    monkeypatch.setattr(StuckChargeSweep, "run", _sweep)
    monkeypatch.setattr(CollectionExpirySweep, "run", _expiry)

    task = asyncio.create_task(worker.main())
    await asyncio.wait_for(
        asyncio.gather(consumer_started.wait(), sweep_started.wait(), expiry_started.wait()), 5
    )
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.usefixtures("_no_database")
async def test_a_consumer_that_returns_ends_the_process(monkeypatch: pytest.MonkeyPatch) -> None:
    sweep_cancelled = asyncio.Event()

    async def _consume() -> None:
        return None

    async def _sweep(self: StuckChargeSweep) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            sweep_cancelled.set()

    async def _expiry(self: CollectionExpirySweep) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(worker.consumers.consumer, "run", _consume)
    monkeypatch.setattr(StuckChargeSweep, "run", _sweep)
    monkeypatch.setattr(CollectionExpirySweep, "run", _expiry)

    with pytest.raises(LoopExited, match="consumer"):
        await asyncio.wait_for(worker.main(), timeout=5)
    assert sweep_cancelled.is_set()


async def test_a_poisoned_tick_does_not_end_the_consumers() -> None:
    ticks = 0
    consumer_cancelled = False
    consumer_alive = asyncio.Event()

    class _Poisoned(StuckChargeSweep):
        async def tick(self, now: Any = None) -> Any:
            nonlocal ticks
            ticks += 1
            raise RuntimeError("discovery failed")

    sweep = _Poisoned(
        discovery_factory=None,  # type: ignore[arg-type]
        work_scope=None,  # type: ignore[arg-type]
        poll_interval=0.01,
        max_backoff=0.02,
    )

    async def _consume() -> None:
        nonlocal consumer_cancelled
        consumer_alive.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            consumer_cancelled = True
            raise

    group = asyncio.create_task(run_supervised(consumer=_consume(), stuck_charge=sweep.run()))
    await consumer_alive.wait()
    for _ in range(200):
        if ticks >= 4:
            break
        await asyncio.sleep(0.01)
    assert ticks >= 4
    assert not group.done()
    assert not consumer_cancelled
    group.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await group
    assert consumer_cancelled


def test_bin_worker_runs_the_supervised_worker() -> None:
    script = (Path(__file__).resolve().parents[1] / "bin" / "worker").read_text()
    assert "exec python -m app.worker" in script
