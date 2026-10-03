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

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app import oidc, services
from app.config import Settings, settings
from app.directory import DirectoryUser
from app.models import PlatformOAuthProvider
from app.provisioning import FakeProvisioningClient
from platform_core.errors import NotFoundProblem, ValidationProblem
from tests.conftest import FakeDirectory, FakeRedis, membership

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def store(redis: FakeRedis) -> oidc.StateStore:
    """Mirrors `test_sso.py`'s own `store` fixture — not shared through `conftest.py`
    because `StateStore` wraps whichever Redis stand-in a module's fixtures hand it, and
    this file has no dependency on single sign-on's."""
    return oidc.StateStore(redis)


async def _seed_google_provider(session: AsyncSession) -> None:
    session.add(
        PlatformOAuthProvider(
            provider="google",
            enabled=True,
            client_id="client-123",
            issuer="https://accounts.google.com",
            authorization_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
            token_endpoint="https://oauth2.googleapis.com/token",
            jwks_uri="https://www.googleapis.com/oauth2/v3/certs",
        )
    )
    await session.flush()


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
