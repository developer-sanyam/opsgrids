"""A/B testing for email campaigns (ENGAGE-15, A/B part).

Two variants (subject and content), a test split (each variant gets `ab_split_percent` of the
audience), a winner picked by open or click rate once `ab_wait_minutes` have passed, and the
remainder sent with the winner. The send path is the ordinary campaign driver; the wait is the
scheduler's `not_before` delivery (ADR-0015) of `notification.campaign.ab_decide_due`; the rates
come from the same open/click events the E1 report reads.

Real PostgreSQL as the app role, like the rest of the campaign suite.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select

from app import consumers, e1_analytics, e1_models, models, services
from app.config import settings
from app.transports import RecordingTransport
from platform_core.auth import Scope
from platform_core.db import dispose_engine, init_engine
from platform_core.events import PLATFORM_SCHEDULE_REQUESTED, validate_event_schema
from tests.helpers import ctx_for, envelope_for
from tests.test_engage_campaigns import (
    CAMPAIGNS,
    K_READ,
    K_SEND,
    K_WRITE,
    _contacts,
    _drain,
    _envelope,
    _seed_identity,
    as_principal,
    caller,
)

pytestmark = pytest.mark.integration

CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"
EMAILS = [f"person{i:02d}@example.com" for i in range(20)]


@pytest_asyncio.fixture
async def worker_engine(session):
    """The engine the consumer's `session_scope` uses; after `session`, which truncates."""
    init_engine(settings())
    yield
    await dispose_engine()


def _fields(sender_id, **overrides):
    fields = {
        "name": "Diwali",
        "sender_id": sender_id,
        "subject": "Subject A",
        "html_body": "<p>Body A</p>",
        "text_body": "Body A",
        "audience": "all_sendable",
        "list_id": None,
        "scheduled_at": None,
        "track_opens": True,
        "track_clicks": False,
        "ab_test": True,
        "subject_b": "Subject B",
        "html_body_b": "<p>Body B</p>",
        "text_body_b": "Body B",
        "ab_split_percent": 10,
        "ab_metric": "open",
        "ab_wait_minutes": 120,
    }
    fields.update(overrides)
    return fields


async def _ab_campaign(session, org_id, **overrides) -> uuid.UUID:
    sender_id = await _seed_identity(session, org_id)
    await _contacts(session, org_id, *EMAILS)
    async with session.begin():
        row = await services.create_campaign(
            session, ctx_for(org_id, K_WRITE, Scope.ORG), fields=_fields(sender_id, **overrides)
        )
    return row.id


async def _start(session, org_id, campaign_id) -> RecordingTransport:
    async with session.begin():
        await services.request_send(
            session, ctx_for(org_id, K_SEND, Scope.ORG), campaign_id=campaign_id
        )
    assert await consumers.expand_campaign_send(_envelope(org_id, campaign_id)) == "expanded"
    wire = RecordingTransport()
    return wire


async def _recipients(session, campaign_id) -> list[models.CampaignRecipient]:
    async with session.begin():
        return list(
            (
                await session.execute(
                    select(models.CampaignRecipient).where(
                        models.CampaignRecipient.campaign_id == campaign_id
                    )
                )
            ).scalars()
        )


async def _open(session, org_id, campaign_id, recipient_id, *, machine=False) -> None:
    async with session.begin():
        session.add(
            e1_models.EmailEvent(
                organization_id=org_id,
                campaign_id=campaign_id,
                recipient_id=recipient_id,
                kind="open",
                user_agent_class="desktop",
                is_machine=machine,
            )
        )


def _decide_envelope(org_id, campaign_id, event_id=None):
    return envelope_for(
        org_id,
        services.EVT_CAMPAIGN_AB_DECIDE,
        {"organization_id": str(org_id), "campaign_id": str(campaign_id)},
        event_id=event_id,
    )


# ----------------------------------------------------------------------------- pure


@pytest.mark.parametrize(
    ("audience", "percent", "each"),
    [(20, 10, 2), (1000, 10, 100), (5, 10, 1), (1, 10, 1), (0, 10, 0), (7, 50, 3)],
)
def test_each_test_group_is_the_percentage_rounded_down_but_never_empty(
    audience: int, percent: int, each: int
) -> None:
    assert services.ab_group_size(audience, percent) == each


@pytest.mark.parametrize(
    ("a", "b", "winner"),
    [
        ((10, 2), (10, 5), "b"),
        ((10, 5), (10, 2), "a"),
        ((10, 3), (10, 3), "a"),
        ((0, 0), (0, 0), "a"),
        ((4, 2), (10, 4), "a"),
    ],
)
def test_the_winner_is_the_higher_rate_and_a_tie_keeps_a(a, b, winner) -> None:
    assert (
        services.ab_pick_winner(delivered_a=a[0], hits_a=a[1], delivered_b=b[0], hits_b=b[1])
        == winner
    )


# ----------------------------------------------------------------------------- the flow


