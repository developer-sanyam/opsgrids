"""Single sign-on: OIDC authorization code with PKCE.

Four properties are what this file exists to hold, and each has a specific failure in mind.

**A domain only ever selects a provider, and only a verified one** (ADR-0000 §1). The
failure this prevents is somebody adding a domain row and thereby routing another
organization's sign-ins through an identity provider they control.

**A response has to belong to a request we started.** State is single-use, PKCE binds the
code to this service, and the nonce binds the ID token to this login. Each is tested for
replay separately, because each covers a capture the others do not.

**An external issuer's word is checked, not taken.** Signature, issuer, audience, expiry,
nonce and a *verified* address — with a fake provider that signs real tokens using a key
generated for this run, so the check under test is PyJWT's and not a mock's.

**A federated sign-in produces an ordinary token.** Same `issue_for`, same claim shape as a
password login, differing only in `amr`. A second minting path is the thing that eventually
mints a token nobody validated.
"""

from __future__ import annotations

import base64
import hashlib
import time
import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import oidc, services
from app.config import Settings
from app.deps import get_idp
from app.directory import DirectoryDomain, DirectoryUser, HttpDirectory
from app.keys import KeyPair
from app.main import app
from app.models import (
    AuditLog,
    ExternalIdentity,
    OrgIdpConfig,
    OutboxEvent,
    RefreshToken,
    Session,
)
from platform_core import pii
from platform_core.auth import Principal, get_principal
from platform_core.db import set_tenant
from platform_core.errors import (
    BusinessRuleProblem,
    ForbiddenProblem,
    UnauthorizedProblem,
    UpstreamUnavailableProblem,
)
from tests.conftest import (
    MEMBERSHIP_A,
    ORG_A,
    ORG_B,
    PASSWORD,
    USER,
    FakeDirectory,
    FakeEntitlements,
    FakeRedis,
    membership,
    user_in_one_org,
    user_in_two_orgs,
)
from tests.fake_idp import (
    AUTHORIZE,
    CLIENT_ID,
    CLIENT_SECRET,
    ISSUER,
    JWKS_URI,
    TOKEN,
    FakeIdp,
)

pytestmark = pytest.mark.integration

EMAIL = "sam@example.com"
VERIFIED_DOMAIN = "example.com"
UNVERIFIED_DOMAIN = "notproven.test"
OTHER_ORG_DOMAIN = "beta.test"
STRANGER = "nobody@example.com"


def _domain(
    domain: str, org: uuid.UUID, slug: str, *, verified: bool = True, status: str = "active"
) -> DirectoryDomain:
    return DirectoryDomain(
        domain=domain,
        organization_id=org,
        organization_slug=slug,
        organization_status=status,
        verified=verified,
    )


@pytest_asyncio.fixture
async def directory() -> FakeDirectory:
    """Overrides the shared fixture to add the domains account-service would answer with.

    `notproven.test` is claimed by the same organization and never verified — the single most
    important row here, because the refusal it triggers is the one ADR-0000 §1 mandates.
    """
    return FakeDirectory(
        [user_in_one_org()],
        [
            _domain(VERIFIED_DOMAIN, ORG_A, "acme"),
            _domain(UNVERIFIED_DOMAIN, ORG_A, "acme", verified=False),
            _domain(OTHER_ORG_DOMAIN, ORG_B, "beta"),
            _domain("suspended.test", ORG_B, "beta", status="suspended"),
        ],
    )


@pytest_asyncio.fixture
async def idp() -> FakeIdp:
    return FakeIdp()


@pytest_asyncio.fixture
async def store(redis: FakeRedis) -> oidc.StateStore:
    return oidc.StateStore(redis)


async def _install_config(
    session: AsyncSession,
    cfg: Settings,
    *,
    organization_id: uuid.UUID = ORG_A,
    enabled: bool = True,
    allowed_domains: list[str] | None = None,
    issuer: str = ISSUER,
) -> OrgIdpConfig:
    """Write an IdP configuration the way an administrator would, tenant bound.

    Bound before the INSERT on purpose: `org_idp_configs` carries a FORCEd policy with a
    WITH CHECK, so an unbound insert is refused. The fixture proving that is the same
    mechanism the flow relies on.
    """
    await set_tenant(session, organization_id)
    sealed, _ = pii.seal(CLIENT_SECRET, kind="generic", settings=cfg)
    row = OrgIdpConfig(
        organization_id=organization_id,
        protocol="oidc",
        display_name="Example IdP",
        enabled=enabled,
        issuer=issuer,
        client_id=CLIENT_ID,
        client_secret_enc=sealed,
        scopes=["openid", "email"],
        authorization_endpoint=AUTHORIZE,
        token_endpoint=TOKEN,
        jwks_uri=JWKS_URI,
        allowed_domains=allowed_domains or [],
    )
    session.add(row)
    await session.commit()
    return row


@pytest_asyncio.fixture
async def configured(session: AsyncSession, cfg: Settings) -> OrgIdpConfig:
    return await _install_config(session, cfg)


async def _start(
    session: AsyncSession,
    cfg: Settings,
    directory: FakeDirectory,
    store: oidc.StateStore,
    *,
    email: str | None = EMAIL,
    domain: str | None = None,
) -> services.SsoRedirect:
    return await services.sso_start(
        session, cfg=cfg, directory=directory, store=store, email=email, domain=domain
    )


def _query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


async def _callback(
    session: AsyncSession,
    cfg: Settings,
    pair: KeyPair,
    directory: FakeDirectory,
    idp: FakeIdp,
    store: oidc.StateStore,
    *,
    state: str,
    code: str = "authorization-code",
) -> services.Issued:
    return await services.sso_callback(
        session,
        cfg=cfg,
        pair=pair,
        directory=directory,
        entitlements=FakeEntitlements(),
        idp=idp,
        store=store,
        code=code,
        state=state,
    )


async def _sign_in(
    session: AsyncSession,
    cfg: Settings,
    pair: KeyPair,
    directory: FakeDirectory,
    idp: FakeIdp,
    store: oidc.StateStore,
    **token_kwargs: Any,
) -> services.Issued:
    """The whole happy path, start to token, with the ID token bent by `token_kwargs`."""
    redirect = await _start(session, cfg, directory, store)
    idp.next_id_token = idp.issue_id_token(
        nonce=_query(redirect.authorization_url)["nonce"], **token_kwargs
    )
    return await _callback(session, cfg, pair, directory, idp, store, state=redirect.state)


async def _sign_in_as(
    session: AsyncSession,
    cfg: Settings,
    pair: KeyPair,
    directory: FakeDirectory,
    idp: FakeIdp,
    store: oidc.StateStore,
    *,
    email: str,
    subject: str,
) -> services.Issued:
    """A sign-in as a specific person through their own domain — for tests that need several
    federated sessions at once (a distinct `subject` each, so the identity links do not clash)."""
    redirect = await _start(session, cfg, directory, store, email=email)
    idp.next_id_token = idp.issue_id_token(
        nonce=_query(redirect.authorization_url)["nonce"], email=email, subject=subject
    )
    return await _callback(session, cfg, pair, directory, idp, store, state=redirect.state)


async def _refresh_token_count(session: AsyncSession) -> int:
    return int((await session.execute(select(func.count()).select_from(RefreshToken))).scalar_one())


# --------------------------------------------------------------------- domain → provider


