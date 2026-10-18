"""Cold e-mail to CRM leads (ADR-0038 addendum 2026-10-16).

Real PostgreSQL as the app role. Lead is faked at `crm_leads.fetch_leads` (the one network seam),
with a fake that applies the `owner_id` it is handed exactly as lead's export does, so the OWN
tests prove the scope reaches the read. The send path runs through the real expansion and the same
claim/send/record loop the driver uses, with a `RecordingTransport`.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app import consent, consumers, crm_leads, models, selectors, services
from app.config import settings
from app.crm_leads import LeadRow, LeadsUnavailable, TooManyLeads
from app.main import app
from app.transports import RecordingTransport
from platform_core.auth import Principal, Scope, get_principal
from platform_core.db import dispose_engine, init_engine
from tests.helpers import ctx_for, principal_for
from tests.test_engage_campaigns import (
    _drain,
    _envelope,
    _seed_identity,
)

pytestmark = pytest.mark.integration

PREVIEW = "/api/v1/engage/lead-campaigns/preview"
CREATE = "/api/v1/engage/lead-campaigns"
CAMPAIGNS = "/api/v1/engage/campaigns"

K_READ = "engage.campaign.campaign.read"
K_WRITE = "engage.campaign.campaign.write"
K_SEND = "engage.campaign.campaign.send"
S_WRITE = "engage.audience.suppression.write"
LEAD_READ = "crm.lead.lead.read"

REP = uuid.uuid4()
OTHER_REP = uuid.uuid4()


def lead(
    email: str | None,
    name: str = "Priya Sharma",
    *,
    company: str | None = "Acme Traders",
    status: str = "new",
    source: str = "form",
) -> LeadRow:
    return LeadRow(uuid.uuid4(), name, company, email, status, source)


class FakeLeads:
    """Lead's side of the wire: a store of `(owner, LeadRow)` read the way `leads:export` reads it,
    narrowed to `owner_id` when one is passed."""

    def __init__(self) -> None:
        self.rows: list[tuple[uuid.UUID, LeadRow]] = []
        self.calls: list[dict] = []
        self.error: Exception | None = None

    def add(self, owner: uuid.UUID, row: LeadRow) -> LeadRow:
        self.rows.append((owner, row))
        return row

    async def __call__(self, cfg, organization_id, *, ids=None, owner_id=None, emails=True, **kw):
        self.calls.append({"org": organization_id, "ids": ids, "owner_id": owner_id, **kw})
        if self.error is not None:
            raise self.error
        out = [
            r
            for o, r in self.rows
            if (ids is None or r.lead_id in ids)
            and (owner_id is None or o == owner_id)
            and (kw.get("status") in (None, r.status))
            and (kw.get("source") in (None, r.source))
        ]
        return out if emails else [replace(r, email=None) for r in out]


@pytest_asyncio.fixture
async def worker_engine(session):
    """The engine the worker's `session_scope` uses; depends on `session` so tables are clean."""
    init_engine(settings())
    yield
    await dispose_engine()


@pytest.fixture
def leads(monkeypatch: pytest.MonkeyPatch) -> FakeLeads:
    fake = FakeLeads()
    monkeypatch.setattr(crm_leads, "fetch_leads", fake)
    return fake


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def rep(
    org_id: uuid.UUID,
    *,
    lead_scope: str | None = "ORG",
    write: bool = True,
    entitlements: tuple[str, ...] = ("engage", "crm"),
    send: bool = False,
) -> Principal:
    perms = [f"{K_READ}:ORG"]
    if write:
        perms.append(f"{K_WRITE}:ORG")
    if send:
        perms.append(f"{K_SEND}:ORG")
    if lead_scope:
        perms.append(f"{LEAD_READ}:{lead_scope}")
    return principal_for(org_id, perms, entitlements=list(entitlements), subject_ref=REP)


def body(selected: list[LeadRow] | None = None, **extra) -> dict:
    out = {"subject": "Hi {{first_name}} at {{company_name}}", "text_body": "Hello {{first_name}}"}
    if selected is not None:
        out["lead_ids"] = [str(r.lead_id) for r in selected]
    return {**out, **extra}


