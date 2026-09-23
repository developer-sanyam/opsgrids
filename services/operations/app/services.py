"""Writes for the operations service.

Every function follows the platform shape: change the row, record the audit entry, stage the
event — **all inside one transaction**. `get_tenant_session` opens the transaction and does not
commit for the route, so a crash between the state change and the event cannot leave one without
the other, and a rolled-back write never leaves an audit row claiming it happened.

Events are staged into the outbox, never published inline (ADR-0000). Money is integer minor
units throughout: a purchase-order line's `amount_minor` is computed as `quantity *
unit_price_minor` at write time, so the lines foot to the derived PO total by construction.

These writers serve both the HTTP surface (`create_purchase_order`, `receive_purchase_order`) and
the demo seed, which calls the same functions with explicit numbers and dates so the live screens
mirror the fixtures the user already approved.

Suppliers and purchase orders live here; inventory items, warehouses and every stock write live
in `app.inventory` (ADR-0014 §1). A receipt posts through `app.inventory.services`.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain, selectors
from app.inventory import selectors as inv_selectors
from app.inventory import services as inv_services
from app.models import (
    AuditLog,
    OutboxEvent,
    PurchaseOrder,
    PurchaseOrderLine,
    Supplier,
)
from app.schemas import (
    PurchaseOrderPatch,
    SupplierPatch,
)
from platform_core.audit import record_audit
from platform_core.auth import Scope, ScopeContext
from platform_core.errors import (
    ConflictProblem,
    ForbiddenProblem,
    NotFoundProblem,
    ValidationProblem,
)
from platform_core.events import publish_event

# Event names. `operations.*` because these are the product's own, not the platform's.
EVT_SUPPLIER_CREATED = "operations.procurement.supplier.created"
EVT_SUPPLIER_UPDATED = "operations.procurement.supplier.updated"
EVT_SUPPLIER_DELETED = "operations.procurement.supplier.deleted"
EVT_PO_RAISED = "operations.procurement.purchase_order.raised"
EVT_PO_UPDATED = "operations.procurement.purchase_order.updated"
EVT_PO_DELETED = "operations.procurement.purchase_order.deleted"
EVT_PO_APPROVED = "operations.procurement.purchase_order.approved"
EVT_PO_RECEIVED = "operations.procurement.purchase_order.received"
EVT_PO_CLOSED = "operations.procurement.purchase_order.closed"
EVT_PO_CANCELLED = "operations.procurement.purchase_order.cancelled"


def _actor(ctx: ScopeContext) -> dict[str, Any]:
    return {
        "type": "user",
        "id": str(ctx.principal.sub),
        "membership_id": str(ctx.principal.membership_id) if ctx.principal.membership_id else None,
    }


async def _bump_supplier(
    session: AsyncSession,
    org_id: uuid.UUID,
    supplier_id: uuid.UUID,
    *,
    open_pos: int = 0,
    spend_minor: int = 0,
) -> Supplier | None:
    """Apply an incremental delta to a supplier's rollups, under a row lock so two concurrent PO
    transitions cannot lose one's update. `open_pos` floors at zero: the seeded figures are
    lifetime counters, not values this delta arithmetic derives from scratch, so a transition
    replayed against a stale start must not go negative. None (a no-op) if the supplier is gone —
    it cannot be, since a PO's `supplier_id` is `ON DELETE RESTRICT`, but a caller must not raise
    over a rollup on the way out of a transition that has already committed its main effect.
    """
    if not open_pos and not spend_minor:
        return await selectors.get_supplier(session, org_id, supplier_id)
    stmt = (
        select(Supplier)
        .where(Supplier.id == supplier_id, Supplier.organization_id == org_id)
        .with_for_update()
    )
    supplier = (await session.execute(stmt)).scalar_one_or_none()
    if supplier is None:
        return None
    if open_pos:
        supplier.open_pos = max(0, supplier.open_pos + open_pos)
    if spend_minor:
        supplier.spend_minor = supplier.spend_minor + spend_minor
    await session.flush()
    return supplier


# ------------------------------------------------------------------ suppliers


async def create_supplier(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    code: str,
    name: str,
    category: str,
    status: str = "active",
    lead_time_days: int = 0,
    on_time_pct: float | Decimal = 0,
    spend_minor: int = 0,
    currency: str = "INR",
    open_pos: int = 0,
    contact_name: str = "",
    contact_email: str = "",
    location: str = "",
    since: date | None = None,
    request: Request | None = None,
) -> Supplier:
    """Create a supplier. `spend_minor` is lifetime spend in integer minor units of `currency`.
    `since` defaults to today rather than staying null — `SupplierOut.since` is a required field
    over a nullable column, and a row created with none used to be an unconditional 500."""
    if await selectors.get_supplier_by_code(session, ctx.organization_id, code):
        raise ConflictProblem(
            f"A supplier coded {code!r} already exists",
            errors=[
                {
                    "field": "code",
                    "code": "duplicate",
                    "message": f"A supplier coded {code!r} already exists",
                }
            ],
        )

    supplier = Supplier(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        code=code,
        name=name,
        category=category,
        status=status,
        lead_time_days=lead_time_days,
        on_time_pct=Decimal(str(on_time_pct)),
        spend_minor=spend_minor,
        currency=currency.upper(),
        open_pos=open_pos,
        contact_name=contact_name,
        contact_email=contact_email,
        location=location,
        since=since or date.today(),
    )
    session.add(supplier)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="supplier.created",
        entity="supplier",
        entity_id=supplier.id,
        after={"code": code, "category": category, "status": status},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_SUPPLIER_CREATED,
        aggregate_type="supplier",
        aggregate_id=supplier.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "supplier_id": str(supplier.id),
            "code": code,
            "category": category,
        },
        actor=_actor(ctx),
    )
    return supplier


async def update_supplier(
    session: AsyncSession,
    ctx: ScopeContext,
    supplier: Supplier,
    payload: SupplierPatch,
    *,
    request: Request | None = None,
) -> Supplier:
    """Patch the mutable supplier fields — including `status`, so this is also how a supplier
    moves `active` <-> `on_hold` <-> `pending`. `code` and `currency` cannot appear here:
    `SupplierPatch` has no such fields, so they are never in `changes`."""
    changes = payload.model_dump(exclude_unset=True)
    before = {field: getattr(supplier, field) for field in changes}
    for field, value in changes.items():
        setattr(supplier, field, value)
    supplier.updated_by = str(ctx.principal.sub)
    await session.flush()

    if "name" in changes:
        # `purchase_orders.supplier_name` is a denormalised copy, not a join — a rename must
        # cascade in the same transaction or every existing PO row keeps the stale name.
        await session.execute(
            update(PurchaseOrder)
            .where(
                PurchaseOrder.organization_id == ctx.organization_id,
                PurchaseOrder.supplier_id == supplier.id,
            )
            .values(supplier_name=changes["name"], updated_by=str(ctx.principal.sub))
        )
        await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="supplier.updated",
        entity="supplier",
        entity_id=supplier.id,
        before=before,
        after=changes,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_SUPPLIER_UPDATED,
        aggregate_type="supplier",
        aggregate_id=supplier.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "supplier_id": str(supplier.id),
            "changed": sorted(changes),
        },
        actor=_actor(ctx),
    )
    return supplier


async def delete_supplier(
    session: AsyncSession,
    ctx: ScopeContext,
    supplier: Supplier,
    *,
    request: Request | None = None,
) -> None:
    """Soft-delete a supplier. Refused (409) while it still has a `draft`/`approved` purchase
    order — put it `on_hold` instead of deleting a vendor mid-order. Historic (received/closed/
    cancelled) POs keep their denormalised `supplier_name` and simply 404 on the supplier link."""
    open_count = await selectors.count_open_purchase_orders_for_supplier(
        session, ctx.organization_id, supplier.id
    )
    if open_count:
        raise ConflictProblem(
            f"{supplier.name} has {open_count} open purchase order(s); put it on hold instead "
            "of deleting it"
        )

    before_deleted_at = supplier.deleted_at
    supplier.deleted_at = datetime.now(UTC)
    supplier.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="supplier.deleted",
        entity="supplier",
        entity_id=supplier.id,
        before={"deleted_at": before_deleted_at},
        after={"deleted_at": supplier.deleted_at},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_SUPPLIER_DELETED,
        aggregate_type="supplier",
        aggregate_id=supplier.id,
        payload={"organization_id": str(ctx.organization_id), "supplier_id": str(supplier.id)},
        actor=_actor(ctx),
    )


# ------------------------------------------------------------------ purchase orders


async def create_purchase_order(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    number: str,
    supplier_id: uuid.UUID | None = None,
    supplier_code: str | None = None,
    status: str = "draft",
    warehouse: str = "",
    raised_by: str = "",
    raised_by_id: uuid.UUID | None = None,
    currency: str = "INR",
    created_date: date,
    expected_date: date,
    received_date: date | None = None,
    notes: str | None = None,
    category: str | None = None,
    lines: Sequence[Mapping[str, Any]] = (),
    request: Request | None = None,
) -> PurchaseOrder:
    """Raise a purchase order and its lines.

    The supplier may be named by `supplier_id` or by `supplier_code` (the seed uses the code);
    the supplier's name and category are denormalised onto the PO. Each line's `amount_minor` is
    computed as `quantity * unit_price_minor`, so the lines foot to the derived PO total.

    A line mapping is `{sku, description, quantity, uom, unit_price_minor, received_qty}`.
    """
    if await selectors.get_purchase_order_by_number(session, ctx.organization_id, number):
        raise ConflictProblem(f"A purchase order numbered {number!r} already exists")

    supplier: Supplier | None = None
    if supplier_id is not None:
        supplier = await selectors.get_supplier(session, ctx.organization_id, supplier_id)
    elif supplier_code is not None:
        supplier = await selectors.get_supplier_by_code(session, ctx.organization_id, supplier_code)
    if supplier is None:
        raise NotFoundProblem("That supplier is not part of this organization")

    po = PurchaseOrder(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        number=number,
        supplier_id=supplier.id,
        supplier_name=supplier.name,
        category=category or supplier.category,
        status=status,
        currency=currency.upper(),
        warehouse=warehouse,
        raised_by=raised_by,
        raised_by_id=raised_by_id,
        created_date=created_date,
        expected_date=expected_date,
        received_date=received_date,
        notes=notes,
    )
    session.add(po)
    await session.flush()

    for spec in lines:
        quantity = int(spec["quantity"])
        unit_price_minor = int(spec["unit_price_minor"])
        session.add(
            PurchaseOrderLine(
                organization_id=ctx.organization_id,
                created_by=str(ctx.principal.sub),
                purchase_order_id=po.id,
                sku=spec["sku"],
                description=spec.get("description", ""),
                quantity=quantity,
                uom=spec.get("uom", ""),
                unit_price_minor=unit_price_minor,
                amount_minor=domain.line_amount_minor(quantity, unit_price_minor),
                received_qty=int(spec.get("received_qty", 0)),
            )
        )
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="purchase_order.raised",
        entity="purchase_order",
        entity_id=po.id,
        after={"number": number, "supplier_id": str(supplier.id), "status": status},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_PO_RAISED,
        aggregate_type="purchase_order",
        aggregate_id=po.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "purchase_order_id": str(po.id),
            "number": number,
            "supplier_id": str(supplier.id),
            "status": status,
        },
        actor=_actor(ctx),
    )
    return po


async def next_po_number(session: AsyncSession, ctx: ScopeContext) -> str:
    """Race-safe server-assigned `PO-{yyyy}-{seq:04d}`, serialised per organization per year.

    `pg_advisory_xact_lock` on a key derived from the org id blocks a second concurrent create
    until the first commits (or rolls back), so two requests racing for the same org never
    compute the same next sequence — the lock is transaction-scoped and releases automatically.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"{ctx.organization_id}:po_number"},
    )
    year = date.today().year
    prefix = f"PO-{year}-"
    # Never filters `deleted_at`: a soft-deleted or cancelled PO's number stays reserved
    # (`uq_purchase_orders_org_number` is intentionally not partial — a number is never reused).
    stmt = select(func.max(PurchaseOrder.number)).where(
        PurchaseOrder.organization_id == ctx.organization_id,
        PurchaseOrder.number.like(f"{prefix}%"),
    )
    current_max = (await session.execute(stmt)).scalar_one_or_none()
    seq = 1
    if current_max:
        try:
            seq = int(current_max.rsplit("-", 1)[-1]) + 1
        except ValueError:
            seq = 1
    return f"{prefix}{seq:04d}"


