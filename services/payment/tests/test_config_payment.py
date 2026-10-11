"""The provider switches and their start-up validators (ADR-0032): an unconfigured box must start; a
misconfigured one must refuse, naming the variable and never a value; and no combination lets keys
being *present* switch the network on.

Settings are built directly with `_env_file=None` so a developer's root `.env` can never decide the
outcome of a test about configuration.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError
from structlog.testing import capture_logs

from app.config import Settings, announce_test_mode
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
#: A recognisable tail for the values a refusal must never print.
_MARKER = "SECRETMARKERabcdef0123"  # pragma: allowlist secret


#: What a production-environment `Settings` needs of its own: real (not `dev-only-`) platform
#: secrets, which `ServiceSettings` refuses to start a production process without.
_PRODUCTION_SECRETS: dict[str, Any] = {
    "internal_service_token": SecretStr("a" * 48),
    "pii_encryption_key": SecretStr("b" * 48),
    "razorpay_webhook_secret": SecretStr("c" * 48),
}


#: What a developer's root `.env` may hold and `make test` exports into the process (`include .env`
#: + `export`). `_env_file=None` ignores the file but not the environment, so without this a
#: developer's sandbox keys would decide which branch of a validator these tests exercise.
_FROM_A_DEVELOPERS_ENV = (
    "RAZORPAY_KEY_ID",
    "RAZORPAY_KEY_SECRET",
    "RAZORPAY_WEBHOOK_SECRET",
    "CASHFREE_APP_ID",
    "CASHFREE_SECRET_KEY",
    "PAYMENT_ALLOW_LIVE_KEYS",
    "PAYMENT_ALLOW_TEST_KEYS",
    "PAYMENT_REQUIRE_LIVE",
    "ENVIRONMENT",
    "PII_ENCRYPTION_KEY",
    "INTERNAL_SERVICE_TOKEN",
)


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _FROM_A_DEVELOPERS_ENV:
        monkeypatch.delenv(name, raising=False)


def build(**kwargs: Any) -> Settings:
    if kwargs.get("environment") in ("production", "staging", "uat"):
        kwargs = {**_PRODUCTION_SECRETS, **kwargs}
    return Settings(_env_file=None, **kwargs)  # type: ignore[call-arg]


def refused(match: str, **kwargs: Any) -> None:
    with pytest.raises(ValidationError, match=match):
        build(**kwargs)


class TestTheWebhookSecret:
    """Empty env vars are ignored, so an unset RAZORPAY_WEBHOOK_SECRET used to fall back to the
    committed dev default — and anyone who read the repo could sign a payment-captured event."""

    @pytest.mark.parametrize("environment", ["production", "staging", "uat"])
    def test_the_committed_default_is_refused_on_a_real_box(self, environment: str) -> None:
        refused(
            "razorpay_webhook_secret",
            environment=environment,
            razorpay_webhook_secret=SecretStr("dev-only-razorpay-webhook-secret"),
        )

    def test_the_default_still_works_in_development(self) -> None:
        assert build().razorpay_webhook_secret.get_secret_value().startswith("dev-only-")


class TestAnUnconfiguredBox:
    def test_the_defaults_are_the_safe_ones(self) -> None:
        cfg = build()
        assert cfg.payment_provider == "razorpay"
        assert cfg.payment_wire == "stub"
        assert cfg.cashfree_env == "stub"
        assert cfg.payment_allow_live_keys is False
        assert cfg.payment_allow_test_keys is False
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


#: A Razorpay test pair on a live wire, and a Cashfree sandbox pair: what the staging box runs on.
_RZP_TEST_LIVE_WIRE: dict[str, Any] = {
    "payment_wire": "live",
    "razorpay_key_id": RZP_TEST_ID,
    "razorpay_key_secret": SecretStr(RZP_SECRET),
}
_CF_SANDBOX: dict[str, Any] = {
    "cashfree_env": "sandbox",
    "cashfree_app_id": SecretStr(CF_TEST_APP),
    "cashfree_secret_key": SecretStr(CF_TEST_SECRET),
}
_CF_UNSHAPED_SECRET = "dev-only-unshaped-secret"  # pragma: allowlist secret


class TestTheStagingTestKeysOverride:
    """`PAYMENT_ALLOW_TEST_KEYS` (2026-10-06): the owner's edge box says `ENVIRONMENT=production`
    but has no real paying customers, and is to run on Razorpay test keys and Cashfree sandbox
    keys. The default is unchanged — test keys are refused there — and the flag never lets real
    money move beside it."""

    @pytest.mark.parametrize(
        ("keys", "match"),
        [
            (_RZP_TEST_LIVE_WIRE, "PAYMENT_WIRE=live with test keys"),
            (_CF_SANDBOX, "CASHFREE_ENV=sandbox is refused"),
        ],
    )
    def test_the_default_still_refuses_test_keys_on_production(
        self, keys: dict[str, Any], match: str
    ) -> None:
        refused(match, environment="production", **keys)
        refused(match, environment="production", payment_allow_test_keys=False, **keys)

    def test_the_flag_allows_razorpay_test_keys_on_a_live_wire_on_production(self) -> None:
        cfg = build(environment="production", payment_allow_test_keys=True, **_RZP_TEST_LIVE_WIRE)
        assert cfg.payment_wire == "live" and cfg.test_mode_providers == ("razorpay",)

    def test_the_flag_allows_cashfree_sandbox_on_production_even_as_the_default(self) -> None:
        cfg = build(
            environment="production",
            payment_allow_test_keys=True,
            payment_provider="cashfree",
            **_CF_SANDBOX,
        )
        assert cfg.cashfree_env == "sandbox" and cfg.test_mode_providers == ("cashfree",)

    def test_the_flag_allows_both_providers_in_test_mode_at_once(self) -> None:
        cfg = build(
            environment="production",
            payment_allow_test_keys=True,
            **_RZP_TEST_LIVE_WIRE,
            **_CF_SANDBOX,
        )
        assert cfg.test_mode_providers == ("razorpay", "cashfree")

    def test_the_flag_without_keys_is_harmless(self) -> None:
        for environment in ("production", "staging", "local"):
            cfg = build(environment=environment, payment_allow_test_keys=True)
            assert (cfg.payment_wire, cfg.cashfree_env) == ("stub", "stub")
            assert cfg.test_mode_providers == ()

    def test_test_keys_on_the_stub_are_not_test_mode(self) -> None:
        """Keys never switch the network on, so keys alone are never "test mode" either."""
        cfg = build(
            environment="production",
            payment_allow_test_keys=True,
            razorpay_key_id=RZP_TEST_ID,
            razorpay_key_secret=SecretStr(RZP_SECRET),
        )
        assert cfg.payment_wire == "stub" and cfg.test_mode_providers == ()

    @pytest.mark.parametrize("environment", ["production", "local"])
    @pytest.mark.parametrize(
        "live",
        [
            pytest.param({"payment_allow_live_keys": True}, id="both-flags"),
            pytest.param(
                {
                    "payment_allow_live_keys": True,
                    "payment_wire": "live",
                    "razorpay_key_id": RZP_LIVE_ID,
                    "razorpay_key_secret": SecretStr(RZP_SECRET),
                },
                id="razorpay-live-allowed",
            ),
            pytest.param(
                {"razorpay_key_id": RZP_LIVE_ID, "razorpay_key_secret": SecretStr(RZP_SECRET)},
                id="razorpay-live-key-on-the-stub",
            ),
            pytest.param(
                {
                    "cashfree_app_id": SecretStr(CF_PROD_APP),
                    "cashfree_secret_key": SecretStr(CF_PROD_SECRET),
                },
                id="cashfree-production-key-on-the-stub",
            ),
            pytest.param(
                {
                    "payment_allow_live_keys": True,
                    "cashfree_env": "production",
                    "cashfree_app_id": SecretStr(CF_PROD_APP),
                    "cashfree_secret_key": SecretStr(CF_PROD_SECRET),
                },
                id="cashfree-production-allowed",
            ),
            pytest.param(
                {
                    "cashfree_env": "production",
                    "cashfree_app_id": SecretStr(CF_PROD_APP),
                    "cashfree_secret_key": SecretStr(_CF_UNSHAPED_SECRET),
                },
                id="cashfree-production-env",
            ),
        ],
    )
    def test_the_flag_beside_anything_that_moves_real_money_is_refused(
        self, live: dict[str, Any], environment: str
    ) -> None:
        """Nobody can run live money with the test flag on, whatever else is set."""
        refused(
            "PAYMENT_ALLOW_TEST_KEYS",
            environment=environment,
            payment_allow_test_keys=True,
            **live,
        )

    def test_live_keys_still_need_the_allow_live_flag_and_pass_without_the_test_flag(self) -> None:
        refused(
            "PAYMENT_ALLOW_LIVE_KEYS",
            environment="production",
            payment_wire="live",
            razorpay_key_id=RZP_LIVE_ID,
            razorpay_key_secret=SecretStr(RZP_SECRET),
        )
        cfg = build(
            environment="production",
            payment_allow_live_keys=True,
            payment_wire="live",
            razorpay_key_id=RZP_LIVE_ID,
            razorpay_key_secret=SecretStr(RZP_SECRET),
        )
        assert cfg.payment_allow_test_keys is False and cfg.test_mode_providers == ()

    def test_the_flag_relaxes_nothing_else(self) -> None:
        """Only the two production refusals move. The stub is still not a default provider on a
        production box, a half-set pair is still refused, and so on."""
        flag: dict[str, Any] = {"environment": "production", "payment_allow_test_keys": True}
        refused(
            "needs CASHFREE_ENV=production",
            payment_provider="cashfree",
            cashfree_env="stub",
            **flag,
        )
        refused("must be set together", razorpay_key_id=RZP_TEST_ID, **flag)
        refused("PAYMENT_WIRE=live needs", payment_wire="live", **flag)
        refused("needs CASHFREE_APP_ID", cashfree_env="sandbox", **flag)
        refused(
            "must be a Razorpay key id",
            payment_wire="live",
            razorpay_key_id="not-a-key",
            razorpay_key_secret=SecretStr(RZP_SECRET),
            **flag,
        )

    def test_read_from_the_environment_as_compose_passes_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Plain strings, the way compose hands them over; `false` (compose's default) is off."""
        for name, value in {
            "ENVIRONMENT": "production",
            "INTERNAL_SERVICE_TOKEN": "a" * 48,
            "PII_ENCRYPTION_KEY": "b" * 48,
            "RAZORPAY_WEBHOOK_SECRET": "c" * 48,
            "PAYMENT_WIRE": "live",
            "RAZORPAY_KEY_ID": RZP_TEST_ID,
            "RAZORPAY_KEY_SECRET": RZP_SECRET,
            "PAYMENT_ALLOW_TEST_KEYS": "false",
        }.items():
            monkeypatch.setenv(name, value)
        with pytest.raises(ValidationError, match="PAYMENT_WIRE=live with test keys"):
            Settings(_env_file=None)  # type: ignore[call-arg]
        monkeypatch.setenv("PAYMENT_ALLOW_TEST_KEYS", "true")
        cfg = Settings(_env_file=None)  # type: ignore[call-arg]
        assert cfg.payment_allow_test_keys is True and cfg.test_mode_providers == ("razorpay",)

    def test_a_refusal_never_prints_a_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PAYMENT_ALLOW_TEST_KEYS", "true")
        monkeypatch.setenv("RAZORPAY_KEY_ID", RZP_LIVE_ID)
        monkeypatch.setenv("RAZORPAY_KEY_SECRET", f"dev-only-{_MARKER}")
        with pytest.raises(ValidationError, match="PAYMENT_ALLOW_TEST_KEYS") as raised:
            Settings(_env_file=None)  # type: ignore[call-arg]
        text = str(raised.value)
        assert _MARKER not in text and RZP_LIVE_ID not in text and "input_value" not in text


