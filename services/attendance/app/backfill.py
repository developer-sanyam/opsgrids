"""The one-time per-organization TEAM backfill (R8; the shape is approval's, ADR-0006 §2.1).

Invoked by `bin/backfill-team`, run directly rather than through `bin/cron` or `bin/worker`: a
one-time job is not a schedule and is not an event handler, so it earns its own entrypoint.

**Why it exists.** `team_membership` is fed by employee's lifecycle events, and an employee who has
not been edited since this service started listening has produced none. Until the table holds that
person, a manager's TEAM read misses them — the safe way to be wrong, but wrong. This reads
employee-service's paged hierarchy export and applies it through the same version-gated upsert the
consumer uses, so the two converge in either order and a backfill racing the live stream loses
nothing.

**Ordering, enforced here rather than only documented.** A topic exchange drops a message nothing
is bound for, so a hierarchy change published while the queue does not exist is gone for good, not
queued. `main()` therefore declares `attendance.events` and binds every routing key the worker
binds *before* the first HTTP request. It declares the queue exactly as `EventConsumer.run()` does
(`platform_core.events`) — same name, `durable=True`, same dead-letter argument and queue — because
a declare that differs in one argument makes RabbitMQ answer 406 PRECONDITION_FAILED on whichever
side declares second, and a 406 on the worker's startup means the worker cannot start.
`platform_core` exposes no declare-only helper to share, so the shape is repeated; a test runs a
real `EventConsumer` against what this function bound. Running the backfill before the new worker
is deployed is still the operator's call to get right — this guards against the queue not existing.

**Nothing is fetched inside a transaction.** Each page is requested with no session open; the
page's transaction is opened only once it is in memory, and closed before the next request. A slow
employee-service therefore never holds a pooled connection or a tenant-bound transaction.

**No progress table.** The upsert is idempotent and version-gated, so a second full pass converges
to the same state as the first — re-running is the resume mechanism. On failure the runner raises
`BackfillInterrupted` carrying the cursor of the last page that committed, and `--cursor <uuid>`
resumes from it (single organization only).

Usage, from the service directory or inside the worker container::

    python -m app.backfill --org <organization-id> [--org <id> ...] [--cursor <uuid>]
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import aio_pika

from app.config import settings
from app.domain import normalise_manager
from app.employee_client import HttpTeamSource, TeamSource
from app.services import TeamMembershipWrite, upsert_team_membership_bulk
from platform_core.db import assert_app_role, dispose_engine, init_engine, session_scope
from platform_core.events import DLX, EXCHANGE
from platform_core.logging import get_logger

log = get_logger("attendance.backfill")

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

    `last_cursor` is the cursor to fetch the *next* page from: `run_backfill` only advances its own
    cursor after a page's transaction has committed, so whatever it holds at the moment of failure
    is already the correct resume point — the page being fetched or applied has not landed, and
    everything before it has. Wraps a retry-exhausted fetch, a 4xx that is never retried (a wrong
    audience, secret, issuer or cursor) and a bad row alike: all are "an operator must look and
    re-run", and all deserve the same resume hint.
    """

    def __init__(self, *, last_cursor: uuid.UUID | None, cause: BaseException) -> None:
        super().__init__(str(cause))
        self.last_cursor = last_cursor


async def ensure_queue_bound(amqp_url: str) -> None:
    """Declare and bind `attendance.events` before any backfill HTTP call is made.

    Idempotent — a queue that already exists with the same arguments is a no-op — so this is safe
    ahead of an already-running worker, and exactly what is needed before a worker exists. A
    mismatch is *not* repaired: unlike approval's earlier shape there is no prior version of this
    function to have left a differently-declared queue behind, so a 406 here means something else
    declared the queue wrongly and must be looked at, not deleted.
    """
    from app.consumers import BOUND_EVENTS, QUEUE_NAME

    connection = await aio_pika.connect_robust(amqp_url)
    try:
        channel = await connection.channel()
        exchange = await channel.declare_exchange(
            EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
        )
        dlx = await channel.declare_exchange(DLX, aio_pika.ExchangeType.TOPIC, durable=True)
        dlq = await channel.declare_queue(f"{QUEUE_NAME}.dlq", durable=True)
        await dlq.bind(dlx, routing_key="#")
        queue = await channel.declare_queue(
            QUEUE_NAME, durable=True, arguments={"x-dead-letter-exchange": DLX}
        )
        for routing_key in BOUND_EVENTS:
            await queue.bind(exchange, routing_key=routing_key)
        log.info("attendance.backfill.queue_bound", routing_keys=list(BOUND_EVENTS))
    finally:
        await connection.close()


