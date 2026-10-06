"""`test_mode` on every platform-checkout answer, and `mode` on `/payment/info` (ADR-0032, the
staging test-mode note of 2026-10-06).

The contract the dashboard codes to: `POST /payment/checkouts`, `GET /payment/checkouts/{id}` and
`POST /payment/checkouts/{id}/confirm` each carry a **required** boolean `test_mode`, true exactly
when the provider that **owns the charge** runs on test / sandbox credentials — a Razorpay
`rzp_test_` key on a live wire, or Cashfree's sandbox — and false on live credentials and on a stub
(nothing is called there at all). "Owns the charge" is the row's provider, never the configured
default: a checkout opened at one provider before a flip to the other is still paid at the first.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from app.providers.base import credentials_mode_of, in_test_mode
from tests import checkout_support as cs

RZP_LIVE_ID = "rzp_live_Devonly000001"


async def _open(client: AsyncClient, org_id: uuid.UUID, provider: Any) -> dict[str, Any]:
    cs.install(provider)
    invoice_id, _ = await cs.seed_invoice(client, org_id)
    response = await cs.open_checkout(client, org_id, invoice_id, cust=cs.customer())
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


class TestTheAdaptersSayWhatTheyRunOn:
    """Unit-level: no database."""

    def test_razorpay(self) -> None:
        assert credentials_mode_of(cs.razorpay_live()) == "test"
        assert credentials_mode_of(cs.razorpay_live(key_id=RZP_LIVE_ID)) == "live"
        # Test keys with no wire: nothing is called, so it is not test mode.
        assert credentials_mode_of(cs.razorpay_stub()) == "stub"

    @pytest.mark.parametrize(
        ("mode", "expected"), [("stub", "stub"), ("sandbox", "test"), ("production", "live")]
    )
    def test_cashfree(self, mode: Any, expected: str) -> None:
        assert credentials_mode_of(cs.cashfree_stub(mode=mode)) == expected

    def test_an_adapter_that_declares_nothing_is_a_stub_and_never_test_mode(self) -> None:
        class Bare:
            name = "razorpay"

        class Odd:
            name = "razorpay"
            credentials_mode = "sandbox"  # not one of the three words: not believed

        for adapter in (Bare(), Odd()):
            assert credentials_mode_of(adapter) == "stub" and in_test_mode(adapter) is False


@pytest.mark.integration
class TestEveryCheckoutAnswerCarriesIt:
    async def test_razorpay_test_keys_are_test_mode_on_open_read_and_confirm(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        provider = cs.razorpay_live()
        out = await _open(client, org_id, provider)
        assert out["test_mode"] is True

        read = await cs.get_checkout(client, org_id, out["charge_id"])
        assert read.status_code == 200 and read.json()["test_mode"] is True

        order_id = out["client_action"]["order_id"]
        paid = cs.razorpay_pay(provider, order_id, status="captured")
        confirm = await cs.confirm_checkout(
            client,
            org_id,
            out["charge_id"],
            {
                "razorpay_order_id": order_id,
                "razorpay_payment_id": paid,
                "razorpay_signature": provider.sign_checkout(order_id, paid),
            },
        )
        assert confirm.status_code == 200, confirm.text
        assert confirm.json()["paid"] is True and confirm.json()["test_mode"] is True
        # A settled checkout answers from the row without asking: still test mode.
        again = await cs.get_checkout(client, org_id, out["charge_id"])
        assert again.json()["provider_checked"] is False and again.json()["test_mode"] is True

    async def test_razorpay_live_keys_are_not_test_mode(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        out = await _open(client, org_id, cs.razorpay_live(key_id=RZP_LIVE_ID))
        assert out["test_mode"] is False
        read = await cs.get_checkout(client, org_id, out["charge_id"])
        assert read.json()["test_mode"] is False

    async def test_cashfree_sandbox_is_test_mode(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        provider = cs.cashfree_stub(mode="sandbox")
        out = await _open(client, org_id, provider)
        assert out["test_mode"] is True
        assert (await cs.get_checkout(client, org_id, out["charge_id"])).json()["test_mode"] is True
        cs.cashfree_pay(provider, out["charge_id"], status="SUCCESS")
        body = (await cs.get_checkout(client, org_id, out["charge_id"])).json()
        assert body["paid"] is True and body["test_mode"] is True

    @pytest.mark.parametrize("mode", ["stub", "production"])
    async def test_cashfree_stub_and_production_are_not_test_mode(
        self, client: AsyncClient, org_id: uuid.UUID, mode: Any
    ) -> None:
        out = await _open(client, org_id, cs.cashfree_stub(mode=mode))
        assert out["test_mode"] is False
        assert (await cs.get_checkout(client, org_id, out["charge_id"])).json()[
            "test_mode"
        ] is False

    async def test_it_is_the_owning_providers_mode_not_the_defaults(
        self, client: AsyncClient, org_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A Cashfree sandbox checkout read after the default became a Razorpay stub is still
        test mode; a Razorpay test-mode checkout read once the default is a stub Razorpay is not
        (the same provider: its adapter is now the stub)."""
        sandbox = cs.cashfree_stub(mode="sandbox")
        out = await _open(client, org_id, sandbox)
        cs.install(cs.razorpay_stub())
        # The row's provider is resolved by name when it is not the default's.
        monkeypatch.setattr(
            "app.api.checkout.resolve_provider",
            lambda name: sandbox if name == "cashfree" else pytest.fail(name),
        )
        read = await cs.get_checkout(client, org_id, out["charge_id"])
        assert read.status_code == 200 and read.json()["provider"] == "cashfree"
        assert read.json()["test_mode"] is True

    async def test_a_provider_that_cannot_be_built_reads_as_not_test_mode(
        self, client: AsyncClient, org_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A paid checkout is answered from its row, with no provider asked: a provider this
        deployment can no longer build must not turn that read into a 500 over a display flag."""
        provider = cs.cashfree_stub(mode="sandbox")
        out = await _open(client, org_id, provider)
        cs.cashfree_pay(provider, out["charge_id"], status="SUCCESS")
        assert (await cs.get_checkout(client, org_id, out["charge_id"])).json()["paid"] is True
        cs.install(cs.razorpay_stub())

        def unbuildable(name: str) -> Any:
            raise ValueError(f"unknown payment provider {name!r}")

        monkeypatch.setattr("app.api.checkout.resolve_provider", unbuildable)
        read = await cs.get_checkout(client, org_id, out["charge_id"])
        assert read.status_code == 200, read.text
        assert read.json()["paid"] is True and read.json()["test_mode"] is False


@pytest.mark.integration
class TestTheInfoRouteSaysTheMode:
    @pytest.mark.parametrize(
        ("adapter", "provider", "mode"),
        [
            (lambda: cs.razorpay_stub(), "razorpay", "stub"),
            (lambda: cs.razorpay_live(), "razorpay", "test"),
            (lambda: cs.razorpay_live(key_id=RZP_LIVE_ID), "razorpay", "live"),
            (lambda: cs.cashfree_stub(mode="sandbox"), "cashfree", "test"),
            (lambda: cs.cashfree_stub(mode="production"), "cashfree", "live"),
        ],
    )
    async def test_the_default_providers_mode_and_never_a_key(
        self, client: AsyncClient, adapter: Any, provider: str, mode: str
    ) -> None:
        cs.install(adapter())
        response = await client.get("/api/v1/payment/info")
        assert response.status_code == 200
        body = response.json()
        assert (body["provider"], body["mode"]) == (provider, mode)
        assert body["test_keys_allowed"] is False  # the test process's own settings
        assert "rzp_" not in response.text and cs.KEY_SECRET not in response.text
