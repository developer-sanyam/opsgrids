"""Periodic re-verification (`services.recheck_domain`, `app/cron.py`'s `DomainRecheckSweep`).

`tests/test_domains.py` covers claim/verify/release in detail; this module covers the other
half of ADR-0000 §1 this service owns — noticing a verified domain's TXT record disappearing —
and the sweep that discovers and drives it unattended, the one thing a `client`-fixture test
bound to a single organization cannot exercise, because this sweep's whole job is to cross that
boundary safely (the same reasoning `services/storefront/tests/test_cron.py`'s own module
docstring gives for its sweep).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.config import settings
from app.cron import DomainRecheckSweep
from app.models import AuditLog, Membership, Organization, OrgDomain
from app.schemas import DomainIn
from platform_core.auth import Scope
from platform_core.db import (
    dispose_directory_engine,
    get_directory_sessionmaker,
    init_directory_engine,
    session_scope,
)
from tests.helpers import ctx_for as _ctx_for
from tests.test_domains import StubResolver

pytestmark = pytest.mark.integration


def ctx_for(org, membership, permission, scope=Scope.ORG):
    return _ctx_for(org.id, membership, permission, scope)


@pytest_asyncio.fixture
async def directory_engine() -> AsyncIterator[None]:
    """The real, process-wide directory engine `get_directory_sessionmaker()` reads — the same
    seam `services/storefront/tests/test_cron.py`'s own `engines` fixture initialises for its
    sweep, for the same reason: discovery has to run on the real BYPASSRLS role to prove
    anything about crossing the tenant boundary."""
    init_directory_engine(settings())
    yield
    await dispose_directory_engine()


async def _verified_domain(
    session: AsyncSession, ctx, name: str, *, resolver: StubResolver | None = None
) -> OrgDomain:
    async with session.begin():
        row = await services.claim_domain(session, ctx, DomainIn(domain=name))
    stub = resolver or StubResolverFactory(row)
    async with session.begin():
        row = await selectors.get_domain(session, ctx.organization_id, row.id)
        assert row is not None
        await services.verify_domain(session, ctx, row, resolver=stub)
    async with session.begin():
        row = await selectors.get_domain(session, ctx.organization_id, row.id)
        assert row is not None
    return row


def StubResolverFactory(row: OrgDomain) -> StubResolver:
    from app import domain as domain_module

    return StubResolver(
        {
            (domain_module.verification_txt_host(row.domain), "TXT"): [
                domain_module.verification_txt_value(row.verification_token)
            ]
        }
    )


class TestRecheckDomain:
    async def test_a_passing_check_records_last_checked_and_ok(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.identity.domain.write")
        row = await _verified_domain(session, ctx, "acme.example")
        resolver = StubResolverFactory(row)

        async with session.begin():
            row = await selectors.get_domain(session, org.id, row.id)
            assert row is not None
            checked = await services.recheck_domain(session, row, resolver=resolver)

        assert checked.last_checked_at is not None
        assert checked.last_check_ok is True
        assert checked.failing_since is None
        assert checked.verified_at is not None  # untouched

    async def test_a_missing_record_flags_failing_and_publishes_once(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.identity.domain.write")
        row = await _verified_domain(session, ctx, "acme.example")
        now = datetime.now(UTC)

        async with session.begin():
            row = await selectors.get_domain(session, org.id, row.id)
            assert row is not None
            first = await services.recheck_domain(session, row, resolver=StubResolver({}), now=now)
        assert first.last_check_ok is False
        assert first.failing_since == now
        assert first.verified_at is not None  # never revoked

        # A second miss must not push the clock forward — "failing since" stays the first
        # failure, which is the number an administrator acts on.
        async with session.begin():
            row = await selectors.get_domain(session, org.id, row.id)
            assert row is not None
            second = await services.recheck_domain(
                session, row, resolver=StubResolver({}), now=now + timedelta(hours=1)
            )
        assert second.failing_since == now

        async with admin_session.begin():
            events = (
                (
                    await admin_session.execute(
                        select(AuditLog).where(
                            AuditLog.organization_id == org.id,
                            AuditLog.action == "domain.check_failed",
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(events) == 1, "the failure must be recorded once, not on every tick"
        assert events[0].acting_as == "domain-recheck-sweep"
        assert events[0].actor_id is None

    async def test_recovering_clears_failing_since_and_publishes_once(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.identity.domain.write")
        row = await _verified_domain(session, ctx, "acme.example")
        resolver = StubResolverFactory(row)

        async with session.begin():
            row = await selectors.get_domain(session, org.id, row.id)
            assert row is not None
            await services.recheck_domain(session, row, resolver=StubResolver({}))

        async with session.begin():
            row = await selectors.get_domain(session, org.id, row.id)
            assert row is not None
            recovered = await services.recheck_domain(session, row, resolver=resolver)
        assert recovered.last_check_ok is True
        assert recovered.failing_since is None

        async with admin_session.begin():
            recovered_rows = (
                (
                    await admin_session.execute(
                        select(AuditLog).where(
                            AuditLog.organization_id == org.id,
                            AuditLog.action == "domain.check_recovered",
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(recovered_rows) == 1


class TestDomainRecheckSweep:
    @pytest.fixture
    def sweep(self, directory_engine: None) -> DomainRecheckSweep:
        return DomainRecheckSweep(
            discovery_factory=get_directory_sessionmaker(),
            work_scope=lambda organization_id: session_scope(organization_id=organization_id),
            interval=timedelta(seconds=60),
            batch_size=10,
        )

    async def test_only_due_verified_domains_are_considered(
        self,
        sweep: DomainRecheckSweep,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.identity.domain.write")
        verified = await _verified_domain(session, ctx, "verified.example")
        async with session.begin():
            await services.claim_domain(session, ctx, DomainIn(domain="pending.example"))
        # Already checked recently — not due again inside the 60s interval.
        async with session.begin():
            fresh = await selectors.get_domain(session, org.id, verified.id)
            assert fresh is not None
            fresh.last_checked_at = datetime.now(UTC)

        report = await sweep.tick()
        assert report.considered == 0

    async def test_a_never_checked_verified_domain_is_due_and_checked(
        self,
        sweep: DomainRecheckSweep,
        session: AsyncSession,
        admin_session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.identity.domain.write")
        row = await _verified_domain(session, ctx, "verified.example")

        # The sweep's own `_recheck_one` never takes a resolver — it always asks
        # `dns_check`'s own default. Patched here so this test never opens a socket, the same
        # seam `test_one_domains_failure_does_not_stop_the_sweep` uses below.
        import app.dns_check as dns_check_module

        resolver = StubResolverFactory(row)
        original = dns_check_module._default_resolver
        dns_check_module._default_resolver = lambda: resolver
        try:
            report = await sweep.tick()
        finally:
            dns_check_module._default_resolver = original
        assert report.considered == 1
        assert report.checked == 1
        assert report.failed == 0

        # The sweep writes through `session_scope`'s own session, a different connection (and
        # identity map) from this test's `session` fixture — `expire_all()` and a read through
        # `admin_session` is what makes the write visible, the same pattern
        # `services/storefront/tests/test_cron.py` uses for its own sweep.
        admin_session.expire_all()
        async with admin_session.begin():
            checked = await selectors.get_domain(admin_session, org.id, row.id)
            assert checked is not None
            assert checked.last_checked_at is not None

    async def test_one_domains_failure_does_not_stop_the_sweep(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
        directory_engine: None,
    ) -> None:
        """A resolver that raises for one host must not stop the tick from advancing the
        others — the poisoned-step guard."""
        ctx = ctx_for(org, people["manager"], "core.identity.domain.write")
        broken = await _verified_domain(session, ctx, "broken.example")
        healthy = await _verified_domain(session, ctx, "healthy.example")

        class _ExplodingResolver:
            async def resolve(self, host: str, rtype: str) -> list[str]:
                if "broken" in host:
                    raise ConnectionError("simulated resolver outage")
                from app import domain as domain_module

                return [domain_module.verification_txt_value(healthy.verification_token)]

        sweep = DomainRecheckSweep(
            discovery_factory=get_directory_sessionmaker(),
            work_scope=lambda organization_id: session_scope(organization_id=organization_id),
            interval=timedelta(seconds=60),
            batch_size=10,
        )
        # The sweep's own `_recheck_one` does not take a resolver parameter — it calls
        # `services.recheck_domain` with the default resolver. Patch the DNS layer's own
        # default constructor instead, the same seam `test_domains.py`'s HTTP-level verify
        # test uses via `monkeypatch`.
        import app.dns_check as dns_check_module

        original = dns_check_module._default_resolver
        dns_check_module._default_resolver = lambda: _ExplodingResolver()
        try:
            report = await sweep.tick()
        finally:
            dns_check_module._default_resolver = original

        assert report.considered == 2
        assert report.checked == 1
        assert report.failed == 1

        admin_session.expire_all()
        async with admin_session.begin():
            healthy_row = await selectors.get_domain(admin_session, org.id, healthy.id)
            assert healthy_row is not None
            assert healthy_row.last_checked_at is not None
            broken_row = await selectors.get_domain(admin_session, org.id, broken.id)
            assert broken_row is not None
            # Untouched: the exception happened before any write in that domain's own
            # transaction, so nothing about it should have changed.
            assert broken_row.last_checked_at is None
