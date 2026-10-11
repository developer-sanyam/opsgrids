"""HTTP surface for the `engage` product: an organization's own sending identity.

A second router on this service rather than more routes in `routes.py`, because it is a second
*product*. `notification` is the platform's transactional plumbing — templates, rules, the
message log — and every route there is gated on the `notification` entitlement. Engage is what a
customer buys, gated on `engage`, built from its own catalogue. Two products served by one
process is only a boundary if the gates tell them apart, and one file per gate is how that stays
visible.

Mounted behind the gateway's `/api/v1/engage` prefix, so every path here begins `/engage/...`.
Reads go through `selectors`, writes through `services`; no route builds a query and none opens
a transaction of its own — the chokepoint owns it.

**Nothing here returns a credential.** `ConnectionOut` has no field derived from the app
password, and the sealed DKIM key has no field at all. The single place either plaintext exists
is the SMTP handshake in `services._transport_for_connection`.
"""

from __future__ import annotations

import html
import uuid
from typing import Annotated, Any, Final

from fastapi import APIRouter, Body, Depends, Query, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app import consent, dns_check, selectors, services, w2_selectors, webhooks
from app import domain as _rules
from app.api.w2_templates import template_out as _whatsapp_template_out
from app.domain import UnsubscribeTarget
from app.models import (
    Campaign,
    ConsentEvent,
    Contact,
    ContactList,
    Conversation,
    EmailConnection,
    EmailDomain,
    ProviderAccount,
    ProviderTemplate,
    Sender,
    Suppression,
    Template,
)
from app.schemas import (
    CampaignIn,
    CampaignOut,
    CampaignPatch,
    CampaignRecipientOut,
    CampaignScheduleIn,
    CampaignStats,
    CampaignStatus,
    CampaignTestSendOut,
    ConnectionIn,
    ConnectionOut,
    ConsentChannel,
    ConsentDecisionOut,
    ConsentEventIn,
    ConsentEventOut,
    ConsentLookupIn,
    ConsentPurpose,
    ConsentRecordedOut,
    ConsentStatus,
    ConsentUnmaskedOut,
    ContactImportIn,
    ContactImportOut,
    ContactImportResult,
    ContactIn,
    ContactListIn,
    ContactListOut,
    ContactListPatch,
    ContactOut,
    ContactPatch,
    ConversationAssignIn,
    ConversationMessageOut,
    ConversationOut,
    ConversationReplyIn,
    ConversationReplyOut,
    ConversationStatus,
    DnsRecordOut,
    DomainIn,
    DomainOut,
    DomainPatch,
    ListMembersIn,
    ListMembersOut,
    ProviderTemplateIn,
    ProviderTemplateOut,
    RecipientStatus,
    SenderIn,
    SenderOut,
    SetupOut,
    SetupStepOut,
    SuppressionIn,
    SuppressionOut,
    SuppressionReason,
    TemplateCategory,
    TemplateIn,
    TemplateOut,
    TemplateStatus,
    TestSendIn,
    UnmaskedMessageOut,
    UnsubscribeOut,
    WhatsAppConnectionIn,
    WhatsAppConnectionOut,
)
from app.w2_schemas import WhatsAppTemplateOut
from platform_core.auth import Scope, ScopeContext, require, require_product
from platform_core.config import ServiceSettings
from platform_core.db import (
    assert_tenant,
    get_directory_session,
    get_session,
    get_tenant_session,
    set_tenant,
)
from platform_core.errors import NotFoundProblem, ValidationProblem
from platform_core.pagination import Page, PageQuery, paginate
from platform_core.pii import mask as mask_pii

router = APIRouter(tags=["engage"])

#: The cross-tenant, BYPASSRLS, SELECT-only reader — the same one the webhook route resolves a
#: tenant from. The WhatsApp connection writes below are the other caller: a phone number's
#: global-uniqueness check must see every organization's row, which the tenant-scoped `session`
#: (RLS-filtered to the caller's own organization) never can.
DirectorySessionDep = Annotated[AsyncSession, Depends(get_directory_session)]

#: **The tenancy chokepoint.** Opens the transaction and binds it to the caller's organization
#: before the route body runs, so no route can read or write without one.
SessionDep = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]

#: The one route in this file that does **not** take `SessionDep` — see `verify_domain` below.
#: A raw, untransacted session; the route binds its own two short transactions around the DNS
#: lookup, the same idiom payment's and finance's internal APIs use for the same reason (D3).
RawSessionDep = Annotated[AsyncSession, Depends(get_session)]

#: The `engage` entitlement gate, applied to every route here as well as at the gateway. It is
#: deliberately not `notification`: a customer paying for transactional plumbing has not bought
#: the product that sends as their own domain.
Entitled = Depends(require_product("engage"))

# Every permission is floored at ORG, like notification's. A sending domain and a sender belong
# to the organization rather than to whoever happened to add them, so there is no "my own"
# reading to protect and an OWN grant is refused rather than silently narrowed to nothing.
ConnectionRead = Annotated[
    ScopeContext, Depends(require("engage.setup.connection.read", minimum=Scope.ORG))
]
ConnectionWrite = Annotated[
    ScopeContext, Depends(require("engage.setup.connection.write", minimum=Scope.ORG))
]
DomainRead = Annotated[
    ScopeContext, Depends(require("engage.setup.domain.read", minimum=Scope.ORG))
]
DomainWrite = Annotated[
    ScopeContext, Depends(require("engage.setup.domain.write", minimum=Scope.ORG))
]
SenderRead = Annotated[
    ScopeContext, Depends(require("engage.setup.sender.read", minimum=Scope.ORG))
]
#: The test send writes `verified_at`, which is what makes a sender selectable by a campaign.
#: That is an edit to the sender, not a read of it, so it takes the write permission.
SenderWrite = Annotated[
    ScopeContext, Depends(require("engage.setup.sender.write", minimum=Scope.ORG))
]
WhatsAppRead = Annotated[
    ScopeContext, Depends(require("engage.setup.whatsapp.read", minimum=Scope.ORG))
]
WhatsAppWrite = Annotated[
    ScopeContext, Depends(require("engage.setup.whatsapp.write", minimum=Scope.ORG))
]
#: Its own permission from the account credential above — see the catalogue note on
#: `whatsapp.template.*` for why the two are not folded together.
WhatsAppTemplateRead = Annotated[
    ScopeContext, Depends(require("engage.whatsapp.template.read", minimum=Scope.ORG))
]
WhatsAppTemplateWrite = Annotated[
    ScopeContext, Depends(require("engage.whatsapp.template.write", minimum=Scope.ORG))
]
#: Its own permission from the account credential above, mirroring `whatsapp.template.*` — see
#: the catalogue note on `email.template.*` for why the two are not folded together.
EmailTemplateRead = Annotated[
    ScopeContext, Depends(require("engage.email.template.read", minimum=Scope.ORG))
]
EmailTemplateWrite = Annotated[
    ScopeContext, Depends(require("engage.email.template.write", minimum=Scope.ORG))
]
ContactRead = Annotated[
    ScopeContext, Depends(require("engage.audience.contact.read", minimum=Scope.ORG))
]
ContactWrite = Annotated[
    ScopeContext, Depends(require("engage.audience.contact.write", minimum=Scope.ORG))
]
SuppressionRead = Annotated[
    ScopeContext, Depends(require("engage.audience.suppression.read", minimum=Scope.ORG))
]
SuppressionWrite = Annotated[
    ScopeContext, Depends(require("engage.audience.suppression.write", minimum=Scope.ORG))
]
ConversationRead = Annotated[
    ScopeContext, Depends(require("engage.inbox.conversation.read", minimum=Scope.ORG))
]
ConversationWrite = Annotated[
    ScopeContext, Depends(require("engage.inbox.conversation.write", minimum=Scope.ORG))
]
MessageReply = Annotated[
    ScopeContext, Depends(require("engage.inbox.message.reply", minimum=Scope.ORG))
]
#: The dangerous, stepped-up unmask. `amr={"otp"}` pairs with the permission the same way
#: `crm.book.contact.read_sensitive` does — an ordinary inbox session must not be a standing
#: route to a customer's real words.
MessageUnmask = Annotated[
    ScopeContext,
    Depends(require("engage.inbox.message.unmask", minimum=Scope.ORG, amr={"otp"})),
]
#: The same permission and the same step-up, reused for opening a consent event's address: there
#: is no separate "unmask a consent subject" code, and adding one would need a production role
#: reconcile. Revealing who a consent event is about is the same class of act as revealing a
#: customer's words, and is gated, stepped-up and audited exactly the same way.
ConsentUnmask = MessageUnmask


