"""Shared set-up for the tenant-collections tests (ADR-0024 §8).

Plain module, not conftest, for the reason `helpers.py` gives. Everything here drives the real
routes and the real Razorpay adapter; only the adapter's *wire* is the in-process stub, and the
"customer paying in Checkout" is a payment recorded on the stub's Order
(`RazorpayProvider.stub_record_payment`) — exactly the state a real Razorpay account would then
report to a status check, and the payload a real webhook would then carry.

The merchant's webhook secret is **not** the platform's: a body signed with the platform secret
must not verify for a merchant, which several tests rely on.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from httpx import AsyncClient

from app.config import settings
from app.main import app
from app.providers.base import PaymentSnapshot
from app.providers.cashfree_collections import CashfreeCollectionsProvider
from app.providers.cashfree_fake import CashfreeFakeWire
from app.providers.payu_collections import PayUCollectionsProvider
from app.providers.payu_fake import PayUFakeWire
from app.providers.phonepe_collections import PhonePeCollectionsProvider
from app.providers.phonepe_fake import PhonePeFakeWire
from app.providers.razorpay import RazorpayProvider
from app.providers.razorpay_collections import RazorpayCollectionsProvider
from platform_core.auth import Principal, get_principal
from platform_core.internal_auth import mint_service_token

MERCHANT_KEY_ID = "rzp_test_Merchant0001"
MERCHANT_KEY_SECRET = "merchant-key-secret-0001"  # pragma: allowlist secret
MERCHANT_WEBHOOK_SECRET = "merchant-webhook-secret-0001"  # pragma: allowlist secret

READ = "operations.payments.merchant_account.read:ORG"
WRITE = "operations.payments.merchant_account.write:ORG"
RECON = "operations.payments.reconciliation.read:ORG"

ACCOUNT = "/api/v1/payment/collections/merchant-account"
RECONCILIATION = "/api/v1/payment/collections/reconciliation"
WEBHOOK = "/api/v1/webhooks/razorpay/collections"
ORDER_REF = "operations.order"


@dataclass
class MerchantProviders:
    """The collections provider factory the app is given in tests: one stub adapter per merchant
    key id (so what a test records at "the provider" is still there on the next call), built with
    whatever webhook secret the service opened — so a signature check really is against the
    merchant's own sealed secret. Records every key secret it was handed, to prove the sealed key
    round-trips and never leaks anywhere else.

    A Cashfree account (ADR-0033) gets the real `CashfreeCollectionsProvider` over one stateful
    `CashfreeFakeWire` per App ID (`cashfree_wires`), the wire `COLLECTIONS_CASHFREE_WIRE=stub` runs
    on: what a test makes "the customer" do there (`wire.pay(...)`) is what a status check finds."""

    adapters: dict[tuple[str, str], RazorpayProvider] = field(default_factory=dict)
    key_secrets_seen: list[str] = field(default_factory=list)
    cashfree_wires: dict[str, CashfreeFakeWire] = field(default_factory=dict)
    #: `(provider, mode)` of every adapter built, in order.
    built: list[tuple[str, str]] = field(default_factory=list)
    #: Build Cashfree adapters as the live wire would (`is_live`), still over the fake: their
    #: Orders are recorded as made live (`order_wire = live`), i.e. ones a customer could pay.
    cashfree_live: bool = False
    #: One stateful `PhonePeFakeWire` per Client ID, as `COLLECTIONS_PHONEPE_WIRE=stub` runs.
    phonepe_wires: dict[str, PhonePeFakeWire] = field(default_factory=dict)
    #: One stateful `PayUFakeWire` per merchant key, as `COLLECTIONS_PAYU_WIRE=stub` runs.
    payu_wires: dict[str, PayUFakeWire] = field(default_factory=dict)
    #: The non-secret config of every adapter built, in order.
    configs: list[dict[str, Any]] = field(default_factory=list)

    def __call__(
        self,
        key_id: str,
        key_secret: str,
        webhook_secret: str,
        *,
        provider: str = "razorpay",
        mode: str = "test",
        config: Any = None,
    ) -> Any:
        if key_secret:
            self.key_secrets_seen.append(key_secret)
        self.built.append((provider, mode))
        self.configs.append(dict(config or {}))
        if provider == "payu":
            return PayUCollectionsProvider(
                wire=self.payu_wires.setdefault(key_id, PayUFakeWire()),
                key=key_id,
                salt=key_secret,
                mode="stub",
            )
        if provider == "phonepe":
            return PhonePeCollectionsProvider(
                wire=self.phonepe_wires.setdefault(key_id, PhonePeFakeWire()),
                mode="stub",
                webhook_username=str((config or {}).get("webhook_username") or ""),
                webhook_password=webhook_secret,
            )
        if provider == "cashfree":
            wire = self.cashfree_wires.setdefault(key_id, CashfreeFakeWire())
            live = "production" if mode == "live" else "sandbox"
            return CashfreeCollectionsProvider(
                wire=wire, secret_key=key_secret, mode=live if self.cashfree_live else "stub"
            )
        adapter = self.adapters.get((key_id, webhook_secret))
        if adapter is None:
            # Share the stub's account state across adapters for one key id — the same Razorpay
            # account, whatever secret this call was built with.
            existing = next((a for (k, _), a in self.adapters.items() if k == key_id), None)
            adapter = RazorpayCollectionsProvider(
                key_id=key_id, key_secret=key_secret or None, webhook_secret=webhook_secret
            )
            if existing is not None:
                adapter._orders_by_receipt = existing._orders_by_receipt
                adapter._payments_by_order = existing._payments_by_order
                adapter._stub_order_payments = existing._stub_order_payments
            self.adapters[(key_id, webhook_secret)] = adapter
        return adapter

    def stub(self, key_id: str = MERCHANT_KEY_ID) -> RazorpayProvider:
        return next(a for (k, _), a in self.adapters.items() if k == key_id)

    def pay(
        self,
        order_id: str,
        *,
        payment_id: str,
        status: str,
        amount_minor: int,
        currency: str = "INR",
        refunded_minor: int = 0,
        created_at: int = 1_700_000_100,
        error_code: str | None = None,
        key_id: str = MERCHANT_KEY_ID,
    ) -> None:
        """What a customer paying in Checkout leaves at the provider."""
        self.stub(key_id).stub_record_payment(
            order_id,
            PaymentSnapshot(
                provider_payment_id=payment_id,
                status=status,
                amount_minor=amount_minor,
                currency=currency,
                amount_refunded_minor=refunded_minor,
                error_code=error_code,
                created_at=created_at,
            ),
        )


def principal_for(
    org_id: uuid.UUID,
    perms: list[str],
    *,
    entitlements: list[str] | None = None,
    amr: list[str] | None = None,
) -> Principal:
    """A tenant admin. Signed in with a one-time code (`amr=["otp"]`) unless a test says
    otherwise: connecting, rotating, verifying and disconnecting need it (ADR-0033); a test of that
    rule passes `amr=["pwd"]`."""
    return Principal(
        sub=uuid.uuid4(),
        organization_id=org_id,
        membership_id=uuid.uuid4(),
        perms=perms,
        entitlements=entitlements if entitlements is not None else ["operations"],
        amr=amr if amr is not None else ["pwd", "otp"],
    )


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def operations_token(caller: str = "operations") -> dict[str, str]:
    return {"X-Internal-Token": mint_service_token(settings(), audience="payment", caller=caller)}


def internal_base(org_id: uuid.UUID, order_id: uuid.UUID) -> str:
    return f"/internal/v1/organizations/{org_id}/collections/{ORDER_REF}/{order_id}"


async def connect(client: AsyncClient, org_id: uuid.UUID, **over: Any) -> dict[str, Any]:
    """Connect the merchant's Razorpay account through the real admin route."""
    body = {
        "provider": "razorpay",
        "key_id": MERCHANT_KEY_ID,
        "key_secret": MERCHANT_KEY_SECRET,
        "webhook_secret": MERCHANT_WEBHOOK_SECRET,
        **over,
    }
    with as_principal(principal_for(org_id, [READ, WRITE])):
        response = await client.put(ACCOUNT, json=body)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


