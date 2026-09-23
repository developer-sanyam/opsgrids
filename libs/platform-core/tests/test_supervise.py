"""`run_supervised` and `GuardedSweep` (platform_core.supervise).

No real database or broker: every loop here is a plain coroutine or a `GuardedSweep` subclass
whose `tick()` is scripted, the same "fake the loop, drive the real supervision code" shape
`test_outbox_relay_idle_backoff.py` uses for `OutboxRelay.run()`.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from platform_core.supervise import (
    GuardedSweep,
    LoopExited,
    run_supervised,
    start_background_sweep,
    stop_background_sweep,
)


class TestRunSupervised:
    async def test_a_returning_coroutine_ends_the_group_and_cancels_the_rest(self) -> None:
        cancelled = asyncio.Event()

        async def returns_immediately() -> None:
            return None

        async def runs_forever() -> None:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        with pytest.raises(LoopExited) as exc_info:
            await run_supervised(quitter=returns_immediately(), forever=runs_forever())
        assert exc_info.value.name == "quitter"
        assert cancelled.is_set()

    async def test_a_raising_coroutine_propagates_its_own_exception_and_cancels_the_rest(
        self,
    ) -> None:
        cancelled = asyncio.Event()

        async def raises() -> None:
            raise RuntimeError("boom")

        async def runs_forever() -> None:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        with pytest.raises(RuntimeError, match="boom"):
            await run_supervised(bad=raises(), forever=runs_forever())
        assert cancelled.is_set()

    async def test_requires_at_least_one_coroutine(self) -> None:
        with pytest.raises(ValueError):
            await run_supervised()

    async def test_a_sibling_that_also_raises_while_being_cancelled_does_not_mask_the_first_error(
        self,
    ) -> None:
        async def raises_first() -> None:
            raise RuntimeError("first")

        async def raises_on_cancel() -> None:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                raise RuntimeError("teardown blew up too") from None

        with pytest.raises(RuntimeError, match="first"):
            await run_supervised(a=raises_first(), b=raises_on_cancel())


class _ScriptedSweep(GuardedSweep):
    """A `GuardedSweep` whose `tick()` plays back a scripted list of outcomes, then stops —
    the same driving shape `scheduler/tests/test_sweep_run.py` uses for `Sweep.run()`."""

    def __init__(
        self,
        outcomes: list[object],
        *,
        poll_interval: float,
        max_backoff: float = 300.0,
        heartbeat_path: str | None = None,
    ) -> None:
        super().__init__(
            poll_interval=poll_interval, max_backoff=max_backoff, heartbeat_path=heartbeat_path
        )
        self._outcomes = list(outcomes)
        self.successes: list[object] = []

    async def tick(self) -> object:
        outcome = self._outcomes.pop(0)
        if not self._outcomes:
            self.stop()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def on_tick_success(self, result: object) -> None:
        if isinstance(result, _RaiseInOnSuccess):
            raise result.exc
        self.successes.append(result)


class _RaiseInOnSuccess:
    """A `tick()` result that makes `on_tick_success` itself raise — the review-round-2 finding
    (L9): a bug in a subclass's own success handling must be caught by the same guard a
    discovery failure is, not escape past it."""

    def __init__(self, exc: Exception) -> None:
        self.exc = exc


class TestGuardedSweep:
    async def test_a_raising_tick_does_not_kill_the_loop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        sweep = _ScriptedSweep(
            [RuntimeError("db gone"), RuntimeError("db gone"), "ok"],
            poll_interval=5.0,
            max_backoff=60.0,
        )
        await sweep.run()
        # Exponential from poll_interval, doubling on each consecutive failure; the clean tick
        # after resets to the ordinary poll_interval sleep.
        assert sleeps == [5.0, 10.0, 5.0]
        assert sweep.successes == ["ok"]

    async def test_backoff_is_capped_at_max_backoff(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        sweep = _ScriptedSweep(
            [RuntimeError("x"), RuntimeError("x"), RuntimeError("x"), "ok"],
            poll_interval=5.0,
            max_backoff=15.0,
        )
        await sweep.run()
        assert sleeps == [5.0, 10.0, 15.0, 5.0]

    async def test_cancelled_error_propagates_and_is_not_swallowed(self) -> None:
        class _CancelsItself(GuardedSweep):
            async def tick(self) -> None:
                raise asyncio.CancelledError()

        sweep = _CancelsItself(poll_interval=1.0)
        with pytest.raises(asyncio.CancelledError):
            await sweep.run()

    async def test_heartbeat_file_written_on_success_and_on_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_sleep(seconds: float) -> None:
            return None

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        heartbeat = str(tmp_path / "heartbeat")
        sweep = _ScriptedSweep(
            [RuntimeError("x"), "ok"], poll_interval=1.0, heartbeat_path=heartbeat
        )
        await sweep.run()
        payload = json.loads(Path(heartbeat).read_text())  # noqa: ASYNC240 - a sync read in a test, nothing real to block
        assert payload["ok"] is True
        assert payload["consecutive_failures"] == 0
        assert isinstance(payload["ts"], float)

    async def test_a_missing_heartbeat_directory_does_not_kill_the_loop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_sleep(seconds: float) -> None:
            return None

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        sweep = _ScriptedSweep(
            ["ok"], poll_interval=1.0, heartbeat_path="/no/such/directory/heartbeat"
        )
        await sweep.run()  # must not raise
        assert sweep.successes == ["ok"]

    async def test_consecutive_failures_counts_up_and_resets_on_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_sleep(seconds: float) -> None:
            return None

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        sweep = _ScriptedSweep([RuntimeError("x"), RuntimeError("x"), "ok"], poll_interval=1.0)
        await sweep.run()
        assert sweep.consecutive_failures == 0

    async def test_heartbeat_reports_the_failure_streak(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_sleep(seconds: float) -> None:
            return None

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        heartbeat = str(tmp_path / "heartbeat")
        # Both ticks raise; the second one's own pop empties the scripted outcomes and calls
        # stop() before raising, so run() ends after exactly two failed ticks.
        sweep = _ScriptedSweep(
            [RuntimeError("x"), RuntimeError("x")], poll_interval=1.0, heartbeat_path=heartbeat
        )
        await sweep.run()
        payload = json.loads(Path(heartbeat).read_text())  # noqa: ASYNC240 - a sync read in a test, nothing real to block
        assert payload["ok"] is False
        assert payload["consecutive_failures"] == 2

    async def test_a_raising_on_tick_success_is_caught_by_the_same_guard(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """L9 (review round 2): on_tick_success runs inside the guarded block — a bug there
        must back off and retry, exactly like a raising tick(), never kill the loop."""
        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        sweep = _ScriptedSweep(
            [_RaiseInOnSuccess(RuntimeError("logging bug")), "ok"],
            poll_interval=5.0,
            max_backoff=60.0,
        )
        await sweep.run()
        assert sleeps == [5.0, 5.0]
        assert sweep.successes == ["ok"]


class TestBackgroundSweep:
    async def test_stop_on_a_never_started_task_is_a_no_op(self) -> None:
        await stop_background_sweep(None)  # must not raise

    async def test_start_then_stop_cancels_the_sweep_cleanly(self) -> None:
        cancelled = asyncio.Event()

        async def forever() -> None:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = start_background_sweep(forever())
        await asyncio.sleep(0)  # let it actually start
        await stop_background_sweep(task)
        assert cancelled.is_set()
        assert task.done()

    async def test_a_sweep_that_finishes_on_its_own_is_not_an_error_to_stop(self) -> None:
        async def finishes() -> None:
            return None

        task = start_background_sweep(finishes())
        await asyncio.sleep(0)
        await stop_background_sweep(task)  # must not raise even though it already completed
