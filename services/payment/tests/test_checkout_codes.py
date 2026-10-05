"""The machine codes `POST /payment/checkouts` refuses with — the contract the dashboard's Pay now
(frontend PR #107) branches on. Every refusal carries `errors[0].code`; a 409 is read as "paid"
**only** when the code is `invoice_already_paid`, so each other 409 must carry a code of its own,
and each 422 a `{field, code}` the page can act on (a phone prompt on `customer.phone`).

One test per code, each driving the real route into the real refusal. The sentences are pinned too:
a client may still be matching them while it moves to the codes.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.checkout import services as checkout_services
from app.providers.base import CheckoutRequest, CheckoutSession, ProviderCapabilities
from app.providers.razorpay import RazorpayProvider
from app.providers.wire import ProviderRejected
from tests import checkout_support as cs
from tests.helpers import force_charge, rows

pytestmark = pytest.mark.integration


def _error(response: Any) -> dict[str, Any]:
    """The one coded error of a refusal, after checking the problem's shape."""
    body = response.json()
    assert response.headers["content-type"].startswith("application/problem+json")
    (error,) = body["errors"]
    assert error["message"] == body["detail"]
    return dict(error)


async def _checkouts(admin: AsyncSession, org: uuid.UUID) -> list[dict[str, Any]]:
    return await rows(
        admin,
        "SELECT id, state, provider_order_id FROM payment.charges "
        "WHERE organization_id = :org AND flow = 'checkout' ORDER BY created_at",
        org=org,
    )


class _Scripted:
    """The live Razorpay adapter with no money rules declared and `ensure_checkout` refusing with a
    provider code: how a refusal the claim could not foresee reaches the route."""

    def __init__(self, code: str) -> None:
        self.inner = cs.razorpay_live()
        self.name = self.inner.name
        self.request_timeouts_seconds = self.inner.request_timeouts_seconds
        self.capabilities = ProviderCapabilities(supports_checkout=True)
        self._code = code

    def ensure_checkout(self, request: CheckoutRequest) -> CheckoutSession:
        raise ProviderRejected("razorpay", 0, self._code)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


