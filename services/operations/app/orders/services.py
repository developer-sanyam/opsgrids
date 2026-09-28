"""Writes for the order engine (ADR-0024 §1, ADR-0025).

Every transition follows ADR-0025 §1's rule: **one transaction** takes `SELECT ... FOR UPDATE` on
the order row, checks the caller's `version` (a stale value is a 409), then writes the status
change, the `order_transitions` row, the audit row and the outbox row together. `place`, `hold`,
`release_hold` and `cancel` do this inside the request's own transaction
(`get_tenant_session` — see `app/api/routes.py`), the same shape every other write in this service
uses.

`accept_order` is the one exception, and deliberately so: a credit-terms accept has to read the
customer's credit position from `services/finance` **before** the transaction opens
(`docs/io-outside-transactions.md` — no network call under a lock), so it takes the *raw*,
unwrapped session (`platform_core.db.get_session`) and opens its own transactions by hand, the
same shape `services/lead/app/services.py:convert_lead` already uses for the identical reason.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain
from app.config import settings
from app.inventory import ledger
from app.inventory import selectors as inv_selectors
from app.inventory.ledger import MoveLine, ReserveLine
from app.inventory.quantity import MAX_ABS_MILLI, QuantityError, format_qty, parse_qty
from app.models import (
    AuditLog,
    InventoryReservation,
    Order,
    OrderLine,
    OrderTransition,
    OutboxEvent,
)
from app.orders import selectors
from platform_core.audit import record_audit
from platform_core.auth import Principal, Scope, ScopeContext
from platform_core.db import assert_tenant, set_tenant
from platform_core.errors import ConflictProblem, NotFoundProblem, ValidationProblem
from platform_core.events import publish_event
from platform_core.logging import get_logger

log = get_logger("operations.orders")

EVT_ORDER_PLACED = "operations.orders.order.placed"
EVT_ORDER_ACCEPTED = "operations.orders.order.accepted"
EVT_ORDER_HELD = "operations.orders.order.held"
EVT_ORDER_CANCELLED = "operations.orders.order.cancelled"
#: `IN_FULFILMENT -> READY` (ADR-0025 §1) — published from `app/fulfilment/services.py`, not from
#: here: the transition fires when a pick task reports every line settled, a fact `app/orders/`
#: never imports `app/fulfilment/` to learn (ADR-0024 §1). Declared here, alongside this module's
#: other order event names, so the plan's event catalogue (docs/fulfilment/plan.md §5) has one
#: home for every `operations.orders.order.*` constant.
EVT_ORDER_READY = "operations.orders.order.ready"
#: `READY -> HANDED_OVER -> DELIVERED`, the counter/walk-in path (ADR-0025 §1) — see
#: `hand_over_order`. There is no separate `delivered` event: the plan's event catalogue
#: (docs/fulfilment/plan.md §5) names `handed_over` only, the same precedent `EVT_ORDER_HELD`
#: sets for a transition with no dedicated wire event of its own.
EVT_ORDER_HANDED_OVER = "operations.orders.order.handed_over"
#: This service's own request for an invoice (ADR-0024 §1/§4; Fulfilment plan F4) — the exact
#: contract `libs/contracts/events/operations.orders.order.invoice_requested/v1.schema.json`
#: fixes, consumed by `services/finance/app/consumers.py::invoice_requested_from_order` (already
#: deployed, ahead of this producer — see that schema's own `$comment`).
EVT_ORDER_INVOICE_REQUESTED = "operations.orders.order.invoice_requested"
#: `services/finance`'s own event name
#: (`services/finance/app/services.py::EVT_ORDER_INVOICE_ISSUED`) — never imported (no
#: cross-service import exists in this platform any more than a cross-schema
#: foreign key does, ADR-0000 §10), so this is the string contract copied by hand, the same way
#: `EVT_OFFER_EXPIRE_REQUESTED` names a target event `services/scheduler` fires without importing
#: it either. `app.consumers.finance_invoice_issued` binds to this exact routing key.
EVT_FINANCE_INVOICE_ISSUED = "finance.orders.invoice.issued"

#: `Order.channel` values whose payment term (`ResolvedSettings.payment_term_for`) needs a
#: recorded payment before handover (ADR-0025 §1's billing gate) — every term except `"credit"`.
_PAYMENT_REQUIRED_TERMS = frozenset({"prepaid", "cash"})
#: `fulfilment_settings.invoice_timing` values under which an invoice must exist **by handover**
#: (ADR-0025 §1's billing gate). `on_delivery` is deliberately excluded here: that policy's own
#: check belongs to the `DELIVERED -> COMPLETED` close-out gate the ADR describes separately
#: ("every invoice the org's timing policy requires exists") — not built in F4 (out of this
#: slice's scope; see this slice's own report), and never this gate's job even for a counter
#: order where handover and delivery happen to land in the same transaction.
_INVOICE_REQUIRED_BY_HANDOVER_TIMINGS = frozenset({"on_accept", "on_dispatch"})

#: A priced line above this is refused before `place` ever flushes it — comfortably inside
#: `BigInteger`'s ~9.22e18 ceiling even summed over `OrderIn.lines`'s max 200 lines, and the same
#: order-of-magnitude ceiling `app.inventory.quantity.MAX_ABS_MILLI` already sets for a quantity
#: (review MINOR D). `OrderLineIn.selling_price_minor`'s own `le=MAX_ABS_MILLI` bound does not by
#: itself stop `quantity_milli * selling_price_minor` from overflowing before the `/ 1000`, nor
#: does it cover a catalogue price (`InventoryItem.selling_price_minor` carries no upper bound) —
#: this is the guard on the actual product, checked once it is computed.
_MAX_LINE_TOTAL_MINOR = MAX_ABS_MILLI


def _actor(ctx: ScopeContext) -> dict[str, Any]:
    return {
        "type": "user",
        "id": str(ctx.principal.sub),
        "membership_id": str(ctx.principal.membership_id) if ctx.principal.membership_id else None,
    }


async def _bind_tenant(session: AsyncSession, organization_id: uuid.UUID) -> None:
    """Bind a transaction to the caller's organization by hand — the same tenancy chokepoint
    `get_tenant_session` applies for every other route's one transaction, needed here because
    `accept_order`'s claim/call/record shape opens more than one transaction on one session."""
    await set_tenant(session, organization_id)
    if not settings().is_production:
        await assert_tenant(session, organization_id)


def _record_transition(order: Order, *, to_status: str) -> None:
    """The status-and-version half of every transition — the `order_transitions` row, the audit
    row and the outbox event are each caller's own, since their payloads differ."""
    order.status = to_status
    order.version += 1


# ------------------------------------------------------------------ create


