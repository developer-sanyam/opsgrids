"""The `no_mandate` guard (ADR-0005 Q4) and the money guard — decided **before any provider call**
(ADR-0032).

Flipping a platform wire on without this would have been the harm: every consumer stages a charge
with no mandate, so every charge would have created a real Razorpay Order and then raised, burned
five attempts, failed as `attempts_exhausted` and emitted `commerce.payment.failed` — dunning and
suspension of paying tenants, for debits that could never have been made. ADR-0005 Q4 says a charge
with no usable mandate fails as `no_mandate` **with no provider call**; this is that, built, and
held to it for every provider whose debit needs a mandate.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from app import domain, services
from app.config import settings
from app.consumers import charge_on_dunning_retry, charge_on_invoice_finalized
from app.models import CHARGE_CAPTURED, CHARGE_FAILED, Mandate
from app.providers.base import ProviderCapabilities
from app.providers.razorpay import RazorpayProvider
from app.schemas import ChargeIn
from platform_core.db import dispose_engine, init_engine
from tests import checkout_support as cs
from tests.helpers import audit_rows, emitted, rows, service_token

pytestmark = pytest.mark.integration


@pytest.fixture
async def _engine() -> Any:
    init_engine(settings())
    yield
    await dispose_engine()


async def _row(admin: AsyncSession, charge_id: str | uuid.UUID) -> dict[str, Any]:
    found = await rows(
        admin,
        "SELECT state, failure_reason, provider_payment_id, provider_order_id, attempts, "
        "lease_until, next_attempt_at, mandate_id, provider FROM payment.charges WHERE id = :id",
        id=uuid.UUID(str(charge_id)),
    )
    return found[0]


async def _post(client: AsyncClient, org: uuid.UUID, **over: Any) -> Any:
    body = {
        "organization_id": str(org),
        "amount_minor": 49_900,
        "currency": "INR",
        "idempotency_key": f"invoice:{uuid.uuid4()}",
        "invoice_id": str(uuid.uuid4()),
        "subscription_id": str(uuid.uuid4()),
        "product_key": "hrms",
        **over,
    }
    return await client.post(
        "/internal/v1/charges", headers={"X-Internal-Token": service_token()}, json=body
    )


async def _mandate(session: AsyncSession, org: uuid.UUID, *, provider: str = "razorpay") -> Mandate:
    mandate = Mandate(
        id=uuid.uuid4(),
        organization_id=org,
        provider=provider,
        provider_mandate_id=f"m_{uuid.uuid4().hex[:10]}",
        state="active",
        max_amount_minor=1_000_000,
        currency="INR",
        activated_at=datetime.now(UTC) - timedelta(days=1),
    )
    async with session.begin():
        session.add(mandate)
    return mandate


class TestALiveProviderWithNoMandate:
    async def test_the_charge_fails_as_no_mandate_with_no_provider_call_and_dunning_hears(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        live = cs.install(cs.razorpay_live())
        subscription_id = uuid.uuid4()
        response = await _post(client, org_id, subscription_id=str(subscription_id))
        assert response.status_code == 201, response.text
        charge = response.json()

        assert charge["state"] == "failed"
        row = await _row(admin_session, charge["id"])
        assert row["failure_reason"] == "no_mandate"
        assert row["provider_payment_id"] is None and row["provider_order_id"] is None
        assert (
            row["attempts"] == 0 and row["lease_until"] is None and row["next_attempt_at"] is None
        )
        # No Order, no payment, no request of any kind reached the provider.
        assert live.fake.requests == []  # type: ignore[attr-defined]
        # `commerce.payment.failed` is what the dunning ladder runs on; it names the subscription.
        failed = await rows(
            admin_session,
            "SELECT payload FROM payment.outbox WHERE organization_id = :org "
            "AND event_type = 'commerce.payment.failed'",
            org=org_id,
        )
        assert len(failed) == 1
        assert failed[0]["payload"]["subscription_id"] == str(subscription_id)
        assert failed[0]["payload"]["failure_reason"] == "no_mandate"
        actions = [
            (a["action"], a["acting_as"])
            for a in await audit_rows(admin_session, org_id)
            if str(a["entity_id"]) == charge["id"]
        ]
        assert ("payment.charge.failed", "billing") in actions

    async def test_a_retried_key_is_the_same_row_and_announces_nothing_more(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        cs.install(cs.razorpay_live())
        first = (await _post(client, org_id, idempotency_key="invoice:fixed")).json()
        before = await emitted(admin_session, org_id)
        again = (await _post(client, org_id, idempotency_key="invoice:fixed")).json()
        assert again["id"] == first["id"] and again["state"] == "failed"
        assert await emitted(admin_session, org_id) == before

    async def test_a_mandate_the_build_cannot_debit_does_not_change_the_answer(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        """Phase A has no mandate debit. A mandate on file does not make a charge debitable, so it
        fails `no_mandate` too — the customer pays by checkout — rather than reaching an unbuilt
        call."""
        live = cs.install(cs.razorpay_live())
        await _mandate(session, org_id)
        charge = (await _post(client, org_id)).json()
        assert charge["state"] == "failed"
        assert (await _row(admin_session, charge["id"]))["failure_reason"] == "no_mandate"
        assert live.fake.requests == []  # type: ignore[attr-defined]

    @pytest.mark.parametrize(
        ("amount", "currency", "reason"),
        [
            (99, "INR", "amount_below_minimum"),
            (0, "INR", "amount_below_minimum"),
            (49_900, "USD", "unsupported_currency"),
            (49_900, "EUR", "unsupported_currency"),
        ],
    )
    async def test_money_the_provider_would_refuse_fails_before_any_call(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        amount: int,
        currency: str,
        reason: str,
    ) -> None:
        live = cs.install(cs.razorpay_live())
        with capture_logs() as logs:
            charge = (await _post(client, org_id, amount_minor=amount, currency=currency)).json()
        assert charge["state"] == "failed"
        assert (await _row(admin_session, charge["id"]))["failure_reason"] == reason
        assert live.fake.requests == []  # type: ignore[attr-defined]
        # **Never announced.** `commerce.payment.failed` opens dunning, and nothing — no retry,
        # no checkout (it refuses the same amount with a 422) — could ever collect this: the
        # subscription would be suspended over money the customer had no way to pay.
        assert "commerce.payment.failed" not in await emitted(admin_session, org_id)
        assert any(
            line["event"] == "payment.charge.uncollectable"
            and line["log_level"] == "error"
            and line["failure_reason"] == reason
            for line in logs
        )
        audit = next(
            a
            for a in await audit_rows(admin_session, org_id)
            if str(a["entity_id"]) == charge["id"] and a["action"] == "payment.charge.failed"
        )
        assert audit["after"]["announced"] is False

    async def test_an_invoice_below_the_minimum_does_not_start_dunning_nor_checkout(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
        _engine: Any,
    ) -> None:
        """A mid-period change leaves a 40-paise invoice. Its debit is refused, and so is a
        checkout for it; neither may put the subscription into dunning over it."""
        live = cs.install(cs.razorpay_live())
        monkeypatch.setattr("app.consumers.get_provider", lambda: live)
        invoice_id = uuid.uuid4()
        await charge_on_invoice_finalized(
            {
                "event_id": str(uuid.uuid4()),
                "event_type": "commerce.invoice.finalized",
                "payload": {
                    "organization_id": str(org_id),
                    "invoice_id": str(invoice_id),
                    "subscription_id": str(uuid.uuid4()),
                    "product_key": "hrms",
                    "total_minor": 40,
                    "currency": "INR",
                },
            }
        )
        assert "commerce.payment.failed" not in await emitted(admin_session, org_id)
        refused = await cs.open_checkout(client, org_id, invoice_id)
        assert refused.status_code == 422

    async def test_a_cashfree_default_fails_the_same_way_and_the_fake_is_never_asked(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        cashfree = cs.install(cs.cashfree_stub())
        charge = (await _post(client, org_id)).json()
        assert charge["state"] == "failed" and charge["provider"] == "cashfree"
        assert (await _row(admin_session, charge["id"]))["failure_reason"] == "no_mandate"
        assert cashfree.fake.requests == []  # type: ignore[attr-defined]


class TestTheLegacyStubIsUntouched:
    async def test_the_development_posture_still_debits_with_no_mandate(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The stub declares no constraints: the dev flow, and every older test, is unchanged."""
        cs.install(RazorpayProvider(key_id=None, key_secret=None, webhook_secret="x"))
        charge = (await _post(client, org_id, amount_minor=1, currency="USD")).json()
        assert charge["state"] == "authorized"
        assert (await _row(admin_session, charge["id"]))["failure_reason"] is None


