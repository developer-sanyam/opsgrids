"""Engage segments and WhatsApp broadcast campaigns.

A segment is a saved filter over the contact book (list membership, created and last-reached
windows), evaluated when a campaign expands — never a stored snapshot. A WhatsApp campaign sends
an approved template to every contact in its audience whose WhatsApp number is on the consent
ledger with a standing marketing opt-in, through `services.stage_message` — the same path, outbox
and dispatcher an automation's WhatsApp step uses. Real PostgreSQL as the app role.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app import consent, models, selectors, services
from app.main import app
from platform_core.auth import Principal, Scope, get_principal
from tests.helpers import ctx_for, principal_for

pytestmark = pytest.mark.integration

SEGMENTS = "/api/v1/engage/segments"
CAMPAIGNS = "/api/v1/engage/campaigns"
CONSENT = "/api/v1/engage/consent/events"

L_READ = "engage.audience.list.read"
L_WRITE = "engage.audience.list.write"
K_READ = "engage.campaign.campaign.read"
K_WRITE = "engage.campaign.campaign.write"
K_SEND = "engage.campaign.campaign.send"
C_WRITE = "engage.audience.contact.write"
CONSENT_WRITE = C_WRITE
ALL = [f"{p}:ORG" for p in (L_READ, L_WRITE, K_READ, K_WRITE, K_SEND, C_WRITE)]
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


async def _contact(session, org_id, email: str, phone: str | None = None) -> uuid.UUID:
    """A contact and, when `phone` is given, a WhatsApp marketing opt-in linked to it."""
    async with session.begin():
        row = await services.create_contact(
            session, ctx_for(org_id, C_WRITE, Scope.ORG), email=email
        )
        if phone is not None:
            await services.record_consent_event(
                session,
                ctx_for(org_id, CONSENT_WRITE, Scope.ORG),
                channel="whatsapp",
                purpose="marketing",
                status="opted_in",
                source="web_form",
                evidence="signup form 12",
                address=phone,
                contact_id=row.id,
                occurred_at=None,
                idempotency_key=None,
            )
    return row.id


async def _opt_out(session, org_id, phone: str) -> None:
    async with session.begin():
        await services.record_consent_event(
            session,
            ctx_for(org_id, CONSENT_WRITE, Scope.ORG),
            channel="whatsapp",
            purpose="marketing",
            status="opted_out",
            source="admin",
            evidence="asked by phone, call 9",
            address=phone,
            contact_id=None,
            occurred_at=None,
            idempotency_key=None,
        )


async def _whatsapp(session, org_id, *, status: str = "approved", connected: bool = True):
    """A WhatsApp connection and one marketing template. Returns the template id."""
    async with session.begin():
        if connected:
            session.add(
                models.ProviderAccount(
                    organization_id=org_id,
                    channel="whatsapp",
                    waba_id=f"waba-{uuid.uuid4().hex[:8]}",
                    phone_number_id=f"pn-{uuid.uuid4().hex[:8]}",
                )
            )
        template = models.ProviderTemplate(
            organization_id=org_id,
            code="sale",
            channel="whatsapp",
            provider_name="autumn_sale",
            language="en",
            category="marketing",
            status=status,
            variables=[{"key": "code", "component": "body", "position": 0, "kind": "text"}],
            preview_text="Sale code {{1}}",
        )
        session.add(template)
        await session.flush()
    return template.id


async def _segment(session, org_id, **fields) -> uuid.UUID:
    async with session.begin():
        row = await services.create_segment(
            session, ctx_for(org_id, L_WRITE, Scope.ORG), name=fields.pop("name", "All"), **fields
        )
    return row.id


async def _wa_campaign(session, org_id, segment_id, template_id) -> uuid.UUID:
    async with session.begin():
        row = await services.create_campaign(
            session,
            ctx_for(org_id, K_WRITE, Scope.ORG),
            fields={
                "name": "Autumn on WhatsApp",
                "channel": "whatsapp",
                "audience": "segment",
                "segment_id": segment_id,
                "provider_template_id": template_id,
                "template_values": {"code": "AUTUMN10"},
            },
        )
    return row.id


async def _send_and_expand(session, org_id, campaign_id) -> None:
    async with session.begin():
        await services.request_send(
            session, ctx_for(org_id, K_SEND, Scope.ORG), campaign_id=campaign_id
        )
    async with session.begin():
        await services.expand_campaign(
            session, organization_id=org_id, campaign_id=campaign_id, batch_size=2
        )


async def _messages(session) -> list[models.Message]:
    async with session.begin():
        return list((await session.execute(select(models.Message))).scalars())


# --------------------------------------------------------------------- consent links a number


async def test_a_whatsapp_opt_in_can_name_the_contact_it_belongs_to(client, session, org_id):
    contact_id = await _contact(session, org_id, "a@example.com")
    payload = {
        "channel": "whatsapp",
        "purpose": "marketing",
        "status": "opted_in",
        "source": "web_form",
        "evidence": "signup form 12",
        "address": "+919812345678",
        "contact_id": str(contact_id),
    }
    with as_principal(caller(org_id, [f"{CONSENT_WRITE}:ORG"])):
        response = await client.post(CONSENT, json=payload)
        assert response.status_code == 201, response.text
        assert response.json()["event"]["contact_id"] == str(contact_id)
        # A WhatsApp event names its number; the contact alone carries no number to copy.
        no_number = await client.post(CONSENT, json={**payload, "address": None})
        assert no_number.status_code == 422
        unknown = await client.post(CONSENT, json={**payload, "contact_id": str(ABSENT)})
        assert unknown.status_code == 404


# --------------------------------------------------------------------- segment evaluation


async def test_a_segment_counts_only_contacts_in_its_filter_with_consent_for_the_channel(
    client, session, org_id
):
    in_list = await _contact(session, org_id, "in@example.com", "+919800000001")
    await _contact(session, org_id, "out@example.com", "+919800000002")  # not on the list
    no_number = await _contact(session, org_id, "nonum@example.com")
    opted_out = await _contact(session, org_id, "gone@example.com", "+919800000003")
    await _opt_out(session, org_id, "+919800000003")
    async with session.begin():
        lst = await services.create_list(session, ctx_for(org_id, L_WRITE, Scope.ORG), name="VIP")
        await services.change_list_members(
            session,
            ctx_for(org_id, L_WRITE, Scope.ORG),
            list_id=lst.id,
            contact_ids=[in_list, no_number, opted_out],
            add=True,
        )
    list_id = lst.id

    with as_principal(caller(org_id, ALL)):
        created = await client.post(SEGMENTS, json={"name": "VIP", "list_id": str(list_id)})
        assert created.status_code == 201, created.text
        seg = created.json()["id"]
        assert (await client.get(SEGMENTS)).json()["total"] == 1
        wa = await client.get(f"{SEGMENTS}/{seg}/count", params={"channel": "whatsapp"})
        assert wa.json() == {"channel": "whatsapp", "recipients": 1}
        # Email consent is its own ledger entry: an operator-added contact is attested for email,
        # so all three list members can be emailed, WhatsApp opt-out or not.
        email = await client.get(f"{SEGMENTS}/{seg}/count", params={"channel": "email"})
        assert email.json() == {"channel": "email", "recipients": 3}


async def test_created_and_last_reached_windows_narrow_the_segment(session, org_id):
    fresh = await _contact(session, org_id, "fresh@example.com", "+919800000011")
    old = await _contact(session, org_id, "old@example.com", "+919800000012")
    async with session.begin():
        await session.execute(
            update(models.Contact)
            .where(models.Contact.id == old)
            .values(created_at=datetime.now(UTC) - timedelta(days=90))
        )
    recent = await _segment(session, org_id, name="New", created_within_days=30)
    reached = await _segment(session, org_id, name="Reached", active_within_days=30)
    ctx = ctx_for(org_id, L_READ, Scope.ORG)
    async with session.begin():
        seg = await selectors.get_segment(session, org_id, recent)
        assert seg is not None
        rows = await selectors.whatsapp_recipients(session, ctx, segment=seg)
        assert [r.contact_id for r in rows] == [fresh]
        # Nobody has been sent a campaign yet, so nobody was reached in the window.
        seg2 = await selectors.get_segment(session, org_id, reached)
        assert seg2 is not None
        assert await selectors.count_audience(session, ctx, channel="whatsapp", segment=seg2) == 0


# --------------------------------------------------------------------- the WhatsApp campaign


async def test_a_whatsapp_campaign_sends_the_approved_template_to_consented_contacts_only(
    session, org_id
):
    yes = await _contact(session, org_id, "yes@example.com", "+919812345601")
    await _contact(session, org_id, "silent@example.com")  # no number on the ledger
    late = await _contact(session, org_id, "late@example.com", "+919812345602")
    template_id = await _whatsapp(session, org_id)
    segment_id = await _segment(session, org_id)
    campaign_id = await _wa_campaign(session, org_id, segment_id, template_id)

    async with session.begin():
        await services.request_send(
            session, ctx_for(org_id, K_SEND, Scope.ORG), campaign_id=campaign_id
        )
    # An opt-out that lands between the gate and expansion wins: stage_message asks the ledger.
    await _opt_out(session, org_id, "+919812345602")
    async with session.begin():
        await services.expand_campaign(
            session, organization_id=org_id, campaign_id=campaign_id, batch_size=1
        )
        # Redelivered expansion stages nothing twice.
        await services.expand_campaign(
            session, organization_id=org_id, campaign_id=campaign_id, batch_size=1
        )

    sent = [m for m in await _messages(session) if m.state == "pending"]
    assert [m.to_address for m in sent] == ["+919812345601"]
    message = sent[0]
    assert message.channel == "whatsapp" and message.category == consent.MARKETING_CATEGORY
    assert message.provider_template_code == "sale"
    assert message.provider_template_payload is not None
    assert "AUTUMN10" in str(message.provider_template_payload)
    assert message.dedupe_key == f"campaign:{campaign_id}:{yes}"
    async with session.begin():
        outbox = list((await session.execute(select(models.OutboxEvent))).scalars())
        campaign = await selectors.get_campaign(session, org_id, campaign_id)
    assert any(e.event_type == services.EVT_MESSAGE_QUEUED for e in outbox)
    assert campaign is not None and campaign.status == "sent"
    # `late` opted out after the gate: the audience is read at expansion, so they are not in it.
    assert (campaign.recipient_count, campaign.sent_count, campaign.suppressed_count) == (1, 1, 0)
    assert all(m.dedupe_key != f"campaign:{campaign_id}:{late}" for m in await _messages(session))


async def test_an_email_campaign_can_address_a_segment(session, org_id):
    from tests.test_engage_campaigns import _seed_identity

    sender_id = await _seed_identity(session, org_id)
    member = await _contact(session, org_id, "member@example.com")
    await _contact(session, org_id, "other@example.com")
    async with session.begin():
        lst = await services.create_list(session, ctx_for(org_id, L_WRITE, Scope.ORG), name="L")
        await services.change_list_members(
            session,
            ctx_for(org_id, L_WRITE, Scope.ORG),
            list_id=lst.id,
            contact_ids=[member],
            add=True,
        )
    segment_id = await _segment(session, org_id, list_id=lst.id)
    async with session.begin():
        row = await services.create_campaign(
            session,
            ctx_for(org_id, K_WRITE, Scope.ORG),
            fields={
                "name": "Email to a segment",
                "sender_id": sender_id,
                "subject": "Hi",
                "text_body": "Hello",
                "audience": "segment",
                "segment_id": segment_id,
            },
        )
        row.status = "sending"
    async with session.begin():
        await services.expand_campaign(
            session, organization_id=org_id, campaign_id=row.id, batch_size=10
        )
        recipients = list((await session.execute(select(models.CampaignRecipient))).scalars())
    assert [r.contact_id for r in recipients] == [member]


async def test_the_whatsapp_send_gate(session, org_id):
    await _contact(session, org_id, "yes@example.com", "+919812345601")
    pending = await _whatsapp(session, org_id, status="pending")
    segment_id = await _segment(session, org_id)
    campaign_id = await _wa_campaign(session, org_id, segment_id, pending)
    with pytest.raises(services.BusinessRuleProblem, match="approved"):
        async with session.begin():
            await services.request_send(
                session, ctx_for(org_id, K_SEND, Scope.ORG), campaign_id=campaign_id
            )


async def test_a_whatsapp_campaign_with_nobody_consented_is_refused(session, org_id):
    await _contact(session, org_id, "silent@example.com")
    template_id = await _whatsapp(session, org_id)
    segment_id = await _segment(session, org_id)
    campaign_id = await _wa_campaign(session, org_id, segment_id, template_id)
    with pytest.raises(services.BusinessRuleProblem, match="WhatsApp"):
        async with session.begin():
            await services.request_send(
                session, ctx_for(org_id, K_SEND, Scope.ORG), campaign_id=campaign_id
            )


async def test_a_whatsapp_campaign_needs_a_connected_account(session, org_id):
    await _contact(session, org_id, "yes@example.com", "+919812345601")
    template_id = await _whatsapp(session, org_id, connected=False)
    segment_id = await _segment(session, org_id)
    campaign_id = await _wa_campaign(session, org_id, segment_id, template_id)
    with pytest.raises(services.BusinessRuleProblem, match="Connect WhatsApp"):
        async with session.begin():
            await services.request_send(
                session, ctx_for(org_id, K_SEND, Scope.ORG), campaign_id=campaign_id
            )


async def test_campaign_shape_is_validated_per_channel(client, session, org_id):
    template_id = await _whatsapp(session, org_id)
    segment_id = await _segment(session, org_id)
    with as_principal(caller(org_id, ALL)):
        # Email still needs a sender.
        assert (await client.post(CAMPAIGNS, json={"name": "x"})).status_code == 422
        # WhatsApp needs a template.
        no_template = await client.post(
            CAMPAIGNS,
            json={
                "name": "x",
                "channel": "whatsapp",
                "audience": "segment",
                "segment_id": str(segment_id),
            },
        )
        assert no_template.status_code == 422
        ok = await client.post(
            CAMPAIGNS,
            json={
                "name": "x",
                "channel": "whatsapp",
                "audience": "segment",
                "segment_id": str(segment_id),
                "provider_template_id": str(template_id),
            },
        )
        assert ok.status_code == 201, ok.text
        body = ok.json()
        assert body["channel"] == "whatsapp" and body["sender_id"] is None
        assert body["segment_id"] == str(segment_id)
        # A WhatsApp campaign has no email test send.
        test = await client.post(f"{CAMPAIGNS}/{body['id']}/test", json={"to_address": "a@b.co"})
        assert test.status_code == 422
        # A segment a campaign uses cannot be deleted from under it.
        assert (await client.delete(f"{SEGMENTS}/{segment_id}")).status_code == 409


# --------------------------------------------------------------------- authz and isolation

ROUTES = [
    ("get", SEGMENTS, None, K_READ),
    ("post", SEGMENTS, {"name": "x"}, L_READ),
    ("delete", f"{SEGMENTS}/{ABSENT}", None, L_READ),
    ("get", f"{SEGMENTS}/{ABSENT}/count?channel=whatsapp", None, K_READ),
]


@pytest.mark.parametrize(("method", "path", "body", "other"), ROUTES)
async def test_every_segment_route_refuses_a_caller_without_its_permission(
    client, org_id, method, path, body, other
):
    kwargs = {"json": body} if body is not None else {}
    with as_principal(caller(org_id, [f"{other}:ORG"])):
        response = await client.request(method.upper(), path, **kwargs)
    assert response.status_code == 403


async def test_another_tenants_segments_templates_and_lists_are_out_of_reach(
    client, session, admin_session, org_id
):
    other = uuid.uuid4()
    their_template = await _whatsapp(admin_session, other)
    async with admin_session.begin():
        their_list = await services.create_list(
            admin_session, ctx_for(other, L_WRITE, Scope.ORG), name="Theirs"
        )
        their_segment = await services.create_segment(
            admin_session, ctx_for(other, L_WRITE, Scope.ORG), name="Theirs"
        )
    their_list_id, their_segment_id = their_list.id, their_segment.id
    mine = await _segment(session, org_id)
    try:
        with as_principal(caller(org_id, ALL)):
            assert (await client.get(SEGMENTS)).json()["total"] == 1
            count = await client.get(f"{SEGMENTS}/{their_segment_id}/count?channel=whatsapp")
            assert count.status_code == 404
            assert (await client.delete(f"{SEGMENTS}/{their_segment_id}")).status_code == 404
            borrowed_list = await client.post(
                SEGMENTS, json={"name": "y", "list_id": str(their_list_id)}
            )
            assert borrowed_list.status_code == 422
            for fields in (
                {"segment_id": str(their_segment_id), "provider_template_id": str(their_template)},
                {"segment_id": str(mine), "provider_template_id": str(their_template)},
            ):
                refused = await client.post(
                    CAMPAIGNS,
                    json={"name": "z", "channel": "whatsapp", "audience": "segment", **fields},
                )
                assert refused.status_code == 422, fields
        async with admin_session.begin():
            assert await admin_session.get(models.Segment, their_segment_id) is not None
    finally:
        async with admin_session.begin():
            for model in (
                models.Segment,
                models.ContactList,
                models.ProviderTemplate,
                models.ProviderAccount,
            ):
                await admin_session.execute(
                    model.__table__.delete().where(model.organization_id == other)
                )