async def _mark_suppressed(session, org_id: uuid.UUID, email: str) -> None:
    async with session.begin():
        await services.add_suppression(session, ctx_for(org_id, S_WRITE, Scope.ORG), email=email)


async def _contacts(session) -> list[models.Contact]:
    async with session.begin():
        return list((await session.execute(select(models.Contact))).scalars())


# --------------------------------------------------------------------- the audience counts


async def test_preview_counts_exclude_suppressed_opted_out_no_email_and_closed_leads(
    client, session, org_id, leads
):
    ok1 = leads.add(REP, lead("a@acme.test", "Priya Sharma"))
    ok2 = leads.add(REP, lead("b@acme.test", "Ravi Kumar", company=None))
    no_email = leads.add(REP, lead(None, "No Email"))
    suppressed = leads.add(REP, lead("sup@acme.test"))
    opted_out = leads.add(REP, lead("out@acme.test"))
    converted = leads.add(REP, lead("won@acme.test", status="converted"))
    await _mark_suppressed(session, org_id, "sup@acme.test")
    async with session.begin():
        await services.record_consent_event(
            session,
            ctx_for(org_id, "engage.audience.consent.write", Scope.ORG),
            channel="email",
            purpose=consent.MARKETING,
            status=consent.OPTED_OUT,
            source="admin",
            evidence="asked by phone to stop marketing mail",
            address="out@acme.test",
            contact_id=None,
            occurred_at=None,
            idempotency_key=None,
        )
    chosen = [ok1, ok2, no_email, suppressed, opted_out, converted]

    with as_principal(rep(org_id)):
        r = await client.post(PREVIEW, json=body(chosen))
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["selected"], out["with_email"]) == (6, 5)
    assert (out["skipped_status"], out["excluded"], out["sendable"]) == (1, 2, 2)
    assert (out["daily_cap"], out["sent_today"]) == (200, 0)
    # A real lead, filled in; never an address.
    assert out["sample"]["subject"] == "Hi Priya at Acme Traders"
    assert "@" not in r.text
    assert out["unknown_fields"] == []
    # Preview writes nothing.
    assert await _contacts(session) == []


async def test_preview_names_merge_fields_no_lead_can_fill(client, org_id, leads):
    one = leads.add(REP, lead("a@acme.test"))
    with as_principal(rep(org_id)):
        r = await client.post(
            PREVIEW, json=body([one], subject="Hi {{compny_name}} {{lead_status}}")
        )
    assert r.json()["unknown_fields"] == ["compny_name"]


async def test_a_filter_selection_reads_everything_that_matches(client, org_id, leads):
    leads.add(REP, lead("a@acme.test", status="new"))
    leads.add(REP, lead("b@acme.test", status="working"))
    with as_principal(rep(org_id)):
        r = await client.post(PREVIEW, json={**body(), "status": "working"})
    assert r.json()["selected"] == 1
    assert leads.calls[-1]["ids"] is None and leads.calls[-1]["status"] == "working"


async def test_ids_and_a_filter_together_are_refused(client, org_id, leads):
    one = leads.add(REP, lead("a@acme.test"))
    with as_principal(rep(org_id)):
        r = await client.post(PREVIEW, json=body([one], status="new"))
    assert r.status_code == 422


# --------------------------------------------------------------------- creating the draft


async def test_creating_copies_leads_into_the_book_and_writes_no_consent(
    client, session, org_id, leads
):
    sender_id = await _seed_identity(session, org_id)
    a = leads.add(REP, lead("a@acme.test"))
    b = leads.add(REP, lead("b@acme.test", "Ravi Kumar", company="Beta Ltd", source="import"))
    leads.add(REP, lead("c@acme.test"))  # not selected

    with as_principal(rep(org_id)):
        r = await client.post(CREATE, json=body([a, b], sender_id=str(sender_id), name="Autumn"))
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["sendable"] == 2 and out["campaign"]["status"] == "draft"
    assert out["campaign"]["cold_outreach"] is True
    assert out["campaign"]["audience"] == "list"

    contacts = {c.crm_lead_id: c for c in await _contacts(session)}
    assert set(contacts) == {a.lead_id, b.lead_id}
    ravi = contacts[b.lead_id]
    assert ravi.origin == "crm_lead"
    assert ravi.attributes == {
        "lead_status": "new",
        "lead_source": "import",
        "company_name": "Beta Ltd",
    }
    async with session.begin():
        assert (await session.execute(select(func.count(models.ConsentEvent.id)))).scalar_one() == 0
        members = (
            await session.execute(select(func.count(models.ContactListMember.id)))
        ).scalar_one()
    assert members == 2


