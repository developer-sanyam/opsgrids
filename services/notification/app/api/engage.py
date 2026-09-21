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

from fastapi import APIRouter, Body, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.domain import verify_unsubscribe_token
from app.models import Contact, EmailConnection, EmailDomain, Sender, Suppression
from app.schemas import (
    ConnectionIn,
    ConnectionOut,
    ContactImportIn,
    ContactImportOut,
    ContactImportResult,
    ContactIn,
    ContactOut,
    ContactPatch,
    DnsRecordOut,
    DomainIn,
    DomainOut,
    SenderIn,
    SenderOut,
    SuppressionIn,
    SuppressionOut,
    SuppressionReason,
    TestSendIn,
    UnsubscribeOut,
)
from platform_core.auth import Scope, ScopeContext, require, require_product
from platform_core.db import get_session, get_tenant_session, set_tenant
from platform_core.errors import NotFoundProblem
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


def _domain_out(row: EmailDomain) -> DomainOut:
    return DomainOut(
        id=str(row.id),
        domain=row.domain,
        status=row.status,
        records=[DnsRecordOut(**record) for record in row.records],
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


# --------------------------------------------------------------------- public unsubscribe

#: The one Engage surface with no principal: the recipient of a campaign clicking the link in
#: it. A separate router because every route on `router` is entitlement-gated and permissioned,
#: and this one cannot be — the token is the whole of its authority. It never uses
#: `get_tenant_session` (there is no caller organization); it opens the transaction itself and
#: binds the GUC to the organization the *verified token* names, before touching a tenant table.
public_router = APIRouter(tags=["engage-public"])

PlainSessionDep = Annotated[AsyncSession, Depends(get_session)]


async def _unsubscribe(token: str, session: AsyncSession, request: Request) -> UnsubscribeOut:
    key = services.unsubscribe_key()
    verified = verify_unsubscribe_token(token, key) if key is not None else None
    if verified is None:
        # Same answer for malformed, forged, and "unsubscribe is not configured": none of them
        # may learn anything, and none of them touches a row.
        raise NotFoundProblem("This unsubscribe link is not valid")
    organization_id, email = verified
    async with session.begin():
        await set_tenant(session, organization_id)
        await services.unsubscribe(
            session, organization_id=organization_id, email=email, request=request
        )
    return UnsubscribeOut(unsubscribed=True)


@public_router.get(
    "/engage/unsubscribe/{token}",
    response_model=UnsubscribeOut,
    summary="Unsubscribe the address a signed link names (no sign-in)",
)
async def unsubscribe_via_link(
    token: str, session: PlainSessionDep, request: Request
) -> UnsubscribeOut:
    return await _unsubscribe(token, session, request)


@public_router.post(
    "/engage/unsubscribe/{token}",
    response_model=UnsubscribeOut,
    summary="One-click unsubscribe (RFC 8058) for the address a signed link names",
)
async def unsubscribe_one_click(
    token: str, session: PlainSessionDep, request: Request
) -> UnsubscribeOut:
    return await _unsubscribe(token, session, request)
