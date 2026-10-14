"""`run_supervised` and `GuardedSweep` (platform_core.supervise).

No real database or broker: every loop here is a plain coroutine or a `GuardedSweep` subclass
whose `tick()` is scripted, the same "fake the loop, drive the real supervision code" shape
`test_outbox_relay_idle_backoff.py` uses for `OutboxRelay.run()`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any

import pytest
import structlog

from platform_core.metrics import Sample
from platform_core.supervise import (
    GuardedSweep,
    LoopExited,
    SweepHealth,
    get_sweep_health,
    register_sweep_health,
    reset_sweep_health,
    run_supervised,
    run_supervised_forever,
    start_background_sweep,
    stop_background_sweep,
    sweep_health_samples,
)


@pytest.fixture(autouse=True)
def _clean_sweep_registry():  # type: ignore[no-untyped-def]
    """The registry is process-wide (that is the point of it); no test may see another's."""
    reset_sweep_health()
    yield
    reset_sweep_health()


def _registered(name: str) -> SweepHealth:
    health = get_sweep_health(name)
    assert health is not None, f"no sweep registered as {name!r}"
    return health


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


class TestFirstBackoffNeverExceedsTheCap:
    """Round 3, LOW-MED: backoff began at `poll_interval` and was only *capped* after the first
    sleep, so a daily sweep (86,400 s) with the default 300 s cap waited a whole day after its
    first failed tick — one DB blip meant no reconciliation for 24 hours."""

    async def test_a_daily_sweep_retries_after_the_cap_not_after_a_day(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        sweep = _ScriptedSweep([RuntimeError("blip"), "ok"], poll_interval=86_400.0)
        await sweep.run()
        assert sleeps == [300.0, 86_400.0]

    async def test_the_reset_after_a_clean_tick_also_respects_the_cap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        sweep = _ScriptedSweep(
            [RuntimeError("x"), "ok", RuntimeError("x"), "ok"], poll_interval=86_400.0
        )
        await sweep.run()
        assert sleeps == [300.0, 86_400.0, 300.0, 86_400.0]


class TestSweepHealth:
    """`SweepHealth` and the process-wide registry `/metrics` renders (round 3, MEDIUM: an
    in-API sweep that died or kept failing was invisible while the API answered healthily)."""

    async def test_a_guarded_sweep_registers_itself_and_records_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_sleep(seconds: float) -> None:
            return None

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        sweep = _ScriptedSweep(["ok"], poll_interval=1.0)
        assert sweep.health.last_success_ts == 0.0
        await sweep.run()
        assert sweep.health.consecutive_failures == 0
        assert sweep.health.last_success_ts > 1_700_000_000  # wall-clock epoch seconds
        assert sweep.health is _registered(sweep._log_name)

    async def test_failures_are_counted_and_success_resets_the_streak(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        streaks: list[int] = []

        async def fake_sleep(seconds: float) -> None:
            return None

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        sweep = _ScriptedSweep([RuntimeError("x"), RuntimeError("x"), "ok"], poll_interval=1.0)
        original = sweep.tick

        async def spying_tick() -> object:
            streaks.append(sweep.health.consecutive_failures)
            return await original()

        sweep.tick = spying_tick  # type: ignore[method-assign]
        await sweep.run()
        assert streaks == [0, 1, 2]
        assert sweep.health.consecutive_failures == 0

    def test_samples_are_grouped_by_metric_and_carry_the_sweep_label(self) -> None:
        register_sweep_health("b.sweep").record_failure()
        a = register_sweep_health("a.sweep")
        a.record_success()
        a.alive = True
        samples = sweep_health_samples()

        def label(sample: Sample) -> str:
            assert sample.labels is not None
            return sample.labels["sweep"]

        assert [(s.name, label(s)) for s in samples] == [
            ("sweep_alive", "a.sweep"),
            ("sweep_alive", "b.sweep"),
            ("sweep_consecutive_failures", "a.sweep"),
            ("sweep_consecutive_failures", "b.sweep"),
            ("sweep_last_success_timestamp_seconds", "a.sweep"),
            ("sweep_last_success_timestamp_seconds", "b.sweep"),
        ]
        by_key = {(s.name, label(s)): s.value for s in samples}
        assert by_key[("sweep_alive", "a.sweep")] == 1.0
        assert by_key[("sweep_alive", "b.sweep")] == 0.0
        assert by_key[("sweep_consecutive_failures", "b.sweep")] == 1.0
        assert by_key[("sweep_last_success_timestamp_seconds", "b.sweep")] == 0.0  # never

    def test_no_registered_sweep_means_no_samples(self) -> None:
        assert sweep_health_samples() == []

    def test_a_newer_sweep_replaces_a_stale_record_of_the_same_name(self) -> None:
        first = register_sweep_health("x.sweep")
        first.record_failure()
        second = register_sweep_health("x.sweep")
        assert second.consecutive_failures == 0
        assert _registered("x.sweep") is second


class TestBackgroundSweepIsLoudWhenItDies:
    async def test_a_sweep_that_raises_is_logged_at_error_and_marked_not_alive(self) -> None:
        register_sweep_health("dies.sweep")

        async def dies() -> None:
            raise RuntimeError("boom")

        with structlog.testing.capture_logs() as logs:
            task = start_background_sweep(dies(), name="dies.sweep")
            await asyncio.wait({task})
            await asyncio.sleep(0)  # let the done-callback run
        exited = [line for line in logs if line["event"] == "supervise.background_sweep_exited"]
        assert len(exited) == 1
        assert exited[0]["log_level"] == "error"
        assert exited[0]["sweep"] == "dies.sweep"
        assert exited[0]["returned"] is False
        assert "boom" in exited[0]["error"]
        assert _registered("dies.sweep").alive is False

    async def test_a_sweep_that_merely_returns_is_also_an_error(self) -> None:
        """A `while True` turned into a bounded loop by a bad edit ends the task without raising —
        just as fatal to the sweep, and just as invisible before this."""

        async def returns() -> None:
            return None

        with structlog.testing.capture_logs() as logs:
            task = start_background_sweep(returns(), name="returns.sweep")
            await asyncio.wait({task})
            await asyncio.sleep(0)
        exited = [line for line in logs if line["event"] == "supervise.background_sweep_exited"]
        assert len(exited) == 1
        assert exited[0]["returned"] is True

    async def test_a_running_sweep_is_alive_and_a_clean_stop_is_not_an_error(self) -> None:
        async def forever() -> None:
            await asyncio.sleep(60)

        with structlog.testing.capture_logs() as logs:
            task = start_background_sweep(forever(), name="lives.sweep")
            await asyncio.sleep(0)
            assert _registered("lives.sweep").alive is True
            await stop_background_sweep(task)
            await asyncio.sleep(0)
        assert not [line for line in logs if line["event"] == "supervise.background_sweep_exited"]
        assert _registered("lives.sweep").alive is False

    async def test_stopping_a_sweep_that_already_died_does_not_raise_at_shutdown(self) -> None:
        async def dies() -> None:
            raise RuntimeError("boom")

        task = start_background_sweep(dies(), name="dead.sweep")
        await asyncio.wait({task})
        await stop_background_sweep(task)  # the lifespan's shutdown must not fail with it


class TestRunSupervisedForever:
    """The worker-folding follow-up's own primitive: what a folded worker's group is wrapped in
    before it is handed to `start_background_sweep`, so a crashed loop restarts with backoff
    instead of ending the group (and, through `start_background_sweep`'s own contract, silently
    staying dead) for good."""

    async def test_a_crashed_group_is_rebuilt_and_restarted_after_a_backoff(self) -> None:
        attempts: list[int] = []
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        restarted_at: float | None = None
        restarted = asyncio.Event()

        def build() -> dict[str, Any]:
            attempt = len(attempts)
            attempts.append(attempt)

            async def one_loop() -> None:
                nonlocal restarted_at
                if attempt == 0:
                    raise RuntimeError("boom")
                restarted_at = loop.time()
                restarted.set()
                await asyncio.sleep(60)

            return {"loop": one_loop()}

        backoff = 0.05
        task = asyncio.ensure_future(
            run_supervised_forever(build, name="probe.group", initial_backoff=backoff)
        )
        # Wait for the restarted loop to actually *run*, not for `build` to have been called a
        # second time: `build` returns a coroutine that only starts a scheduler turn later, so a
        # poll that lands between the two sees two attempts and no restart yet. On a loaded CI
        # runner that gap was wide enough to fail the assertion below.
        await asyncio.wait_for(restarted.wait(), timeout=10)

        assert len(attempts) == 2, "the group must be rebuilt (a fresh coroutine) on restart"
        assert restarted_at is not None
        # Real time, not exact: the point is that the restart waited roughly `initial_backoff`
        # rather than looping immediately (a crash-loop that would starve the event loop and spam
        # logs), not that it waited exactly that long.
        assert restarted_at - started_at >= backoff * 0.5
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def test_a_returned_loop_also_triggers_a_restart_not_just_a_raised_one(self) -> None:
        attempts: list[int] = []

        def build() -> dict[str, Any]:
            attempt = len(attempts)
            attempts.append(attempt)

            async def loop() -> None:
                if attempt == 0:
                    return  # LoopExited, not an exception — must restart all the same
                await asyncio.sleep(60)

            return {"loop": loop()}

        task = asyncio.ensure_future(
            run_supervised_forever(build, name="probe.group", initial_backoff=0.01)
        )
        for _ in range(200):
            if len(attempts) >= 2:
                break
            await asyncio.sleep(0.01)
        assert len(attempts) == 2
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def test_cancellation_of_the_supervisor_itself_is_never_swallowed(self) -> None:
        async def loop() -> None:
            await asyncio.sleep(60)

        task = asyncio.ensure_future(
            run_supervised_forever(lambda: {"loop": loop()}, name="probe.group")
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_a_healthy_group_never_restarts(self) -> None:
        attempts: list[int] = []

        def build() -> dict[str, Any]:
            attempts.append(len(attempts))

            async def loop() -> None:
                await asyncio.sleep(60)

            return {"loop": loop()}

        task = asyncio.ensure_future(run_supervised_forever(build, name="probe.group"))
        await asyncio.sleep(0.05)
        assert attempts == [0]
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


class _CountingSweep(GuardedSweep):
    """A sweep whose ticks are scripted by the test, to drive the real `run()` in real time."""

    def __init__(self, *, poll_interval: float, max_backoff: float = 300.0) -> None:
        super().__init__(poll_interval=poll_interval, max_backoff=max_backoff)
        self.ticks = 0
        self.fail = False

    async def tick(self) -> int:
        self.ticks += 1
        if self.fail:
            raise RuntimeError("db gone")
        return self.ticks


async def _wait_for(predicate: Any, *, within: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + within
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "condition not reached"
        await asyncio.sleep(0.005)


class TestInterruptibleSleep:
    """`stop()` must end a sleeping sweep at once, not after the rest of its poll interval — a
    daily sweep (86,400 s) must not hold a shutdown for a day."""

    async def test_stop_interrupts_the_poll_interval_sleep(self) -> None:
        sweep = _CountingSweep(poll_interval=3600.0)
        task = asyncio.ensure_future(sweep.run())
        await _wait_for(lambda: sweep.ticks == 1)
        await asyncio.sleep(0.02)  # now inside the hour-long sleep
        assert not task.done()
        sweep.stop()
        await asyncio.wait_for(task, timeout=2.0)
        assert sweep.ticks == 1

    async def test_stop_interrupts_a_failure_backoff(self) -> None:
        sweep = _CountingSweep(poll_interval=3600.0, max_backoff=3600.0)
        sweep.fail = True
        task = asyncio.ensure_future(sweep.run())
        await _wait_for(lambda: sweep.health.consecutive_failures == 1)
        await asyncio.sleep(0.02)  # now inside the backoff sleep
        assert not task.done()
        sweep.stop()
        await asyncio.wait_for(task, timeout=2.0)

    async def test_stop_before_run_ends_after_no_tick(self) -> None:
        sweep = _CountingSweep(poll_interval=3600.0)
        sweep.stop()
        await asyncio.wait_for(sweep.run(), timeout=2.0)
        assert sweep.ticks == 0

    async def test_cancelling_a_sleeping_sweep_leaves_no_helper_task_behind(self) -> None:
        sweep = _CountingSweep(poll_interval=3600.0)
        before = {t for t in asyncio.all_tasks() if t is not asyncio.current_task()}
        task = asyncio.ensure_future(sweep.run())
        await _wait_for(lambda: sweep.ticks == 1)
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        leftover = {t for t in asyncio.all_tasks() if t is not asyncio.current_task()} - before
        assert not leftover

    async def test_an_uninterrupted_sleep_still_waits_its_interval(self) -> None:
        sweep = _CountingSweep(poll_interval=0.02)
        task = asyncio.ensure_future(sweep.run())
        await _wait_for(lambda: sweep.ticks >= 3)
        sweep.stop()
        await asyncio.wait_for(task, timeout=2.0)


class _HookedSweep(GuardedSweep):
    """Scripted `_delay_after` / `_tick_succeeded` overrides."""

    def __init__(self, delays: list[float], successes: list[bool]) -> None:
        super().__init__(poll_interval=5.0, max_backoff=60.0)
        self._delays = delays
        self._successes = successes
        self.ticks = 0

    async def tick(self) -> int:
        self.ticks += 1
        if self.ticks == len(self._delays):
            self.stop()
        return self.ticks

    def _delay_after(self, result: int) -> float:
        return self._delays[result - 1]

    def _tick_succeeded(self, result: int) -> bool:
        return self._successes[result - 1]


class TestRunHooks:
    async def test_delay_after_sets_the_sleep_and_zero_skips_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        sweep = _HookedSweep([5.0, 0.0, 10.0, 20.0], [True] * 4)
        await sweep.run()
        assert sleeps == [5.0, 10.0, 20.0]

    async def test_a_non_success_tick_counts_as_a_failure_without_the_failure_backoff(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        sweep = _HookedSweep([5.0, 5.0, 5.0], [False, False, True])
        await sweep.run()
        assert sleeps == [5.0, 5.0, 5.0]  # the poll delay each time, never a doubled backoff
        assert sweep.health.consecutive_failures == 0  # the last tick succeeded and reset it
        assert sweep.health.last_success_ts > 0

    async def test_a_non_success_tick_leaves_the_streak_standing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_sleep(seconds: float) -> None:
            return None

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        sweep = _HookedSweep([5.0, 5.0], [False, False])
        await sweep.run()
        assert sweep.health.consecutive_failures == 2
        assert sweep.health.last_success_ts == 0.0

    async def test_a_raising_delay_hook_backs_off_instead_of_ending_the_loop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        class _BadDelay(_HookedSweep):
            def _delay_after(self, result: int) -> float:
                if result == 1:
                    raise RuntimeError("bug in the hook")
                return 5.0

        sweep = _BadDelay([0.0, 0.0], [True, True])
        await sweep.run()
        assert sleeps == [5.0, 5.0]  # one failure backoff, then the ordinary delay
        assert sweep.ticks == 2