# --------------------------------------------------------------------- assemblers


def _connection_out(row: EmailConnection) -> ConnectionOut:
    """Everything about the connection except the one thing that must never leave the process.

    Written field by field rather than from the row wholesale: a model that serialised whatever
    columns it found would start returning `secret_enc` the day someone renamed it.
    """
    return ConnectionOut(
        kind=row.kind,
        username=row.username,
        host=row.host,
        port=row.port,
        status=row.status,
        daily_cap=row.daily_cap,
    )


def _whatsapp_connection_out(row: ProviderAccount) -> WhatsAppConnectionOut:
    """Everything about the WhatsApp account except the access token, which never leaves the
    process — see `ConnectionOut`'s counterpart for the same rule on the email side."""
    return WhatsAppConnectionOut(
        waba_id=row.waba_id,
        # Empty once disconnected: the routing key is released, and the wire type is a string.
        phone_number_id=row.phone_number_id or "",
        display_number=row.display_number,
        status=row.status,
    )


#: The kinds `domain.expected_records` ever emits. A stored record naming anything else — or
#: missing a field the current shape requires — is not this shape, whatever else it looks like.
_VALID_RECORD_KINDS: Final = frozenset({"spf", "dkim", "dmarc"})


def _has_current_shape(records: list[dict[str, Any]]) -> bool:
    """Whether `records` is a set this version of the service could have written, rather than a
    pre-fix snapshot (`host`/`verified` keys, `return_path`/`tracking` kinds) a domain added
    before the provider-aware rewrite could still be holding.

    Checked here as well as reset once, for every existing row, by the migration
    (`d8f3a52c9e14`'s data reset) — belt and suspenders: `DnsRecordOut(**record)` raises on the
    old shape, and one row holding it must never take a whole organization's domain list down
    with a 500, whether or not the migration's own `WHERE` clause reached it.
    """
    if not records:
        return False
    for record in records:
        # The column's declared type says every entry is already a dict; JSONB loaded from a row
        # nobody has touched since before this shape existed makes no such promise, so this is
        # checked through `object` rather than trusted from the annotation.
        candidate: object = record
        if not isinstance(candidate, dict):
            return False
        if candidate.get("kind") not in _VALID_RECORD_KINDS:
            return False
        if "status" not in candidate or "fqdn" not in candidate:
            return False
    return True


def _domain_out(row: EmailDomain, connection: EmailConnection | None) -> DomainOut:
    """`connection` names the organization's *current* provider, fetched once per request by the
    caller (a list of domains shares one connection read) — never the domain's own history: the
    record set shown is what this domain needs **now**, not what it needed under a provider this
    organization may have already switched away from.

    A domain that has never been checked (`records` empty, or holding the pre-fix shape —
    `_has_current_shape` below) still has to show the customer something to publish before they
    can even press *verify* — so with a connection in hand this previews
    `domain.expected_records` as all `missing`, the honest "nothing found yet" state for records
    nobody has looked up. With no connection at all there is nothing to preview, and the empty
    list is exactly the "connect your email provider first" signal the setup screen shows.
    """
    if _has_current_shape(row.records):
        records = row.records
    elif connection is not None:
        records = [
            {**record, "status": "missing"}
            for record in _rules.expected_records(
                row.domain,
                connection_kind=connection.kind,
                dkim_selector=row.dkim_selector,
                spf_include=row.spf_include,
            )
        ]
    else:
        records = []
    return DomainOut(
        id=str(row.id),
        domain=row.domain,
        status=row.status,
        connection_kind=connection.kind if connection else None,
        dkim_selector=row.dkim_selector,
        spf_include=row.spf_include,
        records=[DnsRecordOut(**record) for record in records],
        last_checked_at=row.last_checked_at,
        created_at=row.created_at,
    )


def _sender_out(row: Sender) -> SenderOut:
    return SenderOut(
        id=str(row.id),
        domain_id=str(row.domain_id),
        name=row.name,
        address=row.address,
        reply_to=row.reply_to,
        signature=row.signature,
        department=row.department,
        usable=row.usable,
        verified_at=row.verified_at,
        created_at=row.created_at,
    )


