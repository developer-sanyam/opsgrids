"""The purge sweep's own loop (`app/cron.py`, ADR-0009 §8): cross-tenant discovery finding
every organization's assets past their retention window, a storage failure leaving the row
un-purged for the next tick, two concurrent sweepers never double-purging the same row, and the
discovery role being unable to read anything the column grant does not name.

`tests/test_assets.py`'s `TestDeletion` covers `remove_asset`'s own `?purge=true` path in
detail; this module covers the loop that discovers *soft-deleted-and-forgotten* assets and
drives the sweep's own action, `services.purge_asset`.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.cron import PurgeSweep
from app.models import Asset
from app.services import db_now
from app.storage import OrphanScanner
from platform_core.db import TENANT_GUC
from tests.helpers import FakeObjectStore

pytestmark = pytest.mark.integration


async def _seed_asset(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    deleted_at: Any,
    purged_at: Any = None,
    storage_key: str | None = None,
) -> Asset:
    """A soft-deleted asset row, backdated to whatever `deleted_at` a test needs — the sweep's
    retention-window comparison cannot be exercised through the API, which only ever sets
    `deleted_at` to the request's own instant."""
    asset_id = uuid.uuid4()
    asset = Asset(
        id=asset_id,
        organization_id=org_id,
        storage_key=storage_key or f"{org_id}/{asset_id}",
        content_type="image/jpeg",
        size_bytes=1024,
        width=64,
        height=48,
        checksum_sha256="0" * 64,
        deleted_at=deleted_at,
        purged_at=purged_at,
    )
    async with session.begin():
        session.add(asset)
    return asset


@asynccontextmanager
async def _org_bound_session(organization_id: uuid.UUID) -> Any:
    """A second, independent connection bound to a different organization than the shared
    `session` fixture — matching `services/approval/tests/test_sweep.py`'s own helper — used
    here to seed a second tenant's rows so cross-tenant discovery is exercised against two
    genuinely separate bindings rather than one session re-purposed."""
    cfg = settings()
    engine = create_async_engine(
        cfg.app_dsn, connect_args={"server_settings": {"search_path": "document,public"}}
    )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as s:

            @sa_event.listens_for(s.sync_session, "after_begin")
            def _bind(_session, _transaction, connection) -> None:  # type: ignore[no-untyped-def]
                connection.exec_driver_sql(
                    f"SELECT set_config('{TENANT_GUC}', '{organization_id}', true)"
                )

            yield s
    finally:
        await engine.dispose()


class TestDiscoveryIsCrossTenant:
    async def test_the_sweep_purges_every_organizations_own_due_assets(
        self, session: AsyncSession, org_id: uuid.UUID, other_org: uuid.UUID, sweep: Any
    ) -> None:
        async with session.begin():
            now = await db_now(session)
        asset_a = await _seed_asset(session, org_id, deleted_at=now - timedelta(days=31))
        asset_a_id = asset_a.id

        async with _org_bound_session(other_org) as other_session:
            asset_b = await _seed_asset(
                other_session, other_org, deleted_at=now - timedelta(days=45)
            )
        asset_b_id = asset_b.id

        report = await sweep.tick()

        assert report.considered == 2
        assert report.purged == 2

        # The sweep's own write ran on a separate session (the sweep's `work_scope`), so this
        # test's `session` still holds the pre-purge row in its identity map — expire it to
        # prove the write actually landed in the database, not merely that nothing raised.
        # The id is captured above, before expiring, so re-selecting on it never touches an
        # expired attribute outside the session's own async context.
        session.expire_all()
        async with session.begin():
            purged_a = (
                await session.execute(select(Asset).where(Asset.id == asset_a_id))
            ).scalar_one()
        assert purged_a.purged_at is not None

        async with _org_bound_session(other_org) as other_session, other_session.begin():
            purged_b = (
                await other_session.execute(select(Asset).where(Asset.id == asset_b_id))
            ).scalar_one()
        assert purged_b.purged_at is not None