async def next_order_number(session: AsyncSession, ctx: ScopeContext) -> str:
    """Race-safe server-assigned `ORD-{yyyy}-{seq:04d}`, serialised per organization per year —
    the same `pg_advisory_xact_lock` shape `services.next_po_number` uses."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"{ctx.organization_id}:order_number"},
    )
    year = date.today().year
    prefix = f"ORD-{year}-"
    stmt = select(func.max(Order.number)).where(
        Order.organization_id == ctx.organization_id, Order.number.like(f"{prefix}%")
    )
    current_max = (await session.execute(stmt)).scalar_one_or_none()
    seq = 1
    if current_max:
        try:
            seq = int(current_max.rsplit("-", 1)[-1]) + 1
        except ValueError:
            seq = 1
    return f"{prefix}{seq:04d}"


async def create_order(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    channel: str,
    warehouse_id: uuid.UUID,
    customer_id: uuid.UUID | None,
    customer_name: str,
    customer_phone: str,
    currency: str,
    lines: Sequence[Mapping[str, Any]],
    request: Request | None = None,
) -> Order:
    """Create a `DRAFT` order and its lines. A line mapping is `{inventory_item_id, quantity,
    selling_price_minor}` — `quantity` a wire `Qty` string, `selling_price_minor` an optional
    explicit override. Nothing is priced or reserved yet; that is `place` and `accept`'s job.
    """
    if channel not in domain.ORDER_CHANNELS:
        raise ValidationProblem(f"{channel!r} is not a recognised order channel")
    warehouse = await inv_selectors.get_warehouse(session, ctx.organization_id, warehouse_id)
    if warehouse is None:
        raise NotFoundProblem("That warehouse is not part of this organization")

    item_ids = [spec["inventory_item_id"] for spec in lines]
    items = await selectors.items_for_lines(session, ctx.organization_id, item_ids)
    missing = set(item_ids) - items.keys()
    if missing:
        raise NotFoundProblem(f"Inventory item {sorted(map(str, missing))[0]} is not in this org")

    number = await next_order_number(session, ctx)
    order = Order(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        number=number,
        channel=channel,
        status="DRAFT",
        version=0,
        warehouse_id=warehouse.id,
        customer_id=customer_id,
        customer_name=customer_name,
        customer_phone=customer_phone,
        currency=currency.upper(),
    )
    session.add(order)
    await session.flush()

    #: Every line whose caller named its own price at create time (review MAJOR B) — the create
    #: audit row names which lines, not just how many there were, and both the price given and
    #: the item's own catalogue price at that moment, so an override is visible and auditable
    #: even though F2 does not restrict who may set one.
    overrides: list[dict[str, Any]] = []
    for spec in lines:
        item = items[spec["inventory_item_id"]]
        try:
            quantity_milli = parse_qty(spec["quantity"], item.uom)
        except QuantityError as exc:
            raise ValidationProblem(str(exc)) from exc
        if quantity_milli <= 0:
            raise ValidationProblem("An order line's quantity must be positive")
        explicit_price = spec.get("selling_price_minor")
        session.add(
            OrderLine(
                organization_id=ctx.organization_id,
                created_by=str(ctx.principal.sub),
                order_id=order.id,
                inventory_item_id=item.id,
                quantity_milli=quantity_milli,
                price_overridden=explicit_price is not None,
                selling_price_minor=explicit_price,
                line_total_minor=None,
            )
        )
        if explicit_price is not None:
            overrides.append(
                {
                    "inventory_item_id": str(item.id),
                    "sku": item.sku,
                    "override_price_minor": explicit_price,
                    "catalogue_price_minor": item.selling_price_minor,
                }
            )
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="order.created",
        entity="order",
        entity_id=order.id,
        after={
            "number": number,
            "channel": channel,
            "status": "DRAFT",
            "line_count": len(lines),
            "price_overrides": overrides,
        },
        request=request,
    )
    # No outbox event: the plan's event catalogue (docs/fulfilment/plan.md §5) starts at
    # `placed` — a draft is a private working copy nothing outside operations needs to know
    # about yet, the same reasoning that keeps `purchase_order.raised` the first PO event too
    # (draft POs, by contrast, *do* get one; orders differ because a draft order may still be
    # missing prices and is not yet a real commercial fact worth telling anyone about).
    return order


# ------------------------------------------------------------------ place


async def place_order(
    session: AsyncSession,
    ctx: ScopeContext,
    order: Order,
    *,
    expected_version: int,
    request: Request | None = None,
) -> Order:
    """`DRAFT -> PLACED` (ADR-0025 §1). Prices every line — from the item's own
    `selling_price_minor` unless the line already named its own — and refuses the whole order if
    any line ends up with neither (ADR-0024 §3). **Reserves nothing**: ADR-0025 §1 is explicit
    that reserving at `PLACED` would promise stock for an order the business has not agreed to
    fill. `order` must already be locked (`FOR UPDATE`) by the caller.

    A line taking the item's own price is refused (409, naming the SKU) when the item's
    `selling_price_currency` disagrees with the order's own `currency` (review MAJOR A) — an
    explicit line price carries no currency of its own, so an overridden line is never checked
    against the item's. A line total that would overflow `BigInteger` is refused the same way
    (review MINOR D) before it is ever written.
    """
    if order.version != expected_version:
        raise ConflictProblem(
            f"Order {order.number} is at version {order.version}, not {expected_version}; "
            "reload it and try again"
        )
    if not domain.can_transition_order(order.status, "PLACED"):
        raise ConflictProblem(f"Order {order.number} is {order.status} and cannot be placed")

    lines = await selectors.lines_for(session, ctx.organization_id, order.id)
    if not lines:
        raise ConflictProblem(f"Order {order.number} has no lines to place")
    items = await selectors.items_for_lines(
        session, ctx.organization_id, [line.inventory_item_id for line in lines]
    )
    missing = {line.inventory_item_id for line in lines} - items.keys()
    if missing:
        raise ConflictProblem("An order line names an item no longer in this organization")

    unpriced: list[str] = []
    subtotal = 0
    #: Same shape as `create_order`'s own `overrides` — carried through to this call's audit row
    #: too, since an override made at create time is only priced (and so only fully known: the
    #: catalogue price it was compared against might have moved) once `place` runs.
    overrides: list[dict[str, Any]] = []
    for line in lines:
        item = items[line.inventory_item_id]
        if line.price_overridden:
            price = line.selling_price_minor
        else:
            price = item.selling_price_minor
            if price is not None and item.selling_price_currency != order.currency:
                raise ConflictProblem(
                    f"{item.sku} is priced in {item.selling_price_currency}, not order "
                    f"{order.number}'s {order.currency}; give the line its own price or align "
                    "the item's currency before placing"
                )
        if price is None:
            unpriced.append(item.sku)
            continue
        if not line.price_overridden:
            line.selling_price_minor = price
        else:
            overrides.append(
                {
                    "inventory_item_id": str(item.id),
                    "sku": item.sku,
                    "override_price_minor": price,
                    "catalogue_price_minor": item.selling_price_minor,
                }
            )
        line_total = domain.order_line_amount_minor(line.quantity_milli, price)
        if line_total > _MAX_LINE_TOTAL_MINOR:
            raise ConflictProblem(
                f"{item.sku}'s line total is too large to process; reduce the quantity or price"
            )
        line.line_total_minor = line_total
        subtotal += line_total
    if unpriced:
        raise ConflictProblem(
            f"{', '.join(sorted(unpriced))} — no selling price, and this order named none either; "
            "set a price on the item or the line before placing"
        )

    before_status = order.status
    _record_transition(order, to_status="PLACED")
    order.subtotal_minor = subtotal
    order.total_minor = subtotal
    order.updated_by = str(ctx.principal.sub)
    order.updated_at = datetime.now(UTC)
    await session.flush()

    session.add(
        OrderTransition(
            organization_id=ctx.organization_id,
            order_id=order.id,
            from_status=before_status,
            to_status="PLACED",
            actor=str(ctx.principal.sub),
            reason="",
        )
    )
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="order.placed",
        entity="order",
        entity_id=order.id,
        after={
            "status": "PLACED",
            "total_minor": subtotal,
            "currency": order.currency,
            "price_overrides": overrides,
        },
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_ORDER_PLACED,
        aggregate_type="order",
        aggregate_id=order.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "order_id": str(order.id),
            "number": order.number,
            "total_minor": subtotal,
            "currency": order.currency,
        },
        actor=_actor(ctx),
    )
    return order


# ------------------------------------------------------------------ accept (ADR-0025 §1 guard)


async def load_order_for_response(
    session: AsyncSession, ctx: ScopeContext, order_id: uuid.UUID
) -> tuple[Order, list[OrderLine], dict[uuid.UUID, Any]]:
    """A bound, short read of one order, its lines and their items — what a route built on
    `RawSessionDep` (today, only `accept`'s) needs after its own transactions have each already
    committed and closed: the tenant GUC is transaction-local (`platform_core.db`), so a plain
    read after that needs its own `_bind_tenant` and its own transaction, the same as every step
    before it. Every read a `RawSessionDep` route makes must be inside one of these blocks — a
    bare `session.execute` between them autobegins a transaction nothing here ever closes."""
    async with session.begin():
        await _bind_tenant(session, ctx.organization_id)
        order = await selectors.get_order(session, ctx.organization_id, order_id)
        if order is None:
            raise NotFoundProblem("That order is not part of this organization")
        lines = await selectors.lines_for(session, ctx.organization_id, order.id)
        items = await selectors.items_for_lines(
            session, ctx.organization_id, [line.inventory_item_id for line in lines]
        )
        return order, lines, items


async def check_credit_before_accept(
    ctx: ScopeContext,
    *,
    customer_id: uuid.UUID | None,
    order_total_minor: int,
    currency: str,
) -> None:
    """The `PLACED -> ACCEPTED` guard's credit-terms check (ADR-0025 §1, step 1) — called with
    **no session and no transaction open**, before `accept_order` ever takes the order row's lock
    (`docs/io-outside-transactions.md`: a network call never runs under a lock).

    TODO(F4 billing): `services/finance` exposes no credit-position endpoint yet — there is
    nothing here to call. This is a documented no-op, not a fabricated finance call: every
    credit-terms accept passes unconditionally until finance ships one. When it does, this
    becomes the "call" step of claim/call/record — fetch `customer_id`'s outstanding exposure and
    limit, and raise `ConflictProblem` naming the limit, the outstanding balance and
    `order_total_minor` when `outstanding + order_total_minor` exceeds the limit (ADR-0025 §1:
    "refused with the numbers ... not with a bare 'no'"). `customer_id` may be `None` for a
    walk-in/manual order with no CRM record; such an order has nothing to check credit against,
    so this stays a pass rather than a refusal either way.
    """
    return None


async def _reserve_lines_for(
    session: AsyncSession, org_id: uuid.UUID, order: Order, lines: Sequence[OrderLine]
) -> list[ReserveLine]:
    """Each line's `ReserveLine`, resolved against its item's home bin (pre-Phase-1: a single
    location per item, the same place every other write in this service posts) and checked
    against the order's own warehouse — in two batched queries, not one per line."""
    items = await selectors.items_for_lines(
        session, org_id, [line.inventory_item_id for line in lines]
    )
    missing = {line.inventory_item_id for line in lines} - items.keys()
    if missing:
        raise ConflictProblem("An order line names an item no longer in this organization")
    locations = await selectors.locations_for_items(session, org_id, list(items.values()))

    reserve_lines: list[ReserveLine] = []
    for line in lines:
        item = items[line.inventory_item_id]
        location = locations.get(item.home_location_id) if item.home_location_id else None
        if location is None or location.warehouse_id != order.warehouse_id:
            raise ConflictProblem(
                f"{item.sku} is not stocked at {order.number}'s warehouse; it cannot be accepted"
            )
        reserve_lines.append(
            ReserveLine(
                item_id=item.id,
                location_id=location.id,
                quantity_milli=line.quantity_milli,
                # `OrderLine.id`, not this loop's position — `reserve_many`'s derived idempotency
                # key uses it precisely so a retry is safe even though `lines_for`'s `created_at`
                # sort has no guaranteed-stable tiebreaker (review MINOR G).
                line_id=line.id,
            )
        )
    return reserve_lines


async def accept_order(
    session: AsyncSession,
    ctx: ScopeContext,
    order_id: uuid.UUID,
    *,
    expected_version: int,
    request: Request | None = None,
) -> Order:
    """`PLACED -> ACCEPTED` (ADR-0025 §1's guard), in the three steps
    `docs/io-outside-transactions.md` names:

    1. **Claim** — a short read-only transaction resolves the order and its effective
       `fulfilment_settings`, to know whether this is a credit-terms accept. Nothing is written;
       the transaction commits (a no-op commit) before step 2 runs.
    2. **Call** — `check_credit_before_accept`, with no session and no transaction open.
    3. **Record** — one transaction: `SELECT ... FOR UPDATE` on the order row, the `status`/
       `version` check, every line reserved in one kernel call
       (`app.inventory.ledger.reserve_many`, idempotency key `order:{id}:accept:v{version}`),
       the settings snapshot, the version bump, the `order_transitions` row, the audit row and
       the outbox event.

    `InsufficientStock` (ADR-0014 §11's shape) propagates from step 3 unchanged, naming every
    short line — no order is ever half-accepted.

    **Concurrency (security review, BLOCKER):** step 3's lock re-read must use
    `populate_existing` (`app.orders.selectors.get_order`'s own docstring) or it returns this same
    session's step-1 object with its stale, pre-lock `version`/`status` — the credit decision made
    in step 2 was based on that step-1 read, so step 3 also compares the order's version *as of
    step 1* against the version the lock shows once re-read, and refuses (409, the same conflict
    shape as the `expected_version` check) if the order moved in between: a credit decision must
    never be applied to a version of the order that is no longer the one being accepted.
    """
    async with session.begin():
        await _bind_tenant(session, ctx.organization_id)
        order = await selectors.get_order(session, ctx.organization_id, order_id)
        if order is None:
            raise NotFoundProblem("That order is not part of this organization")
        resolved = await selectors.resolve_settings(
            session, ctx.organization_id, order.warehouse_id
        )
        claimed_version = order.version
        channel, total_minor, currency, customer_id = (
            order.channel,
            order.total_minor,
            order.currency,
            order.customer_id,
        )

    if resolved.payment_term_for(channel) == "credit":
        await check_credit_before_accept(
            ctx, customer_id=customer_id, order_total_minor=total_minor, currency=currency
        )

    async with session.begin():
        await _bind_tenant(session, ctx.organization_id)
        order = await selectors.get_order(session, ctx.organization_id, order_id, for_update=True)
        if order is None:
            raise NotFoundProblem("That order is not part of this organization")
        if order.version != claimed_version:
            # The order moved between step 1's read (which the credit decision above was made
            # against) and this lock — that decision no longer applies to the version being
            # accepted now, regardless of whether `expected_version` happens to still match.
            raise ConflictProblem(
                f"Order {order.number} is at version {order.version}, not {claimed_version}; "
                "reload it and try again"
            )
        if order.version != expected_version:
            raise ConflictProblem(
                f"Order {order.number} is at version {order.version}, not {expected_version}; "
                "reload it and try again"
            )
        if not domain.can_transition_order(order.status, "ACCEPTED"):
            raise ConflictProblem(f"Order {order.number} is {order.status} and cannot be accepted")

        lines = await selectors.lines_for(session, ctx.organization_id, order.id)
        if not lines:
            raise ConflictProblem(f"Order {order.number} has no lines to accept")
        if any(line.selling_price_minor is None for line in lines):
            raise ConflictProblem(
                f"Order {order.number} has an unpriced line; it must be re-placed"
            )

        reserve_lines = await _reserve_lines_for(session, ctx.organization_id, order, lines)
        await ledger.reserve_many(
            session,
            ctx,
            reserve_lines,
            reference_type="order",
            reference_id=order.id,
            idempotency_key=f"order:{order.id}:accept:v{order.version}",
            request=request,
        )

        before_status = order.status
        settings_snapshot = resolved.as_snapshot()
        # `check_credit_before_accept` is a documented no-op stub until F4 billing ships a real
        # credit-position endpoint (review item 10) — recorded here, not just implied by a route
        # summary, so a historical accept can be told apart from one a real check ever ran
        # against once F4 lands.
        settings_snapshot["credit_check"] = "not_implemented"
        _record_transition(order, to_status="ACCEPTED")
        order.settings_snapshot = settings_snapshot
        order.updated_by = str(ctx.principal.sub)
        order.updated_at = datetime.now(UTC)
        await session.flush()

        session.add(
            OrderTransition(
                organization_id=ctx.organization_id,
                order_id=order.id,
                from_status=before_status,
                to_status="ACCEPTED",
                actor=str(ctx.principal.sub),
                reason="",
            )
        )
        record_audit(
            session,
            AuditLog,
            organization_id=ctx.organization_id,
            actor_id=ctx.principal.sub,
            action="order.accepted",
            entity="order",
            entity_id=order.id,
            after={"status": "ACCEPTED", "settings_snapshot": settings_snapshot},
            request=request,
        )
        publish_event(
            session,
            OutboxEvent,
            organization_id=ctx.organization_id,
            event_type=EVT_ORDER_ACCEPTED,
            aggregate_type="order",
            aggregate_id=order.id,
            payload={
                "organization_id": str(ctx.organization_id),
                "order_id": str(order.id),
                "number": order.number,
                "total_minor": order.total_minor,
                "currency": order.currency,
                "settings_snapshot": settings_snapshot,
            },
            actor=_actor(ctx),
        )
    return order


# ------------------------------------------------------------------ hold / release


async def hold_order(
    session: AsyncSession,
    ctx: ScopeContext,
    order: Order,
    *,
    expected_version: int,
    reason: str,
    request: Request | None = None,
) -> Order:
    """Operator hold (ADR-0025 §1) — moves the order's own status only. `order` must already be
    locked by the caller."""
    if order.version != expected_version:
        raise ConflictProblem(
            f"Order {order.number} is at version {order.version}, not {expected_version}; "
            "reload it and try again"
        )
    if not domain.can_transition_order(order.status, "ON_HOLD"):
        raise ConflictProblem(f"Order {order.number} is {order.status} and cannot be held")

    before_status = order.status
    _record_transition(order, to_status="ON_HOLD")
    order.updated_by = str(ctx.principal.sub)
    order.updated_at = datetime.now(UTC)
    await session.flush()

    session.add(
        OrderTransition(
            organization_id=ctx.organization_id,
            order_id=order.id,
            from_status=before_status,
            to_status="ON_HOLD",
            actor=str(ctx.principal.sub),
            reason=reason,
        )
    )
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="order.held",
        entity="order",
        entity_id=order.id,
        after={"status": "ON_HOLD", "reason": reason},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_ORDER_HELD,
        aggregate_type="order",
        aggregate_id=order.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "order_id": str(order.id),
            "number": order.number,
            "reason": reason,
        },
        actor=_actor(ctx),
    )
    return order


