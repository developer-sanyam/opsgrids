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
from pydantic import SecretStr

from app import main
from app.config import Settings
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

    expiry_built: list[_FakeSweep] = []

    def fake_build_expiry_sweep(*, work_scope: Any) -> _FakeSweep:
        sweep = _FakeSweep(work_scope=work_scope)
        expiry_built.append(sweep)
        return sweep

    poller_built: list[_FakeSweep] = []

    def fake_build_poller(*, work_scope: Any) -> _FakeSweep:
        poller = _FakeSweep(work_scope=work_scope)
        poller_built.append(poller)
        return poller

    monkeypatch.setattr(main.consumers.consumer, "run", fake_consumer_run)
    monkeypatch.setattr(main, "build_sweep", fake_build_sweep)
    monkeypatch.setattr(main, "build_expiry_sweep", fake_build_expiry_sweep)
    monkeypatch.setattr(main, "build_poller", fake_build_poller)

    app = FastAPI()
    await main._start_background_worker(app)
    assert app.state.background_worker_task is not None
    assert len(sweeps_built) == 1
    assert sweeps_built[0].work_scope == ("bulkhead", engines[0])
    await asyncio.wait_for(sweeps_built[0].ran.wait(), timeout=1)
    # The collection expiry sweep (ADR-0024 §8) folds into the same group, on the same bulkhead.
    assert len(expiry_built) == 1
    assert expiry_built[0].work_scope == ("bulkhead", engines[0])
    await asyncio.wait_for(expiry_built[0].ran.wait(), timeout=1)
    # So does the charge poller (ADR-0032): the only thing that learns a platform charge's outcome
    # while the provider webhooks are private, on the same bulkhead.
    assert len(poller_built) == 1
    assert poller_built[0].work_scope == ("bulkhead", engines[0])
    await asyncio.wait_for(poller_built[0].ran.wait(), timeout=1)
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


_RZP_TEST_ID = "rzp_test_Devonly000001"
_RZP_SECRET = "dev-only-razorpay-secret-0001"  # pragma: allowlist secret


def _production(*, allow_test_keys: bool) -> Settings:
    """A real production `Settings` (start-up validators and all): Razorpay test keys over a live
    wire — the staging box's shape, accepted only with `PAYMENT_ALLOW_TEST_KEYS` — or the same keys
    on the stub without it."""
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        environment="production",
        internal_service_token=SecretStr("a" * 48),
        pii_encryption_key=SecretStr("b" * 48),
        razorpay_webhook_secret=SecretStr("c" * 48),
        payment_allow_test_keys=allow_test_keys,
        payment_wire="live" if allow_test_keys else "stub",
        razorpay_key_id=_RZP_TEST_ID,
        razorpay_key_secret=SecretStr(_RZP_SECRET),
    )


@pytest.mark.parametrize("allow_test_keys", [True, False])
async def test_start_up_announces_test_mode_once_and_only_when_allowed(
    monkeypatch: pytest.MonkeyPatch, engines: list[_FakeEngine], allow_test_keys: bool
) -> None:
    """The lifespan hook itself (`create_app(on_startup=...)`): one WARNING naming test mode when
    `PAYMENT_ALLOW_TEST_KEYS` is on, none when it is off, and the worker hook still runs after
    it."""
    _patch_common(monkeypatch)
    cfg = _production(allow_test_keys=allow_test_keys)
    monkeypatch.setattr(main, "settings", lambda: cfg)
    app = FastAPI()
    with structlog.testing.capture_logs() as logs:
        await main._on_startup(app)
    announced = [line for line in logs if line["event"] == "payment.test_mode.enabled"]
    if allow_test_keys:
        assert len(announced) == 1 and announced[0]["log_level"] == "warning"
        assert announced[0]["mode"] == "test"
        assert announced[0]["test_mode_providers"] == ["razorpay"]
        assert _RZP_TEST_ID not in repr(logs) and _RZP_SECRET not in repr(logs)
    else:
        assert announced == []
    # The worker hook ran after it (off by default, so nothing started).
    assert app.state.background_worker_task is None
    await main._stop_background_worker(app)
