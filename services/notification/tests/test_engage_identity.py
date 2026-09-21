"""Engage sending identity — the connection, its domains and the senders mail goes out as.

These run against real PostgreSQL as the **app** role, like the rest of this service's
integration suite, because two of the three things being asserted here are database behaviour
rather than Python behaviour: the row-level-security policies on the new tables, and the
defaults that make a freshly created sender unusable.

The unusable-by-default assertion is the load-bearing one. `verified_at` is written by exactly
one thing — a test send that actually reached the provider — and a sender that arrived usable
because a column defaulted the wrong way would let a campaign go out through a connection
nobody has ever proven works.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app import models

pytestmark = pytest.mark.integration


async def test_sender_is_unverified_until_a_test_send_succeeds(session, org_id) -> None:
    domain = models.EmailDomain(organization_id=org_id, domain="company.com", status="pending")
    session.add(domain)
    await session.flush()

    sender = models.Sender(
        organization_id=org_id,
        domain_id=domain.id,
        name="Sales",
        address="sales@company.com",
        reply_to="sales@company.com",
    )
    session.add(sender)
    await session.flush()

    assert sender.verified_at is None
    assert sender.usable is False


async def test_a_new_domain_starts_pending_with_no_records(session, org_id) -> None:
    """`records` is filled when the record set is generated, not at insert. A domain that
    arrived with an empty set and a `verified` status would be sendable from before anyone
    published anything."""
    domain = models.EmailDomain(organization_id=org_id, domain="company.com")
    session.add(domain)
    await session.flush()

    assert domain.status == "pending"
    assert domain.records == []
    assert domain.last_checked_at is None


async def test_one_connection_per_organization(session, org_id) -> None:
    """A second connection would make "which one sent this" ambiguous before anything records
    the answer, so the database refuses it rather than the service remembering to."""
    from sqlalchemy.exc import IntegrityError

    session.add(
        models.EmailConnection(organization_id=org_id, kind="google", username="a@b.example.com")
    )
    await session.flush()

    session.add(
        models.EmailConnection(organization_id=org_id, kind="smtp", username="c@d.example.com")
    )
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


async def test_another_tenants_sending_identity_is_invisible(
    session, admin_session, org_id
) -> None:
    """The policies, not the query filters. The suite runs as the app role, so this would be a
    false pass under a bypassing connection — which is why the conftest refuses to use one.

    All three rows matter, for different reasons. The connection holds a sealed credential that
    can send mail as another business. The domain is the set of names they are allowed to send
    as. The sender is what a campaign picks from, so a leaked one is a campaign that goes out
    signed by somebody else's Sales team.
    """
    other = uuid.uuid4()
    async with admin_session.begin():
        their_domain = models.EmailDomain(
            organization_id=other, domain="theirs.example.com", status="verified"
        )
        admin_session.add(their_domain)
        await admin_session.flush()
        admin_session.add(
            models.EmailConnection(
                organization_id=other,
                kind="google",
                host="smtp.gmail.com",
                username="ops@theirs.example.com",
                secret_enc=b"sealed-envelope",
            )
        )
        admin_session.add(
            models.Sender(
                organization_id=other,
                domain_id=their_domain.id,
                name="Their Sales",
                address="sales@theirs.example.com",
            )
        )

    assert (await session.execute(select(models.EmailConnection))).scalars().all() == []
    assert (await session.execute(select(models.EmailDomain))).scalars().all() == []
    assert (await session.execute(select(models.Sender))).scalars().all() == []


async def test_writing_a_sender_into_another_tenant_is_refused(session, org_id) -> None:
    """`WITH CHECK`: USING gates existing rows, but the new row of an INSERT needs its own
    check. Without it this insert would succeed — invisible to the tenant that made it, fully
    visible to the one it was planted on."""
    session.add(
        models.Sender(
            organization_id=uuid.uuid4(),
            domain_id=uuid.uuid4(),
            name="Planted",
            address="planted@example.com",
        )
    )
    with pytest.raises(Exception, match="row-level security"):
        await session.flush()
    await session.rollback()
