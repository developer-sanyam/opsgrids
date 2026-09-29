"""Online payment for an order (ADR-0024 A4 — `services/payment`'s tenant collections; Fulfilment
F10), from the order's side.

Two halves:

* **The paid fact** — `payment.collections.payment.captured` is the only way an online payment
  sets `Order.paid_at`, the fact `check_billing_gate` reads. Proven end to end through the real
  counter hand-over: an unpaid prepaid order is refused, the verified capture event lets it through
  exactly once (a redelivery changes nothing), a full refund makes it unpaid again, and a refund
  that then fails restores it. A capture for the wrong amount, another tenant, or something that is
  not an order is refused visibly; an operator's cash attestation is never overwritten or reversed.
* **The routes** — every one refuses a caller without its permission, 404s another organization's
  order, sends payment the order's own stored total (never anything from the request), and none of
  them can mark an order paid: a body claiming "paid" is ignored.

payment's own process cannot be imported here (every service exposes a top-level `app`), so the
routes talk to a recording double of `CollectionsClient`; payment's side of the same contract is
proven in `services/payment/tests/test_collections_*.py`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app import consumers, domain
from app.config import settings
from app.main import app
from app.models import AuditLog, FulfilmentSettings, ProcessedEvent
from app.orders import selectors as order_selectors
from app.orders.payment_client import (
    CollectionsUnavailable,
    HttpCollectionsClient,
    ProviderRefused,
    get_collections_client,
)
from platform_core.db import dispose_engine, init_engine
from platform_core.errors import ConflictProblem, NotFoundProblem
from tests.fulfilment_fixtures import Floor, accept_order, build_floor, ready_order
from tests.helpers import as_principal, principal_for

pytestmark = pytest.mark.integration

READ = "operations.orders.order.read:ORG"
COLLECT = "operations.orders.payment.collect:ORG"
REFUND = "operations.orders.payment.refund:ORG"
HAND_OVER = "operations.orders.order.hand_over:ORG"
CAPTURED = "payment.collections.payment.captured"
REFUNDED = "payment.collections.payment.refunded"


@pytest.fixture
async def floor(session, org_id) -> Floor:  # type: ignore[no-untyped-def]
    return await build_floor(session, org_id, stock=100)


@pytest.fixture(autouse=True)
async def _engine():  # type: ignore[no-untyped-def]
    """The consumers open their own `session_scope`, independent of the fixture session."""
    init_engine(settings())
    yield
    await dispose_engine()


async def _prepaid_settings(session, org_id: uuid.UUID) -> None:  # type: ignore[no-untyped-def]
    """A prepaid manual channel, and no invoice needed by handover — so the payment condition is
    the one the billing gate is left asking about."""
    async with session.begin():
        session.add(
            FulfilmentSettings(
                organization_id=org_id,
                warehouse_id=None,
                invoice_timing="on_delivery",
                payment_defaults={"manual": "prepaid"},
                offer_timeout_seconds=300,
                offer_rounds=3,
                require_pickup_code=False,
            )
        )


@dataclass(frozen=True)
class Facts:
    """An order's payment facts, read inside a transaction (the session expires on commit)."""

    id: uuid.UUID
    version: int
    total_minor: int
    paid_at: datetime | None
    paid_via_charge_id: uuid.UUID | None


async def _order(session, org_id: uuid.UUID, order_id: uuid.UUID) -> Facts:  # type: ignore[no-untyped-def]
    async with session.begin():
        row = await order_selectors.get_order(session, org_id, order_id, for_update=True)
        assert row is not None
        return Facts(row.id, row.version, row.total_minor, row.paid_at, row.paid_via_charge_id)


def _envelope(
    event_type: str,
    *,
    org_id: uuid.UUID,
    order_id: uuid.UUID,
    charge_id: uuid.UUID,
    amount_minor: int,
    currency: str = "INR",
    payload_org: uuid.UUID | None = None,
    reference_type: str = "operations.order",
    **extra: Any,
) -> dict[str, Any]:
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "organization_id": str(org_id),
        "payload": {
            "organization_id": str(payload_org or org_id),
            "charge_id": str(charge_id),
            "reference_type": reference_type,
            "reference_id": str(order_id),
            "amount_minor": amount_minor,
            "currency": currency,
            "provider": "razorpay",
            "provider_payment_id": "pay_1",
            "provider_order_id": "order_1",
            "state": "captured",
            "occurred_at": datetime.now(UTC).isoformat(),
            **extra,
        },
    }


