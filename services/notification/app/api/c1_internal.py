"""The internal messaging summary — service-to-service, for customer-service's 360 (slice C1).

`GET /internal/v1/contacts/{contact_id}/messaging-summary` answers "what does messaging know
about this contact": WhatsApp opt-in, the 24-hour window, last message and conversation count,
and email subscription. It returns facts, never an address, so it has nothing to unmask.

Guarded three ways, because a valid signature only proves *which service* is calling:

  - `require_service_token`   the token is signed, unexpired and addressed to notification;
  - `_claimed_organization`   only customer-service has a reason to ask;
  - the token's `org` claim   is the tenant. There is no organization in the path, so the
                              claim is the only source of it: a caller cannot ask about another
                              tenant's contact by editing a URL, and a token with no `org`
                              claim is refused rather than guessed at.

The email address, when the caller has one, travels in `X-Contact-Email` rather than the URL so it
does not land in an access log.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.c1_messaging_summary import messaging_summary
from platform_core.auth import require_service_token
from platform_core.config import ServiceSettings
from platform_core.db import assert_tenant, get_session, set_tenant
from platform_core.errors import ForbiddenProblem

router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
    include_in_schema=False,
    dependencies=[Depends(require_service_token)],
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]

#: The one service that asks. Everything else holding a valid token is refused.
ALLOWED_CALLERS = frozenset({"customer"})


class WhatsAppChannelOut(BaseModel):
    opted_in: bool
    window_open_until: datetime | None
    last_message_at: datetime | None
    conversations: int


class EmailChannelOut(BaseModel):
    subscribed: bool
    last_email_at: datetime | None


class ChannelsOut(BaseModel):
    """The `channels` object of the customer 360. A channel is `null` when it does not apply
    (WhatsApp not connected and nothing about the contact) or cannot be answered (no email
    address supplied)."""

    whatsapp: WhatsAppChannelOut | None
    email: EmailChannelOut | None


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
    "/contacts/{contact_id}/messaging-summary",
    response_model=ChannelsOut,
    summary="One contact's WhatsApp and email standing",
)
async def read_messaging_summary(
    contact_id: uuid.UUID,
    session: SessionDep,
    request: Request,
    contact_email: Annotated[str | None, Header(alias="X-Contact-Email", max_length=254)] = None,
) -> ChannelsOut:
    organization_id = _claimed_organization(request)
    settings: ServiceSettings = request.app.state.settings
    async with session.begin():
        await set_tenant(session, organization_id)
        if not settings.is_production:
            await assert_tenant(session, organization_id)
        summary = await messaging_summary(
            session, organization_id, contact_id, email_address=contact_email or None
        )
    wa, em = summary.whatsapp, summary.email
    return ChannelsOut(
        whatsapp=WhatsAppChannelOut(
            opted_in=wa.opted_in,
            window_open_until=wa.window_open_until,
            last_message_at=wa.last_message_at,
            conversations=wa.conversations,
        )
        if wa
        else None,
        email=EmailChannelOut(subscribed=em.subscribed, last_email_at=em.last_email_at)
        if em
        else None,
    )
