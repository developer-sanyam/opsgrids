"""`app.orders.finance_client.resolve_finance_customer` on the wire: what it sends, and how it
reads each kind of answer. No network — an `httpx.MockTransport` stands in for finance."""

from __future__ import annotations

import json
import uuid

import httpx
import jwt
import pytest

from app.config import Settings
from app.orders.finance_client import FinanceUnavailable, resolve_finance_customer

ORG = uuid.uuid4()
CONTACT = uuid.uuid4()


async def _resolve(cfg: Settings, handler) -> uuid.UUID | None:  # type: ignore[no-untyped-def]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await resolve_finance_customer(cfg, ORG, CONTACT, client=client)


async def test_sends_the_contact_with_a_token_for_finance_and_this_org() -> None:
    cfg = Settings()
    seen: list[httpx.Request] = []
    customer = uuid.uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"customer_id": str(customer)})

    assert await _resolve(cfg, handler) == customer
    (request,) = seen
    assert request.method == "POST"
    assert request.url.path == (
        f"/internal/v1/organizations/{ORG}/customers:resolve-for-crm-contact"
    )
    assert json.loads(request.content) == {"crm_contact_id": str(CONTACT)}
    claims = jwt.decode(
        request.headers["X-Internal-Token"],
        cfg.internal_service_token.get_secret_value(),
        algorithms=["HS256"],
        audience="finance",
    )
    assert claims["org"] == str(ORG)
    assert claims["iss"] == "operations"


async def test_404_is_a_definite_no() -> None:
    assert await _resolve(Settings(), lambda r: httpx.Response(404, json={})) is None


@pytest.mark.parametrize("status", [401, 403, 422, 500, 503])
async def test_anything_else_is_unavailable(status: int) -> None:
    with pytest.raises(FinanceUnavailable):
        await _resolve(Settings(), lambda r: httpx.Response(status, json={}))


async def test_an_unparseable_200_is_unavailable() -> None:
    with pytest.raises(FinanceUnavailable):
        await _resolve(Settings(), lambda r: httpx.Response(200, json={"nope": 1}))


async def test_unreachable_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(FinanceUnavailable):
        await _resolve(Settings(), handler)
