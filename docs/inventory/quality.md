# Quality — QC templates, inspections, release and reject

Phase 4 of [plan.md](plan.md); builds on the stock kernel in [ledger.md](ledger.md) and the
decisions in [ADR-0014](../adr/0014-inventory-and-manufacturing.md). Code: `services/operations/app/quality/`.

## 1. The idea in one paragraph

An **inspection** is a decision about a quantity of stock. Opening one moves that quantity
`AVAILABLE → QUALITY_HOLD`, so nothing can sell, reserve or issue it while it is being checked.
An inspector records readings against a **QC template**; the server judges each reading. An
approver then **releases** the stock (`QUALITY_HOLD → AVAILABLE`, all or part) or a rejector
**rejects** it (`QUALITY_HOLD → REJECTED` or `QUARANTINE`), and the rejected stock is finally
**disposed of** — returned to the vendor or scrapped. Every stock movement is a `ledger.post_move`
call; nothing in `app/quality/` writes a ledger row or a balance.

## 2. Tables

All four carry `organization_id`, `ENABLE`+`FORCE` row-level security and the standard
`tenant_isolation` policy (migration `a3c5e8f1b290`, up and down; its `down_revision` is the single place the migration is parented).

| Table | Holds |
|---|---|
| `qc_templates` | `code` (unique per org among live rows), `name`, `kind` (`incoming`/`in-process`/`finished`), optional `item_id`, `is_active` |
| `qc_template_params` | ordered parameters: `name`, `kind` (`numeric`/`pass_fail`), `unit`, `min_milli`/`max_milli`, `is_critical` |
| `inspections` | item, location, optional lot/roll/supplier/template, `quantity_milli`, `status`, `accepted_milli`/`rejected_milli`, `rejected_bucket`, the ids of the kernel moves (`hold_move_id`, `decision_move_id`, `disposition_move_id`), disposition, and the idempotency keys |
| `inspection_results` | one row per measured parameter, **snapshotting** its name, bounds and criticality, plus the value and the server's `passed` |

`item_id`, `location_id`, `lot_id`, `roll_id`, `template_id` and `supplier_id` are **plain indexed
UUIDs, deliberately not foreign keys**: an inspection is a record about stock at a moment that must outlive — and never block — the rename or
retirement of what it points at. Every read re-checks the organization on the referenced row. The
only foreign keys are within the service (`template → params`, `inspection → results`).

Bounds, readings and quantities are integers of milli-units (three implied decimals) in the
database and **decimal strings** on the wire, exactly as quantities are everywhere else
(`app/inventory/quantity.py`). No float touches one — on the server or in the UI.

## 3. The state machine

`app/quality/domain.py::INSPECTION_TRANSITIONS`, exhaustively tested pair by pair:

```
PENDING ──► PASSED | FAILED | HOLD | REWORK | PARTIAL_PASS
HOLD    ──► PASSED | FAILED | REWORK | PARTIAL_PASS
REWORK  ──► PENDING (re-inspect) | FAILED
PASSED, FAILED, PARTIAL_PASS: terminal
```

A terminal inspection is never edited: a correction is a new inspection. `HOLD → HOLD` and every
other pair not listed is a 409. Readings can be recorded only while `PENDING` or `HOLD`.

## 4. Stock movements (all through `post_move`)

| Step | Move | Reason |
|---|---|---|
| Open (default `hold_stock: true`) | `relocation`: `AVAILABLE −q`, `QUALITY_HOLD +q` | `quality_hold` |
| Release all (`PASSED`) | `relocation`: `QUALITY_HOLD −q`, `AVAILABLE +q` | `quality_release` |
| Release part (`PARTIAL_PASS`) | `relocation`: `QUALITY_HOLD −q`, `AVAILABLE +a`, `<bucket> +(q−a)` | `quality_partial_release` |
| Reject (`FAILED`) | `relocation`: `QUALITY_HOLD −q`, `REJECTED`/`QUARANTINE` `+q` | `quality_reject` |
| Return to vendor | `issue`: `<bucket> −r` | `return_to_vendor` |
| Scrap | `adjustment`: `<bucket> −r` | `scrap` |