async def raise_purchase_order(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    supplier_id: uuid.UUID,
    warehouse: str,
    expected_date: date,
    currency: str | None = None,
    notes: str | None = None,
    raised_by: str = "",
    lines: Sequence[Mapping[str, Any]],
    request: Request | None = None,
) -> PurchaseOrder:
    """The HTTP creation path: `POST /purchase-orders`. Wraps `create_purchase_order` with the
    things a client must never control — the server-assigned number, the supplier lookup and its
    `active` gate, and the `open_pos` rollup. Kept separate from `create_purchase_order` itself
    because the demo seed also calls that writer directly with its own literal historical
    `open_pos` figures, and must not have this rollup applied a second time on top of them."""
    supplier = await selectors.get_supplier(session, ctx.organization_id, supplier_id)
    if supplier is None:
        raise NotFoundProblem("That supplier is not part of this organization")
    if supplier.status != "active":
        raise ConflictProblem(
            f"Supplier {supplier.code} is {supplier.status} and cannot receive a new purchase order"
        )
    if await inv_selectors.get_warehouse_by_name(session, ctx.organization_id, warehouse) is None:
        raise ValidationProblem(f"{warehouse!r} does not match an existing warehouse")

    number = await next_po_number(session, ctx)
    po = await create_purchase_order(
        session,
        ctx,
        number=number,
        supplier_id=supplier.id,
        status="draft",
        warehouse=warehouse,
        raised_by=raised_by,
        raised_by_id=ctx.subject_ref,
        currency=(currency or supplier.currency),
        created_date=date.today(),
        expected_date=expected_date,
        category=supplier.category,
        notes=notes,
        lines=lines,
        request=request,
    )
    await _bump_supplier(session, ctx.organization_id, supplier.id, open_pos=1)
    return po


