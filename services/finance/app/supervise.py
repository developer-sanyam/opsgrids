"""Supervision for a worker process that runs more than one always-on loop.

`finance-worker` runs two loops in one asyncio process — the event consumer and the
filing-reclaim sweep (`app/worker.py`) — so a container's health is one process's health. Plain
`asyncio.gather` does not give that: when one awaitable raises it does not cancel the other, and
when one merely *returns* (a `while` loop whose condition went stale) `gather` keeps waiting on the
rest, so half a worker silently going idle looks exactly like a healthy container.

`run_supervised` ends the group on the first loop to return **or** raise: every other loop is
cancelled and awaited, then the reason is re-raised (`LoopExited` for a plain return), so the
process exits non-zero and the orchestrator restarts it. A `CancelledError` delivered to the
supervisor itself cancels every loop and propagates.

Service-local on purpose: a platform-wide `platform_core.supervise` belongs to the memory-headroom
slice, which had not merged when this landed (D3 round 3). The shape here is the same, so moving
to it is an import change.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, NoReturn

from platform_core.logging import get_logger

log = get_logger("finance.supervise")


class LoopExited(RuntimeError):
    """A supervised loop returned instead of running forever — a bug this module refuses to let
    look like a clean exit."""

    def __init__(self, name: str) -> None:
        super().__init__(f"supervised loop {name!r} returned instead of running forever")
        self.name = name


async def run_supervised(**named: Coroutine[Any, Any, Any]) -> NoReturn:
    """Run every coroutine as a task; the first to finish — by returning *or* raising — ends the
    group. Never returns normally."""
    if not named:
        raise ValueError("run_supervised needs at least one loop")
    tasks = {name: asyncio.ensure_future(coro) for name, coro in named.items()}
    try:
        done, pending = await asyncio.wait(tasks.values(), return_when=asyncio.FIRST_COMPLETED)
    except asyncio.CancelledError:
        for task in tasks.values():
            task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)
        raise
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    name, task = next((n, t) for n, t in tasks.items() if t in done)
    exc = task.exception()
    if exc is not None:
        log.error("finance.supervise.loop_raised", loop=name, error=repr(exc))
        raise exc
    log.error("finance.supervise.loop_returned", loop=name)
    raise LoopExited(name)
