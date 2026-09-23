"""The internal-API callers — without a network, and without customer-service or deal-service
actually running. Same shape as notification-service's `test_targets.py`: drive the real
function against a stubbed transport, so the behaviour worth protecting (translating an HTTP
response into a return value or the right exception) is exercised for real.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from app import clients
from app.config import Settings


def _settings() -> Settings:
    return Settings(
        customer_internal_url="http://customer.invalid",
        deal_internal_url="http://deal.invalid",
    )


async def _with_transport(handler, monkeypatch: pytest.MonkeyPatch, coro_factory):
    mock = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def patched(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("timeout", None)
        return original(transport=mock, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", patched)
    return await coro_factory()


class TestEnsureCompanyAndContact:
    async def test_the_happy_path_returns_both_ids(self, monkeypatch: pytest.MonkeyPatch) -> None:
        company_id, contact_id = uuid.uuid4(), uuid.uuid4()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"company_id": str(company_id), "contact_id": str(contact_id)}
            )

        result = await _with_transport(
            handler,
            monkeypatch,
            lambda: clients.ensure_company_and_contact(
                _settings(),
                uuid.uuid4(),
                company_id=uuid.uuid4(),
                contact_id=uuid.uuid4(),
                company_name="Stellar Robotics",
                owner_id=uuid.uuid4(),
                contact_full_name="Priya Sharma",
                contact_email="priya@stellar.example",
                contact_raw_phone=None,
            ),
        )
        assert result == (company_id, contact_id)

    async def test_no_email_can_still_return_no_contact(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        company_id = uuid.uuid4()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"company_id": str(company_id), "contact_id": None})

        result = await _with_transport(
            handler,
            monkeypatch,
            lambda: clients.ensure_company_and_contact(
                _settings(),
                uuid.uuid4(),
                company_id=uuid.uuid4(),
                contact_id=uuid.uuid4(),
                company_name="Vertex Freight",
                owner_id=uuid.uuid4(),
                contact_full_name="Arjun Verma",
                contact_email=None,
                contact_raw_phone=None,
            ),
        )
        assert result == (company_id, None)

    async def test_an_unknown_owner_raises_owner_unknown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"detail": "not found"})

        with pytest.raises(clients.OwnerUnknown):
            await _with_transport(
                handler,
                monkeypatch,
                lambda: clients.ensure_company_and_contact(
                    _settings(),
                    uuid.uuid4(),
                    company_id=uuid.uuid4(),
                    contact_id=uuid.uuid4(),
                    company_name="Stellar Robotics",
                    owner_id=uuid.uuid4(),
                    contact_full_name="Priya Sharma",
                    contact_email=None,
                    contact_raw_phone=None,
                ),
            )

    async def test_a_5xx_raises_conversion_unavailable_not_owner_unknown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A transient outage must be retried, never confused with a permanent refusal."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="upstream is down")

        with pytest.raises(clients.ConversionUnavailable):
            await _with_transport(
                handler,
                monkeypatch,
                lambda: clients.ensure_company_and_contact(
                    _settings(),
                    uuid.uuid4(),
                    company_id=uuid.uuid4(),
                    contact_id=uuid.uuid4(),
                    company_name="Stellar Robotics",
                    owner_id=uuid.uuid4(),
                    contact_full_name="Priya Sharma",
                    contact_email=None,
                    contact_raw_phone=None,
                ),
            )

    async def test_the_request_carries_a_token_addressed_to_customer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["token"] = request.headers.get("X-Internal-Token")
            seen["path"] = request.url.path
            return httpx.Response(200, json={"company_id": str(uuid.uuid4()), "contact_id": None})

        await _with_transport(
            handler,
            monkeypatch,
            lambda: clients.ensure_company_and_contact(
                _settings(),
                uuid.uuid4(),
                company_id=uuid.uuid4(),
                contact_id=uuid.uuid4(),
                company_name="Stellar Robotics",
                owner_id=uuid.uuid4(),
                contact_full_name="Priya Sharma",
                contact_email=None,
                contact_raw_phone=None,
            ),
        )
        assert seen["token"]
        assert str(seen["path"]).endswith("/leads:convert")


class TestEnsureDeal:
    async def test_the_happy_path_returns_the_deal_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        deal_id = uuid.uuid4()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"deal_id": str(deal_id)})

        result = await _with_transport(
            handler,
            monkeypatch,
            lambda: clients.ensure_deal(
                _settings(),
                uuid.uuid4(),
                deal_id=uuid.uuid4(),
                company_id=uuid.uuid4(),
                contact_id=None,
                owner_id=uuid.uuid4(),
                name="Stellar Robotics — Priya Sharma",
                close_date="2026-10-23",
                source="form",
            ),
        )
        assert result == deal_id

    async def test_a_5xx_raises_conversion_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        with pytest.raises(clients.ConversionUnavailable):
            await _with_transport(
                handler,
                monkeypatch,
                lambda: clients.ensure_deal(
                    _settings(),
                    uuid.uuid4(),
                    deal_id=uuid.uuid4(),
                    company_id=uuid.uuid4(),
                    contact_id=None,
                    owner_id=uuid.uuid4(),
                    name="x",
                    close_date="2026-10-23",
                    source="form",
                ),
            )
