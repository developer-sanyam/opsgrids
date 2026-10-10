"""Receiving and purchase (inventory Phase 2): requisitions, goods receipts, unit conversions.

* `domain` — pure rules: the requisition state machine, tolerance, unit conversion, PO status.
* `receipts` — posting a goods receipt: the one writer from a delivery to stock, through
  `app.inventory.ledger.post_move`.
* `requisitions` — request, approve, convert to a purchase order.
* `conversions` — item-specific container ratios (`uom_conversions`).
* `selectors` / `schemas` / `api` — reads, wire contracts and routes.

See `docs/inventory/receiving.md`.
"""
