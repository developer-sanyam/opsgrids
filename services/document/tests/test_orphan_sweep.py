"""The orphan pass of the purge sweep (ADR-0009 §9 addendum, `app/cron.py`'s
`PurgeSweep._sweep_orphans`): an object with no row at all, discovered by listing the bucket
rather than by querying a table.

Every test here uses `FakeOrphanScanner`, which lists and deletes against an in-memory dict —
`test_storage_minio.py` is where a genuine `ListObjectsV2` against the real, scoped credential
is proven; neither test substitutes for the other, the same split the write-path tests already
draw between `FakeObjectStore` and the real bucket.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.cron import PurgeSweep
from app.services import db_now
from app.storage import OrphanScanner
from tests.helpers import FakeObjectStore, FakeOrphanScanner

pytestmark = pytest.mark.integration


def _make_sweep(
    discovery_factory: async_sessionmaker[AsyncSession],
    scanner: FakeOrphanScanner,
    *,
    dry_run: bool = True,
    max_deletes: int = 100,
    min_age: timedelta = timedelta(hours=24),
) -> PurgeSweep:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _no_op_work_scope(organization_id: uuid.UUID):
        raise AssertionError("the orphan pass never writes through a tenant-bound session")
        yield  # pragma: no cover - unreachable, keeps this an async generator

    return PurgeSweep(
        discovery_factory=discovery_factory,
        work_scope=_no_op_work_scope,
        store=FakeObjectStore(settings()),
        scanner=scanner,
        retention=timedelta(days=30),
        orphan_enabled=True,
        orphan_dry_run=dry_run,
        orphan_min_age=min_age,
        orphan_max_deletes=max_deletes,
    )


class TestDryRunListsOnlyRealOrphans:
    async def test_an_object_with_no_row_is_reported_and_a_referenced_one_is_never_touched(
        self, session: AsyncSession, org_id: uuid.UUID, sweep_engines
    ) -> None:
        from app.models import Asset

        discovery_engine, _work_engine = sweep_engines
        discovery = async_sessionmaker(discovery_engine, expire_on_commit=False)

        async with session.begin():
            now = await db_now(session)
            referenced_id = uuid.uuid4()
            session.add(
                Asset(
                    id=referenced_id,
                    organization_id=org_id,
                    storage_key=f"{org_id}/{referenced_id}",
                    content_type="image/jpeg",
                    size_bytes=1024,
                    width=64,
                    height=48,
                    checksum_sha256="0" * 64,
                )
            )

        orphan_id = uuid.uuid4()
        old = now - timedelta(hours=48)
        scanner = FakeOrphanScanner(settings())
        scanner.seed(f"{org_id}/{referenced_id}", last_modified=old)  # has a row: never an orphan
        scanner.seed(f"{org_id}/{orphan_id}", last_modified=old)  # no row: the real orphan

        sweep = _make_sweep(discovery, scanner, dry_run=True)
        report = await sweep.tick(now=now)

        assert report.orphan_considered == 2
        assert report.orphan_would_delete == 1
        assert report.orphan_deleted == 0
        # Dry run: nothing is actually removed, from either key.
        assert f"{org_id}/{referenced_id}" in scanner.objects
        assert f"{org_id}/{orphan_id}" in scanner.objects
        assert scanner.deleted == []

    async def test_an_object_younger_than_the_safety_margin_is_never_a_candidate(
        self, sweep_engines
    ) -> None:
        """The write-order race ADR-0009 §9 names: the object write and the row commit are two
        operations, so an object that is merely mid-upload must never look like an orphan."""
        discovery_engine, _work_engine = sweep_engines
        discovery = async_sessionmaker(discovery_engine, expire_on_commit=False)

        now = datetime.now(UTC)
        fresh_orphan = f"{uuid.uuid4()}/{uuid.uuid4()}"
        scanner = FakeOrphanScanner(settings())
        scanner.seed(fresh_orphan, last_modified=now - timedelta(minutes=1))

        sweep = _make_sweep(discovery, scanner, dry_run=True, min_age=timedelta(hours=24))
        report = await sweep.tick(now=now)

        assert report.orphan_considered == 0
        assert report.orphan_would_delete == 0

    async def test_a_key_this_service_never_wrote_is_skipped_not_guessed_at(
        self, sweep_engines
    ) -> None:
        discovery_engine, _work_engine = sweep_engines
        discovery = async_sessionmaker(discovery_engine, expire_on_commit=False)

        now = datetime.now(UTC)
        scanner = FakeOrphanScanner(settings())
        scanner.seed("not-a-valid-key-shape", last_modified=now - timedelta(hours=48))

        sweep = _make_sweep(discovery, scanner, dry_run=True)
        report = await sweep.tick(now=now)

        assert report.orphan_considered == 0


class TestLiveModeActuallyDeletes:
    async def test_switching_off_dry_run_deletes_the_orphan_and_spares_the_referenced_object(
        self, session: AsyncSession, org_id: uuid.UUID, sweep_engines
    ) -> None:
        from app.models import Asset

        discovery_engine, _work_engine = sweep_engines
        discovery = async_sessionmaker(discovery_engine, expire_on_commit=False)

        async with session.begin():
            now = await db_now(session)
            referenced_id = uuid.uuid4()
            session.add(
                Asset(
                    id=referenced_id,
                    organization_id=org_id,
                    storage_key=f"{org_id}/{referenced_id}",
                    content_type="image/jpeg",
                    size_bytes=1024,
                    width=64,
                    height=48,
                    checksum_sha256="0" * 64,
                )
            )

        orphan_id = uuid.uuid4()
        old = now - timedelta(hours=48)
        scanner = FakeOrphanScanner(settings())
        scanner.seed(f"{org_id}/{referenced_id}", last_modified=old)
        scanner.seed(f"{org_id}/{orphan_id}", last_modified=old)

        sweep = _make_sweep(discovery, scanner, dry_run=False)
        report = await sweep.tick(now=now)

        assert report.orphan_deleted == 1
        assert scanner.deleted == [f"{org_id}/{orphan_id}"]
        assert f"{org_id}/{orphan_id}" not in scanner.objects
        assert f"{org_id}/{referenced_id}" in scanner.objects, (
            "a referenced object is never deleted"
        )


class TestTheSafetyCap:
    async def test_one_tick_never_deletes_more_than_the_configured_cap(self, sweep_engines) -> None:
        discovery_engine, _work_engine = sweep_engines
        discovery = async_sessionmaker(discovery_engine, expire_on_commit=False)

        now = datetime.now(UTC)
        old = now - timedelta(hours=48)
        scanner = FakeOrphanScanner(settings())
        for _ in range(10):
            scanner.seed(f"{uuid.uuid4()}/{uuid.uuid4()}", last_modified=old)

        sweep = _make_sweep(discovery, scanner, dry_run=False, max_deletes=3)
        report = await sweep.tick(now=now)

        assert report.orphan_considered == 3
        assert report.orphan_deleted == 3
        assert len(scanner.deleted) == 3


class TestUnconfiguredScannerIsASkipNotAFailure:
    async def test_a_scanner_with_no_credential_skips_the_pass_quietly(self, sweep_engines) -> None:
        discovery_engine, _work_engine = sweep_engines
        discovery = async_sessionmaker(discovery_engine, expire_on_commit=False)

        cfg = settings().model_copy(
            update={"s3_orphan_access_key": None, "s3_orphan_secret_key": None}
        )
        unconfigured = OrphanScanner(cfg)
        assert not unconfigured.is_configured()

        sweep = _make_sweep(discovery, unconfigured, dry_run=True)
        report = await sweep.tick(now=datetime.now(UTC))

        assert report.orphan_considered == 0
        assert report.orphan_failed == 0
