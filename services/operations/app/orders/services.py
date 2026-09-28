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

import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain
from app.config import settings
from app.inventory import ledger
from app.inventory import selectors as inv_selectors
from app.inventory.ledger import ReserveLine
from app.inventory.quantity import MAX_ABS_MILLI, QuantityError, parse_qty
from app.models import AuditLog, Order, OrderLine, OrderTransition, OutboxEvent
from app.orders import selectors
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.db import assert_tenant, set_tenant
from platform_core.errors import ConflictProblem, NotFoundProblem, ValidationProblem
from platform_core.events import publish_event

EVT_ORDER_PLACED = "operations.orders.order.placed"
EVT_ORDER_ACCEPTED = "operations.orders.order.accepted"
EVT_ORDER_HELD = "operations.orders.order.held"
EVT_ORDER_CANCELLED = "operations.orders.order.cancelled"

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
    (`app.domain.order_release_target`). `app/fulfilment/`'s pick tasks (F3) have not landed, so
    there is never an open or picked task to ask about, and every release lands on `ACCEPTED`
    until one exists — this call always passes `has_open_tasks=False, all_tasks_picked=False`,
    revisited the day F3 ships. `order` must already be locked by the caller."""
    if order.version != expected_version:
        raise ConflictProblem(
            f"Order {order.number} is at version {order.version}, not {expected_version}; "
            "reload it and try again"
        )
    if order.status != "ON_HOLD":
        raise ConflictProblem(f"Order {order.number} is {order.status}, not on hold")

    # TODO(F3 fulfilment): once `pick_tasks` exists, resolve these from the order's own tasks
    # instead of hardcoding "none yet".
    target = domain.order_release_target(has_open_tasks=False, all_tasks_picked=False)
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
