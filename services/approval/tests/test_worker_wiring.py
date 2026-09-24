"""`app.worker.main()` wires the hierarchy/regularization consumer and the SLA escalation sweep
into one `run_supervised` group (memory-headroom slice, 2026-09-23 fix round).

No real database or broker: every DB-role assertion and engine constructor `main()` calls is
faked to a no-op, and `run_supervised` itself is faked to simply await whatever it was given —
this is a wiring test, not a behavioural one (that is `platform_core`'s own
`test_supervise.py`, and `test_sweep_run.py` for the sweep's own guard).
"""

from __future__ import annotations

from typing import Any

import pytest

from app import worker


async def test_main_wires_consumer_and_sla_escalation_into_run_supervised(
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

    ran: set[str] = set()

    async def fake_consumer_run() -> None:
        ran.add("consumer")

    async def fake_sweep_run(self: worker.EscalationSweep) -> None:
        ran.add("sla_escalation")

    monkeypatch.setattr(worker.consumers.consumer, "run", fake_consumer_run)
    monkeypatch.setattr(worker.EscalationSweep, "run", fake_sweep_run)

    captured: dict[str, Any] = {}

    async def fake_run_supervised(**named_coroutines: Any) -> None:
        captured.update(named_coroutines)
        for coro in named_coroutines.values():
            await coro

    monkeypatch.setattr(worker, "run_supervised", fake_run_supervised)

    await worker.main()

    assert set(captured) == {"consumer", "sla_escalation"}
    assert ran == {"consumer", "sla_escalation"}
