"""`app.main`'s worker-folding lifespan hooks (the worker-folding follow-up to the memory-headroom
slice, 2026-09-24): this service's one event consumer starts as a supervised background task when
`Settings.run_consumers_in_api` is on, stays off otherwise, and is cleanly cancelled on shutdown —
the same shape `services/operations/tests/test_main_sweep_wiring.py` proves for its own in-API
sweep.

No real database or broker: the bulkhead engine constructor is faked to a no-op.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI

from app import main
from platform_core.supervise import get_sweep_health, reset_sweep_health


class _FakeSettings:
    def __init__(self, *, run_consumers_in_api: bool) -> None:
        self.run_consumers_in_api = run_consumers_in_api


class _FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


@pytest.fixture(autouse=True)
def _clean_sweep_registry():  # type: ignore[no-untyped-def]
    reset_sweep_health()
    yield
    reset_sweep_health()


@pytest.fixture
def engines(monkeypatch: pytest.MonkeyPatch) -> list[_FakeEngine]:
    created: list[_FakeEngine] = []

    def fake_create(cfg: Any, *, name: str) -> _FakeEngine:
        assert name == "worker"
        engine = _FakeEngine()
        created.append(engine)
        return engine

    monkeypatch.setattr(main, "create_bulkhead_engine", fake_create)
    monkeypatch.setattr(main, "bulkhead_scope", lambda engine: ("bulkhead", engine))
    return created


async def test_nothing_starts_when_the_setting_is_off(
    monkeypatch: pytest.MonkeyPatch, engines: list[_FakeEngine]
) -> None:
    monkeypatch.setattr(main, "settings", lambda: _FakeSettings(run_consumers_in_api=False))
    app = FastAPI()
    await main._start_background_worker(app)
    assert app.state.background_worker_task is None
    assert engines == []
    await main._stop_background_worker(app)


async def test_the_consumer_runs_as_a_supervised_background_task(
    monkeypatch: pytest.MonkeyPatch, engines: list[_FakeEngine]
) -> None:
    monkeypatch.setattr(main, "settings", lambda: _FakeSettings(run_consumers_in_api=True))

    started = asyncio.Event()

    async def fake_consumer_run() -> None:
        started.set()
        await asyncio.sleep(10)

    monkeypatch.setattr(main.consumers.consumer, "run", fake_consumer_run)

    app = FastAPI()
    await main._start_background_worker(app)
    assert app.state.background_worker_task is not None
    await asyncio.wait_for(started.wait(), timeout=1)
    assert not app.state.background_worker_task.done()
    assert main.consumers._session_scope == ("bulkhead", engines[0])
    health = get_sweep_health("leave.worker")
    assert health is not None and health.alive is True

    await main._stop_background_worker(app)
    assert app.state.background_worker_task.done()
    assert health.alive is False
    assert engines[0].disposed is True


async def test_a_group_that_fails_to_build_does_not_propagate_out_of_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "settings", lambda: _FakeSettings(run_consumers_in_api=True))

    def _boom(cfg: Any, *, name: str) -> Any:
        raise RuntimeError("no db")

    monkeypatch.setattr(main, "create_bulkhead_engine", _boom)

    app = FastAPI()
    await main._start_background_worker(app)  # must not raise
    assert app.state.background_worker_task is None
    await main._stop_background_worker(app)
