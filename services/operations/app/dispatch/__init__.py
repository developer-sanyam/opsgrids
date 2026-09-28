"""Dispatch: the carrier handover and its own shipment lifecycle (ADR-0024 §6; `docs/fulfilment
/plan.md` F5) — the delivery/courier counterpart to `app.orders.services.hand_over_order`'s
counter/walk-in path.

Imports `app.orders` (for `check_billing_gate` and the shared `complete_handover` write) and
`app.fulfilment` (for the pick/pack reservation-transfer chain a dispatch issue draws down),
never the other way — `app.orders` is forbidden from importing this package or `app.fulfilment`
(`.importlinter`'s `orders-fulfilment-independence` contract), the same one-way dependency ADR-
0024 §1 draws between every fulfilment module.
"""

from __future__ import annotations
