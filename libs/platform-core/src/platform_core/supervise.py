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
from pathlib import Path
from typing import Any

from platform_core.logging import get_logger

log = get_logger("platform_core.supervise")


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


class GuardedSweep:
    """A base for the claim-and-drain sweep shape this platform repeats (dunning, SLA
    escalation, campaign sends, the scheduler's own due-job sweep): discover, then act on each
    candidate under its own tenant binding.

    Subclasses implement `tick()` (discover and act, once) and may override `on_tick_success` to
    log or react to a tick's own result — `run()` itself is not meant to be overridden. The guard
    this class exists to add is around the **whole tick**, not around each individual candidate a
    tick acts on (a subclass's own `tick()` is still responsible for isolating one candidate's
    failure from the rest of its own pass, the way `EscalationSweep._escalate` and
    `CampaignDriver._drain` already do): a discovery failure — a DB restart, a deadlock — is
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

    def stop(self) -> None:
        self._stop = True

    async def tick(self) -> Any:
        raise NotImplementedError

    async def on_tick_success(self, result: Any) -> None:
        """Override to log or act on a successful tick's own result. Default: nothing — a
        subclass that already logs inside its own `tick()` needs no override here."""

    async def run(self) -> None:
        log.info(
            f"{self._log_name}.started",
            poll_interval=self._poll_interval,
            max_backoff=self._max_backoff,
        )
        backoff = self._poll_interval
        while not self._stop:
            try:
                result = await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception(f"{self._log_name}.tick_failed")
                self._write_heartbeat()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._max_backoff)
                continue
            backoff = self._poll_interval
            self._write_heartbeat()
            await self.on_tick_success(result)
            await asyncio.sleep(self._poll_interval)

    @property
    def _log_name(self) -> str:
        return self.__class__.__name__.lower()

    def _write_heartbeat(self) -> None:
        """Best-effort. A heartbeat file existing to be read by something outside this process
        (an eventual health-key mechanism) must never itself be the reason this loop stops —
        a disk error here is logged once and otherwise ignored."""
        if self._heartbeat_path is None:
            return
        try:
            Path(self._heartbeat_path).write_text(str(asyncio.get_running_loop().time()))
        except OSError:
            log.warning("supervise.heartbeat_write_failed", path=self._heartbeat_path)
