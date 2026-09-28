"""Fulfilment order-lifecycle notifications — `operations.orders.order.{placed,accepted,cancelled}`.

Nothing new is built here. `app.consumers.register` already binds **every** entry in
`domain.SUBSCRIBABLE_EVENTS` to the one generic fold (`app.consumers.fold` ->
`services.apply_rules`) — unlike `services/payment/app/consumers.py`, which hand-writes a handler
per event, notification has no per-event consumer code at all: adding an event here is exactly
the registry change below, nothing more. This module exists to prove that, for real, against the
actual payload shape `services/operations/app/orders/services.py` publishes (read, never imported
— one process holds one service's `app`), and to pin down exactly where that pipeline stops.

**`operations.orders.order.held` is not registered.** An operator's hold is an internal
operational pause, not a commercial milestone — the same call this service's own producer already
makes for the matching release (`release_hold_order`'s docstring: "No outbox event here: releasing
a hold resumes whatever commercial milestone the order was already at rather than reaching a new
one") and for a fresh order's `DRAFT` status ("a private working copy nothing outside operations
needs to know about yet"). `domain.validate_rule` refuses a rule on an unregistered event type, so
this is enforced, not merely an omission — see
`test_a_rule_cannot_be_authored_against_the_held_event`.

**The recipient gap.** `Order` (`services/operations/app/models.py`) carries `customer_id`,
`customer_name` and `customer_phone` — there is no email column at all. None of the three
`publish_event(...)` payloads below carries any of them: `placed`/`accepted` publish only
`organization_id, order_id, number, total_minor, currency[, settings_snapshot]`; `cancelled` adds
`reason, from_status, released_reservations`. Nobody a rule could point `recipient_path` or
`recipient_contact_path` at exists in the wire event today.
`test_todays_real_payload_resolves_no_recipient` below proves it by folding the exact payload
shape `place_order`/`accept_order`/`cancel_order` construct, unmodified. This is the same shape
of gap F4's finance slice found and punted on
(resolving a CRM contact into a finance customer id was not that slice's job) — resolving an order
into a CRM contact is not this slice's job either, and nothing here invents one. See the branch's
report for the specific field operations would need to add.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app import domain, services
from app.domain import STATE_PENDING, STATE_SUPPRESSED
from platform_core.auth import Scope
from tests.helpers import ctx_for, envelope_for

pytestmark = pytest.mark.integration

# Bare string constants, not imported cross-service — `services/payment/app/consumers.py`'s own
# convention for the same reason: `import app` is ambiguous across services (CLAUDE.md), so
# notification can never `from services.operations.app.orders.services import EVT_ORDER_PLACED`.
EVT_ORDER_PLACED = "operations.orders.order.placed"
EVT_ORDER_ACCEPTED = "operations.orders.order.accepted"
EVT_ORDER_CANCELLED = "operations.orders.order.cancelled"
EVT_ORDER_HELD = "operations.orders.order.held"

# Reference copy for each event's default customer message, in this platform's existing house
# style (the password-reset email in
# `migrations/versions/b8c1d2e3f4a5_platform_password_reset_rule.py`, and the ticket-acknowledged
# rule `tests/conftest.py::seed_automation` seeds for every other product's event): short, second
# person, names the thing that happened, nothing more. `{{number}}` matches the payload's real
# field — `publish_event(...)`'s `payload["number"]`, not `order_number`.
PLACED_BODY = "Your order {{number}} has been placed."
ACCEPTED_BODY = "Your order {{number}} has been accepted and is being prepared."
CANCELLED_BODY = "Your order {{number}} has been cancelled. Reason: {{reason}}"


def _placed_payload(**over: Any) -> dict[str, Any]:
    """Exactly `place_order`'s own `publish_event(..., payload={...})` (services.py ~L334)."""
    payload = {
        "organization_id": "",  # filled by the caller — order_id is real per-call
        "order_id": str(uuid.uuid4()),
        "number": "ORD-1001",
        "total_minor": 250_000,
        "currency": "INR",
    }
    payload.update(over)
    return payload


def _accepted_payload(**over: Any) -> dict[str, Any]:
    """Exactly `accept_order`'s own `publish_event(...)` payload (services.py ~L343 in the
    function body, second transaction)."""
    payload = {
        "organization_id": "",
        "order_id": str(uuid.uuid4()),
        "number": "ORD-1001",
        "total_minor": 250_000,
        "currency": "INR",
        "settings_snapshot": {"credit_check": "not_implemented"},
    }
    payload.update(over)
    return payload