Each move is one paired transaction, so the ledger nets to zero for hold/release/reject and the
reconciler (`app/inventory/reconcile.py`) reports no drift — asserted after every flow in the tests.
Each carries `reference_type = "inspection"`, `reference_id`, and a deterministic kernel
idempotency key `qc:<inspection id>:<step>`. Reserved stock cannot be held: the kernel refuses with
the usual `insufficient-stock` problem naming the reservations in the way.

`hold_stock: false` says the stock already sits in `QUALITY_HOLD` (received there). It is verified
against the balance **minus what other open inspections already claim**, so one pool of held stock
cannot be inspected twice over.

Rework and hold decisions leave stock in `QUALITY_HOLD`. Disposition is all-or-nothing over the
rejected quantity, once.

## 5. Rules the server enforces

- **A failed critical parameter blocks release** (409). Reject, rework or hold instead.
- **A failed non-critical parameter** allows release only with a note saying why (422 without).
- **Every template parameter needs a recorded reading** before release.
- `accepted` must be above zero and no more than the inspected quantity (zero accepted is a
  rejection); a fraction of a discrete UOM (`pcs`) is refused like everywhere else.
- A **numeric** reading is judged by the server against the template's inclusive bounds; a
  client-sent verdict on it is ignored. Without a template only pass/fail observations are allowed.
- Editing or deleting a template never changes what a past inspection was judged against — the
  results hold their own snapshot.

## 6. Concurrency and idempotency

- Every decision locks the **inspection row `FOR UPDATE`** first, then calls the kernel (which takes
  item, balance and reservation locks in its own fixed order). Two simultaneous decisions queue on
  that row; the loser re-reads the committed status and gets a 409.
- **`Idempotency-Key`** is accepted on every mutating endpoint. For creates (template, inspection)
  and for each decision/disposition the key and a hash of the request are stored: the same key and
  payload **replays** the first result (no second posting), the same key with a different payload is
  a 409. Creation is serialised per (organization, key) by a transaction advisory lock. `PATCH`
  template, `DELETE` template and `PUT results` are naturally idempotent (same body, same end state)
  and accept the header without storing it. A keyed retry replays only while that step is the
  inspection's last keyed step; an unkeyed retry of a done step is a 409 by the state machine.
- Proven under real concurrent connections in `tests/test_quality_concurrency.py`: release vs reject
  (one wins), two different partial releases, ten identical keyed releases (one move), eight
  concurrent keyed opens (one inspection, stock held once), five inspections racing for 60 of 100
  units (one wins), two dispositions (one wins). The reconciler is clean after each.

## 7. API

All under `/api/v1/operations/quality`, all floored at **ORG** scope (an inspection is a shared
operational object — there is no useful "inspections I personally raised" reading), all 404 on
another organization's id, never 403.

| Route | Permission |
|---|---|
| `GET /templates`, `GET /templates/{id}` | `inspection.read` |
| `POST /templates`, `PATCH /templates/{id}`, `DELETE /templates/{id}` | `inspection.approve` |
| `GET /inspections` (filters: status, kind, item_id, lot_id), `GET /inspections/{id}` | `inspection.read` |
| `POST /inspections`, `PUT /inspections/{id}/results` | `inspection.inspect` |
| `POST /inspections/{id}/hold`, `/rework`, `/reinspect` | `inspection.inspect` |
| `POST /inspections/{id}/release` | `inspection.approve` |
| `POST /inspections/{id}/reject`, `/disposition` | `inspection.reject` |

## 8. Permissions, roles, events

Permissions `operations.quality.inspection.read|inspect|approve|reject`. **`approve` and `reject` are
dangerous** — they are the steps that move stock out of quality hold. (plan §4 lists the four
without the dangerous mark; this phase sets it on those two, the same flag `count.review` and
`stock.adjust` carry for the same reason.) `approve` also owns **template management**: what counts
as acceptable is an approver's call, not the inspector's being measured against it.