async def test_creating_with_nobody_mailable_is_a_422_and_leaves_nothing(
    client, session, org_id, leads
):
    sender_id = await _seed_identity(session, org_id)
    only = leads.add(REP, lead(None))
    with as_principal(rep(org_id)):
        r = await client.post(CREATE, json=body([only], sender_id=str(sender_id)))
    assert r.status_code == 422
    assert await _contacts(session) == []


async def test_an_existing_contact_is_linked_not_duplicated_and_keeps_its_consent(
    client, session, org_id, leads
):
    sender_id = await _seed_identity(session, org_id)
    async with session.begin():
        existing = await services.create_contact(
            session,
            ctx_for(org_id, "engage.audience.contact.write", Scope.ORG),
            email="a@acme.test",
            name="Priya",
        )
    a = leads.add(REP, lead("a@acme.test"))
    with as_principal(rep(org_id)):
        r = await client.post(CREATE, json=body([a], sender_id=str(sender_id)))
        again = await client.post(CREATE, json=body([a], sender_id=str(sender_id)))
    assert r.status_code == again.status_code == 201
    contacts = await _contacts(session)
    assert [(c.id, c.origin, c.crm_lead_id) for c in contacts] == [
        (existing.id, "manual", a.lead_id)
    ]


# --------------------------------------------------------------------- sending


async def _create(client, session, org_id, leads, *selected, **extra) -> tuple[str, uuid.UUID]:
    sender_id = await _seed_identity(session, org_id, cap=extra.pop("cap", 2000))
    with as_principal(rep(org_id, send=True)):
        r = await client.post(CREATE, json=body(list(selected), sender_id=str(sender_id), **extra))
        assert r.status_code == 201, r.text
    return r.json()["campaign"]["id"], sender_id


async def _go(client, org_id, campaign_id: str) -> None:
    with as_principal(rep(org_id, send=True)):
        sent = await client.post(f"{CAMPAIGNS}/{campaign_id}/send")
    assert sent.status_code == 202, sent.text
    assert (
        await consumers.expand_campaign_send(_envelope(org_id, uuid.UUID(campaign_id)))
        == "expanded"
    )


async def test_a_cold_campaign_mails_leads_with_filled_fields_and_an_unsubscribe_link(
    client,
    session,
    org_id,
    leads,
    worker_engine,
):
    a = leads.add(REP, lead("a@acme.test", "Priya Sharma", status="working"))
    b = leads.add(REP, lead("b@acme.test", "Ravi Kumar", company="Beta Ltd"))
    campaign_id, _ = await _create(
        client,
        session,
        org_id,
        leads,
        a,
        b,
        subject="Hi {{first_name}} of {{company_name}}",
        text_body="Your lead status is {{lead_status}}.",
    )
    await _go(client, org_id, campaign_id)

    wire = RecordingTransport()
    assert await _drain(session, org_id, uuid.UUID(campaign_id), transport=wire) == "done"
    by_to = {m.to_address: m for m in wire.sent}
    assert set(by_to) == {"a@acme.test", "b@acme.test"}
    assert by_to["a@acme.test"].subject == "Hi Priya of Acme Traders"
    assert "Your lead status is working." in by_to["a@acme.test"].body
    assert by_to["b@acme.test"].subject == "Hi Ravi of Beta Ltd"
    for m in wire.sent:
        assert m.headers is not None
        assert m.headers["List-Unsubscribe"].startswith("<http")
        assert m.headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
        assert "/engage/unsubscribe/" in m.body
    async with session.begin():
        assert (await session.execute(select(func.count(models.ConsentEvent.id)))).scalar_one() == 0


