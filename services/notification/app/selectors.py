"""Reads for the notification service. Visibility rules live here, in one place.

Every scoped query calls `ctx.apply(...)` **and** filters `organization_id` explicitly. The
explicit filter is not redundant with row-level security: RLS is the net under the tightrope,
not permission to walk without one (CLAUDE.md, ADR-0002). Both stay.

A list endpoint that builds its own query instead of coming through a selector is how an
organization leaks, so routers never write SQL.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, Select, exists, func, literal, or_, select, tuple_, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.config import settings
from app.models import (
    Campaign,
    CampaignRecipient,
    ConsentEvent,
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
    Segment,
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
    session: AsyncSession, org_id: uuid.UUID, message_id: uuid.UUID, *, for_update: bool = False
) -> Message | None:
    """One message. `for_update` takes the row lock (and refreshes an already-loaded copy), so
    a writer that decides on the row's state - the send claim, the retry re-queue - cannot decide
    on a state another transaction is changing at the same moment."""
    stmt = select(Message).where(
        Message.id == message_id,
        Message.organization_id == org_id,
        Message.deleted_at.is_(None),
    )
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
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


async def accounts_for_meta_user_id(
    session: AsyncSession, meta_user_id: str
) -> Sequence[ProviderAccount]:
    """Every WhatsApp connection whose Embedded Signup was completed by this Facebook user id --
    what Meta's deauthorize and data-deletion callbacks resolve against.

    **Deliberately not org-scoped**, for the same reason `account_for_phone_number` is not: a
    Meta-scoped user id names a *person*, not one organization, and this platform holds only one
    token per connection today, so the same person legitimately shows up on more than one
    organization's row (a consultant who set up WhatsApp for two customers) or on none (every
    connection made before `meta_user_id` was captured, or one made through the manual
    "paste a token" path, which never asks Meta's SDK for a user id at all). Runs on the directory
    session (SELECT-only, BYPASSRLS); the caller binds each returned row's own organization before
    touching anything else, once per row -- never once for the whole result, the same rule the
    webhook route's per-number resolution already follows.

    **Deliberately no `deleted_at IS NULL` filter**, unlike `account_for_phone_number` above: a
    person's request that their data be deleted must reach a soft-deleted row too (nothing
    soft-deletes `provider_accounts` today, but a lookup a data-deletion request depends on must
    not silently start skipping rows the day something does).
    """
    stmt = select(ProviderAccount).where(
        ProviderAccount.meta_user_id == meta_user_id,
        ProviderAccount.channel == "whatsapp",
    )
    return (await session.execute(stmt)).scalars().all()


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


async def first_usable_sender(session: AsyncSession, org_id: uuid.UUID) -> Sender | None:
    """The organization's oldest sender that has proved itself with a test send, or None.

    What a rule-driven email is sent as when the connection's own login is not an address
    (`app/tenant_transport.py`). Oldest first, `id` breaking a tie, so the choice is stable from
    one send to the next rather than whichever row the planner returned.
    """
    stmt = (
        select(Sender)
        .where(
            Sender.organization_id == org_id,
            Sender.deleted_at.is_(None),
            Sender.verified_at.is_not(None),
        )
        .order_by(Sender.created_at.asc(), Sender.id.asc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def setup_checklist(session: AsyncSession, ctx: ScopeContext) -> list[dict[str, Any]]:
    """The "get started" checklist: five steps, in the order an organization actually completes
    them, each folded from the same rows every other Engage screen already reads — nothing here
    is stored, and a step's status can never disagree with the screen it links to.

    `blocked` names a step that cannot be started until an earlier one is: a domain has nothing
    to verify against with no provider connected, a sender cannot be proven on a domain nobody
    has verified, and a campaign has no sender to pick until one has passed its own test send.
    """
    org_id = ctx.organization_id
    connection = await get_connection(session, org_id)
    domains = (await session.execute(domains_stmt(ctx))).scalars().all()
    senders = (await session.execute(senders_stmt(ctx))).scalars().all()
    contact_count = int(
        (
            await session.execute(
                select(func.count()).where(
                    Contact.organization_id == org_id, Contact.deleted_at.is_(None)
                )
            )
        ).scalar_one()
    )
    campaigns = (await session.execute(campaigns_stmt(ctx))).scalars().all()

    steps: list[dict[str, Any]] = []

    if connection is None:
        steps.append(
            {
                "step": "connection",
                "status": "todo",
                "detail": "No email provider connected yet.",
                "link": "/connection",
            }
        )
    elif connection.status != "connected":
        steps.append(
            {
                "step": "connection",
                "status": "todo",
                "detail": f"{connection.username} is disconnected.",
                "link": "/connection",
            }
        )
    else:
        steps.append(
            {
                "step": "connection",
                "status": "done",
                "detail": f"Connected via {connection.kind}.",
                "link": "/connection",
            }
        )

    verified_domains = [d for d in domains if d.status == "verified"]
    if connection is None:
        steps.append(
            {
                "step": "domain",
                "status": "blocked",
                "detail": "Connect your email provider first.",
                "link": "/domains",
            }
        )
    elif not domains:
        steps.append(
            {
                "step": "domain",
                "status": "todo",
                "detail": "No sending domain added yet.",
                "link": "/domains",
            }
        )
    elif not verified_domains:
        steps.append(
            {
                "step": "domain",
                "status": "todo",
                "detail": f"{len(domains)} domain(s) added, none verified yet.",
                "link": "/domains",
            }
        )
    else:
        steps.append(
            {
                "step": "domain",
                "status": "done",
                "detail": f"{len(verified_domains)} of {len(domains)} domain(s) verified.",
                "link": "/domains",
            }
        )

    usable_senders = [s for s in senders if s.usable]
    if not verified_domains:
        steps.append(
            {
                "step": "sender",
                "status": "blocked",
                "detail": "Verify a sending domain first.",
                "link": "/senders",
            }
        )
    elif not senders:
        steps.append(
            {
                "step": "sender",
                "status": "todo",
                "detail": "No sender added yet.",
                "link": "/senders",
            }
        )
    elif not usable_senders:
        steps.append(
            {
                "step": "sender",
                "status": "todo",
                "detail": f"{len(senders)} sender(s) added, none tested yet.",
                "link": "/senders",
            }
        )
    else:
        steps.append(
            {
                "step": "sender",
                "status": "done",
                "detail": f"{len(usable_senders)} of {len(senders)} sender(s) usable.",
                "link": "/senders",
            }
        )

    steps.append(
        {
            "step": "contacts",
            "status": "done" if contact_count else "todo",
            "detail": (
                f"{contact_count} contact(s) in your audience."
                if contact_count
                else "No contacts yet."
            ),
            "link": "/contacts",
        }
    )

    sent_campaigns = [c for c in campaigns if c.status in ("sending", "sent")]
    if not usable_senders:
        steps.append(
            {
                "step": "campaign",
                "status": "blocked",
                "detail": "Add a usable sender first.",
                "link": "/campaigns",
            }
        )
    elif not campaigns:
        steps.append(
            {
                "step": "campaign",
                "status": "todo",
                "detail": "No campaign created yet.",
                "link": "/campaigns",
            }
        )
    elif not sent_campaigns:
        steps.append(
            {
                "step": "campaign",
                "status": "todo",
                "detail": f"{len(campaigns)} draft campaign(s), none sent yet.",
                "link": "/campaigns",
            }
        )
    else:
        steps.append(
            {
                "step": "campaign",
                "status": "done",
                "detail": f"{len(sent_campaigns)} campaign(s) sent.",
                "link": "/campaigns",
            }
        )

    return steps


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


async def contact_by_index_including_deleted(
    session: AsyncSession, org_id: uuid.UUID, index: str
) -> Contact | None:
    """The contact row holding this address, deleted or not — `uq_contact_org_email` spans both,
    so a writer that would create one must know about a deleted row too."""
    stmt = select(Contact).where(Contact.organization_id == org_id, Contact.email_index == index)
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
    return {c.email_index: c for c in (await session.execute(stmt)).scalars() if c.email_index}


async def contact_by_crm_id(
    session: AsyncSession, org_id: uuid.UUID, crm_contact_id: uuid.UUID
) -> Contact | None:
    """The Engage copy of one CRM contact, if there is one."""
    stmt = select(Contact).where(
        Contact.organization_id == org_id, Contact.crm_contact_id == crm_contact_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()


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
    segment: Segment | None = None,
    lock: bool = False,
) -> list[Contact]:
    """Contacts a campaign may send to: the organization's contacts minus every suppressed
    address, whatever the reason, **and only those whose latest email/marketing consent event is
    an opt-in** (`consent.decide` — silence is not consent).

    The exclusion is an anti-join on the blind index, so it holds for a suppression written
    before the contact existed and for one written after — a re-imported address that once
    unsubscribed stays unreachable. The consent predicate is `latest_consent_is`, the SQL form
    of the decision `consent.decide` makes, restated here because the audience is read a page at
    a time and a decision per contact would be a query per row; `tests/test_consent.py` pins the
    two together. Keyset-paged by id (`after_id`) so a sender can walk a large audience without
    an OFFSET that shifts under concurrent writes.

    `lock=True` is `expand_campaign`'s: `SELECT ... FOR KEY SHARE` on every contact in the
    page, held until expansion's transaction commits. Postgres's `DELETE` takes a row lock that
    conflicts with `FOR KEY SHARE`, so a concurrent `delete_contact` on one of these rows blocks
    until this transaction ends — the read and the copy `expand_campaign` writes into
    `campaign_recipients` cannot straddle a deletion whose own `erase_contact_campaign_copies`
    ran and committed before the copy existed to erase. `FOR KEY SHARE` rather than a stronger
    mode because it is the one lock that never conflicts with another `expand_campaign` page
    reading the same contact for a different campaign, or with an ordinary read.
    """
    stmt = _email_audience_stmt(select(Contact), ctx, list_id, segment)
    if after_id is not None:
        stmt = stmt.where(Contact.id > after_id)
    stmt = stmt.order_by(Contact.id.asc()).limit(limit)
    if lock:
        stmt = stmt.with_for_update(key_share=True)
    return list((await session.execute(stmt)).scalars())


def _email_audience_stmt(
    stmt: Select[Any], ctx: ScopeContext, list_id: uuid.UUID | None, segment: Segment | None
) -> Select[Any]:
    """`sendable_contacts`'s predicate on any select over `Contact` — see its docstring."""
    suppressed = exists().where(
        Suppression.organization_id == Contact.organization_id,
        Suppression.email_index == Contact.email_index,
        Suppression.deleted_at.is_(None),
    )
    stmt = stmt.where(
        Contact.organization_id == ctx.organization_id,
        Contact.deleted_at.is_(None),
        # A phone-only CRM projection has no address to mail.
        Contact.email_index.is_not(None),
        ~suppressed,
        latest_consent_is(
            "opted_in",
            organization_id=Contact.organization_id,
            channel="email",
            purpose="marketing",
            subject_index=Contact.email_index,
        ),
    )
    return _narrow_audience(ctx.apply(stmt, owner_col=Contact.created_by), list_id, segment)


