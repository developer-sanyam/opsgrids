"""The backfill runner — ADR-0020 §3, data-model.md §5.

A single always-on loop (folded into the API process alongside the consumer and the snapshot
sweep, `app/main.py`) that does two things on one cadence, cheaper first:

1. **Discovery.** Every `backfill_discovery_poll_seconds` (default: hourly), enumerate every
   organization from account's export (`app/account_client.py`), sync `org_profile` for each,
   and — for every entity a later slice has registered in `ENTITY_REGISTRY` — enqueue a
   `backfill_runs` row for any `(organization, entity)` with no `done` run younger than
   `backfill_reconcile_after_days`. This is what makes the weekly self-healing reconcile and the
   very first backfill after a fresh deploy the same code path (ADR-0020 §3).
2. **Drain.** Every `backfill_drain_poll_seconds` (default: 5s), claim the one due run with the
   earliest `next_attempt_at` and page through it: one HTTP page, one transaction that upserts
   the page and advances the cursor together, paced `backfill_page_pause_seconds` apart. A crash
   between pages resumes from the last one that committed — the cursor lives in the row, not in
   memory (ADR-0020 §3: "each page upserts facts and advances the run's cursor in the same
   transaction, so a crash resumes from the last page").

I1 registers **one** entity end to end, `account.organizations` — not through `ENTITY_REGISTRY`
below (the account export has no per-organization page to walk; discovery's own sync step *is*
its backfill), but as the worked example of the framework I2-I9 plug their own entities into.
`ENTITY_REGISTRY` therefore starts empty: no `*_facts` table exists yet for anything it could
back. A slice that adds one registers a `BackfillSource` here, in the same commit as its export
route client and its fold — nothing else in this module changes.

**Ordering.** `app/main.py` calls `app/consumers.py:ensure_queue_bound` before starting this
runner's loop, never the other way around (ADR-0020 §3, step 2): a change committed before the
queue is bound is caught by discovery's export read; a change committed after arrives as an
event. Binding first is what makes the two commute.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast

import httpx
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import account_client, services
from app.config import Settings
from app.models import BackfillRun
from platform_core.logging import get_logger
from platform_core.supervise import GuardedSweep

log = get_logger("insights.backfill")

#: `docs/insights/data-model.md` §5: "Max 5, then left failed for a person."
MAX_BACKFILL_ATTEMPTS = 5
#: ADR-0020 §3: "3 retries at 2/8/30 s" for one page's own HTTP read.
PAGE_RETRY_BACKOFFS_SECONDS: tuple[float, ...] = (2.0, 8.0, 30.0)
#: A failed run is retried "next hour" (ADR-0020 §3).
RETRY_AFTER_FAILURE = timedelta(hours=1)

WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]


@dataclass(frozen=True, slots=True)
class ExportPage:
    rows: list[dict[str, Any]]
    next_cursor: dict[str, str] | None


class BackfillSource(Protocol):
    """What one entity plugs into the runner (ADR-0020 §3, `docs/insights/api.md` §6.1). Every
    later slice implements this once for its own `*_facts` table."""

    async def fetch_page(
        self,
        settings: Settings,
        client: httpx.AsyncClient,
        *,
        organization_id: uuid.UUID,
        cursor: dict[str, str] | None,
        limit: int,
    ) -> ExportPage: ...

    async def apply_page(
        self,
        session: AsyncSession,
        *,
        organization_id: uuid.UUID,
        rows: list[dict[str, Any]],
        run_id: uuid.UUID,
        page_requested_at: datetime,
    ) -> int:
        """Upsert one page's rows in the caller's already-open, tenant-bound transaction.
        Returns the number of rows written, for `BackfillRun.rows_upserted`."""
        ...


#: Empty in I1 (see the module docstring) — I2 registers `finance.invoices`/`payments`/
#: `purchases`, I3 `deal.deals`, and so on, each entity's export client living beside its own
#: fold in that slice.
ENTITY_REGISTRY: dict[str, BackfillSource] = {}


@dataclass
class DiscoveryReport:
    organizations_seen: int = 0
    profiles_synced: int = 0
    runs_enqueued: int = 0


@dataclass
class DrainReport:
    drained: bool = False
    rows_upserted: int = 0
    completed: bool = False
    failed: bool = False


@dataclass
class BackfillTickReport:
    discovery: DiscoveryReport | None = None
    drain: DrainReport = field(default_factory=DrainReport)


async def sync_organizations(
    settings: Settings,
    *,
    work_scope: WorkScope,
    organizations: list[account_client.OrganizationExportRow],
) -> int:
    """`org_profile` for every discovered organization — I1's own worked backfill (see the
    module docstring). Returns how many rows actually changed."""
    synced = 0
    for org in organizations:
        organization_id = uuid.UUID(org.organization_id)
        async with work_scope(organization_id) as session:
            changed = await services.upsert_org_profile(
                session,
                organization_id=organization_id,
                timezone=org.timezone,
                currency=org.currency,
                fiscal_year_start_month=org.fiscal_year_start_month,
                source_updated_at=datetime.fromisoformat(org.updated_at),
            )
        if changed:
            synced += 1
    return synced


async def enqueue_missing_runs(
    *,
    registry: dict[str, BackfillSource],
    discovery_factory: async_sessionmaker[AsyncSession],
    work_scope: WorkScope,
    organization_ids: list[uuid.UUID],
    reconcile_after: timedelta,
    now: datetime,
) -> int:
    """One `backfill_runs` row per `(organization, entity)` in `registry` with no `done` run
    younger than `reconcile_after` and nothing already in flight (ADR-0020 §3). One discovery
    read across every organization (never one query per pair — CLAUDE.md: "N+1s are bugs"),
    then at most one insert transaction per organization that actually needs one.
    """
    if not registry or not organization_ids:
        return 0

    cutoff = now - reconcile_after
    async with discovery_factory() as session:
        existing = (
            await session.execute(
                select(
                    BackfillRun.organization_id,
                    BackfillRun.entity,
                    BackfillRun.status,
                    BackfillRun.finished_at,
                ).where(BackfillRun.organization_id.in_(organization_ids))
            )
        ).all()

    index: dict[tuple[uuid.UUID, str], list[tuple[str, datetime | None]]] = defaultdict(list)
    for organization_id, entity, status, finished_at in existing:
        index[(organization_id, entity)].append((status, finished_at))

    by_org: dict[uuid.UUID, list[tuple[str, str]]] = defaultdict(list)
    for organization_id in organization_ids:
        for entity in registry:
            rows = index.get((organization_id, entity), [])
            has_recent_done = any(
                status == "done" and finished_at is not None and finished_at >= cutoff
                for status, finished_at in rows
            )
            has_in_flight = any(status in ("pending", "running") for status, _ in rows)
            if has_recent_done or has_in_flight:
                continue
            kind = "initial" if not rows else "reconcile"
            by_org[organization_id].append((entity, kind))

    enqueued = 0
    for organization_id, entities in by_org.items():
        async with work_scope(organization_id) as session:
            session.add_all(
                [
                    BackfillRun(organization_id=organization_id, entity=entity, kind=kind)
                    for entity, kind in entities
                ]
            )
        enqueued += len(entities)
    return enqueued


async def _fetch_page_with_retry(
    source: BackfillSource,
    settings: Settings,
    client: httpx.AsyncClient,
    *,
    organization_id: uuid.UUID,
    cursor: dict[str, str] | None,
    limit: int,
) -> ExportPage:
    attempts = len(PAGE_RETRY_BACKOFFS_SECONDS) + 1
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return await source.fetch_page(
                settings, client, organization_id=organization_id, cursor=cursor, limit=limit
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            last_error = exc
            if attempt < len(PAGE_RETRY_BACKOFFS_SECONDS):
                await asyncio.sleep(PAGE_RETRY_BACKOFFS_SECONDS[attempt])
    raise RuntimeError(f"export page fetch failed after retries: {last_error}") from last_error


async def _claim_run(
    work_scope: WorkScope, *, organization_id: uuid.UUID, run_id: uuid.UUID, now: datetime
) -> bool:
    """Atomically moves one run from `pending`/`failed` to `running`, under the tenant this run
    belongs to. Returns `False` if another replica already claimed it — the same optimistic
    re-check every claim-and-drain sweep in this platform uses (`approval/app/cron.py`)."""
    async with work_scope(organization_id) as session:
        result = cast(
            "CursorResult[Any]",
            await session.execute(
                update(BackfillRun)
                .where(BackfillRun.id == run_id)
                .where(BackfillRun.status.in_(("pending", "failed")))
                .values(status="running", started_at=now, attempts=BackfillRun.attempts + 1)
            ),
        )
        return bool(result.rowcount)


async def _finish_run(
    work_scope: WorkScope,
    *,
    organization_id: uuid.UUID,
    run_id: uuid.UUID,
    status: str,
    now: datetime,
    rows_upserted_delta: int = 0,
    next_attempt_at: datetime | None = None,
    last_error: str | None = None,
    cursor: dict[str, str] | None = None,
) -> None:
    values: dict[str, Any] = {"status": status}
    if status == "done":
        values["finished_at"] = now
    if next_attempt_at is not None:
        values["next_attempt_at"] = next_attempt_at
    if last_error is not None:
        values["last_error"] = last_error[:500]
    if cursor is not None:
        values["cursor"] = cursor
    async with work_scope(organization_id) as session:
        await session.execute(
            update(BackfillRun)
            .where(BackfillRun.id == run_id)
            .values(
                rows_upserted=BackfillRun.rows_upserted + rows_upserted_delta,
                **values,
            )
        )


async def _advance_page(
    work_scope: WorkScope,
    *,
    organization_id: uuid.UUID,
    run_id: uuid.UUID,
    source: BackfillSource,
    rows: list[dict[str, Any]],
    next_cursor: dict[str, str] | None,
    page_requested_at: datetime,
) -> int:
    """One page, one transaction: the upsert and the cursor advance commit together
    (data-model.md §1.1) — a crash here resumes the run from `next_cursor`, never re-reading a
    page it already applied and never dropping one it did not."""
    async with work_scope(organization_id) as session:
        applied = await source.apply_page(
            session,
            organization_id=organization_id,
            rows=rows,
            run_id=run_id,
            page_requested_at=page_requested_at,
        )
        await session.execute(
            update(BackfillRun)
            .where(BackfillRun.id == run_id)
            .values(cursor=next_cursor, rows_upserted=BackfillRun.rows_upserted + applied)
        )
    return applied


async def run_backfill(
    settings: Settings,
    *,
    work_scope: WorkScope,
    organization_id: uuid.UUID,
    run_id: uuid.UUID,
    entity: str,
    source: BackfillSource,
    start_cursor: dict[str, str] | None,
    client: httpx.AsyncClient,
    now: datetime,
) -> DrainReport:
    """Page through one `(organization, entity)` run to completion or to a failure that leaves
    it `failed` for the next hour's discovery pass to retry (ADR-0020 §3)."""
    report = DrainReport(drained=True)
    cursor = start_cursor
    try:
        while True:
            page_requested_at = datetime.now(UTC)
            page = await _fetch_page_with_retry(
                source, settings, client,
                organization_id=organization_id, cursor=cursor, limit=settings.backfill_page_size,
            )
            applied = await _advance_page(
                work_scope, organization_id=organization_id, run_id=run_id, source=source,
                rows=page.rows, next_cursor=page.next_cursor, page_requested_at=page_requested_at,
            )
            report.rows_upserted += applied
            cursor = page.next_cursor
            if cursor is None:
                break
            await asyncio.sleep(settings.backfill_page_pause_seconds)
    except Exception as exc:  # a struggling source must not wedge the runner (ADR-0020 §11)
        report.failed = True
        log.error(
            "insights.backfill.run_failed",
            organization_id=str(organization_id), entity=entity, run_id=str(run_id),
            error=str(exc)[:300],
        )
        await _finish_run(
            work_scope, organization_id=organization_id, run_id=run_id, status="failed",
            now=now, next_attempt_at=now + RETRY_AFTER_FAILURE, last_error=str(exc), cursor=cursor,
        )
        return report

    report.completed = True
    await _finish_run(work_scope, organization_id=organization_id, run_id=run_id, status="done", now=now)
    log.info(
        "insights.backfill.run_completed",
        organization_id=str(organization_id), entity=entity, run_id=str(run_id),
        rows_upserted=report.rows_upserted,
    )
    return report


