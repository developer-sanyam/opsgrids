# ADR-0026 — Fulfilment F8: cycle counting and stocktaking

**Status:** Accepted — scoped and built in this change, under standing authorization to scope and
implement F8 without a separate sign-off round (the mission brief that opened this work item is
that authorization; see "Sign-off" below) · **Date:** 2026-09-29 ·
**Plan:** [fulfilment/plan.md](../fulfilment/plan.md)

Builds on [ADR-0014](0014-inventory-and-manufacturing.md) §3 (the ledger/balance reconciler,
`app/inventory/reconcile.py`) and §11 (`post_move`'s kinds and idempotency);
[ADR-0024](0024-fulfilment-boundaries.md) §1 (fulfilment modules live in `operations`) and §6
(`post_move`'s reservation-transfer amendment — the precedent for "extend the kernel, don't fork
it"); [ADR-0025](0025-order-and-task-state.md) §2/§4 (a session-scoped state machine with its
history on `AuditLog`, the shape this ADR reuses for count sessions). Does **not** amend any of
them — no kernel change was needed (§3).

## Sign-off

Unlike ADR-0024/ADR-0025, this ADR was not put to the platform owner before being written. The
work item that produced it (`feat/fulfilment-f8-scope-and-build`) explicitly pre-authorizes
exactly this: investigate what F8 was meant to be, and if nothing was ever decided, scope and
build the documented fallback (cycle counting / stocktaking) without waiting for a human
approval round on the scope document itself. §1 below is the evidence that justifies taking that
fallback rather than inventing a different F8. The usual review still applies to the branch as a
whole — this ADR is not a substitute for code review, only for the scoping conversation that
would otherwise have blocked starting it.

## 1. What F8 was — the evidence

F8 has no prior scope anywhere in this repository. Specifically:

- **`docs/fulfilment/plan.md`** (the only document that names fulfilment phases at all) defines
  phases **F0 through F5** in full (§3 of that file) and stops. Its own "Definition of done"
  section (§6) and MVP exit criteria (F5) treat F5 as the release; nothing in the file mentions an
  F6, F7, F8 or F9 — those numbers do not appear in `plan.md` at all.
- **`docs/adr/*.md`** — every ADR was grepped (case-insensitive) for `F8`, `cycle count`,
  `stocktake`/`stock take`, and `reconcil*`. The only genuine hits are (a) ADR-0014
  ("[inventory and manufacturing](0014-inventory-and-manufacturing.md)") §2's inventory phase list,
  which names **`stocktakes`** once as one of several document types a later *inventory* phase
  (not a fulfilment phase) was expected to grow, with zero elaboration — never designed, never
  scheduled, never given a phase number; and (b) dozens of unrelated hits for "reconcile" against
  ADR-0007's permission-rollout reconciler, ADR-0008's membership-link reconciler, and ADR-0014's
  own ledger/balance reconciler (`reconcile.py`, discussed in §2 below) — all a different kind of
  "reconcile" than a human physical stock count. No ADR proposes, scopes or even names a fulfilment
  F8.
- **Git history.** `git log --all --grep="F8" -i` and `git log --all --oneline | grep -iE "F8\b"`
  return only false positives — every apparent hit is an abbreviated commit hash that happens to
  contain the substring "f8" (e.g. `f81ff9a8` inside a merge-commit subject), not a real reference
  to a phase named F8. There has never been a commit, PR title or branch that scopes F8.
- **`services/operations` `TODO`/`FIXME` comments.** Grepped for phase references; the only hits
  are `TODO(F4 billing)` and a stale `TODO(F3)` already resolved by an earlier security review —
  no `TODO(F8)` anywhere.
- **The phase numbers that *do* exist, outside `plan.md`, only as commit messages and branch
  names**, tell a consistent story: F0–F5 are `plan.md`'s own phases (all merged to `main` as of
  this ADR: F0 `52f3b9b6`/`#76`, F1 `506e8d0f`/`#79`, F2 `6b4a47eb`/`#81`, F3
  `521e63be`/`#86`, F4 `cfd533d0`/`#87`, F5 `0d0481b8`/`#89`). **F9** ("a real-data 3D warehouse
  viewer", merged `30994074`/`#85`) is real and already shipped — its own commit message states
  plainly: *"No floor-plan geometry exists yet (that's F7's job)"* — direct evidence that F7 was
  informally understood, elsewhere in this build's planning (not in any file this repository
  tracks), to mean warehouse floor/layout geometry, and that F6–F8 were reserved numbers with no
  content committed anywhere this investigation could read. Sibling branches
  (`feat/fulfilment-f6-auto-assign`, `feat/fulfilment-f7-warehouse-geometry`,
  `feat/fulfilment-f10-online-payments`, `feat/fulfilment-f11-channels`,
  `feat/fulfilment-f12-automation`) exist as separate, parallel, not-yet-diverged work items (each
  still sitting at the same commit as `origin/main`, `0d0481b8`, when this ADR was written) — i.e.
  numbers assigned to work, but F8's own branch
  (`feat/fulfilment-f8-scope-and-build`) is the one explicitly named for scoping it, confirming by
  omission that no other branch or agent already owns it.

