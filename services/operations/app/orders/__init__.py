"""The customer order module (ADR-0024 §1) — the sell-side document, its lines, its state
machine and accept-reserves-stock, per `docs/fulfilment/plan.md` F2.

`app/orders/` imports `app.inventory` (item, location and ledger reads/writes) and nothing else
in the four fulfilment modules the plan lays out; `app/fulfilment/`, `app/warehouse/` and
`app/dispatch/` (F3+) import `app.orders`, never the other way — the dependency direction
ADR-0024 §1 fixes so a later module cannot reach back into this one's internals.
"""

from __future__ import annotations
