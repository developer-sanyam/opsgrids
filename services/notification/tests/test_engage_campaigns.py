"""Engage lists and campaigns — CRUD, the send gate, expansion, and the send driver.

Real PostgreSQL as the app role. Expansion runs through `consumers.expand_campaign_send` itself,
on the process-wide engine the worker uses. Sending runs through `_drain`, a small test harness
that drives `services.claim_campaign_batch`/`record_campaign_batch` exactly the way `app.cron.
CampaignDriver._drain` does in production, against this test's own session rather than a second
engine — with a `RecordingTransport` injected, so nothing here opens a socket and every assertion
about "sent" is an assertion about what the transport was handed.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import func, select, update

from app import consumers, models, services
from app.config import settings
from app.main import app
from app.transports import Outgoing, Receipt, RecordingTransport, Transport, TransportRefused
from app.transports import send_batch as transport_send_batch
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


async def _drain(
    session,
    org_id,
    campaign_id,
    *,
    transport: Transport,
    batch_size: int = 200,
    lease_seconds: int = 300,
) -> str:
    """Sends batches for one campaign until it stops — the same claim/send/record loop
    `app.cron.CampaignDriver._drain` runs in production, against this test's own session
    instead of a second engine. Returns the final outcome (`done`, `stopped`, `capped`,
    `paused`, `idle`, or `unavailable`)."""
    while True:
        async with session.begin():
            outcome, batch = await services.claim_campaign_batch(
                session,
                organization_id=org_id,
                campaign_id=campaign_id,
                batch_size=batch_size,
                lease_seconds=lease_seconds,
                transport=transport,
            )
        if outcome != "claimed":
            return outcome
        assert batch is not None
        results = await transport_send_batch(transport, [m.outgoing for m in batch.messages])
        paired = list(zip((m.recipient_id for m in batch.messages), results, strict=True))
        async with session.begin():
            unavailable = await services.record_campaign_batch(
                session,
                organization_id=org_id,
                campaign_id=campaign_id,
                resolved=batch.resolved,
                sent=paired,
            )
        if unavailable:
            return "unavailable"


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


# --------------------------------------------------------------------- expansion (shared consumer)


async def test_expand_campaign_send_only_expands_and_never_touches_a_transport(
    session, org_id, worker_engine
):
    """The point of splitting expansion from sending (review finding 1): the handler bound to
    `notification.events` — the queue every other product's events, password resets included,
    also ride — must be able to finish fast whatever the size of the audience, because it never
    calls a provider at all. `expand_campaign_send` does not even accept a transport, so there
    is nothing for a large campaign to block another event behind here."""
    sender_id = await _seed_identity(session, org_id)
    await _contacts(session, org_id, "a@example.com", "b@example.com", "gone@example.com")
    async with session.begin():
        await services.add_suppression(
            session, ctx_for(org_id, S_WRITE, Scope.ORG), email="gone@example.com"
        )
    campaign_id = await _campaign(session, org_id, sender_id)
    await _send(session, org_id, campaign_id)

    envelope = _envelope(org_id, campaign_id)
    assert await consumers.expand_campaign_send(envelope) == "expanded"
    # The same event redelivered: expansion is idempotent, not run twice.
    assert await consumers.expand_campaign_send(envelope) == "redelivered"

    statuses = await _recipients(session, campaign_id)
    assert sorted(statuses.values()) == ["queued", "queued"]  # suppressed contact never expanded
    reloaded = await _reload(session, campaign_id)
    assert reloaded.status == "sending" and reloaded.sent_count == 0


async def test_expand_campaign_send_on_a_cancelled_campaign_is_a_no_op(
    session, org_id, worker_engine
):
    sender_id = await _seed_identity(session, org_id)
    await _contacts(session, org_id, "a@example.com")
    campaign_id = await _campaign(session, org_id, sender_id)
    await _send(session, org_id, campaign_id)
    async with session.begin():
        await services.cancel_campaign(
            session, ctx_for(org_id, K_WRITE, Scope.ORG), campaign_id=campaign_id
        )

    assert await consumers.expand_campaign_send(_envelope(org_id, campaign_id)) == "not_sending"
    assert await _recipients(session, campaign_id) == {}


# --------------------------------------------------------------------- the send driver


async def test_the_driver_sends_the_whole_audience_with_unsubscribe_header_and_footer(
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
    assert await consumers.expand_campaign_send(_envelope(org_id, campaign_id)) == "expanded"

    wire = RecordingTransport()
    assert await _drain(session, org_id, campaign_id, transport=wire, batch_size=1) == "done"
    # Draining an already-`sent` campaign again does nothing more.
    assert await _drain(session, org_id, campaign_id, transport=wire) == "stopped"

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
    assert await _drain(session, org_id, campaign_id, transport=wire) == "done"
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
    assert await consumers.expand_campaign_send(_envelope(org_id, campaign_id)) == "expanded"

    class CancelAfterFirst:
        """Sends, then cancels the campaign through the API — the operator pressing cancel
        while the first batch is going out, committed between this driver's claim/send and its
        next claim call."""

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
    outcome = await _drain(session, org_id, campaign_id, transport=wire, batch_size=1)
    assert outcome == "stopped"
    assert len(wire.sent) == 1
    reloaded = await _reload(session, campaign_id)
    # The batch already sent when cancel committed is recorded truthfully — cancel stops the
    # *next* batch, never un-sends mail that already left. And the campaign itself must never be
    # overwritten back to `sent` by a batch that started before the cancel (review finding 8).
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
    assert await consumers.expand_campaign_send(_envelope(org_id, campaign_id)) == "expanded"

    wire = RecordingTransport()
    assert await _drain(session, org_id, campaign_id, transport=wire) == "capped"
    assert len(wire.sent) == 1
    assert (await _reload(session, campaign_id)).status == "sending"


async def test_the_daily_cap_counts_rule_driven_mail_on_the_same_connection(
    session, org_id, worker_engine
):
    """Review finding 8: a rule that already sent transactional mail today leaves that much less
    room for a campaign, since both share one connection's `daily_cap`."""
    sender_id = await _seed_identity(session, org_id, cap=1)
    async with session.begin():
        session.add(
            models.Message(
                organization_id=org_id,
                channel="email",
                to_address="someone@example.com",
                body="Your invoice is ready",
                state="sent",
                sent_at=datetime.now(UTC),
            )
        )
    await _contacts(session, org_id, "a@example.com")
    campaign_id = await _campaign(session, org_id, sender_id)
    await _send(session, org_id, campaign_id)
    assert await consumers.expand_campaign_send(_envelope(org_id, campaign_id)) == "expanded"

    wire = RecordingTransport()
    assert await _drain(session, org_id, campaign_id, transport=wire) == "capped"
    assert len(wire.sent) == 0  # the cap was already spent by the rule-driven send


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
    assert await consumers.expand_campaign_send(_envelope(org_id, campaign_id)) == "expanded"

    wire = RecordingTransport()
    assert await _drain(session, org_id, campaign_id, transport=wire) == "done"
    assert [m.to_address for m in wire.sent] == ["a@example.com"]


