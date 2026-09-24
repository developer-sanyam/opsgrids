"""`app.worker.main()` wires all four loops — the events consumer, the dispatcher, the campaign
driver and the dispatch recovery sweep — into one `run_supervised` group (memory-headroom slice,
2026-09-23 fix round; the recovery sweep joined it with NOTIF-A).

The recovery sweep is the one whose absence nothing notices: a worker without it still consumes
and dispatches, and every message its provider fumbles once is silently never retried. So its
presence in the group is pinned here rather than left to a reader of `worker.py`.

No real database or broker: every DB-role assertion and engine constructor `main()` calls is
faked to a no-op, and `run_supervised` itself is faked to simply await whatever it was given —
this is a wiring test, not a behavioural one (that is `platform_core`'s own
`test_supervise.py`, and `test_cron_run.py` for the campaign driver's own guard).
"""

from __future__ import annotations

from typing import Any

import pytest

from app import worker


async def test_main_wires_all_four_loops_into_run_supervised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _noop(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(worker, "assert_app_role", _noop)
    monkeypatch.setattr(worker, "assert_outbox_role", _noop)
    monkeypatch.setattr(worker, "init_engine", lambda cfg: object())
    monkeypatch.setattr(worker, "init_outbox_engine", lambda cfg: None)
    monkeypatch.setattr(worker, "get_outbox_engine", lambda: object())
    monkeypatch.setattr(worker, "get_outbox_sessionmaker", lambda: object())
    monkeypatch.setattr(worker.consumers, "register", lambda *a, **kw: None)
    monkeypatch.setattr(worker.dispatcher, "register", lambda *a, **kw: None)

    ran: set[str] = set()

    async def fake_events_run() -> None:
        ran.add("events")

    async def fake_dispatch_run() -> None:
        ran.add("dispatch")

    async def fake_driver_run(self: worker.CampaignDriver) -> None:
        ran.add("campaign_driver")

    async def fake_recovery_run(self: worker.DispatchRecovery) -> None:
        ran.add("dispatch_recovery")

    monkeypatch.setattr(worker.consumers.consumer, "run", fake_events_run)
    monkeypatch.setattr(worker.dispatcher.consumer, "run", fake_dispatch_run)
    monkeypatch.setattr(worker.CampaignDriver, "run", fake_driver_run)
    monkeypatch.setattr(worker.DispatchRecovery, "run", fake_recovery_run)

    captured: dict[str, Any] = {}

    async def fake_run_supervised(**named_coroutines: Any) -> None:
        captured.update(named_coroutines)
        for coro in named_coroutines.values():
            await coro

    monkeypatch.setattr(worker, "run_supervised", fake_run_supervised)

    shutdown_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        worker.CAMPAIGN_SMTP_EXECUTOR,
        "shutdown",
        lambda **kwargs: shutdown_calls.append(kwargs),
    )

    await worker.main()

    expected = {"events", "dispatch", "campaign_driver", "dispatch_recovery"}
    assert set(captured) == expected
    assert ran == expected
    # L11 (review round 2): the campaign thread pool must not outlive this process — a
    # non-daemon thread pool with a call in flight would otherwise hold the interpreter open
    # past the point the orchestrator expects this container to have stopped.
    assert shutdown_calls == [{"wait": False, "cancel_futures": True}]


async def test_campaign_smtp_executor_is_shut_down_even_when_run_supervised_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _noop(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(worker, "assert_app_role", _noop)
    monkeypatch.setattr(worker, "assert_outbox_role", _noop)
    monkeypatch.setattr(worker, "init_engine", lambda cfg: object())
    monkeypatch.setattr(worker, "init_outbox_engine", lambda cfg: None)
    monkeypatch.setattr(worker, "get_outbox_engine", lambda: object())
    monkeypatch.setattr(worker, "get_outbox_sessionmaker", lambda: object())
    monkeypatch.setattr(worker.consumers, "register", lambda *a, **kw: None)
    monkeypatch.setattr(worker.dispatcher, "register", lambda *a, **kw: None)

    async def fake_run_supervised(**named_coroutines: Any) -> None:
        # Close what was handed in rather than leave never-awaited coroutines to warn at GC.
        for coro in named_coroutines.values():
            coro.close()
        raise RuntimeError("a loop died")

    monkeypatch.setattr(worker, "run_supervised", fake_run_supervised)

    shutdown_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        worker.CAMPAIGN_SMTP_EXECUTOR,
        "shutdown",
        lambda **kwargs: shutdown_calls.append(kwargs),
    )

    with pytest.raises(RuntimeError, match="a loop died"):
        await worker.main()

    assert shutdown_calls == [{"wait": False, "cancel_futures": True}]


async def test_the_recovery_sweeps_discovery_is_the_outbox_sessionmaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The silent-stall shape (CLAUDE.md): discovery is cross-tenant, and under the app role with
    no tenant bound it selects nothing — a `DispatchRecovery` built on the wrong sessionmaker
    looks exactly like a healthy, idle sweep while every message that ever fails once sits
    `pending` forever. Pinned on the object identity `main()` actually passes, not on a mock that
    would pass whatever it was told to."""

    async def _noop(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(worker, "assert_app_role", _noop)
    monkeypatch.setattr(worker, "assert_outbox_role", _noop)
    monkeypatch.setattr(worker, "init_engine", lambda cfg: object())
    monkeypatch.setattr(worker, "init_outbox_engine", lambda cfg: None)
    monkeypatch.setattr(worker, "get_outbox_engine", lambda: object())

    outbox_sessionmaker = object()
    monkeypatch.setattr(worker, "get_outbox_sessionmaker", lambda: outbox_sessionmaker)
    monkeypatch.setattr(worker.consumers, "register", lambda *a, **kw: None)
    monkeypatch.setattr(worker.dispatcher, "register", lambda *a, **kw: None)

    captured: dict[str, Any] = {}

    async def fake_run_supervised(**named_coroutines: Any) -> None:
        captured.update(named_coroutines)
        for coro in named_coroutines.values():
            coro.close()

    monkeypatch.setattr(worker, "run_supervised", fake_run_supervised)
    monkeypatch.setattr(worker.CAMPAIGN_SMTP_EXECUTOR, "shutdown", lambda **kwargs: None)

    built: list[worker.DispatchRecovery] = []
    real_init = worker.DispatchRecovery.__init__

    def spy_init(self: worker.DispatchRecovery, **kwargs: Any) -> None:
        built.append(self)
        real_init(self, **kwargs)

    monkeypatch.setattr(worker.DispatchRecovery, "__init__", spy_init)

    await worker.main()

    assert "dispatch_recovery" in captured
    (recovery,) = built
    assert recovery._discovery_factory is outbox_sessionmaker
