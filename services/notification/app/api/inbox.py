"""HTTP surface for the per-user in-app notification feed.

`GET /notifications/me` is the feed; the two `POST` routes mark one or every unread notification
read. None of the three declares a `notification.*` permission the way `app/api/routes.py`'s
routes do — this is the caller's **own** inbox, scoped by identity rather than by a grant, the
same shape `/auth/me` in auth-service already is. `require_product("notification")` still gates
every route here, the same defence in depth the rest of this service carries: the gateway already
refuses a request for a product the organization has not activated, but a routing-table mistake
must not become an entitlement bypass.

Reads and writes both go through `app.inbox` — never a query built here, and never `app.services`
or `app.selectors`, which this slice deliberately leaves untouched.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app import inbox
from platform_core.auth import CurrentPrincipal, require_product
from platform_core.db import get_tenant_session

router = APIRouter(tags=["notification-inbox"])

#: **The tenancy chokepoint**, same as `app/api/routes.py`: opens the transaction and binds it to
#: the caller's organization before the route body runs.
SessionDep = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]

#: The `notification` entitlement gate, applied to every route below.
Entitled = Depends(require_product("notification"))

Limit = Annotated[int, Query(ge=1, le=inbox.MAX_LIMIT)]
Cursor = Annotated[
    str | None,
    Query(description="The `next_cursor` from a previous page. Omit for the first page."),
]


@router.get(
    "/notifications/me",
    response_model=inbox.NotificationPage,
    summary="The caller's own in-app notification feed",
    dependencies=[Entitled],
)
async def list_my_notifications(
    session: SessionDep,
    principal: CurrentPrincipal,
    cursor: Cursor = None,
    limit: Limit = inbox.DEFAULT_LIMIT,
    unread_only: bool = False,
) -> inbox.NotificationPage:
    recipient_id = inbox.recipient_id_for(principal)
    return await inbox.list_for_recipient(
        session,
        organization_id=principal.organization_id,
        recipient_id=recipient_id,
        limit=limit,
        cursor=cursor,
        unread_only=unread_only,
    )


@router.post(
    "/notifications/me/{notification_id}:read",
    response_model=inbox.NotificationOut,
    summary="Mark one notification read",
    dependencies=[Entitled],
)
async def mark_notification_read(
    notification_id: uuid.UUID,
    session: SessionDep,
    principal: CurrentPrincipal,
) -> inbox.NotificationOut:
    recipient_id = inbox.recipient_id_for(principal)
    return await inbox.mark_read(
        session,
        organization_id=principal.organization_id,
        recipient_id=recipient_id,
        message_id=notification_id,
        actor=str(principal.sub),
    )


@router.post(
    "/notifications/me:read-all",
    response_model=inbox.ReadAllOut,
    summary="Mark every one of the caller's unread notifications read",
    dependencies=[Entitled],
)
async def mark_all_notifications_read(
    session: SessionDep,
    principal: CurrentPrincipal,
) -> inbox.ReadAllOut:
    recipient_id = inbox.recipient_id_for(principal)
    return await inbox.mark_all_read(
        session,
        organization_id=principal.organization_id,
        recipient_id=recipient_id,
        actor=str(principal.sub),
    )
