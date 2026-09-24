"""Reads for the notification service. Visibility rules live here, in one place.

Every scoped query calls `ctx.apply(...)` **and** filters `organization_id` explicitly. The
explicit filter is not redundant with row-level security: RLS is the net under the tightrope,
not permission to walk without one (CLAUDE.md, ADR-0002). Both stay.

A list endpoint that builds its own query instead of coming through a selector is how an
organization leaks, so routers never write SQL.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Select, exists, func, literal, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    Campaign,
    CampaignRecipient,
    Contact,
    ContactList,
    ContactListMember,
    Conversation,
    Delivery,
    EmailConnection,
    EmailDomain,
    InboundMessage,
    Message,
    Preference,
    ProviderAccount,
    ProviderTemplate,
    Rule,
    Sender,
    Suppression,
    Template,
)
from platform_core import pii
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
    session: AsyncSession,
    org_id: uuid.UUID,
    message_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> Message | None:
    """One message. `for_update` takes the row lock, **skipping** a row somebody else holds — the
    recovery sweep's shape: a message a dispatcher is claiming this instant is not one the sweep
    should be settling, so it gets `None` and looks again next pass. `populate_existing` so the
    values compared under the lock are the committed ones, not a copy this session read earlier."""
    stmt = select(Message).where(
        Message.id == message_id,
        Message.organization_id == org_id,
        Message.deleted_at.is_(None),
    )
    if for_update:
        stmt = stmt.with_for_update(skip_locked=True).execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalar_one_or_none()


async def due_message_redrives(
    session: AsyncSession, *, now: datetime, limit: int
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """`(organization_id, message_id)` for every message whose `retry_at` has arrived: a retry
    whose backoff is over (`pending`) and a claim whose lease has lapsed (`sending`). Oldest
    first, at most `limit`.

    Cross-tenant and BYPASSRLS by design — the same shape as `due_campaigns` and, like it, reading
    only the four columns the outbox role is granted on `messages` (`migrations/env.py`). There is
    no tenant to bind while asking which tenants have work; every write the sweep makes off the
    back of this runs tenant-bound (`app/cron.py`).
    """
    stmt = (
        select(Message.organization_id, Message.id)
        .where(
            Message.retry_at.is_not(None),
            Message.retry_at <= now,
            Message.state.in_(("pending", "sending")),
        )
        .order_by(Message.retry_at.asc(), Message.id.asc())
        .limit(limit)
    )
    return [(row[0], row[1]) for row in (await session.execute(stmt)).all()]


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


async def get_connection(
    session: AsyncSession, org_id: uuid.UUID, *, lock: bool = False
) -> EmailConnection | None:
    """The one connection an organization sends through, or None.

    Org-scoped like every read here. The writer uses it to refuse a second connection, and the
    test send uses it to find the credential — neither may see another tenant's. `lock=True` is
    the campaign driver's: it holds this row `FOR UPDATE` for the length of its claim
    transaction, which is what serialises two concurrent batches (same campaign racing itself,
    or two campaigns on one connection) on `daily_cap` — the cap check and the recipient claim
    that spends it happen inside the same lock rather than as two independent reads that could
    each see room for a batch the other has already spent.
    """
    stmt = select(EmailConnection).where(
        EmailConnection.organization_id == org_id,
        EmailConnection.deleted_at.is_(None),
    )
    if lock:
        stmt = stmt.with_for_update()
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
    was built, not newest-first like a log. `id` breaks a tie on `created_at`: rows written in one
    transaction share a timestamp, and an `OFFSET` page over an order with ties can skip or repeat
    a row between two requests."""
    stmt = select(EmailDomain).where(
        EmailDomain.organization_id == ctx.organization_id,
        EmailDomain.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=EmailDomain.created_by)
    if status:
        stmt = stmt.where(EmailDomain.status == status)
    return stmt.order_by(EmailDomain.created_at.asc(), EmailDomain.id.asc())


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
    """Scope-filtered sender list, by name. `id` breaks a tie: two senders may share a display name
    ("Support" on two domains), and an `OFFSET` page over an order with ties can skip or repeat a
    row between two requests — a reader paging the whole list would lose one."""
    stmt = select(Sender).where(
        Sender.organization_id == ctx.organization_id,
        Sender.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=Sender.created_by)
    if domain_id:
        stmt = stmt.where(Sender.domain_id == domain_id)
    return stmt.order_by(Sender.name.asc(), Sender.id.asc())


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


