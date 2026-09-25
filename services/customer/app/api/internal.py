"""The internal API — service-to-service, never reachable from outside the cluster.

The original endpoint here exists because of one constraint: a masked phone number cannot be
messaged, and the service that does the messaging must not hold the key that opens it.

Notification-service needs a contact's real number to send a WhatsApp template. It could have
been given the PII key and the `customer` schema; both would have been wrong. Decryption stays
inside the service that owns the data, and notification asks for a *messaging target* over the
internal API under a short-lived token addressed to customer by name — the same shape every
cross-service read on this platform takes, and the rule (ADR-0000 §10) that keeps a later
database-per-service split a configuration change rather than a rewrite.

So the automation engine never decrypts anything. It knows a contact id and a channel; what
comes back is an address or nothing.

Every resolution is audited twice: an `entity.sensitive_read` row, because opening sealed PII is
an auditable read whoever does it, and a `contact.messaging_target_resolved` row naming the
calling service in `acting_as`. "Which service read this customer's number, when, and to send on
which channel" is answerable. It would not be if a machine's read went unrecorded merely because
no human was present.

Two more routes since (ADR-0017): `leads:convert`, the customer-side half of lead-service's
`POST /leads/{id}:convert`, and `owners:validate`, the gate lead-service's own writes call
before trusting an `owner_id`. Both are restricted to `service_caller == "lead"`
(`_require_caller`) and to a token whose `org` claim names the path's organization
(`_require_org_claim`) — a valid signature proves *which service* is calling, never *which
route it was meant for* or *which tenant*, and both of those are separate checks here.

Mounted at the root and excluded from the OpenAPI document: not public API, and it must not
appear as though it were.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.config import settings
from app.schemas import (
    CompanyExportCursorOut,
    CompanyExportPageOut,
    CompanyExportRowOut,
    ContactResolveIn,
    ContactResolveOut,
    LeadConversionIn,
    LeadConversionOut,
    MessagingTargetOut,
    OwnersValidateIn,
    OwnersValidateOut,
)
from platform_core.auth import require_service_token
from platform_core.config import ServiceSettings
from platform_core.db import assert_tenant, get_session, set_tenant
from platform_core.errors import (
    ForbiddenProblem,
    NotFoundProblem,
    UnauthorizedProblem,
    ValidationProblem,
)
from platform_core.logging import get_logger
from platform_core.pii import blind_index

log = get_logger("customer.internal")

router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
    include_in_schema=False,
    dependencies=[Depends(require_service_token)],
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]

#: Channels a messaging target can be asked for. `whatsapp` is the only one that needs a phone
#: number today; `sms` is listed because it will need the same one and the endpoint should not
#: have to change shape to serve it. Anything else is refused rather than silently answered with
#: a phone number, because a caller asking for an address on a channel this service does not
#: understand is a caller making an assumption.
_PHONE_CHANNELS = frozenset({"whatsapp", "sms"})


async def _bind(session: AsyncSession, organization_id: uuid.UUID, request: Request) -> None:
    """Bind this transaction to the caller's organization — the internal analogue of the
    tenancy chokepoint. Must run as the first statement inside the transaction."""
    settings: ServiceSettings = request.app.state.settings
    await set_tenant(session, organization_id)
    if not settings.is_production:
        await assert_tenant(session, organization_id)


def _require_caller(request: Request, *expected: str) -> None:
    """Restrict an internal route to a named allow-list of calling services, on top of the
    valid-signature check `require_service_token` already did.

    A validly signed token from a service this route was never meant to serve is not a forged
    token — it is a service using a capability nobody meant to give it. `owners:validate` in
    particular hands back which of an org's reps are active, in bulk, by id; that answer is
    lead-service's own business (its round-robin, its explicit `owner_id` writes) and nobody
    else's, so the allow-list is the actual authorization, not the signature check alone.
    """
    caller = getattr(request.state, "service_caller", "unknown-service")
    if caller not in expected:
        raise ForbiddenProblem(f"{caller!r} may not call this endpoint")


def _require_org_claim(request: Request, organization_id: uuid.UUID) -> None:
    """The token's own `org` claim must name the organization in the path.

    The signature and audience alone only prove *which service* minted the token, never *which
    organization* it was minted for — a service holding one valid token could otherwise replay
    it against any path it can construct. `_bind`'s `assert_tenant` catches a mismatch here
    too, but only outside production (ADR-0002 §4's own trade-off for that probe); this check
    is the one that holds everywhere.
    """
    claims = getattr(request.state, "service_claims", None) or {}
    if claims.get("org") != str(organization_id):
        raise ForbiddenProblem("This token is not addressed to this organization")


def _claimed_actor(request: Request) -> uuid.UUID | None:
    """The human `sub` the caller's token carried, if any — `lead_conversion_ctx`'s actor for
    the audit trail. `None` when the token carried no `sub` (an older or system-initiated
    caller), which `lead_conversion_ctx` degrades to "the platform" rather than raising on."""
    claims = getattr(request.state, "service_claims", None) or {}
    sub = claims.get("sub")
    if not sub:
        return None
    try:
        return uuid.UUID(str(sub))
    except ValueError:
        return None


#: insights' backfill export is the only caller of the route below (ADR-0020 §3), a single
#: named caller rather than `_require_caller`'s allow-list shape — the same pattern deal's and
#: lead's own export routes take.
TRUSTED_INSIGHTS_CALLER = "insights"


def _require_insights_caller(request: Request) -> None:
    caller = getattr(request.state, "service_caller", None)
    if caller != TRUSTED_INSIGHTS_CALLER:
        raise UnauthorizedProblem("Valid service token required")


@router.get(
    "/organizations/{organization_id}/contacts/{contact_id}/messaging-target",
    response_model=MessagingTargetOut,
    status_code=status.HTTP_200_OK,
    summary="A contact's address on one channel, for a service about to message them",
)
async def messaging_target(
    organization_id: uuid.UUID,
    contact_id: uuid.UUID,
    channel: str,
    session: SessionDep,
    request: Request,
) -> MessagingTargetOut:
    """Resolve one contact's messaging address, audited.

    `organization_id` is in the path and bound to the transaction, so row-level security
    applies to a service call exactly as it does to a request: a token addressed to customer
    does not widen into a cross-tenant read. A contact belonging to another organization reads
    as not-found, never as forbidden — confirming that an id exists elsewhere is itself a
    disclosure.

    A contact with no sealed number comes back with `phone: null` and a 200. That is an
    ordinary state, not a fault: most contacts have never had a real number supplied, and a
    caller must treat it as "not messageable here" rather than retrying an error.
    """
    if channel not in _PHONE_CHANNELS:
        raise ValidationProblem(
            f"{channel!r} is not a channel customer-service can resolve an address for"
        )

    async with session.begin():
        await _bind(session, organization_id, request)

        contact = await selectors.get_contact(session, organization_id, contact_id)
        if contact is None:
            raise NotFoundProblem("That contact is not part of this organization")

        phone = await services.resolve_messaging_phone(
            session,
            organization_id=organization_id,
            contact=contact,
            channel=channel,
            # Recorded in the audit row's `acting_as`. The token's `iss`, stashed by
            # `require_service_token` after the signature and audience were verified — not
            # anything the caller asserts about itself in a header.
            service=getattr(request.state, "service_caller", "unknown-service"),
        )

    # Never the number. A log line is the easiest place for PII to escape a service, and this
    # is the one function on the platform whose whole job is to produce a phone number.
    log.info(
        "customer.messaging_target.resolved",
        contact_id=str(contact_id),
        channel=channel,
        resolved=phone is not None,
    )
    return MessagingTargetOut(contact_id=str(contact_id), channel=channel, phone=phone)


@router.post(
    "/organizations/{organization_id}/contacts:resolve",
    response_model=ContactResolveOut,
    status_code=status.HTTP_200_OK,
    summary="Which contact holds this phone number",
)
async def resolve_contact(
    organization_id: uuid.UUID,
    payload: ContactResolveIn,
    session: SessionDep,
    request: Request,
) -> ContactResolveOut:
    """The reverse of a messaging target: a number in, a contact id out.

    It exists so an inbound WhatsApp message can be attached to the customer who sent it
    rather than creating a duplicate. The match is made on the keyed blind index — no sealed
    value is decrypted, so this reads no PII and writes no sensitive-read audit row. That is
    the point of the index: a *search* is not a read of somebody's number.

    POST rather than GET because the number is in the body. A query string is recorded in
    access logs, proxy logs and browser history, and a customer's phone number has no business
    in any of them.

    A number nobody holds returns `contact_id: null` with a 200 — a stranger messaging the
    business is ordinary, not an error.
    """
    try:
        canonical = services._canonical_e164(payload.phone)
    except ValidationProblem:
        # Not a messageable number, so it cannot be anyone's. Answered rather than refused:
        # the caller is a webhook handler and a 422 would make it retry something that will
        # never succeed.
        log.info("customer.resolve.not_e164")
        return ContactResolveOut(contact_id=None)

    index = blind_index(canonical, kind="phone", settings=settings())

    async with session.begin():
        await _bind(session, organization_id, request)
        contact = await selectors.get_contact_by_phone_index(session, organization_id, index)

    # Never the number, and not the index either — it is a stable identifier derived from a
    # phone number, so a log full of them is a log that correlates customers across lines.
    log.info("customer.resolve.done", matched=contact is not None)
    return ContactResolveOut(contact_id=str(contact.id) if contact else None)


@router.post(
    "/organizations/{organization_id}/leads:convert",
    response_model=LeadConversionOut,
    status_code=status.HTTP_200_OK,
    summary="Ensure a company (+ contact) exists for a converting lead",
)
async def convert_lead(
    organization_id: uuid.UUID,
    payload: LeadConversionIn,
    session: SessionDep,
    request: Request,
) -> LeadConversionOut:
    """The customer-side half of `POST /leads/{id}:convert`.

    Idempotent by the caller's own ids first (`services.ensure_company_and_contact_for_lead`):
    lead-service derives `company_id`/`contact_id` deterministically from the lead's own id, so
    calling this twice with the same lead reuses the same rows rather than creating a second
    company — the guarantee ADR-0017 asks for ("same lead converted twice -> one company").

    404 when `owner_id` does not resolve to an active rep in this organization's directory —
    lead-service maps that to a 422 telling its own caller to assign an owner first, the same
    way `_assert_owner_assignable` already refuses an unknown or deactivated owner on every
    other write here.

    Restricted to lead-service (`_require_caller`) — deal-service reaches its own,
    identically-shaped route, never this one — and to a token whose `org` claim names this
    path's organization (`_require_org_claim`).
    """
    _require_caller(request, "lead")
    _require_org_claim(request, organization_id)

    async with session.begin():
        await _bind(session, organization_id, request)

        ctx = services.lead_conversion_ctx(organization_id, _claimed_actor(request))
        # `NotFoundProblem` here (an unknown or deactivated owner) reaches FastAPI's
        # problem-detail handler unchanged, exactly as it would for a request from a human —
        # lead-service is the one that knows how to translate "no such owner" into its own
        # caller's 422.
        company, contact = await services.ensure_company_and_contact_for_lead(
            session,
            ctx,
            company_id=payload.company_id,
            contact_id=payload.contact_id,
            company_name=payload.company_name,
            owner_id=payload.owner_id,
            contact_full_name=payload.contact_full_name,
            contact_email=payload.contact_email,
            contact_raw_phone=payload.contact_raw_phone,
        )

    log.info(
        "customer.lead_conversion.ensured",
        company_id=str(company.id),
        contact_id=str(contact.id) if contact else None,
    )
    return LeadConversionOut(
        company_id=str(company.id), contact_id=str(contact.id) if contact else None
    )


@router.post(
    "/organizations/{organization_id}/owners:validate",
    response_model=OwnersValidateOut,
    status_code=status.HTTP_200_OK,
    summary="Which of these owner ids are active reps (lead-service only)",
)
async def validate_owners(
    organization_id: uuid.UUID,
    payload: OwnersValidateIn,
    session: SessionDep,
    request: Request,
) -> OwnersValidateOut:
    """lead-service's own gate for an `owner_id` it is about to write — an explicit one on a
    lead create/patch, or every id in a round-robin rule before it accepts the rule
    (`PUT /leads/assignment-rule`) — so an inactive or unknown rep never enters lead-service's
    own writes in the first place.

    Restricted to lead-service alone: no other caller has a legitimate reason to enumerate,
    in bulk, which of an organization's reps are active. Read-only and unaudited on this side
    — the read itself discloses nothing beyond what the allow-listed caller already needed to
    ask to do its own job, and no PII is opened.
    """
    _require_caller(request, "lead")
    _require_org_claim(request, organization_id)

    async with session.begin():
        await _bind(session, organization_id, request)
        invalid = await services.invalid_owner_ids(session, organization_id, payload.owner_ids)

    return OwnersValidateOut(invalid_owner_ids=[str(owner_id) for owner_id in invalid])


@router.get(
    "/insights/export/companies",
    response_model=CompanyExportPageOut,
    summary="Every company, paginated — insights' backfill read (ADR-0020 slice I4)",
)
async def export_companies(
    organization_id: uuid.UUID,
    session: SessionDep,
    request: Request,
    after_updated_at: Annotated[datetime | None, Query()] = None,
    after_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 500,
) -> CompanyExportPageOut:
    """`docs/insights/api.md` §6.1: keyset-paginated on `(updated_at, id)`, soft-deleted rows
    included so a deletion propagates, shaped like `crm.book.company.*`'s full-state fields.
    """
    _require_insights_caller(request)
    async with session.begin():
        await _bind(session, organization_id, request)
        rows = (
            (
                await session.execute(
                    selectors.companies_export_stmt(
                        after_updated_at=after_updated_at, after_id=after_id, limit=limit
                    )
                )
            )
            .scalars()
            .all()
        )
    next_cursor = (
        CompanyExportCursorOut(after_updated_at=rows[-1].updated_at, after_id=rows[-1].id)
        if len(rows) == limit
        else None
    )
    return CompanyExportPageOut(
        rows=[CompanyExportRowOut.model_validate(row) for row in rows], next=next_cursor
    )
