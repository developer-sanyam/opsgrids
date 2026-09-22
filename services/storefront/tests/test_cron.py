"""The custom-domain sweep's own loop (`app/cron.py`, ADR-0012 §6): discovery finding only the
organizations with a claimed domain, a due claim being advanced, an expired unverified claim
being released, one organization's failure not stopping the rest, and every actual state
transition being audited with the sweep's system actor and clearing the host cache.

`tests/test_domain.py` covers the state machine's own decisions in detail (`advance_verified_or_
later`, the backoff, the audit-action mapping); `tests/test_probes.py` covers the real DNS/TLS
probes; `tests/test_store_domain.py` covers the routes that drive the same `advance_domain_claim`
core synchronously. This module covers the loop that discovers work and drives it unattended —
the one thing none of those three can, because a `client`-fixture test binds to one organization
for the whole test and this sweep's whole job is to cross that boundary safely.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

import pytest
import pytest_asyncio
from sqlalchemy import select

from app import services
from app.config import settings
from app.cron import DomainSweep
from app.models import AuditLog, CustomDomainClaim, Storefront
from app.probes import ProbeResult, ProbeSet
from app.selectors import host_cache_key
from platform_core.cache import cache_get, cache_set, close_redis, init_redis
from platform_core.db import get_directory_sessionmaker, session_scope
from tests.helpers import seed_custom_domain_claim, seed_store

pytestmark = pytest.mark.integration


@dataclass
class _StaticProbe:
    result: ProbeResult

    async def check(self, host: str, **kwargs: object) -> ProbeResult:
        return self.result


_OK = ProbeResult(ok=True, detail="ok")


def _probes(
    *,
    ownership: ProbeResult = _OK,
    routing: ProbeResult = _OK,
    certificate: ProbeResult = _OK,
) -> ProbeSet:
    return ProbeSet(
        ownership=_StaticProbe(ownership),
        routing=_StaticProbe(routing),
        certificate=_StaticProbe(certificate),
    )


@pytest_asyncio.fixture
async def sweep(engines: None, session) -> DomainSweep:
    """The real loop, on the real BYPASSRLS directory reader and the real app role — the same
    two engines `engines` (conftest) initialises for the anonymous HTTP path, reused here
    because discovery is the same role and the same table (ADR-0012 §6's "no new grant").

    Depends on `session` **only** for its side effect: that fixture is what truncates every
    tenant table between tests (`tests/conftest.py`), and this module never opens one of its
    own — every write here goes through `admin_session` (cross-tenant seeding and reading back)
    or the sweep's own `session_scope` (production's own write path).
    """
    init_redis(settings().redis_url)
    try:
        yield DomainSweep(
            discovery_factory=get_directory_sessionmaker(),
            work_scope=lambda organization_id: session_scope(organization_id=organization_id),
            probes=_probes(),
        )
    finally:
        await close_redis()


async def _audit_rows(admin_session, org_id: uuid.UUID) -> list[AuditLog]:
    rows = (
        await admin_session.execute(
            select(AuditLog).where(AuditLog.organization_id == org_id).order_by(AuditLog.at.asc())
        )
    ).scalars()
    return list(rows)


class TestDiscoveryOnlyConsidersClaimedDomains:
    async def test_a_store_with_no_custom_domain_is_never_discovered(
        self, admin_session, sweep: DomainSweep, org_id: uuid.UUID, slug: str
    ) -> None:
        await seed_store(admin_session, org_id, slug)  # no custom_domain at all

        report = await sweep.tick()

        assert report.considered == 0

    async def test_a_due_claim_is_discovered_and_considered(
        self, admin_session, sweep: DomainSweep, org_id: uuid.UUID, slug: str
    ) -> None:
        store = await seed_store(admin_session, org_id, slug, custom_domain="merchant.example")
        await seed_custom_domain_claim(admin_session, org_id, store.id)

        report = await sweep.tick()

        assert report.considered == 1


class TestTheStorePutSeam:
    async def test_a_domain_claimed_through_the_older_route_with_no_claim_row_is_skipped(
        self, admin_session, sweep: DomainSweep, org_id: uuid.UUID, slug: str
    ) -> None:
        """`storefronts.custom_domain` can be set with no `custom_domain_claims` row — the
        `StorePut` seam `app/services.py` documents. The sweep must not crash on it; it has
        nothing to probe until the merchant reaches the dedicated route."""
        await seed_store(admin_session, org_id, slug, custom_domain="merchant.example")

        report = await sweep.tick()

        assert report.considered == 1
        assert report.skipped == 1
        assert report.failed == 0


class TestADueClaimIsAdvanced:
    async def test_a_successful_ownership_check_promotes_to_verified_and_is_audited(
        self, admin_session, sweep: DomainSweep, org_id: uuid.UUID, slug: str
    ) -> None:
        store = await seed_store(admin_session, org_id, slug, custom_domain="merchant.example")
        store_id = store.id
        await seed_custom_domain_claim(admin_session, org_id, store.id)
        await cache_set(host_cache_key("merchant.example"), "stale-entry", ttl=300)

        report = await sweep.tick()

        assert report.transitioned == 1
        await admin_session.rollback()
        admin_session.expire_all()
        refreshed = (
            await admin_session.execute(select(Storefront).where(Storefront.id == store_id))
        ).scalar_one()
        assert refreshed.custom_domain_status == "verified"

        rows = await _audit_rows(admin_session, org_id)
        transition = next(r for r in rows if r.action == "storefront.domain.verified")
        assert transition.actor_id is None
        assert transition.acting_as == "storefront-domain-sweep"

        # The transition must have cleared the stale cache entry (ADR-0012 §2, §6).
        assert await cache_get(host_cache_key("merchant.example")) is None

    async def test_a_claim_not_yet_due_is_left_alone(
        self, admin_session, sweep: DomainSweep, org_id: uuid.UUID, slug: str
    ) -> None:
        store = await seed_store(admin_session, org_id, slug, custom_domain="merchant.example")
        await seed_custom_domain_claim(
            admin_session,
            org_id,
            store.id,
            next_check_at=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        )

        report = await sweep.tick()

        assert report.considered == 1
        assert report.skipped == 1
        assert report.transitioned == 0

    async def test_no_probe_result_writes_no_audit_row(
        self, admin_session, sweep: DomainSweep, org_id: uuid.UUID, slug: str
    ) -> None:
        """A tick that finds nothing changed writes nothing (ADR-0012 §15) — a claim already
        `live` that stays `live` must not accumulate an audit row every tick."""
        store = await seed_store(
            admin_session,
            org_id,
            slug,
            custom_domain="merchant.example",
            custom_domain_status="live",
        )
        await seed_custom_domain_claim(
            admin_session,
            org_id,
            store.id,
            ownership_state="ok",
            routing_state="ok",
            certificate_state="ok",
            verified_at=dt.datetime.now(dt.UTC),
            live_since=dt.datetime.now(dt.UTC),
        )

        report = await sweep.tick()

        assert report.checked == 1
        assert report.transitioned == 0
        assert await _audit_rows(admin_session, org_id) == []


class TestAnExpiredUnverifiedClaimIsReleased:
    async def test_expiry_clears_the_domain_and_deletes_the_claim(
        self, admin_session, sweep: DomainSweep, org_id: uuid.UUID, slug: str
    ) -> None:
        store = await seed_store(admin_session, org_id, slug, custom_domain="merchant.example")
        store_id = store.id
        now = dt.datetime.now(dt.UTC)
        await seed_custom_domain_claim(
            admin_session,
            org_id,
            store.id,
            token_issued_at=now - dt.timedelta(days=15),
            claim_expires_at=now - dt.timedelta(days=1),
            next_check_at=now - dt.timedelta(hours=1),
        )
        await cache_set(host_cache_key("merchant.example"), "stale-entry", ttl=300)

        report = await sweep.tick()

        assert report.released == 1
        await admin_session.rollback()
        admin_session.expire_all()
        refreshed = (
            await admin_session.execute(select(Storefront).where(Storefront.id == store_id))
        ).scalar_one()
        assert refreshed.custom_domain is None
        assert refreshed.custom_domain_status is None

        remaining = (
            (
                await admin_session.execute(
                    select(CustomDomainClaim).where(CustomDomainClaim.organization_id == org_id)
                )
            )
            .scalars()
            .all()
        )
        assert remaining == []

        rows = await _audit_rows(admin_session, org_id)
        released = next(r for r in rows if r.action == "storefront.domain.released")
        assert released.actor_id is None
        assert released.acting_as == "storefront-domain-sweep"

        assert await cache_get(host_cache_key("merchant.example")) is None

    async def test_a_verified_claim_past_its_old_expiry_timestamp_is_not_released(
        self, admin_session, sweep: DomainSweep, org_id: uuid.UUID, slug: str
    ) -> None:
        """`claim_expires_at` only governs a claim still `unverified` (ADR-0012 §9) — a verified
        or later claim keeps its original expiry timestamp on the row, and it must not matter."""
        store = await seed_store(
            admin_session,
            org_id,
            slug,
            custom_domain="merchant.example",
            custom_domain_status="verified",
        )
        store_id = store.id
        now = dt.datetime.now(dt.UTC)
        await seed_custom_domain_claim(
            admin_session,
            org_id,
            store.id,
            token_issued_at=now - dt.timedelta(days=20),
            claim_expires_at=now - dt.timedelta(days=6),
            next_check_at=now - dt.timedelta(hours=1),
            ownership_state="ok",
            verified_at=now - dt.timedelta(days=20),
        )

        report = await sweep.tick()

        assert report.released == 0
        await admin_session.rollback()
        admin_session.expire_all()
        refreshed = (
            await admin_session.execute(select(Storefront).where(Storefront.id == store_id))
        ).scalar_one()
        assert refreshed.custom_domain == "merchant.example"


class TestOneOrganizationsFailureDoesNotStopTheRest:
    async def test_a_failing_organization_is_counted_and_the_other_still_advances(
        self,
        admin_session,
        sweep: DomainSweep,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
        slug: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`app/cron.py`'s `_advance_one` wraps the whole per-organization advance in its own
        `try`/`except` — the same guard `services/approval/app/cron.py`'s `_escalate` states the
        reasoning for: one organization's exception must not abandon every candidate discovered
        alongside it (ADR-0012 §6's "a check that cannot run never takes a domain off the air")."""
        broken_store = await seed_store(
            admin_session, org_id, slug, custom_domain="merchant.example"
        )
        broken_store_id = broken_store.id
        await seed_custom_domain_claim(admin_session, org_id, broken_store.id)

        other_slug = f"t{uuid.uuid4().hex[:16]}"
        healthy_store = await seed_store(
            admin_session, other_org, other_slug, custom_domain="other.example"
        )
        healthy_store_id = healthy_store.id
        await seed_custom_domain_claim(admin_session, other_org, healthy_store.id)

        real_advance = services.advance_domain_claim

        async def _raise_for_org(store, claim, *, probes, now):
            if store.organization_id == org_id:
                raise RuntimeError("simulated probe failure")
            return await real_advance(store, claim, probes=probes, now=now)

        monkeypatch.setattr(services, "advance_domain_claim", _raise_for_org)

        report = await sweep.tick()

        assert report.considered == 2
        assert report.failed == 1
        assert report.transitioned == 1

        await admin_session.rollback()
        admin_session.expire_all()
        refreshed = (
            await admin_session.execute(select(Storefront).where(Storefront.id == healthy_store_id))
        ).scalar_one()
        assert refreshed.custom_domain_status == "verified"

        # The failing organization's own row is untouched, not partially written — the raise
        # happened inside the tenant-bound transaction (`_work_scope`), which rolled back.
        broken = (
            await admin_session.execute(select(Storefront).where(Storefront.id == broken_store_id))
        ).scalar_one()
        assert broken.custom_domain_status == "unverified"