def _refunded(**kw: Any) -> dict[str, Any]:
    fully = kw.pop("fully_refunded")
    total = kw.pop("refunded_total_minor")
    return _envelope(
        REFUNDED,
        refund_id=str(uuid.uuid4()),
        provider_refund_id="rfnd_1",
        refund_amount_minor=total or 1,
        refund_state="processed",
        refunded_total_minor=total,
        fully_refunded=fully,
        **kw,
    )


async def _hand_over(client, org_id: uuid.UUID, order: Facts):  # type: ignore[no-untyped-def]
    with as_principal(principal_for(org_id, [HAND_OVER], subject_ref=uuid.uuid4())):
        return await client.post(
            f"/api/v1/operations/orders/{order.id}/hand-over",
            json={"version": order.version, "payment_confirmed": False},
        )


class TestThePaidFact:
    async def test_only_a_verified_capture_lets_an_unpaid_prepaid_order_through_the_gate(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _prepaid_settings(session, org_id)
        order_id = await ready_order(client, org_id, session, floor, qty="2")
        order = await _order(session, org_id, order_id)
        refused = await _hand_over(client, org_id, order)
        assert refused.status_code == 409 and "has not been paid" in refused.text

        charge_id = uuid.uuid4()
        captured = _envelope(
            CAPTURED,
            org_id=org_id,
            order_id=order_id,
            charge_id=charge_id,
            amount_minor=order.total_minor,
        )
        await consumers.payment_collection_captured(captured)
        await consumers.payment_collection_captured(captured)  # a redelivery: nothing more
        paid = await _order(session, org_id, order_id)
        assert paid.paid_at is not None and paid.paid_via_charge_id == charge_id
        assert paid.version == order.version + 1

        # The same capture under a fresh event id is still one fact.
        await consumers.payment_collection_captured(
            _envelope(
                CAPTURED,
                org_id=org_id,
                order_id=order_id,
                charge_id=charge_id,
                amount_minor=order.total_minor,
            )
        )
        again = await _order(session, org_id, order_id)
        assert again.version == paid.version

        handed = await _hand_over(client, org_id, again)
        assert handed.status_code == 200, handed.text
        assert handed.json()["status"] == "DELIVERED"
        async with session.begin():
            actions = (
                (
                    await session.execute(
                        select(AuditLog.action).where(
                            AuditLog.organization_id == org_id, AuditLog.entity_id == order_id
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert actions.count("order.paid_online") == 1

    async def test_a_full_refund_makes_it_unpaid_again_and_a_failed_refund_restores_it(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _prepaid_settings(session, org_id)
        order_id = await ready_order(client, org_id, session, floor, qty="1")
        order = await _order(session, org_id, order_id)
        charge_id = uuid.uuid4()
        common = {"org_id": org_id, "order_id": order_id, "charge_id": charge_id}
        await consumers.payment_collection_captured(
            _envelope(CAPTURED, amount_minor=order.total_minor, **common)
        )
        await consumers.payment_collection_refunded(
            _refunded(
                amount_minor=order.total_minor,
                fully_refunded=False,
                refunded_total_minor=100,
                **common,
            )
        )
        assert (await _order(session, org_id, order_id)).paid_at is not None  # partial: still paid

        await consumers.payment_collection_refunded(
            _refunded(
                amount_minor=order.total_minor,
                fully_refunded=True,
                refunded_total_minor=order.total_minor,
                **common,
            )
        )
        reversed_ = await _order(session, org_id, order_id)
        assert reversed_.paid_at is None and reversed_.paid_via_charge_id == charge_id
        refused = await _hand_over(client, org_id, reversed_)
        assert refused.status_code == 409 and "has not been paid" in refused.text

        await consumers.payment_collection_refunded(
            _refunded(
                amount_minor=order.total_minor,
                fully_refunded=False,
                refunded_total_minor=0,
                **common,
            )
        )
        assert (await _order(session, org_id, order_id)).paid_at is not None

    async def test_a_capture_for_another_amount_never_marks_it_paid(
        self, org_id, session, floor: Floor
    ) -> None:
        order = await accept_order(
            session,
            org_id,
            floor.shop,
            lines=[{"inventory_item_id": floor.shop.widget, "quantity": "1"}],
        )
        await consumers.payment_collection_captured(
            _envelope(
                CAPTURED, org_id=org_id, order_id=order.id, charge_id=uuid.uuid4(), amount_minor=1
            )
        )
        await consumers.payment_collection_captured(
            _envelope(
                CAPTURED,
                org_id=org_id,
                order_id=order.id,
                charge_id=uuid.uuid4(),
                amount_minor=order.total_minor,
                currency="USD",
            )
        )
        after = await _order(session, org_id, order.id)
        assert after.paid_at is None
        async with session.begin():
            mismatches = (
                (
                    await session.execute(
                        select(AuditLog).where(
                            AuditLog.organization_id == org_id,
                            AuditLog.action == "order.online_payment_mismatch",
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(mismatches) == 2

    async def test_an_operator_attested_payment_is_never_overwritten_or_reversed(
        self, org_id, session, floor: Floor
    ) -> None:
        order = await accept_order(
            session,
            org_id,
            floor.shop,
            lines=[{"inventory_item_id": floor.shop.widget, "quantity": "1"}],
        )
        attested_at = datetime(2026, 9, 1, tzinfo=UTC)
        async with session.begin():
            row = await order_selectors.get_order(session, org_id, order.id, for_update=True)
            assert row is not None
            row.paid_at = attested_at
        charge_id = uuid.uuid4()
        common = {"org_id": org_id, "order_id": order.id, "charge_id": charge_id}
        await consumers.payment_collection_captured(
            _envelope(CAPTURED, amount_minor=order.total_minor, **common)
        )
        await consumers.payment_collection_refunded(
            _refunded(
                amount_minor=order.total_minor,
                fully_refunded=True,
                refunded_total_minor=order.total_minor,
                **common,
            )
        )
        after = await _order(session, org_id, order.id)
        assert after.paid_at == attested_at and after.paid_via_charge_id is None

    @pytest.mark.parametrize(
        "variant", ["other_tenants_order", "payload_org_mismatch", "not_an_order"]
    )
    async def test_a_capture_that_is_not_this_tenants_order_changes_nothing(
        self, org_id, session, floor: Floor, variant: str
    ) -> None:
        org_b = uuid.uuid4()
        order = await accept_order(
            session,
            org_id,
            floor.shop,
            lines=[{"inventory_item_id": floor.shop.widget, "quantity": "1"}],
        )
        kwargs: dict[str, Any] = {
            "order_id": order.id,
            "charge_id": uuid.uuid4(),
            "amount_minor": order.total_minor,
        }
        if variant == "other_tenants_order":
            envelope = _envelope(CAPTURED, org_id=org_b, **kwargs)
        elif variant == "payload_org_mismatch":
            envelope = _envelope(CAPTURED, org_id=org_b, payload_org=org_id, **kwargs)
        else:
            envelope = _envelope(
                CAPTURED, org_id=org_id, reference_type="storefront.cart", **kwargs
            )
        await consumers.payment_collection_captured(envelope)
        assert (await _order(session, org_id, order.id)).paid_at is None
        async with session.begin():
            processed = await session.get(ProcessedEvent, uuid.UUID(envelope["event_id"]))
        # Handled (so it is never redelivered into a loop), and nothing else.
        assert processed is not None


class FakeCollections:
    """A recording `CollectionsClient`: what operations sent payment, and a canned answer."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.configured = True

    def _collection(self, order_id: uuid.UUID, state: str = "created") -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        return {
            "id": str(uuid.uuid4()),
            "reference_type": "operations.order",
            "reference_id": str(order_id),
            "state": state,
            "amount_minor": 1,
            "currency": "INR",
            "provider": "razorpay",
            "provider_order_id": "order_1",
            "provider_payment_id": None,
            "failure_reason": None,
            "refunded_minor": 0,
            "refunds": [],
            "history": [],
            "created_at": now,
            "updated_at": now,
        }

    async def list_for_order(self, organization_id, order_id):  # type: ignore[no-untyped-def]
        self.calls.append(("list", {"org": organization_id, "order": order_id}))
        return {
            "merchant_account_configured": self.configured,
            "mode": "test" if self.configured else None,
            "wire": "stub",
            "collections": [self._collection(order_id)],
        }

    async def checkout(self, organization_id, order_id, *, amount_minor, currency):  # type: ignore[no-untyped-def]
        self.calls.append(
            (
                "checkout",
                {
                    "org": organization_id,
                    "order": order_id,
                    "amount": amount_minor,
                    "currency": currency,
                },
            )
        )
        return {
            "collection": self._collection(order_id),
            "checkout": {
                "provider": "razorpay",
                "key_id": "rzp_test_Merchant0001",
                "provider_order_id": "order_1",
                "amount_minor": amount_minor,
                "currency": currency,
                "mode": "test",
                "openable": False,
            },
        }

    async def sync(self, organization_id, order_id, charge_id):  # type: ignore[no-untyped-def]
        self.calls.append(
            ("sync", {"org": organization_id, "order": order_id, "charge": charge_id})
        )
        return self._collection(order_id)

    async def cancel(self, organization_id, order_id, charge_id, *, actor_id):  # type: ignore[no-untyped-def]
        self.calls.append(
            ("cancel", {"org": organization_id, "order": order_id, "actor": actor_id})
        )
        return self._collection(order_id, "cancelled")

    async def refund(
        self,
        organization_id,
        order_id,
        charge_id,
        *,
        amount_minor,
        reason,
        idempotency_key,
        actor_id,
    ):  # type: ignore[no-untyped-def]
        self.calls.append(
            (
                "refund",
                {
                    "org": organization_id,
                    "amount": amount_minor,
                    "key": idempotency_key,
                    "actor": actor_id,
                },
            )
        )
        return self._collection(order_id, "captured")


@pytest.fixture
def fake_collections(client) -> FakeCollections:  # type: ignore[no-untyped-def]
    fake = FakeCollections()
    app.dependency_overrides[get_collections_client] = lambda: fake
    return fake


def _base(order_id: uuid.UUID) -> str:
    return f"/api/v1/operations/orders/{order_id}/payments"


class TestRoutes:
    async def test_checkout_sends_the_orders_own_total_and_marks_nothing_paid(
        self, client, org_id, session, floor: Floor, fake_collections: FakeCollections
    ) -> None:
        order = await accept_order(
            session,
            org_id,
            floor.shop,
            lines=[{"inventory_item_id": floor.shop.widget, "quantity": "3"}],
        )
        with as_principal(principal_for(org_id, [COLLECT])):
            response = await client.post(
                f"{_base(order.id)}/checkout",
                # Ignored: the amount is always the order's stored total.
                json={"amount_minor": 1, "currency": "USD", "paid": True},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["checkout"]["key_id"] == "rzp_test_Merchant0001"
        assert body["checkout"]["prefill"] == {"name": "Asha Verma", "contact": "9999900000"}
        (call,) = [c for c in fake_collections.calls if c[0] == "checkout"]
        assert call[1]["amount"] == order.total_minor and call[1]["currency"] == "INR"
        assert call[1]["order"] == order.id and call[1]["org"] == org_id
        after = await _order(session, org_id, order.id)
        assert after.paid_at is None
        async with session.begin():
            opened = (
                (
                    await session.execute(
                        select(AuditLog.action).where(
                            AuditLog.entity_id == order.id,
                            AuditLog.action == "order.online_payment_started",
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(opened) == 1

    async def test_a_status_check_can_never_be_told_the_order_is_paid(
        self, client, org_id, session, floor: Floor, fake_collections: FakeCollections
    ) -> None:
        order = await accept_order(
            session,
            org_id,
            floor.shop,
            lines=[{"inventory_item_id": floor.shop.widget, "quantity": "1"}],
        )
        with as_principal(principal_for(org_id, [COLLECT])):
            response = await client.post(
                f"{_base(order.id)}/{uuid.uuid4()}/sync",
                json={"razorpay_payment_id": "pay_forged", "razorpay_signature": "x", "paid": True},
            )
        assert response.status_code == 200
        assert (await _order(session, org_id, order.id)).paid_at is None

    async def test_the_order_reports_whether_it_can_be_paid_online(
        self, client, org_id, session, floor: Floor, fake_collections: FakeCollections
    ) -> None:
        order = await accept_order(
            session,
            org_id,
            floor.shop,
            lines=[{"inventory_item_id": floor.shop.widget, "quantity": "1"}],
        )
        with as_principal(principal_for(org_id, [READ])):
            ok = (await client.get(_base(order.id))).json()
        assert ok["can_collect"] is True and ok["payment_term"] == "cash"
        assert ok["total_minor"] == order.total_minor and len(ok["collections"]) == 1
        fake_collections.configured = False
        with as_principal(principal_for(org_id, [READ])):
            off = (await client.get(_base(order.id))).json()
        assert off["can_collect"] is False and "not set up" in off["collect_blocked_reason"]

    async def test_a_credit_terms_order_is_not_paid_online(
        self, client, org_id, session, floor: Floor, fake_collections: FakeCollections
    ) -> None:
        async with session.begin():
            session.add(
                FulfilmentSettings(
                    organization_id=org_id,
                    warehouse_id=None,
                    invoice_timing="on_accept",
                    payment_defaults={"manual": "credit"},
                    offer_timeout_seconds=300,
                    offer_rounds=3,
                    require_pickup_code=False,
                )
            )
        order = await accept_order(
            session,
            org_id,
            floor.shop,
            lines=[{"inventory_item_id": floor.shop.widget, "quantity": "1"}],
        )
        with as_principal(principal_for(org_id, [COLLECT])):
            response = await client.post(f"{_base(order.id)}/checkout")
        assert response.status_code == 409 and "credit terms" in response.text
        assert not [c for c in fake_collections.calls if c[0] == "checkout"]

    async def test_a_refund_needs_an_idempotency_key_and_sends_it(
        self, client, org_id, session, floor: Floor, fake_collections: FakeCollections
    ) -> None:
        order = await accept_order(
            session,
            org_id,
            floor.shop,
            lines=[{"inventory_item_id": floor.shop.widget, "quantity": "1"}],
        )
        charge = uuid.uuid4()
        with as_principal(principal_for(org_id, [REFUND])):
            missing = await client.post(
                f"{_base(order.id)}/{charge}/refunds",
                json={"amount_minor": 100, "reason": "return"},
            )
            assert missing.status_code == 422
            done = await client.post(
                f"{_base(order.id)}/{charge}/refunds",
                json={"amount_minor": 100, "reason": "return"},
                headers={"Idempotency-Key": "refund-key-0001"},
            )
        assert done.status_code == 200, done.text
        (call,) = [c for c in fake_collections.calls if c[0] == "refund"]
        assert call[1]["key"] == "refund-key-0001" and call[1]["amount"] == 100

    @pytest.mark.parametrize(
        ("method", "suffix", "perms"),
        [
            ("GET", "", []),
            ("GET", "", [COLLECT]),
            ("POST", "/checkout", [READ]),
            ("POST", "/checkout", ["operations.orders.payment.collect:OWN"]),
            ("POST", "/{charge}/sync", [READ]),
            ("POST", "/{charge}/cancel", [READ]),
            ("POST", "/{charge}/refunds", [COLLECT]),
            ("POST", "/{charge}/refunds", [READ, COLLECT]),
        ],
    )
    async def test_every_route_refuses_a_caller_without_its_permission(
        self,
        client,
        org_id,
        session,
        floor: Floor,
        fake_collections: FakeCollections,
        method: str,
        suffix: str,
        perms: list[str],
    ) -> None:
        order = await accept_order(
            session,
            org_id,
            floor.shop,
            lines=[{"inventory_item_id": floor.shop.widget, "quantity": "1"}],
        )
        path = _base(order.id) + suffix.replace("{charge}", str(uuid.uuid4()))
        body = {"amount_minor": 1, "reason": "x"} if suffix.endswith("refunds") else None
        with as_principal(principal_for(org_id, perms)):
            response = await client.request(
                method, path, json=body, headers={"Idempotency-Key": "refund-key-0002"}
            )
        assert response.status_code == 403, response.text
        assert fake_collections.calls == []

    @pytest.mark.parametrize(
        ("method", "suffix", "perms"),
        [
            ("GET", "", [READ]),
            ("POST", "/checkout", [COLLECT]),
            ("POST", "/{charge}/sync", [COLLECT]),
            ("POST", "/{charge}/cancel", [COLLECT]),
            ("POST", "/{charge}/refunds", [REFUND]),
        ],
    )
    async def test_another_organizations_order_is_a_404(
        self,
        client,
        org_id,
        session,
        floor: Floor,
        fake_collections: FakeCollections,
        method: str,
        suffix: str,
        perms: list[str],
    ) -> None:
        org_b = uuid.uuid4()
        order = await accept_order(
            session,
            org_id,
            floor.shop,
            lines=[{"inventory_item_id": floor.shop.widget, "quantity": "1"}],
        )
        path = _base(order.id) + suffix.replace("{charge}", str(uuid.uuid4()))
        body = {"amount_minor": 1, "reason": "x"} if suffix.endswith("refunds") else None
        with as_principal(principal_for(org_b, perms)):
            response = await client.request(
                method, path, json=body, headers={"Idempotency-Key": "refund-key-0003"}
            )
        assert response.status_code == 404, response.text
        assert fake_collections.calls == []


class TestPaymentClient:
    """The HTTP client's mapping of payment's answers — status and payment's own sentence only."""

    def _client(self, status: int, body: dict[str, Any] | None = None) -> HttpCollectionsClient:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["X-Internal-Token"]
            return httpx.Response(status, json=body or {})

        client = HttpCollectionsClient("http://payment.test", settings(), timeout=1.0)
        transport = httpx.MockTransport(handler)
        original = httpx.AsyncClient

        def _patched(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            kwargs["transport"] = transport
            return original(*args, **kwargs)

        self._patch = _patched
        return client

    @pytest.mark.parametrize(
        ("status", "raises"),
        [
            (409, ConflictProblem),
            (404, NotFoundProblem),
            (502, ProviderRefused),
            (503, CollectionsUnavailable),
            (403, CollectionsUnavailable),
        ],
    )
    async def test_payment_refusals_map_to_problems(
        self, monkeypatch: pytest.MonkeyPatch, status: int, raises: type[Exception]
    ) -> None:
        client = self._client(status, {"detail": "This order has already been paid online"})
        monkeypatch.setattr(httpx, "AsyncClient", self._patch)
        with pytest.raises(raises) as raised:
            await client.sync(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
        if status == 409:
            assert "already been paid online" in str(raised.value)

    async def test_an_unreachable_payment_service_is_a_503(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        original = httpx.AsyncClient
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda *a, **k: original(*a, **{**k, "transport": httpx.MockTransport(handler)}),
        )
        client = HttpCollectionsClient("http://payment.test", settings(), timeout=1.0)
        with pytest.raises(CollectionsUnavailable):
            await client.list_for_order(uuid.uuid4(), uuid.uuid4())


@pytest.mark.parametrize(
    ("status", "total", "paid", "term", "blocked"),
    [
        ("ACCEPTED", 100, False, "prepaid", False),
        ("READY", 100, False, "cash", False),
        ("PLACED", 100, False, "cash", False),
        ("DRAFT", 100, False, "cash", True),
        ("DELIVERED", 100, False, "cash", True),
        ("CANCELLED", 100, False, "cash", True),
        ("ACCEPTED", 0, False, "cash", True),
        ("ACCEPTED", 100, True, "cash", True),
        ("ACCEPTED", 100, False, "credit", True),
    ],
)
def test_when_an_order_can_be_paid_online(
    status: str, total: int, paid: bool, term: str, blocked: bool
) -> None:
    reason = domain.online_payment_blocker(
        number="ORD-1", status=status, total_minor=total, paid=paid, payment_term=term
    )
    assert (reason is not None) is blocked