async def test_an_ordinary_campaign_still_needs_an_opt_in_for_the_same_leads(
    client, session, org_id, leads
):
    """The flag is the only way round `opted_in`: a normal campaign over the lead copies reaches
    nobody, and so does a cold one pointed at a contact that is not a lead."""
    a = leads.add(REP, lead("a@acme.test"))
    campaign_id, sender_id = await _create(client, session, org_id, leads, a)
    async with session.begin():
        row = await session.get(models.Campaign, uuid.UUID(campaign_id))
        list_id = row.list_id
        plain = models.Campaign(
            organization_id=org_id,
            name="Ordinary",
            sender_id=sender_id,
            subject="s",
            text_body="t",
            audience="list",
            list_id=list_id,
            status="draft",
        )
        session.add(plain)
        await session.flush()
        plain_id = plain.id
    with as_principal(rep(org_id, send=True)):
        refused = await client.post(f"{CAMPAIGNS}/{plain_id}/send")
    assert refused.status_code == 422

    # A manual contact added to the cold list is not mailable by the cold flag.
    async with session.begin():
        manual = await services.create_contact(
            session, ctx_for(org_id, "engage.audience.contact.write", Scope.ORG), email="m@x.test"
        )
        await services.change_list_members(
            session,
            ctx_for(org_id, "engage.audience.list.write", Scope.ORG),
            list_id=list_id,
            contact_ids=[manual.id],
            add=True,
        )
    async with session.begin():
        audience = await selectors.sendable_contacts(
            session, ctx_for(org_id, K_SEND, Scope.ORG), list_id=list_id, cold=True
        )
    assert [c.crm_lead_id for c in audience] == [a.lead_id]


async def test_an_unsubscribe_after_expansion_still_wins(
    client,
    session,
    org_id,
    leads,
    worker_engine,
):
    a = leads.add(REP, lead("a@acme.test"))
    b = leads.add(REP, lead("late@acme.test", "Late Lead"))
    campaign_id, _ = await _create(client, session, org_id, leads, a, b)
    await _go(client, org_id, campaign_id)
    async with session.begin():
        recipient = (
            await session.execute(
                select(models.CampaignRecipient).where(
                    models.CampaignRecipient.email_masked.like("l%")
                )
            )
        ).scalar_one()
    async with session.begin():
        await services.unsubscribe_recipient(
            session, organization_id=org_id, recipient_id=recipient.id
        )
    wire = RecordingTransport()
    await _drain(session, org_id, uuid.UUID(campaign_id), transport=wire)
    assert [m.to_address for m in wire.sent] == ["a@acme.test"]


async def test_the_daily_cold_cap_is_enforced_and_the_rest_waits(
    client,
    session,
    org_id,
    leads,
    worker_engine,
    monkeypatch,
):
    monkeypatch.setattr(settings(), "engage_cold_daily_cap", 1)
    a = leads.add(REP, lead("a@acme.test"))
    b = leads.add(REP, lead("b@acme.test", "Ravi Kumar"))
    campaign_id, _ = await _create(client, session, org_id, leads, a, b)
    await _go(client, org_id, campaign_id)

    wire = RecordingTransport()
    assert await _drain(session, org_id, uuid.UUID(campaign_id), transport=wire) == "capped"
    assert len(wire.sent) == 1

    with as_principal(rep(org_id)):
        r = await client.post(PREVIEW, json=body([a, b]))
    assert (r.json()["daily_cap"], r.json()["sent_today"]) == (1, 1)


async def test_an_unknown_merge_field_blocks_the_send(client, session, org_id, leads):
    a = leads.add(REP, lead("a@acme.test"))
    campaign_id, _ = await _create(client, session, org_id, leads, a, subject="Hi {{compny_name}}")
    with as_principal(rep(org_id, send=True)):
        r = await client.post(f"{CAMPAIGNS}/{campaign_id}/send")
    assert r.status_code == 422 and "compny_name" in r.text


