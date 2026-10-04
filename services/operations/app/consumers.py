"""Event consumers for operations. Runs as its own process.

Every handler is idempotent: `already_processed` before acting, `mark_processed` in the
same transaction as its side effects. Redelivery is normal, not exceptional.

Two fulfilment handlers (ADR-0024 §1, ADR-0025 §3; `docs/fulfilment/plan.md` F3):

* **`expire_offer_requested`** — bound to `operations.fulfilment.offer.expire_requested`, the
  target event `app.fulfilment.services.offer_task` asks `services/scheduler` to relay back once
  an offer's timeout is up (`platform_core.events.schedule_event`, ADR-0015). Fires the exact
  conditional update ADR-0025 §3 specifies; racing an accept that already landed is a silent
  no-op, never an error.
* **`order_cancelled`** — bound to `operations.orders.order.cancelled`, `app/orders/`'s own
  event. This is how a cancelled order's fan-out reaches its open pick tasks: `app/orders/`
  never imports `app/fulfilment/` (ADR-0024 §1, enforced by `.importlinter`), so the only path
  from "order cancelled" to "its tasks are cancelled too" is this round trip through the outbox.

One more, F4's own (ADR-0025 §1's billing gate; Fulfilment plan F4):

* **`finance_invoice_issued`** — bound to `finance.orders.invoice.issued`, a **different
  service's** own event (`services/finance/app/services.py::EVT_ORDER_INVOICE_ISSUED`, already
  deployed). There is no cross-service import to bind against — the routing key is copied by hand
  onto `app.orders.services.EVT_FINANCE_INVOICE_ISSUED`, the same contract-not-code discipline
  `EVT_OFFER_EXPIRE_REQUESTED` already documents for `services/scheduler`. Writes the order's own
  `invoice_id` fact, which `app.orders.services.check_billing_gate` reads.

And two for online payment (ADR-0024 A4, `services/payment`'s tenant collections; Fulfilment F10):

* **`payment_collection_captured`** — bound to `payment.collections.payment.captured`. The only
  path by which an online payment sets the order's `paid_at` (the fact the billing gate reads):
  payment emits it only after a signature-verified provider webhook or a server-to-server status
  check, never on a browser's word.
* **`payment_collection_refunded`** — bound to `payment.collections.payment.refunded`. A full
  refund of the collection that paid the order reverses that fact; a refund that then fails
  restores it.

Two for inventory Phase 3's approvals (ADR-0006; docs/inventory/movements.md): the approval
service is a different service, so the product applies its answer in its own transaction —

* **`approval_request_opened`** — bound to `core.approval.request.opened`; records which request is
  deciding a transfer or adjustment and who it waits on.
* **`approval_request_decided`** — bound to `core.approval.request.decided`; moves the transfer
  REQUESTED -> APPROVED/REJECTED, or posts (or refuses) the held adjustment.

One more, F11's own (channel fulfilment/tracking sync; Fulfilment plan F11):

* **`dispatch_shipment_transitioned`** — bound to all four of `app.dispatch.services`'
  `EVT_SHIPMENT_DISPATCHED`/`DELIVERED`/`FAILED`/`RETURNED`. This is the one place `app.channels`
  reaches `app.dispatch`'s own tables at all — `app.channels.services` itself never imports
  `app.dispatch` (its own module boundary, `.importlinter`'s `channels-boundary` contract), so the
  shipment's `tracking_reference`/`carrier_name` (not carried on the event payload itself) are read
  here, at the one layer already allowed to import both, and handed to
  `app.channels.services.push_fulfilment_update_for_order` as plain arguments. That function makes
  a real outbound HTTP call and manages its own transactions (claim → call → record,
  `docs/io-outside-transactions.md`), so — unlike the three handlers above — this one runs on a
  **raw**, un-transacted session (`_raw_session_scope`), never `_session_scope`'s ambient one.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.channels import services as channel_services
from app.config import settings
from app.dispatch import selectors as dispatch_selectors
from app.dispatch import services as dispatch_services
from app.fulfilment import services as fulfilment_services
from app.models import ProcessedEvent
from app.movements import adjustments, transfers
from app.movements import common as movement_common
from app.orders import services as order_services
from platform_core.db import (
    assert_app_role,
    get_sessionmaker,
    init_engine,
    session_scope,
    set_tenant,
)
from platform_core.events import EventConsumer, already_processed, mark_processed
from platform_core.logging import get_logger

log = get_logger("operations.consumers")

consumer = EventConsumer(amqp_url=settings().rabbitmq_url, queue_name="operations.events")

#: Same shape as every service's `app.cron.WorkScope` — this service has no sweep of its own to
#: import it from, so it is defined here rather than imported.
WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]
#: A raw, un-transacted session factory — `platform_core.db.get_session`'s own shape (no ambient
#: `session.begin()`), needed by `dispatch_shipment_transitioned` because the function it calls
#: manages its own transactions. Not exported by `platform_core.db` as a `WorkScope`-shaped
#: callable, so it is built the same way `bulkhead_scope` builds its own sessionmaker.
RawScope = Callable[[], AbstractAsyncContextManager[AsyncSession]]


def _default_session_scope(organization_id: uuid.UUID) -> Any:
    return session_scope(organization_id=organization_id)


def _default_raw_scope() -> AbstractAsyncContextManager[AsyncSession]:
    return get_sessionmaker()()


#: The transaction scope the three ordinary handlers below open. Defaults to the process-wide
#: engine — this consumer's own connection pool when it runs standalone (`python -m
#: app.consumers`, `bin/worker`). `app/main.py` swaps it to a bulkhead scope
#: (`platform_core.db.bulkhead_scope`) before starting this consumer as a supervised background
#: task inside the API process, on `Settings.run_consumers_in_api` (MAJOR 2, security review) —
#: so a folded consumer's per-message transactions can neither take a connection from request
#: traffic nor be starved by it.
_session_scope: WorkScope = _default_session_scope
#: The raw scope `dispatch_shipment_transitioned` opens. Defaults the same way `_session_scope`
#: does; `app/main.py` swaps both to the same bulkhead engine together.
_raw_session_scope: RawScope = _default_raw_scope


def use_session_scope(scope: WorkScope) -> None:
    """Point the three ordinary handlers at `scope` instead of the default engine. Called once,
    before this consumer starts, by whichever entrypoint constructs it that way — never by a
    handler."""
    global _session_scope
    _session_scope = scope


def use_raw_session_scope(scope: RawScope) -> None:
    """Point `dispatch_shipment_transitioned` at `scope` instead of the default engine — called
    alongside `use_session_scope`, on the same engine, so the two share one bulkhead."""
    global _raw_session_scope
    _raw_session_scope = scope


def bulkhead_raw_scope(engine: AsyncEngine) -> RawScope:
    """`get_session`'s own shape (no ambient transaction), on `engine` instead of the process-wide
    one — the raw counterpart to `platform_core.db.bulkhead_scope`, which only ever opens an
    ambient transaction and so cannot serve a handler that manages its own."""
    sessionmaker = async_sessionmaker(
        engine, expire_on_commit=False, autoflush=False, class_=AsyncSession
    )

    def scope() -> AbstractAsyncContextManager[AsyncSession]:
        return sessionmaker()

    return scope


async def expire_offer_requested(envelope: dict[str, Any]) -> None:
    event_id = uuid.UUID(envelope["event_id"])
    organization_id = uuid.UUID(str(envelope["organization_id"]))
    payload = envelope["payload"]
    offer_id = uuid.UUID(str(payload["offer_id"]))

    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("fulfilment.offer_expiry.duplicate", event_id=str(event_id))
            return
        ctx = fulfilment_services.service_ctx(organization_id)
        fired = await fulfilment_services.expire_offer(session, ctx, offer_id=offer_id)
        mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])
    log.info(
        "fulfilment.offer_expiry.handled",
        event_id=str(event_id),
        offer_id=str(offer_id),
        fired=fired,
    )


consumer.on(fulfilment_services.EVT_OFFER_EXPIRE_REQUESTED)(expire_offer_requested)


async def order_cancelled(envelope: dict[str, Any]) -> None:
    event_id = uuid.UUID(envelope["event_id"])
    organization_id = uuid.UUID(str(envelope["organization_id"]))
    payload = envelope["payload"]
    order_id = uuid.UUID(str(payload["order_id"]))

    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("fulfilment.order_cancelled.duplicate", event_id=str(event_id))
            return
        ctx = fulfilment_services.service_ctx(organization_id)
        cancelled = await fulfilment_services.cancel_tasks_for_order(
            session, ctx, order_id=order_id
        )
        mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])
    log.info(
        "fulfilment.order_cancelled.handled",
        event_id=str(event_id),
        order_id=str(order_id),
        cancelled_tasks=len(cancelled),
    )


consumer.on("operations.orders.order.cancelled")(order_cancelled)


async def finance_invoice_issued(envelope: dict[str, Any]) -> None:
    """Bound to `finance.orders.invoice.issued` — see this module's own docstring for why the
    routing key is a copied string, not an import. **The GUC is set from the envelope's own
    `organization_id`, never the payload's** — the payload is JSON this consumer cannot prove
    nothing altered in transit, while the envelope's column is one finance's own row-level
    security enforced; a mismatch is marked processed (so it is never retried into a loop) and
    logged as an error rather than trusted, the identical guard
    `services/finance/app/consumers.py::invoice_requested_from_order` already documents for the
    same cross-service shape.
    """
    event_id = uuid.UUID(envelope["event_id"])
    envelope_organization_id = uuid.UUID(str(envelope["organization_id"]))
    payload = envelope["payload"]
    payload_organization_id = uuid.UUID(str(payload["organization_id"]))

    if payload_organization_id != envelope_organization_id:
        async with _session_scope(envelope_organization_id) as session:
            if await already_processed(session, ProcessedEvent, event_id):
                log.info("orders.invoice_issued.duplicate", event_id=str(event_id))
                return
            mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])
        log.error(
            "orders.invoice_issued.organization_mismatch",
            event_id=str(event_id),
            envelope_organization_id=str(envelope_organization_id),
            payload_organization_id=str(payload_organization_id),
        )
        return

    order_id = uuid.UUID(str(payload["order_id"]))
    invoice_id = uuid.UUID(str(payload["invoice_id"]))
    invoice_number = str(payload["invoice_number"])

    async with _session_scope(envelope_organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("orders.invoice_issued.duplicate", event_id=str(event_id))
            return
        ctx = order_services.service_ctx(envelope_organization_id)
        recorded = await order_services.record_invoice_issued(
            session,
            ctx,
            order_id=order_id,
            invoice_id=invoice_id,
            invoice_number=invoice_number,
        )
        mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])
    log.info(
        "orders.invoice_issued.handled",
        event_id=str(event_id),
        order_id=str(order_id),
        invoice_id=str(invoice_id),
        recorded=recorded,
    )


consumer.on(order_services.EVT_FINANCE_INVOICE_ISSUED)(finance_invoice_issued)


async def _collection_envelope(
    envelope: dict[str, Any], *, log_prefix: str
) -> tuple[uuid.UUID, uuid.UUID, dict[str, Any]] | None:
    """The shared front half of both payment-collection handlers: the envelope's organization is
    the tenant (never the payload's — a mismatch is marked processed and logged, never trusted,
    the same guard `finance_invoice_issued` documents), and only a collection *for an order* is
    this service's business. Returns `(organization_id, event_id, payload)`, or None when the
    delivery has been dealt with already (a duplicate, a mismatch, not an order)."""
    event_id = uuid.UUID(envelope["event_id"])
    organization_id = uuid.UUID(str(envelope["organization_id"]))
    payload = envelope["payload"]
    mismatch = uuid.UUID(str(payload["organization_id"])) != organization_id
    not_ours = payload.get("reference_type") != order_services.PAYMENT_REFERENCE_ORDER
    if mismatch or not_ours:
        async with _session_scope(organization_id) as session:
            if await already_processed(session, ProcessedEvent, event_id):
                return None
            mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])
        (log.error if mismatch else log.info)(
            f"{log_prefix}.skipped",
            event_id=str(event_id),
            reason="organization_mismatch" if mismatch else "not_an_order",
        )
        return None
    return organization_id, event_id, payload


async def payment_collection_captured(envelope: dict[str, Any]) -> None:
    """Bound to `payment.collections.payment.captured` (`services/payment`'s tenant collections,
    ADR-0024 A4) — a customer paid an order online, verified by payment against the provider
    (a signed webhook or a server-to-server status check). Sets the order's `paid_at`, the fact
    `check_billing_gate` reads — the one path by which an online payment can ever satisfy it."""
    front = await _collection_envelope(envelope, log_prefix="orders.online_payment")
    if front is None:
        return
    organization_id, event_id, payload = front
    occurred = payload.get("occurred_at")
    paid_at = datetime.fromisoformat(occurred) if occurred else datetime.now(UTC)
    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("orders.online_payment.duplicate", event_id=str(event_id))
            return
        outcome = await order_services.record_online_payment(
            session,
            order_services.service_ctx(organization_id),
            order_id=uuid.UUID(str(payload["reference_id"])),
            charge_id=uuid.UUID(str(payload["charge_id"])),
            amount_minor=int(payload["amount_minor"]),
            currency=str(payload["currency"]),
            paid_at=paid_at,
        )
        mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])
    log.info(
        "orders.online_payment.handled",
        event_id=str(event_id),
        order_id=str(payload["reference_id"]),
        outcome=outcome,
    )


consumer.on(order_services.EVT_PAYMENT_COLLECTION_CAPTURED)(payment_collection_captured)


async def payment_collection_refunded(envelope: dict[str, Any]) -> None:
    """Bound to `payment.collections.payment.refunded` — the order's online payment's standing
    refund total changed. A full refund reverses the paid fact that payment set; a refund that
    later failed restores it (`record_online_refund`)."""
    front = await _collection_envelope(envelope, log_prefix="orders.online_refund")
    if front is None:
        return
    organization_id, event_id, payload = front
    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("orders.online_refund.duplicate", event_id=str(event_id))
            return
        outcome = await order_services.record_online_refund(
            session,
            order_services.service_ctx(organization_id),
            order_id=uuid.UUID(str(payload["reference_id"])),
            charge_id=uuid.UUID(str(payload["charge_id"])),
            fully_refunded=bool(payload["fully_refunded"]),
            refunded_total_minor=int(payload["refunded_total_minor"]),
        )
        mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])
    log.info(
        "orders.online_refund.handled",
        event_id=str(event_id),
        order_id=str(payload["reference_id"]),
        outcome=outcome,
    )


consumer.on(order_services.EVT_PAYMENT_COLLECTION_REFUNDED)(payment_collection_refunded)


async def dispatch_shipment_transitioned(envelope: dict[str, Any], *, channel_status: str) -> None:
    """Bound four times below, once per `app.dispatch.services` shipment-transition event, each
    with its own `channel_status` (`app.channels.services.shipment_status_to_channel_status`'s
    own vocabulary) — see module docstring for why this handler, alone, runs on a raw session.
    """
    event_id = uuid.UUID(envelope["event_id"])
    organization_id = uuid.UUID(str(envelope["organization_id"]))
    payload = envelope["payload"]
    shipment_id = uuid.UUID(str(payload["shipment_id"]))
    order_id = uuid.UUID(str(payload["order_id"]))

    async with _raw_session_scope() as session:
        async with session.begin():
            await set_tenant(session, organization_id)
            if await already_processed(session, ProcessedEvent, event_id):
                log.info("channels.fulfilment_push.duplicate", event_id=str(event_id))
                return
            shipment = await dispatch_selectors.get_shipment(session, organization_id, shipment_id)
            tracking_number = shipment.tracking_reference if shipment else None
            carrier = shipment.carrier_name if shipment else None

        ctx = channel_services.service_ctx(organization_id)
        pushed = await channel_services.push_fulfilment_update_for_order(
            session,
            ctx,
            order_id,
            status=channel_status,
            tracking_number=tracking_number,
            carrier=carrier,
            occurred_at=datetime.now(UTC),
        )

        async with session.begin():
            await set_tenant(session, organization_id)
            mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])
    log.info(
        "channels.fulfilment_push.handled",
        event_id=str(event_id),
        order_id=str(order_id),
        channel_status=channel_status,
        pushed=pushed,
    )


#: Event -> the `Shipment.status` it fires on (`app.dispatch.services._TRANSITION_EVENTS`,
#: mirrored rather than imported since it is a private module constant). Translated through
#: `channel_services.shipment_status_to_channel_status` at bind time rather than duplicating that
#: vocabulary here a second time.
for _event_name, _shipment_status in (
    (dispatch_services.EVT_SHIPMENT_DISPATCHED, "IN_TRANSIT"),
    (dispatch_services.EVT_SHIPMENT_DELIVERED, "DELIVERED"),
    (dispatch_services.EVT_SHIPMENT_FAILED, "FAILED"),
    (dispatch_services.EVT_SHIPMENT_RETURNED, "RETURNED"),
):

    def _bind_dispatch_handler(
        shipment_status: str = _shipment_status,
    ) -> Callable[[dict[str, Any]], Any]:
        channel_status = channel_services.shipment_status_to_channel_status(shipment_status)

        async def _handler(envelope: dict[str, Any]) -> None:
            await dispatch_shipment_transitioned(envelope, channel_status=channel_status)

        return _handler

    consumer.on(_event_name)(_bind_dispatch_handler())


def _approval_target(envelope: dict[str, Any]) -> tuple[uuid.UUID, str, uuid.UUID] | None:
    """`(organization, request_type, source_ref)` of an approval event that is ours — a request
    type this service opened — or `None`. Every request type approval routes shares one event
    type, so the binding cannot be narrower than the routing key and the filtering is here.

    The organization is the **envelope's**, and a payload that disagrees with it is refused,
    never trusted: the envelope's column is what the producer's row-level security enforced; the
    payload is JSON nothing vouches for (the guard `finance_invoice_issued` documents too)."""
    payload = envelope.get("payload") or {}
    request_type = payload.get("request_type")
    if request_type not in (
        movement_common.REQUEST_TYPE_TRANSFER,
        movement_common.REQUEST_TYPE_ADJUSTMENT,
    ):
        return None
    envelope_org = envelope.get("organization_id")
    payload_org = payload.get("organization_id")
    if envelope_org is None or payload_org is None or str(envelope_org) != str(payload_org):
        log.error(
            "movements.approval.organization_mismatch",
            event_id=envelope.get("event_id"),
            envelope_organization_id=envelope_org,
            payload_organization_id=payload_org,
        )
        return None
    return uuid.UUID(str(envelope_org)), str(request_type), uuid.UUID(str(payload["source_ref"]))


async def approval_request_opened(envelope: dict[str, Any]) -> None:
    """`core.approval.request.opened` — the approval service has routed one of our requests:
    remember its id and who it is waiting on, so the transfer or adjustment can say so."""
    target = _approval_target(envelope)
    if target is None:
        return
    organization_id, request_type, source_ref = target
    event_id = uuid.UUID(envelope["event_id"])
    payload = envelope["payload"]
    approval_request_id = uuid.UUID(str(payload["request_id"]))
    approver_name = payload.get("approver_name")

    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("movements.approval_opened.duplicate", event_id=str(event_id))
            return
        if request_type == movement_common.REQUEST_TYPE_TRANSFER:
            recorded = await transfers.record_approval_opened(
                session,
                organization_id,
                source_ref,
                approval_request_id=approval_request_id,
                approver_name=approver_name,
            )
        else:
            recorded = await adjustments.record_approval_opened(
                session,
                organization_id,
                source_ref,
                approval_request_id=approval_request_id,
                approver_name=approver_name,
            )
        mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])
    log.info(
        "movements.approval_opened.handled",
        event_id=str(event_id),
        request_type=request_type,
        recorded=recorded,
    )


consumer.on(movement_common.EVT_APPROVAL_OPENED)(approval_request_opened)


async def approval_request_decided(envelope: dict[str, Any]) -> None:
    """`core.approval.request.decided` — apply the approval service's decision to the transfer or
    adjustment it was asked about, in this service's own transaction and under its own state
    machine (ADR-0006 §1: the product applies the outcome). A decision for something no longer
    waiting (cancelled meanwhile, or a redelivery) changes nothing."""
    target = _approval_target(envelope)
    if target is None:
        return
    organization_id, request_type, source_ref = target
    event_id = uuid.UUID(envelope["event_id"])
    payload = envelope["payload"]
    approval_request_id = uuid.UUID(str(payload["request_id"]))
    outcome = str(payload["outcome"])
    decided_by_name = payload.get("decided_by_name")
    comment = payload.get("comment")

    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("movements.approval_decided.duplicate", event_id=str(event_id))
            return
        if request_type == movement_common.REQUEST_TYPE_TRANSFER:
            applied = await transfers.apply_approval_decision(
                session,
                organization_id,
                source_ref,
                outcome=outcome,
                approval_request_id=approval_request_id,
                decided_by_name=decided_by_name,
                comment=comment,
            )
        else:
            applied = await adjustments.apply_approval_decision(
                session,
                organization_id,
                source_ref,
                outcome=outcome,
                approval_request_id=approval_request_id,
                decided_by_name=decided_by_name,
                comment=comment,
            )
        mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])
    log.info(
        "movements.approval_decided.handled",
        event_id=str(event_id),
        request_type=request_type,
        outcome=outcome,
        applied=applied,
    )


consumer.on(movement_common.EVT_APPROVAL_DECIDED)(approval_request_decided)


async def main() -> None:
    cfg = settings()
    engine = init_engine(cfg)
    # Consumers run NOBYPASSRLS: the per-message tenant GUC only means something if
    # row-level security can bind this role. Started on the admin or outbox DSN by
    # mistake, RLS would be silently inert — refuse outside local (ADR-0002).
    await assert_app_role(engine, environment=cfg.environment)
    await consumer.run()


if __name__ == "__main__":
    asyncio.run(main())