async def run_backfill(
    organization_id: uuid.UUID,
    source: TeamSource,
    *,
    start_cursor: uuid.UUID | None = None,
    page_size: int = 500,
    pace_seconds: float = PAGE_PACING_SECONDS,
) -> BackfillResult:
    """Page through employee's hierarchy export and apply it, one transaction per page.

    **One transaction per page, on attendance's side**: a page applies wholly or not at all, and
    because the upsert is version-gated a re-applied page is a no-op — which is what makes
    "resume by re-running" safe rather than merely convenient. A row with no `hierarchy_version`
    (an un-upgraded employee pod) is dropped, never read as version 0, as the live consumer drops
    it. A page is **one upsert statement**, not one per employee.

    The fetch is outside any transaction; the transaction opens only over a page already in
    memory.
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

        # The moment the backfill fetched the row: the export is a point-in-time snapshot with no
        # `occurred_at` of its own, and the column is display-only and never compared.
        fetched_at = datetime.now(UTC)
        writes: list[TeamMembershipWrite] = []
        for item in page.items:
            if item.hierarchy_version is None:
                log.warning(
                    "attendance.backfill.item_without_version",
                    organization_id=str(organization_id),
                    employee_id=str(item.employee_id),
                )
                continue
            writes.append(
                TeamMembershipWrite(
                    employee_id=item.employee_id,
                    manager_employee_id=normalise_manager(
                        item.employee_id, item.reporting_manager_id
                    ),
                    status=item.status,
                    source_version=item.hierarchy_version,
                    source_occurred_at=fetched_at,
                )
            )

        try:
            async with session_scope(organization_id=organization_id) as session:
                items_applied += await upsert_team_membership_bulk(
                    session, organization_id=organization_id, rows=writes
                )
        except Exception as exc:
            raise BackfillInterrupted(last_cursor=cursor, cause=exc) from exc

        log.info(
            "attendance.backfill.page_applied",
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

    parser = argparse.ArgumentParser(
        description="Backfill attendance.team_membership from employee-service"
    )
    parser.add_argument(
        "--org",
        action="append",
        required=True,
        type=uuid.UUID,
        help="organization id to backfill; repeat for several, run one after another",
    )
    parser.add_argument(
        "--cursor",
        type=uuid.UUID,
        default=None,
        help="resume from a previously printed cursor (one --org only)",
    )
    args = parser.parse_args(argv)
    if args.cursor is not None and len(args.org) != 1:
        parser.error("--cursor resumes one organization; pass exactly one --org with it")

    cfg = settings()
    engine = init_engine(cfg)
    # Writes go through the app engine, tenant-bound per page — the same NOBYPASSRLS role and the
    # same RLS net a live request runs under. There is no cross-tenant write here to widen.
    await assert_app_role(engine, environment=cfg.environment)

    # ADR-0006 §2: the queue must be bound before the backfill starts, or a hierarchy change
    # published mid-run is dropped by the topic exchange rather than queued.
    await ensure_queue_bound(cfg.rabbitmq_url)

    source = HttpTeamSource(cfg.employee_service_url, cfg)
    try:
        for organization_id in args.org:
            try:
                result = await run_backfill(organization_id, source, start_cursor=args.cursor)
            except BackfillInterrupted as exc:
                resume = exc.last_cursor
                log.error(
                    "attendance.backfill.failed",
                    organization_id=str(organization_id),
                    error=str(exc)[:300],
                    hint=(
                        f"resume with: bin/backfill-team --org {organization_id}"
                        + (f" --cursor {resume}" if resume is not None else "")
                    ),
                )
                raise
            log.info(
                "attendance.backfill.complete",
                organization_id=str(organization_id),
                pages=result.pages,
                items_seen=result.items_seen,
                items_applied=result.items_applied,
            )
    finally:
        await source.aclose()
        await dispose_engine()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
