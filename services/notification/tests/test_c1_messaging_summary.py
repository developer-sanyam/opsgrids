"""`GET /internal/v1/contacts/{contact_id}/messaging-summary` (slice C1).

Service-to-service: a token addressed to notification by customer-service, whose `org` claim is
the tenant. The response is facts and timestamps only — these tests pin that no address, phone or
email, ever comes back, and that another tenant's conversations are invisible.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app import consent
from app.c1_messaging_summary import messaging_summary
from app.config import settings
from app.models import Conversation, InboundMessage, Message, ProviderAccount
from platform_core.internal_auth import mint_service_token
from platform_core.pii import seal

pytestmark = pytest.mark.integration

PHONE = "+16505551234"
EMAIL = "rahul.sharma@example.com"


def _token(
    org: uuid.UUID | None, *, caller: str = "customer", audience: str = "notification"
) -> str:
    claims = {"org": str(org)} if org else {}
    return mint_service_token(settings(), audience=audience, caller=caller, claims=claims)


def _url(contact_id: uuid.UUID) -> str:
    return f"/internal/v1/contacts/{contact_id}/messaging-summary"


async def _seed_conversation(
    session: AsyncSession,
    org: uuid.UUID,
    contact_id: uuid.UUID,
    *,
    last_customer_message_at: datetime | None,
    phone: str = PHONE,
) -> uuid.UUID:
    enc, masked = seal(phone, kind="phone", settings=settings())
    body_enc, _ = seal("hello", kind="generic", settings=settings())
    conversation = Conversation(
        organization_id=org,
        channel="whatsapp",
        wa_address_enc=enc,
        wa_address_masked=masked,
        wa_address_index=consent.index_for("whatsapp", phone),
        status="open",
        contact_id=contact_id,
        last_customer_message_at=last_customer_message_at,
    )
    async with session.begin():
        session.add(conversation)
        await session.flush()
        if last_customer_message_at is not None:
            session.add(
                InboundMessage(
                    organization_id=org,
                    conversation_id=conversation.id,
                    provider_message_id=f"wamid.{uuid.uuid4().hex}",
                    type="text",
                    body_enc=body_enc,
                    body_masked="•" * 10,
                    raw_enc=body_enc,
                    occurred_at=last_customer_message_at,
                )
            )
    return conversation.id


async def _opt_in(session: AsyncSession, org: uuid.UUID, channel: str, address: str) -> None:
    async with session.begin():
        await consent.append(
            session,
            organization_id=org,
            subject=consent.subject_for(channel, address),
            purposes=[consent.MARKETING],
            status="opted_in",
            source="admin",
            evidence="seeded by test",
        )


class TestAuthentication:
    async def test_no_token_is_rejected(self, client, org_id) -> None:
        assert (await client.get(_url(uuid.uuid4()))).status_code == 401

    async def test_a_token_for_another_service_is_rejected(self, client, org_id) -> None:
        response = await client.get(
            _url(uuid.uuid4()), headers={"X-Internal-Token": _token(org_id, audience="customer")}
        )
        assert response.status_code == 401

    async def test_a_valid_token_from_another_caller_is_forbidden(self, client, org_id) -> None:
        response = await client.get(
            _url(uuid.uuid4()), headers={"X-Internal-Token": _token(org_id, caller="lead")}
        )
        assert response.status_code == 403

    async def test_a_token_naming_no_organization_is_forbidden(self, client, org_id) -> None:
        response = await client.get(_url(uuid.uuid4()), headers={"X-Internal-Token": _token(None)})
        assert response.status_code == 403

    async def test_a_user_bearer_token_does_not_open_it(self, client, org_id) -> None:
        response = await client.get(_url(uuid.uuid4()), headers={"Authorization": "Bearer x"})
        assert response.status_code == 401


class TestWhatsApp:
    async def test_an_open_window_and_an_opt_in(self, client, session, org_id) -> None:
        contact = uuid.uuid4()
        now = datetime.now(UTC)
        heard = now - timedelta(hours=2)
        await _seed_conversation(session, org_id, contact, last_customer_message_at=heard)
        await _opt_in(session, org_id, "whatsapp", PHONE)
        sent = now - timedelta(hours=1)
        async with session.begin():
            session.add(
                Message(
                    organization_id=org_id,
                    channel="whatsapp",
                    to_address=PHONE,
                    body="reply",
                    state="sent",
                    direction="outbound",
                    sent_at=sent,
                    conversation_id=(
                        await session.execute(
                            Conversation.__table__.select().with_only_columns(Conversation.id)
                        )
                    ).scalar_one(),
                )
            )

        response = await client.get(_url(contact), headers={"X-Internal-Token": _token(org_id)})
        assert response.status_code == 200, response.text
        wa = response.json()["whatsapp"]
        assert wa["opted_in"] is True
        assert wa["conversations"] == 1
        assert datetime.fromisoformat(wa["window_open_until"]) == heard + timedelta(hours=24)
        assert datetime.fromisoformat(wa["last_message_at"]) == sent
        assert response.json()["email"] is None

    async def test_a_lapsed_window_is_null_and_no_consent_is_not_opted_in(
        self, client, session, org_id
    ) -> None:
        contact = uuid.uuid4()
        await _seed_conversation(
            session, org_id, contact, last_customer_message_at=datetime.now(UTC) - timedelta(days=3)
        )
        response = await client.get(_url(contact), headers={"X-Internal-Token": _token(org_id)})
        wa = response.json()["whatsapp"]
        assert wa["window_open_until"] is None
        assert wa["opted_in"] is False
        assert wa["last_message_at"] is not None

    async def test_not_connected_and_no_history_is_null(self, client, org_id) -> None:
        response = await client.get(
            _url(uuid.uuid4()), headers={"X-Internal-Token": _token(org_id)}
        )
        assert response.status_code == 200
        assert response.json() == {"whatsapp": None, "email": None}

    async def test_a_connected_channel_with_no_history_is_zeroes_not_null(
        self, client, session, org_id
    ) -> None:
        async with session.begin():
            session.add(
                ProviderAccount(
                    organization_id=org_id,
                    channel="whatsapp",
                    waba_id="w",
                    phone_number_id="p",
                    display_number="+1 555",
                    status="connected",
                )
            )
        response = await client.get(
            _url(uuid.uuid4()), headers={"X-Internal-Token": _token(org_id)}
        )
        assert response.json()["whatsapp"] == {
            "opted_in": False,
            "window_open_until": None,
            "last_message_at": None,
            "conversations": 0,
        }


class TestEmail:
    async def test_subscription_and_last_email(self, client, session, org_id) -> None:
        await _opt_in(session, org_id, "email", EMAIL)
        sent = datetime.now(UTC) - timedelta(days=2)
        async with session.begin():
            session.add(
                Message(
                    organization_id=org_id,
                    channel="email",
                    # As every writer stores it (`domain.normalize_recipient`: trimmed, lowered).
                    to_address=EMAIL,
                    subject="Hi",
                    body="b",
                    state="sent",
                    direction="outbound",
                    sent_at=sent,
                )
            )
        response = await client.get(
            _url(uuid.uuid4()),
            # The caller's casing must not matter: the address is normalized before the lookup.
            headers={"X-Internal-Token": _token(org_id), "X-Contact-Email": f"  {EMAIL.upper()} "},
        )
        email = response.json()["email"]
        assert email["subscribed"] is True
        assert datetime.fromisoformat(email["last_email_at"]) == sent

    async def test_the_last_email_lookup_leaves_the_address_column_bare(
        self, session, org_id
    ) -> None:
        """`func.lower(to_address)` cannot use `ix_messages_org_channel_recipient` (org, channel,
        to_address), so every Customer 360 view scanned the org's email. The column is compared as
        stored, which the writers guarantee is already normalized."""
        statements: list[str] = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
            statements.append(statement)

        event.listen(session.bind.sync_engine, "before_cursor_execute", capture)
        try:
            async with session.begin():
                await messaging_summary(session, org_id, uuid.uuid4(), email_address=EMAIL)
        finally:
            event.remove(session.bind.sync_engine, "before_cursor_execute", capture)

        (lookup,) = [s for s in statements if "messages.sent_at" in s and "max(" in s]
        assert "lower(" not in lookup.lower()
        assert "messages.to_address = " in lookup

    async def test_an_address_never_seen_is_not_subscribed(self, client, org_id) -> None:
        response = await client.get(
            _url(uuid.uuid4()),
            headers={"X-Internal-Token": _token(org_id), "X-Contact-Email": EMAIL},
        )
        assert response.json()["email"] == {"subscribed": False, "last_email_at": None}

    async def test_a_malformed_address_is_null_not_an_error(self, client, org_id) -> None:
        response = await client.get(
            _url(uuid.uuid4()),
            headers={"X-Internal-Token": _token(org_id), "X-Contact-Email": "not-an-email"},
        )
        assert response.status_code == 200
        assert response.json()["email"] is None


class TestTenantIsolationAndMasking:
    async def test_another_tenants_conversations_are_invisible(
        self, client, admin_session, org_id
    ) -> None:
        other_org, contact = uuid.uuid4(), uuid.uuid4()
        await _seed_conversation(
            admin_session,
            other_org,
            contact,
            last_customer_message_at=datetime.now(UTC),
        )
        mine = await client.get(_url(contact), headers={"X-Internal-Token": _token(org_id)})
        assert mine.status_code == 200
        assert mine.json() == {"whatsapp": None, "email": None}
        # The same id, asked under the owning tenant's token, does find it: the isolation is the
        # tenant, not an absent row.
        theirs = await client.get(_url(contact), headers={"X-Internal-Token": _token(other_org)})
        assert theirs.json()["whatsapp"]["conversations"] == 1

    async def test_no_address_is_ever_returned(self, client, session, org_id) -> None:
        contact = uuid.uuid4()
        await _seed_conversation(
            session, org_id, contact, last_customer_message_at=datetime.now(UTC)
        )
        await _opt_in(session, org_id, "email", EMAIL)
        response = await client.get(
            _url(contact),
            headers={"X-Internal-Token": _token(org_id), "X-Contact-Email": EMAIL},
        )
        text = response.text
        assert "@" not in text
        assert "6505551234" not in text and "example.com" not in text