_LIVE_RZP: dict[str, Any] = {
    "environment": "production",
    "payment_require_live": True,
    "payment_allow_live_keys": True,
    "payment_wire": "live",
    "razorpay_key_id": RZP_LIVE_ID,
    "razorpay_key_secret": SecretStr(RZP_SECRET),
}


class TestRequireLive:
    """`PAYMENT_REQUIRE_LIVE` (PLATFORM-23): on a production box, nothing but live money starts."""

    def test_live_razorpay_and_live_cashfree_start(self) -> None:
        build(**_LIVE_RZP)
        build(
            environment="production",
            payment_require_live=True,
            payment_allow_live_keys=True,
            payment_provider="cashfree",
            cashfree_env="production",
            cashfree_app_id=SecretStr(CF_PROD_APP),
            cashfree_secret_key=SecretStr(CF_PROD_SECRET),
        )

    def test_the_stub_wire_is_refused(self) -> None:
        refused(
            "PAYMENT_REQUIRE_LIVE=true needs PAYMENT_WIRE=live",
            environment="production",
            payment_require_live=True,
        )
        refused(
            "PAYMENT_REQUIRE_LIVE=true needs CASHFREE_ENV=production",
            environment="production",
            payment_require_live=True,
            payment_provider="cashfree",
        )

    def test_test_keys_are_refused_even_beside_a_live_default(self) -> None:
        refused(
            "PAYMENT_REQUIRE_LIVE=true refuses test keys",
            **_LIVE_RZP,
            cashfree_app_id=SecretStr(CF_TEST_APP),
            cashfree_secret_key=SecretStr(CF_TEST_SECRET),
        )
        refused(
            "PAYMENT_REQUIRE_LIVE=true refuses test keys",
            environment="production",
            payment_require_live=True,
            payment_wire="live",
            razorpay_key_id=RZP_TEST_ID,
            razorpay_key_secret=SecretStr(RZP_SECRET),
        )

    def test_the_test_mode_override_is_refused(self) -> None:
        refused(
            "PAYMENT_REQUIRE_LIVE=true refuses PAYMENT_ALLOW_TEST_KEYS",
            environment="production",
            payment_require_live=True,
            payment_allow_test_keys=True,
            payment_wire="live",
            razorpay_key_id=RZP_TEST_ID,
            razorpay_key_secret=SecretStr(RZP_SECRET),
        )

    def test_off_or_outside_production_it_changes_nothing(self) -> None:
        build(environment="production")
        build(environment="staging", payment_require_live=True)


