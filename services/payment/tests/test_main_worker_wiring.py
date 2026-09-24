"""`app.main`'s worker-folding lifespan hooks (the worker-folding follow-up to the memory-headroom
slice, 2026-09-24): the charge consumers and the stuck-charge sweep start as one supervised
background task when `Settings.run_consumers_in_api` is on, stay off otherwise, and a started
group is cleanly cancelled on shutdown — the same shape
`services/operations/tests/test_main_sweep_wiring.py` proves for its own in-API sweep.

No real database or broker: every DB-role assertion and engine constructor these hooks call is
faked to a no-op.
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
    def __init__(self, *, run_consumers_in_api: bool) -> None:
        self.run_consumers_in_api = run_consumers_in_api
        self.environment = "local"


class _FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


class _FakeSweep:
    def __init__(self, *, work_scope: Any) -> None:
        self.work_scope = work_scope
        self.ran = asyncio.Event()

    async def run(self) -> None:
        self.ran.set()
        await asyncio.sleep(10)


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


def _patch_common(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(main, "init_outbox_engine", lambda cfg: None)
    monkeypatch.setattr(main, "get_outbox_engine", lambda: object())
    monkeypatch.setattr(main, "assert_outbox_role", _noop)


async def test_nothing_starts_when_the_setting_is_off(
    monkeypatch: pytest.MonkeyPatch, engines: list[_FakeEngine]
) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(main, "settings", lambda: _FakeSettings(run_consumers_in_api=False))
    app = FastAPI()
    await main._start_background_worker(app)
    assert app.state.background_worker_task is None
    assert engines == []
    await main._stop_background_worker(app)


async def test_the_consumer_and_sweep_both_run_inside_one_supervised_group(
    monkeypatch: pytest.MonkeyPatch, engines: list[_FakeEngine]
) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(main, "settings", lambda: _FakeSettings(run_consumers_in_api=True))

    ran: set[str] = set()
    sweeps_built: list[_FakeSweep] = []

    async def fake_consumer_run() -> None:
        ran.add("consumer")
        await asyncio.sleep(10)

    def fake_build_sweep(*, work_scope: Any) -> _FakeSweep:
        sweep = _FakeSweep(work_scope=work_scope)
        sweeps_built.append(sweep)
        return sweep

    monkeypatch.setattr(main.consumers.consumer, "run", fake_consumer_run)
    monkeypatch.setattr(main, "build_sweep", fake_build_sweep)

    app = FastAPI()
    await main._start_background_worker(app)
    assert app.state.background_worker_task is not None
    assert len(sweeps_built) == 1
    assert sweeps_built[0].work_scope == ("bulkhead", engines[0])
    await asyncio.wait_for(sweeps_built[0].ran.wait(), timeout=1)
    for _ in range(50):
        if "consumer" in ran:
            break
        await asyncio.sleep(0.01)
    assert "consumer" in ran
    assert not app.state.background_worker_task.done()
    health = get_sweep_health("payment.worker")
    assert health is not None and health.alive is True

    await main._stop_background_worker(app)
    assert app.state.background_worker_task.done()
    assert health.alive is False
    assert engines[0].disposed is True


async def test_a_failed_outbox_role_precondition_skips_only_the_group(
    monkeypatch: pytest.MonkeyPatch, engines: list[_FakeEngine]
) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(main, "settings", lambda: _FakeSettings(run_consumers_in_api=True))

    async def role_is_wrong(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("opsgrids_outbox is not BYPASSRLS")

    monkeypatch.setattr(main, "assert_outbox_role", role_is_wrong)

    app = FastAPI()
    with structlog.testing.capture_logs() as logs:
        await main._start_background_worker(app)  # must not raise
    assert app.state.background_worker_task is None
    assert engines == []
    not_started = [line for line in logs if line["event"] == "payment.worker.not_started"]
    assert len(not_started) == 1 and not_started[0]["log_level"] == "error"
    await main._stop_background_worker(app)