**Conclusion:** F8 was never concretely scoped. It is a reserved number with no committed content.
Per the mission brief's own fallback, this ADR scopes F8 as **cycle counting and stocktaking** —
the explicit fallback — having confirmed below that it does not already exist.

## 2. Does cycle counting already exist? No — but two adjacent things do, and neither is it

Two pieces of existing code touch "does the stock figure agree with reality", and both were read
in full before writing a line of new code, specifically to avoid building a duplicate.

**`app/inventory/reconcile.py` (`reconcile_organization`).** This is an **internal consistency
checker between two computed representations of the same ledger** — it sums
`inventory_ledger` per bucket and compares that sum against the cached `inventory_balances`
projection (and separately, active reservations against `inventory_balances.reserved_milli`). It
never involves a human, a physical count, or anything outside the database. A finding becomes an
`inventory_discrepancies` row and an `operations.inventory.inconsistency_detected` event; nothing
is ever auto-repaired. This answers *"did our own bookkeeping stay internally consistent"*, not
*"does the shelf match what the system thinks is on it"* — a bug-detection tool, not a physical
count workflow. **Not overlapping** with cycle counting in the operational sense: no worker ever
walks a warehouse to produce the number this compares against; both sides of its comparison are
already inside the database when it runs.

**`app/inventory/services.py::adjust_stock`** (reachable at
`POST /operations/inventory/items/{id}/stock`, permission `operations.inventory.stock.adjust`).
This function already accepts a physical count (`counted_on_hand`) for **one item, at its single
"home" bucket only** — it explicitly refuses if the item holds stock anywhere else
(`stock_outside_home`) — and posts it **immediately**, through `app.inventory.ledger.post_move`
with `kind="count"`, with no review step: whoever holds `stock.adjust` overwrites the figure
directly. This is the one genuine piece of prior art for "post a physical count through the
kernel", and it is reused wholesale (§3) rather than duplicated. It is **not** cycle counting:
it has no notion of a session, a scope wider than one item's home bucket, multiple items, multiple
counters, or a discrepancy that a second person reviews before it posts. It is the "quick
single-item recount" tool a counter clerk already had; F8 is the structured, auditable,
multi-location, second-person-reviewed workflow that a real stocktake needs and this tool was
never meant to be.

**Decision:** F8 is genuinely new work, not a duplicate of either. It sits between them: it
produces the human-counted numbers `reconcile.py`'s comparison logic is the template for (expected
vs. actual, record what disagrees, never silently repair), and it posts an approved correction
through exactly the same kernel call `adjust_stock` already proves correct — reusing both, adding
neither a second reconciler nor a second posting path.

## 3. No kernel prerequisite — the biggest finding of this investigation

Every other fulfilment phase in `plan.md` needed a kernel change before its own exit criteria
could pass (F2: multi-line `reserve`; F3: `post_move`'s reservation-transfer parameter, ADR-0024
§6). **F8 needs none.** `app.inventory.ledger.post_move` already has first-class support for
exactly this posting:

- `MOVE_KINDS` already includes `"count"` (`app/inventory/ledger.py`, present since the ledger
  foundation migration `d4e8b1c93f20`, long before this ADR).
- `MoveLine.counted_milli` already exists: *"A `count` line gives `counted_milli` instead [of
  `quantity_milli`] — the physical figure — and the kernel computes the change under the bucket's
  lock, so a count can never be computed from a stale read."* `post_move` computes
  `delta = counted_milli - on_hand` itself, under the bucket's row lock, at post time — never from
  whatever the caller believed the balance was when the count was taken. This is precisely the
  correctness property a stocktake needs: if stock moved between when a worker counted a bin and
  when a manager approves the adjustment, the kernel still lands the bucket on the physically
  counted figure, not on a stale arithmetic difference.
