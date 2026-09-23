"""`app.main`'s domain-sweep lifespan hooks (memory-headroom slice, fix round 2): the sweep
starts as a background task when `Settings.run_sweeps_in_api` is on, stays off otherwise, and a
started sweep is cleanly cancelled on shutdown.

No real database: `assert_directory_role_configured` and the directory sessionmaker are faked,
the same wiring-test shape `services/operations/tests/test_main_sweep_wiring.py` uses.
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
        self.domain_sweep_batch_size = 20
        self.domain_sweep_poll_interval_seconds = 60.0


def _patch_common(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "assert_directory_role_configured", lambda cfg: None)
    monkeypatch.setattr(main, "get_directory_sessionmaker", lambda: object())


async def test_sweep_is_not_started_when_the_setting_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(main, "settings", lambda: _FakeSettings(run_sweeps_in_api=False))
    app = FastAPI()
    await main._startup(app)
    assert app.state.domain_sweep_task is None
    await main._shutdown(app)  # must not raise on a task that was never started


async def test_sweep_starts_as_a_background_task_when_the_setting_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(main, "settings", lambda: _FakeSettings(run_sweeps_in_api=True))

    started = asyncio.Event()

    async def fake_run(self: main.DomainSweep) -> None:
        started.set()
        await asyncio.sleep(10)

    monkeypatch.setattr(main.DomainSweep, "run", fake_run)

    app = FastAPI()
    await main._startup(app)
    assert app.state.domain_sweep_task is not None
    await asyncio.wait_for(started.wait(), timeout=1)
    assert not app.state.domain_sweep_task.done()

    await main._shutdown(app)
    assert app.state.domain_sweep_task.done()


async def test_a_sweep_that_fails_every_tick_does_not_propagate_out_of_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(main, "settings", lambda: _FakeSettings(run_sweeps_in_api=True))

    async def fake_tick(self: main.DomainSweep, now: Any = None) -> None:
        raise RuntimeError("db gone")

    monkeypatch.setattr(main.DomainSweep, "tick", fake_tick)

    app = FastAPI()
    await main._startup(app)  # must return, not hang or raise
    assert app.state.domain_sweep_task is not None
    await main._shutdown(app)
