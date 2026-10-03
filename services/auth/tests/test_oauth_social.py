"""Self-serve social sign-in: `GET /auth/oauth/{provider}/start`.

The platform-wide sibling of `test_sso.py`'s enterprise flow. Where SSO resolves a
provider from a DNS-verified domain and an organization's own `OrgIdpConfig`, this starts
from a fixed platform registration (`PlatformOAuthProvider`, Task 1) that exists before any
organization does — there is no domain, no tenant, and nothing to bind. `oauth_social_start`
is the one function this file exercises today; later tasks in this plan (6 through 9) append
the callback, provisioning and linking tests to this same file, so the fixtures and helpers
below are written to be reused rather than duplicated.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import oidc, services
from app.config import Settings, settings
from app.directory import DirectoryUser
from app.keys import KeyPair
from app.models import ExternalIdentity, PlatformOAuthProvider, Session
from app.provisioning import FakeProvisioningClient
from platform_core import pii
from platform_core.errors import NotFoundProblem, ValidationProblem
from tests.conftest import (
    ORG_A,
    ORG_B,
    FakeDirectory,
    FakeEntitlements,
    FakeRedis,
    membership,
    user_in_two_orgs,
)
from tests.fake_idp import FakeIdp

pytestmark = pytest.mark.integration

#: `oauth_social_callback` never calls `idp.discover()` — the provider's endpoints come from
#: the `PlatformOAuthProvider` row, already pinned at registration time
#: (`bin/seed-oauth-providers`), not rediscovered on every sign-in — so these two values only
#: ever have to match each other:
#: the row seeded by `_seed_google_provider` below and the `issuer`/`audience` passed to
#: `FakeIdp.issue_id_token` when a test mints its ID token. They do not need to match `FakeIdp`'s
#: own `ISSUER`/`CLIENT_ID` module constants, which matter only to `fetch_jwks`/`exchange_code` —
#: and those two ignore the URL/id arguments they are given entirely (see `tests/fake_idp.py`).
GOOGLE_ISSUER = "https://accounts.google.com"
GOOGLE_CLIENT_ID = "client-123"


@pytest_asyncio.fixture
async def store(redis: FakeRedis) -> oidc.StateStore:
    """Mirrors `test_sso.py`'s own `store` fixture — not shared through `conftest.py`
    because `StateStore` wraps whichever Redis stand-in a module's fixtures hand it, and
    this file has no dependency on single sign-on's."""
    return oidc.StateStore(redis)


@pytest_asyncio.fixture
async def idp() -> FakeIdp:
    """The same fake `test_sso.py` uses for enterprise SSO (`tests/fake_idp.py`). Nothing
    about it is SSO-specific — `issue_id_token`'s `issuer`/`audience` parameters, and the
    generic `discover`/`fetch_jwks`/`exchange_code` `IdpClient` implementation, already serve
    a platform OAuth provider's shape as well as an organization's own OIDC one. See
    `_seed_google_provider` for the one thing a test does have to keep in sync: the
    `PlatformOAuthProvider` row's `issuer`/`client_id` and the token's `issuer`/`audience`."""
    return FakeIdp()


async def _seed_google_provider(session: AsyncSession, cfg: Settings | None = None) -> None:
    """Seed the platform's Google registration.

    `cfg` is optional and only needed by callback tests: `oauth_social_callback` opens
    `client_secret_enc` with `pii.open_sealed` before it ever exchanges a code, so a test that
    reaches that far needs a *validly sealed* secret in the row, not the unset column the
    existing `oauth_social_start` tests above were content to leave untouched (that flow never
    opens the secret at all).
    """
    client_secret_enc = None
    if cfg is not None:
        client_secret_enc, _ = pii.seal("google-test-client-secret", kind="generic", settings=cfg)
    session.add(
        PlatformOAuthProvider(
            provider="google",
            enabled=True,
            client_id=GOOGLE_CLIENT_ID,
            client_secret_enc=client_secret_enc,
            issuer=GOOGLE_ISSUER,
            authorization_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
            token_endpoint="https://oauth2.googleapis.com/token",
            jwks_uri="https://www.googleapis.com/oauth2/v3/certs",
        )
    )
    await session.flush()


