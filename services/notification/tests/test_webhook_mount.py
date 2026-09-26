"""Meta's webhook is served where the gateway sends it, and cannot be made to buffer without bound.

Two things, both about the door rather than the fold (the fold is `test_inbound_webhook.py`):

1. **The path the gateway forwards is the path this service must serve.** app-gateway forwards a
   request's path *unchanged* (`route.upstream + full_path`), so Meta's callback
   `https://<edge>/api/v1/webhooks/whatsapp` arrives here as `/api/v1/webhooks/whatsapp`. The router
   used to be mounted only at the root (`/webhooks/whatsapp`), so even once the gateway let the
   request through every delivery and every handshake was a 404. The root mount stays, for a
   developer pointing a tunnel straight at the service.

2. **The body is read before the signature can be checked**, so an unauthenticated caller reaching
   the service must not be able to make it buffer an unbounded one just to be told 401.

No database: the handshake is a pure function of the query string and the configured verify token,
and a body that carries no phone number is acknowledged before the directory is asked anything
(its session dependency is stubbed here so nothing tries to open an engine).
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import AsyncIterator, Iterator
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.api.webhooks import MAX_WEBHOOK_BODY_BYTES
from app.main import app
from platform_core.db import get_directory_session

GATEWAY_PATH = "/api/v1/webhooks/whatsapp"
ROOT_PATH = "/webhooks/whatsapp"
BOTH = [GATEWAY_PATH, ROOT_PATH]
# The repository's `ci-only-` prefix, which the secret scanner's exclude pattern recognises.
TOKEN = "ci-only-verify-token"
SECRET = "ci-only-app-secret"


def _sign(body: bytes) -> dict[str, str]:
    digest = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return {"X-Hub-Signature-256": f"sha256={digest}", "Content-Type": "application/json"}


def _body_of(size: int) -> bytes:
    """A JSON document of exactly `size` bytes that carries no phone number."""
    frame = b'{"object":"whatsapp_business_account","entry":[],"pad":""}'
    return frame[:-2] + b"a" * (size - len(frame)) + frame[-2:]


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A verify token and an App Secret, and a stub for the directory-session dependency."""
    monkeypatch.setattr(
        "app.api.webhooks.settings",
        lambda: SimpleNamespace(
            whatsapp_verify_token=SecretStr(TOKEN), whatsapp_app_secret=SecretStr(SECRET)
        ),
    )

    async def _no_session() -> AsyncIterator[None]:
        yield None

    app.dependency_overrides[get_directory_session] = _no_session
    yield
    app.dependency_overrides.pop(get_directory_session, None)


@pytest.fixture
async def http() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


class TestBothMounts:
    @pytest.mark.parametrize("path", BOTH)
    async def test_metas_handshake_is_answered(self, http: AsyncClient, path: str) -> None:
        response = await http.get(
            path,
            params={"hub.mode": "subscribe", "hub.verify_token": TOKEN, "hub.challenge": "1158201"},
        )

        assert response.status_code == 200, path
        assert response.text == "1158201"

    async def test_the_handshake_still_refuses_a_wrong_token_on_the_gateway_path(
        self, http: AsyncClient
    ) -> None:
        response = await http.get(
            GATEWAY_PATH,
            params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"},
        )

        assert response.status_code == 403

    @pytest.mark.parametrize("path", BOTH)
    async def test_an_unsigned_delivery_reaches_the_signature_check_and_is_refused(
        self, http: AsyncClient, path: str
    ) -> None:
        """A 404 here is the bug. A 401 is the route being reached and refusing an unsigned body,
        which is the answer this service is supposed to give."""
        response = await http.post(path, content=b"{}")

        assert response.status_code == 401, (path, response.text)

    @pytest.mark.parametrize("path", BOTH)
    async def test_a_signed_delivery_is_verified_over_the_exact_bytes_and_acknowledged(
        self, http: AsyncClient, path: str
    ) -> None:
        """Whitespace and key order the signature covers: parsing and re-serialising the body
        would change them and every genuine delivery would be refused."""
        body = b'{ "object" : "whatsapp_business_account",\n  "entry" : [ ] }'

        response = await http.post(path, content=body, headers=_sign(body))

        assert response.status_code == 200, (path, response.text)

    async def test_a_delivery_signed_with_the_wrong_secret_is_refused(
        self, http: AsyncClient
    ) -> None:
        body = b'{"entry":[]}'
        forged = {"X-Hub-Signature-256": "sha256=" + "0" * 64}

        assert (await http.post(GATEWAY_PATH, content=body, headers=forged)).status_code == 401

    def test_neither_mount_appears_in_the_openapi_document(self) -> None:
        """Meta's endpoint, not public API: it must not appear as though anyone else should call
        it."""
        assert not [p for p in app.openapi()["paths"] if "webhooks" in p]

    async def test_no_other_method_is_served_on_either_mount(self, http: AsyncClient) -> None:
        for path in BOTH:
            for method in ("PUT", "PATCH", "DELETE"):
                assert (await http.request(method, path, content=b"{}")).status_code == 405