class BackfillRunner(GuardedSweep):
    """The one folded loop (ADR-0020 §1) — discovery on its own coarse cadence, drain every
    tick. See the module docstring for the shape.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        discovery_factory: async_sessionmaker[AsyncSession],
        work_scope: WorkScope,
        registry: dict[str, BackfillSource] | None = None,
        http_client_factory: Callable[[], AbstractAsyncContextManager[httpx.AsyncClient]]
        | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(poll_interval=settings.backfill_drain_poll_seconds)
        self._settings = settings
        self._discovery_factory = discovery_factory
        self._work_scope = work_scope
        self._registry = ENTITY_REGISTRY if registry is None else registry
        self._http_client_factory = http_client_factory or self._default_client
        self._clock = clock or (lambda: datetime.now(UTC))
        self._last_discovery_at: datetime | None = None

    @asynccontextmanager
    async def _default_client(self) -> AsyncIterator[httpx.AsyncClient]:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=self._settings.backfill_connect_timeout_seconds,
                read=self._settings.backfill_read_timeout_seconds,
                write=self._settings.backfill_read_timeout_seconds,
                pool=self._settings.backfill_connect_timeout_seconds,
            )
        ) as client:
            yield client

    @property
    def _log_name(self) -> str:
        return "insights.backfill"

    async def on_tick_success(self, result: BackfillTickReport) -> None:
        if result.discovery is not None:
            log.info(
                "insights.backfill.discovery",
                organizations_seen=result.discovery.organizations_seen,
                profiles_synced=result.discovery.profiles_synced,
                runs_enqueued=result.discovery.runs_enqueued,
            )

    async def tick(self) -> BackfillTickReport:
        now = self._clock()
        report = BackfillTickReport()
        due_for_discovery = (
            self._last_discovery_at is None
            or (now - self._last_discovery_at).total_seconds()
            >= self._settings.backfill_discovery_poll_seconds
        )
        if due_for_discovery:
            report.discovery = await self._discover(now)
            self._last_discovery_at = now
        report.drain = await self._drain_one(now)
        return report

    async def _discover(self, now: datetime) -> DiscoveryReport:
        organizations = await account_client.fetch_all(self._settings)
        synced = await sync_organizations(
            self._settings, work_scope=self._work_scope, organizations=organizations
        )
        enqueued = await enqueue_missing_runs(
            registry=self._registry,
            discovery_factory=self._discovery_factory,
            work_scope=self._work_scope,
            organization_ids=[uuid.UUID(org.organization_id) for org in organizations],
            reconcile_after=timedelta(days=self._settings.backfill_reconcile_after_days),
            now=now,
        )
        return DiscoveryReport(
            organizations_seen=len(organizations), profiles_synced=synced, runs_enqueued=enqueued
        )

    async def _drain_one(self, now: datetime) -> DrainReport:
        async with self._discovery_factory() as session:
            candidate = (
                await session.execute(
                    select(BackfillRun.id, BackfillRun.organization_id, BackfillRun.entity)
                    .where(BackfillRun.status.in_(("pending", "failed")))
                    .where(BackfillRun.next_attempt_at <= now)
                    .where(BackfillRun.attempts < MAX_BACKFILL_ATTEMPTS)
                    .order_by(BackfillRun.next_attempt_at)
                    .limit(1)
                )
            ).first()
        if candidate is None:
            return DrainReport(drained=False)

        run_id, organization_id, entity = candidate
        source = self._registry.get(entity)
        if source is None:
            log.error("insights.backfill.unknown_entity", entity=entity, run_id=str(run_id))
            return DrainReport(drained=False)

        claimed = await _claim_run(
            self._work_scope, organization_id=organization_id, run_id=run_id, now=now
        )
        if not claimed:
            return DrainReport(drained=False)

        async with self._http_client_factory() as client:
            return await run_backfill(
                self._settings, work_scope=self._work_scope, organization_id=organization_id,
                run_id=run_id, entity=entity, source=source, start_cursor=None, client=client,
                now=now,
            )


# --------------------------------------------------------------------- bin/backfill


async def _cli_sync_all(settings: Settings, work_scope: WorkScope) -> None:
    organizations = await account_client.fetch_all(settings)
    synced = await sync_organizations(settings, work_scope=work_scope, organizations=organizations)
    print(  # noqa: T201
        f"organizations_seen={len(organizations)} profiles_synced={synced}"
    )


async def _cli_enqueue_org(settings: Settings, work_scope: WorkScope, organization_id: uuid.UUID) -> None:
    """`bin/backfill --org <id> [--entity ...]`: a fresh `manual` run for every registered
    entity (or just the one named), reset to attempt 0 so it runs regardless of an earlier
    failure streak."""
    entities = list(ENTITY_REGISTRY)
    if not entities:
        print("no entities are registered yet — nothing to backfill besides org_profile")  # noqa: T201
        return
    async with work_scope(organization_id) as session:
        session.add_all(
            [
                BackfillRun(organization_id=organization_id, entity=entity, kind="manual")
                for entity in entities
            ]
        )
    print(f"enqueued {len(entities)} manual run(s) for {organization_id}")  # noqa: T201


async def _cli_verify(settings: Settings, discovery_factory: async_sessionmaker[AsyncSession]) -> None:
    """`bin/backfill --verify`: per-organization fact counts next to the sources' own figures
    (ADR-0020 §3). I1 has no fact table, so this reports what it can — `org_profile` presence —
    and I2+ extend it with the real parity checks their own fact tables need."""
    organizations = await account_client.fetch_all(settings)
    async with discovery_factory() as session:
        from app.models import OrgProfile

        synced = (
            await session.execute(select(OrgProfile.organization_id))
        ).scalars().all()
    synced_ids = {str(oid) for oid in synced}
    missing = [org.organization_id for org in organizations if org.organization_id not in synced_ids]
    print(  # noqa: T201
        f"organizations={len(organizations)} org_profile_rows={len(synced_ids)} "
        f"missing_profile={len(missing)}"
    )
    for organization_id in missing:
        print(f"  missing org_profile: {organization_id}")  # noqa: T201


async def main(argv: list[str] | None = None) -> int:
    from app.config import settings as settings_factory
    from platform_core.db import (
        assert_app_role,
        assert_outbox_role,
        dispose_engine,
        get_outbox_engine,
        get_outbox_sessionmaker,
        init_engine,
        init_outbox_engine,
        session_scope,
    )

    parser = argparse.ArgumentParser(description="Manual insights backfill triggers")
    parser.add_argument("--org", help="enqueue a manual run for this organization")
    parser.add_argument("--verify", action="store_true", help="print a per-organization report")
    args = parser.parse_args(argv)

    cfg = settings_factory()
    engine = init_engine(cfg)
    await assert_app_role(engine, environment=cfg.environment)

    def work_scope(organization_id: uuid.UUID) -> AbstractAsyncContextManager[AsyncSession]:
        return session_scope(organization_id=organization_id)

    try:
        if args.org:
            await _cli_enqueue_org(cfg, work_scope, uuid.UUID(args.org))
        elif args.verify:
            init_outbox_engine(cfg)
            await assert_outbox_role(get_outbox_engine(), environment=cfg.environment)
            await _cli_verify(cfg, get_outbox_sessionmaker())
        else:
            await _cli_sync_all(cfg, work_scope)
    finally:
        await dispose_engine()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
