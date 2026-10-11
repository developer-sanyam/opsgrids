"""A verifier meeting a token signed by a key it has not fetched yet.

PyJWT 2.14 (GHSA-2gx3-rcp4-g85q) stopped `PyJWKClient` from re-fetching the JWKS for every
token carrying an unknown `kid`: after any successful fetch it waits `cooldown_duration`
(30 s) before a forced refresh, and in the meantime `get_signing_key` raises
`PyJWKClientError`, which is a `PyJWTError` but not an `InvalidTokenError`. `decode_token`
answers it with a 401, as it does any token it cannot verify; a JWKS it cannot reach (or that
holds no usable key) is a 503, because that is our outage and a 401 makes every SPA sign its
user out.

These tests run the real thing end to end: a real JWKS endpoint on a loopback socket, the real
`PyJWKClient` fetching from it over HTTP, real EdDSA signatures. Only the clock PyJWT reads is
faked, because the behaviour under test is a 30 second wait.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Any

import httpx
import jwt
import jwt.jwks_client
import pytest
import structlog.testing
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from jwt.algorithms import OKPAlgorithm

from platform_core import auth as auth_module
from platform_core.auth import CurrentPrincipal, decode_token, reset_jwks_cache
from platform_core.config import ServiceSettings
from platform_core.errors import (
    UnauthorizedProblem,
    UpstreamUnavailableProblem,
    register_error_handlers,
)

ORG_ID = str(uuid.uuid4())
USER_ID = str(uuid.uuid4())


class _Settings(ServiceSettings):
    service_name: str = "account"
    db_schema: str = "account"


class _Signer:
    """One signing key: what auth-service holds, reduced to a kid and a private key."""

    def __init__(self, kid: str) -> None:
        self.kid = kid
        self._private = Ed25519PrivateKey.generate()

    def public_jwk(self) -> dict[str, Any]:
        jwk = OKPAlgorithm.to_jwk(self._private.public_key(), as_dict=True)
        return {**jwk, "kid": self.kid, "use": "sig", "alg": "EdDSA"}

    def token(self, cfg: ServiceSettings) -> str:
        now = int(time.time())
        claims = {
            "sub": USER_ID,
            "iss": cfg.jwt_issuer,
            "iat": now,
            "exp": now + 900,
            "typ": "access",
            "act_org": ORG_ID,
        }
        return jwt.encode(claims, self._private, algorithm="EdDSA", headers={"kid": self.kid})


class _JwksServer:
    """`/.well-known/jwks.json` on a loopback port, serving whatever `keys` holds right now."""

    def __init__(self) -> None:
        self.keys: list[dict[str, Any]] = []
        self.fetches = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                outer.fetches += 1
                body = json.dumps({"keys": outer.keys}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:
                """Silence the per-request line on stderr."""

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        # A short poll interval, because `shutdown()` waits for one: the default half second
        # would be added to every test that starts a server.
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_address[1]}/.well-known/jwks.json"

    def publish(self, *signers: _Signer) -> None:
        self.keys.extend(s.public_jwk() for s in signers)

    def __enter__(self) -> _JwksServer:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)


class _Clock:
    """The monotonic clock `PyJWKClient` reads, which a test can move forward."""

    def __init__(self) -> None:
        self._offset = 0.0

    def monotonic(self) -> float:
        return time.monotonic() + self._offset

    def advance(self, seconds: float) -> None:
        self._offset += seconds


@pytest.fixture
def jwks() -> Iterator[_JwksServer]:
    with _JwksServer() as server:
        yield server


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    """Replace `time` as seen by `jwt.jwks_client` only — not the process-wide clock, which
    the HTTP server thread and pytest itself also read."""
    fake = _Clock()
    monkeypatch.setattr(jwt.jwks_client, "time", SimpleNamespace(monotonic=fake.monotonic))
    return fake


@pytest.fixture(autouse=True)
def _fresh_jwks_client() -> Iterator[None]:
    reset_jwks_cache()
    yield
    reset_jwks_cache()


def _settings(server: _JwksServer) -> ServiceSettings:
    return _Settings(jwks_url=server.url)


def _verify(cfg: ServiceSettings, signer: _Signer) -> dict[str, Any]:
    return decode_token(cfg, signer.token(cfg), expected_type="access")


class TestNewKeyInsideTheCooldown:
    """The review's scenario: kid A fetched, kid B published, a token signed with B arrives
    before PyJWT will re-fetch."""

    def test_is_a_401_problem_not_an_unhandled_error(
        self, jwks: _JwksServer, clock: _Clock
    ) -> None:
        old, new = _Signer("key-a"), _Signer("key-b")
        jwks.publish(old)
        cfg = _settings(jwks)
        assert _verify(cfg, old)["sub"] == USER_ID  # the fetch that starts the cooldown

        jwks.publish(new)
        clock.advance(5)  # well inside the 30 s cooldown

        with pytest.raises(UnauthorizedProblem) as caught:
            _verify(cfg, new)

        problem = caught.value
        assert problem.status_code == 401
        assert problem.problem_type == "unauthorized"
        assert "key-b" not in problem.detail

    def test_verifies_once_the_cooldown_has_passed(self, jwks: _JwksServer, clock: _Clock) -> None:
        old, new = _Signer("key-a"), _Signer("key-b")
        jwks.publish(old)
        cfg = _settings(jwks)
        _verify(cfg, old)
        jwks.publish(new)
        clock.advance(5)
        with pytest.raises(UnauthorizedProblem):
            _verify(cfg, new)

        clock.advance(30)  # past the cooldown, with no restart and no cache reset

        assert _verify(cfg, new)["sub"] == USER_ID
        # The key signed before the rotation is still good alongside it.
        assert _verify(cfg, old)["sub"] == USER_ID

    def test_does_not_refetch_inside_the_cooldown(self, jwks: _JwksServer, clock: _Clock) -> None:
        """The reason the cooldown exists: a stream of tokens with unknown kids must not become
        a stream of requests to auth-service. Making the 401 friendlier must not weaken it."""
        old, new = _Signer("key-a"), _Signer("key-b")
        jwks.publish(old)
        cfg = _settings(jwks)
        _verify(cfg, old)
        assert jwks.fetches == 1
        jwks.publish(new)

        for _ in range(25):
            clock.advance(0.5)
            with pytest.raises(UnauthorizedProblem):
                _verify(cfg, new)

        assert jwks.fetches == 1

    def test_the_cooldown_is_pyjwts_and_is_not_disabled(self, jwks: _JwksServer) -> None:
        client = auth_module._jwks(_settings(jwks))
        assert client.cooldown_duration == auth_module.JWKS_REFRESH_COOLDOWN_SECONDS
        assert client.cooldown_duration == 30


class TestGenuinelyUnknownKid:
    def test_is_a_401_inside_the_cooldown(self, jwks: _JwksServer, clock: _Clock) -> None:
        known, stranger = _Signer("key-a"), _Signer("never-published")
        jwks.publish(known)
        cfg = _settings(jwks)
        _verify(cfg, known)

        with pytest.raises(UnauthorizedProblem) as caught:
            _verify(cfg, stranger)

        assert caught.value.status_code == 401

    def test_is_a_401_after_a_refresh_that_still_does_not_know_it(
        self, jwks: _JwksServer, clock: _Clock
    ) -> None:
        """Past the cooldown PyJWT does re-fetch, and a key that still is not there raises the
        same exception — a forged `kid` was a 500 on every PyJWT version, not only since 2.14."""
        known, stranger = _Signer("key-a"), _Signer("never-published")
        jwks.publish(known)
        cfg = _settings(jwks)
        _verify(cfg, known)
        clock.advance(31)

        with pytest.raises(UnauthorizedProblem) as caught:
            _verify(cfg, stranger)

        assert caught.value.status_code == 401
        assert jwks.fetches == 2  # the forced refresh did happen

    def test_a_token_with_no_kid_is_a_401(self, jwks: _JwksServer, clock: _Clock) -> None:
        known = _Signer("key-a")
        jwks.publish(known)
        cfg = _settings(jwks)
        _verify(cfg, known)
        now = int(time.time())
        bare = jwt.encode(
            {"sub": USER_ID, "iss": cfg.jwt_issuer, "iat": now, "exp": now + 60, "typ": "access"},
            Ed25519PrivateKey.generate(),
            algorithm="EdDSA",
        )

        with pytest.raises(UnauthorizedProblem) as caught:
            decode_token(cfg, bare, expected_type="access")

        assert caught.value.status_code == 401


class TestJwksUnreachable:
    def test_is_a_logged_503_not_a_401(self) -> None:
        """An unreachable JWKS is our outage, not a bad credential: a 401 here would make every
        SPA refresh, fail and sign its user out, so it is a 503 and an error line names it."""
        with _JwksServer() as server:
            cfg = _settings(server)
        # The server is closed now: nothing is listening on that port.
        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(UpstreamUnavailableProblem) as caught,
        ):
            decode_token(cfg, _Signer("key-a").token(cfg), expected_type="access")

        assert caught.value.status_code == 503
        assert [e["event"] for e in logs if e["log_level"] == "error"] == ["auth.jwks_unavailable"]


class TestAsServedToAClient:
    """The same scenario through a real ASGI app, because "not a 500" is a statement about
    what the client receives."""

    @pytest.fixture
    async def client(self, jwks: _JwksServer) -> AsyncIterator[httpx.AsyncClient]:
        app = FastAPI()
        app.state.settings = _settings(jwks)
        register_error_handlers(app)

        @app.get("/whoami")
        def whoami(principal: CurrentPrincipal) -> dict[str, str]:
            return {"sub": str(principal.sub)}

        # `raise_app_exceptions=False` is what makes an unhandled exception show up as the 500
        # a real server would send, instead of re-raising into the test.
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            yield http

    async def test_a_new_key_inside_the_cooldown_is_a_401_problem(
        self, client: httpx.AsyncClient, jwks: _JwksServer, clock: _Clock
    ) -> None:
        old, new = _Signer("key-a"), _Signer("key-b")
        jwks.publish(old)
        cfg = _settings(jwks)
        first = await client.get("/whoami", headers={"Authorization": f"Bearer {old.token(cfg)}"})
        assert first.status_code == 200
        jwks.publish(new)
        clock.advance(5)

        response = await client.get(
            "/whoami", headers={"Authorization": f"Bearer {new.token(cfg)}"}
        )

        assert response.status_code == 401
        assert response.headers["content-type"].startswith("application/problem+json")
        body = response.json()
        assert body["status"] == 401
        assert body["type"].endswith("/unauthorized")

        clock.advance(30)
        retried = await client.get("/whoami", headers={"Authorization": f"Bearer {new.token(cfg)}"})
        assert retried.status_code == 200

    async def test_an_unreachable_jwks_is_a_503_problem(self) -> None:
        with _JwksServer() as server:
            cfg = _settings(server)
        app = FastAPI()
        app.state.settings = cfg
        register_error_handlers(app)

        @app.get("/whoami")
        def whoami(principal: CurrentPrincipal) -> dict[str, str]:
            return {"sub": str(principal.sub)}

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            token = _Signer("key-a").token(cfg)
            response = await http.get("/whoami", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 503
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["type"].endswith("/upstream-unavailable")
