"""Provider selection and routing (ADR-0032): `PAYMENT_PROVIDER` chooses where **new** charges go;
every existing row is finished by the provider it was created under.

"Switchable" does not mean migratable. A Razorpay charge left `created` while the owner flips the
default to Cashfree must be looked up, resumed and polled through **Razorpay** — Cashfree cannot
find Razorpay's Order, and "creating" the payment there is a second debit. These tests are that
promise, and the registry that makes the choice: the wire is live only on an explicit switch, never
because keys are present.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import select

from app import deps, services
from app.cron import StuckChargeSweep
from app.models import Charge
from app.providers import registry
from app.providers.cashfree import CashfreeProvider
from app.providers.cashfree_fake import CashfreeFakeWire
from app.providers.cashfree_wire import CashfreeHttpWire
from app.providers.razorpay import RazorpayProvider
from app.providers.razorpay_wire import RazorpayHttpWire
from app.schemas import ChargeIn
from platform_core.db import get_outbox_sessionmaker, session_scope
from tests import checkout_support as cs
from tests.helpers import emitted, force_charge, rows, service_token

RZP_ID = "rzp_test_Devonly000001"
RZP_SECRET = "dev-only-routing-razorpay-secret"  # pragma: allowlist secret
CF_APP = "TESTdev-only-app"  # pragma: allowlist secret
CF_SECRET = "cfsk_ma_test_dev-only-0001"  # pragma: allowlist secret


class Cfg:
    """A minimal stand-in for `Settings` — the registry reads it with `getattr` defaults."""

    def __init__(self, **kw: Any) -> None:
        self.payment_provider = "razorpay"
        self.payment_wire = "stub"
        self.cashfree_env = "stub"
        self.razorpay_key_id = None
        self.razorpay_key_secret = None
        self.razorpay_webhook_secret = SecretStr(
            "dev-only-routing-hook"
        )  # pragma: allowlist secret
        self.cashfree_app_id = None
        self.cashfree_secret_key = None
        self.__dict__.update(kw)


class TestTheRegistry:
    def test_razorpay_is_the_stub_unless_the_wire_is_switched_live_whatever_keys_exist(
        self,
    ) -> None:
        keys = {"razorpay_key_id": RZP_ID, "razorpay_key_secret": SecretStr(RZP_SECRET)}
        stub = registry.build("razorpay", Cfg(**keys))
        assert isinstance(stub, RazorpayProvider) and stub.is_live is False
        live = registry.build("razorpay", Cfg(payment_wire="live", **keys))
        assert isinstance(live, RazorpayProvider) and live.is_live is True
        assert isinstance(live._wire, RazorpayHttpWire)
        assert live._wire._base_url == "https://api.razorpay.com/v1"
        assert live._platform_billing is True
        assert RZP_SECRET not in repr(live) + repr(live._wire)

    def test_a_live_razorpay_wire_without_keys_is_an_error_not_a_quiet_stub(self) -> None:
        with pytest.raises(RuntimeError, match="PAYMENT_WIRE=live"):
            registry.build("razorpay", Cfg(payment_wire="live"))

    def test_cashfree_stub_is_the_in_process_fake_whatever_keys_exist(self) -> None:
        keys = {"cashfree_app_id": SecretStr(CF_APP), "cashfree_secret_key": SecretStr(CF_SECRET)}
        provider = registry.build("cashfree", Cfg(**keys))
        assert isinstance(provider, CashfreeProvider) and provider.is_live is False
        assert isinstance(provider._wire, CashfreeFakeWire)

    @pytest.mark.parametrize(
        ("env", "base"),
        [
            ("sandbox", "https://sandbox.cashfree.com/pg"),
            ("production", "https://api.cashfree.com/pg"),
        ],
    )
    def test_cashfree_live_environments_get_their_own_base_url(self, env: str, base: str) -> None:
        provider = registry.build(
            "cashfree",
            Cfg(
                cashfree_env=env,
                cashfree_app_id=SecretStr(CF_APP),
                cashfree_secret_key=SecretStr(CF_SECRET),
            ),
        )
        assert isinstance(provider, CashfreeProvider) and provider.is_live is True
        assert isinstance(provider._wire, CashfreeHttpWire)
        assert provider._wire._base_url == base
        assert CF_SECRET not in repr(provider) + repr(provider._wire)
        assert provider.capabilities.wire == "live"

    def test_cashfree_live_without_credentials_is_an_error(self) -> None:
        with pytest.raises(RuntimeError, match="CASHFREE_ENV=sandbox"):
            registry.build("cashfree", Cfg(cashfree_env="sandbox"))

    def test_unknown_names_are_refused(self) -> None:
        with pytest.raises(ValueError, match="unknown payment provider"):
            registry.build("stripe", Cfg())
        with pytest.raises(ValueError, match="CASHFREE_ENV"):
            registry.build("cashfree", Cfg(cashfree_env="staging"))

    def test_the_names_and_builders_agree(self) -> None:
        assert set(registry.PROVIDER_NAMES) == set(registry.BUILDERS) == {"razorpay", "cashfree"}


class TestTheDeps:
    def test_the_default_is_per_new_charge_and_each_name_is_cached_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = Cfg(payment_provider="cashfree")
        monkeypatch.setattr(deps, "settings", lambda: cfg)
        deps.reset_providers()
        try:
            assert deps.default_provider_name() == "cashfree"
            assert deps.get_provider().name == "cashfree"
            assert deps.get_provider() is deps.get_provider()
            assert deps.provider_for("cashfree") is deps.get_provider()
            assert deps.provider_for("razorpay") is deps.provider_for("razorpay")
            assert deps.resolve_provider("razorpay").name == "razorpay"
        finally:
            deps.reset_providers()

    def test_a_settings_stand_in_with_no_provider_field_defaults_to_razorpay(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Bare:
            razorpay_webhook_secret = SecretStr("dev-only-bare-hook")  # pragma: allowlist secret

        monkeypatch.setattr(deps, "settings", lambda: Bare())
        deps.reset_providers()
        try:
            assert deps.default_provider_name() == "razorpay"
            assert deps.get_provider().name == "razorpay"
        finally:
            deps.reset_providers()

    def test_each_webhook_route_gets_its_own_adapter_whatever_the_default_is(self) -> None:
        cashfree, razorpay = cs.cashfree_stub(), cs.razorpay_live()
        assert deps.razorpay_provider(razorpay) is razorpay  # a test's own override keeps its state
        assert deps.cashfree_provider(cashfree) is cashfree
        # The default flipped to Cashfree: the Razorpay route is still Razorpay's.
        assert deps.razorpay_provider(cashfree).name == "razorpay"
        assert deps.cashfree_provider(razorpay).name == "cashfree"

    def test_the_adapter_for_an_existing_charge_is_the_default_only_when_it_is_that_provider(
        self,
    ) -> None:
        cashfree, razorpay = cs.cashfree_stub(), cs.razorpay_live()
        razorpay_charge = Charge(id=uuid.uuid4(), provider="razorpay")
        cashfree_charge = Charge(id=uuid.uuid4(), provider="cashfree")
        assert services.provider_for_charge(razorpay, razorpay_charge) is razorpay
        assert services.provider_for_charge(cashfree, cashfree_charge) is cashfree
        assert services.provider_for_charge(cashfree, razorpay_charge).name == "razorpay"
        assert services.provider_for_charge(razorpay, cashfree_charge).name == "cashfree"


class TestAnExistingChargeKeepsItsProvider:
    async def test_the_sweep_finishes_a_razorpay_charge_through_razorpay_not_the_new_default(
        self, org_id: uuid.UUID, session: Any, engines: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        razorpay_stub = RazorpayProvider(key_id=None, key_secret=None, webhook_secret="x")
        cashfree = cs.cashfree_stub()
        payload = ChargeIn(
            organization_id=org_id, amount_minor=1000, currency="INR", idempotency_key="k-route"
        )
        async with session_scope(organization_id=org_id) as s:
            charge, needs_call = await services.stage_charge(s, razorpay_stub, payload)
        assert needs_call and charge.provider == "razorpay"

        asked: list[str] = []

        def resolver(name: str) -> Any:
            asked.append(name)
            return razorpay_stub

        sweep = StuckChargeSweep(
            discovery_factory=get_outbox_sessionmaker(),
            work_scope=lambda organization_id: session_scope(organization_id=organization_id),
            provider_factory=lambda: cashfree,  # the default has moved
            provider_resolver=resolver,
            reclaim_after=timedelta(minutes=5),
        )
        report = await sweep.tick(now=datetime.now(UTC) + timedelta(minutes=10))

        assert report.resumed == 1 and asked == ["razorpay"]
        assert cashfree.fake.requests == []  # type: ignore[attr-defined]
        async with session_scope(organization_id=org_id) as s:
            row = (await s.execute(select(Charge).where(Charge.id == charge.id))).scalar_one()
        assert row.provider == "razorpay" and row.provider_payment_id is not None

    async def test_without_a_resolver_the_sweep_uses_the_default_only_when_it_names_the_provider(
        self, org_id: uuid.UUID, session: Any, engines: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        razorpay_stub = RazorpayProvider(key_id=None, key_secret=None, webhook_secret="x")
        cashfree = cs.cashfree_stub()
        monkeypatch.setattr("app.cron.resolve_provider", lambda name: razorpay_stub)
        sweep = StuckChargeSweep(
            discovery_factory=get_outbox_sessionmaker(),
            work_scope=lambda organization_id: session_scope(organization_id=organization_id),
            provider_factory=lambda: cashfree,
        )
        charge = Charge(id=uuid.uuid4(), provider="razorpay")
        assert sweep._provider_for(charge) is razorpay_stub
        assert sweep._provider_for(Charge(id=uuid.uuid4(), provider="cashfree")) is cashfree

    async def test_a_retried_key_calls_the_provider_the_charge_was_created_under(
        self,
        client: AsyncClient,
        admin_session: Any,
        org_id: uuid.UUID,
        engines: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        razorpay_stub = RazorpayProvider(key_id=None, key_secret=None, webhook_secret="x")
        cashfree = cs.cashfree_stub()
        payload = ChargeIn(
            organization_id=org_id, amount_minor=1000, currency="INR", idempotency_key="k-retry"
        )
        async with session_scope(organization_id=org_id) as s:
            charge, _ = await services.stage_charge(s, razorpay_stub, payload)
        await force_charge(
            admin_session,
            charge.id,
            lease_until=datetime.now(UTC) - timedelta(minutes=1),
            next_attempt_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        monkeypatch.setattr(deps, "provider_for", lambda name: razorpay_stub)
        cs.install(cashfree)

        response = await client.post(
            "/internal/v1/charges",
            headers={"X-Internal-Token": service_token()},
            json={
                "organization_id": str(org_id),
                "amount_minor": 1000,
                "currency": "INR",
                "idempotency_key": "k-retry",
            },
        )

        assert response.status_code == 201, response.text
        assert response.json()["provider"] == "razorpay"
        assert response.json()["state"] == "authorized"  # made through Razorpay
        assert cashfree.fake.requests == []  # type: ignore[attr-defined]

    async def test_a_new_charge_goes_to_the_default_provider(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        cashfree = cs.install(cs.cashfree_stub())
        response = await client.post(
            "/internal/v1/charges",
            headers={"X-Internal-Token": service_token()},
            json={
                "organization_id": str(org_id),
                "amount_minor": 49_900,
                "currency": "INR",
                "idempotency_key": "k-new",
            },
        )
        assert response.json()["provider"] == cashfree.name


class TestTheWebhookRoutesAreBoundToTheirOwnAdapter:
    async def test_a_razorpay_delivery_is_verified_with_razorpays_secret_after_the_default_flips(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        from app.config import settings

        cs.install(cs.cashfree_stub())  # the default is now Cashfree
        body = (
            b'{"event": "payment.captured", "created_at": 1, '
            b'"payload": {"payment": {"entity": {"id": "pay_1"}}}}'
        )
        signer = RazorpayProvider(
            key_id=None,
            key_secret=None,
            webhook_secret=settings().razorpay_webhook_secret.get_secret_value(),
        )
        good = await client.post(
            "/api/v1/webhooks/razorpay",
            content=body,
            headers={"X-Razorpay-Signature": signer.sign(body), "X-Razorpay-Event-Id": "evt_1"},
        )
        assert good.status_code == 200, good.text
        assert good.json()["ignored_reason"] == "unattributable"  # verified, and names no tenant
        bad = await client.post(
            "/api/v1/webhooks/razorpay",
            content=body,
            headers={"X-Razorpay-Signature": "0" * 64, "X-Razorpay-Event-Id": "evt_2"},
        )
        assert bad.status_code == 400

    async def test_nothing_is_recorded_for_an_unattributable_delivery(
        self, client: AsyncClient, admin_session: Any, org_id: uuid.UUID
    ) -> None:
        assert await emitted(admin_session, org_id) == []
        assert await rows(admin_session, "SELECT 1 FROM payment.provider_events") == []