- `adjust_stock` (§2) is the existing, working proof that `kind="count"` + `counted_milli` posts
  correctly end to end, including its idempotency and audit shape.

F8's implementation (§6) therefore calls `ledger.post_move(kind="count", ...)` per approved line,
unchanged, with an idempotency key of `f"count:{line.id}"` — the same
`f"{prefix}:{id}"` convention every other kernel caller in this build uses
(`f"pick:{line.id}"`, `f"pick-short:{line.id}"`).

## 4. Scope

**In scope (this build):**

1. A **count session** — a scoped physical count of a whole warehouse, one zone, or an explicit
   list of locations, org-scoped, with **at most one session active (or under review) per
   warehouse at a time** (§5 — the concurrency decision).
2. **Count lines** — one per bucket in scope, snapshotting the expected quantity from
   `inventory_balances` at session-open, holding the counted quantity a worker later submits
   (scan or manual entry).
3. **Discrepancy review** — every line where `counted_milli != expected_milli` (as it stood at
   *snapshot* time — the review screen also shows the *current* live balance, since it can have
   moved since the snapshot; see §6) is approved or rejected, per line, by a second permission
   (`operations.inventory.count.review`). Nothing posts without an explicit approval.
4. **Posting** — an approved line posts through `ledger.post_move(kind="count", ...)`, reusing the
   kernel exactly as `adjust_stock` already does (§3), never a second implementation.
5. **Full audit trail** — every session transition, every line count, and every review decision is
   an `AuditLog` row (who, what, when, before/after) — the same mechanism `PickTask`,
   `AssignmentOffer` and every other fulfilment object already uses; no new audit table.
6. **Permissions** under `operations.inventory.count.*` (module `inventory`, matching where
   `warehouse`, `location`, `layout` and `stock` already live in `app/catalogue.py` — cycle
   counting is an inventory-accuracy concern, not a new top-level product area).
7. **Frontend**: a mobile count-execution flow in `frontend/worker-app` (barcode/location entry +
   manual quantity entry, offline-tolerant is **not** attempted this phase — see "Not in scope"),
   and a manager-facing session + discrepancy-review screen in `frontend/product-operations`.

**Not in scope (explicitly deferred, and why):**

- **Offline-tolerant scanning.** F3's plan calls this out as a real requirement for the *pick*
  scan screen (queue-and-replay on reconnect); F8's worker screen submits synchronously and shows
  a clear error on failure. A warehouse doing a scheduled stocktake is a materially different
  situation from a picker working an aisle with patchy connectivity all shift — deferred rather
  than speculatively built before there is a real complaint about it.
- **Auto-scheduling of recurring counts** (e.g. "count zone A every Monday"). No scheduler-backed
  recurrence exists for any fulfilment object yet (`services/scheduler` is used today only for
  offer-timeout sweeps); adding one is a separate, generic capability, not specific to counting.
- **Recount workflow as a distinct state** (asking a second worker to re-count a line before it
  reaches review). A manager can reject a discrepancy with a note and the line simply is not
  posted — re-opening a line for a fresh count is left to "add another count line for the same
  bucket" via a new session, not built as a dedicated recount action in this phase.