def _cancelled_payload(**over: Any) -> dict[str, Any]:
    """Exactly `cancel_order`'s own `publish_event(...)` payload — the only one of the four that
    carries a reason, because `OrderCancelIn.reason` is `Field(min_length=1)` at the API layer
    (`services/operations/app/schemas.py`), so it is always present and never blank."""
    payload = {
        "organization_id": "",
        "order_id": str(uuid.uuid4()),
        "number": "ORD-1001",
        "reason": "Customer asked to cancel before dispatch",
        "from_status": "ACCEPTED",
        "released_reservations": [],
    }
    payload.update(over)
    return payload


def _event(org_id: uuid.UUID, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(payload)
    payload["organization_id"] = str(org_id)
    return envelope_for(org_id, event_type, payload)


# --------------------------------------------------------------------- the registry


async def test_the_three_events_are_subscribable_and_held_is_deliberately_not() -> None:
    assert EVT_ORDER_PLACED in domain.SUBSCRIBABLE_EVENTS
    assert EVT_ORDER_ACCEPTED in domain.SUBSCRIBABLE_EVENTS
    assert EVT_ORDER_CANCELLED in domain.SUBSCRIBABLE_EVENTS
    assert EVT_ORDER_HELD not in domain.SUBSCRIBABLE_EVENTS


async def test_a_rule_cannot_be_authored_against_the_held_event() -> None:
    """The same guard a typo'd event type hits also stops a rule on `held` — an organization
    cannot accidentally (or deliberately) wire a customer message to it."""
    with pytest.raises(domain.RuleError, match="not an event this service subscribes to"):
        domain.validate_rule(
            channel="email",
            event_type=EVT_ORDER_HELD,
            recipient_path=None,
            recipient_literal=None,
            recipient_contact_path="customer.contact_id",
            template_code="order_held",
        )


async def test_an_unrelated_operations_event_is_untouched() -> None:
    """Sanity: adding the order events changes nothing about the operations events already
    subscribable before this branch."""
    assert "operations.procurement.purchase_order.raised" in domain.SUBSCRIBABLE_EVENTS
    assert "operations.inventory.item.created" in domain.SUBSCRIBABLE_EVENTS


# --------------------------------------------------------------------- the default rule + template
#
# What an org's own admin would author via the existing `PUT /notifications/templates/{code}` and
# `PUT /notifications/rules/{code}` the moment an event is subscribable — the exact call shape
# `tests/conftest.py::seed_automation` and `tests/test_rules_engine.py::_seed_contact_rule` already
# use for every other product's event. `recipient_contact_path` is the resolver seam
# (`app/targets.py`) built for exactly this: "the event carries a contact id, never a phone number
# [or email address], and the address is resolved by the service that owns it." Email here is a
# stand-in channel to prove the *template* renders correctly — `Order` carries no email column at
# all (see module docstring), so this is deliberately not a claim about which channel a real rule
# would use.


async def _seed_order_rule(
    session, org_id: uuid.UUID, *, event_type: str, code: str, body: str
) -> None:
    write = ctx_for(org_id, "notification.delivery.template.write", Scope.ORG)
    async with session.begin():
        await services.upsert_template(
            session,
            write,
            code=code,
            channel="email",
            subject=None,
            body=body,
        )
        await services.upsert_rule(
            session,
            write,
            code=code,
            event_type=event_type,
            template_code=code,
            channel="email",
            category="operational",
            recipient_contact_path="customer.contact_id",
        )


async def test_placed_renders_the_default_copy_once_a_contact_resolves(session, org_id) -> None:
    await _seed_order_rule(
        session, org_id, event_type=EVT_ORDER_PLACED, code="order_placed", body=PLACED_BODY
    )
    payload = _placed_payload()
    payload["customer"] = {"contact_id": "c0ffee"}
    envelope = _event(org_id, EVT_ORDER_PLACED, payload)

    async def resolver(org: str, contact: str, channel: str) -> str | None:
        assert contact == "c0ffee"
        return "priya@northwind.example.com"

    async with session.begin():
        staged = await services.apply_rules(
            session, organization_id=org_id, envelope=envelope, resolve_target=resolver
        )

    assert len(staged) == 1
    message = staged[0]
    assert message.to_address == "priya@northwind.example.com"
    assert message.body == "Your order ORD-1001 has been placed."
    assert message.state == STATE_PENDING


async def test_accepted_renders_the_default_copy(session, org_id) -> None:
    await _seed_order_rule(
        session, org_id, event_type=EVT_ORDER_ACCEPTED, code="order_accepted", body=ACCEPTED_BODY
    )
    payload = _accepted_payload()
    payload["customer"] = {"contact_id": "c0ffee"}
    envelope = _event(org_id, EVT_ORDER_ACCEPTED, payload)

    async def resolver(org: str, contact: str, channel: str) -> str | None:
        return "priya@northwind.example.com"

    async with session.begin():
        staged = await services.apply_rules(
            session, organization_id=org_id, envelope=envelope, resolve_target=resolver
        )

    assert len(staged) == 1
    assert staged[0].body == "Your order ORD-1001 has been accepted and is being prepared."


async def test_cancelled_includes_the_reason_from_the_real_payload_field(session, org_id) -> None:
    await _seed_order_rule(
        session,
        org_id,
        event_type=EVT_ORDER_CANCELLED,
        code="order_cancelled",
        body=CANCELLED_BODY,
    )
    payload = _cancelled_payload(reason="Out of stock at pick time")
    payload["customer"] = {"contact_id": "c0ffee"}
    envelope = _event(org_id, EVT_ORDER_CANCELLED, payload)

    async def resolver(org: str, contact: str, channel: str) -> str | None:
        return "priya@northwind.example.com"

    async with session.begin():
        staged = await services.apply_rules(
            session, organization_id=org_id, envelope=envelope, resolve_target=resolver
        )

    assert len(staged) == 1
    body = staged[0].body
    assert "ORD-1001" in body
    assert "Out of stock at pick time" in body


async def test_a_rule_driven_order_send_respects_an_opt_out(session, org_id) -> None:
    """ADR-0021: consent is checked on every send path this service has, and a rule-driven fold
    is not a second path that could forget to. Transactional (`category="operational"`, per
    `consent.purposes_for_category`) needs no opt-in, but an explicit opt-out still refuses it."""
    from app import consent

    await _seed_order_rule(
        session, org_id, event_type=EVT_ORDER_PLACED, code="order_placed", body=PLACED_BODY
    )
    address = "priya@northwind.example.com"
    subject = consent.subject_for("email", address)
    async with session.begin():
        await consent.append(
            session,
            organization_id=org_id,
            subject=subject,
            purposes=("transactional",),
            status="opted_out",
            source="admin",
            evidence="test fixture",
        )

    payload = _placed_payload()
    payload["customer"] = {"contact_id": "c0ffee"}
    envelope = _event(org_id, EVT_ORDER_PLACED, payload)

    async def resolver(org: str, contact: str, channel: str) -> str | None:
        return address

    async with session.begin():
        staged = await services.apply_rules(
            session, organization_id=org_id, envelope=envelope, resolve_target=resolver
        )

    assert len(staged) == 1
    assert staged[0].state == STATE_SUPPRESSED
    assert staged[0].suppression_reason == "consent:opted_out"


# --------------------------------------------------------------------- the gap, proven


async def test_todays_real_payload_resolves_no_recipient(session, org_id) -> None:
    """The gap, executable. Fold the *exact* payload `place_order` publishes today — no
    `customer`, no `customer_id`, no phone, no email, nothing a rule could name — through a rule
    that would otherwise fire, and nothing is staged. Not an exception, not a skip that looks like
    success: `apply_rules` returns an empty list, exactly as it does for any event a rule cannot
    reach anyone on (`services.py::apply_rules` docstring). The resolver is never even called."""
    await _seed_order_rule(
        session, org_id, event_type=EVT_ORDER_PLACED, code="order_placed", body=PLACED_BODY
    )
    envelope = _event(org_id, EVT_ORDER_PLACED, _placed_payload())  # unmodified — the real shape

    called = False

    async def resolver(org: str, contact: str, channel: str) -> str | None:
        nonlocal called
        called = True
        return "should-not-be-reached@example.com"

    async with session.begin():
        staged = await services.apply_rules(
            session, organization_id=org_id, envelope=envelope, resolve_target=resolver
        )

    assert staged == []
    assert called is False


async def test_todays_real_cancelled_payload_also_resolves_no_recipient(session, org_id) -> None:
    await _seed_order_rule(
        session,
        org_id,
        event_type=EVT_ORDER_CANCELLED,
        code="order_cancelled",
        body=CANCELLED_BODY,
    )
    envelope = _event(org_id, EVT_ORDER_CANCELLED, _cancelled_payload())

    async with session.begin():
        staged = await services.apply_rules(
            session, organization_id=org_id, envelope=envelope, resolve_target=None
        )

    assert staged == []