async def test_the_send_gate_still_needs_a_verified_sender(client, session, org_id, leads):
    a = leads.add(REP, lead("a@acme.test"))
    sender_id = await _seed_identity(session, org_id, verified=False)
    with as_principal(rep(org_id, send=True)):
        made = await client.post(CREATE, json=body([a], sender_id=str(sender_id)))
        sent = await client.post(f"{CAMPAIGNS}/{made.json()['campaign']['id']}/send")
    assert sent.status_code == 422 and "Verify the sender" in sent.text


# --------------------------------------------------------------------- authorization


@pytest.mark.parametrize("url", [PREVIEW, CREATE])
@pytest.mark.parametrize(
    "who",
    [
        pytest.param({"write": False}, id="no_campaign_write"),
        pytest.param({"lead_scope": None}, id="no_lead_read"),
        pytest.param({"lead_scope": "TEAM"}, id="team_scope_on_leads"),
        pytest.param({"entitlements": ("crm",)}, id="no_engage_entitlement"),
        pytest.param({"entitlements": ("engage",)}, id="no_crm_entitlement"),
        pytest.param({"entitlements": ("notification", "crm")}, id="notification_is_not_engage"),
    ],
)
async def test_every_gate_is_enforced_server_side(client, session, org_id, leads, url, who):
    sender_id = await _seed_identity(session, org_id)
    one = leads.add(REP, lead("a@acme.test"))
    with as_principal(rep(org_id, **who)):
        extra = {"sender_id": str(sender_id)} if url == CREATE else {}
        r = await client.post(url, json=body([one], **extra))
    assert r.status_code in (402, 403), r.text
    assert leads.calls == [] and await _contacts(session) == []


async def test_an_own_scoped_rep_only_targets_their_own_leads(client, session, org_id, leads):
    sender_id = await _seed_identity(session, org_id)
    mine = leads.add(REP, lead("mine@acme.test", "Mine"))
    theirs = leads.add(OTHER_REP, lead("theirs@acme.test", "Theirs"))

    with as_principal(rep(org_id, lead_scope="OWN")):
        r = await client.post(PREVIEW, json=body([mine, theirs]))
        everything = await client.post(PREVIEW, json=body())
        made = await client.post(CREATE, json=body([mine, theirs], sender_id=str(sender_id)))
    assert r.json()["selected"] == 1 and everything.json()["selected"] == 1
    assert {c["owner_id"] for c in leads.calls} == {REP}
    assert made.status_code == 201 and made.json()["selected"] == 1
    assert [c.crm_lead_id for c in await _contacts(session)] == [mine.lead_id]

    # An OWN reader who is only offered someone else's lead has nothing to email.
    with as_principal(rep(org_id, lead_scope="OWN")):
        none = await client.post(CREATE, json=body([theirs], sender_id=str(sender_id)))
    assert none.status_code == 422

    # An ORG reader reaches both.
    with as_principal(rep(org_id, lead_scope="ORG")):
        wide = await client.post(PREVIEW, json=body([mine, theirs]))
    assert wide.json()["selected"] == 2


async def test_an_own_rep_with_no_employee_link_is_refused(client, org_id, leads):
    p = principal_for(
        org_id, [f"{K_WRITE}:ORG", f"{LEAD_READ}:OWN"], entitlements=["engage", "crm"]
    )
    with as_principal(p):
        r = await client.post(PREVIEW, json=body())
    assert r.status_code == 403