class TestTheConflicts:
    async def test_an_invoice_already_paid_is_invoice_already_paid(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        cs.install(cs.razorpay_live())
        invoice_id, debit = await cs.seed_invoice(client, org_id)
        await force_charge(admin_session, debit["id"], state="captured", failure_reason=None)

        response = await cs.open_checkout(client, org_id, invoice_id)

        assert response.status_code == 409
        assert _error(response) == {
            "field": "invoice_id",
            "code": "invoice_already_paid",
            "message": "This invoice has already been paid",
        }

    async def test_one_paid_meanwhile_on_its_checkout_is_invoice_already_paid_too(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Paid at the provider, learned only when the reopen asks: the same coded answer."""
        provider = cs.install(cs.razorpay_live())
        invoice_id, _ = await cs.seed_invoice(client, org_id)
        first = (await cs.open_checkout(client, org_id, invoice_id)).json()
        cs.razorpay_pay(provider, first["client_action"]["order_id"], status="captured")
        await force_charge(
            admin_session,
            first["charge_id"],
            checkout_expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
        response = await cs.open_checkout(client, org_id, invoice_id)
        assert response.status_code == 409
        assert _error(response)["code"] == "invoice_already_paid"

    async def test_a_call_in_flight_is_checkout_in_progress(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        cs.install(cs.razorpay_live())
        invoice_id, _ = await cs.seed_invoice(client, org_id)
        first = (await cs.open_checkout(client, org_id, invoice_id)).json()
        await force_charge(
            admin_session, first["charge_id"], lease_until=datetime.now(UTC) + timedelta(minutes=5)
        )
        response = await cs.open_checkout(client, org_id, invoice_id)
        assert response.status_code == 409
        assert _error(response) == {
            "code": "checkout_in_progress",
            "message": "This invoice's checkout is being prepared; try again in a moment",
        }

    async def test_an_expired_checkout_the_provider_cannot_be_asked_about_is_not_closable(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        provider = cs.install(cs.razorpay_live())
        invoice_id, _ = await cs.seed_invoice(client, org_id)
        first = (await cs.open_checkout(client, org_id, invoice_id)).json()
        await force_charge(
            admin_session,
            first["charge_id"],
            checkout_expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
        provider.fake.inject(httpx.ConnectError("down"))  # type: ignore[attr-defined]

        response = await cs.open_checkout(client, org_id, invoice_id)

        assert response.status_code == 409
        assert _error(response) == {
            "code": "previous_checkout_not_closable",
            "message": (
                "The previous checkout for this invoice could not be closed yet; try again shortly"
            ),
        }
        assert len(await _checkouts(admin_session, org_id)) == 1

    async def test_an_expired_checkout_still_being_paid_is_not_closable(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        provider = cs.install(cs.razorpay_live())
        invoice_id, _ = await cs.seed_invoice(client, org_id)
        first = (await cs.open_checkout(client, org_id, invoice_id)).json()
        cs.razorpay_pay(provider, first["client_action"]["order_id"], status="authorized")
        await force_charge(
            admin_session,
            first["charge_id"],
            checkout_expires_at=datetime.now(UTC) - timedelta(seconds=5),
        )
        response = await cs.open_checkout(client, org_id, invoice_id)
        assert response.status_code == 409
        assert _error(response)["code"] == "previous_checkout_not_closable"

    async def test_a_closed_checkout_with_a_payment_in_flight_is_its_own_code(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        provider = cs.install(cs.razorpay_live())
        invoice_id, _ = await cs.seed_invoice(client, org_id)
        first = (await cs.open_checkout(client, org_id, invoice_id)).json()
        await force_charge(
            admin_session,
            first["charge_id"],
            checkout_expires_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        closed = await cs.get_checkout(client, org_id, first["charge_id"])
        assert closed.json()["failure_reason"] == "checkout_expired"
        cs.razorpay_pay(provider, first["client_action"]["order_id"], status="authorized")

        response = await cs.open_checkout(client, org_id, invoice_id)

        assert response.status_code == 409
        assert _error(response) == {
            "code": "previous_checkout_payment_in_flight",
            "message": (
                "A payment on this invoice's previous checkout is still being processed; "
                "try again in a few minutes"
            ),
        }

    async def test_a_provider_with_no_checkout_is_checkout_not_supported(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        invoice_id, _ = await cs.seed_invoice(client, org_id)

        class NoCheckout:
            """An adapter that declares no capabilities: the legacy defaults, no hosted checkout."""

            name = "razorpay"
            request_timeouts_seconds = (7.0,)

        cs.install(NoCheckout())  # type: ignore[arg-type]
        response = await cs.open_checkout(client, org_id, invoice_id)
        assert response.status_code == 409
        assert _error(response) == {
            "code": "checkout_not_supported",
            "message": "Online payment is not available with the configured provider",
        }
        assert await _checkouts(admin_session, org_id) == []

    async def test_an_order_the_charge_does_not_record_is_withheld_as_checkout_in_progress(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The call came back with another Order than the charge records (two exist for one charge
        — Razorpay accepts a duplicate receipt). The browser is never handed it; the lease is given
        back, so the very next request is handed the recorded Order."""
        live = cs.razorpay_live()
        cs.install(live)
        invoice_id, _ = await cs.seed_invoice(client, org_id)
        first = (await cs.open_checkout(client, org_id, invoice_id)).json()
        recorded = first["client_action"]["order_id"]
        live.fake.index_new_orders = False  # type: ignore[attr-defined]
        live.fake.unindexed.add(recorded)  # type: ignore[attr-defined]

        class IgnoresTheRecord:
            """Makes a fresh Order whatever the charge records (the defect being guarded)."""

            name = live.name
            capabilities = live.capabilities
            request_timeouts_seconds = live.request_timeouts_seconds

            def ensure_checkout(self, request: CheckoutRequest) -> CheckoutSession:
                return live.ensure_checkout(replace(request, recorded_order_id=None))

            def __getattr__(self, name: str) -> Any:
                return getattr(live, name)

        cs.install(IgnoresTheRecord())  # type: ignore[arg-type]
        withheld = await cs.open_checkout(client, org_id, invoice_id)

        assert withheld.status_code == 409, withheld.text
        assert _error(withheld)["code"] == "checkout_in_progress"
        (row,) = await _checkouts(admin_session, org_id)
        assert row["provider_order_id"] == recorded
        duplicates = [o for o in live.fake.orders if o != recorded]  # type: ignore[attr-defined]
        assert len(duplicates) == 1 and duplicates[0] not in withheld.text

        cs.install(live)
        again = await cs.open_checkout(client, org_id, invoice_id)
        assert again.status_code == 201, again.text
        assert again.json()["client_action"]["order_id"] == recorded


class TestTheRefusals:
    async def test_nothing_owed_is_nothing_to_pay(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        invoice_id, _ = await cs.seed_invoice(client, org_id, amount_minor=0)
        provider = cs.install(cs.razorpay_live())
        response = await cs.open_checkout(client, org_id, invoice_id)
        assert response.status_code == 422, response.text
        assert _error(response) == {
            "field": "invoice_id",
            "code": "nothing_to_pay",
            "message": "There is nothing to pay on this invoice",
        }
        assert provider.fake.requests == []  # type: ignore[attr-defined]

    async def test_a_currency_that_cannot_be_paid_online_is_currency_unsupported(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        invoice_id, _ = await cs.seed_invoice(client, org_id, currency="USD")
        provider = cs.install(cs.razorpay_live())
        response = await cs.open_checkout(client, org_id, invoice_id)
        assert response.status_code == 422
        assert _error(response) == {
            "field": "invoice_id",
            "code": "currency_unsupported",
            "message": "Only INR invoices can be paid online",
        }
        assert provider.fake.requests == []  # type: ignore[attr-defined]

    async def test_an_amount_below_the_minimum_is_amount_below_minimum(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        invoice_id, _ = await cs.seed_invoice(client, org_id, amount_minor=99)
        provider = cs.install(cs.razorpay_live())
        response = await cs.open_checkout(client, org_id, invoice_id)
        assert response.status_code == 422
        assert _error(response) == {
            "field": "invoice_id",
            "code": "amount_below_minimum",
            "message": "The amount is below the minimum that can be paid online (Rs 1)",
        }
        assert provider.fake.requests == []  # type: ignore[attr-defined]

    @pytest.mark.parametrize("phone", [None, "12345"])
    async def test_no_usable_phone_where_one_is_needed_is_phone_required(
        self, client: AsyncClient, org_id: uuid.UUID, phone: str | None
    ) -> None:
        provider = cs.install(cs.cashfree_stub())
        invoice_id, _ = await cs.seed_invoice(client, org_id)
        response = await cs.open_checkout(client, org_id, invoice_id, cust=cs.customer(phone=phone))
        assert response.status_code == 422
        assert _error(response) == {
            "field": "customer.phone",
            "code": "phone_required",
            "message": "Add a customer phone number to pay online",
        }
        assert provider.fake.requests == []  # type: ignore[attr-defined]

    @pytest.mark.parametrize(
        ("provider_code", "code", "field"),
        [
            ("customer_details_required", "customer_details_required", "customer"),
            ("customer_phone_required", "phone_required", "customer.phone"),
            ("unsupported_currency", "currency_unsupported", "invoice_id"),
            ("amount_below_minimum", "amount_below_minimum", "invoice_id"),
        ],
    )
    async def test_a_providers_own_refusal_of_the_request_carries_the_apis_code(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        provider_code: str,
        code: str,
        field: str,
    ) -> None:
        """A refusal the claim could not foresee (the adapter's own rule) is the same coded 422,
        in the API's vocabulary, never the adapter's — and the lease is given back."""
        invoice_id, _ = await cs.seed_invoice(client, org_id)
        cs.install(_Scripted(provider_code))  # type: ignore[arg-type]
        response = await cs.open_checkout(client, org_id, invoice_id)
        assert response.status_code == 422, response.text
        error = _error(response)
        assert (error["code"], error["field"]) == (code, field)
        (row,) = await rows(
            admin_session,
            "SELECT lease_until FROM payment.charges WHERE organization_id = :org "
            "AND flow = 'checkout'",
            org=org_id,
        )
        assert row["lease_until"] is None

    async def test_a_malformed_body_keeps_the_frameworks_field_errors(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        with cs.owner(org_id):
            response = await client.post(cs.CHECKOUTS, json={"invoice_id": "not-a-uuid"})
        assert response.status_code == 422
        (error,) = response.json()["errors"]
        assert error["field"] == "invoice_id" and error["code"]


def test_every_refusal_code_is_distinct_and_every_provider_code_maps_to_one() -> None:
    codes = [
        checkout_services.CODE_INVOICE_ALREADY_PAID,
        checkout_services.CODE_CHECKOUT_IN_PROGRESS,
        checkout_services.CODE_PREVIOUS_CHECKOUT_NOT_CLOSABLE,
        checkout_services.CODE_PREVIOUS_PAYMENT_IN_FLIGHT,
        checkout_services.CODE_CHECKOUT_NOT_SUPPORTED,
        *checkout_services._REFUSALS,
    ]
    assert len(codes) == len(set(codes))
    assert set(checkout_services._PROVIDER_REQUEST_CODES.values()) <= set(
        checkout_services._REFUSALS
    )
    for provider_code, code in checkout_services._PROVIDER_REQUEST_CODES.items():
        problem = checkout_services._classify_for_request(
            ProviderRejected("razorpay", 0, provider_code)
        )
        assert problem.status_code == 422 and problem.errors is not None
        assert problem.errors[0]["code"] == code
    other = checkout_services._classify_for_request(
        ProviderRejected("razorpay", 400, "BAD_REQUEST_ERROR")
    )
    assert other.status_code == 502 and not other.errors


def test_the_stub_is_still_a_checkout_provider_that_never_has_strays() -> None:
    stub = RazorpayProvider(key_id=None, key_secret=None, webhook_secret="x")
    assert stub.stray_order_payments(receipt="r", recorded_order_id="order_x") == []
