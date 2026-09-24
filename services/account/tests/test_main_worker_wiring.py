"""`app.main`'s worker-folding lifespan hooks (the worker-folding follow-up to the memory-headroom
slice, 2026-09-24): the activation consumer and the domain re-verification sweep start as one
supervised background task when `Settings.run_consumers_in_api` is on, stay off otherwise, and a
started group is cleanly cancelled on shutdown — the same shape
`services/operations/tests/test_main_sweep_wiring.py` proves for its own in-API sweep.

No real database or broker: every DB-role assertion and engine constructor these hooks call is
faked to a no-op.
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
        self.environment = "local"
        self.redis_url = "redis://localhost:6380/0"
        self.domain_recheck_interval_seconds = 86_400.0
        self.domain_recheck_batch_size = 50
        self.domain_recheck_poll_interval_seconds = 900.0


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


@pytest.fixture(autouse=True)
def _restore_session_scope():  # type: ignore[no-untyped-def]
    """`use_session_scope` mutates `app.consumers`' module-level `_session_scope` — process-wide,
    not test-scoped. Left pointed at a fake `("bulkhead", engine)` tuple after this test, the next
    test in the same process (any test, in any file, that happens to run later — pytest collects
    alphabetically, so a real integration test can be the one that pays for this) would call it as
    a callable and get `TypeError: 'tuple' object is not callable` instead of a session, for a
    reason with nothing to do with what it was testing. Restored here, every time."""
    yield
    main.consumers.use_session_scope(main.consumers._default_session_scope)


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
    monkeypatch.setattr(main, "init_redis", lambda url: None)
    monkeypatch.setattr(main, "get_directory_sessionmaker", lambda: object())


async def test_nothing_starts_when_the_setting_is_off(
    monkeypatch: pytest.MonkeyPatch, engines: list[_FakeEngine]
) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(main, "settings", lambda: _FakeSettings(run_consumers_in_api=False))
    app = FastAPI()
    await main._start_background_worker(app)
    assert app.state.background_worker_task is None
    assert engines == []
    await main._stop_background_worker(app)  # must not raise on a task that was never started


async def test_the_consumer_and_sweep_both_run_inside_one_supervised_group(
    monkeypatch: pytest.MonkeyPatch, engines: list[_FakeEngine]
) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(main, "settings", lambda: _FakeSettings(run_consumers_in_api=True))

    ran: set[str] = set()

    async def fake_consumer_run() -> None:
        ran.add("consumer")
        await asyncio.sleep(10)

    async def fake_sweep_run(self: main.DomainRecheckSweep) -> None:
        ran.add("domain_recheck")
        await asyncio.sleep(10)

    monkeypatch.setattr(main.consumers.consumer, "run", fake_consumer_run)
    monkeypatch.setattr(main.DomainRecheckSweep, "run", fake_sweep_run)

    app = FastAPI()
    await main._start_background_worker(app)
    assert app.state.background_worker_task is not None
    for _ in range(50):
        if ran == {"consumer", "domain_recheck"}:
            break
        await asyncio.sleep(0.01)
    assert ran == {"consumer", "domain_recheck"}
    assert not app.state.background_worker_task.done()
    health = get_sweep_health("account.worker")
    assert health is not None and health.alive is True

    await main._stop_background_worker(app)
    assert app.state.background_worker_task.done()
    assert health.alive is False
    assert len(engines) == 1
    assert engines[0].disposed is True


async def test_the_group_runs_on_its_own_bulkhead_engine(
    monkeypatch: pytest.MonkeyPatch, engines: list[_FakeEngine]
) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(main, "settings", lambda: _FakeSettings(run_consumers_in_api=True))

    scopes: list[Any] = []

    async def fake_sweep_run(self: main.DomainRecheckSweep) -> None:
        scopes.append(self._work_scope)
        await asyncio.sleep(10)

    async def fake_consumer_run() -> None:
        await asyncio.sleep(10)

    monkeypatch.setattr(main.consumers.consumer, "run", fake_consumer_run)
    monkeypatch.setattr(main.DomainRecheckSweep, "run", fake_sweep_run)

    app = FastAPI()
    await main._start_background_worker(app)
    for _ in range(50):
        if scopes:
            break
        await asyncio.sleep(0.01)
    assert scopes == [("bulkhead", engines[0])]
    assert main.consumers._session_scope == ("bulkhead", engines[0])

    await main._stop_background_worker(app)


async def test_a_group_that_fails_to_build_does_not_propagate_out_of_startup(
    monkeypatch: pytest.MonkeyPatch, engines: list[_FakeEngine]
) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(main, "settings", lambda: _FakeSettings(run_consumers_in_api=True))

    def _boom(cfg: Any, *, name: str) -> Any:
        raise RuntimeError("no db")

    monkeypatch.setattr(main, "create_bulkhead_engine", _boom)

    app = FastAPI()
    await main._start_background_worker(app)  # must not raise
    assert app.state.background_worker_task is None
    await main._stop_background_worker(app)