def _in_list(list_id: Any) -> ColumnElement[bool]:
    return exists().where(
        ContactListMember.organization_id == Contact.organization_id,
        ContactListMember.list_id == list_id,
        ContactListMember.contact_id == Contact.id,
    )


def _narrow_audience(
    stmt: Select[Any], list_id: uuid.UUID | None, segment: Segment | None
) -> Select[Any]:
    """A select over `Contact` narrowed to one list's members, or to a segment's filter.

    A segment is evaluated now, against today's contacts: its windows are relative to the moment
    of the read, which for a campaign is expansion."""
    if list_id is not None:
        stmt = stmt.where(_in_list(list_id))
    if segment is None:
        return stmt
    now = datetime.now(UTC)
    if segment.list_id is not None:
        stmt = stmt.where(_in_list(segment.list_id))
    if segment.created_within_days is not None:
        stmt = stmt.where(Contact.created_at >= now - timedelta(days=segment.created_within_days))
    if segment.crm_status is not None:
        stmt = stmt.where(Contact.attributes["crm_status"].astext == segment.crm_status)
    if segment.active_within_days is not None:
        since = now - timedelta(days=segment.active_within_days)
        stmt = stmt.where(
            exists().where(
                CampaignRecipient.organization_id == Contact.organization_id,
                CampaignRecipient.contact_id == Contact.id,
                CampaignRecipient.status == "sent",
                CampaignRecipient.sent_at >= since,
            )
        )
    return stmt


