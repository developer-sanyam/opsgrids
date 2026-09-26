"""WhatsApp Embedded Signup, numbers and health over real HTTP (slice W1).

Real PostgreSQL as the app role, so row-level security shows up in every isolation assertion; Meta
is an `httpx.MockTransport` injected through the `graph_transport` dependency, so no test reaches a
network. Every route has a negative-authorization test, and none of them may reach Meta or write.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import consent, services
from app.api.w1_signup import graph_transport
from app.main import app
from app.models import AuditLog, Message, OutboxEvent, ProviderAccount, ProviderTemplate
from app.w1_models import ConnectionHealth, SetupRun, WhatsAppNumber
from platform_core.auth import Principal, get_principal
from tests.helpers import principal_for
from tests.w1_helpers import (  # noqa: F401
    APP_ID,
    APP_SECRET,
    CODE,
    TOKEN,
    WABA,
    FakeMeta,
    clean_w1_tables,
    configure_signup,
    connect,
)

pytestmark = pytest.mark.integration

BASE = "/api/v1/engage/whatsapp"
READ = "engage.setup.whatsapp.read"
WRITE = "engage.setup.whatsapp.write"
NIL = "00000000-0000-0000-0000-000000000001"
TEST_BODY = {"to": "+919876543210", "template_name": "order_update", "language": "en"}


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def admin(org_id: uuid.UUID) -> Principal:
    return principal_for(org_id, [f"{READ}:ORG", f"{WRITE}:ORG"], entitlements=["engage"])


@pytest.fixture
def meta() -> Iterator[FakeMeta]:
    fake = FakeMeta()
    app.dependency_overrides[graph_transport] = lambda: httpx.MockTransport(fake)
    yield fake
    app.dependency_overrides.pop(graph_transport, None)


CACHED = [("1001", "+91 98765 43210"), ("1002", "+91 91234 56789")]

# --------------------------------------------------------------------- signup config


async def test_config_says_not_configured_until_the_meta_app_is_set(
    client: AsyncClient, org_id: uuid.UUID
) -> None:
    with as_principal(admin(org_id)):
        response = await client.get(f"{BASE}/signup/config")
    assert response.status_code == 200
    assert response.json() == {
        "enabled": False,
        "app_id": None,
        "config_id": None,
        "graph_version": "v25.0",
        "reason": "not_configured",
    }


async def test_config_when_enabled_never_carries_the_app_secret(
    client: AsyncClient, org_id: uuid.UUID, signup_configured: None
) -> None:
    with as_principal(admin(org_id)):
        response = await client.get(f"{BASE}/signup/config")
    assert response.json() == {
        "enabled": True,
        "app_id": APP_ID,
        "config_id": "cfg-123",
        "graph_version": "v25.0",
    }
    assert APP_SECRET not in response.text


# --------------------------------------------------------------------- signup complete and status


async def test_complete_returns_202_and_persists_a_run_the_worker_will_pick_up(
    client: AsyncClient,
    session: AsyncSession,
    org_id: uuid.UUID,
    meta: FakeMeta,
    signup_configured: None,
) -> None:
    body = {"code": CODE, "waba_id": WABA, "phone_number_id": "1001"}
    with as_principal(admin(org_id)):
        response = await client.post(f"{BASE}/signup/complete", json=body)
        again = await client.post(f"{BASE}/signup/complete", json=body)
        status = await client.get(f"{BASE}/signup/{response.json()['setup_id']}")
    assert response.status_code == 202
    assert response.json()["status"] == "running"
    assert again.status_code == 202 and again.json() == response.json()
    assert CODE not in response.text + again.text + status.text

    assert status.status_code == 200
    payload = status.json()
    assert payload["status"] == "running" and payload["error_code"] is None
    assert [(s["key"], s["state"]) for s in payload["steps"]] == [
        ("token", "waiting"),
        ("number", "waiting"),
        ("webhook", "waiting"),
        ("templates", "waiting"),
        ("verify", "waiting"),
    ]
    assert all(s["label"] for s in payload["steps"])

    async with session.begin():
        runs = (await session.execute(select(SetupRun))).scalars().all()
        events = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "notification.whatsapp.setup_requested"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(runs) == 1 and len(events) == 1
    assert runs[0].code_enc is not None and CODE.encode() not in runs[0].code_enc
    assert events[0].payload == {
        "organization_id": str(org_id),
        "setup_id": str(runs[0].id),
    }
    # The request itself never called Meta: the exchange belongs to the worker, after commit.
    assert meta.calls == []


async def test_complete_is_refused_when_the_platform_has_no_meta_app(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
) -> None:
    with as_principal(admin(org_id)):
        response = await client.post(f"{BASE}/signup/complete", json={"code": CODE})
    assert response.status_code == 409
    assert response.json()["errors"] == [{"code": "not_configured"}]
    async with session.begin():
        assert (await session.execute(select(func.count()).select_from(SetupRun))).scalar_one() == 0


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"code": ""},
        {"code": CODE, "waba_id": "not-digits"},
        {"code": CODE, "phone_number_id": "12; drop"},
        {"code": CODE, "access_token": "smuggled"},
    ],
)
async def test_complete_validates_the_body_and_refuses_unknown_fields(
    client: AsyncClient, org_id: uuid.UUID, signup_configured: None, body: dict[str, Any]
) -> None:
    with as_principal(admin(org_id)):
        response = await client.post(f"{BASE}/signup/complete", json=body)
    assert response.status_code == 422


async def test_a_second_setup_while_one_runs_is_a_conflict(
    client: AsyncClient, org_id: uuid.UUID, signup_configured: None
) -> None:
    with as_principal(admin(org_id)):
        first = await client.post(f"{BASE}/signup/complete", json={"code": "code-one"})
        second = await client.post(f"{BASE}/signup/complete", json={"code": "code-two"})
    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["errors"] == [{"code": "setup_in_progress"}]


async def test_another_organizations_setup_is_a_404_not_a_leak(
    client: AsyncClient, org_id: uuid.UUID, signup_configured: None
) -> None:
    with as_principal(admin(org_id)):
        started = await client.post(f"{BASE}/signup/complete", json={"code": CODE})
    setup_id = started.json()["setup_id"]
    with as_principal(admin(uuid.uuid4())):
        response = await client.get(f"{BASE}/signup/{setup_id}")
        unknown = await client.get(f"{BASE}/signup/{uuid.uuid4()}")
    assert response.status_code == 404 and unknown.status_code == 404
    assert response.json()["detail"] == "No such setup." and "steps" not in response.json()


# --------------------------------------------------------------------- numbers


async def test_numbers_are_served_from_a_fresh_cache_masked_with_the_default_first(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID, meta: FakeMeta
) -> None:
    await connect(session, org_id, phone_number_id="1002", cached=CACHED)
    with as_principal(admin(org_id)):
        response = await client.get(f"{BASE}/numbers")
    assert response.status_code == 200
    items = response.json()["items"]
    assert [(i["phone_number_id"], i["is_default"]) for i in items] == [
        ("1002", True),
        ("1001", False),
    ]
    first = items[0]
    assert set(first) == {
        "id",
        "phone_number_id",
        "display_phone_number",
        "verified_name",
        "name_status",
        "quality_rating",
        "messaging_limit",
        "status",
        "is_default",
    }
    assert "98765" not in response.text and "91234" not in response.text  # masked
    assert first["display_phone_number"] != "+91 91234 56789"
    assert (first["name_status"], first["quality_rating"], first["messaging_limit"]) == (
        "approved",
        "green",
        "TIER_1K",
    )
    assert meta.calls == []  # a fresh cache never asks Meta


async def test_a_stale_cache_is_refreshed_from_meta_with_this_organizations_token(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID, meta: FakeMeta
) -> None:
    stale = datetime.now(UTC) - timedelta(minutes=30)
    await connect(session, org_id, cached=CACHED, refreshed_at=stale)
    with as_principal(admin(org_id)):
        response = await client.get(f"{BASE}/numbers")
    by_id = {i["phone_number_id"]: i for i in response.json()["items"]}
    assert by_id["1002"]["quality_rating"] == "yellow"
    assert by_id["1002"]["messaging_limit"] == "TIER_250"
    assert by_id["1001"]["is_default"] is True
    assert [c.headers["authorization"] for c in meta.calls] == [f"Bearer {TOKEN}"]
    assert TOKEN not in response.text
    # ...and a read right after is served from the refreshed cache.
    with as_principal(admin(org_id)):
        await client.get(f"{BASE}/numbers")
    assert len(meta.calls) == 1


async def test_a_refused_token_serves_the_cache_and_marks_the_token_invalid(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID, meta: FakeMeta
) -> None:
    stale = datetime.now(UTC) - timedelta(minutes=30)
    await connect(session, org_id, cached=CACHED, refreshed_at=stale)
    meta.fail["numbers"] = 401
    with as_principal(admin(org_id)):
        response = await client.get(f"{BASE}/numbers")
        health = await client.get(f"{BASE}/health")
    assert response.status_code == 200 and len(response.json()["items"]) == 2
    assert health.json()["token"]["state"] == "invalid"
    assert health.json()["token"]["checked_at"] is not None


async def test_numbers_never_show_another_organizations_numbers(
    client: AsyncClient,
    session: AsyncSession,
    admin_session: AsyncSession,
    org_id: uuid.UUID,
    meta: FakeMeta,
) -> None:
    other = uuid.uuid4()
    await connect(session, org_id, cached=[("1001", "+91 98765 43210")])
    async with admin_session.begin():
        admin_session.add(
            WhatsAppNumber(
                organization_id=other,
                waba_id="7777",
                phone_number_id="3001",
                display_phone_number="+1 555 000 1111",
                refreshed_at=datetime.now(UTC),
            )
        )
    with as_principal(admin(org_id)):
        mine = await client.get(f"{BASE}/numbers")
    with as_principal(admin(uuid.uuid4())):
        nobody = await client.get(f"{BASE}/numbers")
    assert [i["phone_number_id"] for i in mine.json()["items"]] == ["1001"]
    assert nobody.json() == {"items": []}


# --------------------------------------------------------------------- default number


async def test_setting_the_default_moves_the_sending_number(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
) -> None:
    await connect(session, org_id, cached=CACHED)
    async with session.begin():
        target = (
            await session.execute(
                select(WhatsAppNumber).where(WhatsAppNumber.phone_number_id == "1002")
            )
        ).scalar_one()
    with as_principal(admin(org_id)):
        response = await client.post(f"{BASE}/numbers/{target.id}/default")
        listed = await client.get(f"{BASE}/numbers")
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert [(i["phone_number_id"], i["is_default"]) for i in listed.json()["items"]] == [
        ("1002", True),
        ("1001", False),
    ]
    async with session.begin():
        account = (await session.execute(select(ProviderAccount))).scalar_one()
        audited = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "engage.whatsapp.default_number_changed"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert account.phone_number_id == "1002"
    assert len(audited) == 1


async def test_a_number_another_organization_already_holds_cannot_become_the_default(
    client: AsyncClient,
    session: AsyncSession,
    admin_session: AsyncSession,
    org_id: uuid.UUID,
) -> None:
    await connect(session, org_id, cached=CACHED)
    async with admin_session.begin():
        admin_session.add(
            ProviderAccount(
                organization_id=uuid.uuid4(),
                channel="whatsapp",
                waba_id="8888",
                phone_number_id="1002",
                display_number="+910000000000",
                status="connected",
            )
        )
    async with session.begin():
        target = (
            await session.execute(
                select(WhatsAppNumber).where(WhatsAppNumber.phone_number_id == "1002")
            )
        ).scalar_one()
    with as_principal(admin(org_id)):
        response = await client.post(f"{BASE}/numbers/{target.id}/default")
    assert response.status_code == 409
    assert response.json()["errors"] == [{"code": "number_claimed"}]
    async with session.begin():
        account = (
            await session.execute(
                select(ProviderAccount).where(ProviderAccount.organization_id == org_id)
            )
        ).scalar_one()
    assert account.phone_number_id == "1001"


async def test_another_organizations_number_id_is_a_404(
    client: AsyncClient, session: AsyncSession, admin_session: AsyncSession, org_id: uuid.UUID
) -> None:
    other = uuid.uuid4()
    await connect(session, org_id, cached=CACHED)
    foreign = WhatsAppNumber(
        organization_id=other,
        waba_id="7777",
        phone_number_id="3001",
        display_phone_number="+1 555 000 1111",
    )
    async with admin_session.begin():
        admin_session.add(foreign)
    with as_principal(admin(org_id)):
        response = await client.post(f"{BASE}/numbers/{foreign.id}/default")
        missing = await client.post(f"{BASE}/numbers/{uuid.uuid4()}/default")
    assert response.status_code == 404 and missing.status_code == 404


# --------------------------------------------------------------------- health


async def test_health_when_nothing_is_connected(
    client: AsyncClient, org_id: uuid.UUID, meta: FakeMeta
) -> None:
    with as_principal(admin(org_id)):
        response = await client.get(f"{BASE}/health")
    assert response.status_code == 200
    assert response.json() == {
        "connected": False,
        "token": {"state": "unknown", "checked_at": None},
        "webhook": {"state": "unverified", "last_event_at": None},
        "templates": {"count": 0, "synced_at": None},
        "number": None,
    }
    assert meta.calls == []


async def test_health_reports_what_is_known_without_calling_meta(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID, meta: FakeMeta
) -> None:
    await connect(session, org_id, cached=CACHED, quality="yellow")
    now = datetime.now(UTC)
    async with session.begin():
        session.add(
            ConnectionHealth(
                organization_id=org_id,
                token_state="valid",
                token_checked_at=now,
                webhook_subscribed_at=now,
            )
        )
        session.add(
            Message(
                organization_id=org_id,
                channel="whatsapp",
                to_address="+919876543210",
                body="hi",
                state="delivered",
                delivered_at=now - timedelta(hours=1),
            )
        )
        session.add(
            ProviderTemplate(
                organization_id=org_id,
                code="order_update",
                channel="whatsapp",
                provider_name="order_update",
                language="en",
                category="utility",
                status="approved",
                variables=[],
                preview_text="Hello",
                status_checked_at=now,
            )
        )
    with as_principal(admin(org_id)):
        response = await client.get(f"{BASE}/health")
    body = response.json()
    assert body["connected"] is True
    assert body["token"]["state"] == "valid"
    assert body["webhook"]["state"] == "receiving" and body["webhook"]["last_event_at"]
    assert body["templates"]["count"] == 1 and body["templates"]["synced_at"]
    assert body["number"] == {"quality_rating": "yellow", "messaging_limit": "TIER_1K"}
    assert meta.calls == []
    assert TOKEN not in response.text


async def test_health_of_one_organization_is_not_another(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
) -> None:
    await connect(session, org_id, cached=CACHED)
    with as_principal(admin(uuid.uuid4())):
        response = await client.get(f"{BASE}/health")
    assert response.json()["connected"] is False and response.json()["number"] is None


# --------------------------------------------------------------------- test message


async def test_a_test_message_is_staged_through_consent_for_the_dispatcher(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
) -> None:
    await connect(session, org_id)
    with as_principal(admin(org_id)):
        response = await client.post(f"{BASE}/test-message", json=TEST_BODY)
    assert response.status_code == 200
    message_id = response.json()["message_id"]
    async with session.begin():
        message = (await session.execute(select(Message))).scalar_one()
        queued = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == services.EVT_MESSAGE_QUEUED)
                )
            )
            .scalars()
            .all()
        )
    assert str(message.id) == message_id
    assert (message.channel, message.state, message.to_address) == (
        "whatsapp",
        "pending",
        "+919876543210",
    )
    assert message.provider_template_payload == {
        "name": "order_update",
        "language": {"code": "en"},
    }
    assert len(queued) == 1  # the dispatcher sends it, on this organization's own connection


async def test_a_test_message_without_a_connection_is_refused_with_a_code(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
) -> None:
    with as_principal(admin(org_id)):
        response = await client.post(f"{BASE}/test-message", json=TEST_BODY)
    assert response.status_code == 409
    assert response.json()["errors"] == [{"code": "channel_not_connected"}]
    async with session.begin():
        assert (await session.execute(select(func.count()).select_from(Message))).scalar_one() == 0


async def test_a_test_message_to_someone_who_opted_out_is_refused_by_the_consent_decision(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
) -> None:
    await connect(session, org_id)
    async with session.begin():
        await consent.opt_out(
            session,
            organization_id=org_id,
            subject=consent.subject_for("whatsapp", "+919876543210"),
            purposes=consent.BOTH_PURPOSES,
            source=consent.SOURCE_ADMIN,
            evidence="test",
        )
    with as_principal(admin(org_id)):
        response = await client.post(f"{BASE}/test-message", json=TEST_BODY)
    assert response.status_code == 409
    assert response.json()["errors"] == [{"code": "consent_denied"}]
    async with session.begin():
        queued = (
            await session.execute(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.event_type == services.EVT_MESSAGE_QUEUED)
            )
        ).scalar_one()
    assert queued == 0


@pytest.mark.parametrize(
    "body",
    [
        {**TEST_BODY, "to": "9876543210"},
        {**TEST_BODY, "to": "+0123"},
        {**TEST_BODY, "template_name": "Bad Name"},
        {**TEST_BODY, "language": "english please"},
        {**TEST_BODY, "extra": 1},
    ],
)
async def test_a_test_message_body_is_validated(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID, body: dict[str, Any]
) -> None:
    await connect(session, org_id)
    with as_principal(admin(org_id)):
        response = await client.post(f"{BASE}/test-message", json=body)
    assert response.status_code == 422


# --------------------------------------------------------------------- disconnect


async def test_disconnect_wipes_the_token_and_keeps_the_history(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
) -> None:
    await connect(session, org_id, cached=CACHED)
    async with session.begin():
        session.add(
            ProviderTemplate(
                organization_id=org_id,
                code="order_update",
                channel="whatsapp",
                provider_name="order_update",
                language="en",
                category="utility",
                status="approved",
                variables=[],
                preview_text="Hello",
            )
        )
    with as_principal(admin(org_id)):
        response = await client.post(f"{BASE}/disconnect")
        again = await client.post(f"{BASE}/disconnect")
        health = await client.get(f"{BASE}/health")
        blocked = await client.post(f"{BASE}/test-message", json=TEST_BODY)
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert again.status_code == 409
    assert again.json()["errors"] == [{"code": "channel_not_connected"}]
    assert health.json()["connected"] is False
    assert health.json()["templates"]["count"] == 1  # history kept
    assert blocked.status_code == 409

    async with session.begin():
        account = (await session.execute(select(ProviderAccount))).scalar_one()
        numbers = (await session.execute(select(WhatsAppNumber))).scalars().all()
        audited = (
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "engage.whatsapp.disconnected")
                )
            )
            .scalars()
            .all()
        )
    assert account.status == "disconnected" and account.access_token_enc is None
    assert {n.status for n in numbers} == {"disconnected"}
    assert len(audited) == 1 and TOKEN not in str(audited[0].after)


async def test_disconnect_only_touches_the_callers_own_connection(
    client: AsyncClient, session: AsyncSession, admin_session: AsyncSession, org_id: uuid.UUID
) -> None:
    other = uuid.uuid4()
    await connect(session, org_id)
    async with admin_session.begin():
        admin_session.add(
            ProviderAccount(
                organization_id=other,
                channel="whatsapp",
                waba_id="8888",
                phone_number_id="4004",
                display_number="+1",
                status="connected",
            )
        )
    with as_principal(admin(org_id)):
        assert (await client.post(f"{BASE}/disconnect")).status_code == 200
    async with admin_session.begin():
        theirs = (
            await admin_session.execute(
                select(ProviderAccount).where(ProviderAccount.organization_id == other)
            )
        ).scalar_one()
    assert theirs.status == "connected"


# --------------------------------------------------------------------- authorization

ROUTES = [
    ("GET", f"{BASE}/signup/config", None, READ),
    ("POST", f"{BASE}/signup/complete", {"code": CODE}, WRITE),
    ("GET", f"{BASE}/signup/{NIL}", None, READ),
    ("GET", f"{BASE}/numbers", None, READ),
    ("POST", f"{BASE}/numbers/{NIL}/default", None, WRITE),
    ("GET", f"{BASE}/health", None, READ),
    ("POST", f"{BASE}/test-message", TEST_BODY, WRITE),
    ("POST", f"{BASE}/disconnect", None, WRITE),
]


async def _nothing_happened(session: AsyncSession, meta: FakeMeta) -> None:
    assert meta.calls == []
    async with session.begin():
        for model in (SetupRun, Message, AuditLog):
            count = (await session.execute(select(func.count()).select_from(model))).scalar_one()
            assert count == 0, model.__name__
        account = (await session.execute(select(ProviderAccount))).scalar_one()
    assert account.status == "connected" and account.access_token_enc is not None


@pytest.mark.parametrize(("method", "path", "body", "needs"), ROUTES)
async def test_no_permission_is_403_and_changes_nothing(
    client: AsyncClient,
    session: AsyncSession,
    org_id: uuid.UUID,
    meta: FakeMeta,
    signup_configured: None,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    needs: str,
) -> None:
    await connect(session, org_id, cached=CACHED)
    with as_principal(principal_for(org_id, [], entitlements=["engage"])):
        response = await client.request(method, path, json=body)
    assert response.status_code == 403
    await _nothing_happened(session, meta)


@pytest.mark.parametrize(("method", "path", "body", "needs"), ROUTES)
async def test_the_wrong_permission_is_403(
    client: AsyncClient,
    session: AsyncSession,
    org_id: uuid.UUID,
    meta: FakeMeta,
    signup_configured: None,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    needs: str,
) -> None:
    await connect(session, org_id, cached=CACHED)
    wrong = WRITE if needs == READ else READ
    with as_principal(principal_for(org_id, [f"{wrong}:ORG"], entitlements=["engage"])):
        response = await client.request(method, path, json=body)
    assert response.status_code == 403
    await _nothing_happened(session, meta)


@pytest.mark.parametrize(("method", "path", "body", "needs"), ROUTES)
async def test_an_unentitled_organization_is_refused_everywhere(
    client: AsyncClient,
    session: AsyncSession,
    org_id: uuid.UUID,
    meta: FakeMeta,
    signup_configured: None,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    needs: str,
) -> None:
    await connect(session, org_id, cached=CACHED)
    with as_principal(
        principal_for(org_id, [f"{READ}:ORG", f"{WRITE}:ORG"], entitlements=["notification"])
    ):
        response = await client.request(method, path, json=body)
    assert response.status_code == 402
    await _nothing_happened(session, meta)


@pytest.mark.parametrize(("method", "path", "body", "needs"), ROUTES)
async def test_a_permission_of_another_product_does_not_open_the_route(
    client: AsyncClient,
    org_id: uuid.UUID,
    meta: FakeMeta,
    signup_configured: None,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    needs: str,
) -> None:
    other = "engage.whatsapp.template.write"
    with as_principal(principal_for(org_id, [f"{other}:ORG"], entitlements=["engage"])):
        response = await client.request(method, path, json=body)
    assert response.status_code == 403
    assert meta.calls == []


@pytest.mark.parametrize(("method", "path", "body", "needs"), ROUTES)
async def test_an_unauthenticated_request_is_refused(
    client: AsyncClient,
    meta: FakeMeta,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    needs: str,
) -> None:
    response = await client.request(method, path, json=body)
    assert response.status_code in (401, 403)
    assert meta.calls == []