async def test_a_refused_recipient_is_failed_without_the_providers_words(
    client, session, org_id, worker_engine
):
    sender_id = await _seed_identity(session, org_id)
    await _contacts(session, org_id, "a@example.com")
    campaign_id = await _campaign(session, org_id, sender_id)
    await _send(session, org_id, campaign_id)
    assert await consumers.expand_campaign_send(_envelope(org_id, campaign_id)) == "expanded"

    wire = RecordingTransport(fail_with=TransportRefused("550 5.1.1 secret-host said no"))
    assert await _drain(session, org_id, campaign_id, transport=wire) == "done"
    with as_principal(caller(org_id, ALL)):
        page = await client.get(f"{CAMPAIGNS}/{campaign_id}/recipients")
        stats = await client.get(f"{CAMPAIGNS}/{campaign_id}/stats")
    assert page.json()["items"][0]["status"] == "failed"
    assert "secret-host" not in page.text
    assert "a@example.com" not in page.text
    assert stats.json()["failed"] == 1


async def test_a_bad_row_does_not_block_the_rest_of_the_batch(session, org_id, worker_engine):
    """Review finding 4: one recipient whose sealed address will not open must not sink a batch
    that otherwise sent fine."""
    sender_id = await _seed_identity(session, org_id)
    a, _b = await _contacts(session, org_id, "a@example.com", "b@example.com")
    campaign_id = await _campaign(session, org_id, sender_id)
    await _send(session, org_id, campaign_id)
    assert await consumers.expand_campaign_send(_envelope(org_id, campaign_id)) == "expanded"
    async with session.begin():
        await session.execute(
            update(models.CampaignRecipient)
            .where(models.CampaignRecipient.contact_id == a.id)
            .values(email_enc=b"not-actually-sealed")
        )

    wire = RecordingTransport()
    assert await _drain(session, org_id, campaign_id, transport=wire) == "done"
    assert [m.to_address for m in wire.sent] == ["b@example.com"]
    statuses = await _recipients(session, campaign_id)
    assert sorted(statuses.values()) == ["failed", "sent"]