async def release_hold_order(
    session: AsyncSession,
    ctx: ScopeContext,
    order: Order,
    *,
    expected_version: int,
    request: Request | None = None,
) -> Order:
    """Hold released (ADR-0025 §1) — the target is **recomputed from facts, not remembered**
    (`app.domain.order_release_target`), from the order's own pick tasks: no tasks yet ->
    `ACCEPTED`; an open task remains -> `IN_FULFILMENT`; no task is open and at least one is
    settled -> `READY` — the exact reading `app.fulfilment.services._maybe_ready_order` uses for
    the same edge (MAJOR 1 #2, security review; replaces the F2-era `TODO(F3)` that hardcoded
    "no tasks yet" regardless of what fulfilment had actually done). `PickTask.status` is read
    directly (`app.orders.selectors.pick_task_statuses_for_order`) rather than through
    `app.fulfilment.selectors`, which `app/orders/` may never import (ADR-0024 §1) — a plain
    model read is not that boundary. `order` must already be locked by the caller.
    """
    if order.version != expected_version:
        raise ConflictProblem(
            f"Order {order.number} is at version {order.version}, not {expected_version}; "
            "reload it and try again"
        )
    if order.status != "ON_HOLD":
        raise ConflictProblem(f"Order {order.number} is {order.status}, not on hold")

    statuses = await selectors.pick_task_statuses_for_order(session, ctx.organization_id, order.id)
    has_open_tasks = any(s in domain.PICK_TASK_OPEN_STATUSES for s in statuses)
    has_settled_tasks = any(s in domain.PICK_TASK_SETTLED_STATUSES for s in statuses)
    target = domain.order_release_target(
        has_open_tasks=has_open_tasks, all_tasks_picked=has_settled_tasks
    )
    if not domain.can_transition_order(order.status, target):  # pragma: no cover - defensive
        raise ConflictProblem(f"Order {order.number} cannot be released to {target}")

    before_status = order.status
    _record_transition(order, to_status=target)
    order.updated_by = str(ctx.principal.sub)
    order.updated_at = datetime.now(UTC)
    await session.flush()

    session.add(
        OrderTransition(
            organization_id=ctx.organization_id,
            order_id=order.id,
            from_status=before_status,
            to_status=target,
            actor=str(ctx.principal.sub),
            reason="",
        )
    )
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="order.hold_released",
        entity="order",
        entity_id=order.id,
        after={"status": target},
        request=request,
    )
    # No outbox event here: releasing a hold resumes whatever commercial milestone the order was
    # already at rather than reaching a new one, and the plan's event catalogue (§5) names
    # `held`, not a matching `released`.
    if target == "READY":
        # MAJOR 1 (security review): a release landing on `READY` is the same milestone
        # `_maybe_ready_order` reaches on the ordinary `IN_FULFILMENT -> READY` path, so it has to
        # run the same READY-triggered facts (`handle_order_ready`) — a pickup code, if this org
        # requires one, and the invoice request (or its visibly-logged skip). Skipping this left
        # `require_pickup_code=True` orgs with a `pickup_code` that is `None` forever: every
        # hand-over then 409s permanently, with no way out but cancelling the order. Same module
        # as `handle_order_ready`, called with the order row this function already holds locked —
        # no import-linter boundary crossed.
        await handle_order_ready(session, ctx, order, request=request)
    return order


