# Movements and control (Phase 3)

What Phase 3 of the [inventory plan](plan.md) built: inter-warehouse **transfers** with an in-transit
bucket, **reservations** over HTTP with expiry, and **adjustments** with a reason, evidence and
approval. Decisions live in [ADR-0014](../adr/0014-inventory-and-manufacturing.md) (§6 state machines,
§14 this phase) and [ADR-0006](../adr/0006-approval-engine.md) (the approval service); the ledger
kernel they sit on is described in [ledger.md](ledger.md). This page is the reference.

**Nothing here writes a quantity.** Every stock effect is one call into `app/inventory/ledger.py` —
`post_move`, `reserve`, `release` — so the locks, the idempotency index, the reconciler and the
audit/outbox staging are the kernel's, unchanged. The code is `app/movements/`.

---

## Transfers

A transfer is a document between two **different warehouses**. It never moves stock by itself; it
drives two moves.

```
DRAFT ──request──▶ REQUESTED ──approve──▶ APPROVED ──dispatch──▶ IN_TRANSIT ──receive──▶ RECEIVED
  │                   │  └─reject──▶ REJECTED                                  (until every line is settled)
  └──────cancel───────┴────────────cancel────────┘  (CANCELLED; never after dispatch)
```

`app/movements/domain.py::TRANSFER_TRANSITIONS` is the only definition; every step goes through one
helper that checks it and appends a row to the append-only `stock_transfer_transitions` trail
(`UPDATE`/`DELETE` revoked from the app role, re-revoked in `migrations/env.py`).

| Step | Route (`/operations/inventory/transfers…`) | Permission | Stock effect |
|---|---|---|---|
| draft | `POST ` | `inventory.transfer.write` | none |
| edit a draft | `PATCH /{id}` | `inventory.transfer.write` | none |
| request | `POST /{id}:request` | `inventory.transfer.write` | none |
| approve / reject (direct) | `POST /{id}:approve` `:reject` | `inventory.transfer.approve` (dangerous) | none |
| cancel | `POST /{id}:cancel` | `inventory.transfer.write` | none |
| dispatch | `POST /{id}:dispatch` | `inventory.transfer.dispatch` | source `AVAILABLE` → destination warehouse's `IN_TRANSIT` |
| receive | `POST /{id}:receive` | `inventory.transfer.receive` | `IN_TRANSIT` → destination bin; shortage written off |
| list, read | `GET `, `GET /{id}` | `inventory.stock.read` | — |
| in-transit report | `GET /operations/inventory/in-transit` | `inventory.stock.read` | — |

Quantities are decimal strings validated against the item's UOM (a fraction of a `pcs` is a 422).
A tracked item's line must name its lot and roll, and they must exist and belong to the item — checked
at drafting, so the author finds out while the line can still change. A bucket may appear on one line
only. `Idempotency-Key` is honoured on create, dispatch and receive.

### What dispatch and receive post

* **Dispatch** — one `relocation` move (`reference_type = stock_transfer`, key
  `transfer:<id>:dispatch`): each line leaves its source bin (status `AVAILABLE`) and lands in the
  `IN_TRANSIT` bucket at the **destination warehouse's default bin**, lot and roll intact. Two lines
  that feed one in-transit bucket are folded into one ledger line (the kernel refuses a bucket twice
  in a move). Reserved stock is refused exactly as for an issue — `InsufficientStock`, naming the
  reservations in the way (ADR-0014 §11); release one and retry.
* **Receive** — per line, any number of times: a `relocation` out of `IN_TRANSIT` into the chosen bin
  of the destination warehouse (default: its default bin), and, for what never arrived, an
  `adjustment` (reason `lost`, `reference_type = stock_transfer_shortage`) out of `IN_TRANSIT`. A line
  is settled when `received + shortage == dispatched` (a CHECK, so over-receipt is impossible even
  for a writer that forgot to look); the transfer becomes `RECEIVED` when every line is. **The
  in-transit figure always drains to exactly zero.**
* A tracked roll gets a `MOVED` roll event at dispatch and at receipt (`detail.phase`).
* A retried receipt with the same `Idempotency-Key` is a replay (200, `replayed: true`) and moves
  nothing more. Without a key a repeat is protected by the per-line counters: it can only account for
  what is still in transit.
* A shortage is a loss and is written off **without a second approval** — the receiver declares it, the
  adjustment and the `transfer.received` event record it. See *Deferred*.

