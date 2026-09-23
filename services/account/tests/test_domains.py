"""Email-domain claim and verification (ADR-0000 §1).

Three layers, mirroring the module split: `app/domain.py`'s pure shape and clock rules need
no fixture at all; `services.claim_domain`/`verify_domain`/`release_domain`/`recheck_domain`
are proved against real PostgreSQL with a stub DNS resolver — no test here ever opens a socket;
and the HTTP surface is proved for authorization and for the one thing that only exists at that
layer, the per-organization rate limit on `POST /domains/{id}/verify`.

**Read `models.py`'s `OrgDomain` docstring and `services.py`'s "domains" section before
extending `TestTakeoverSafety` below.** Exclusivity is verified-only
(`uq_org_domains_domain_verified`, a partial unique index), not global — more than one
organization may hold the same domain pending at once, and the only real collision left is at
`verify_domain`, enforced by the database with no cross-tenant read or write anywhere in this
module.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import delete
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app import dns_check, selectors, services
from app import domain as domain_module
from app.main import app
from app.models import Membership, Organization, OrgDomain
from app.schemas import DomainIn
from platform_core.auth import Scope, get_principal
from platform_core.errors import (
    BusinessRuleProblem,
    ConflictProblem,
    NotFoundProblem,
    ValidationProblem,
)
from tests.helpers import ctx_for as _ctx_for
from tests.helpers import principal_for

pytestmark = pytest.mark.integration


def ctx_for(org, membership, permission, scope=Scope.ORG, *, locations=None):
    return _ctx_for(org.id, membership, permission, scope, locations=locations)


class _AsPrincipal:
    """Override `get_principal` for the duration of one `async with` block — the same seam
    `test_admin_gaps.py` uses for its own negative-authz assertions."""

    def __init__(self, principal) -> None:
        self._principal = principal

    async def __aenter__(self) -> None:
        app.dependency_overrides[get_principal] = lambda: self._principal

    async def __aexit__(self, *exc: object) -> None:
        app.dependency_overrides.pop(get_principal, None)


class StubResolver:
    """A `dns_check.Resolver` that answers from a fixed map instead of the network."""

    def __init__(self, answers: dict[tuple[str, str], list[str]]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, str]] = []

    async def resolve(self, host: str, rtype: str) -> list[str]:
        self.calls.append((host, rtype))
        return self.answers.get((host, rtype), [])


# --------------------------------------------------------------------- app/domain.py — pure


class TestNormaliseDomain:
    def test_lowercases_and_strips_the_trailing_dot(self) -> None:
        assert domain_module.normalise_domain("Acme.COM.") == "acme.com"

    def test_encodes_unicode_to_punycode(self) -> None:
        assert domain_module.normalise_domain("köln.de") == "xn--kln-sna.de"

    def test_rejects_empty(self) -> None:
        with pytest.raises(domain_module.DomainRejected) as excinfo:
            domain_module.normalise_domain("   ")
        assert excinfo.value.code == "domain_required"

    def test_rejects_a_pasted_email_address(self) -> None:
        with pytest.raises(domain_module.DomainRejected) as excinfo:
            domain_module.normalise_domain("person@acme.com")
        assert excinfo.value.code == "domain_invalid"

    def test_rejects_a_single_label(self) -> None:
        """The Public Suffix List's own prevailing rule treats any unlisted single label as a
        suffix in its own right (`*`) — so this is `domain_public_suffix`, not the generic
        shape complaint, and it is the real library saying so, not a special case here."""
        with pytest.raises(domain_module.DomainRejected) as excinfo:
            domain_module.normalise_domain("localhost")
        assert excinfo.value.code == "domain_public_suffix"

    def test_rejects_an_ip_address(self) -> None:
        with pytest.raises(domain_module.DomainRejected) as excinfo:
            domain_module.normalise_domain("10.0.0.1")
        assert excinfo.value.code == "domain_invalid"

    @pytest.mark.parametrize(
        "raw",
        ["opsgrids.com", "OPSGRIDS.COM", "login.opsgrids.com", "acme.stores.opsgrids.com"],
    )
    def test_rejects_platform_domains(self, raw: str) -> None:
        with pytest.raises(domain_module.DomainRejected) as excinfo:
            domain_module.normalise_domain(raw)
        assert excinfo.value.code == "domain_reserved"

    @pytest.mark.parametrize("raw", ["com", "co.uk", "CO.UK", "org"])
    def test_rejects_bare_public_suffixes(self, raw: str) -> None:
        with pytest.raises(domain_module.DomainRejected) as excinfo:
            domain_module.normalise_domain(raw)
        assert excinfo.value.code == "domain_public_suffix"

    def test_a_real_domain_under_a_public_suffix_is_fine(self) -> None:
        assert domain_module.normalise_domain("acme.co.uk") == "acme.co.uk"

    def test_uses_the_real_public_suffix_list_not_a_curated_subset(self) -> None:
        """`github.io` is a real, commonly-hit PSL entry an earlier, hand-curated version of
        this list did not carry — proof this is backed by the actual list
        (`publicsuffixlist`), not a handful of the most common TLDs."""
        with pytest.raises(domain_module.DomainRejected) as excinfo:
            domain_module.normalise_domain("github.io")
        assert excinfo.value.code == "domain_public_suffix"
        # A real domain registered under that same suffix is unaffected.
        assert domain_module.normalise_domain("acme.github.io") == "acme.github.io"

    def test_strips_every_trailing_dot_not_just_one(self) -> None:
        assert domain_module.normalise_domain("acme.com...") == "acme.com"

    def test_a_string_of_nothing_but_dots_is_required_not_invalid(self) -> None:
        with pytest.raises(domain_module.DomainRejected) as excinfo:
            domain_module.normalise_domain("...")
        assert excinfo.value.code == "domain_required"

    def test_a_domain_at_the_236_character_ceiling_is_accepted(self) -> None:
        # 236 = 253 - len("_opsgrids-verify.") — right at the ceiling the TXT host itself can
        # still fit under DNS's 253-octet FQDN limit.
        label = "a" * 59
        domain = ".".join([label, label, label, "a" * 52 + ".com"])
        assert len(domain) == domain_module.MAX_DOMAIN_LENGTH
        assert domain_module.normalise_domain(domain) == domain
        assert len(domain_module.verification_txt_host(domain)) <= 253

    def test_a_domain_one_over_the_236_character_ceiling_is_refused(self) -> None:
        label = "a" * 59
        domain = ".".join([label, label, label, "a" * 53 + ".com"])
        assert len(domain) == domain_module.MAX_DOMAIN_LENGTH + 1
        with pytest.raises(domain_module.DomainRejected) as excinfo:
            domain_module.normalise_domain(domain)
        assert excinfo.value.code == "domain_too_long"

    @pytest.mark.parametrize(
        "raw",
        [
            "acme.local",
            "acme.internal",
            "acme.test",
            "corp.example",
            "acme.invalid",
            "service.onion",
            "printer.home.arpa",
            "reverse.arpa",
        ],
    )
    def test_rejects_rfc_6761_special_use_names(self, raw: str) -> None:
        """None of these can ever resolve on the public internet, so a TXT record
        "published" under one can never mean anything a real resolver would see."""
        with pytest.raises(domain_module.DomainRejected) as excinfo:
            domain_module.normalise_domain(raw)
        assert excinfo.value.code in ("domain_reserved", "domain_public_suffix")


class TestVerificationRecord:
    def test_token_is_hex_and_unique(self) -> None:
        a = domain_module.generate_verification_token()
        b = domain_module.generate_verification_token()
        assert a != b
        int(a, 16)  # does not raise

    def test_txt_host_and_value(self) -> None:
        assert domain_module.verification_txt_host("acme.com") == "_opsgrids-verify.acme.com"
        assert domain_module.verification_txt_value("deadbeef") == "opsgrids-verify=deadbeef"


class TestClaimExpiry:
    def test_not_expired_within_the_ttl(self) -> None:
        claimed = datetime.now(UTC) - timedelta(days=1)
        assert domain_module.is_claim_expired(claimed) is False

    def test_expired_past_the_ttl(self) -> None:
        claimed = datetime.now(UTC) - timedelta(days=15)
        assert domain_module.is_claim_expired(claimed) is True


# --------------------------------------------------------------------- services.py — writes


class TestClaimDomain:
    async def test_claiming_a_new_domain_creates_an_unverified_row(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        async with session.begin():
            row = await services.claim_domain(
                session,
                ctx_for(org, people["manager"], "core.identity.domain.write"),
                DomainIn(domain="Acme.example.com."),
            )
        assert row.domain == "acme.example.com"
        assert row.verified_at is None
        assert row.is_primary is True
        out = selectors.to_domain_out(row)
        assert out.verification_host == "_opsgrids-verify.acme.example.com"
        assert out.verification_value == f"opsgrids-verify={row.verification_token}"
        assert out.expires_at is not None

    async def test_a_second_domain_is_not_primary(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        async with session.begin():
            await services.claim_domain(
                session,
                ctx_for(org, people["manager"], "core.identity.domain.write"),
                DomainIn(domain="first.example.com"),
            )
        async with session.begin():
            second = await services.claim_domain(
                session,
                ctx_for(org, people["manager"], "core.identity.domain.write"),
                DomainIn(domain="second.example.com"),
            )
        assert second.is_primary is False

    async def test_reclaiming_rotates_the_token_and_resets_the_clock(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        write_ctx = ctx_for(org, people["manager"], "core.identity.domain.write")
        payload = DomainIn(domain="acme.example.com")
        async with session.begin():
            first = await services.claim_domain(session, write_ctx, payload)
            first_token, first_id = first.verification_token, first.id
        async with session.begin():
            second = await services.claim_domain(session, write_ctx, payload)
        assert second.id == first_id, "re-claiming must reuse the same row, not create another"
        assert second.verification_token != first_token

    async def test_reclaiming_an_already_verified_domain_is_a_no_op(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        write_ctx = ctx_for(org, people["manager"], "core.identity.domain.write")
        resolver = StubResolver({})
        async with session.begin():
            row = await services.claim_domain(
                session, write_ctx, DomainIn(domain="acme.example.com")
            )
            resolver.answers[(domain_module.verification_txt_host(row.domain), "TXT")] = [
                domain_module.verification_txt_value(row.verification_token)
            ]
        async with session.begin():
            row = await selectors.get_domain(session, org.id, row.id)
            assert row is not None
            await services.verify_domain(session, write_ctx, row, resolver=resolver)
            token_after_verify = row.verification_token

        async with session.begin():
            row = await selectors.get_domain(session, org.id, row.id)
            assert row is not None
            reclaimed = await services.claim_domain(
                session, write_ctx, DomainIn(domain="acme.example.com")
            )
        assert reclaimed.verification_token == token_after_verify
        assert reclaimed.verified_at is not None

    async def test_claiming_normalises_before_storing(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        write_ctx = ctx_for(org, people["manager"], "core.identity.domain.write")
        async with session.begin():
            first = await services.claim_domain(
                session, write_ctx, DomainIn(domain="Acme.Example.com.")
            )
        async with session.begin():
            same = await services.claim_domain(
                session, write_ctx, DomainIn(domain="acme.example.com")
            )
        assert same.id == first.id, (
            "differently-cased/dotted input for the same domain must resolve to the one row"
        )

    async def test_a_platform_domain_is_refused(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        with pytest.raises(ValidationProblem):
            async with session.begin():
                await services.claim_domain(
                    session,
                    ctx_for(org, people["manager"], "core.identity.domain.write"),
                    DomainIn(domain="sso.opsgrids.com"),
                )

    async def test_an_organization_cannot_claim_past_the_cap(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        write_ctx = ctx_for(org, people["manager"], "core.identity.domain.write")
        for i in range(domain_module.MAX_DOMAINS_PER_ORGANIZATION):
            async with session.begin():
                await services.claim_domain(
                    session, write_ctx, DomainIn(domain=f"acme-{i}.example.com")
                )

        with pytest.raises(ValidationProblem):
            async with session.begin():
                await services.claim_domain(
                    session, write_ctx, DomainIn(domain="one-too-many.example.com")
                )

    async def test_the_cap_does_not_count_re_claiming_an_existing_domain(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """Re-claiming a domain this organization already holds must not itself be refused by
        a cap meant to bound how many *distinct* domains an organization accumulates."""
        write_ctx = ctx_for(org, people["manager"], "core.identity.domain.write")
        for i in range(domain_module.MAX_DOMAINS_PER_ORGANIZATION):
            async with session.begin():
                await services.claim_domain(
                    session, write_ctx, DomainIn(domain=f"acme-{i}.example.com")
                )

        async with session.begin():
            reclaimed = await services.claim_domain(
                session, write_ctx, DomainIn(domain="acme-0.example.com")
            )
        assert reclaimed.domain == "acme-0.example.com"


class TestTakeoverSafety:
    """`uq_org_domains_org_domain` (per-organization) and `uq_org_domains_domain_verified`
    (platform-wide, verified rows only) together are the whole enforcement mechanism — see
    `OrgDomain`'s own docstring in `models.py` and `services.py`'s "domains" section. No
    cross-tenant read or write anywhere in this class's own call paths; the database decides."""

    async def _other_org(
        self, admin_session: AsyncSession, *, name: str = "Holder Inc", slug: str = "holder"
    ) -> tuple[Organization, Membership]:
        from app.schemas import OrganizationIn

        async with admin_session.begin():
            other = await services.create_organization(
                admin_session, OrganizationIn(name=name, slug=slug), actor_user_id=None
            )
            owner = await services.add_member(
                admin_session,
                org_id=other.id,
                email=f"owner@{slug}.example.com",
                full_name="Holder Owner",
                actor_user_id=None,
                role_codes=["ORG_OWNER"],
            )
        return other, owner

    async def _claim_as_other_org(
        self, admin_session: AsyncSession, domain: str, *, verified: bool
    ) -> Organization:
        other, owner = await self._other_org(admin_session)
        async with admin_session.begin():
            row = await services.claim_domain(
                admin_session,
                ctx_for(other, owner, "core.identity.domain.write"),
                DomainIn(domain=domain),
            )
            if verified:
                row.verified_at = datetime.now(UTC)
        return other

    async def test_claiming_a_domain_already_verified_elsewhere_still_succeeds(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        """A pending claim proves nothing, so it is never blocked by another organization's
        claim — verified or not. It just cannot ever be verified while that holds (the next
        test)."""
        await self._claim_as_other_org(admin_session, "contested.example.com", verified=True)

        async with session.begin():
            row = await services.claim_domain(
                session,
                ctx_for(org, people["manager"], "core.identity.domain.write"),
                DomainIn(domain="contested.example.com"),
            )
        assert row.domain == "contested.example.com"
        assert row.verified_at is None

    async def test_a_pending_claim_by_one_org_never_blocks_another(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        await self._claim_as_other_org(
            admin_session, "pending-elsewhere.example.com", verified=False
        )

        async with session.begin():
            row = await services.claim_domain(
                session,
                ctx_for(org, people["manager"], "core.identity.domain.write"),
                DomainIn(domain="pending-elsewhere.example.com"),
            )
        assert row.verified_at is None

    async def test_verifying_after_another_org_already_verified_is_a_generic_409(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        """B verifies first; A's own, still-pending claim on the same domain can never be
        verified afterwards — refused by `uq_org_domains_domain_verified`, not by a read that
        would have to name B to explain why."""
        # Captured before the transaction that raises below: it rolls back the shared
        # `session` fixture's outer transaction, which expires every attribute on every ORM
        # object bound to it — including `org` itself — and refreshing an expired attribute
        # needs an `await` this event listener cannot make (`tests/conftest.py::_bind_tenant`
        # runs in a sync callback). Plain values sidestep it entirely.
        org_id = org.id
        holder = await self._claim_as_other_org(
            admin_session, "quiet-conflict.example.com", verified=True
        )

        write_ctx = ctx_for(org, people["manager"], "core.identity.domain.write")
        async with session.begin():
            mine = await services.claim_domain(
                session, write_ctx, DomainIn(domain="quiet-conflict.example.com")
            )
        mine_id = mine.id
        resolver = StubResolver(
            {
                (domain_module.verification_txt_host(mine.domain), "TXT"): [
                    domain_module.verification_txt_value(mine.verification_token)
                ]
            }
        )
        with pytest.raises(ConflictProblem) as excinfo:
            async with session.begin():
                mine = await selectors.get_domain(session, org_id, mine_id)
                assert mine is not None
                await services.verify_domain(session, write_ctx, mine, resolver=resolver)
        assert "Holder" not in str(excinfo.value)
        assert str(holder.id) not in str(excinfo.value)

        async with session.begin():
            still_pending = await selectors.get_domain(session, org_id, mine_id)
            assert still_pending is not None
            assert still_pending.verified_at is None

    async def test_two_organizations_racing_to_verify_the_same_domain_exactly_one_wins(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        """The partial unique index is the arbiter, not application ordering — proved here by
        actually racing two independent sessions, bound to two different organizations, both
        committing an `UPDATE ... SET verified_at` for the same domain concurrently."""
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.config import settings
        from platform_core.db import TENANT_GUC

        other, owner = await self._other_org(admin_session, name="Racer Inc", slug="racer")
        domain = "race.example.com"

        a_ctx = ctx_for(org, people["manager"], "core.identity.domain.write")
        b_ctx = ctx_for(other, owner, "core.identity.domain.write")
        async with session.begin():
            a_row = await services.claim_domain(session, a_ctx, DomainIn(domain=domain))
        async with admin_session.begin():
            b_row = await services.claim_domain(admin_session, b_ctx, DomainIn(domain=domain))

        resolver = StubResolver(
            {
                (domain_module.verification_txt_host(domain), "TXT"): [
                    domain_module.verification_txt_value(a_row.verification_token),
                    domain_module.verification_txt_value(b_row.verification_token),
                ]
            }
        )

        engine = create_async_engine(settings().app_dsn)
        maker = async_sessionmaker(engine, expire_on_commit=False)

        async def _verify_as(organization_id, domain_id, ctx) -> Exception | None:
            try:
                async with maker() as s, s.begin():
                    await s.execute(
                        sa_text(f"SELECT set_config('{TENANT_GUC}', '{organization_id}', true)")
                    )
                    row = await selectors.get_domain(s, organization_id, domain_id)
                    assert row is not None
                    await services.verify_domain(s, ctx, row, resolver=resolver)
            except Exception as exc:
                return exc
            return None

        try:
            results = await asyncio.gather(
                _verify_as(org.id, a_row.id, a_ctx),
                _verify_as(other.id, b_row.id, b_ctx),
            )
        finally:
            await engine.dispose()

        outcomes = list(results)
        winners = [r for r in outcomes if r is None]
        losers = [r for r in outcomes if r is not None]
        assert len(winners) == 1, f"expected exactly one winner, got {outcomes}"
        assert len(losers) == 1
        assert isinstance(losers[0], ConflictProblem)

        async with admin_session.begin():
            verified_rows = (
                await admin_session.execute(
                    sa_text(
                        "SELECT organization_id FROM account.org_domains "
                        "WHERE domain = :d AND verified_at IS NOT NULL"
                    ),
                    {"d": domain},
                )
            ).all()
        assert len(verified_rows) == 1


class TestVerifyDomain:
    async def _claim(self, session, ctx, name: str) -> OrgDomain:
        async with session.begin():
            return await services.claim_domain(session, ctx, DomainIn(domain=name))

    async def test_verify_succeeds_when_the_txt_record_is_published(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.identity.domain.write")
        row = await self._claim(session, ctx, "acme.example.com")
        resolver = StubResolver(
            {
                (domain_module.verification_txt_host(row.domain), "TXT"): [
                    domain_module.verification_txt_value(row.verification_token)
                ]
            }
        )
        async with session.begin():
            row = await selectors.get_domain(session, org.id, row.id)
            assert row is not None
            verified = await services.verify_domain(session, ctx, row, resolver=resolver)
        assert verified.verified_at is not None

    async def test_verify_fails_when_the_txt_record_is_missing(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.identity.domain.write")
        row = await self._claim(session, ctx, "acme.example.com")
        with pytest.raises(BusinessRuleProblem, match="No matching TXT record"):
            async with session.begin():
                row = await selectors.get_domain(session, org.id, row.id)
                assert row is not None
                await services.verify_domain(session, ctx, row, resolver=StubResolver({}))

    async def test_verify_is_idempotent_and_does_not_hit_dns_again(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.identity.domain.write")
        row = await self._claim(session, ctx, "acme.example.com")
        resolver = StubResolver(
            {
                (domain_module.verification_txt_host(row.domain), "TXT"): [
                    domain_module.verification_txt_value(row.verification_token)
                ]
            }
        )
        async with session.begin():
            row = await selectors.get_domain(session, org.id, row.id)
            assert row is not None
            await services.verify_domain(session, ctx, row, resolver=resolver)
        assert len(resolver.calls) == 1

        # A resolver that would fail the check proves the second call never asks DNS at all.
        exploding = StubResolver({})
        async with session.begin():
            row = await selectors.get_domain(session, org.id, row.id)
            assert row is not None
            again = await services.verify_domain(session, ctx, row, resolver=exploding)
        assert again.verified_at is not None
        assert exploding.calls == []

    async def test_verify_refuses_an_expired_claim(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.identity.domain.write")
        row = await self._claim(session, ctx, "acme.example.com")
        async with session.begin():
            row = await selectors.get_domain(session, org.id, row.id)
            assert row is not None
            row.created_at = datetime.now(UTC) - timedelta(days=15)
        with pytest.raises(BusinessRuleProblem, match="expired"):
            async with session.begin():
                row = await selectors.get_domain(session, org.id, row.id)
                assert row is not None
                resolver = StubResolver(
                    {
                        (domain_module.verification_txt_host(row.domain), "TXT"): [
                            domain_module.verification_txt_value(row.verification_token)
                        ]
                    }
                )
                await services.verify_domain(session, ctx, row, resolver=resolver)

    async def test_verify_refuses_when_a_concurrent_reclaim_rotated_the_token(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        """The conditional `UPDATE`'s own `verification_token` guard (MINOR5): a DNS answer
        checked against the token this `row` was read with must not silently verify a *new*
        token a concurrent re-claim already rotated in, even though the domain and the
        organization are both still exactly right."""
        ctx = ctx_for(org, people["manager"], "core.identity.domain.write")
        row = await self._claim(session, ctx, "acme.example.com")
        stale_token = row.verification_token
        # Captured before the transaction that raises below rolls back the shared `session`
        # fixture and expires every attribute on every ORM object bound to it, `org` included
        # — see `test_verifying_after_another_org_already_verified_is_a_generic_409`'s own
        # comment for why a plain value sidesteps it.
        org_id, domain_id = org.id, row.id

        # A concurrent re-claim, for real, on a separate connection — rotates the token in
        # the database while this test's own `row` object still remembers the old one.
        async with admin_session.begin():
            await services.claim_domain(
                session=admin_session, ctx=ctx, payload=DomainIn(domain="acme.example.com")
            )

        resolver = StubResolver(
            {
                (domain_module.verification_txt_host(row.domain), "TXT"): [
                    domain_module.verification_txt_value(stale_token)
                ]
            }
        )
        with pytest.raises(ConflictProblem):
            async with session.begin():
                await services.verify_domain(session, ctx, row, resolver=resolver)

        admin_session.expire_all()
        async with admin_session.begin():
            fresh = await selectors.get_domain(admin_session, org_id, domain_id)
            assert fresh is not None
            assert fresh.verified_at is None
            assert fresh.verification_token != stale_token

    async def test_verify_answers_not_found_when_the_domain_is_gone(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.identity.domain.write")
        row = await self._claim(session, ctx, "acme.example.com")
        resolver = StubResolver(
            {
                (domain_module.verification_txt_host(row.domain), "TXT"): [
                    domain_module.verification_txt_value(row.verification_token)
                ]
            }
        )
        async with admin_session.begin():
            await admin_session.execute(delete(OrgDomain).where(OrgDomain.id == row.id))

        with pytest.raises(NotFoundProblem):
            async with session.begin():
                await services.verify_domain(session, ctx, row, resolver=resolver)


class TestReleaseDomain:
    async def test_release_deletes_the_row(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        ctx = ctx_for(org, people["manager"], "core.identity.domain.write")
        async with session.begin():
            row = await services.claim_domain(session, ctx, DomainIn(domain="acme.example.com"))
            domain_id = row.id
        async with session.begin():
            row = await selectors.get_domain(session, org.id, domain_id)
            assert row is not None
            await services.release_domain(session, ctx, row)
        async with session.begin():
            assert await selectors.get_domain(session, org.id, domain_id) is None


# --------------------------------------------------------------------- HTTP surface


class TestDomainRoutesAuthz:
    async def test_list_requires_the_read_permission(
        self, client: AsyncClient, org: Organization, people: dict[str, Membership]
    ) -> None:
        principal = principal_for(org.id, people["manager"], [])
        async with _AsPrincipal(principal):
            response = await client.get("/api/v1/domains")
        assert response.status_code == 403

    async def test_claim_requires_the_write_permission(
        self, client: AsyncClient, org: Organization, people: dict[str, Membership]
    ) -> None:
        principal = principal_for(org.id, people["manager"], ["core.identity.domain.read:ORG"])
        async with _AsPrincipal(principal):
            response = await client.post("/api/v1/domains", json={"domain": "acme.example.com"})
        assert response.status_code == 403

    async def test_verify_requires_the_write_permission(
        self,
        client: AsyncClient,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        write_ctx = ctx_for(org, people["manager"], "core.identity.domain.write")
        async with session.begin():
            row = await services.claim_domain(
                session, write_ctx, DomainIn(domain="acme.example.com")
            )
            domain_id = row.id

        principal = principal_for(org.id, people["manager"], ["core.identity.domain.read:ORG"])
        async with _AsPrincipal(principal):
            response = await client.post(f"/api/v1/domains/{domain_id}/verify")
        assert response.status_code == 403

    async def test_release_requires_the_write_permission(
        self,
        client: AsyncClient,
        session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        write_ctx = ctx_for(org, people["manager"], "core.identity.domain.write")
        async with session.begin():
            row = await services.claim_domain(
                session, write_ctx, DomainIn(domain="acme.example.com")
            )
            domain_id = row.id

        principal = principal_for(org.id, people["manager"], [])
        async with _AsPrincipal(principal):
            response = await client.delete(f"/api/v1/domains/{domain_id}")
        assert response.status_code == 403

    async def test_a_member_permission_at_own_scope_is_refused(
        self, client: AsyncClient, org: Organization, people: dict[str, Membership]
    ) -> None:
        """`core.identity.domain.write` carries a `minimum=Scope.ORG` floor — there is no
        "own domain" reading, so every scope narrower than ORG is refused."""
        principal = principal_for(org.id, people["report"], ["core.identity.domain.write:OWN"])
        async with _AsPrincipal(principal):
            response = await client.post("/api/v1/domains", json={"domain": "acme.example.com"})
        assert response.status_code == 403


class TestDomainRoutesHTTP:
    async def _writer(self, org: Organization, people: dict[str, Membership]):
        return principal_for(
            org.id,
            people["manager"],
            ["core.identity.domain.write:ORG", "core.identity.domain.read:ORG"],
        )

    async def test_claim_list_and_read(
        self, client: AsyncClient, org: Organization, people: dict[str, Membership]
    ) -> None:
        async with _AsPrincipal(await self._writer(org, people)):
            created = await client.post("/api/v1/domains", json={"domain": "Acme.Example.com."})
            assert created.status_code == 201, created.text
            body = created.json()
            assert body["domain"] == "acme.example.com"
            assert body["verified_at"] is None
            assert body["verification_host"] == "_opsgrids-verify.acme.example.com"

            listed = await client.get("/api/v1/domains")
            assert listed.status_code == 200
            assert any(d["id"] == body["id"] for d in listed.json())

            fetched = await client.get(f"/api/v1/domains/{body['id']}")
            assert fetched.status_code == 200
            assert fetched.json()["domain"] == "acme.example.com"

    async def test_claim_rejects_unknown_fields(
        self, client: AsyncClient, org: Organization, people: dict[str, Membership]
    ) -> None:
        async with _AsPrincipal(await self._writer(org, people)):
            response = await client.post(
                "/api/v1/domains", json={"domain": "acme.example.com", "auto_join": True}
            )
        assert response.status_code == 422

    async def test_claim_rejects_a_public_suffix(
        self, client: AsyncClient, org: Organization, people: dict[str, Membership]
    ) -> None:
        async with _AsPrincipal(await self._writer(org, people)):
            response = await client.post("/api/v1/domains", json={"domain": "com"})
        assert response.status_code == 422
        assert response.json()["errors"][0]["code"] == "domain_public_suffix"

    async def test_reading_a_missing_domain_is_404(
        self, client: AsyncClient, org: Organization, people: dict[str, Membership]
    ) -> None:
        async with _AsPrincipal(await self._writer(org, people)):
            response = await client.get(f"/api/v1/domains/{uuid.uuid4()}")
        assert response.status_code == 404

    async def test_verify_over_http_with_a_stubbed_resolver(
        self,
        client: AsyncClient,
        org: Organization,
        people: dict[str, Membership],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        writer = await self._writer(org, people)
        async with _AsPrincipal(writer):
            created = await client.post("/api/v1/domains", json={"domain": "acme.example.com"})
        body = created.json()

        resolver = StubResolver(
            {
                (body["verification_host"], "TXT"): [body["verification_value"]],
            }
        )
        monkeypatch.setattr(dns_check, "_default_resolver", lambda: resolver)

        async with _AsPrincipal(writer):
            response = await client.post(f"/api/v1/domains/{body['id']}/verify")
        assert response.status_code == 200, response.text
        assert response.json()["verified_at"] is not None

    async def test_verify_is_rate_limited_per_organization(
        self,
        client: AsyncClient,
        org: Organization,
        people: dict[str, Membership],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        writer = await self._writer(org, people)
        async with _AsPrincipal(writer):
            created = await client.post("/api/v1/domains", json={"domain": "acme.example.com"})
        domain_id = created.json()["id"]

        monkeypatch.setattr(dns_check, "_default_resolver", lambda: StubResolver({}))

        async with _AsPrincipal(writer):
            first = await client.post(f"/api/v1/domains/{domain_id}/verify")
            second = await client.post(f"/api/v1/domains/{domain_id}/verify")

        # The first attempt fails DNS (nothing published) but still spends the window.
        assert first.status_code == 422
        assert second.status_code == 429

    async def test_release_over_http_then_internal_lookup_finds_nothing(
        self,
        client: AsyncClient,
        directory_session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        from app.config import settings
        from platform_core.internal_auth import mint_service_token

        writer = await self._writer(org, people)
        async with _AsPrincipal(writer):
            created = await client.post("/api/v1/domains", json={"domain": "acme.example.com"})
            domain_id = created.json()["id"]

            deleted = await client.delete(f"/api/v1/domains/{domain_id}")
        assert deleted.status_code == 204

        internal_headers = {
            "X-Internal-Token": mint_service_token(settings(), audience="account", caller="tests")
        }
        looked_up = await client.get(
            "/internal/v1/domains/acme.example.com", headers=internal_headers
        )
        assert looked_up.status_code == 404