@dataclass(frozen=True)
class WhatsAppRecipient:
    """One contact a WhatsApp campaign may reach, and the number to reach them on — sealed, as
    the consent ledger holds it."""

    contact_id: uuid.UUID
    number_enc: bytes
    number_index: str


def _whatsapp_audience_stmt(
    ctx: ScopeContext, list_id: uuid.UUID | None, segment: Segment | None
) -> Select[Any]:
    """Contacts with a WhatsApp number on the consent ledger whose latest marketing event is an
    opt-in, with that number. The number is the one an opt-in naming the contact recorded
    (`record_consent_event` with `contact_id`); a contact with several takes the newest. The
    ledger decides per *number*, so a STOP from that number — which names no contact — still
    wins here."""
    event = aliased(ConsentEvent)
    stmt = (
        select(Contact.id, event.subject_enc, event.subject_index)
        .join(
            event,
            (event.organization_id == Contact.organization_id)
            # Named by the event, or (a CRM projection) holding the very number it is about.
            & ((event.contact_id == Contact.id) | (event.subject_index == Contact.phone_index))
            & (event.channel == "whatsapp")
            & (event.purpose == "marketing")
            & (event.status == "opted_in"),
        )
        .where(
            Contact.organization_id == ctx.organization_id,
            Contact.deleted_at.is_(None),
            latest_consent_is(
                "opted_in",
                organization_id=event.organization_id,
                channel="whatsapp",
                purpose="marketing",
                subject_index=event.subject_index,
            ),
        )
    )
    stmt = _narrow_audience(ctx.apply(stmt, owner_col=Contact.created_by), list_id, segment)
    return stmt.distinct(Contact.id).order_by(
        Contact.id.asc(), event.occurred_at.desc(), event.created_at.desc()
    )