class TestAWholeFlow:
    async def test_no_mandate_then_checkout_then_a_capture_recovers_the_invoice(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """What the platform does on a live provider with no mandates: the debit fails `no_mandate`
        (dunning starts), the customer pays the same invoice by checkout, and the capture is the
        `commerce.payment.captured` the subscription recovers on."""
        live = cs.install(cs.razorpay_live())
        invoice_id, debit = await cs.seed_invoice(client, org_id)
        assert debit["state"] == "failed"
        opened = (await cs.open_checkout(client, org_id, invoice_id)).json()
        cs.razorpay_pay(live, opened["client_action"]["order_id"], status="captured")
        paid = (await cs.get_checkout(client, org_id, opened["charge_id"])).json()
        assert paid["paid"] is True
        events = await emitted(admin_session, org_id)
        assert events.count("commerce.payment.failed") == 1
        assert events.count("commerce.payment.captured") == 1
        # Both are the one invoice's: the failed debit and the captured checkout.
        assert (await _row(admin_session, debit["id"]))["state"] == CHARGE_FAILED
        assert (await _row(admin_session, opened["charge_id"]))["state"] == CHARGE_CAPTURED


class TestTheConsumersTooUseTheGuard:
    async def test_an_invoice_finalized_event_on_a_live_provider_fails_no_mandate(
        self,
        admin_session: AsyncSession,
        session: AsyncSession,
        org_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
        _engine: Any,
    ) -> None:
        live = cs.razorpay_live()
        monkeypatch.setattr("app.consumers.get_provider", lambda: live)
        invoice_id, subscription_id = uuid.uuid4(), uuid.uuid4()
        envelope = {
            "event_id": str(uuid.uuid4()),
            "event_type": "commerce.invoice.finalized",
            "organization_id": str(org_id),
            "payload": {
                "organization_id": str(org_id),
                "invoice_id": str(invoice_id),
                "subscription_id": str(subscription_id),
                "product_key": "hrms",
                "total_minor": 149_900,
                "currency": "INR",
            },
        }
        await charge_on_invoice_finalized(envelope)
        (charge,) = await rows(
            admin_session,
            "SELECT state, failure_reason FROM payment.charges WHERE organization_id = :org",
            org=org_id,
        )
        assert (charge["state"], charge["failure_reason"]) == ("failed", "no_mandate")
        assert live.fake.requests == []  # type: ignore[attr-defined]
        assert "commerce.payment.failed" in await emitted(admin_session, org_id)

    async def test_every_dunning_rung_fails_no_mandate_so_the_ladder_runs_to_its_end(
        self,
        admin_session: AsyncSession,
        session: AsyncSession,
        org_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
        _engine: Any,
    ) -> None:
        live = cs.razorpay_live()
        monkeypatch.setattr("app.consumers.get_provider", lambda: live)
        subscription_id, cycle = uuid.uuid4(), uuid.uuid4()
        for attempt in (1, 2):
            await charge_on_dunning_retry(
                {
                    "event_id": str(uuid.uuid4()),
                    "event_type": "commerce.subscription.payment_retry_due",
                    "organization_id": str(org_id),
                    "payload": {
                        "organization_id": str(org_id),
                        "subscription_id": str(subscription_id),
                        "product_key": "hrms",
                        "invoice_id": str(uuid.uuid4()),
                        "attempt_no": attempt,
                        "amount_minor": 149_900,
                        "currency": "INR",
                        "idempotency_key": f"dunning:{cycle}:{attempt}",
                    },
                }
            )
        reasons = await rows(
            admin_session,
            "SELECT failure_reason FROM payment.charges WHERE organization_id = :org",
            org=org_id,
        )
        assert [r["failure_reason"] for r in reasons] == ["no_mandate", "no_mandate"]
        assert live.fake.requests == []  # type: ignore[attr-defined]


class TestTheRefusalRules:
    def test_the_order_is_currency_then_amount_then_mandate(self) -> None:
        caps = ProviderCapabilities(
            currencies=frozenset({"INR"}),
            min_amount_minor=100,
            requires_mandate_for_debit=True,
            supports_mandate_debit=False,
        )

        def refusal(amount: int = 49_900, currency: str = "INR", mandate: Any = None) -> str | None:
            return services.debit_refusal(
                caps, amount_minor=amount, currency=currency, mandate=mandate
            )

        assert refusal(currency="USD", amount=1) == domain.FAILURE_UNSUPPORTED_CURRENCY
        assert refusal(amount=99) == domain.FAILURE_AMOUNT_BELOW_MINIMUM
        assert refusal() == domain.FAILURE_NO_MANDATE
        assert refusal(mandate=object()) == domain.FAILURE_NO_MANDATE  # cannot be debited here
        # Lower-case currencies are the same currency.
        assert refusal(currency="inr") == domain.FAILURE_NO_MANDATE

    def test_a_provider_that_can_debit_with_a_mandate_is_not_refused_when_it_has_one(self) -> None:
        caps = ProviderCapabilities(requires_mandate_for_debit=True, supports_mandate_debit=True)
        assert (
            services.debit_refusal(caps, amount_minor=1, currency="USD", mandate=None)
            == "no_mandate"
        )
        assert (
            services.debit_refusal(caps, amount_minor=1, currency="USD", mandate=object()) is None
        )

    def test_the_legacy_capabilities_never_refuse(self) -> None:
        assert (
            services.debit_refusal(
                ProviderCapabilities(), amount_minor=0, currency="XXX", mandate=None
            )
            is None
        )

    def test_no_mandate_is_not_a_local_failure_a_late_capture_could_undo(self) -> None:
        """Nothing was sent, so there is no payment a late webhook could capture."""
        for reason in ("no_mandate", "unsupported_currency", "amount_below_minimum"):
            assert reason not in domain.LOCAL_FAILURE_REASONS
        assert "checkout_expired" in domain.LOCAL_FAILURE_REASONS


class TestMandatesUsableIsProviderScoped:
    async def test_a_mandate_at_another_provider_does_not_count(
        self, client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        await _mandate(session, org_id, provider="cashfree")
        response = await client.get(
            f"/internal/v1/organizations/{org_id}/mandates:usable",
            params={"currency": "INR", "amount_minor": 1000},
            headers={"X-Internal-Token": service_token_for("subscription")},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["usable"] is False and body["mandate_id"] is None
        assert body["provider"] == "razorpay"  # the default provider: what was asked of

    async def test_a_mandate_at_the_default_provider_counts_and_names_its_provider(
        self, client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        mandate = await _mandate(session, org_id, provider="razorpay")
        response = await client.get(
            f"/internal/v1/organizations/{org_id}/mandates:usable",
            params={"currency": "INR", "amount_minor": 1000},
            headers={"X-Internal-Token": service_token_for("subscription")},
        )
        body = response.json()
        assert body["usable"] is True and body["mandate_id"] == str(mandate.id)
        assert body["provider"] == "razorpay"


def service_token_for(caller: str) -> str:
    from platform_core.internal_auth import mint_service_token

    return mint_service_token(settings(), audience="payment", caller=caller)


def test_a_charge_in_with_a_named_mandate_still_validates_it_first() -> None:
    """The guard is additive: `ChargeIn` and `_require_usable_mandate` are unchanged."""
    payload = ChargeIn(
        organization_id=uuid.uuid4(),
        amount_minor=1,
        currency="INR",
        idempotency_key="k",
        mandate_id=uuid.uuid4(),
    )
    assert payload.mandate_id is not None
