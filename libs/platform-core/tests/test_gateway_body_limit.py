"""The gateway refuses a request body it would otherwise buffer whole.

`_forward` reads the entire request body into memory before it opens the upstream, so
without a ceiling a single large POST exhausts the gateway process *before* the downstream
service is ever asked its opinion — a service's own limit protects that service, not the
platform. ADR-0009 §5 names this cap a precondition for exposing an upload endpoint to the
internet, but the exposure is not upload-specific: it applies to every POST the gateway
forwards.

Two properties carry the weight here:

1. **The bytes decide, not the header.** `Content-Length` is client-supplied. A request that
   omits it, or lies about it, must still be caught.
2. **The refusal happens before the upstream is opened.** Every over-limit case asserts that
   the echo upstream recorded no call at all.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from platform_core import cache
from platform_core.app import create_app
from platform_core.config import ServiceSettings
from platform_core.gateway import (
    DEFAULT_MAX_BODY_BYTES,
    MAX_ROUTE_BODY_BYTES,
    GatewayPolicy,
    Route,
    RouteTable,
    _format_bytes,
    build_gateway,
)

PUBLIC = "/api/v1/auth/login"
PRIVATE = "/api/v1/employees"
UPLOAD = "/api/v1/documents/assets"

UPSTREAM = "http://upstream-service:9999"

#: Small on purpose — the behaviour is the same at 2 MiB and the test stays cheap.
LIMIT = 1024
#: What the upload route is allowed instead of the default.
ROUTE_LIMIT = 4096


class _Settings(ServiceSettings):
    service_name: str = "probe-gateway"
    db_schema: str = "public"


class _FakeRedis:
    """Enough Redis for the rate limiter, which is the only thing these tests trip."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    async def incr(self, key: str) -> int:
        value = int(self.store.get(key, "0")) + 1
        self.store[key] = str(value)
        return value

    async def expire(self, key: str, seconds: int) -> None:
        return None

    async def aclose(self) -> None:
        return None


