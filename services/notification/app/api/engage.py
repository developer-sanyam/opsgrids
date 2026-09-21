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

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services, unsubscribe
from app.config import settings
from app.models import (
    Contact,
    ContactList,
    EmailConnection,
    EmailDomain,
    ProviderAccount,
    Sender,
    Suppression,
)
from app.schemas import (
    ConnectionIn,
    ConnectionOut,
    ContactBulkImportIn,
    ContactBulkImportOut,
    ContactBulkImportRowOut,
    ContactIn,
    ContactListIn,
    ContactListOut,
    ContactOut,
    ContactUpdateIn,
    DnsRecordOut,
    DomainIn,
    DomainOut,
    ListMembershipIn,
    ListMembershipOut,
    SenderIn,
    SenderOut,
    SuppressionIn,
    SuppressionOut,
    TestSendIn,
    UnsubscribeOut,
    WhatsAppConnectionIn,
    WhatsAppConnectionOut,
)
from platform_core.auth import Scope, ScopeContext, require, require_product
from platform_core.db import get_session, get_tenant_session, set_tenant
from platform_core.errors import NotFoundProblem, ValidationProblem
from platform_core.pagination import Page, PageQuery, paginate

router = APIRouter(tags=["engage"])

#: **The tenancy chokepoint.** Opens the transaction and binds it to the caller's organization
#: before the route body runs, so no route can read or write without one.
SessionDep = Annotated[AsyncSession, Depends(get_tenant_session)]

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
        phone_number_id=row.phone_number_id,
        display_number=row.display_number,
        status=row.status,
    )


def _domain_out(row: EmailDomain) -> DomainOut:
    return DomainOut(
        id=str(row.id),
        domain=row.domain,
        status=row.status,
        records=[DnsRecordOut(**record) for record in row.records],
        last_checked_at=row.last_checked_at,
        created_at=row.created_at,
    )


def _contact_out(row: Contact) -> ContactOut:
    return ContactOut(
        id=str(row.id),
        email=row.email,
        name=row.name,
        attributes=row.attributes,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _list_out(row: ContactList) -> ContactListOut:
    return ContactListOut(
        id=str(row.id), name=row.name, description=row.description, created_at=row.created_at
    )


def _suppression_out(row: Suppression) -> SuppressionOut:
    return SuppressionOut(
        id=str(row.id),
        contact_id=str(row.contact_id),
        reason=row.reason,
        note=row.note,
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
    body: WhatsAppConnectionIn, session: SessionDep, ctx: WhatsAppWrite, request: Request
) -> WhatsAppConnectionOut:
    """POST rather than PUT, for the reason `connect_provider` is: one organization gets one
    WhatsApp account and a second POST is refused rather than silently accepted."""
    row = await services.create_whatsapp_connection(
        session,
        ctx,
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
    body: WhatsAppConnectionIn, session: SessionDep, ctx: WhatsAppWrite, request: Request
) -> WhatsAppConnectionOut:
    row = await services.replace_whatsapp_connection(
        session,
        ctx,
        waba_id=body.waba_id,
        phone_number_id=body.phone_number_id,
        display_number=body.display_number,
        access_token=body.access_token,
        request=request,
    )
    return _whatsapp_connection_out(row)


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
    return Page.build([_domain_out(r) for r in rows], total=total, params=params)


@router.post(
    "/engage/domains",
    response_model=DomainOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a sending domain and generate its DNS records",
    dependencies=[Entitled],
)
async def add_domain(
    body: DomainIn, session: SessionDep, ctx: DomainWrite, request: Request
) -> DomainOut:
    row = await services.create_domain(session, ctx, domain=body.domain, request=request)
    return _domain_out(row)


@router.post(
    "/engage/domains/{domain_id}/verify",
    response_model=DomainOut,
    summary="Re-read DNS and fold the answer into the domain's status",
    dependencies=[Entitled],
)
async def verify_domain(
    domain_id: uuid.UUID, session: SessionDep, ctx: DomainWrite, request: Request
) -> DomainOut:
    """Takes the write permission although it publishes nothing: it rewrites `records` and
    `status`, and a domain flipping to verified is what lets mail go out as that name."""
    row = await services.verify_domain(session, ctx, domain_id=domain_id, request=request)
    return _domain_out(row)


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
    summary="The contact book",
    dependencies=[Entitled],
)
async def list_contacts(
    session: SessionDep,
    ctx: ContactRead,
    params: PageQuery,
    q: Annotated[str | None, Query(description="Search by email or name")] = None,
) -> Page[ContactOut]:
    stmt = selectors.contacts_stmt(ctx, q=q)
    rows, total = await paginate(session, stmt, params)
    return Page.build([_contact_out(r) for r in rows], total=total, params=params)


