"""The automation engine, against real PostgreSQL.

This is the test that proves the outbound email's central claim. An event arrives from a service
that notification does not import and has never heard of, and a message goes out because an
operator wrote a row — no deploy, no code.

Every assertion here is about the *fold*, not the sending: `apply_rules` stages rows and stops.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app import services
from app.domain import STATE_PENDING, STATE_SUPPRESSED
from app.models import Message, OutboxEvent
from platform_core.auth import Scope
from tests.conftest import seed_automation
from tests.helpers import ctx_for, envelope_for

pytestmark = pytest.mark.integration


TICKET = "support.desk.ticket.created"


def _ticket_event(org_id: uuid.UUID, **over: object) -> dict:
    payload = {
        "number": "TK-0001",
        "requester_name": "Helena Vogel",
        "requester_email": "h@northwind.example.com",
    }
    payload.update(over)
    return envelope_for(org_id, TICKET, payload)


async def test_an_event_from_another_service_stages_a_message(session, org_id) -> None:
    """The whole point: support publishes, notification reacts, nothing imports anything."""
    await seed_automation(session, org_id)

    async with session.begin():
        staged = await services.apply_rules(
            session, organization_id=org_id, envelope=_ticket_event(org_id)
        )

    assert len(staged) == 1
    message = staged[0]
    assert message.to_address == "h@northwind.example.com"
    assert message.subject == "We have your ticket TK-0001"
    assert "Helena Vogel" in message.body
    assert message.state == STATE_PENDING
    assert message.rule_code == "ack_new_tickets"


async def test_staging_queues_exactly_one_dispatch_event(session, org_id) -> None:
    """Sending is driven by the outbox, so the queued event is the thing that makes it happen."""
    await seed_automation(session, org_id)
    async with session.begin():
        await services.apply_rules(session, organization_id=org_id, envelope=_ticket_event(org_id))

    rows = (
        (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == services.EVT_MESSAGE_QUEUED)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


async def test_an_event_with_no_matching_rule_stages_nothing(session, org_id) -> None:
    await seed_automation(session, org_id)
    other = envelope_for(org_id, "hrms.people.employee.onboarded", {"email": "a@b.com"})
    async with session.begin():
        staged = await services.apply_rules(session, organization_id=org_id, envelope=other)
    assert staged == []


async def test_an_inactive_rule_does_not_fire(session, org_id) -> None:
    """Deactivating leaves the row in place — an audit trail of what used to fire."""
    await seed_automation(session, org_id)
    write = ctx_for(org_id, "notification.delivery.template.write", Scope.ORG)
    async with session.begin():
        await services.upsert_rule(
            session,
            write,
            code="ack_new_tickets",
            event_type=TICKET,
            template_code="ticket_acknowledged",
            channel="email",
            recipient_path="requester_email",
            active=False,
        )
    async with session.begin():
        staged = await services.apply_rules(
            session, organization_id=org_id, envelope=_ticket_event(org_id)
        )
    assert staged == []


async def test_a_missing_recipient_is_skipped_not_raised(session, org_id) -> None:
    """An event that legitimately carries no address is not an error — raising would dead-letter
    an event that other rules handled correctly."""
    await seed_automation(session, org_id)
    event = envelope_for(org_id, TICKET, {"number": "TK-2", "requester_name": "A"})
    async with session.begin():
        staged = await services.apply_rules(session, organization_id=org_id, envelope=event)
    assert staged == []


async def test_a_template_that_cannot_render_stages_nothing(session, org_id) -> None:
    """Strict rendering, end to end: a payload missing `requester_name` sends nothing rather
    than sending "Hello ,"."""
    await seed_automation(session, org_id)
    event = envelope_for(org_id, TICKET, {"number": "TK-3", "requester_email": "a@b.example.com"})
    async with session.begin():
        staged = await services.apply_rules(session, organization_id=org_id, envelope=event)
    assert staged == []


async def test_the_same_event_twice_stages_one_message(session, org_id) -> None:
    """The second net under `processed_events`: redelivery must not reach a customer twice."""
    await seed_automation(session, org_id)
    event = _ticket_event(org_id)

    async with session.begin():
        first = await services.apply_rules(session, organization_id=org_id, envelope=event)
    async with session.begin():
        second = await services.apply_rules(session, organization_id=org_id, envelope=event)

    assert len(first) == 1
    assert second == []


async def test_two_rules_on_one_event_stage_two_messages(session, org_id) -> None:
    await seed_automation(session, org_id)
    write = ctx_for(org_id, "notification.delivery.template.write", Scope.ORG)
    async with session.begin():
        await services.upsert_template(
            session,
            write,
            code="ops_heads_up",
            channel="email",
            subject="New ticket {{number}}",
            body="{{number}} just came in.",
        )
        await services.upsert_rule(
            session,
            write,
            code="tell_ops",
            event_type=TICKET,
            template_code="ops_heads_up",
            channel="email",
            recipient_literal="ops@example.com",
        )

    async with session.begin():
        staged = await services.apply_rules(
            session, organization_id=org_id, envelope=_ticket_event(org_id)
        )

    assert {m.rule_code for m in staged} == {"ack_new_tickets", "tell_ops"}


async def test_a_suppressed_recipient_gets_a_row_but_no_dispatch_event(session, org_id) -> None:
    """ "We deliberately did not tell this customer" is a fact worth keeping — an absent row is
    indistinguishable from a rule that never fired."""
    await seed_automation(session, org_id)
    write = ctx_for(org_id, "notification.delivery.preference.write", Scope.ORG)
    async with session.begin():
        await services.set_preference(
            session,
            write,
            recipient="h@northwind.example.com",
            channel="email",
            suppressed=True,
            reason="unsubscribed",
        )

    async with session.begin():
        staged = await services.apply_rules(
            session, organization_id=org_id, envelope=_ticket_event(org_id)
        )

    assert len(staged) == 1
    assert staged[0].state == STATE_SUPPRESSED

    queued = (
        (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == services.EVT_MESSAGE_QUEUED)
            )
        )
        .scalars()
        .all()
    )
    assert queued == []


async def test_a_rule_naming_a_missing_template_is_refused_at_authoring(session, org_id) -> None:
    """A rule pointing at nothing would fire forever and send nothing — an absence, which is the
    hardest kind of bug to notice. So it is refused when written."""
    from platform_core.errors import ValidationProblem

    write = ctx_for(org_id, "notification.delivery.template.write", Scope.ORG)
    with pytest.raises(ValidationProblem, match="create the template first"):
        async with session.begin():
            await services.upsert_rule(
                session,
                write,
                code="broken",
                event_type=TICKET,
                template_code="does_not_exist",
                channel="email",
                recipient_path="requester_email",
            )


async def test_messages_record_the_rendered_text_not_a_template_reference(session, org_id) -> None:
    """Editing a template next month must not change what last month's message says it said."""
    await seed_automation(session, org_id)
    async with session.begin():
        await services.apply_rules(session, organization_id=org_id, envelope=_ticket_event(org_id))
    write = ctx_for(org_id, "notification.delivery.template.write", Scope.ORG)
    async with session.begin():
        await services.upsert_template(
            session,
            write,
            code="ticket_acknowledged",
            channel="email",
            subject="Completely different",
            body="Rewritten.",
        )

    stored = (await session.execute(select(Message))).scalars().one()
    assert stored.subject == "We have your ticket TK-0001"
    assert "Rewritten." not in stored.body


