"""The WhatsApp inbox API — conversations, threads, replies, assignment, and the audited unmask.

Real PostgreSQL as the app role, like the rest of the integration suite: masking is proven by
reading the response body rather than trusting the assembler, and the window boundary is proven
against the same `webhooks.within_service_window` the reply route actually calls.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app import services
from app.main import app
from app.models import AuditLog, Conversation, InboundMessage, Message
from platform_core.auth import Principal, get_principal
from platform_core.pii import seal
from tests.helpers import principal_for

pytestmark = pytest.mark.integration

BASE = "/api/v1/engage/conversations"

C_READ = "engage.inbox.conversation.read"
C_WRITE = "engage.inbox.conversation.write"
M_REPLY = "engage.inbox.message.reply"
M_UNMASK = "engage.inbox.message.unmask"
ALL = [f"{p}:ORG" for p in (C_READ, C_WRITE, M_REPLY, M_UNMASK)]

# `webhooks.within_service_window` has no injectable clock at the routes this file drives — both
# `services.py` and `api/engage.py` call it without a `now=` override, so it reads the real wall
# clock. NOW has to track that same clock rather than pin a date, or the 24-hour window check
# drifts out of sync with reality and the window-boundary tests fail for a reason that has nothing
# to do with the behaviour they're proving.
NOW = datetime.now(UTC)


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def caller(org_id: uuid.UUID, perms: list[str], *, amr: list[str] | None = None) -> Principal:
    return principal_for(org_id, perms, entitlements=["engage"], amr=amr)


@pytest.fixture(autouse=True)
def _assume_membership_confirmed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every assign test here is about the conversation write, not about account-service's own
    answer — `test_assign_refuses_an_unconfirmed_membership` is the one test that overrides this
    to prove the refusal path, everything else gets a confirmed membership by default."""

    async def _confirmed(*args: object, **kwargs: object) -> bool:
        return True

    monkeypatch.setattr(services.memberships, "membership_is_active", _confirmed)


async def _seed_conversation(
    session,
    org_id: uuid.UUID,
    *,
    last_customer_message_at: datetime | None = NOW,
    body: str = "Is my order ready?",
) -> tuple[Conversation, InboundMessage]:
    from app.config import settings

    cfg = settings()
    enc, masked = seal("+16505551234", kind="phone", settings=cfg)
    body_enc, _ = seal(body, kind="generic", settings=cfg)
    profile_name_enc, _ = seal("Jane Customer", kind="generic", settings=cfg)
    raw_enc, _ = seal(json.dumps({"id": "wamid.SEED1"}), kind="generic", settings=cfg)
    async with session.begin():
        conversation = Conversation(
            organization_id=org_id,
            channel="whatsapp",
            wa_address_enc=enc,
            wa_address_masked=masked,
            wa_address_index="idx-does-not-matter",
            last_customer_message_at=last_customer_message_at,
            status="open",
        )
        session.add(conversation)
        await session.flush()
        message = InboundMessage(
            organization_id=org_id,
            conversation_id=conversation.id,
            provider_message_id="wamid.SEED1",
            type="text",
            body_enc=body_enc,
            # A fixed placeholder, exactly as `services._BODY_MASK_PLACEHOLDER` writes it — never
            # derived from `body`, so a seeded row here matches what the real fold would produce.
            body_masked="•" * 10,
            raw_enc=raw_enc,
            occurred_at=last_customer_message_at or NOW,
            profile_name_enc=profile_name_enc,
            profile_name_masked="J•••",
        )
        session.add(message)
    return conversation, message


# --------------------------------------------------------------------- list & read


async def test_list_conversations_masks_the_address(client, session, org_id) -> None:
    conversation, _ = await _seed_conversation(session, org_id)
    with as_principal(caller(org_id, ALL)):
        response = await client.get(BASE)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["id"] == str(conversation.id)
    assert "16505551234" not in response.text
    assert item["within_service_window"] is True


async def test_get_one_conversation(client, session, org_id) -> None:
    conversation, _ = await _seed_conversation(session, org_id)
    with as_principal(caller(org_id, ALL)):
        response = await client.get(f"{BASE}/{conversation.id}")
    assert response.status_code == 200
    assert response.json()["id"] == str(conversation.id)


async def test_a_conversation_from_another_org_is_not_found(client, session, org_id) -> None:
    other_conversation_id = uuid.uuid4()
    with as_principal(caller(org_id, ALL)):
        response = await client.get(f"{BASE}/{other_conversation_id}")
    assert response.status_code == 404


