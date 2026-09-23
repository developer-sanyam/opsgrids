"""`app.main`'s reconcile-sweep lifespan hooks (memory-headroom slice, fix round 2): the sweep
starts as a background task when `Settings.run_sweeps_in_api` is on, stays off otherwise, and a
started sweep is cleanly cancelled on shutdown.

No real database: every DB-role assertion and engine constructor these hooks call is faked to a
no-op, the same wiring-test shape `services/notification/tests/test_worker_wiring.py` and
`services/approval/tests/test_worker_wiring.py` use for their own worker processes.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI

from app import main


class _FakeSettings:
    def __init__(self, *, run_sweeps_in_api: bool) -> None:
        self.run_sweeps_in_api = run_sweeps_in_api
        self.environment = "local"
        self.reconcile_interval_seconds = 86_400.0


def _patch_common(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(main, "init_outbox_engine", lambda cfg: None)
    monkeypatch.setattr(main, "get_outbox_engine", lambda: object())
    monkeypatch.setattr(main, "get_outbox_sessionmaker", lambda: object())
    monkeypatch.setattr(main, "assert_outbox_role", _noop)


async def test_sweep_is_not_started_when_the_setting_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(main, "settings", lambda: _FakeSettings(run_sweeps_in_api=False))
    app = FastAPI()
    await main._start_reconcile_sweep(app)
    assert app.state.reconcile_sweep_task is None
    await main._stop_reconcile_sweep(app)  # must not raise on a task that was never started


async def test_sweep_starts_as_a_background_task_when_the_setting_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(main, "settings", lambda: _FakeSettings(run_sweeps_in_api=True))

    started = asyncio.Event()

    async def fake_run(self: main.ReconcileSweep) -> None:
        started.set()
        await asyncio.sleep(10)

    monkeypatch.setattr(main.ReconcileSweep, "run", fake_run)

    app = FastAPI()
    await main._start_reconcile_sweep(app)
    assert app.state.reconcile_sweep_task is not None
    await asyncio.wait_for(started.wait(), timeout=1)
    assert not app.state.reconcile_sweep_task.done()

    await main._stop_reconcile_sweep(app)
    assert app.state.reconcile_sweep_task.done()


async def test_a_sweep_that_fails_every_tick_does_not_propagate_out_of_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of `GuardedSweep`: even a sweep that cannot do its job at all must not
    take the API down with it. `start_background_sweep` returns immediately — the failing loop
    runs in the background, backing off on its own — so this asserts startup itself completes."""
    _patch_common(monkeypatch)
    monkeypatch.setattr(main, "settings", lambda: _FakeSettings(run_sweeps_in_api=True))

    async def fake_tick(self: main.ReconcileSweep) -> None:
        raise RuntimeError("db gone")

    monkeypatch.setattr(main.ReconcileSweep, "tick", fake_tick)

    app = FastAPI()
    await main._start_reconcile_sweep(app)  # must return, not hang or raise
    assert app.state.reconcile_sweep_task is not None
    await main._stop_reconcile_sweep(app)