class TestOnlyPastTheWindowIsPurged:
    async def test_a_recently_deleted_asset_is_not_touched(
        self, session: AsyncSession, org_id: uuid.UUID, sweep: Any, store: FakeObjectStore
    ) -> None:
        async with session.begin():
            now = await db_now(session)
        recent = await _seed_asset(session, org_id, deleted_at=now - timedelta(days=1))
        store.objects[recent.storage_key] = (b"x", "image/jpeg")

        report = await sweep.tick()

        assert report.considered == 0
        assert report.purged == 0
        assert recent.storage_key in store.objects

        async with session.begin():
            row = (await session.execute(select(Asset).where(Asset.id == recent.id))).scalar_one()
        assert row.purged_at is None

    async def test_an_asset_exactly_thirty_days_old_is_due(
        self, session: AsyncSession, org_id: uuid.UUID, sweep: Any
    ) -> None:
        async with session.begin():
            now = await db_now(session)
        due = await _seed_asset(session, org_id, deleted_at=now - timedelta(days=30, seconds=1))
        due_id = due.id

        report = await sweep.tick()

        assert report.purged == 1
        session.expire_all()
        async with session.begin():
            row = (await session.execute(select(Asset).where(Asset.id == due_id))).scalar_one()
        assert row.purged_at is not None


class TestAlreadyPurgedIsSkipped:
    async def test_an_already_purged_asset_is_not_rediscovered(
        self, session: AsyncSession, org_id: uuid.UUID, sweep: Any
    ) -> None:
        async with session.begin():
            now = await db_now(session)
        already = await _seed_asset(
            session, org_id, deleted_at=now - timedelta(days=60), purged_at=now - timedelta(days=1)
        )
        already_id, already_purged_at = already.id, already.purged_at
        due = await _seed_asset(session, org_id, deleted_at=now - timedelta(days=45))
        due_id = due.id

        report = await sweep.tick()

        assert report.considered == 1, "the already-purged row must never appear in discovery"
        assert report.purged == 1

        session.expire_all()
        async with session.begin():
            row = (await session.execute(select(Asset).where(Asset.id == due_id))).scalar_one()
        assert row.purged_at is not None
        untouched = (
            await session.execute(select(Asset).where(Asset.id == already_id))
        ).scalar_one()
        assert untouched.purged_at == already_purged_at


class TestMissingObjectIsSuccess:
    async def test_purging_a_row_whose_object_is_already_gone_still_succeeds(
        self, session: AsyncSession, org_id: uuid.UUID, sweep: Any, store: FakeObjectStore
    ) -> None:
        """S3 `DeleteObject` on a missing key returns 204 — `store.delete` never raises for a
        key it does not hold, which is what lets the sweep mark a row purged even when the
        object was somehow already removed."""
        async with session.begin():
            now = await db_now(session)
        due = await _seed_asset(session, org_id, deleted_at=now - timedelta(days=31))
        due_id, due_key = due.id, due.storage_key
        assert due_key not in store.objects

        report = await sweep.tick()

        assert report.purged == 1
        session.expire_all()
        async with session.begin():
            row = (await session.execute(select(Asset).where(Asset.id == due_id))).scalar_one()
        assert row.purged_at is not None


class TestAStorageFailureLeavesTheRowUnpurged:
    async def test_a_failed_object_delete_leaves_purged_at_null_for_the_next_tick(
        self, session: AsyncSession, org_id: uuid.UUID, sweep: Any, store: FakeObjectStore
    ) -> None:
        async with session.begin():
            now = await db_now(session)
        due = await _seed_asset(session, org_id, deleted_at=now - timedelta(days=31))
        store.fail_delete = True

        report = await sweep.tick()

        assert report.failed == 1
        assert report.purged == 0

        async with session.begin():
            row = (await session.execute(select(Asset).where(Asset.id == due.id))).scalar_one()
        assert row.purged_at is None, (
            "a row claiming the object is gone while it may still be world-readable is the "
            "one lie this table must never tell"
        )

        # The next tick, once storage recovers, purges it.
        store.fail_delete = False
        second = await sweep.tick()
        assert second.purged == 1


class TestTheDiscoveryRoleIsColumnLimited:
    async def test_the_outbox_role_cannot_read_a_column_outside_the_grant(
        self, sweep_engines: tuple[Any, Any]
    ) -> None:
        """`migrations/env.py`'s `grant_purge_sweep_reader` names exactly four columns —
        `organization_id`, `id`, `deleted_at`, `purged_at`. A query that reaches for a fifth
        must fail loudly with `42501`, not quietly widen what a cross-tenant bypass role can
        read (the same assertion `services/approval/tests/test_sweep_plan.py` makes for the
        SLA sweep's own reader)."""
        discovery_engine, _work_engine = sweep_engines
        with pytest.raises(DBAPIError, match="permission denied"):
            async with discovery_engine.connect() as conn:
                await conn.execute(text("SELECT storage_key FROM document.assets LIMIT 1"))