class TestDomainResolution:
    async def test_a_verified_domain_produces_an_authorization_url(
        self, session, cfg, directory, store, configured
    ) -> None:
        redirect = await _start(session, cfg, directory, store)
        params = _query(redirect.authorization_url)

        assert redirect.authorization_url.startswith(AUTHORIZE)
        assert params["response_type"] == "code"
        assert params["client_id"] == CLIENT_ID
        assert params["redirect_uri"] == cfg.sso_redirect_uri
        assert params["scope"] == "openid email"
        assert params["state"] == redirect.state
        assert params["code_challenge_method"] == "S256"
        assert params["nonce"]
        # The address the person typed rides along as a hint and decides nothing.
        assert params["login_hint"] == EMAIL
        assert redirect.organization_slug == "acme"

    async def test_a_bare_domain_works_as_well_as_an_address(
        self, session, cfg, directory, store, configured
    ) -> None:
        redirect = await _start(session, cfg, directory, store, email=None, domain=VERIFIED_DOMAIN)
        assert "login_hint" not in _query(redirect.authorization_url)

    async def test_an_unverified_domain_is_refused(
        self, session, cfg, directory, store, configured
    ) -> None:
        """The ADR-0000 §1 refusal. Verification is what makes domain routing safe at all."""
        with pytest.raises(oidc.SsoNotConfiguredProblem):
            await _start(session, cfg, directory, store, email=f"someone@{UNVERIFIED_DOMAIN}")

    async def test_an_unknown_domain_is_refused_identically_to_an_unverified_one(
        self, session, cfg, directory, store, configured
    ) -> None:
        """Same status, same type, same words — or this endpoint maps our customers."""
        with pytest.raises(oidc.SsoNotConfiguredProblem) as unknown:
            await _start(session, cfg, directory, store, email="someone@nobody.test")
        with pytest.raises(oidc.SsoNotConfiguredProblem) as unverified:
            await _start(session, cfg, directory, store, email=f"someone@{UNVERIFIED_DOMAIN}")

        assert unknown.value.detail == unverified.value.detail
        assert unknown.value.status_code == unverified.value.status_code
        assert unknown.value.problem_type == unverified.value.problem_type

    async def test_a_suspended_organization_is_refused(
        self, session, cfg, directory, store, configured
    ) -> None:
        with pytest.raises(oidc.SsoNotConfiguredProblem):
            await _start(session, cfg, directory, store, email="someone@suspended.test")

    async def test_a_verified_domain_with_no_configuration_is_refused(
        self, session, cfg, directory, store
    ) -> None:
        with pytest.raises(oidc.SsoNotConfiguredProblem):
            await _start(session, cfg, directory, store)

    async def test_a_disabled_configuration_is_refused(
        self, session, cfg, directory, store
    ) -> None:
        await _install_config(session, cfg, enabled=False)
        with pytest.raises(oidc.SsoNotConfiguredProblem):
            await _start(session, cfg, directory, store)

    async def test_another_organizations_configuration_is_not_reachable(
        self, session, cfg, directory, store
    ) -> None:
        """ORG_B has a provider; a sign-in for ORG_A's domain must not find it.

        The transaction is bound to the organization the *verified domain* named, and
        row-level security is what makes the wrong row unreadable rather than merely
        unselected.
        """
        await _install_config(session, cfg, organization_id=ORG_B)
        with pytest.raises(oidc.SsoNotConfiguredProblem):
            await _start(session, cfg, directory, store)

    async def test_an_explicit_domain_list_beats_a_catch_all(
        self, session, cfg, directory, store
    ) -> None:
        await _install_config(session, cfg, allowed_domains=[VERIFIED_DOMAIN])
        redirect = await _start(session, cfg, directory, store)
        assert redirect.provider_name == "Example IdP"

    async def test_a_domain_outside_the_list_is_refused(
        self, session, cfg, directory, store
    ) -> None:
        """A configuration scoped to one domain does not answer for the organization's others."""
        await _install_config(session, cfg, allowed_domains=["somewhere.else"])
        with pytest.raises(oidc.SsoNotConfiguredProblem):
            await _start(session, cfg, directory, store)


class TestPathTraversalEnumeration:
    """The reviewer's own repro (round 3, MAJOR): a `domain` shaped like
    `../users/by-email?email=<address>` used to reach a *different* internal route than
    `resolve_domain` meant to call, and account-service's real answer from that other route —
    200 for a known address, 404 for an unknown one — leaked back out through two different
    exception types. That made `sso_start` an unauthenticated user-enumeration oracle: anyone
    could learn whether an email address exists in the system with no credential at all.

    Uses the real `HttpDirectory` against a mock transport standing in for account-service —
    `FakeDirectory` never had this bug, because it never built a URL by f-string in the first
    place.
    """

    KNOWN_EMAIL = "victim@victim.com"

    @staticmethod
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/internal/v1/users/by-email":
            if request.url.params.get("email") == TestPathTraversalEnumeration.KNOWN_EMAIL:
                return httpx.Response(
                    200,
                    json={
                        "user_id": "00000000-0000-0000-0000-000000000001",
                        "email": TestPathTraversalEnumeration.KNOWN_EMAIL,
                        "full_name": "Victim",
                        "is_active": True,
                        "memberships": [],
                    },
                )
            return httpx.Response(404, json={})
        return httpx.Response(404, json={})

    def _directory(self) -> HttpDirectory:
        from app.config import settings

        directory = HttpDirectory("http://account", settings())
        directory._client = httpx.AsyncClient(
            base_url="http://account", transport=httpx.MockTransport(self._handler)
        )
        return directory

    async def test_a_known_email_in_the_payload_is_refused_the_same_as_an_unknown_one(
        self, session, cfg, store
    ) -> None:
        directory = self._directory()
        try:
            with pytest.raises(oidc.SsoNotConfiguredProblem) as known:
                await services.sso_start(
                    session,
                    cfg=cfg,
                    directory=directory,
                    store=store,
                    domain=f"../users/by-email?email={self.KNOWN_EMAIL}",
                )
            with pytest.raises(oidc.SsoNotConfiguredProblem) as unknown:
                await services.sso_start(
                    session,
                    cfg=cfg,
                    directory=directory,
                    store=store,
                    domain="../users/by-email?email=nobody@victim.com",
                )
        finally:
            await directory.aclose()

        # Not merely the same exception type — the same status, type and message, so nothing
        # about which of the two branches was taken travels back to the caller at all.
        assert known.value.detail == unknown.value.detail
        assert known.value.status_code == unknown.value.status_code

    async def test_the_traversal_payload_never_reaches_the_directory_at_all(
        self, session, cfg, store
    ) -> None:
        """Belt and suspenders: `normalise_sso_domain` now refuses anything not domain-shaped
        before `sso_start` ever calls `directory.resolve_domain` — the payload is refused on
        `target` being empty, not merely refused *after* still being sent somewhere."""
        called = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal called
            called = True
            return httpx.Response(404, json={})

        from app.config import settings

        directory = HttpDirectory("http://account", settings())
        directory._client = httpx.AsyncClient(
            base_url="http://account", transport=httpx.MockTransport(handler)
        )
        try:
            with pytest.raises(oidc.SsoNotConfiguredProblem):
                await services.sso_start(
                    session,
                    cfg=cfg,
                    directory=directory,
                    store=store,
                    domain=f"../users/by-email?email={self.KNOWN_EMAIL}",
                )
        finally:
            await directory.aclose()
        assert called is False


# --------------------------------------------------------------------- state and PKCE