# ------------------------------------------------------------------ cancel


async def cancel_order(
    session: AsyncSession,
    ctx: ScopeContext,
    order: Order,
    *,
    expected_version: int,
    reason: str,
    request: Request | None = None,
) -> Order:
    """Cancellation fan-out (ADR-0025 §1's "Cancellation" section), the part that is complete in
    `operations` immediately: releases every open reservation the order holds, in the same
    transaction, then writes the transition, the audit row and the outbox event so finance and
    payment can issue their own credit note / refund **after commit** — operations never writes
    their tables (ADR-0000 §10).

    "Creates a putaway task for anything already picked" (the plan's own wording) is unreachable
    today: no pick/putaway module exists yet (`app/fulfilment/`, F3), so nothing in this call can
    ever find a picked task to putaway. `order` must already be locked by the caller.
    """
    if order.version != expected_version:
        raise ConflictProblem(
            f"Order {order.number} is at version {order.version}, not {expected_version}; "
            "reload it and try again"
        )
    if not domain.can_transition_order(order.status, "CANCELLED"):
        raise ConflictProblem(f"Order {order.number} is {order.status} and cannot be cancelled")

    open_reservations = await selectors.open_reservations_for_order(
        session, ctx.organization_id, order.id
    )
    released_ids: list[str] = []
    for reservation in open_reservations:
        await ledger.release(session, ctx, reservation.id, request=request)
        released_ids.append(str(reservation.id))

    before_status = order.status
    _record_transition(order, to_status="CANCELLED")
    order.updated_by = str(ctx.principal.sub)
    order.updated_at = datetime.now(UTC)
    await session.flush()

    session.add(
        OrderTransition(
            organization_id=ctx.organization_id,
            order_id=order.id,
            from_status=before_status,
            to_status="CANCELLED",
            actor=str(ctx.principal.sub),
            reason=reason,
        )
    )
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="order.cancelled",
        entity="order",
        entity_id=order.id,
        after={"status": "CANCELLED", "reason": reason, "released_reservations": released_ids},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_ORDER_CANCELLED,
        aggregate_type="order",
        aggregate_id=order.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "order_id": str(order.id),
            "number": order.number,
            "reason": reason,
            "from_status": before_status,
            "released_reservations": released_ids,
        },
        actor=_actor(ctx),
    )
    return order


