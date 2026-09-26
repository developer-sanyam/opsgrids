"""Customer 360 (slice C1) — `GET /customers/{id}/360`.

Every upstream is faked **at the HTTP boundary** (`httpx.MockTransport` behind
`c1_360.client_factory`), so the real request building, identity propagation, timeouts and
response parsing are all exercised; only the network is absent. Runs against real PostgreSQL for
the contact, company and owner rows, like the rest of this suite.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import jwt
import pytest

from app import c1_360
from app.api import c1_customer360
from app.config import settings
from app.main import app
from platform_core.auth import Principal, get_principal
from tests.helpers import principal_for

pytestmark = pytest.mark.integration

Handler = Callable[[httpx.Request], Any]

WHATSAPP_ONLY = {
    "whatsapp": {
        "opted_in": True,
        "window_open_until": "2026-09-27T10:00:00+00:00",
        "last_message_at": "2026-09-26T10:00:00+00:00",
        "conversations": 2,
    },
    "email": {"subscribed": False, "last_email_at": "2026-09-20T08:00:00+00:00"},
}


def _deal(stage: str, probability: int, created: str = "2026-09-01T00:00:00+00:00") -> dict:
    return {
        "id": str(uuid.uuid4()),
        "name": "Deal",
        "stage": stage,
        "probability": probability,
        "created_at": created,
        "value_minor": 100,
    }


class Fake:
    """Records every request the aggregator makes and answers per upstream port."""

    def __init__(self, deal: Handler | None = None, notification: Handler | None = None) -> None:
        self.calls: list[httpx.Request] = []
        self._deal = deal or (
            lambda r: httpx.Response(
                200, json={"items": [_deal("negotiation", 60), _deal("closed_won", 100)]}
            )
        )
        self._notification = notification or (lambda r: httpx.Response(200, json=WHATSAPP_ONLY))

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        handler = self._deal if request.url.port == 8131 else self._notification
        result = handler(request)
        if asyncio.iscoroutine(result):
            result = await result
        return result

    def to(self, port: int) -> list[httpx.Request]:
        return [r for r in self.calls if r.url.port == port]


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Callable[..., Fake]:
    def install(deal: Handler | None = None, notification: Handler | None = None) -> Fake:
        upstream = Fake(deal, notification)
        monkeypatch.setattr(
            c1_360,
            "client_factory",
            lambda timeout: httpx.AsyncClient(
                transport=httpx.MockTransport(upstream), timeout=timeout
            ),
        )
        return upstream

    return install


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def _reader(org_id: uuid.UUID, entitlements: list[str] | None = None) -> Principal:
    return principal_for(
        org_id,
        ["crm.book.contact.read:ORG"],
        entitlements=entitlements if entitlements is not None else ["crm", "engage"],
    )


IDENTITY = {"X-User-Id": "u-1", "X-Org-Id": "o-1", "X-Perms": "crm.pipeline.deal.read:OWN"}


async def _get(client, contact_id, headers: dict[str, str] | None = None):
    return await client.get(f"/api/v1/customers/{contact_id}/360", headers=headers or {})


class TestAggregation:
    async def test_full_picture(self, client, org_id, data, fake) -> None:
        upstream = fake()
        with as_principal(_reader(org_id)):
            response = await _get(client, data["contact_a_id"], IDENTITY)
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["customer"]["name"] == "Rajesh Khanna"
        assert body["customer"]["company"] == "Northwind Logistics"
        assert body["customer"]["city"] == "Mumbai"
        assert body["customer"]["owner"]["name"] == "Sofia Marchetti"
        assert body["customer"]["status"] == "active"
        assert body["customer"]["tags"] == []
        assert body["crm"] == {"stage": "negotiation", "open_opportunities": 1, "hot": True}
        assert body["channels"]["whatsapp"]["conversations"] == 2
        assert body["channels"]["email"]["subscribed"] is False
        # Orders, finance and support have no upstream: null and named, never invented.
        assert body["orders"] is None and body["finance"] is None and body["support"] is None
        assert body["unavailable"] == ["orders", "finance", "support"]
        kinds = [e["kind"] for e in body["timeline"]]
        assert "contact_created" in kinds and "whatsapp_message" in kinds
        assert [e["at"] for e in body["timeline"]] == sorted(
            (e["at"] for e in body["timeline"]), reverse=True
        )
        assert len(upstream.calls) == 2

    async def test_deal_is_asked_for_this_account_under_the_callers_identity(
        self, client, org_id, data, fake
    ) -> None:
        upstream = fake()
        with as_principal(_reader(org_id)):
            await _get(client, data["contact_a_id"], {**IDENTITY, "X-Unrelated": "leak"})
        (deal_call,) = upstream.to(8131)
        assert deal_call.url.path == "/deals"
        assert deal_call.url.params["company_id"] == str(data["company_a_id"])
        assert deal_call.headers["X-Perms"] == "crm.pipeline.deal.read:OWN"
        assert deal_call.headers["X-User-Id"] == "u-1"
        assert "X-Unrelated" not in deal_call.headers

    async def test_notification_gets_a_service_token_bound_to_the_tenant(
        self, client, org_id, data, fake
    ) -> None:
        upstream = fake()
        with as_principal(_reader(org_id)):
            await _get(client, data["contact_a_id"])
        (call,) = upstream.to(8151)
        assert call.url.path == f"/internal/v1/contacts/{data['contact_a_id']}/messaging-summary"
        claims = jwt.decode(
            call.headers["X-Internal-Token"],
            settings().internal_service_token.get_secret_value(),
            algorithms=["HS256"],
            audience="notification",
        )
        assert claims["org"] == str(org_id)
        assert claims["iss"] == "customer"
        # The address is a header for the lookup, never part of the URL.
        assert call.headers["X-Contact-Email"] == "rajesh.khanna@northwind.co"
        assert "northwind.co" not in str(call.url)

    async def test_open_stage_and_hotness_are_derived_from_open_deals_only(
        self, client, org_id, data, fake
    ) -> None:
        fake(
            deal=lambda r: httpx.Response(
                200,
                json={
                    "items": [
                        _deal("lead", 10),
                        _deal("proposal", 40),
                        _deal("closed_lost", 95),  # closed: must not make the account hot
                    ]
                },
            )
        )
        with as_principal(_reader(org_id)):
            body = (await _get(client, data["contact_a_id"])).json()
        assert body["crm"] == {"stage": "proposal", "open_opportunities": 2, "hot": False}

    async def test_an_account_with_no_deals_is_an_empty_section_not_a_missing_one(
        self, client, org_id, data, fake
    ) -> None:
        fake(deal=lambda r: httpx.Response(200, json={"items": []}))
        with as_principal(_reader(org_id)):
            body = (await _get(client, data["contact_a_id"])).json()
        assert body["crm"] == {"stage": None, "open_opportunities": 0, "hot": False}
        assert "crm" not in body["unavailable"]


class TestPartialResults:
    async def test_a_failing_upstream_nulls_only_its_section(
        self, client, org_id, data, fake
    ) -> None:
        fake(deal=lambda r: httpx.Response(500, text="boom"))
        with as_principal(_reader(org_id)):
            response = await _get(client, data["contact_a_id"])
        assert response.status_code == 200
        body = response.json()
        assert body["crm"] is None
        assert body["unavailable"] == ["orders", "finance", "support", "crm"]
        assert body["channels"]["whatsapp"] is not None
        assert "boom" not in response.text

    async def test_every_upstream_down_still_returns_the_contact(
        self, client, org_id, data, fake
    ) -> None:
        def down(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        fake(deal=down, notification=down)
        with as_principal(_reader(org_id)):
            response = await _get(client, data["contact_a_id"])
        assert response.status_code == 200
        body = response.json()
        assert body["customer"]["name"] == "Rajesh Khanna"
        assert body["crm"] is None
        assert body["channels"] == {"whatsapp": None, "email": None}
        assert set(body["unavailable"]) == {"orders", "finance", "support", "crm", "channels"}
        # Only what this service itself knows: no deal, WhatsApp or email entries.
        assert {e["kind"] for e in body["timeline"]} == {"contact_created", "activity"}

    async def test_a_forbidden_upstream_is_unavailable_not_an_error(
        self, client, org_id, data, fake
    ) -> None:
        fake(deal=lambda r: httpx.Response(403, json={"detail": "no"}))
        with as_principal(_reader(org_id)):
            response = await _get(client, data["contact_a_id"])
        assert response.status_code == 200
        assert "crm" in response.json()["unavailable"]

    async def test_a_malformed_upstream_body_is_unavailable(
        self, client, org_id, data, fake
    ) -> None:
        fake(notification=lambda r: httpx.Response(200, json={"whatsapp": "not-an-object"}))
        with as_principal(_reader(org_id)):
            response = await _get(client, data["contact_a_id"])
        assert response.status_code == 200
        assert "channels" in response.json()["unavailable"]

    async def test_a_slow_upstream_times_out_and_the_others_still_answer(
        self, client, org_id, data, fake, monkeypatch
    ) -> None:
        async def slow(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(3)
            return httpx.Response(200, json={"items": []})

        fake(deal=slow)
        quick = settings().model_copy(update={"customer360_upstream_timeout_seconds": 0.2})
        monkeypatch.setattr(c1_customer360, "settings", lambda: quick)
        started = time.monotonic()
        with as_principal(_reader(org_id)):
            response = await _get(client, data["contact_a_id"])
        elapsed = time.monotonic() - started
        assert response.status_code == 200
        body = response.json()
        assert body["crm"] is None and "crm" in body["unavailable"]
        assert body["channels"]["whatsapp"] is not None
        assert elapsed < 2.0

    async def test_upstreams_run_concurrently(
        self, client, org_id, data, fake, monkeypatch
    ) -> None:
        async def slowish_deal(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(0.5)
            return httpx.Response(200, json={"items": []})

        async def slowish_notification(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(0.5)
            return httpx.Response(200, json=WHATSAPP_ONLY)

        fake(deal=slowish_deal, notification=slowish_notification)
        started = time.monotonic()
        with as_principal(_reader(org_id)):
            response = await _get(client, data["contact_a_id"])
        assert response.status_code == 200
        assert time.monotonic() - started < 0.95  # two 0.5s calls, not one after the other


class TestEntitlements:
    async def test_a_product_the_org_lacks_is_null_and_never_called(
        self, client, org_id, data, fake
    ) -> None:
        upstream = fake()
        with as_principal(_reader(org_id, entitlements=["crm"])):
            response = await _get(client, data["contact_a_id"])
        assert response.status_code == 200
        body = response.json()
        assert body["channels"] == {"whatsapp": None, "email": None}
        assert "channels" in body["unavailable"]
        assert body["crm"] is not None
        assert upstream.to(8151) == []

    async def test_no_crm_entitlement_is_payment_required(self, client, org_id, data, fake) -> None:
        upstream = fake()
        with as_principal(_reader(org_id, entitlements=["engage"])):
            response = await _get(client, data["contact_a_id"])
        assert response.status_code == 402
        assert upstream.calls == []


class TestAuthorization:
    async def test_unauthenticated_is_rejected(self, client, data, fake) -> None:
        upstream = fake()
        assert (await _get(client, data["contact_a_id"])).status_code == 401
        assert upstream.calls == []

    async def test_missing_the_permission_is_forbidden(self, client, org_id, data, fake) -> None:
        upstream = fake()
        with as_principal(principal_for(org_id, ["crm.book.company.read:ORG"])):
            response = await _get(client, data["contact_a_id"])
        assert response.status_code == 403
        assert upstream.calls == []

    async def test_own_scope_cannot_read_another_reps_contact(
        self, client, org_id, data, fake
    ) -> None:
        upstream = fake()
        principal = principal_for(
            org_id,
            ["crm.book.contact.read:OWN"],
            entitlements=["crm", "engage"],
            subject_ref=data["subject_a"],
        )
        with as_principal(principal):
            refused = await _get(client, data["contact_b_id"])
            allowed = await _get(client, data["contact_a_id"])
        assert refused.status_code == 403
        assert allowed.status_code == 200
        # The refused request reached no upstream: only the allowed one fanned out.
        assert len(upstream.calls) == 2

    async def test_another_organizations_contact_is_not_found(
        self, client, org_id, data, fake
    ) -> None:
        upstream = fake()
        with as_principal(_reader(uuid.uuid4())):  # a different tenant entirely
            response = await _get(client, data["contact_a_id"])
        assert response.status_code == 404
        assert upstream.calls == []

    async def test_an_unknown_id_and_a_company_id_are_not_found(
        self, client, org_id, data, fake
    ) -> None:
        fake()
        with as_principal(_reader(org_id)):
            assert (await _get(client, uuid.uuid4())).status_code == 404
            # The 360 is a contact's card; an account id is not one.
            assert (await _get(client, data["company_a_id"])).status_code == 404


class TestMasking:
    async def test_phone_and_email_are_masked_and_nothing_is_unmasked(
        self, client, org_id, data, fake
    ) -> None:
        upstream = fake()
        with as_principal(_reader(org_id)):
            response = await _get(client, data["contact_a_id"])
        body = response.json()
        assert body["customer"]["phone"] == "+91 98 xxx210"
        assert body["customer"]["email"] != "rajesh.khanna@northwind.co"
        assert "rajesh.khanna" not in response.text
        assert "@" in body["customer"]["email"]
        # No request to any upstream carried a phone number in its URL or body.
        for call in upstream.calls:
            assert "210" not in str(call.url)
            assert call.content == b""
        assert json.loads(response.text)["customer"]["id"] == str(data["contact_a_id"])
