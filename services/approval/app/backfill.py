"""The one-time per-organization hierarchy backfill (ADR-0006 §2.1).

Invoked by `bin/backfill-hierarchy`, run directly rather than through `bin/cron`. ADR-0006 §2.1
calls it "a one-shot command (`bin/backfill-hierarchy --org <id> ...`)", distinct from `bin/cron`
(the SLA sweep, a recurring poll that does not exist until slice 4). A one-time job is not a
schedule, so it earns its own entrypoint rather than a `--once` flag bolted onto the sweep binary
that will land later — the two have nothing in common except both being started by an operator.

**Ordering, enforced by this module rather than only documented.** ADR-0006 §2 is explicit that
"the approval worker's queue must be declared and bound before the backfill starts, so that no
event published during the backfill window is lost to an unbound queue" — a topic exchange drops
unroutable messages, so a hierarchy change published while nothing is listening is gone forever,
not queued. `main()` therefore binds `app.consumers.consumer`'s queue over AMQP before issuing a
single HTTP request, using the same queue name and the same declaration `EventConsumer.run()`
(`platform_core/events.py`) uses for the worker process — not a parallel declaration that could
drift from it. `platform_core` exposes no declare-only helper to share instead, so the shape is
repeated here deliberately identically, argument for argument, including the dead-letter queue
and its binding: a declare that differs even in one argument makes RabbitMQ answer 406
PRECONDITION_FAILED on whichever side declares second, and a 406 on the worker's own startup
declare means the worker cannot start at all. This does not start consuming (the worker process
owns that loop); it only ensures the queue and its bindings exist, which is the durable,
idempotent part of "bound" that a topic exchange needs before it stops discarding messages for
that routing key. Running the backfill against an organization before the real worker is deployed
is still an operator's call to get right — this only guards against the *queue itself* not
existing yet.

**No progress table.** The upsert is idempotent and version-gated (data model §1.1), so a second
full pass converges to the same state as the first — re-running from the start is the resume
mechanism. On failure the runner logs and re-raises a `BackfillInterrupted`, carrying the cursor
of the last page that committed before the failure, and `--cursor <uuid>` resumes from it. A
permanent table for a one-time job is not worth carrying (ADR-0006 §2.1's own "What this
rejects").
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import aio_pika

from app.config import settings
from app.employee_client import HierarchySource, HttpHierarchySource
from app.services import HierarchyNodeWrite, upsert_hierarchy_nodes_bulk
from platform_core.db import assert_app_role, init_engine, session_scope
from platform_core.events import DLX, EXCHANGE
from platform_core.logging import get_logger

log = get_logger("approval.backfill")

#: ADR-0006 §2.1: "100 ms between pages. A backfill must not saturate employee's pool."
PAGE_PACING_SECONDS = 0.1


@dataclass(frozen=True, slots=True)
class BackfillResult:
    organization_id: uuid.UUID
    pages: int
    items_seen: int
    items_applied: int


class BackfillInterrupted(RuntimeError):
    """Any failure while paging or applying a page, carrying the cursor to resume from.

    Wraps whatever `source.fetch_page` or the page's upsert raised — a `RetryExhaustedError`
    after its own retries, an `httpx.HTTPStatusError` on a 4xx that is never retried (a malformed
    `--cursor`, a wrong audience or secret), or a `ValueError` from a status outside the closed
    vocabulary. All three are equally "the operator must look at this and re-run", and all three
    used to escape `main()`'s narrower `except RetryExhaustedError` without the resume-hint log
    that makes re-running cheap. `last_cursor` is the cursor to fetch the *next* page from: this
    module only ever advances its own `cursor` variable after a page's transaction has committed
    (see `run_backfill`), so whatever it holds at the moment of failure is already the correct
    resume point — the page currently being fetched or applied has not landed, and everything
    before it has.
    """

    def __init__(self, *, last_cursor: uuid.UUID | None, cause: BaseException) -> None:
        super().__init__(str(cause))
        self.last_cursor = last_cursor


async def ensure_queue_bound(amqp_url: str) -> None:
    """Declare and bind `approval.events` before any backfill HTTP call is made.

    Declared **identically** to `EventConsumer.run()` (`platform_core/events.py`): the same
    queue name (`app.consumers.QUEUE_NAME`), the same `durable=True`, the same
    `arguments={"x-dead-letter-exchange": DLX}`, and the same dead-letter queue and binding.
    `platform_core` exposes no declare-only helper this could call instead, so the two
    declarations are kept from drifting the only other way available: by being visibly the same
    shape, checked by a test that runs a real `EventConsumer` against what this function bound
    (`tests/test_backfill.py`).

    Declaring a queue and its bindings is idempotent — a queue that already exists with the
    *same* arguments is a no-op — so running this ahead of an already-running worker is harmless,
    and running it before the worker exists is exactly the case ADR-0006 §2 requires: the topic
    exchange stops dropping `hrms.people.employee.*` for this routing key the moment the binding
    exists, whether or not anything is consuming from it yet.

    **Self-healing against the pre-fix shape.** A queue declared by the version of this function
    that shipped without `arguments` (durable, no dead-letter exchange) makes RabbitMQ answer the
    corrected declaration below with 406 PRECONDITION_FAILED — and that 406 closes the channel it
    was raised on. A queue in that shape was never usable by the real worker either:
    `EventConsumer.run()` declares the same name with the same `arguments` and would hit the
    identical 406 on startup, so it was never actually dead-lettering anything. Deleting and
    recreating it on that one error therefore loses no routing decision that was ever honoured,
    and repairs a broker an earlier deploy of this function may have already left in the broken
    shape — the exact scenario this finding describes.
    """
    from app.consumers import HIERARCHY_EVENTS, QUEUE_NAME

    connection = await aio_pika.connect_robust(amqp_url)
    try:
        channel = await connection.channel()
        exchange = await channel.declare_exchange(
            EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
        )
        dlx = await channel.declare_exchange(DLX, aio_pika.ExchangeType.TOPIC, durable=True)
        dlq = await channel.declare_queue(f"{QUEUE_NAME}.dlq", durable=True)
        await dlq.bind(dlx, routing_key="#")

        try:
            queue = await channel.declare_queue(
                QUEUE_NAME,
                durable=True,
                arguments={"x-dead-letter-exchange": DLX},
            )
        except aio_pika.exceptions.ChannelPreconditionFailed:
            log.warning("approval.backfill.queue_argument_mismatch_repairing", queue=QUEUE_NAME)
            # The broker already closed the channel that raised; every further call needs a
            # fresh one.
            channel = await connection.channel()
            await channel.queue_delete(QUEUE_NAME)
            queue = await channel.declare_queue(
                QUEUE_NAME,
                durable=True,
                arguments={"x-dead-letter-exchange": DLX},
            )

        for routing_key in HIERARCHY_EVENTS:
            await queue.bind(exchange, routing_key=routing_key)
        log.info("approval.backfill.queue_bound", routing_keys=list(HIERARCHY_EVENTS))
    finally:
        await connection.close()


async def run_backfill(
    organization_id: uuid.UUID,
    source: HierarchySource,
    *,
    start_cursor: uuid.UUID | None = None,
    page_size: int = 500,
    pace_seconds: float = PAGE_PACING_SECONDS,
) -> BackfillResult:
    """Page through employee's hierarchy export and apply it, one transaction per page.

    **One transaction per page, on approval's side** (ADR-0006 §2.1): a page either applies
    wholly or not at all, and because the upsert is version-gated, a re-applied page is a no-op —
    which is what makes "resume by re-running" safe rather than merely convenient. A row whose
    `hierarchy_version` is `None` (an un-upgraded employee pod) is dropped, never treated as
    version 0, exactly as the live consumer drops it (`app/consumers.py`).

    **One upsert statement per page, not one per item.** All of a page's rows go through
    `upsert_hierarchy_nodes_bulk` in a single round trip inside the page's transaction, rather
    than one call per employee in a loop — a page can carry up to `page_size` items, and a round
    trip per item inside that loop is exactly the N+1 shape CLAUDE.md calls a bug. The version
    gate is unaffected: PostgreSQL evaluates `ON CONFLICT ... WHERE` per conflicting row.

    Raises `BackfillInterrupted` — wrapping whatever `source.fetch_page` or the page's upsert
    raised, with the cursor to resume from — rather than letting the underlying exception escape
    bare; see that class's docstring for why. The caller (`main`, below) logs the resume hint and
    re-raises the original traceback.
    """
    pages = 0
    items_seen = 0
    items_applied = 0
    cursor = start_cursor

    while True:
        try:
            page = await source.fetch_page(organization_id, cursor=cursor, limit=page_size)
        except Exception as exc:
            raise BackfillInterrupted(last_cursor=cursor, cause=exc) from exc
        pages += 1
        items_seen += len(page.items)

        try:
            async with session_scope(organization_id=organization_id) as session:
                writes: list[HierarchyNodeWrite] = []
                for item in page.items:
                    if item.hierarchy_version is None:
                        log.warning(
                            "approval.backfill.item_without_version",
                            organization_id=str(organization_id),
                            employee_id=str(item.employee_id),
                        )
                        continue
                    writes.append(
                        HierarchyNodeWrite(
                            employee_id=item.employee_id,
                            reporting_manager_id=item.reporting_manager_id,
                            dotted_line_manager_id=item.dotted_line_manager_id,
                            status=item.status,
                            display_name=item.display_name,
                            employee_no=item.employee_no,
                            location_code=item.location_code,
                            source_version=item.hierarchy_version,
                            # The internal export carries no `occurred_at` of its own (it is a
                            # point-in-time snapshot, not an event) and the column is
                            # display/debugging only and never compared (data model §1.1) — the
                            # moment the backfill fetched the row is therefore what it records.
                            source_occurred_at=datetime.now(UTC),
                        )
                    )
                items_applied += await upsert_hierarchy_nodes_bulk(
                    session, organization_id=organization_id, nodes=writes
                )
        except Exception as exc:
            raise BackfillInterrupted(last_cursor=cursor, cause=exc) from exc

        log.info(
            "approval.backfill.page_applied",
            organization_id=str(organization_id),
            page=pages,
            items=len(page.items),
            cursor=str(cursor) if cursor else None,
            next_cursor=str(page.next_cursor) if page.next_cursor else None,
        )

        cursor = page.next_cursor
        if cursor is None:
            break
        await asyncio.sleep(pace_seconds)

    return BackfillResult(
        organization_id=organization_id,
        pages=pages,
        items_seen=items_seen,
        items_applied=items_applied,
    )


async def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Backfill approval.hierarchy_nodes from employee")
    parser.add_argument("--org", required=True, help="organization id to backfill")
    parser.add_argument("--cursor", default=None, help="resume from a previously printed cursor")
    args = parser.parse_args(argv)

    organization_id = uuid.UUID(args.org)
    start_cursor = uuid.UUID(args.cursor) if args.cursor else None

    cfg = settings()
    engine = init_engine(cfg)
    # The backfill writes through the app engine, tenant-bound per page — the same role and the
    # same RLS net a live request runs under (ADR-0006 §2.1: "runs under that tenant's GUC on the
    # app role. There is no cross-tenant listing on employee, and this ADR does not create one").
    await assert_app_role(engine, environment=cfg.environment)

    # ADR-0006 §2: the queue must be bound before the backfill starts, or a hierarchy change
    # published mid-run is dropped by the topic exchange rather than queued.
    await ensure_queue_bound(cfg.rabbitmq_url)

    source = HttpHierarchySource(cfg.employee_service_url, cfg)
    try:
        result = await run_backfill(organization_id, source, start_cursor=start_cursor)
    except BackfillInterrupted as exc:
        # Catches every failure mode a page can raise — a retryable error exhausted, a 4xx that
        # was never retried, or a malformed status — not only `RetryExhaustedError`: all of them
        # are non-transient from an operator's chair and all of them need the same resume hint,
        # which a narrower `except` used to skip for exactly the non-transient cases.
        resume_cursor = exc.last_cursor
        log.error(
            "approval.backfill.failed",
            organization_id=str(organization_id),
            error=str(exc)[:300],
            hint=(
                "resume with: bin/backfill-hierarchy --org "
                f"{organization_id}"
                + (f" --cursor {resume_cursor}" if resume_cursor is not None else "")
            ),
        )
        raise
    finally:
        await source.aclose()

    log.info(
        "approval.backfill.complete",
        organization_id=str(organization_id),
        pages=result.pages,
        items_seen=result.items_seen,
        items_applied=result.items_applied,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