class TestBodyIsBoundedBeforeTheSignatureIsChecked:
    def test_the_ceiling_is_metas_documented_three_mebibytes(self) -> None:
        """The same figure as `max_body_bytes` on the route in both gateway route tables;
        `tests/infra/test_route_table_parity.py` holds the three together."""
        assert MAX_WEBHOOK_BODY_BYTES == 3 * 1024 * 1024 == 3145728

    @pytest.mark.parametrize("path", BOTH)
    async def test_a_body_at_the_ceiling_is_still_verified_and_acknowledged(
        self, http: AsyncClient, path: str
    ) -> None:
        body = _body_of(MAX_WEBHOOK_BODY_BYTES)
        assert len(body) == MAX_WEBHOOK_BODY_BYTES

        response = await http.post(path, content=body, headers=_sign(body))

        assert response.status_code == 200, (path, response.text)

    @pytest.mark.parametrize("path", BOTH)
    async def test_a_body_one_byte_over_is_refused_even_when_correctly_signed(
        self, http: AsyncClient, path: str
    ) -> None:
        body = _body_of(MAX_WEBHOOK_BODY_BYTES + 1)

        response = await http.post(path, content=body, headers=_sign(body))

        assert response.status_code == 413, path
        assert response.json()["type"].endswith("/payload-too-large")

    async def test_the_refusal_does_not_echo_internals(self, http: AsyncClient) -> None:
        response = await http.post(GATEWAY_PATH, content=b"x" * (MAX_WEBHOOK_BODY_BYTES + 1))

        assert response.status_code == 413
        assert "Traceback" not in response.text and "webhooks.py" not in response.text

    async def test_an_honest_oversized_declaration_is_refused_without_reading_the_body(
        self, http: AsyncClient
    ) -> None:
        consumed = 0

        async def chunks() -> AsyncIterator[bytes]:
            nonlocal consumed
            for _ in range(8):
                consumed += 1
                yield b"z" * (1024 * 1024)

        response = await http.post(
            GATEWAY_PATH,
            content=chunks(),
            headers={"content-length": str(8 * 1024 * 1024)},
        )

        assert response.status_code == 413
        assert consumed == 0, "a declared-oversized body must not be read"

    async def test_a_lying_content_length_is_caught_by_the_counter(self, http: AsyncClient) -> None:
        """The header is a claim; the bytes are the fact."""
        response = await http.post(
            GATEWAY_PATH,
            content=b"x" * (MAX_WEBHOOK_BODY_BYTES + 1),
            headers={"content-length": "10"},
        )

        assert response.status_code == 413

    async def test_an_undeclared_body_is_counted_and_reading_stops_at_the_ceiling(
        self, http: AsyncClient
    ) -> None:
        """Chunked, no `Content-Length`: draining the rest would buffer exactly what was refused."""
        consumed = 0
        chunk = 512 * 1024

        async def chunks() -> AsyncIterator[bytes]:
            nonlocal consumed
            for _ in range(64):  # 32 MiB on offer
                consumed += 1
                yield b"y" * chunk

        response = await http.post(GATEWAY_PATH, content=chunks())

        assert response.status_code == 413
        assert consumed * chunk <= MAX_WEBHOOK_BODY_BYTES + 2 * chunk, (
            f"read {consumed} chunks of 64 before refusing"
        )
