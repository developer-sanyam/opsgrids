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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import oidc, services
from app.config import Settings
from app.deps import get_idp
from app.directory import DirectoryDomain
from app.keys import KeyPair
from app.main import app
from app.models import AuditLog, ExternalIdentity, OrgIdpConfig, OutboxEvent, Session
from platform_core import pii
from platform_core.auth import Principal, get_principal
from platform_core.db import set_tenant
from platform_core.errors import BusinessRuleProblem, UpstreamUnavailableProblem
from tests.conftest import (
    MEMBERSHIP_A,
    ORG_A,
    ORG_B,
    PASSWORD,
    USER,
    FakeDirectory,
    FakeEntitlements,
    FakeRedis,
    user_in_one_org,
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


class TestTheHttpClient:
    async def test_it_sends_the_secret_and_the_verifier_as_form_fields(self) -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(dict(parse_qs(request.content.decode())))  # type: ignore[arg-type]
            return httpx.Response(200, json={"id_token": "a.b.c"})

        client = oidc.HttpIdpClient(transport=_transport(handler))
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

        client = oidc.HttpIdpClient(transport=_transport(handler))
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

        client = oidc.HttpIdpClient(transport=_transport(handler))
        with pytest.raises(UpstreamUnavailableProblem):
            await client.fetch_jwks(JWKS_URI)
        await client.aclose()

    async def test_a_token_response_with_no_id_token_is_refused(self) -> None:
        """A plain OAuth2 response is not OIDC. Refusing beats inventing an identity."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"access_token": "at", "token_type": "Bearer"})

        client = oidc.HttpIdpClient(transport=_transport(handler))
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

        client = oidc.HttpIdpClient(transport=_transport(handler))
        with pytest.raises(UpstreamUnavailableProblem):
            await client.discover(f"{ISSUER}/.well-known/openid-configuration")
        await client.aclose()


# --------------------------------------------------------------------- administration


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
