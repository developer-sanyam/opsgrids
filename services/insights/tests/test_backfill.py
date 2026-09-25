"""The backfill runner framework (ADR-0020 §3), against real PostgreSQL — resumability, the
claim-and-drain race, and discovery's enqueue rule. Every test here injects its own fake entity
into the functions under test via their own `registry=` parameter, rather than depending on
`ENTITY_REGISTRY`'s real, growing contents (`finance.invoices`/`payments`/`purchases` as of I2,
`app/finance_client.py`) — so this suite's assertions never drift with what a later slice adds.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backfill import (
    ExportPage,
    _claim_run,
    enqueue_missing_runs,
    run_backfill,
)
from app.config import settings as settings_factory
from app.models import BackfillRun

pytestmark = pytest.mark.integration


@dataclass
class FakeSource:
    """A `BackfillSource` whose pages and failures are scripted per test. Raising a plain
    `RuntimeError` (never `httpx.HTTPError`/`KeyError`/`TypeError`/`ValueError`) deliberately
    skips `_fetch_page_with_retry`'s own retry-with-backoff loop — real seconds of sleep this
    unit test has no reason to pay for; the retry loop itself is exercised by
    `test_account_client.py`.
    """

    pages: dict[int, ExportPage]
    fail_on_page_index: set[int] = field(default_factory=set)
    applied: list[list[dict[str, Any]]] = field(default_factory=list)
    calls: int = 0

    async def fetch_page(
        self,
        settings: Any,
        client: httpx.AsyncClient,
        *,
        organization_id: uuid.UUID,
        cursor: dict[str, str] | None,
        limit: int,
    ) -> ExportPage:
        self.calls += 1
        index = 0 if cursor is None else int(cursor["page"])
        if index in self.fail_on_page_index:
            raise RuntimeError(f"simulated failure on page {index}")
        return self.pages[index]

    async def apply_page(
        self,
        session: AsyncSession,
        *,
        organization_id: uuid.UUID,
        rows: list[dict[str, Any]],
        run_id: uuid.UUID,
        page_requested_at: datetime,
    ) -> int:
        self.applied.append(rows)
        return len(rows)


async def _create_run(work_scope: Any, *, organization_id: uuid.UUID, entity: str) -> uuid.UUID:
    """Seeds through `work_scope`, which commits on exit — the run must be durably visible to
    the *other* connections (`admin_session`, `discovery_sessionmaker`, and `work_scope` itself
    on its next call) that act on it next, the same cross-connection discipline `test_rls.py`'s
    `admin_session` seeding follows for the identical reason."""
    run_id = uuid.uuid4()
    async with work_scope(organization_id) as scoped_session:
        scoped_session.add(
            BackfillRun(id=run_id, organization_id=organization_id, entity=entity, kind="initial")
        )
    return run_id


class TestResumability:
    async def test_a_failed_page_leaves_the_run_resumable_from_its_cursor(
        self, session: AsyncSession, org_id: uuid.UUID, work_scope: Any
    ) -> None:
        run_id = await _create_run(work_scope, organization_id=org_id, entity="test.widgets")

        source = FakeSource(
            pages={
                0: ExportPage(rows=[{"id": "a"}], next_cursor={"page": "1"}),
            },
            fail_on_page_index={1},
        )

        now = datetime.now(UTC)
        report = await run_backfill(
            settings=settings_factory(),
            work_scope=work_scope,
            organization_id=org_id,
            run_id=run_id,
            entity="test.widgets",
            source=source,
            start_cursor=None,
            client=httpx.AsyncClient(),
            now=now,
        )
        assert report.failed is True
        assert report.rows_upserted == 1

        # `populate_existing()`: SQLAlchemy's identity map would otherwise hand back whatever
        # was loaded for this PK earlier in the test, unrefreshed — the row changed underneath
        # it via a different connection (`work_scope`, inside `run_backfill`).
        refreshed = (
            await session.execute(
                select(BackfillRun)
                .where(BackfillRun.id == run_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert refreshed.status == "failed"
        assert refreshed.cursor == {"page": "1"}
        assert refreshed.rows_upserted == 1
        assert refreshed.last_error is not None and "simulated" in refreshed.last_error
        assert refreshed.next_attempt_at >= now + timedelta(minutes=55)

        # Resume: a fresh source, page 1 no longer fails, starting from the persisted cursor.
        resumed_source = FakeSource(pages={1: ExportPage(rows=[{"id": "b"}], next_cursor=None)})
        second_report = await run_backfill(
            settings=settings_factory(),
            work_scope=work_scope,
            organization_id=org_id,
            run_id=run_id,
            entity="test.widgets",
            source=resumed_source,
            start_cursor=refreshed.cursor,
            client=httpx.AsyncClient(),
            now=datetime.now(UTC),
        )
        assert second_report.completed is True
        assert second_report.rows_upserted == 1  # only the resumed page, not page 0 again

        final = (
            await session.execute(
                select(BackfillRun)
                .where(BackfillRun.id == run_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert final.status == "done"
        assert final.finished_at is not None
        # Total across both attempts: page 0 (1 row) + the resumed page (1 row).
        assert final.rows_upserted == 2


class TestClaim:
    async def test_claim_moves_pending_to_running(
        self, session: AsyncSession, org_id: uuid.UUID, work_scope: Any
    ) -> None:
        run_id = await _create_run(work_scope, organization_id=org_id, entity="test.widgets")
        now = datetime.now(UTC)
        claimed = await _claim_run(work_scope, organization_id=org_id, run_id=run_id, now=now)
        assert claimed is True

        refreshed = (
            await session.execute(select(BackfillRun).where(BackfillRun.id == run_id))
        ).scalar_one()
        assert refreshed.status == "running"
        assert refreshed.attempts == 1
        assert refreshed.started_at is not None

    async def test_a_second_claim_of_the_same_run_loses_the_race(
        self, session: AsyncSession, org_id: uuid.UUID, work_scope: Any
    ) -> None:
        run_id = await _create_run(work_scope, organization_id=org_id, entity="test.widgets")
        now = datetime.now(UTC)
        first = await _claim_run(work_scope, organization_id=org_id, run_id=run_id, now=now)
        second = await _claim_run(work_scope, organization_id=org_id, run_id=run_id, now=now)
        assert first is True
        assert second is False


class TestEnqueueMissingRuns:
    async def test_enqueues_one_row_per_missing_pair(
        self,
        session: AsyncSession,
        org_id: uuid.UUID,
        work_scope: Any,
        discovery_sessionmaker: Any,
    ) -> None:
        registry = {"test.a": object(), "test.b": object()}
        enqueued = await enqueue_missing_runs(
            registry=registry,
            discovery_factory=discovery_sessionmaker,
            work_scope=work_scope,
            organization_ids=[org_id],
            reconcile_after=timedelta(days=7),
            now=datetime.now(UTC),
        )
        assert enqueued == 2
        rows = (
            (
                await session.execute(
                    select(BackfillRun).where(BackfillRun.organization_id == org_id)
                )
            )
            .scalars()
            .all()
        )
        assert {row.entity for row in rows} == {"test.a", "test.b"}
        assert all(row.kind == "initial" for row in rows)

    async def test_a_recent_done_run_is_not_re_enqueued(
        self,
        session: AsyncSession,
        org_id: uuid.UUID,
        work_scope: Any,
        discovery_sessionmaker: Any,
    ) -> None:
        async with work_scope(org_id) as scoped_session:
            scoped_session.add(
                BackfillRun(
                    organization_id=org_id,
                    entity="test.a",
                    kind="initial",
                    status="done",
                    finished_at=datetime.now(UTC),
                )
            )

        registry = {"test.a": object()}
        enqueued = await enqueue_missing_runs(
            registry=registry,
            discovery_factory=discovery_sessionmaker,
            work_scope=work_scope,
            organization_ids=[org_id],
            reconcile_after=timedelta(days=7),
            now=datetime.now(UTC),
        )
        assert enqueued == 0

    async def test_a_stale_done_run_is_reconciled(
        self,
        session: AsyncSession,
        org_id: uuid.UUID,
        work_scope: Any,
        discovery_sessionmaker: Any,
    ) -> None:
        async with work_scope(org_id) as scoped_session:
            scoped_session.add(
                BackfillRun(
                    organization_id=org_id,
                    entity="test.a",
                    kind="initial",
                    status="done",
                    finished_at=datetime.now(UTC) - timedelta(days=10),
                )
            )

        registry = {"test.a": object()}
        enqueued = await enqueue_missing_runs(
            registry=registry,
            discovery_factory=discovery_sessionmaker,
            work_scope=work_scope,
            organization_ids=[org_id],
            reconcile_after=timedelta(days=7),
            now=datetime.now(UTC),
        )
        assert enqueued == 1
        rows = (
            (
                await session.execute(
                    select(BackfillRun).where(
                        BackfillRun.organization_id == org_id, BackfillRun.kind == "reconcile"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