### In transit is reported separately

`IN_TRANSIT` is owned stock that is not yet there (ADR-0014 §3): it is never inside an item's on-hand.
`GET /operations/inventory/in-transit` returns one row per (item, destination warehouse) with
`in_transit` and `physical` side by side — physical being the item's on-hand **in that warehouse**,
which excludes the in-transit stock. `tests/test_movements_transfers.py` proves the figures after
dispatch, after a partial receipt and after settlement, and that the reconciler stays clean.

### Concurrency

Lock order is always **the transfer row, then the kernel's** (items, balances in bucket order,
reservations). Two dispatches of one transfer queue on the transfer row; the second finds
`IN_TRANSIT` and is a 409 — and the dispatch move's fixed key means even a path that skipped the check
could not post twice. A dispatch racing an issue on one bin cannot take it below zero; two receivers
of one line cannot both receive it. All proven on real connections in
`tests/test_movements_concurrency.py`.

**Rolls.** A transfer line that names a roll posts through `post_move`, so it follows Phase 1's lock
order (item -> roll `FOR SHARE` -> balances; ADR-0014 §12): a dispatch racing a split of the same roll
queues on the roll row and the loser is refused cleanly (409 / `INSUFFICIENT_STOCK`), never a deadlock
(`tests/test_movements_roll_concurrency.py`). A dispatched roll (all or part of it) sits in the
`IN_TRANSIT` bucket, and a roll split or merge needs the roll to live in **one bucket across every
status** — in transit counts — so a roll with a portion on its way is refused a split
(`roll_in_several_buckets`) until the rest has been received. Receipt moves the stock on the same roll
into the destination bin; a roll may end up spread over two warehouses, which is allowed.

---

## Approvals

Two thresholds per organization (`inventory_control_settings`, one row; `GET`/`PUT
/operations/inventory/control-settings`; the PUT needs `inventory.control.configure`, dangerous, and
is audited with what the values were). A threshold is milli-units compared to **each line's own
quantity in that item's own UOM** — metres are never summed with pieces. `null` means none set. A
value-based threshold waits for costing (Phase 8).

| | no threshold | a threshold, line under it | a threshold, a line at or over it |
|---|---|---|---|
| **transfer** | `DIRECT` — a holder of `transfer.approve`, **not the requester**, signs off | `AUTO` — approved on the spot | `SERVICE` — the approval service decides |
| **adjustment** | applies at once | applies at once | held `PENDING_APPROVAL` — the approval service decides, evidence required |

The older item-level `POST /operations/inventory/{id}/adjustments` cannot skip a threshold: once one is
set, a change that size is a 409 pointing at `POST /operations/inventory/adjustments`. With none set
(the default) it behaves exactly as before.

### The approval-service round trip (ADR-0006 §1: events in both directions)

```
operations                                   approval                        operations
requested  ─ operations.inventory.<x>.requested ─▶ open request (type            │
 (needs approval)       {employee_id, summary, reason}    operations.inventory.<x>) │
                                                        │ routes up the requester's │
                      ◀─ core.approval.request.opened ──┘ reporting line            │
 record approval id + approver name                     approver decides            │
                      ◀─ core.approval.request.decided ─────────────────────────────┘
 apply it in OUR transaction, under OUR state machine
 (transfer → APPROVED/REJECTED; adjustment → post the move / REJECTED / FAILED)
                      ─ operations.inventory.<x>.applied ─▶ record the return leg
 cancel before decided ─ operations.inventory.<x>.cancelled ─▶ withdraw the request
```

* Request types are `operations.inventory.transfer` and `operations.inventory.adjustment`
  (`services/approval/app/services.py`, `KNOWN_REQUEST_TYPES`, so they appear in the policy settings
  with SLA/escalation like any other type). The requester is the principal's `subject_ref` (employee);
  an account with none cannot raise something that needs approval (409, said plainly).
* The decision is applied by `app/consumers.py::approval_request_decided`, which trusts the
  **envelope's** organization and refuses a payload that disagrees, ignores other request types, and
  is idempotent (`ProcessedEvent`, plus the state check: a late or repeated decision on something no
  longer waiting changes nothing).
* An approved adjustment is posted as the system under the key `adjustment-request:<id>`, inside a
  savepoint. If the stock cannot take it any more (someone issued it meanwhile) the request becomes
  `FAILED` with the kernel's reason and the return leg says `failed`; the transaction is not poisoned.
