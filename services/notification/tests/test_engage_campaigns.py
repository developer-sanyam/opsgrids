"""Engage lists and campaigns — CRUD, the send gate, and the fan-out consumer.

Real PostgreSQL as the app role. The fan-out runs through `consumers.fan_out` itself, on the
process-wide engine the worker uses, with a `RecordingTransport` injected: nothing here opens a
socket, and every assertion about "sent" is an assertion about what the transport was handed.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app import consumers, models, services
from app.config import settings
from app.main import app
from app.transports import Outgoing, Receipt, RecordingTransport, TransportRefused
from platform_core.auth import Principal, Scope, get_principal
from platform_core.db import dispose_engine, init_engine
from tests.helpers import ctx_for, envelope_for, principal_for

pytestmark = pytest.mark.integration

LISTS = "/api/v1/engage/lists"
CAMPAIGNS = "/api/v1/engage/campaigns"
CONTACTS = "/api/v1/engage/contacts"

L_READ = "engage.audience.list.read"
L_WRITE = "engage.audience.list.write"
K_READ = "engage.campaign.campaign.read"
K_WRITE = "engage.campaign.campaign.write"
K_SEND = "engage.campaign.campaign.send"
C_WRITE = "engage.audience.contact.write"
S_WRITE = "engage.audience.suppression.write"
ALL = [f"{p}:ORG" for p in (L_READ, L_WRITE, K_READ, K_WRITE, K_SEND, C_WRITE, S_WRITE)]
ABSENT = uuid.UUID(int=0)


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def caller(org_id: uuid.UUID, perms: list[str]) -> Principal:
    return principal_for(org_id, perms, entitlements=["engage"])


@pytest_asyncio.fixture
async def worker_engine(session) -> AsyncIterator[None]:
    """The engine `session_scope` uses, as the worker process would have it. Depends on
    `session` so the tables are truncated before the consumer sees them."""
    init_engine(settings())
    yield
    await dispose_engine()


async def _seed_identity(
    session, org_id: uuid.UUID, *, verified: bool = True, connected: bool = True, cap: int = 2000
) -> uuid.UUID:
    """A domain, a sender and (optionally) a connection, written directly: the gates they pass
    are tested in test_engage_identity, and here they are preconditions."""
    domain = models.EmailDomain(organization_id=org_id, domain="company.com", status="verified")
    async with session.begin():
        session.add(domain)
        await session.flush()
        sender = models.Sender(
            organization_id=org_id,
            domain_id=domain.id,
            name="News",
            address="news@company.com",
            verified_at=datetime.now(UTC) if verified else None,
        )
        session.add(sender)
        if connected:
            session.add(
                models.EmailConnection(
                    organization_id=org_id,
                    kind="google",
                    host="smtp.gmail.com",
                    username="news@company.com",
                    daily_cap=cap,
                )
            )
    # Ids, not rows: a request that fails rolls the shared session back and expires every
    # loaded row, and touching one afterwards would lazy-load outside the event loop.
    return sender.id


async def _contacts(session, org_id: uuid.UUID, *emails: str) -> list[models.Contact]:
    write = ctx_for(org_id, C_WRITE, Scope.ORG)
    async with session.begin():
        return [await services.create_contact(session, write, email=e) for e in emails]


async def _campaign(session, org_id, sender_id, *, list_id=None) -> uuid.UUID:
    write = ctx_for(org_id, K_WRITE, Scope.ORG)
    async with session.begin():
        row = await services.create_campaign(
            session,
            write,
            fields={
                "name": "Autumn",
                "sender_id": sender_id,
                "subject": "News",
                "html_body": "<p>Hello</p>",
                "text_body": "Hello",
                "audience": "list" if list_id else "all_sendable",
                "list_id": list_id,
                "scheduled_at": None,
            },
        )
    return row.id


async def _send(session, org_id, campaign_id) -> None:
    async with session.begin():
        await services.request_send(
            session, ctx_for(org_id, K_SEND, Scope.ORG), campaign_id=campaign_id
        )


def _envelope(org_id, campaign_id, event_id=None):
    return envelope_for(
        org_id,
        services.EVT_CAMPAIGN_SEND_REQUESTED,
        {"campaign_id": str(campaign_id)},
        event_id=event_id,
    )


async def _recipients(session, campaign_id) -> dict[str, str]:
    async with session.begin():
        rows = (
            await session.execute(
                select(models.CampaignRecipient).where(
                    models.CampaignRecipient.campaign_id == campaign_id
                )
            )
        ).scalars()
        return {r.email_masked: r.status for r in rows}


async def _reload(session, campaign_id) -> models.Campaign:
    async with session.begin():
        row = await session.get(models.Campaign, campaign_id, populate_existing=True)
        assert row is not None
        return row


# --------------------------------------------------------------------- lists


async def test_list_crud_and_membership_is_idempotent(client, session, org_id):
    a_id, b_id = [c.id for c in await _contacts(session, org_id, "a@example.com", "b@example.com")]
    with as_principal(caller(org_id, ALL)):
        created = await client.post(LISTS, json={"name": "VIP"})
        assert created.status_code == 201, created.text
        lid = created.json()["id"]
        assert (await client.post(LISTS, json={"name": "VIP"})).status_code == 409

        added = await client.post(
            f"{LISTS}/{lid}/members", json={"contact_ids": [str(a_id), str(a_id), str(ABSENT)]}
        )
        assert added.json() == {"changed": 1, "unknown": [str(ABSENT)]}
        again = await client.post(f"{LISTS}/{lid}/members", json={"contact_ids": [str(a_id)]})
        assert again.json()["changed"] == 0

        got = await client.get(f"{LISTS}/{lid}")
        assert got.json()["member_count"] == 1
        members = await client.get(f"{LISTS}/{lid}/members")
        assert [m["id"] for m in members.json()["items"]] == [str(a_id)]
        assert "a@example.com" not in members.text

        renamed = await client.patch(f"{LISTS}/{lid}", json={"name": "Very VIP"})
        assert renamed.json()["name"] == "Very VIP"
        assert (await client.get(LISTS)).json()["total"] == 1

        removed = await client.post(
            f"{LISTS}/{lid}/members/remove", json={"contact_ids": [str(a_id), str(b_id)]}
        )
        assert removed.json()["changed"] == 1
        assert (await client.delete(f"{LISTS}/{lid}")).status_code == 204
        assert (await client.get(f"{LISTS}/{lid}")).status_code == 404


async def test_a_list_a_campaign_names_cannot_be_deleted(client, session, org_id):
    sender_id = await _seed_identity(session, org_id)
    async with session.begin():
        lst = await services.create_list(session, ctx_for(org_id, L_WRITE, Scope.ORG), name="L")
    lst_id = lst.id
    await _campaign(session, org_id, sender_id, list_id=lst_id)
    with as_principal(caller(org_id, ALL)):
        assert (await client.delete(f"{LISTS}/{lst_id}")).status_code == 409


# --------------------------------------------------------------------- campaign CRUD


async def test_campaign_draft_crud(client, session, org_id):
    sender_id = await _seed_identity(session, org_id)
    with as_principal(caller(org_id, ALL)):
        bad = await client.post(CAMPAIGNS, json={"name": "x", "sender_id": str(ABSENT)})
        assert bad.status_code == 422
        no_list = await client.post(
            CAMPAIGNS, json={"name": "x", "sender_id": str(sender_id), "audience": "list"}
        )
        assert no_list.status_code == 422

        created = await client.post(
            CAMPAIGNS, json={"name": "Autumn", "sender_id": str(sender_id), "subject": "Hi"}
        )
        assert created.status_code == 201, created.text
        cid = created.json()["id"]
        assert created.json()["status"] == "draft"
        assert created.json()["stats"]["recipients"] == 0

        patched = await client.patch(f"{CAMPAIGNS}/{cid}", json={"text_body": "Body"})
        assert patched.status_code == 200
        assert patched.json()["text_body"] == "Body"
        assert (await client.get(CAMPAIGNS, params={"status": "draft"})).json()["total"] == 1
        assert (await client.delete(f"{CAMPAIGNS}/{cid}")).status_code == 204
        assert (await client.get(f"{CAMPAIGNS}/{cid}")).status_code == 404


async def test_a_campaign_past_draft_cannot_be_edited_or_deleted(client, session, org_id):
    sender_id = await _seed_identity(session, org_id)
    campaign_id = await _campaign(session, org_id, sender_id)
    await _send(session, org_id, campaign_id)
    with as_principal(caller(org_id, ALL)):
        assert (
            await client.patch(f"{CAMPAIGNS}/{campaign_id}", json={"name": "y"})
        ).status_code == 409
        assert (await client.delete(f"{CAMPAIGNS}/{campaign_id}")).status_code == 409


# --------------------------------------------------------------------- the send gate


async def test_send_refuses_an_unverified_sender(client, session, org_id):
    sender_id = await _seed_identity(session, org_id, verified=False)
    campaign_id = await _campaign(session, org_id, sender_id)
    with as_principal(caller(org_id, ALL)):
        r = await client.post(f"{CAMPAIGNS}/{campaign_id}/send")
    assert r.status_code == 422
    assert "test send" in r.json()["detail"]
    assert (await _reload(session, campaign_id)).status == "draft"


async def test_send_refuses_without_an_active_connection(client, session, org_id):
    sender_id = await _seed_identity(session, org_id, connected=False)
    campaign_id = await _campaign(session, org_id, sender_id)
    with as_principal(caller(org_id, ALL)):
        r = await client.post(f"{CAMPAIGNS}/{campaign_id}/send")
    assert r.status_code == 422
    assert "email provider" in r.json()["detail"]


async def test_send_refuses_a_disconnected_connection(client, session, org_id):
    sender_id = await _seed_identity(session, org_id)
    async with session.begin():
        conn = (await session.execute(select(models.EmailConnection))).scalar_one()
        conn.status = "disconnected"
    campaign_id = await _campaign(session, org_id, sender_id)
    with as_principal(caller(org_id, ALL)):
        assert (await client.post(f"{CAMPAIGNS}/{campaign_id}/send")).status_code == 422


async def test_send_stages_one_event_and_sends_nothing_inline(client, session, org_id):
    sender_id = await _seed_identity(session, org_id)
    await _contacts(session, org_id, "a@example.com")
    campaign_id = await _campaign(session, org_id, sender_id)
    with as_principal(caller(org_id, ALL)):
        r = await client.post(f"{CAMPAIGNS}/{campaign_id}/send")
        assert r.status_code == 202, r.text
        assert r.json()["status"] == "sending"
        # A second send is a state conflict, not a gate refusal.
        assert (await client.post(f"{CAMPAIGNS}/{campaign_id}/send")).status_code == 409
    async with session.begin():
        events = (
            await session.execute(
                select(func.count())
                .select_from(models.OutboxEvent)
                .where(models.OutboxEvent.event_type == services.EVT_CAMPAIGN_SEND_REQUESTED)
            )
        ).scalar_one()
    assert events == 1
    assert await _recipients(session, campaign_id) == {}


# --------------------------------------------------------------------- the fan-out


async def test_fan_out_sends_once_with_unsubscribe_header_and_footer(
    session, org_id, worker_engine
):
    sender_id = await _seed_identity(session, org_id)
    await _contacts(session, org_id, "a@example.com", "b@example.com", "gone@example.com")
    async with session.begin():
        await services.add_suppression(
            session, ctx_for(org_id, S_WRITE, Scope.ORG), email="gone@example.com"
        )
    campaign_id = await _campaign(session, org_id, sender_id)
    await _send(session, org_id, campaign_id)

    wire = RecordingTransport()
    envelope = _envelope(org_id, campaign_id)
    assert await consumers.fan_out(envelope, transport=wire, batch_size=1) == "done"
    # The same event redelivered, and a second event for the same campaign: nothing more goes.
    assert await consumers.fan_out(envelope, transport=wire) == "redelivered"
    assert await consumers.fan_out(_envelope(org_id, campaign_id), transport=wire) == "stopped"

    assert sorted(m.to_address for m in wire.sent) == ["a@example.com", "b@example.com"]
    for message in wire.sent:
        link = services.unsubscribe_url(org_id, message.to_address)
        assert message.headers is not None
        assert message.headers["List-Unsubscribe"] == f"<{link}>"
        assert message.headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
        assert link in message.body
        assert message.html is not None and link in message.html

    reloaded = await _reload(session, campaign_id)
    assert reloaded.status == "sent"
    assert (reloaded.recipient_count, reloaded.sent_count) == (2, 2)
    statuses = await _recipients(session, campaign_id)
    assert sorted(statuses.values()) == ["sent", "sent"]


async def test_an_address_suppressed_after_expansion_is_skipped(session, org_id, worker_engine):
    sender_id = await _seed_identity(session, org_id)
    await _contacts(session, org_id, "a@example.com", "late@example.com")
    campaign_id = await _campaign(session, org_id, sender_id)
    await _send(session, org_id, campaign_id)
    async with session.begin():
        await services.expand_campaign(
            session, organization_id=org_id, campaign_id=campaign_id, batch_size=10
        )
    await services_unsubscribe(session, org_id, "late@example.com")

    wire = RecordingTransport()
    assert await consumers.fan_out(_envelope(org_id, campaign_id), transport=wire) == "done"
    assert [m.to_address for m in wire.sent] == ["a@example.com"]
    statuses = await _recipients(session, campaign_id)
    assert sorted(statuses.values()) == ["sent", "suppressed"]
    assert (await _reload(session, campaign_id)).suppressed_count == 1


async def services_unsubscribe(session, org_id, email) -> None:
    async with session.begin():
        await services.unsubscribe(session, organization_id=org_id, email=email)


async def test_cancel_stops_the_remaining_recipients(client, session, org_id, worker_engine):
    sender_id = await _seed_identity(session, org_id)
    await _contacts(session, org_id, "a@example.com", "b@example.com", "c@example.com")
    campaign_id = await _campaign(session, org_id, sender_id)
    await _send(session, org_id, campaign_id)

    class CancelAfterFirst:
        """Sends, then cancels the campaign through the API — the operator pressing cancel
        while the first batch is going out."""

        name = "recording"

        def __init__(self) -> None:
            self.sent: list[Outgoing] = []

        async def send(self, message: Outgoing) -> Receipt:
            self.sent.append(message)
            if len(self.sent) == 1:
                with as_principal(caller(org_id, ALL)):
                    r = await client.post(f"{CAMPAIGNS}/{campaign_id}/cancel")
                    assert r.status_code == 200, r.text
            return Receipt(provider=self.name)

    wire = CancelAfterFirst()
    outcome = await consumers.fan_out(_envelope(org_id, campaign_id), transport=wire, batch_size=1)
    assert outcome == "stopped"
    assert len(wire.sent) == 1
    reloaded = await _reload(session, campaign_id)
    assert reloaded.status == "cancelled"
    assert sorted((await _recipients(session, campaign_id)).values()) == [
        "queued",
        "queued",
        "sent",
    ]
    with as_principal(caller(org_id, ALL)):
        assert (await client.post(f"{CAMPAIGNS}/{campaign_id}/cancel")).status_code == 409


async def test_the_daily_cap_holds_the_rest_queued(session, org_id, worker_engine):
    sender_id = await _seed_identity(session, org_id, cap=1)
    await _contacts(session, org_id, "a@example.com", "b@example.com")
    campaign_id = await _campaign(session, org_id, sender_id)
    await _send(session, org_id, campaign_id)

    wire = RecordingTransport()
    assert await consumers.fan_out(_envelope(org_id, campaign_id), transport=wire) == "capped"
    assert len(wire.sent) == 1
    assert (await _reload(session, campaign_id)).status == "sending"


async def test_a_list_campaign_reaches_only_the_list(session, org_id, worker_engine):
    sender_id = await _seed_identity(session, org_id)
    a, _b = await _contacts(session, org_id, "a@example.com", "b@example.com")
    async with session.begin():
        lst = await services.create_list(session, ctx_for(org_id, L_WRITE, Scope.ORG), name="L")
        await services.change_list_members(
            session,
            ctx_for(org_id, L_WRITE, Scope.ORG),
            list_id=lst.id,
            contact_ids=[a.id],
            add=True,
        )
    campaign_id = await _campaign(session, org_id, sender_id, list_id=lst.id)
    await _send(session, org_id, campaign_id)

    wire = RecordingTransport()
    assert await consumers.fan_out(_envelope(org_id, campaign_id), transport=wire) == "done"
    assert [m.to_address for m in wire.sent] == ["a@example.com"]


async def test_a_refused_recipient_is_failed_without_the_providers_words(
    client, session, org_id, worker_engine
):
    sender_id = await _seed_identity(session, org_id)
    await _contacts(session, org_id, "a@example.com")
    campaign_id = await _campaign(session, org_id, sender_id)
    await _send(session, org_id, campaign_id)

    wire = RecordingTransport(fail_with=TransportRefused("550 5.1.1 secret-host said no"))
    assert await consumers.fan_out(_envelope(org_id, campaign_id), transport=wire) == "done"
    with as_principal(caller(org_id, ALL)):
        page = await client.get(f"{CAMPAIGNS}/{campaign_id}/recipients")
        stats = await client.get(f"{CAMPAIGNS}/{campaign_id}/stats")
    assert page.json()["items"][0]["status"] == "failed"
    assert "secret-host" not in page.text
    assert "a@example.com" not in page.text
    assert stats.json()["failed"] == 1


# --------------------------------------------------------------------- negative authz

ROUTES = [
    ("get", LISTS, None, L_WRITE),
    ("post", LISTS, {"name": "x"}, L_READ),
    ("get", f"{LISTS}/{ABSENT}", None, L_WRITE),
    ("patch", f"{LISTS}/{ABSENT}", {"name": "x"}, L_READ),
    ("delete", f"{LISTS}/{ABSENT}", None, L_READ),
    ("get", f"{LISTS}/{ABSENT}/members", None, L_WRITE),
    ("post", f"{LISTS}/{ABSENT}/members", {"contact_ids": [str(ABSENT)]}, L_READ),
    ("post", f"{LISTS}/{ABSENT}/members/remove", {"contact_ids": [str(ABSENT)]}, L_READ),
    ("get", CAMPAIGNS, None, K_WRITE),
    ("post", CAMPAIGNS, {"name": "x", "sender_id": str(ABSENT)}, K_READ),
    ("get", f"{CAMPAIGNS}/{ABSENT}", None, K_WRITE),
    ("patch", f"{CAMPAIGNS}/{ABSENT}", {"name": "x"}, K_READ),
    ("delete", f"{CAMPAIGNS}/{ABSENT}", None, K_READ),
    # Drafting a campaign is not permission to send it.
    ("post", f"{CAMPAIGNS}/{ABSENT}/send", None, K_WRITE),
    ("post", f"{CAMPAIGNS}/{ABSENT}/cancel", None, K_READ),
    ("get", f"{CAMPAIGNS}/{ABSENT}/stats", None, K_WRITE),
    ("get", f"{CAMPAIGNS}/{ABSENT}/recipients", None, K_WRITE),
]


@pytest.mark.parametrize(("method", "path", "body", "other"), ROUTES)
async def test_every_route_refuses_a_caller_without_its_permission(
    client, org_id, method, path, body, other
):
    kwargs = {"json": body} if body is not None else {}
    with as_principal(caller(org_id, [f"{other}:ORG"])):
        response = await client.request(method.upper(), path, **kwargs)
    assert response.status_code == 403


@pytest.mark.parametrize(("method", "path", "body", "other"), ROUTES)
async def test_the_notification_entitlement_does_not_unlock_campaigns(
    client, org_id, method, path, body, other
):
    kwargs = {"json": body} if body is not None else {}
    with as_principal(principal_for(org_id, ALL, entitlements=["notification"])):
        response = await client.request(method.upper(), path, **kwargs)
    assert response.status_code == 402


# --------------------------------------------------------------------- cross-tenant isolation


async def test_another_tenants_lists_and_campaigns_are_invisible(
    client, session, admin_session, org_id
):
    other = uuid.uuid4()
    their_sender_id = await _seed_identity(admin_session, other)
    async with admin_session.begin():
        their_contact = await services.create_contact(
            admin_session, ctx_for(other, C_WRITE, Scope.ORG), email="them@example.com"
        )
        their_list = await services.create_list(
            admin_session, ctx_for(other, L_WRITE, Scope.ORG), name="Theirs"
        )
    their_contact_id, their_list_id = their_contact.id, their_list.id
    their_campaign_id = await _campaign(admin_session, other, their_sender_id)
    mine_id = await _seed_identity(session, org_id)

    try:
        with as_principal(caller(org_id, ALL)):
            assert (await client.get(LISTS)).json()["total"] == 0
            assert (await client.get(CAMPAIGNS)).json()["total"] == 0
            for path in (
                f"{LISTS}/{their_list_id}",
                f"{LISTS}/{their_list_id}/members",
                f"{CAMPAIGNS}/{their_campaign_id}",
                f"{CAMPAIGNS}/{their_campaign_id}/stats",
                f"{CAMPAIGNS}/{their_campaign_id}/recipients",
            ):
                assert (await client.get(path)).status_code == 404, path
            assert (await client.post(f"{CAMPAIGNS}/{their_campaign_id}/send")).status_code == 404
            assert (await client.post(f"{CAMPAIGNS}/{their_campaign_id}/cancel")).status_code == 404
            assert (await client.delete(f"{LISTS}/{their_list_id}")).status_code == 404
            assert (
                await client.post(
                    f"{LISTS}/{their_list_id}/members",
                    json={"contact_ids": [str(their_contact_id)]},
                )
            ).status_code == 404

            # Their contact cannot join my list, and their sender cannot front my campaign.
            my_list = (await client.post(LISTS, json={"name": "Mine"})).json()["id"]
            joined = await client.post(
                f"{LISTS}/{my_list}/members", json={"contact_ids": [str(their_contact_id)]}
            )
            assert joined.json() == {"changed": 0, "unknown": [str(their_contact_id)]}
            borrowed = await client.post(
                CAMPAIGNS, json={"name": "x", "sender_id": str(their_sender_id)}
            )
            assert borrowed.status_code == 422
            ok = await client.post(CAMPAIGNS, json={"name": "x", "sender_id": str(mine_id)})
            assert ok.status_code == 201

        async with session.begin():
            ids = set((await session.execute(select(models.Campaign.id))).scalars())
        assert their_campaign_id not in ids

        async with admin_session.begin():
            row = await admin_session.get(models.Campaign, their_campaign_id)
            assert row is not None and row.status == "draft"
    finally:
        async with admin_session.begin():
            for model in (
                models.Campaign,
                models.ContactListMember,
                models.ContactList,
                models.Contact,
                models.Sender,
                models.EmailDomain,
                models.EmailConnection,
                models.AuditLog,
            ):
                await admin_session.execute(
                    model.__table__.delete().where(model.organization_id == other)
                )
