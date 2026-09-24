"""`app.main`'s reconcile-sweep lifespan hooks (memory-headroom slice, fix rounds 2 and 3): the
sweep starts as a background task when `Settings.run_sweeps_in_api` is on, stays off otherwise, and
a started sweep is cleanly cancelled on shutdown. Round 3 added: the sweep runs on its own
one-connection bulkhead engine (disposed at shutdown), a failed outbox-role precondition skips only
the sweep and never stops the API from starting, and the sweep is visible on `/metrics`.

No real database: every DB-role assertion and engine constructor these hooks call is faked to a
no-op, the same wiring-test shape `services/notification/tests/test_worker_wiring.py` and
`services/approval/tests/test_worker_wiring.py` use for their own worker processes.
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


@pytest.fixture
def engines(monkeypatch: pytest.MonkeyPatch) -> list[_FakeEngine]:
    """Every bulkhead engine the hooks create, and what `bulkhead_scope` was handed."""
    created: list[_FakeEngine] = []

    def fake_create(cfg: Any, *, name: str) -> _FakeEngine:
        assert name == "sweep"
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
    await main._start_reconcile_sweep(app)
    assert app.state.reconcile_sweep_task is None
    assert engines == []  # a service with the sweep off never opens a connection it does not use
    await main._stop_reconcile_sweep(app)  # must not raise on a task that was never started


async def test_sweep_starts_as_a_background_task_when_the_setting_is_on(
    monkeypatch: pytest.MonkeyPatch, engines: list[_FakeEngine]
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
    health = get_sweep_health("inventory.reconcile")
    assert health is not None and health.alive is True

    await main._stop_reconcile_sweep(app)
    assert app.state.reconcile_sweep_task.done()
    assert health.alive is False


async def test_the_sweep_runs_on_its_own_bulkhead_engine_and_disposes_it_at_shutdown(
    monkeypatch: pytest.MonkeyPatch, engines: list[_FakeEngine]
) -> None:
    """Round 3, LOW: the sweep's per-tenant transactions must not come out of the API's request
    pool. The work scope it is built with is the bulkhead's, and the bulkhead engine is disposed
    when the API shuts down."""
    _patch_common(monkeypatch)
    monkeypatch.setattr(main, "settings", lambda: _FakeSettings(run_sweeps_in_api=True))

    scopes: list[Any] = []

    async def fake_run(self: main.ReconcileSweep) -> None:
        scopes.append(self._work_scope)
        await asyncio.sleep(10)

    monkeypatch.setattr(main.ReconcileSweep, "run", fake_run)

    app = FastAPI()
    await main._start_reconcile_sweep(app)
    await asyncio.sleep(0)
    assert len(engines) == 1
    assert scopes == [("bulkhead", engines[0])]
    assert engines[0].disposed is False

    await main._stop_reconcile_sweep(app)
    assert engines[0].disposed is True


async def test_a_sweep_that_fails_every_tick_does_not_propagate_out_of_startup(
    monkeypatch: pytest.MonkeyPatch, engines: list[_FakeEngine]
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


async def test_a_failed_outbox_role_precondition_skips_only_the_sweep(
    monkeypatch: pytest.MonkeyPatch, engines: list[_FakeEngine]
) -> None:
    """Round 3, LOW: the customer-facing API must not refuse to start because the *sweep's*
    dependency (the BYPASSRLS outbox role) is misconfigured. The sweep is skipped, loudly — an
    error log, and a gauge showing it never ran — and the API comes up."""
    _patch_common(monkeypatch)
    monkeypatch.setattr(main, "settings", lambda: _FakeSettings(run_sweeps_in_api=True))

    async def role_is_wrong(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("opsgrids_outbox is not BYPASSRLS")

    monkeypatch.setattr(main, "assert_outbox_role", role_is_wrong)

    app = FastAPI()
    with structlog.testing.capture_logs() as logs:
        await main._start_reconcile_sweep(app)  # must not raise
    assert app.state.reconcile_sweep_task is None
    assert engines == []  # nothing to dispose: the bulkhead is only opened once the check passes
    not_started = [line for line in logs if line["event"] == "inventory.reconcile.not_started"]
    assert len(not_started) == 1 and not_started[0]["log_level"] == "error"
    health = get_sweep_health("inventory.reconcile")
    assert health is not None
    assert health.alive is False and health.consecutive_failures == 1
    assert health.last_success_ts == 0.0
    await main._stop_reconcile_sweep(app)  # and shutdown copes with there being nothing to stop