# --------------------------------------------------------------------- engage audience


def email_index(email: str) -> str:
    """The blind index a normalised address is stored and found under. One definition, so the
    writer that stores it and every reader that looks it up cannot drift apart."""
    return pii.blind_index(email, kind="email", settings=settings())


def _like_escape(term: str) -> str:
    """A user's search term as a literal ILIKE fragment. Backslash first, or escaping `%` as
    `\\%` would itself introduce a second escape sequence."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def contacts_stmt(ctx: ScopeContext, *, search: str | None = None) -> Select[Any]:
    """Scope-filtered contact list, newest first.

    `search` matches the name as a substring, and the address **exactly** (case-insensitively):
    the address is sealed, so a substring of it is not something the database can be asked
    about — only equality, through the blind index. A term that is a whole address finds that
    contact; a fragment of one finds nothing by address, which is the honest cost of not
    storing the address in the clear.
    """
    stmt = select(Contact).where(
        Contact.organization_id == ctx.organization_id,
        Contact.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=Contact.created_by)
    term = (search or "").strip()
    if term:
        needle = f"%{_like_escape(term)}%"
        stmt = stmt.where(
            or_(
                Contact.name.ilike(needle, escape="\\"),
                Contact.email_index == email_index(term.lower()),
            )
        )
    return stmt.order_by(Contact.created_at.desc(), Contact.id.desc())


async def get_contact(
    session: AsyncSession, org_id: uuid.UUID, contact_id: uuid.UUID
) -> Contact | None:
    stmt = select(Contact).where(
        Contact.id == contact_id,
        Contact.organization_id == org_id,
        Contact.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def contacts_by_index(
    session: AsyncSession, org_id: uuid.UUID, indexes: list[str]
) -> dict[str, Contact]:
    """Existing contacts for a batch of blind indexes, keyed by index — one query for a bulk
    import rather than one per row."""
    if not indexes:
        return {}
    stmt = select(Contact).where(
        Contact.organization_id == org_id,
        Contact.email_index.in_(indexes),
        Contact.deleted_at.is_(None),
    )
    return {c.email_index: c for c in (await session.execute(stmt)).scalars()}


def suppressions_stmt(ctx: ScopeContext, *, reason: str | None = None) -> Select[Any]:
    """Scope-filtered suppression list, newest first."""
    stmt = select(Suppression).where(
        Suppression.organization_id == ctx.organization_id,
        Suppression.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=Suppression.created_by)
    if reason:
        stmt = stmt.where(Suppression.reason == reason)
    return stmt.order_by(Suppression.created_at.desc(), Suppression.id.desc())


async def get_suppression(
    session: AsyncSession, org_id: uuid.UUID, suppression_id: uuid.UUID
) -> Suppression | None:
    stmt = select(Suppression).where(
        Suppression.id == suppression_id,
        Suppression.organization_id == org_id,
        Suppression.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_suppression_by_index(
    session: AsyncSession, org_id: uuid.UUID, index: str
) -> Suppression | None:
    stmt = select(Suppression).where(
        Suppression.email_index == index,
        Suppression.organization_id == org_id,
        Suppression.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def sendable_contacts(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    limit: int = 1000,
    after_id: uuid.UUID | None = None,
    list_id: uuid.UUID | None = None,
    lock: bool = False,
) -> list[Contact]:
    """Contacts a campaign may send to: the organization's contacts minus every suppressed
    address, whatever the reason.

    The exclusion is an anti-join on the blind index, so it holds for a suppression written
    before the contact existed and for one written after — a re-imported address that once
    unsubscribed stays unreachable. Keyset-paged by id (`after_id`) so a sender can walk a large
    audience without an OFFSET that shifts under concurrent writes.

    `lock=True` is `expand_campaign`'s: `SELECT ... FOR KEY SHARE` on every contact in the
    page, held until expansion's transaction commits. Postgres's `DELETE` takes a row lock that
    conflicts with `FOR KEY SHARE`, so a concurrent `delete_contact` on one of these rows blocks
    until this transaction ends — the read and the copy `expand_campaign` writes into
    `campaign_recipients` cannot straddle a deletion whose own `erase_contact_campaign_copies`
    ran and committed before the copy existed to erase. `FOR KEY SHARE` rather than a stronger
    mode because it is the one lock that never conflicts with another `expand_campaign` page
    reading the same contact for a different campaign, or with an ordinary read.
    """
    suppressed = exists().where(
        Suppression.organization_id == Contact.organization_id,
        Suppression.email_index == Contact.email_index,
        Suppression.deleted_at.is_(None),
    )
    stmt = select(Contact).where(
        Contact.organization_id == ctx.organization_id,
        Contact.deleted_at.is_(None),
        ~suppressed,
    )
    stmt = ctx.apply(stmt, owner_col=Contact.created_by)
    if list_id is not None:
        # Narrowed to one list's members: the audience a list campaign sends to.
        stmt = stmt.where(
            exists().where(
                ContactListMember.organization_id == Contact.organization_id,
                ContactListMember.list_id == list_id,
                ContactListMember.contact_id == Contact.id,
            )
        )
    if after_id is not None:
        stmt = stmt.where(Contact.id > after_id)
    stmt = stmt.order_by(Contact.id.asc()).limit(limit)
    if lock:
        stmt = stmt.with_for_update(key_share=True)
    return list((await session.execute(stmt)).scalars())


# --------------------------------------------------------------------- engage lists


def lists_stmt(ctx: ScopeContext) -> Select[Any]:
    """Scope-filtered contact lists, by name."""
    stmt = select(ContactList).where(
        ContactList.organization_id == ctx.organization_id,
        ContactList.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=ContactList.created_by)
    return stmt.order_by(ContactList.name.asc(), ContactList.id.asc())


async def get_list(
    session: AsyncSession, org_id: uuid.UUID, list_id: uuid.UUID
) -> ContactList | None:
    stmt = select(ContactList).where(
        ContactList.id == list_id,
        ContactList.organization_id == org_id,
        ContactList.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_list_by_name(
    session: AsyncSession, org_id: uuid.UUID, name: str
) -> ContactList | None:
    stmt = select(ContactList).where(
        ContactList.name == name,
        ContactList.organization_id == org_id,
        ContactList.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_member_counts(
    session: AsyncSession, org_id: uuid.UUID, list_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """Member count per list, one grouped query for a page of lists."""
    if not list_ids:
        return {}
    stmt = (
        select(ContactListMember.list_id, func.count())
        .where(
            ContactListMember.organization_id == org_id,
            ContactListMember.list_id.in_(list_ids),
        )
        .group_by(ContactListMember.list_id)
    )
    return {row[0]: int(row[1]) for row in (await session.execute(stmt)).all()}


def list_members_stmt(ctx: ScopeContext, list_id: uuid.UUID) -> Select[Any]:
    """The contacts on one list, newest first — the same rows `contacts_stmt` would return."""
    member = exists().where(
        ContactListMember.organization_id == Contact.organization_id,
        ContactListMember.list_id == list_id,
        ContactListMember.contact_id == Contact.id,
    )
    stmt = select(Contact).where(
        Contact.organization_id == ctx.organization_id,
        Contact.deleted_at.is_(None),
        member,
    )
    stmt = ctx.apply(stmt, owner_col=Contact.created_by)
    return stmt.order_by(Contact.created_at.desc(), Contact.id.desc())


async def existing_contact_ids(
    session: AsyncSession, org_id: uuid.UUID, contact_ids: list[uuid.UUID]
) -> set[uuid.UUID]:
    """Which of these ids are this organization's contacts."""
    if not contact_ids:
        return set()
    stmt = select(Contact.id).where(
        Contact.organization_id == org_id,
        Contact.id.in_(contact_ids),
        Contact.deleted_at.is_(None),
    )
    return set((await session.execute(stmt)).scalars())


