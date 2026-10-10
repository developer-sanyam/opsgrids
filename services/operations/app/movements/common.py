"""Shared plumbing for the Phase 3 write paths: event names, the system caller used when the
approval service answers, settings and document numbers, request hashing for idempotency."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.inventory.quantity import QuantityError, parse_qty
from app.models import AuditLog, InventoryControlSettings
from platform_core.audit import record_audit
from platform_core.auth import Principal, Scope, ScopeContext
from platform_core.errors import ValidationProblem

# --- events this package emits (JSON contracts in libs/contracts/events) -----------------------
EVT_TRANSFER_REQUESTED = "operations.inventory.transfer.requested"
EVT_TRANSFER_APPROVED = "operations.inventory.transfer.approved"
EVT_TRANSFER_REJECTED = "operations.inventory.transfer.rejected"
EVT_TRANSFER_CANCELLED = "operations.inventory.transfer.cancelled"
EVT_TRANSFER_DISPATCHED = "operations.inventory.transfer.dispatched"
EVT_TRANSFER_RECEIVED = "operations.inventory.transfer.received"
#: The approval service's return leg (ADR-0006 §12): what became of a decision it sent us.
EVT_TRANSFER_APPLIED = "operations.inventory.transfer.applied"
EVT_ADJUSTMENT_REQUESTED = "operations.inventory.adjustment.requested"
EVT_ADJUSTMENT_APPLIED = "operations.inventory.adjustment.applied"
EVT_ADJUSTMENT_CANCELLED = "operations.inventory.adjustment.cancelled"
EVT_RESERVATION_EXPIRED = "operations.inventory.reservation.expired"

# --- the approval service's side of the contract (ADR-0006) -------------------------------------
#: `<product>.<module>.<thing>` — the shape `approval_requests.request_type` is constrained to.
REQUEST_TYPE_TRANSFER = "operations.inventory.transfer"
REQUEST_TYPE_ADJUSTMENT = "operations.inventory.adjustment"
EVT_APPROVAL_OPENED = "core.approval.request.opened"
EVT_APPROVAL_DECIDED = "core.approval.request.decided"

_SYSTEM_PERMISSION = "operations.inventory.stock.adjust"


def system_ctx(organization_id: uuid.UUID) -> ScopeContext:
    """A `ScopeContext` for code with no human caller: the approval consumer applying a decision,
    and the reservation-expiry sweep. The same shape `app.orders.services.service_ctx` and the
    other modules establish; the zero uuid is how an audit row and a ledger row say "the system"."""
    principal = Principal(
        sub=uuid.UUID(int=0),
        organization_id=organization_id,
        membership_id=None,
        perms=[f"{_SYSTEM_PERMISSION}:ORG"],
        entitlements=["operations"],
    )
    return ScopeContext(principal=principal, scope=Scope.ORG, permission=_SYSTEM_PERMISSION)


def audit_only(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    action: str,
    entity: str,
    entity_id: uuid.UUID,
    after: dict[str, Any],
    reason: str | None,
    request: Request | None,
) -> None:
    """An audit row with no event — for a step nobody downstream needs to hear about (a draft
    being created or edited)."""
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action=action,
        entity=entity,
        entity_id=entity_id,
        after=after,
        reason=reason or None,
        request=request,
    )


def request_hash(payload: dict[str, Any]) -> str:
    """Stable digest of a request body — what a replayed idempotency key is compared against."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def serialise_key(session: AsyncSession, organization_id: uuid.UUID, key: str) -> None:
    """Take a transaction-scoped advisory lock on `(organization, key)`, so two concurrent
    requests carrying one idempotency key queue: the second then finds the first's committed row
    instead of colliding with it on the unique index."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"{organization_id}:{key}"},
    )


def parse_signed(value: str, uom: str, field: str) -> int:
    """A signed decimal string -> milli-units, refusing zero, floats, and a fraction on a
    discrete unit."""
    try:
        milli = parse_qty(value, uom, allow_negative=True)
    except QuantityError as exc:
        raise ValidationProblem(
            str(exc), errors=[{"field": field, "code": "invalid_quantity", "message": str(exc)}]
        ) from exc
    if milli == 0:
        raise ValidationProblem(
            "A quantity here must not be zero",
            errors=[{"field": field, "code": "invalid_quantity", "message": "must not be 0"}],
        )
    return milli


def parse_non_negative(value: str, uom: str, field: str) -> int:
    try:
        return parse_qty(value, uom)
    except QuantityError as exc:
        raise ValidationProblem(
            str(exc), errors=[{"field": field, "code": "invalid_quantity", "message": str(exc)}]
        ) from exc


async def get_settings(
    session: AsyncSession, organization_id: uuid.UUID
) -> InventoryControlSettings | None:
    return (
        await session.execute(
            select(InventoryControlSettings).where(
                InventoryControlSettings.organization_id == organization_id
            )
        )
    ).scalar_one_or_none()


def parse_threshold(value: str | None, field: str) -> int | None:
    """A threshold is a positive quantity (or `None`: none set)."""
    if value is None:
        return None
    try:
        milli = parse_qty(value)
    except QuantityError as exc:
        raise ValidationProblem(
            str(exc), errors=[{"field": field, "code": "invalid_quantity", "message": str(exc)}]
        ) from exc
    if milli <= 0:
        raise ValidationProblem(
            "A threshold must be greater than zero (clear it to set none)",
            errors=[{"field": field, "code": "invalid_quantity", "message": "must be > 0"}],
        )
    return milli


async def set_thresholds(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    transfer_milli: int | None,
    adjustment_milli: int | None,
    request: Request | None = None,
) -> InventoryControlSettings:
    """Replace both thresholds (creating the settings row on first use), audited with what they
    were and what they became — this decides which movements need a second pair of eyes."""
    org_id = ctx.organization_id
    before = await get_settings(session, org_id)
    now = datetime.now(UTC)
    stmt = (
        pg_insert(InventoryControlSettings)
        .values(
            organization_id=org_id,
            transfer_approval_threshold_milli=transfer_milli,
            adjustment_approval_threshold_milli=adjustment_milli,
            updated_by=str(ctx.principal.sub),
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=[InventoryControlSettings.organization_id],
            set_={
                "transfer_approval_threshold_milli": transfer_milli,
                "adjustment_approval_threshold_milli": adjustment_milli,
                "updated_by": str(ctx.principal.sub),
                "updated_at": now,
            },
        )
    )
    await session.execute(stmt)
    row = (
        await session.execute(
            select(InventoryControlSettings)
            .where(InventoryControlSettings.organization_id == org_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    audit_only(
        session,
        ctx,
        action="inventory_control_settings.updated",
        entity="inventory_control_settings",
        entity_id=row.id,
        after={
            "transfer_approval_threshold_milli": transfer_milli,
            "adjustment_approval_threshold_milli": adjustment_milli,
            "was": {
                "transfer_approval_threshold_milli": before.transfer_approval_threshold_milli
                if before
                else None,
                "adjustment_approval_threshold_milli": before.adjustment_approval_threshold_milli
                if before
                else None,
            },
        },
        reason=None,
        request=request,
    )
    return row


async def next_number(session: AsyncSession, organization_id: uuid.UUID, kind: str) -> int:
    """Allocate the next document number of `kind` (`transfer` | `adjustment`) for the
    organization. One statement: the row is created on first use and incremented after, and the
    increment is the lock, so two concurrent creates can never be handed the same number."""
    column = {
        "transfer": InventoryControlSettings.next_transfer_no,
        "adjustment": InventoryControlSettings.next_adjustment_no,
    }[kind]
    stmt = (
        pg_insert(InventoryControlSettings)
        .values(organization_id=organization_id, **{column.key: 2})
        .on_conflict_do_update(
            index_elements=[InventoryControlSettings.organization_id],
            set_={column.key: column + 1},
        )
        .returning(column)
    )
    allocated = (await session.execute(stmt)).scalar_one()
    return int(allocated) - 1
