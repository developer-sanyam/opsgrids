"""Wiring for the provider seam.

The service layer depends on a `PaymentProvider` (`app/providers/base.py`), never a concrete
processor. This is the single place the concrete ones are chosen and built from configuration,
so selecting a provider is a wiring change here, not a change to any rule. Exposed as FastAPI
dependencies so a test overrides them the same way it overrides a session.

**Two questions, answered differently (ADR-0029).**

* *Which provider takes a NEW charge?* `get_provider()` — the configured default
  (`PAYMENT_PROVIDER`, `razorpay` unless the owner changes it). Zero-argument, so every existing
  call site and test override keeps working.
* *Which provider finishes an EXISTING row?* `provider_for(name)` — **the row's own provider**
  (`charges.provider`), whatever the default is now. A Razorpay charge left `created` is looked up,
  resumed and polled through Razorpay while the default is Cashfree: Cashfree cannot find
  Razorpay's Order, and "creating" it again there is a second debit. The webhook routes bind to
  their own adapter the same way (`razorpay_provider`, `cashfree_provider`): a Razorpay delivery
  is never verified with Cashfree's secret because of a default flip.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from functools import cache, lru_cache
from typing import Annotated, Any, Literal, Protocol

from fastapi import Depends

from app.config import settings
from app.providers import registry
from app.providers.base import CollectionsProvider, PaymentProvider
from app.providers.cashfree_collections import CashfreeCollectionsProvider
from app.providers.cashfree_fake import CashfreeFakeWire
from app.providers.cashfree_wire import CashfreeHttpWire
from app.providers.razorpay_collections import build_collections_provider
from app.providers.razorpay_wire import RazorpayHttpWire


class CollectionsProviderFactory(Protocol):
    """Builds one merchant account's adapter from its opened keys and the account's own provider
    and mode (`test` / `live`) — never from configuration alone, and never with a fallback to the
    platform's own credentials. For a provider with no webhook secret (Cashfree) `webhook_secret`
    is empty and `key_secret` is what verifies a delivery."""

    def __call__(
        self,
        key_id: str,
        key_secret: str,
        webhook_secret: str,
        *,
        provider: str = ...,
        mode: str = ...,
    ) -> CollectionsProvider: ...


#: Resolves an existing row's provider by name — what the sweeps and the poller use.
ProviderResolver = Callable[[str], PaymentProvider]


def default_provider_name() -> str:
    """The provider new charges are raised on. Read with a default so the minimal settings
    stand-ins some tests monkeypatch in still resolve."""
    return str(getattr(settings(), "payment_provider", registry.PROVIDER_RAZORPAY))


@cache
def provider_for(name: str) -> PaymentProvider:
    """The adapter for provider `name`, built once per process from configuration. The Razorpay
    adapter's wire is live only under `PAYMENT_WIRE=live` and the Cashfree adapter's only under
    `CASHFREE_ENV=sandbox|production`; **keys never decide it** (`registry`)."""
    return registry.build(name, settings())


@lru_cache(maxsize=1)
def _build() -> PaymentProvider:
    return provider_for(default_provider_name())


def reset_providers() -> None:
    """Forget every cached adapter (tests; never called by the service itself)."""
    _build.cache_clear()
    provider_for.cache_clear()


def get_provider() -> PaymentProvider:
    """The provider new charges are raised on. Cached per process; overridable in tests."""
    return _build()


def resolve_provider(name: str) -> PaymentProvider:
    """The adapter for an existing row's provider: `provider_for`, named for its use."""
    return provider_for(name)


def razorpay_provider(
    default: Annotated[PaymentProvider, Depends(get_provider)],
) -> PaymentProvider:
    """The Razorpay adapter for the Razorpay webhook route: the default when it is Razorpay (so a
    test that overrides `get_provider` is verifying with the adapter it signed with), otherwise
    the registry's, so a default flip to Cashfree never makes Cashfree verify a Razorpay body."""
    return default if default.name == registry.PROVIDER_RAZORPAY else provider_for("razorpay")


def cashfree_provider(
    default: Annotated[PaymentProvider, Depends(get_provider)],
) -> PaymentProvider:
    """The Cashfree adapter for the Cashfree webhook route (the same shape as above)."""
    return default if default.name == registry.PROVIDER_CASHFREE else provider_for("cashfree")


def _collections_wire_live(cfg: Any, provider: str) -> bool:
    """Whether `provider`'s collections wire is switched on. `collections_wire_for` when the
    settings have it; the legacy reading (`COLLECTIONS_WIRE=razorpay` is Razorpay live) when it is
    a minimal stand-in that does not."""
    per_provider = getattr(cfg, "collections_wire_for", None)
    if callable(per_provider):
        return bool(per_provider(provider) == "live")
    return bool(getattr(cfg, "collections_wire", "stub") == provider)


#: The in-process Cashfree a merchant's stub adapter runs on, one per App ID, so what one call
#: created is still there on the next (a status check finds the Order a checkout made) — as a real
#: account would be. It holds no credential (a fake wire takes none), only stub Orders, and exists
#: only while `COLLECTIONS_CASHFREE_WIRE=stub`.
_CASHFREE_STUB_WIRES: dict[str, CashfreeFakeWire] = {}
_CASHFREE_STUB_LOCK = threading.Lock()


def _cashfree_stub_wire(app_id: str) -> CashfreeFakeWire:
    with _CASHFREE_STUB_LOCK:
        wire = _CASHFREE_STUB_WIRES.get(app_id)
        if wire is None:
            wire = _CASHFREE_STUB_WIRES[app_id] = CashfreeFakeWire()
        return wire


def _collections_provider(
    key_id: str,
    key_secret: str,
    webhook_secret: str,
    *,
    provider: str = registry.PROVIDER_RAZORPAY,
    mode: str = "test",
) -> CollectionsProvider:
    """A merchant's adapter, built per call from that merchant's opened keys and never cached —
    a cached adapter would hold a tenant's plaintext key secret in memory past the one call it
    exists for (ADR-0019 §3). The live wire only when the deployment enables it for that provider
    (`collections_wire_for`); the platform's own `RAZORPAY_*` / `CASHFREE_*` credentials are never
    read here — a merchant's adapter has only the merchant's keys, or none."""
    cfg = settings()
    if provider == registry.PROVIDER_RAZORPAY:
        wire = (
            RazorpayHttpWire(key_id=key_id, key_secret=key_secret)
            if _collections_wire_live(cfg, registry.PROVIDER_RAZORPAY)
            else None
        )
        return build_collections_provider(
            key_id=key_id, key_secret=key_secret, webhook_secret=webhook_secret, wire=wire
        )
    if provider == registry.PROVIDER_CASHFREE:
        env: Literal["sandbox", "production"] = "production" if mode == "live" else "sandbox"
        if _collections_wire_live(cfg, registry.PROVIDER_CASHFREE):
            return CashfreeCollectionsProvider(
                wire=CashfreeHttpWire(app_id=key_id, secret_key=key_secret, env=env),
                secret_key=key_secret,
                mode=env,
            )
        return CashfreeCollectionsProvider(
            wire=_cashfree_stub_wire(key_id), secret_key=key_secret, mode="stub"
        )
    raise ValueError(f"{provider!r} is not a collections provider")


def get_collections_provider_factory() -> CollectionsProviderFactory:
    """How collections builds a merchant's adapter. A FastAPI dependency so a test overrides it
    the way it overrides `get_provider`; the sweep takes it as a constructor argument."""
    return _collections_provider
