"""The provider switches and their start-up validators (ADR-0029): an unconfigured box must start; a
misconfigured one must refuse, naming the variable and never a value; and no combination lets keys
being *present* switch the network on.

Settings are built directly with `_env_file=None` so a developer's root `.env` can never decide the
outcome of a test about configuration.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from app.config import Settings
from app.providers.wire import MAX_DECLARED_CALL_BUDGET_SECONDS

# Placeholders in the shapes the validators judge by: not real keys, and not shaped like the real
# ones the secret scan looks for (`tests/infra/test_payment_secret_shapes.py`).
RZP_TEST_ID = "rzp_test_Devonly000001"
RZP_LIVE_ID = "rzp_live_Devonly000001"
RZP_SECRET = "dev-only-razorpay-secret-0001"  # pragma: allowlist secret
CF_TEST_APP = "TESTdev-only-app"  # pragma: allowlist secret
CF_TEST_SECRET = "cfsk_ma_test_dev-only-0001"  # pragma: allowlist secret
CF_PROD_APP = "dev-only-prod-app"  # pragma: allowlist secret
CF_PROD_SECRET = "cfsk_ma_prod_dev-only-0001"  # pragma: allowlist secret


#: What a production-environment `Settings` needs of its own: real (not `dev-only-`) platform
#: secrets, which `ServiceSettings` refuses to start a production process without.
_PRODUCTION_SECRETS: dict[str, Any] = {
    "internal_service_token": SecretStr("a" * 48),
    "pii_encryption_key": SecretStr("b" * 48),
}


def build(**kwargs: Any) -> Settings:
    if kwargs.get("environment") in ("production", "staging", "uat"):
        kwargs = {**_PRODUCTION_SECRETS, **kwargs}
    return Settings(_env_file=None, **kwargs)  # type: ignore[call-arg]


def refused(match: str, **kwargs: Any) -> None:
    with pytest.raises(ValidationError, match=match):
        build(**kwargs)


class TestAnUnconfiguredBox:
    def test_the_defaults_are_the_safe_ones(self) -> None:
        cfg = build()
        assert cfg.payment_provider == "razorpay"
        assert cfg.payment_wire == "stub"
        assert cfg.cashfree_env == "stub"
        assert cfg.payment_allow_live_keys is False
        assert cfg.razorpay_key_id is None and cfg.razorpay_key_secret is None
        assert cfg.cashfree_app_id is None and cfg.cashfree_secret_key is None
        assert cfg.provider_call_timeout_seconds == 45.0

    def test_empty_values_are_unset_not_empty_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """compose passes `${RAZORPAY_KEY_ID:-}` — defined but empty. That must not be a key, and
        must not make a half-set pair."""
        for name in (
            "RAZORPAY_KEY_ID",
            "RAZORPAY_KEY_SECRET",
            "CASHFREE_APP_ID",
            "CASHFREE_SECRET_KEY",
        ):
            monkeypatch.setenv(name, "")
        cfg = build()
        assert cfg.razorpay_key_id is None and cfg.cashfree_secret_key is None

    @pytest.mark.parametrize("name", ["payment_provider", "payment_wire", "cashfree_env"])
    def test_an_unknown_switch_value_fails_start_up(self, name: str) -> None:
        with pytest.raises(ValidationError):
            build(**{name: "stripe"})

    def test_keys_present_never_enable_the_network(self) -> None:
        """The switches, not the keys, decide: keys with the stub switches change nothing."""
        cfg = build(
            razorpay_key_id=RZP_TEST_ID,
            razorpay_key_secret=SecretStr(RZP_SECRET),
            cashfree_app_id=SecretStr(CF_TEST_APP),
            cashfree_secret_key=SecretStr(CF_TEST_SECRET),
        )
        assert cfg.payment_wire == "stub" and cfg.cashfree_env == "stub"


class TestRazorpay:
    def test_a_half_set_pair_is_refused_whatever_the_wire(self) -> None:
        refused("must be set together", razorpay_key_id=RZP_TEST_ID)
        refused("must be set together", razorpay_key_secret=SecretStr(RZP_SECRET))

    def test_the_live_wire_needs_both_keys(self) -> None:
        refused("PAYMENT_WIRE=live needs", payment_wire="live")

    def test_the_live_wire_needs_a_razorpay_shaped_key_id(self) -> None:
        refused(
            "must be a Razorpay key id",
            payment_wire="live",
            razorpay_key_id="not-a-key",
            razorpay_key_secret=SecretStr(RZP_SECRET),
        )

    def test_a_live_key_is_refused_unless_allowed_even_on_the_stub(self) -> None:
        refused(
            "PAYMENT_ALLOW_LIVE_KEYS",
            razorpay_key_id=RZP_LIVE_ID,
            razorpay_key_secret=SecretStr(RZP_SECRET),
        )
        refused(
            "PAYMENT_ALLOW_LIVE_KEYS",
            payment_wire="live",
            razorpay_key_id=RZP_LIVE_ID,
            razorpay_key_secret=SecretStr(RZP_SECRET),
        )
        cfg = build(
            payment_wire="live",
            payment_allow_live_keys=True,
            environment="production",
            razorpay_key_id=RZP_LIVE_ID,
            razorpay_key_secret=SecretStr(RZP_SECRET),
        )
        assert cfg.payment_wire == "live"

    def test_test_keys_on_a_live_wire_never_reach_production(self) -> None:
        """A customer could pay a real invoice with a public test card."""
        refused(
            "ENVIRONMENT=production",
            payment_wire="live",
            environment="production",
            razorpay_key_id=RZP_TEST_ID,
            razorpay_key_secret=SecretStr(RZP_SECRET),
        )

    def test_test_keys_on_a_live_wire_are_fine_off_production_and_on_the_stub_anywhere(
        self,
    ) -> None:
        for environment in ("local", "ci", "staging"):
            cfg = build(
                payment_wire="live",
                environment=environment,
                razorpay_key_id=RZP_TEST_ID,
                razorpay_key_secret=SecretStr(RZP_SECRET),
            )
            assert cfg.payment_wire == "live"
        cfg = build(
            environment="production",
            razorpay_key_id=RZP_TEST_ID,
            razorpay_key_secret=SecretStr(RZP_SECRET),
        )
        assert cfg.payment_wire == "stub"


class TestCashfree:
    def test_a_half_set_pair_is_refused(self) -> None:
        refused("must be set together", cashfree_app_id=SecretStr(CF_TEST_APP))
        refused("must be set together", cashfree_secret_key=SecretStr(CF_TEST_SECRET))

    @pytest.mark.parametrize("env", ["sandbox", "production"])
    def test_a_live_environment_needs_both_credentials(self, env: str) -> None:
        refused("needs CASHFREE_APP_ID", cashfree_env=env)

    def test_sandbox_with_test_keys_off_production_is_fine(self) -> None:
        cfg = build(
            cashfree_env="sandbox",
            cashfree_app_id=SecretStr(CF_TEST_APP),
            cashfree_secret_key=SecretStr(CF_TEST_SECRET),
        )
        assert cfg.cashfree_env == "sandbox"

    def test_sandbox_never_reaches_a_production_box(self) -> None:
        """Sandbox accepts public test cards and VPAs: real invoices payable for free."""
        refused(
            "sandbox is refused when ENVIRONMENT=production",
            cashfree_env="sandbox",
            environment="production",
            cashfree_app_id=SecretStr(CF_TEST_APP),
            cashfree_secret_key=SecretStr(CF_TEST_SECRET),
        )

    def test_sandbox_with_production_shaped_keys_is_refused(self) -> None:
        refused(
            "PAYMENT_ALLOW_LIVE_KEYS|production-shaped",
            cashfree_env="sandbox",
            payment_allow_live_keys=True,
            cashfree_app_id=SecretStr(CF_PROD_APP),
            cashfree_secret_key=SecretStr(CF_PROD_SECRET),
        )

    def test_production_shaped_keys_are_refused_unless_allowed_on_any_switch(self) -> None:
        refused(
            "PAYMENT_ALLOW_LIVE_KEYS",
            cashfree_app_id=SecretStr(CF_PROD_APP),
            cashfree_secret_key=SecretStr(CF_PROD_SECRET),
        )

    def test_production_needs_the_allow_flag_and_refuses_test_shaped_keys(self) -> None:
        refused(
            "needs PAYMENT_ALLOW_LIVE_KEYS",
            cashfree_env="production",
            cashfree_app_id=SecretStr(CF_PROD_APP),
            cashfree_secret_key=SecretStr("dev-only-unshaped-secret"),  # pragma: allowlist secret
        )
        refused(
            "test-shaped",
            cashfree_env="production",
            payment_allow_live_keys=True,
            cashfree_app_id=SecretStr(CF_TEST_APP),
            cashfree_secret_key=SecretStr(CF_TEST_SECRET),
        )
        cfg = build(
            cashfree_env="production",
            payment_allow_live_keys=True,
            environment="production",
            payment_provider="cashfree",
            cashfree_app_id=SecretStr(CF_PROD_APP),
            cashfree_secret_key=SecretStr(CF_PROD_SECRET),
        )
        assert cfg.cashfree_env == "production" and cfg.payment_provider == "cashfree"

    @pytest.mark.parametrize("env", ["stub", "sandbox"])
    def test_cashfree_as_the_default_on_production_needs_the_real_thing(self, env: str) -> None:
        keys: dict[str, Any] = (
            {}
            if env == "stub"
            else {
                "cashfree_app_id": SecretStr(CF_TEST_APP),
                "cashfree_secret_key": SecretStr(CF_TEST_SECRET),
            }
        )
        refused(
            "ENVIRONMENT=production|needs CASHFREE_ENV=production",
            payment_provider="cashfree",
            environment="production",
            cashfree_env=env,
            **keys,
        )

    def test_cashfree_as_the_default_is_fine_on_the_stub_off_production(self) -> None:
        cfg = build(payment_provider="cashfree")
        assert cfg.payment_provider == "cashfree" and cfg.cashfree_env == "stub"


class TestTheCallBudget:
    def test_a_timeout_below_the_worst_declared_wire_is_refused(self) -> None:
        refused(
            "provider_call_timeout_seconds must be at least",
            provider_call_timeout_seconds=MAX_DECLARED_CALL_BUDGET_SECONDS - 1,
            charge_reclaim_after_seconds=1000,
        )

    def test_the_budget_itself_is_accepted_and_the_lease_floor_still_holds(self) -> None:
        cfg = build(
            provider_call_timeout_seconds=MAX_DECLARED_CALL_BUDGET_SECONDS,
            charge_reclaim_after_seconds=int(2 * MAX_DECLARED_CALL_BUDGET_SECONDS + 60),
        )
        assert cfg.provider_call_timeout_seconds == MAX_DECLARED_CALL_BUDGET_SECONDS
        refused("charge_reclaim_after_seconds", charge_reclaim_after_seconds=100)

    def test_the_default_lease_outlives_the_default_call_twice_over(self) -> None:
        cfg = build()
        assert cfg.charge_reclaim_after_seconds >= 2 * cfg.provider_call_timeout_seconds + 60


class TestNoSecretEverEscapes:
    def test_a_repr_and_a_dump_carry_no_credential(self) -> None:
        cfg = build(
            razorpay_key_id=RZP_TEST_ID,
            razorpay_key_secret=SecretStr(RZP_SECRET),
            cashfree_app_id=SecretStr(CF_TEST_APP),
            cashfree_secret_key=SecretStr(CF_TEST_SECRET),
        )
        shown = repr(cfg) + str(cfg) + repr(cfg.model_dump())
        for secret in (RZP_SECRET, CF_TEST_APP, CF_TEST_SECRET):
            assert secret not in shown

    def test_a_validation_error_names_the_variable_and_never_the_value(self) -> None:
        with pytest.raises(ValidationError) as raised:
            build(
                payment_wire="live",
                environment="production",
                razorpay_key_id=RZP_TEST_ID,
                razorpay_key_secret=SecretStr(RZP_SECRET),
            )
        text = str(raised.value)
        assert "PAYMENT_WIRE" in text
        assert RZP_SECRET not in text and RZP_TEST_ID not in text

    def test_a_cashfree_validation_error_never_echoes_the_keys(self) -> None:
        with pytest.raises(ValidationError) as raised:
            build(cashfree_env="production", cashfree_secret_key=SecretStr(CF_TEST_SECRET))
        assert CF_TEST_SECRET not in str(raised.value)