async def update_purchase_order(
    session: AsyncSession,
    ctx: ScopeContext,
    po: PurchaseOrder,
    payload: PurchaseOrderPatch,
    *,
    request: Request | None = None,
) -> PurchaseOrder:
    """Edit a **draft** purchase order. `status` never appears on `PurchaseOrderPatch` — every
    transition goes through its own endpoint. When `lines` is present it fully replaces the
    order's lines in this transaction (drafts only, so nothing has been received against them).
    Changing `supplier_id` re-copies `supplier_name`/`category` and moves `open_pos` between the
    two suppliers."""
    if po.status != "draft":
        raise ConflictProblem(
            f"Purchase order {po.number} is {po.status}; only a draft can be edited"
        )

    changes = payload.model_dump(exclude_unset=True)
    lines_payload = changes.pop("lines", None)

    old_supplier_id = po.supplier_id
    new_supplier: Supplier | None = None
    if "supplier_id" in changes and changes["supplier_id"] != po.supplier_id:
        new_supplier = await selectors.get_supplier(
            session, ctx.organization_id, changes["supplier_id"]
        )
        if new_supplier is None:
            raise NotFoundProblem("That supplier is not part of this organization")
        if new_supplier.status != "active":
            raise ConflictProblem(
                f"Supplier {new_supplier.code} is {new_supplier.status} and cannot be assigned "
                "a purchase order"
            )

    if "warehouse" in changes and (
        await inv_selectors.get_warehouse_by_name(
            session, ctx.organization_id, changes["warehouse"]
        )
        is None
    ):
        raise ValidationProblem(f"{changes['warehouse']!r} does not match an existing warehouse")

    if "currency" in changes:
        changes["currency"] = changes["currency"].upper()

    before: dict[str, Any] = {field: getattr(po, field) for field in changes}
    for field, value in changes.items():
        setattr(po, field, value)
    if new_supplier is not None:
        # `changes["supplier_id"]` already set `po.supplier_id` above; the denormalised name and
        # category are not on `PurchaseOrderPatch`, so they are re-copied here explicitly.
        po.supplier_name = new_supplier.name
        po.category = new_supplier.category

    lines_before: list[dict[str, Any]] | None = None
    lines_after: list[dict[str, Any]] | None = None
    if lines_payload is not None:
        existing_lines = await selectors.lines_for(session, ctx.organization_id, po.id)
        lines_before = [
            {"sku": line.sku, "quantity": line.quantity, "unit_price_minor": line.unit_price_minor}
            for line in existing_lines
        ]
        for line in existing_lines:
            await session.delete(line)
        await session.flush()

        lines_after = []
        for spec in lines_payload:
            quantity = int(spec["quantity"])
            unit_price_minor = int(spec["unit_price_minor"])
            session.add(
                PurchaseOrderLine(
                    organization_id=ctx.organization_id,
                    created_by=str(ctx.principal.sub),
                    purchase_order_id=po.id,
                    sku=spec["sku"],
                    description=spec.get("description", ""),
                    quantity=quantity,
                    uom=spec.get("uom", ""),
                    unit_price_minor=unit_price_minor,
                    amount_minor=domain.line_amount_minor(quantity, unit_price_minor),
                    received_qty=0,
                )
            )
            lines_after.append(
                {"sku": spec["sku"], "quantity": quantity, "unit_price_minor": unit_price_minor}
            )

    po.updated_by = str(ctx.principal.sub)
    po.updated_at = datetime.now(UTC)
    await session.flush()

    if new_supplier is not None:
        await _bump_supplier(session, ctx.organization_id, old_supplier_id, open_pos=-1)
        await _bump_supplier(session, ctx.organization_id, new_supplier.id, open_pos=1)

    audit_before = dict(before)
    audit_after = dict(changes)
    if lines_before is not None:
        audit_before["lines"] = lines_before
        audit_after["lines"] = lines_after

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="purchase_order.updated",
        entity="purchase_order",
        entity_id=po.id,
        before=audit_before,
        after=audit_after,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_PO_UPDATED,
        aggregate_type="purchase_order",
        aggregate_id=po.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "purchase_order_id": str(po.id),
            "changed": sorted(set(changes) | ({"lines"} if lines_payload is not None else set())),
        },
        actor=_actor(ctx),
    )
    return po


