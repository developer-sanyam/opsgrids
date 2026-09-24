"""`app.main`'s worker-folding lifespan hooks (the worker-folding follow-up to the memory-headroom
slice, 2026-09-24): the payment-event consumer, the dunning sweep and — when enabled — the billing
run start as one supervised background task when `Settings.run_consumers_in_api` is on, stay off
otherwise, and a started group is cleanly cancelled on shutdown — the same shape
`services/operations/tests/test_main_sweep_wiring.py` proves for its own in-API sweep.

No real database, broker or HTTP: every DB-role assertion and engine constructor these hooks call
is faked to a no-op, and `HttpBilling`/`HttpPayment` are never exercised (the billing run stays off
in every scenario here — its own gate, `billing_run_loop`, is proven in `test_billing_run.py`).
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
    def __init__(self, *, run_consumers_in_api: bool, billing_run_enabled: bool = False) -> None:
        self.run_consumers_in_api = run_consumers_in_api
        self.environment = "local"
        self.catalog_service_url = "http://catalog.internal"
        self.product_service_url = "http://product.internal"
        self.billing_service_url = "http://billing.internal"
        self.payment_service_url = "http://payment.internal"
        self.billing_run_poll_interval = 300.0
        self.billing_run_enabled = billing_run_enabled
        self.billing_run_not_before = None
        self.billing_run_not_before_invalid = None


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


async def test_the_consumer_and_dunning_sweep_run_inside_one_supervised_group(
    monkeypatch: pytest.MonkeyPatch, engines: list[_FakeEngine]
) -> None:
    """Billing run stays off (the default) here — `billing_run_loop` refuses without a cutover,
    so only the payment consumer and the dunning sweep should actually run."""
    _patch_common(monkeypatch)
    monkeypatch.setattr(main, "settings", lambda: _FakeSettings(run_consumers_in_api=True))

    ran: set[str] = set()

    async def fake_consumer_run() -> None:
        ran.add("payment_consumer")
        await asyncio.sleep(10)

    async def fake_dunning_run(self: main.DunningSweep) -> None:
        ran.add("dunning_sweep")
        await asyncio.sleep(10)

    monkeypatch.setattr(main.consumers.consumer, "run", fake_consumer_run)
    monkeypatch.setattr(main.DunningSweep, "run", fake_dunning_run)

    app = FastAPI()
    await main._start_background_worker(app)
    assert app.state.background_worker_task is not None
    for _ in range(50):
        if ran == {"payment_consumer", "dunning_sweep"}:
            break
        await asyncio.sleep(0.01)
    assert ran == {"payment_consumer", "dunning_sweep"}
    assert not app.state.background_worker_task.done()
    health = get_sweep_health("subscription.worker")
    assert health is not None and health.alive is True

    await main._stop_background_worker(app)
    assert app.state.background_worker_task.done()
    assert health.alive is False
    assert engines[0].disposed is True


async def test_the_billing_run_joins_the_group_once_enabled_with_a_cutover(
    monkeypatch: pytest.MonkeyPatch, engines: list[_FakeEngine]
) -> None:
    _patch_common(monkeypatch)
    from datetime import UTC, datetime

    fake_settings = _FakeSettings(run_consumers_in_api=True, billing_run_enabled=True)
    fake_settings.billing_run_not_before = datetime(2026, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(main, "settings", lambda: fake_settings)

    ran: set[str] = set()

    async def fake_consumer_run() -> None:
        ran.add("payment_consumer")
        await asyncio.sleep(10)

    async def fake_dunning_run(self: main.DunningSweep) -> None:
        ran.add("dunning_sweep")
        await asyncio.sleep(10)

    async def fake_billing_run(self: main.BillingRunSweep) -> None:
        ran.add("billing_run")
        await asyncio.sleep(10)

    monkeypatch.setattr(main.consumers.consumer, "run", fake_consumer_run)
    monkeypatch.setattr(main.DunningSweep, "run", fake_dunning_run)
    monkeypatch.setattr(main.BillingRunSweep, "run", fake_billing_run)

    app = FastAPI()
    await main._start_background_worker(app)
    for _ in range(50):
        if ran == {"payment_consumer", "dunning_sweep", "billing_run"}:
            break
        await asyncio.sleep(0.01)
    assert ran == {"payment_consumer", "dunning_sweep", "billing_run"}

    await main._stop_background_worker(app)


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
    not_started = [line for line in logs if line["event"] == "subscription.worker.not_started"]
    assert len(not_started) == 1 and not_started[0]["log_level"] == "error"
    await main._stop_background_worker(app)
