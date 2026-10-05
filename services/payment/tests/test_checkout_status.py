"""`GET /payment/checkouts/{id}` and `POST /payment/checkouts/{id}/confirm` — how a checkout learns
what happened (ADR-0029, Phase A).

**The browser is never evidence.** Every state change below comes from the provider's own answer to
`order_payments` (or a signed webhook, `test_platform_webhook_phase_a.py`) and goes through the
path a signed webhook takes: the amount and currency checks, the Order check, the provider-id
binding, the state machine, the audit row, the `commerce.payment.captured` event. A checkout ends
`captured` or `failed` as `checkout_expired` — a failed *attempt* is the customer retrying, and
never moves it.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.providers.wire import CREDENTIALS
from tests import checkout_support as cs
from tests.helpers import audit_rows, emitted, rows

pytestmark = pytest.mark.integration


async def _open(
    client: AsyncClient, org_id: uuid.UUID, *, provider: Any | None = None, **seed: Any
) -> tuple[Any, uuid.UUID, dict[str, Any]]:
    """Install a live Razorpay (or the given provider), seed an invoice and open its checkout."""
    provider = cs.install(provider or cs.razorpay_live())
    invoice_id, _ = await cs.seed_invoice(client, org_id, **seed)
    response = await cs.open_checkout(client, org_id, invoice_id, cust=cs.customer())
    assert response.status_code == 201, response.text
    return provider, invoice_id, response.json()


async def _row(admin: AsyncSession, charge_id: str) -> dict[str, Any]:
    found = await rows(
        admin,
        "SELECT state, failure_reason, provider_payment_id, provider_order_id, subscription_id, "
        "lease_until, next_attempt_at FROM payment.charges WHERE id = :id",
        id=uuid.UUID(charge_id),
    )
    return found[0]


async def _captured_events(admin: AsyncSession, org: uuid.UUID) -> list[dict[str, Any]]:
    return await rows(
        admin,
        "SELECT event_type, payload FROM payment.outbox WHERE organization_id = :org "
        "AND event_type = 'commerce.payment.captured'",
        org=org,
    )


class TestReadThrough:
    async def test_an_unpaid_checkout_is_created_and_the_provider_was_asked(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        provider, _, out = await _open(client, org_id)
        response = await cs.get_checkout(client, org_id, out["charge_id"])
        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "created" and body["paid"] is False
        assert body["provider_checked"] is True and body["provider"] == "razorpay"
        assert (body["amount_minor"], body["currency"]) == (49_900, "INR")
        assert provider.fake.methods()[-1].startswith("GET /v1/orders/")

    async def test_a_payment_the_provider_reports_captured_captures_the_checkout_once(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        provider, invoice_id, out = await _open(client, org_id)
        paid = cs.razorpay_pay(provider, out["client_action"]["order_id"], status="captured")

        body = (await cs.get_checkout(client, org_id, out["charge_id"])).json()

        assert body["state"] == "captured" and body["paid"] is True
        row = await _row(admin_session, out["charge_id"])
        assert row["state"] == "captured" and row["provider_payment_id"] == paid
        (event,) = await _captured_events(admin_session, org_id)
        payload = event["payload"]
        assert payload["charge_id"] == out["charge_id"]
        assert payload["invoice_id"] == str(invoice_id)
        assert payload["subscription_id"] == str(row["subscription_id"])  # what recovers it
        assert (payload["amount_minor"], payload["currency"]) == (49_900, "INR")
        assert payload["provider"] == "razorpay" and payload["provider_payment_id"] == paid
        actions = [
            (a["action"], a["acting_as"])
            for a in await audit_rows(admin_session, org_id)
            if str(a["entity_id"]) == out["charge_id"]
        ]
        assert ("payment.charge.captured", "razorpay-sync") in actions

        # Asking again is idempotent: no second event, and a captured checkout asks nobody.
        calls = len(provider.fake.requests)
        again = (await cs.get_checkout(client, org_id, out["charge_id"])).json()
        assert again["state"] == "captured" and again["provider_checked"] is False
        assert len(provider.fake.requests) == calls
        assert len(await _captured_events(admin_session, org_id)) == 1

    async def test_a_failed_attempt_is_the_customer_retrying_not_the_charge_failing(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """A wrong OTP is a failed payment on an Order that can still be paid. If it failed the
        charge it would be `commerce.payment.failed` — dunning, suspension — for a customer in the
        middle of paying; and binding the failed attempt's id would turn the attempt that then
        succeeds into a `provider_id_conflict`."""
        provider, _, out = await _open(client, org_id)
        order_id = out["client_action"]["order_id"]
        before = await emitted(admin_session, org_id)

        cs.razorpay_pay(provider, order_id, status="failed", error_code="BAD_REQUEST_ERROR")
        failed = (await cs.get_checkout(client, org_id, out["charge_id"])).json()
        assert failed["state"] == "created" and failed["paid"] is False
        row = await _row(admin_session, out["charge_id"])
        assert row["provider_payment_id"] is None and row["failure_reason"] is None
        assert await emitted(admin_session, org_id) == before

        good = cs.razorpay_pay(provider, order_id, status="captured")
        body = (await cs.get_checkout(client, org_id, out["charge_id"])).json()
        assert body["state"] == "captured"
        assert (await _row(admin_session, out["charge_id"]))["provider_payment_id"] == good

    async def test_an_authorization_alone_is_not_money_yet(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        provider, _, out = await _open(client, org_id)
        cs.razorpay_pay(provider, out["client_action"]["order_id"], status="authorized")
        body = (await cs.get_checkout(client, org_id, out["charge_id"])).json()
        assert body["state"] == "created" and body["paid"] is False
        assert (await _row(admin_session, out["charge_id"]))["provider_payment_id"] is None

    async def test_a_payment_for_another_amount_is_kept_and_flagged_not_applied(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        provider, _, out = await _open(client, org_id)
        cs.razorpay_pay(provider, out["client_action"]["order_id"], status="captured", amount=100)
        body = (await cs.get_checkout(client, org_id, out["charge_id"])).json()
        assert body["state"] == "created" and body["paid"] is False
        assert (await _captured_events(admin_session, org_id)) == []
        flagged = [
            a
            for a in await audit_rows(admin_session, org_id)
            if a["action"] == "payment.charge.sync_not_applied"
        ]
        assert len(flagged) == 1 and flagged[0]["after"]["reason"] == "amount_mismatch"

    async def test_an_unreachable_provider_leaves_the_state_and_says_it_was_not_asked(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        provider, _, out = await _open(client, org_id)
        provider.fake.inject(httpx.ConnectError("down"))
        body = (await cs.get_checkout(client, org_id, out["charge_id"])).json()
        assert body["state"] == "created" and body["provider_checked"] is False
        assert (await _row(admin_session, out["charge_id"]))["state"] == "created"

    async def test_rejected_credentials_trip_the_latch_and_nothing_is_failed(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        provider, _, out = await _open(client, org_id)
        provider.fake.inject(httpx.Response(401, json={"error": {"code": "BAD_REQUEST_ERROR"}}))
        body = (await cs.get_checkout(client, org_id, out["charge_id"])).json()
        assert body["provider_checked"] is False and CREDENTIALS.tripped("razorpay")
        assert (await _row(admin_session, out["charge_id"]))["state"] == "created"

    async def test_a_cashfree_checkout_is_read_through_the_same_way(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        provider, _, out = await _open(client, org_id, provider=cs.cashfree_stub())
        # An abandoned attempt asserts nothing; the success captures.
        provider.fake.pay(out["charge_id"], status="USER_DROPPED")
        provider.fake.pay(out["charge_id"], status="FAILED", error_code="card_declined")
        assert (await cs.get_checkout(client, org_id, out["charge_id"])).json()[
            "state"
        ] == "created"
        paid = cs.cashfree_pay(provider, out["charge_id"], status="SUCCESS")
        body = (await cs.get_checkout(client, org_id, out["charge_id"])).json()
        assert body["state"] == "captured" and body["provider"] == "cashfree"
        assert (await _row(admin_session, out["charge_id"]))["provider_payment_id"] == str(paid)


class TestReadAuthorization:
    async def test_no_token_is_a_401(self, client: AsyncClient) -> None:
        assert (await client.get(f"{cs.CHECKOUTS}/{uuid.uuid4()}")).status_code == 401

    @pytest.mark.parametrize("perms", [[], [cs.READ_PERM], ["core.product.activation.write:OWN"]])
    async def test_a_caller_without_the_owner_permission_is_a_403(
        self, client: AsyncClient, org_id: uuid.UUID, perms: list[str]
    ) -> None:
        provider, _, out = await _open(client, org_id)
        calls = len(provider.fake.requests)
        response = await cs.get_checkout(client, org_id, out["charge_id"], perms=perms)
        assert response.status_code == 403
        assert len(provider.fake.requests) == calls

    async def test_another_organizations_checkout_is_a_404_and_asks_nobody(
        self, client: AsyncClient, org_id: uuid.UUID, org_b: uuid.UUID
    ) -> None:
        provider, _, out = await _open(client, org_id)
        calls = len(provider.fake.requests)
        assert (await cs.get_checkout(client, org_b, out["charge_id"])).status_code == 404
        assert len(provider.fake.requests) == calls

    async def test_a_debit_charge_and_an_unknown_id_are_the_same_404(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        cs.install(cs.razorpay_live())
        _, debit = await cs.seed_invoice(client, org_id)
        assert (await cs.get_checkout(client, org_id, debit["id"])).status_code == 404
        assert (await cs.get_checkout(client, org_id, uuid.uuid4())).status_code == 404

    async def test_a_malformed_id_is_a_422(self, client: AsyncClient, org_id: uuid.UUID) -> None:
        with cs.owner(org_id):
            assert (await client.get(f"{cs.CHECKOUTS}/not-a-uuid")).status_code == 422


class TestConfirm:
    def _body(self, provider: Any, out: dict[str, Any], payment_id: str) -> dict[str, str]:
        order_id = out["client_action"]["order_id"]
        return {
            "razorpay_order_id": order_id,
            "razorpay_payment_id": payment_id,
            "razorpay_signature": provider.sign_checkout(order_id, payment_id),
        }

    async def test_a_verified_callback_is_only_a_precondition_the_fetch_is_the_evidence(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        provider, _, out = await _open(client, org_id)
        paid = cs.razorpay_pay(provider, out["client_action"]["order_id"], status="captured")
        response = await cs.confirm_checkout(
            client, org_id, out["charge_id"], self._body(provider, out, paid)
        )
        assert response.status_code == 200, response.text
        assert response.json()["paid"] is True and response.json()["state"] == "captured"
        assert len(await _captured_events(admin_session, org_id)) == 1

    async def test_a_valid_signature_with_nothing_paid_is_not_success(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The browser says "paid". The provider says nothing was. The browser is wrong."""
        provider, _, out = await _open(client, org_id)
        response = await cs.confirm_checkout(
            client, org_id, out["charge_id"], self._body(provider, out, "pay_made_up")
        )
        assert response.status_code == 200
        assert response.json()["paid"] is False and response.json()["state"] == "created"
        assert (await _row(admin_session, out["charge_id"]))["state"] == "created"
        assert await _captured_events(admin_session, org_id) == []

    async def test_a_mismatched_signature_is_a_400_and_asks_the_provider_nothing(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        provider, _, out = await _open(client, org_id)
        order_id = out["client_action"]["order_id"]
        paid = cs.razorpay_pay(provider, order_id, status="captured")
        calls = len(provider.fake.requests)
        body = self._body(provider, out, paid)
        body["razorpay_signature"] = "0" * 64
        response = await cs.confirm_checkout(client, org_id, out["charge_id"], body)
        assert response.status_code == 400, response.text
        assert len(provider.fake.requests) == calls
        assert (await _row(admin_session, out["charge_id"]))["state"] == "created"  # not paid

    @pytest.mark.parametrize(
        "missing", ["razorpay_order_id", "razorpay_payment_id", "razorpay_signature"]
    )
    async def test_a_missing_or_blank_field_is_a_400(
        self, client: AsyncClient, org_id: uuid.UUID, missing: str
    ) -> None:
        provider, _, out = await _open(client, org_id)
        full = self._body(provider, out, "pay_x")
        for broken in ({k: v for k, v in full.items() if k != missing}, {**full, missing: ""}):
            response = await cs.confirm_checkout(client, org_id, out["charge_id"], broken)
            assert response.status_code == 400, (missing, response.text)
        assert (await cs.confirm_checkout(client, org_id, out["charge_id"], {})).status_code == 400

    async def test_a_valid_signature_for_another_order_is_a_400(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        provider, invoice_id, out = await _open(client, org_id)
        other = await cs.seed_invoice(client, org_id)
        other_out = (await cs.open_checkout(client, org_id, other[0])).json()
        stolen = self._body(provider, other_out, "pay_x")  # a real signature, for the wrong Order
        response = await cs.confirm_checkout(client, org_id, out["charge_id"], stolen)
        assert response.status_code == 400
        assert invoice_id != other[0]

    async def test_an_unknown_field_is_a_422(self, client: AsyncClient, org_id: uuid.UUID) -> None:
        provider, _, out = await _open(client, org_id)
        body = {**self._body(provider, out, "pay_x"), "amount": 1}
        assert (
            await cs.confirm_checkout(client, org_id, out["charge_id"], body)
        ).status_code == 422

    async def test_an_unreachable_provider_is_a_503_not_a_guess(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        provider, _, out = await _open(client, org_id)
        provider.fake.inject(httpx.ConnectError("down"))
        response = await cs.confirm_checkout(
            client, org_id, out["charge_id"], self._body(provider, out, "pay_x")
        )
        assert response.status_code == 503
        assert (await _row(admin_session, out["charge_id"]))["state"] == "created"

    async def test_cashfree_has_no_browser_confirmation(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        _, _, out = await _open(client, org_id, provider=cs.cashfree_stub())
        body = {
            "razorpay_order_id": out["charge_id"],
            "razorpay_payment_id": "p",
            "razorpay_signature": "s",
        }
        assert (
            await cs.confirm_checkout(client, org_id, out["charge_id"], body)
        ).status_code == 409

    async def test_authorization(
        self, client: AsyncClient, org_id: uuid.UUID, org_b: uuid.UUID
    ) -> None:
        provider, _, out = await _open(client, org_id)
        body = self._body(provider, out, "pay_x")
        url = f"{cs.CHECKOUTS}/{out['charge_id']}/confirm"
        assert (await client.post(url, json=body)).status_code == 401
        for perms in ([], [cs.READ_PERM], ["core.product.activation.write:OWN"]):
            denied = await cs.confirm_checkout(client, org_id, out["charge_id"], body, perms=perms)
            assert denied.status_code == 403
        assert (await cs.confirm_checkout(client, org_b, out["charge_id"], body)).status_code == 404

    async def test_confirming_a_captured_checkout_asks_nobody_again(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        provider, _, out = await _open(client, org_id)
        paid = cs.razorpay_pay(provider, out["client_action"]["order_id"], status="captured")
        body = self._body(provider, out, paid)
        await cs.confirm_checkout(client, org_id, out["charge_id"], body)
        calls = len(provider.fake.requests)
        again = await cs.confirm_checkout(client, org_id, out["charge_id"], body)
        assert again.status_code == 200 and again.json()["paid"] is True
        assert len(provider.fake.requests) == calls


async def test_a_checkout_charge_shows_no_session_token_in_any_read(
    client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
) -> None:
    _, _, out = await _open(client, org_id, provider=cs.cashfree_stub())
    token = out["client_action"]["payment_session_id"]
    status = await cs.get_checkout(client, org_id, out["charge_id"])
    assert token not in status.text
    audits = await audit_rows(admin_session, org_id)
    assert token not in str(audits)
