"""`bin/seed-oauth-providers`, and the `app.bootstrap` CLI dispatch that fronts it.

The dev-operator seed (`bin/seed-staff`) already exercises `bootstrap_staff_owner` indirectly
through `test_operator.py`; this covers the OAuth provider seed added alongside it, which is
the part a deploy actually depends on (Task 10 of the 2026-10-03 oauth2-social-login plan).
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from app import oidc
from app.bootstrap import seed_oauth_providers
from app.models import PlatformOAuthProvider
from platform_core import pii

pytestmark = pytest.mark.integration

FAKE_DISCOVERY_DOC = {
    "issuer": "https://accounts.google.com",
    "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
    "token_endpoint": "https://oauth2.googleapis.com/token",
    "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
}


class _FakeHttpIdpClient:
    """Stands in for `oidc.HttpIdpClient` so a credentialed seed never touches the network —
    the same "no network in tests" posture `tests/fake_idp.py` holds for the SSO flow proper."""

    def __init__(self, doc: dict[str, Any] = FAKE_DISCOVERY_DOC) -> None:
        self.doc = doc
        self.discovered: list[str] = []
        self.closed = False

    async def discover(self, discovery_url: str) -> dict[str, Any]:
        self.discovered.append(discovery_url)
        return self.doc

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def fake_idp(monkeypatch) -> _FakeHttpIdpClient:
    """Patches the `HttpIdpClient` *class* `app.bootstrap` reaches for via `oidc.HttpIdpClient`
    (it imports the module, not the name, so patching the module attribute is what the call
    site actually sees) and hands back the one instance it will construct."""
    instance = _FakeHttpIdpClient()
    monkeypatch.setattr(oidc, "HttpIdpClient", lambda: instance)
    return instance


class TestSeedOauthProviders:
    async def test_with_no_credentials_both_providers_are_created_disabled(
        self, session, cfg, fake_idp
    ) -> None:
        assert cfg.google_oauth_client_id is None
        assert cfg.microsoft_oauth_client_id is None

        await seed_oauth_providers(session, cfg=cfg)
        await session.commit()

        google = await session.get(PlatformOAuthProvider, "google")
        microsoft = await session.get(PlatformOAuthProvider, "microsoft")
        assert google is not None and google.enabled is False
        assert microsoft is not None and microsoft.enabled is False
        assert google.client_id is None
        assert google.client_secret_enc is None
        assert fake_idp.discovered == [], "no credentials means no discovery call at all"

    async def test_a_provider_with_only_a_client_id_stays_disabled_not_half_configured(
        self, session, cfg, fake_idp
    ) -> None:
        """Self-review question: half a credential pair must not read as "configured"."""
        partial_cfg = cfg.model_copy(update={"google_oauth_client_id": "only-the-id"})

        await seed_oauth_providers(session, cfg=partial_cfg)
        await session.commit()

        google = await session.get(PlatformOAuthProvider, "google")
        assert google.enabled is False
        assert google.client_id is None, "a stray client_id with no secret is not stored either"
        assert google.client_secret_enc is None
        assert fake_idp.discovered == []

    async def test_a_provider_with_only_a_client_secret_stays_disabled(
        self, session, cfg, fake_idp
    ) -> None:
        partial_cfg = cfg.model_copy(
            update={"google_oauth_client_secret": SecretStr("only-the-secret")}
        )

        await seed_oauth_providers(session, cfg=partial_cfg)
        await session.commit()

        google = await session.get(PlatformOAuthProvider, "google")
        assert google.enabled is False
        assert fake_idp.discovered == []

    async def test_both_credentials_present_enables_the_provider_and_runs_discovery(
        self, session, cfg, fake_idp
    ) -> None:
        full_cfg = cfg.model_copy(
            update={
                "google_oauth_client_id": "client-123",
                "google_oauth_client_secret": SecretStr("s3cr3t-value"),
            }
        )

        await seed_oauth_providers(session, cfg=full_cfg)
        await session.commit()

        google = await session.get(PlatformOAuthProvider, "google")
        assert google.enabled is True
        assert google.client_id == "client-123"
        assert google.issuer == FAKE_DISCOVERY_DOC["issuer"]
        assert google.authorization_endpoint == FAKE_DISCOVERY_DOC["authorization_endpoint"]
        assert google.token_endpoint == FAKE_DISCOVERY_DOC["token_endpoint"]
        assert google.jwks_uri == FAKE_DISCOVERY_DOC["jwks_uri"]
        assert fake_idp.discovered == [
            "https://accounts.google.com/.well-known/openid-configuration"
        ]
        # microsoft had no credentials in this run and must be unaffected.
        microsoft = await session.get(PlatformOAuthProvider, "microsoft")
        assert microsoft.enabled is False

    async def test_the_stored_secret_is_sealed_never_plaintext(
        self, session, cfg, fake_idp
    ) -> None:
        secret_value = "s3cr3t-value-should-never-appear-in-the-column"
        full_cfg = cfg.model_copy(
            update={
                "google_oauth_client_id": "client-123",
                "google_oauth_client_secret": SecretStr(secret_value),
            }
        )

        await seed_oauth_providers(session, cfg=full_cfg)
        await session.commit()

        google = await session.get(PlatformOAuthProvider, "google")
        assert google.client_secret_enc is not None
        assert secret_value.encode() not in google.client_secret_enc
        assert pii.open_sealed(google.client_secret_enc, settings=full_cfg) == secret_value

    async def test_running_it_twice_is_idempotent_not_a_duplicate_key_error(
        self, session, cfg, fake_idp
    ) -> None:
        full_cfg = cfg.model_copy(
            update={
                "google_oauth_client_id": "client-123",
                "google_oauth_client_secret": SecretStr("s3cr3t-value"),
            }
        )

        await seed_oauth_providers(session, cfg=full_cfg)
        await session.commit()
        await seed_oauth_providers(session, cfg=full_cfg)
        await session.commit()

        count = (
            await session.execute(
                select(func.count()).select_from(PlatformOAuthProvider).where(
                    PlatformOAuthProvider.provider == "google"
                )
            )
        ).scalar_one()
        assert count == 1, "re-running must update the existing row, not insert a second one"

    async def test_a_credential_rotation_overwrites_the_previous_sealed_secret(
        self, session, cfg, fake_idp
    ) -> None:
        """"run again whenever a client secret rotates" (the function's own docstring) must
        actually take effect — a second run with a new secret must not leave the old one
        readable under either credential."""
        first_cfg = cfg.model_copy(
            update={
                "google_oauth_client_id": "client-123",
                "google_oauth_client_secret": SecretStr("old-secret"),
            }
        )
        await seed_oauth_providers(session, cfg=first_cfg)
        await session.commit()

        second_cfg = cfg.model_copy(
            update={
                "google_oauth_client_id": "client-123",
                "google_oauth_client_secret": SecretStr("new-secret"),
            }
        )
        await seed_oauth_providers(session, cfg=second_cfg)
        await session.commit()

        google = await session.get(PlatformOAuthProvider, "google")
        assert pii.open_sealed(google.client_secret_enc, settings=second_cfg) == "new-secret"


class TestTheBootstrapCommandLine:
    """`python -m app.bootstrap` must keep seeding the dev operator with no argument (the form
    `bin/seed-staff` already uses); `seed-oauth-providers` must route to the new seed instead
    of also running the dev-operator path."""

    def test_no_argument_still_seeds_the_dev_operator(self, monkeypatch) -> None:
        import sys

        from app import bootstrap

        calls: list[str] = []

        async def fake_dev_owner(_cfg):
            calls.append("seed_dev_owner")

        async def fake_oauth(_cfg):
            calls.append("seed_platform_oauth_providers")

        monkeypatch.setattr(bootstrap, "seed_dev_owner", fake_dev_owner)
        monkeypatch.setattr(bootstrap, "seed_platform_oauth_providers", fake_oauth)
        monkeypatch.setattr(sys, "argv", ["app.bootstrap"])

        bootstrap.main()
        assert calls == ["seed_dev_owner"]

    def test_seed_oauth_providers_argument_routes_to_the_new_seed(self, monkeypatch) -> None:
        import sys

        from app import bootstrap

        calls: list[str] = []

        async def fake_dev_owner(_cfg):
            calls.append("seed_dev_owner")

        async def fake_oauth(_cfg):
            calls.append("seed_platform_oauth_providers")

        monkeypatch.setattr(bootstrap, "seed_dev_owner", fake_dev_owner)
        monkeypatch.setattr(bootstrap, "seed_platform_oauth_providers", fake_oauth)
        monkeypatch.setattr(sys, "argv", ["app.bootstrap", "seed-oauth-providers"])

        bootstrap.main()
        assert calls == ["seed_platform_oauth_providers"]