# ------------------------------------------------------------------ READY-triggered facts
# (pickup code, invoice request — Fulfilment plan F4, ADR-0025 §1)


def _generate_pickup_code() -> str:
    """A 6-digit code a customer presents at counter handover (`check_billing_gate`).
    `secrets.choice`, not `random`: guessing this code lets a stranger claim somebody else's
    paid order, so it has to come from the CSPRNG the same way any other bearer credential does."""
    return "".join(secrets.choice("0123456789") for _ in range(6))


def _resolve_finance_customer_id(order: Order) -> uuid.UUID | None:
    """Where `request_invoice_for_order` would resolve a `finance.customers.id` for this order's
    buyer. **Always `None` today** — this is F4's documented, unresolved gap, not an oversight:

    `Order.customer_id` is a `services/customer` id (ADR-0000 §10: a plain UUID, no cross-schema
    FK); `finance.customers.id` is a different id space entirely, and `finance.customers` carries
    no CRM link field to bridge them by design (its own model docstring: "Distinct from any CRM
    account by design — no cross-schema link"). `services/finance` exposes no lookup/create-by-
    external-reference endpoint today either (`services/finance/app/api/routes.py` and
    `internal.py` grepped for this slice: only the ordinary `POST /finance/customers`, full
    `CustomerCreate` payload, no dedup key) — calling it from here would mint a brand new finance
    customer on every order a repeat buyer places, which is worse than not invoicing them at all.

    Returns `None` until finance ships a real resolve-or-create surface. When it does, this
    function is the one place that changes: a real implementation would be a **call**, run with
    no session and no transaction open, before `request_invoice_for_order`'s own transaction —
    the identical `docs/io-outside-transactions.md` discipline `check_credit_before_accept`
    documents for the same reason.
    """
    return None


def _invoice_request_lines(
    lines: Sequence[OrderLine], items: Mapping[uuid.UUID, Any]
) -> list[dict[str, Any]]:
    """The `lines` array of the `invoice_requested` payload — each line's own accepted price
    snapshot (ADR-0024 §3: `selling_price_minor`/`line_total_minor`, never recomputed here).
    `quantity` is a wire float, the shape the schema and finance's `OrderInvoiceLineIn` both take
    it as (`format_qty` turns `quantity_milli` back into a decimal string first, the same
    conversion `order_line_out` already uses, rather than dividing by `QTY_SCALE` by hand a second
    way)."""
    out: list[dict[str, Any]] = []
    for line in lines:
        item = items.get(line.inventory_item_id)
        assert line.selling_price_minor is not None  # every READY order's lines are priced
        assert line.line_total_minor is not None
        out.append(
            {
                "sku": item.sku if item is not None else "",
                "quantity": float(format_qty(line.quantity_milli)),
                "unit_price_minor": line.selling_price_minor,
                "line_amount_minor": line.line_total_minor,
            }
        )
    return out