def _contact_out(row: Contact) -> ContactOut:
    """The mask, never the address. `email_enc` has no field here at all."""
    return ContactOut(
        id=str(row.id),
        email=row.email_masked,
        name=row.name,
        attributes=row.attributes,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _suppression_out(row: Suppression) -> SuppressionOut:
    return SuppressionOut(
        id=str(row.id),
        email=row.email_masked,
        reason=row.reason,
        created_at=row.created_at,
    )


def _conversation_out(row: Conversation) -> ConversationOut:
    """The mask, never the address — same rule as `_contact_out`. `within_service_window` is
    computed at read time so the inbox never reimplements the 24-hour rule client-side."""
    return ConversationOut(
        id=str(row.id),
        channel=row.channel,
        wa_address=row.wa_address_masked,
        contact_id=str(row.contact_id) if row.contact_id else None,
        last_customer_message_at=row.last_customer_message_at,
        within_service_window=webhooks.within_service_window(row.last_customer_message_at),
        status=row.status,
        assignee_membership_id=(
            str(row.assignee_membership_id) if row.assignee_membership_id else None
        ),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# --------------------------------------------------------------------- the provider connection


@router.get(
    "/engage/connection",
    response_model=Page[ConnectionOut],
    summary="How this organization sends email",
    dependencies=[Entitled],
)
async def read_connection(
    session: SessionDep, ctx: ConnectionRead, params: PageQuery
) -> Page[ConnectionOut]:
    """A page of at most one.

    A list shape for a singleton reads oddly until you ask what the alternative costs: a route
    returning `ConnectionOut | None` puts "is there one yet?" in the router, where every other
    read in this service keeps it in the selector, and makes the unconnected case a null the
    setup screen has to special-case rather than an empty page it already handles.
    """
    stmt = selectors.connection_stmt(ctx)
    rows, total = await paginate(session, stmt, params)
    return Page.build([_connection_out(r) for r in rows], total=total, params=params)


@router.post(
    "/engage/connection",
    response_model=ConnectionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Connect the organization's email provider",
    dependencies=[Entitled],
)
async def connect_provider(
    body: ConnectionIn, session: SessionDep, ctx: ConnectionWrite, request: Request
) -> ConnectionOut:
    """POST rather than PUT, because one organization gets one connection and a second is
    refused. A verb that promises to be repeatable, and then fails the second time it is
    repeated, promises something the writer does not deliver."""
    row = await services.create_connection(
        session,
        ctx,
        kind=body.kind,
        username=body.username,
        secret=body.secret,
        host=body.host,
        port=body.port,
        request=request,
    )
    return _connection_out(row)


@router.put(
    "/engage/connection",
    response_model=ConnectionOut,
    summary="Replace the organization's email connection",
    dependencies=[Entitled],
)
async def replace_provider(
    body: ConnectionIn, session: SessionDep, ctx: ConnectionWrite, request: Request
) -> ConnectionOut:
    """PUT rather than POST, for the reverse of `connect_provider`'s reason: this replaces the
    one connection a caller already knows exists, and calling it twice with the same body
    leaves the same state — an app password rotated to the same value, or a provider switched
    to the same provider, is not a new fact each time.

    Same request shape as the POST, because what a caller supplies to replace a connection is
    exactly what they supply to create one — a second schema for the same four fields would be
    a distinction with no difference for anyone filling in the form.
    """
    row = await services.replace_connection(
        session,
        ctx,
        kind=body.kind,
        username=body.username,
        secret=body.secret,
        host=body.host,
        port=body.port,
        request=request,
    )
    return _connection_out(row)


# --------------------------------------------------------------------- the whatsapp connection


@router.get(
    "/engage/whatsapp/connection",
    response_model=Page[WhatsAppConnectionOut],
    summary="How this organization sends WhatsApp",
    dependencies=[Entitled],
)
async def read_whatsapp_connection(
    session: SessionDep, ctx: WhatsAppRead, params: PageQuery
) -> Page[WhatsAppConnectionOut]:
    """A page of at most one, same shape as `read_connection` and for the same reason: the
    setup screen already handles an empty page, so "not connected yet" needs no special case."""
    stmt = selectors.whatsapp_connection_stmt(ctx)
    rows, total = await paginate(session, stmt, params)
    return Page.build([_whatsapp_connection_out(r) for r in rows], total=total, params=params)


@router.post(
    "/engage/whatsapp/connection",
    response_model=WhatsAppConnectionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Connect the organization's WhatsApp Business account",
    dependencies=[Entitled],
)
async def connect_whatsapp(
    body: WhatsAppConnectionIn,
    session: SessionDep,
    directory: DirectorySessionDep,
    ctx: WhatsAppWrite,
    request: Request,
) -> WhatsAppConnectionOut:
    """POST rather than PUT, for the reason `connect_provider` is: one organization gets one
    WhatsApp account and a second POST is refused rather than silently accepted."""
    row = await services.create_whatsapp_connection(
        session,
        ctx,
        directory=directory,
        waba_id=body.waba_id,
        phone_number_id=body.phone_number_id,
        display_number=body.display_number,
        access_token=body.access_token,
        request=request,
    )
    return _whatsapp_connection_out(row)


@router.put(
    "/engage/whatsapp/connection",
    response_model=WhatsAppConnectionOut,
    summary="Replace the organization's WhatsApp Business account",
    dependencies=[Entitled],
)
async def replace_whatsapp(
    body: WhatsAppConnectionIn,
    session: SessionDep,
    directory: DirectorySessionDep,
    ctx: WhatsAppWrite,
    request: Request,
) -> WhatsAppConnectionOut:
    row = await services.replace_whatsapp_connection(
        session,
        ctx,
        directory=directory,
        waba_id=body.waba_id,
        phone_number_id=body.phone_number_id,
        display_number=body.display_number,
        access_token=body.access_token,
        request=request,
    )
    return _whatsapp_connection_out(row)


# --------------------------------------------------------------------- whatsapp templates


def _provider_template_out(row: ProviderTemplate) -> ProviderTemplateOut:
    """Same shape as `routes._provider_template_out` — each router keeps its own assembler
    rather than importing the other's, the pattern every `_x_out` function in this file follows.
    Only the permission that reaches it differs; the row and the fields never do."""
    return ProviderTemplateOut(
        id=row.code,
        channel=row.channel,
        provider_name=row.provider_name,
        provider_template_id=row.provider_template_id,
        language=row.language,
        category=row.category,
        parameter_format=row.parameter_format,
        status=row.status,
        status_reason=row.status_reason,
        status_checked_at=row.status_checked_at,
        variables=list(row.variables),
        preview_text=row.preview_text,
        # Derived rather than stored, so it can never disagree with `status`.
        sendable=_rules.template_is_sendable(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get(
    "/engage/whatsapp/templates",
    response_model=Page[WhatsAppTemplateOut],
    summary="WhatsApp templates and their real approval status",
    dependencies=[Entitled],
)
async def list_whatsapp_templates(
    session: SessionDep,
    ctx: WhatsAppTemplateRead,
    params: PageQuery,
    channel: Annotated[str | None, Query(description="email or whatsapp")] = "whatsapp",
    category: Annotated[TemplateCategory | None, Query()] = None,
    template_status: Annotated[TemplateStatus | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=100, description="Name or wording contains")] = None,
) -> Page[WhatsAppTemplateOut]:
    """What can be sent outside a conversation window, and what is still in review.

    The same rows `notification`'s own `GET /notifications/provider-templates` reads
    (`selectors.provider_templates_stmt`) — read here under Engage's own permission so an
    organization that has bought Engage but not the transactional `notification` product can
    still see the real approval status of the templates it sends through its own WhatsApp
    account, rather than a 403 for a permission it was never sold.

    Filterable by category, status and a search over name and wording (`w2_selectors`).
    """
    stmt = w2_selectors.templates_stmt(
        ctx, channel=channel, category=category, status=template_status, q=q
    )
    rows, total = await paginate(session, stmt, params)
    return Page.build([_whatsapp_template_out(r) for r in rows], total=total, params=params)


@router.put(
    "/engage/whatsapp/templates",
    response_model=ProviderTemplateOut,
    summary="Register or update a WhatsApp template",
    dependencies=[Entitled],
)
async def put_whatsapp_template(
    body: ProviderTemplateIn,
    session: SessionDep,
    ctx: WhatsAppTemplateWrite,
    request: Request,
) -> ProviderTemplateOut:
    """Same write `notification`'s own `PUT /notifications/provider-templates` makes
    (`services.upsert_provider_template`) — the row is the platform's, the permission is
    Engage's own."""
    row = await services.upsert_provider_template(
        session,
        ctx,
        code=body.code,
        channel=body.channel,
        provider_name=body.provider_name,
        language=body.language,
        category=body.category,
        parameter_format=body.parameter_format,
        status=body.status,
        status_reason=body.status_reason,
        variables=[v.model_dump() for v in body.variables],
        preview_text=body.preview_text,
        request=request,
    )
    return _provider_template_out(row)


# --------------------------------------------------------------------- email templates


def _template_out(row: Template) -> TemplateOut:
    """Same shape as `routes._template_out` — each router keeps its own assembler rather than
    importing the other's, the pattern every `_x_out` function in this file follows. Only the
    permission that reaches it differs; the row and the fields never do."""
    refs = _rules.placeholders(row.body) | _rules.placeholders(row.subject or "")
    return TemplateOut(
        id=row.code,
        channel=row.channel,
        subject=row.subject,
        body=row.body,
        description=row.description,
        placeholders=sorted(refs),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get(
    "/engage/email/templates",
    response_model=Page[TemplateOut],
    summary="Email templates",
    dependencies=[Entitled],
)
async def list_email_templates(
    session: SessionDep,
    ctx: EmailTemplateRead,
    params: PageQuery,
) -> Page[TemplateOut]:
    """The same rows `notification`'s own `GET /notifications/templates` reads
    (`selectors.templates_stmt`) — read here under Engage's own permission so an organization
    that has bought Engage but not the transactional `notification` product can still see the
    email templates it sends through its own account, rather than a 403 for a permission it was
    never sold.
    """
    stmt = selectors.templates_stmt(ctx, channel="email")
    rows, total = await paginate(session, stmt, params)
    return Page.build([_template_out(r) for r in rows], total=total, params=params)


@router.put(
    "/engage/email/templates",
    response_model=TemplateOut,
    status_code=status.HTTP_200_OK,
    summary="Create or replace an email template",
    dependencies=[Entitled],
)
async def put_email_template(
    body: TemplateIn, session: SessionDep, ctx: EmailTemplateWrite, request: Request
) -> TemplateOut:
    """Same write `notification`'s own `PUT /notifications/templates` makes
    (`services.upsert_template`) — the row is the platform's, the permission is Engage's own."""
    row = await services.upsert_template(
        session,
        ctx,
        code=body.code,
        channel=body.channel,
        subject=body.subject,
        body=body.body,
        description=body.description,
        request=request,
    )
    return _template_out(row)


# --------------------------------------------------------------------- setup checklist


@router.get(
    "/engage/setup",
    response_model=SetupOut,
    summary="The get-started checklist: five steps, and where each one stands",
    dependencies=[Entitled],
)
async def get_setup(session: SessionDep, ctx: ConnectionRead) -> SetupOut:
    """Gated on the same permission as reading the connection — the first of the five steps —
    rather than a new one of its own: this is a summary of facts every one of the five
    permissions below already lets someone read individually, and the whole point of the
    checklist is to be the one screen that does not make an organization's first user collect
    five grants before they can see where they stand.
    """
    steps = await selectors.setup_checklist(session, ctx)
    return SetupOut(steps=[SetupStepOut(**step) for step in steps])


# --------------------------------------------------------------------- sending domains


@router.get(
    "/engage/domains",
    response_model=Page[DomainOut],
    summary="Sending domains and the state of their DNS",
    dependencies=[Entitled],
)
async def list_domains(
    session: SessionDep,
    ctx: DomainRead,
    params: PageQuery,
    domain_status: Annotated[str | None, Query(alias="status")] = None,
) -> Page[DomainOut]:
    stmt = selectors.domains_stmt(ctx, status=domain_status)
    rows, total = await paginate(session, stmt, params)
    connection = await selectors.get_connection(session, ctx.organization_id)
    return Page.build([_domain_out(r, connection) for r in rows], total=total, params=params)


@router.post(
    "/engage/domains",
    response_model=DomainOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a sending domain",
    dependencies=[Entitled],
)
async def add_domain(
    body: DomainIn, session: SessionDep, ctx: DomainWrite, request: Request
) -> DomainOut:
    row = await services.create_domain(session, ctx, domain=body.domain, request=request)
    connection = await selectors.get_connection(session, ctx.organization_id)
    return _domain_out(row, connection)


@router.patch(
    "/engage/domains/{domain_id}",
    response_model=DomainOut,
    summary="Edit a domain's DKIM selector or SMTP SPF include",
    dependencies=[Entitled],
)
async def edit_domain(
    domain_id: uuid.UUID, body: DomainPatch, session: SessionDep, ctx: DomainWrite, request: Request
) -> DomainOut:
    row = await services.update_domain_settings(
        session,
        ctx,
        domain_id=domain_id,
        dkim_selector=body.dkim_selector,
        spf_include=body.spf_include,
        request=request,
    )
    connection = await selectors.get_connection(session, ctx.organization_id)
    return _domain_out(row, connection)


@router.delete(
    "/engage/domains/{domain_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a sending domain and its senders",
    dependencies=[Entitled],
)
async def remove_domain(
    domain_id: uuid.UUID, session: SessionDep, ctx: DomainWrite, request: Request
) -> Response:
    await services.delete_domain(session, ctx, domain_id=domain_id, request=request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/engage/domains/{domain_id}/verify",
    response_model=DomainOut,
    summary="Re-read DNS and fold the answer into the domain's status",
    dependencies=[Entitled],
)
async def verify_domain(
    domain_id: uuid.UUID, session: RawSessionDep, ctx: DomainWrite, request: Request
) -> DomainOut:
    """Takes the write permission although it publishes nothing: it rewrites `records` and
    `status`, and a domain flipping to verified is what lets mail go out as that name.

    **Not `SessionDep`.** Every other route in this file runs its whole body inside the one
    transaction the chokepoint opens before the handler starts; this is the one place that would
    hold it across a DNS lookup, and a resolver that is slow or simply cannot be reached
    (`NoNameservers`, `Timeout`) would then pin a pool connection for however long it takes to
    give up (D3). So this route binds two short transactions of its own instead — the same idiom
    payment's and finance's internal APIs use, and for the same reason — with the lookups
    themselves running between them, with no session and no transaction open at all.
    """
    settings: ServiceSettings = request.app.state.settings

    async def _bind() -> None:
        await set_tenant(session, ctx.organization_id)
        if not settings.is_production:
            await assert_tenant(session, ctx.organization_id)

    async with session.begin():
        await _bind()
        loaded, connection = await services.load_domain_for_check(session, ctx, domain_id=domain_id)
        domain_name = loaded.domain
        dkim_selector = loaded.dkim_selector
        spf_include = loaded.spf_include
        connection_kind = connection.kind if connection else None

    checked = await dns_check.check_domain_records(
        domain_name,
        connection_kind=connection_kind,
        dkim_selector=dkim_selector,
        spf_include=spf_include,
    )

    async with session.begin():
        await _bind()
        row = await services.record_domain_check(
            session, ctx, domain_id=domain_id, checked=checked, request=request
        )
    return _domain_out(row, connection)


# --------------------------------------------------------------------- senders


@router.get(
    "/engage/senders",
    response_model=Page[SenderOut],
    summary="The named identities mail can go out as",
    dependencies=[Entitled],
)
async def list_senders(
    session: SessionDep,
    ctx: SenderRead,
    params: PageQuery,
    domain_id: Annotated[uuid.UUID | None, Query(description="Only senders on this domain")] = None,
) -> Page[SenderOut]:
    stmt = selectors.senders_stmt(ctx, domain_id=domain_id)
    rows, total = await paginate(session, stmt, params)
    return Page.build([_sender_out(r) for r in rows], total=total, params=params)


@router.post(
    "/engage/senders",
    response_model=SenderOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a sender on a verified domain",
    dependencies=[Entitled],
)
async def add_sender(
    body: SenderIn, session: SessionDep, ctx: SenderWrite, request: Request
) -> SenderOut:
    row = await services.create_sender(
        session,
        ctx,
        domain_id=body.domain_id,
        name=body.name,
        address=body.address,
        reply_to=body.reply_to,
        signature=body.signature,
        department=body.department,
        request=request,
    )
    return _sender_out(row)


@router.post(
    "/engage/senders/{sender_id}/test",
    response_model=SenderOut,
    summary="Send a test message and, if it arrives, make the sender usable",
    dependencies=[Entitled],
)
async def send_test_message(
    sender_id: uuid.UUID,
    body: TestSendIn,
    session: SessionDep,
    ctx: SenderWrite,
    request: Request,
) -> SenderOut:
    """The gate, over HTTP. The caller waits for the provider's answer because that answer *is*
    the state change — there is nothing to enqueue until mail has actually left."""
    row = await services.send_test(
        session,
        ctx,
        sender_id=sender_id,
        to_address=body.to_address,
        request=request,
    )
    return _sender_out(row)


# --------------------------------------------------------------------- contacts


@router.get(
    "/engage/contacts",
    response_model=Page[ContactOut],
    summary="The organization's contacts, addresses masked",
    dependencies=[Entitled],
)
async def list_contacts(
    session: SessionDep,
    ctx: ContactRead,
    params: PageQuery,
    search: Annotated[
        str | None,
        Query(max_length=254, description="Name substring, or an exact email address"),
    ] = None,
) -> Page[ContactOut]:
    stmt = selectors.contacts_stmt(ctx, search=search)
    rows, total = await paginate(session, stmt, params)
    return Page.build([_contact_out(r) for r in rows], total=total, params=params)


@router.post(
    "/engage/contacts",
    response_model=ContactOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a contact",
    dependencies=[Entitled],
)
async def add_contact(
    body: ContactIn, session: SessionDep, ctx: ContactWrite, request: Request
) -> ContactOut:
    row = await services.create_contact(
        session,
        ctx,
        email=body.email,
        name=body.name,
        attributes=body.attributes,
        request=request,
    )
    return _contact_out(row)


@router.post(
    "/engage/contacts/import",
    response_model=ContactImportOut,
    summary="Upsert up to 1000 contacts by address, reporting each row",
    dependencies=[Entitled],
)
async def import_contacts(
    body: Annotated[ContactImportIn, Body()],
    session: SessionDep,
    ctx: ContactWrite,
    request: Request,
) -> ContactImportOut:
    """200 even when some rows are invalid: the per-row `status` is the answer, and one bad
    address in a pasted spreadsheet should not throw away the other 999."""
    outcomes = await services.import_contacts(
        session,
        ctx,
        rows=[(r.email, r.name, r.attributes) for r in body],
        request=request,
    )
    results = [
        ContactImportResult(
            index=i,
            status=outcome,
            id=str(contact.id) if contact is not None else None,
            error=error,
        )
        for i, (outcome, contact, error) in enumerate(outcomes)
    ]
    return ContactImportOut(
        created=sum(r.status == "created" for r in results),
        updated=sum(r.status == "updated" for r in results),
        invalid=sum(r.status == "invalid" for r in results),
        results=results,
    )


@router.get(
    "/engage/contacts/{contact_id}",
    response_model=ContactOut,
    summary="One contact",
    dependencies=[Entitled],
)
async def get_contact(contact_id: uuid.UUID, session: SessionDep, ctx: ContactRead) -> ContactOut:
    row = await selectors.get_contact(session, ctx.organization_id, contact_id)
    if row is None:
        raise NotFoundProblem("No such contact")
    return _contact_out(row)


@router.patch(
    "/engage/contacts/{contact_id}",
    response_model=ContactOut,
    summary="Edit a contact",
    dependencies=[Entitled],
)
async def edit_contact(
    contact_id: uuid.UUID,
    body: ContactPatch,
    session: SessionDep,
    ctx: ContactWrite,
    request: Request,
) -> ContactOut:
    row = await services.update_contact(
        session,
        ctx,
        contact_id=contact_id,
        email=body.email,
        name=body.name,
        attributes=body.attributes,
        request=request,
    )
    return _contact_out(row)


@router.delete(
    "/engage/contacts/{contact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a contact (any suppression of the address stays)",
    dependencies=[Entitled],
)
async def remove_contact(
    contact_id: uuid.UUID, session: SessionDep, ctx: ContactWrite, request: Request
) -> Response:
    await services.delete_contact(session, ctx, contact_id=contact_id, request=request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------- suppressions


@router.get(
    "/engage/suppressions",
    response_model=Page[SuppressionOut],
    summary="Addresses that may not be mailed, and why",
    dependencies=[Entitled],
)
async def list_suppressions(
    session: SessionDep,
    ctx: SuppressionRead,
    params: PageQuery,
    reason: SuppressionReason | None = None,
) -> Page[SuppressionOut]:
    stmt = selectors.suppressions_stmt(ctx, reason=reason)
    rows, total = await paginate(session, stmt, params)
    return Page.build([_suppression_out(r) for r in rows], total=total, params=params)


@router.post(
    "/engage/suppressions",
    response_model=SuppressionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Suppress an address by hand",
    dependencies=[Entitled],
)
async def add_suppression(
    body: SuppressionIn, session: SessionDep, ctx: SuppressionWrite, request: Request
) -> SuppressionOut:
    row = await services.add_suppression(session, ctx, email=body.email, request=request)
    return _suppression_out(row)


@router.delete(
    "/engage/suppressions/{suppression_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Lift a manual suppression",
    dependencies=[Entitled],
)
async def remove_suppression(
    suppression_id: uuid.UUID, session: SessionDep, ctx: SuppressionWrite, request: Request
) -> Response:
    await services.remove_suppression(session, ctx, suppression_id=suppression_id, request=request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------- consent ledger
#
# The record of who opted in or out of what, through which source (`app/consent.py`). Deliberately
# small: record an event, read a subject's history, open one address. Nothing here updates or
# deletes an event — the ledger is append-only, and the database enforces it.
#
# **No new permission codes.** Reading and recording ride the existing contact permissions (the
# subjects are the organization's audience) and unmasking rides the existing dangerous unmask
# permission with its OTP step-up. Both are floored at ORG like the rest of Engage, so a
# consent history is the organization's, never one person's own.


def _consent_event_out(row: ConsentEvent) -> ConsentEventOut:
    """The mask, never the address. `subject_enc` and `subject_index` have no field here."""
    return ConsentEventOut(
        id=str(row.id),
        channel=row.channel,
        purpose=row.purpose,
        status=row.status,
        source=row.source,
        evidence=row.evidence,
        address=row.subject_masked,
        contact_id=str(row.contact_id) if row.contact_id else None,
        recorded_by=str(row.recorded_by) if row.recorded_by else None,
        occurred_at=row.occurred_at,
        created_at=row.created_at,
    )


@router.post(
    "/engage/consent/events",
    response_model=ConsentRecordedOut,
    status_code=status.HTTP_201_CREATED,
    summary="Record that an address opted in or out (append-only; source and evidence required)",
    dependencies=[Entitled],
)
async def record_consent_event(
    body: ConsentEventIn,
    session: SessionDep,
    ctx: ContactWrite,
    request: Request,
    response: Response,
) -> ConsentRecordedOut:
    """201 for a new event; 200 with the original event for a retry under the same
    `idempotency_key`. `effective` says whether the decision now allows sending — an opt-in does
    not lift a legacy suppression or a customer's STOP."""
    recorded = await services.record_consent_event(
        session,
        ctx,
        channel=body.channel,
        purpose=body.purpose,
        status=body.status,
        source=body.source,
        evidence=body.evidence,
        address=body.address,
        contact_id=body.contact_id,
        occurred_at=body.occurred_at,
        idempotency_key=body.idempotency_key,
        request=request,
    )
    if recorded.replayed:
        response.status_code = status.HTTP_200_OK
    return ConsentRecordedOut(
        event=_consent_event_out(recorded.event),
        effective=ConsentDecisionOut(
            allowed=recorded.effective.allowed, reason=recorded.effective.reason
        ),
    )


@router.get(
    "/engage/consent/events",
    response_model=Page[ConsentEventOut],
    summary="The consent ledger, newest first; addresses masked. `contact_id` narrows it to "
    "one contact's history",
    dependencies=[Entitled],
)
async def list_consent_events(
    session: SessionDep,
    ctx: ContactRead,
    params: PageQuery,
    channel: ConsentChannel | None = None,
    purpose: ConsentPurpose | None = None,
    event_status: Annotated[ConsentStatus | None, Query(alias="status")] = None,
    contact_id: uuid.UUID | None = None,
) -> Page[ConsentEventOut]:
    subject_index: str | None = None
    if contact_id is not None:
        contact = await selectors.get_contact(session, ctx.organization_id, contact_id)
        if contact is None:
            raise NotFoundProblem("No such contact")
        # A contact is an email subject: their history is every email event for that address,
        # whether or not it was recorded against this contact id.
        subject_index = contact.email_index
        channel = "email"
    stmt = selectors.consent_events_stmt(
        ctx,
        channel=channel,
        purpose=purpose,
        status=event_status,
        subject_index=subject_index,
    )
    rows, total = await paginate(session, stmt, params)
    return Page.build([_consent_event_out(r) for r in rows], total=total, params=params)


@router.post(
    "/engage/consent/events:lookup",
    response_model=Page[ConsentEventOut],
    summary="One subject's consent history, looked up by address (in the body, so it never "
    "reaches an access log)",
    dependencies=[Entitled],
)
async def lookup_consent_history(
    body: ConsentLookupIn,
    session: SessionDep,
    ctx: ContactRead,
    params: PageQuery,
) -> Page[ConsentEventOut]:
    try:
        subject_index = consent.index_for(body.channel, body.address)
    except _rules.RuleError as exc:
        raise ValidationProblem(str(exc)) from exc
    stmt = selectors.consent_events_stmt(ctx, channel=body.channel, subject_index=subject_index)
    rows, total = await paginate(session, stmt, params)
    return Page.build([_consent_event_out(r) for r in rows], total=total, params=params)


@router.post(
    "/engage/consent/events/{event_id}:unmask",
    response_model=ConsentUnmaskedOut,
    summary="Reveal the address one consent event is about (dangerous; audited every time)",
    dependencies=[Entitled],
)
async def unmask_consent_event(
    event_id: uuid.UUID,
    session: SessionDep,
    ctx: ConsentUnmask,
    request: Request,
) -> ConsentUnmaskedOut:
    address = await services.unmask_consent_subject(
        session, ctx, event_id=event_id, request=request
    )
    return ConsentUnmaskedOut(event_id=str(event_id), address=address)


# --------------------------------------------------------------------- lists & campaigns

ListRead = Annotated[ScopeContext, Depends(require("engage.audience.list.read", minimum=Scope.ORG))]
ListWrite = Annotated[
    ScopeContext, Depends(require("engage.audience.list.write", minimum=Scope.ORG))
]
CampaignRead = Annotated[
    ScopeContext, Depends(require("engage.campaign.campaign.read", minimum=Scope.ORG))
]
CampaignWrite = Annotated[
    ScopeContext, Depends(require("engage.campaign.campaign.write", minimum=Scope.ORG))
]
#: Separate from write: a drafter is not thereby someone who can mail the whole audience.
CampaignSend = Annotated[
    ScopeContext, Depends(require("engage.campaign.campaign.send", minimum=Scope.ORG))
]


# --------------------------------------------------------------------- lists


def _list_out(row: ContactList, members: int) -> ContactListOut:
    return ContactListOut(
        id=str(row.id),
        name=row.name,
        description=row.description,
        member_count=members,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _one_list_out(session: AsyncSession, row: ContactList) -> ContactListOut:
    counts = await selectors.list_member_counts(session, row.organization_id, [row.id])
    return _list_out(row, counts.get(row.id, 0))


@router.get(
    "/engage/lists",
    response_model=Page[ContactListOut],
    summary="Contact lists, by name",
    dependencies=[Entitled],
)
async def list_lists(session: SessionDep, ctx: ListRead, params: PageQuery) -> Page[ContactListOut]:
    rows, total = await paginate(session, selectors.lists_stmt(ctx), params)
    counts = await selectors.list_member_counts(session, ctx.organization_id, [r.id for r in rows])
    return Page.build([_list_out(r, counts.get(r.id, 0)) for r in rows], total=total, params=params)


@router.post(
    "/engage/lists",
    response_model=ContactListOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a contact list",
    dependencies=[Entitled],
)
async def create_list(
    body: ContactListIn, session: SessionDep, ctx: ListWrite, request: Request
) -> ContactListOut:
    row = await services.create_list(
        session, ctx, name=body.name, description=body.description, request=request
    )
    return _list_out(row, 0)


@router.get(
    "/engage/lists/{list_id}",
    response_model=ContactListOut,
    summary="One contact list",
    dependencies=[Entitled],
)
async def get_list(list_id: uuid.UUID, session: SessionDep, ctx: ListRead) -> ContactListOut:
    row = await selectors.get_list(session, ctx.organization_id, list_id)
    if row is None:
        raise NotFoundProblem("No such list")
    return await _one_list_out(session, row)


@router.patch(
    "/engage/lists/{list_id}",
    response_model=ContactListOut,
    summary="Rename or describe a contact list",
    dependencies=[Entitled],
)
async def edit_list(
    list_id: uuid.UUID,
    body: ContactListPatch,
    session: SessionDep,
    ctx: ListWrite,
    request: Request,
) -> ContactListOut:
    row = await services.update_list(
        session,
        ctx,
        list_id=list_id,
        name=body.name,
        description=body.description,
        request=request,
    )
    return await _one_list_out(session, row)


@router.delete(
    "/engage/lists/{list_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a contact list (its contacts stay)",
    dependencies=[Entitled],
)
async def remove_list(
    list_id: uuid.UUID, session: SessionDep, ctx: ListWrite, request: Request
) -> Response:
    await services.delete_list(session, ctx, list_id=list_id, request=request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/engage/lists/{list_id}/members",
    response_model=Page[ContactOut],
    summary="The contacts on a list, addresses masked",
    dependencies=[Entitled],
)
async def list_members(
    list_id: uuid.UUID, session: SessionDep, ctx: ListRead, params: PageQuery
) -> Page[ContactOut]:
    if await selectors.get_list(session, ctx.organization_id, list_id) is None:
        raise NotFoundProblem("No such list")
    rows, total = await paginate(session, selectors.list_members_stmt(ctx, list_id), params)
    return Page.build([_contact_out(r) for r in rows], total=total, params=params)


@router.post(
    "/engage/lists/{list_id}/members",
    response_model=ListMembersOut,
    summary="Add contacts to a list (idempotent)",
    dependencies=[Entitled],
)
async def add_list_members(
    list_id: uuid.UUID,
    body: ListMembersIn,
    session: SessionDep,
    ctx: ListWrite,
    request: Request,
) -> ListMembersOut:
    changed, unknown = await services.change_list_members(
        session, ctx, list_id=list_id, contact_ids=body.contact_ids, add=True, request=request
    )
    return ListMembersOut(changed=changed, unknown=[str(u) for u in unknown])


@router.post(
    "/engage/lists/{list_id}/members/remove",
    response_model=ListMembersOut,
    summary="Remove contacts from a list (idempotent)",
    dependencies=[Entitled],
)
async def remove_list_members(
    list_id: uuid.UUID,
    body: ListMembersIn,
    session: SessionDep,
    ctx: ListWrite,
    request: Request,
) -> ListMembersOut:
    changed, unknown = await services.change_list_members(
        session, ctx, list_id=list_id, contact_ids=body.contact_ids, add=False, request=request
    )
    return ListMembersOut(changed=changed, unknown=[str(u) for u in unknown])


# --------------------------------------------------------------------- campaigns


def _campaign_out(row: Campaign, counts: dict[str, int]) -> CampaignOut:
    return CampaignOut(
        id=str(row.id),
        name=row.name,
        sender_id=str(row.sender_id),
        subject=row.subject,
        html_body=row.html_body,
        text_body=row.text_body,
        audience=row.audience,
        list_id=str(row.list_id) if row.list_id else None,
        status=row.status,
        scheduled_at=row.scheduled_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
        cancelled_at=row.cancelled_at,
        pause_reason=row.pause_reason,
        stats=CampaignStats(
            recipients=sum(counts.values()),
            # `sending` is the driver's in-flight claim lease (app/models.py's
            # `CampaignRecipient` docstring) — not a status a caller outside this service should
            # need to know about, so it is folded into `queued` here, same as a row still
            # waiting for its first attempt.
            queued=counts.get("queued", 0) + counts.get("sending", 0),
            sent=counts.get("sent", 0),
            failed=counts.get("failed", 0),
            suppressed=counts.get("suppressed", 0),
        ),
        track_opens=row.track_opens,
        track_clicks=row.track_clicks,
        ab_test=row.ab_test,
        subject_b=row.subject_b,
        html_body_b=row.html_body_b,
        text_body_b=row.text_body_b,
        ab_split_percent=row.ab_split_percent,
        ab_metric=row.ab_metric,
        ab_wait_minutes=row.ab_wait_minutes,
        ab_winner=row.ab_winner,
        ab_decided_at=row.ab_decided_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _one_campaign_out(session: AsyncSession, row: Campaign) -> CampaignOut:
    counts = await selectors.recipient_status_counts(session, row.organization_id, [row.id])
    return _campaign_out(row, counts.get(row.id, {}))


async def _campaign_or_404(
    session: AsyncSession, ctx: ScopeContext, campaign_id: uuid.UUID
) -> Campaign:
    row = await selectors.get_campaign(session, ctx.organization_id, campaign_id)
    if row is None:
        raise NotFoundProblem("No such campaign")
    return row


@router.get(
    "/engage/campaigns",
    response_model=Page[CampaignOut],
    summary="Campaigns, newest first",
    dependencies=[Entitled],
)
async def list_campaigns(
    session: SessionDep,
    ctx: CampaignRead,
    params: PageQuery,
    status_filter: Annotated[CampaignStatus | None, Query(alias="status")] = None,
) -> Page[CampaignOut]:
    rows, total = await paginate(
        session, selectors.campaigns_stmt(ctx, status=status_filter), params
    )
    counts = await selectors.recipient_status_counts(
        session, ctx.organization_id, [r.id for r in rows]
    )
    return Page.build(
        [_campaign_out(r, counts.get(r.id, {})) for r in rows], total=total, params=params
    )


@router.post(
    "/engage/campaigns",
    response_model=CampaignOut,
    status_code=status.HTTP_201_CREATED,
    summary="Draft a campaign",
    dependencies=[Entitled],
)
async def create_campaign(
    body: CampaignIn, session: SessionDep, ctx: CampaignWrite, request: Request
) -> CampaignOut:
    row = await services.create_campaign(session, ctx, fields=body.model_dump(), request=request)
    return _campaign_out(row, {})


@router.get(
    "/engage/campaigns/{campaign_id}",
    response_model=CampaignOut,
    summary="One campaign, with its delivery stats",
    dependencies=[Entitled],
)
async def get_campaign(
    campaign_id: uuid.UUID, session: SessionDep, ctx: CampaignRead
) -> CampaignOut:
    return await _one_campaign_out(session, await _campaign_or_404(session, ctx, campaign_id))


@router.patch(
    "/engage/campaigns/{campaign_id}",
    response_model=CampaignOut,
    summary="Edit a draft campaign",
    dependencies=[Entitled],
)
async def edit_campaign(
    campaign_id: uuid.UUID,
    body: CampaignPatch,
    session: SessionDep,
    ctx: CampaignWrite,
    request: Request,
) -> CampaignOut:
    row = await services.update_campaign(
        session,
        ctx,
        campaign_id=campaign_id,
        changes=body.model_dump(exclude_unset=True),
        request=request,
    )
    return await _one_campaign_out(session, row)


@router.delete(
    "/engage/campaigns/{campaign_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a draft campaign",
    dependencies=[Entitled],
)
async def remove_campaign(
    campaign_id: uuid.UUID, session: SessionDep, ctx: CampaignWrite, request: Request
) -> Response:
    await services.delete_campaign(session, ctx, campaign_id=campaign_id, request=request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/engage/campaigns/{campaign_id}/test",
    response_model=CampaignTestSendOut,
    summary="Send a test copy of a campaign exactly as recipients would receive it",
    dependencies=[Entitled],
)
async def send_campaign_test(
    campaign_id: uuid.UUID,
    body: TestSendIn,
    session: SessionDep,
    ctx: CampaignSend,
    request: Request,
) -> CampaignTestSendOut:
    """Takes the send permission, not the write one: proving what a campaign would look like in
    an inbox is close enough to sending it that a drafter without send rights should not be able
    to preview it by mailing themselves a copy under a fixed name."""
    await services.send_campaign_test(
        session,
        ctx,
        campaign_id=campaign_id,
        to_address=body.to_address,
        request=request,
    )
    return CampaignTestSendOut(sent=True, to=body.to_address)


@router.post(
    "/engage/campaigns/{campaign_id}/send",
    response_model=CampaignOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Send a draft campaign to its audience",
    dependencies=[Entitled],
)
async def send_campaign(
    campaign_id: uuid.UUID, session: SessionDep, ctx: CampaignSend, request: Request
) -> CampaignOut:
    """202: the gate passed and the fan-out is staged. Sending happens after the commit, in the
    worker; progress is read back from the campaign's stats and recipients."""
    row = await services.request_send(session, ctx, campaign_id=campaign_id, request=request)
    return await _one_campaign_out(session, row)


@router.post(
    "/engage/campaigns/{campaign_id}/schedule",
    response_model=CampaignOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Schedule a draft to send later, or push back one already scheduled",
    dependencies=[Entitled],
)
async def schedule_campaign(
    campaign_id: uuid.UUID,
    body: CampaignScheduleIn,
    session: SessionDep,
    ctx: CampaignSend,
    request: Request,
) -> CampaignOut:
    """202: the gate passed and the scheduler has been asked to hold the send. Calling this again
    on a campaign that is already `scheduled` reschedules it to the new `scheduled_at`; calling
    `/cancel` instead withdraws it. Same gate as `/send` (409 off a draft or already-scheduled
    campaign, 422 on anything that would make it unsendable, or a `scheduled_at` not in the
    future)."""
    row = await services.schedule_send(
        session, ctx, campaign_id=campaign_id, scheduled_at=body.scheduled_at, request=request
    )
    return await _one_campaign_out(session, row)


@router.post(
    "/engage/campaigns/{campaign_id}/cancel",
    response_model=CampaignOut,
    summary="Cancel a campaign; recipients not yet sent are never sent",
    dependencies=[Entitled],
)
async def cancel_campaign(
    campaign_id: uuid.UUID, session: SessionDep, ctx: CampaignWrite, request: Request
) -> CampaignOut:
    row = await services.cancel_campaign(session, ctx, campaign_id=campaign_id, request=request)
    return await _one_campaign_out(session, row)


@router.post(
    "/engage/campaigns/{campaign_id}/resume",
    response_model=CampaignOut,
    summary="Retry a paused campaign now, ignoring the driver's own backoff",
    dependencies=[Entitled],
)
async def resume_campaign(
    campaign_id: uuid.UUID, session: SessionDep, ctx: CampaignSend, request: Request
) -> CampaignOut:
    """409 unless the campaign is `paused` — the send driver (`app/cron.py`) pauses a campaign
    when it finds no connected provider or no usable sender, and would otherwise retry it on its
    own after `Settings.engage_campaign_pause_retry_seconds`. This is the operator's "now"."""
    row = await services.resume_campaign(session, ctx, campaign_id=campaign_id, request=request)
    return await _one_campaign_out(session, row)


@router.get(
    "/engage/campaigns/{campaign_id}/stats",
    response_model=CampaignStats,
    summary="A campaign's delivery counts",
    dependencies=[Entitled],
)
async def campaign_stats(
    campaign_id: uuid.UUID, session: SessionDep, ctx: CampaignRead
) -> CampaignStats:
    row = await _campaign_or_404(session, ctx, campaign_id)
    return (await _one_campaign_out(session, row)).stats


@router.get(
    "/engage/campaigns/{campaign_id}/recipients",
    response_model=Page[CampaignRecipientOut],
    summary="A campaign's recipients and what happened to each, addresses masked",
    dependencies=[Entitled],
)
async def campaign_recipients(
    campaign_id: uuid.UUID,
    session: SessionDep,
    ctx: CampaignRead,
    params: PageQuery,
    status_filter: Annotated[RecipientStatus | None, Query(alias="status")] = None,
) -> Page[CampaignRecipientOut]:
    await _campaign_or_404(session, ctx, campaign_id)
    stmt = selectors.recipients_stmt(ctx, campaign_id, status=status_filter)
    rows, total = await paginate(session, stmt, params)
    return Page.build(
        [
            CampaignRecipientOut(
                id=str(r.id),
                contact_id=str(r.contact_id),
                email=r.email_masked,
                # `sending` is the driver's claim lease (see CampaignRecipient's docstring),
                # never a status this API promises — a caller mid-batch sees `queued`, same as
                # before the driver picked the row up.
                status="queued" if r.status == "sending" else r.status,
                provider_message_id=r.provider_message_id,
                error=r.error,
                sent_at=r.sent_at,
            )
            for r in rows
        ],
        total=total,
        params=params,
    )


# --------------------------------------------------------------------- inbox (WhatsApp)


async def _conversation_or_404(
    session: AsyncSession, ctx: ScopeContext, conversation_id: uuid.UUID
) -> Conversation:
    row = await selectors.get_conversation(session, ctx.organization_id, conversation_id)
    if row is None:
        raise NotFoundProblem("No such conversation")
    return row


@router.get(
    "/engage/conversations",
    response_model=Page[ConversationOut],
    summary="WhatsApp conversations, most recently active first",
    dependencies=[Entitled],
)
async def list_conversations(
    session: SessionDep,
    ctx: ConversationRead,
    params: PageQuery,
    conversation_status: Annotated[ConversationStatus | None, Query(alias="status")] = None,
) -> Page[ConversationOut]:
    stmt = selectors.conversations_stmt(ctx, conversation_status=conversation_status)
    rows, total = await paginate(session, stmt, params)
    return Page.build([_conversation_out(r) for r in rows], total=total, params=params)


@router.get(
    "/engage/conversations/{conversation_id}",
    response_model=ConversationOut,
    summary="One conversation",
    dependencies=[Entitled],
)
async def get_conversation(
    conversation_id: uuid.UUID, session: SessionDep, ctx: ConversationRead
) -> ConversationOut:
    return _conversation_out(await _conversation_or_404(session, ctx, conversation_id))


@router.get(
    "/engage/conversations/{conversation_id}/messages",
    response_model=Page[ConversationMessageOut],
    summary="A conversation's thread, both directions, oldest first, bodies masked on the "
    "inbound side",
    dependencies=[Entitled],
)
async def list_conversation_messages(
    conversation_id: uuid.UUID,
    session: SessionDep,
    ctx: ConversationRead,
    params: PageQuery,
) -> Page[ConversationMessageOut]:
    await _conversation_or_404(session, ctx, conversation_id)
    rows, total = await selectors.thread_messages(
        session, ctx, conversation_id, offset=params.offset, limit=params.size
    )
    items = [
        ConversationMessageOut(
            id=str(r.id),
            direction=r.direction,
            message_type=r.message_type,
            body=r.body or "",
            occurred_at=r.occurred_at,
            provider_message_id=r.provider_message_id,
            profile_name=r.profile_name,
        )
        for r in rows
    ]
    return Page.build(items, total=total, params=params)


@router.post(
    "/engage/conversations/{conversation_id}/reply",
    response_model=ConversationReplyOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Send a free-text WhatsApp reply inside the 24-hour service window",
    dependencies=[Entitled],
)
async def reply_to_conversation(
    conversation_id: uuid.UUID,
    body: ConversationReplyIn,
    session: SessionDep,
    ctx: MessageReply,
    request: Request,
) -> ConversationReplyOut:
    """202: staged, not yet sent — the dispatcher sends it, same as every other message in this
    service. 409 with `remedy: "template"` when the window has closed; nothing is staged then."""
    message = await services.reply_to_conversation(
        session,
        ctx,
        conversation_id=conversation_id,
        body=body.body,
        idempotency_key=body.idempotency_key,
        request=request,
    )
    return ConversationReplyOut(
        message_id=str(message.id), state=message.state, conversation_id=str(conversation_id)
    )


@router.post(
    "/engage/conversations/{conversation_id}:assign",
    response_model=ConversationOut,
    summary="Assign a conversation to a membership, or clear its assignment",
    dependencies=[Entitled],
)
async def assign_conversation(
    conversation_id: uuid.UUID,
    body: ConversationAssignIn,
    session: SessionDep,
    ctx: ConversationWrite,
    request: Request,
) -> ConversationOut:
    row = await services.assign_conversation(
        session,
        ctx,
        conversation_id=conversation_id,
        assignee_membership_id=body.assignee_membership_id,
        request=request,
    )
    return _conversation_out(row)


@router.post(
    "/engage/conversations/{conversation_id}:close",
    response_model=ConversationOut,
    summary="Close a conversation. A later reply, or the customer writing back, reopens it",
    dependencies=[Entitled],
)
async def close_conversation(
    conversation_id: uuid.UUID, session: SessionDep, ctx: ConversationWrite, request: Request
) -> ConversationOut:
    row = await services.close_conversation(
        session, ctx, conversation_id=conversation_id, request=request
    )
    return _conversation_out(row)


@router.post(
    "/engage/conversations/{conversation_id}/messages/{message_id}:unmask",
    response_model=UnmaskedMessageOut,
    summary="Reveal one customer message's real text (dangerous; audited every time)",
    dependencies=[Entitled],
)
async def unmask_message(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    session: SessionDep,
    ctx: MessageUnmask,
    request: Request,
) -> UnmaskedMessageOut:
    body = await services.unmask_inbound_message(
        session, ctx, conversation_id=conversation_id, message_id=message_id, request=request
    )
    return UnmaskedMessageOut(message_id=str(message_id), body=body)


# --------------------------------------------------------------------- public unsubscribe

#: The one Engage surface with no principal: the recipient of a campaign clicking the link in
#: it. A separate router because every route on `router` is entitlement-gated and permissioned,
#: and this one cannot be — the token is the whole of its authority. It never uses
#: `get_tenant_session` (there is no caller organization); it opens the transaction itself and
#: binds the GUC to the organization the *verified token* names, before touching a tenant table.
#:
#: **The GET is a confirmation page, not an action.** A mail scanner or link-preview bot that
#: prefetches every URL in a message body used to unsubscribe the recipient on GET alone — a
#: request nobody clicked. GET now only ever renders HTML and never opens a write transaction;
#: the only route that touches `suppressions` is the POST below, reached either by a human
#: submitting the confirmation page's form or by a mailbox provider's RFC 8058 one-click POST.
#: Both are the same request — the response is simply rendered for whichever one asked, by
#: `Accept`: a browser navigating a form submit sends `Accept: text/html`, a one-click POST
#: does not, so it gets exactly the JSON body it always has.
public_router = APIRouter(tags=["engage-public"])

PlainSessionDep = Annotated[AsyncSession, Depends(get_session)]

#: Every response this router sends, success or failure, HTML or JSON. No search engine has any
#: business indexing a recipient-specific, single-use link (`X-Robots-Tag`); no browser has any
#: business running a script, loading a subresource, or being framed on a page that renders
#: nothing but a name and a button (`Content-Security-Policy`); nothing here is meant to be
#: sniffed as anything but what its `Content-Type` already says (`X-Content-Type-Options`);
#: the referrer header carries a token in its path and must not leak to whatever `href` this
#: page never actually has (`Referrer-Policy`); and no intermediate cache has any business
#: holding a copy of a page whose whole point is one-time, one-recipient state (`Cache-Control`).
_PUBLIC_HEADERS = {
    "X-Robots-Tag": "noindex",
    "Content-Security-Policy": "default-src 'none'; form-action 'self'; frame-ancestors 'none'; "
    "base-uri 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def _page(title: str, body: str) -> str:
    """The one template every page on this router renders from — plain HTML, no external
    assets (no stylesheet, script or font to fetch), so nothing here ever depends on a CDN
    being reachable from a recipient's mail client."""
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(title)}</title>\n"
        "</head>\n"
        "<body>\n"
        f"<main>\n{body}\n</main>\n"
        "</body>\n</html>\n"
    )


def _invalid_page() -> str:
    return _page(
        "Unsubscribe link not valid",
        "<h1>This unsubscribe link is not valid</h1>"
        "<p>It may have expired or been used already. If you no longer wish to receive "
        "these emails, please contact the sender directly.</p>",
    )


def _confirm_page(masked_email: str | None) -> str:
    detail = (
        f"<p>This stops email to <strong>{html.escape(masked_email)}</strong> from this sender.</p>"
        if masked_email
        else "<p>This stops future email from this sender to the address this link was sent to.</p>"
    )
    return _page(
        "Unsubscribe",
        f"<h1>Unsubscribe from future emails?</h1>{detail}"
        '<form method="post"><button type="submit">Unsubscribe me</button></form>',
    )


def _done_page(masked_email: str | None) -> str:
    who = f"<strong>{html.escape(masked_email)}</strong>" if masked_email else "This address"
    return _page(
        "Unsubscribed",
        f"<h1>You're unsubscribed</h1><p>{who} will no longer receive email from this sender.</p>",
    )


def _html(body: str, *, status_code: int = status.HTTP_200_OK) -> HTMLResponse:
    return HTMLResponse(body, status_code=status_code, headers=_PUBLIC_HEADERS)


def _wants_html(request: Request) -> bool:
    """A browser navigating a form submit sends `Accept: text/html, ...`; an RFC 8058
    one-click POST from a mailbox provider does not (it wants and gets the JSON body this
    endpoint has always returned). Content negotiation, not a hidden field or a second
    endpoint, is what tells the two apart."""
    return "text/html" in request.headers.get("accept", "").lower()


def _invalid_response(request: Request) -> Response:
    """Same answer for malformed, forged, unconfigured, and a v2 link naming a recipient row
    that is gone or erased: none of them may learn anything, and none of them touches a row."""
    if _wants_html(request):
        return _html(_invalid_page(), status_code=status.HTTP_404_NOT_FOUND)
    raise NotFoundProblem("This unsubscribe link is not valid", headers=_PUBLIC_HEADERS)


async def _masked_preview(target: UnsubscribeTarget, session: AsyncSession) -> str | None:
    """The masked address for a confirmation page. A v1 link already carries it; a v2 link
    names a recipient row instead, so this reads (never decrypts) that row's own masked column
    — a single `SELECT`, not a write, so `GET` stays side-effect-free. `None` when a v2 link's
    row is gone or erased: the page still renders, just without naming an address."""
    if target.email is not None:
        return mask_pii(target.email, kind="email")
    assert target.recipient_id is not None
    async with session.begin():
        await set_tenant(session, target.organization_id)
        return await selectors.masked_recipient_email(
            session, target.organization_id, target.recipient_id
        )


async def _resolve_and_unsubscribe(
    target: UnsubscribeTarget, session: AsyncSession, request: Request
) -> str | None:
    """Writes the suppression `target` names, inside the caller's transaction. Returns the
    masked address to show on the result page, or `None` if there was nothing to act on (a v2
    link naming a recipient row that is gone or erased) — the caller answers that exactly like
    an invalid token."""
    await set_tenant(session, target.organization_id)
    if target.email is not None:
        await services.unsubscribe(
            session, organization_id=target.organization_id, email=target.email, request=request
        )
        return mask_pii(target.email, kind="email")
    assert target.recipient_id is not None
    result = await services.unsubscribe_recipient(
        session,
        organization_id=target.organization_id,
        recipient_id=target.recipient_id,
        request=request,
    )
    if result is None:
        return None
    _wrote, masked = result
    return masked


@public_router.get(
    "/engage/unsubscribe/{token}",
    response_class=HTMLResponse,
    summary="A confirmation page for the address a signed link names (no sign-in, no side effects)",
)
async def unsubscribe_confirm(token: str, session: PlainSessionDep) -> HTMLResponse:
    """Renders a page and nothing else. `GET` must be safe to prefetch — the only read this
    does is a single masked-column `SELECT` for a v2 link, never a write, so a scanner fetching
    every link in a message body cannot unsubscribe anyone by doing so."""
    target = services.verify_unsubscribe_link(token)
    if target is None:
        # Same page for malformed, forged, and "unsubscribe is not configured": none of them
        # may learn anything, and none of them touches a row.
        return _html(_invalid_page(), status_code=status.HTTP_404_NOT_FOUND)
    return _html(_confirm_page(await _masked_preview(target, session)))


@public_router.post(
    "/engage/unsubscribe/{token}",
    response_model=UnsubscribeOut,
    summary="Unsubscribe the address a signed link names — the confirmation form's submit, "
    "and RFC 8058 one-click unsubscribe, both land here",
)
async def unsubscribe_via_link(token: str, session: PlainSessionDep, request: Request) -> Response:
    target = services.verify_unsubscribe_link(token)
    if target is None:
        return _invalid_response(request)
    async with session.begin():
        masked = await _resolve_and_unsubscribe(target, session, request)
    if masked is None:
        return _invalid_response(request)
    if _wants_html(request):
        return _html(_done_page(masked))
    return JSONResponse(UnsubscribeOut(unsubscribed=True).model_dump(), headers=_PUBLIC_HEADERS)
