"""`app.main`'s worker-folding lifespan hooks (the worker-folding follow-up to the memory-headroom
slice, 2026-09-24): the filing consumer and the filing-reclaim sweep start as one supervised
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
        self.filing_reclaim_poll_interval_seconds = 60.0


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


def _patch_common(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(main, "init_outbox_engine", lambda cfg: None)
    monkeypatch.setattr(main, "get_outbox_engine", lambda: object())
    monkeypatch.setattr(main, "get_outbox_sessionmaker", lambda: object())
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

    async def fake_consumer_run() -> None:
        ran.add("consumer")
        await asyncio.sleep(10)

    async def fake_sweep_run(self: main.FilingReclaimSweep) -> None:
        ran.add("filing_reclaim")
        await asyncio.sleep(10)

    monkeypatch.setattr(main.consumers.consumer, "run", fake_consumer_run)
    monkeypatch.setattr(main.FilingReclaimSweep, "run", fake_sweep_run)

    app = FastAPI()
    await main._start_background_worker(app)
    assert app.state.background_worker_task is not None
    for _ in range(50):
        if ran == {"consumer", "filing_reclaim"}:
            break
        await asyncio.sleep(0.01)
    assert ran == {"consumer", "filing_reclaim"}
    assert not app.state.background_worker_task.done()
    group_health = get_sweep_health("finance.worker")
    assert group_health is not None and group_health.alive is True

    await main._stop_background_worker(app)
    assert app.state.background_worker_task.done()
    assert group_health.alive is False
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
    not_started = [line for line in logs if line["event"] == "finance.worker.not_started"]
    assert len(not_started) == 1 and not_started[0]["log_level"] == "error"
    await main._stop_background_worker(app)
