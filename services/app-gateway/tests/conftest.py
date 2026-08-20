"""Fixtures for gateway tests.

The gateway has no database, so nothing here touches Postgres. What it does need is a
Redis stand-in, a signing key to mint test tokens with, and an upstream that echoes back
the headers it received — because *what the gateway forwards* is most of what the gateway
is.
"""

from __future__ import annotations

import sys
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from platform_core import cache

ORG = str(uuid.uuid4())
USER = str(uuid.uuid4())
MEMBERSHIP = str(uuid.uuid4())


class FakeRedis:
    """Enough Redis for the gateway: counters and string get/set.

    A real Redis buys nothing here — the behaviour under test is the gateway's
    decision-making, and the interesting cases are what it does when Redis says a
    particular thing, including failing.
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.fail = False

    def _guard(self) -> None:
        if self.fail:
            raise ConnectionError("redis is down")

    async def get(self, key: str) -> str | None:
        self._guard()
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._guard()
        self.store[key] = value

    async def incr(self, key: str) -> int:
        self._guard()
        value = int(self.store.get(key, "0")) + 1
        self.store[key] = str(value)
        return value

    async def expire(self, key: str, seconds: int) -> None:
        self._guard()

    async def delete(self, *keys: str) -> None:
        self._guard()
        for key in keys:
            self.store.pop(key, None)

    async def aclose(self) -> None:
        return None


class EchoUpstream:
    """Stands in for every downstream service, reflecting what it was sent."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.status = 200

    async def handler(self, request: httpx.Request) -> httpx.Response:
        record = {
            "path": request.url.path,
            "method": request.method,
            "headers": {k.lower(): v for k, v in request.headers.items()},
        }
        self.calls.append(record)
        return httpx.Response(self.status, json={"ok": True})

    @property
    def last(self) -> dict[str, Any]:
        return self.calls[-1]

    def header(self, name: str) -> str | None:
        return self.last["headers"].get(name.lower())


@pytest.fixture
def signing_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture
def mint(signing_key: Ed25519PrivateKey):
    """Mint an access token the way auth-service would."""
    cfg = settings()

    def _mint(
        *,
        audience: str | None = None,
        org: str | None = ORG,
        entitlements: list[str] | None = None,
        permission_version: int = 1,
        staff: bool = False,
        expired: bool = False,
        typ: str = "access",
        membership: str | None = MEMBERSHIP,
    ) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": cfg.jwt_issuer,
            "sub": USER,
            "aud": audience or cfg.audience,
            "iat": now - 10,
            "exp": now - 5 if expired else now + 900,
            "jti": uuid.uuid4().hex,
            "typ": typ,
            "sid": str(uuid.uuid4()),
            "ent": entitlements if entitlements is not None else ["hrms"],
            "roles": ["MEMBER"],
            "loc": [],
            "amr": ["pwd"],
            "pv": permission_version,
        }
        if org:
            claims["act_org"] = org
        if membership:
            claims["mem"] = membership
        if staff:
            claims["stf"] = True
        return jwt.encode(claims, signing_key, algorithm="EdDSA", headers={"kid": "test"})

    return _mint


@pytest_asyncio.fixture
async def redis() -> AsyncIterator[FakeRedis]:
    fake = FakeRedis()
    cache._redis = fake  # type: ignore[assignment]
    yield fake
    cache._redis = None


@pytest.fixture
def upstream() -> EchoUpstream:
    return EchoUpstream()


@pytest_asyncio.fixture
async def client(
    redis: FakeRedis,
    upstream: EchoUpstream,
    signing_key: Ed25519PrivateKey,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    from app.main import app
    from platform_core import auth

    # Verify against the test key rather than fetching a real JWKS document.
    class _Key:
        key = signing_key.public_key()

    class _JwkClient:
        def get_signing_key_from_jwt(self, token: str) -> Any:
            return _Key()

    monkeypatch.setattr(auth, "_jwk_client", _JwkClient())

    # Permission resolution normally calls account-service. Stubbing it keeps these tests
    # about the gateway's decisions — and the fact that *not* stubbing it produces a 503
    # rather than an open door is itself asserted, in `test_resolution_fails_closed`.
    from app import main as gateway_main

    async def _grants(membership_id: str, org_id: str, version: int) -> list[str]:
        return ["core.profile.self.read:OWN", "hrms.profile.employee.read:TEAM"]

    monkeypatch.setattr(gateway_main.resolver, "resolve", _grants)

    app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(upstream.handler))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await app.state.http.aclose()