async def whatsapp_recipients(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    limit: int = 1000,
    after_id: uuid.UUID | None = None,
    list_id: uuid.UUID | None = None,
    segment: Segment | None = None,
) -> list[WhatsAppRecipient]:
    """A WhatsApp campaign's audience, keyset-paged by contact id like `sendable_contacts`."""
    stmt = _whatsapp_audience_stmt(ctx, list_id, segment)
    if after_id is not None:
        stmt = stmt.where(Contact.id > after_id)
    rows = (await session.execute(stmt.limit(limit))).all()
    return [WhatsAppRecipient(r[0], r[1], r[2]) for r in rows]


async def count_audience(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    channel: str,
    list_id: uuid.UUID | None = None,
    segment: Segment | None = None,
) -> int:
    """How many contacts a campaign on `channel` would reach right now."""
    if channel == "whatsapp":
        inner = _whatsapp_audience_stmt(ctx, list_id, segment).subquery()
    else:
        inner = _email_audience_stmt(select(Contact.id), ctx, list_id, segment).subquery()
    return int((await session.execute(select(func.count()).select_from(inner))).scalar_one())


# --------------------------------------------------------------------- engage segments


def segments_stmt(ctx: ScopeContext) -> Select[Any]:
    stmt = select(Segment).where(
        Segment.organization_id == ctx.organization_id, Segment.deleted_at.is_(None)
    )
    stmt = ctx.apply(stmt, owner_col=Segment.created_by)
    return stmt.order_by(Segment.name.asc(), Segment.id.asc())


