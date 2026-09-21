"""Reads for the notification service. Visibility rules live here, in one place.

Every scoped query calls `ctx.apply(...)` **and** filters `organization_id` explicitly. The
explicit filter is not redundant with row-level security: RLS is the net under the tightrope,
not permission to walk without one (CLAUDE.md, ADR-0002). Both stay.

A list endpoint that builds its own query instead of coming through a selector is how an
organization leaks, so routers never write SQL.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Delivery,
    EmailConnection,
    EmailDomain,
    Message,
    Preference,
    ProviderAccount,
    ProviderTemplate,
    Rule,
    Sender,
    Template,
)
from platform_core.auth import ScopeContext


def templates_stmt(ctx: ScopeContext, *, channel: str | None = None) -> Select[Any]:
    """Scope-filtered template list. `ctx.apply` is the enforcement point."""
    stmt = select(Template).where(
        Template.organization_id == ctx.organization_id,
        Template.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=Template.created_by)
    if channel:
        stmt = stmt.where(Template.channel == channel)
    return stmt.order_by(Template.code.asc(), Template.channel.asc())


async def get_template(
    session: AsyncSession, org_id: uuid.UUID, code: str, channel: str
) -> Template | None:
    """Org-scoped fetch by code and channel. None on a tenant mismatch — confirming a code
    exists in *some* organization is itself a disclosure."""
    stmt = select(Template).where(
        Template.code == code,
        Template.channel == channel,
        Template.organization_id == org_id,
        Template.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def rules_stmt(ctx: ScopeContext, *, event_type: str | None = None) -> Select[Any]:
    """Scope-filtered rule list."""
    stmt = select(Rule).where(
        Rule.organization_id == ctx.organization_id,
        Rule.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=Rule.created_by)
    if event_type:
        stmt = stmt.where(Rule.event_type == event_type)
    return stmt.order_by(Rule.code.asc())


async def get_rule(session: AsyncSession, org_id: uuid.UUID, code: str) -> Rule | None:
    stmt = select(Rule).where(
        Rule.code == code,
        Rule.organization_id == org_id,
        Rule.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def active_rules_for(session: AsyncSession, org_id: uuid.UUID, event_type: str) -> list[Rule]:
    """Every active rule watching one event type — the consumer's hot path.

    Org-scoped like every read here even though the consumer has already bound the tenant GUC to
    this message's organization. The filter is the tightrope; RLS is the net.
    """
    stmt = (
        select(Rule)
        .where(
            Rule.organization_id == org_id,
            Rule.event_type == event_type,
            Rule.active.is_(True),
            Rule.deleted_at.is_(None),
        )
        .order_by(Rule.code.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


def messages_stmt(
    ctx: ScopeContext,
    *,
    state: str | None = None,
    channel: str | None = None,
    q: str | None = None,
) -> Select[Any]:
    """Scope-filtered message list, newest first — a delivery log is read from the top."""
    stmt = select(Message).where(
        Message.organization_id == ctx.organization_id,
        Message.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=Message.created_by)
    if state:
        stmt = stmt.where(Message.state == state)
    if channel:
        stmt = stmt.where(Message.channel == channel)
    if q:
        needle = f"%{q.lower()}%"
        stmt = stmt.where(or_(Message.to_address.ilike(needle), Message.subject.ilike(needle)))
    return stmt.order_by(Message.created_at.desc())


async def get_message(
    session: AsyncSession, org_id: uuid.UUID, message_id: uuid.UUID
) -> Message | None:
    stmt = select(Message).where(
        Message.id == message_id,
        Message.organization_id == org_id,
        Message.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def find_by_dedupe_key(session: AsyncSession, org_id: uuid.UUID, key: str) -> Message | None:
    """Has this exact send already been staged? The second net under consumer idempotency."""
    stmt = select(Message).where(
        Message.dedupe_key == key,
        Message.organization_id == org_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_deliveries(
    session: AsyncSession, org_id: uuid.UUID, message_id: uuid.UUID
) -> list[Delivery]:
    """One message's attempt ledger, oldest attempt first."""
    stmt = (
        select(Delivery)
        .where(
            Delivery.message_id == message_id,
            Delivery.organization_id == org_id,
        )
        .order_by(Delivery.attempt_no.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def deliveries_by_message(
    session: AsyncSession, org_id: uuid.UUID, message_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[Delivery]]:
    """Ledgers for many messages in one query — so a list endpoint is not a query per row."""
    if not message_ids:
        return {}
    stmt = (
        select(Delivery)
        .where(
            Delivery.message_id.in_(message_ids),
            Delivery.organization_id == org_id,
        )
        .order_by(Delivery.attempt_no.asc())
    )
    grouped: dict[uuid.UUID, list[Delivery]] = {}
    for row in (await session.execute(stmt)).scalars().all():
        grouped.setdefault(row.message_id, []).append(row)
    return grouped


async def suppressions_for(
    session: AsyncSession, org_id: uuid.UUID, recipient: str
) -> list[tuple[str, str]]:
    """One recipient's refusals as `(channel, category)` pairs, for `domain.is_suppressed`.

    Returns pairs rather than rows so the pure function stays free of the ORM, and so the same
    check runs identically in the consumer, the dispatcher and a unit test.
    """
    stmt = select(Preference.channel, Preference.category).where(
        Preference.organization_id == org_id,
        Preference.recipient == recipient,
        Preference.suppressed.is_(True),
        Preference.deleted_at.is_(None),
    )
    return [(row[0], row[1]) for row in (await session.execute(stmt)).all()]


def preferences_stmt(ctx: ScopeContext, *, recipient: str | None = None) -> Select[Any]:
    stmt = select(Preference).where(
        Preference.organization_id == ctx.organization_id,
        Preference.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=Preference.created_by)
    if recipient:
        stmt = stmt.where(Preference.recipient == recipient)
    return stmt.order_by(Preference.recipient.asc(), Preference.channel.asc())


async def get_preference(
    session: AsyncSession, org_id: uuid.UUID, recipient: str, channel: str, category: str
) -> Preference | None:
    stmt = select(Preference).where(
        Preference.organization_id == org_id,
        Preference.recipient == recipient,
        Preference.channel == channel,
        Preference.category == category,
        Preference.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def provider_templates_stmt(ctx: ScopeContext, *, channel: str | None = None) -> Select[Any]:
    """Scope-filtered provider-template list."""
    stmt = select(ProviderTemplate).where(
        ProviderTemplate.organization_id == ctx.organization_id,
        ProviderTemplate.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=ProviderTemplate.created_by)
    if channel:
        stmt = stmt.where(ProviderTemplate.channel == channel)
    return stmt.order_by(ProviderTemplate.code.asc())


async def get_provider_template(
    session: AsyncSession, org_id: uuid.UUID, code: str, channel: str
) -> ProviderTemplate | None:
    """Org-scoped fetch by code and channel. None on a tenant mismatch."""
    stmt = select(ProviderTemplate).where(
        ProviderTemplate.code == code,
        ProviderTemplate.channel == channel,
        ProviderTemplate.organization_id == org_id,
        ProviderTemplate.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_message_by_provider_id(
    session: AsyncSession, org_id: uuid.UUID, provider_message_id: str
) -> Message | None:
    """The message a provider receipt is about. Org-scoped like every read here."""
    stmt = select(Message).where(
        Message.provider_message_id == provider_message_id,
        Message.organization_id == org_id,
        Message.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def account_for_phone_number(
    session: AsyncSession, phone_number_id: str
) -> ProviderAccount | None:
    """Which organization owns the number a webhook arrived on.

    **Deliberately not org-scoped**, because the organization is what this resolves — it is the
    lookup that happens before a tenant is known. It must therefore run on the directory
    session (SELECT-only, BYPASSRLS), never the tenant-bound one, and the caller binds the
    organization it returns before touching anything else.
    """
    stmt = select(ProviderAccount).where(
        ProviderAccount.phone_number_id == phone_number_id,
        ProviderAccount.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


# --------------------------------------------------------------------- engage sending identity


def connection_stmt(ctx: ScopeContext) -> Select[Any]:
    """The organization's email connection, as a statement a route can page or count.

    A statement rather than a row even though there is only ever one: the read route returns a
    list-or-nothing shape, and a selector that returned the row would put the "is there one?"
    branch in the router instead of here.
    """
    stmt = select(EmailConnection).where(
        EmailConnection.organization_id == ctx.organization_id,
        EmailConnection.deleted_at.is_(None),
    )
    return ctx.apply(stmt, owner_col=EmailConnection.created_by)


async def get_connection(session: AsyncSession, org_id: uuid.UUID) -> EmailConnection | None:
    """The one connection an organization sends through, or None.

    Org-scoped like every read here. The writer uses it to refuse a second connection, and the
    test send uses it to find the credential — neither may see another tenant's.
    """
    stmt = select(EmailConnection).where(
        EmailConnection.organization_id == org_id,
        EmailConnection.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def whatsapp_connection_stmt(ctx: ScopeContext) -> Select[Any]:
    """The organization's WhatsApp provider account, as a statement a route can page or count.

    Same list-or-nothing shape as `connection_stmt`, for the same reason: "is there one yet?"
    stays a selector concern, not a router branch.
    """
    stmt = select(ProviderAccount).where(
        ProviderAccount.organization_id == ctx.organization_id,
        ProviderAccount.channel == "whatsapp",
        ProviderAccount.deleted_at.is_(None),
    )
    return ctx.apply(stmt, owner_col=ProviderAccount.created_by)


async def get_whatsapp_connection(
    session: AsyncSession, org_id: uuid.UUID
) -> ProviderAccount | None:
    """The one WhatsApp account an organization sends through, or None."""
    stmt = select(ProviderAccount).where(
        ProviderAccount.organization_id == org_id,
        ProviderAccount.channel == "whatsapp",
        ProviderAccount.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def domains_stmt(ctx: ScopeContext, *, status: str | None = None) -> Select[Any]:
    """Scope-filtered sending-domain list, oldest first — a setup list is read in the order it
    was built, not newest-first like a log."""
    stmt = select(EmailDomain).where(
        EmailDomain.organization_id == ctx.organization_id,
        EmailDomain.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=EmailDomain.created_by)
    if status:
        stmt = stmt.where(EmailDomain.status == status)
    return stmt.order_by(EmailDomain.created_at.asc())


async def get_domain(
    session: AsyncSession, org_id: uuid.UUID, domain_id: uuid.UUID
) -> EmailDomain | None:
    stmt = select(EmailDomain).where(
        EmailDomain.id == domain_id,
        EmailDomain.organization_id == org_id,
        EmailDomain.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_domain_by_name(
    session: AsyncSession, org_id: uuid.UUID, name: str
) -> EmailDomain | None:
    """Org-scoped fetch by name. None on a tenant mismatch: whether some other organization has
    claimed a domain is not this caller's business, and answering would say so."""
    stmt = select(EmailDomain).where(
        EmailDomain.domain == name,
        EmailDomain.organization_id == org_id,
        EmailDomain.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def senders_stmt(ctx: ScopeContext, *, domain_id: uuid.UUID | None = None) -> Select[Any]:
    """Scope-filtered sender list."""
    stmt = select(Sender).where(
        Sender.organization_id == ctx.organization_id,
        Sender.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=Sender.created_by)
    if domain_id:
        stmt = stmt.where(Sender.domain_id == domain_id)
    return stmt.order_by(Sender.name.asc())


async def get_sender(
    session: AsyncSession, org_id: uuid.UUID, sender_id: uuid.UUID
) -> Sender | None:
    stmt = select(Sender).where(
        Sender.id == sender_id,
        Sender.organization_id == org_id,
        Sender.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_sender_by_address(
    session: AsyncSession, org_id: uuid.UUID, address: str
) -> Sender | None:
    stmt = select(Sender).where(
        Sender.address == address,
        Sender.organization_id == org_id,
        Sender.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()
