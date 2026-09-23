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
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import dns_check, selectors, services
from app import domain as domain_module
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
from platform_core.internal_auth import mint_service_token
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


class _ResolverErrorResolver:
    """A `dns_check.Resolver` that always fails to get an answer at all — never a confirmed
    absence. Round 3: this must never be mistaken for a failed check by `recheck_domain`."""

    async def resolve(self, host: str, rtype: str) -> list[str]:
        raise dns_check.DnsLookupError("simulated: our own resolver could not be reached")


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
        row = await _verified_domain(session, ctx, "acme.example.com")
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
        row = await _verified_domain(session, ctx, "acme.example.com")
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
        row = await _verified_domain(session, ctx, "acme.example.com")
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

    async def test_a_resolver_error_moves_only_last_checked_at(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """Round 3, MINOR: our own resolver failing to get an answer at all is not a fact
        about the customer's domain — `consecutive_failures` and `failing_since` must be
        untouched, and `last_check_ok` must keep whatever it last genuinely was, not become
        `False` for a check that never actually happened."""
        ctx = ctx_for(org, people["manager"], "core.identity.domain.write")
        row = await _verified_domain(session, ctx, "acme.example.com")
        domain_id = row.id
        before_at = row.last_checked_at

        async with session.begin():
            fresh = await selectors.get_domain(session, org.id, domain_id)
            assert fresh is not None
            checked = await services.recheck_domain(
                session, fresh, resolver=_ResolverErrorResolver()
            )

        assert checked.last_checked_at is not None
        assert checked.last_checked_at != before_at
        assert checked.last_check_ok is None  # never checked yet, still true — no answer came
        assert checked.consecutive_failures == 0
        assert checked.failing_since is None
        assert checked.verified_at is not None

    async def test_a_resolver_error_does_not_disturb_an_existing_failing_streak(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """A domain already failing for a real reason must not have its streak reset, nor
        pushed forward, by a tick where the outage was on our own side."""
        ctx = ctx_for(org, people["manager"], "core.identity.domain.write")
        row = await _verified_domain(session, ctx, "acme.example.com")
        domain_id = row.id
        start = datetime.now(UTC) - timedelta(days=1)

        async with session.begin():
            fresh = await selectors.get_domain(session, org.id, domain_id)
            assert fresh is not None
            failing = await services.recheck_domain(
                session, fresh, resolver=StubResolver({}), now=start
            )
        assert failing.consecutive_failures == 1
        assert failing.failing_since == start

        async with session.begin():
            fresh = await selectors.get_domain(session, org.id, domain_id)
            assert fresh is not None
            after_error = await services.recheck_domain(
                session, fresh, resolver=_ResolverErrorResolver(), now=start + timedelta(hours=1)
            )
        assert after_error.consecutive_failures == 1  # unchanged, not incremented
        assert after_error.failing_since == start  # unchanged, not reset
        assert after_error.last_checked_at == start + timedelta(hours=1)  # still moved


class TestDemotion:
    """MAJOR2: a sustained run of failures — long in wall-clock time *and* repeated on
    distinct checks — clears `verified_at` on its own. See `app.domain.should_demote`'s own
    docstring for why both conditions are required."""

    async def test_a_long_failing_streak_with_enough_checks_demotes(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.identity.domain.write")
        row = await _verified_domain(session, ctx, "acme.example.com")
        domain_id = row.id

        start = datetime.now(UTC) - timedelta(days=20)
        for i in range(domain_module.DEMOTION_MIN_CONSECUTIVE_FAILURES):
            async with session.begin():
                fresh = await selectors.get_domain(session, org.id, domain_id)
                assert fresh is not None
                checked = await services.recheck_domain(
                    session, fresh, resolver=StubResolver({}), now=start + timedelta(hours=i)
                )
        assert checked.verified_at is not None, "not demoted yet — the streak is not old enough"

        # The streak is now both long enough (well past 14 days) and has enough distinct
        # failures. One more failing check should demote.
        async with session.begin():
            fresh = await selectors.get_domain(session, org.id, domain_id)
            assert fresh is not None
            demoted = await services.recheck_domain(
                session,
                fresh,
                resolver=StubResolver({}),
                now=datetime.now(UTC),
            )
        assert demoted.verified_at is None
        assert demoted.demoted_at is not None

        async with admin_session.begin():
            demotion_rows = (
                (
                    await admin_session.execute(
                        select(AuditLog).where(
                            AuditLog.organization_id == org.id,
                            AuditLog.action == "domain.demoted",
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(demotion_rows) == 1

    async def test_a_long_streak_with_too_few_distinct_checks_does_not_demote(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """Old in wall-clock time is not enough on its own — a domain checked only rarely,
        failing each time, must not demote on elapsed time alone before enough distinct
        checks have actually seen it fail."""
        ctx = ctx_for(org, people["manager"], "core.identity.domain.write")
        row = await _verified_domain(session, ctx, "acme.example.com")
        domain_id = row.id
        long_ago = datetime.now(UTC) - timedelta(days=30)

        # The first failure, a long time ago — sets `failing_since`.
        async with session.begin():
            fresh = await selectors.get_domain(session, org.id, domain_id)
            assert fresh is not None
            await services.recheck_domain(session, fresh, resolver=StubResolver({}), now=long_ago)

        # A second failure, "now" — `failing_since` is 30 days old (well past the 14-day
        # bar), but this is only the *second* distinct failed check.
        async with session.begin():
            fresh = await selectors.get_domain(session, org.id, domain_id)
            assert fresh is not None
            checked = await services.recheck_domain(
                session, fresh, resolver=StubResolver({}), now=datetime.now(UTC)
            )
        assert checked.consecutive_failures < domain_module.DEMOTION_MIN_CONSECUTIVE_FAILURES
        assert checked.verified_at is not None

    async def test_frequent_failures_over_too_short_a_span_do_not_demote(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """Many failures is not enough on its own either — an incident that fails every check
        for an hour must not demote a domain by lunchtime on failure count alone."""
        ctx = ctx_for(org, people["manager"], "core.identity.domain.write")
        row = await _verified_domain(session, ctx, "acme.example.com")
        domain_id = row.id
        start = datetime.now(UTC)

        for i in range(domain_module.DEMOTION_MIN_CONSECUTIVE_FAILURES + 5):
            async with session.begin():
                fresh = await selectors.get_domain(session, org.id, domain_id)
                assert fresh is not None
                checked = await services.recheck_domain(
                    session, fresh, resolver=StubResolver({}), now=start + timedelta(minutes=i)
                )
        assert checked.consecutive_failures >= domain_module.DEMOTION_MIN_CONSECUTIVE_FAILURES
        assert checked.verified_at is not None, (
            "too recent a streak to demote, however many failures"
        )

    async def test_our_own_resolver_being_down_never_demotes_a_domain(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """Round 3, MINOR: the same shape as `test_a_long_failing_streak_with_enough_checks_
        demotes` above — enough distinct checks, spanning well over the 14-day bar — except
        every one of them is a resolver error, never a confirmed absence. `should_demote` must
        never even be asked, because `consecutive_failures` and `failing_since` never move."""
        ctx = ctx_for(org, people["manager"], "core.identity.domain.write")
        row = await _verified_domain(session, ctx, "acme.example.com")
        domain_id = row.id
        start = datetime.now(UTC) - timedelta(days=20)

        for i in range(domain_module.DEMOTION_MIN_CONSECUTIVE_FAILURES + 5):
            async with session.begin():
                fresh = await selectors.get_domain(session, org.id, domain_id)
                assert fresh is not None
                checked = await services.recheck_domain(
                    session,
                    fresh,
                    resolver=_ResolverErrorResolver(),
                    now=start + timedelta(hours=i),
                )
        async with session.begin():
            fresh = await selectors.get_domain(session, org.id, domain_id)
            assert fresh is not None
            checked = await services.recheck_domain(
                session, fresh, resolver=_ResolverErrorResolver(), now=datetime.now(UTC)
            )

        assert checked.consecutive_failures == 0
        assert checked.failing_since is None
        assert checked.verified_at is not None
        assert checked.demoted_at is None

    async def test_a_demoted_domain_is_no_longer_returned_by_the_internal_sso_lookup(
        self,
        client: AsyncClient,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        """The whole point: `api/internal.py`'s `verified_at IS NOT NULL` filter means a
        demoted domain stops routing sign-in the instant this commits."""
        ctx = ctx_for(org, people["manager"], "core.identity.domain.write")
        row = await _verified_domain(session, ctx, "acme.example.com")
        domain_id = row.id
        start = datetime.now(UTC) - timedelta(days=20)

        # Enough distinct failures, shortly after `start`, to clear the count bar...
        for i in range(domain_module.DEMOTION_MIN_CONSECUTIVE_FAILURES - 1):
            async with session.begin():
                fresh = await selectors.get_domain(session, org.id, domain_id)
                assert fresh is not None
                await services.recheck_domain(
                    session, fresh, resolver=StubResolver({}), now=start + timedelta(hours=i)
                )
        # ...and one more, now — 20 days after the first, clearing the elapsed-time bar too.
        async with session.begin():
            fresh = await selectors.get_domain(session, org.id, domain_id)
            assert fresh is not None
            await services.recheck_domain(
                session, fresh, resolver=StubResolver({}), now=datetime.now(UTC)
            )

        internal_headers = {
            "X-Internal-Token": mint_service_token(settings(), audience="account", caller="tests")
        }
        looked_up = await client.get(
            "/internal/v1/domains/acme.example.com", headers=internal_headers
        )
        assert looked_up.status_code == 404


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
        verified = await _verified_domain(session, ctx, "verified.example.com")
        async with session.begin():
            await services.claim_domain(session, ctx, DomainIn(domain="pending.example.com"))
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
        row = await _verified_domain(session, ctx, "verified.example.com")

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
        broken = await _verified_domain(session, ctx, "broken.example.com")
        healthy = await _verified_domain(session, ctx, "healthy.example.com")

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
            # The domain whose own check raised is *not* left untouched: `_record_check_error`
            # bumps `last_checked_at`/`consecutive_failures` in a fresh transaction precisely
            # so this domain rotates out of "due" instead of being retried, and failing, every
            # single tick forever. Its own DNS-answer-driven fields (`failing_since`, an audit
            # row, an event) are untouched — this path never had a real answer to record one
            # from.
            assert broken_row.last_checked_at is not None
            assert broken_row.last_check_ok is False
            assert broken_row.consecutive_failures == 1
            assert broken_row.failing_since is None
            assert broken_row.verified_at is not None