* The return leg's `outcome` is the approval service's own vocabulary (`applied` | `failed`); `decision`
  is which way it went (`approved` | `rejected`).
* Approvers decide in the approval inbox (today served by the HRMS app); the operations screens show
  the pending state, the approver's name and the decision, and never offer a decision themselves.

Counting (ADR-0026, `app/counting`) is **unchanged**: its discrepancy review is its own permission-gated
approval (`inventory.count.review`) that posts through the kernel. Phase 3 deliberately built no second
stocktake; routing a large count variance through the approval service is the one extension left (see
*Deferred*).

---

## Reservations

The promise is the kernel's `ledger.reserve`: lock the `AVAILABLE` bucket, check
`quantity - reserved >= requested` under that lock, raise `reserved_milli` in the same transaction.
Over-reservation is impossible — 50 parallel reservations of 1 against 10 free units reserve exactly 10
and the other 40 get `InsufficientStock` with the numbers.

| Route | Permission |
|---|---|
| `POST /operations/inventory/reservations` | `inventory.stock.reserve` |
| `GET /operations/inventory/reservations`, `/{id}` | `inventory.stock.read` |
| `POST /operations/inventory/reservations/{id}:release` (Phase 1) | `inventory.stock.reserve` |

A reservation is for `order` (the sales order — the value the orders module has always written),
`production_order` or `manual`; an order or production-order reservation must name its document. It
carries an optional `expires_at` (timezone-aware, in the future) and a `note`. A tracked item names its
lot and roll. The list is keyset-paginated with filters (item, location, status, reference, expiring).
`Idempotency-Key` replays; the same key with a different body is a 409. No separate "reservation
documents" table was needed: `reference_type` / `reference_id` already link a reservation to its demand.

### Expiry

`app/movements/sweep.py::ReservationExpirySweep` (`RESERVATION_EXPIRY_INTERVAL_SECONDS`, default 300;
in `bin/cron` and, with `RUN_SWEEPS_IN_API`, inside the API like the other sweeps). The same
discovery/work split as the reconciler: discovery on the BYPASSRLS outbox role, which holds
`SELECT (organization_id, status, expires_at)` on `inventory_reservations` and nothing more
(`migrations/env.py`); every read and write that matters runs on the app role under the tenant policy.
Each lapsed reservation goes through `ledger.release` (key `expire:<id>`, so a repeated tick is a
replay), then `expired_at` is stamped and `operations.inventory.reservation.expired` is staged. A
reservation consumed or released by someone else between read and lock is skipped, not failed; each
release runs in its own savepoint; one organization failing never stops the pass. A partly consumed
reservation releases only what is still open.

---

## Adjustments

`POST /operations/inventory/adjustments` (`inventory.stock.adjust`) takes a bucket (item, bin, status,
lot, roll), a **signed** decimal delta, a reason code (`damaged | lost | found | consumed | returned |
other`), a note and an `evidence_document_id` (a plain UUID into the document service — no cross-schema
key). Every adjustment made here is an `adjustment_requests` row numbered `ADJ-000123`:

* under the threshold (or none set): applied in the same call — `201`, `status: APPLIED`;
* at or over it: **`202`, `status: PENDING_APPROVAL`**, evidence required (422 naming the field
  otherwise), approval routed as above; no stock moves. `POST /adjustments/{id}:cancel` withdraws it.

A refusal by the kernel (taking more than is there) is a 409 and leaves no row. A retried
`Idempotency-Key` replays; the same key with another body is a 409. Reads: `GET /adjustments`
(status/item filters, cursor), `GET /adjustments/{id}`.

---

## Events

All envelope v1, schemas in `libs/contracts/events/`, every payload validated against its schema in the
tests that emit it. The kernel's own `operations.inventory.stock.posted` accompanies every move.

| Event | When |
|---|---|
| `operations.inventory.transfer.requested` | a draft was requested (`approval_route`, `approval_required`, `employee_id`, `summary`) |
| `…transfer.approved` / `…rejected` | signed off or refused — `AUTO`, `DIRECT` or `SERVICE` |
| `…transfer.cancelled` | before dispatch (`was_status` tells the approval service whether to withdraw) |
| `…transfer.dispatched` | stock left the source for in-transit |
| `…transfer.received` | stock accounted for at the destination (`complete`, per-line received/shortage) |
| `…transfer.applied` | the return leg of an approval-service decision |
| `…adjustment.requested` | an adjustment is held for approval |
| `…adjustment.applied` | an adjustment reached APPLIED / REJECTED / FAILED |
| `…adjustment.cancelled` | a held adjustment was withdrawn |
| `…reservation.expired` | the sweep released a lapsed reservation |
| `…stock.reserved`, `…stock.released`, `…stock.adjusted` | existing events, now with committed contracts (`reserved` carries `expires_at`) |