async def test_a_crashed_batch_is_reclaimed_and_not_double_sent(session, org_id, worker_engine):
    """Review: crash mid-batch resumes without double-sending already-sent recipients. Claiming
    (setting `status='sending'`) and never recording is exactly what a process dying between
    those two steps leaves behind; a fresh claim with an already-expired lease must pick the row
    back up, and a recipient already resolved `sent` must never be claimed again."""
    sender_id = await _seed_identity(session, org_id)
    await _contacts(session, org_id, "a@example.com", "b@example.com")
    campaign_id = await _campaign(session, org_id, sender_id)
    await _send(session, org_id, campaign_id)
    assert await consumers.expand_campaign_send(_envelope(org_id, campaign_id)) == "expanded"

    # Simulate the crash: claim both recipients and never call record — both rows are left
    # `sending`, exactly what a process dying between claim and record leaves behind.
    async with session.begin():
        outcome, batch = await services.claim_campaign_batch(
            session,
            organization_id=org_id,
            campaign_id=campaign_id,
            batch_size=10,
            lease_seconds=300,
            transport=RecordingTransport(),
        )
    assert outcome == "claimed" and batch is not None
    assert len(batch.messages) == 2
    rows = await _recipients_by_id(session, campaign_id)
    assert all(r.status == "sending" and r.claimed_at is not None for r in rows)

    # A lease that has not gone stale yet: nothing claimable at all.
    async with session.begin():
        outcome, _ = await services.claim_campaign_batch(
            session,
            organization_id=org_id,
            campaign_id=campaign_id,
            batch_size=1,
            lease_seconds=300,
            transport=RecordingTransport(),
        )
    assert outcome == "idle"

    # Once the lease is stale (`lease_seconds=0`, so any claimed-at is already past it), the
    # crashed row is reclaimed and this pass drains the whole campaign, sending it exactly once.
    wire = RecordingTransport()
    outcome = await _drain(
        session, org_id, campaign_id, transport=wire, batch_size=1, lease_seconds=0
    )
    assert outcome == "done"
    assert sorted(m.to_address for m in wire.sent) == ["a@example.com", "b@example.com"]
    assert len(wire.sent) == 2  # never sent twice, despite the abandoned claim
    statuses = await _recipients(session, campaign_id)
    assert sorted(statuses.values()) == ["sent", "sent"]


async def _recipients_by_id(session, campaign_id):
    async with session.begin():
        rows = (
            await session.execute(
                select(models.CampaignRecipient).where(
                    models.CampaignRecipient.campaign_id == campaign_id
                )
            )
        ).scalars()
        return list(rows)


# --------------------------------------------------------------------- pause and resume


async def test_no_connected_provider_pauses_the_campaign(session, org_id, worker_engine):
    sender_id = await _seed_identity(session, org_id, connected=False)
    await _contacts(session, org_id, "a@example.com")
    campaign_id = await _campaign(session, org_id, sender_id)
    async with session.begin():
        # `request_send` itself refuses without a connection (test_send_refuses_without_an_
        # active_connection), so seed `sending` directly to exercise the driver's own guard —
        # the connection dropping *after* a send was accepted, mid-campaign.
        row = await session.get(models.Campaign, campaign_id)
        row.status = "sending"
    assert await consumers.expand_campaign_send(_envelope(org_id, campaign_id)) == "expanded"

    async with session.begin():
        outcome, batch = await services.claim_campaign_batch(
            session,
            organization_id=org_id,
            campaign_id=campaign_id,
            batch_size=10,
            lease_seconds=300,
        )
    assert outcome == "paused" and batch is None
    reloaded = await _reload(session, campaign_id)
    assert reloaded.status == "paused"
    assert "provider" in reloaded.pause_reason.lower()
    assert reloaded.paused_at is not None