| Role | Grants |
|---|---|
| `OPERATIONS_ADMIN` | everything (catalogue `all()`) |
| `OPERATIONS_MANAGER` | read, inspect, approve, reject |
| `QC_INSPECTOR` (new) | read, inspect, plus read of items, warehouses and locations. Cannot release, reject or define templates — the person measuring is never the person who decides |

Events (envelope v1, schemas in `libs/contracts/events/`): `operations.quality.hold`,
`operations.quality.released`, `operations.quality.rejected`, and `operations.quality.disposed` (an
addition to plan §5 — disposing of rejected stock is its own fact). A partial pass publishes both
`released` (the accepted part) and `rejected` (the remainder). Events are staged in the decision's
own transaction and published by the relay after commit; every step also writes an audit row. Tests
validate every published payload against its committed schema.

**Rollout to live tenants (ADR-0007)** — a deploy step, not run by this change:

1. Merge; `tools/export_permissions.py` has already regenerated `libs/contracts/permissions/operations.json`.
2. Deploy `operations` (it enforces the new codes).
3. `uv run python tools/reconcile_permissions.py operations --all --dry-run`, review, then for real.
   This grants the four permissions to existing `OPERATIONS_ADMIN` and `OPERATIONS_MANAGER` roles and
   installs `QC_INSPECTOR` as a new system role. Customer-authored roles are untouched and reported.
4. Only then publish the `product-operations` bundle.

## 9. Release through an inspection versus `stock.adjust`

Moving stock out of `QUALITY_HOLD` / `REJECTED` / `QUARANTINE` into `AVAILABLE` through the Phase 1
status-change endpoint needs `operations.inventory.stock.adjust`. **A release through an inspection
deliberately does not:** `release`, `reject` and `disposition` call `ledger.post_move` directly, and
what governs them is `operations.quality.inspection.approve` / `.reject` (both dangerous, both
audited, both tied to a recorded inspection with readings). A holder of `approve` but not
`stock.adjust` can release held stock only through the inspection that holds it, never by hand; the
inverse also holds. This is the intended separation: the quality permission, not the stock one,
decides release via an inspection.

## 10. Deferred and risks

- **Lots and rolls** are validated on create: `create_inspection` calls
  `ledger.validate_tracking` (the check every posted move makes), so an unknown lot or roll, one
  from another organization, or one belonging to a different item is a 404 / 422 and no inspection
  row is written, with or without a hold.
- **Receipt into `QUALITY_HOLD` (Phase 2)** is not wired. TODO(Phase 2 integration): when the goods
  receipt lands (Phase 2 is on a separate stack), a received line of a quality-tracked item should
  post into `QUALITY_HOLD` and open an `incoming` inspection with `hold_stock: false` (the hook),
  `reference_type` / `reference_id` pointing at the receipt line and `supplier_id` set. Until then an
  inspection over received stock is opened by hand. (The job-work receipt hand-off, which does not
  depend on Phase 2, is wired by the Phase 6 branch.)
- **Single disposition per inspection**, over the whole rejected quantity. Splitting a rejection
  between RTV and scrap is a new inspection of the rejected bucket.
- **No supplier-return document / debit note.** `reference` is free text; finance integration of an
  RTV is out of scope.
- **No quality dashboard / reports / mobile inspect route** (Phase 8/9). No attachments (photos).
- `reinspect` clears earlier readings (they described pre-rework stock); they remain in the audit
  trail's `after` summaries only as counts and failed names, not values.
- A keyed retry of an *earlier* step after a *later* keyed step has overwritten the stored key
  answers 409, not a replay (one stored key per step family, not a log).
- Migration chain: on the stacked branches `a3c5e8f1b290` follows Phase 9's `d9a4c6e1b3f7`
  (the chain is `c4a8e1f6d273` -> `c5e91a7d3b28` (P2) -> `c5a82f6d3b19` (P3) -> `d9a4c6e1b3f7` (P9)
  -> `a3c5e8f1b290`). To re-parent again, edit `down_revision` in the migration file only (the
  line marked `RE-PARENT HERE`); the migration test derives its "before" revision from it. Rolling
  back a code deploy leaves the four tables in place and unused; do not `alembic downgrade` past
  `a3c5e8f1b290` once inspections exist (it drops them).
