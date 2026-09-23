"""Inventory: item master, locations, the stock ledger and its balances (ADR-0014).

* `quantity` — exact milli-unit quantities and fixed UOM conversions. Pure, no I/O.
* `ledger` — `post_move`, `reserve`, `release`: the only code that writes the ledger, the
  balances or reservations.
* `reconcile` — ledger ↔ balance verification. Detects and records drift; never repairs it.
* `selectors` / `services` — inventory and warehouse reads and writes, built on `ledger`.
"""