async def delete_purchase_order(
    session: AsyncSession,
    ctx: ScopeContext,
    po: PurchaseOrder,
    *,
    request: Request | None = None,
) -> None:
    """Soft-delete a **draft** purchase order. An approved-or-later PO is a commitment; the path
    off it is cancel, not delete."""
    if po.status != "draft":
        raise ConflictProblem(
            f"Purchase order {po.number} is {po.status}; only a draft can be deleted — cancel it "
            "instead"
        )

    before_deleted_at = po.deleted_at
    po.deleted_at = datetime.now(UTC)
    po.updated_by = str(ctx.principal.sub)
    await session.flush()

    await _bump_supplier(session, ctx.organization_id, po.supplier_id, open_pos=-1)

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="purchase_order.deleted",
        entity="purchase_order",
        entity_id=po.id,
        before={"deleted_at": before_deleted_at},
        after={"deleted_at": po.deleted_at},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_PO_DELETED,
        aggregate_type="purchase_order",
        aggregate_id=po.id,
        payload={"organization_id": str(ctx.organization_id), "purchase_order_id": str(po.id)},
        actor=_actor(ctx),
    )


async def approve_purchase_order(
    session: AsyncSession,
    ctx: ScopeContext,
    po: PurchaseOrder,
    *,
    request: Request | None = None,
) -> PurchaseOrder:
    """`draft -> approved`. Bumps the supplier's lifetime `spend_minor` by the PO's derived
    total — only when the PO's currency matches the supplier's; a mismatch is skipped and noted
    in the audit row rather than silently mixing currencies in one running total."""
    if not domain.can_transition_po(po.status, "approved"):
        raise ConflictProblem(f"Purchase order {po.number} is {po.status} and cannot be approved")

    po.status = "approved"
    po.updated_by = str(ctx.principal.sub)
    po.updated_at = datetime.now(UTC)
    await session.flush()

    lines = await selectors.lines_for(session, ctx.organization_id, po.id)
    total = domain.po_total_minor(lines)
    supplier = await selectors.get_supplier(session, ctx.organization_id, po.supplier_id)
    spend_applied = supplier is not None and supplier.currency == po.currency
    if spend_applied:
        await _bump_supplier(session, ctx.organization_id, po.supplier_id, spend_minor=total)

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="purchase_order.approved",
        entity="purchase_order",
        entity_id=po.id,
        after={"status": "approved", "spend_applied": spend_applied},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_PO_APPROVED,
        aggregate_type="purchase_order",
        aggregate_id=po.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "purchase_order_id": str(po.id),
            "number": po.number,
            "spend_applied": spend_applied,
        },
        actor=_actor(ctx),
    )
    return po