def _pending_google_request(
    *, nonce: str, intent: str = "signup"
) -> oidc.SocialAuthorizationRequest:
    return oidc.SocialAuthorizationRequest(
        provider="google",
        intent=intent,
        invitation_token=None,
        nonce=nonce,
        code_verifier=oidc.new_verifier(),
        created_at=oidc.now_iso(),
    )


class TestOauthSocialStart:
    async def test_returns_authorization_url(
        self, session: AsyncSession, cfg: Settings, store: oidc.StateStore
    ) -> None:
        await _seed_google_provider(session)
        redirect = await services.oauth_social_start(
            session, cfg=cfg, store=store, provider="google", intent="signup"
        )
        assert redirect.authorization_url.startswith(
            "https://accounts.google.com/o/oauth2/v2/auth?"
        )
        assert "client-123" in redirect.authorization_url
        assert redirect.state
        assert redirect.expires_in == cfg.sso_state_ttl_seconds

    async def test_refuses_a_disabled_provider(
        self, session: AsyncSession, cfg: Settings, store: oidc.StateStore
    ) -> None:
        session.add(PlatformOAuthProvider(provider="microsoft", enabled=False))
        await session.flush()
        with pytest.raises(NotFoundProblem):
            await services.oauth_social_start(
                session, cfg=cfg, store=store, provider="microsoft", intent="signup"
            )

    async def test_refuses_a_provider_with_no_row_at_all(
        self, session: AsyncSession, cfg: Settings, store: oidc.StateStore
    ) -> None:
        with pytest.raises(NotFoundProblem):
            await services.oauth_social_start(
                session, cfg=cfg, store=store, provider="google", intent="signup"
            )

    async def test_refuses_a_provider_outside_the_known_set(
        self, session: AsyncSession, cfg: Settings, store: oidc.StateStore
    ) -> None:
        """Not just "disabled or missing" — a name that is not `google`/`microsoft` at all
        must not even reach a lookup, the same posture `OAUTH_PROVIDERS` exists to enforce."""
        with pytest.raises(NotFoundProblem):
            await services.oauth_social_start(
                session, cfg=cfg, store=store, provider="facebook", intent="signup"
            )

    async def test_refuses_an_invalid_intent(
        self, session: AsyncSession, cfg: Settings, store: oidc.StateStore
    ) -> None:
        await _seed_google_provider(session)
        with pytest.raises(ValidationProblem):
            await services.oauth_social_start(
                session, cfg=cfg, store=store, provider="google", intent="not-a-real-intent"
            )

    async def test_an_invite_intent_requires_an_invitation_token(
        self, session: AsyncSession, cfg: Settings, store: oidc.StateStore
    ) -> None:
        await _seed_google_provider(session)
        with pytest.raises(ValidationProblem):
            await services.oauth_social_start(
                session, cfg=cfg, store=store, provider="google", intent="invite"
            )

    async def test_an_invite_intent_with_a_token_succeeds(
        self, session: AsyncSession, cfg: Settings, store: oidc.StateStore
    ) -> None:
        await _seed_google_provider(session)
        redirect = await services.oauth_social_start(
            session,
            cfg=cfg,
            store=store,
            provider="google",
            intent="invite",
            invitation_token="inv-token-123",
        )
        assert redirect.authorization_url

    async def test_the_pending_request_round_trips_through_the_state_store(
        self, session: AsyncSession, cfg: Settings, store: oidc.StateStore
    ) -> None:
        """`SocialAuthorizationRequest` is a second pending-request shape sharing the SSO
        state store's single-use mechanism (Task 3's `consume(state, parse)`) — proved here
        directly, the same way `test_sso.py::test_state_store_consume_takes_explicit_parser`
        proves it for `AuthorizationRequest`."""
        await _seed_google_provider(session)
        redirect = await services.oauth_social_start(
            session, cfg=cfg, store=store, provider="google", intent="signup"
        )
        pending = await store.consume(redirect.state, oidc.SocialAuthorizationRequest.from_json)
        assert pending.provider == "google"
        assert pending.intent == "signup"
        assert pending.invitation_token is None


