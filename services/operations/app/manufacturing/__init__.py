"""Manufacturing (ADR-0014 §7, `docs/inventory/plan.md` Phase 5): versioned multi-level BOMs and
production orders that move stock only through `app.inventory.ledger`.

Same anatomy as every service, inside the package: `models` < `domain` < `schemas` <
`selectors` < `services` < `api`. See `docs/inventory/manufacturing.md`.
"""
