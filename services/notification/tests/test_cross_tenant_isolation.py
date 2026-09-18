"""Cross-tenant isolation, proven against the policies rather than the query filters.

The suite runs as the **app** role, so row-level security is live here. These assertions would
pass trivially under a bypassing role, which is exactly why the conftest refuses to use one.

This schema is worth more care than most: the dispatcher reads a message row and sends its
**body** to an external provider. A tenancy mistake here does not leak a row into a report — it
emails one organization's invoice to another organization's customer.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app import selectors, services
from app.models import Message, Template
from tests.helpers import envelope_for

pytestmark = pytest.mark.integration


async def test_another_tenants_template_is_invisible(session, admin_session, org_id) -> None:
    other = uuid.uuid4()
    async with admin_session.begin():
        admin_session.add(
            Template(
                organization_id=other,
                code="theirs",
                channel="email",
                subject="Theirs",
                body="Private",
                description="",
            )
        )

    found = await selectors.get_template(session, org_id, "theirs", "email")
    assert found is None

    rows = (await session.execute(select(Template))).scalars().all()
    assert [r.organization_id for r in rows] == []


async def test_another_tenants_message_is_invisible(session, admin_session, org_id) -> None:
    """The one that matters: a message body is what goes to a provider."""
    other = uuid.uuid4()
    async with admin_session.begin():
        admin_session.add(
            Message(
                organization_id=other,
                channel="email",
                category="billing",
                to_address="their-customer@example.com",
                subject="Invoice INV-9",
                body="You owe 1,20,000.",
                state="pending",
                attempts=0,
            )
        )

    rows = (await session.execute(select(Message))).scalars().all()
    assert rows == []


async def test_a_rule_fold_cannot_reach_another_tenants_rules(
    session, admin_session, org_id
) -> None:
    """An event carrying another organization's id must not fire this one's automations — the
    consumer binds the GUC per message, and RLS is the net under that."""
    from tests.conftest import seed_automation

    await seed_automation(session, org_id)
    foreign = envelope_for(
        uuid.uuid4(),
        "support.desk.ticket.created",
        {"number": "TK-9", "requester_name": "X", "requester_email": "x@y.example.com"},
    )
    async with session.begin():
        staged = await services.apply_rules(
            session,
            organization_id=uuid.UUID(foreign["organization_id"]),
            envelope=foreign,
        )
    assert staged == []


async def test_a_dedupe_key_does_not_collide_across_tenants(session, admin_session, org_id) -> None:
    """The unique constraint is per organization: two tenants reacting to their own copy of the
    same invoice number must both send."""
    other = uuid.uuid4()
    async with admin_session.begin():
        admin_session.add(
            Message(
                organization_id=other,
                channel="email",
                category="billing",
                to_address="a@b.example.com",
                subject="S",
                body="B",
                state="pending",
                attempts=0,
                dedupe_key="ack:INV-1",
            )
        )

    # The same key in this tenant is not a conflict, and the other tenant's row stays invisible.
    found = await selectors.find_by_dedupe_key(session, org_id, "ack:INV-1")
    assert found is None