def _invoice_request_payload(
    order: Order,
    lines: Sequence[OrderLine],
    items: Mapping[uuid.UUID, Any],
    *,
    customer_id: uuid.UUID,
    tax_rate_pct: Decimal,
) -> dict[str, Any]:
    """The exact `operations.orders.order.invoice_requested` v1 shape
    (`libs/contracts/events/operations.orders.order.invoice_requested/v1.schema.json`,
    `additionalProperties: false` — every key here is one the schema names, nothing more).

    `expected_tax_minor`/`expected_total_minor` are computed with
    `domain.expected_gst_amount_minor`, which mirrors finance's own rounding (that function's own
    docstring explains why this is safe without knowing finance's seller GST registration:
    `place_of_supply` is always omitted here, and the total a rate produces does not depend on
    the intra/inter-state split). `reverse_charge` is always `False` and `supply_type` always
    `"b2b"` — operations tracks neither yet, and both are this schema's own defaults."""
    invoice_lines = _invoice_request_lines(lines, items)
    subtotal = domain.order_subtotal_minor(lines)
    tax = sum(
        domain.expected_gst_amount_minor(line.line_total_minor or 0, tax_rate_pct) for line in lines
    )
    return {
        "organization_id": str(order.organization_id),
        "order_id": str(order.id),
        "order_number": order.number,
        "customer_id": str(customer_id),
        "currency": order.currency,
        "lines": invoice_lines,
        "tax_rate_pct": float(tax_rate_pct),
        "expected_subtotal_minor": subtotal,
        "expected_tax_minor": tax,
        "expected_total_minor": subtotal + tax,
        "supply_type": "b2b",
        "reverse_charge": False,
    }


async def request_invoice_for_order(
    session: AsyncSession,
    ctx: ScopeContext,
    order: Order,
    lines: Sequence[OrderLine],
    *,
    request: Request | None = None,
) -> None:
    """Emit `operations.orders.order.invoice_requested` once picking/packing completes for an
    order (ADR-0024 §1/§4; Fulfilment plan F4) — called from
    `app.fulfilment.services._maybe_ready_order` (via `handle_order_ready`), inside the same
    transaction that already holds the order row lock for the `IN_FULFILMENT -> READY` move
    (ADR-0025 §1's order-row-first discipline), so this needs no lock of its own.

    **This slice's customer_id gap (see the slice's own report).** The event's `customer_id` must
    be a `finance.customers.id` (the schema's own description). `_resolve_finance_customer_id`
    always returns `None` today — there is no bridge from `Order.customer_id` to one, and no
    finance endpoint to build one against in this request/response cycle (that function's own
    docstring has the full reasoning). Rather than send an empty/fabricated id, which would only
    bounce off finance's `unknown_customer` rejection, this is a **visibly-logged no-op**: a
    structured warning and an audit row (`order.invoice_request_skipped`) so the gap is on the
    order's own trail, not only in a log line. The `READY` transition itself is unaffected —
    picking completion is not conditional on billing.
    """
    tax_rate_pct = Decimal((order.settings_snapshot or {}).get("default_tax_rate_pct", "0"))
    finance_customer_id = _resolve_finance_customer_id(order)
    if finance_customer_id is None:
        log.warning(
            "orders.invoice_request.skipped",
            organization_id=str(ctx.organization_id),
            order_id=str(order.id),
            order_number=order.number,
            reason="no_finance_customer_mapping",
        )
        record_audit(
            session,
            AuditLog,
            organization_id=ctx.organization_id,
            actor_id=ctx.principal.sub,
            action="order.invoice_request_skipped",
            entity="order",
            entity_id=order.id,
            after={"reason": "no_finance_customer_mapping"},
            request=request,
        )
        return

    item_ids = [line.inventory_item_id for line in lines]
    items = await selectors.items_for_lines(session, ctx.organization_id, item_ids)
    payload = _invoice_request_payload(
        order, lines, items, customer_id=finance_customer_id, tax_rate_pct=tax_rate_pct
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_ORDER_INVOICE_REQUESTED,
        aggregate_type="order",
        aggregate_id=order.id,
        payload=payload,
        actor=_actor(ctx),
    )
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="order.invoice_requested",
        entity="order",
        entity_id=order.id,
        after={
            "customer_id": str(finance_customer_id),
            "expected_total_minor": payload["expected_total_minor"],
        },
        request=request,
    )


async def handle_order_ready(
    session: AsyncSession, ctx: ScopeContext, order: Order, *, request: Request | None = None
) -> None:
    """Runs once, inside the same transaction that moves an order to `READY` — either
    `app.fulfilment.services._maybe_ready_order`'s ordinary `IN_FULFILMENT -> READY` path, or
    `release_hold_order`'s `ON_HOLD -> READY` release (MAJOR 1, security review) — both of which
    already hold the order row lock before calling this. The two READY-triggered facts Fulfilment
    plan F4 adds:

    1. Issues a pickup code, if this org requires one at handover
       (`fulfilment_settings.require_pickup_code`, snapshotted onto the order at accept) and one
       is not already set — idempotent against being called twice for the same order, though
       `_maybe_ready_order`'s own guard (`order.status != "IN_FULFILMENT"` returns early) means
       it structurally never is.
    2. Requests an invoice from finance (`request_invoice_for_order` — see that function's own
       docstring for this slice's customer_id gap).
    """
    snapshot = order.settings_snapshot or {}
    if snapshot.get("require_pickup_code", False) and order.pickup_code is None:
        order.pickup_code = _generate_pickup_code()
        # `release_hold_order` already flushed its own `updated_at` write before calling here, so
        # this pickup-code write is a second, later dirty column on the same row: an autoflush
        # from here on (the `lines_for` read two lines down, say) issues a second `UPDATE order`
        # that does not carry `updated_at` explicitly, and its `onupdate=func.now()` is a SQL-side
        # default, not a literal value SQLAlchemy can keep in memory — the Python attribute is
        # left expired. A caller that reads it later with no awaited ORM call of its own
        # (`app/api/routes.py::_order_out`, building the response) would then try to lazy-load it
        # and crash with `MissingGreenlet`. Touching it again here keeps it a real, already-loaded
        # value no matter how many separate flushes this call ends up costing.
        order.updated_at = datetime.now(UTC)

    lines = await selectors.lines_for(session, ctx.organization_id, order.id)
    await request_invoice_for_order(session, ctx, order, lines, request=request)


# ------------------------------------------------------------------ billing gate (ADR-0025 §1)