async def test_only_the_test_groups_go_out_until_a_winner_is_picked(
    session, org_id, worker_engine
) -> None:
    campaign_id = await _ab_campaign(session, org_id)
    wire = await _start(session, org_id, campaign_id)
    # Nothing left that may be sent yet: the remainder waits for the winner.
    assert await _drain(session, org_id, campaign_id, transport=wire) == "idle"

    subjects = sorted(m.subject for m in wire.sent)
    assert subjects == ["Subject A", "Subject A", "Subject B", "Subject B"]
    rows = await _recipients(session, campaign_id)
    assert sorted((r.variant or "-", r.status) for r in rows).count(("-", "queued")) == 16
    assert {r.variant for r in rows if r.status == "sent"} == {"a", "b"}
    async with session.begin():
        campaign = await session.get(models.Campaign, campaign_id)
    assert campaign is not None and campaign.status == "sending" and campaign.ab_winner is None


async def test_the_decision_is_scheduled_for_after_the_wait(session, org_id, worker_engine) -> None:
    campaign_id = await _ab_campaign(session, org_id)
    before = datetime.now(UTC)
    await _start(session, org_id, campaign_id)
    async with session.begin():
        row = (
            await session.execute(
                select(models.OutboxEvent).where(
                    models.OutboxEvent.event_type == PLATFORM_SCHEDULE_REQUESTED
                )
            )
        ).scalar_one()
    payload = dict(row.payload)
    assert payload["target"]["event_type"] == "notification.campaign.ab_decide_due"
    due = datetime.fromisoformat(payload["due_at"])
    assert before + timedelta(minutes=120) <= due <= datetime.now(UTC) + timedelta(minutes=120)
    validate_event_schema(
        "notification.campaign.ab_decide_due",
        1,
        payload["target"]["payload"],
        contracts_root=CONTRACTS_ROOT,
    )


async def test_the_winner_by_open_rate_is_sent_to_everyone_else(
    session, org_id, worker_engine
) -> None:
    campaign_id = await _ab_campaign(session, org_id)
    wire = await _start(session, org_id, campaign_id)
    assert await _drain(session, org_id, campaign_id, transport=wire) == "idle"
    rows = await _recipients(session, campaign_id)
    b_rows = [r for r in rows if r.variant == "b"]
    a_rows = [r for r in rows if r.variant == "a"]
    # B: both opened (one twice); A: one machine open only, which does not count.
    for r in b_rows:
        await _open(session, org_id, campaign_id, r.id)
    await _open(session, org_id, campaign_id, b_rows[0].id)
    await _open(session, org_id, campaign_id, a_rows[0].id, machine=True)

    assert await consumers.decide_ab_test(_decide_envelope(org_id, campaign_id)) == "b"
    async with session.begin():
        campaign = await session.get(models.Campaign, campaign_id)
        audit = (
            (
                await session.execute(
                    select(models.AuditLog.action).where(
                        models.AuditLog.entity_id == campaign_id,
                        models.AuditLog.action == "engage.campaign.ab_winner_chosen",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert campaign is not None and campaign.ab_winner == "b" and campaign.ab_decided_at is not None
    assert len(audit) == 1

    rest = RecordingTransport()
    assert await _drain(session, org_id, campaign_id, transport=rest) == "done"
    assert len(rest.sent) == 16
    assert {m.subject for m in rest.sent} == {"Subject B"}
    assert all("Body B" in (m.html or "") and m.body.startswith("Body B") for m in rest.sent)


async def test_a_redelivered_decision_changes_nothing(session, org_id, worker_engine) -> None:
    campaign_id = await _ab_campaign(session, org_id)
    wire = await _start(session, org_id, campaign_id)
    await _drain(session, org_id, campaign_id, transport=wire)
    event_id = uuid.uuid4()
    assert await consumers.decide_ab_test(_decide_envelope(org_id, campaign_id, event_id)) == "a"
    rows = await _recipients(session, campaign_id)
    for r in rows:
        if r.variant == "b":
            await _open(session, org_id, campaign_id, r.id)
    assert await consumers.decide_ab_test(_decide_envelope(org_id, campaign_id, event_id)) == (
        "redelivered"
    )
    # A second, distinct firing (a duplicate schedule) does not re-decide either.
    assert await consumers.decide_ab_test(_decide_envelope(org_id, campaign_id)) == "decided"
    async with session.begin():
        campaign = await session.get(models.Campaign, campaign_id)
    assert campaign is not None and campaign.ab_winner == "a"


async def test_a_cancelled_campaign_is_never_decided(session, org_id, worker_engine) -> None:
    campaign_id = await _ab_campaign(session, org_id)
    await _start(session, org_id, campaign_id)
    async with session.begin():
        await services.cancel_campaign(
            session, ctx_for(org_id, K_SEND, Scope.ORG), campaign_id=campaign_id
        )
    assert await consumers.decide_ab_test(_decide_envelope(org_id, campaign_id)) == "not_sending"


async def test_another_organizations_decision_cannot_touch_this_campaign(
    session, org_id, worker_engine
) -> None:
    campaign_id = await _ab_campaign(session, org_id)
    await _start(session, org_id, campaign_id)
    stranger = uuid.uuid4()
    assert await consumers.decide_ab_test(_decide_envelope(stranger, campaign_id)) == "not_found"
    async with session.begin():
        campaign = await session.get(models.Campaign, campaign_id)
    assert campaign is not None and campaign.ab_winner is None


async def test_a_mismatched_payload_organization_is_refused(session, org_id, worker_engine) -> None:
    campaign_id = await _ab_campaign(session, org_id)
    await _start(session, org_id, campaign_id)
    envelope = _decide_envelope(org_id, campaign_id)
    envelope["payload"]["organization_id"] = str(uuid.uuid4())
    assert await consumers.decide_ab_test(envelope) == "mismatch"


# ----------------------------------------------------------------------------- the send gate


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"subject_b": ""}, "subject for version B"),
        ({"html_body_b": "", "text_body_b": ""}, "body for version B"),
        ({"track_opens": False}, "open tracking"),
        ({"ab_metric": "click", "track_clicks": False}, "click tracking"),
    ],
)
async def test_an_incomplete_ab_test_cannot_be_sent(session, org_id, overrides, fragment) -> None:
    from platform_core.errors import BusinessRuleProblem

    campaign_id = await _ab_campaign(session, org_id, **overrides)
    with pytest.raises(BusinessRuleProblem) as excinfo:
        async with session.begin():
            await services.request_send(
                session, ctx_for(org_id, K_SEND, Scope.ORG), campaign_id=campaign_id
            )
    assert fragment in excinfo.value.detail


