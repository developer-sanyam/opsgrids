"""The campaign send driver's own loop (`app/cron.py`): cross-tenant discovery finding every
organization's due campaigns in one pass, one campaign's own failure never stopping another's,
and the in-memory backoff actually holding off a campaign that just failed. `test_engage_
campaigns.py` covers `claim_campaign_batch`/`record_campaign_batch`'s own decisions in detail
(suppression, the cap, pause/resume, crash recovery); this module covers the loop that discovers
work and drives it — the same split `services/approval/tests/test_sweep.py` documents for its
own sweep.

Real PostgreSQL, on the same two engines `app/cron.py`'s own `main()` wires: discovery on the
outbox DSN (BYPASSRLS, cross-tenant), work on the app DSN with the tenant bound per transaction.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import event as sa_event
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import models, services
from app.config import settings
from app.cron import CampaignDriver
from app.transports import TransportUnavailable
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC
from tests.conftest import _connect_args
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration


@pytest.fixture
def other_org() -> uuid.UUID:
    return uuid.uuid4()


@asynccontextmanager
async def _org_bound_session(organization_id: uuid.UUID) -> Any:
    """A second, independent app-role connection bound to a different organization than the
    shared `session` fixture — the same seam `approval`'s and `subscription`'s own sweep tests
    use to seed a second tenant's rows with real network I/O rather than one session re-purposed.
    """
    cfg = settings()
    engine = create_async_engine(cfg.app_dsn, connect_args=_connect_args())
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


@pytest_asyncio.fixture
async def driver_engines(session: AsyncSession) -> AsyncIterator[tuple[Any, Any]]:
    """The two engines the driver runs on, wired exactly as `app/cron.py`'s own `main()` wires
    them. Depends on `session` only for its truncation, so a test starts from a clean schema."""
    cfg = settings()
    discovery = create_async_engine(cfg.outbox_dsn, connect_args=_connect_args())
    work = create_async_engine(cfg.app_dsn, connect_args=_connect_args())
    yield discovery, work
    await discovery.dispose()
    await work.dispose()


@pytest_asyncio.fixture
async def driver(driver_engines: tuple[Any, Any]) -> CampaignDriver:
    """A `CampaignDriver` over real PostgreSQL, drivable one `tick(now)` at a time — real rows,
    real locks, real policies, no clock and no engine patched."""
    discovery_engine, work_engine = driver_engines
    discovery = async_sessionmaker(discovery_engine, expire_on_commit=False)
    work_maker = async_sessionmaker(work_engine, expire_on_commit=False)

    @asynccontextmanager
    async def work_scope(organization_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
        async with work_maker() as s, s.begin():
            await s.execute(
                text(f"SELECT set_config('{TENANT_GUC}', :org, true)"),
                {"org": str(organization_id)},
            )
            yield s

    return CampaignDriver(
        discovery_factory=discovery,
        work_scope=work_scope,
        batch_size=200,
        lease_seconds=300,
        pause_retry_seconds=900.0,
        poll_interval=10.0,
    )


async def _seed_sending_campaign(
    session: AsyncSession, org_id: uuid.UUID, *emails: str
) -> uuid.UUID:
    """A campaign already `sending` with its audience already expanded — the state the shared
    consumer's `expand_campaign_send` leaves behind, and the state the driver picks up from."""
    domain = models.EmailDomain(organization_id=org_id, domain="company.com", status="verified")
    async with session.begin():
        session.add(domain)
        await session.flush()
        sender = models.Sender(
            organization_id=org_id,
            domain_id=domain.id,
            name="News",
            address="news@company.com",
            verified_at=datetime.now(UTC),
        )
        session.add(sender)
        session.add(
            models.EmailConnection(
                organization_id=org_id,
                kind="google",
                host="smtp.gmail.com",
                username="news@company.com",
                daily_cap=2000,
            )
        )
        await session.flush()
        sender_id = sender.id

    write = ctx_for(org_id, "engage.audience.contact.write", Scope.ORG)
    async with session.begin():
        for email in emails:
            await services.create_contact(session, write, email=email)

    wr = ctx_for(org_id, "engage.campaign.campaign.write", Scope.ORG)
    async with session.begin():
        row = await services.create_campaign(
            session,
            wr,
            fields={
                "name": "Autumn",
                "sender_id": sender_id,
                "subject": "News",
                "html_body": "<p>Hello</p>",
                "text_body": "Hello",
                "audience": "all_sendable",
                "list_id": None,
                "scheduled_at": None,
            },
        )
        campaign_id = row.id

    send = ctx_for(org_id, "engage.campaign.campaign.send", Scope.ORG)
    async with session.begin():
        await services.request_send(session, send, campaign_id=campaign_id)
    async with session.begin():
        await services.expand_campaign(
            session, organization_id=org_id, campaign_id=campaign_id, batch_size=200
        )
    return campaign_id