class TestStateAndPkce:
    async def test_the_state_is_single_use(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        """The replay this whole mechanism exists for."""
        redirect = await _start(session, cfg, directory, store)
        nonce = _query(redirect.authorization_url)["nonce"]
        idp.next_id_token = idp.issue_id_token(nonce=nonce)
        await _callback(session, cfg, pair, directory, idp, store, state=redirect.state)

        idp.next_id_token = idp.issue_id_token(nonce=nonce)
        with pytest.raises(oidc.SsoExchangeProblem):
            await _callback(session, cfg, pair, directory, idp, store, state=redirect.state)

    async def test_a_state_we_never_issued_is_refused(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        with pytest.raises(oidc.SsoExchangeProblem):
            await _callback(session, cfg, pair, directory, idp, store, state="not-ours")

    async def test_a_provider_disabled_mid_flight_cannot_finish_its_request(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        """Turning single sign-on off has to take effect immediately, including for requests
        already in the air — otherwise revoking a compromised provider leaves a ten-minute
        window in which it can still mint sessions."""
        redirect = await _start(session, cfg, directory, store)
        await set_tenant(session, ORG_A)
        row = await services.get_sso_config(session, ORG_A)
        assert row is not None
        row.enabled = False
        await session.commit()

        idp.next_id_token = idp.issue_id_token(nonce=_query(redirect.authorization_url)["nonce"])
        with pytest.raises(oidc.SsoExchangeProblem):
            await _callback(session, cfg, pair, directory, idp, store, state=redirect.state)

    async def test_a_replayed_state_never_reaches_the_provider(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        """Order matters: the cheap local check runs before any network call.

        A replay that reached the token endpoint would spend a code and hand the provider a
        request on behalf of somebody who never asked.
        """
        redirect = await _start(session, cfg, directory, store)
        idp.next_id_token = idp.issue_id_token(nonce=_query(redirect.authorization_url)["nonce"])
        await _callback(session, cfg, pair, directory, idp, store, state=redirect.state)
        before = len(idp.exchanges)

        with pytest.raises(oidc.SsoExchangeProblem):
            await _callback(session, cfg, pair, directory, idp, store, state=redirect.state)
        assert len(idp.exchanges) == before

    async def test_the_verifier_sent_to_the_provider_matches_the_challenge_advertised(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        """PKCE, end to end: the challenge the browser carried is the hash of the verifier
        that never left this service."""
        redirect = await _start(session, cfg, directory, store)
        challenge = _query(redirect.authorization_url)["code_challenge"]
        idp.next_id_token = idp.issue_id_token(nonce=_query(redirect.authorization_url)["nonce"])
        await _callback(session, cfg, pair, directory, idp, store, state=redirect.state)

        verifier = idp.exchanges[0]["code_verifier"]
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        assert base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == challenge
        # And it is genuinely a secret this service kept: nothing in the redirect carries it.
        assert verifier not in redirect.authorization_url

    async def test_the_verifier_is_different_every_time(
        self, session, cfg, directory, store, configured
    ) -> None:
        first = _query((await _start(session, cfg, directory, store)).authorization_url)
        second = _query((await _start(session, cfg, directory, store)).authorization_url)
        assert first["code_challenge"] != second["code_challenge"]
        assert first["state"] != second["state"]
        assert first["nonce"] != second["nonce"]

    async def test_the_redirect_uri_is_configuration_not_input(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        """The same value goes to `authorize` and to the token endpoint, and both come from
        settings — an attacker-influenced redirect target is how a code is stolen."""
        redirect = await _start(session, cfg, directory, store)
        idp.next_id_token = idp.issue_id_token(nonce=_query(redirect.authorization_url)["nonce"])
        await _callback(session, cfg, pair, directory, idp, store, state=redirect.state)
        assert _query(redirect.authorization_url)["redirect_uri"] == cfg.sso_redirect_uri
        assert idp.exchanges[0]["redirect_uri"] == cfg.sso_redirect_uri

    async def test_a_code_the_provider_rejects_is_a_401_not_a_500(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        """An expired or already-spent authorization code."""
        redirect = await _start(session, cfg, directory, store)
        idp.reject_code = True
        with pytest.raises(oidc.SsoExchangeProblem) as raised:
            await _callback(session, cfg, pair, directory, idp, store, state=redirect.state)
        assert raised.value.status_code == 401


# --------------------------------------------------------------------- the ID token


class TestIdTokenValidation:
    async def test_a_token_signed_by_another_key_is_refused(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        with pytest.raises(oidc.SsoExchangeProblem):
            await _sign_in(session, cfg, pair, directory, idp, store, wrong_key=True)

    async def test_a_token_from_another_issuer_is_refused(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        """A valid token from *some* provider is not a token from this organization's."""
        with pytest.raises(oidc.SsoExchangeProblem):
            await _sign_in(
                session, cfg, pair, directory, idp, store, issuer="https://elsewhere.test"
            )

    async def test_a_token_minted_for_another_client_is_refused(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        """Otherwise any other relying party at the same provider can mint logins here."""
        with pytest.raises(oidc.SsoExchangeProblem):
            await _sign_in(session, cfg, pair, directory, idp, store, audience="someone-else")

    async def test_a_token_carrying_the_wrong_nonce_is_refused(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        """The defence against replaying a token minted for a different login."""
        redirect = await _start(session, cfg, directory, store)
        idp.next_id_token = idp.issue_id_token(nonce="a-nonce-from-somewhere-else")
        with pytest.raises(oidc.SsoExchangeProblem):
            await _callback(session, cfg, pair, directory, idp, store, state=redirect.state)

    async def test_an_expired_token_is_refused(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        with pytest.raises(oidc.SsoExchangeProblem):
            await _sign_in(
                session,
                cfg,
                pair,
                directory,
                idp,
                store,
                issued_at=int(time.time()) - 3600,
                expires_in=60,
            )

    async def test_an_unverified_address_is_refused(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        """An unverified address at the provider is an address anybody could have claimed."""
        with pytest.raises(oidc.SsoExchangeProblem):
            await _sign_in(session, cfg, pair, directory, idp, store, email_verified=False)

    async def test_a_missing_email_verified_claim_is_not_read_as_true(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        with pytest.raises(oidc.SsoExchangeProblem):
            await _sign_in(session, cfg, pair, directory, idp, store, omit_email_verified=True)

    async def test_a_symmetric_forgery_with_the_client_secret_is_refused(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        """Algorithm confusion: HS256 signed with the client secret, which the relying party
        and the provider both hold. Refused by the allow-list, not by luck."""
        with pytest.raises(oidc.SsoExchangeProblem):
            await _sign_in(session, cfg, pair, directory, idp, store, symmetric=True)

    async def test_every_rejection_reads_the_same_to_the_caller(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        """Naming which check failed tells a forger which half of the attempt worked."""
        details = set()
        for kwargs in (
            {"wrong_key": True},
            {"issuer": "https://elsewhere.test"},
            {"audience": "someone-else"},
            {"email_verified": False},
        ):
            with pytest.raises(oidc.SsoExchangeProblem) as raised:
                await _sign_in(session, cfg, pair, directory, idp, store, **kwargs)
            details.add((raised.value.status_code, raised.value.detail))
        assert len(details) == 1


# --------------------------------------------------------------------- who gets in


class TestMembership:
    async def test_an_address_the_directory_does_not_know_is_refused(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        """No just-in-time provisioning: an invitation is the way into this platform."""
        with pytest.raises(oidc.SsoNoAccessProblem):
            await _sign_in(session, cfg, pair, directory, idp, store, email=STRANGER)

    async def test_a_known_person_with_no_membership_here_is_refused(
        self, session, cfg, pair, directory, idp, store
    ) -> None:
        """ORG_B runs the provider; the person is a member of ORG_A only."""
        await _install_config(session, cfg, organization_id=ORG_B)
        directory.domains = [_domain(VERIFIED_DOMAIN, ORG_B, "beta")]
        with pytest.raises(oidc.SsoNoAccessProblem):
            await _sign_in(session, cfg, pair, directory, idp, store)

    async def test_an_unknown_address_and_a_non_member_are_indistinguishable(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        """No account-enumeration oracle: the provider authenticated them either way."""
        with pytest.raises(oidc.SsoNoAccessProblem) as stranger:
            await _sign_in(session, cfg, pair, directory, idp, store, email=STRANGER)

        deactivated = user_in_one_org()
        directory.users = [
            type(deactivated)(
                user_id=deactivated.user_id,
                email=deactivated.email,
                full_name=deactivated.full_name,
                is_active=False,
                memberships=deactivated.memberships,
            )
        ]
        with pytest.raises(oidc.SsoNoAccessProblem) as inactive:
            await _sign_in(session, cfg, pair, directory, idp, store)

        assert stranger.value.detail == inactive.value.detail
        assert stranger.value.status_code == inactive.value.status_code

    async def test_a_provider_subject_bound_to_another_account_is_refused(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        """Somebody else's provider account cannot be walked onto this platform account."""
        session.add(
            ExternalIdentity(
                user_id=uuid.uuid4(),
                provider=f"oidc:{ORG_A}",
                subject="idp-subject-1",
                email_at_link="someone@example.com",
            )
        )
        await session.commit()
        with pytest.raises(oidc.SsoIdentityConflictProblem):
            await _sign_in(session, cfg, pair, directory, idp, store)

    async def test_a_changed_subject_for_a_linked_account_is_refused(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        """The address was reassigned at the provider; the new holder does not inherit."""
        await _sign_in(session, cfg, pair, directory, idp, store)
        with pytest.raises(oidc.SsoIdentityConflictProblem):
            await _sign_in(session, cfg, pair, directory, idp, store, subject="a-different-sub")

    async def test_signing_in_twice_reuses_the_one_link(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        await _sign_in(session, cfg, pair, directory, idp, store)
        await _sign_in(session, cfg, pair, directory, idp, store)
        links = (await session.execute(select(ExternalIdentity))).scalars().all()
        await session.rollback()
        assert len(links) == 1


class TestCrossDomainTakeover:
    """The attack this class exists to close: an organization proves ownership of a domain it
    genuinely controls, configures OIDC for it, and then has that same (fully self-controlled)
    provider assert the email of a real user who belongs to the organization through some
    *other*, unrelated domain — minting a session as that person with no action of theirs at
    all. Every test here reproduces one step of the chain and proves it now fails there.
    """

    ATTACKER_DOMAIN = "attacker.test"

    async def test_the_full_attack_chain_is_refused_at_the_domain_check(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        """`configured` installs one OIDC provider for ORG_A with no `allowed_domains`
        (a catch-all), so it answers for *any* domain verified to ORG_A — including one the
        organization itself just added. `sam@example.com` is a real, existing member of
        ORG_A, reached here through `attacker.test`, a domain that has nothing to do with
        them. Before the fix this minted a session as Sam; now it must not.
        """
        directory.domains.append(_domain(self.ATTACKER_DOMAIN, ORG_A, "acme"))

        redirect = await _start(
            session, cfg, directory, store, email=None, domain=self.ATTACKER_DOMAIN
        )
        idp.next_id_token = idp.issue_id_token(
            nonce=_query(redirect.authorization_url)["nonce"], email=EMAIL
        )
        with pytest.raises(oidc.SsoNoAccessProblem):
            await _callback(session, cfg, pair, directory, idp, store, state=redirect.state)

        # Nothing was minted: no session, no external identity link, for anyone.
        sessions = (await session.execute(select(Session))).scalars().all()
        links = (await session.execute(select(ExternalIdentity))).scalars().all()
        await session.rollback()
        assert sessions == []
        assert links == []

    async def test_a_same_domain_assertion_still_works(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        """The control: the fix must refuse the mismatch above without breaking the ordinary
        case of the asserted email actually belonging to the domain the flow started for."""
        issued = await _sign_in(session, cfg, pair, directory, idp, store)
        assert issued.access_token

    async def test_a_domain_revoked_between_start_and_callback_is_refused(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        """The re-resolve. `pending.domain` was verified when the flow *started* — a release
        or an expiry landing before the callback returns must take effect immediately rather
        than let an in-flight login finish on stale information."""
        redirect = await _start(session, cfg, directory, store)
        idp.next_id_token = idp.issue_id_token(nonce=_query(redirect.authorization_url)["nonce"])

        # The domain stops being verified in the moment between the redirect and the return —
        # released, or its own periodic re-check finally demoting it.
        directory.domains = [d for d in directory.domains if d.domain != VERIFIED_DOMAIN]

        with pytest.raises(oidc.SsoNoAccessProblem):
            await _callback(session, cfg, pair, directory, idp, store, state=redirect.state)

    async def test_a_domain_reassigned_to_another_organization_mid_flow_is_refused(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        """The domain is re-verified elsewhere (a race won by a different organization,
        `services/account`'s own takeover-safety mechanism) while this callback is in flight.
        `resolved.organization_id` no longer matches the organization this state was bound to,
        and that must refuse rather than mint a session for the organization the state named."""
        redirect = await _start(session, cfg, directory, store)
        idp.next_id_token = idp.issue_id_token(nonce=_query(redirect.authorization_url)["nonce"])

        directory.domains = [
            _domain(VERIFIED_DOMAIN, ORG_B, "beta") if d.domain == VERIFIED_DOMAIN else d
            for d in directory.domains
        ]

        with pytest.raises(oidc.SsoNoAccessProblem):
            await _callback(session, cfg, pair, directory, idp, store, state=redirect.state)


class TestOrganizationPinning:
    """A session minted through SSO is bound to the one organization the identity provider
    was actually trusted for — see `_federated_organization_pin`'s own docstring for why.

    The control — a password session may still move freely between every organization the
    person belongs to — is `test_signin.py::TestOrgSwitching`, unchanged by this fix and
    still green; not duplicated here.
    """

    async def test_switching_organizations_is_refused_after_an_sso_sign_in(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        """Sam signed in through ORG_A's provider, for ORG_A's own verified domain. Sam is
        also, quite legitimately, a member of ORG_B — and that membership must still not be
        reachable from this session: the provider that vouched for Sam in ORG_A proved
        nothing at all about ORG_B."""
        directory.users = [user_in_two_orgs()]
        issued = await _sign_in(session, cfg, pair, directory, idp, store)

        with pytest.raises(ForbiddenProblem):
            await services.switch_organization(
                session,
                cfg=cfg,
                pair=pair,
                directory=directory,
                entitlements=FakeEntitlements(),
                session_id=issued.session_id,
                user_id=USER,
                organization_id=ORG_B,
            )

    async def test_switching_back_into_the_ssos_own_organization_still_works(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        """The pin refuses a *different* organization — it is not a blanket refusal of
        `switch_organization` for every federated session."""
        directory.users = [user_in_two_orgs()]
        issued = await _sign_in(session, cfg, pair, directory, idp, store)

        second = await services.switch_organization(
            session,
            cfg=cfg,
            pair=pair,
            directory=directory,
            entitlements=FakeEntitlements(),
            session_id=issued.session_id,
            user_id=USER,
            organization_id=ORG_A,
        )
        assert second.organization_id == ORG_A

    async def test_issue_for_itself_refuses_a_pinned_session_minting_for_another_org(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        """NIT (round 3): the pin is asserted inside `issue_for` itself now, not only by its
        two callers — proved by calling it directly, bypassing `switch_organization`'s own
        earlier check (and its side effects) entirely."""
        directory.users = [user_in_two_orgs()]
        issued = await _sign_in(session, cfg, pair, directory, idp, store)

        auth_session = await session.get(Session, issued.session_id)
        assert auth_session is not None
        user = await directory.get(USER)
        assert user is not None
        membership_b = user.membership_for(ORG_B)
        assert membership_b is not None

        with pytest.raises(ForbiddenProblem):
            await services.issue_for(
                session,
                pair=pair,
                cfg=cfg,
                auth_session=auth_session,
                membership=membership_b,
                entitlements=FakeEntitlements(),
                audience="app",
            )


class TestPinnedRefreshRevalidation:
    """MINOR5 (round 3): the pin (`_federated_organization_pin`) only ever proves a session
    was *once* minted through this organization's provider. `rotate_refresh` now re-checks,
    on every refresh, that the domain it was minted for is still verified to this
    organization and that the organization's IdP configuration is still enabled — both can
    change long after the original sign-in, and neither one changing was previously reflected
    in a session that would otherwise refresh forever."""

    async def test_a_domain_no_longer_verified_ends_the_session(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory, idp, store)

        # The domain stops being verified — released, or demoted by account-service's own
        # sweep (ADR-0018) — some time after the sign-in that trusted it.
        directory.domains = [
            _domain(VERIFIED_DOMAIN, ORG_A, "acme", verified=False)
            if d.domain == VERIFIED_DOMAIN
            else d
            for d in directory.domains
        ]

        with pytest.raises(UnauthorizedProblem):
            await services.rotate_refresh(
                session,
                entitlements=FakeEntitlements(),
                cfg=cfg,
                pair=pair,
                directory=directory,
                raw_token=issued.refresh_token,
            )

    async def test_a_released_domain_ends_the_session(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory, idp, store)
        directory.domains = [d for d in directory.domains if d.domain != VERIFIED_DOMAIN]

        with pytest.raises(UnauthorizedProblem):
            await services.rotate_refresh(
                session,
                entitlements=FakeEntitlements(),
                cfg=cfg,
                pair=pair,
                directory=directory,
                raw_token=issued.refresh_token,
            )

    async def test_a_disabled_idp_configuration_ends_the_session(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory, idp, store)

        async with session.begin():
            # `org_idp_configs` is RLS-protected; the tenant GUC `sso_callback` set is
            # transaction-local and cleared by its own commit, so this read needs its own.
            await set_tenant(session, ORG_A)
            row = (
                await session.execute(
                    select(OrgIdpConfig).where(OrgIdpConfig.organization_id == ORG_A)
                )
            ).scalar_one()
            row.enabled = False

        with pytest.raises(UnauthorizedProblem):
            await services.rotate_refresh(
                session,
                entitlements=FakeEntitlements(),
                cfg=cfg,
                pair=pair,
                directory=directory,
                raw_token=issued.refresh_token,
            )

    async def test_an_untouched_pinned_session_still_refreshes(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        """The control: the re-check must not refuse a federated session whose domain and IdP
        configuration are both still exactly as they were at sign-in."""
        issued = await _sign_in(session, cfg, pair, directory, idp, store)

        rotated = await services.rotate_refresh(
            session,
            entitlements=FakeEntitlements(),
            cfg=cfg,
            pair=pair,
            directory=directory,
            raw_token=issued.refresh_token,
        )
        assert rotated.refresh_token != issued.refresh_token

    async def test_the_result_is_cached_for_the_next_refresh(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        """A federated session refreshes often; the re-check must not cost a fresh directory
        round trip and a fresh RLS-bound read on every single one. Demoting the domain right
        after a first, successful refresh must not be visible to the *very next* refresh —
        only once the (five-minute) cache entry expires, which a unit test has no business
        waiting out."""
        issued = await _sign_in(session, cfg, pair, directory, idp, store)

        first = await services.rotate_refresh(
            session,
            entitlements=FakeEntitlements(),
            cfg=cfg,
            pair=pair,
            directory=directory,
            raw_token=issued.refresh_token,
        )

        directory.domains = [d for d in directory.domains if d.domain != VERIFIED_DOMAIN]

        # Still cached "ok" from the check the first refresh just made — this one must not
        # re-derive a fresh, now-negative answer from the directory it just changed.
        second = await services.rotate_refresh(
            session,
            entitlements=FakeEntitlements(),
            cfg=cfg,
            pair=pair,
            directory=directory,
            raw_token=first.refresh_token,
        )
        assert second.refresh_token != first.refresh_token

    # ------------------------------------------------------------- the session row is ended

    async def test_the_refusal_ends_the_session_row_not_just_the_refresh_family(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        """Final-review MAJOR: `_revoke_family` alone ends one refresh chain and leaves
        `sessions.revoked_at` NULL — the column `_live_session`, and so every route that takes
        a session id, actually checks. The session survived its own refusal and could go on to
        mint through `switch_organization`."""
        issued = await _sign_in(session, cfg, pair, directory, idp, store)
        directory.domains = [d for d in directory.domains if d.domain != VERIFIED_DOMAIN]

        with pytest.raises(UnauthorizedProblem):
            await services.rotate_refresh(
                session,
                entitlements=FakeEntitlements(),
                cfg=cfg,
                pair=pair,
                directory=directory,
                raw_token=issued.refresh_token,
            )

        revoked_at, reason = (
            await session.execute(
                select(Session.revoked_at, Session.revoked_reason).where(
                    Session.id == issued.session_id
                )
            )
        ).one()
        assert revoked_at is not None
        assert reason == "sso_no_longer_valid"

        tokens_before = await _refresh_token_count(session)
        with pytest.raises(UnauthorizedProblem):
            await services.switch_organization(
                session,
                cfg=cfg,
                pair=pair,
                directory=directory,
                entitlements=FakeEntitlements(),
                session_id=issued.session_id,
                user_id=USER,
                organization_id=ORG_A,
            )
        assert await _refresh_token_count(session) == tokens_before, "nothing may be minted"

    async def test_switching_ends_a_session_whose_domain_is_gone_without_a_refresh_first(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        """Final-review MAJOR: the same re-validation `rotate_refresh` runs, on the other path
        that mints. Switching into the session's *own* pinned organization mints a fresh
        access and refresh pair — a session that never happened to call refresh after its
        domain was released must not get one here."""
        issued = await _sign_in(session, cfg, pair, directory, idp, store)
        directory.domains = [d for d in directory.domains if d.domain != VERIFIED_DOMAIN]
        tokens_before = await _refresh_token_count(session)

        with pytest.raises(UnauthorizedProblem):
            await services.switch_organization(
                session,
                cfg=cfg,
                pair=pair,
                directory=directory,
                entitlements=FakeEntitlements(),
                session_id=issued.session_id,
                user_id=USER,
                organization_id=ORG_A,
            )

        assert await _refresh_token_count(session) == tokens_before, "nothing may be minted"
        revoked_at, reason = (
            await session.execute(
                select(Session.revoked_at, Session.revoked_reason).where(
                    Session.id == issued.session_id
                )
            )
        ).one()
        assert revoked_at is not None
        assert reason == "sso_no_longer_valid"
        live_tokens = (
            (
                await session.execute(
                    select(RefreshToken).where(
                        RefreshToken.session_id == issued.session_id,
                        RefreshToken.revoked_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert live_tokens == [], "the session's refresh tokens are ended with it"

    async def test_the_switch_route_ends_the_session_and_the_end_is_committed(
        self, client: AsyncClient, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        """Over HTTP, where the route's tenant session rolls back on the exception: the
        revocation has to be committed *before* raising, or the refusal would undo itself."""
        issued = await _sign_in(session, cfg, pair, directory, idp, store)
        directory.domains = [d for d in directory.domains if d.domain != VERIFIED_DOMAIN]
        principal = Principal(
            sub=USER,
            organization_id=ORG_A,
            membership_id=MEMBERSHIP_A,
            session_id=str(issued.session_id),
            roles=["MEMBER"],
            amr=["sso"],
        )
        app.dependency_overrides[get_principal] = lambda: principal

        response = await client.post(
            "/api/v1/auth/switch-org", json={"organization_id": str(ORG_A)}
        )

        assert response.status_code == 401, response.text
        revoked_at = (
            await session.execute(select(Session.revoked_at).where(Session.id == issued.session_id))
        ).scalar_one()
        assert revoked_at is not None

    # ------------------------------------------------------------- the cache is per domain

    @pytest.mark.parametrize("order", ["healthy_first", "demoted_first"])
    async def test_one_domains_cached_answer_never_answers_for_its_sibling(
        self, session, cfg, pair, directory, idp, store, configured, order
    ) -> None:
        """Final-review MAJOR: the cache was keyed by organization alone, but one organization
        can hold several verified domains. Refreshing a healthy domain's session cached "valid"
        for the whole organization — so a demoted sibling's session refreshed on the strength
        of it — and refreshing the demoted one first cached "invalid", ending the healthy
        domain's session. Both orders, because each is a different wrong answer."""
        pat = DirectoryUser(
            user_id=uuid.uuid4(),
            email="pat@second.test",
            full_name="Pat Second",
            is_active=True,
            memberships=[membership(ORG_A, uuid.uuid4(), "acme", "Acme Technologies")],
        )
        directory.users = [user_in_one_org(), pat]
        directory.domains.append(_domain("second.test", ORG_A, "acme"))

        healthy = await _sign_in_as(
            session, cfg, pair, directory, idp, store, email=EMAIL, subject="sub-healthy"
        )
        doomed = await _sign_in_as(
            session, cfg, pair, directory, idp, store, email="pat@second.test", subject="sub-doomed"
        )

        directory.domains = [
            _domain("second.test", ORG_A, "acme", verified=False)
            if d.domain == "second.test"
            else d
            for d in directory.domains
        ]

        async def refresh_healthy() -> services.Issued:
            return await services.rotate_refresh(
                session,
                entitlements=FakeEntitlements(),
                cfg=cfg,
                pair=pair,
                directory=directory,
                raw_token=healthy.refresh_token,
            )

        async def refresh_doomed() -> None:
            with pytest.raises(UnauthorizedProblem):
                await services.rotate_refresh(
                    session,
                    entitlements=FakeEntitlements(),
                    cfg=cfg,
                    pair=pair,
                    directory=directory,
                    raw_token=doomed.refresh_token,
                )

        if order == "healthy_first":
            rotated = await refresh_healthy()
            await refresh_doomed()
        else:
            await refresh_doomed()
            rotated = await refresh_healthy()
        assert rotated.refresh_token != healthy.refresh_token

    # ------------------------------------------------------------- fail closed on NULL

    async def test_a_federated_session_with_no_recorded_domain_fails_closed(
        self, session, cfg, pair, directory, redis, configured
    ) -> None:
        """Final-review MINOR: `sso_domain IS NULL` used to pass as valid. A fact that cannot be
        re-checked must not be assumed to hold. Not cached either — there is no domain to key
        it by."""
        auth_session = await services.start_session(
            session, user_id=USER, cfg=cfg, amr=["sso"], idp=f"oidc:{ORG_A}"
        )
        user = await directory.get(USER)
        assert user is not None
        member = user.membership_for(ORG_A)
        assert member is not None
        issued = await services.issue_for(
            session,
            pair=pair,
            cfg=cfg,
            auth_session=auth_session,
            membership=member,
            entitlements=FakeEntitlements(),
            audience="app",
        )
        await session.commit()

        with pytest.raises(UnauthorizedProblem):
            await services.rotate_refresh(
                session,
                entitlements=FakeEntitlements(),
                cfg=cfg,
                pair=pair,
                directory=directory,
                raw_token=issued.refresh_token,
            )
        assert [k for k in redis.store if k.startswith("auth:sso_pin_ok:")] == []

    # ------------------------------------------------------------- respects allowed_domains

    async def test_narrowing_allowed_domains_ends_existing_sessions_for_the_removed_domain(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        """Final-review MINOR: the provider check used `get_sso_config` (any row, enabled) rather
        than the lookup `sso_start` routes with, so a provider narrowed to exclude a domain
        still kept that domain's existing sessions alive."""
        issued = await _sign_in(session, cfg, pair, directory, idp, store)

        await set_tenant(session, ORG_A)
        row = (
            await session.execute(select(OrgIdpConfig).where(OrgIdpConfig.organization_id == ORG_A))
        ).scalar_one()
        row.allowed_domains = ["somewhere.else"]
        await session.commit()

        with pytest.raises(UnauthorizedProblem):
            await services.rotate_refresh(
                session,
                entitlements=FakeEntitlements(),
                cfg=cfg,
                pair=pair,
                directory=directory,
                raw_token=issued.refresh_token,
            )

    async def test_a_second_oidc_provider_does_not_break_refresh(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        """`get_sso_config` is `scalar_one_or_none()` — with two OIDC rows for an organization
        (a staff provider and a subsidiary's, `_config_for_domain`'s own documented case) it
        raised instead of answering. The lookup that already handles that is the one used."""
        issued = await _sign_in(session, cfg, pair, directory, idp, store)
        await _install_config(
            session,
            cfg,
            allowed_domains=["subsidiary.test"],
            issuer="https://idp2.example.test",
        )

        rotated = await services.rotate_refresh(
            session,
            entitlements=FakeEntitlements(),
            cfg=cfg,
            pair=pair,
            directory=directory,
            raw_token=issued.refresh_token,
        )
        assert rotated.refresh_token != issued.refresh_token


class _RecordingDirectory(FakeDirectory):
    """Remembers every `find_by_email` — the assertion that matters for a gate is not the
    exception type but that the lookup it guards was never made."""

    def __init__(self, users: list[DirectoryUser], domains: list[DirectoryDomain]) -> None:
        super().__init__(users, domains)
        self.lookups: list[str] = []

    async def find_by_email(self, email: str) -> DirectoryUser | None:
        self.lookups.append(email)
        return await super().find_by_email(email)


class TestNonAsciiIdentityIsRefusedOnTheRawString:
    """Final-review MINOR: the ASCII gate tested `NFKC(identity.email)` while `find_by_email`
    and the comparison after it received the *raw* string. `K` (U+212A, KELVIN SIGN)
    NFKC-normalises to plain `K`, passed the gate, then folded to `k` under `lower()` and
    matched a real user's all-ASCII address."""

    KELVIN = chr(0x212A)  # U+212A, built from the code point so no editor can flatten it to ASCII K

    async def test_a_kelvin_sign_assertion_is_refused_before_any_lookup(
        self, session, cfg, pair, idp, store, configured
    ) -> None:
        kate = DirectoryUser(
            user_id=uuid.uuid4(),
            email="kate@example.com",
            full_name="Kate",
            is_active=True,
            memberships=[membership(ORG_A, uuid.uuid4(), "acme", "Acme Technologies")],
        )
        directory = _RecordingDirectory([kate], [_domain(VERIFIED_DOMAIN, ORG_A, "acme")])
        redirect = await _start(session, cfg, directory, store)
        idp.next_id_token = idp.issue_id_token(
            nonce=_query(redirect.authorization_url)["nonce"],
            email=f"{self.KELVIN}ate@example.com",
        )

        with pytest.raises(oidc.SsoNoAccessProblem):
            await _callback(session, cfg, pair, directory, idp, store, state=redirect.state)

        assert directory.lookups == [], "refused before the directory was ever asked"
        assert (await session.execute(select(Session))).scalars().all() == []
        await session.rollback()

    async def test_a_stored_non_ascii_address_never_matches_an_ascii_assertion(
        self, session, cfg, pair, idp, store, configured
    ) -> None:
        """The mirror image: the assertion is all-ASCII, but the *stored* address holds a
        Kelvin sign, which `lower()` folds to `k` and compares equal to it. Only a stored
        address that is itself ASCII can be the address an ASCII assertion names."""
        kate = DirectoryUser(
            user_id=uuid.uuid4(),
            email=f"{self.KELVIN}ate@example.com",
            full_name="Kate",
            is_active=True,
            memberships=[membership(ORG_A, uuid.uuid4(), "acme", "Acme Technologies")],
        )
        directory = _RecordingDirectory([kate], [_domain(VERIFIED_DOMAIN, ORG_A, "acme")])
        redirect = await _start(session, cfg, directory, store)
        idp.next_id_token = idp.issue_id_token(
            nonce=_query(redirect.authorization_url)["nonce"], email="kate@example.com"
        )

        with pytest.raises(oidc.SsoNoAccessProblem):
            await _callback(session, cfg, pair, directory, idp, store, state=redirect.state)

        assert (await session.execute(select(Session))).scalars().all() == []
        await session.rollback()


class _CitextDirectory(FakeDirectory):
    """`find_by_email` compares the way account-service's real `/users/by-email` route does:
    through Postgres `CITEXT`, not Python's own casefold — the mismatch round 3 found."""

    def __init__(
        self, users: list[DirectoryUser], domains: list[DirectoryDomain], session: AsyncSession
    ) -> None:
        super().__init__(users, domains)
        self._session = session

    async def find_by_email(self, email: str) -> DirectoryUser | None:
        for u in self.users:
            same = (
                await self._session.execute(
                    text("select cast(:a as citext) = cast(:b as citext)"),
                    {"a": u.email, "b": email},
                )
            ).scalar_one()
            if same:
                return u
        return None


class TestUnicodeCasefoldBypass:
    """Postgres's `CITEXT` folds case with the database's collation; `idna`'s UTS-46 folds
    Unicode by an entirely different table. `İ` (U+0130, LATIN CAPITAL LETTER I WITH DOT ABOVE)
    is the concrete case round 3 found: `normalise_sso_domain("vİctim.com")` encodes to
    `xn--victim-qyd.com` — a real, distinct domain an attacker's own honestly-configured IdP
    could verify and use — while Postgres's default collation lower-cases the same character to
    plain `i`, so `find_by_email` can still match a *completely unrelated* real user whose
    stored address is the plain-ASCII `victim@victim.com`.
    """

    async def test_the_dotted_capital_i_casefold_is_refused(
        self, session, cfg, pair, store
    ) -> None:
        attacker_domain = services.normalise_sso_domain("vİctim.com")
        assert attacker_domain == "xn--victim-qyd.com"

        victim_id = uuid.uuid4()
        victim = DirectoryUser(
            user_id=victim_id,
            email="victim@victim.com",
            full_name="Victim",
            is_active=True,
            # A real member of ORG_A, through a domain that has nothing to do with the one
            # the attacker's provider actually proved — reached here through the matching
            # function disagreeing with Postgres, rather than through an outright mismatch.
            memberships=[membership(ORG_A, uuid.uuid4(), "acme", "Acme Technologies")],
        )
        fake_directory = _CitextDirectory(
            [victim], [_domain(attacker_domain, ORG_A, "acme")], session
        )
        await _install_config(session, cfg)
        idp = FakeIdp()

        redirect = await _start(
            session, cfg, fake_directory, store, email=None, domain=attacker_domain
        )
        idp.next_id_token = idp.issue_id_token(
            nonce=_query(redirect.authorization_url)["nonce"], email="victim@vİctim.com"
        )

        with pytest.raises(oidc.SsoNoAccessProblem):
            await _callback(session, cfg, pair, fake_directory, idp, store, state=redirect.state)

        # Nothing was minted as the real victim.
        sessions = (await session.execute(select(Session))).scalars().all()
        await session.rollback()
        assert sessions == []

    async def test_an_ascii_only_assertion_still_signs_in_normally(
        self, session, cfg, pair, store
    ) -> None:
        """The control: the ASCII-only requirement must not refuse an ordinary, all-ASCII
        sign-in — only a non-ASCII identity."""
        fake_directory = _CitextDirectory(
            [user_in_one_org()], [_domain(VERIFIED_DOMAIN, ORG_A, "acme")], session
        )
        await _install_config(session, cfg)
        issued = await _sign_in(session, cfg, pair, fake_directory, FakeIdp(), store, email=EMAIL)
        assert issued.access_token


# --------------------------------------------------------------------- what comes out


class TestTheTokenItMints:
    async def test_a_successful_sign_in_issues_a_usable_pair(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory, idp, store)
        assert issued.organization_id == ORG_A
        assert issued.membership_id == MEMBERSHIP_A
        assert issued.refresh_token

    async def test_the_access_token_is_shaped_exactly_like_a_password_login(
        self, session, cfg, pair, directory, idp, store, configured, registered
    ) -> None:
        """The property that keeps this from becoming a second minting path.

        Every claim a password login produces, produced here, with the same values —
        audience, organization, membership, entitlements, roles, permission version. `amr` is
        the one deliberate difference, and it is a difference that must exist: a step-up gate
        demanding a second factor cannot be satisfiable by an external assertion.
        """
        federated = await _sign_in(session, cfg, pair, directory, idp, store)
        outcome = await services.login(
            session,
            cfg=cfg,
            pair=pair,
            directory=directory,
            entitlements=FakeEntitlements(),
            email=EMAIL,
            password=PASSWORD,
        )
        assert outcome.issued is not None

        sso_claims = jwt.decode(federated.access_token, options={"verify_signature": False})
        pwd_claims = jwt.decode(outcome.issued.access_token, options={"verify_signature": False})

        assert set(sso_claims) == set(pwd_claims)
        shared = ("iss", "sub", "typ", "aud", "act_org", "mem", "ent", "roles", "loc", "pv")
        assert {k: sso_claims[k] for k in shared} == {k: pwd_claims[k] for k in shared}
        assert sso_claims["amr"] == ["sso"]
        assert pwd_claims["amr"] == ["pwd"]
        # No SSO-only claim crept in, and nothing marks it as staff.
        assert "stf" not in sso_claims

    async def test_the_token_verifies_against_the_platform_signing_key(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        """Only auth-service signs. An SSO token is signed by the same key as every other."""
        issued = await _sign_in(session, cfg, pair, directory, idp, store)
        claims = jwt.decode(
            issued.access_token,
            pair.public,
            algorithms=["EdDSA"],
            issuer=cfg.jwt_issuer,
            audience="app",
        )
        assert claims["sub"] == str(USER)

    async def test_the_session_records_which_provider_vouched(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        await _sign_in(session, cfg, pair, directory, idp, store)
        idp_name, amr = (await session.execute(select(Session.idp, Session.amr))).one()
        await session.rollback()
        assert idp_name == f"oidc:{ORG_A}"
        assert amr == ["sso"]

    async def test_it_leaves_an_audit_row_and_an_event(
        self, session, cfg, pair, directory, idp, store, configured
    ) -> None:
        await _sign_in(session, cfg, pair, directory, idp, store)
        actions = (await session.execute(select(AuditLog.action))).scalars().all()
        events = (await session.execute(select(OutboxEvent.event_type))).scalars().all()
        await session.rollback()
        assert "auth.sso_signed_in" in actions
        assert services.EVT_SSO_SIGNED_IN in events


# --------------------------------------------------------------------- the HTTP client
#
# Everything above runs against `FakeIdp`, which is the right seam for the flow and the wrong
# one for the class that actually speaks HTTP. These drive `HttpIdpClient` through
# `httpx.MockTransport` — still no network — so the half of the module a fake cannot cover is
# not the untested half.


def _transport(handler: Any) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


#: A stub resolver standing in for real DNS: `ISSUER`'s host is a fake TLD
#: (`idp.example.test`) that no test may actually look up. Public and stable, so nothing in
#: `TestTheHttpClient` is testing the egress guard itself — that is `TestTheEgressGuard` and
#: `libs/platform-core`'s own suite — only that a guarded client still forwards a *permitted*
#: URL to its transport exactly as an unguarded one did.
async def _resolves_public(host: str) -> list[str]:
    return ["93.184.216.34"]


async def _resolves_internal(host: str) -> list[str]:
    """What a rebinding attacker, or a plain misconfiguration, would have `discover` reach."""
    return ["169.254.169.254"]


class TestTheHttpClient:
    async def test_it_sends_the_secret_and_the_verifier_as_form_fields(self) -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(dict(parse_qs(request.content.decode())))  # type: ignore[arg-type]
            return httpx.Response(200, json={"id_token": "a.b.c"})

        client = oidc.HttpIdpClient(transport=_transport(handler), resolver=_resolves_public)
        result = await client.exchange_code(
            TOKEN,
            code="code",
            redirect_uri="https://opsgrids.test/callback",
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            code_verifier="verifier",
        )
        await client.aclose()

        assert result.id_token == "a.b.c"
        assert seen["grant_type"] == ["authorization_code"]
        assert seen["code_verifier"] == ["verifier"]
        assert seen["client_secret"] == [CLIENT_SECRET]

    async def test_a_rejected_code_becomes_a_401_and_the_body_is_not_echoed(self) -> None:
        """A provider's error body is written for a developer, not for whoever holds a
        stolen code."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": "invalid_grant", "hint": "already used"})

        client = oidc.HttpIdpClient(transport=_transport(handler), resolver=_resolves_public)
        with pytest.raises(oidc.SsoExchangeProblem) as raised:
            await client.exchange_code(
                TOKEN,
                code="spent",
                redirect_uri="https://opsgrids.test/callback",
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                code_verifier="verifier",
            )
        await client.aclose()
        assert "invalid_grant" not in raised.value.detail

    async def test_a_response_that_is_not_json_is_a_503_not_a_500(self) -> None:
        """The realistic misconfiguration: an endpoint pointed at an HTML page, answering
        200. It is an upstream we cannot use, not a stack trace."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>sign in</html>")

        client = oidc.HttpIdpClient(transport=_transport(handler), resolver=_resolves_public)
        with pytest.raises(UpstreamUnavailableProblem):
            await client.fetch_jwks(JWKS_URI)
        await client.aclose()

    async def test_a_token_response_with_no_id_token_is_refused(self) -> None:
        """A plain OAuth2 response is not OIDC. Refusing beats inventing an identity."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"access_token": "at", "token_type": "Bearer"})

        client = oidc.HttpIdpClient(transport=_transport(handler), resolver=_resolves_public)
        with pytest.raises(oidc.SsoExchangeProblem):
            await client.exchange_code(
                TOKEN,
                code="code",
                redirect_uri="https://opsgrids.test/callback",
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                code_verifier="verifier",
            )
        await client.aclose()

    async def test_an_unreachable_provider_is_a_503(self) -> None:
        """Not "your credentials are wrong" — the client should retry, and an operator
        reading the logs should see the two apart."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host")

        client = oidc.HttpIdpClient(transport=_transport(handler), resolver=_resolves_public)
        with pytest.raises(UpstreamUnavailableProblem):
            await client.discover(f"{ISSUER}/.well-known/openid-configuration")
        await client.aclose()


# --------------------------------------------------------------------- the egress guard
#
# `HttpIdpClient` is the one place in this service that fetches a URL an organization admin
# chose. These tests are the SSRF finding, from the seam auth-service actually owns: a stub
# resolver stands in for DNS returning something internal, and the assertion that matters most
# is not the exception type — it is that the mock transport was never asked to do anything.


class TestTheEgressGuard:
    async def test_a_discovery_url_resolving_internally_is_refused_with_no_request_sent(
        self,
    ) -> None:
        called = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal called
            called = True
            return httpx.Response(200, json={"issuer": ISSUER})  # pragma: no cover - unreached

        client = oidc.HttpIdpClient(transport=_transport(handler), resolver=_resolves_internal)
        with pytest.raises(UpstreamUnavailableProblem):
            await client.discover(f"{ISSUER}/.well-known/openid-configuration")
        await client.aclose()

        assert called is False, "a host resolving to a disallowed address must never be dialed"

    async def test_the_refusal_never_names_the_disallowed_address(self) -> None:
        """The client-visible message is the same generic one an unreachable provider gets —
        it does not confirm that a probe against the platform's internal network landed."""

        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - unreached
            return httpx.Response(200, json={})

        client = oidc.HttpIdpClient(transport=_transport(handler), resolver=_resolves_internal)
        with pytest.raises(UpstreamUnavailableProblem) as raised:
            await client.fetch_jwks(JWKS_URI)
        await client.aclose()

        assert "169.254.169.254" not in raised.value.detail

    async def test_a_plain_http_discovery_url_is_refused(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - unreached
            return httpx.Response(200, json={})

        client = oidc.HttpIdpClient(transport=_transport(handler), resolver=_resolves_public)
        with pytest.raises(UpstreamUnavailableProblem):
            await client.discover("http://idp.example.test/.well-known/openid-configuration")
        await client.aclose()


class TestNormaliseSsoDomain:
    """Round 3: this function's own contract — a domain-shaped ASCII A-label, or `""`, never
    the raw input verbatim. `""` is what let a path-traversal payload sail through unrefused
    all the way to `directory.resolve_domain`'s own f-string (`TestPathTraversalEnumeration`,
    above, closes that end to end); this is the unit-level version."""

    def test_a_normal_domain_round_trips(self) -> None:
        assert services.normalise_sso_domain("Acme.COM.") == "acme.com"

    def test_a_unicode_domain_becomes_its_a_label(self) -> None:
        assert services.normalise_sso_domain("köln.de") == "xn--kln-sna.de"

    def test_a_string_idna_refuses_outright_is_empty_not_unchanged(self) -> None:
        """The exact regression: this used to return the raw string verbatim."""
        assert services.normalise_sso_domain("../users/by-email?email=x") == ""

    def test_a_query_string_appended_to_a_real_domain_is_empty(self) -> None:
        assert services.normalise_sso_domain("acme.com?x=../../users/by-email") == ""

    def test_blank_input_is_empty(self) -> None:
        assert services.normalise_sso_domain("   ") == ""

    def test_nothing_but_dots_is_empty(self) -> None:
        assert services.normalise_sso_domain("...") == ""


# --------------------------------------------------------------------- administration


class TestJitProvisioningDefaultsToFalse:
    """A flag that is inert today but means "let an external issuer create an account" the
    day it is wired up must not default to on for a row nobody explicitly configured — see
    the column's docstring in `app/models.py` and the migration that corrected it."""

    async def test_the_orm_default_is_false(self, session, cfg) -> None:
        await set_tenant(session, ORG_A)
        row = OrgIdpConfig(
            organization_id=ORG_A,
            protocol="oidc",
            display_name="No jit_provisioning passed",
            issuer="https://defaults.example.test",
            client_id="defaults-client",
        )
        session.add(row)
        await session.flush()
        assert row.jit_provisioning is False
        await session.rollback()

    async def test_the_database_column_itself_defaults_to_false(self, session, cfg) -> None:
        """Not just the ORM's default: `information_schema` is asked what the *column*
        would supply for a row that never mentions it — a fixture, a repair script, a
        future migration run outside this service's models — which is a question a row
        insert cannot isolate from every other NOT NULL column also needing a value."""
        result = await session.execute(
            text(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_schema = 'auth' AND table_name = 'org_idp_configs' "
                "AND column_name = 'jit_provisioning'"
            )
        )
        assert result.scalar_one() == "false"
        await session.rollback()


class TestConfiguration:
    async def test_the_client_secret_round_trips_through_the_envelope(
        self, session, cfg, configured
    ) -> None:
        """Sealed at rest, and openable only with the configured key."""
        await set_tenant(session, ORG_A)
        row = await services.get_sso_config(session, ORG_A)
        assert row is not None and row.client_secret_enc is not None
        assert CLIENT_SECRET.encode() not in row.client_secret_enc
        assert pii.open_sealed(row.client_secret_enc, settings=cfg) == CLIENT_SECRET
        await session.rollback()

    async def test_discovery_fills_the_endpoints(self, session, cfg, idp) -> None:
        await set_tenant(session, ORG_A)
        row = await services.save_sso_config(
            session,
            cfg=cfg,
            organization_id=ORG_A,
            actor_id=USER,
            display_name="Example IdP",
            issuer=ISSUER,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            discovery_url=f"{ISSUER}/.well-known/openid-configuration",
            authorization_endpoint=None,
            token_endpoint=None,
            jwks_uri=None,
            scopes=None,
            allowed_domains=[VERIFIED_DOMAIN],
            enabled=True,
            enforce_sso=False,
            idp=idp,
        )
        await session.commit()
        assert row.authorization_endpoint == AUTHORIZE
        assert row.token_endpoint == TOKEN
        assert row.jwks_uri == JWKS_URI

    async def test_a_discovery_document_for_another_issuer_is_refused(
        self, session, cfg, idp
    ) -> None:
        """Adopting whatever the URL returned would make the issuer check circular."""
        await set_tenant(session, ORG_A)
        with pytest.raises(BusinessRuleProblem):
            await services.save_sso_config(
                session,
                cfg=cfg,
                organization_id=ORG_A,
                actor_id=USER,
                display_name="Example IdP",
                issuer="https://we-expected-this.test",
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                discovery_url=f"{ISSUER}/.well-known/openid-configuration",
                authorization_endpoint=None,
                token_endpoint=None,
                jwks_uri=None,
                scopes=None,
                allowed_domains=[],
                enabled=False,
                enforce_sso=False,
                idp=idp,
            )
        await session.rollback()

    async def test_an_incomplete_configuration_cannot_be_enabled(self, session, cfg) -> None:
        """Refused at save, so nobody discovers it at nine in the morning."""
        await set_tenant(session, ORG_A)
        with pytest.raises(BusinessRuleProblem):
            await services.save_sso_config(
                session,
                cfg=cfg,
                organization_id=ORG_A,
                actor_id=USER,
                display_name="Half a provider",
                issuer=ISSUER,
                client_id=CLIENT_ID,
                client_secret=None,
                discovery_url=None,
                authorization_endpoint=None,
                token_endpoint=None,
                jwks_uri=None,
                scopes=None,
                allowed_domains=[],
                enabled=True,
                enforce_sso=False,
            )
        await session.rollback()

    async def test_updating_without_a_secret_keeps_the_stored_one(
        self, session, cfg, configured
    ) -> None:
        await set_tenant(session, ORG_A)
        row = await services.save_sso_config(
            session,
            cfg=cfg,
            organization_id=ORG_A,
            actor_id=USER,
            display_name="Renamed",
            issuer=ISSUER,
            client_id=CLIENT_ID,
            client_secret=None,
            discovery_url=None,
            authorization_endpoint=AUTHORIZE,
            token_endpoint=TOKEN,
            jwks_uri=JWKS_URI,
            scopes=None,
            allowed_domains=[],
            enabled=True,
            enforce_sso=False,
        )
        await session.commit()
        assert row.display_name == "Renamed"
        assert row.client_secret_enc is not None
        assert pii.open_sealed(row.client_secret_enc, settings=cfg) == CLIENT_SECRET

    async def test_the_audit_row_never_carries_the_secret(self, session, cfg, configured) -> None:
        await set_tenant(session, ORG_A)
        await services.save_sso_config(
            session,
            cfg=cfg,
            organization_id=ORG_A,
            actor_id=USER,
            display_name="Example IdP",
            issuer=ISSUER,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            discovery_url=None,
            authorization_endpoint=AUTHORIZE,
            token_endpoint=TOKEN,
            jwks_uri=JWKS_URI,
            scopes=None,
            allowed_domains=[],
            enabled=True,
            enforce_sso=False,
        )
        await session.commit()
        rows = (await session.execute(select(AuditLog.action, AuditLog.after))).all()
        await session.rollback()
        written = [after for action, after in rows if action.startswith("auth.sso_config")]
        assert written
        for after in written:
            assert CLIENT_SECRET not in str(after)
            assert after is not None and after["client_secret_rotated"] is True


# --------------------------------------------------------------------- over HTTP


def _admin() -> Principal:
    return Principal(
        sub=USER,
        organization_id=ORG_A,
        membership_id=MEMBERSHIP_A,
        session_id=str(uuid.uuid4()),
        roles=["ORG_OWNER"],
        perms=["core.identity.sso.read:ORG", "core.identity.sso.write:ORG"],
        amr=["pwd"],
    )


def _member() -> Principal:
    return Principal(
        sub=USER,
        organization_id=ORG_A,
        membership_id=MEMBERSHIP_A,
        session_id=str(uuid.uuid4()),
        roles=["MEMBER"],
        perms=["core.profile.self.read:OWN"],
        amr=["pwd"],
    )


class TestOverHttp:
    async def test_start_and_callback_answer_like_the_login_endpoint(
        self, client: AsyncClient, session, cfg, idp, redis, configured
    ) -> None:
        app.dependency_overrides[get_idp] = lambda: idp

        started = await client.get("/api/v1/auth/sso/start", params={"email": EMAIL})
        assert started.status_code == 200, started.text
        body = started.json()
        idp.next_id_token = idp.issue_id_token(nonce=_query(body["authorization_url"])["nonce"])

        done = await client.get(
            "/api/v1/auth/sso/callback", params={"code": "abc", "state": body["state"]}
        )
        assert done.status_code == 200, done.text
        assert done.json()["status"] == "complete"
        assert done.json()["organization_id"] == str(ORG_A)
        # The refresh token is a cookie and never a field, exactly as on the password path.
        assert cfg.refresh_cookie_name in done.cookies
        assert "refresh_token" not in done.json()

    async def test_start_needs_an_email_or_a_domain(self, client: AsyncClient) -> None:
        assert (await client.get("/api/v1/auth/sso/start")).status_code == 422

    async def test_an_unknown_domain_answers_a_problem_document(
        self, client: AsyncClient, configured
    ) -> None:
        response = await client.get(
            "/api/v1/auth/sso/start", params={"email": "someone@nobody.test"}
        )
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["type"].endswith("/sso-not-configured")

    async def test_reading_the_configuration_needs_the_permission(
        self, client: AsyncClient, configured
    ) -> None:
        app.dependency_overrides[get_principal] = _member
        assert (await client.get("/api/v1/auth/sso/config")).status_code == 403

    async def test_the_configuration_endpoint_never_returns_the_secret(
        self, client: AsyncClient, idp, configured
    ) -> None:
        app.dependency_overrides[get_principal] = _admin
        response = await client.get("/api/v1/auth/sso/config")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["client_secret_set"] is True
        assert "client_secret" not in body
        assert CLIENT_SECRET not in response.text

    async def test_an_administrator_can_write_the_configuration(
        self, client: AsyncClient, session, cfg, idp
    ) -> None:
        app.dependency_overrides[get_principal] = _admin
        app.dependency_overrides[get_idp] = lambda: idp

        response = await client.put(
            "/api/v1/auth/sso/config",
            json={
                "display_name": "Example IdP",
                "issuer": ISSUER,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "discovery_url": f"{ISSUER}/.well-known/openid-configuration",
                "allowed_domains": [VERIFIED_DOMAIN],
                "enabled": True,
                "enforce_sso": False,
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["jwks_uri"] == JWKS_URI
        assert CLIENT_SECRET not in response.text