async def checkout(
    client: AsyncClient,
    org_id: uuid.UUID,
    order_id: uuid.UUID,
    *,
    amount_minor: int = 50_000,
    currency: str = "INR",
    caller: str = "operations",
) -> Any:
    return await client.post(
        f"{internal_base(org_id, order_id)}/checkout",
        headers=operations_token(caller),
        json={"amount_minor": amount_minor, "currency": currency},
    )


def merchant_signer() -> RazorpayProvider:
    return RazorpayProvider(key_id=None, key_secret=None, webhook_secret=MERCHANT_WEBHOOK_SECRET)


def payment_webhook(
    *,
    event: str,
    payment_id: str,
    order_id: str | None,
    amount_minor: int = 50_000,
    currency: str = "INR",
    created_at: int = 1_700_000_200,
    error_code: str | None = None,
    notes: dict[str, str] | None = None,
) -> bytes:
    """A real-shaped Razorpay merchant webhook — no top-level `id`, customer details on the
    payment entity (which the inbox must never keep)."""
    entity: dict[str, Any] = {
        "id": payment_id,
        "entity": "payment",
        "amount": amount_minor,
        "currency": currency,
        "status": event.split(".")[1] if event.startswith("payment.") else "captured",
        "order_id": order_id,
        "method": "upi",
        "email": "customer@example.com",
        "contact": "+919876543210",
        "vpa": "customer@okbank",
        "card": {"last4": "1111", "name": "A Customer"},
        "notes": notes if notes is not None else {"note": "set in the browser"},
    }
    if error_code is not None:
        entity["error_code"] = error_code
        entity["error_description"] = "Payment was declined by the bank"
    payload: dict[str, Any] = {"payment": {"entity": entity}}
    if event == "order.paid" and order_id:
        payload["order"] = {"entity": {"id": order_id, "entity": "order", "status": "paid"}}
    body = {
        "entity": "event",
        "account_id": "acc_merchant",
        "event": event,
        "contains": list(payload),
        "payload": payload,
        "created_at": created_at,
    }
    return json.dumps(body).encode()


