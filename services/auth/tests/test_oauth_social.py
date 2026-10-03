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
from urllib.parse import parse_qs, urlparse

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import oidc, security, services
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
    USER,
    FakeDirectory,
    FakeEntitlements,
    FakeRedis,
    membership,
    user_in_one_org,
    user_in_two_orgs,
)
from tests.fake_idp import FakeIdp

pytestmark = pytest.mark.integration


def _nonce_from(authorization_url: str) -> str:
    """The `nonce` query parameter `oauth_social_start` generated — needed only by tests that
    drive `oauth_social_start` for real (most of this file hand-builds a `pending` request
    directly, with a caller-chosen nonce, since the nonce itself is not what they test)."""
    return parse_qs(urlparse(authorization_url).query)["nonce"][0]


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


async def _seed_microsoft_provider(session: AsyncSession, cfg: Settings) -> None:
    """A second, real platform provider row — used only by the provider/state-mismatch test
    (round 2 test-quality fix), so that removing the `pending.provider != provider` guard would
    actually let `oauth_social_callback` reach `idp.exchange_code` for `microsoft`, rather than
    failing one step earlier for the unrelated reason that `microsoft` was never registered at
    all. Always sealed (unlike `_seed_google_provider`'s unsealed default) since the one test
    that uses this seeds `cfg` unconditionally."""
    client_secret_enc, _ = pii.seal("microsoft-test-client-secret", kind="generic", settings=cfg)
    session.add(
        PlatformOAuthProvider(
            provider="microsoft",
            enabled=True,
            client_id="ms-client-456",
            client_secret_enc=client_secret_enc,
            issuer="https://login.microsoftonline.com/common/v2.0",
            authorization_endpoint="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
            token_endpoint="https://login.microsoftonline.com/common/oauth2/v2.0/token",
            jwks_uri="https://login.microsoftonline.com/common/discovery/v2.0/keys",
        )
    )
    await session.flush()


def _pending_google_request(
    *,
    nonce: str,
    intent: str = "signup",
    organization_name: str | None = None,
    slug: str | None = None,
) -> oidc.SocialAuthorizationRequest:
    return oidc.SocialAuthorizationRequest(
        provider="google",
        intent=intent,
        invitation_token=None,
        nonce=nonce,
        code_verifier=oidc.new_verifier(),
        created_at=oidc.now_iso(),
        organization_name=organization_name,
        slug=slug,
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

    async def test_a_signup_intent_succeeds_at_start_without_organization_details(
        self, session: AsyncSession, cfg: Settings, store: oidc.StateStore
    ) -> None:
        """Important B (round 2): the opposite of what round 1 asserted here. `/start` cannot
        know yet whether the email behind this `intent=signup` redirect belongs to a returning
        user (who needs no organization details at all — `_resolve_or_provision_identity`'s
        email-first lookup finds them immediately) or a genuinely new one (who does, but only
        once that same lookup has actually come back empty, which happens long after this
        function returns). Requiring them here 422'd a returning user before the flow ever
        reached the lookup that would have recognized them — the exact regression round 1
        introduced. See `TestOauthSocialCallback`'s
        `test_a_new_signup_without_organization_details_is_refused_at_the_callback` for where a
        genuine new signup without details is actually (and correctly, just later) refused."""
        await _seed_google_provider(session)
        redirect = await services.oauth_social_start(
            session, cfg=cfg, store=store, provider="google", intent="signup"
        )
        assert redirect.authorization_url

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
            session,
            cfg=cfg,
            store=store,
            provider="google",
            intent="signup",
            organization_name="New Co",
            slug="new-co",
        )
        pending = await store.consume(redirect.state, oidc.SocialAuthorizationRequest.from_json)
        assert pending.provider == "google"
        assert pending.intent == "signup"
        assert pending.invitation_token is None
        assert pending.organization_name == "New Co"
        assert pending.slug == "new-co"


