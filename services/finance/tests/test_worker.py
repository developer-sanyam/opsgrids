"""`finance-worker` runs the filing consumer and the filing-reclaim sweep together, supervised
(D3 round 3). The sweep had no container of its own, so it ran nowhere; it now runs in the worker,
and these tests pin the three properties that make that safe:

* `app.worker.main` starts **both** loops;
* either loop **returning** ends the process (non-zero, so the orchestrator restarts it) rather
  than leaving half a worker running;
* a sweep tick that **raises** backs off and tries again — it never ends the sweep, and so never
  takes the consumer down with it.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import pytest

import app.worker as worker
from app.cron import FilingReclaimSweep
from app.supervise import LoopExited, run_supervised


@pytest.fixture
def _no_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """`main` asserts the app and outbox roles against a live database before it starts anything;
    these tests are about what it starts, so the start-up checks are stubbed out."""

    async def _ok(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(worker, "init_engine", lambda cfg: object())
    monkeypatch.setattr(worker, "init_outbox_engine", lambda cfg: None)
    monkeypatch.setattr(worker, "get_outbox_engine", lambda: object())
    monkeypatch.setattr(worker, "get_outbox_sessionmaker", lambda: object())
    monkeypatch.setattr(worker, "assert_app_role", _ok)
    monkeypatch.setattr(worker, "assert_outbox_role", _ok)


class _Started:
    def __init__(self) -> None:
        self.consumer = asyncio.Event()
        self.sweep = asyncio.Event()


@pytest.mark.usefixtures("_no_database")
async def test_the_worker_runs_the_consumer_and_the_reclaim_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = _Started()

    async def _consume() -> None:
        started.consumer.set()
        await asyncio.Event().wait()

    async def _sweep(self: FilingReclaimSweep) -> None:
        started.sweep.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(worker.consumers.consumer, "run", _consume)
    monkeypatch.setattr(FilingReclaimSweep, "run", _sweep)

    task = asyncio.create_task(worker.main())
    await asyncio.wait_for(asyncio.gather(started.consumer.wait(), started.sweep.wait()), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.usefixtures("_no_database")
async def test_a_consumer_that_returns_ends_the_process(monkeypatch: pytest.MonkeyPatch) -> None:
    sweep_cancelled = asyncio.Event()

    async def _consume() -> None:
        return None  # a bug: a consumer loop is meant to run forever

    async def _sweep(self: FilingReclaimSweep) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            sweep_cancelled.set()

    monkeypatch.setattr(worker.consumers.consumer, "run", _consume)
    monkeypatch.setattr(FilingReclaimSweep, "run", _sweep)

    with pytest.raises(LoopExited, match="consumer"):
        await asyncio.wait_for(worker.main(), timeout=5)
    assert sweep_cancelled.is_set(), "the sweep was left running after the consumer ended"


async def test_a_poisoned_tick_does_not_end_the_consumer() -> None:
    """The real sweep loop, with a tick that always raises (a discovery query failing): it backs
    off and ticks again, and the consumer beside it is never cancelled."""
    ticks = 0
    consumer_alive = asyncio.Event()
    consumer_cancelled = False

    class _Poisoned(FilingReclaimSweep):
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

    group = asyncio.create_task(run_supervised(consumer=_consume(), filing_reclaim=sweep.run()))
    await consumer_alive.wait()
    for _ in range(200):
        if ticks >= 4:
            break
        await asyncio.sleep(0.01)
    assert ticks >= 4, "the sweep stopped ticking after a failed tick"
    assert not group.done(), "a failed tick ended the supervised group"
    assert sweep.consecutive_failures >= 4
    assert not consumer_cancelled

    group.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await group
    assert consumer_cancelled  # cancellation of the process reaches every loop


def test_bin_worker_runs_the_supervised_worker() -> None:
    from pathlib import Path

    script = (Path(__file__).resolve().parents[1] / "bin" / "worker").read_text()
    assert "exec python -m app.worker" in script
