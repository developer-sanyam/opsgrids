"""Shared set-up for the platform-checkout tests (ADR-0032). A plain module, not conftest, for the
reason `helpers.py` gives.

The providers a test installs are the real adapters over stateful fakes — the live Razorpay adapter
over `FakeRazorpay`, the Cashfree adapter over `CashfreeFakeWire` — so what a checkout does at "the
provider" is observable (`fake.requests`, `fake.orders`) and what a customer does there is
`fake.pay(...)`. Nothing here opens a socket.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from decimal import Decimal
from typing import Any, Literal

from httpx import AsyncClient

from app.deps import get_provider
from app.main import app
from app.providers.base import PaymentProvider
from app.providers.cashfree import CashfreeProvider
from app.providers.cashfree_fake import CashfreeFakeWire
from app.providers.razorpay import RazorpayProvider
from app.providers.razorpay_wire import RazorpayHttpWire
from platform_core.auth import Principal
from tests.collections_support import as_principal, principal_for
from tests.helpers import seed_charge
from tests.razorpay_fake import KEY_ID, KEY_SECRET, FakeRazorpay

PERM = "core.product.activation.write:ORG"
READ_PERM = "core.product.activation.read:ORG"
CHECKOUTS = "/api/v1/payment/checkouts"
HOOK = "dev-only-checkout-webhook-secret"  # pragma: allowlist secret
CASHFREE_SECRET = "dev-only-checkout-cashfree-secret"  # pragma: allowlist secret
PHONE = "9876543210"
EMAIL = "pay.customer@example.com"
NAME = "Pay Customer"


def razorpay_live(fake: FakeRazorpay | None = None, *, key_id: str = KEY_ID) -> RazorpayProvider:
    """The platform's live Razorpay adapter over a stateful fake Razorpay. The fake's own key is a
    test-mode one (`rzp_test_…`); pass a `rzp_live_…` `key_id` for an adapter on live
    credentials (the fake is then built for that key)."""
    fake = fake or FakeRazorpay(key_id=key_id)
    provider = RazorpayProvider(
        key_id=key_id,
        key_secret=KEY_SECRET,
        webhook_secret=HOOK,
        wire=RazorpayHttpWire(key_id=key_id, key_secret=KEY_SECRET, transport=fake.transport()),
        platform_billing=True,
    )
    provider.fake = fake  # type: ignore[attr-defined]
    return provider


def razorpay_stub() -> RazorpayProvider:
    """The platform's Razorpay adapter with no wire — what `PAYMENT_WIRE=stub` builds, test keys
    or not."""
    return RazorpayProvider(
        key_id=KEY_ID, key_secret=KEY_SECRET, webhook_secret=HOOK, platform_billing=True
    )


def cashfree_stub(
    fake: CashfreeFakeWire | None = None,
    *,
    mode: Literal["stub", "sandbox", "production"] = "stub",
) -> CashfreeProvider:
    """The Cashfree adapter over its stateful fake — the wire `CASHFREE_ENV=stub` runs on. `mode`
    is what the adapter believes it runs on (`sandbox` / `production`), over the same fake."""
    fake = fake or CashfreeFakeWire()
    provider = CashfreeProvider(wire=fake, secret_key=CASHFREE_SECRET, mode=mode)
    provider.fake = fake  # type: ignore[attr-defined]
    return provider


def install(provider: PaymentProvider) -> PaymentProvider:
    """Make `provider` the configured default for the app under test (the `client` fixture's own
    override, replaced; it clears every override on teardown)."""
    app.dependency_overrides[get_provider] = lambda: provider
    return provider


def owner(
    org_id: uuid.UUID, perms: list[str] | None = None
) -> contextlib.AbstractContextManager[None]:
    """The organization's owner: `core.product.activation.write` at ORG, the gate every checkout
    route is behind."""
    return as_principal(principal_for(org_id, perms if perms is not None else [PERM]))


@contextlib.contextmanager
def acting(principal: Principal) -> Iterator[None]:
    with as_principal(principal):
        yield


def customer(phone: str | None = PHONE, **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"name": NAME, "email": EMAIL}
    if phone is not None:
        body["phone"] = phone
    body.update(over)
    return body


async def seed_invoice(
    client: AsyncClient,
    org_id: uuid.UUID,
    *,
    invoice_id: uuid.UUID | None = None,
    amount_minor: int = 49_900,
    currency: str = "INR",
    subscription_id: uuid.UUID | None = None,
    product_key: str | None = "hrms",
    key: str | None = None,
) -> tuple[uuid.UUID, dict[str, Any]]:
    """The invoice's own `debit`-flow charge, raised exactly as billing's event raises it. Under a
    live provider it is born `failed` as `no_mandate` — the realistic Phase A state of the charge a
    customer then pays by checkout."""
    invoice_id = invoice_id or uuid.uuid4()
    charge = await seed_charge(
        client,
        org_id,
        key=key or f"invoice:{invoice_id}",
        amount_minor=amount_minor,
        currency=currency,
        invoice_id=invoice_id,
        subscription_id=subscription_id or uuid.uuid4(),
        product_key=product_key,
    )
    return invoice_id, charge


async def open_checkout(
    client: AsyncClient,
    org_id: uuid.UUID,
    invoice_id: uuid.UUID,
    *,
    cust: dict[str, Any] | None = None,
    perms: list[str] | None = None,
) -> Any:
    body: dict[str, Any] = {"invoice_id": str(invoice_id)}
    if cust is not None:
        body["customer"] = cust
    with owner(org_id, perms):
        return await client.post(CHECKOUTS, json=body)


async def get_checkout(
    client: AsyncClient, org_id: uuid.UUID, charge_id: str | uuid.UUID, **kw: Any
) -> Any:
    with owner(org_id, kw.get("perms")):
        return await client.get(f"{CHECKOUTS}/{charge_id}")


async def confirm_checkout(
    client: AsyncClient,
    org_id: uuid.UUID,
    charge_id: str | uuid.UUID,
    body: dict[str, Any],
    **kw: Any,
) -> Any:
    with owner(org_id, kw.get("perms")):
        return await client.post(f"{CHECKOUTS}/{charge_id}/confirm", json=body)


def razorpay_pay(provider: Any, order_id: str, **kw: Any) -> str:
    """A customer paying in Razorpay's Checkout: the payment now exists at the provider."""
    payment_id: str = provider.fake.pay(order_id, **kw)
    return payment_id


def cashfree_pay(provider: Any, order_id: str, **kw: Any) -> int:
    payment_id: int = provider.fake.pay(order_id, **kw)
    return payment_id


def money(rupees: str) -> Decimal:
    return Decimal(rupees)
