"""`app.main`'s MRP-sweep lifespan hooks (inventory plan Phase 7) — the same shape
`test_main_sweep_wiring.py` proves for `ReconcileSweep`: starts as a background task on its own
bulkhead engine when `Settings.run_sweeps_in_api` is on, stays off otherwise, survives a failing
tick, and a failed outbox-role precondition skips only this sweep rather than the API's startup.

No real database or broker: the engine constructor is faked to a no-op.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import structlog
from fastapi import FastAPI

from app import main
from platform_core.supervise import get_sweep_health, reset_sweep_health


class _FakeSettings:
    def __init__(self, *, run_sweeps_in_api: bool) -> None:
        self.run_sweeps_in_api = run_sweeps_in_api
        self.environment = "local"
        self.mrp_sweep_interval_seconds = 30.0


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
        assert name == "mrp-sweep"
        engine = _FakeEngine()
        created.append(engine)
        return engine

    monkeypatch.setattr(main, "create_bulkhead_engine", fake_create)
    monkeypatch.setattr(main, "bulkhead_scope", lambda engine: ("bulkhead", engine))
    return created


def _patch_common(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(main, "init_outbox_engine", lambda cfg: None)
    monkeypatch.setattr(main, "get_outbox_engine", lambda: object())
    monkeypatch.setattr(main, "get_outbox_sessionmaker", lambda: object())
    monkeypatch.setattr(main, "assert_outbox_role", _noop)


async def test_sweep_is_not_started_when_the_setting_is_off(
    monkeypatch: pytest.MonkeyPatch, engines: list[_FakeEngine]
) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(main, "settings", lambda: _FakeSettings(run_sweeps_in_api=False))
    app = FastAPI()
    await main._start_mrp_sweep(app)
    assert app.state.mrp_sweep_task is None
    assert engines == []
    await main._stop_mrp_sweep(app)


async def test_sweep_starts_on_its_own_bulkhead_engine_and_disposes_it(
    monkeypatch: pytest.MonkeyPatch, engines: list[_FakeEngine]
) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(main, "settings", lambda: _FakeSettings(run_sweeps_in_api=True))
    scopes: list[Any] = []
    started = asyncio.Event()

    async def fake_run(self: main.MrpRunSweep) -> None:
        scopes.append(self._work_scope)
        started.set()
        await asyncio.sleep(10)

    monkeypatch.setattr(main.MrpRunSweep, "run", fake_run)
    app = FastAPI()
    await main._start_mrp_sweep(app)
    await asyncio.wait_for(started.wait(), timeout=1)
    assert len(engines) == 1 and scopes == [("bulkhead", engines[0])]
    health = get_sweep_health("operations.mrp")
    assert health is not None and health.alive is True
    await main._stop_mrp_sweep(app)
    assert app.state.mrp_sweep_task.done()
    assert engines[0].disposed is True and health.alive is False


async def test_a_sweep_that_fails_every_tick_does_not_propagate_out_of_startup(
    monkeypatch: pytest.MonkeyPatch, engines: list[_FakeEngine]
) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(main, "settings", lambda: _FakeSettings(run_sweeps_in_api=True))

    async def fake_tick(self: main.MrpRunSweep) -> None:
        raise RuntimeError("db gone")

    monkeypatch.setattr(main.MrpRunSweep, "tick", fake_tick)
    app = FastAPI()
    await main._start_mrp_sweep(app)
    assert app.state.mrp_sweep_task is not None
    await main._stop_mrp_sweep(app)


async def test_a_failed_outbox_role_precondition_skips_only_the_sweep(
    monkeypatch: pytest.MonkeyPatch, engines: list[_FakeEngine]
) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(main, "settings", lambda: _FakeSettings(run_sweeps_in_api=True))

    async def role_is_wrong(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("opsgrids_outbox is not BYPASSRLS")

    monkeypatch.setattr(main, "assert_outbox_role", role_is_wrong)
    app = FastAPI()
    with structlog.testing.capture_logs() as logs:
        await main._start_mrp_sweep(app)
    assert app.state.mrp_sweep_task is None and engines == []
    not_started = [line for line in logs if line["event"] == "operations.mrp.not_started"]
    assert len(not_started) == 1 and not_started[0]["log_level"] == "error"
    health = get_sweep_health("operations.mrp")
    assert health is not None and health.alive is False and health.consecutive_failures == 1
    await main._stop_mrp_sweep(app)