def refund_webhook(
    *,
    event: str,
    refund_id: str,
    payment_id: str,
    amount_minor: int,
    currency: str = "INR",
    created_at: int = 1_700_000_300,
) -> bytes:
    status = {
        "refund.created": "pending",
        "refund.processed": "processed",
        "refund.failed": "failed",
    }[event]
    body = {
        "entity": "event",
        "account_id": "acc_merchant",
        "event": event,
        "contains": ["refund", "payment"],
        "payload": {
            "refund": {
                "entity": {
                    "id": refund_id,
                    "entity": "refund",
                    "payment_id": payment_id,
                    "amount": amount_minor,
                    "currency": currency,
                    "status": status,
                    "notes": {},
                }
            },
            "payment": {
                "entity": {
                    "id": payment_id,
                    "entity": "payment",
                    "amount": 50_000,
                    "currency": currency,
                    "status": "refunded",
                    "email": "customer@example.com",
                }
            },
        },
        "created_at": created_at,
    }
    return json.dumps(body).encode()


async def deliver(
    client: AsyncClient,
    account_id: str,
    body: bytes,
    *,
    event_id: str | None = None,
    signature: str | None = None,
    signer: RazorpayProvider | None = None,
) -> Any:
    headers = {"X-Razorpay-Signature": signature or (signer or merchant_signer()).sign(body)}
    if event_id is not None:
        headers["X-Razorpay-Event-Id"] = event_id
    return await client.post(f"{WEBHOOK}?account={account_id}", content=body, headers=headers)
