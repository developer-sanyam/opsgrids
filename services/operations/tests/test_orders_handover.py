"""The counter/walk-in handover and ADR-0025 §1's billing gate (Fulfilment plan F4).

Drives a real order all the way to `READY` through the real F2/F3 write paths (accept, create
task, offer, accept offer, confirm pick — the same shape `test_fulfilment_pick_and_pack.py` uses),
then exercises `POST /orders/{id}/hand-over` — the plan's own exit criterion: a missing invoice,
an unpaid prepaid order and an unmatched pickup code are each refused with a 409 naming the
specific condition, one test per condition.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.inventory.reconcile import reconcile_organization
from app.models import AuditLog, FulfilmentSettings, InventoryBalance, Order, OrderTransition
from app.orders import selectors as order_selectors
from app.orders import services as order_services
from tests.fulfilment_fixtures import (
    Floor,
    accept_offer,
    accept_order,
    build_floor,
    confirm_pick,
    create_tasks,
    offer_task,
    ready_order,
)
from tests.helpers import as_principal, principal_for
from tests.order_fixtures import build_shop

pytestmark = pytest.mark.integration

READ_PERM = "operations.orders.order.read:ORG"
WRITE_PERM = "operations.orders.order.write:ORG"
ACCEPT_PERM = "operations.orders.order.accept:ORG"
CANCEL_PERM = "operations.orders.order.cancel:ORG"
HAND_OVER_PERM = "operations.orders.order.hand_over:ORG"
HAND_OVER_OWN_PERM = "operations.orders.order.hand_over:OWN"


@pytest.fixture
async def floor(session, org_id) -> Floor:
    return await build_floor(session, org_id, stock=100)


async def _seed_settings(
    session,
    org_id: uuid.UUID,
    *,
    invoice_timing: str = "on_accept",
    payment_defaults: dict[str, str] | None = None,
    require_pickup_code: bool = False,
    default_tax_rate_pct: Decimal = Decimal("0"),
) -> None:
    """No endpoint creates `fulfilment_settings` yet (F2/F3/F4 all leave it out of scope) — the
    same direct-model-insert shape `test_item_master.py` already uses to exercise a table no
    service function writes."""
    async with session.begin():
        session.add(
            FulfilmentSettings(
                organization_id=org_id,
                warehouse_id=None,
                invoice_timing=invoice_timing,
                payment_defaults=payment_defaults or {},
                offer_timeout_seconds=300,
                offer_rounds=3,
                require_pickup_code=require_pickup_code,
                default_tax_rate_pct=default_tax_rate_pct,
            )
        )


async def _ready_order(client, org_id, session, floor: Floor, *, qty: str = "10") -> uuid.UUID:
    return await ready_order(client, org_id, session, floor, qty=qty)


async def _reload(session, org_id: uuid.UUID, order_id: uuid.UUID) -> Order:
    async with session.begin():
        order = await order_selectors.get_order(session, org_id, order_id)
        assert order is not None
    return order


async def _hand_over(  # type: ignore[no-untyped-def]
    client,
    org_id,
    order_id: uuid.UUID,
    version: int,
    *,
    pickup_code: str | None = None,
    payment_confirmed: bool = False,
    perms: list[str] | None = None,
):
    body: dict[str, object] = {"version": version, "payment_confirmed": payment_confirmed}
    if pickup_code is not None:
        body["pickup_code"] = pickup_code
    principal = principal_for(
        org_id, perms if perms is not None else [HAND_OVER_PERM], subject_ref=uuid.uuid4()
    )
    with as_principal(principal):
        return await client.post(f"/api/v1/operations/orders/{order_id}/hand-over", json=body)


async def _hold(  # type: ignore[no-untyped-def]
    client, org_id, order_id: uuid.UUID, version: int, *, reason: str = "customer asked to wait"
):
    principal = principal_for(org_id, [WRITE_PERM], subject_ref=uuid.uuid4())
    with as_principal(principal):
        response = await client.post(
            f"/api/v1/operations/orders/{order_id}/hold",
            json={"version": version, "reason": reason},
        )
    assert response.status_code == 200, response.text
    return response.json()


async def _release(client, org_id, order_id: uuid.UUID, version: int):  # type: ignore[no-untyped-def]
    principal = principal_for(org_id, [WRITE_PERM], subject_ref=uuid.uuid4())
    with as_principal(principal):
        return await client.post(
            f"/api/v1/operations/orders/{order_id}/release", json={"version": version}
        )


class TestBillingGateRefusals:
    """The plan's own exit criterion: one test per missing condition, each naming it in the 409."""

    async def test_a_missing_invoice_is_refused(
        self, client, org_id, session, floor: Floor
    ) -> None:
        # invoice_timing="on_accept" (default) requires an invoice by handover; nothing ever set
        # `order.invoice_id` (this slice's own customer_id gap — no consumer call ran).
        await _seed_settings(
            session, org_id, invoice_timing="on_accept", payment_defaults={"manual": "credit"}
        )
        order_id = await _ready_order(client, org_id, session, floor)
        order = await _reload(session, org_id, order_id)

        response = await _hand_over(client, org_id, order_id, order.version)
        assert response.status_code == 409, response.text
        assert "invoice" in response.text.lower()

        after = await _reload(session, org_id, order_id)
        assert after.status == "READY"  # refused cleanly, nothing moved

    async def test_an_unpaid_prepaid_order_is_refused(
        self, client, org_id, session, floor: Floor
    ) -> None:
        # on_delivery keeps the invoice check out of the way; "manual" (the fixture's own
        # channel) is prepaid, which does need a recorded payment.
        await _seed_settings(
            session, org_id, invoice_timing="on_delivery", payment_defaults={"manual": "prepaid"}
        )
        order_id = await _ready_order(client, org_id, session, floor)
        order = await _reload(session, org_id, order_id)

        response = await _hand_over(
            client, org_id, order_id, order.version, payment_confirmed=False
        )
        assert response.status_code == 409, response.text
        assert "paid" in response.text.lower()

        after = await _reload(session, org_id, order_id)
        assert after.status == "READY"
        assert after.paid_at is None

    async def test_an_unmatched_pickup_code_is_refused(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _seed_settings(
            session,
            org_id,
            invoice_timing="on_delivery",
            payment_defaults={"manual": "credit"},
            require_pickup_code=True,
        )
        order_id = await _ready_order(client, org_id, session, floor)
        order = await _reload(session, org_id, order_id)
        assert order.pickup_code is not None  # issued at READY

        response = await _hand_over(client, org_id, order_id, order.version, pickup_code="000000")
        assert response.status_code == 409, response.text
        assert "pickup code" in response.text.lower()

        after = await _reload(session, org_id, order_id)
        assert after.status == "READY"
        assert after.pickup_code_verified_at is None

    async def test_an_omitted_pickup_code_is_also_refused(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _seed_settings(
            session,
            org_id,
            invoice_timing="on_delivery",
            payment_defaults={"manual": "credit"},
            require_pickup_code=True,
        )
        order_id = await _ready_order(client, org_id, session, floor)
        order = await _reload(session, org_id, order_id)

        response = await _hand_over(client, org_id, order_id, order.version)
        assert response.status_code == 409, response.text
        assert "pickup code" in response.text.lower()


class TestBillingGateSuccess:
    async def test_every_condition_met_hands_over_and_delivers_in_one_call(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _seed_settings(
            session,
            org_id,
            invoice_timing="on_delivery",  # no invoice needed at handover
            payment_defaults={"manual": "credit"},  # no payment needed at handover
            require_pickup_code=True,
        )
        order_id = await _ready_order(client, org_id, session, floor)
        order = await _reload(session, org_id, order_id)
        assert order.pickup_code is not None
        code = order.pickup_code
        # Captured as a plain int **before** the call: `client`'s `get_session` override hands
        # HTTP requests this same test session (`tests/conftest.py::client`), so `order` is the
        # identity-mapped object the route itself mutates in place — reading `order.version`
        # again after the call would silently observe the post-handover value, not the one this
        # request was sent with.
        ready_version = order.version

        response = await _hand_over(client, org_id, order_id, ready_version, pickup_code=code)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "DELIVERED"
        assert body["version"] == ready_version + 2  # HANDED_OVER, then DELIVERED
        assert body["pickup_code_verified_at"] is not None
        assert "pickup_code" not in body  # the secret itself is never returned

    async def test_a_credit_terms_order_needs_no_payment_confirmation(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _seed_settings(
            session, org_id, invoice_timing="on_delivery", payment_defaults={"manual": "credit"}
        )
        order_id = await _ready_order(client, org_id, session, floor)
        order = await _reload(session, org_id, order_id)

        response = await _hand_over(client, org_id, order_id, order.version)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "DELIVERED"

    async def test_a_prepaid_orders_operator_attestation_satisfies_the_gate(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _seed_settings(
            session, org_id, invoice_timing="on_delivery", payment_defaults={"manual": "prepaid"}
        )
        order_id = await _ready_order(client, org_id, session, floor)
        order = await _reload(session, org_id, order_id)

        response = await _hand_over(client, org_id, order_id, order.version, payment_confirmed=True)
        assert response.status_code == 200, response.text

        after = await _reload(session, org_id, order_id)
        assert after.paid_at is not None


class TestReservationGenuinelyConsumed:
    """Security review M2: `complete_handover` now posts the same reservation-consuming `issue`
    move on the counter path that the carrier path (F5) always posted — previously this path
    wrote zero ledger activity, so every counter-delivered order in production left its staging
    reservation `active` forever: stock both "on hand" and "reserved," permanently unavailable to
    any other order, and `reconcile_organization` never flagged it (nothing was internally
    inconsistent, just business-logic-leaked). This is the regression test proving that bug is
    actually fixed, not merely documented as fixed."""

    async def test_hand_over_consumes_the_staging_reservation(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _seed_settings(
            session, org_id, invoice_timing="on_delivery", payment_defaults={"manual": "credit"}
        )
        order_id = await _ready_order(client, org_id, session, floor, qty="10")
        order = await _reload(session, org_id, order_id)

        response = await _hand_over(client, org_id, order_id, order.version)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "DELIVERED"

        async with session.begin():
            staging_qty, staging_reserved = (
                await session.execute(
                    select(InventoryBalance.quantity_milli, InventoryBalance.reserved_milli).where(
                        InventoryBalance.organization_id == org_id,
                        InventoryBalance.inventory_item_id == floor.shop.widget,
                        InventoryBalance.location_id == floor.staging_location_id,
                        InventoryBalance.status == "AVAILABLE",
                    )
                )
            ).one()
        # Before the fix: (0, 10_000) — issued off the shelf by the pick, but still "reserved"
        # forever because nothing ever posted the consuming move. After: genuinely issued out.
        assert (staging_qty, staging_reserved) == (0, 0)

        async with session.begin():
            report = await reconcile_organization(session, org_id)
        assert report.findings == [], [
            (f.kind, f.stored_milli, f.expected_milli) for f in report.findings
        ]


class TestTransitionsAppendOnly:
    async def test_handover_writes_two_transition_rows_in_one_call(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _seed_settings(
            session, org_id, invoice_timing="on_delivery", payment_defaults={"manual": "credit"}
        )
        order_id = await _ready_order(client, org_id, session, floor)
        order = await _reload(session, org_id, order_id)

        response = await _hand_over(client, org_id, order_id, order.version)
        assert response.status_code == 200, response.text

        async with session.begin():
            rows = (
                (
                    await session.execute(
                        select(OrderTransition)
                        .where(
                            OrderTransition.organization_id == org_id,
                            OrderTransition.order_id == order_id,
                        )
                        .order_by(OrderTransition.at.asc())
                    )
                )
                .scalars()
                .all()
            )
        assert [(r.from_status, r.to_status) for r in rows[-2:]] == [
            ("READY", "HANDED_OVER"),
            ("HANDED_OVER", "DELIVERED"),
        ]


class TestAuthzAndTenancy:
    async def test_a_caller_without_hand_over_is_forbidden(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _seed_settings(
            session, org_id, invoice_timing="on_delivery", payment_defaults={"manual": "credit"}
        )
        order_id = await _ready_order(client, org_id, session, floor)
        order = await _reload(session, org_id, order_id)

        response = await _hand_over(client, org_id, order_id, order.version, perms=[READ_PERM])
        assert response.status_code == 403

    async def test_an_own_scope_grant_is_forbidden(
        self, client, org_id, session, floor: Floor
    ) -> None:
        """MINOR 8 (security review): `hand_over` has no meaningful "own" reading (nobody's own
        counter transaction is more theirs than another operator's), so the route pins
        `minimum=Scope.ORG` (`app/api/routes.py::OrderHandOver`) — an `OWN` grant of the same
        permission must still be refused, not silently treated as sufficient."""
        await _seed_settings(
            session, org_id, invoice_timing="on_delivery", payment_defaults={"manual": "credit"}
        )
        order_id = await _ready_order(client, org_id, session, floor)
        order = await _reload(session, org_id, order_id)

        response = await _hand_over(
            client, org_id, order_id, order.version, perms=[HAND_OVER_OWN_PERM]
        )
        assert response.status_code == 403

    async def test_write_accept_cancel_without_hand_over_is_forbidden(
        self, client, org_id, session, floor: Floor
    ) -> None:
        """MINOR 8 (security review): every other order-write permission except `hand_over`
        itself must not be enough — each order action is its own permission, not a bundle."""
        await _seed_settings(
            session, org_id, invoice_timing="on_delivery", payment_defaults={"manual": "credit"}
        )
        order_id = await _ready_order(client, org_id, session, floor)
        order = await _reload(session, org_id, order_id)

        response = await _hand_over(
            client,
            org_id,
            order_id,
            order.version,
            perms=[WRITE_PERM, ACCEPT_PERM, CANCEL_PERM],
        )
        assert response.status_code == 403

    async def test_cross_tenant_handover_is_not_found(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _seed_settings(
            session, org_id, invoice_timing="on_delivery", payment_defaults={"manual": "credit"}
        )
        order_id = await _ready_order(client, org_id, session, floor)
        order = await _reload(session, org_id, order_id)

        other_org = uuid.uuid4()
        principal = principal_for(other_org, [HAND_OVER_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/orders/{order_id}/hand-over",
                json={"version": order.version, "payment_confirmed": False},
            )
        assert response.status_code == 404

    async def test_a_stale_version_is_conflict(self, client, org_id, session, floor: Floor) -> None:
        await _seed_settings(
            session, org_id, invoice_timing="on_delivery", payment_defaults={"manual": "credit"}
        )
        order_id = await _ready_order(client, org_id, session, floor)
        order = await _reload(session, org_id, order_id)

        response = await _hand_over(client, org_id, order_id, order.version + 5)
        assert response.status_code == 409

    async def test_handing_over_a_non_ready_order_is_conflict(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _seed_settings(
            session, org_id, invoice_timing="on_delivery", payment_defaults={"manual": "credit"}
        )
        order = await accept_order(
            session,
            org_id,
            floor.shop,
            lines=[{"inventory_item_id": floor.shop.widget, "quantity": "10"}],
        )  # ACCEPTED, never sent through pick/pack — not READY

        response = await _hand_over(client, org_id, order.id, order.version)
        assert response.status_code == 409


class TestDoubleHandoverReplay:
    async def test_a_second_hand_over_on_an_already_delivered_order_is_a_clean_409(
        self, client, org_id, session, floor: Floor
    ) -> None:
        """MINOR 8 (security review): a retried hand-over call (the client's own connection
        dropped after the first actually succeeded, say) must land on the same clean 409
        `can_transition_order` already gives any other terminal-status retry — never an
        unhandled exception."""
        await _seed_settings(
            session, org_id, invoice_timing="on_delivery", payment_defaults={"manual": "credit"}
        )
        order_id = await _ready_order(client, org_id, session, floor)
        order = await _reload(session, org_id, order_id)

        first = await _hand_over(client, org_id, order_id, order.version)
        assert first.status_code == 200, first.text
        delivered_version = first.json()["version"]

        second = await _hand_over(client, org_id, order_id, delivered_version)
        assert second.status_code == 409, second.text

        after = await _reload(session, org_id, order_id)
        assert after.status == "DELIVERED"  # unaffected by the replay


class TestGateCheckIsAtomic:
    async def test_a_correct_pickup_code_alongside_a_different_failing_gate_is_not_applied(
        self, client, org_id, session, floor: Floor
    ) -> None:
        """MINOR 8 (security review): `check_billing_gate` raising for one failing condition must
        roll back every field this call wrote, including a *correct* pickup-code match — the gate
        is checked atomically, not field by field."""
        await _seed_settings(
            session,
            org_id,
            invoice_timing="on_delivery",
            payment_defaults={"manual": "prepaid"},  # unpaid, and payment_confirmed is not sent
            require_pickup_code=True,
        )
        order_id = await _ready_order(client, org_id, session, floor)
        order = await _reload(session, org_id, order_id)
        assert order.pickup_code is not None
        code = order.pickup_code

        response = await _hand_over(
            client, org_id, order_id, order.version, pickup_code=code, payment_confirmed=False
        )
        assert response.status_code == 409, response.text
        assert "paid" in response.text.lower()

        after = await _reload(session, org_id, order_id)
        assert after.status == "READY"
        assert after.pickup_code_verified_at is None  # the correct-code match never stuck
        assert after.paid_at is None


class TestPickupCodeCharset:
    async def test_a_non_ascii_pickup_code_is_a_clean_422(
        self, client, org_id, session, floor: Floor
    ) -> None:
        """MINOR 3 (security review): `secrets.compare_digest(pickup_code, order.pickup_code)`
        raises `TypeError` on a non-ASCII operand; the schema now refuses the shape (the code is
        always 6 ASCII digits, `_generate_pickup_code`) before the domain layer ever sees it."""
        await _seed_settings(
            session,
            org_id,
            invoice_timing="on_delivery",
            payment_defaults={"manual": "credit"},
            require_pickup_code=True,
        )
        order_id = await _ready_order(client, org_id, session, floor)
        order = await _reload(session, org_id, order_id)

        response = await _hand_over(client, org_id, order_id, order.version, pickup_code="12345é")
        assert response.status_code == 422, response.text

        after = await _reload(session, org_id, order_id)
        assert after.status == "READY"  # rejected before it ever reached the domain layer


class TestPaymentAttestationAudit:
    async def test_operator_attested_payment_is_marked_in_the_audit_row(
        self, client, org_id, session, floor: Floor
    ) -> None:
        """MINOR 5 (security review): the audit row for a call that itself set `paid_at` must say
        so — `"paid": true` alone cannot tell this call's own attestation from a fact recorded
        earlier."""
        await _seed_settings(
            session, org_id, invoice_timing="on_delivery", payment_defaults={"manual": "prepaid"}
        )
        order_id = await _ready_order(client, org_id, session, floor)
        order = await _reload(session, org_id, order_id)

        response = await _hand_over(client, org_id, order_id, order.version, payment_confirmed=True)
        assert response.status_code == 200, response.text

        async with session.begin():
            row = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.organization_id == org_id,
                        AuditLog.action == "order.handed_over",
                        AuditLog.entity_id == order_id,
                    )
                )
            ).scalar_one()
        assert row.after["paid"] is True
        assert row.after["payment_attested_by_operator"] is True

    async def test_a_previously_recorded_payment_is_not_marked_as_this_calls_attestation(
        self, client, org_id, session, floor: Floor
    ) -> None:
        """The other half: when `paid_at` was already set before this call runs (payment recorded
        earlier), this call attested nothing, so the marker must be absent even though
        `payment_confirmed=True` is sent again."""
        await _seed_settings(
            session, org_id, invoice_timing="on_delivery", payment_defaults={"manual": "prepaid"}
        )
        order_id = await _ready_order(client, org_id, session, floor)

        async with session.begin():
            locked = await order_selectors.get_order(session, org_id, order_id, for_update=True)
            assert locked is not None
            locked.paid_at = datetime.now(UTC)  # simulates a payment fact recorded earlier

        order = await _reload(session, org_id, order_id)
        response = await _hand_over(client, org_id, order_id, order.version, payment_confirmed=True)
        assert response.status_code == 200, response.text

        async with session.begin():
            row = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.organization_id == org_id,
                        AuditLog.action == "order.handed_over",
                        AuditLog.entity_id == order_id,
                    )
                )
            ).scalar_one()
        assert row.after["paid"] is True
        assert "payment_attested_by_operator" not in row.after


class TestHoldReleaseTriggersReadyFacts:
    """MAJOR 1 (security review): `release_hold_order` reaching `READY` must run the exact same
    READY-triggered facts `_maybe_ready_order` runs on the ordinary `IN_FULFILMENT -> READY`
    path (`app.orders.services.handle_order_ready`) — a pickup code, if this org requires one,
    and the invoice request (or its visibly-logged skip audit row) — not silently skip them.
    Without this fix a `require_pickup_code=True` order released from a hold keeps
    `pickup_code=None` forever, and every hand-over then 409s permanently with no way out except
    cancelling."""

    async def test_release_to_ready_assigns_pickup_code_and_writes_invoice_skip_audit(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _seed_settings(
            session,
            org_id,
            invoice_timing="on_delivery",
            payment_defaults={"manual": "credit"},
            require_pickup_code=True,
        )
        order = await accept_order(
            session,
            org_id,
            floor.shop,
            lines=[{"inventory_item_id": floor.shop.widget, "quantity": "10"}],
        )
        tasks = await create_tasks(client, org_id, order)
        task = tasks[0]
        offer = await offer_task(client, org_id, task["id"], floor.worker_id)
        await accept_offer(client, org_id, offer["id"], floor.worker_employee_id)

        order_after_offer = await _reload(session, org_id, order.id)
        held = await _hold(client, org_id, order.id, order_after_offer.version)
        assert held["status"] == "ON_HOLD"

        line_id = task["lines"][0]["id"]
        picked = await confirm_pick(client, org_id, task["id"], line_id, floor.worker_employee_id)
        assert picked["status"] == "PICKED"

        before_release = await _reload(session, org_id, order.id)
        assert before_release.status == "ON_HOLD"  # a task settling alone can't move a held order
        assert before_release.pickup_code is None

        response = await _release(client, org_id, order.id, before_release.version)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "READY"

        after = await _reload(session, org_id, order.id)
        assert after.status == "READY"
        assert after.pickup_code is not None  # MAJOR 1: left `None` forever before the fix
        assert len(after.pickup_code) == 6
        assert after.pickup_code.isdigit()

        async with session.begin():
            rows = (
                (
                    await session.execute(
                        select(AuditLog).where(
                            AuditLog.organization_id == org_id,
                            AuditLog.action == "order.invoice_request_skipped",
                            AuditLog.entity_id == order.id,
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 1  # the same skip-audit trail the ordinary READY path writes


async def test_record_invoice_issued_is_idempotent_at_the_domain_not_only_the_event_id(
    org_id, session
) -> None:
    """`record_invoice_issued`'s own docstring: idempotent **twice over**. The event-id half
    (`ProcessedEvent`, the plan's own "duplicate invoice_requested" exit criterion) is proven on
    genuinely new consumer code in `tests/test_orders_invoice_issued_consumer.py`; this is the
    other half — two *different* deliveries, naming two different invoice ids, for an order that
    already has one, still leave only the first standing. An invoice is immutable once issued
    (ADR-0000 §9)."""
    shop = await build_shop(session, org_id)
    order = await accept_order(
        session, org_id, shop, lines=[{"inventory_item_id": shop.widget, "quantity": "1"}]
    )

    ctx = order_services.service_ctx(org_id)
    first_invoice = uuid.uuid4()
    second_invoice = uuid.uuid4()
    async with session.begin():
        recorded_first = await order_services.record_invoice_issued(
            session, ctx, order_id=order.id, invoice_id=first_invoice, invoice_number="INV-1"
        )
    async with session.begin():
        recorded_second = await order_services.record_invoice_issued(
            session, ctx, order_id=order.id, invoice_id=second_invoice, invoice_number="INV-2"
        )
    assert recorded_first is True
    assert recorded_second is False

    after = await _reload(session, org_id, order.id)
    assert after.invoice_id == first_invoice

    async with session.begin():
        rows = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.organization_id == org_id,
                        AuditLog.action == "order.invoice_recorded",
                        AuditLog.entity_id == order.id,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
