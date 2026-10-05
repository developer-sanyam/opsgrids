"""Configuration for the payment service."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import SecretStr, model_validator

from app.providers.wire import MAX_DECLARED_CALL_BUDGET_SECONDS
from platform_core.config import ServiceSettings, settings_for

#: Razorpay key ids carry their mode in the prefix: `rzp_test_…` / `rzp_live_…`.
_RAZORPAY_KEY_ID = re.compile(r"^rzp_(test|live)_[A-Za-z0-9]{6,40}$")
#: Cashfree secret keys carry theirs too (`cfsk_ma_test_…` / `cfsk_ma_prod_…`), and a sandbox App ID
#: starts with `TEST`. Third-party-sourced shapes: a mismatch is a safe false-refuse, never a
#: reason to let a key through, and a key that matches neither is judged by the switch alone.
_CASHFREE_SECRET_TEST_PREFIX = "cfsk_ma_test_"  # noqa: S105
_CASHFREE_SECRET_PROD_PREFIX = "cfsk_ma_prod_"  # noqa: S105
_CASHFREE_APP_ID_TEST_PREFIX = "TEST"


class Settings(ServiceSettings):
    service_name: str = "payment"
    db_schema: str = "payment"
    #: Its own NOBYPASSRLS role, granted DML on the `payment` schema and nothing else. Every
    #: business table here is tenant-scoped — mandates, charges, refunds and the provider_events
    #: inbox all carry a tenant policy — so the API must connect as a role row-level security can
    #: bind (ADR-0002 finding 2). Append-only on `refunds` and `provider_events` is enforced
    #: *below* this role with a trigger, not by trusting the application to behave.
    db_app_role: str | None = "opsgrids_payment"
    db_admin_role: str | None = "opsgrids_admin"
    #: The outbox relay's role: BYPASSRLS, SELECT+UPDATE on `outbox` only. payment emits
    #: `commerce.payment.captured` / `commerce.payment.failed`, and a failure is what closes the
    #: suspend loop (ADR-0004 §5) — so the relay draining this outbox is load-bearing.
    db_outbox_role: str | None = "opsgrids_outbox"

    # --- provider (Razorpay and Cashfree, ADR-0029) ---------------------------------------------
    #: The provider **new** platform charges are raised on. An existing charge, mandate and
    #: webhook keep the provider they were created under for life (`charges.provider`): a Razorpay
    #: charge left `created` is finished through Razorpay while this says Cashfree, because a
    #: provider cannot find, debit or refund another's payment. Flipping this is therefore not a
    #: migration, and flipping it back is the rollback. An unknown value fails start-up.
    payment_provider: Literal["razorpay", "cashfree"] = "razorpay"

    #: Whether the **platform's Razorpay** adapter talks to Razorpay over HTTPS. `stub` (the
    #: default, everywhere): no socket is ever opened, **whatever keys are present** — keys in a
    #: developer's `.env` never switch the network on. `live`: customer-present checkout against
    #: Razorpay (Phase A; there is no mandate debit). Needs both keys.
    payment_wire: Literal["stub", "live"] = "stub"

    #: Whether keys that move real money (`rzp_live_…`, or Cashfree `production`) are accepted at
    #: all. Off: nothing the environment holds can move real money until the owner says so, the
    #: same switch `COLLECTIONS_ALLOW_LIVE_KEYS` is for tenants.
    payment_allow_live_keys: bool = False

    #: Razorpay credentials. **Optional**: an unconfigured box passes preflight and runs on the
    #: stub. The key id is public by design (Checkout is opened in the browser with it); the key
    #: secret is a `SecretStr` and is used for nothing but a checkout callback's HMAC and the
    #: wire's basic auth. The webhook secret verifies the HMAC Razorpay puts on every callback; in
    #: dev a fixed dev-only value stands in so the signature check is exercised, never skipped.
    razorpay_key_id: str | None = None
    razorpay_key_secret: SecretStr | None = None
    razorpay_webhook_secret: SecretStr = SecretStr("dev-only-razorpay-webhook-secret")

    #: `stub` (the default): an in-process stateful fake — no network, **whatever keys are
    #: present**. `sandbox` / `production`: Cashfree over HTTPS against that environment's base
    #: URL; both need both credentials. This is the one switch for "wire" and "environment":
    #: keys present never enable the network by themselves.
    cashfree_env: Literal["stub", "sandbox", "production"] = "stub"
    #: Cashfree's App ID (`x-client-id`) and Secret Key (`x-client-secret`). Optional-empty. The
    #: secret key also signs every webhook (Cashfree has no separate webhook secret). Both are
    #: `SecretStr`: neither is ever in a repr, a log or an exception.
    cashfree_app_id: SecretStr | None = None
    cashfree_secret_key: SecretStr | None = None

    #: How long an unpaid checkout stays payable before it is closed (and, at Cashfree, expires at
    #: the provider too). A day: long enough to come back to it, short enough that a stale
    #: checkout is not left open.
    checkout_ttl_seconds: int = 86_400
    #: How often the poller (`app/checkout/cron.py`) asks the provider about a charge that has an
    #: Order and no terminal state — the only way a platform charge learns an outcome while the
    #: webhook is private. Both the sweep's own tick and the minimum gap between two such
    #: questions about one charge.
    charge_poll_interval_seconds: float = 30.0

    # --- the stuck-charge sweep (app/cron.py) ---------------------------------------
    #: How long a charge may sit `created` with no `provider_payment_id` before the sweep
    #: resumes it. Comfortably longer than any provider call is expected to take, so a charge
    #: genuinely mid-flight in the request that staged it is never mistaken for stuck.
    charge_reclaim_after_seconds: int = 300
    charge_sweep_poll_interval_seconds: float = 60.0
    #: How many provider calls one charge may claim (the stager's included) before the sweep
    #: stops, fails it, and emits `commerce.payment.failed` so dunning takes over (D3).
    charge_max_attempts: int = 5
    #: How long one synchronous provider call may take before the caller stops waiting (D3). The
    #: call runs on a worker thread (`services.call_provider_charge_off_loop`); a call that
    #: outlives this leaves the charge `created`, and the sweep resumes it once the lease expires
    #: — lookup-before-create finding whatever the abandoned call achieved. 45s, not 30: a live
    #: Razorpay charge makes up to five requests of (2s connect + 5s read) each — 35s — where 30s
    #: left two seconds of slack for four. The call also runs under an overall monotonic deadline
    #: of exactly this long (`providers/wire.py`), and the validator below keeps it above the
    #: worst live adapter's declared budget.
    provider_call_timeout_seconds: float = 45.0
    #: How many times, past `charge_max_attempts`, the sweep may try its **final lookup** (the
    #: provider's Order and payment, never a create) before it gives up asking and fails the
    #: charge as `lookup_unavailable` — failing without having looked would risk dunning a
    #: customer whose last attempt the provider did take (D3).
    charge_final_lookup_attempts: int = 3

    #: Runs this service's worker loops — the charge consumers, the stuck-charge sweep and the
    #: charge poller (`app/worker.py`) — as supervised background tasks inside the API process
    #: instead of the separate `payment-worker` container (the memory-headroom follow-up that
    #: folds every service's remaining worker in). Off by default everywhere except the edge
    #: box's own `.env`: `bin/worker` still runs `app.worker` standalone unchanged, and setting
    #: this to `false` while adding `split-workers` to `EDGE_PROFILES` is the rollback
    #: (RUNBOOK.md).
    run_consumers_in_api: bool = False

    # --- tenant collections (app/collections/, ADR-0024 §8) ---------------------------------
    #: `stub` (the default, everywhere): a merchant's adapter answers from the in-process stub —
    #: no socket is opened, no money can move, and a checkout is marked as not openable in a
    #: browser. `razorpay`: a merchant's adapter talks to Razorpay over HTTPS with **that
    #: merchant's own keys** (`app/providers/razorpay_wire.py`). The value is the provider whose
    #: collections wire is live, and the legacy `razorpay` keeps meaning exactly that — Razorpay
    #: live — so an environment that already says it cannot silently fall back to the stub (see
    #: `collections_wire_for`). The platform's own provider is switched by `PAYMENT_WIRE`, never
    #: by this. Turning this on is an owner decision, made in the box's `.env`.
    collections_wire: Literal["stub", "razorpay"] = "stub"
    #: Whether a merchant may connect **live** keys (`rzp_live_…`). Off: only test-mode keys are
    #: accepted, so nothing a tenant configures can move real money until the owner says so.
    collections_allow_live_keys: bool = False
    #: How long one collection provider call (an Order create, a status check, a refund) may take
    #: before the caller stops waiting. The call runs on a worker thread; an abandoned one is safe
    #: to repeat (Orders are looked up by receipt first, refunds carry an idempotency key).
    collections_call_timeout_seconds: float = 15.0
    #: How long an unpaid checkout stays open before the expiry sweep closes it (after asking the
    #: provider, server to server, that nothing was paid). A day: long enough for a customer to
    #: come back to a payment link, short enough that a stale checkout is not left payable.
    collection_checkout_ttl_seconds: int = 86_400
    collection_sweep_poll_interval_seconds: float = 300.0
    #: The most collections one reconciliation report asks the provider about, and how long the
    #: whole report may spend asking — under the gateway's 30 s, so a slow provider shows up as
    #: "unchecked" rows rather than a gateway timeout.
    collections_reconciliation_limit: int = 50
    collections_reconciliation_budget_seconds: float = 20.0

    @property
    def collection_lease_seconds(self) -> int:
        """The right-to-call lease on one collection: longer than any call it covers, twice over
        plus a minute — the same margin `charge_reclaim_after_seconds` is held to."""
        return int(2 * self.collections_call_timeout_seconds + 60)

    def collections_wire_for(self, provider: str) -> Literal["stub", "live"]:
        """Whether `provider`'s collections wire is live. Per provider, so a second provider can
        be switched on without touching the first; `COLLECTIONS_WIRE=razorpay` (the only
        non-stub value there is) means Razorpay live, as it always has."""
        return "live" if self.collections_wire == provider else "stub"

    @model_validator(mode="after")
    def _call_timeout_covers_the_wire_budget(self) -> Settings:
        """The overall call timeout must cover what the worst live adapter declares it can spend
        (every request timing out in turn): a smaller number abandons calls that were within their
        own budget, and leaves the stuck-charge sweep resuming charges that were merely slow."""
        if self.provider_call_timeout_seconds < MAX_DECLARED_CALL_BUDGET_SECONDS:
            raise ValueError(
                "provider_call_timeout_seconds must be at least "
                f"{MAX_DECLARED_CALL_BUDGET_SECONDS:g}s, the live wires' declared worst case; got "
                f"{self.provider_call_timeout_seconds:g}s"
            )
        return self

    @model_validator(mode="after")
    def _razorpay_switch_and_keys(self) -> Settings:
        """`PAYMENT_WIRE=live` needs a real pair; a half-set pair is refused whatever the wire; a
        live-shaped key is refused unless allowed; test keys never reach a production box on a
        live wire (a customer could pay a real invoice with a public test card). Refused at
        start-up, with the variable named and never a value."""
        key_id = self.razorpay_key_id
        secret = self.razorpay_key_secret
        if bool(key_id) != bool(secret and secret.get_secret_value()):
            raise ValueError("RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET must be set together")
        mode: str | None = None
        if key_id:
            match = _RAZORPAY_KEY_ID.fullmatch(key_id)
            mode = match.group(1) if match else None
            if mode == "live" and not self.payment_allow_live_keys:
                raise ValueError(
                    "RAZORPAY_KEY_ID is a live key (rzp_live_…) and PAYMENT_ALLOW_LIVE_KEYS is "
                    "not true: live keys move real money and are refused until the owner allows"
                )
        if self.payment_wire == "live":
            if not key_id:
                raise ValueError("PAYMENT_WIRE=live needs RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET")
            if mode is None:
                raise ValueError(
                    "RAZORPAY_KEY_ID must be a Razorpay key id (rzp_test_… or rzp_live_…)"
                )
            if mode == "test" and self.environment == "production":
                raise ValueError(
                    "PAYMENT_WIRE=live with test keys (rzp_test_…) is refused when "
                    "ENVIRONMENT=production: real invoices would be payable with a test card"
                )
        return self

    @model_validator(mode="after")
    def _cashfree_switch_and_keys(self) -> Settings:
        """The same rules for Cashfree. `sandbox` needs both credentials and never reaches a
        production box (sandbox accepts public test cards and VPAs: real invoices payable for
        free); `production` needs both, needs `PAYMENT_ALLOW_LIVE_KEYS`, and refuses test-shaped
        keys; production-shaped keys are refused unless allowed, on any switch; and
        `PAYMENT_PROVIDER=cashfree` on a production box needs the real thing, not the stub."""
        app_id = self.cashfree_app_id.get_secret_value() if self.cashfree_app_id else ""
        secret = self.cashfree_secret_key.get_secret_value() if self.cashfree_secret_key else ""
        if bool(app_id) != bool(secret):
            raise ValueError("CASHFREE_APP_ID and CASHFREE_SECRET_KEY must be set together")
        prod_shaped = secret.startswith(_CASHFREE_SECRET_PROD_PREFIX)
        test_shaped = secret.startswith(_CASHFREE_SECRET_TEST_PREFIX) or app_id.startswith(
            _CASHFREE_APP_ID_TEST_PREFIX
        )
        if prod_shaped and not self.payment_allow_live_keys:
            raise ValueError(
                "CASHFREE_SECRET_KEY is a production key and PAYMENT_ALLOW_LIVE_KEYS is not "
                "true: production keys move real money and are refused until the owner allows"
            )
        if self.cashfree_env != "stub" and not (app_id and secret):
            raise ValueError(
                f"CASHFREE_ENV={self.cashfree_env} needs CASHFREE_APP_ID and CASHFREE_SECRET_KEY"
            )
        if self.cashfree_env == "sandbox":
            if self.environment == "production":
                raise ValueError(
                    "CASHFREE_ENV=sandbox is refused when ENVIRONMENT=production: sandbox "
                    "accepts public test cards, so real invoices would be payable for free"
                )
            if prod_shaped:
                raise ValueError("CASHFREE_ENV=sandbox with production-shaped keys is refused")
        if self.cashfree_env == "production":
            if not self.payment_allow_live_keys:
                raise ValueError("CASHFREE_ENV=production needs PAYMENT_ALLOW_LIVE_KEYS=true")
            if test_shaped:
                raise ValueError("CASHFREE_ENV=production with test-shaped keys is refused")
        if (
            self.payment_provider == "cashfree"
            and self.environment == "production"
            and self.cashfree_env != "production"
        ):
            raise ValueError(
                "PAYMENT_PROVIDER=cashfree on a production box needs CASHFREE_ENV=production "
                "(the stub and the sandbox cannot collect real money)"
            )
        return self

    @model_validator(mode="after")
    def _lease_outlives_the_call(self) -> Settings:
        """The lease must outlast any call it covers, twice over plus a minute: a call that
        outlives its lease can still land a payment after the sweep's lookup-before-create found
        none — a second debit (D3). Refused at start-up rather than discovered in production."""
        floor = 2 * self.provider_call_timeout_seconds + 60
        if self.charge_reclaim_after_seconds < floor:
            raise ValueError(
                "charge_reclaim_after_seconds must be at least 2 * provider_call_timeout_seconds"
                f" + 60 ({floor:g}s); got {self.charge_reclaim_after_seconds}s"
            )
        return self


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