#: The edge box's preflight mirror of these validators (`lib/payment-switches.sh`).
_PREFLIGHT_LIB = Path(__file__).resolve().parents[3] / "infrastructure/edge/lib/payment-switches.sh"
#: Every variable either side reads, cleared before each case.
_SWITCH_ENV = (
    "PAYMENT_PROVIDER",
    "PAYMENT_WIRE",
    "CASHFREE_ENV",
    "PAYMENT_ALLOW_LIVE_KEYS",
    "PAYMENT_ALLOW_TEST_KEYS",
    "PAYMENT_REQUIRE_LIVE",
    "RAZORPAY_KEY_ID",
    "RAZORPAY_KEY_SECRET",
    "CASHFREE_APP_ID",
    "CASHFREE_SECRET_KEY",
)
_STAGING: dict[str, str] = {
    "PAYMENT_ALLOW_TEST_KEYS": "true",
    "PAYMENT_WIRE": "live",
    "RAZORPAY_KEY_ID": RZP_TEST_ID,
    "RAZORPAY_KEY_SECRET": RZP_SECRET,
    "CASHFREE_ENV": "sandbox",
    "CASHFREE_APP_ID": CF_TEST_APP,
    "CASHFREE_SECRET_KEY": CF_TEST_SECRET,
}