async def campaigns_using_list(session: AsyncSession, org_id: uuid.UUID, list_id: uuid.UUID) -> int:
    stmt = select(func.count()).where(
        Campaign.organization_id == org_id,
        Campaign.list_id == list_id,
        Campaign.deleted_at.is_(None),
    )
    return int((await session.execute(stmt)).scalar_one())


# --------------------------------------------------------------------- engage campaigns


def campaigns_stmt(ctx: ScopeContext, *, status: str | None = None) -> Select[Any]:
    """Scope-filtered campaigns, newest first."""
    stmt = select(Campaign).where(
        Campaign.organization_id == ctx.organization_id,
        Campaign.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=Campaign.created_by)
    if status:
        stmt = stmt.where(Campaign.status == status)
    return stmt.order_by(Campaign.created_at.desc(), Campaign.id.desc())


async def get_campaign(
    session: AsyncSession, org_id: uuid.UUID, campaign_id: uuid.UUID, *, lock: bool = False
) -> Campaign | None:
    stmt = select(Campaign).where(
        Campaign.id == campaign_id,
        Campaign.organization_id == org_id,
        Campaign.deleted_at.is_(None),
    )
    if lock:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


def recipients_stmt(
    ctx: ScopeContext, campaign_id: uuid.UUID, *, status: str | None = None
) -> Select[Any]:
    """One campaign's recipients, in expansion order."""
    stmt = select(CampaignRecipient).where(
        CampaignRecipient.organization_id == ctx.organization_id,
        CampaignRecipient.campaign_id == campaign_id,
        CampaignRecipient.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=CampaignRecipient.created_by)
    if status == "queued":
        # `sending` is the driver's in-flight claim lease, not a status the API exposes — a
        # caller asking for "queued" means "not yet resolved", which includes a row currently
        # claimed by a batch.
        stmt = stmt.where(CampaignRecipient.status.in_(("queued", "sending")))
    elif status:
        stmt = stmt.where(CampaignRecipient.status == status)
    return stmt.order_by(CampaignRecipient.contact_id.asc())