class _EchoUpstream:
    """Records what it was sent. An over-limit request must never reach it."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append({"path": request.url.path, "body": request.content})
        return httpx.Response(200, json={"ok": True})

    @property
    def last(self) -> dict[str, Any]:
        return self.calls[-1]


def _routes() -> RouteTable:
    return RouteTable(
        [
            Route("/api/v1/auth", UPSTREAM),
            Route("/api/v1/employees", UPSTREAM),
            # The shape an upload route takes: it says so itself, in the route table.
            Route("/api/v1/documents", UPSTREAM, max_body_bytes=ROUTE_LIMIT),
        ]
    )


@pytest.fixture
def upstream() -> _EchoUpstream:
    return _EchoUpstream()


@pytest.fixture
def redis() -> Any:
    fake = _FakeRedis()
    cache._redis = fake  # type: ignore[assignment]
    yield fake
    cache._redis = None


@pytest.fixture
def app(upstream: _EchoUpstream, redis: Any, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    from platform_core import gateway as gateway_module

    def _claims(*args: Any, **kwargs: Any) -> dict[str, Any]:
        """Stand in for a verified access token.

        The authenticated path is worth exercising as well as the public one: the cap must
        be a property of forwarding, not of one branch of it.
        """
        return {
            "sub": "00000000-0000-0000-0000-000000000001",
            "act_org": "00000000-0000-0000-0000-000000000002",
            "pv": 0,
            "ent": [],
            "roles": [],
            "loc": [],
            "amr": ["pwd"],
        }

    monkeypatch.setattr(gateway_module, "decode_token", _claims)

    cfg = _Settings()
    application = create_app(cfg, title="probe-gateway", database=False, redis=False)
    policy = GatewayPolicy(
        audience="app",
        public_paths=frozenset({PUBLIC}),
        max_body_bytes=LIMIT,
    )
    build_gateway(application, settings=cfg, routes=_routes(), policy=policy)
    application.state.http = httpx.AsyncClient(transport=httpx.MockTransport(upstream.handler))
    return application


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await app.state.http.aclose()


def auth() -> dict[str, str]:
    return {"Authorization": "Bearer stubbed"}


class TestUnderTheLimit:
    async def test_a_body_under_the_limit_is_forwarded_byte_for_byte(
        self, client: AsyncClient, upstream: _EchoUpstream
    ) -> None:
        payload = json.dumps({"email": "a@example.com", "note": "x" * 200}).encode()
        response = await client.post(PUBLIC, content=payload)

        assert response.status_code == 200
        assert upstream.last["body"] == payload, "the cap must not alter what it lets through"

    async def test_a_body_exactly_at_the_limit_is_allowed(
        self, client: AsyncClient, upstream: _EchoUpstream
    ) -> None:
        """The boundary is inclusive. An off-by-one here refuses a legitimate request."""
        response = await client.post(PUBLIC, content=b"x" * LIMIT)

        assert response.status_code == 200
        assert len(upstream.last["body"]) == LIMIT

    async def test_a_request_with_no_body_at_all_still_works(
        self, client: AsyncClient, upstream: _EchoUpstream
    ) -> None:
        """The regression that matters most: every GET goes through this code path now."""
        response = await client.get(PUBLIC)

        assert response.status_code == 200
        assert upstream.last["body"] == b""


class TestOverTheLimit:
    async def test_an_oversized_body_is_refused_as_a_problem_document(
        self, client: AsyncClient, upstream: _EchoUpstream
    ) -> None:
        response = await client.post(PUBLIC, content=b"x" * (LIMIT + 1))

        assert response.status_code == 413
        assert response.headers["content-type"].startswith("application/problem+json")
        body = response.json()
        assert body["type"].endswith("/payload-too-large")
        assert body["status"] == 413
        assert body["instance"] == PUBLIC
        assert not upstream.calls, "the downstream service must never see the request"

    async def test_the_authenticated_path_is_capped_too(
        self, client: AsyncClient, upstream: _EchoUpstream
    ) -> None:
        """The cap belongs to forwarding, not to the unauthenticated branch of it."""
        response = await client.post(PRIVATE, content=b"x" * (LIMIT + 1), headers=auth())

        assert response.status_code == 413
        assert not upstream.calls

    async def test_the_refusal_names_the_limit(self, client: AsyncClient) -> None:
        """A client that cannot see the limit cannot do anything but retry blindly."""
        response = await client.post(PUBLIC, content=b"x" * (LIMIT + 1))

        assert "1 KiB" in response.json()["detail"]

    async def test_the_refusal_leaks_neither_the_upstream_nor_the_internals(
        self, client: AsyncClient
    ) -> None:
        """A 413 is reachable unauthenticated, so it is a free probe of the topology unless
        it says nothing but "too big"."""
        response = await client.post(PUBLIC, content=b"x" * (LIMIT + 1))

        rendered = response.text
        assert UPSTREAM not in rendered
        assert "upstream-service" not in rendered
        assert "http://" not in rendered.replace("https://errors.", "")
        assert "max_body_bytes" not in rendered
        assert "Traceback" not in rendered


class TestContentLengthIsAClaim:
    async def test_a_lying_content_length_is_caught_by_the_counter(
        self, client: AsyncClient, upstream: _EchoUpstream
    ) -> None:
        """The header check is an early exit; the byte counter is the enforcement.

        A caller who declares 10 bytes and sends a gigabyte defeats any cap that trusts
        the declaration.
        """
        response = await client.post(
            PUBLIC,
            content=b"x" * (LIMIT * 4),
            headers={"content-length": "10"},
        )

        assert response.status_code == 413
        assert not upstream.calls

    async def test_a_body_with_no_content_length_is_still_caught(
        self, client: AsyncClient, upstream: _EchoUpstream
    ) -> None:
        """A chunked request never has to send one at all."""

        async def chunks() -> AsyncIterator[bytes]:
            for _ in range(8):
                yield b"y" * 512

        response = await client.post(PUBLIC, content=chunks())

        assert response.status_code == 413
        assert not upstream.calls

    async def test_an_honest_oversized_declaration_is_refused_without_reading_the_body(
        self, client: AsyncClient, upstream: _EchoUpstream
    ) -> None:
        """The cheap path: a declared-huge body costs no memory and no upstream socket."""
        consumed = 0

        async def chunks() -> AsyncIterator[bytes]:
            nonlocal consumed
            for _ in range(64):
                consumed += 1
                yield b"z" * 1024

        response = await client.post(
            PUBLIC,
            content=chunks(),
            headers={"content-length": str(64 * 1024)},
        )

        assert response.status_code == 413
        assert consumed == 0, "a declared-oversized body must not be read"
        assert not upstream.calls

    async def test_reading_stops_at_the_limit_rather_than_draining(
        self, client: AsyncClient
    ) -> None:
        """Draining the remainder would buffer exactly what was refused."""
        consumed = 0

        async def chunks() -> AsyncIterator[bytes]:
            nonlocal consumed
            for _ in range(64):
                consumed += 1
                yield b"z" * 1024

        response = await client.post(PUBLIC, content=chunks())

        assert response.status_code == 413
        assert consumed <= 2, f"stopped after {consumed} chunks of 64"


class TestPerRouteOverride:
    async def test_a_route_may_raise_its_own_ceiling(
        self, client: AsyncClient, upstream: _EchoUpstream
    ) -> None:
        """An upload route legitimately needs more than a JSON POST — and says so in the
        route table, so the gateway's default stays tight for everything else."""
        response = await client.post(UPLOAD, content=b"x" * (LIMIT * 2), headers=auth())

        assert response.status_code == 200
        assert len(upstream.last["body"]) == LIMIT * 2

    async def test_the_override_is_still_a_ceiling(
        self, client: AsyncClient, upstream: _EchoUpstream
    ) -> None:
        response = await client.post(UPLOAD, content=b"x" * (ROUTE_LIMIT + 1), headers=auth())

        assert response.status_code == 413
        assert "4 KiB" in response.json()["detail"]
        assert not upstream.calls


