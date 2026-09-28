"""Warehouse geometry (plan §2/F7): floors, versioned layouts, per-version location placements,
the walk graph, and routing over it. Extends `app.inventory`'s `Warehouse`/`Location` — this
module owns no location identity of its own (CLAUDE.md: never duplicate it), only where things
sit and how to walk between them.

Anatomy: `selectors.py` (reads, plus the routing/validation reads that raise their own 404/409 —
no state changes, so no audit/outbox row belongs with them) and `services.py` (writes: floors,
draft versions, draft geometry, publish/rollback — every one leaves an audit row and, where it
changes what the rest of the platform can see, an outbox event). Depends on `app.inventory` for
`Warehouse`/`Location` reads; nothing in `app.inventory` depends back (ADR-0024 §1).
"""

from __future__ import annotations
