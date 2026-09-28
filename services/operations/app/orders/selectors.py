"""Reads for the order engine, plus the ORM -> wire projections (`app.selectors`'s own
convention: shaping a row for the wire is part of reading it).

Orders are an org-wide operational queue — whoever is at the counter or on the phone needs to see
every order to fulfil it, not only the one they personally took — so, like suppliers, inventory
and warehouses, order reads are **not scope-narrowed**: every grant is ORG (`app/catalogue.py`),
and these selectors filter by organization only, the same reasoning `app/selectors.py`'s own
module docstring gives for reference data.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import order_line_amount_minor
from app.inventory.quantity import QuantityError, format_qty, parse_qty
from app.models import (
    FulfilmentSettings,
    InventoryBalance,
    InventoryItem,
    InventoryReservation,
    Location,
    Order,
    OrderLine,
    PickTask,
)
from app.schemas import OrderLineItem, OrderOut, QuoteIn, QuoteLineOut, QuoteOut
from platform_core.errors import ConflictProblem, NotFoundProblem, ValidationProblem

# ------------------------------------------------------------------ orders


def orders_stmt(
    org_id: uuid.UUID,
    *,
    status: str | None = None,
    channel: str | None = None,
    warehouse_id: uuid.UUID | None = None,
) -> Select[Any]:
    stmt = select(Order).where(Order.organization_id == org_id, Order.deleted_at.is_(None))
    if status:
        stmt = stmt.where(Order.status == status)
    if channel:
        stmt = stmt.where(Order.channel == channel)
    if warehouse_id:
        stmt = stmt.where(Order.warehouse_id == warehouse_id)
    return stmt.order_by(Order.number.desc())


async def get_order(
    session: AsyncSession, org_id: uuid.UUID, order_id: uuid.UUID, *, for_update: bool = False
) -> Order | None:
    """`populate_existing` matters exactly the way it does for `app.inventory.ledger.lock_bucket`
    (that module's own docstring names the hazard): a session that already holds this `Order` in
    its identity map — `accept_order`'s claim-step read, before its record-step lock — would
    otherwise get that same Python object back from the lock query still carrying the
    `version`/`status` it had before the lock was ever taken, not the row the lock just waited
    for."""
    stmt = select(Order).where(
        Order.id == order_id, Order.organization_id == org_id, Order.deleted_at.is_(None)
    )
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_order_by_number(
    session: AsyncSession, org_id: uuid.UUID, number: str
) -> Order | None:
    stmt = select(Order).where(
        Order.number == number, Order.organization_id == org_id, Order.deleted_at.is_(None)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def lines_for(
    session: AsyncSession, org_id: uuid.UUID, order_id: uuid.UUID
) -> list[OrderLine]:
    stmt = (
        select(OrderLine)
        .where(OrderLine.organization_id == org_id, OrderLine.order_id == order_id)
        .order_by(OrderLine.created_at.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def lines_map(
    session: AsyncSession, org_id: uuid.UUID, order_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[OrderLine]]:
    """Every line for a set of orders, grouped by order id — one query for a whole list page
    (N+1 avoidance, the same shape `app.selectors.lines_map` uses for purchase orders)."""
    if not order_ids:
        return {}
    stmt = (
        select(OrderLine)
        .where(OrderLine.organization_id == org_id, OrderLine.order_id.in_(order_ids))
        .order_by(OrderLine.created_at.asc())
    )
    out: dict[uuid.UUID, list[OrderLine]] = {}
    for line in (await session.execute(stmt)).scalars().all():
        out.setdefault(line.order_id, []).append(line)
    return out


async def items_for_lines(
    session: AsyncSession, org_id: uuid.UUID, item_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, InventoryItem]:
    """The inventory items a set of order lines name, in one query — for SKU projection and for
    `place`'s pricing pass, so neither loops a query per line (N+1 avoidance)."""
    if not item_ids:
        return {}
    stmt = select(InventoryItem).where(
        InventoryItem.organization_id == org_id,
        InventoryItem.id.in_(set(item_ids)),
        InventoryItem.deleted_at.is_(None),
    )
    return {item.id: item for item in (await session.execute(stmt)).scalars().all()}


async def locations_for_items(
    session: AsyncSession, org_id: uuid.UUID, items: Sequence[InventoryItem]
) -> dict[uuid.UUID, Location]:
    """Each item's home location, in one query — what `accept` needs to know which bucket to
    reserve against, without a query per line (N+1 avoidance). Pre-Phase-1: an order line always
    reserves at its item's single home bin, the same place every other single-location write
    posts (`app.inventory.services`); per-line location choice is a later phase's addition."""
    ids = {item.home_location_id for item in items if item.home_location_id is not None}
    if not ids:
        return {}
    stmt = select(Location).where(Location.organization_id == org_id, Location.id.in_(ids))
    return {loc.id: loc for loc in (await session.execute(stmt)).scalars().all()}


async def open_reservations_for_order(
    session: AsyncSession, org_id: uuid.UUID, order_id: uuid.UUID
) -> list[InventoryReservation]:
    """The order's still-open reservations — what `cancel` releases, in the kernel's own bucket
    order (`app.inventory.ledger._bucket_key`: item, location, status, lot, roll — N6, security
    review). F3 shares staging buckets across orders (every pick's destination is one warehouse's
    staging location), so two orders cancelling at once can now contend the same balance rows;
    releasing them in whatever order Postgres happens to return is the same opposite-order
    deadlock surface the kernel already sorts away for `post_move`/`reserve_many`. Reads
    `inventory_reservations` directly (same schema, no chokepoint bypass): the kernel is still
    the only *writer* of it (ADR-0014 §3); a plain scoped read is not a kernel operation."""
    stmt = (
        select(InventoryReservation)
        .join(InventoryBalance, InventoryBalance.id == InventoryReservation.balance_id)
        .where(
            InventoryReservation.organization_id == org_id,
            InventoryReservation.reference_type == "order",
            InventoryReservation.reference_id == order_id,
            InventoryReservation.status == "active",
        )
        .order_by(
            InventoryBalance.inventory_item_id,
            InventoryBalance.location_id,
            InventoryBalance.status,
            InventoryBalance.lot_id,
            InventoryBalance.roll_id,
        )
    )
    return list((await session.execute(stmt)).scalars().all())


async def pick_task_statuses_for_order(
    session: AsyncSession, org_id: uuid.UUID, order_id: uuid.UUID
) -> list[str]:
    """Every live pick task's status for one order — what `release_hold_order` needs to
    recompute the real release target (ADR-0025 §1; MAJOR 1 #2, security review) without
    importing `app.fulfilment` (ADR-0024 §1 forbids that direction; `app.models.PickTask` itself
    is a plain model import two layers below `app/orders/services.py`, not a layering violation —
    confirmed by `lint-imports` after this change)."""
    stmt = select(PickTask.status).where(
        PickTask.organization_id == org_id,
        PickTask.order_id == order_id,
        PickTask.deleted_at.is_(None),
    )
    return list((await session.execute(stmt)).scalars().all())


# ------------------------------------------------------------------ projections (ORM -> wire)


def order_line_out(
    line: OrderLine, *, sku: str, catalogue_price_minor: int | None
) -> OrderLineItem:
    return OrderLineItem(
        id=str(line.id),
        inventory_item_id=str(line.inventory_item_id),
        sku=sku,
        quantity=format_qty(line.quantity_milli),
        selling_price_minor=line.selling_price_minor,
        line_total_minor=line.line_total_minor,
        price_overridden=line.price_overridden,
        catalogue_price_minor=catalogue_price_minor,
    )


def order_out(
    order: Order,
    *,
    lines: Sequence[OrderLine] = (),
    items: dict[uuid.UUID, InventoryItem] | None = None,
) -> OrderOut:
    items = items or {}
    return OrderOut(
        id=str(order.id),
        number=order.number,
        channel=order.channel,
        status=order.status,
        version=order.version,
        warehouse_id=str(order.warehouse_id),
        customer_id=str(order.customer_id) if order.customer_id else None,
        customer_name=order.customer_name,
        customer_phone=order.customer_phone,
        subtotal_minor=order.subtotal_minor,
        total_minor=order.total_minor,
        currency=order.currency,
        lines=[
            order_line_out(
                line,
                sku=items[line.inventory_item_id].sku if line.inventory_item_id in items else "",
                catalogue_price_minor=(
                    items[line.inventory_item_id].selling_price_minor
                    if line.inventory_item_id in items
                    else None
                ),
            )
            for line in lines
        ],
        created_at=order.created_at,
        updated_at=order.updated_at,
    )


# ------------------------------------------------------------------ fulfilment settings


@dataclass(frozen=True, slots=True)
class ResolvedSettings:
    """What `resolve_settings` hands back: the effective policy for one order, and enough of a
    trail (`source`) to snapshot honestly onto `Order.settings_snapshot` at accept."""

    invoice_timing: str
    payment_defaults: dict[str, str]
    offer_timeout_seconds: int
    offer_rounds: int
    #: "warehouse" | "organization" | "default" — which row (or lack of one) this came from.
    source: str
    warehouse_id: uuid.UUID | None = None

    def payment_term_for(self, channel: str) -> str:
        """The channel's payment term, defaulting to `"cash"` — never `"credit"` — for a channel
        the settings row does not name (`FulfilmentSettings.payment_defaults`' own docstring)."""
        return self.payment_defaults.get(channel, "cash")

    def as_snapshot(self) -> dict[str, Any]:
        return {
            "invoice_timing": self.invoice_timing,
            "payment_defaults": dict(self.payment_defaults),
            "offer_timeout_seconds": self.offer_timeout_seconds,
            "offer_rounds": self.offer_rounds,
            "source": self.source,
            "warehouse_id": str(self.warehouse_id) if self.warehouse_id else None,
        }


#: What an organization gets when neither a per-warehouse nor an org-default `fulfilment_settings`
#: row exists yet — no endpoint in F2 creates one (out of this phase's scope), so a fresh
#: organization must still be able to place and accept orders.
_HARDCODED_DEFAULT = ResolvedSettings(
    invoice_timing="on_accept",
    payment_defaults={},
    offer_timeout_seconds=300,
    offer_rounds=3,
    source="default",
)


async def resolve_settings(
    session: AsyncSession, org_id: uuid.UUID, warehouse_id: uuid.UUID
) -> ResolvedSettings:
    """The effective `fulfilment_settings` for an order at this warehouse: prefer a live row
    scoped to the warehouse, else the org's default row (`warehouse_id IS NULL`), else a
    hardcoded default (ADR-0024 §4). A warehouse row overrides **wholesale** — every column, not
    merged field by field with the org row — which is this module's documented shape, not the
    ADR's (the ADR leaves the merge granularity to the plan)."""
    stmt = select(FulfilmentSettings).where(
        FulfilmentSettings.organization_id == org_id,
        FulfilmentSettings.warehouse_id == warehouse_id,
        FulfilmentSettings.deleted_at.is_(None),
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is not None:
        return ResolvedSettings(
            invoice_timing=row.invoice_timing,
            payment_defaults=dict(row.payment_defaults),
            offer_timeout_seconds=row.offer_timeout_seconds,
            offer_rounds=row.offer_rounds,
            source="warehouse",
            warehouse_id=warehouse_id,
        )
    stmt = select(FulfilmentSettings).where(
        FulfilmentSettings.organization_id == org_id,
        FulfilmentSettings.warehouse_id.is_(None),
        FulfilmentSettings.deleted_at.is_(None),
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is not None:
        return ResolvedSettings(
            invoice_timing=row.invoice_timing,
            payment_defaults=dict(row.payment_defaults),
            offer_timeout_seconds=row.offer_timeout_seconds,
            offer_rounds=row.offer_rounds,
            source="organization",
        )
    return _HARDCODED_DEFAULT


# ------------------------------------------------------------------ quote preview


@dataclass(slots=True)
class PricedLine:
    item: InventoryItem
    quantity_milli: int
    selling_price_minor: int | None
    priced: bool
    line_total_minor: int | None


def price_lines(
    items: dict[uuid.UUID, InventoryItem],
    lines: Sequence[tuple[uuid.UUID, int, int | None]],
    *,
    currency: str,
) -> list[PricedLine]:
    """The pricing pass the quote preview runs (`place` prices its own lines inline, since it also
    has to write them): for each `(item_id, quantity_milli, explicit_price_minor)`, resolve the
    price (explicit, else the item's own) and the line total (`app.domain.order_line_amount_minor`),
    or mark the line unpriced when neither exists.

    A line taking the item's own price is refused (409, naming the SKU) when the item's
    `selling_price_currency` disagrees with `currency` — the quote's own currency-consistency half
    of MAJOR A, mirroring `place_order`'s check line for line. An explicit `explicit_price_minor`
    carries no currency of its own (there is no per-line currency field), so it is never checked
    against the item's."""
    out: list[PricedLine] = []
    for item_id, quantity_milli, explicit_price in lines:
        item = items[item_id]
        price: int | None
        if explicit_price is not None:
            price = explicit_price
        else:
            price = item.selling_price_minor
            if price is not None and item.selling_price_currency != currency:
                raise ConflictProblem(
                    f"{item.sku} is priced in {item.selling_price_currency}, not this quote's "
                    f"{currency}; give the line its own price or match the quote's currency"
                )
        if price is None:
            out.append(
                PricedLine(
                    item=item,
                    quantity_milli=quantity_milli,
                    selling_price_minor=None,
                    priced=False,
                    line_total_minor=None,
                )
            )
            continue
        out.append(
            PricedLine(
                item=item,
                quantity_milli=quantity_milli,
                selling_price_minor=price,
                priced=True,
                line_total_minor=order_line_amount_minor(quantity_milli, price),
            )
        )
    return out


def build_quote(
    *,
    warehouse_id: uuid.UUID,
    currency: str,
    priced: Sequence[PricedLine],
) -> QuoteOut:
    lines: list[QuoteLineOut] = []
    subtotal = 0
    fully_priced = True
    fully_available = True
    for p in priced:
        available = p.item.available_milli
        sufficient = available >= p.quantity_milli
        fully_available = fully_available and sufficient
        fully_priced = fully_priced and p.priced
        if p.line_total_minor is not None:
            subtotal += p.line_total_minor
        lines.append(
            QuoteLineOut(
                inventory_item_id=str(p.item.id),
                sku=p.item.sku,
                quantity=format_qty(p.quantity_milli),
                priced=p.priced,
                selling_price_minor=p.selling_price_minor,
                line_total_minor=p.line_total_minor,
                available_qty=format_qty(available),
                sufficient=sufficient,
            )
        )
    return QuoteOut(
        warehouse_id=str(warehouse_id),
        currency=currency,
        subtotal_minor=subtotal,
        total_minor=subtotal,
        fully_priced=fully_priced,
        fully_available=fully_available,
        lines=lines,
    )


async def quote_preview(session: AsyncSession, org_id: uuid.UUID, payload: QuoteIn) -> QuoteOut:
    """`POST /orders/quote`'s whole read-only computation: resolve every line's item, price it the
    same way `place` would, and check it against `InventoryItem.available_milli` at the requested
    warehouse. Posts nothing — no order, no lines, no reservation."""
    item_ids = [line.inventory_item_id for line in payload.lines]
    items = await items_for_lines(session, org_id, item_ids)
    missing = set(item_ids) - items.keys()
    if missing:
        raise NotFoundProblem(
            f"Inventory item {sorted(map(str, missing))[0]} is not in this organization"
        )

    parsed: list[tuple[uuid.UUID, int, int | None]] = []
    for line in payload.lines:
        item = items[line.inventory_item_id]
        try:
            quantity_milli = parse_qty(line.quantity, item.uom)
        except QuantityError as exc:
            raise ValidationProblem(str(exc)) from exc
        if quantity_milli <= 0:
            raise ValidationProblem("A quote line's quantity must be positive")
        parsed.append((item.id, quantity_milli, line.selling_price_minor))

    priced = price_lines(items, parsed, currency=payload.currency)
    return build_quote(warehouse_id=payload.warehouse_id, currency=payload.currency, priced=priced)