@pytest.mark.skipif(
    shutil.which("bash") is None or not _PREFLIGHT_LIB.exists(),
    reason="needs bash and the repository's infrastructure/ (absent from a service image)",
)
class TestPreflightAgreesWithStartUp:
    """The box's preflight must refuse what `payment` refuses and pass what it starts with — for the
    test-keys override above all, since a disagreement either crash-loops `payment` after a green
    preflight or blocks a deploy `payment` would have accepted. Every compose service runs with
    `ENVIRONMENT=production`, so that is the environment both are judged in."""

    @pytest.mark.parametrize(
        "env",
        [
            pytest.param({}, id="unconfigured"),
            pytest.param({"PAYMENT_ALLOW_TEST_KEYS": "true"}, id="flag-alone"),
            pytest.param(_STAGING, id="staging"),
            pytest.param({**_STAGING, "PAYMENT_PROVIDER": "cashfree"}, id="staging-cashfree"),
            pytest.param({**_STAGING, "PAYMENT_ALLOW_TEST_KEYS": "1"}, id="staging-flag-1"),
            pytest.param({**_STAGING, "PAYMENT_ALLOW_TEST_KEYS": "false"}, id="flag-off"),
            pytest.param({**_STAGING, "PAYMENT_ALLOW_TEST_KEYS": ""}, id="flag-empty"),
            pytest.param({"PAYMENT_ALLOW_TEST_KEYS": "maybe"}, id="flag-not-a-bool"),
            pytest.param(
                {"PAYMENT_ALLOW_TEST_KEYS": "true", "PAYMENT_ALLOW_LIVE_KEYS": "true"},
                id="both-flags",
            ),
            pytest.param(
                {"PAYMENT_ALLOW_TEST_KEYS": "True", "PAYMENT_ALLOW_LIVE_KEYS": "true"},
                id="both-flags-capitalised",
            ),
            pytest.param(
                {
                    "PAYMENT_ALLOW_TEST_KEYS": "true",
                    "RAZORPAY_KEY_ID": RZP_LIVE_ID,
                    "RAZORPAY_KEY_SECRET": RZP_SECRET,
                },
                id="flag-and-a-live-razorpay-key",
            ),
            pytest.param(
                {
                    "PAYMENT_ALLOW_TEST_KEYS": "true",
                    "CASHFREE_APP_ID": CF_PROD_APP,
                    "CASHFREE_SECRET_KEY": CF_PROD_SECRET,
                },
                id="flag-and-a-production-cashfree-key",
            ),
            pytest.param(
                {
                    "PAYMENT_ALLOW_TEST_KEYS": "true",
                    "PAYMENT_ALLOW_LIVE_KEYS": "true",
                    "CASHFREE_ENV": "production",
                    "CASHFREE_APP_ID": CF_PROD_APP,
                    "CASHFREE_SECRET_KEY": CF_PROD_SECRET,
                },
                id="flag-and-cashfree-production",
            ),
            pytest.param(
                {
                    "PAYMENT_ALLOW_TEST_KEYS": "true",
                    "PAYMENT_PROVIDER": "cashfree",
                    "CASHFREE_ENV": "stub",
                },
                id="flag-cashfree-default-on-the-stub",
            ),
            pytest.param(
                {
                    "PAYMENT_ALLOW_LIVE_KEYS": "true",
                    "PAYMENT_WIRE": "live",
                    "RAZORPAY_KEY_ID": RZP_LIVE_ID,
                    "RAZORPAY_KEY_SECRET": RZP_SECRET,
                },
                id="live-without-the-flag",
            ),
            pytest.param({"PAYMENT_REQUIRE_LIVE": "true"}, id="require-live-on-the-stub"),
            pytest.param({**_STAGING, "PAYMENT_REQUIRE_LIVE": "true"}, id="require-live-staging"),
            pytest.param({"PAYMENT_REQUIRE_LIVE": "maybe"}, id="require-live-not-a-bool"),
            pytest.param(
                {
                    "PAYMENT_REQUIRE_LIVE": "true",
                    "PAYMENT_ALLOW_LIVE_KEYS": "true",
                    "PAYMENT_WIRE": "live",
                    "RAZORPAY_KEY_ID": RZP_LIVE_ID,
                    "RAZORPAY_KEY_SECRET": RZP_SECRET,
                },
                id="require-live-on-live-razorpay",
            ),
            pytest.param(
                {
                    "PAYMENT_REQUIRE_LIVE": "true",
                    "PAYMENT_ALLOW_LIVE_KEYS": "true",
                    "PAYMENT_PROVIDER": "cashfree",
                    "CASHFREE_ENV": "production",
                    "CASHFREE_APP_ID": CF_PROD_APP,
                    "CASHFREE_SECRET_KEY": CF_PROD_SECRET,
                },
                id="require-live-on-live-cashfree",
            ),
        ],
    )
    def test_both_reach_the_same_verdict(
        self, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in _SWITCH_ENV:
            monkeypatch.delenv(name, raising=False)
        for name, value in {
            "ENVIRONMENT": "production",
            "INTERNAL_SERVICE_TOKEN": "a" * 48,
            "PII_ENCRYPTION_KEY": "b" * 48,
            "RAZORPAY_WEBHOOK_SECRET": "c" * 48,
            **env,
        }.items():
            monkeypatch.setenv(name, value)
        try:
            Settings(_env_file=None)  # type: ignore[call-arg]
            starts = True
        except ValidationError:
            starts = False

        shell_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **env}
        # A fixed argv (no shell interpolation of `env`: it travels as the environment), the
        # committed library, bash from PATH as preflight itself runs it.
        done = subprocess.run(  # noqa: S603
            ["bash", "-c", f'source "{_PREFLIGHT_LIB}"; payment_switch_problems'],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
            env=shell_env,
        )
        preflight_passes = done.stdout.strip() == ""
        assert starts == preflight_passes, (starts, done.stdout)


