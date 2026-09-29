"""Wiring for the provider seam.

The service layer depends on a `PaymentProvider` (`app/providers/base.py`), never a concrete
processor. This is the single place the concrete one is chosen and built from configuration —
Razorpay today, a `StripeProvider` later — so selecting a provider is a wiring change here, not a
change to any rule. Exposed as a FastAPI dependency so a test overrides it the same way it
overrides a session.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache

from app.config import settings
from app.providers.base import CollectionsProvider, PaymentProvider
from app.providers.razorpay import build_collections_provider, build_provider
from app.providers.razorpay_wire import RazorpayHttpWire

#: Builds one merchant account's adapter from its opened keys:
#: `(key_id, key_secret, webhook_secret) -> CollectionsProvider`.
CollectionsProviderFactory = Callable[[str, str, str], CollectionsProvider]


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


def _collections_provider(key_id: str, key_secret: str, webhook_secret: str) -> CollectionsProvider:
    """A merchant's adapter, built per call from that merchant's opened keys and never cached —
    a cached adapter would hold a tenant's plaintext key secret in memory past the one call it
    exists for (ADR-0019 §3). The live wire only when the deployment enables it."""
    wire = (
        RazorpayHttpWire(key_id=key_id, key_secret=key_secret)
        if settings().collections_wire == "razorpay"
        else None
    )
    return build_collections_provider(
        key_id=key_id, key_secret=key_secret, webhook_secret=webhook_secret, wire=wire
    )


def get_collections_provider_factory() -> CollectionsProviderFactory:
    """How collections builds a merchant's adapter. A FastAPI dependency so a test overrides it
    the way it overrides `get_provider`; the sweep takes it as a constructor argument."""
    return _collections_provider
