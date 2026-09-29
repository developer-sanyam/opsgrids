"""`app.main`'s worker-folding lifespan hooks (MAJOR 2, security review, 2026-09-28): the two
fulfilment consumers (`app/consumers.py`) start as one supervised background task when
`Settings.run_consumers_in_api` is on, stay off otherwise, and a started worker is cleanly
cancelled on shutdown — the same shape `services/leave/tests/test_main_worker_wiring.py` and
`services/operations/tests/test_main_sweep_wiring.py` (this service's own in-API sweep) both prove
for their own background work. Before this fix, `operations` had no `*-worker` container and no
`run_consumers_in_api` field at all: neither consumer could ever run in production, regardless of
how correct the handler code was.

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
        # `_start_reconcile_sweep` also reads `settings()` when `_startup` runs both hooks —
        # off, so it is a no-op alongside the worker assertions this file actually cares about.
        self.run_sweeps_in_api = False
        self.environment = "local"
        self.reconcile_interval_seconds = 86_400.0


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
    """`use_session_scope`/`use_raw_session_scope` mutate `app.consumers`' module-level
    `_session_scope`/`_raw_session_scope` — process-wide, not test-scoped. Left pointed at a fake
    `("bulkhead", engine)` tuple (or a fake engine's sessionmaker) after this test, the next test
    in the same process that happens to run later would call it as a plain callable and get a
    confusing `TypeError` instead of a session, for a reason with nothing to do with what it was
    testing (`services/leave/tests/test_main_worker_wiring.py`'s own fixture states the same
    hazard). Restored here, every time."""
    yield
    main.consumers.use_session_scope(main.consumers._default_session_scope)
    main.consumers.use_raw_session_scope(main.consumers._default_raw_scope)


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
    await main._stop_background_worker(app)  # must not raise on a task that was never started


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
    # `consumers.bulkhead_raw_scope` is the real function (not faked by this file's `engines`
    # fixture) — it just closes over the same fake engine, so the meaningful assertion is that
    # wiring actually swapped it away from the process-wide default (F11: `dispatch_shipment_
    # transitioned` must share the worker's own bulkhead, not fall back to it silently).
    assert main.consumers._raw_session_scope is not main.consumers._default_raw_scope
    health = get_sweep_health("operations.worker")
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


async def test_startup_folds_both_the_sweep_and_the_worker(
    monkeypatch: pytest.MonkeyPatch, engines: list[_FakeEngine]
) -> None:
    """`app.main.app`'s real `on_startup`/`on_shutdown` are `_startup`/`_shutdown`, which run
    both hooks — this service is the first to fold a sweep (`RUN_SWEEPS_IN_API`) and a worker
    (`RUN_CONSUMERS_IN_API`) into the same API process, so this proves the combined entrypoint
    does not drop either one, the same way `services/subscription/app/main.py`'s own `_startup`/
    `_shutdown` combine its sweep and worker hooks."""

    async def _noop(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(main, "settings", lambda: _FakeSettings(run_consumers_in_api=True))
    monkeypatch.setattr(main, "init_outbox_engine", lambda cfg: None)
    monkeypatch.setattr(main, "get_outbox_engine", lambda: object())
    monkeypatch.setattr(main, "get_outbox_sessionmaker", lambda: object())
    monkeypatch.setattr(main, "assert_outbox_role", _noop)

    started = asyncio.Event()

    async def fake_consumer_run() -> None:
        started.set()
        await asyncio.sleep(10)

    monkeypatch.setattr(main.consumers.consumer, "run", fake_consumer_run)

    app = FastAPI()
    await main._startup(app)
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        assert app.state.background_worker_task is not None
        assert app.state.reconcile_sweep_task is None  # sweep setting is off in `_FakeSettings`
    finally:
        await main._shutdown(app)
    assert app.state.background_worker_task.done()