- **Extending `inventory_discrepancies`** (the reconciler's own table, §2) to carry count-session
  rows. Considered and rejected: that table's shape and its
  `ON CONFLICT (org, kind, item, location, status, lot, roll) WHERE resolved_at IS NULL` upsert
  are purpose-built for the automatic reconciler's own idempotent per-bucket-per-run recording; a
  session-scoped, worker-attributed, manager-reviewed line would either bloat that table with
  columns the reconciler never uses or force two conceptually different discrepancy notions
  (silent internal drift vs. a human physical count) to share one row shape and one unique
  constraint. `count_lines` is its own table (§6).

## 5. Concurrency — one active session per warehouse, not per finer scope

The mission brief asks for "one active session per scope at a time to avoid double-counting
races." A session's declared *scope* (whole warehouse / one zone / an explicit location list) is
finer than the *exclusivity* this ADR enforces: **exclusivity is at the warehouse level** — a
partial unique index refuses a second `ACTIVE` or `REVIEW` session for a warehouse that already
has one, regardless of whether the new one's zone or location list would, on paper, be disjoint
from the existing one's.

This is deliberate, not a shortcut. Two nominally non-overlapping counts running at once in the
same warehouse reintroduce exactly the race the requirement exists to prevent the moment either
assumption breaks: locations nest (a zone contains racks contains bins — two "disjoint" zone
counts can still share a bin if the tree is re-parented mid-count), and stock itself moves between
locations while a count is open (a relocation mid-count can carry a unit from an already-counted
bin into a bin a second, concurrently active session is about to count, double- or zero-counting
it across the two sessions with neither session's own bookkeeping able to see the other). A single
warehouse-wide exclusivity lock is the one rule that is airtight under both failure modes, is
enforceable as a real database constraint rather than an application-level overlap check
(`uq_count_sessions_org_warehouse_live`, `UNIQUE (organization_id, warehouse_id) WHERE status IN
('ACTIVE', 'REVIEW')`), and matches this codebase's stated preference for a real constraint over a
reviewed-but-racy application check. The cost — a warehouse cannot run two independent zone counts
in parallel — is a real MVP limitation, named here rather than discovered later, and is the first
candidate to revisit if a customer's warehouse is large enough that sequential zone counts are
operationally painful (see "Revisit when").

## 6. Data model

New tables, `services/operations/app/models.py`, schema `operations`, migration
`c9a2f1e6b7d4_cycle_counting.py` (chained after `b7d4f1c9a06e`, the F5 dispatch migration — the
current head):

- **`count_sessions`** (`Base, TenantBase`) — `warehouse_id` (FK), `scope_kind` (`WAREHOUSE |
  ZONE | LOCATIONS`), `zone_location_id` (nullable FK, set iff `scope_kind='ZONE'`),
  `location_ids` (nullable `JSONB` array of UUIDs, set iff `scope_kind='LOCATIONS'`), `status`
  (`ACTIVE | REVIEW | CLOSED | CANCELLED`, `COUNT_SESSION_TRANSITIONS` in `app/domain.py`, the
  same `*_TRANSITIONS` map shape as `PICK_TASK_TRANSITIONS`), `label` (free text). Exclusivity
  per §5. No dedicated `count_session_transitions` table — like `PickTask` (and unlike `Order`/
  `Shipment`, which ADR-0025 gave dedicated append-only transition tables because they are
  customer-facing documents), a count session's history is an operational trail served entirely by
  `AuditLog`.
- **`count_lines`** (`Base, TenantBase`) — `session_id` (FK, `RESTRICT`), the bucket
  (`inventory_item_id`, `location_id`, `status`, `lot_id`, `roll_id` — the exact tuple
  `app.inventory.ledger.Bucket` already keys on), `expected_milli` + `expected_snapshot_at`
  (frozen at line creation, never rewritten), `counted_milli` + `count_method`
  (`scan | manual`) + `scanned_barcode` + `counted_by` + `counted_at` (set once, by the worker who
  counted it), `review_status` (`NONE | PENDING | APPROVED | REJECTED`), `reviewed_by` +
  `reviewed_at` + `review_note`, `posted_move_id` (nullable FK into `inventory_moves`, set once an
  approved line posts), `is_unlisted` (a line a worker added for a bucket that had no balance row
  at snapshot time — genuine "found stock nobody expected" is a real stocktake outcome, not an
  edge case to refuse). One row per `(session_id, bucket)` —
  `uq_count_lines_session_bucket`, `NULLS NOT DISTINCT` (the same technique
  `uq_inventory_balances_bucket` already uses, since `lot_id`/`roll_id` are frequently `NULL` and
  plain `UNIQUE` treats two `NULL`s as distinct).

No change to `inventory_balances`, `inventory_ledger`, `inventory_reservations`,
`inventory_discrepancies`, or `post_move`'s signature. Both new tables carry `organization_id` +
RLS, the same `tenant_isolation` policy and `FORCE ROW LEVEL SECURITY` every migration since
`c3f1a7e5b2d9` uses.

## 7. API and permissions

Four permissions, module `inventory`, resource `count` (`app/catalogue.py`, next to
`stock`/`warehouse`/`location`/`layout`):

| Code | Default → max scope | Dangerous | Who |
|---|---|---|---|
| `operations.inventory.count.read` | ORG → ORG | | Everyone with an Operations seat |
| `operations.inventory.count.manage` | ORG → ORG | | Manager: open/move-to-review/close/cancel a session |
| `operations.inventory.count.execute` | ORG → ORG | | Worker: submit a counted quantity on a line in an active session |
| `operations.inventory.count.review` | ORG → ORG | ✔ | Manager: approve/reject a discrepancy (triggers a kernel post) |

All four are ORG-only (no OWN reading), the same reasoning `orders.order.*` and `dispatch.
shipment.*` already document: a count session is a shared operational object, not personal work —
any worker with `count.execute` can count any line in the one session currently open for their
warehouse, the same way any picker can be offered any task. `count.review` is `is_dangerous=True`,
the same flag `stock.adjust` and `stock.reconcile` already carry, for the same reason: it is the
step that changes recorded stock. `OPERATIONS_MANAGER` gains `count.manage`/`count.review` (and
`count.read`/`count.execute` for testing/covering a shift); `OPERATIONS_WORKER` gains
`count.read`/`count.execute` only — mirroring the existing `task.execute`-only shape for workers.

Routes (`app/api/routes.py`, new `app/counting/` module for `selectors.py`/`services.py`, mirroring
`app/fulfilment/`'s shape):

```
GET    /operations/counting/sessions                       count.read
POST   /operations/counting/sessions                       count.manage   — opens a session, snapshots its lines
GET    /operations/counting/sessions/{id}                  count.read
POST   /operations/counting/sessions/{id}/review           count.manage   — ACTIVE -> REVIEW
POST   /operations/counting/sessions/{id}/close             count.manage   — REVIEW -> CLOSED (refuses if any line is still PENDING review)
POST   /operations/counting/sessions/{id}/cancel            count.manage
GET    /operations/counting/sessions/{id}/lines            count.read
POST   /operations/counting/sessions/{id}/lines            count.execute  — add an unlisted line (found, not expected)
POST   /operations/counting/sessions/{id}/lines/{lid}/count count.execute  — submit counted_milli
POST   /operations/counting/sessions/{id}/lines/{lid}/review count.review — approve or reject; approve posts through the kernel
```

## 8. Acceptance criteria

- Opening a second `ACTIVE`/`REVIEW` session for a warehouse that already has one is refused with
  409, proven by a concurrency test (two simultaneous opens, exactly one succeeds).
- A session snapshots every `AVAILABLE` bucket in its scope at open time; a bucket added to the
  warehouse after the snapshot does not silently appear as a line (a worker can still count it via
  the unlisted-line path).
- Submitting a count when `counted_milli == expected_milli` needs no review (`review_status`
  stays `NONE`) and never touches the ledger.
- Submitting a count that disagrees sets `review_status='PENDING'`; approving it posts exactly one
  `count` move through `ledger.post_move`, idempotent on `f"count:{line.id}"`, with the resulting
  balance equal to `counted_milli` even if the live balance drifted from the line's own
  `expected_milli` snapshot between count and approval (proven by a test that posts an unrelated
  move to the same bucket between count and approval).
- Rejecting a discrepancy leaves the balance untouched and the line closed at `REJECTED` — never
  silently retried.
- Closing a session with any line still `PENDING` review is refused with 409 naming the count.
- Every new endpoint has a negative-authz test and a cross-tenant 404 test
  (`operations.inventory.count.*`, per CLAUDE.md).
- Frontend: worker-app's count-entry screen and product-operations' review screen both typecheck,
  lint and build; an E2E-style flow (open session → count two lines, one matching, one short →
  move to review → approve the discrepancy → balance reflects the approved count) is covered by an
  integration test at the API layer (`services/operations/tests`).

## Revisit when

- **A warehouse is large enough that sequential single-session counting (§5) is operationally
  painful** — the first candidate for relaxing exclusivity to true scope-overlap checking, once a
  real customer complains rather than speculatively.
- **F7 (warehouse geometry) lands** — if it gives locations formal floor/zone geometry beyond the
  adjacency list this ADR reads today, a count session's `ZONE` scope can resolve descendants
  through that instead of the recursive CTE this build adds to `app/inventory/selectors.py`.
- **A customer asks for scheduled, recurring counts** — once `services/scheduler` grows a general
  recurrence primitive (not counting-specific), wire session creation to it.

## Consequences

**Enables:** a real stocktake workflow where none existed; closes the one inventory-accuracy gap
`reconcile.py` was explicitly never meant to close (it finds bugs in the system's own bookkeeping,
not disagreements with the physical shelf).

**Costs:** two new tables, four new permissions, a new `app/counting/` module (small — no kernel
change), one new frontend screen pair. No migration risk to existing data (additive only, no
existing table or column touched).

**Accepted for now:** warehouse-level exclusivity over finer scope-overlap checking (§5); no
offline-tolerant worker screen this phase (§4); no recount state (§4).