# ----------------------------------------------------------------------------- the API


async def test_the_ab_fields_round_trip_through_the_api(client, session, org_id) -> None:
    sender_id = await _seed_identity(session, org_id)
    body = _fields(str(sender_id))
    with as_principal(caller(org_id, [f"{K_WRITE}:ORG", f"{K_READ}:ORG"])):
        created = await client.post(CAMPAIGNS, json=body)
        assert created.status_code == 201, created.text
        got = (await client.get(f"{CAMPAIGNS}/{created.json()['id']}")).json()
        patched = await client.patch(
            f"{CAMPAIGNS}/{got['id']}", json={"ab_split_percent": 20, "ab_metric": "click"}
        )
    assert patched.status_code == 200, patched.text
    assert got["ab_test"] is True and got["subject_b"] == "Subject B"
    assert got["ab_winner"] is None and got["ab_decided_at"] is None
    assert (patched.json()["ab_split_percent"], patched.json()["ab_metric"]) == (20, "click")


@pytest.mark.parametrize(
    "bad",
    [
        {"ab_split_percent": 0},
        {"ab_split_percent": 51},
        {"ab_wait_minutes": 5},
        {"ab_wait_minutes": 20000},
        {"ab_metric": "reply"},
        {"subject_b": "two\r\nlines"},
    ],
)
async def test_bad_ab_settings_are_a_422(client, session, org_id, bad) -> None:
    sender_id = await _seed_identity(session, org_id)
    with as_principal(caller(org_id, [f"{K_WRITE}:ORG"])):
        response = await client.post(CAMPAIGNS, json={**_fields(str(sender_id)), **bad})
    assert response.status_code == 422, response.text


async def test_a_reader_cannot_turn_on_an_ab_test(client, session, org_id) -> None:
    campaign_id = await _ab_campaign(session, org_id, ab_test=False)
    with as_principal(caller(org_id, [f"{K_READ}:ORG"])):
        response = await client.patch(f"{CAMPAIGNS}/{campaign_id}", json={"ab_test": True})
    assert response.status_code == 403


async def test_the_report_shows_each_variants_rates_and_the_winner(
    client, session, org_id, worker_engine
) -> None:
    campaign_id = await _ab_campaign(session, org_id)
    wire = await _start(session, org_id, campaign_id)
    await _drain(session, org_id, campaign_id, transport=wire)
    rows = await _recipients(session, campaign_id)
    await _open(session, org_id, campaign_id, next(r.id for r in rows if r.variant == "b"))
    await consumers.decide_ab_test(_decide_envelope(org_id, campaign_id))
    with as_principal(caller(org_id, [f"{K_READ}:ORG"])):
        report = (await client.get(f"{CAMPAIGNS}/{campaign_id}/analytics")).json()
    ab = report["ab_test"]
    assert (ab["metric"], ab["winner"], ab["split_percent"]) == ("open", "b", 10)
    variants = {v["variant"]: v for v in ab["variants"]}
    assert variants["a"]["subject"] == "Subject A" and variants["b"]["subject"] == "Subject B"
    assert (variants["a"]["delivered"], variants["a"]["opened"]) == (2, 0)
    assert (variants["b"]["delivered"], variants["b"]["opened"], variants["b"]["open_rate"]) == (
        2,
        1,
        0.5,
    )


async def test_a_plain_campaigns_report_has_no_ab_section(session, org_id) -> None:
    campaign_id = await _ab_campaign(session, org_id, ab_test=False)
    async with session.begin():
        report = await e1_analytics.campaign_analytics(
            session, ctx_for(org_id, K_READ, Scope.ORG), campaign_id
        )
    assert report is not None and report["ab_test"] is None
