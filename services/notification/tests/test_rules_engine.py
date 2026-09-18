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