async def test_a_route_that_comes_back_resumes_on_its_own(session, org_id, worker_engine):
    sender_id = await _seed_identity(session, org_id, connected=False)
    await _contacts(session, org_id, "a@example.com")
    campaign_id = await _campaign(session, org_id, sender_id)
    async with session.begin():
        row = await session.get(models.Campaign, campaign_id)
        row.status = "sending"
    assert await consumers.expand_campaign_send(_envelope(org_id, campaign_id)) == "expanded"
    async with session.begin():
        await services.claim_campaign_batch(
            session,
            organization_id=org_id,
            campaign_id=campaign_id,
            batch_size=10,
            lease_seconds=300,
        )
    assert (await _reload(session, campaign_id)).status == "paused"

    async with session.begin():
        session.add(
            models.EmailConnection(
                organization_id=org_id,
                kind="google",
                host="smtp.gmail.com",
                username="news@company.com",
                daily_cap=2000,
            )
        )

    wire = RecordingTransport()
    assert await _drain(session, org_id, campaign_id, transport=wire) == "done"
    assert [m.to_address for m in wire.sent] == ["a@example.com"]
    reloaded = await _reload(session, campaign_id)
    assert reloaded.status == "sent" and reloaded.pause_reason == ""


async def test_resume_endpoint_retries_now_and_refuses_a_non_paused_campaign(
    client, session, org_id, worker_engine
):
    sender_id = await _seed_identity(session, org_id, connected=False)
    await _contacts(session, org_id, "a@example.com")
    campaign_id = await _campaign(session, org_id, sender_id)
    async with session.begin():
        row = await session.get(models.Campaign, campaign_id)
        row.status = "sending"
    assert await consumers.expand_campaign_send(_envelope(org_id, campaign_id)) == "expanded"
    async with session.begin():
        await services.claim_campaign_batch(
            session,
            organization_id=org_id,
            campaign_id=campaign_id,
            batch_size=10,
            lease_seconds=300,
        )
    assert (await _reload(session, campaign_id)).status == "paused"

    with as_principal(caller(org_id, ALL)):
        # Still no connection: resuming flips the status but the very next claim pauses again.
        resumed = await client.post(f"{CAMPAIGNS}/{campaign_id}/resume")
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["status"] == "sending"
        assert (await client.post(f"{CAMPAIGNS}/{campaign_id}/resume")).status_code == 409

    async with session.begin():
        session.add(
            models.EmailConnection(
                organization_id=org_id,
                kind="google",
                host="smtp.gmail.com",
                username="news@company.com",
                daily_cap=2000,
            )
        )
    wire = RecordingTransport()
    assert await _drain(session, org_id, campaign_id, transport=wire) == "done"
    assert [m.to_address for m in wire.sent] == ["a@example.com"]


# --------------------------------------------------------------------- header injection


async def test_crlf_in_a_campaign_subject_is_rejected(client, session, org_id):
    sender_id = await _seed_identity(session, org_id)
    with as_principal(caller(org_id, ALL)):
        bad = await client.post(
            CAMPAIGNS,
            json={
                "name": "x",
                "sender_id": str(sender_id),
                "subject": "Hi\r\nBcc: attacker@evil.example",
            },
        )
        assert bad.status_code == 422

        created = await client.post(
            CAMPAIGNS, json={"name": "y", "sender_id": str(sender_id), "subject": "Fine"}
        )
        cid = created.json()["id"]
        patched = await client.patch(f"{CAMPAIGNS}/{cid}", json={"subject": "Hi\nSubject: second"})
        assert patched.status_code == 422


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
    ("post", f"{CAMPAIGNS}/{ABSENT}/resume", None, K_WRITE),
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
            assert (await client.post(f"{CAMPAIGNS}/{their_campaign_id}/resume")).status_code == 404
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
