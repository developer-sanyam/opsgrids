"""`POST /payment/checkouts` — a customer opening the provider's checkout for one of OpsGrids' own
invoices (ADR-0032, Phase A).

What these pin, each a way this could go wrong in production:

* **The amount is the invoice's own charge's**, read server-side — never the body's.
* **One open checkout per invoice**; a second request returns it, and a request that races a call in
  flight is refused rather than making a second Order.
* **Claim → commit → provider call → record**: the charge is committed *before* the provider is
  asked, and the provider is asked with **no transaction open**.
* **Nothing the customer typed is kept.** The phone, the email and the name go to the provider and
  to no table, log line, audit row or event.
* **Authorization is server-side**: no token is a 401, a caller without
  `core.product.activation.write` at ORG is a 403, and another organization's invoice is a 404.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from app.checkout.cron import ChargePoller
from app.providers.cashfree import CashfreeProvider
from app.providers.cashfree_fake import CashfreeFakeWire
from app.providers.razorpay import RazorpayProvider
from app.providers.wire import CREDENTIALS
from platform_core.db import get_outbox_sessionmaker, session_scope
from tests import checkout_support as cs
from tests.helpers import audit_rows, emitted, force_charge, rows

pytestmark = pytest.mark.integration


async def _charge(admin: AsyncSession, charge_id: str | uuid.UUID) -> dict[str, Any]:
    found = await rows(
        admin,
        "SELECT flow, state, provider, provider_order_id, provider_payment_id, failure_reason, "
        "amount_minor, currency, invoice_id, subscription_id, product_key, attempts, lease_until, "
        "checkout_expires_at, idempotency_key FROM payment.charges WHERE id = :id",
        id=uuid.UUID(str(charge_id)),
    )
    return found[0]


async def _checkouts(admin: AsyncSession, org: uuid.UUID) -> list[dict[str, Any]]:
    return await rows(
        admin,
        "SELECT id, state, idempotency_key, failure_reason FROM payment.charges "
        "WHERE organization_id = :org AND flow = 'checkout' ORDER BY created_at",
        org=org,
    )


class TestOpeningARazorpayCheckout:
    async def test_it_returns_what_the_browser_needs_and_the_invoices_own_amount(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        provider = cs.install(cs.razorpay_live())
        invoice_id, debit = await cs.seed_invoice(client, org_id, amount_minor=49_900)
        assert debit["state"] == "failed"  # born `no_mandate`: the charge the customer now pays

        response = await cs.open_checkout(client, org_id, invoice_id)

        assert response.status_code == 201, response.text
        out = response.json()
        assert out["provider"] == "razorpay"
        assert (out["amount_minor"], out["currency"]) == (49_900, "INR")
        order_id = out["client_action"]["order_id"]
        assert out["client_action"] == {
            "type": "razorpay_checkout",
            "key_id": cs.KEY_ID,
            "order_id": order_id,
            "openable": True,
        }
        assert cs.KEY_SECRET not in response.text and cs.HOOK not in response.text
        assert datetime.fromisoformat(out["expires_at"]) > datetime.now(UTC)
        # What reached Razorpay: the invoice's money, captured automatically, tenant metadata.
        (order_body,) = [b for b in provider.fake.bodies if b.get("receipt")]  # type: ignore[attr-defined]
        assert order_body["amount"] == 49_900 and order_body["currency"] == "INR"
        assert order_body["payment_capture"] is True
        assert order_body["receipt"] == out["charge_id"]
        assert order_body["notes"]["organization_id"] == str(org_id)
        assert order_body["notes"]["flow"] == "checkout"

    async def test_the_charge_is_a_checkout_copied_from_the_invoices_charge(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        cs.install(cs.razorpay_live())
        subscription_id = uuid.uuid4()
        invoice_id, debit = await cs.seed_invoice(
            client, org_id, subscription_id=subscription_id, product_key="hrms"
        )
        out = (await cs.open_checkout(client, org_id, invoice_id)).json()

        row = await _charge(admin_session, out["charge_id"])
        assert row["flow"] == "checkout" and row["state"] == "created"
        assert row["provider"] == "razorpay" and row["provider_payment_id"] is None
        assert row["provider_order_id"] == out["client_action"]["order_id"]
        assert uuid.UUID(str(row["invoice_id"])) == invoice_id
        assert uuid.UUID(str(row["subscription_id"])) == subscription_id
        assert row["product_key"] == "hrms"
        assert row["lease_until"] is None  # released once the Order is recorded
        assert row["idempotency_key"] == f"checkout:{invoice_id}"
        expires = row["checkout_expires_at"]
        assert timedelta(hours=23) < expires - datetime.now(UTC) <= timedelta(hours=24, minutes=1)
        # The invoice's own debit charge is untouched.
        assert (await _charge(admin_session, debit["id"]))["state"] == "failed"

    async def test_opening_is_audited_and_announces_nothing(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        cs.install(cs.razorpay_live())
        invoice_id, _ = await cs.seed_invoice(client, org_id)
        before = await emitted(admin_session, org_id)
        out = (await cs.open_checkout(client, org_id, invoice_id)).json()
        actions = [
            a["action"]
            for a in await audit_rows(admin_session, org_id)
            if str(a["entity_id"]) == out["charge_id"]
        ]
        assert actions == ["payment.checkout.opened", "payment.checkout.order_created"]
        # An unpaid checkout is not a payment event: nothing for subscription to react to.
        assert await emitted(admin_session, org_id) == before

    async def test_the_body_cannot_name_an_amount(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        cs.install(cs.razorpay_live())
        invoice_id, _ = await cs.seed_invoice(client, org_id, amount_minor=49_900)
        with cs.owner(org_id):
            response = await client.post(
                cs.CHECKOUTS, json={"invoice_id": str(invoice_id), "amount_minor": 1}
            )
        assert response.status_code == 422

    async def test_a_second_request_returns_the_same_open_checkout(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        provider = cs.install(cs.razorpay_live())
        invoice_id, _ = await cs.seed_invoice(client, org_id)
        first = (await cs.open_checkout(client, org_id, invoice_id)).json()
        second = await cs.open_checkout(client, org_id, invoice_id)
        assert second.status_code == 201
        assert second.json()["charge_id"] == first["charge_id"]
        assert second.json()["client_action"]["order_id"] == first["client_action"]["order_id"]
        assert len(provider.fake.orders) == 1  # type: ignore[attr-defined]
        assert len(await _checkouts(admin_session, org_id)) == 1

    async def test_a_request_that_races_a_call_in_flight_is_refused_not_doubled(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The lease is the right to call the provider for this checkout, held by one caller at a
        time: a double-click whose first call is still out must not make a second Order."""
        provider = cs.install(cs.razorpay_live())
        invoice_id, _ = await cs.seed_invoice(client, org_id)
        first = (await cs.open_checkout(client, org_id, invoice_id)).json()
        await force_charge(
            admin_session, first["charge_id"], lease_until=datetime.now(UTC) + timedelta(minutes=5)
        )
        before = len(provider.fake.requests)  # type: ignore[attr-defined]
        response = await cs.open_checkout(client, org_id, invoice_id)
        assert response.status_code == 409
        assert len(provider.fake.requests) == before  # type: ignore[attr-defined]

    async def test_an_invoice_already_paid_is_a_409(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        cs.install(cs.razorpay_live())
        invoice_id, debit = await cs.seed_invoice(client, org_id)
        await force_charge(admin_session, debit["id"], state="captured", failure_reason=None)
        response = await cs.open_checkout(client, org_id, invoice_id)
        assert response.status_code == 409
        assert await _checkouts(admin_session, org_id) == []

    async def test_an_invoice_with_no_charge_is_a_404(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        cs.install(cs.razorpay_live())
        response = await cs.open_checkout(client, org_id, uuid.uuid4())
        assert response.status_code == 404

    async def test_another_organizations_invoice_is_a_404_and_creates_nothing(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID, org_b: uuid.UUID
    ) -> None:
        provider = cs.install(cs.razorpay_live())
        invoice_id, _ = await cs.seed_invoice(client, org_b)
        response = await cs.open_checkout(client, org_id, invoice_id)
        assert response.status_code == 404
        assert provider.fake.methods() == []  # type: ignore[attr-defined]
        assert await _checkouts(admin_session, org_id) == []
        assert await _checkouts(admin_session, org_b) == []


class TestTheRecordedOrderIsTheCheckout:
    """Razorpay accepts a duplicate receipt and its receipt search can miss a fresh Order (sandbox,
    2026-10-06). So the Order id is recorded before any browser sees it, a reopen fetches that Order
    by its id, and nothing else is ever handed to a browser."""

    async def test_a_reopen_finds_its_order_by_id_when_the_receipt_search_cannot_see_it(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        provider = cs.install(cs.razorpay_live())
        invoice_id, _ = await cs.seed_invoice(client, org_id)
        first = (await cs.open_checkout(client, org_id, invoice_id)).json()
        order_id = first["client_action"]["order_id"]
        provider.fake.unindexed.add(order_id)  # type: ignore[attr-defined]
        provider.fake.requests.clear()  # type: ignore[attr-defined]

        again = await cs.open_checkout(client, org_id, invoice_id)

        assert again.status_code == 201, again.text
        assert again.json()["client_action"]["order_id"] == order_id
        assert provider.fake.methods() == [f"GET /v1/orders/{order_id}"]  # type: ignore[attr-defined]
        assert len(provider.fake.orders) == 1  # type: ignore[attr-defined]

    async def test_a_reopen_never_hands_out_a_duplicate_of_the_receipt(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        provider = cs.install(cs.razorpay_live())
        invoice_id, _ = await cs.seed_invoice(client, org_id)
        first = (await cs.open_checkout(client, org_id, invoice_id)).json()
        order_id = first["client_action"]["order_id"]
        duplicate = provider.fake.duplicate(first["charge_id"])  # type: ignore[attr-defined]
        provider.fake.unindexed.add(order_id)  # type: ignore[attr-defined]

        again = await cs.open_checkout(client, org_id, invoice_id)

        assert again.status_code == 201, again.text
        assert again.json()["client_action"]["order_id"] == order_id
        assert duplicate not in again.text

    async def test_a_lost_create_the_search_cannot_see_leaves_an_order_nobody_is_shown(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The unknown-outcome retry the sandbox makes possible: the first create landed and its
        answer was lost, the retry's search sees nothing, and the retry makes a second Order. Only
        the second is recorded, and only the recorded one is handed out, then and on every
        reopen."""
        provider = cs.install(cs.razorpay_live())
        invoice_id, _ = await cs.seed_invoice(client, org_id)
        provider.fake.index_new_orders = False  # type: ignore[attr-defined]
        provider.fake.lose_next_create_response = True  # type: ignore[attr-defined]
        assert (await cs.open_checkout(client, org_id, invoice_id)).status_code == 503
        (lost,) = provider.fake.orders  # type: ignore[attr-defined]

        opened = await cs.open_checkout(client, org_id, invoice_id)
        reopened = await cs.open_checkout(client, org_id, invoice_id)

        assert opened.status_code == reopened.status_code == 201, opened.text
        recorded = opened.json()["client_action"]["order_id"]
        assert recorded != lost and reopened.json()["client_action"]["order_id"] == recorded
        assert lost not in opened.text and lost not in reopened.text
        (row,) = await _checkouts(admin_session, org_id)
        assert (await _charge(admin_session, row["id"]))["provider_order_id"] == recorded
        assert provider.fake.receipt_orders(str(row["id"])) == [lost, recorded]  # type: ignore[attr-defined]


class TestMoneyIsRefusedBeforeAnyCall:
    @pytest.mark.parametrize(("amount", "currency"), [(99, "INR"), (49_900, "USD"), (1, "INR")])
    async def test_an_unpayable_invoice_is_a_422_and_the_provider_is_never_asked(
        self, client: AsyncClient, org_id: uuid.UUID, amount: int, currency: str
    ) -> None:
        # Seeded under the stub, which is tolerant, so a charge exists to read the money from.
        invoice_id, _ = await cs.seed_invoice(
            client, org_id, amount_minor=amount, currency=currency
        )
        provider = cs.install(cs.razorpay_live())
        response = await cs.open_checkout(client, org_id, invoice_id)
        assert response.status_code == 422, response.text
        assert provider.fake.requests == []  # type: ignore[attr-defined]


class TestACashfreeCheckout:
    async def test_it_needs_a_usable_phone_and_never_invents_one(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        provider = cs.install(cs.cashfree_stub())
        invoice_id, _ = await cs.seed_invoice(client, org_id)
        for cust in (None, cs.customer(phone=None), cs.customer(phone="12345")):
            response = await cs.open_checkout(client, org_id, invoice_id, cust=cust)
            assert response.status_code == 422, response.text
        assert provider.fake.requests == []  # type: ignore[attr-defined]

    async def test_it_opens_with_the_session_token_and_amount_from_the_invoice(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        provider = cs.install(cs.cashfree_stub())
        invoice_id, debit = await cs.seed_invoice(client, org_id, amount_minor=1999)
        assert debit["state"] == "failed"  # Cashfree has no debit in Phase A: `no_mandate`
        response = await cs.open_checkout(client, org_id, invoice_id, cust=cs.customer())
        assert response.status_code == 201, response.text
        out = response.json()
        assert out["provider"] == "cashfree" and (out["amount_minor"], out["currency"]) == (
            1999,
            "INR",
        )
        action = out["client_action"]
        assert action["type"] == "cashfree_checkout" and action["openable"] is False
        assert action["payment_session_id"].startswith("session_stub_")
        order = provider.fake.orders[out["charge_id"]]  # type: ignore[attr-defined]
        assert str(order["order_amount"]) == "19.99"  # the exact decimal, never a float

        row = await _charge(admin_session, out["charge_id"])
        assert row["provider"] == "cashfree" and row["provider_order_id"] == out["charge_id"]

    async def test_a_reopened_checkout_fetches_the_session_again_and_stores_none(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        provider = cs.install(cs.cashfree_stub())
        invoice_id, _ = await cs.seed_invoice(client, org_id)
        first = (await cs.open_checkout(client, org_id, invoice_id, cust=cs.customer())).json()
        second = (await cs.open_checkout(client, org_id, invoice_id, cust=cs.customer())).json()
        assert first["charge_id"] == second["charge_id"]
        assert (
            first["client_action"]["payment_session_id"]
            == second["client_action"]["payment_session_id"]
        )
        assert len(provider.fake.orders) == 1  # type: ignore[attr-defined]
        token = first["client_action"]["payment_session_id"]
        stored = await rows(
            admin_session,
            "SELECT count(*) AS n FROM payment.charges c "
            "WHERE c.organization_id = :org AND c::text LIKE :t",
            org=org_id,
            t=f"%{token}%",
        )
        assert stored == [{"n": 0}], "the session token must never be persisted"


class TestNothingTheCustomerTypedIsKept:
    async def test_the_phone_email_and_name_reach_the_provider_and_no_table_or_log(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        provider = cs.install(cs.cashfree_stub())
        invoice_id, _ = await cs.seed_invoice(client, org_id)
        with capture_logs() as logs:
            response = await cs.open_checkout(client, org_id, invoice_id, cust=cs.customer())
        assert response.status_code == 201
        # The provider received them (the Order needs them)…
        details = provider.fake.orders[response.json()["charge_id"]]  # type: ignore[attr-defined]
        assert details is not None
        # …and nothing of ours kept them: every payment table, as text, and every log line.
        everything = await rows(
            admin_session,
            "SELECT 'charges' AS t, c::text AS body FROM payment.charges c "
            "UNION ALL SELECT 'audit_logs', a::text FROM payment.audit_logs a "
            "UNION ALL SELECT 'outbox', o::text FROM payment.outbox o "
            "UNION ALL SELECT 'provider_events', p::text FROM payment.provider_events p",
        )
        text = json.dumps(everything) + json.dumps(logs, default=str) + response.text
        for personal in (cs.PHONE, cs.EMAIL, cs.NAME):
            assert personal not in text, f"{personal!r} was kept"


class TestAuthorization:
    async def test_no_token_is_a_401(self, client: AsyncClient) -> None:
        response = await client.post(cs.CHECKOUTS, json={"invoice_id": str(uuid.uuid4())})
        assert response.status_code == 401

    @pytest.mark.parametrize(
        "perms",
        [
            [],
            [cs.READ_PERM],  # reading the activations is not paying for them
            ["core.product.activation.write:OWN"],  # below the ORG floor
            ["operations.payments.merchant_account.write:ORG"],  # another surface's permission
        ],
    )
    async def test_a_caller_without_the_owner_permission_is_a_403_and_nothing_is_created(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID, perms: list[str]
    ) -> None:
        provider = cs.install(cs.razorpay_live())
        invoice_id, _ = await cs.seed_invoice(client, org_id)
        response = await cs.open_checkout(client, org_id, invoice_id, perms=perms)
        assert response.status_code == 403, response.text
        assert provider.fake.requests == []  # type: ignore[attr-defined]
        assert await _checkouts(admin_session, org_id) == []

    async def test_a_malformed_body_is_a_422(self, client: AsyncClient, org_id: uuid.UUID) -> None:
        with cs.owner(org_id):
            for body in (
                {},
                {"invoice_id": "not-a-uuid"},
                {"invoice_id": str(uuid.uuid4()), "x": 1},
            ):
                assert (await client.post(cs.CHECKOUTS, json=body)).status_code == 422


class TestWhenTheProviderFails:
    async def test_rejected_credentials_are_a_503_that_fails_nothing_and_stops_the_calls(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The platform's own bad key is nobody's fault but ours: the customer sees "unavailable",
        the charge is not failed, nothing is announced, and — the latch — the provider is not
        hammered with a request per click."""
        invoice_id, _ = await cs.seed_invoice(client, org_id)
        provider = cs.install(cs.razorpay_live())
        provider.fake.inject(  # type: ignore[attr-defined]
            httpx.Response(401, json={"error": {"code": "BAD_REQUEST_ERROR", "description": "x"}})
        )
        before_events = await emitted(admin_session, org_id)
        with capture_logs() as logs:
            first = await cs.open_checkout(client, org_id, invoice_id)
        assert first.status_code == 503
        assert any(
            line["event"] == "payment.provider.credentials_rejected"
            and line["log_level"] == "error"
            for line in logs
        )
        assert CREDENTIALS.tripped("razorpay")
        requests = len(provider.fake.requests)  # type: ignore[attr-defined]
        second = await cs.open_checkout(client, org_id, invoice_id)
        assert second.status_code == 503
        assert len(provider.fake.requests) == requests  # type: ignore[attr-defined]
        assert await emitted(admin_session, org_id) == before_events
        # The claim is not wedged: the lease was given back.
        (row,) = await _checkouts(admin_session, org_id)
        assert row["state"] == "created" and row["failure_reason"] is None
        assert (await _charge(admin_session, row["id"]))["lease_until"] is None

    async def test_a_timeout_is_a_503_and_the_next_click_finds_the_order_it_may_have_made(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        invoice_id, _ = await cs.seed_invoice(client, org_id)
        provider = cs.install(cs.razorpay_live())
        provider.fake.lose_next_create_response = True  # type: ignore[attr-defined]
        first = await cs.open_checkout(client, org_id, invoice_id)
        assert first.status_code == 503
        assert len(provider.fake.orders) == 1  # type: ignore[attr-defined]  # it did land
        second = await cs.open_checkout(client, org_id, invoice_id)
        assert second.status_code == 201
        assert len(provider.fake.orders) == 1  # type: ignore[attr-defined]  # looked up, not created

    async def test_a_refusal_of_the_request_is_a_502_with_the_providers_code_only(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        invoice_id, _ = await cs.seed_invoice(client, org_id)
        provider = cs.install(cs.razorpay_live())
        provider.fake.inject(httpx.Response(200, json={"items": []}))  # type: ignore[attr-defined]
        provider.fake.inject(  # type: ignore[attr-defined]
            httpx.Response(
                400,
                json={
                    "error": {"code": "BAD_REQUEST_ERROR", "description": "free text with a secret"}
                },
            )
        )
        provider.fake.inject(httpx.Response(200, json={"items": []}))  # type: ignore[attr-defined]
        response = await cs.open_checkout(client, org_id, invoice_id)
        assert response.status_code == 502
        assert "BAD_REQUEST_ERROR" in response.text and "free text" not in response.text

    async def test_a_server_error_is_a_503(self, client: AsyncClient, org_id: uuid.UUID) -> None:
        invoice_id, _ = await cs.seed_invoice(client, org_id)
        provider = cs.install(cs.razorpay_live())
        provider.fake.inject(httpx.Response(500, json={}))  # type: ignore[attr-defined]
        assert (await cs.open_checkout(client, org_id, invoice_id)).status_code == 503


class TestTheProviderIsCalledWithNoTransactionOpen:
    async def test_the_charge_is_committed_before_the_call_and_no_transaction_is_open_during_it(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        """Claim → commit → call → record. A provider that holds a pool connection (and a row lock)
        for however long it takes to answer is how a handful of slow requests exhaust the pool."""
        invoice_id, _ = await cs.seed_invoice(client, org_id)
        inner = cs.razorpay_live()
        observed: dict[str, Any] = {}

        class Spy:
            """The live adapter, observed at the moment the provider is asked."""

            name = inner.name
            capabilities = inner.capabilities
            request_timeouts_seconds = inner.request_timeouts_seconds

            def ensure_checkout(self, request: Any) -> Any:
                observed["in_transaction"] = session.in_transaction()
                return inner.ensure_checkout(request)

            def __getattr__(self, name: str) -> Any:
                return getattr(inner, name)

        cs.install(Spy())  # type: ignore[arg-type]
        response = await cs.open_checkout(client, org_id, invoice_id)
        assert response.status_code == 201, response.text
        assert observed["in_transaction"] is False, "the provider was called inside a transaction"

    async def test_the_claim_is_visible_to_other_connections_while_the_provider_is_out(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        invoice_id, _ = await cs.seed_invoice(client, org_id)
        inner = cs.razorpay_live()
        seen: list[list[dict[str, Any]]] = []

        class Spy:
            name = inner.name
            capabilities = inner.capabilities
            request_timeouts_seconds = inner.request_timeouts_seconds

            def ensure_checkout(self, request: Any) -> Any:
                # The call runs on a worker thread; read what another connection sees meanwhile.
                loop = asyncio.new_event_loop()
                try:
                    seen.append(loop.run_until_complete(_fresh_look(org_id)))
                finally:
                    loop.close()
                return inner.ensure_checkout(request)

            def __getattr__(self, name: str) -> Any:
                return getattr(inner, name)

        cs.install(Spy())  # type: ignore[arg-type]
        assert (await cs.open_checkout(client, org_id, invoice_id)).status_code == 201
        assert len(seen) == 1 and len(seen[0]) == 1, "the claim was not committed before the call"
        assert seen[0][0]["state"] == "created"


async def _fresh_look(org_id: uuid.UUID) -> list[dict[str, Any]]:
    """What a brand-new connection sees of this organization's checkouts right now."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.config import settings
    from app.models import SCHEMA

    engine = create_async_engine(
        settings().admin_dsn, connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}}
    )
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT state, idempotency_key FROM payment.charges "
                    "WHERE organization_id = :org AND flow = 'checkout'"
                ),
                {"org": org_id},
            )
            return [dict(r) for r in result.mappings().all()]
    finally:
        await engine.dispose()


class TestAnExpiredCheckout:
    async def test_it_is_asked_about_then_closed_and_a_fresh_one_is_opened(
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
        before = await emitted(admin_session, org_id)

        second = await cs.open_checkout(client, org_id, invoice_id)

        assert second.status_code == 201, second.text
        assert second.json()["charge_id"] != first["charge_id"]
        old, new = await _checkouts(admin_session, org_id)
        assert (old["state"], old["failure_reason"]) == ("failed", "checkout_expired")
        assert new["state"] == "created" and new["idempotency_key"] == f"checkout:{invoice_id}:1"
        # Closing an unpaid checkout announces nothing: it is not a failed collection.
        assert await emitted(admin_session, org_id) == before
        assert len(provider.fake.orders) == 2  # type: ignore[attr-defined]

    async def test_one_that_was_paid_meanwhile_is_captured_not_replaced(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
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

        assert response.status_code == 409  # already paid
        (only,) = await _checkouts(admin_session, org_id)
        assert only["state"] == "captured"
        assert "commerce.payment.captured" in await emitted(admin_session, org_id)

    async def test_one_that_never_got_an_order_is_closed_and_a_fresh_one_opened(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Every open of it failed at the provider and the customer did not come back inside its
        day. No Order id ever reached a browser, so nothing can have been paid on it, and there is
        nothing to ask a provider about: it is closed on the spot, not left to answer every later
        "Pay now" with a 409 for good."""
        provider = cs.install(cs.razorpay_live())
        invoice_id, _ = await cs.seed_invoice(client, org_id)
        provider.fake.inject(httpx.Response(500, json={}))  # type: ignore[attr-defined]
        assert (await cs.open_checkout(client, org_id, invoice_id)).status_code == 503
        (stuck,) = await _checkouts(admin_session, org_id)
        assert (await _charge(admin_session, stuck["id"]))["provider_order_id"] is None
        await force_charge(
            admin_session, stuck["id"], checkout_expires_at=datetime.now(UTC) - timedelta(hours=1)
        )
        before = await emitted(admin_session, org_id)

        response = await cs.open_checkout(client, org_id, invoice_id)

        assert response.status_code == 201, response.text
        assert response.json()["charge_id"] != str(stuck["id"])
        old, new = await _checkouts(admin_session, org_id)
        assert (old["state"], old["failure_reason"]) == ("failed", "checkout_expired")
        assert new["state"] == "created"
        assert await emitted(admin_session, org_id) == before
        audits = [
            (a["action"], a["acting_as"])
            for a in await audit_rows(admin_session, org_id)
            if a["entity_id"] == old["id"]
        ]
        assert ("payment.charge.checkout_expired", "checkout") in audits

    async def test_one_that_never_got_an_order_waits_while_a_call_for_it_is_out(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """A call still in flight may yet record an Order and hand it to a browser."""
        provider = cs.install(cs.razorpay_live())
        invoice_id, _ = await cs.seed_invoice(client, org_id)
        provider.fake.inject(httpx.Response(500, json={}))  # type: ignore[attr-defined]
        assert (await cs.open_checkout(client, org_id, invoice_id)).status_code == 503
        (stuck,) = await _checkouts(admin_session, org_id)
        await force_charge(
            admin_session,
            stuck["id"],
            checkout_expires_at=datetime.now(UTC) - timedelta(hours=1),
            lease_until=datetime.now(UTC) + timedelta(minutes=5),
        )
        assert (await cs.open_checkout(client, org_id, invoice_id)).status_code == 409
        (still,) = await _checkouts(admin_session, org_id)
        assert still["state"] == "created"

    async def test_one_with_a_payment_in_flight_is_neither_closed_nor_replaced(
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

        assert response.status_code == 409, response.text
        (only,) = await _checkouts(admin_session, org_id)
        assert (only["state"], only["failure_reason"]) == ("created", None)
        assert len(provider.fake.orders) == 1  # type: ignore[attr-defined]


class TestAClosedCheckoutBesideANewOne:
    """A checkout closed as `checkout_expired` keeps an Order the customer may still pay. Before a
    second Order is put in front of the same customer for the same invoice, the closed one is asked
    about: paid meanwhile is recorded (and the invoice is paid), a payment still in flight refuses
    the new checkout."""

    async def _closed(
        self, client: AsyncClient, admin: AsyncSession, org_id: uuid.UUID, *, ago: timedelta
    ) -> tuple[Any, uuid.UUID, dict[str, Any]]:
        provider = cs.install(cs.razorpay_live())
        invoice_id, _ = await cs.seed_invoice(client, org_id)
        first = (await cs.open_checkout(client, org_id, invoice_id)).json()
        await force_charge(admin, first["charge_id"], checkout_expires_at=datetime.now(UTC) - ago)
        closed = await cs.get_checkout(client, org_id, first["charge_id"])
        assert closed.json()["failure_reason"] == "checkout_expired", closed.text
        return provider, invoice_id, first

    async def test_one_paid_after_it_closed_is_recorded_and_no_second_is_opened(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        provider, invoice_id, first = await self._closed(
            client, admin_session, org_id, ago=timedelta(hours=1)
        )
        cs.razorpay_pay(provider, first["client_action"]["order_id"], status="captured")

        response = await cs.open_checkout(client, org_id, invoice_id)

        assert response.status_code == 409  # already paid
        (only,) = await _checkouts(admin_session, org_id)
        assert only["state"] == "captured"
        assert len(provider.fake.orders) == 1  # type: ignore[attr-defined]
        assert (await emitted(admin_session, org_id)).count("commerce.payment.captured") == 1

    async def test_one_with_a_payment_in_flight_refuses_a_second_until_its_grace_ends(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        provider, invoice_id, first = await self._closed(
            client, admin_session, org_id, ago=timedelta(minutes=5)
        )
        cs.razorpay_pay(provider, first["client_action"]["order_id"], status="authorized")

        refused = await cs.open_checkout(client, org_id, invoice_id)

        assert refused.status_code == 409 and "still being processed" in refused.text
        assert len(await _checkouts(admin_session, org_id)) == 1
        # An attempt the provider never resolves holds the invoice for the grace and no longer.
        await force_charge(
            admin_session,
            first["charge_id"],
            checkout_expires_at=datetime.now(UTC) - timedelta(hours=2),
        )
        opened = await cs.open_checkout(client, org_id, invoice_id)
        assert opened.status_code == 201, opened.text
        assert len(await _checkouts(admin_session, org_id)) == 2

    async def test_one_nobody_is_paying_does_not_stand_in_the_way(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        provider, invoice_id, first = await self._closed(
            client, admin_session, org_id, ago=timedelta(minutes=5)
        )
        cs.razorpay_pay(provider, first["client_action"]["order_id"], status="failed")
        response = await cs.open_checkout(client, org_id, invoice_id)
        assert response.status_code == 201, response.text
        assert response.json()["charge_id"] != first["charge_id"]


class TestARollbackToTheStub:
    """The owner turns a live wire back to the stub while a checkout is open at the real provider.
    The stub must never become the authority on that checkout's Order: reopening it is refused
    rather than minting a stand-in Order under the same id (Cashfree's `order_id` *is* the
    receipt), so the poller still cannot expire it, and the real wire recovers the payment."""

    async def test_a_cashfree_checkout_opened_live_is_never_reminted_by_the_stub(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
        engines: None,
    ) -> None:
        real_wire = CashfreeFakeWire()
        real = CashfreeProvider(wire=real_wire, secret_key=cs.CASHFREE_SECRET, mode="production")
        cs.install(real)
        invoice_id, _ = await cs.seed_invoice(client, org_id)
        first = await cs.open_checkout(client, org_id, invoice_id, cust=cs.customer())
        assert first.status_code == 201, first.text
        charge_id = first.json()["charge_id"]
        real_wire.pay(charge_id, status="SUCCESS")

        # Rolled back: the default is the Razorpay stub, Cashfree resolves to a fresh stub.
        stub = cs.cashfree_stub()
        cs.install(
            RazorpayProvider(
                key_id=None, key_secret=None, webhook_secret="x", platform_billing=True
            )
        )
        monkeypatch.setattr("app.api.checkout.resolve_provider", lambda name: stub)
        reopened = await cs.open_checkout(client, org_id, invoice_id, cust=cs.customer())

        assert reopened.status_code == 503, reopened.text
        assert stub.fake.orders == {}  # type: ignore[attr-defined]
        await force_charge(
            admin_session, charge_id, checkout_expires_at=datetime.now(UTC) - timedelta(hours=1)
        )
        report = await _poller(lambda name: stub).tick()
        assert (report.unchecked, report.expired) == (1, 0)
        assert (await _charge(admin_session, charge_id))["state"] == "created"

        # The wire comes back on: the payment made at the real Cashfree is recorded.
        report = await _poller(lambda name: real).tick(now=datetime.now(UTC) + timedelta(minutes=2))
        assert report.captured == 1
        assert (await _charge(admin_session, charge_id))["state"] == "captured"

    async def test_a_razorpay_checkout_opened_live_is_never_reminted_by_the_stub(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        live = cs.install(cs.razorpay_live())
        invoice_id, _ = await cs.seed_invoice(client, org_id)
        first = (await cs.open_checkout(client, org_id, invoice_id)).json()
        stub = RazorpayProvider(
            key_id=None, key_secret=None, webhook_secret="x", platform_billing=True
        )
        cs.install(stub)

        reopened = await cs.open_checkout(client, org_id, invoice_id)

        assert reopened.status_code == 503, reopened.text
        assert stub.find_order(receipt=first["charge_id"]) is None
        row = await _charge(admin_session, first["charge_id"])
        assert row["provider_order_id"] == first["client_action"]["order_id"]
        assert len(live.fake.orders) == 1  # type: ignore[attr-defined]


def _poller(resolver: Any) -> ChargePoller:
    return ChargePoller(
        discovery_factory=get_outbox_sessionmaker(),
        work_scope=lambda organization_id: session_scope(organization_id=organization_id),
        provider_resolver=resolver,
        poll_interval=30.0,
    )


class TestRoutedByTheRowNotTheDefault:
    async def test_a_razorpay_checkout_is_reopened_through_razorpay_while_the_default_is_cashfree(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        razorpay = cs.razorpay_live()
        cashfree = cs.cashfree_stub()
        invoice_id, _ = await cs.seed_invoice(client, org_id)
        cs.install(razorpay)
        first = (await cs.open_checkout(client, org_id, invoice_id)).json()
        assert first["provider"] == "razorpay"

        # The owner flips the default to Cashfree. The open Razorpay checkout is not Cashfree's.
        cs.install(cashfree)
        monkeypatch.setattr("app.api.checkout.resolve_provider", lambda name: razorpay)
        again = await cs.open_checkout(client, org_id, invoice_id, cust=cs.customer())

        assert again.status_code == 201, again.text
        assert again.json()["charge_id"] == first["charge_id"]
        assert again.json()["provider"] == "razorpay"
        assert cashfree.fake.requests == []  # type: ignore[attr-defined]
        assert len(razorpay.fake.orders) == 1  # type: ignore[attr-defined]

    async def test_a_new_checkout_goes_to_the_default_provider(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        cashfree = cs.cashfree_stub()
        cs.install(cashfree)
        invoice_id, _ = await cs.seed_invoice(client, org_id)
        out = (await cs.open_checkout(client, org_id, invoice_id, cust=cs.customer())).json()
        assert out["provider"] == "cashfree"
        assert len(cashfree.fake.orders) == 1  # type: ignore[attr-defined]