class TestOauthSocialStartRoute:
    async def test_route_returns_the_authorization_url(self, client, session: AsyncSession) -> None:
        """No `organization_name`/`slug` in the request — Important B (round 2): the route
        must accept a bare `intent=signup`, the same shape a returning user's frontend sends,
        without 422ing before the callback ever gets a chance to recognize them."""
        await _seed_google_provider(session)
        response = await client.get("/api/v1/auth/oauth/google/start", params={"intent": "signup"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["authorization_url"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")
        assert body["state"]
        assert body["expires_in"] == settings().sso_state_ttl_seconds

    async def test_route_accepts_organization_details_for_a_fresh_signup(
        self, client, session: AsyncSession
    ) -> None:
        """The other shape the same route serves: a frontend that already expects this to be a
        new signup and collected the workspace details up front. Both this and the test above
        must succeed — neither is the "right" shape, since the route cannot tell which is which."""
        await _seed_google_provider(session)
        response = await client.get(
            "/api/v1/auth/oauth/google/start",
            params={"intent": "signup", "organization_name": "New Co", "slug": "new-co"},
        )
        assert response.status_code == 200, response.text

    async def test_route_404s_for_an_unknown_provider(self, client, session: AsyncSession) -> None:
        response = await client.get(
            "/api/v1/auth/oauth/facebook/start", params={"intent": "signup"}
        )
        assert response.status_code == 404, response.text

    async def test_route_rejects_a_slug_that_is_too_short(
        self, client, session: AsyncSession
    ) -> None:
        """Bundled fix (round 2): a 1-character slug used to survive the whole Google round
        trip and then fail as an unhandled 500 inside `provisioning.py`, after the user's state
        had already been consumed. `min_length=2` matches account-service's own `Slug` type and
        fails fast, at the edge, before any of that."""
        await _seed_google_provider(session)
        response = await client.get(
            "/api/v1/auth/oauth/google/start",
            params={"intent": "signup", "organization_name": "New Co", "slug": "a"},
        )
        assert response.status_code == 422, response.text

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


class _RecordingDirectory(FakeDirectory):
    """Mirrors `test_sso.py`'s own `_RecordingDirectory` — the assertion that matters for a
    gate is not the exception type but that the lookup it guards was never made."""

    def __init__(self, users: list[DirectoryUser]) -> None:
        super().__init__(users)
        self.lookups: list[str] = []

    async def find_by_email(self, email: str) -> DirectoryUser | None:
        self.lookups.append(email)
        return await super().find_by_email(email)


class TestOauthSocialCallback:
    """`oauth_social_callback` (Task 8) — the central, security-sensitive piece of this plan.

    Modelled on `sso_callback`'s own verify/link/mint sequence, with one deliberate
    architectural difference pinned by `test_session_is_not_federation_pinned` below: no
    domain binding and no organization pin, because this flow proves a *person*, not an
    organization's own verified domain. Also checks the resolved account is active, requires
    its second factor if one is enrolled, and resolves by `(provider, subject)` before email —
    the three fixes a dedicated review round added on top of the original implementation.
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
        place of the ordinary `k` is exactly the class of mismatch the check exists to close.

        The real victim ("kate@example.com") is present in the directory — a `FakeDirectory([])`
        would let this pass for the wrong reason (nothing to fold onto even if the gate did not
        fire). `_RecordingDirectory` proves the gate fires *before* any lookup, the same way
        `test_sso.py::TestNonAsciiIdentityIsRefusedOnTheRawString` proves it for `sso_callback`.
        """
        victim = DirectoryUser(
            user_id=uuid.uuid4(),
            email="kate@example.com",
            full_name="Kate",
            is_active=True,
            memberships=[membership(ORG_A, uuid.uuid4(), "acme", "Acme Technologies")],
        )
        directory = _RecordingDirectory([victim])
        await _seed_google_provider(session, cfg)
        state = await store.issue(_pending_google_request(nonce="n2"), ttl_seconds=600)
        idp.next_id_token = idp.issue_id_token(
            nonce="n2",
            email=f"{self.KELVIN}ate@example.com",
            issuer=GOOGLE_ISSUER,
            audience=GOOGLE_CLIENT_ID,
        )

        with pytest.raises(oidc.SsoExchangeProblem) as excinfo:
            await services.oauth_social_callback(
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

        assert excinfo.value.detail == "That sign-in could not be completed. Please start again."

        # The proof that matters: the gate fired before any directory lookup, not merely that
        # the right exception type came back for some other reason.
        assert directory.lookups == []
        assert (await session.execute(select(Session))).scalars().all() == []

    async def test_refuses_when_the_matched_users_stored_email_is_not_ascii(
        self,
        session: AsyncSession,
        cfg: Settings,
        pair: KeyPair,
        store: oidc.StateStore,
        entitlements: FakeEntitlements,
        idp: FakeIdp,
    ) -> None:
        """The mirror image of the gate above, and `test_sso.py`'s own
        `test_a_stored_non_ascii_address_never_matches_an_ascii_assertion`: the *assertion* is
        all-ASCII, but the directory's stored address holds the Kelvin sign, which `lower()`
        folds to plain `k` and so `find_by_email` matches it to an all-ASCII assertion. Gate 2
        — "the matched user's own stored email must itself be ASCII" — is what refuses this one,
        not gate 1, since the identity token here is unremarkable."""
        kate = DirectoryUser(
            user_id=uuid.uuid4(),
            email=f"{self.KELVIN}ate@example.com",
            full_name="Kate",
            is_active=True,
            memberships=[membership(ORG_A, uuid.uuid4(), "acme", "Acme Technologies")],
        )
        directory = FakeDirectory([kate])
        await _seed_google_provider(session, cfg)
        state = await store.issue(_pending_google_request(nonce="n2b"), ttl_seconds=600)
        idp.next_id_token = idp.issue_id_token(
            nonce="n2b", email="kate@example.com", issuer=GOOGLE_ISSUER, audience=GOOGLE_CLIENT_ID
        )

        with pytest.raises(oidc.SsoExchangeProblem) as excinfo:
            await services.oauth_social_callback(
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

        assert excinfo.value.detail == "That sign-in could not be completed. Please start again."
        assert (await session.execute(select(Session))).scalars().all() == []

    async def test_refuses_when_the_states_provider_does_not_match_the_route(
        self,
        session: AsyncSession,
        cfg: Settings,
        pair: KeyPair,
        store: oidc.StateStore,
        entitlements: FakeEntitlements,
        idp: FakeIdp,
    ) -> None:
        """A state issued for `google` presented at the `microsoft` callback is refused before
        anything is attempted against the real identity provider — `idp.exchanges` stays empty,
        proving the check runs ahead of `exchange_code`, not merely that some later step
        happened to fail too.

        **Round 2 test-quality fix:** a Microsoft row is seeded too, deliberately — without one,
        this test has no teeth, since `exchange_code` is never reached anyway once `microsoft`
        turns out not to be registered at all (`_get_enabled_provider` raises `NotFoundProblem`
        first), so `idp.exchanges == []` would hold whether or not the `pending.provider !=
        provider` check exists. With a real, enabled Microsoft row, removing that check would
        let execution reach `exchange_code` for real — the assertion below only has something
        to prove because of this seed.
        """
        await _seed_google_provider(session, cfg)
        await _seed_microsoft_provider(session, cfg)
        state = await store.issue(_pending_google_request(nonce="n2c"), ttl_seconds=600)

        with pytest.raises(oidc.SsoExchangeProblem):
            await services.oauth_social_callback(
                session,
                cfg=cfg,
                pair=pair,
                directory=FakeDirectory([]),
                provisioning=FakeProvisioningClient(),
                entitlements=entitlements,
                idp=idp,
                store=store,
                provider="microsoft",
                code="irrelevant-fake-code",
                state=state,
            )

        assert idp.exchanges == []

    async def test_refuses_a_deactivated_user(
        self,
        session: AsyncSession,
        cfg: Settings,
        pair: KeyPair,
        store: oidc.StateStore,
        entitlements: FakeEntitlements,
        idp: FakeIdp,
    ) -> None:
        """Critical #1: every other session-minting path (`_verify_credentials`,
        `sso_callback`, `switch_organization`, `rotate_refresh`) refuses a deactivated account;
        `oauth_social_callback` must too, whichever branch resolved it."""
        deactivated = user_in_one_org()
        directory = FakeDirectory(
            [
                DirectoryUser(
                    user_id=deactivated.user_id,
                    email=deactivated.email,
                    full_name=deactivated.full_name,
                    is_active=False,
                    memberships=deactivated.memberships,
                )
            ]
        )
        await _seed_google_provider(session, cfg)
        state = await store.issue(_pending_google_request(nonce="n2d"), ttl_seconds=600)
        idp.next_id_token = idp.issue_id_token(
            nonce="n2d", email="sam@example.com", issuer=GOOGLE_ISSUER, audience=GOOGLE_CLIENT_ID
        )

        with pytest.raises(oidc.SsoExchangeProblem) as excinfo:
            await services.oauth_social_callback(
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

        assert excinfo.value.detail == "That sign-in could not be completed. Please start again."
        assert (await session.execute(select(Session))).scalars().all() == []

    async def test_requires_mfa_for_an_mfa_enrolled_account(
        self,
        session: AsyncSession,
        cfg: Settings,
        pair: KeyPair,
        store: oidc.StateStore,
        directory: FakeDirectory,
        entitlements: FakeEntitlements,
        idp: FakeIdp,
    ) -> None:
        """Critical #2: an MFA-enrolled account must not become reachable through a door that
        skips its second factor. Same check `login()` runs, same outcome shape — a pre-auth
        token for the existing `/auth/mfa/verify` route, not a new flow.

        **Round 2 test-quality fix:** round 1's version stopped at the `LoginOutcome` shape
        (`status="mfa_required"`), which proves the branch was taken but not that the token it
        hands back is the *real* thing. This version enrols a real TOTP factor the same way
        `test_signin.py` does (`begin_mfa_enrolment`/`confirm_mfa_enrolment`,
        `security._totp_at`) and redeems the pre-auth token through the actual `verify_mfa` —
        the same function `/auth/mfa/verify` calls — confirming it completes the sign-in and
        that the minted session's `amr` carries both factors.
        """
        factor, secret, _ = await services.begin_mfa_enrolment(
            session, USER, account_email="sam@example.com", cfg=cfg
        )
        step = int(datetime.now(UTC).timestamp()) // 30
        await services.confirm_mfa_enrolment(
            session,
            factor_id=factor.id,
            user_id=USER,
            code=security._totp_at(secret, step),
            cfg=cfg,
        )

        await _seed_google_provider(session, cfg)
        state = await store.issue(_pending_google_request(nonce="n2e"), ttl_seconds=600)
        idp.next_id_token = idp.issue_id_token(
            nonce="n2e", email="sam@example.com", issuer=GOOGLE_ISSUER, audience=GOOGLE_CLIENT_ID
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

        assert outcome.status == "mfa_required"
        assert outcome.pre_auth_token is not None
        assert outcome.issued is None

        # Redeem it through the real machinery. A later step than confirmation consumed, the
        # same replay-protection reasoning `test_signin.py`'s own equivalent assertion uses.
        done = await services.verify_mfa(
            session,
            cfg=cfg,
            pair=pair,
            directory=directory,
            entitlements=entitlements,
            pre_auth_token=outcome.pre_auth_token,
            code=security._totp_at(secret, step + 1),
        )
        assert done.status == "complete"
        assert done.issued is not None
        row = await session.get(Session, done.issued.session_id)
        assert row is not None
        assert row.amr == ["oauth", "otp"]

    async def test_resolves_by_subject_before_email_to_avoid_orphaning_a_new_signup(
        self,
        session: AsyncSession,
        cfg: Settings,
        pair: KeyPair,
        store: oidc.StateStore,
        entitlements: FakeEntitlements,
        idp: FakeIdp,
    ) -> None:
        """Important #3: an already-linked subject is resolved directly — never through
        `_resolve_or_provision_identity`'s email-first lookup — so a Google account's email
        changing after it was linked cannot cause a *second*, orphaned workspace to be created
        for the "new" address before the subject conflict would otherwise be discovered. The
        sign-in is still refused here (gate 2: the directory's stored address no longer matches
        what Google now asserts), but refused *before* anything was provisioned — that's the
        property this test exists to pin, not the refusal itself.

        **Round 2 test-quality fix:** the pending request now carries real `organization_name`/
        `slug` — round 1's version left them `None`, which (after Important B's fix dropped the
        `/start`-time requirement) only proved that an *unrelated* `ValidationProblem` fires for
        missing org details, never reaching the subject-conflict logic this test exists to
        prove at all. With real org details present, a reverted subject-first fix would actually
        call `provisioning.signup_federated(...)` for "new@example.com" — which is exactly what
        `provisioning.calls == []` below is positioned to catch.
        """
        existing_user = DirectoryUser(
            user_id=uuid.uuid4(),
            email="old@example.com",
            full_name="Pat",
            is_active=True,
            memberships=[membership(ORG_A, uuid.uuid4(), "acme", "Acme Technologies")],
        )
        directory = FakeDirectory([existing_user])
        session.add(
            ExternalIdentity(
                user_id=existing_user.user_id,
                provider="google",
                subject="google-sub-orphan-test",
                email_at_link="old@example.com",
            )
        )
        await session.flush()

        await _seed_google_provider(session, cfg)
        state = await store.issue(
            _pending_google_request(nonce="n2f", organization_name="New Co", slug="new-co"),
            ttl_seconds=600,
        )
        idp.next_id_token = idp.issue_id_token(
            nonce="n2f",
            email="new@example.com",
            subject="google-sub-orphan-test",
            issuer=GOOGLE_ISSUER,
            audience=GOOGLE_CLIENT_ID,
        )
        provisioning = FakeProvisioningClient()

        with pytest.raises(oidc.SsoExchangeProblem):
            await services.oauth_social_callback(
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

        # The orphan this fix exists to prevent: no new organization was ever provisioned for
        # "new@example.com", even though email-first resolution would have found no existing
        # user at that address and tried to.
        assert provisioning.calls == []

    async def test_subject_first_lookup_skips_the_email_lookup_entirely(
        self,
        session: AsyncSession,
        cfg: Settings,
        pair: KeyPair,
        store: oidc.StateStore,
        entitlements: FakeEntitlements,
        idp: FakeIdp,
    ) -> None:
        """Round 2 test-quality gap: the orphan-avoidance test above proves a *consequence* of
        subject-first resolution; this proves the mechanism itself actually fires for the
        ordinary case — a returning, already-linked user — rather than merely happening not to
        break anything if `directory.find_by_email` were called anyway. `_RecordingDirectory`
        (used elsewhere in this file for the identical reason against `sso_callback`'s own
        gate) proves the negative directly: the lookup is never made at all."""
        existing_user = DirectoryUser(
            user_id=uuid.uuid4(),
            email="pat@example.com",
            full_name="Pat",
            is_active=True,
            memberships=[membership(ORG_A, uuid.uuid4(), "acme", "Acme Technologies")],
        )
        directory = _RecordingDirectory([existing_user])
        session.add(
            ExternalIdentity(
                user_id=existing_user.user_id,
                provider="google",
                subject="google-sub-returning-link",
                email_at_link="pat@example.com",
            )
        )
        await session.flush()

        await _seed_google_provider(session, cfg)
        state = await store.issue(_pending_google_request(nonce="n2h"), ttl_seconds=600)
        idp.next_id_token = idp.issue_id_token(
            nonce="n2h",
            email="pat@example.com",
            subject="google-sub-returning-link",
            issuer=GOOGLE_ISSUER,
            audience=GOOGLE_CLIENT_ID,
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

        assert outcome.status == "complete"
        assert directory.lookups == []

    async def test_a_new_signup_without_organization_details_is_refused_at_the_callback(
        self,
        session: AsyncSession,
        cfg: Settings,
        pair: KeyPair,
        store: oidc.StateStore,
        entitlements: FakeEntitlements,
        idp: FakeIdp,
    ) -> None:
        """Important B's counterpart: `oauth_social_start` no longer requires organization
        details for `intent=signup` (a returning user needs none at all — see
        `TestOauthSocialStart::test_a_signup_intent_succeeds_at_start_without_organization_details`),
        so a genuine new signup that omitted them is instead refused here, inside
        `_resolve_or_provision_identity` (Task 6's own validation, unchanged), once
        `find_by_email` has actually come back empty. Later than before, but still a clear
        refusal — and not a case a correctly-built frontend ever reaches, since it collects
        these before ever redirecting to Google. Thin on purpose: Task 6/7 already cover
        `_resolve_or_provision_identity`'s own validation at the function-unit level; this only
        has to prove the callback actually reaches it."""
        await _seed_google_provider(session, cfg)
        state = await store.issue(_pending_google_request(nonce="n2i"), ttl_seconds=600)
        idp.next_id_token = idp.issue_id_token(
            nonce="n2i",
            email="brand-new@example.com",
            issuer=GOOGLE_ISSUER,
            audience=GOOGLE_CLIENT_ID,
        )

        with pytest.raises(ValidationProblem):
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

    async def test_a_returning_user_signs_in_through_signup_intent_with_no_organization_details(
        self,
        session: AsyncSession,
        cfg: Settings,
        pair: KeyPair,
        store: oidc.StateStore,
        directory: FakeDirectory,
        entitlements: FakeEntitlements,
        idp: FakeIdp,
    ) -> None:
        """Important B's own re-verification, end to end: `oauth_social_start` driven for
        real (not hand-built, unlike this class's other pending requests) with a bare
        `intent=signup` and no organization details, for the shared `directory` fixture's
        returning `sam@example.com` — the whole point of one button that resolves by email,
        proved all the way through to a minted session rather than merely asserted of
        `oauth_social_start` in isolation."""
        await _seed_google_provider(session, cfg)
        redirect = await services.oauth_social_start(
            session, cfg=cfg, store=store, provider="google", intent="signup"
        )
        idp.next_id_token = idp.issue_id_token(
            nonce=_nonce_from(redirect.authorization_url),
            email="sam@example.com",
            issuer=GOOGLE_ISSUER,
            audience=GOOGLE_CLIENT_ID,
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
            state=redirect.state,
        )

        assert outcome.status == "complete"
        assert outcome.issued is not None

    async def test_a_fresh_signup_with_organization_details_succeeds(
        self,
        session: AsyncSession,
        cfg: Settings,
        pair: KeyPair,
        store: oidc.StateStore,
        entitlements: FakeEntitlements,
        idp: FakeIdp,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Important #4: closes the "no end-to-end fresh-signup test" gap and proves the new
        `organization_name`/`slug` pass-through actually reaches `_resolve_or_provision_identity`
        — before this fix, every real `intent=signup` on an unknown email 422'd inside that
        function for lack of a workspace name/slug `oauth_social_start` never collected."""
        await _seed_google_provider(session, cfg)
        directory = FakeDirectory([])
        provisioning = FakeProvisioningClient()

        pending = oidc.SocialAuthorizationRequest(
            provider="google",
            intent="signup",
            invitation_token=None,
            nonce="n2g",
            code_verifier=oidc.new_verifier(),
            created_at=oidc.now_iso(),
            organization_name="New Co",
            slug="new-co",
        )
        state = await store.issue(pending, ttl_seconds=600)
        idp.next_id_token = idp.issue_id_token(
            nonce="n2g", email="new@example.com", issuer=GOOGLE_ISSUER, audience=GOOGLE_CLIENT_ID
        )

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
        assert (
            "signup_federated",
            {"email": "new@example.com", "slug": "new-co"},
        ) in provisioning.calls

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

    **Important #5 fix:** asserting `_federated_organization_pin(...) is None` alone passes
    vacuously whenever `active_org_id` is left `None` (the dataclass default) — it would keep
    passing even after a prefix-widening regression that started recognising `"oauth"`, or an
    unrelated fail-closed-pin refactor, because neither of those would touch what this bare
    assertion actually reads. `active_org_id` is set to a real (random) organization id, and the
    property that matters — this session is free to mint for an *entirely different* random
    organization without being refused — is pinned directly against
    `_assert_organization_unpinned_or_matches`, the function every minting path actually calls
    to enforce (or not enforce) a pin; it is exercised here as a bare call precisely because
    "does not raise" is the thing under test.

    No database, no fixtures: both functions are pure over the one `Session` field they read, so
    the regression is pinned directly against a `Session` built in memory rather than through
    the whole callback flow.
    """
    auth_session = Session(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        amr=["oauth"],
        auth_time=datetime.now(UTC),
        idp="oauth:google",
        active_org_id=uuid.uuid4(),
    )
    assert services._federated_organization_pin(auth_session) is None
    # Does not raise: an unpinned session may mint for any organization, not just its current
    # `active_org_id` — a different random id on every run, deliberately, so this can never
    # pass by accident of matching `active_org_id` itself.
    services._assert_organization_unpinned_or_matches(auth_session, uuid.uuid4())