class TestOauthSocialStartRoute:
    async def test_route_returns_the_authorization_url(self, client, session: AsyncSession) -> None:
        await _seed_google_provider(session)
        response = await client.get("/api/v1/auth/oauth/google/start", params={"intent": "signup"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["authorization_url"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")
        assert body["state"]
        assert body["expires_in"] == settings().sso_state_ttl_seconds

    async def test_route_404s_for_an_unknown_provider(self, client, session: AsyncSession) -> None:
        response = await client.get(
            "/api/v1/auth/oauth/facebook/start", params={"intent": "signup"}
        )
        assert response.status_code == 404, response.text

    async def test_route_rejects_an_invalid_intent_at_the_edge(
        self, client, session: AsyncSession
    ) -> None:
        await _seed_google_provider(session)
        response = await client.get(
            "/api/v1/auth/oauth/google/start", params={"intent": "whatever"}
        )
        assert response.status_code == 422, response.text


class TestResolveOrProvisionIdentity:
    """`_resolve_or_provision_identity` (Task 6) — the `intent == "signup"` path, run when
    `directory.find_by_email` comes back empty. The returning-user short circuit above it and
    the `invite` branch below it get their own dedicated, adversarial coverage in Task 7; this
    class only has to prove the happy path this task adds actually provisions a workspace.

    No `session`/`client`/`store` fixtures here — `_resolve_or_provision_identity` is pure
    orchestration over `Directory`/`ProvisioningClient`, nothing in it touches the database.
    """

    async def test_resolve_or_provision_creates_new_workspace(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        directory = FakeDirectory([])
        provisioning = FakeProvisioningClient()

        # `FakeDirectory` and `FakeProvisioningClient` are independent fakes (app/provisioning.py's
        # own docstring says so) — nothing wires them together the way account-service's signup
        # endpoint and its directory-read endpoint, two views onto the same row, are wired in
        # reality. `_resolve_or_provision_identity` depends on telling two moments apart: no row
        # yet when `find_by_email` is asked (hence `directory` starts empty, and must stay that
        # way for "new@example.com" or the function would short-circuit on the returning-user
        # path before ever calling `signup_federated`), and the row existing by the time it calls
        # `directory.get(provisioned.user_id)` afterward. A plain upfront seed of `directory.users`
        # cannot model both moments at once — `find_by_email` and `get` read the very same list —
        # so `get` is pointed at the row `FakeProvisioningClient` is about to mint instead, the
        # same way `tests/conftest.py`'s `membership(...)` builds the rest of the shape.
        provisioned_user = DirectoryUser(
            user_id=provisioning._next_user_id,
            email="new@example.com",
            full_name="New Person",
            is_active=True,
            memberships=[membership(provisioning._next_org_id, uuid.uuid4(), "new-co", "New Co")],
        )

        async def get(user_id: uuid.UUID) -> DirectoryUser | None:
            return provisioned_user if user_id == provisioned_user.user_id else None

        monkeypatch.setattr(directory, "get", get)

        identity = oidc.VerifiedIdentity(subject="google-sub-1", email="new@example.com")
        user = await services._resolve_or_provision_identity(
            directory=directory,
            provisioning=provisioning,
            intent="signup",
            invitation_token=None,
            identity=identity,
            full_name_hint="New Person",
            organization_name="New Co",
            slug="new-co",
        )

        assert user.email == "new@example.com"
        assert user.user_id == provisioning._next_user_id
        assert (
            "signup_federated",
            {"email": "new@example.com", "slug": "new-co"},
        ) in provisioning.calls


class TestResolveOrProvisionIdentityInvite:
    """`_resolve_or_provision_identity`'s `else` (invite) branch (Task 6) — the adversarial
    email-match case and the happy path, the two cases the signup-focused class above
    deliberately left out.

    The mismatch test is the important one: `FakeProvisioningClient.accept_invitation_federated`
    (Task 4) is where the email check actually lives, and it must refuse a mismatch the same
    opaque way it refuses an unknown or revoked token — never a distinct message that would let
    an attacker learn *which* part of their guess was wrong (CLAUDE.md's "authorization is
    server-side" posture applied to an error message rather than a query).
    """

    async def test_resolve_or_provision_invite_rejects_email_mismatch(self) -> None:
        directory = FakeDirectory([])
        provisioning = FakeProvisioningClient()
        org_id, user_id = uuid.uuid4(), uuid.uuid4()
        provisioning.invitations["tok-1"] = ("invitee@acme.com", org_id, user_id, False)

        async def resolve(*, token: str, email: str) -> None:
            await services._resolve_or_provision_identity(
                directory=directory,
                provisioning=provisioning,
                intent="invite",
                invitation_token=token,
                identity=oidc.VerifiedIdentity(subject="google-sub-x", email=email),
                full_name_hint="Attacker",
                organization_name=None,
                slug=None,
            )

        with pytest.raises(NotFoundProblem) as mismatched:
            await resolve(token="tok-1", email="attacker@evil.com")

        # Same opaque refusal as an unknown token entirely — proving the message does not
        # enumerate *why* ("wrong email" vs "no such invitation"), just as `test_sso.py`
        # proves equivalent SSO failures share one message (`unknown.value.detail ==
        # unverified.value.detail`).
        with pytest.raises(NotFoundProblem) as unknown_token:
            await resolve(token="does-not-exist", email="attacker@evil.com")

        assert mismatched.value.detail == unknown_token.value.detail
        assert mismatched.value.detail == "That invitation is not valid"
        assert mismatched.value.status_code == 404

        # The mismatch was actually exercised, not short-circuited by a missing token lookup:
        # `accept_invitation_federated` was called with the attacker's (wrong) email against the
        # real invitation token.
        assert (
            "accept_invitation_federated",
            {"token": "tok-1", "email": "attacker@evil.com"},
        ) in provisioning.calls

        # `directory` never had to answer anything — the refusal happens inside provisioning,
        # before `_resolve_or_provision_identity` would ever reach `directory.get`.
        assert directory.users == []

    async def test_resolve_or_provision_invite_happy_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        directory = FakeDirectory([])
        provisioning = FakeProvisioningClient()
        org_id, user_id = uuid.uuid4(), uuid.uuid4()
        provisioning.invitations["tok-2"] = ("invitee@acme.com", org_id, user_id, False)

        # Same fixture-design issue Task 6 hit and resolved (see that task's test above):
        # `FakeDirectory.find_by_email` and `.get` share one backing list, so pre-seeding a user
        # at `user_id` with this email would make `find_by_email` return it immediately and the
        # invite branch — `accept_invitation_federated` — would never run. `directory` therefore
        # starts empty, and `.get` alone is patched to resolve the row that would exist *after*
        # `accept_invitation_federated` succeeds, strictly keyed on the provisioned `user_id` so
        # a wrong id threaded through the implementation would resolve to `None` (and surface as
        # `UpstreamUnavailableProblem`) rather than silently passing.
        provisioned_user = DirectoryUser(
            user_id=user_id,
            email="invitee@acme.com",
            full_name="Invitee",
            is_active=True,
            memberships=[membership(org_id, uuid.uuid4(), "acme", "Acme Technologies")],
        )

        async def get(looked_up_user_id: uuid.UUID) -> DirectoryUser | None:
            return provisioned_user if looked_up_user_id == user_id else None

        monkeypatch.setattr(directory, "get", get)

        identity = oidc.VerifiedIdentity(subject="google-sub-3", email="invitee@acme.com")
        user = await services._resolve_or_provision_identity(
            directory=directory,
            provisioning=provisioning,
            intent="invite",
            invitation_token="tok-2",
            identity=identity,
            full_name_hint="Invitee",
            organization_name=None,
            slug=None,
        )

        assert user.user_id == user_id
        assert user.email == "invitee@acme.com"
        assert (
            "accept_invitation_federated",
            {"token": "tok-2", "email": "invitee@acme.com"},
        ) in provisioning.calls


class TestOauthSocialCallback:
    """`oauth_social_callback` (Task 8) — the central, security-sensitive piece of this plan.

    Modelled on `sso_callback`'s own verify/link/mint sequence, with one deliberate
    architectural difference pinned by `test_session_is_not_federation_pinned` below: no
    domain binding and no organization pin, because this flow proves a *person*, not an
    organization's own verified domain.
    """

    async def test_signs_in_an_existing_user(
        self,
        session: AsyncSession,
        cfg: Settings,
        pair: KeyPair,
        store: oidc.StateStore,
        directory: FakeDirectory,
        entitlements: FakeEntitlements,
        idp: FakeIdp,
    ) -> None:
        """The simplest path: `directory` (the shared fixture) already knows
        `sam@example.com` with one active membership, so `_resolve_or_provision_identity`
        takes its returning-user short circuit and never touches `provisioning` at all."""
        await _seed_google_provider(session, cfg)
        state = await store.issue(_pending_google_request(nonce="n1"), ttl_seconds=600)
        idp.next_id_token = idp.issue_id_token(
            nonce="n1", email="sam@example.com", issuer=GOOGLE_ISSUER, audience=GOOGLE_CLIENT_ID
        )
        provisioning = FakeProvisioningClient()

        outcome = await services.oauth_social_callback(
            session,
            cfg=cfg,
            pair=pair,
            directory=directory,
            provisioning=provisioning,
            entitlements=entitlements,
            idp=idp,
            store=store,
            provider="google",
            code="irrelevant-fake-code",
            state=state,
        )

        assert outcome.status == "complete"
        assert outcome.issued is not None
        assert outcome.issued.organization_id == ORG_A
        # The returning-user short circuit really ran: nothing was provisioned.
        assert provisioning.calls == []

        # `idp` is `oauth:<provider>` on the minted session row itself — not just something
        # `_federated_organization_pin` is trusted to interpret correctly in isolation (that is
        # the separate regression test below). Read back from the database, not from a value
        # this test constructed, so a typo'd literal in the implementation (`oidc:google`,
        # `oauth-google`, the bare provider name) would be caught here.
        row = await session.get(Session, outcome.issued.session_id)
        assert row is not None
        assert row.idp == "oauth:google"
        assert row.amr == [services.OAUTH_AMR]
        assert row.sso_domain is None

        # `_link_external_identity` is reused as-is (not reimplemented): the provider recorded
        # on the link is the bare provider name, exactly as `sso_callback` passes its own
        # (differently-namespaced) `provider` string through to the same helper.
        linked = (
            await session.execute(
                select(ExternalIdentity).where(ExternalIdentity.provider == "google")
            )
        ).scalar_one_or_none()
        assert linked is not None
        assert linked.provider == "google"
        assert linked.email_at_link == "sam@example.com"

    #: Same idiom as `test_sso.py::TestNonAsciiIdentityIsRefusedOnTheRawString` — built from
    #: the code point, not a literal glyph, so no editor (and no linter's confusable-character
    #: check) can flatten it to ASCII `K` and silently defeat the point of the test.
    KELVIN = chr(0x212A)  # U+212A, KELVIN SIGN

    async def test_refuses_a_non_ascii_asserted_identity(
        self,
        session: AsyncSession,
        cfg: Settings,
        pair: KeyPair,
        store: oidc.StateStore,
        entitlements: FakeEntitlements,
        idp: FakeIdp,
    ) -> None:
        """Same ASCII hardening `sso_callback` applies, exercised here with a real token: the
        same Kelvin sign that function's own comment documents (NFKC-normalises to plain `K`,
        then folds to `k` under `lower()` and could match a real user's all-ASCII address) in
        place of the ordinary `s` is exactly the class of mismatch the check exists to close.
        `directory` starts empty — the refusal fires before `_resolve_or_provision_identity`
        is ever reached, so nothing should need it."""
        await _seed_google_provider(session, cfg)
        state = await store.issue(_pending_google_request(nonce="n2"), ttl_seconds=600)
        idp.next_id_token = idp.issue_id_token(
            nonce="n2",
            email=f"{self.KELVIN}am@example.com",
            issuer=GOOGLE_ISSUER,
            audience=GOOGLE_CLIENT_ID,
        )

        with pytest.raises(oidc.SsoExchangeProblem) as excinfo:
            await services.oauth_social_callback(
                session,
                cfg=cfg,
                pair=pair,
                directory=FakeDirectory([]),
                provisioning=FakeProvisioningClient(),
                entitlements=entitlements,
                idp=idp,
                store=store,
                provider="google",
                code="irrelevant-fake-code",
                state=state,
            )

        assert excinfo.value.detail == "That sign-in could not be completed. Please start again."

        # Confirms the refusal really happened before any directory read — not merely that
        # the right exception type came back for some other reason.
        assert (await session.execute(select(ExternalIdentity))).scalar_one_or_none() is None

    async def test_requires_organization_selection_for_a_multi_org_user(
        self,
        session: AsyncSession,
        cfg: Settings,
        pair: KeyPair,
        store: oidc.StateStore,
        entitlements: FakeEntitlements,
        idp: FakeIdp,
    ) -> None:
        """`resolve_organization` returns `None` for anything but exactly one active
        membership (`services.py`'s own doc on that function) — a multi-organization person
        signing in through this flow for the first time is exactly that ambiguity, built
        cheaply from the existing `user_in_two_orgs` fixture helper rather than a fresh one."""
        await _seed_google_provider(session, cfg)
        directory = FakeDirectory([user_in_two_orgs()])
        state = await store.issue(_pending_google_request(nonce="n3"), ttl_seconds=600)
        idp.next_id_token = idp.issue_id_token(
            nonce="n3", email="sam@example.com", issuer=GOOGLE_ISSUER, audience=GOOGLE_CLIENT_ID
        )

        outcome = await services.oauth_social_callback(
            session,
            cfg=cfg,
            pair=pair,
            directory=directory,
            provisioning=FakeProvisioningClient(),
            entitlements=entitlements,
            idp=idp,
            store=store,
            provider="google",
            code="irrelevant-fake-code",
            state=state,
        )

        assert outcome.status == "org_selection_required"
        assert outcome.issued is None
        assert outcome.pre_auth_token is not None
        assert outcome.organizations is not None
        assert {m.organization_id for m in outcome.organizations} == {ORG_A, ORG_B}


def test_oauth_session_is_not_federation_pinned() -> None:
    """Pins the one deliberate architectural difference from enterprise SSO (spec §6):
    `_federated_organization_pin` recognises only the `oidc:`/`saml:` prefixes
    (`services.py`'s own docstring on that function), so a session minted by
    `oauth_social_callback` — `idp="oauth:<provider>"` — must come back unpinned and free to
    move between every organization the person belongs to, exactly like a password session.

    No database, no fixtures: `_federated_organization_pin` is a pure function of the one
    field it reads, so the regression is pinned directly against a `Session` built in memory
    rather than through the whole callback flow.
    """
    auth_session = Session(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        amr=["oauth"],
        auth_time=datetime.now(UTC),
        idp="oauth:google",
    )
    assert services._federated_organization_pin(auth_session) is None
