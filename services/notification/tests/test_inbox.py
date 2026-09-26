"""The per-user in-app notification feed: `GET /notifications/me` and its two mark-read routes.

Two things this suite has to prove that the rest of the service's tests do not: that a feed is
scoped to the caller's own membership, not merely to the organization (an ORG-scoped reader of
`/notifications/messages` legitimately sees every recipient's mail; this feed must not), and that
the existing rule engine can produce an in-app delivery with no code of its own — `domain.CHANNELS`
and `transports.InAppTransport` are the whole of what changed to make that true.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from app import domain, services
from app.main import app
from app.models import Message
from app.transports import InAppTransport, Outgoing
from platform_core.auth import Principal, Scope, get_principal
from tests.helpers import ctx_for, envelope_for, principal_for

pytestmark = pytest.mark.integration


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


async def _seed(
    session,
    *,
    org_id: uuid.UUID,
    recipient_id: uuid.UUID,
    body: str = "Body",
    state: str = domain.STATE_SENT,
    created_at: datetime | None = None,
    read_at: datetime | None = None,
) -> Message:
    message = Message(
        organization_id=org_id,
        channel=domain.IN_APP_CHANNEL,
        category="operational",
        to_address=str(recipient_id),
        subject="Subject",
        body=body,
        state=state,
        attempts=0,
        read_at=read_at,
    )
    session.add(message)
    await session.flush()
    if created_at is not None:
        message.created_at = created_at
        await session.flush()
    return message


# --------------------------------------------------------------------- authn / authz


async def test_the_feed_refuses_a_caller_with_no_token(client, org_id) -> None:
    response = await client.get("/api/v1/notifications/me")
    assert response.status_code == 401


async def test_the_feed_refuses_a_caller_without_the_entitlement(client, org_id) -> None:
    membership_id = uuid.uuid4()
    with as_principal(principal_for(org_id, [], entitlements=[], membership_id=membership_id)):
        response = await client.get("/api/v1/notifications/me")
    assert response.status_code == 402


async def test_a_membership_less_caller_has_no_feed(client, org_id) -> None:
    """An operator token: staff hold no membership, so this route is not for them."""
    with as_principal(principal_for(org_id, [], membership_id=None)):
        response = await client.get("/api/v1/notifications/me")
    assert response.status_code == 403


# --------------------------------------------------------------------- the feed itself


async def test_the_feed_lists_only_the_callers_own_notifications(session, client, org_id) -> None:
    mine = uuid.uuid4()
    someone_elses = uuid.uuid4()
    async with session.begin():
        await _seed(session, org_id=org_id, recipient_id=mine, body="Mine")
        await _seed(session, org_id=org_id, recipient_id=someone_elses, body="Theirs")

    with as_principal(principal_for(org_id, [], membership_id=mine)):
        response = await client.get("/api/v1/notifications/me")
    assert response.status_code == 200
    body = response.json()
    assert [item["body"] for item in body["items"]] == ["Mine"]


async def test_the_feed_excludes_undelivered_and_failed_messages(session, client, org_id) -> None:
    mine = uuid.uuid4()
    async with session.begin():
        await _seed(session, org_id=org_id, recipient_id=mine, body="Pending", state="pending")
        await _seed(session, org_id=org_id, recipient_id=mine, body="Failed", state="failed")
        await _seed(
            session, org_id=org_id, recipient_id=mine, body="Suppressed", state="suppressed"
        )
        await _seed(session, org_id=org_id, recipient_id=mine, body="Sent", state=domain.STATE_SENT)

    with as_principal(principal_for(org_id, [], membership_id=mine)):
        response = await client.get("/api/v1/notifications/me")
    assert [item["body"] for item in response.json()["items"]] == ["Sent"]


async def test_unread_only_narrows_the_feed(session, client, org_id) -> None:
    mine = uuid.uuid4()
    async with session.begin():
        await _seed(
            session, org_id=org_id, recipient_id=mine, body="Read", read_at=datetime.now(UTC)
        )
        await _seed(session, org_id=org_id, recipient_id=mine, body="Unread")

    with as_principal(principal_for(org_id, [], membership_id=mine)):
        response = await client.get("/api/v1/notifications/me", params={"unread_only": "true"})
    assert [item["body"] for item in response.json()["items"]] == ["Unread"]
    assert response.json()["unread_count"] == 1


async def test_the_feed_is_newest_first_and_pages_by_cursor(session, client, org_id) -> None:
    mine = uuid.uuid4()
    base = datetime.now(UTC)
    async with session.begin():
        for i in range(3):
            await _seed(
                session,
                org_id=org_id,
                recipient_id=mine,
                body=f"msg-{i}",
                created_at=base + timedelta(seconds=i),
            )

    with as_principal(principal_for(org_id, [], membership_id=mine)):
        first = await client.get("/api/v1/notifications/me", params={"limit": 2})
        assert [item["body"] for item in first.json()["items"]] == ["msg-2", "msg-1"]
        cursor = first.json()["next_cursor"]
        assert cursor is not None

        second = await client.get("/api/v1/notifications/me", params={"limit": 2, "cursor": cursor})
    assert [item["body"] for item in second.json()["items"]] == ["msg-0"]
    assert second.json()["next_cursor"] is None


async def test_an_invalid_cursor_is_a_422(client, org_id) -> None:
    mine = uuid.uuid4()
    with as_principal(principal_for(org_id, [], membership_id=mine)):
        response = await client.get(
            "/api/v1/notifications/me", params={"cursor": "not-a-real-cursor"}
        )
    assert response.status_code == 422


# --------------------------------------------------------------------- marking read


async def test_marking_one_notification_read(session, client, org_id) -> None:
    mine = uuid.uuid4()
    async with session.begin():
        message = await _seed(session, org_id=org_id, recipient_id=mine)

    with as_principal(principal_for(org_id, [], membership_id=mine)):
        response = await client.post(f"/api/v1/notifications/me/{message.id}:read")
    assert response.status_code == 200
    body = response.json()
    assert body["read_at"] is not None

    with as_principal(principal_for(org_id, [], membership_id=mine)):
        again = await client.get("/api/v1/notifications/me", params={"unread_only": "true"})
    assert again.json()["items"] == []


async def test_marking_one_notification_read_is_idempotent(session, client, org_id) -> None:
    mine = uuid.uuid4()
    async with session.begin():
        message = await _seed(session, org_id=org_id, recipient_id=mine)

    with as_principal(principal_for(org_id, [], membership_id=mine)):
        first = await client.post(f"/api/v1/notifications/me/{message.id}:read")
        second = await client.post(f"/api/v1/notifications/me/{message.id}:read")
    assert first.status_code == second.status_code == 200
    assert first.json()["read_at"] == second.json()["read_at"]


async def test_marking_someone_elses_notification_read_is_a_404(session, client, org_id) -> None:
    """Not a 403: `to_address` is part of the lookup itself, so a foreign id looks exactly like
    one that does not exist — nothing here confirms it belongs to anybody in particular."""
    mine = uuid.uuid4()
    someone_elses = uuid.uuid4()
    async with session.begin():
        theirs = await _seed(session, org_id=org_id, recipient_id=someone_elses)

    with as_principal(principal_for(org_id, [], membership_id=mine)):
        response = await client.post(f"/api/v1/notifications/me/{theirs.id}:read")
    assert response.status_code == 404


async def test_marking_an_unknown_id_read_is_a_404(client, org_id) -> None:
    mine = uuid.uuid4()
    with as_principal(principal_for(org_id, [], membership_id=mine)):
        response = await client.post(f"/api/v1/notifications/me/{uuid.uuid4()}:read")
    assert response.status_code == 404


async def test_marking_every_notification_read(session, client, org_id) -> None:
    mine = uuid.uuid4()
    someone_elses = uuid.uuid4()
    async with session.begin():
        await _seed(session, org_id=org_id, recipient_id=mine, body="one")
        await _seed(session, org_id=org_id, recipient_id=mine, body="two")
        await _seed(session, org_id=org_id, recipient_id=someone_elses, body="not mine")

    with as_principal(principal_for(org_id, [], membership_id=mine)):
        response = await client.post("/api/v1/notifications/me:read-all")
        assert response.status_code == 200
        assert response.json()["updated"] == 2

        after = await client.get("/api/v1/notifications/me", params={"unread_only": "true"})
    assert after.json()["items"] == []

    with as_principal(principal_for(org_id, [], membership_id=someone_elses)):
        theirs = await client.get("/api/v1/notifications/me", params={"unread_only": "true"})
    assert len(theirs.json()["items"]) == 1


# --------------------------------------------------------------------- cross-tenant isolation


async def test_another_tenants_in_app_message_is_invisible(session, admin_session, org_id) -> None:
    """Same recipient id, different organization — RLS is the net under the tenant filter."""
    from app import inbox

    shared_membership = uuid.uuid4()
    other_org = uuid.uuid4()
    async with admin_session.begin():
        admin_session.add(
            Message(
                organization_id=other_org,
                channel=domain.IN_APP_CHANNEL,
                category="operational",
                to_address=str(shared_membership),
                subject="Theirs",
                body="Not yours",
                state=domain.STATE_SENT,
                attempts=0,
            )
        )

    page = await inbox.list_for_recipient(
        session,
        organization_id=org_id,
        recipient_id=shared_membership,
        limit=25,
        cursor=None,
        unread_only=False,
    )
    assert page.items == []


# --------------------------------------------------------------------- the rule engine, end to end


async def test_a_rule_can_stage_and_deliver_an_in_app_notification(session, org_id) -> None:
    """Proves the existing automation engine needs no changes of its own: `domain.CHANNELS`
    accepting `in_app` and `transports.InAppTransport` are the whole of what this channel added.
    `services.upsert_template`/`upsert_rule`/`apply_rules`/`claim_for_send`/`record_sent` are all
    called exactly as `email` already calls them."""
    recipient = uuid.uuid4()
    write = ctx_for(org_id, "notification.delivery.template.write", Scope.ORG)

    async with session.begin():
        await services.upsert_template(
            session,
            write,
            code="assigned_to_you",
            channel=domain.IN_APP_CHANNEL,
            subject=None,
            body="{{ticket_number}} was assigned to you",
        )
        await services.upsert_rule(
            session,
            write,
            code="notify_assignee_in_app",
            event_type="support.desk.ticket.assigned",
            template_code="assigned_to_you",
            channel=domain.IN_APP_CHANNEL,
            category="operational",
            recipient_literal=str(recipient),
        )

    envelope = envelope_for(org_id, "support.desk.ticket.assigned", {"ticket_number": "TK-42"})
    async with session.begin():
        staged = await services.apply_rules(session, organization_id=org_id, envelope=envelope)
    assert len(staged) == 1
    message = staged[0]
    assert message.channel == domain.IN_APP_CHANNEL
    assert message.to_address == str(recipient)
    assert message.state == domain.STATE_PENDING

    async with session.begin():
        claimed = await services.claim_for_send(
            session, organization_id=org_id, message_id=message.id
        )
        assert claimed is not None
        receipt = await InAppTransport().send(
            Outgoing(to_address=claimed.to_address, subject=claimed.subject, body=claimed.body)
        )
        await services.record_sent(
            session,
            organization_id=org_id,
            message_id=message.id,
            provider=receipt.provider,
            provider_message_id=receipt.provider_message_id,
        )

    from app import inbox

    page = await inbox.list_for_recipient(
        session,
        organization_id=org_id,
        recipient_id=recipient,
        limit=25,
        cursor=None,
        unread_only=False,
    )
    assert [item.body for item in page.items] == ["TK-42 was assigned to you"]