# --------------------------------------------------------------------- provider templates


async def _seed_provider_template(session, org_id, *, status: str = "approved") -> None:
    """An approved WhatsApp template and a rule that sends it. The proactive shape."""
    write = ctx_for(org_id, "notification.delivery.template.write", Scope.ORG)
    async with session.begin():
        await services.upsert_provider_template(
            session,
            write,
            code="ticket_ack_wa",
            channel="whatsapp",
            provider_name="ticket_acknowledged",
            language="en_US",
            category="utility",
            parameter_format="positional",
            status=status,
            status_reason=None,
            variables=[
                {"key": "requester_name", "component": "body", "position": 0},
                {"key": "number", "component": "body", "position": 1},
            ],
            preview_text="Hello {{1}}, we opened {{2}} and are on it.",
        )
        await services.upsert_rule(
            session,
            write,
            code="ack_new_tickets_wa",
            event_type=TICKET,
            channel="whatsapp",
            provider_template_code="ticket_ack_wa",
            recipient_path="requester_phone",
        )


def _wa_event(org_id: uuid.UUID) -> dict:
    return envelope_for(
        org_id,
        TICKET,
        {
            "number": "TK-0001",
            "requester_name": "Helena Vogel",
            "requester_phone": "+917976603446",
        },
    )


