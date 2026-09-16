"""The backfill's orchestration (ADR-0006 §2.1): paging, the version gate, resume-by-cursor, one
transaction per page, and the queue-bound-before-any-HTTP-call ordering.

`run_backfill` is exercised against a stubbed `HierarchySource` (no real employee-service) but a
**real** PostgreSQL, through the same `upsert_hierarchy_node` the consumer uses — so what this
proves about pagination and the version gate is proved against the real database, and only the
network hop is faked. `ensure_queue_bound` is exercised against the real broker, because what it
guarantees is a property of RabbitMQ's topic exchange, not of this process's memory.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import aio_pika
import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.backfill import BackfillInterrupted, ensure_queue_bound, run_backfill
from app.config import settings
from app.employee_client import HierarchyItem, HierarchyPage
from app.services import upsert_hierarchy_node
from platform_core.db import dispose_engine, init_engine
from platform_core.events import EXCHANGE, EventConsumer

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _engine() -> Any:
    """`run_backfill` writes through `session_scope`, which needs the process engine
    initialised — the app lifespan does this in production."""
    init_engine(settings())
    yield
    await dispose_engine()


@dataclass
class FakeHierarchySource:
    """An in-memory, keyset-paginated stand-in for employee's internal export.

    `pages` is the full ordered sequence a real cursor walk would produce; `fail_before_page`
    raises `boom` before serving that 1-based page number, standing in for the run failing
    partway through — the property under test is that every page served *before* the failure is
    already committed, because the backfill's transaction is per page, not per run.
    """

    pages: list[list[HierarchyItem]]
    fail_before_page: int | None = None
    calls: list[uuid.UUID | None] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.calls = []

    async def fetch_page(
        self, organization_id: uuid.UUID, *, cursor: uuid.UUID | None, limit: int = 500
    ) -> HierarchyPage:
        self.calls.append(cursor)
        page_number = len(self.calls)
        if self.fail_before_page == page_number:
            raise RuntimeError("boom")
        items = self.pages[page_number - 1]
        next_cursor = uuid.uuid4() if page_number < len(self.pages) else None
        return HierarchyPage(items=items, next_cursor=next_cursor)


def _item(employee_id: uuid.UUID, *, version: int, display_name: str = "Someone") -> HierarchyItem:
    return HierarchyItem(
        employee_id=employee_id,
        reporting_manager_id=None,
        dotted_line_manager_id=None,
        status="active",
        display_name=display_name,
        employee_no="EMP0001",
        location_code="MUM",
        hierarchy_version=version,
    )


async def _stored_version(
    session: AsyncSession, org_id: uuid.UUID, employee_id: uuid.UUID
) -> int | None:
    row = (
        await session.execute(
            text(
                "SELECT source_version FROM approval.hierarchy_nodes "
                "WHERE organization_id = :org AND employee_id = :emp"
            ),
            {"org": str(org_id), "emp": str(employee_id)},
        )
    ).first()
    return row[0] if row else None


class TestPagingAndTheVersionGate:
    async def test_multiple_pages_are_all_applied(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        alice, bob = uuid.uuid4(), uuid.uuid4()
        source = FakeHierarchySource(pages=[[_item(alice, version=1)], [_item(bob, version=1)]])

        result = await run_backfill(org_id, source, pace_seconds=0)

        assert result.pages == 2
        assert result.items_seen == 2
        assert result.items_applied == 2
        async with session.begin():
            assert await _stored_version(session, org_id, alice) == 1
            assert await _stored_version(session, org_id, bob) == 1

    async def test_the_version_gate_applies_during_a_backfill_too(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """A backfill can race the live event stream (ADR-0006 §2): a page carrying a stale
        version must not clobber a state the consumer already applied."""
        employee_id = uuid.uuid4()
        async with session.begin():
            await upsert_hierarchy_node(
                session,
                organization_id=org_id,
                employee_id=employee_id,
                reporting_manager_id=None,
                dotted_line_manager_id=None,
                status="active",
                display_name="Already Live",
                employee_no="EMP0001",
                location_code="MUM",
                source_version=9,
                source_occurred_at=datetime.now(UTC),
            )

        source = FakeHierarchySource(pages=[[_item(employee_id, version=3, display_name="Stale")]])
        result = await run_backfill(org_id, source, pace_seconds=0)

        assert result.items_applied == 0
        async with session.begin():
            assert await _stored_version(session, org_id, employee_id) == 9

    async def test_an_item_with_no_hierarchy_version_is_skipped(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        employee_id = uuid.uuid4()
        item = _item(employee_id, version=1)
        object.__setattr__(item, "hierarchy_version", None)
        source = FakeHierarchySource(pages=[[item]])

        result = await run_backfill(org_id, source, pace_seconds=0)

        assert result.items_applied == 0
        async with session.begin():
            assert await _stored_version(session, org_id, employee_id) is None

    async def test_a_single_page_with_mixed_new_and_stale_items_applies_only_the_newer_ones(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """`run_backfill` batches a whole page into one multi-row upsert
        (`upsert_hierarchy_nodes_bulk`) rather than one round trip per item — this proves the
        version gate still applies per row inside that one statement, not to the page as a
        whole: one item in the page is genuinely new, the other carries a stale version next to
        a row the live stream already advanced, and only the first should land."""
        fresh, already_live = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await upsert_hierarchy_node(
                session,
                organization_id=org_id,
                employee_id=already_live,
                reporting_manager_id=None,
                dotted_line_manager_id=None,
                status="active",
                display_name="Already Live",
                employee_no="EMP0001",
                location_code="MUM",
                source_version=9,
                source_occurred_at=datetime.now(UTC),
            )

        source = FakeHierarchySource(
            pages=[
                [
                    _item(fresh, version=1),
                    _item(already_live, version=3, display_name="Stale"),
                ]
            ]
        )
        result = await run_backfill(org_id, source, pace_seconds=0)

        assert result.items_seen == 2
        assert result.items_applied == 1
        async with session.begin():
            assert await _stored_version(session, org_id, fresh) == 1
            assert await _stored_version(session, org_id, already_live) == 9


class TestOneTransactionPerPageAndResume:
    async def test_pages_already_applied_survive_a_later_page_failing(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """One transaction per page (ADR-0006 §2.1): the first page's write is not rolled back
        by the second page's failure — resuming re-applies only what never landed."""
        alice, bob = uuid.uuid4(), uuid.uuid4()
        source = FakeHierarchySource(
            pages=[[_item(alice, version=1)], [_item(bob, version=1)]],
            fail_before_page=2,
        )

        with pytest.raises(RuntimeError, match="boom"):
            await run_backfill(org_id, source, pace_seconds=0)

        async with session.begin():
            assert await _stored_version(session, org_id, alice) == 1
            assert await _stored_version(session, org_id, bob) is None

    async def test_a_failure_raises_backfillinterrupted_carrying_the_cursor_to_resume_from(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The second page's fetch fails after the first page already committed — the exception
        must carry the cursor a resumed run should pass to `--cursor`, which is exactly the
        cursor `source.calls` recorded for the failing fetch (page 1's `next_cursor`)."""
        alice, bob = uuid.uuid4(), uuid.uuid4()
        source = FakeHierarchySource(
            pages=[[_item(alice, version=1)], [_item(bob, version=1)]],
            fail_before_page=2,
        )

        with pytest.raises(BackfillInterrupted) as excinfo:
            await run_backfill(org_id, source, pace_seconds=0)

        assert isinstance(excinfo.value.__cause__, RuntimeError)
        assert excinfo.value.last_cursor == source.calls[-1]

    async def test_a_4xx_style_fetch_failure_still_raises_backfillinterrupted(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """`main()` used to catch only `RetryExhaustedError`, so an `httpx.HTTPStatusError` from
        a malformed `--cursor` or a wrong audience/secret escaped without the resume-hint log.
        `run_backfill` now wraps every failure mode the same way, regardless of its type."""

        class _AlwaysReturns422:
            async def fetch_page(
                self, organization_id: uuid.UUID, *, cursor: uuid.UUID | None, limit: int = 500
            ) -> HierarchyPage:
                request = httpx.Request("GET", "http://employee.test/internal/v1/hierarchy")
                raise httpx.HTTPStatusError(
                    "422 from employee-service",
                    request=request,
                    response=httpx.Response(422, request=request),
                )

        resume_from = uuid.uuid4()
        with pytest.raises(BackfillInterrupted) as excinfo:
            await run_backfill(
                org_id, _AlwaysReturns422(), start_cursor=resume_from, pace_seconds=0
            )

        assert isinstance(excinfo.value.__cause__, httpx.HTTPStatusError)
        # Nothing was ever fetched successfully, so the resume point is exactly where it started.
        assert excinfo.value.last_cursor == resume_from

    async def test_an_out_of_vocabulary_status_still_raises_backfillinterrupted(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The other escaping case the finding names: a `ValueError` from a status outside the
        closed vocabulary, raised inside the page's upsert rather than the fetch."""
        bad_item = _item(uuid.uuid4(), version=1)
        object.__setattr__(bad_item, "status", "not-a-real-status")
        source = FakeHierarchySource(pages=[[bad_item]])

        with pytest.raises(BackfillInterrupted) as excinfo:
            await run_backfill(org_id, source, pace_seconds=0)

        assert isinstance(excinfo.value.__cause__, ValueError)
        # The failing page's own fetch is what advanced `source.calls`, but the page never
        # committed, so the resume point is still the run's start.
        assert excinfo.value.last_cursor is None

    async def test_resuming_from_a_cursor_continues_rather_than_restarts(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        bob = uuid.uuid4()
        resume_cursor = uuid.uuid4()
        source = FakeHierarchySource(pages=[[_item(bob, version=1)]])

        await run_backfill(org_id, source, start_cursor=resume_cursor, pace_seconds=0)

        assert source.calls == [resume_cursor]

    async def test_a_full_rerun_converges_to_the_same_state_idempotently(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """No progress table: re-running the whole thing from the start is the resume mechanism,
        and it must be safe to do (ADR-0006 §2.1)."""
        employee_id = uuid.uuid4()
        source = FakeHierarchySource(pages=[[_item(employee_id, version=1)]])

        first = await run_backfill(org_id, source, pace_seconds=0)
        second_source = FakeHierarchySource(pages=[[_item(employee_id, version=1)]])
        second = await run_backfill(org_id, second_source, pace_seconds=0)

        assert first.items_applied == 1
        assert second.items_applied == 0  # same version: a no-op, not an error
        async with session.begin():
            assert await _stored_version(session, org_id, employee_id) == 1


class TestQueueBoundBeforeBackfill:
    async def test_ensure_queue_bound_declares_and_binds_without_error(self) -> None:
        """The durable, idempotent half of ADR-0006 §2's ordering requirement: the queue and its
        bindings exist against the real broker before any HTTP call is made. Declaring twice
        (as a second backfill run, or a run after the worker is already deployed, would) must
        stay a no-op rather than an error."""
        await ensure_queue_bound(settings().rabbitmq_url)
        await ensure_queue_bound(settings().rabbitmq_url)

    async def test_a_real_event_consumer_can_start_against_the_queue_this_function_bound(
        self,
    ) -> None:
        """The blocker this test guards against: `ensure_queue_bound` used to declare
        `approval.events` with no `arguments`, while `EventConsumer.run()`
        (`platform_core/events.py`) declares the same queue with
        `arguments={"x-dead-letter-exchange": ...}`. RabbitMQ answers a mismatched redeclare with
        406 PRECONDITION_FAILED — on whichever side declares second — so a queue the backfill
        bound first made the real worker unable to start. This starts a real `EventConsumer`
        against the queue `ensure_queue_bound` just bound and proves it does not raise."""
        from app.consumers import HIERARCHY_EVENTS, QUEUE_NAME

        await ensure_queue_bound(settings().rabbitmq_url)

        consumer = EventConsumer(amqp_url=settings().rabbitmq_url, queue_name=QUEUE_NAME)
        for routing_key in HIERARCHY_EVENTS:

            async def _noop(envelope: dict[str, Any]) -> None:
                return None

            consumer.on(routing_key)(_noop)

        run_task = asyncio.create_task(consumer.run())
        try:
            await asyncio.sleep(0.5)
            if run_task.done():
                # Re-raises whatever `run()` raised (e.g. `ChannelPreconditionFailed`) with a
                # real traceback, rather than leaving a cancelled-but-never-started task looking
                # like a pass.
                run_task.result()
        finally:
            run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await run_task

    async def test_a_hierarchy_event_published_after_binding_lands_in_the_bound_queue(
        self,
    ) -> None:
        """Proves the *binding*, not just the declare — the current test above would still pass
        even if `queue.bind(...)` were deleted from `ensure_queue_bound`, because declaring an
        unbound queue does not raise. A topic exchange drops an unroutable message rather than
        holding it (ADR-0006 §2), so an unbound queue would stay silently empty forever; this
        publishes a real message with a hierarchy routing key and proves it actually lands."""
        from app.consumers import EVT_EMPLOYEE_ONBOARDED, QUEUE_NAME

        await ensure_queue_bound(settings().rabbitmq_url)

        connection = await aio_pika.connect_robust(settings().rabbitmq_url)
        try:
            channel = await connection.channel()
            exchange = await channel.get_exchange(EXCHANGE)
            queue = await channel.get_queue(QUEUE_NAME)
            await queue.purge()

            await exchange.publish(
                aio_pika.Message(body=b'{"hello": "world"}'),
                routing_key=EVT_EMPLOYEE_ONBOARDED,
            )

            message = await queue.get(timeout=5, fail=False)
            assert message is not None
            await message.ack()
        finally:
            await connection.close()

    async def test_repairs_a_queue_declared_by_the_pre_fix_shape(self) -> None:
        """Self-healing against a broker that already has `approval.events` in the shape the
        pre-fix `ensure_queue_bound` (durable, no `arguments`) left it in — the exact scenario
        this finding describes, which a fresh broker in CI would never otherwise reproduce."""
        from app.consumers import HIERARCHY_EVENTS, QUEUE_NAME

        connection = await aio_pika.connect_robust(settings().rabbitmq_url)
        try:
            channel = await connection.channel()
            with contextlib.suppress(Exception):
                await channel.queue_delete(QUEUE_NAME)
            channel = await connection.channel()
            await channel.declare_queue(QUEUE_NAME, durable=True)
        finally:
            await connection.close()

        # Must not raise, despite the mismatched pre-existing declaration.
        await ensure_queue_bound(settings().rabbitmq_url)

        consumer = EventConsumer(amqp_url=settings().rabbitmq_url, queue_name=QUEUE_NAME)
        for routing_key in HIERARCHY_EVENTS:

            async def _noop(envelope: dict[str, Any]) -> None:
                return None

            consumer.on(routing_key)(_noop)

        run_task = asyncio.create_task(consumer.run())
        try:
            await asyncio.sleep(0.5)
            if run_task.done():
                run_task.result()
        finally:
            run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await run_task
