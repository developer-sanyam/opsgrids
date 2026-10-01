"""`app.main`'s channel-sync-sweep lifespan hooks (Fulfilment plan F11) — the same shape
`test_main_sweep_wiring.py` proves for `ReconcileSweep`, mirrored here for `ChannelSyncSweep`:
starts as a background task on its own bulkhead engine when `Settings.run_sweeps_in_api` is on,
stays off otherwise, survives a failing tick, and a failed outbox-role precondition skips only
this sweep rather than the API's own startup.

Wired on `raw_bulkhead_scope`, not `bulkhead_scope` — the one sweep of the three whose own
downstream code (`app.channels.services.load_client_for_channel` and its siblings) opens its own
short transaction around each piece of work, closed before the call to the marketplace is made
(`docs/io-outside-transactions.md`); a pre-opened transaction here collided with that as a second
`session.begin()` (production incident, 2026-10-01 — see `platform_core.db.raw_bulkhead_scope`'s
own docstring). `test_main_sweep_wiring.py`'s `ReconcileSweep`/`AlertSweep` correctly keep
`bulkhead_scope`: neither opens a transaction inside the one `work_scope` already gives them.

No real database or broker: the bulkhead engine constructor is faked to a no-op, the same
`test_main_sweep_wiring.py` shape.
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
        self.channel_sync_interval_seconds = 300.0


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
        assert name == "channel-sweep"
        engine = _FakeEngine()
        created.append(engine)
        return engine

    monkeypatch.setattr(main, "create_bulkhead_engine", fake_create)
    monkeypatch.setattr(main, "raw_bulkhead_scope", lambda engine: ("raw_bulkhead", engine))
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
    await main._start_channel_sync_sweep(app)
    assert app.state.channel_sweep_task is None
    assert engines == []
    await main._stop_channel_sync_sweep(app)  # must not raise on a task that was never started


async def test_sweep_starts_as_a_background_task_on_its_own_bulkhead_and_disposes_at_shutdown(
    monkeypatch: pytest.MonkeyPatch, engines: list[_FakeEngine]
) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(main, "settings", lambda: _FakeSettings(run_sweeps_in_api=True))

    started = asyncio.Event()
    scopes: list[Any] = []

    async def fake_run(self: main.ChannelSyncSweep) -> None:
        scopes.append(self._work_scope)
        started.set()
        await asyncio.sleep(10)

    monkeypatch.setattr(main.ChannelSyncSweep, "run", fake_run)

    app = FastAPI()
    await main._start_channel_sync_sweep(app)
    assert app.state.channel_sweep_task is not None
    await asyncio.wait_for(started.wait(), timeout=1)
    assert not app.state.channel_sweep_task.done()
    assert len(engines) == 1
    assert scopes == [("raw_bulkhead", engines[0])]
    health = get_sweep_health("channels.sync")
    assert health is not None and health.alive is True
    assert engines[0].disposed is False

    await main._stop_channel_sync_sweep(app)
    assert app.state.channel_sweep_task.done()
    assert engines[0].disposed is True
    assert health.alive is False


async def test_a_sweep_that_fails_every_tick_does_not_propagate_out_of_startup(
    monkeypatch: pytest.MonkeyPatch, engines: list[_FakeEngine]
) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(main, "settings", lambda: _FakeSettings(run_sweeps_in_api=True))

    async def fake_tick(self: main.ChannelSyncSweep) -> None:
        raise RuntimeError("db gone")

    monkeypatch.setattr(main.ChannelSyncSweep, "tick", fake_tick)

    app = FastAPI()
    await main._start_channel_sync_sweep(app)  # must return, not hang or raise
    assert app.state.channel_sweep_task is not None
    await main._stop_channel_sync_sweep(app)


async def test_a_failed_outbox_role_precondition_skips_only_this_sweep(
    monkeypatch: pytest.MonkeyPatch, engines: list[_FakeEngine]
) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(main, "settings", lambda: _FakeSettings(run_sweeps_in_api=True))

    async def role_is_wrong(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("opsgrids_outbox is not BYPASSRLS")

    monkeypatch.setattr(main, "assert_outbox_role", role_is_wrong)

    app = FastAPI()
    with structlog.testing.capture_logs() as logs:
        await main._start_channel_sync_sweep(app)  # must not raise
    assert app.state.channel_sweep_task is None
    assert engines == []
    not_started = [line for line in logs if line["event"] == "channels.sync.not_started"]
    assert len(not_started) == 1 and not_started[0]["log_level"] == "error"
    health = get_sweep_health("channels.sync")
    assert health is not None
    assert health.alive is False and health.consecutive_failures == 1
    await main._stop_channel_sync_sweep(app)