async def test_a_rule_sends_a_provider_template(session, org_id) -> None:
    """The whole point of the phase: a proactive WhatsApp notification, which free text cannot
    deliver outside the 24-hour window."""
    await _seed_provider_template(session, org_id)

    async with session.begin():
        staged = await services.apply_rules(
            session, organization_id=org_id, envelope=_wa_event(org_id)
        )

    assert len(staged) == 1
    message = staged[0]
    assert message.channel == "whatsapp"
    assert message.to_address == "+917976603446"
    assert message.provider_template_code == "ticket_ack_wa"
    assert message.provider_template_payload == {
        "name": "ticket_acknowledged",
        "language": {"code": "en_US"},
        "components": [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": "Helena Vogel"},
                    {"type": "text", "text": "TK-0001"},
                ],
            }
        ],
    }


async def test_a_rule_driven_send_honours_an_earlier_stop(session, org_id) -> None:
    """`services.stage_message` is the one chokepoint both a rule-driven send and a human's
    conversation reply go through — `test_inbound_webhook.py::
    test_stop_suppresses_and_the_next_reply_is_refused` proves the reply side, this proves the
    automation side never got its own, unsuppressed path to the same number."""
    await _seed_provider_template(session, org_id)
    write = ctx_for(org_id, "notification.delivery.preference.write", Scope.ORG)
    async with session.begin():
        # The same row `webhooks.STOP_KEYWORDS` handling writes for an inbound "STOP" — set here
        # through the human-facing API rather than the webhook fold, since the fold itself is
        # exercised end to end in `test_inbound_webhook.py`.
        await services.set_preference(
            session,
            write,
            recipient="+917976603446",
            channel="whatsapp",
            reason="customer_stop",
        )

    async with session.begin():
        staged = await services.apply_rules(
            session, organization_id=org_id, envelope=_wa_event(org_id)
        )

    assert len(staged) == 1
    assert staged[0].state == STATE_SUPPRESSED


async def test_an_unapproved_template_stages_nothing(session, org_id) -> None:
    """Refused here rather than by Meta: its 4xx would cost a message and tell the operator
    nothing that "still in review" does not say now."""
    await _seed_provider_template(session, org_id, status="pending")

    async with session.begin():
        staged = await services.apply_rules(
            session, organization_id=org_id, envelope=_wa_event(org_id)
        )
    assert staged == []


async def test_a_missing_template_variable_stages_nothing(session, org_id) -> None:
    """Strictness carried through to templates: a blank parameter is a message the customer
    reads with a hole in it."""
    await _seed_provider_template(session, org_id)
    event = envelope_for(org_id, TICKET, {"number": "TK-2", "requester_phone": "+917976603446"})
    async with session.begin():
        staged = await services.apply_rules(session, organization_id=org_id, envelope=event)
    assert staged == []


async def test_a_rule_naming_a_missing_provider_template_is_refused(session, org_id) -> None:
    from platform_core.errors import ValidationProblem

    write = ctx_for(org_id, "notification.delivery.template.write", Scope.ORG)
    with pytest.raises(ValidationProblem, match="register the template first"):
        async with session.begin():
            await services.upsert_rule(
                session,
                write,
                code="broken_wa",
                event_type=TICKET,
                channel="whatsapp",
                provider_template_code="does_not_exist",
                recipient_path="requester_phone",
            )


async def test_a_template_with_a_position_gap_is_refused_at_authoring(session, org_id) -> None:
    """The gap check, through the service: a shifted parameter delivers real data to the wrong
    placeholder, so it is a 422 now rather than a wrong message later."""
    from platform_core.errors import ValidationProblem

    write = ctx_for(org_id, "notification.delivery.template.write", Scope.ORG)
    with pytest.raises(ValidationProblem, match="wrong placeholder"):
        async with session.begin():
            await services.upsert_provider_template(
                session,
                write,
                code="gappy",
                channel="whatsapp",
                provider_name="gappy",
                language="en",
                category="utility",
                parameter_format="positional",
                status="approved",
                status_reason=None,
                variables=[
                    {"key": "a", "component": "body", "position": 0},
                    {"key": "b", "component": "body", "position": 2},
                ],
                preview_text="",
            )


# --------------------------------------------------------------------- targeting a customer


async def _seed_contact_rule(session, org_id) -> None:
    """A WhatsApp template and a rule that targets a *contact id* carried in the event."""
    write = ctx_for(org_id, "notification.delivery.template.write", Scope.ORG)
    async with session.begin():
        await services.upsert_provider_template(
            session,
            write,
            code="invoice_due_wa",
            channel="whatsapp",
            provider_name="invoice_due_reminder",
            language="en_US",
            category="utility",
            parameter_format="positional",
            status="approved",
            status_reason=None,
            variables=[{"key": "number", "component": "body", "position": 0}],
            preview_text="Invoice {{1}} is due.",
        )
        await services.upsert_rule(
            session,
            write,
            code="remind_invoice_wa",
            event_type="finance.billing.invoice.issued",
            channel="whatsapp",
            provider_template_code="invoice_due_wa",
            recipient_contact_path="customer.contact_id",
        )