async def test_thread_masks_inbound_and_shows_outbound_in_full(client, session, org_id) -> None:
    conversation, _ = await _seed_conversation(session, org_id, body="my card number is secret")
    async with session.begin():
        session.add(
            Message(
                organization_id=org_id,
                channel="whatsapp",
                category="conversational",
                to_address="+16505551234",
                body="Thanks, we'll check on that.",
                state="pending",
                conversation_id=conversation.id,
                direction="outbound",
            )
        )

    with as_principal(caller(org_id, ALL)):
        response = await client.get(f"{BASE}/{conversation.id}/messages")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 2
    inbound = next(i for i in items if i["direction"] == "inbound")
    outbound = next(i for i in items if i["direction"] == "outbound")
    assert "secret" not in inbound["body"]
    assert outbound["body"] == "Thanks, we'll check on that."


# --------------------------------------------------------------------- reply


async def test_a_reply_inside_the_window_is_staged(client, session, org_id) -> None:
    conversation, _ = await _seed_conversation(session, org_id)
    with as_principal(caller(org_id, ALL)):
        response = await client.post(
            f"{BASE}/{conversation.id}/reply",
            json={"body": "On it now", "idempotency_key": "k1"},
        )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["state"] == "pending"

    async with session.begin():
        message = (
            await session.execute(select(Message).where(Message.conversation_id == conversation.id))
        ).scalar_one()
    assert message.body == "On it now"
    assert message.direction == "outbound"
    assert message.channel == "whatsapp"


async def test_a_repeated_idempotency_key_does_not_send_twice(client, session, org_id) -> None:
    conversation, _ = await _seed_conversation(session, org_id)
    with as_principal(caller(org_id, ALL)):
        first = await client.post(
            f"{BASE}/{conversation.id}/reply", json={"body": "hi", "idempotency_key": "same"}
        )
        second = await client.post(
            f"{BASE}/{conversation.id}/reply", json={"body": "hi", "idempotency_key": "same"}
        )
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["message_id"] == second.json()["message_id"]

    async with session.begin():
        count = (
            (
                await session.execute(
                    select(Message).where(Message.conversation_id == conversation.id)
                )
            )
            .scalars()
            .all()
        )
    assert len(count) == 1


async def test_a_reply_outside_the_window_is_refused_with_the_template_remedy(
    client, session, org_id
) -> None:
    stale = NOW - timedelta(hours=25)
    conversation, _ = await _seed_conversation(session, org_id, last_customer_message_at=stale)
    # Captured before the call that refuses: a 409 rolls the shared session back, which expires
    # every attribute of every object it holds — `conversation.id` read afterward would try to
    # reload itself from the database outside of any async context and crash with
    # `MissingGreenlet`, not with the assertion this test is actually about.
    conversation_id = conversation.id
    with as_principal(caller(org_id, ALL)):
        response = await client.post(
            f"{BASE}/{conversation_id}/reply",
            json={"body": "too late", "idempotency_key": "k1"},
        )
    assert response.status_code == 409
    assert response.json()["remedy"] == "template"

    async with session.begin():
        count = (
            (
                await session.execute(
                    select(Message).where(Message.conversation_id == conversation_id)
                )
            )
            .scalars()
            .all()
        )
    assert count == []


async def test_a_conversation_that_has_never_heard_from_the_customer_cannot_be_replied_to(
    client, session, org_id
) -> None:
    conversation, _ = await _seed_conversation(session, org_id, last_customer_message_at=None)
    with as_principal(caller(org_id, ALL)):
        response = await client.post(
            f"{BASE}/{conversation.id}/reply",
            json={"body": "hello?", "idempotency_key": "k1"},
        )
    assert response.status_code == 409


# --------------------------------------------------------------------- assign & close


async def test_assign_and_close(client, session, org_id) -> None:
    conversation, _ = await _seed_conversation(session, org_id)
    membership_id = uuid.uuid4()
    with as_principal(caller(org_id, ALL)):
        assigned = await client.post(
            f"{BASE}/{conversation.id}:assign",
            json={"assignee_membership_id": str(membership_id)},
        )
        closed = await client.post(f"{BASE}/{conversation.id}:close")
    assert assigned.status_code == 200
    assert assigned.json()["assignee_membership_id"] == str(membership_id)
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"


async def test_a_reply_reopens_a_closed_conversation(client, session, org_id) -> None:
    conversation, _ = await _seed_conversation(session, org_id)
    with as_principal(caller(org_id, ALL)):
        await client.post(f"{BASE}/{conversation.id}:close")
        reply = await client.post(
            f"{BASE}/{conversation.id}/reply", json={"body": "hi", "idempotency_key": "k1"}
        )
        after = await client.get(f"{BASE}/{conversation.id}")
    assert reply.status_code == 202
    assert after.json()["status"] == "open"


