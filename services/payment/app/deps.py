"""Wiring for the provider seam.

The service layer depends on a `PaymentProvider` (`app/providers/base.py`), never a concrete
processor. This is the single place the concrete one is chosen and built from configuration —
Razorpay today, a `StripeProvider` later — so selecting a provider is a wiring change here, not a
change to any rule. Exposed as a FastAPI dependency so a test overrides it the same way it
overrides a session.
"""

from __future__ import annotations

from functools import lru_cache

from app.config import settings
from app.providers.base import PaymentProvider
from app.providers.razorpay import build_provider


@lru_cache(maxsize=1)
def _build() -> PaymentProvider:
    cfg = settings()
    # Keys are absent in development; `build_provider` and every method still work, because the
    # provider's outbound HTTP is stubbed. The webhook secret is always present — the signature
    # check is exercised, never skipped.
    return build_provider(
        key_id=cfg.razorpay_key_id,
        key_secret=cfg.razorpay_key_secret.get_secret_value() if cfg.razorpay_key_secret else None,
        webhook_secret=cfg.razorpay_webhook_secret.get_secret_value(),
    )


def get_provider() -> PaymentProvider:
    """The configured provider. Cached per process; overridable in tests."""
    return _build()
