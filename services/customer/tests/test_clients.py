"""`clients.deal_references` — the probe the aborted-conversion cleanup asks deal before it deletes
an orphan company or contact. Driven against a stubbed transport: no network, no deal-service.
Any failure is `ReferencesProbeUnavailable` (the handler raises it, so the message is redelivered),
and no response body ever reaches an exception or a log line."""

from __future__ import annotations

import uuid

import httpx
import pytest
from structlog.testing import capture_logs

from app import clients
from app.config import Settings
from platform_core.internal_auth import verify_service_token


def _settings() -> Settings:
    return Settings(deal_internal_url="http://deal.invalid")


async def _call(handler, **kwargs: object) -> clients.DealReferences:  # type: ignore[no-untyped-def]
    org = kwargs.pop("organization_id", uuid.uuid4())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await clients.deal_references(
            _settings(),
            org,  # type: ignore[arg-type]
            company_id=uuid.uuid4(),
            contact_id=uuid.uuid4(),
            exclude_deal_id=uuid.uuid4(),
            client=client,
        )


_COUNTS = {
    "company_deal_count": 1,
    "contact_deal_count": 0,
    "company_activity_count": 2,
    "contact_activity_count": 3,
}


async def test_the_deal_client_sends_a_service_token_for_audience_deal_with_the_org_claim() -> None:
    org = uuid.uuid4()
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["token"] = request.headers["X-Internal-Token"]
        seen["path"] = request.url.path
        return httpx.Response(200, json=_COUNTS)

    await _call(handler, organization_id=org)

    deal_settings = Settings(
        service_name="deal", internal_service_token=_settings().internal_service_token
    )
    claims = verify_service_token(deal_settings, seen["token"])
    assert claims["aud"] == "deal"
    assert claims["iss"] == "customer"
    assert claims["org"] == str(org)
    assert seen["path"] == f"/internal/v1/organizations/{org}/references"


async def test_the_client_parses_the_four_counts() -> None:
    result = await _call(lambda request: httpx.Response(200, json=_COUNTS))
    assert result == clients.DealReferences(**_COUNTS)


def _timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("slow", request=request)


def _refused(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused", request=request)


@pytest.mark.parametrize(
    "handler",
    [
        _timeout,
        _refused,
        lambda request: httpx.Response(500),
        lambda request: httpx.Response(403, json={"detail": "nope"}),
    ],
)
async def test_a_timeout_a_connection_error_a_500_and_a_403_each_raise_probe_unavailable(
    handler,  # type: ignore[no-untyped-def]
) -> None:
    with pytest.raises(clients.ReferencesProbeUnavailable):
        await _call(handler)


@pytest.mark.parametrize(
    "body", [{}, {**_COUNTS, "company_deal_count": -1}, {**_COUNTS, "contact_deal_count": "x"}]
)
async def test_a_malformed_body_raises_probe_unavailable(body: dict) -> None:
    with pytest.raises(clients.ReferencesProbeUnavailable):
        await _call(lambda request: httpx.Response(200, json=body))


async def test_the_query_carries_only_ids_and_no_response_body_reaches_the_exception() -> None:
    seen: dict[str, httpx.URL] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = request.url
        return httpx.Response(403, json={"detail": "Priya Sharma priya@stellar.example"})

    with capture_logs() as logs, pytest.raises(clients.ReferencesProbeUnavailable) as exc:
        await _call(handler)

    for value in seen["url"].params.values():
        uuid.UUID(value)
    assert set(seen["url"].params) == {"company_id", "contact_id", "exclude_deal_id"}
    assert "Priya" not in str(exc.value)
    assert "Priya" not in str(logs)
