"""`app.main`'s worker-folding lifespan hooks (the worker-folding follow-up to the memory-headroom
slice, 2026-09-24): the two event consumers and the campaign send driver start as one supervised
background task when `Settings.run_consumers_in_api` is on, stay off otherwise, and a started
group is cleanly cancelled on shutdown — the same shape
`services/operations/tests/test_main_sweep_wiring.py` proves for its own in-API sweep.

No real database or broker: every DB-role assertion and engine constructor these hooks call is
faked to a no-op, and the target resolver / transport registration calls are faked too (they are
proven separately by `test_worker_wiring.py`'s own standalone-process test).
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
        self.engage_campaign_batch_size = 200
        self.engage_campaign_claim_lease_seconds = 300
        self.engage_campaign_pause_retry_seconds = 900.0
        self.engage_campaign_poll_interval_seconds = 15.0


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
    """`use_session_scope` mutates `app.consumers`'/`app.dispatcher`'s module-level
    `_session_scope` — process-wide, not test-scoped. Left pointed at a fake `("bulkhead",
    engine)` tuple after this test, the next test in the same process (any test, in any file,
    that happens to run later — pytest collects alphabetically, so a real integration test can be
    the one that pays for this) would call it as a callable and get `TypeError: 'tuple' object is
    not callable` instead of a session, for a reason with nothing to do with what it was testing.
    Restored here, every time."""
    yield
    main.consumers.use_session_scope(main.consumers._default_session_scope)
    main.dispatcher.use_session_scope(main.dispatcher._default_session_scope)


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
    monkeypatch.setattr(main, "make_target_resolver", lambda cfg: object())
    monkeypatch.setattr(main, "build_transports", lambda cfg: {})
    monkeypatch.setattr(main.consumers, "register", lambda resolver: None)
    monkeypatch.setattr(main.dispatcher, "register", lambda transports, cfg: None)


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


async def test_both_consumers_and_the_campaign_driver_run_inside_one_supervised_group(
    monkeypatch: pytest.MonkeyPatch, engines: list[_FakeEngine]
) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(main, "settings", lambda: _FakeSettings(run_consumers_in_api=True))

    ran: set[str] = set()

    async def fake_events_run() -> None:
        ran.add("events")
        await asyncio.sleep(10)

    async def fake_dispatch_run() -> None:
        ran.add("dispatch")
        await asyncio.sleep(10)

    async def fake_driver_run(self: main.CampaignDriver) -> None:
        ran.add("campaign_driver")
        await asyncio.sleep(10)

    monkeypatch.setattr(main.consumers.consumer, "run", fake_events_run)
    monkeypatch.setattr(main.dispatcher.consumer, "run", fake_dispatch_run)
    monkeypatch.setattr(main.CampaignDriver, "run", fake_driver_run)

    app = FastAPI()
    await main._start_background_worker(app)
    assert app.state.background_worker_task is not None
    for _ in range(50):
        if ran == {"events", "dispatch", "campaign_driver"}:
            break
        await asyncio.sleep(0.01)
    assert ran == {"events", "dispatch", "campaign_driver"}
    assert not app.state.background_worker_task.done()
    group_health = get_sweep_health("notification.worker")
    assert group_health is not None and group_health.alive is True
    # `CampaignDriver` is its own `GuardedSweep` and self-registers a second gauge under its
    # `_log_name` ("notification.cron") — toggled by hand alongside the group's own, since only
    # `start_background_sweep` (wrapping the whole group here, not this one driver) would
    # otherwise ever flip it.
    driver_health = get_sweep_health("notification.cron")
    assert driver_health is not None and driver_health.alive is True

    await main._stop_background_worker(app)
    assert app.state.background_worker_task.done()
    assert group_health.alive is False
    assert driver_health.alive is False
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
    not_started = [line for line in logs if line["event"] == "notification.worker.not_started"]
    assert len(not_started) == 1 and not_started[0]["log_level"] == "error"
    await main._stop_background_worker(app)
