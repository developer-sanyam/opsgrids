"""A verifier meeting a token signed by a key it has not fetched yet.

PyJWT 2.14 (GHSA-2gx3-rcp4-g85q) stopped `PyJWKClient` from re-fetching the JWKS for every
token carrying an unknown `kid`: after any successful fetch it waits `cooldown_duration`
(30 s) before a forced refresh, and in the meantime `get_signing_key` raises
`PyJWKClientError`. That exception is a `PyJWTError` but not an `InvalidTokenError`, so
`decode_token` let it through and the app answered an unhandled 500 — for every service and
gateway at once, in the 30 seconds after auth-service starts signing with a new key.

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
from platform_core.errors import UnauthorizedProblem, register_error_handlers

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
        # The client is told to re-authenticate and retry, in the standard header and in a
        # machine-readable code, and the message says why without naming a key.
        assert problem.headers["WWW-Authenticate"].startswith('Bearer error="invalid_token"')
        assert int(problem.headers["Retry-After"]) > 0
        assert problem.errors is not None
        assert problem.errors[0]["code"] == "signing_key_unavailable"
        # The documented `errors` shape (`field`, `code`, `message`), which the SPAs parse.
        assert problem.errors[0]["field"] == "authorization"
        assert problem.errors[0]["message"] == problem.detail
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
    def test_is_a_401_rather_than_an_unhandled_error(self) -> None:
        """`PyJWKClientConnectionError` is a `PyJWKClientError`, and is handled with it."""
        with _JwksServer() as server:
            cfg = _settings(server)
        # The server is closed now: nothing is listening on that port.
        with pytest.raises(UnauthorizedProblem) as caught:
            decode_token(cfg, _Signer("key-a").token(cfg), expected_type="access")

        assert caught.value.status_code == 401
        assert caught.value.errors is not None
        assert caught.value.errors[0]["code"] == "signing_key_unavailable"


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
        assert response.headers["www-authenticate"].startswith('Bearer error="invalid_token"')
        body = response.json()
        assert body["status"] == 401
        assert body["type"].endswith("/unauthorized")
        assert body["errors"][0]["code"] == "signing_key_unavailable"

        clock.advance(30)
        retried = await client.get("/whoami", headers={"Authorization": f"Bearer {new.token(cfg)}"})
        assert retried.status_code == 200


class TestTheWarning:
    def test_one_structured_line_per_occurrence_names_the_kid_and_nothing_else_secret(
        self, jwks: _JwksServer, clock: _Clock
    ) -> None:
        known, stranger = _Signer("key-a"), _Signer("never-published")
        jwks.publish(known)
        cfg = _settings(jwks)
        _verify(cfg, known)
        token = stranger.token(cfg)

        with structlog.testing.capture_logs() as logs, pytest.raises(UnauthorizedProblem):
            decode_token(cfg, token, expected_type="access")

        lines = [e for e in logs if e["event"] == "auth.jwks.signing_key_unavailable"]
        assert len(lines) == 1
        line = lines[0]
        assert line["log_level"] == "warning"
        assert line["kid"] == "never-published"
        assert line["reason"] == "signing_key_not_found"
        # Nothing that identifies the token or the key leaves the process through the log.
        rendered = json.dumps(line, default=str)
        assert token not in rendered
        assert token.split(".")[1] not in rendered  # the claims segment
        assert token.split(".")[2] not in rendered  # the signature

    def test_an_unreachable_jwks_is_logged_as_that(self) -> None:
        with _JwksServer() as server:
            cfg = _settings(server)
        with structlog.testing.capture_logs() as logs, pytest.raises(UnauthorizedProblem):
            decode_token(cfg, _Signer("key-a").token(cfg), expected_type="access")

        [line] = [e for e in logs if e["event"] == "auth.jwks.signing_key_unavailable"]
        assert line["reason"] == "jwks_unreachable"
        assert line["kid"] == "key-a"

    def test_a_flood_of_one_kid_is_one_line(self, jwks: _JwksServer, clock: _Clock) -> None:
        known, stranger = _Signer("key-a"), _Signer("never-published")
        jwks.publish(known)
        cfg = _settings(jwks)
        _verify(cfg, known)
        token = stranger.token(cfg)

        with structlog.testing.capture_logs() as logs:
            for _ in range(200):
                with pytest.raises(UnauthorizedProblem):
                    decode_token(cfg, token, expected_type="access")

        assert len([e for e in logs if e["event"] == "auth.jwks.signing_key_unavailable"]) == 1

    def test_a_flood_of_distinct_kids_is_bounded(self, jwks: _JwksServer, clock: _Clock) -> None:
        known = _Signer("key-a")
        jwks.publish(known)
        cfg = _settings(jwks)
        _verify(cfg, known)

        with structlog.testing.capture_logs() as logs:
            for n in range(500):
                with pytest.raises(UnauthorizedProblem):
                    decode_token(cfg, _Signer(f"random-{n}").token(cfg), expected_type="access")

        emitted = [e for e in logs if e["event"] == "auth.jwks.signing_key_unavailable"]
        assert 1 <= len(emitted) <= auth_module.SIGNING_KEY_WARNING_MAX_KIDS + 1

    def test_a_hostile_kid_cannot_inject_lines_or_balloon_a_line(
        self, jwks: _JwksServer, clock: _Clock
    ) -> None:
        known = _Signer("key-a")
        jwks.publish(known)
        cfg = _settings(jwks)
        _verify(cfg, known)
        hostile = _Signer('ab\n{"event": "forged"}\x1b[31m' + "x" * 10_000)

        with structlog.testing.capture_logs() as logs, pytest.raises(UnauthorizedProblem):
            decode_token(cfg, hostile.token(cfg), expected_type="access")

        [line] = [e for e in logs if e["event"] == "auth.jwks.signing_key_unavailable"]
        assert len(line["kid"]) <= auth_module.SIGNING_KEY_WARNING_KID_CHARS
        assert "\n" not in line["kid"]
        assert "\x1b" not in line["kid"]


class TestWarningThrottle:
    """The rate limiter on its own, with a clock the test owns."""

    def _throttle(self, now: list[float]) -> auth_module._WarningThrottle:
        return auth_module._WarningThrottle(interval=60, max_kids=3, clock=lambda: now[0])

    def test_admits_the_first_occurrence_and_counts_what_it_suppresses(self) -> None:
        now = [1000.0]
        throttle = self._throttle(now)

        assert throttle.admit("k") == (True, 0)
        assert throttle.admit("k") == (False, 0)
        assert throttle.admit("k") == (False, 0)
        now[0] += 59
        assert throttle.admit("k") == (False, 0)
        now[0] += 2
        # The next line says how many it stood in for, so a flood is visible as a number.
        assert throttle.admit("k") == (True, 3)
        assert throttle.admit("k") == (False, 0)

    def test_kids_are_throttled_independently(self) -> None:
        throttle = self._throttle([1000.0])

        assert throttle.admit("a") == (True, 0)
        assert throttle.admit("b") == (True, 0)
        assert throttle.admit("a") == (False, 0)

    def test_the_number_of_remembered_kids_is_bounded(self) -> None:
        now = [1000.0]
        throttle = self._throttle(now)

        admitted = sum(throttle.admit(f"k{n}")[0] for n in range(1000))

        # `max_kids` distinct kids, plus one shared slot for everything past them.
        assert admitted == 3 + 1
        assert len(throttle._seen) <= 3 + 1

    def test_forgets_old_kids_to_make_room(self) -> None:
        now = [1000.0]
        throttle = self._throttle(now)
        for n in range(3):
            throttle.admit(f"old{n}")
        now[0] += 61

        assert throttle.admit("fresh") == (True, 0)
