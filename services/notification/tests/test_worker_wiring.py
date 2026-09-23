"""`app.worker.main()` wires all three loops — the events consumer, the dispatcher and the
campaign driver — into one `run_supervised` group (memory-headroom slice, 2026-09-23 fix round).

No real database or broker: every DB-role assertion and engine constructor `main()` calls is
faked to a no-op, and `run_supervised` itself is faked to simply await whatever it was given —
this is a wiring test, not a behavioural one (that is `platform_core`'s own
`test_supervise.py`, and `test_cron_run.py` for the campaign driver's own guard).
"""

from __future__ import annotations

from typing import Any

import pytest

from app import worker


async def test_main_wires_events_dispatch_and_campaign_driver_into_run_supervised(
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

    monkeypatch.setattr(worker.consumers.consumer, "run", fake_events_run)
    monkeypatch.setattr(worker.dispatcher.consumer, "run", fake_dispatch_run)
    monkeypatch.setattr(worker.CampaignDriver, "run", fake_driver_run)

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

    assert set(captured) == {"events", "dispatch", "campaign_driver"}
    assert ran == {"events", "dispatch", "campaign_driver"}
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
