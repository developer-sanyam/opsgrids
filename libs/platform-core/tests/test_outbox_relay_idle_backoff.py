"""`OutboxRelay.run()`'s idle backoff.

Production runs 26 relay processes on a 2-vCPU box. Without backoff, an idle platform issues
26 empty `SELECT`s a second forever. The fix: each empty pass doubles the sleep, capped at
`max_idle_interval`, and any pass that publishes at least one row resets it to
`poll_interval` so a backlog still drains at full speed. The failure backoff (`backoff`,
`max_backoff`) is a separate counter and must not move or reset because of idle passes, or
vice versa.

No real broker: `aio_pika.connect_robust` and `_drain` are faked, and `asyncio.sleep` is
replaced with a recorder, the same way `test_outbox_wiring.py`'s `_FakeExchange` stands in
for the broker without one running.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from platform_core.events import OutboxRelay


class _FakeExchange:
    """Never touched: `_drain` is faked out below, so nothing reaches the broker."""


class _FakeChannel:
    async def declare_exchange(self, *args: Any, **kwargs: Any) -> _FakeExchange:
        return _FakeExchange()


class _FakeConnection:
    """Stands in for `aio_pika.connect_robust`'s return value."""

    async def __aenter__(self) -> _FakeConnection:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def channel(self, *, publisher_confirms: bool = True) -> _FakeChannel:
        return _FakeChannel()


async def _fake_connect_robust(url: str) -> _FakeConnection:
    return _FakeConnection()


def _relay(**overrides: Any) -> OutboxRelay:
    return OutboxRelay(
        amqp_url="amqp://unused",
        outbox_cls=object,
        session_factory=None,  # type: ignore[arg-type]  # _drain is faked; never dereferenced
        producer="test",
        **overrides,
    )


async def _run_and_collect(
    relay: OutboxRelay,
    monkeypatch: pytest.MonkeyPatch,
    drain_results: list[tuple[int, int]],
) -> list[float]:
    """Drives `relay.run()` through one `(published, failed)` pair per pass, then stops it.

    Returns every duration `asyncio.sleep` was called with, in order.
    """
    monkeypatch.setattr("platform_core.events.aio_pika.connect_robust", _fake_connect_robust)

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    remaining = list(drain_results)

    async def fake_drain(exchange: Any) -> tuple[int, int]:
        # Stop the relay as we hand back the *last* scripted pass, not after: `run()` still
        # sleeps for that final pass before the loop condition is re-checked, but a "stop
        # after the queue is empty" approach would add one extra, unwanted sleep call.
        result = remaining.pop(0)
        if not remaining:
            relay.stop()
        return result

    monkeypatch.setattr(relay, "_drain", fake_drain)

    await relay.run()
    return sleeps


class TestIdleBackoff:
    async def test_consecutive_empty_passes_double_and_cap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        relay = _relay()  # defaults: poll_interval=1.0, max_idle_interval=5.0
        sleeps = await _run_and_collect(relay, monkeypatch, [(0, 0)] * 5)
        assert sleeps == [1.0, 2.0, 4.0, 5.0, 5.0]

    async def test_a_publishing_pass_resets_the_idle_interval(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        relay = _relay()
        # empty, empty, publish (no sleep at all, backlog drains immediately), empty again.
        sleeps = await _run_and_collect(relay, monkeypatch, [(0, 0), (0, 0), (3, 0), (0, 0)])
        # 1, 2 while idle climbs; the publish adds no sleep and resets it; then back to 1.
        assert sleeps == [1.0, 2.0, 1.0]

    async def test_failure_backoff_is_unaffected_by_idle_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        relay = _relay(max_backoff=60.0)
        # One clean empty pass grows idle to 2 first, then four straight failing passes.
        sleeps = await _run_and_collect(
            relay, monkeypatch, [(0, 0), (0, 1), (0, 1), (0, 1), (0, 1)]
        )
        # sleeps[0] is the idle sleep from the first pass; the rest is the failure backoff,
        # doubling from poll_interval regardless of where idle had climbed to.
        assert sleeps == [1.0, 1.0, 2.0, 4.0, 8.0]

    async def test_max_idle_interval_below_poll_interval_is_a_no_op(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        relay = _relay(poll_interval=2.0, max_idle_interval=1.0)
        sleeps = await _run_and_collect(relay, monkeypatch, [(0, 0)] * 4)
        assert sleeps == [2.0, 2.0, 2.0, 2.0]
