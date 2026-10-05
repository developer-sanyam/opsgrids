"""The one place a worker's coordinates leave the database: the audited reveal of a put-away's GPS
fix (owner decision D3, 2026-10-05 — ADMIN only, every call audited).

Reads are not otherwise in this module's remit, but this one *writes*: an audit row, in the
caller's transaction, every time it succeeds. That is what makes it a service and not a selector.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.inventory import putaway_selectors
from app.inventory.putaway_schemas import PutawayLocationOut
from app.models import AuditLog
from platform_core.audit import record_sensitive_read
from platform_core.auth import ScopeContext
from platform_core.errors import NotFoundProblem
from platform_core.logging import get_logger

log = get_logger("operations.inventory.putaway")

#: The names of what a reveal discloses — the audit row records these, never their values.
REVEALED_FIELDS = ["lat_micro", "lng_micro", "accuracy_m"]


async def reveal_putaway_location(
    session: AsyncSession,
    ctx: ScopeContext,
    move_id: uuid.UUID,
    *,
    request: Request | None = None,
) -> PutawayLocationOut:
    """Return the GPS fix recorded with a put-away, and write **one** audit row for having done so
    (`entity.sensitive_read`, field names only) — also when the capture holds no fix, because the
    act of asking is what is audited, not what was found.

    A move with no capture (it was not a put-away, the capture could not be written, or it was
    purged after 30 days) and another tenant's move are the same 404 and write nothing; so does a
    caller without the permission, who never reaches this function. The audit row and the response
    commit or roll back together: a reveal the request could not complete leaves no row claiming it
    happened, and a returned fix always has its row."""
    capture = await putaway_selectors.get_capture(session, ctx.organization_id, move_id)
    if capture is None:
        raise NotFoundProblem("That put-away has no recorded capture")
    record_sensitive_read(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        entity="inventory_move",
        entity_id=move_id,
        fields=REVEALED_FIELDS,
        request=request,
    )
    log.info("putaway.location_revealed", actor_id=str(ctx.principal.sub), move_id=str(move_id))
    return PutawayLocationOut(
        lat_micro=capture.lat_micro,
        lng_micro=capture.lng_micro,
        accuracy_m=capture.accuracy_m,
        captured_at=capture.captured_at,
    )