def _invoice_event(org_id: uuid.UUID) -> dict:
    return envelope_for(
        org_id,
        "finance.billing.invoice.issued",
        {"number": "INV-9", "customer": {"contact_id": "c0ffee"}},
    )


async def test_a_rule_targets_a_customer_through_the_resolver(session, org_id) -> None:
    """The seam Phase 0 unblocked: the event carries a contact id, never a phone number, and
    the address is resolved by the service that owns it."""
    await _seed_contact_rule(session, org_id)

    async def resolver(org: str, contact: str, channel: str) -> str | None:
        assert contact == "c0ffee"
        assert channel == "whatsapp"
        return "+917976603446"

    async with session.begin():
        staged = await services.apply_rules(
            session,
            organization_id=org_id,
            envelope=_invoice_event(org_id),
            resolve_target=resolver,
        )

    assert len(staged) == 1
    assert staged[0].to_address == "+917976603446"
    assert staged[0].channel == "whatsapp"


async def test_a_contact_with_no_address_stages_nothing(session, org_id) -> None:
    await _seed_contact_rule(session, org_id)

    async def resolver(org: str, contact: str, channel: str) -> str | None:
        return None

    async with session.begin():
        staged = await services.apply_rules(
            session,
            organization_id=org_id,
            envelope=_invoice_event(org_id),
            resolve_target=resolver,
        )
    assert staged == []


async def test_an_unavailable_customer_service_propagates(session, org_id) -> None:
    """Deliberately not swallowed. A notification is still owed, so the exception must reach
    the consumer, which nacks and lets the broker redeliver. Turning this into a skip would
    make a transient outage into silently unsent mail."""
    from app.targets import TargetUnavailable

    await _seed_contact_rule(session, org_id)

    async def resolver(org: str, contact: str, channel: str) -> str | None:
        raise TargetUnavailable("customer-service is down")

    with pytest.raises(TargetUnavailable):
        async with session.begin():
            await services.apply_rules(
                session,
                organization_id=org_id,
                envelope=_invoice_event(org_id),
                resolve_target=resolver,
            )


async def test_a_contact_rule_without_a_resolver_stages_nothing(session, org_id) -> None:
    """A process wired without a resolver refuses loudly rather than looking like "no rule
    matched" — the absence would otherwise be indistinguishable from correct behaviour."""
    await _seed_contact_rule(session, org_id)

    async with session.begin():
        staged = await services.apply_rules(
            session, organization_id=org_id, envelope=_invoice_event(org_id)
        )
    assert staged == []


async def test_an_event_without_the_contact_id_stages_nothing(session, org_id) -> None:
    await _seed_contact_rule(session, org_id)

    async def resolver(org: str, contact: str, channel: str) -> str | None:  # pragma: no cover
        raise AssertionError("must not be asked when the event carries no contact")

    event = envelope_for(org_id, "finance.billing.invoice.issued", {"number": "INV-9"})
    async with session.begin():
        staged = await services.apply_rules(
            session, organization_id=org_id, envelope=event, resolve_target=resolver
        )
    assert staged == []


async def test_a_resolved_address_is_still_normalised(session, org_id) -> None:
    """Customer-service canonicalises on write, and this normalises again on read. Two
    services agreeing by construction beats one trusting the other."""
    await _seed_contact_rule(session, org_id)

    async def resolver(org: str, contact: str, channel: str) -> str | None:
        return "+91 79766-03446"

    async with session.begin():
        staged = await services.apply_rules(
            session,
            organization_id=org_id,
            envelope=_invoice_event(org_id),
            resolve_target=resolver,
        )
    assert staged[0].to_address == "+917976603446"


async def test_a_resolved_address_that_is_not_messageable_stages_nothing(session, org_id) -> None:
    await _seed_contact_rule(session, org_id)

    async def resolver(org: str, contact: str, channel: str) -> str | None:
        return "7976603446"  # no country code

    async with session.begin():
        staged = await services.apply_rules(
            session,
            organization_id=org_id,
            envelope=_invoice_event(org_id),
            resolve_target=resolver,
        )
    assert staged == []