class TestTheStartUpWarning:
    def test_the_flag_logs_one_warning_naming_test_mode_and_no_key(self) -> None:
        cfg = build(
            environment="production",
            payment_allow_test_keys=True,
            **_RZP_TEST_LIVE_WIRE,
            **_CF_SANDBOX,
        )
        with capture_logs() as logs:
            assert announce_test_mode(cfg) is True
        assert len(logs) == 1
        (entry,) = logs
        assert entry["log_level"] == "warning" and entry["event"] == "payment.test_mode.enabled"
        assert entry["mode"] == "test" and entry["environment"] == "production"
        assert entry["test_mode_providers"] == ["razorpay", "cashfree"]
        assert "TEST MODE" in entry["detail"]
        shown = repr(logs)
        for value in (RZP_TEST_ID, RZP_SECRET, CF_TEST_APP, CF_TEST_SECRET):
            assert value not in shown

    def test_off_it_is_silent_whatever_keys_are_set(self) -> None:
        cfg = build(environment="staging", **_RZP_TEST_LIVE_WIRE, **_CF_SANDBOX)
        with capture_logs() as logs:
            assert announce_test_mode(cfg) is False
        assert logs == []


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

    @pytest.mark.parametrize(
        ("env", "match"),
        [
            # A half-set Razorpay pair: the secret is the last field set, so it was the tail of the
            # input pydantic used to print.
            ({"RAZORPAY_KEY_SECRET": f"dev-only-{_MARKER}"}, "must be set together"),
            (
                {"RAZORPAY_KEY_ID": RZP_LIVE_ID, "RAZORPAY_KEY_SECRET": f"dev-only-{_MARKER}"},
                "PAYMENT_ALLOW_LIVE_KEYS",
            ),
            (
                {"CASHFREE_APP_ID": CF_PROD_APP, "CASHFREE_SECRET_KEY": f"cfsk_ma_prod_-{_MARKER}"},
                "PAYMENT_ALLOW_LIVE_KEYS",
            ),
            ({"CASHFREE_SECRET_KEY": f"dev-only-{_MARKER}"}, "must be set together"),
            # The base class's own refusal (a short PII key on production) with a provider pair
            # set: the same input, printed by a different validator.
            (
                {
                    "ENVIRONMENT": "production",
                    "INTERNAL_SERVICE_TOKEN": "a" * 48,
                    "PII_ENCRYPTION_KEY": "short",
                    "RAZORPAY_KEY_ID": RZP_TEST_ID,
                    "RAZORPAY_KEY_SECRET": f"dev-only-{_MARKER}",
                },
                "pii_encryption_key",
            ),
        ],
    )
    def test_a_refusal_read_from_the_environment_never_prints_a_value(
        self, monkeypatch: pytest.MonkeyPatch, env: dict[str, str], match: str
    ) -> None:
        """The real path: compose (or `make`'s exported `.env`) hands pydantic **plain strings**,
        not `SecretStr`s, so a test that passes `SecretStr(...)` cannot see what a crash at start-up
        prints. Every refusal names its variable; none prints the input it was given."""
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        with pytest.raises(ValidationError, match=match) as raised:
            Settings(_env_file=None)  # type: ignore[call-arg]
        text = str(raised.value)
        assert _MARKER not in text
        assert "input_value" not in text