async def get_segment(
    session: AsyncSession, org_id: uuid.UUID, segment_id: uuid.UUID
) -> Segment | None:
    stmt = select(Segment).where(
        Segment.id == segment_id, Segment.organization_id == org_id, Segment.deleted_at.is_(None)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_segment_by_name(
    session: AsyncSession, org_id: uuid.UUID, name: str
) -> Segment | None:
    stmt = select(Segment).where(
        Segment.name == name, Segment.organization_id == org_id, Segment.deleted_at.is_(None)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def segments_using_list(session: AsyncSession, org_id: uuid.UUID, list_id: uuid.UUID) -> int:
    stmt = select(func.count()).where(Segment.organization_id == org_id, Segment.list_id == list_id)
    return int((await session.execute(stmt)).scalar_one())


async def campaigns_using_segment(
    session: AsyncSession, org_id: uuid.UUID, segment_id: uuid.UUID
) -> int:
    stmt = select(func.count()).where(
        Campaign.organization_id == org_id, Campaign.segment_id == segment_id
    )
    return int((await session.execute(stmt)).scalar_one())


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


async def campaigns_using_sender(
    session: AsyncSession, org_id: uuid.UUID, sender_id: uuid.UUID
) -> int:
    """How many of this sender's campaigns are not a draft or cancelled — the count
    `delete_domain` refuses to delete a sender (and so its domain) out from under.

    Draft and cancelled are excluded because neither ever reached anyone: a draft's sender choice
    is not yet a fact about mail that went out, and a cancelled send stopped before or during its
    run with its own record of who it actually reached — deleting the sender changes neither.
    """
    stmt = select(func.count()).where(
        Campaign.organization_id == org_id,
        Campaign.sender_id == sender_id,
        Campaign.status.notin_(("draft", "cancelled")),
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


# --------------------------------------------------------------------- consent ledger
#
# `app/consent.py` is the one decision; these are its reads. Every one filters `organization_id`
# explicitly, RLS or not.


def latest_consent_is(
    status: str,
    *,
    organization_id: Any,
    channel: str,
    purpose: str,
    subject_index: Any,
) -> ColumnElement[bool]:
    """`EXISTS` an event with `status` for this subject that no later event supersedes — "the
    latest event wins", as a correlated predicate an index can answer per row.

    "Later" is `(occurred_at, created_at, id)`, the ordering every reader of the ledger shares.
    Correlated on the caller's own columns (a contact's `organization_id` and `email_index`), so
    it composes into any `select`.
    """
    event = aliased(ConsentEvent)
    newer = aliased(ConsentEvent)
    superseded = exists().where(
        newer.organization_id == event.organization_id,
        newer.channel == event.channel,
        newer.purpose == event.purpose,
        newer.subject_index == event.subject_index,
        tuple_(newer.occurred_at, newer.created_at, newer.id)
        > tuple_(event.occurred_at, event.created_at, event.id),
    )
    return exists().where(
        event.organization_id == organization_id,
        event.channel == channel,
        event.purpose == purpose,
        event.subject_index == subject_index,
        event.status == status,
        ~superseded,
    )


async def latest_consent_status(
    session: AsyncSession,
    org_id: uuid.UUID,
    channel: str,
    purpose: str,
    indexes: list[str],
) -> dict[str, str]:
    """`{subject_index: status}` of each subject's latest event on one channel and purpose, in one
    query. A subject with no event is simply absent."""
    if not indexes:
        return {}
    stmt = (
        select(ConsentEvent.subject_index, ConsentEvent.status)
        .where(
            ConsentEvent.organization_id == org_id,
            ConsentEvent.channel == channel,
            ConsentEvent.purpose == purpose,
            ConsentEvent.subject_index.in_(indexes),
        )
        .distinct(ConsentEvent.subject_index)
        .order_by(
            ConsentEvent.subject_index,
            ConsentEvent.occurred_at.desc(),
            ConsentEvent.created_at.desc(),
            ConsentEvent.id.desc(),
        )
    )
    return {row[0]: row[1] for row in (await session.execute(stmt)).all()}


async def latest_consent_status_by_purpose(
    session: AsyncSession,
    org_id: uuid.UUID,
    channel: str,
    index: str,
    purposes: Sequence[str],
) -> dict[str, str]:
    """`{purpose: status}` of one subject's latest event in each of `purposes`."""
    stmt = (
        select(ConsentEvent.purpose, ConsentEvent.status)
        .where(
            ConsentEvent.organization_id == org_id,
            ConsentEvent.channel == channel,
            ConsentEvent.subject_index == index,
            ConsentEvent.purpose.in_(list(purposes)),
        )
        .distinct(ConsentEvent.purpose)
        .order_by(
            ConsentEvent.purpose,
            ConsentEvent.occurred_at.desc(),
            ConsentEvent.created_at.desc(),
            ConsentEvent.id.desc(),
        )
    )
    return {row[0]: row[1] for row in (await session.execute(stmt)).all()}


async def ever_opted_in_purposes(
    session: AsyncSession, org_id: uuid.UUID, channel: str, index: str
) -> set[str]:
    """Which purposes this subject has, at any point, opted in to — what `consent.restore` checks
    before it will re-open marketing."""
    stmt = (
        select(ConsentEvent.purpose)
        .where(
            ConsentEvent.organization_id == org_id,
            ConsentEvent.channel == channel,
            ConsentEvent.subject_index == index,
            ConsentEvent.status == "opted_in",
        )
        .distinct()
    )
    return set((await session.execute(stmt)).scalars())


async def suppression_reasons(
    session: AsyncSession, org_id: uuid.UUID, indexes: list[str]
) -> dict[str, str]:
    """`{email_index: reason}` for the suppressed ones among `indexes`, in one query — the legacy
    store the consent decision still honours."""
    if not indexes:
        return {}
    stmt = select(Suppression.email_index, Suppression.reason).where(
        Suppression.organization_id == org_id,
        Suppression.email_index.in_(indexes),
        Suppression.deleted_at.is_(None),
    )
    return {row[0]: row[1] for row in (await session.execute(stmt)).all()}


def consent_events_stmt(
    ctx: ScopeContext,
    *,
    channel: str | None = None,
    purpose: str | None = None,
    status: str | None = None,
    subject_index: str | None = None,
    linked_contact_id: uuid.UUID | None = None,
) -> Select[Any]:
    """Scope-filtered consent history, newest first. `ctx.apply` is the enforcement point.

    `linked_contact_id` keeps every event about any number an event linked to that contact —
    including a STOP from that number, which names no contact."""
    stmt = select(ConsentEvent).where(ConsentEvent.organization_id == ctx.organization_id)
    stmt = ctx.apply(stmt, owner_col=ConsentEvent.recorded_by)
    if channel:
        stmt = stmt.where(ConsentEvent.channel == channel)
    if purpose:
        stmt = stmt.where(ConsentEvent.purpose == purpose)
    if status:
        stmt = stmt.where(ConsentEvent.status == status)
    if subject_index:
        stmt = stmt.where(ConsentEvent.subject_index == subject_index)
    if linked_contact_id is not None:
        linked = select(ConsentEvent.subject_index).where(
            ConsentEvent.organization_id == ctx.organization_id,
            ConsentEvent.contact_id == linked_contact_id,
            ConsentEvent.channel == channel,
        )
        stmt = stmt.where(ConsentEvent.subject_index.in_(linked))
    return stmt.order_by(
        ConsentEvent.occurred_at.desc(), ConsentEvent.created_at.desc(), ConsentEvent.id.desc()
    )


async def latest_linked_consent_event(
    session: AsyncSession, org_id: uuid.UUID, contact_id: uuid.UUID, channel: str
) -> ConsentEvent | None:
    """The newest event that linked `contact_id` to an address on `channel` — the number a
    withdrawal from the contact view is about."""
    stmt = (
        select(ConsentEvent)
        .where(
            ConsentEvent.organization_id == org_id,
            ConsentEvent.contact_id == contact_id,
            ConsentEvent.channel == channel,
        )
        .order_by(
            ConsentEvent.occurred_at.desc(),
            ConsentEvent.created_at.desc(),
            ConsentEvent.id.desc(),
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_consent_event(
    session: AsyncSession, org_id: uuid.UUID, event_id: uuid.UUID
) -> ConsentEvent | None:
    """Org-scoped fetch. None on a tenant mismatch — confirming an id exists in *some*
    organization is itself a disclosure."""
    stmt = select(ConsentEvent).where(
        ConsentEvent.id == event_id, ConsentEvent.organization_id == org_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_consent_event_by_key(
    session: AsyncSession, org_id: uuid.UUID, key: str
) -> ConsentEvent | None:
    """The event an idempotency key already wrote, if any."""
    stmt = select(ConsentEvent).where(
        ConsentEvent.organization_id == org_id, ConsentEvent.idempotency_key == key
    )
    return (await session.execute(stmt)).scalar_one_or_none()


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