@router.get(
    "/engage/contacts/{contact_id}",
    response_model=ContactOut,
    summary="One contact",
    dependencies=[Entitled],
)
async def get_contact(contact_id: uuid.UUID, session: SessionDep, ctx: ContactRead) -> ContactOut:
    row = await selectors.get_contact(session, ctx.organization_id, contact_id)
    if row is None:
        raise NotFoundProblem("Contact not found")
    return _contact_out(row)


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


@router.put(
    "/engage/contacts/{contact_id}",
    response_model=ContactOut,
    summary="Edit a contact",
    dependencies=[Entitled],
)
async def edit_contact(
    contact_id: uuid.UUID,
    body: ContactUpdateIn,
    session: SessionDep,
    ctx: ContactWrite,
    request: Request,
) -> ContactOut:
    row = await services.update_contact(
        session,
        ctx,
        contact_id=contact_id,
        name=body.name,
        attributes=body.attributes,
        request=request,
    )
    return _contact_out(row)


@router.delete(
    "/engage/contacts/{contact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a contact",
    dependencies=[Entitled],
)
async def remove_contact(
    contact_id: uuid.UUID, session: SessionDep, ctx: ContactWrite, request: Request
) -> None:
    await services.delete_contact(session, ctx, contact_id=contact_id, request=request)


@router.post(
    "/engage/contacts/import",
    response_model=ContactBulkImportOut,
    summary="Bulk-import contacts, idempotent on email",
    dependencies=[Entitled],
)
async def import_contacts(
    body: ContactBulkImportIn, session: SessionDep, ctx: ContactWrite, request: Request
) -> ContactBulkImportOut:
    report = await services.bulk_import_contacts(
        session,
        ctx,
        rows=[row.model_dump() for row in body.rows],
        request=request,
    )
    rows_out = [ContactBulkImportRowOut(**r) for r in report]
    return ContactBulkImportOut(
        total=len(rows_out),
        created=sum(1 for r in rows_out if r.status == "created"),
        updated=sum(1 for r in rows_out if r.status == "updated"),
        errors=sum(1 for r in rows_out if r.status == "error"),
        rows=rows_out,
    )


# --------------------------------------------------------------------- lists


@router.get(
    "/engage/lists",
    response_model=Page[ContactListOut],
    summary="Named contact lists",
    dependencies=[Entitled],
)
async def list_lists(
    session: SessionDep, ctx: ContactRead, params: PageQuery
) -> Page[ContactListOut]:
    stmt = selectors.contact_lists_stmt(ctx)
    rows, total = await paginate(session, stmt, params)
    return Page.build([_list_out(r) for r in rows], total=total, params=params)


@router.post(
    "/engage/lists",
    response_model=ContactListOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a contact list",
    dependencies=[Entitled],
)
async def add_list(
    body: ContactListIn, session: SessionDep, ctx: ContactWrite, request: Request
) -> ContactListOut:
    row = await services.create_contact_list(
        session, ctx, name=body.name, description=body.description, request=request
    )
    return _list_out(row)


@router.get(
    "/engage/lists/{list_id}/members",
    response_model=Page[ContactOut],
    summary="Who is on this list",
    dependencies=[Entitled],
)
async def list_members(
    list_id: uuid.UUID, session: SessionDep, ctx: ContactRead, params: PageQuery
) -> Page[ContactOut]:
    if await selectors.get_contact_list(session, ctx.organization_id, list_id) is None:
        raise NotFoundProblem("List not found")
    stmt = selectors.list_members_stmt(ctx, list_id=list_id)
    rows, total = await paginate(session, stmt, params)
    return Page.build([_contact_out(r) for r in rows], total=total, params=params)


