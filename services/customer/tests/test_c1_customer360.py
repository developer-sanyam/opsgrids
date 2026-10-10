"""Customer 360 (slice C1) — `GET /customers/{id}/360`.

Every upstream is faked **at the HTTP boundary** (`httpx.MockTransport` behind
`c1_360.client_factory`), so the real request building, identity propagation, timeouts and
response parsing are all exercised; only the network is absent. Runs against real PostgreSQL for
the contact, company and owner rows, like the rest of this suite.

**The deal fake is mount-aware.** It answers only the routes the committed OpenAPI document of
the real deal app declares (`libs/contracts/openapi/deal.json`, whose keys carry the `/api/v1`
mount), and a 404 for anything else, exactly as production does. A fake that answered by port
alone let the 360 ask deal for `/deals`, a path nobody serves, while every test passed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
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

#: `test_c1_customer360.py`, `tests/`, `customer`, `services`, ROOT.
DEAL_OPENAPI = Path(__file__).resolve().parents[3] / "libs" / "contracts" / "openapi" / "deal.json"
HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})


class RealDealRoutes:
    """The deal service's real route table: the committed OpenAPI document of its real app.

    `libs/contracts/openapi/deal.json` is `app.openapi()` of the deal app, byte-checked by `make
    openapi-check`, and its path keys carry the `/api/v1` mount (`/api/v1/deals`,
    `/api/v1/deals/{deal_id}`). A fake upstream that answers only what this table serves cannot
    hide a wrong mount path: the call gets the 404 the real service would give."""

    def __init__(self) -> None:
        self.document: dict[str, Any] = json.loads(DEAL_OPENAPI.read_text(encoding="utf-8"))
        self._routes: list[tuple[re.Pattern[str], str, frozenset[str]]] = []
        for template, operations in self.document["paths"].items():
            segments = [
                "[^/]+" if segment.startswith("{") else re.escape(segment)
                for segment in template.split("/")
            ]
            self._routes.append(
                (
                    re.compile("^" + "/".join(segments) + "$"),
                    template,
                    frozenset(method.upper() for method in operations if method in HTTP_METHODS),
                )
            )

    def _template(self, method: str, path: str) -> str | None:
        """The document's path key that a concrete request path fills, for a method it declares."""
        return next(
            (
                template
                for pattern, template, methods in self._routes
                if pattern.match(path) is not None and method.upper() in methods
            ),
            None,
        )

    def serves(self, method: str, path: str) -> bool:
        return self._template(method, path) is not None

    def operation(self, method: str, path: str) -> dict[str, Any]:
        """What the document declares for `method` on this concrete request `path`."""
        template = self._template(method, path)
        assert template is not None, f"deal serves no {method} {path}"
        return self.document["paths"][template][method.lower()]

    def query_parameters(self, method: str, path: str) -> dict[str, dict[str, Any]]:
        """Each query parameter the route declares, by name, as the document describes it (its
        `schema` carries the bounds)."""
        parameters = self.operation(method, path).get("parameters", [])
        return {p["name"]: p for p in parameters if p["in"] == "query"}

    def listed_item(self, method: str, path: str) -> dict[str, Any]:
        """The schema of one element of the page the route answers 200 with, found by following
        the route's own response (`$ref` to the page, then the page's `items`), never by name."""
        response = self.operation(method, path)["responses"]["200"]["content"]["application/json"]
        page = self._schema(response["schema"])
        return self._schema(page["properties"]["items"]["items"])

    def _schema(self, ref: dict[str, Any]) -> dict[str, Any]:
        return self.document["components"]["schemas"][ref["$ref"].rsplit("/", 1)[-1]]


DEAL_ROUTES = RealDealRoutes()

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
        if request.url.port == 8131 and not DEAL_ROUTES.serves(request.method, request.url.path):
            # What the real deal app answers for a path it does not route.
            return httpx.Response(404, json={"detail": "Not Found"})
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
        assert deal_call.url.path == "/api/v1/deals"
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


async def _sent_to_deal() -> httpx.Request:
    """The one request `fetch_crm` sends, caught at the HTTP boundary and not read back from the
    constants it is built from: a renamed parameter or a changed verb is only visible here."""
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"items": []})

    org = uuid.uuid4()
    principal = principal_for(org, ["crm.pipeline.deal.read:ORG"], entitlements=["crm"])
    caller = c1_360.CallerContext(
        organization_id=org, user_id=principal.sub, principal=principal, forward_headers={}
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(record)) as http:
        await c1_360.fetch_crm(http, settings(), caller, uuid.uuid4())
    (request,) = seen
    return request