class TestRouteTableParsing:
    def _write(self, tmp_path: Path, route: dict[str, Any]) -> Path:
        path = tmp_path / "routes.json"
        path.write_text(json.dumps({"routes": [route]}))
        return path

    def test_a_route_without_an_override_uses_the_default(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, {"prefix": "/api/v1/x", "upstream": UPSTREAM})
        assert RouteTable.from_file(path).resolve("/api/v1/x").max_body_bytes is None  # type: ignore[union-attr]

    def test_an_override_is_read_from_the_route_table(self, tmp_path: Path) -> None:
        """Data, not code: raising a limit for one route is a deployment artefact rather
        than an edit to the shared gateway."""
        path = self._write(
            tmp_path, {"prefix": "/api/v1/x", "upstream": UPSTREAM, "max_body_bytes": 5000}
        )
        assert RouteTable.from_file(path).resolve("/api/v1/x").max_body_bytes == 5000  # type: ignore[union-attr]

    @pytest.mark.parametrize("value", ["5mb", 0, -1, 1.5, True, MAX_ROUTE_BODY_BYTES + 1])
    def test_a_nonsense_override_fails_at_boot(self, tmp_path: Path, value: Any) -> None:
        """Loud at startup beats silent at request time: a route table that parsed `"5mb"`
        as absent would hand back the unbounded buffer on the one route that asked to be
        different."""
        path = self._write(
            tmp_path, {"prefix": "/api/v1/x", "upstream": UPSTREAM, "max_body_bytes": value}
        )
        with pytest.raises(ValueError, match="max_body_bytes"):
            RouteTable.from_file(path)


class TestDefaults:
    def test_the_default_ceiling_is_two_mebibytes(self) -> None:
        """Roughly eight times the largest legitimate JSON body on the platform (the
        1000-event metering batch), and bounded enough that concurrency cannot exhaust the
        process."""
        assert DEFAULT_MAX_BODY_BYTES == 2 * 1024 * 1024
        assert GatewayPolicy(audience="app", public_paths=frozenset()).max_body_bytes == (
            DEFAULT_MAX_BODY_BYTES
        )

    @pytest.mark.parametrize(
        ("limit", "rendered"),
        [
            (2 * 1024 * 1024, "2 MiB"),
            (5 * 1024 * 1024, "5 MiB"),
            (1024, "1 KiB"),
            (500, "500 bytes"),
        ],
    )
    def test_the_limit_reads_as_a_size_a_human_can_act_on(self, limit: int, rendered: str) -> None:
        assert _format_bytes(limit) == rendered
