"""WhatsApp template management over real HTTP (slice W2): sync, list, detail, create, preview.

Real PostgreSQL as the app role so row-level security shows up in every isolation assertion; Meta
is an `httpx.MockTransport`, injected through the `graph_transport` dependency, so no test reaches
a network and every request the service makes to Graph can be inspected — including which
organization's token it carried.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.w2_templates import graph_transport
from app.config import settings
from app.main import app
from app.models import ProviderAccount, ProviderTemplate
from platform_core import pii
from platform_core.auth import Principal, get_principal
from tests.helpers import principal_for

pytestmark = pytest.mark.integration

BASE = "/api/v1/engage/whatsapp/templates"
READ = "engage.whatsapp.template.read"
WRITE = "engage.whatsapp.template.write"
TOKEN_A = "ci-only-token-org-a"
TOKEN_B = "ci-only-token-org-b"


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def admin(org_id: uuid.UUID) -> Principal:
    return principal_for(org_id, [f"{READ}:ORG", f"{WRITE}:ORG"], entitlements=["engage"])


def viewer(org_id: uuid.UUID) -> Principal:
    return principal_for(org_id, [f"{READ}:ORG"], entitlements=["engage"])


def meta_template(
    meta_id: str,
    name: str,
    *,
    status: str = "APPROVED",
    category: str = "UTILITY",
    language: str = "en_US",
    quality: str = "GREEN",
    rejected_reason: str = "NONE",
) -> dict[str, Any]:
    return {
        "id": meta_id,
        "name": name,
        "language": language,
        "status": status,
        "category": category,
        "quality_score": {"score": quality},
        "rejected_reason": rejected_reason,
        "components": [
            {
                "type": "BODY",
                "text": "Hi {{1}}, your order {{2}} is on the way.",
                "example": {"body_text": [["Rahul", "A-1"]]},
            },
            {
                "type": "BUTTONS",
                "buttons": [
                    {
                        "type": "URL",
                        "text": "Track",
                        "url": "https://shop.example/t/{{1}}",
                        "example": ["https://shop.example/t/abc"],
                    }
                ],
            },
        ],
    }


class FakeMeta:
    """A minimal Graph API: per-WABA template pages, a create answer, and a failure switch."""

    def __init__(self) -> None:
        self.pages: dict[str, list[list[dict[str, Any]]]] = {}
        self.calls: list[httpx.Request] = []
        self.fail_status: int | None = None
        self.create_answer: dict[str, Any] = {
            "id": "990001",
            "status": "PENDING",
            "category": "UTILITY",
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.fail_status:
            return httpx.Response(
                self.fail_status,
                json={"error": {"code": 190 if self.fail_status == 401 else 100, "message": "x"}},
            )
        waba = request.url.path.split("/")[-2]
        if request.method == "GET":
            pages = self.pages.get(waba, [[]])
            after = request.url.params.get("after")
            index = int(after[1:]) if after else 0
            body: dict[str, Any] = {"data": pages[index]}
            if index + 1 < len(pages):
                body["paging"] = {
                    "cursors": {"after": f"c{index + 1}"},
                    "next": "https://graph.facebook.com/never-followed",
                }
            return httpx.Response(200, json=body)
        return httpx.Response(200, json=self.create_answer)


@pytest.fixture
def meta() -> Iterator[FakeMeta]:
    fake = FakeMeta()
    app.dependency_overrides[graph_transport] = lambda: httpx.MockTransport(fake)
    yield fake
    app.dependency_overrides.pop(graph_transport, None)


async def connect(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    waba_id: str = "waba-a",
    phone_number_id: str = "1001",
    token: str | None = TOKEN_A,
) -> None:
    sealed = pii.seal(token, kind="generic", settings=settings())[0] if token else None
    async with session.begin():
        session.add(
            ProviderAccount(
                organization_id=org_id,
                channel="whatsapp",
                waba_id=waba_id,
                phone_number_id=phone_number_id,
                display_number="+15550783881",
                access_token_enc=sealed,
                status="connected",
            )
        )


async def rows_for(session: AsyncSession) -> list[ProviderTemplate]:
    async with session.begin():
        return list((await session.execute(select(ProviderTemplate))).scalars().all())


# --------------------------------------------------------------------- sync


async def test_sync_creates_rows_across_pages_and_shapes_them_to_the_contract(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID, meta: FakeMeta
) -> None:
    await connect(session, org_id)
    meta.pages["waba-a"] = [
        [meta_template("1", "order_shipped"), meta_template("2", "promo", category="MARKETING")],
        [meta_template("3", "old_offer", status="REJECTED", rejected_reason="INVALID_FORMAT")],
    ]
    with as_principal(admin(org_id)):
        response = await client.post(f"{BASE}/sync")
        listed = await client.get(BASE)
    assert response.status_code == 200
    assert response.json() == {"synced": 3, "created": 3, "updated": 0, "removed": 0}

    items = {i["name"]: i for i in listed.json()["items"]}
    assert set(items) == {"order_shipped", "promo", "old_offer"}
    shipped = items["order_shipped"]
    assert shipped["meta_template_id"] == "1"
    assert shipped["status"] == "approved"
    assert shipped["sendable"] is True
    assert shipped["quality"] == "high"
    assert shipped["language"] == "en_US"
    assert shipped["body_preview"] == "Hi {{1}}, your order {{2}} is on the way."
    assert [(v["index"], v["name"], v["kind"], v["example"]) for v in shipped["variables"]] == [
        (1, "1", "text", "Rahul"),
        (2, "2", "text", "A-1"),
        (3, "button_0", "url", "https://shop.example/t/abc"),
    ]
    assert shipped["components"][0]["type"] == "BODY"
    assert items["promo"]["category"] == "marketing"
    assert items["old_offer"]["status"] == "rejected"
    assert items["old_offer"]["rejection_reason"] == "INVALID_FORMAT"

    # Meta's own cursor was passed back, never the `next` URL it also sent.
    hosts = {c.url.host for c in meta.calls}
    assert hosts == {"graph.facebook.com"}
    assert all(c.headers["authorization"] == f"Bearer {TOKEN_A}" for c in meta.calls)
    assert [c.url.params.get("after") for c in meta.calls] == [None, "c1"]


async def test_a_second_sync_updates_changed_rows_and_removes_what_meta_dropped(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID, meta: FakeMeta
) -> None:
    await connect(session, org_id)
    meta.pages["waba-a"] = [[meta_template("1", "a", status="PENDING"), meta_template("2", "b")]]
    with as_principal(admin(org_id)):
        await client.post(f"{BASE}/sync")
        # A template we started but Meta has not acknowledged (no Meta id) must survive a sync.
        async with session.begin():
            session.add(
                ProviderTemplate(
                    organization_id=org_id,
                    code="local_draft",
                    channel="whatsapp",
                    provider_name="local_draft",
                    language="en",
                    category="utility",
                    status="draft",
                    variables=[],
                    preview_text="x",
                )
            )
        meta.pages["waba-a"] = [[meta_template("1", "a", status="APPROVED")]]
        again = await client.post(f"{BASE}/sync")
        detail = await client.get(f"{BASE}/a")
        listed = await client.get(BASE)

    assert again.json() == {"synced": 1, "created": 0, "updated": 1, "removed": 1}
    assert detail.json()["status"] == "approved"
    assert [(h["status"], h["source"]) for h in detail.json()["history"]] == [
        ("pending", "sync"),
        ("approved", "sync"),
    ]
    assert {i["id"] for i in listed.json()["items"]} == {"a", "local_draft"}

    with as_principal(admin(org_id)):
        third = await client.post(f"{BASE}/sync")
    assert third.json() == {"synced": 1, "created": 0, "updated": 0, "removed": 0}


async def test_a_second_language_of_a_name_gets_its_own_code(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID, meta: FakeMeta
) -> None:
    await connect(session, org_id)
    meta.pages["waba-a"] = [
        [meta_template("1", "hello", language="en"), meta_template("2", "hello", language="hi")]
    ]
    with as_principal(admin(org_id)):
        response = await client.post(f"{BASE}/sync")
        listed = await client.get(BASE)
    assert response.json()["created"] == 2
    assert sorted(i["id"] for i in listed.json()["items"]) == ["hello", "hello_hi"]


async def test_sync_without_a_connection_is_a_409_naming_the_reason(
    client: AsyncClient, org_id: uuid.UUID, meta: FakeMeta
) -> None:
    with as_principal(admin(org_id)):
        response = await client.post(f"{BASE}/sync")
    assert response.status_code == 409
    assert response.json()["errors"] == [{"code": "channel_not_connected"}]
    assert meta.calls == []


async def test_a_disconnected_or_tokenless_account_is_not_connected(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID, meta: FakeMeta
) -> None:
    await connect(session, org_id, token=None)
    with as_principal(admin(org_id)):
        response = await client.post(f"{BASE}/sync")
    assert response.status_code == 409
    assert meta.calls == []


async def test_meta_refusing_the_token_is_a_502_that_leaks_nothing(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID, meta: FakeMeta
) -> None:
    await connect(session, org_id)
    meta.fail_status = 401
    with as_principal(admin(org_id)):
        response = await client.post(f"{BASE}/sync")
    assert response.status_code == 502
    assert TOKEN_A not in response.text
    assert "graph.facebook.com" not in response.text
    assert response.json()["errors"] == [{"code": "meta_refused"}]


async def test_a_failed_page_never_removes_anything(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID, meta: FakeMeta
) -> None:
    await connect(session, org_id)
    meta.pages["waba-a"] = [[meta_template("1", "a")]]
    with as_principal(admin(org_id)):
        await client.post(f"{BASE}/sync")
        meta.fail_status = 503
        failed = await client.post(f"{BASE}/sync")
        meta.fail_status = None
        listed = await client.get(BASE)
    assert failed.status_code == 502
    assert failed.json()["errors"] == [{"code": "meta_unreachable"}]
    assert [i["id"] for i in listed.json()["items"]] == ["a"]


async def test_sync_uses_the_callers_own_token_and_waba_and_touches_only_their_rows(
    client: AsyncClient,
    session: AsyncSession,
    admin_session: AsyncSession,
    org_id: uuid.UUID,
    meta: FakeMeta,
) -> None:
    other_org = uuid.uuid4()
    await connect(session, org_id)
    async with admin_session.begin():
        admin_session.add(
            ProviderAccount(
                organization_id=other_org,
                channel="whatsapp",
                waba_id="waba-b",
                phone_number_id="2002",
                display_number="+15550000000",
                access_token_enc=pii.seal(TOKEN_B, kind="generic", settings=settings())[0],
                status="connected",
            )
        )
        admin_session.add(
            ProviderTemplate(
                organization_id=other_org,
                code="theirs",
                channel="whatsapp",
                provider_name="theirs",
                provider_template_id="777",
                language="en",
                category="utility",
                status="approved",
                variables=[],
                preview_text="Their wording",
            )
        )
    meta.pages["waba-a"] = [[meta_template("1", "mine")]]
    meta.pages["waba-b"] = [[]]
    with as_principal(admin(org_id)):
        response = await client.post(f"{BASE}/sync")
        listed = await client.get(BASE)
        theirs = await client.get(f"{BASE}/theirs")
    assert response.json() == {"synced": 1, "created": 1, "updated": 0, "removed": 0}
    assert [i["id"] for i in listed.json()["items"]] == ["mine"]
    assert theirs.status_code == 404
    assert {c.url.path.split("/")[-2] for c in meta.calls} == {"waba-a"}
    assert all(TOKEN_B not in c.headers["authorization"] for c in meta.calls)

    async with admin_session.begin():
        survivor = (
            await admin_session.execute(
                select(ProviderTemplate).where(ProviderTemplate.organization_id == other_org)
            )
        ).scalar_one()
    assert survivor.deleted_at is None
    assert survivor.preview_text == "Their wording"


# --------------------------------------------------------------------- list and detail


async def test_list_filters_by_category_status_and_search(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID, meta: FakeMeta
) -> None:
    await connect(session, org_id)
    meta.pages["waba-a"] = [
        [
            meta_template("1", "order_shipped"),
            meta_template("2", "promo_diwali", category="MARKETING", status="PENDING"),
            meta_template("3", "promo_holi", category="MARKETING"),
        ]
    ]
    with as_principal(admin(org_id)):
        await client.post(f"{BASE}/sync")
        marketing = await client.get(BASE, params={"category": "marketing"})
        pending = await client.get(BASE, params={"status": "pending"})
        searched = await client.get(BASE, params={"q": "holi"})
        percent = await client.get(BASE, params={"q": "100%"})
        paged = await client.get(BASE, params={"page": 1, "size": 2})
        bad = await client.get(BASE, params={"status": "nonsense"})
    assert {i["id"] for i in marketing.json()["items"]} == {"promo_diwali", "promo_holi"}
    assert [i["id"] for i in pending.json()["items"]] == ["promo_diwali"]
    assert [i["id"] for i in searched.json()["items"]] == ["promo_holi"]
    assert percent.json()["total"] == 0
    assert paged.json()["total"] == 3 and len(paged.json()["items"]) == 2
    assert bad.status_code == 422


async def test_detail_accepts_the_code_or_the_row_uuid(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID, meta: FakeMeta
) -> None:
    await connect(session, org_id)
    meta.pages["waba-a"] = [[meta_template("1", "order_shipped")]]
    with as_principal(admin(org_id)):
        await client.post(f"{BASE}/sync")
        by_code = await client.get(f"{BASE}/order_shipped")
        by_uuid = await client.get(f"{BASE}/{by_code.json()['row_id']}")
        missing = await client.get(f"{BASE}/nope")
    assert by_code.status_code == 200 and by_uuid.json()["id"] == "order_shipped"
    assert by_code.json()["history"][0]["source"] == "sync"
    assert missing.status_code == 404


async def test_the_hand_registered_put_route_still_works_and_reads_back_in_the_new_shape(
    client: AsyncClient, org_id: uuid.UUID
) -> None:
    body = {
        "code": "delivery_confirmed",
        "channel": "whatsapp",
        "provider_name": "delivery_confirmed",
        "preview_text": "Hi {{1}}, out for delivery.",
        "variables": [{"key": "customer.name", "component": "body", "position": 0}],
    }
    with as_principal(admin(org_id)):
        put = await client.put(BASE, json=body)
        listed = await client.get(BASE)
        detail = await client.get(f"{BASE}/delivery_confirmed")
    assert put.status_code == 200 and put.json()["id"] == "delivery_confirmed"
    item = listed.json()["items"][0]
    assert item["status"] == "draft" and item["quality"] == "unknown"
    assert item["variables"][0]["key"] == "customer.name"
    assert item["variables"][0]["index"] == 1
    assert detail.json()["history"] == []


# --------------------------------------------------------------------- create


def create_body(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "order_update",
        "category": "utility",
        "language": "en_US",
        "components": {
            "header": {"type": "text", "text": "Order {{1}}", "example": "A-1"},
            "body": {
                "text": "Hi {{1}}, your order {{2}} has shipped today.",
                "examples": ["Rahul", "A-1"],
            },
            "footer": {"text": "Thanks for shopping"},
            "buttons": [
                {"type": "quick_reply", "text": "Got it"},
                {"type": "phone", "text": "Call us", "phone": "+919876543210"},
            ],
        },
    }
    body.update(over)
    return body


async def test_create_submits_to_meta_and_stores_a_pending_row(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID, meta: FakeMeta
) -> None:
    await connect(session, org_id)
    with as_principal(admin(org_id)):
        response = await client.post(BASE, json=create_body())
        stored = await client.get(f"{BASE}/order_update")
    assert response.status_code == 201
    row = response.json()
    assert row["status"] == "pending" and row["sendable"] is False
    assert row["meta_template_id"] == "990001"
    assert row["submitted_at"] is not None
    assert [(h["status"], h["source"]) for h in row["history"]] == [("pending", "submit")]
    assert stored.json()["id"] == "order_update"

    [call] = meta.calls
    assert call.method == "POST" and call.url.path.endswith("/waba-a/message_templates")
    assert call.headers["authorization"] == f"Bearer {TOKEN_A}"
    sent = json.loads(call.content)
    assert sent["name"] == "order_update" and sent["category"] == "UTILITY"
    assert sent["language"] == "en_US"
    by_type = {c["type"]: c for c in sent["components"]}
    assert by_type["BODY"]["example"] == {"body_text": [["Rahul", "A-1"]]}
    assert by_type["HEADER"]["example"] == {"header_text": ["A-1"]}
    assert [b["type"] for b in by_type["BUTTONS"]["buttons"]] == ["QUICK_REPLY", "PHONE_NUMBER"]


async def test_create_reports_every_local_problem_at_once_and_never_calls_meta(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID, meta: FakeMeta
) -> None:
    await connect(session, org_id)
    bad = create_body(
        name="Order Update!",
        components={
            "body": {"text": "Hi {{1}} and {{3}} bye", "examples": ["only-one"]},
            "buttons": [{"type": "url", "text": "Go", "url": "ftp://nope"}],
        },
    )
    with as_principal(admin(org_id)):
        response = await client.post(BASE, json=bad)
    assert response.status_code == 422
    fields = {e["field"] for e in response.json()["errors"]}
    assert {"name", "components.body.text", "components.buttons.0.url"} <= fields
    assert meta.calls == []
    assert await rows_for(session) == []


@pytest.mark.parametrize(
    ("components", "field"),
    [
        ({"body": {"text": "Hi {{1}} there", "examples": []}}, "components.body.examples"),
        ({"body": {"text": "{{1}} starts", "examples": ["a"]}}, "components.body.text"),
        ({"body": {"text": "x" * 1025}}, "components.body.text"),
        ({"body": {"text": "ok"}, "footer": {"text": "y" * 61}}, "components.footer.text"),
        (
            {"body": {"text": "ok"}, "header": {"type": "image"}},
            "components.header.example",
        ),
        (
            {"body": {"text": "ok"}, "buttons": [{"type": "phone", "text": "c", "phone": "123"}]},
            "components.buttons.0.phone",
        ),
    ],
)
async def test_create_local_rules(
    client: AsyncClient,
    session: AsyncSession,
    org_id: uuid.UUID,
    meta: FakeMeta,
    components: dict[str, Any],
    field: str,
) -> None:
    await connect(session, org_id)
    with as_principal(admin(org_id)):
        response = await client.post(BASE, json=create_body(components=components))
    assert response.status_code == 422
    assert field in {e["field"] for e in response.json()["errors"]}


async def test_create_refuses_unknown_fields_and_authentication_category(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID, meta: FakeMeta
) -> None:
    await connect(session, org_id)
    with as_principal(admin(org_id)):
        extra = await client.post(BASE, json={**create_body(), "status": "approved"})
        auth = await client.post(BASE, json=create_body(category="authentication"))
    assert extra.status_code == 422
    assert auth.status_code == 422
    assert "category" in {e["field"] for e in auth.json()["errors"]}


async def test_create_duplicate_name_and_language_is_409(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID, meta: FakeMeta
) -> None:
    await connect(session, org_id)
    with as_principal(admin(org_id)):
        first = await client.post(BASE, json=create_body())
        second = await client.post(BASE, json=create_body())
        other_language = await client.post(BASE, json=create_body(language="hi"))
    assert first.status_code == 201 and second.status_code == 409
    assert other_language.status_code == 201
    assert other_language.json()["id"] == "order_update_hi"
    assert len(meta.calls) == 2


async def test_a_meta_refusal_stores_nothing(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID, meta: FakeMeta
) -> None:
    await connect(session, org_id)
    meta.fail_status = 400
    with as_principal(admin(org_id)):
        response = await client.post(BASE, json=create_body())
    assert response.status_code == 502
    assert await rows_for(session) == []


async def test_create_without_a_connection_is_409(
    client: AsyncClient, org_id: uuid.UUID, meta: FakeMeta
) -> None:
    with as_principal(admin(org_id)):
        response = await client.post(BASE, json=create_body())
    assert response.status_code == 409
    assert response.json()["errors"] == [{"code": "channel_not_connected"}]


# --------------------------------------------------------------------- preview


async def test_preview_renders_body_header_and_buttons(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID, meta: FakeMeta
) -> None:
    await connect(session, org_id)
    meta.pages["waba-a"] = [[meta_template("1", "order_shipped")]]
    with as_principal(admin(org_id)):
        await client.post(f"{BASE}/sync")
    with as_principal(viewer(org_id)):
        ok = await client.post(
            f"{BASE}/order_shipped/preview", json={"values": {"1": "Rahul", "2": "A-1", "3": "xyz"}}
        )
        missing = await client.post(
            f"{BASE}/order_shipped/preview", json={"values": {"1": "Rahul"}}
        )
        unknown = await client.post(f"{BASE}/nope/preview", json={"values": {}})
    assert ok.status_code == 200
    assert ok.json()["text"] == "Hi Rahul, your order A-1 is on the way."
    assert ok.json()["buttons"] == [
        {"type": "url", "text": "Track", "url": "https://shop.example/t/xyz", "phone": None}
    ]
    assert missing.status_code == 422
    assert {e["field"] for e in missing.json()["errors"]} == {"values.2", "values.3"}
    assert unknown.status_code == 404


# --------------------------------------------------------------------- authorization


ROUTES: list[tuple[str, str, dict[str, Any] | None, str]] = [
    ("GET", BASE, None, READ),
    ("GET", f"{BASE}/some_code", None, READ),
    ("POST", f"{BASE}/some_code/preview", {"values": {}}, READ),
    ("POST", f"{BASE}/sync", None, WRITE),
    ("POST", BASE, create_body(), WRITE),
]


@pytest.mark.parametrize(("method", "path", "body", "needs"), ROUTES)
async def test_no_role_is_refused_everywhere(
    client: AsyncClient,
    org_id: uuid.UUID,
    meta: FakeMeta,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    needs: str,
) -> None:
    with as_principal(principal_for(org_id, [], entitlements=["engage"])):
        response = await client.request(method, path, json=body)
    assert response.status_code == 403
    assert meta.calls == []


@pytest.mark.parametrize(("method", "path", "body", "needs"), ROUTES)
async def test_an_unentitled_org_is_refused_everywhere(
    client: AsyncClient,
    org_id: uuid.UUID,
    meta: FakeMeta,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    needs: str,
) -> None:
    with as_principal(
        principal_for(org_id, [f"{READ}:ORG", f"{WRITE}:ORG"], entitlements=["notification"])
    ):
        response = await client.request(method, path, json=body)
    assert response.status_code == 402
    assert meta.calls == []


@pytest.mark.parametrize(("method", "path", "body", "needs"), ROUTES)
async def test_a_reader_cannot_write_and_a_writer_cannot_read_by_accident(
    client: AsyncClient,
    session: AsyncSession,
    org_id: uuid.UUID,
    meta: FakeMeta,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    needs: str,
) -> None:
    await connect(session, org_id)
    wrong = WRITE if needs == READ else READ
    with as_principal(principal_for(org_id, [f"{wrong}:ORG"], entitlements=["engage"])):
        response = await client.request(method, path, json=body)
    assert response.status_code == 403
    assert meta.calls == []


async def test_a_notification_permission_does_not_open_the_engage_routes(
    client: AsyncClient, org_id: uuid.UUID, meta: FakeMeta
) -> None:
    with as_principal(
        principal_for(org_id, ["notification.delivery.template.write:ORG"], entitlements=["engage"])
    ):
        response = await client.post(f"{BASE}/sync")
    assert response.status_code == 403