class TestDealLegPath:
    """The deal leg asks for the path deal really serves. These fail on a mount mistake the old
    fake could not see: it answered by port, whatever the path."""

    async def test_the_crm_section_comes_back_when_the_route_exists(
        self, client, org_id, data, fake
    ) -> None:
        fake()
        with as_principal(_reader(org_id)):
            body = (await _get(client, data["contact_a_id"], IDENTITY)).json()
        assert "crm" not in body["unavailable"]
        assert body["crm"] == {"stage": "negotiation", "open_opportunities": 1, "hot": True}

    async def test_the_fake_answers_404_for_a_path_deal_does_not_route(self) -> None:
        """Proof the fake is not vacuous, and a test of the fake itself rather than of the table
        it reads: without its 404 branch it answers by port, which is how the 360 asked for
        `/deals` for as long as it did."""
        upstream = Fake()
        old = await upstream(httpx.Request("GET", "http://localhost:8131/deals"))
        assert old.status_code == 404
        mounted = await upstream(httpx.Request("GET", "http://localhost:8131/api/v1/deals"))
        assert mounted.status_code == 200

    async def test_the_leg_reads_a_route_deal_serves(self) -> None:
        request = await _sent_to_deal()
        assert request.method == "GET"  # POST on the same path creates a deal
        assert DEAL_ROUTES.serves(request.method, request.url.path)

    async def test_every_query_parameter_the_leg_sends_is_declared_by_the_route(self) -> None:
        """FastAPI ignores a parameter a route does not declare and answers its default page, so
        a renamed `size` changes the answer without failing any call."""
        request = await _sent_to_deal()
        declared = DEAL_ROUTES.query_parameters(request.method, request.url.path)
        assert set(request.url.params) <= set(declared)

    async def test_the_page_size_the_leg_sends_is_within_the_bound_the_route_declares(self) -> None:
        """A declared parameter is not enough. Deal refuses a `size` above its maximum with a 422,
        and a 422 reads exactly like the 404 above: the section goes `null`, one log line says why
        and the page looks as it did before the fix. The bound is a literal in each service
        (`PageQuery`, `libs/platform-core/src/platform_core/pagination.py:33`, and
        `_DEAL_PAGE_SIZE`), so only the committed document can say whether they still agree."""
        request = await _sent_to_deal()
        size = DEAL_ROUTES.query_parameters(request.method, request.url.path)["size"]["schema"]
        assert size["minimum"] <= int(request.url.params["size"]) <= size["maximum"]

    async def test_the_fields_the_leg_reads_are_always_sent_by_the_route(self) -> None:
        """The leg parses `stage`, `probability` and `created_at` from every item
        (`c1_360._DealRow`), and one item without any of them fails the whole page: the section
        reads `null` for a reason no fake that returns a hand-written item can show. In the
        committed schema of the item the route sends (followed from its own response, so the
        route and the schema cannot drift apart), each field must be required and never null."""
        request = await _sent_to_deal()
        item = DEAL_ROUTES.listed_item(request.method, request.url.path)
        for field in c1_360._DealRow.model_fields:
            assert field in item["required"], field
            assert "null" not in json.dumps(item["properties"][field]), field


class TestOwnWithoutSubjectRef:
    """A reader whose only contact grant is OWN, in a tenant with no HRMS, has no `subject_ref`
    (programme fact F1). The two cases below are the whole of what that means for the 360: one
    never reaches deal, the other reaches it and gets an honest empty answer."""

    async def test_an_own_contact_reader_without_a_subject_ref_is_forbidden(
        self, client, org_id, data, fake
    ) -> None:
        upstream = fake()
        principal = principal_for(
            org_id, ["crm.book.contact.read:OWN"], entitlements=["crm", "engage"]
        )
        with as_principal(principal):
            response = await _get(client, data["contact_a_id"], IDENTITY)
        assert response.status_code == 403
        assert upstream.calls == []  # the deal leg is never reached

    async def test_a_mixed_grant_reader_without_a_subject_ref_gets_an_empty_crm_section(
        self, client, org_id, data, fake
    ) -> None:
        """Characterization of customer's half only. Contact read at ORG passes; deal answers an
        empty list for a deal grant at OWN with no subject (`deal/tests/test_authz.py:56-58`), so
        the section reads empty rather than `null`."""
        fake(deal=lambda r: httpx.Response(200, json={"items": []}))
        principal = principal_for(
            org_id,
            ["crm.book.contact.read:ORG", "crm.pipeline.deal.read:OWN"],
            entitlements=["crm", "engage"],
        )
        with as_principal(principal):
            response = await _get(client, data["contact_a_id"], IDENTITY)
        assert response.status_code == 200, response.text
        body = response.json()
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