async def recipient_status_counts(
    session: AsyncSession, org_id: uuid.UUID, campaign_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[str, int]]:
    """`{campaign_id: {status: n}}` for a page of campaigns, in one grouped query."""
    if not campaign_ids:
        return {}
    stmt = (
        select(CampaignRecipient.campaign_id, CampaignRecipient.status, func.count())
        .where(
            CampaignRecipient.organization_id == org_id,
            CampaignRecipient.campaign_id.in_(campaign_ids),
        )
        .group_by(CampaignRecipient.campaign_id, CampaignRecipient.status)
    )
    out: dict[uuid.UUID, dict[str, int]] = {}
    for campaign_id, status, n in (await session.execute(stmt)).all():
        out.setdefault(campaign_id, {})[status] = int(n)
    return out


async def campaign_mail_sent_since(
    session: AsyncSession, org_id: uuid.UUID, since: datetime
) -> int:
    """Email this organization has sent on its one connection since `since` — what counts
    against the connection's `daily_cap`.

    Both campaign mail *and* rule-driven mail (`messages`, the ordinary transactional path)
    share the same SMTP connection and the same provider-side limit, so both count: a rule that
    fires a thousand times today leaves that much less room for a campaign, and the reverse. Two
    grouped counts rather than a union query, because the tables have no join key worth writing
    (a `Message` is not a `CampaignRecipient`) — one round trip either way.
    """
    campaign_sent = select(func.count()).where(
        CampaignRecipient.organization_id == org_id,
        CampaignRecipient.status == "sent",
        CampaignRecipient.sent_at >= since,
    )
    rule_sent = select(func.count()).where(
        Message.organization_id == org_id,
        Message.channel == "email",
        Message.state.in_(("sent", "delivered", "read")),
        Message.sent_at >= since,
    )
    campaign_count = int((await session.execute(campaign_sent)).scalar_one())
    rule_count = int((await session.execute(rule_sent)).scalar_one())
    return campaign_count + rule_count


async def suppressed_indexes(
    session: AsyncSession, org_id: uuid.UUID, indexes: list[str]
) -> set[str]:
    """Which of these blind indexes are currently suppressed — one `IN (...)` query for a whole
    batch, rather than one query per recipient (the campaign driver's re-check at send time)."""
    if not indexes:
        return set()
    stmt = select(Suppression.email_index).where(
        Suppression.organization_id == org_id,
        Suppression.email_index.in_(indexes),
        Suppression.deleted_at.is_(None),
    )
    return set((await session.execute(stmt)).scalars())