class TestTenantBoundWrite:
    async def test_an_asset_is_invisible_to_a_session_bound_to_a_different_organization(
        self, session: AsyncSession, org_id: uuid.UUID, other_org: uuid.UUID
    ) -> None:
        """The purge sweep's own write always runs `session_scope(organization_id=...)` —
        `app/selectors.get_asset_for_purge_sweep` is called with the organization discovery
        found, but row-level security is the net underneath that call, not merely the
        argument: a session bound to the *wrong* tenant must find nothing even when asked
        about an id that legitimately belongs to someone else — org A's own row must not be
        reachable through a transaction bound to org B's GUC."""
        from app import selectors

        async with session.begin():
            now = await db_now(session)
        asset = await _seed_asset(session, org_id, deleted_at=now - timedelta(days=31))

        async with _org_bound_session(other_org) as other_session, other_session.begin():
            found = await selectors.get_asset_for_purge_sweep(other_session, org_id, asset.id)
        assert found is None, "org B's GUC must never see org A's row, whatever id is asked for"

        async with session.begin():
            found_correctly = await selectors.get_asset_for_purge_sweep(session, org_id, asset.id)
        assert found_correctly is not None


class TestConcurrentSweepersNeverDoublePurge:
    async def test_two_sweepers_draining_the_same_backlog_agree_on_the_total(
        self, session: AsyncSession, org_id: uuid.UUID, sweep_engines: Any
    ) -> None:
        async with session.begin():
            now = await db_now(session)
        for i in range(5):
            await _seed_asset(session, org_id, deleted_at=now - timedelta(days=31 + i))

        discovery_engine, work_engine = sweep_engines
        discovery = async_sessionmaker(discovery_engine, expire_on_commit=False)
        work_maker = async_sessionmaker(work_engine, expire_on_commit=False)

        @asynccontextmanager
        async def work_scope(organization_id: uuid.UUID) -> Any:
            async with work_maker() as s, s.begin():
                await s.execute(
                    text(f"SELECT set_config('{TENANT_GUC}', :org, true)"),
                    {"org": str(organization_id)},
                )
                yield s

        store_a, store_b = FakeObjectStore(settings()), FakeObjectStore(settings())

        sweep_a = PurgeSweep(
            discovery_factory=discovery,
            work_scope=work_scope,
            store=store_a,
            scanner=OrphanScanner(settings()),
            retention=timedelta(days=30),
        )
        sweep_b = PurgeSweep(
            discovery_factory=discovery,
            work_scope=work_scope,
            store=store_b,
            scanner=OrphanScanner(settings()),
            retention=timedelta(days=30),
        )

        report_a, report_b = await asyncio.gather(sweep_a.tick(), sweep_b.tick())

        # `purged + skipped` fully accounts for both sweepers' ten combined attempts (five
        # rows, two racing sweepers each) — `== 5` is the whole of "no double purge, no missed
        # one" for this backlog.
        assert report_a.purged + report_b.purged == 5, (
            "two sweepers draining the same 5-row backlog purged a different total than 5 — "
            "either a row was double-purged or one was missed"
        )

        async with session.begin():
            still_unpurged = (
                (
                    await session.execute(
                        select(Asset).where(
                            Asset.organization_id == org_id, Asset.purged_at.is_(None)
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert still_unpurged == []


class TestOneAssetsFailureDoesNotStopTheRest:
    async def test_a_failing_asset_is_counted_and_every_other_still_purges(
        self,
        session: AsyncSession,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
        sweep: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app import services

        async with session.begin():
            now = await db_now(session)
        broken = await _seed_asset(session, org_id, deleted_at=now - timedelta(days=40))
        async with _org_bound_session(other_org) as other_session:
            healthy = await _seed_asset(
                other_session, other_org, deleted_at=now - timedelta(days=31)
            )

        real_purge_asset = services.purge_asset

        async def _raise_for_org(work_session, store, *, organization_id, asset_id, retention):
            if organization_id == org_id:
                raise RuntimeError("simulated purge failure")
            return await real_purge_asset(
                work_session,
                store,
                organization_id=organization_id,
                asset_id=asset_id,
                retention=retention,
            )

        monkeypatch.setattr(services, "purge_asset", _raise_for_org)

        report = await sweep.tick()

        assert report.considered == 2
        assert report.failed == 1
        assert report.purged == 1

        async with _org_bound_session(other_org) as other_session, other_session.begin():
            row = (
                await other_session.execute(select(Asset).where(Asset.id == healthy.id))
            ).scalar_one()
        assert row.purged_at is not None

        async with session.begin():
            row = (await session.execute(select(Asset).where(Asset.id == broken.id))).scalar_one()
        assert row.purged_at is None
