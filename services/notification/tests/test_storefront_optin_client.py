"""`fetch_storefront_opt_in`: the token it sends, and the three answers it tells apart."""

from __future__ import annotations

import uuid

import httpx
import jwt
import pytest

from app.config import settings
from app.storefront_optin import StorefrontUnavailable, fetch_storefront_opt_in

ORG = str(uuid.uuid4())
ENQUIRY = str(uuid.uuid4())


def _client(status: int, body: object = None, seen: list | None = None) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_a_ticked_enquiry_is_returned_and_the_token_names_storefront_and_the_org() -> None:
    seen: list[httpx.Request] = []
    body = {
        "name": "Priya",
        "email": "p@example.com",
        "phone": "+919876543210",
        "opted_in_at": "2026-10-01T09:30:00+00:00",
    }
    async with _client(200, body, seen) as client:
        found = await fetch_storefront_opt_in(settings(), ORG, ENQUIRY, client=client)
    assert found is not None and found.phone == "+919876543210"
    [request] = seen
    assert request.url.path == f"/internal/v1/enquiries/{ENQUIRY}/whatsapp-opt-in"
    claims = jwt.decode(
        request.headers["X-Internal-Token"],
        settings().internal_service_token.get_secret_value(),
        algorithms=["HS256"],
        audience="storefront",
    )
    assert (claims["iss"], claims["org"]) == ("notification", ORG)


async def test_not_found_is_a_definite_none() -> None:
    async with _client(404, {"detail": "No such enquiry"}) as client:
        assert await fetch_storefront_opt_in(settings(), ORG, ENQUIRY, client=client) is None


@pytest.mark.parametrize(("status", "body"), [(503, None), (403, None), (200, {"nope": 1})])
async def test_anything_else_is_unavailable_so_the_event_is_retried(status, body) -> None:
    async with _client(status, body) as client:
        with pytest.raises(StorefrontUnavailable):
            await fetch_storefront_opt_in(settings(), ORG, ENQUIRY, client=client)