async def recipients_by_id(
    session: AsyncSession, org_id: uuid.UUID, campaign_id: uuid.UUID, recipient_ids: list[uuid.UUID]
) -> dict[uuid.UUID, CampaignRecipient]:
    """A claimed batch's own rows, by id — the campaign driver's record step re-selecting
    exactly what it just claimed, to write the outcome of each."""
    if not recipient_ids:
        return {}
    stmt = select(CampaignRecipient).where(
        CampaignRecipient.organization_id == org_id,
        CampaignRecipient.campaign_id == campaign_id,
        CampaignRecipient.id.in_(recipient_ids),
    )
    rows = (await session.execute(stmt)).scalars()
    return {r.id: r for r in rows}


async def get_recipient_for_unsubscribe(
    session: AsyncSession, org_id: uuid.UUID, recipient_id: uuid.UUID
) -> CampaignRecipient | None:
    """One `campaign_recipients` row, by id — what a v2 unsubscribe link resolves against.
    No campaign id: the token names the recipient row directly, not the campaign it belongs to.
    Erased rows are invisible here — the caller (`services.unsubscribe_recipient`) treats that
    the same as the id naming nothing at all, since there is no address left to suppress."""
    stmt = select(CampaignRecipient).where(
        CampaignRecipient.id == recipient_id,
        CampaignRecipient.organization_id == org_id,
        CampaignRecipient.erased_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def erased_recipient_ids(
    session: AsyncSession, org_id: uuid.UUID, recipient_ids: list[uuid.UUID]
) -> set[uuid.UUID]:
    """Which of `recipient_ids` have since been erased — the campaign driver's last check,
    immediately before a transport is asked to send any of them (`services.
    drop_erased_from_batch`), closing most of the window between a batch's claim and its send.
    """
    if not recipient_ids:
        return set()
    stmt = select(CampaignRecipient.id).where(
        CampaignRecipient.organization_id == org_id,
        CampaignRecipient.id.in_(recipient_ids),
        CampaignRecipient.erased_at.is_not(None),
    )
    return set((await session.execute(stmt)).scalars())


async def masked_recipient_email(
    session: AsyncSession, org_id: uuid.UUID, recipient_id: uuid.UUID
) -> str | None:
    """The masked address for one campaign recipient row, for a v2 unsubscribe link's
    confirmation page — read directly off the row, never a decrypt, since the page has not
    acted on anything yet."""
    stmt = select(CampaignRecipient.email_masked).where(
        CampaignRecipient.id == recipient_id,
        CampaignRecipient.organization_id == org_id,
        CampaignRecipient.erased_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def due_campaigns(
    session: AsyncSession, *, paused_retry_before: datetime, limit: int
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """`(organization_id, campaign_id)` for every campaign the send driver should look at this
    tick: everything `sending`, plus a `paused` campaign whose last pause is old enough to be
    worth retrying (`paused_at <= paused_retry_before`) — the driver's own backoff against a
    route that keeps failing.

    Cross-tenant and BYPASSRLS by design (the same shape as `approval`'s escalation sweep and
    `subscription`'s dunning sweep): discovery exists to find out *which organizations* have a
    campaign to work, and there is no tenant to bind while asking. Every write the driver makes
    off the back of this still runs tenant-bound (`app/cron.py`).
    """
    stmt = (
        select(Campaign.organization_id, Campaign.id)
        .where(
            Campaign.deleted_at.is_(None),
            or_(
                Campaign.status == "sending",
                (Campaign.status == "paused")
                & (Campaign.paused_at.is_(None) | (Campaign.paused_at <= paused_retry_before)),
            ),
        )
        .order_by(Campaign.started_at.asc().nulls_first(), Campaign.id.asc())
        .limit(limit)
    )
    return [(row[0], row[1]) for row in (await session.execute(stmt)).all()]


async def is_suppressed(session: AsyncSession, org_id: uuid.UUID, index: str) -> bool:
    return await get_suppression_by_index(session, org_id, index) is not None


# --------------------------------------------------------------------- engage inbox (WhatsApp)


def conversations_stmt(ctx: ScopeContext, *, conversation_status: str | None = None) -> Select[Any]:
    """Conversations, most recently active first. `ctx.apply` is the enforcement point."""
    stmt = select(Conversation).where(
        Conversation.organization_id == ctx.organization_id,
        Conversation.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=Conversation.created_by)
    if conversation_status:
        stmt = stmt.where(Conversation.status == conversation_status)
    return stmt.order_by(
        Conversation.last_customer_message_at.desc().nulls_last(),
        Conversation.created_at.desc(),
    )


async def get_conversation(
    session: AsyncSession, org_id: uuid.UUID, conversation_id: uuid.UUID, *, lock: bool = False
) -> Conversation | None:
    """One conversation, org-scoped. `lock=True` holds it `FOR UPDATE` for the caller's whole
    transaction — the reply endpoint's serialisation point against the service window, which a
    concurrent inbound webhook could otherwise move underneath it mid-check."""
    stmt = select(Conversation).where(
        Conversation.id == conversation_id,
        Conversation.organization_id == org_id,
        Conversation.deleted_at.is_(None),
    )
    if lock:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_conversation_by_address_index(
    session: AsyncSession, org_id: uuid.UUID, channel: str, wa_address_index: str
) -> Conversation | None:
    """The webhook fold's lookup: does a conversation already exist for this number."""
    stmt = select(Conversation).where(
        Conversation.organization_id == org_id,
        Conversation.channel == channel,
        Conversation.wa_address_index == wa_address_index,
        Conversation.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def thread_stmt(ctx: ScopeContext, conversation_id: uuid.UUID) -> Select[Any]:
    """One conversation's timeline, both directions, oldest first — a `UNION ALL` of the inbound
    and outbound tables rather than two paginated reads, so a page of the thread is one page of
    the actual conversation and not one page of each side of it.

    Bodies come back masked on the inbound side (`InboundMessage.body_masked`) and in full on
    the outbound side (`Message.body`): only the customer's own words are PII gated behind
    `engage.inbox.message.unmask` here — an agent's own sent reply is not a secret from the
    agent holding the read permission that already shows it. `services.unmask_inbound_message`
    is the audited path to the sealed side of an inbound row.
    """
    inbound = select(
        InboundMessage.id,
        literal("inbound").label("direction"),
        InboundMessage.type.label("message_type"),
        InboundMessage.body_masked.label("body"),
        InboundMessage.occurred_at,
        InboundMessage.provider_message_id,
        InboundMessage.profile_name_masked.label("profile_name"),
    ).where(
        InboundMessage.organization_id == ctx.organization_id,
        InboundMessage.conversation_id == conversation_id,
        InboundMessage.deleted_at.is_(None),
    )
    outbound = select(
        Message.id,
        literal("outbound").label("direction"),
        literal("text").label("message_type"),
        Message.body.label("body"),
        Message.created_at.label("occurred_at"),
        Message.provider_message_id,
        literal(None).label("profile_name"),
    ).where(
        Message.organization_id == ctx.organization_id,
        Message.conversation_id == conversation_id,
        Message.deleted_at.is_(None),
    )
    thread = union_all(inbound, outbound).subquery()
    return select(thread).order_by(thread.c.occurred_at.asc())


async def thread_messages(
    session: AsyncSession,
    ctx: ScopeContext,
    conversation_id: uuid.UUID,
    *,
    offset: int,
    limit: int,
) -> tuple[list[Any], int]:
    """A page of `thread_stmt`, counted and fetched in one call.

    Not routed through `platform_core.pagination.paginate`: that helper assumes a `select(Model)`
    it can `.scalars()` off, and this is a `UNION ALL` of plain columns from two tables — a page
    of rows, not a page of one ORM entity. The two-statement shape (count, then fetch) is the
    same one `paginate` uses internally.
    """
    stmt = thread_stmt(ctx, conversation_id)
    total = (
        await session.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))
    ).scalar_one()
    rows = (await session.execute(stmt.offset(offset).limit(limit))).all()
    return list(rows), int(total)


async def get_inbound_message(
    session: AsyncSession, org_id: uuid.UUID, conversation_id: uuid.UUID, message_id: uuid.UUID
) -> InboundMessage | None:
    """One inbound message, scoped to both the organization and the conversation it claims to be
    in — a message id from another conversation must not unmask through this one."""
    stmt = select(InboundMessage).where(
        InboundMessage.id == message_id,
        InboundMessage.conversation_id == conversation_id,
        InboundMessage.organization_id == org_id,
        InboundMessage.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()
