"""Inventory Phase 3 — movements and control: inter-warehouse transfers, reservations and
approval-gated adjustments (docs/inventory/movements.md). Every stock change here is one call into
`app.inventory.ledger` (`post_move`, `reserve`, `release`); nothing in this package writes a
quantity itself.
"""
