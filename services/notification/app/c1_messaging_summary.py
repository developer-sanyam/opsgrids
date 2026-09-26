"""What this service knows about one CRM contact's messaging — the `channels` half of the
customer 360 (slice C1).

Customer-service aggregates the 360; this is the one part only notification can answer, because
conversations, the consent ledger and sent messages live in this schema. It returns facts and
timestamps, never an address: the summary is derived from blind indexes and counters, so there is
nothing here to unmask and no sensitive-read to audit.

Two things are looked up, and they are looked up differently on purpose.

WhatsApp is keyed by the **contact id**. A conversation carries a plain `contact_id` once its
sender was resolved to a CRM contact (`app/contacts.py`), so the whole channel is answered from
this schema's own rows. Consent for it is read by the blind index of each conversation's number,
plus any ledger row that names the contact directly.

Email has no such link — an audience `Contact` here is a different id space from the CRM's — so
the caller supplies the address in a header and this service derives its own blind index from it.
The address is used for two equality lookups and is never logged, stored or echoed. When the
caller has no address the email half is `None`: unknown, not "not subscribed".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import consent, domain
from app.models import (
    ConsentEvent,
    Conversation,
    InboundMessage,
    Message,
    ProviderAccount,
)
from app.webhooks import SERVICE_WINDOW


@dataclass(frozen=True)
class WhatsAppSummary:
    opted_in: bool
    window_open_until: datetime | None
    last_message_at: datetime | None
    conversations: int


@dataclass(frozen=True)
class EmailSummary:
    subscribed: bool
    last_email_at: datetime | None


@dataclass(frozen=True)
class MessagingSummary:
    whatsapp: WhatsAppSummary | None
    email: EmailSummary | None


def _latest(*values: datetime | None) -> datetime | None:
    present = [v for v in values if v is not None]
    return max(present) if present else None


async def _whatsapp(
    session: AsyncSession, organization_id: uuid.UUID, contact_id: uuid.UUID, now: datetime
) -> WhatsAppSummary | None:
    conversations = (
        await session.execute(
            select(
                Conversation.id,
                Conversation.wa_address_index,
                Conversation.last_customer_message_at,
            ).where(
                Conversation.organization_id == organization_id,
                Conversation.contact_id == contact_id,
            )
        )
    ).all()

    connected = (
        await session.execute(
            select(func.count())
            .select_from(ProviderAccount)
            .where(
                ProviderAccount.organization_id == organization_id,
                ProviderAccount.channel == "whatsapp",
            )
        )
    ).scalar_one()

    # The ledger rows that name this contact directly — a consent recorded through the API
    # against a contact before any conversation exists.
    ledger_indexes = (
        (
            await session.execute(
                select(ConsentEvent.subject_index)
                .where(
                    ConsentEvent.organization_id == organization_id,
                    ConsentEvent.channel == "whatsapp",
                    ConsentEvent.contact_id == contact_id,
                )
                .distinct()
            )
        )
        .scalars()
        .all()
    )

    if not conversations and not connected and not ledger_indexes:
        # No number connected and nothing about this contact: the channel does not apply.
        return None

    indexes = list(dict.fromkeys([c.wa_address_index for c in conversations] + list(ledger_indexes)))
    decisions = await consent.decide_indexes(
        session, organization_id, "whatsapp", consent.MARKETING, indexes
    )
    opted_in = any(d.allowed for d in decisions.values())

    window_opened = _latest(*(c.last_customer_message_at for c in conversations))
    window_open_until = None
    if window_opened is not None and window_opened + SERVICE_WINDOW > now:
        window_open_until = window_opened + SERVICE_WINDOW

    last_message_at: datetime | None = None
    if conversations:
        conversation_ids = [c.id for c in conversations]
        last_out = (
            await session.execute(
                select(func.max(func.coalesce(Message.sent_at, Message.created_at))).where(
                    Message.organization_id == organization_id,
                    Message.conversation_id.in_(conversation_ids),
                )
            )
        ).scalar_one()
        last_in = (
            await session.execute(
                select(func.max(InboundMessage.occurred_at)).where(
                    InboundMessage.organization_id == organization_id,
                    InboundMessage.conversation_id.in_(conversation_ids),
                )
            )
        ).scalar_one()
        last_message_at = _latest(last_out, last_in)

    return WhatsAppSummary(
        opted_in=opted_in,
        window_open_until=window_open_until,
        last_message_at=last_message_at,
        conversations=len(conversations),
    )


async def _email(
    session: AsyncSession, organization_id: uuid.UUID, address: str
) -> EmailSummary | None:
    try:
        normalized = consent.normalize("email", address)
        index = consent.index_for("email", address)
    except domain.RuleError:
        return None
    decisions = await consent.decide_indexes(
        session, organization_id, "email", consent.MARKETING, [index]
    )
    last_email_at = (
        await session.execute(
            select(func.max(func.coalesce(Message.sent_at, Message.created_at))).where(
                Message.organization_id == organization_id,
                Message.channel == "email",
                Message.direction == "outbound",
                func.lower(Message.to_address) == normalized,
            )
        )
    ).scalar_one()
    decision = decisions.get(index)
    return EmailSummary(
        subscribed=bool(decision and decision.allowed),
        last_email_at=last_email_at,
    )


async def messaging_summary(
    session: AsyncSession,
    organization_id: uuid.UUID,
    contact_id: uuid.UUID,
    *,
    email_address: str | None = None,
    now: datetime | None = None,
) -> MessagingSummary:
    """Both channels for one contact. A handful of indexed reads, independent of how many
    conversations or messages the contact has."""
    reference = now or datetime.now(UTC)
    whatsapp = await _whatsapp(session, organization_id, contact_id, reference)
    email = await _email(session, organization_id, email_address) if email_address else None
    return MessagingSummary(whatsapp=whatsapp, email=email)


__all__ = ["EmailSummary", "MessagingSummary", "WhatsAppSummary", "messaging_summary"]
