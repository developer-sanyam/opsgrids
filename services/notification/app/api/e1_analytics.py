"""Email open/click tracking endpoints and campaign analytics reads (slice E1).

Two routers. `public_router` is unauthenticated by design: a mail client fetching an image or a
person clicking a link has no bearer token, and the signed token in the path is the whole of its
authority. `router` is the ordinary authenticated, entitlement-gated Engage surface.

Every failure on the public side is the same answer: a forged, malformed, wrong-kind, unknown or
foreign-organization token is a 404 (or, for the pixel, the same GIF with nothing recorded), so a
caller learns nothing about why. No IP address and no raw user agent is ever stored or logged.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app import e1_analytics, e1_tracking
from app.api.engage import _PUBLIC_HEADERS, CampaignRead, Entitled, PlainSessionDep, SessionDep
from app.e1_schemas import CampaignAnalyticsOut, EmailOverviewOut
from app.models import CampaignRecipient
from platform_core.db import set_tenant
from platform_core.errors import NotFoundProblem, ValidationProblem

router = APIRouter(tags=["engage"])
public_router = APIRouter(tags=["engage-public"])

#: Repeated fetches must reach us: an image cached by an intermediary would hide every later open.
_NO_STORE = {**_PUBLIC_HEADERS, "Cache-Control": "no-store, max-age=0"}
_NOT_FOUND = "This link is not valid"


def _client_ip(request: Request) -> str | None:
    """Read for classification only (Apple's proxy range) and dropped: never stored or logged."""
    return request.headers.get("x-forwarded-for") or (
        request.client.host if request.client else None
    )


async def _recipient_context(session, organization_id: uuid.UUID, recipient_id: uuid.UUID):  # type: ignore[no-untyped-def]
    stmt = select(CampaignRecipient.campaign_id, CampaignRecipient.sent_at).where(
        CampaignRecipient.organization_id == organization_id,
        CampaignRecipient.id == recipient_id,
    )
    return (await session.execute(stmt)).one_or_none()


@public_router.get(
    "/engage/t/o/{token}",
    response_class=Response,
    responses={200: {"content": {"image/gif": {}}}},
    summary="Open-tracking pixel: a 1x1 GIF, and an open recorded when the token is genuine",
)
async def track_open(token: str, request: Request, session: PlainSessionDep) -> Response:
    """Always the same GIF, so a bad token is indistinguishable from a good one."""
    pixel = Response(e1_tracking.PIXEL_GIF, media_type="image/gif", headers=_NO_STORE)
    claims = e1_tracking.verify_token("open", token, e1_tracking.tracking_key())
    if claims is None:
        return pixel
    async with session.begin():
        await set_tenant(session, claims.organization_id)
        context = await _recipient_context(session, claims.organization_id, claims.recipient_id)
        if context is not None:
            campaign_id, sent_at = context
            device, machine = e1_tracking.classify(
                request.headers.get("user-agent"), client_ip=_client_ip(request), sent_at=sent_at
            )
            await e1_tracking.record_event(
                session,
                organization_id=claims.organization_id,
                recipient_id=claims.recipient_id,
                campaign_id=campaign_id,
                kind="open",
                link_id=None,
                user_agent_class=device,
                is_machine=machine,
            )
    return pixel


@public_router.get(
    "/engage/t/c/{token}",
    response_class=Response,
    status_code=status.HTTP_302_FOUND,
    summary="Click tracking: records the click and redirects to a link that was in the message",
)
async def track_click(token: str, request: Request, session: PlainSessionDep) -> Response:
    """Redirects only to a destination registered from the sent message's own body. The
    requester supplies a signed token, never a URL, so this cannot be an open redirect."""
    claims = e1_tracking.verify_token("click", token, e1_tracking.tracking_key())
    if claims is None or claims.link_id is None:
        raise NotFoundProblem(_NOT_FOUND, headers=_NO_STORE)
    async with session.begin():
        await set_tenant(session, claims.organization_id)
        link = await e1_tracking.link_destination(session, claims.organization_id, claims.link_id)
        context = await _recipient_context(session, claims.organization_id, claims.recipient_id)
        if (
            link is None
            or context is None
            or context[0] != link.campaign_id
            or not e1_tracking.is_trackable_url(link.url)
        ):
            raise NotFoundProblem(_NOT_FOUND, headers=_NO_STORE)
        device, machine = e1_tracking.classify(
            request.headers.get("user-agent"), client_ip=_client_ip(request), sent_at=context[1]
        )
        await e1_tracking.record_event(
            session,
            organization_id=claims.organization_id,
            recipient_id=claims.recipient_id,
            campaign_id=link.campaign_id,
            kind="click",
            link_id=link.id,
            user_agent_class=device,
            is_machine=machine,
        )
        destination = link.url
    return RedirectResponse(destination, status_code=status.HTTP_302_FOUND, headers=_NO_STORE)


@router.get(
    "/engage/campaigns/{campaign_id}/analytics",
    response_model=CampaignAnalyticsOut,
    summary="One campaign's delivery and engagement (human opens and clicks only)",
    dependencies=[Entitled],
)
async def campaign_analytics(
    campaign_id: uuid.UUID, session: SessionDep, ctx: CampaignRead
) -> CampaignAnalyticsOut:
    result = await e1_analytics.campaign_analytics(session, ctx, campaign_id)
    if result is None:
        raise NotFoundProblem("No such campaign")
    return CampaignAnalyticsOut.model_validate(result)


@router.get(
    "/engage/analytics/email/overview",
    response_model=EmailOverviewOut,
    summary="Email sends and engagement over a date range (inclusive UTC dates)",
    dependencies=[Entitled],
)
async def email_overview(
    session: SessionDep,
    ctx: CampaignRead,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
) -> EmailOverviewOut:
    end = date_to or datetime.now(UTC).date()
    start = date_from or end - timedelta(days=29)
    if start > end:
        raise ValidationProblem("`from` must not be after `to`")
    if (end - start).days >= e1_analytics.MAX_RANGE_DAYS:
        raise ValidationProblem(f"The range may span at most {e1_analytics.MAX_RANGE_DAYS} days")
    result = await e1_analytics.email_overview(session, ctx.organization_id, start, end)
    return EmailOverviewOut.model_validate(result)
