"""Supervision for a worker process that runs more than one always-on loop.

Every worker in this platform is one asyncio process running an event consumer, a sweep, or
both, `gather`ed together so a container's health is one process's health rather than two.
Plain `asyncio.gather(*aws)` does not actually give that: when one awaitable raises,
`gather` (with its default `return_exceptions=False`) propagates the exception immediately, but
it does **not** cancel the other awaitables — they keep running as orphaned tasks, touching a
connection pool and a broker channel `main()`'s own cleanup is about to tear down, until
`asyncio.run()`'s own shutdown eventually cancels them. And if a loop simply *returns* instead of
raising — a bug, not a crash, `while True` turned into `while some_stale_condition` by a bad
edit — `gather` treats that as a completed result and keeps waiting on whatever is left, so half
a worker silently going idle looks exactly like a healthy process to `docker compose ps`.

`run_supervised` fixes both: the first named coroutine to either return or raise ends the group.
Every other loop is cancelled and awaited before this function raises, so there is no reachable
state where one loop is dead and another is still running alone against a half-torn-down process.

`GuardedSweep` is the poisoned-step guard `scheduler.app.cron.Sweep.run()` introduced (ADR-0015
review finding), generalised: a sweep's own *discovery* query raising (a DB restart, a deadlock)
must back off and retry, not kill the loop — and now, since more than one loop typically shares a
process via `run_supervised`, not take the others down with it either. `CancelledError` is never
caught here: a supervisor asking a loop to stop must always be able to.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from platform_core.logging import get_logger
from platform_core.metrics import Sample

log = get_logger("platform_core.supervise")


@dataclass
class SweepHealth:
    """What one background sweep says about itself — the three numbers an alert needs.

    A sweep that runs inside an API process (`start_background_sweep`) fails invisibly: the API
    stays healthy, `docker compose ps` says `healthy`, and the only trace of a dead or
    always-failing sweep is a log line nobody is tailing. These are exposed on that service's own
    `/metrics` (`sweep_health_samples`, rendered by `platform_core.app`'s health router) so an
    ops-monitor check can page on them without anyone reading logs.

    `last_success_ts` is wall-clock (`time.time()`, epoch seconds), 0.0 if the sweep has not
    succeeded since this process started — an alert computes `now - ts`, so "never" and "very
    long ago" deliberately look the same.
    """

    name: str
    alive: bool = False
    consecutive_failures: int = 0
    last_success_ts: float = 0.0

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.last_success_ts = time.time()

    def record_failure(self) -> None:
        self.consecutive_failures += 1


#: Process-wide, by sweep name — the latest sweep constructed under a name wins (a restarted
#: sweep replaces its own stale record; two tests never share one).
_SWEEP_HEALTH: dict[str, SweepHealth] = {}

SWEEP_METRICS_HELP = {
    "sweep_alive": "1 while this process's background sweep task is running, 0 once it has exited.",
    "sweep_consecutive_failures": "Ticks of this background sweep that have failed in a row.",
    "sweep_last_success_timestamp_seconds": (
        "Unix time of the sweep's last successful tick; 0 if it has not succeeded since start."
    ),
}
SWEEP_METRICS_TYPES = dict.fromkeys(SWEEP_METRICS_HELP, "gauge")


def register_sweep_health(name: str) -> SweepHealth:
    """A fresh health record for the sweep called `name`, registered process-wide. `name` is the
    sweep's log-event prefix (`inventory.reconcile`, `storefront.domain.sweep`,
    `document.sweep`), so a metric label and the log line that explains it read the same."""
    health = SweepHealth(name=name)
    _SWEEP_HEALTH[name] = health
    return health


def get_sweep_health(name: str) -> SweepHealth | None:
    """The registered record for `name`, or None. What a test (or an operator's debug shell) reads;
    `/metrics` uses `sweep_health_samples`."""
    return _SWEEP_HEALTH.get(name)


def reset_sweep_health() -> None:
    """Forget every registered sweep. Test isolation only."""
    _SWEEP_HEALTH.clear()


def sweep_health_samples() -> list[Sample]:
    """Every registered sweep's gauges, grouped by metric (Prometheus wants one metric family's
    samples together). Empty when no sweep runs in this process — an API with its sweeps off
    exposes nothing extra."""
    samples: list[Sample] = []
    ordered = sorted(_SWEEP_HEALTH.values(), key=lambda health: health.name)
    for health in ordered:
        samples.append(Sample("sweep_alive", 1.0 if health.alive else 0.0, {"sweep": health.name}))
    for health in ordered:
        samples.append(
            Sample(
                "sweep_consecutive_failures",
                float(health.consecutive_failures),
                {"sweep": health.name},
            )
        )
    for health in ordered:
        samples.append(
            Sample(
                "sweep_last_success_timestamp_seconds",
                health.last_success_ts,
                {"sweep": health.name},
            )
        )
    return samples


class LoopExited(RuntimeError):
    """A supervised loop returned instead of running forever. Raised by `run_supervised` — a
    worker's loops are meant to run until cancelled, so a plain return is a bug this module
    refuses to let look like a clean exit."""

    def __init__(self, name: str) -> None:
        super().__init__(f"supervised loop {name!r} returned instead of running forever")
        self.name = name


async def run_supervised(**named_coroutines: Any) -> None:
    """Run every coroutine as a task; the first to finish — by returning *or* raising — ends
    the group.

    Every other task is cancelled and awaited (with `return_exceptions=True`, so one loop's own
    `CancelledError` or teardown error never masks the reason the group actually ended) before
    this function raises, so a caller never gets control back with a sibling task still running.

    Raises the first-finished task's own exception if it raised one; `LoopExited` naming which
    loop returned, if it merely returned. Never returns normally itself — the same "this must run
    forever" contract every loop passed to it is expected to keep.
    """
    if not named_coroutines:
        raise ValueError("run_supervised requires at least one named coroutine")
    tasks = {name: asyncio.ensure_future(coro) for name, coro in named_coroutines.items()}
    try:
        done, pending = await asyncio.wait(tasks.values(), return_when=asyncio.FIRST_COMPLETED)
    except asyncio.CancelledError:
        # The supervisor itself was cancelled (process shutdown) before anything finished —
        # cancel every loop and let the cancellation propagate, the same contract GuardedSweep
        # gives an individual loop.
        for task in tasks.values():
            task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)
        raise
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    finished_name, finished_task = next(
        (name, task) for name, task in tasks.items() if task in done
    )
    exc = finished_task.exception()
    if exc is not None:
        log.error("supervise.loop_raised", loop=finished_name, error=str(exc))
        raise exc
    log.error("supervise.loop_returned", loop=finished_name)
    raise LoopExited(finished_name)


async def run_supervised_forever(
    build_loops: Callable[[], dict[str, Coroutine[Any, Any, Any]]],
    *,
    name: str,
    initial_backoff: float = 5.0,
    max_backoff: float = 300.0,
) -> None:
    """`run_supervised`, restarted with backoff instead of ending for good — what lets a worker
    folded into an API process (via `start_background_sweep`) recover from a crashed loop the
    same way a standalone worker container does, without taking the API down to get it.

    A standalone `bin/worker` container gets that self-healing for free: `run_supervised` ending
    exits the process non-zero, and `restart: unless-stopped` brings a fresh one back. Folded into
    an API process, a bare `run_supervised(**loops)` passed to `start_background_sweep` would
    instead just die once — logged (`supervise.background_sweep_exited`) and never touched again,
    because `start_background_sweep`'s whole contract is the opposite of that container restart:
    a background task ending must never be allowed to end the process serving requests. This
    closes that gap: `build_loops()` is called fresh on every attempt (a coroutine object can only
    be awaited once, so the loops — `consumer.run()`, `sweep.run()`, ... — must be rebuilt, not
    reused) and `run_supervised` is re-entered after a capped exponential backoff, indefinitely.

    `CancelledError` is never caught — a supervisor asking this to stop (API shutdown) must
    always be able to.
    """
    backoff = initial_backoff
    while True:
        try:
            await run_supervised(**build_loops())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error(f"{name}.crashed", error=str(exc)[:300], retry_in_seconds=backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)


def start_background_sweep(coro: Any, *, name: str | None = None) -> asyncio.Task[None]:
    """Starts a `GuardedSweep.run()` (or any other forever-running coroutine) as a background
    task and returns it, for a caller that is not itself a supervised loop — an ASGI app's own
    `lifespan`, chiefly (the memory-headroom slice's fix round 2: `operations`, `storefront` and
    `document`'s own sweeps now run inside their API process, not a separate `*-cron` container).

    Deliberately **not** `run_supervised`: that primitive's whole point is "any one loop ending
    takes every other one down with it," which is exactly wrong here — a sweep dying must never
    be allowed to end the API process itself, only to stop that one sweep (and `GuardedSweep`'s
    own guard means it should never end on its own at all, short of cancellation). Pair with
    `stop_background_sweep` in the same lifespan's shutdown.

    **A sweep that ends by itself is loud (round 3).** The task carries a done-callback: if it
    finishes without having been cancelled — it raised, or a bug made it return — that is logged
    at error as `supervise.background_sweep_exited`, and (when `name` is given) the sweep's
    `sweep_alive` gauge drops to 0. Before this, a dead in-API sweep left no trace at all: the
    API kept answering, `docker compose ps` said healthy, and the platform quietly stopped
    reconciling stock, verifying domains or purging deleted uploads. A cancellation (shutdown)
    is the normal end and logs nothing.

    `name` is the sweep's registered health name (`register_sweep_health`, which every sweep's
    constructor calls) — the label its `/metrics` gauges carry.
    """
    task: asyncio.Task[None] = asyncio.ensure_future(coro)
    health = _SWEEP_HEALTH.setdefault(name, SweepHealth(name=name)) if name else None
    if health is not None:
        health.alive = True

    def _on_done(finished: asyncio.Task[None]) -> None:
        if health is not None:
            health.alive = False
        if finished.cancelled():
            return
        exc = finished.exception()
        log.error(
            "supervise.background_sweep_exited",
            sweep=name,
            returned=exc is None,
            error=repr(exc) if exc is not None else None,
        )

    task.add_done_callback(_on_done)
    return task


async def stop_background_sweep(task: asyncio.Task[None] | None) -> None:
    """Cancels a task `start_background_sweep` returned and awaits its clean shutdown. `None`
    (the sweep was never started — the setting that gates it was off) is a no-op."""
    if task is None:
        return
    task.cancel()
    # `Exception` too: a sweep that already died (see the done-callback above) would re-raise
    # its own exception here, out of the lifespan's shutdown — already logged, and no reason to
    # make the API's own shutdown fail with it.
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task


class GuardedSweep:
    """A base for the claim-and-drain sweep shape this platform repeats (dunning, SLA
    escalation, campaign sends, the scheduler's own due-job sweep): discover, then act on each
    candidate under its own tenant binding.

    Subclasses implement `tick()` (discover and act, once) and may override `on_tick_success` to
    log or react to a tick's own result — `run()` itself is not meant to be overridden. The guard
    this class exists to add is around **the whole tick, `on_tick_success` included** (review
    finding: a first version of this guard called `on_tick_success` *outside* the guarded block,
    so a bug in a subclass's own logging — a bad format string, an unexpected `None` — killed the
    loop exactly as an unguarded discovery query would have), not around each individual
    candidate a tick acts on (a subclass's own `tick()` is still responsible for isolating one
    candidate's failure from the rest of its own pass, the way `EscalationSweep._escalate` and
    `CampaignDriver._drain` already do). A discovery failure — a DB restart, a deadlock — is
    logged and backed off exponentially, capped at `max_backoff`, and reset to `poll_interval` on
    the next clean tick, without ever propagating out of `run()`. `asyncio.CancelledError` is
    never caught — a supervisor's cancellation must always reach this loop.
    """

    def __init__(
        self,
        *,
        poll_interval: float,
        max_backoff: float = 300.0,
        heartbeat_path: str | None = None,
    ) -> None:
        self._poll_interval = poll_interval
        self._max_backoff = max_backoff
        self._heartbeat_path = heartbeat_path
        self._stop = False
        #: Registered process-wide under this sweep's log name, so an API that runs it in-process
        #: can expose `sweep_consecutive_failures` / `sweep_last_success_timestamp_seconds` /
        #: `sweep_alive` on `/metrics` (`SweepHealth`). Also the source of truth for
        #: `consecutive_failures` below.
        self.health = register_sweep_health(self._log_name)

    @property
    def consecutive_failures(self) -> int:
        """Consecutive failed ticks — reset to 0 the moment one succeeds. This is the number an
        ops-monitor probe cares about far more than any single failure: one bad tick backing off
        is normal operation; a climbing streak is the thing worth paging on. Read from here by a
        caller with a direct reference, from `/metrics` (`sweep_consecutive_failures`) by one
        without, and written into the heartbeat file's own JSON when one is configured."""
        return self.health.consecutive_failures

    @property
    def _initial_backoff(self) -> float:
        """Where a failure streak's backoff starts (and resets to): the poll interval, but never
        more than `max_backoff`. A daily sweep (86,400 s) with the default 300 s cap used to wait
        a whole day after its first failed tick, because backoff began at the poll interval and
        was only *capped* after the first sleep — a one-second DB blip meant no reconciliation
        for 24 hours."""
        return min(self._poll_interval, self._max_backoff)

    def stop(self) -> None:
        self._stop = True

    async def tick(self) -> Any:
        raise NotImplementedError

    async def on_tick_success(self, result: Any) -> None:
        """Override to log or act on a successful tick's own result. Default: nothing — a
        subclass that already logs inside its own `tick()` needs no override here. Runs inside
        the same guarded block `tick()` itself does — see the class docstring."""

    async def run(self) -> None:
        log.info(
            f"{self._log_name}.started",
            poll_interval=self._poll_interval,
            max_backoff=self._max_backoff,
        )
        backoff = self._initial_backoff
        while not self._stop:
            try:
                result = await self.tick()
                await self.on_tick_success(result)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.health.record_failure()
                log.exception(
                    f"{self._log_name}.tick_failed",
                    consecutive_failures=self.consecutive_failures,
                )
                self._write_heartbeat(ok=False)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._max_backoff)
                continue
            backoff = self._initial_backoff
            self.health.record_success()
            self._write_heartbeat(ok=True)
            await asyncio.sleep(self._poll_interval)

    @property
    def _log_name(self) -> str:
        return self.__class__.__name__.lower()

    def _write_heartbeat(self, *, ok: bool) -> None:
        """Best-effort. A heartbeat file existing to be read by something outside this process
        (an eventual ops-monitor health key) must never itself be the reason this loop stops —
        a disk error here is logged once and otherwise ignored. `ts` is wall-clock
        (`time.time()`), not `asyncio`'s own monotonic loop clock, because it is only ever
        meaningful compared against another process's wall clock (or across this one's own
        restarts, when the loop clock resets to zero). Not wired to any real path by any service
        yet — `heartbeat_path` stays an opt-in constructor argument until an ops-monitor probe
        actually exists to read it; no service should pay for a file write nobody consumes.
        """
        if self._heartbeat_path is None:
            return
        payload = json.dumps(
            {
                "service": self._log_name,
                "ts": time.time(),
                "ok": ok,
                "consecutive_failures": self.consecutive_failures,
            }
        )
        try:
            Path(self._heartbeat_path).write_text(payload)
        except OSError:
            log.warning("supervise.heartbeat_write_failed", path=self._heartbeat_path)