async def receive_purchase_order(
    session: AsyncSession,
    ctx: ScopeContext,
    po: PurchaseOrder,
    *,
    received_date: date | None = None,
    request: Request | None = None,
    unmatched_out: list[str] | None = None,
) -> PurchaseOrder:
    """Mark a purchase order received: move it to `received`, stamp the delivery date, fill in
    each line's `received_qty` to the ordered quantity, and post the stock. Only an *approved*
    PO can be received — approval is a prerequisite gate, not a skippable commitment step, so a
    draft (not yet signed off by an ORG-scope approver), an already-received, a closed or a
    cancelled PO is refused.

    Each line whose `sku` matches a live inventory item in the org gets a `receipt` movement of
    `+quantity`, referencing this PO. A line with no matching SKU is a service/logistics line,
    not an error — it posts nothing, and its SKU is listed in `unmatched_skus` on the audit row
    and the event payload so that is visible rather than silent.

    The ledger row's cost is recorded in **the PO's currency**, not the item's: `unit_price_minor`
    is priced in `po.currency`, and a receipt in a currency other than the item's `currency` is
    common (an item's stock value can be set in the org's home currency while a supplier bills in
    their own) and is never converted here — that would be inventing an exchange rate nobody
    agreed to (ADR-0014 §8).
    """
    if not domain.can_transition_po(po.status, "received"):
        if po.status == "draft":
            raise ConflictProblem(
                f"Purchase order {po.number} is draft and must be approved before it can be "
                "received"
            )
        raise ConflictProblem(f"Purchase order {po.number} is {po.status} and cannot be received")

    landed = received_date or datetime.now(UTC).date()
    po.status = "received"
    po.received_date = landed
    po.updated_by = str(ctx.principal.sub)
    po.updated_at = datetime.now(UTC)

    lines = await selectors.lines_for(session, ctx.organization_id, po.id)
    unmatched_skus: list[str] = []
    matched_lines: list[dict[str, Any]] = []
    for line in lines:
        line.received_qty = line.quantity
        line.updated_by = str(ctx.principal.sub)
        item = await inv_selectors.get_inventory_by_sku(
            session, ctx.organization_id, line.sku, for_update=True
        )
        if item is None:
            unmatched_skus.append(line.sku)
            continue
        movement_id = await inv_services.receive_into_home(
            session,
            ctx,
            item,
            quantity=line.quantity,
            purchase_order_id=po.id,
            occurred_on=landed,
            unit_cost_minor=line.unit_price_minor,
            unit_cost_currency=po.currency,
            request=request,
        )
        matched_lines.append(
            {"sku": line.sku, "movement_id": str(movement_id), "quantity": line.quantity}
        )
    await session.flush()

    # Spend was already committed once, at approve time — the only way into `received` now that
    # `approved` is a prerequisite. Receiving only closes out the supplier's open-PO count.
    await _bump_supplier(session, ctx.organization_id, po.supplier_id, open_pos=-1)

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="purchase_order.received",
        entity="purchase_order",
        entity_id=po.id,
        after={
            "number": po.number,
            "status": "received",
            "received_date": landed.isoformat(),
            "unmatched_skus": unmatched_skus,
        },
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_PO_RECEIVED,
        aggregate_type="purchase_order",
        aggregate_id=po.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "purchase_order_id": str(po.id),
            "number": po.number,
            "received_date": landed.isoformat(),
            "unmatched_skus": unmatched_skus,
            "matched_lines": matched_lines,
        },
        actor=_actor(ctx),
    )
    # The route answers with these: a line that posted no stock is something the person who
    # just clicked Receive needs to see, not something to be found in the audit log later.
    if unmatched_out is not None:
        unmatched_out.extend(unmatched_skus)
    return po