class TestDiscoveryIsCrossTenant:
    async def test_one_tick_drains_every_organizations_own_campaign(
        self, session: AsyncSession, org_id: uuid.UUID, other_org: uuid.UUID, driver: CampaignDriver
    ) -> None:
        """Discovery is cross-tenant, every write is not — the same seam `approval.cron`'s and
        `subscription.cron`'s own sweeps document: the bypass buys discovery, never authority. A
        bug that lost the per-tenant binding on the write side would fail the tenant policy here
        rather than send across the platform."""
        campaign_a = await _seed_sending_campaign(session, org_id, "a@example.com")
        async with _org_bound_session(other_org) as other_session:
            campaign_b = await _seed_sending_campaign(other_session, other_org, "b@example.com")

        report = await driver.tick()

        assert report.considered == 2
        assert report.completed == 2

        async with session.begin():
            row_a = await session.get(models.Campaign, campaign_a)
            assert row_a is not None and row_a.status == "sent"

        async with _org_bound_session(other_org) as other_session, other_session.begin():
            row_b = await other_session.get(models.Campaign, campaign_b)
            assert row_b is not None and row_b.status == "sent"


class TestOneCampaignsFailureIsContained:
    async def test_a_campaign_that_raises_does_not_stop_the_rest_of_the_tick(
        self,
        session: AsyncSession,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
        driver: CampaignDriver,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`_drain`'s own `try`/`except`: a platform-wide `ORDER BY started_at` means a campaign
        that keeps raising is head-of-line on every tick unless its own exception is caught and
        counted rather than left to propagate past every candidate ordered after it."""
        broken = await _seed_sending_campaign(session, org_id, "a@example.com")
        async with _org_bound_session(other_org) as other_session:
            healthy = await _seed_sending_campaign(other_session, other_org, "b@example.com")

        real_claim = services.claim_campaign_batch

        async def _boom_for_broken(
            session: AsyncSession, *, campaign_id: uuid.UUID, **kwargs: Any
        ) -> Any:
            if campaign_id == broken:
                raise RuntimeError("a bug, not a business outcome")
            return await real_claim(session, campaign_id=campaign_id, **kwargs)

        monkeypatch.setattr(services, "claim_campaign_batch", _boom_for_broken)

        report = await driver.tick()

        assert report.considered == 2
        assert report.failed == 1
        assert report.completed == 1

        async with _org_bound_session(other_org) as other_session, other_session.begin():
            row = await other_session.get(models.Campaign, healthy)
            assert row is not None and row.status == "sent"


class TestBackoff:
    async def test_a_provider_outage_backs_the_campaign_off_without_a_busy_loop(
        self,
        session: AsyncSession,
        org_id: uuid.UUID,
        driver: CampaignDriver,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A `TransportUnavailable` batch marks the campaign backed off in memory; the very next
        tick, before the backoff window has passed, must not attempt it again — the review's
        "no busy loop when rows are locked" and "add backoff on provider-unavailable" together."""
        campaign_id = await _seed_sending_campaign(
            session, org_id, "a@example.com", "b@example.com"
        )

        class AlwaysUnavailable:
            name = "recording"

            async def send(self, message: Any) -> Any:
                raise TransportUnavailable("provider is down")

        async def _fake_resolve(batch: Any) -> Any:
            return AlwaysUnavailable()

        monkeypatch.setattr(services, "resolve_campaign_transport", _fake_resolve)

        at = datetime.now(UTC)
        report = await driver.tick(now=at)
        assert report.backed_off == 1
        assert report.considered == 1

        # Still backed off a moment later, in the same tick's own clock terms: nothing was
        # claimed a second time.
        report_again = await driver.tick(now=at)
        assert report_again.backed_off == 1

        async with session.begin():
            row = await session.get(models.Campaign, campaign_id)
            assert row is not None and row.status == "sending"  # never resumed, never lost


class TestPausedRetrySchedule:
    async def test_a_paused_campaign_is_not_retried_before_its_backoff_elapses(
        self, session: AsyncSession, org_id: uuid.UUID, driver_engines: tuple[Any, Any]
    ) -> None:
        """`selectors.due_campaigns` itself, not the in-memory backoff: a `paused` campaign is
        only due once `paused_at` is old enough, which is what keeps a still-broken route from
        being retried every tick even across a driver restart (the in-memory backoff does not
        survive one, by design — see `app/cron.py`'s module docstring)."""
        from app import selectors

        discovery_engine, _work_engine = driver_engines
        campaign_id = await _seed_sending_campaign(session, org_id, "a@example.com")
        async with session.begin():
            row = await session.get(models.Campaign, campaign_id)
            row.status = "paused"
            row.paused_at = datetime.now(UTC)

        discovery_maker = async_sessionmaker(discovery_engine, expire_on_commit=False)
        async with discovery_maker() as discovery_session, discovery_session.begin():
            not_yet_due = await selectors.due_campaigns(
                discovery_session,
                paused_retry_before=datetime.now(UTC) - timedelta(seconds=1),
                limit=10,
            )
        assert campaign_id not in {c for _org, c in not_yet_due}

        async with discovery_maker() as discovery_session, discovery_session.begin():
            due_now = await selectors.due_campaigns(
                discovery_session,
                paused_retry_before=datetime.now(UTC) + timedelta(seconds=1),
                limit=10,
            )
        assert campaign_id in {c for _org, c in due_now}