# --------------------------------------------------------------------- unmask


async def test_unmask_requires_the_permission_and_the_step_up(client, session, org_id) -> None:
    _, message = await _seed_conversation(session, org_id, body="the real words")
    # Captured before either call: a 403 dependency failure still rolls the shared session back
    # (see the window-boundary test above for why that matters), and the second call must not
    # re-read an expired ORM attribute to build its own URL.
    conversation_id = message.conversation_id
    message_id = message.id

    # Holds the permission but never stepped up.
    with as_principal(caller(org_id, [f"{M_UNMASK}:ORG"])):
        no_step_up = await client.post(f"{BASE}/{conversation_id}/messages/{message_id}:unmask")
    assert no_step_up.status_code == 403

    with as_principal(caller(org_id, [f"{M_UNMASK}:ORG"], amr=["otp"])):
        revealed = await client.post(f"{BASE}/{conversation_id}/messages/{message_id}:unmask")
    assert revealed.status_code == 200
    assert revealed.json()["body"] == "the real words"


async def test_unmasking_writes_an_audit_row_every_time(client, session, org_id) -> None:
    _, message = await _seed_conversation(session, org_id)
    with as_principal(caller(org_id, [f"{M_UNMASK}:ORG"], amr=["otp"])):
        await client.post(f"{BASE}/{message.conversation_id}/messages/{message.id}:unmask")
        await client.post(f"{BASE}/{message.conversation_id}/messages/{message.id}:unmask")

    async with session.begin():
        rows = (
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "entity.sensitive_read")
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 2


async def test_conversation_read_never_returns_the_unmasked_body(client, session, org_id) -> None:
    """The thread read and the unmask reveal never travel in the same response."""
    conversation, _message = await _seed_conversation(session, org_id, body="the real words")
    with as_principal(caller(org_id, ALL)):
        response = await client.get(f"{BASE}/{conversation.id}/messages")
    assert "the real words" not in response.text


# --------------------------------------------------------------------- negative authz


READ_ROUTES = [
    ("GET", BASE),
]


@pytest.mark.parametrize(("method", "path"), READ_ROUTES)
async def test_reads_refuse_a_caller_without_the_permission(client, org_id, method, path) -> None:
    with as_principal(caller(org_id, [])):
        response = await client.request(method, path)
    assert response.status_code == 403


@pytest.mark.parametrize(("method", "path"), READ_ROUTES)
async def test_reads_refuse_a_caller_without_the_entitlement(client, org_id, method, path) -> None:
    with as_principal(principal_for(org_id, [f"{C_READ}:ORG"], entitlements=[])):
        response = await client.request(method, path)
    assert response.status_code == 402


async def test_reply_refuses_a_caller_with_only_read(client, session, org_id) -> None:
    conversation, _ = await _seed_conversation(session, org_id)
    with as_principal(caller(org_id, [f"{C_READ}:ORG"])):
        response = await client.post(
            f"{BASE}/{conversation.id}/reply", json={"body": "hi", "idempotency_key": "k1"}
        )
    assert response.status_code == 403


async def test_assign_refuses_a_caller_with_only_read(client, session, org_id) -> None:
    conversation, _ = await _seed_conversation(session, org_id)
    with as_principal(caller(org_id, [f"{C_READ}:ORG"])):
        response = await client.post(
            f"{BASE}/{conversation.id}:assign", json={"assignee_membership_id": None}
        )
    assert response.status_code == 403


async def test_close_refuses_a_caller_with_only_read(client, session, org_id) -> None:
    conversation, _ = await _seed_conversation(session, org_id)
    with as_principal(caller(org_id, [f"{C_READ}:ORG"])):
        response = await client.post(f"{BASE}/{conversation.id}:close")
    assert response.status_code == 403


async def test_unmask_refuses_a_caller_with_only_conversation_read(client, session, org_id) -> None:
    _, message = await _seed_conversation(session, org_id)
    with as_principal(caller(org_id, [f"{C_READ}:ORG"], amr=["otp"])):
        response = await client.post(
            f"{BASE}/{message.conversation_id}/messages/{message.id}:unmask"
        )
    assert response.status_code == 403


# --------------------------------------------------------------------- assign target validation


