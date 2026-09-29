"""The channel connector seam — one `Protocol` every external-channel client satisfies (Fulfilment
plan F11). The same discipline ADR-0019's `services/finance/app/eway/base.py` establishes for a
statutory portal, carried over to a commerce channel: a `Protocol` (structural, so an adapter and a
test double satisfy it by shape, not by inheritance), real validation and idempotency behind it,
and only the actual external wire call itself ever stubbed or mocked.

What is deliberately encoded here rather than left to each implementation:

* **The platform owns the canonical order, not the provider.** `ChannelOrder` is the neutral
  shape every provider's own order format maps onto — never the provider's raw JSON — so
  `app.channels.services.import_channel_order` reasons about one vocabulary regardless of which
  `ChannelClient` produced it, the same reason `EwayPayload` is "not the government JSON" (eway's
  own module docstring).
* **Refusal is data.** `ChannelRejected` carries the provider's own reason, the same bargain
  `EwayRejected` strikes with `EwayRejected.code`.
* **Three distinct failure shapes, not one generic exception**, because a caller must react to
  each differently: `ChannelUnavailable` (a timeout, a 5xx — retry the identical request later,
  nothing was learned about its contents), `ChannelRejected` (the provider understood the request
  and refused it — retrying unchanged will refuse again), and `ChannelAuthError` (the credential
  itself is no good — no retry helps until the connection is reconnected with a fresh key). This
  mirrors `EwayRejected` vs `EwayUnavailable`, plus one addition eway's stateless government
  portal has no equivalent of: a *tenant's own* API key that can expire or be revoked.
* **Amounts are integer minor units all the way to the wire**, same as every other money value on
  this platform (ADR-0000 §8) — a provider's own decimal-currency wire format is the adapter's
  job to convert to, at the last moment, never a `float` carried through this seam.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable


class ChannelUnavailable(Exception):
    """The provider could not be reached, or did not answer in time.

    Retryable with the identical request — nothing about the request was learned to be wrong,
    only that this attempt could not complete. The mock provider's chaos controls
    (`app.channels.mock_marketplace`) raise this for a simulated timeout or 5xx.
    """


class ChannelRejected(Exception):
    """The provider understood the request and refused it on its contents — a bad SKU, a
    negative quantity, an order it does not recognise. Not a transport failure: retrying the
    identical request will be refused again, the same bargain `EwayRejected` strikes."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class ChannelAuthError(Exception):
    """The credential this connection holds is no good — expired, revoked, or never valid.

    Distinct from `ChannelUnavailable`: a caller that retries an auth failure without reconnecting
    the channel will fail identically forever, so `app.channels.services` treats this one
    specially — it flips `Channel.status` to `error` rather than merely counting a failure toward
    the ordinary backoff threshold.
    """


@dataclass(frozen=True, slots=True)
class ChannelOrderLine:
    """One line of an external order, in the provider's own SKU vocabulary — resolved to an
    `InventoryItem` only later, by `app.channels.services` via `ChannelSkuMapping` (this module
    knows nothing of this platform's item master)."""

    external_sku: str
    quantity: Decimal
    #: What the customer was actually charged for one unit, on the channel — carried through as
    #: the order line's own explicit price (ADR-0024 §3 already allows a line to name its own
    #: price), so an imported order reflects what money really changed hands rather than
    #: whatever this org's own catalogue happens to say today.
    unit_price_minor: int


@dataclass(frozen=True, slots=True)
class ChannelOrder:
    """One external order, in the shape every provider's own format maps onto — never a
    provider's raw payload (see module docstring)."""

    external_order_id: str
    placed_at: datetime
    currency: str
    customer_name: str
    customer_phone: str
    lines: list[ChannelOrderLine] = field(default_factory=list)
    #: The provider's own payload, kept only for `ChannelOrderImport.raw_payload` — never
    #: interpreted by anything past the connector that produced it.
    raw: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ChannelOrderPage:
    """One page of `list_new_orders` — a provider's own order feed is paginated the way a real
    one (Shopify, Amazon) is, never "fetch everything in one call"."""

    orders: list[ChannelOrder]
    #: Opaque to this platform; stored on `Channel.last_order_cursor` and presented back
    #: unchanged on the next poll. `None` means "nothing further past this page today".
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class ChannelInventoryUpdate:
    """What one SKU's available quantity should read on the channel, in the item's own stock
    UOM — the platform-side number (`InventoryItem.available_milli`, converted to whole units by
    the caller); the provider's own inventory model is the adapter's problem to map onto."""

    external_sku: str
    available_qty: Decimal


@dataclass(frozen=True, slots=True)
class ChannelFulfilmentUpdate:
    """One shipment's tracking fact, pushed to the channel when F5's dispatch reaches a shipment
    state the channel cares about (`app.consumers`, bound to `operations.dispatch.shipment.*`)."""

    external_order_id: str
    #: dispatched | delivered | failed | returned — the same vocabulary
    #: `app.dispatch.services._TRANSITION_EVENTS` fires on, minus `IN_TRANSIT`'s own event name
    #: (`"dispatched"` here, matching what a channel's own tracking API calls the moment a
    #: shipment leaves — see `app.channels.services.push_fulfilment_update`'s mapping).
    status: str
    tracking_number: str | None
    carrier: str | None
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ChannelSyncAck:
    """What a successful push returns — deliberately thin. A provider's own generated ids (an
    order-fulfilment id, say) are not modelled here because nothing on this platform stores one
    yet; add a field when a real provider needs one recorded."""

    accepted: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ChannelHealth:
    """What `check_health` returns — a synchronous, side-effect-free "can this connection still
    talk to the provider" probe, distinct from an order/inventory/fulfilment sync attempt."""

    healthy: bool
    detail: str = ""


@runtime_checkable
class ChannelClient(Protocol):
    """The contract `app/channels/services.py` and `app/channels/sweep.py` depend on — never a
    concrete provider. A `Protocol` rather than an ABC, the same precedent `EwayClient` and
    `IrpClient` follow (`services/finance/app/eway/base.py`, `irp/base.py`)."""

    name: str

    async def check_health(self) -> ChannelHealth:
        """Raises `ChannelAuthError` for an invalid or expired credential, `ChannelUnavailable`
        if the provider cannot be reached. A clean return (`ChannelHealth`, `healthy` either way)
        means the provider was reached and answered — `healthy=False` is a provider-reported
        problem short of an auth failure (maintenance, a suspended shop)."""
        ...

    async def list_new_orders(self, *, cursor: str | None) -> ChannelOrderPage:
        """One page of orders placed since `cursor` (opaque, provider-defined; `None` means "from
        the beginning"). A provider's own order feed does not remove or consume an order once
        listed — a re-poll of the same or an earlier cursor may return orders already seen, which
        is exactly why import is idempotent on `ChannelOrder.external_order_id` rather than on
        "was it in this page". Raises `ChannelUnavailable` or `ChannelAuthError`."""
        ...

    async def push_inventory(self, updates: list[ChannelInventoryUpdate]) -> ChannelSyncAck:
        """Set the channel's own available quantity for every SKU named. Raises `ChannelRejected`
        for a SKU the provider does not recognise, `ChannelUnavailable`, or `ChannelAuthError`."""
        ...

    async def push_fulfilment_update(self, update: ChannelFulfilmentUpdate) -> ChannelSyncAck:
        """Tell the channel a shipment reached a new tracked state. Raises `ChannelRejected` for
        an order id the provider does not recognise, `ChannelUnavailable`, or `ChannelAuthError`.
        """
        ...
