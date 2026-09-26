"""`GET /auth/me` — role display names.

`MeOut.roles` stays role *codes*, unchanged: it is what the token itself carries, and
`services/auth/app/api/routes.py` builds it straight from `principal.roles`. `role_names`
rides alongside it, additively, sourced from account-service's `roles` table (its `name`
column — the one place a role has a friendly label) by way of
`DirectoryMembership.display_names_for`. See `services/account/tests/test_internal.py`
for the same field asserted from the account-service side of the wire.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_directory, get_key
from app.directory import DirectoryUser
from app.keys import KeyPair
from app.main import app
from platform_core.auth import Principal, get_principal
from platform_core.db import get_session
from tests.conftest import (
    MEMBERSHIP_A,
    ORG_A,
    USER,
    FakeDirectory,
    FakeEntitlements,
    FakeRedis,
    membership,
)

pytestmark = pytest.mark.integration


@contextmanager
def _as(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def _signed_in(roles: list[str]) -> Principal:
    return Principal(
        sub=USER,
        organization_id=ORG_A,
        membership_id=MEMBERSHIP_A,
        session_id=str(uuid.uuid4()),
        roles=roles,
        amr=["pwd"],
    )


class TestRoleDisplayNames:
    """Against the default fixture directory: one org, `MEMBER`, whose display name the
    `membership()` helper defaults to something visibly not the code."""

    async def test_role_names_are_the_directorys_labels_not_the_codes(
        self, client: AsyncClient, registered: None
    ) -> None:
        with _as(_signed_in(["MEMBER"])):
            response = await client.get("/api/v1/auth/me")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["roles"] == ["MEMBER"]
        assert body["role_names"] == ["Member Role"]
        assert "MEMBER" not in body["role_names"]

        # The active organization's entry in `organizations` carries the same pair — the
        # picker, and `useCurrentUser` after switching orgs, both read from here.
        active = next(o for o in body["organizations"] if o["organization_id"] == str(ORG_A))
        assert active["roles"] == ["MEMBER"]
        assert active["role_names"] == ["Member Role"]

    async def test_a_role_the_directory_no_longer_knows_falls_back_to_its_code(
        self, client: AsyncClient, registered: None
    ) -> None:
        """A token can carry a role code the current membership does not (a grant since
        revoked, a stale claim mid-refresh) — `role_names` degrades to the code for that one
        entry rather than dropping it or failing the request."""
        with _as(_signed_in(["MEMBER", "GHOST"])):
            response = await client.get("/api/v1/auth/me")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["roles"] == ["MEMBER", "GHOST"]
        assert body["role_names"] == ["Member Role", "GHOST"]


@pytest_asyncio.fixture
async def named_role_client(
    session: AsyncSession, pair: KeyPair, redis: FakeRedis
) -> AsyncIterator[AsyncClient]:
    """A client wired to a directory carrying a real catalogue role (`ORG_ADMIN` /
    "Administrator"), rather than the default fixture's synthetic one."""
    directory = FakeDirectory(
        [
            DirectoryUser(
                user_id=USER,
                email="sam@example.com",
                full_name="Sam Singleton",
                is_active=True,
                memberships=[
                    membership(
                        ORG_A,
                        MEMBERSHIP_A,
                        "acme",
                        "Acme Technologies",
                        roles=["ORG_ADMIN"],
                        role_names=["Administrator"],
                    )
                ],
            )
        ]
    )

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_key] = lambda: pair
    app.state.entitlements = FakeEntitlements()
    app.dependency_overrides[get_directory] = lambda: directory
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


class TestRoleDisplayNamesFromACatalogueRole:
    async def test_a_named_role_shows_its_name(
        self, named_role_client: AsyncClient, registered: None
    ) -> None:
        with _as(_signed_in(["ORG_ADMIN"])):
            response = await named_role_client.get("/api/v1/auth/me")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["roles"] == ["ORG_ADMIN"]
        assert body["role_names"] == ["Administrator"]
