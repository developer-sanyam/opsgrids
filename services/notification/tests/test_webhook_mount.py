"""S07, the second break: the path the gateway forwards is the path this service must serve.

app-gateway forwards a request's path *unchanged* (`route.upstream + full_path`), so Meta's
callback `https://<edge>/api/v1/webhooks/whatsapp` arrives here as `/api/v1/webhooks/whatsapp`.
The router used to be mounted only at the root (`/webhooks/whatsapp`), so even once the gateway
let the request through, every delivery and every handshake was a 404 from this service.

No database: the handshake is a pure function of the query string and the configured verify token,
and a delivery with no App Secret configured is refused before it touches the directory session
(which is stubbed here so the dependency does not try to open an engine).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.main import app
from platform_core.db import get_directory_session

GATEWAY_PATH = "/api/v1/webhooks/whatsapp"
ROOT_PATH = "/webhooks/whatsapp"
TOKEN = "verify-me"


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A verify token, no App Secret (so a POST is refused with 401 before any lookup), and a
    stub for the directory-session dependency."""
    monkeypatch.setattr(
        "app.api.webhooks.settings",
        lambda: SimpleNamespace(whatsapp_verify_token=SecretStr(TOKEN), whatsapp_app_secret=None),
    )

    async def _no_session() -> AsyncIterator[None]:
        yield None

    app.dependency_overrides[get_directory_session] = _no_session
    yield
    app.dependency_overrides.pop(get_directory_session, None)


@pytest.mark.parametrize("path", [GATEWAY_PATH, ROOT_PATH])
async def test_metas_handshake_is_answered_on_both_mounts(path: str) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            path,
            params={"hub.mode": "subscribe", "hub.verify_token": TOKEN, "hub.challenge": "1158201"},
        )
    assert response.status_code == 200, path
    assert response.text == "1158201"


async def test_the_handshake_still_refuses_a_wrong_token_on_the_gateway_path() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            GATEWAY_PATH,
            params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"},
        )
    assert response.status_code == 403


@pytest.mark.parametrize("path", [GATEWAY_PATH, ROOT_PATH])
async def test_a_delivery_reaches_the_signature_check_on_both_mounts(path: str) -> None:
    """A 404 here is the bug. A 401 is the route being reached and refusing an unsigned body —
    the answer the service is supposed to give (no signature, nothing else happens)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(path, content=b"{}")
    assert response.status_code == 401, (path, response.text)


def test_neither_mount_appears_in_the_openapi_document() -> None:
    """Meta's endpoint, not public API: it must not appear as though anyone else should call it."""
    assert not [p for p in app.openapi()["paths"] if "webhooks" in p]