async def close_purchase_order(
    session: AsyncSession,
    ctx: ScopeContext,
    po: PurchaseOrder,
    *,
    request: Request | None = None,
) -> PurchaseOrder:
    """`received -> closed`. The terminal, ordinary end of the lifecycle — nothing else changes."""
    if not domain.can_transition_po(po.status, "closed"):
        raise ConflictProblem(f"Purchase order {po.number} is {po.status} and cannot be closed")

    po.status = "closed"
    po.updated_by = str(ctx.principal.sub)
    po.updated_at = datetime.now(UTC)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="purchase_order.closed",
        entity="purchase_order",
        entity_id=po.id,
        after={"status": "closed"},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_PO_CLOSED,
        aggregate_type="purchase_order",
        aggregate_id=po.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "purchase_order_id": str(po.id),
            "number": po.number,
        },
        actor=_actor(ctx),
    )
    return po


async def cancel_purchase_order(
    session: AsyncSession,
    ctx: ScopeContext,
    po: PurchaseOrder,
    *,
    reason: str,
    request: Request | None = None,
) -> PurchaseOrder:
    """`draft|approved -> cancelled`. The terminal, unordinary end: an approved PO that will
    never arrive must not stay "open" forever, inflating `open_pos` and the late-delivery
    attention queue. Reverses the `approve` spend commitment if the PO had made it; a PO
    cancelled straight from `draft` never committed spend, so there is nothing to reverse.

    Cancelling an **approved** PO needs `purchase_order.approve` at ORG, the same floor as
    approving itself — it reverses spend an ORG-scope approver committed, and a buyer holding
    only `write:OWN` unilaterally undoing that would be the same separation-of-duty hole as
    letting them approve their own PO, worked from the other end. Cancelling a **draft** PO
    reverses nothing (no spend was ever committed) and stays at `write:OWN`, the same as
    deleting one.
    """
    if not domain.can_transition_po(po.status, "cancelled"):
        raise ConflictProblem(f"Purchase order {po.number} is {po.status} and cannot be cancelled")

    was_approved = po.status == "approved"
    if was_approved:
        approve_scope = ctx.principal.scope_for("operations.procurement.purchase_order.approve")
        if approve_scope is None or approve_scope.rank < Scope.ORG.rank:
            raise ForbiddenProblem(
                "Cancelling an approved purchase order needs the approve permission"
            )

    po.status = "cancelled"
    po.updated_by = str(ctx.principal.sub)
    po.updated_at = datetime.now(UTC)
    await session.flush()

    total = domain.po_total_minor(await selectors.lines_for(session, ctx.organization_id, po.id))
    supplier = await selectors.get_supplier(session, ctx.organization_id, po.supplier_id)
    spend_reversed = was_approved and supplier is not None and supplier.currency == po.currency
    spend_delta = -total if spend_reversed else 0
    await _bump_supplier(
        session, ctx.organization_id, po.supplier_id, open_pos=-1, spend_minor=spend_delta
    )

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="purchase_order.cancelled",
        entity="purchase_order",
        entity_id=po.id,
        after={"status": "cancelled", "reason": reason, "spend_reversed": spend_reversed},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_PO_CANCELLED,
        aggregate_type="purchase_order",
        aggregate_id=po.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "purchase_order_id": str(po.id),
            "number": po.number,
            "reason": reason,
        },
        actor=_actor(ctx),
    )
    return po
