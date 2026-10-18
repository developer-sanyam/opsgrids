"""Which providers exist, and how each is built from configuration (ADR-0032).

`app/deps.py` is the single place a provider is chosen and cached; this module is the table it
chooses from, kept free of caching and of any import of `deps` so the adapters stay importable
without the service's wiring. Each builder takes the service's settings (read with `getattr`
defaults, so a minimal test stand-in for `Settings` still builds) and returns an adapter whose wire
is decided by **an explicit switch and never by the presence of keys**:

* Razorpay: `PAYMENT_WIRE=live` (and both keys) → `RazorpayHttpWire`; otherwise the in-process
  stub, whatever keys are present.
* Cashfree: `CASHFREE_ENV=sandbox|production` (and both credentials) → `CashfreeHttpWire`;
  `stub` → the stateful `CashfreeFakeWire`.

Existing rows are routed by their own `provider` column (`provider_for(charge.provider)`), so
adding a provider here never changes what happens to a charge that already exists.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from app.providers.base import PaymentProvider
from app.providers.cashfree import CashfreeProvider
from app.providers.cashfree_fake import CashfreeFakeWire
from app.providers.cashfree_wire import CashfreeHttpWire
from app.providers.razorpay import build_provider
from app.providers.razorpay_wire import RazorpayHttpWire

PROVIDER_RAZORPAY = "razorpay"
PROVIDER_CASHFREE = "cashfree"
PROVIDER_PHONEPE = "phonepe"
PROVIDER_PAYU = "payu"
PROVIDER_PAYTM = "paytm"
#: The providers platform billing can run on (each has a `BUILDERS` entry).
PROVIDER_NAMES: tuple[str, ...] = (PROVIDER_RAZORPAY, PROVIDER_CASHFREE)
#: The providers a tenant may connect for collections (ADR-0033): every platform provider, and the
#: merchant-only ones, which have a collections adapter (`app/deps.py`) and no platform builder.
COLLECTIONS_PROVIDER_NAMES: tuple[str, ...] = (
    *PROVIDER_NAMES,
    PROVIDER_PHONEPE,
    PROVIDER_PAYU,
    PROVIDER_PAYTM,
)


def _secret(value: Any) -> str | None:
    """A `SecretStr` (or None) as its plaintext, for the one place it is handed to a wire."""
    if value is None:
        return None
    text: str = value.get_secret_value() if hasattr(value, "get_secret_value") else str(value)
    return text or None


def build_razorpay(cfg: Any) -> PaymentProvider:
    key_id: str | None = getattr(cfg, "razorpay_key_id", None) or None
    key_secret = _secret(getattr(cfg, "razorpay_key_secret", None))
    wire = None
    if getattr(cfg, "payment_wire", "stub") == "live":
        if not key_id or not key_secret:
            # Settings refuses this at start-up; a stand-in that skipped it must not quietly run
            # on the stub while the owner believes the wire is live.
            raise RuntimeError("PAYMENT_WIRE=live needs RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET")
        wire = RazorpayHttpWire(key_id=key_id, key_secret=key_secret)
    # Keys are absent in development; every method still works, because the wire is stubbed. The
    # webhook secret is always present — the signature check is exercised, never skipped.
    return build_provider(
        key_id=key_id,
        key_secret=key_secret,
        webhook_secret=cfg.razorpay_webhook_secret.get_secret_value(),
        wire=wire,
    )


def build_cashfree(cfg: Any) -> PaymentProvider:
    env = str(getattr(cfg, "cashfree_env", "stub"))
    app_id = _secret(getattr(cfg, "cashfree_app_id", None))
    secret_key = _secret(getattr(cfg, "cashfree_secret_key", None))
    if env == "stub":
        return CashfreeProvider(wire=CashfreeFakeWire(), secret_key=secret_key, mode="stub")
    if env not in ("sandbox", "production"):
        raise ValueError(f"unknown CASHFREE_ENV {env!r}")
    if not app_id or not secret_key:
        raise RuntimeError(f"CASHFREE_ENV={env} needs CASHFREE_APP_ID and CASHFREE_SECRET_KEY")
    live: Literal["sandbox", "production"] = "sandbox" if env == "sandbox" else "production"
    return CashfreeProvider(
        wire=CashfreeHttpWire(app_id=app_id, secret_key=secret_key, env=live),
        secret_key=secret_key,
        mode=live,
    )


BUILDERS: dict[str, Callable[[Any], PaymentProvider]] = {
    PROVIDER_RAZORPAY: build_razorpay,
    PROVIDER_CASHFREE: build_cashfree,
}


def build(name: str, cfg: Any) -> PaymentProvider:
    """The adapter for provider `name`, or a `KeyError`-free `ValueError` for an unknown one."""
    builder = BUILDERS.get(name)
    if builder is None:
        raise ValueError(f"unknown payment provider {name!r}")
    return builder(cfg)