Event names follow the codebase's existing `operations.inventory.*` namespace rather than the plan's
shorter `operations.stock.*`. Low-stock alerting already exists as `operations.inventory.item.low_stock`
(the alert sweep); a `stockout` event was not added.

---

## Permissions

Added to the operations catalogue (all `ORG`-only):

| Code | Dangerous | OPERATIONS_ADMIN | OPERATIONS_MANAGER | OPERATIONS_WORKER |
|---|---|---|---|---|
| `inventory.transfer.write` | | ✔ | ✔ | ✔ |
| `inventory.transfer.approve` | ✔ | ✔ | ✔ | |
| `inventory.transfer.dispatch` | | ✔ | ✔ | ✔ |
| `inventory.transfer.receive` | | ✔ | ✔ | ✔ |
| `inventory.control.configure` | ✔ | ✔ | | |

`inventory.stock.reserve` and `inventory.stock.adjust` already existed (manager). The plan's
STORE_MANAGER / STOREKEEPER are the existing `OPERATIONS_MANAGER` / `OPERATIONS_WORKER` templates. Every
route has a negative-authz test and a cross-tenant test (`tests/test_movements_authz.py`): no permission,
every *other* permission, the same code at `OWN`, no entitlement, and another tenant — which gets a 404
on a named row, an empty page on a list, and cannot change the owner's data.

---

## Migration `c5a82f6d3b19`

Down revision `c5e91a7d3b28` (Phase 2's receiving migration). One migration, reversible, nothing destructive:

1. `inventory_control_settings` — thresholds and document-number counters.
2. `stock_transfers`, `stock_transfer_lines`, `stock_transfer_transitions` (append-only).
3. `adjustment_requests`.
4. `inventory_reservations` gains `expires_at`, `expired_at`, `note` (nullable / defaulted) and the
   sweep and list indexes.

Every new table carries `organization_id` with ENABLE + FORCE RLS and the `tenant_isolation` policy; all
foreign keys stay inside the schema, lot and roll references use Phase 1's organization-scoped composite
keys. **Downgrade refuses** while any transfer line still holds stock in transit (`dispatched >
received + shortage`) rather than stranding `IN_TRANSIT` balances nothing could then receive; once
settled it drops the tables and columns and leaves every reservation untouched.
`tests/test_migration_movements.py` runs it for real: RLS and the append-only trail, every CHECK and the
tenant-scoped keys, the outbox role's exact column grant, the refusal, a reservation surviving the round
trip, and up again.

**Rollout** is in `infrastructure/edge/RUNBOOK.md` (*Inventory Phase 3*): migrate, deploy `operations`
(and `approval`, which gains the two request types and their consumers — it must be deployed **before**
anything is held for approval: its queue is only bound to the `operations.inventory.*.requested` keys
once its worker has started with the new consumers, and a topic exchange drops a message no queue is
bound for), reconcile the operations permissions with a dry run first, then the SPA. Until a threshold
is set nothing is routed to the approval service at all, so the order only matters once an
administrator configures one.

---

## Deferred, and risks

* **Approval for a large count variance.** Counting's review is its own permission-gated approval; sending
  a variance above a threshold through the approval service (and auto-adjusting under it) is the one
  stocktake extension left. Nothing was built that would compete with `app/counting`.
* **Transit-loss write-off is not itself approved.** The receiver declares a shortage and it is posted
  and evented. A threshold on shortage, or a mandatory reason code, is a small follow-up.
* **Thresholds are per-line quantities, not value.** Costing (Phase 8) is when a value threshold becomes
  meaningful.
* **No partial dispatch.** A transfer ships in full at dispatch; to ship less, edit the draft. Partial
  receipt, which is where reality diverges, is supported.
* **Approvers decide in the HRMS approval inbox**, which does not yet render the operations summaries
  richly. The approval row carries `summary` and `request_note`; a product-neutral inbox is future work.
* **A transfer's in-transit stock sits at the destination warehouse's default bin.** A warehouse without
  a default bin cannot receive a dispatch (409, said plainly). Every warehouse gets one at creation.
