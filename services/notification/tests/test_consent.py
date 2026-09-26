"""The consent ledger and its one decision, against real PostgreSQL as the app role.

What is proven here, in the order the policy states it: the decision matrix (transactional vs
marketing, `opted_in` / `opted_out` / nothing, the legacy `Preference` and `Suppression` stores
that must still count), that the ledger really is append-only (a privilege the database refuses,
not a habit), STOP then START, the unsubscribe link, campaign selection and the per-batch claim,
a rule-driven marketing send with no opt-in, cross-tenant isolation, and that no address ever sits
in the table in the clear.

Run as the **app** role, so row-level security and the revoked `UPDATE`/`DELETE` are live — a suite
run as a bypassing role would make every one of those assertions a false pass.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app import consent, domain, models, selectors, services, webhooks
from app.config import settings
from app.models import ConsentEvent, Message, Preference, Suppression
from app.transports import RecordingTransport
from platform_core import pii
from platform_core.auth import Scope
from platform_core.errors import BusinessRuleProblem
from tests.helpers import ctx_for, envelope_for

pytestmark = pytest.mark.integration

EMAIL = "reader@example.com"
PHONE = "+16505551234"
TICKET = "support.desk.ticket.created"

C_WRITE = "engage.audience.contact.write"
S_WRITE = "engage.audience.suppression.write"
N_WRITE = "notification.delivery.template.write"
P_WRITE = "notification.delivery.preference.write"


async def _record(
    session,
    org_id: uuid.UUID,
    channel: str,
    purpose: str,
    status: str,
    address: str,
    *,
    source: str = "admin",
    when: datetime | None = None,
) -> ConsentEvent:
    subject = consent.subject_for(channel, address)
    async with session.begin():
        rows = await consent.append(
            session,
            organization_id=org_id,
            subject=subject,
            purposes=(purpose,),
            status=status,
            source=source,
            evidence="test fixture",
            occurred_at=when,
        )
    return rows[0]


async def _decide(session, org_id, channel, purpose, address, **kw) -> consent.ConsentDecision:
    async with session.begin():
        return await consent.decide(session, org_id, channel, purpose, address, **kw)


async def _suppress(session, org_id: uuid.UUID, address: str, reason: str) -> None:
    sealed, masked = pii.seal(address, kind="email", settings=settings())
    async with session.begin():
        session.add(
            Suppression(
                organization_id=org_id,
                email_enc=sealed,
                email_masked=masked,
                email_index=selectors.email_index(address),
                reason=reason,
            )
        )


async def _events(session, org_id: uuid.UUID) -> list[ConsentEvent]:
    async with session.begin():
        return list(
            (
                await session.execute(
                    select(ConsentEvent)
                    .where(ConsentEvent.organization_id == org_id)
                    .order_by(ConsentEvent.created_at, ConsentEvent.id)
                )
            ).scalars()
        )


# --------------------------------------------------------------------- the decision matrix

CHANNEL_ADDRESSES = [("email", EMAIL), ("whatsapp", PHONE)]


@pytest.mark.parametrize(("channel", "address"), CHANNEL_ADDRESSES)
async def test_transactional_is_allowed_with_no_events(session, org_id, channel, address) -> None:
    decision = await _decide(session, org_id, channel, "transactional", address)
    assert decision == consent.ConsentDecision(True, "no_opt_out")


@pytest.mark.parametrize(("channel", "address"), CHANNEL_ADDRESSES)
async def test_transactional_is_allowed_when_opted_in(session, org_id, channel, address) -> None:
    await _record(session, org_id, channel, "transactional", "opted_in", address)
    decision = await _decide(session, org_id, channel, "transactional", address)
    assert decision == consent.ConsentDecision(True, "opted_in")


@pytest.mark.parametrize(("channel", "address"), CHANNEL_ADDRESSES)
async def test_transactional_is_refused_when_opted_out(session, org_id, channel, address) -> None:
    await _record(session, org_id, channel, "transactional", "opted_out", address)
    decision = await _decide(session, org_id, channel, "transactional", address)
    assert decision == consent.ConsentDecision(False, "opted_out")


@pytest.mark.parametrize(("channel", "address"), CHANNEL_ADDRESSES)
async def test_marketing_is_refused_with_no_events(session, org_id, channel, address) -> None:
    """Silence is not consent."""
    decision = await _decide(session, org_id, channel, "marketing", address)
    assert decision == consent.ConsentDecision(False, "no_opt_in")


@pytest.mark.parametrize(("channel", "address"), CHANNEL_ADDRESSES)
async def test_marketing_is_allowed_only_when_opted_in(session, org_id, channel, address) -> None:
    await _record(session, org_id, channel, "marketing", "opted_in", address)
    decision = await _decide(session, org_id, channel, "marketing", address)
    assert decision == consent.ConsentDecision(True, "opted_in")


@pytest.mark.parametrize(("channel", "address"), CHANNEL_ADDRESSES)
async def test_marketing_is_refused_when_opted_out(session, org_id, channel, address) -> None:
    await _record(session, org_id, channel, "marketing", "opted_out", address)
    decision = await _decide(session, org_id, channel, "marketing", address)
    assert decision == consent.ConsentDecision(False, "opted_out")


async def test_the_latest_event_wins_in_both_directions(session, org_id) -> None:
    base = datetime.now(UTC) - timedelta(days=3)
    await _record(session, org_id, "email", "marketing", "opted_in", EMAIL, when=base)
    await _record(
        session, org_id, "email", "marketing", "opted_out", EMAIL, when=base + timedelta(days=1)
    )
    assert (await _decide(session, org_id, "email", "marketing", EMAIL)).allowed is False

    await _record(
        session, org_id, "email", "marketing", "opted_in", EMAIL, when=base + timedelta(days=2)
    )
    assert (await _decide(session, org_id, "email", "marketing", EMAIL)).allowed is True


async def test_a_back_dated_event_does_not_override_a_later_one(session, org_id) -> None:
    """`occurred_at` is when the person acted, and "latest" is ordered by it — so recording an
    old opt-in today cannot resurrect consent that a newer refusal withdrew."""
    now = datetime.now(UTC)
    await _record(session, org_id, "email", "marketing", "opted_out", EMAIL, when=now)
    await _record(
        session,
        org_id,
        "email",
        "marketing",
        "opted_in",
        EMAIL,
        when=now - timedelta(days=30),
    )
    assert (await _decide(session, org_id, "email", "marketing", EMAIL)).allowed is False


async def test_purposes_are_independent(session, org_id) -> None:
    """Opting out of marketing leaves an invoice deliverable; a transactional opt-out does not
    make a marketing opt-in disappear from the marketing history."""
    await _record(session, org_id, "email", "marketing", "opted_out", EMAIL)
    assert (await _decide(session, org_id, "email", "transactional", EMAIL)).allowed is True

    await _record(session, org_id, "email", "marketing", "opted_in", EMAIL)
    await _record(session, org_id, "email", "transactional", "opted_out", EMAIL)
    assert (await _decide(session, org_id, "email", "marketing", EMAIL)).allowed is True
    assert (await _decide(session, org_id, "email", "transactional", EMAIL)).allowed is False


async def test_channels_are_independent(session, org_id) -> None:
    await _record(session, org_id, "email", "marketing", "opted_in", EMAIL)
    assert (await _decide(session, org_id, "whatsapp", "marketing", PHONE)).allowed is False


async def test_the_address_is_normalised_before_it_is_looked_up(session, org_id) -> None:
    await _record(session, org_id, "email", "marketing", "opted_in", "  Reader@Example.COM ")
    assert (await _decide(session, org_id, "email", "marketing", EMAIL)).allowed is True
    await _record(session, org_id, "whatsapp", "marketing", "opted_in", "+1 (650) 555-1234")
    assert (await _decide(session, org_id, "whatsapp", "marketing", PHONE)).allowed is True


async def test_sms_is_a_ledger_channel_with_an_e164_subject(session, org_id) -> None:
    await _record(session, org_id, "sms", "marketing", "opted_in", PHONE)
    assert (await _decide(session, org_id, "sms", "marketing", PHONE)).allowed is True
    # The same number on WhatsApp is a different channel and a different answer.
    assert (await _decide(session, org_id, "whatsapp", "marketing", PHONE)).allowed is False


async def test_an_address_that_is_not_one_is_refused_rather_than_raised(session, org_id) -> None:
    decision = await _decide(session, org_id, "whatsapp", "transactional", "6505551234")
    assert decision == consent.ConsentDecision(False, "invalid_address")


async def test_an_unknown_channel_or_purpose_is_a_programming_error(session, org_id) -> None:
    with pytest.raises(ValueError, match="channel"):
        await _decide(session, org_id, "pigeon", "marketing", EMAIL)
    with pytest.raises(ValueError, match="purpose"):
        await _decide(session, org_id, "email", "spam", EMAIL)


async def test_the_bulk_decision_agrees_with_the_single_one(session, org_id) -> None:
    other = "other@example.com"
    await _record(session, org_id, "email", "marketing", "opted_in", EMAIL)
    async with session.begin():
        bulk = await consent.decide_indexes(
            session,
            org_id,
            "email",
            "marketing",
            [consent.index_for("email", EMAIL), consent.index_for("email", other)],
        )
    assert bulk[consent.index_for("email", EMAIL)].allowed is True
    assert bulk[consent.index_for("email", other)].reason == "no_opt_in"
    assert bulk[consent.index_for("email", EMAIL)] == await _decide(
        session, org_id, "email", "marketing", EMAIL
    )


# --------------------------------------------------------------------- legacy stores still count


@pytest.mark.parametrize("reason", ["bounced", "complained"])
async def test_a_bounce_or_complaint_refuses_transactional_email_too(
    session, org_id, reason
) -> None:
    """Facts about the mailbox, not choices: nothing is deliverable to it."""
    await _suppress(session, org_id, EMAIL, reason)
    assert await _decide(session, org_id, "email", "transactional", EMAIL) == (
        consent.ConsentDecision(False, f"suppressed_{reason}")
    )
    await _record(session, org_id, "email", "marketing", "opted_in", EMAIL)
    assert (await _decide(session, org_id, "email", "marketing", EMAIL)).reason == (
        f"suppressed_{reason}"
    )


@pytest.mark.parametrize("reason", ["unsubscribed", "manual"])
async def test_a_choice_suppression_refuses_marketing_but_not_an_invoice(
    session, org_id, reason
) -> None:
    """What the store did before the ledger existed: it stopped campaigns, and rule-driven
    transactional mail to the same address carried on."""
    await _suppress(session, org_id, EMAIL, reason)
    assert (await _decide(session, org_id, "email", "transactional", EMAIL)).allowed is True
    assert await _decide(session, org_id, "email", "marketing", EMAIL) == (
        consent.ConsentDecision(False, f"suppressed_{reason}")
    )


async def test_a_suppression_beats_a_later_opt_in(session, org_id) -> None:
    """Deny if *either* store says no: a ledger opt-in cannot unblock what a legacy row blocks."""
    await _suppress(session, org_id, EMAIL, "unsubscribed")
    await _record(session, org_id, "email", "marketing", "opted_in", EMAIL)
    assert (await _decide(session, org_id, "email", "marketing", EMAIL)).allowed is False


async def test_the_preference_check_alone_refuses_without_a_ledger_row(
    session, admin_session, org_id
) -> None:
    """The legacy `Preference` is consulted directly, per category, whatever the ledger says."""
    now = datetime.now(UTC)
    async with admin_session.begin():
        admin_session.add(
            Preference(
                organization_id=org_id,
                recipient=PHONE,
                channel="whatsapp",
                category="billing",
                suppressed=True,
                created_at=now,
                updated_at=now,
            )
        )
    assert await _decide(
        session, org_id, "whatsapp", "transactional", PHONE, category="billing"
    ) == (consent.ConsentDecision(False, "preference_suppressed"))
    # A different category is not refused by it, and neither is no category at all.
    assert (
        await _decide(session, org_id, "whatsapp", "transactional", PHONE, category="shipping")
    ).allowed is True
    assert (await _decide(session, org_id, "whatsapp", "transactional", PHONE)).allowed is True


async def test_a_star_preference_and_a_marketing_preference_refuse_marketing(
    session, admin_session, org_id
) -> None:
    now = datetime.now(UTC)
    async with admin_session.begin():
        for recipient, category in (("+16505550001", "*"), ("+16505550002", "marketing")):
            admin_session.add(
                Preference(
                    organization_id=org_id,
                    recipient=recipient,
                    channel="whatsapp",
                    category=category,
                    suppressed=True,
                    created_at=now,
                    updated_at=now,
                )
            )
    for recipient in ("+16505550001", "+16505550002"):
        await _record(session, org_id, "whatsapp", "marketing", "opted_in", recipient)
        assert (await _decide(session, org_id, "whatsapp", "marketing", recipient)).reason == (
            "preference_suppressed"
        )
    # A `*` preference also refuses transactional; a `marketing` one leaves it alone.
    assert (
        await _decide(session, org_id, "whatsapp", "transactional", "+16505550001")
    ).allowed is (False)
    assert (
        await _decide(session, org_id, "whatsapp", "transactional", "+16505550002")
    ).allowed is (True)


# --------------------------------------------------------------------- which purpose a send is


async def _seed_marketing_template(session, org_id, *, category: str) -> None:
    write = ctx_for(org_id, N_WRITE, Scope.ORG)
    async with session.begin():
        await services.upsert_provider_template(
            session,
            write,
            code="promo_wa",
            channel="whatsapp",
            provider_name="promo",
            language="en_US",
            category=category,
            parameter_format="positional",
            status="approved",
            status_reason=None,
            variables=[{"key": "requester_name", "component": "body", "position": 0}],
            preview_text="Hello {{1}}",
        )


async def test_a_marketing_provider_template_is_marketing_whatever_the_rule_calls_it(
    session, org_id
) -> None:
    """Meta's own classification carries the opt-in obligation; a rule's free-text bucket can
    neither add to it nor waive it."""
    await _seed_marketing_template(session, org_id, category="marketing")
    async with session.begin():
        purpose = await consent.purpose_for_send(
            session,
            org_id,
            channel="whatsapp",
            category="operational",
            provider_template_code="promo_wa",
        )
    assert purpose == "marketing"

    async with session.begin():
        assert (
            await consent.purpose_for_send(
                session,
                org_id,
                channel="whatsapp",
                category="marketing",
                provider_template_code=None,
            )
            == "marketing"
        )
        assert (
            await consent.purpose_for_send(
                session,
                org_id,
                channel="whatsapp",
                category="billing",
                provider_template_code=None,
            )
            == "transactional"
        )


async def test_in_app_has_no_consent_regime(session, org_id) -> None:
    async with session.begin():
        assert (
            await consent.check_send(
                session,
                org_id,
                channel="in_app",
                category="marketing",
                address=str(uuid.uuid4()),
            )
            is None
        )


# --------------------------------------------------------------------- rule-driven sends


async def _seed_email_rule(session, org_id, *, category: str) -> None:
    write = ctx_for(org_id, N_WRITE, Scope.ORG)
    async with session.begin():
        await services.upsert_template(
            session,
            write,
            code="promo",
            channel="email",
            subject="Autumn offers for {{requester_name}}",
            body="Hello {{requester_name}}",
        )
        await services.upsert_rule(
            session,
            write,
            code="promo_on_ticket",
            event_type=TICKET,
            template_code="promo",
            channel="email",
            category=category,
            recipient_path="requester_email",
        )


def _ticket_event(org_id: uuid.UUID, *, email: str = EMAIL) -> dict:
    return envelope_for(
        org_id,
        TICKET,
        {"number": "TK-1", "requester_name": "Helena", "requester_email": email},
    )


async def test_a_rule_driven_marketing_send_is_denied_without_an_opt_in(session, org_id) -> None:
    await _seed_email_rule(session, org_id, category="marketing")
    async with session.begin():
        staged = await services.apply_rules(
            session, organization_id=org_id, envelope=_ticket_event(org_id)
        )
    assert len(staged) == 1
    message = staged[0]
    assert message.state == domain.STATE_SUPPRESSED
    assert message.suppression_reason == "consent:no_opt_in"

    # Nothing was queued for the dispatcher: there is nothing for it to do.
    queued = (
        (
            await session.execute(
                select(models.OutboxEvent).where(
                    models.OutboxEvent.event_type == services.EVT_MESSAGE_QUEUED
                )
            )
        )
        .scalars()
        .all()
    )
    assert queued == []


async def test_a_rule_driven_marketing_send_goes_once_an_opt_in_is_recorded(
    session, org_id
) -> None:
    await _seed_email_rule(session, org_id, category="marketing")
    await _record(session, org_id, "email", "marketing", "opted_in", EMAIL, source="web_form")
    async with session.begin():
        staged = await services.apply_rules(
            session, organization_id=org_id, envelope=_ticket_event(org_id)
        )
    assert staged[0].state == domain.STATE_PENDING
    assert staged[0].suppression_reason is None


async def test_a_rule_driven_transactional_send_needs_no_opt_in(session, org_id) -> None:
    await _seed_email_rule(session, org_id, category="operational")
    async with session.begin():
        staged = await services.apply_rules(
            session, organization_id=org_id, envelope=_ticket_event(org_id)
        )
    assert staged[0].state == domain.STATE_PENDING


async def test_a_transactional_send_to_an_opted_out_address_is_denied_with_a_reason(
    session, org_id
) -> None:
    await _seed_email_rule(session, org_id, category="operational")
    await _record(session, org_id, "email", "transactional", "opted_out", EMAIL)
    async with session.begin():
        staged = await services.apply_rules(
            session, organization_id=org_id, envelope=_ticket_event(org_id)
        )
    assert staged[0].state == domain.STATE_SUPPRESSED
    assert staged[0].suppression_reason == "consent:opted_out"


async def test_a_legacy_preference_refusal_is_recorded_with_a_reason_too(
    session, admin_session, org_id
) -> None:
    await _seed_email_rule(session, org_id, category="billing")
    now = datetime.now(UTC)
    async with admin_session.begin():
        admin_session.add(
            Preference(
                organization_id=org_id,
                recipient=EMAIL,
                channel="email",
                category="billing",
                suppressed=True,
                created_at=now,
                updated_at=now,
            )
        )
    async with session.begin():
        staged = await services.apply_rules(
            session, organization_id=org_id, envelope=_ticket_event(org_id)
        )
    assert staged[0].state == domain.STATE_SUPPRESSED
    assert staged[0].suppression_reason == "consent:preference_suppressed"


async def test_a_marketing_whatsapp_template_is_denied_without_an_explicit_opt_in(
    session, org_id
) -> None:
    """There are no legacy WhatsApp marketing sends to carry over, so nothing grandfathers one:
    a marketing template needs an `opted_in` event, and a utility one does not."""
    await _seed_marketing_template(session, org_id, category="marketing")
    write = ctx_for(org_id, N_WRITE, Scope.ORG)
    async with session.begin():
        await services.upsert_rule(
            session,
            write,
            code="promo_wa_rule",
            event_type=TICKET,
            channel="whatsapp",
            provider_template_code="promo_wa",
            recipient_path="requester_phone",
        )
    event = envelope_for(
        org_id, TICKET, {"number": "TK-1", "requester_name": "Helena", "requester_phone": PHONE}
    )
    async with session.begin():
        denied = await services.apply_rules(session, organization_id=org_id, envelope=event)
    assert denied[0].state == domain.STATE_SUPPRESSED
    assert denied[0].suppression_reason == "consent:no_opt_in"

    await _record(session, org_id, "whatsapp", "marketing", "opted_in", PHONE, source="checkout")
    event2 = envelope_for(
        org_id, TICKET, {"number": "TK-2", "requester_name": "Helena", "requester_phone": PHONE}
    )
    async with session.begin():
        allowed = await services.apply_rules(session, organization_id=org_id, envelope=event2)
    assert allowed[0].state == domain.STATE_PENDING


# --------------------------------------------------------------------- dispatch re-check


async def test_an_opt_out_between_staging_and_dispatch_stops_the_send(session, org_id) -> None:
    """The later check is the true one: the message was pending when it was staged, and is
    refused — with a reason on it — when the dispatcher claims it."""
    await _seed_email_rule(session, org_id, category="operational")
    async with session.begin():
        staged = await services.apply_rules(
            session, organization_id=org_id, envelope=_ticket_event(org_id)
        )
    message_id = staged[0].id
    assert staged[0].state == domain.STATE_PENDING

    await _record(session, org_id, "email", "transactional", "opted_out", EMAIL)
    async with session.begin():
        claimed = await services.claim_for_send(
            session, organization_id=org_id, message_id=message_id
        )
    assert claimed is None
    stored = (await session.execute(select(Message).where(Message.id == message_id))).scalar_one()
    assert stored.state == domain.STATE_SUPPRESSED
    assert stored.suppression_reason == "consent:opted_out"


async def test_a_marketing_message_is_refused_at_dispatch_if_the_opt_in_is_withdrawn(
    session, org_id
) -> None:
    await _seed_email_rule(session, org_id, category="marketing")
    await _record(session, org_id, "email", "marketing", "opted_in", EMAIL)
    async with session.begin():
        staged = await services.apply_rules(
            session, organization_id=org_id, envelope=_ticket_event(org_id)
        )
    assert staged[0].state == domain.STATE_PENDING
    message_id = staged[0].id

    await _record(session, org_id, "email", "marketing", "opted_out", EMAIL)
    async with session.begin():
        assert (
            await services.claim_for_send(session, organization_id=org_id, message_id=message_id)
            is None
        )
    stored = (await session.execute(select(Message).where(Message.id == message_id))).scalar_one()
    assert stored.suppression_reason == "consent:opted_out"


async def test_a_permitted_message_is_claimed_as_before(session, org_id) -> None:
    await _seed_email_rule(session, org_id, category="operational")
    async with session.begin():
        staged = await services.apply_rules(
            session, organization_id=org_id, envelope=_ticket_event(org_id)
        )
    async with session.begin():
        claimed = await services.claim_for_send(
            session, organization_id=org_id, message_id=staged[0].id
        )
    assert claimed is not None
    assert claimed.state == domain.STATE_SENDING


# --------------------------------------------------------------------- STOP then START


def _inbound(body: str, wamid: str, *, sender: str = "16505551234") -> webhooks.InboundMessage:
    return webhooks.InboundMessage(
        provider_message_id=wamid,
        from_address=sender,
        message_type="text",
        occurred_at=datetime.now(UTC),
        body=body,
        raw={"id": wamid, "from": sender, "type": "text", "text": {"body": body}},
    )


async def _fold(session, org_id, body: str, wamid: str) -> None:
    async with session.begin():
        await services.fold_inbound_message(
            session, organization_id=org_id, inbound=_inbound(body, wamid)
        )


async def test_stop_opts_the_number_out_of_both_purposes(session, org_id) -> None:
    await _record(session, org_id, "whatsapp", "marketing", "opted_in", PHONE, source="checkout")
    await _fold(session, org_id, "STOP", "wamid.STOP1")

    assert (await _decide(session, org_id, "whatsapp", "marketing", PHONE)).reason == "opted_out"
    assert (await _decide(session, org_id, "whatsapp", "transactional", PHONE)).reason == (
        "opted_out"
    )
    stops = [e for e in await _events(session, org_id) if e.source == "inbound_keyword"]
    assert {(e.purpose, e.status) for e in stops} == {
        ("marketing", "opted_out"),
        ("transactional", "opted_out"),
    }
    # A pointer to the message that carried it, not the words and not the number.
    assert all("wamid.STOP1" in (e.evidence or "") for e in stops)
    assert all(PHONE not in (e.evidence or "") for e in stops)


async def test_a_repeated_stop_adds_nothing_to_a_history_that_already_ends_in_one(
    session, org_id
) -> None:
    await _fold(session, org_id, "STOP", "wamid.STOP1")
    await _fold(session, org_id, "Stop.", "wamid.STOP2")
    assert len([e for e in await _events(session, org_id) if e.source == "inbound_keyword"]) == 2


async def test_start_restores_marketing_only_where_it_existed_before_the_stop(
    session, org_id
) -> None:
    await _record(session, org_id, "whatsapp", "marketing", "opted_in", PHONE, source="checkout")
    await _fold(session, org_id, "STOP", "wamid.STOP1")
    await _fold(session, org_id, "START", "wamid.START1")

    assert (await _decide(session, org_id, "whatsapp", "marketing", PHONE)).allowed is True
    assert (await _decide(session, org_id, "whatsapp", "transactional", PHONE)).allowed is True
    starts = [
        e
        for e in await _events(session, org_id)
        if e.source == "inbound_keyword" and e.status == "opted_in"
    ]
    assert {e.purpose for e in starts} == {"marketing", "transactional"}


async def test_a_bare_start_never_creates_marketing_consent(session, org_id) -> None:
    """A customer who was only ever messaged transactionally, typed STOP, then START, has undone
    their refusal. They have not opted in to marketing, and the keyword must not be allowed to
    stand in for the affirmative opt-in Meta requires."""
    await _fold(session, org_id, "STOP", "wamid.STOP1")
    await _fold(session, org_id, "START", "wamid.START1")

    assert (await _decide(session, org_id, "whatsapp", "transactional", PHONE)).allowed is True
    assert await _decide(session, org_id, "whatsapp", "marketing", PHONE) == (
        consent.ConsentDecision(False, "opted_out")
    )


async def test_start_with_no_prior_stop_writes_nothing(session, org_id) -> None:
    await _fold(session, org_id, "START", "wamid.START1")
    assert await _events(session, org_id) == []


async def test_start_does_not_undo_an_operator_suppression(session, org_id) -> None:
    write = ctx_for(org_id, P_WRITE, Scope.ORG)
    async with session.begin():
        await services.set_preference(
            session,
            write,
            recipient=PHONE,
            channel="whatsapp",
            category="*",
            reason="legal_hold",
        )
    await _fold(session, org_id, "START", "wamid.START1")
    assert (await _decide(session, org_id, "whatsapp", "transactional", PHONE)).allowed is False
    assert [e.source for e in await _events(session, org_id)] == ["admin", "admin"]


# --------------------------------------------------------------------- operator preference writes


async def test_setting_and_lifting_a_preference_is_mirrored_into_the_ledger(
    session, org_id
) -> None:
    write = ctx_for(org_id, P_WRITE, Scope.ORG)
    async with session.begin():
        await services.set_preference(
            session, write, recipient=PHONE, channel="whatsapp", category="marketing"
        )
    events = await _events(session, org_id)
    assert [(e.purpose, e.status, e.source) for e in events] == [
        ("marketing", "opted_out", "admin")
    ]
    assert events[0].recorded_by is not None

    async with session.begin():
        await services.set_preference(
            session,
            write,
            recipient=PHONE,
            channel="whatsapp",
            category="marketing",
            suppressed=False,
        )
    # Lifting restores consent that existed. There was no earlier WhatsApp marketing opt-in, so
    # the lift cannot invent one: the preference is gone, but marketing still needs an opt-in.
    assert await _decide(session, org_id, "whatsapp", "marketing", PHONE) == (
        consent.ConsentDecision(False, "opted_out")
    )
    assert len(await _events(session, org_id)) == 1


async def test_lifting_a_preference_restores_a_prior_opt_in(session, org_id) -> None:
    write = ctx_for(org_id, P_WRITE, Scope.ORG)
    await _record(session, org_id, "whatsapp", "marketing", "opted_in", PHONE, source="checkout")
    async with session.begin():
        await services.set_preference(
            session, write, recipient=PHONE, channel="whatsapp", category="marketing"
        )
    assert (await _decide(session, org_id, "whatsapp", "marketing", PHONE)).allowed is False
    async with session.begin():
        await services.set_preference(
            session,
            write,
            recipient=PHONE,
            channel="whatsapp",
            category="marketing",
            suppressed=False,
        )
    assert (await _decide(session, org_id, "whatsapp", "marketing", PHONE)).allowed is True


# --------------------------------------------------------------------- contacts and suppressions


async def _add_contact(session, org_id, email: str = EMAIL):
    write = ctx_for(org_id, C_WRITE, Scope.ORG)
    async with session.begin():
        return await services.create_contact(session, write, email=email)


async def test_adding_a_contact_records_the_organizations_attestation(session, org_id) -> None:
    contact = await _add_contact(session, org_id)
    events = await _events(session, org_id)
    assert len(events) == 1
    event = events[0]
    assert (event.channel, event.purpose, event.status) == ("email", "marketing", "opted_in")
    assert event.source == "admin"
    assert event.contact_id == contact.id
    assert event.recorded_by is not None
    assert "attests" in (event.evidence or "") and "unverified" in (event.evidence or "")
    assert (await _decide(session, org_id, "email", "marketing", EMAIL)).allowed is True


async def test_importing_contacts_attests_only_the_new_ones_in_one_batch(session, org_id) -> None:
    write = ctx_for(org_id, C_WRITE, Scope.ORG)
    async with session.begin():
        await services.import_contacts(
            session,
            write,
            rows=[
                ("a@example.com", "A", {}),
                ("b@example.com", "B", {}),
                ("a@example.com", "A", {}),
            ],
        )
    assert [e.source for e in await _events(session, org_id)] == ["import", "import"]
    # A re-import updates and attests nothing new.
    async with session.begin():
        await services.import_contacts(session, write, rows=[("a@example.com", "A2", {})])
    assert len(await _events(session, org_id)) == 2


async def test_re_adding_an_address_that_opted_out_does_not_undo_the_refusal(
    session, org_id
) -> None:
    """The attestation is only ever written where the ledger is silent: re-importing somebody is
    not how an opt-out gets reversed."""
    await _record(
        session, org_id, "email", "marketing", "opted_out", EMAIL, source="unsubscribe_link"
    )
    await _add_contact(session, org_id)
    assert (await _decide(session, org_id, "email", "marketing", EMAIL)).allowed is False
    assert [e.status for e in await _events(session, org_id)] == ["opted_out"]


async def test_editing_a_contacts_address_carries_the_attestation_to_the_new_one(
    session, org_id
) -> None:
    contact = await _add_contact(session, org_id)
    write = ctx_for(org_id, C_WRITE, Scope.ORG)
    async with session.begin():
        await services.update_contact(
            session, write, contact_id=contact.id, email="new@example.com"
        )
    assert (await _decide(session, org_id, "email", "marketing", "new@example.com")).allowed is True


async def test_a_manual_suppression_is_mirrored_and_its_lift_restores_the_attested_opt_in(
    session, org_id
) -> None:
    await _add_contact(session, org_id)
    write = ctx_for(org_id, S_WRITE, Scope.ORG)
    async with session.begin():
        row = await services.add_suppression(session, write, email=EMAIL)
    suppression_id = row.id
    assert (await _decide(session, org_id, "email", "marketing", EMAIL)).allowed is False
    assert (await _decide(session, org_id, "email", "transactional", EMAIL)).allowed is True

    async with session.begin():
        await services.remove_suppression(session, write, suppression_id=suppression_id)
    assert (await _decide(session, org_id, "email", "marketing", EMAIL)).allowed is True


# --------------------------------------------------------------------- the unsubscribe link

UNSUBSCRIBE = "/api/v1/engage/unsubscribe"


async def test_the_one_click_post_writes_an_opted_out_event_for_both_purposes(
    client, session, org_id
) -> None:
    await _add_contact(session, org_id)
    token = services.unsubscribe_token(org_id, EMAIL)
    assert (await client.post(f"{UNSUBSCRIBE}/{token}")).status_code == 200

    unsub = [e for e in await _events(session, org_id) if e.source == "unsubscribe_link"]
    assert {(e.purpose, e.status) for e in unsub} == {
        ("marketing", "opted_out"),
        ("transactional", "opted_out"),
    }
    assert (await _decide(session, org_id, "email", "marketing", EMAIL)).allowed is False
    assert (await _decide(session, org_id, "email", "transactional", EMAIL)).allowed is False


async def test_the_confirmation_form_post_writes_it_too(client, session, org_id) -> None:
    token = services.unsubscribe_token(org_id, EMAIL)
    response = await client.post(f"{UNSUBSCRIBE}/{token}", headers={"Accept": "text/html"})
    assert response.status_code == 200
    assert len([e for e in await _events(session, org_id) if e.source == "unsubscribe_link"]) == 2


async def test_the_confirmation_page_get_writes_nothing(client, session, org_id) -> None:
    """A mail scanner prefetching the link must not opt anyone out."""
    token = services.unsubscribe_token(org_id, EMAIL)
    assert (await client.get(f"{UNSUBSCRIBE}/{token}")).status_code == 200
    assert await _events(session, org_id) == []


async def test_repeat_clicks_add_nothing_but_a_click_after_an_opt_in_still_counts(
    client, session, org_id
) -> None:
    token = services.unsubscribe_token(org_id, EMAIL)
    await client.post(f"{UNSUBSCRIBE}/{token}")
    await client.post(f"{UNSUBSCRIBE}/{token}")
    assert len(await _events(session, org_id)) == 2

    # The person re-subscribes through a form the organization recorded — and later clicks the
    # very same old link again. The click is a fresh refusal, not a duplicate to swallow.
    await _record(session, org_id, "email", "marketing", "opted_in", EMAIL, source="web_form")
    await client.post(f"{UNSUBSCRIBE}/{token}")
    marketing = [e for e in await _events(session, org_id) if e.purpose == "marketing"]
    assert [e.status for e in marketing] == ["opted_out", "opted_in", "opted_out"]


async def test_a_v2_link_opts_out_the_recipient_it_names_without_opening_the_address(
    client, session, org_id
) -> None:
    contact = await _add_contact(session, org_id)
    async with session.begin():
        recipient = models.CampaignRecipient(
            organization_id=org_id,
            campaign_id=await _bare_campaign(session, org_id),
            contact_id=contact.id,
            email_enc=contact.email_enc,
            email_masked=contact.email_masked,
            email_index=contact.email_index,
            status="sent",
        )
        session.add(recipient)
        await session.flush()
        recipient_id = recipient.id
    token = services.unsubscribe_token_for_recipient(org_id, recipient_id)
    assert (await client.post(f"{UNSUBSCRIBE}/{token}")).status_code == 200

    unsub = [e for e in await _events(session, org_id) if e.source == "unsubscribe_link"]
    assert len(unsub) == 2
    assert {e.contact_id for e in unsub} == {contact.id}
    assert (await _decide(session, org_id, "email", "marketing", EMAIL)).allowed is False


async def _bare_campaign(session, org_id: uuid.UUID) -> uuid.UUID:
    domain_row = models.EmailDomain(organization_id=org_id, domain="company.com", status="verified")
    session.add(domain_row)
    await session.flush()
    sender = models.Sender(
        organization_id=org_id,
        domain_id=domain_row.id,
        name="News",
        address="news@company.com",
        verified_at=datetime.now(UTC),
    )
    session.add(sender)
    await session.flush()
    campaign = models.Campaign(
        organization_id=org_id,
        name="Autumn",
        sender_id=sender.id,
        subject="News",
        html_body="<p>Hi</p>",
        text_body="Hi",
        audience="all_sendable",
        status="sent",
    )
    session.add(campaign)
    await session.flush()
    return campaign.id


# --------------------------------------------------------------------- campaign selection


async def _selectable_emails(session, org_id) -> set[str]:
    ctx = ctx_for(org_id, C_WRITE, Scope.ORG)
    async with session.begin():
        rows = await selectors.sendable_contacts(session, ctx)
    return {r.email_masked for r in rows}


async def test_campaigns_select_only_contacts_who_opted_in(session, org_id) -> None:
    await _add_contact(session, org_id, "yes@example.com")
    await _add_contact(session, org_id, "no@example.com")
    await _record(session, org_id, "email", "marketing", "opted_out", "no@example.com")
    ours = pii.mask("yes@example.com", kind="email")
    assert await _selectable_emails(session, org_id) == {ours}


async def test_a_contact_with_no_event_at_all_is_not_selected(session, org_id) -> None:
    """A contact written by some path that records no consent is not mailable — silence is not
    consent — whatever the legacy suppression store says."""
    sealed, masked = pii.seal("silent@example.com", kind="email", settings=settings())
    async with session.begin():
        session.add(
            models.Contact(
                organization_id=org_id,
                email_enc=sealed,
                email_masked=masked,
                email_index=selectors.email_index("silent@example.com"),
                name="",
                attributes={},
            )
        )
    assert await _selectable_emails(session, org_id) == set()


async def test_unsubscribing_removes_the_contact_from_campaign_selection(
    client, session, org_id
) -> None:
    await _add_contact(session, org_id)
    assert len(await _selectable_emails(session, org_id)) == 1
    token = services.unsubscribe_token(org_id, EMAIL)
    assert (await client.post(f"{UNSUBSCRIBE}/{token}")).status_code == 200
    assert await _selectable_emails(session, org_id) == set()


async def test_sendable_contacts_agrees_with_the_decision_for_every_state(session, org_id) -> None:
    """The SQL predicate and the Python decision are two statements of one policy; this pins them
    together across every state a subject can be in."""
    addresses = {
        "attested": "attested@example.com",
        "opted_out": "opted-out@example.com",
        "reopted": "reopted@example.com",
        "nothing": "nothing@example.com",
        "unsubscribed": "unsub@example.com",
        "bounced": "bounced@example.com",
        "backdated": "backdated@example.com",
    }
    now = datetime.now(UTC)
    for address in addresses.values():
        sealed, masked = pii.seal(address, kind="email", settings=settings())
        async with session.begin():
            session.add(
                models.Contact(
                    organization_id=org_id,
                    email_enc=sealed,
                    email_masked=masked,
                    email_index=selectors.email_index(address),
                    name="",
                    attributes={},
                )
            )
    await _record(session, org_id, "email", "marketing", "opted_in", addresses["attested"])
    await _record(
        session,
        org_id,
        "email",
        "marketing",
        "opted_in",
        addresses["opted_out"],
        when=now - timedelta(days=2),
    )
    await _record(
        session,
        org_id,
        "email",
        "marketing",
        "opted_out",
        addresses["opted_out"],
        when=now - timedelta(days=1),
    )
    for status, days in (("opted_out", 3), ("opted_in", 1)):
        await _record(
            session,
            org_id,
            "email",
            "marketing",
            status,
            addresses["reopted"],
            when=now - timedelta(days=days),
        )
    await _record(session, org_id, "email", "marketing", "opted_in", addresses["unsubscribed"])
    await _suppress(session, org_id, addresses["unsubscribed"], "unsubscribed")
    await _record(session, org_id, "email", "marketing", "opted_in", addresses["bounced"])
    await _suppress(session, org_id, addresses["bounced"], "bounced")
    await _record(
        session, org_id, "email", "marketing", "opted_out", addresses["backdated"], when=now
    )
    await _record(
        session,
        org_id,
        "email",
        "marketing",
        "opted_in",
        addresses["backdated"],
        when=now - timedelta(days=9),
    )

    by_decision = set()
    for address in addresses.values():
        if (await _decide(session, org_id, "email", "marketing", address)).allowed:
            by_decision.add(pii.mask(address, kind="email"))
    assert by_decision, "the matrix must leave someone selectable or it proves nothing"
    assert by_decision == await _selectable_emails(session, org_id)


async def test_the_send_gate_refuses_an_audience_nobody_in_which_opted_in(session, org_id) -> None:
    """The gate reads `sendable_contacts`, so a campaign whose whole audience is unconsented
    fails at the gate with a sentence rather than completing `sent` to nobody."""
    sender_id = await _seed_sender(session, org_id)
    write = ctx_for(org_id, "engage.campaign.campaign.write", Scope.ORG)
    send = ctx_for(org_id, "engage.campaign.campaign.send", Scope.ORG)
    sealed, masked = pii.seal("silent@example.com", kind="email", settings=settings())
    async with session.begin():
        session.add(
            models.Contact(
                organization_id=org_id,
                email_enc=sealed,
                email_masked=masked,
                email_index=selectors.email_index("silent@example.com"),
                name="",
                attributes={},
            )
        )
    async with session.begin():
        campaign = await services.create_campaign(
            session,
            write,
            fields={
                "name": "Autumn",
                "sender_id": sender_id,
                "subject": "News",
                "html_body": "<p>Hello</p>",
                "text_body": "Hello",
                "audience": "all_sendable",
                "list_id": None,
                "scheduled_at": None,
            },
        )
    campaign_id = campaign.id
    with pytest.raises(BusinessRuleProblem, match="no contacts who can be emailed"):
        async with session.begin():
            await services.request_send(session, send, campaign_id=campaign_id)


async def _seed_sender(session, org_id: uuid.UUID) -> uuid.UUID:
    async with session.begin():
        domain_row = models.EmailDomain(
            organization_id=org_id, domain="company.com", status="verified"
        )
        session.add(domain_row)
        await session.flush()
        sender = models.Sender(
            organization_id=org_id,
            domain_id=domain_row.id,
            name="News",
            address="news@company.com",
            verified_at=datetime.now(UTC),
        )
        session.add(sender)
        session.add(
            models.EmailConnection(
                organization_id=org_id,
                kind="google",
                host="smtp.gmail.com",
                username="news@company.com",
                daily_cap=2000,
            )
        )
        await session.flush()
        return sender.id


# --------------------------------------------------------------------- the per-batch claim


async def _sending_campaign(session, org_id, *emails: str) -> uuid.UUID:
    """A campaign already `sending` and expanded, with one queued recipient per address."""
    for email in emails:
        await _add_contact(session, org_id, email)
    sender_id = await _seed_sender(session, org_id)
    write = ctx_for(org_id, "engage.campaign.campaign.write", Scope.ORG)
    send = ctx_for(org_id, "engage.campaign.campaign.send", Scope.ORG)
    async with session.begin():
        campaign = await services.create_campaign(
            session,
            write,
            fields={
                "name": "Autumn",
                "sender_id": sender_id,
                "subject": "News",
                "html_body": "<p>Hello</p>",
                "text_body": "Hello",
                "audience": "all_sendable",
                "list_id": None,
                "scheduled_at": None,
            },
        )
    campaign_id = campaign.id
    async with session.begin():
        await services.request_send(session, send, campaign_id=campaign_id)
    async with session.begin():
        await services.expand_campaign(
            session, organization_id=org_id, campaign_id=campaign_id, batch_size=100
        )
    return campaign_id


async def test_an_opt_out_between_expansion_and_claim_is_refused_with_a_reason(
    session, org_id
) -> None:
    campaign_id = await _sending_campaign(session, org_id, "keep@example.com", "drop@example.com")
    # A ledger-only refusal (an operator recording a phone call): no legacy suppression exists,
    # so only the consent check can catch it, and it must say why.
    await _record(session, org_id, "email", "marketing", "opted_out", "drop@example.com")

    async with session.begin():
        outcome, batch = await services.claim_campaign_batch(
            session,
            organization_id=org_id,
            campaign_id=campaign_id,
            batch_size=100,
            lease_seconds=300,
            transport=RecordingTransport(),
        )
    assert outcome == "claimed"
    assert batch is not None
    assert len(batch.messages) == 1
    assert [(status, note) for _, status, note in batch.resolved] == [
        ("suppressed", "consent:opted_out")
    ]


async def test_a_recipient_with_no_opt_in_on_record_is_refused_at_the_claim(
    session, org_id
) -> None:
    """The recipient row can outlive the reason it was expanded: here a contact whose consent
    was never recorded is handed the claim, and is refused with `consent:no_opt_in`."""
    campaign_id = await _sending_campaign(session, org_id, "keep@example.com")
    sealed, masked = pii.seal("silent@example.com", kind="email", settings=settings())
    async with session.begin():
        session.add(
            models.CampaignRecipient(
                organization_id=org_id,
                campaign_id=campaign_id,
                contact_id=uuid.uuid4(),
                email_enc=sealed,
                email_masked=masked,
                email_index=selectors.email_index("silent@example.com"),
                status="queued",
            )
        )
    async with session.begin():
        outcome, batch = await services.claim_campaign_batch(
            session,
            organization_id=org_id,
            campaign_id=campaign_id,
            batch_size=100,
            lease_seconds=300,
            transport=RecordingTransport(),
        )
    assert outcome == "claimed" and batch is not None
    assert [(status, note) for _, status, note in batch.resolved] == [
        ("suppressed", "consent:no_opt_in")
    ]
    assert len(batch.messages) == 1


async def test_a_legacy_suppression_at_the_claim_is_recorded_as_it_always_was(
    session, org_id
) -> None:
    campaign_id = await _sending_campaign(session, org_id, "keep@example.com", "gone@example.com")
    await _suppress(session, org_id, "gone@example.com", "unsubscribed")
    async with session.begin():
        _, batch = await services.claim_campaign_batch(
            session,
            organization_id=org_id,
            campaign_id=campaign_id,
            batch_size=100,
            lease_seconds=300,
            transport=RecordingTransport(),
        )
    assert batch is not None
    assert [(status, note) for _, status, note in batch.resolved] == [("suppressed", "")]


# --------------------------------------------------------------------- append-only


async def test_the_app_role_cannot_update_a_ledger_row(session, org_id) -> None:
    await _record(session, org_id, "email", "marketing", "opted_in", EMAIL)
    with pytest.raises(ProgrammingError, match="permission denied"):
        async with session.begin():
            await session.execute(
                text("UPDATE notification.consent_events SET status = 'opted_out'")
            )


async def test_the_app_role_cannot_delete_a_ledger_row(session, org_id) -> None:
    await _record(session, org_id, "email", "marketing", "opted_in", EMAIL)
    with pytest.raises(ProgrammingError, match="permission denied"):
        async with session.begin():
            await session.execute(text("DELETE FROM notification.consent_events"))
    assert len(await _events(session, org_id)) == 1


async def test_the_orm_cannot_be_used_to_rewrite_a_ledger_row_either(session, org_id) -> None:
    event = await _record(session, org_id, "email", "marketing", "opted_in", EMAIL)
    # Captured before either attempt: a failed flush rolls the shared session back and expires
    # every loaded row, and the second attempt must not re-read an expired attribute.
    event_id = event.id
    with pytest.raises(DBAPIError):
        async with session.begin():
            row = await session.get(ConsentEvent, event_id)
            assert row is not None
            row.status = "opted_out"
    with pytest.raises(DBAPIError):
        async with session.begin():
            row = await session.get(ConsentEvent, event_id)
            assert row is not None
            await session.delete(row)


def test_no_code_path_updates_or_deletes_a_ledger_row() -> None:
    """The privilege is the enforcement; this is the tripwire that says nobody has written the
    statement the privilege would have refused."""
    forbidden = re.compile(
        r"(update|delete)\(\s*ConsentEvent\b"
        r"|ConsentEvent\.__table__\.(update|delete)"
        r"|(UPDATE|DELETE\s+FROM)\s+\S*consent_events",
        re.IGNORECASE,
    )
    root = Path(__file__).resolve().parent.parent / "app"
    offenders = [
        path.name
        for path in root.rglob("*.py")
        if path.name != "consent_backfill.py" and forbidden.search(path.read_text())
    ]
    assert offenders == []


async def test_a_retried_write_under_the_same_key_is_one_row(session, org_id) -> None:
    subject = consent.subject_for("email", EMAIL)
    for _ in range(2):
        async with session.begin():
            await consent.append(
                session,
                organization_id=org_id,
                subject=subject,
                purposes=("marketing",),
                status="opted_in",
                source="api",
                evidence="retry",
                idempotency_key="key-0001-abcdef",
            )
    assert len(await _events(session, org_id)) == 1


async def test_the_check_constraints_refuse_a_value_outside_the_vocabulary(session, org_id) -> None:
    subject = consent.subject_for("email", EMAIL)
    for channel, purpose, status in (
        ("pigeon", "marketing", "opted_in"),
        ("email", "spam", "opted_in"),
        ("email", "marketing", "maybe"),
    ):
        rows = [
            {
                "id": uuid.uuid4(),
                "organization_id": org_id,
                "channel": channel,
                "purpose": purpose,
                "subject_enc": subject.enc,
                "subject_masked": subject.masked,
                "subject_index": subject.index,
                "status": status,
                "source": "api",
                "occurred_at": datetime.now(UTC),
            }
        ]
        with pytest.raises(DBAPIError, match="violates check constraint"):
            async with session.begin():
                await session.execute(ConsentEvent.__table__.insert(), rows)


# --------------------------------------------------------------------- PII


async def test_no_plaintext_address_is_in_the_table(session, admin_session, org_id) -> None:
    address = "very.identifiable.person@example.com"
    phone = "+919812345678"
    await _record(session, org_id, "email", "marketing", "opted_in", address, source="web_form")
    await _record(session, org_id, "whatsapp", "marketing", "opted_in", phone, source="checkout")
    await _fold(session, org_id, "STOP", "wamid.PII1")

    async with admin_session.begin():
        dump = (
            await admin_session.execute(
                text(
                    "SELECT string_agg(row_to_json(t)::text, ' ') "
                    "FROM notification.consent_events t"
                )
            )
        ).scalar_one()
    assert address not in dump
    assert phone not in dump
    assert "very.identifiable" not in dump
    assert "9812345678" not in dump
    assert "+16505551234" not in dump

    rows = await _events(session, org_id)
    email_row = next(e for e in rows if e.channel == "email")
    assert email_row.subject_masked == pii.mask(address, kind="email")
    assert email_row.subject_masked != address
    # Sealed for real: it opens, and only with the key.
    assert pii.open_sealed(email_row.subject_enc, settings=settings()) == address
    assert email_row.subject_index == selectors.email_index(address)


# --------------------------------------------------------------------- cross-tenant


async def test_another_tenants_ledger_is_invisible_and_does_not_influence_a_decision(
    session, admin_session, org_id
) -> None:
    other = uuid.uuid4()
    subject = consent.subject_for("email", EMAIL)
    async with admin_session.begin():
        await consent.append(
            admin_session,
            organization_id=other,
            subject=subject,
            purposes=("marketing", "transactional"),
            status="opted_out",
            source="unsubscribe_link",
            evidence="theirs",
        )

    async with session.begin():
        assert (await session.execute(select(ConsentEvent))).scalars().all() == []
        # Even naming the other organization's id, the tenant policy and the explicit filter both
        # refuse: their events are not ours to read.
        assert (
            await selectors.latest_consent_status(
                session, other, "email", "marketing", [subject.index]
            )
            == {}
        )
    # Their refusal does not become ours...
    assert await _decide(session, org_id, "email", "transactional", EMAIL) == (
        consent.ConsentDecision(True, "no_opt_out")
    )
    # ...and our opt-in does not undo theirs.
    await _record(session, org_id, "email", "marketing", "opted_in", EMAIL)
    assert (await _decide(session, org_id, "email", "marketing", EMAIL)).allowed is True


async def test_the_other_tenants_decision_is_unaffected_by_ours(
    session, admin_session, org_id
) -> None:
    other = uuid.uuid4()
    await _record(session, org_id, "email", "marketing", "opted_in", EMAIL)
    async with admin_session.begin():
        theirs = await consent.decide(admin_session, other, "email", "marketing", EMAIL)
    assert theirs == consent.ConsentDecision(False, "no_opt_in")


async def test_a_tenant_cannot_write_into_another_tenants_ledger(session, org_id) -> None:
    subject = consent.subject_for("email", EMAIL)
    with pytest.raises(DBAPIError, match="row-level security"):
        async with session.begin():
            await consent.append(
                session,
                organization_id=uuid.uuid4(),
                subject=subject,
                purposes=("marketing",),
                status="opted_out",
                source="admin",
            )


async def test_a_tenants_unsubscribe_link_cannot_opt_out_another_tenants_contact(
    client, session, admin_session, org_id
) -> None:
    other = uuid.uuid4()
    other_token = services.unsubscribe_token(other, EMAIL)
    assert (await client.post(f"{UNSUBSCRIBE}/{other_token}")).status_code == 200
    # Their token wrote into their ledger; ours holds nothing.
    assert await _events(session, org_id) == []
    async with admin_session.begin():
        theirs = list(
            (
                await admin_session.execute(
                    select(ConsentEvent).where(ConsentEvent.organization_id == other)
                )
            ).scalars()
        )
    assert len(theirs) == 2
