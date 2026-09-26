"""Fixtures for auth-service tests.

The directory is faked. auth-service authenticates *against* account-service, and the
whole point of the `Directory` protocol is that sign-in — lockout, MFA, organization
resolution, token rotation — can be exercised without a second service running. The fake
implements exactly the protocol the real client does, so a change to that shape breaks
both together.

Signing uses a keypair generated per run rather than one on disk: tests must not depend on
`make devkeys` having been run, and a committed test key is a key that reaches production
by accident.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# The service directory is the import root: every service exposes a top-level `app`, so
# only one may be on the path in a given process.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import services
from app.config import Settings, settings
from app.deps import get_directory, get_key
from app.directory import DirectoryDomain, DirectoryMembership, DirectoryUser
from app.keys import KeyPair, generate_keypair
from app.main import app
from app.models import SCHEMA, Base
from platform_core import cache
from platform_core.db import get_session

ORG_A = uuid.uuid4()
ORG_B = uuid.uuid4()
USER = uuid.uuid4()
MEMBERSHIP_A = uuid.uuid4()
MEMBERSHIP_B = uuid.uuid4()

PASSWORD = "correct-horse-7"  # pragma: allowlist secret


class FakeDirectory:
    """An in-memory stand-in for account-service, matching the `Directory` protocol."""

    def __init__(
        self, users: list[DirectoryUser], domains: list[DirectoryDomain] | None = None
    ) -> None:
        self.users = users
        self.domains = domains or []

    async def find_by_email(self, email: str) -> DirectoryUser | None:
        return next((u for u in self.users if u.email.lower() == email.lower()), None)

    async def get(self, user_id: uuid.UUID) -> DirectoryUser | None:
        return next((u for u in self.users if u.user_id == user_id), None)

    async def resolve_domain(self, domain: str) -> DirectoryDomain | None:
        return next((d for d in self.domains if d.domain.lower() == domain.lower()), None)


def membership(
    org_id: uuid.UUID,
    membership_id: uuid.UUID,
    slug: str,
    name: str,
    *,
    roles: list[str] | None = None,
    role_names: list[str] | None = None,
    status: str = "active",
) -> DirectoryMembership:
    resolved_roles = roles or ["MEMBER"]
    return DirectoryMembership(
        membership_id=membership_id,
        organization_id=org_id,
        organization_slug=slug,
        organization_name=name,
        status=status,
        roles=resolved_roles,
        # Defaults to a display label distinct from the code, so a test that forgets to
        # pass one still fails loudly if code ever leaks through where a name belongs.
        role_names=role_names or [f"{code.title()} Role" for code in resolved_roles],
        locations=[],
        permission_version=1,
    )


def user_in_one_org() -> DirectoryUser:
    return DirectoryUser(
        user_id=USER,
        email="sam@example.com",
        full_name="Sam Singleton",
        is_active=True,
        memberships=[membership(ORG_A, MEMBERSHIP_A, "acme", "Acme Technologies")],
    )


def user_in_two_orgs() -> DirectoryUser:
    return DirectoryUser(
        user_id=USER,
        email="sam@example.com",
        full_name="Sam Singleton",
        is_active=True,
        memberships=[
            membership(ORG_A, MEMBERSHIP_A, "acme", "Acme Technologies"),
            membership(ORG_B, MEMBERSHIP_B, "beta", "Beta Corporation"),
        ],
    )


@pytest.fixture(scope="session")
def pair() -> KeyPair:
    return generate_keypair()


@pytest.fixture
def cfg() -> Settings:
    return settings()


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    conf = settings()
    connect_args = {"server_settings": {"search_path": f"{SCHEMA},public"}}
    tables = ", ".join(f'"{SCHEMA}"."{t.name}"' for t in reversed(Base.metadata.sorted_tables))

    # Teardown as the admin role: TRUNCATE is withheld from the app role because it is not
    # filtered by row-level security.
    admin = create_async_engine(conf.admin_dsn, connect_args=connect_args)
    async with admin.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    await admin.dispose()

    # The code under test runs as the app role, so the privilege split is visible here rather
    # than only in production.
    engine = create_async_engine(conf.app_dsn, connect_args=connect_args)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


class FakeEntitlements:
    """Stands in for product-service.

    Returns a fixed list so a test can assert the `ent` claim is actually populated. The real
    client degrades to an empty list when product-service is unreachable — that behaviour has
    its own test rather than being the default everything else silently inherits.
    """

    def __init__(self, keys: list[str] | None = None) -> None:
        self.keys = keys if keys is not None else ["core", "hrms"]
        self.asked: list[str] = []

    async def for_organization(self, organization_id) -> list[str]:
        self.asked.append(str(organization_id))
        return list(self.keys)


@pytest_asyncio.fixture
async def entitlements() -> FakeEntitlements:
    return FakeEntitlements()


@pytest_asyncio.fixture
async def directory() -> FakeDirectory:
    return FakeDirectory([user_in_one_org()])


@pytest_asyncio.fixture
async def registered(session: AsyncSession, cfg: Settings) -> None:
    """Give the fixture user a password."""
    async with session.begin():
        await services.set_password(session, USER, PASSWORD, cfg=cfg)


class FakeRedis:
    """Enough Redis for the single-sign-on state store, and no more.

    Faithful where it has to be. `set(nx=True)` really refuses an existing key and `getdel`
    really deletes before returning — those two are the replay defence, and a stand-in that
    quietly allowed either would make the replay test pass while the property was gone.
    Expiry is *not* simulated: the TTL is a ceiling on an already single-use value, and a
    clock in a fake is a source of flaky tests rather than of confidence.
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(
        self, key: str, value: str, ex: int | None = None, nx: bool = False
    ) -> bool | None:
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def getdel(self, key: str) -> str | None:
        return self.store.pop(key, None)

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self.store.pop(key, None)

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


@pytest_asyncio.fixture
async def redis() -> AsyncIterator[FakeRedis]:
    """Installed process-wide, the way the real client is.

    `StateStore` resolves the client through `platform_core.cache.get_redis()` rather than
    holding one, so the seam is the module global — the same one account-service's tests
    replace.
    """
    fake = FakeRedis()
    cache._redis = fake  # type: ignore[assignment]
    yield fake
    cache._redis = None


@pytest_asyncio.fixture
async def client(
    session: AsyncSession, directory: FakeDirectory, pair: KeyPair, redis: FakeRedis
) -> AsyncIterator[AsyncClient]:
    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_key] = lambda: pair
    app.state.entitlements = FakeEntitlements()
    app.dependency_overrides[get_directory] = lambda: directory
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