async def test_reading_leads_is_rate_limited_per_person(client, org_id, leads, monkeypatch):
    monkeypatch.setattr(settings(), "engage_lead_audience_rate_limit_per_minute", 2)
    with as_principal(rep(org_id)):
        codes = [(await client.post(PREVIEW, json=body())).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


async def test_lead_being_down_is_a_503_and_a_huge_selection_a_422(client, org_id, leads):
    with as_principal(rep(org_id)):
        leads.error = LeadsUnavailable("boom")
        down = await client.post(PREVIEW, json=body())
        leads.error = TooManyLeads()
        big = await client.post(PREVIEW, json=body())
    assert down.status_code == 503 and "boom" not in down.text
    assert big.status_code == 422 and "Narrow the filter" in big.text


# --------------------------------------------------------------------- tenants


async def test_another_tenants_suppressions_contacts_and_history_do_not_leak(
    client, session, admin_session, org_id, leads
):
    other = uuid.uuid4()
    sender_id = await _seed_identity(session, org_id)
    a = leads.add(REP, lead("a@acme.test"))
    # The same address is suppressed, and already a contact, in another organization.
    sealed, masked, index = services._sealed_email("a@acme.test")
    async with admin_session.begin():
        admin_session.add(
            models.Suppression(
                organization_id=other,
                email_enc=sealed,
                email_masked=masked,
                email_index=index,
                reason="bounced",
            )
        )
        admin_session.add(
            models.Contact(
                organization_id=other,
                email_enc=sealed,
                email_masked=masked,
                email_index=index,
                origin="manual",
            )
        )
    with as_principal(rep(org_id)):
        r = await client.post(CREATE, json=body([a], sender_id=str(sender_id)))
    assert r.status_code == 201 and r.json()["excluded"] == 0 and r.json()["sendable"] == 1
    async with admin_session.begin():
        theirs = (
            await admin_session.execute(
                select(models.Contact).where(models.Contact.organization_id == other)
            )
        ).scalar_one()
    assert theirs.crm_lead_id is None and theirs.origin == "manual"
    assert all(c.organization_id == org_id for c in await _contacts(session))


# --------------------------------------------------------------------- the lead's timeline


async def test_the_timeline_shows_the_subject_and_never_the_body_or_the_address(
    client,
    session,
    org_id,
    leads,
    worker_engine,
):
    a = leads.add(REP, lead("a@acme.test", "Priya Sharma"))
    campaign_id, _ = await _create(
        client,
        session,
        org_id,
        leads,
        a,
        subject="Intro for {{company_name}}",
        text_body="SECRET-BODY-TEXT write to a@acme.test",
    )
    await _go(client, org_id, campaign_id)
    url = f"{CREATE}/leads/{a.lead_id}/emails"

    with as_principal(rep(org_id)):
        before = await client.get(url)
    assert before.json() == []

    await _drain(session, org_id, uuid.UUID(campaign_id), transport=RecordingTransport())
    with as_principal(rep(org_id)):
        r = await client.get(url)
    assert r.status_code == 200, r.text
    [line] = r.json()
    assert line["title"] == "Cold email sent: Intro for Acme Traders"
    assert set(line) == {"campaign_id", "title", "at"}
    assert "SECRET-BODY-TEXT" not in r.text and "@" not in r.text


async def test_the_timeline_respects_lead_scope_and_permissions(client, org_id, leads):
    theirs = leads.add(OTHER_REP, lead("t@acme.test"))
    url = f"{CREATE}/leads/{theirs.lead_id}/emails"
    with as_principal(rep(org_id, lead_scope="OWN")):
        assert (await client.get(url)).status_code == 404
    with as_principal(rep(org_id, lead_scope=None)):
        assert (await client.get(url)).status_code == 403
    with as_principal(principal_for(org_id, [f"{LEAD_READ}:ORG"], entitlements=["engage", "crm"])):
        assert (await client.get(url)).status_code == 403


async def test_the_timeline_is_per_tenant(client, session, admin_session, org_id, leads):
    other = uuid.uuid4()
    a = leads.add(REP, lead("a@acme.test"))
    async with admin_session.begin():
        contact = models.Contact(
            organization_id=other,
            email_masked="a***@acme.test",
            email_index="x" * 64,
            email_enc=b"x",
            crm_lead_id=a.lead_id,
            origin="crm_lead",
        )
        campaign = models.Campaign(
            organization_id=other,
            name="n",
            sender_id=uuid.uuid4(),
            subject="OTHER-TENANT",
            cold_outreach=True,
            status="sent",
        )
        admin_session.add_all([contact, campaign])
        await admin_session.flush()
        admin_session.add(
            models.CampaignRecipient(
                organization_id=other,
                campaign_id=campaign.id,
                contact_id=contact.id,
                email_enc=b"x",
                email_masked="m",
                email_index="x" * 64,
                status="sent",
                sent_at=datetime.now(UTC),
            )
        )
    with as_principal(rep(org_id)):
        r = await client.get(f"{CREATE}/leads/{a.lead_id}/emails")
    assert r.status_code == 200 and r.json() == []
