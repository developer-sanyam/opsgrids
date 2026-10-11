"""Service-to-service reads (`/internal/v1`, outside `/api/v1`, never in the public schema).

`GET /internal/v1/enquiries/{id}/whatsapp-opt-in` hands notification-service the buyer's number
for an enquiry whose buyer ticked the form's WhatsApp marketing opt-in, so notification can record
it on its consent ledger (ENGAGE-02). The `storefront.enquiry.whatsapp_opted_in` event carries
ids only; this is where the number crosses, once, audited.

Guarded the way notification's own `/internal/v1` is: a signed service token addressed to
storefront, issued by notification and no other service, whose `org` claim is the tenant (there
is no organization in the path to edit). An enquiry that was not ticked, or belongs to another
organization, is a 404 — so this cannot be used to read any other buyer's number.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.schemas import EnquiryWhatsAppOptInOut
from platform_core.auth import require_service_token
from platform_core.db import get_session, set_tenant
from platform_core.errors import ForbiddenProblem, NotFoundProblem

router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
    include_in_schema=False,
    dependencies=[Depends(require_service_token)],
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]

#: The one service that asks.
ALLOWED_CALLERS = frozenset({"notification"})


def _claimed_organization(request: Request) -> uuid.UUID:
    caller = getattr(request.state, "service_caller", "unknown-service")
    if caller not in ALLOWED_CALLERS:
        raise ForbiddenProblem(f"{caller!r} may not call this endpoint")
    claims = getattr(request.state, "service_claims", None) or {}
    try:
        return uuid.UUID(str(claims["org"]))
    except (KeyError, ValueError):
        raise ForbiddenProblem("This token is not addressed to an organization") from None


@router.get(
    "/enquiries/{enquiry_id}/whatsapp-opt-in",
    response_model=EnquiryWhatsAppOptInOut,
    summary="A ticked enquiry's buyer number, for notification's consent ledger",
)
async def read_whatsapp_opt_in(
    enquiry_id: uuid.UUID, session: SessionDep, request: Request
) -> EnquiryWhatsAppOptInOut:
    organization_id = _claimed_organization(request)
    async with session.begin():
        await set_tenant(session, organization_id)
        found = await services.read_whatsapp_opt_in(
            session, organization_id, enquiry_id, request=request
        )
        if found is None:
            raise NotFoundProblem("No such enquiry")
    enquiry, email, phone = found
    assert enquiry.whatsapp_opt_in_at is not None  # the selector only returns ticked enquiries
    return EnquiryWhatsAppOptInOut(
        name=enquiry.name, email=email, phone=phone, opted_in_at=enquiry.whatsapp_opt_in_at
    )
