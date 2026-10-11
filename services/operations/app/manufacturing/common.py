"""Helpers the BOM and production write paths share: parsing at the edge, existence checks that
answer 404 for anything outside the caller's organization, idempotency claims, and the audit +
outbox staging every write ends with.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.inventory import selectors as inv_selectors
from app.inventory.quantity import QuantityError, parse_qty
from app.manufacturing import domain
from app.manufacturing.models import ManufacturingRequest
from app.models import AuditLog, InventoryItem, Location, OutboxEvent
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import ConflictProblem, NotFoundProblem, ValidationProblem
from platform_core.events import publish_event

EVT_PRODUCTION_STARTED = "operations.production.started"
EVT_PRODUCTION_COMPLETED = "operations.production.completed"
EVT_MATERIAL_SHORTAGE = "operations.production.material_shortage"
EVT_WASTAGE_THRESHOLD = "operations.production.wastage_threshold_exceeded"


def today() -> date:
    return datetime.now(UTC).date()


def actor(ctx: ScopeContext) -> dict[str, Any]:
    return {
        "type": "user",
        "id": str(ctx.principal.sub),
        "membership_id": str(ctx.principal.membership_id) if ctx.principal.membership_id else None,
    }


def user_ref(ctx: ScopeContext) -> str:
    return str(ctx.principal.sub)


# ------------------------------------------------------------------ parsing


def qty(value: str, uom: str = "", *, field: str = "quantity", positive: bool = True) -> int:
    """A wire quantity -> milli-units, as a 422 rather than a ValueError."""
    try:
        milli = parse_qty(value, uom)
    except QuantityError as exc:
        raise ValidationProblem(f"{field}: {exc}") from exc
    if positive and milli <= 0:
        raise ValidationProblem(f"{field} must be greater than zero")
    return milli


def pct(value: str, *, field: str = "scrap_percent") -> int:
    try:
        return domain.parse_pct_bps(value)
    except domain.PercentError as exc:
        raise ValidationProblem(f"{field}: {exc}") from exc


def check_window(start: date | None, end: date | None, *, what: str = "effective") -> None:
    if start is not None and end is not None and end < start:
        raise ValidationProblem(f"{what}_to is before {what}_from")


# ------------------------------------------------------------------ existence (404 = not yours)


async def require_item(
    session: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID
) -> InventoryItem:
    item = (
        await session.execute(
            select(InventoryItem).where(
                InventoryItem.id == item_id,
                InventoryItem.organization_id == org_id,
                InventoryItem.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise NotFoundProblem("That item is not part of this organization")
    return item


async def require_location(
    session: AsyncSession, org_id: uuid.UUID, location_id: uuid.UUID
) -> Location:
    location = await inv_selectors.get_location(session, org_id, location_id)
    if location is None:
        raise NotFoundProblem("That location is not part of this organization")
    return location


# ------------------------------------------------------------------ audit + events


def audit(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    action: str,
    entity: str,
    entity_id: uuid.UUID,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    reason: str | None = None,
    request: Request | None = None,
) -> None:
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action=action,
        entity=entity,
        entity_id=entity_id,
        before=before,
        after=after,
        reason=reason,
        request=request,
    )


def emit(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    event_type: str,
    aggregate_id: uuid.UUID,
    payload: dict[str, Any],
    aggregate_type: str = "production_order",
) -> None:
    """Stage an event in the caller's transaction; the relay publishes it only after commit."""
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload={"organization_id": str(ctx.organization_id), **payload},
        actor=actor(ctx),
    )


# ------------------------------------------------------------------ idempotency


@dataclass(slots=True)
class Claim:
    """The outcome of presenting an `Idempotency-Key`. `replay` means an earlier request with
    this key already did the work: return its result, do nothing."""

    row: ManufacturingRequest | None
    replay: bool = False

    @property
    def result_id(self) -> uuid.UUID | None:
        return self.row.result_id if self.row else None

    def done(self, result_id: uuid.UUID) -> None:
        if self.row is not None and not self.replay:
            self.row.result_id = result_id


def _hash(scope: str, payload: Any, actor: uuid.UUID) -> str:
    # The caller is part of the request: a key someone else guessed or saw never replays for them.
    canonical = json.dumps(
        [scope, payload, str(actor)], sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def claim(
    session: AsyncSession,
    ctx: ScopeContext,
    key: str | None,
    *,
    scope: str,
    payload: Any,
) -> Claim:
    """Claim `key` for this request. Inserted first with `ON CONFLICT DO NOTHING`: a concurrent
    duplicate blocks on the unique index until the first request commits, then sees its row and
    replays. The same key with a different request (scope or body) is a 409, never a silent
    second execution."""
    if key is None:
        return Claim(None)
    org_id = ctx.organization_id
    digest = _hash(scope, payload, ctx.principal.sub)
    inserted = (
        await session.execute(
            pg_insert(ManufacturingRequest)
            .values(
                id=uuid.uuid4(),
                organization_id=org_id,
                idempotency_key=key,
                scope=scope,
                request_hash=digest,
            )
            .on_conflict_do_nothing(index_elements=["organization_id", "idempotency_key"])
            .returning(ManufacturingRequest.id)
        )
    ).scalar_one_or_none()
    row = (
        await session.execute(
            select(ManufacturingRequest)
            .where(
                ManufacturingRequest.organization_id == org_id,
                ManufacturingRequest.idempotency_key == key,
            )
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    if inserted is not None:
        return Claim(row)
    if row.request_hash != digest or row.scope != scope:
        raise ConflictProblem("That Idempotency-Key was already used for a different request")
    return Claim(row, replay=True)