def check_billing_gate(order: Order) -> None:
    """`READY -> HANDED_OVER`'s guard (ADR-0025 §1) — reads **only** `order`'s own columns
    (including its `settings_snapshot`, the policy it was accepted under). Calls no other
    service: every fact it checks arrived earlier, from an event or from the same handover call
    that is about to use it. Raises `ConflictProblem` naming exactly the condition that is
    missing; never more than one order's worth of state, so it is cheap enough to call from
    F5's carrier handover unchanged — the reason this is its own function rather than inlined
    into `hand_over_order`.
    """
    snapshot = order.settings_snapshot or {}

    invoice_timing = snapshot.get("invoice_timing", "on_accept")
    if invoice_timing in _INVOICE_REQUIRED_BY_HANDOVER_TIMINGS and order.invoice_id is None:
        raise ConflictProblem(
            f"Order {order.number} has no invoice yet, and this organization's invoice-timing "
            f"policy ({invoice_timing}) requires one before handover"
        )

    payment_term = snapshot.get("payment_defaults", {}).get(order.channel, "cash")
    if payment_term in _PAYMENT_REQUIRED_TERMS and order.paid_at is None:
        raise ConflictProblem(
            f"Order {order.number} has not been paid, and its payment term ({payment_term}) "
            "requires payment before handover"
        )

    if snapshot.get("require_pickup_code", False) and order.pickup_code_verified_at is None:
        raise ConflictProblem(
            f"Order {order.number}'s pickup code has not been verified, and this organization "
            "requires one before handover"
        )


# ------------------------------------------------------------------ handover completion (shared:
# counter/walk-in `hand_over_order` and F5's carrier `app.dispatch.services.dispatch_order`)


async def _consume_order_stock_for_handover(
    session: AsyncSession, ctx: ScopeContext, order: Order, *, request: Request | None
) -> list[InventoryReservation]:
    """The stock-consuming half of a handover (security review M2): posts the kernel `issue`
    move that actually draws down whatever pick and pack transferred (ADR-0024 §6) — previously
    only `app.dispatch.services.dispatch_order`'s own job, leaving every counter hand-over
    (`hand_over_order`, F4, already live) writing zero ledger activity: the staging reservation
    stayed `active` forever, stock both "on hand" and "reserved", permanently unavailable to any
    other order. Both handover paths call this identically now.

    Refuses (409) if any of the order's own lines still has an untouched accept-time reservation
    (security review M1: never draw straight from the storage bin — only a pick or pack
    destination is fair game). The carrier path additionally runs its own, stronger,
    fulfilment-aware check before ever reaching here (`app.dispatch.services.dispatch_order`,
    naming a partially-packed line too); this one is the generic floor both paths share, using
    only `app.inventory`/`app.orders` data (ADR-0024 §1: `orders` never imports `fulfilment`).
    """
    org_id = ctx.organization_id
    unpicked = await selectors.unpicked_lines_for_order(session, org_id, order.id)
    if unpicked:
        item_ids = [r.inventory_item_id for r in unpicked]
        items = await selectors.items_for_lines(session, org_id, item_ids)
        names = sorted({items[i].sku if i in items else str(i) for i in item_ids})
        raise ConflictProblem(
            f"Order {order.number} cannot be handed over — never picked: {', '.join(names)}"
        )

    leaves = await selectors.leaf_reservations_for_order(session, org_id, order.id)
    balances = await inv_selectors.balances_by_id(session, org_id, [r.balance_id for r in leaves])
    move_lines: list[MoveLine] = []
    for reservation in leaves:
        open_remainder = (
            reservation.quantity_milli
            - reservation.consumed_milli
            - reservation.released_milli
            - reservation.transferred_milli
        )
        if open_remainder <= 0:  # pragma: no cover - defensive, `status == "active"` implies this
            continue
        balance = balances.get(reservation.balance_id)
        if balance is None:  # pragma: no cover - defensive, the reservation's own FK guarantees it
            raise ConflictProblem(
                f"{reservation.id}'s reservation has no stock bucket to draw from"
            )
        move_lines.append(
            MoveLine(
                item_id=reservation.inventory_item_id,
                location_id=balance.location_id,
                quantity_milli=-open_remainder,
                reservation_id=reservation.id,
            )
        )
    if not move_lines:  # pragma: no cover - defensive, `unpicked`'s own guard above rules this out
        raise ConflictProblem(f"Order {order.number} has nothing staged to hand over")

    await ledger.post_move(
        session,
        ctx,
        kind="issue",
        lines=move_lines,
        occurred_on=date.today(),
        reference_type="order",
        reference_id=order.id,
        # Stable per order (security review cheap-fix list): a retry after a lost response
        # replays this exact move instead of a fresh key finding nothing and answering 409.
        idempotency_key=f"handover:{order.id}",
        occurred_on_explicit=False,
        request=request,
    )
    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=ctx.principal.sub,
        action="order.handover_stock_issued",
        entity="order",
        entity_id=order.id,
        after={
            "reservations_consumed": [str(r.id) for r in leaves],
            "line_count": len(move_lines),
        },
        request=request,
    )
    return leaves


async def complete_handover(
    session: AsyncSession, ctx: ScopeContext, order: Order, *, request: Request | None = None
) -> None:
    """The `READY -> HANDED_OVER -> DELIVERED` write itself (ADR-0025 §1: "the handover *is*
    delivery... both transition rows are written in one transaction, so there is only one path"),
    factored out so the two-transition write, its `order_transitions` rows, and the
    `operations.orders.order.handed_over` event exist in **exactly one place** — both handover
    paths this build has write them identically, only how each path gets to this point differs.

    **Also** where the stock actually leaves (security review M2): `_consume_order_stock_for_
    handover` posts the kernel `issue` move against every reservation pick and pack ever
    transferred for this order, before either transition is written — a refusal there rolls back
    cleanly, the same fail-fast order `dispatch_order` already used for its own version/transition
    checks.

    **Not this function's job**, and already done by the time a caller reaches here: the
    `order.version`/`can_transition_order(order.status, "HANDED_OVER")` check (each caller's own
    first action, before it does any of its own path-specific work — recording a counter
    attestation, or creating a shipment and posting the dispatch issue — so a stale version or a
    non-`READY` order is refused before that work ever happens, not only before this write), and
    `check_billing_gate` itself. `order` must already be locked by the caller, the same `FOR
    UPDATE` shape every order write in this module uses.
    """
    await _consume_order_stock_for_handover(session, ctx, order, request=request)

    handed_over_from = order.status
    _record_transition(order, to_status="HANDED_OVER")
    session.add(
        OrderTransition(
            organization_id=ctx.organization_id,
            order_id=order.id,
            from_status=handed_over_from,
            to_status="HANDED_OVER",
            actor=str(ctx.principal.sub),
            reason="",
        )
    )
    delivered_from = order.status
    if not domain.can_transition_order(  # pragma: no cover - defensive
        delivered_from, "DELIVERED"
    ):
        raise ConflictProblem(
            f"Order {order.number} cannot be marked delivered from {delivered_from}"
        )
    _record_transition(order, to_status="DELIVERED")
    session.add(
        OrderTransition(
            organization_id=ctx.organization_id,
            order_id=order.id,
            from_status=delivered_from,
            to_status="DELIVERED",
            actor=str(ctx.principal.sub),
            reason="",
        )
    )
    order.updated_by = str(ctx.principal.sub)
    order.updated_at = datetime.now(UTC)
    await session.flush()

    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_ORDER_HANDED_OVER,
        aggregate_type="order",
        aggregate_id=order.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "order_id": str(order.id),
            "number": order.number,
        },
        actor=_actor(ctx),
    )


