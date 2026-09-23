"""`app.main`'s purge-sweep lifespan hooks (memory-headroom slice, fix rounds 2 and 3): the sweep
starts as a background task when `Settings.run_sweeps_in_api` is on, stays off otherwise, and a
started sweep is cleanly cancelled on shutdown. Round 3 added: the sweep runs on its own
one-connection bulkhead engine (a purge holds its connection across the object-store delete),
disposed at shutdown; a failed outbox-role precondition skips only the sweep and never stops the
upload API from starting; and the API never hands the sweep the orphan credential's job.

No real database: every DB-role assertion and engine constructor these hooks call is faked to a
no-op, the same wiring-test shape `services/operations/tests/test_main_sweep_wiring.py` and
`services/storefront/tests/test_main_sweep_wiring.py` use.
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
        self.asset_purge_retention_days = 30
        self.orphan_sweep_enabled = True  # even switched on, the API never runs the orphan pass
        self.orphan_sweep_dry_run = True
        self.orphan_sweep_min_age_hours = 24
        self.orphan_sweep_max_deletes_per_tick = 100


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

    monkeypatch.setattr(main, "get_outbox_engine", lambda: object())
    monkeypatch.setattr(main, "get_outbox_sessionmaker", lambda: object())
    monkeypatch.setattr(main, "assert_outbox_role", _noop)
    monkeypatch.setattr(main, "get_object_store", lambda: object())
    monkeypatch.setattr(main, "get_orphan_scanner", lambda: object())


async def test_sweep_is_not_started_when_the_setting_is_off(
    monkeypatch: pytest.MonkeyPatch, engines: list[_FakeEngine]
) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(main, "settings", lambda: _FakeSettings(run_sweeps_in_api=False))
    app = FastAPI()
    await main._start_purge_sweep(app)
    assert app.state.purge_sweep_task is None
    assert engines == []
    await main._stop_purge_sweep(app)  # must not raise on a task that was never started


async def test_sweep_starts_as_a_background_task_when_the_setting_is_on(
    monkeypatch: pytest.MonkeyPatch, engines: list[_FakeEngine]
) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(main, "settings", lambda: _FakeSettings(run_sweeps_in_api=True))

    started = asyncio.Event()

    async def fake_run(self: main.PurgeSweep) -> None:
        started.set()
        await asyncio.sleep(10)

    monkeypatch.setattr(main.PurgeSweep, "run", fake_run)

    app = FastAPI()
    await main._start_purge_sweep(app)
    assert app.state.purge_sweep_task is not None
    await asyncio.wait_for(started.wait(), timeout=1)
    assert not app.state.purge_sweep_task.done()
    health = get_sweep_health("document.sweep")
    assert health is not None and health.alive is True

    await main._stop_purge_sweep(app)
    assert app.state.purge_sweep_task.done()
    assert health.alive is False


async def test_the_orphan_pass_never_runs_in_the_api_even_when_the_setting_is_on(
    monkeypatch: pytest.MonkeyPatch, engines: list[_FakeEngine]
) -> None:
    """ADR-0009 §3: the request-serving API must never hold the credential that can list the
    bucket. `orphan_sweep_enabled` being true (as it is in this fake) changes nothing here."""
    _patch_common(monkeypatch)
    monkeypatch.setattr(main, "settings", lambda: _FakeSettings(run_sweeps_in_api=True))

    seen: list[bool] = []

    async def fake_run(self: main.PurgeSweep) -> None:
        seen.append(self._orphan_enabled)
        await asyncio.sleep(10)

    monkeypatch.setattr(main.PurgeSweep, "run", fake_run)

    app = FastAPI()
    await main._start_purge_sweep(app)
    await asyncio.sleep(0)
    assert seen == [False]
    await main._stop_purge_sweep(app)


async def test_the_sweep_runs_on_its_own_bulkhead_engine_and_disposes_it_at_shutdown(
    monkeypatch: pytest.MonkeyPatch, engines: list[_FakeEngine]
) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(main, "settings", lambda: _FakeSettings(run_sweeps_in_api=True))

    scopes: list[Any] = []

    async def fake_run(self: main.PurgeSweep) -> None:
        scopes.append(self._work_scope)
        await asyncio.sleep(10)

    monkeypatch.setattr(main.PurgeSweep, "run", fake_run)

    app = FastAPI()
    await main._start_purge_sweep(app)
    await asyncio.sleep(0)
    assert len(engines) == 1
    assert scopes == [("bulkhead", engines[0])]
    assert engines[0].disposed is False

    await main._stop_purge_sweep(app)
    assert engines[0].disposed is True


async def test_a_sweep_that_fails_every_tick_does_not_propagate_out_of_startup(
    monkeypatch: pytest.MonkeyPatch, engines: list[_FakeEngine]
) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(main, "settings", lambda: _FakeSettings(run_sweeps_in_api=True))

    async def fake_tick(self: main.PurgeSweep) -> None:
        raise RuntimeError("s3 gone")

    monkeypatch.setattr(main.PurgeSweep, "tick", fake_tick)

    app = FastAPI()
    await main._start_purge_sweep(app)  # must return, not hang or raise
    assert app.state.purge_sweep_task is not None
    await main._stop_purge_sweep(app)


async def test_a_failed_outbox_role_precondition_skips_only_the_sweep(
    monkeypatch: pytest.MonkeyPatch, engines: list[_FakeEngine]
) -> None:
    """Round 3, LOW: the upload API must not refuse to start because the purge sweep's dependency
    (the BYPASSRLS outbox role) is misconfigured. The sweep is skipped, loudly."""
    _patch_common(monkeypatch)
    monkeypatch.setattr(main, "settings", lambda: _FakeSettings(run_sweeps_in_api=True))

    async def role_is_wrong(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("opsgrids_outbox is not BYPASSRLS")

    monkeypatch.setattr(main, "assert_outbox_role", role_is_wrong)

    app = FastAPI()
    with structlog.testing.capture_logs() as logs:
        await main._start_purge_sweep(app)  # must not raise
    assert app.state.purge_sweep_task is None
    assert engines == []
    not_started = [line for line in logs if line["event"] == "document.sweep.not_started"]
    assert len(not_started) == 1 and not_started[0]["log_level"] == "error"
    health = get_sweep_health("document.sweep")
    assert health is not None
    assert health.alive is False and health.consecutive_failures == 1
    await main._stop_purge_sweep(app)
