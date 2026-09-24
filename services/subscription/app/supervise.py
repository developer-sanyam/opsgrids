"""Keeping the worker's loops honest: a poisoned pass is retried, a dead loop kills the process.

The worker runs three loops in one process — the payment-event consumer, the dunning sweep and the
billing run (`app/worker.py`). Two ways that went wrong, both fixed here:

* **A sweep pass that raised took the process down**, and with it the consumer — and the same
  poisoned row was first in line on every restart, so the worker crash-looped and payment events
  stopped being consumed. `backoff_loop` catches a failed pass, logs it, and retries after a capped
  exponential delay; the sweeps also guard each row, so one poisoned row never stops the others.
* **`asyncio.gather` did not notice a loop that simply returned** — an `EventConsumer.run()` whose
  iterator ended left a zombie worker with no consumer. `supervise` waits for the **first** loop to
  finish in any way, cancels the rest, and raises: the process exits non-zero and the orchestrator
  restarts it.

`CancelledError` is never swallowed by either: it is a `BaseException`, and the only `except` here
is `Exception`. Deliberately local and small — a shared `platform_core.supervise` helper is on its
way and this should be replaced by it rather than grow.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from platform_core.logging import get_logger

log = get_logger("subscription.supervise")

#: The longest a failing loop waits between passes.
MAX_BACKOFF_SECONDS = 3600.0


class LoopStopped(RuntimeError):
    """A supervised loop finished — by returning, or by raising. Either is fatal for the worker."""


def backoff_delay(poll_interval: float, consecutive_failures: int) -> float:
    """The wait after a pass: the poll interval when healthy, doubling per consecutive failure,
    capped at `MAX_BACKOFF_SECONDS`."""
    if consecutive_failures <= 0:
        return poll_interval
    return float(min(poll_interval * (2 ** min(consecutive_failures, 16)), MAX_BACKOFF_SECONDS))


async def backoff_loop(
    name: str,
    tick: Callable[[], Awaitable[Any]],
    *,
    poll_interval: float,
    stopped: Callable[[], bool],
    on_pass: Callable[[Any], None] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Run `tick` until `stopped()`; a pass that raises is logged and retried with backoff."""
    failures = 0
    while not stopped():
        try:
            result = await tick()
        except Exception as exc:
            failures += 1
            log.error(
                f"{name}.tick_failed",
                error=str(exc)[:300],
                consecutive_failures=failures,
            )
        else:
            failures = 0
            if on_pass is not None:
                on_pass(result)
        await sleep(backoff_delay(poll_interval, failures))


async def supervise(loops: dict[str, Coroutine[Any, Any, Any]]) -> None:
    """Run every loop until the first one finishes; then cancel the rest and raise.

    A loop that raises re-raises here (wrapped in `LoopStopped`, naming it); one that returns is
    just as fatal — the worker must not keep running with a loop missing. Cancelling `supervise`
    itself cancels every loop and propagates `CancelledError`.
    """
    tasks = {asyncio.create_task(coro, name=name): name for name, coro in loops.items()}
    try:
        done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    task = next(iter(done))
    name = tasks[task]
    if task.cancelled():
        raise LoopStopped(f"{name} was cancelled")
    error = task.exception()
    if error is not None:
        log.error("worker.loop_failed", loop=name, error=str(error)[:300])
        raise LoopStopped(f"{name} failed: {error}") from error
    log.error("worker.loop_returned", loop=name)
    raise LoopStopped(f"{name} returned; the worker cannot run without it")