def attest_payment_if_confirmed(order: Order, *, payment_confirmed: bool) -> bool:
    """The cash/prepaid payment attestation both handover paths share (security review M3): the
    counter operator's own confirmation (`hand_over_order`) and, identically, the carrier-desk
    operator's own confirmation (`app.dispatch.services.dispatch_order`) that COD was collected
    or prepayment is confirmed — the same stand-in `OrderHandoverIn`'s own docstring documents for
    `services/payment`'s tenant collections module (ADR-0024 A4), not yet built. Never touches
    `paid_at` for a credit-terms order (nothing to confirm) or if it is already set.

    Returns whether *this* call is what set it (MINOR 5, security review): `order.paid_at is not
    None` alone cannot tell a fact this call just established from one recorded earlier or by an
    event, which the caller's own audit row needs to say correctly.
    """
    snapshot = order.settings_snapshot or {}
    payment_term = snapshot.get("payment_defaults", {}).get(order.channel, "cash")
    attested = (
        payment_confirmed and payment_term in _PAYMENT_REQUIRED_TERMS and order.paid_at is None
    )
    if attested:
        order.paid_at = datetime.now(UTC)
    return attested


# ------------------------------------------------------------------ hand over (counter/walk-in)


async def hand_over_order(
    session: AsyncSession,
    ctx: ScopeContext,
    order: Order,
    *,
    expected_version: int,
    pickup_code: str | None = None,
    payment_confirmed: bool = False,
    request: Request | None = None,
) -> Order:
    """`READY -> HANDED_OVER -> DELIVERED`, the counter/walk-in handover (ADR-0025 §1): "the
    handover *is* delivery... both transition rows are written in one transaction, so there is
    only one path." `order` must already be locked by the caller (`app/api/routes.py`, the same
    `FOR UPDATE` shape `hold_order`/`cancel_order` use).

    Two facts are recorded **before** the gate runs, in this same transaction, if the caller
    supplied them — this is how the reusable `check_billing_gate` ends up with something to read
    without itself calling anyone: this call is the one that *establishes* the fact for the
    counter path, exactly as an earlier event establishes it for the eventual carrier path (F5).
    Recording happens before the gate check but the whole call is still one transaction: a failed
    gate raises and rolls every field write in this call back too, so a caller must resend both
    the code and the confirmation together next time, never rely on a partial attempt having
    stuck.

    * `pickup_code`, compared against `order.pickup_code`, sets `pickup_code_verified_at` on a
      match. A caller who does not yet know the code (or is wrong) leaves it unset, and the gate
      answers exactly why.
    * `payment_confirmed`, the operator's own attestation for a cash/prepaid order — see
      `OrderHandoverIn`'s own docstring for the honest gap this stands in for. Never touches
      `paid_at` for a credit-terms order (nothing to confirm) or if it is already set.
    """
    if order.version != expected_version:
        raise ConflictProblem(
            f"Order {order.number} is at version {order.version}, not {expected_version}; "
            "reload it and try again"
        )
    if not domain.can_transition_order(order.status, "HANDED_OVER"):
        raise ConflictProblem(f"Order {order.number} is {order.status} and cannot be handed over")

    if (
        pickup_code is not None
        and order.pickup_code is not None
        and order.pickup_code_verified_at is None
        and secrets.compare_digest(pickup_code, order.pickup_code)
    ):
        order.pickup_code_verified_at = datetime.now(UTC)

    payment_attested_by_operator = attest_payment_if_confirmed(
        order, payment_confirmed=payment_confirmed
    )

    check_billing_gate(order)

    await complete_handover(session, ctx, order, request=request)

    handover_audit_after: dict[str, Any] = {
        "status": "DELIVERED",
        "pickup_code_verified": order.pickup_code_verified_at is not None,
        "paid": order.paid_at is not None,
    }
    if payment_attested_by_operator:
        handover_audit_after["payment_attested_by_operator"] = True
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="order.handed_over",
        entity="order",
        entity_id=order.id,
        after=handover_audit_after,
        request=request,
    )
    return order


# ------------------------------------------------------------------ consumer support
# (finance.orders.invoice.issued — Fulfilment plan F4)

#: The permission a system-originated write (this module's own event consumer) presents as —
#: same shape `app.fulfilment.services.service_ctx` already uses, and the same reasoning: no
#: human is behind this write, so it is floored at the ordinary `orders.order.write` grant rather
#: than anything stronger, and it carries no real `sub` (`uuid.UUID(int=0)`, never a real
#: principal's id).
_ORDERS_SYSTEM_PERMISSION = "operations.orders.order.write"


def service_ctx(organization_id: uuid.UUID) -> ScopeContext:
    principal = Principal(
        sub=uuid.UUID(int=0),
        organization_id=organization_id,
        membership_id=None,
        perms=[f"{_ORDERS_SYSTEM_PERMISSION}:ORG"],
        entitlements=["operations"],
    )
    return ScopeContext(principal=principal, scope=Scope.ORG, permission=_ORDERS_SYSTEM_PERMISSION)


async def record_invoice_issued(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    order_id: uuid.UUID,
    invoice_id: uuid.UUID,
    invoice_number: str,
    request: Request | None = None,
) -> bool:
    """Writes the invoice fact `check_billing_gate` reads (ADR-0025 §1) — bound to finance's own
    `finance.orders.invoice.issued` (`app.consumers.finance_invoice_issued`). Returns whether it
    actually wrote anything, for the consumer's own log line.

    Idempotent twice over, `order_cancelled`'s own consumer's discipline: the event id, via
    `ProcessedEvent`, is the consumer's job; here, the domain — a second delivery for an order
    that already carries an `invoice_id` is a silent no-op, never an overwrite. An invoice is
    immutable once issued (ADR-0000 §9), so operations' own record of it must never look like it
    changed underneath a second, redelivered copy of the same fact.
    """
    order = await selectors.get_order(session, ctx.organization_id, order_id, for_update=True)
    if order is None:
        log.warning(
            "orders.invoice_issued.unknown_order",
            organization_id=str(ctx.organization_id),
            order_id=str(order_id),
        )
        return False
    if order.invoice_id is not None:
        return False

    order.invoice_id = invoice_id
    order.version += 1
    order.updated_by = str(ctx.principal.sub)
    order.updated_at = datetime.now(UTC)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="order.invoice_recorded",
        entity="order",
        entity_id=order.id,
        after={"invoice_id": str(invoice_id), "invoice_number": invoice_number},
        request=request,
    )
    return True