async def test_assign_refuses_an_unconfirmed_membership(
    client, session, org_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    """account-service says no (wrong organization, removed, or simply unreachable) — the
    conversation must not be written to at all."""

    async def _unconfirmed(*args: object, **kwargs: object) -> bool:
        return False

    monkeypatch.setattr(services.memberships, "membership_is_active", _unconfirmed)

    conversation, _ = await _seed_conversation(session, org_id)
    # Captured before the call that refuses: a 422 rolls the shared session back, which expires
    # every attribute of every object it holds (see the window-boundary test's comment for why).
    conversation_id = conversation.id
    membership_id = uuid.uuid4()
    with as_principal(caller(org_id, ALL)):
        response = await client.post(
            f"{BASE}/{conversation_id}:assign",
            json={"assignee_membership_id": str(membership_id)},
        )
    assert response.status_code == 422

    async with session.begin():
        row = (
            await session.execute(select(Conversation).where(Conversation.id == conversation_id))
        ).scalar_one()
    assert row.assignee_membership_id is None


async def test_clearing_an_assignment_needs_no_membership_check(
    client, session, org_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    """There is no id to misroute to, so `assignee_membership_id: null` never calls out."""

    async def _must_not_be_called(*args: object, **kwargs: object) -> bool:
        raise AssertionError("membership_is_active must not be called when clearing an assignment")

    monkeypatch.setattr(services.memberships, "membership_is_active", _must_not_be_called)

    conversation, _ = await _seed_conversation(session, org_id)
    with as_principal(caller(org_id, ALL)):
        response = await client.post(
            f"{BASE}/{conversation.id}:assign", json={"assignee_membership_id": None}
        )
    assert response.status_code == 200


# --------------------------------------------------------------------- cross-tenant isolation


async def _seed_for_other_org(admin_session, other_org: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """Another organization's conversation and message, written on the BYPASSRLS session — never
    through the app role, which is exactly the boundary these tests exist to prove holds."""
    from app.config import settings

    cfg = settings()
    enc, masked = seal("+19995550000", kind="phone", settings=cfg)
    body_enc, _ = seal("theirs", kind="generic", settings=cfg)
    raw_enc, _ = seal(json.dumps({"id": "wamid.THEIRS"}), kind="generic", settings=cfg)
    async with admin_session.begin():
        conversation = Conversation(
            organization_id=other_org,
            channel="whatsapp",
            wa_address_enc=enc,
            wa_address_masked=masked,
            wa_address_index="idx-theirs",
            last_customer_message_at=NOW,
            status="open",
        )
        admin_session.add(conversation)
        await admin_session.flush()
        message = InboundMessage(
            organization_id=other_org,
            conversation_id=conversation.id,
            provider_message_id="wamid.THEIRS",
            type="text",
            body_enc=body_enc,
            body_masked="•" * 10,
            raw_enc=raw_enc,
            occurred_at=NOW,
        )
        admin_session.add(message)
        await admin_session.flush()
    return conversation.id, message.id


async def test_a_conversation_in_another_org_is_not_found_on_every_route(
    client, admin_session, org_id
) -> None:
    other_org = uuid.uuid4()
    conversation_id, message_id = await _seed_for_other_org(admin_session, other_org)

    # `amr=["otp"]` so the unmask route's own step-up gate never fires first — this test is about
    # tenant scoping, not about the permission dependency, and every route here must answer the
    # same "not found" regardless of which gate would otherwise apply.
    with as_principal(caller(org_id, ALL, amr=["otp"])):
        detail = await client.get(f"{BASE}/{conversation_id}")
        thread = await client.get(f"{BASE}/{conversation_id}/messages")
        reply = await client.post(
            f"{BASE}/{conversation_id}/reply", json={"body": "hi", "idempotency_key": "k1"}
        )
        assign = await client.post(
            f"{BASE}/{conversation_id}:assign", json={"assignee_membership_id": None}
        )
        close = await client.post(f"{BASE}/{conversation_id}:close")
        unmask = await client.post(f"{BASE}/{conversation_id}/messages/{message_id}:unmask")

    assert detail.status_code == 404
    assert thread.status_code == 404
    assert reply.status_code == 404
    assert assign.status_code == 404
    assert close.status_code == 404
    assert unmask.status_code == 404

    # Nothing this organization's caller did leaked into the other tenant's rows: still open,
    # still unassigned, still exactly what `_seed_for_other_org` wrote, and no audit trail this
    # organization's own caller has no business having produced against it.
    async with admin_session.begin():
        theirs = (
            await admin_session.execute(
                select(Conversation).where(Conversation.id == conversation_id)
            )
        ).scalar_one()
        their_messages = (
            (
                await admin_session.execute(
                    select(InboundMessage).where(InboundMessage.conversation_id == conversation_id)
                )
            )
            .scalars()
            .all()
        )
        their_audit_rows = (
            (
                await admin_session.execute(
                    select(AuditLog).where(AuditLog.organization_id == other_org)
                )
            )
            .scalars()
            .all()
        )
    assert theirs.status == "open"
    assert theirs.assignee_membership_id is None
    assert len(their_messages) == 1
    assert their_audit_rows == []
