"""External sales-channel connectors (Fulfilment plan F11; ADR-0024 §1's module-boundary
discipline extended: `channels` depends on `orders` and `inventory`, never on `fulfilment` or
`dispatch` — a shipment's own tracking sync reaches this module through the outbox/consumer round
trip (`app.consumers`), the same "no direct import across the fulfilment/dispatch boundary" rule
`orders` itself already follows (`.importlinter`'s `channels-boundary` contract)."""

from __future__ import annotations