@router.post(
    "/engage/lists/{list_id}/members",
    response_model=ListMembershipOut,
    summary="Add contacts to a list",
    dependencies=[Entitled],
)
async def add_members(
    list_id: uuid.UUID,
    body: ListMembershipIn,
    session: SessionDep,
    ctx: ContactWrite,
    request: Request,
) -> ListMembershipOut:
    added = await services.add_list_members(
        session, ctx, list_id=list_id, contact_ids=body.contact_ids, request=request
    )
    return ListMembershipOut(contact_ids=[str(c) for c in added])


@router.post(
    "/engage/lists/{list_id}/members/remove",
    response_model=ListMembershipOut,
    summary="Remove contacts from a list",
    dependencies=[Entitled],
)
async def remove_members(
    list_id: uuid.UUID,
    body: ListMembershipIn,
    session: SessionDep,
    ctx: ContactWrite,
    request: Request,
) -> ListMembershipOut:
    removed = await services.remove_list_members(
        session, ctx, list_id=list_id, contact_ids=body.contact_ids, request=request
    )
    return ListMembershipOut(contact_ids=[str(c) for c in removed])


# --------------------------------------------------------------------- suppressions


@router.get(
    "/engage/suppressions",
    response_model=Page[SuppressionOut],
    summary="Who this organization may not send Engage campaigns to",
    dependencies=[Entitled],
)
async def list_suppressions(
    session: SessionDep,
    ctx: SuppressionRead,
    params: PageQuery,
    reason: Annotated[str | None, Query()] = None,
) -> Page[SuppressionOut]:
    stmt = selectors.suppressions_stmt(ctx, reason=reason)
    rows, total = await paginate(session, stmt, params)
    return Page.build([_suppression_out(r) for r in rows], total=total, params=params)


@router.post(
    "/engage/suppressions",
    response_model=SuppressionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Manually suppress a contact",
    dependencies=[Entitled],
)
async def add_suppression(
    body: SuppressionIn, session: SessionDep, ctx: SuppressionWrite, request: Request
) -> SuppressionOut:
    row = await services.suppress_contact(
        session,
        ctx,
        contact_id=body.contact_id,
        reason=body.reason,
        note=body.note,
        request=request,
    )
    return _suppression_out(row)


# --------------------------------------------------------------------- public unsubscribe
#
# A second router — no `Entitled`, no permission, no `get_tenant_session`. There is no
# principal: a stranger with a signed link is the whole of the caller. `token` carries the
# organization and the contact directly (see `app/unsubscribe.py`), which is what lets this
# route resolve its tenant *from the link* rather than from a caller nobody can authenticate —
# the same shape the WhatsApp webhook uses to resolve a tenant from `phone_number_id` before
# opening a bound transaction.
#
# Mounted at an exact path with no path variable, matching every other anonymous route in the
# platform (ADR-0010 §4): the gateway's `public_paths` match is `full_path in {...}` against the
# path only, so a route parameter would not be expressible there. `token` therefore travels in
# the query string, which is also what an email client needs — a plain link, no request body.
public_router = APIRouter(tags=["engage (public)"])


@public_router.get(
    "/engage/public/unsubscribe",
    response_model=UnsubscribeOut,
    summary="One-click unsubscribe from a signed link",
)
async def public_unsubscribe(
    token: Annotated[str, Query(min_length=1, max_length=500)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> UnsubscribeOut:
    try:
        target = unsubscribe.verify(token, settings=settings())
    except unsubscribe.UnsubscribeTokenError as exc:
        raise ValidationProblem("This unsubscribe link is invalid or has expired") from exc

    async with session.begin():
        await set_tenant(session, target.organization_id)
        result = await services.unsubscribe_public(
            session,
            organization_id=target.organization_id,
            contact_id=target.contact_id,
            request=request,
        )
    if result is None:
        # A token signed for a contact that no longer exists in this organization. Answered as
        # success rather than 404: whether that contact still exists is not something a stranger
        # holding an old link should be able to learn (see `services.unsubscribe_public`).
        return UnsubscribeOut(status="unsubscribed", already=True)
    _suppression, already = result
    return UnsubscribeOut(status="unsubscribed", already=already)
