# ADR-0024 — Fulfilment: orders, pick/pack, warehouse layout and dispatch — who owns what

**Status:** Accepted — A1-A4 signed off by the platform owner 2026-09-28 (PR #76) · **Date:** 2026-09-28 ·
**Plan:** [fulfilment/plan.md](../fulfilment/plan.md)

Fulfilment — a customer order, reserving stock against it, picking, packing, dispatching, and
collecting payment for it — lands on top of what `operations` (ADR-0014) and `payment`
(ADR-0004) already ship. Nothing in the platform takes a customer order today: `grep` finds
purchase orders and storefront enquiries, and neither is one (§2). Several choices below are
**irreversible once the first fulfilment task exists in production**, and three of them amend an
accepted ADR, so they are settled on paper before the first migration.

Builds on [ADR-0000](0000-phase-0-decisions.md) §6 (state machines), §8 (money), §10 (tenancy, no
cross-schema foreign keys) and "Also settled" (PII sealed, outbox after commit);
[ADR-0014](0014-inventory-and-manufacturing.md) §1, §2, §3, §6 and §11;
[ADR-0004](0004-commercial-model.md) §1 and §6; [ADR-0019](0019-statutory-portal-credentials.md)
(sealed tenant credentials live in the service that uses them). **Amends** ADR-0014 §1 (§5 here),
ADR-0014 §3 (§6 here) and ADR-0001 "Mobile" (§7 here). Answers the "order engine" ADR that
[ADR-0010](0010-delivery-serviceability-and-slots.md) deferred, and places it outside storefront.

## Sign-off

All four decisions were put to the platform owner explicitly, one at a time, and each was
answered **Approve**, 2026-09-28. F0 (PR #76) merged on that basis. Everything else here follows
from these four; re-opening any of them re-opens what follows from it.

| ID | Decision | Section | Status |
|---|---|---|---|
| **A1** | Orders, fulfilment, warehouse layout and dispatch are modules of `operations`, not a new service | §1 | signed off |
| **A2** | A nullable selling price on `inventory_items` is the MVP price source | §3 | signed off |
| **A3** | The worker app is a browser PWA, not a native app (amends ADR-0001) | §7 | signed off |
| **A4** | Tenant payment collection is a module of `payment`, not a new service | §8 | signed off |

## 1. Orders, fulfilment, warehouse layout and dispatch live in `operations` — A1, signed off

`operations` owns every table a fulfilment step touches: `inventory_items`, `locations`, the
ledger, `inventory_balances` and `inventory_reservations`. The two hottest write paths fulfilment
adds are both stock writes:

- **Accepting an order reserves its stock in the same transaction** as the order's state change.
  `inventory_reservations` already carries `reference_type` / `reference_id`, so an order line
  reserves through the existing `app.inventory.ledger.reserve` with no change to the reservation
  table. If any line is short, the whole accept rolls back and the caller gets
  `InsufficientStock`'s numbers (ADR-0014 §11) — the order is never half-accepted.
- **Every pick, pack and dispatch scan is a `post_move` call** — a relocation from a bin to a
  staging or pack location, then an issue at dispatch — under the bucket-ordered locks and
  idempotency key ADR-0014 §3 already proves under concurrency.

In a separate service, the first becomes a saga: order created `pending` → event → operations
reserves → event back → order accepted, or a compensation that un-accepts it. Every scan becomes a
remote call that has to be idempotent across two databases. That is ADR-0014 §1's argument
against a separate `manufacturing` service, and it applies with more force here: a picker scans
dozens of times an hour, and each scan is a stock move.

It also costs memory the box doesn't have. `infrastructure/edge/memory-budget.json` prices the
production VM at 3862 MB MemTotal. As of its last re-pricing (2026-09-24), the running set is
4044 MB (104.7%) and the ten sellable profiles are 4581 MB (118.6%). A new service is at least
one more `api` container at the measured 98 MB (`kinds.api`), or 99 MB as `api_with_worker`, on a
box already over its hard line. Inside `operations`, fulfilment adds no container. The
offer-timeout sweep (§4) folds into the `operations` API container the same way `ReconcileSweep`
already does (`RUN_SWEEPS_IN_API`).

**Decision:** four new internal modules in `services/operations/app/`, each with the standard
anatomy (`selectors`, `services`, `domain`), models in `app/models.py` (one metadata, one alembic
history), as ADR-0014 §1 set for `app/inventory/`:

| Module | Owns |
|---|---|
| `app/orders/` | The customer order, its lines, its state machine, accept-reserves-stock |
| `app/fulfilment/` | Pick and pack tasks, assignment and offers to workers, the scans that complete them |
| `app/warehouse/` | Layout: location CRUD across the extended kinds (§5), pick sequence, pack stations, docks |
| `app/dispatch/` | Handover: shipments, dispatch scan (the issue move), proof of handover |

Dependency rules. An import against the arrow is a review failure:

- `app/inventory/` imports none of the four. `app/inventory/ledger.py` stays the **only** writer
  of the ledger, balances and reservations (ADR-0014 §3); no new module writes those tables.
- `warehouse` → `inventory`. `orders` → `inventory`. `fulfilment` → `orders`, `warehouse`,
  `inventory`. `dispatch` → `fulfilment`, `orders`, `inventory`.
- `orders` never imports `fulfilment` or `dispatch`. Downstream modules advance an order through
  `app/orders/services.py`, never by writing its row.

Only one location CRUD gets built. ADR-0014's Phase 1 plan (item 4) schedules zone/rack/bin CRUD
in `app/inventory/`, and today nothing but the default bin is ever created (`kind="bin"` in
`app/inventory/services.py`; no schema in `app/schemas.py` exposes `kind`). Whichever lands
first, inventory Phase 1 or fulfilment's layout slice, builds it in `app/warehouse/`, and the
other one uses it.

Other services integrate **only by events** (outbox → RabbitMQ), and no foreign key crosses a
schema (ADR-0000 §10): storefront and CRM originate orders, finance invoices them (§4), payment
collects for them (§8), notification tells people about them.

**Rejected:**

- **A new `fulfilment` or `sales` service.** It turns order acceptance into a saga and every scan
  into a distributed write, and adds a container to an over-budget box, for no isolation benefit,
  because both sides would share one ledger.
- **Orders in `storefront`.** ADR-0010 expected "the order engine" to be its own ADR under the
  storefront product. But storefront holds no stock (§3: it has no link to an inventory item at
  all), and it is one channel among several. Manual, WhatsApp and B2B orders don't pass through
  it. An order accepted in storefront's transaction cannot reserve stock held in `operations`'.
- **`services/quotation`.** It is a scaffold with no business tables (`app/models.py` holds only
  the outbox, processed-events and audit tables). A quotation is a pre-order document anyway; it
  can seed an order the way an enquiry can (§2).

## 2. The order is a new document — not a `PurchaseOrder`, not a `storefront.Enquiry`

The two existing tables that look like orders are both the wrong kind of document.

**`PurchaseOrder` is buy-side.** It is raised *against a supplier* (`supplier_id`, a `NOT NULL`
in-schema FK). Its lifecycle is `draft → approved → received → closed` plus `cancelled`, the
vocabulary of `app.domain.PO_TRANSITIONS`. Its lines are keyed by a `sku` string and count
`quantity` / `received_qty` as `Integer` whole units. A customer order is sell-side:

- it has a customer, not a supplier;
- its states are accepted / picked / packed / dispatched / delivered, not approved / received;
- its lines must hold milli-unit quantities (82.5 m of fabric, ADR-0014 §2) against an item id.

Overloading the PO table means a nullable `supplier_id`, a direction flag every PO selector,
list, report and permission must remember to filter on, and one status column with two
vocabularies. Mixing buy and sell documents like that is the bug the house state-machine rule
(ADR-0014 §6) exists to prevent.

**`storefront.Enquiry` is a pre-sale lead.** Its own docstring calls it a buyer's line list "sent
as an enquiry", with no cart, no checkout and no pricing engine. Its content is append-only (only
`status` changes). Its `lines` are **opaque JSON** (`[{"product_id", "name", "quantity"}]`)
pointing at storefront products that "may since have been deleted". `quantity` is free text.
`status` runs `new | contacted | won | lost`. It carries no price, no stock reference and no
fulfilment state. By its own contract it must not gain any of those: "correcting it is a new row,
not an edit."

**Decision:** the order is a new top-level document in `app/orders/`. Order and lines are
tenant-scoped, and money is integer minor units plus an ISO currency (ADR-0000 §8). Line
quantities are milli-units of the item's stock UOM (ADR-0014 §2). The state machine is an
`ORDER_TRANSITIONS` map in `domain.py`. Converting a `won` enquiry (or a quotation, when one
exists) **creates** an order that records its origin by plain UUID (`source_type`, `source_id`);
the enquiry row is untouched. Because enquiry lines name storefront products and not inventory
items (§3), a person maps each line to an item during conversion. Nothing can do that
automatically today.

**Rejected:** overloading `purchase_orders` with a direction flag; reinterpreting an enquiry row
as an order; a generic "document" table shared by POs, orders and enquiries. That last one is the
PO overload made worse: one status column and three vocabularies.

## 3. The MVP price is a nullable selling price on `inventory_items` — A2, signed off

The gap, stated plainly:

- **`InventoryItem` has no selling price.** It carries `hsn_code` (tax classification) and
  `unit_value_minor` + `currency`. The model documents that pair as "per-unit stock value": a
  valuation figure on the cost side of ADR-0014 §8, next to the ledger's `unit_cost_minor` /
  `unit_cost_currency`. Using it as a selling price would make every margin figure the platform
  ever computes equal to zero.
- **Storefront's prices are bands on products that aren't linked to stock.** `Product` and
  `ProductVariant` carry `price_min_minor` / `price_max_minor` (a "from" range) and an optional
  `mrp_minor`, with the currency taken from `storefronts.currency`. Stock is represented only by
  an `in_stock` boolean. A search of `services/storefront/app/models.py` for `inventory_item_id`,
  `item_id` or `sku` finds **nothing**. A search of storefront's app modules for `operations` or
  `inventory` finds nothing either. **No reference, FK or plain UUID, links a storefront product
  to an inventory item today.** A band is also not a price: an order line needs one number.

**Decision:** add `selling_price_minor` (`BIGINT`, nullable) and `selling_price_currency`
(`String(3)`, nullable) to `inventory_items`. A `CHECK` requires both to be null or both set, the
same shape as the ledger's `unit_cost_currency_matches_minor`, plus `selling_price_minor >= 0`.
The price is per **one whole stock UOM**. A line's amount is `quantity_milli × price / 1000`,
rounded half-to-even **once per line** (ADR-0014 §2's money × quantity rule). The order line
**snapshots** the price at acceptance, so editing the item's price later never reprices an
accepted order. This is the same reason subscriptions pin plan versions (ADR-0000 §9). An item
with no selling price can go on an order only if the line's price is entered explicitly.

Whether the stored price is tax-inclusive (storefront's `Product.gst_inclusive` says its bands
can be either) is **not decided here**. The plan must settle it before the first line is priced.

**Out of scope for this ADR:** price lists per customer or customer group, quantity breaks,
schemes and discounts, and linking storefront products to inventory items. Each is a later ADR.

**Rejected:** a pricing service (a container and a cross-service read on every order line, for a
single number per item); storefront's bands as the source (unlinked, a range, and absent for
every non-storefront channel); `unit_value_minor` (cost, not price).

## 4. `fulfilment_settings` lives in `operations`, one row per organization, optionally per warehouse

The settings are: invoice timing (on accept, on dispatch, on delivery); payment defaults per
channel (prepaid, cash on delivery, credit); and offer timeout and rounds (how long a task is
offered to a worker, and how many rounds run before it escalates to a supervisor).

**Decision:** an `operations.fulfilment_settings` table. One row per organization has
`warehouse_id IS NULL`. An optional row per warehouse overrides it, and a null column on the
warehouse row means "inherit the organization's". Two partial unique indexes enforce one of each.
An order **records the resolved settings it was accepted under**, so a settings change applies to
new orders only.

**Why not `finance`.** Finance owns sales `invoices`, the `invoice_number_series`, tax rates,
e-invoice filing and e-way bills. These settings don't govern how finance books an invoice. They
govern **when operations asks for one**: which order transition emits the event finance consumes.
If the timing lived in finance, finance would have to know operations' order states to act on it,
which ties its code to another service's state vocabulary.

**Why not a config service.** `services/config` is a scaffold, and `config-service-api` is only a
`planned` placeholder in `memory-budget.json`. ADR-0019 turned down the same service as the home
for tenant **credentials**, choosing to keep what one service acts on inside that service. The
same reasoning applies to settings only `operations` reads: putting them elsewhere would mean a
cross-service fetch on every order accept.

## 5. Amendment to ADR-0014 §1 — location kinds are extended, not duplicated

ADR-0014 §1 put locations in the `operations` schema. The vocabulary is on the model: `Location.
kind` is `String(10)`, constrained by
`CheckConstraint("kind IN ('zone', 'rack', 'bin')", name="kind_known")`. Fulfilment needs places
that are neither racks nor bins: where picks are staged, where orders are packed, where trucks
load, where returns and receipts land.

**Decision:** extend the same column. The vocabulary becomes `zone, aisle, rack, shelf, bin,
staging, pack_station, dock, receiving, returns, quarantine`. Two schema facts come with it:

- **The column must widen.** `pack_station` is 12 characters and `String(10)` cannot hold it.
  The migration widens `kind` (to `String(20)`) as well as replacing `kind_known`.
- **It ships as its own reversible migration**, not bundled into the orders or tasks migration.
  Upgrade widens the column, then drops and recreates `kind_known`. Downgrade **refuses** if any
  row uses a new kind, as ADR-0014 §10's migration refuses rather than guessing, then restores
  the old constraint and width. A revert of the fulfilment tables must never depend on reverting
  the vocabulary, or the other way round.

**Why now:** no API exposes `kind`, and the only write path creates `bin`. Every location in
production is a bin today. This is the cheapest the change will ever be.

**A location kind is not a stock status.** A `quarantine` location is *where* stock is;
`QUARANTINE` in `STOCK_STATUSES` is *what condition* it is in. Moving stock into a quarantine
location doesn't change its status, and a status change is still a paired ledger move (ADR-0014
§4). A flow that needs both (returns intake, say) posts both. Which kinds may hold stock and which
are only tree nodes (`zone`, `aisle`) is a write-path rule in `app/warehouse/`, not a column. The
same holds for which kinds may nest under which.

**Rejected:** separate `docks` / `pack_stations` / `staging_areas` tables. The ledger bucket key
is `(item, location, status, lot, roll)`. Stock sitting at a pack station has to *be at a
location* to be in the ledger at all, so a pack station that isn't a `locations` row can't hold
stock. Also rejected: deriving stock status from location kind, which creates two sources of truth
for one fact.

## 6. Amendment to ADR-0014 §3 — `post_move` gains a reservation transfer

Today the kernel has three writers. `post_move` posts a move, and a `MoveLine.reservation_id`
lets an outbound line **draw on** a reservation of its own bucket (recorded as `consumed_milli`).
`reserve` promises available stock, and `release` gives it back. Each has its own idempotency
key. A pick moves reserved stock from a bin to a staging or pack location. With today's kernel
that takes two calls:

1. `post_move` relocates the stock. The outbound line draws on the order's reservation, which
   marks it **consumed**, and the inbound line lands at the destination as *unreserved*
   available stock.
2. The caller then calls `reserve` on the destination.

In one transaction, the destination's balance lock is held from step 1 until commit, so no other
transaction can reserve the stock in between. But that holds only because the caller did it
right. Each caller must:

- make both calls in the same transaction, with matching quantities;
- copy `reference_type` / `reference_id` across;
- key both calls so a retry replays both or neither. Two keys means a retry can replay the move
  and post the reservation again, or the reverse.

A caller that commits between the two calls leaves the stock **free** for another order to take.
And "consumed" is the wrong record for a pick: the demand wasn't met, it moved. `release` is worse
still, because `operations.inventory.stock.released` announces stock becoming free, which is
exactly what a pick must not do.

**Decision:** `app/inventory/ledger.py::post_move` gains an optional reservation transfer on a
relocation. When a line moving stock out of a bucket names a reservation *to transfer*, the
kernel, in the one call:

- draws that quantity off the source reservation, and opens (or tops up) a reservation on the
  destination bucket with the **same** `reference_type` / `reference_id`;
- takes both balance locks in the existing bucket order, then the reservation locks after their
  balances. This is ADR-0014 §3's rule, unchanged, so the transfer adds no new deadlock order;
- covers it all with the move's single idempotency key and request hash, one audit row and one
  outbox event.

The source reservation records the drawn quantity as **transferred**, distinct from consumed and
from released. The existing `consumed_within` `CHECK` is extended to count it, and the
destination reservation is traceable back to its source. Whether that is a column or a
transfer-history row (like `inventory_reservation_releases`) is the plan's call. The behaviour
above is not. The nightly reconciler's check (open reservations vs `reserved_milli`) must count
transferred quantity correctly, and is tested to.

The result: **pick and pack transfer a reservation; dispatch consumes it** (the issue line's
existing `reservation_id`); cancellation releases it. Transferring more than the reservation's
open remainder is a `ConflictProblem`. Only a relocation can transfer: same item, lot, roll and
status, different location.

**Rejected:**

- **Leaving it to callers** to pair `post_move` with `reserve`. That puts the invariant in every
  caller's discipline instead of the kernel, and gives a retry two keys to disagree on.
- **Re-pointing the reservation row's `balance_id`** at the destination. That mutates what was
  promised against which bucket, and it can't express a partial pick, which splits one
  reservation across two buckets.
- **A "soft allocation" table outside the kernel.** That is a second writer of reserved state,
  which ADR-0014 §3 forbids.

**Why now:** once reservations with fulfilment history exist, their accounting can't be
reinterpreted. A "consumed" written at pick time can't later be told apart from a real dispatch.

## 7. Amendment to ADR-0001 — the worker app is a browser PWA first — A3, signed off

ADR-0001's "Mobile" section plans one native app per product per platform, sharing
`android/shared` and `ios/OpsGridsKit`. It cites a reference architecture that ships "separate
consumer, admin, delivery and picking apps for exactly this reason: a warehouse app and a sales
app have nothing in common but the account." Neither `android/` nor `ios/` exists in the
repository today. The inventory plan's Phase 9 already specifies browser scan routes
(`BarcodeDetector` with a JS fallback), not a native app.

**Decision:** the picker/packer/dispatcher surface is an installable, browser-based PWA. It is
**its own app under `frontend/`**: ADR-0001's one-app-per-surface rule stands, and the quoted
sentence is still why the surface is separate. What changes is the platform, not the boundary. It
imports `@opsgrids/ui` and `@opsgrids/client` like every other app, keeps the access token in
memory (ADR-0001, "What every product app inherits"), and sends an idempotency key with every
scan, so a scan queued offline and replayed is a replay, not a second move (§6, ADR-0014 §3).
Hardware scanners that type into a focused field (keyboard-wedge and Android enterprise devices)
work in a browser unchanged. Inventory Phase 9's scan routes should land in this app, not be
built twice.

**The trade-off, honestly:**

- **A PWA cannot do background BLE scanning or reliable background location.** Browsers suspend
  backgrounded pages, and iOS restricts this most. If beacon-based indoor positioning is ever
  wanted, native becomes necessary for the worker surface. The location-technology choice is
  **an open question** in the fulfilment design document, not decided here.
- **Web push is not guaranteed** (on iOS it needs the PWA installed to the home screen). A task
  offer must never depend on a push arriving: the app refreshes while open, and the offer timeout
  (§4) covers a worker who never saw it.
- **Camera scanning varies by browser** (`BarcodeDetector` is not universal), hence the JS
  fallback the inventory plan already names.

For a scan-driven MVP with no background requirement, none of these bite. The PWA avoids two
native codebases that don't exist yet, and an app-store review cycle on every fix, for a team
ADR-0001 sizes at three developers.

**Rejected:** native Android and iOS apps now. A wrapper (e.g. Capacitor) around the same PWA is
the likely route if native becomes necessary, but it is not decided here.

**Revisit when:** indoor positioning is chosen, or a background requirement (location while
driving, BLE) becomes an MVP requirement. Either supersedes this amendment for the worker surface
only.

### Addendum (2026-10-14) — task-scoped display fields for the worker

The `OPERATIONS_WORKER` role holds `task.read` but, by design, none of `orders.order.read`,
`inventory.item.read` or `inventory.location.read`, so the worker app could show a picker only
UUID fragments. The platform owner approved giving workers a **task-scoped** read of the order
number, the bin's code/name and the item's name/SKU/unit, never a general read. It is done without
a new permission and without widening any role: the fulfilment task and package payloads carry
denormalised display fields (`PickTaskOut.order_number`, `PickTaskLineOut.item_name`/`item_unit`/
`source_location_code`/`source_location_name`/`destination_location_code`,
`PackageLineOut.item_name`/`item_unit`), resolved inside `operations` (same schema, no
cross-service call).

A field is populated only when the caller either holds the matching org-wide read (so nothing is
exposed that they could not already fetch) or **holds a live offer (`OFFERED`/`ACCEPTED`) on that
task** — for a package line, on a task of that package's order. Otherwise it is `null`; the task
itself stays readable under `task.read` exactly as before. A released, declined or reassigned task
therefore stops resolving for that worker. `GET /fulfilment/tasks?assigned_to_me=true` narrows the
list to the caller's own live offers and never widens it. No role, scope or permission code
changed, so there is no edge reconcile step.

## 8. Tenant payment collection lives in `payment`, as `app/collections/` — A4, signed off

**What `payment` does today.** Its model docstring: "mandates, charges, refunds, and the webhook
inbox", carrying "*what was actually collected*" for commerce (ADR-0004 §1). A charge points at a
**billing invoice** and a **subscription**. A failure travels out on `commerce.payment.failed`,
and the subscription service turns it into a suspension. So `payment` collects **OpsGrids' own
subscription charges from tenants**. Its Razorpay adapter "keeps its wire stubbed"
(`providers/base.py`), and the root `pyproject.toml` still lists the `razorpay` SDK as waiting its
turn. **No live money moves through `payment` yet.**

**What fulfilment needs is the opposite direction:** a tenant's *own customers* paying the
*tenant*, into the tenant's own Razorpay account. OpsGrids is not the merchant. This is a
philosophically different job for the same service, and this ADR names it as such.

**Decision:** a new `app/collections/` module in `payment`:

- **`merchant_accounts`**: one row per organization, holding the provider, key id, key secret and
  webhook secret. The secrets are sealed with `platform_core.pii.seal`, only masked forms are ever
  returned, and every change is audited. ADR-0019 does the same for statutory portal credentials
  in finance, and notification does it for WhatsApp tokens.
- **A `payee` dimension** (`platform` | `merchant`) on `charges` and on the two append-only
  tables, `refunds` and `provider_events`. Existing rows are `platform`. The column is added with
  a server default, so **no `UPDATE` backfill runs**. That matters because those two tables carry
  a `BEFORE UPDATE OR DELETE` trigger that would refuse one.
- **Collections reuse the `PaymentProvider` protocol** with one adapter instance per merchant
  account, built from that account's sealed keys. The protocol's own docstring makes which
  concrete provider is held "a wiring decision, not a code change in the rules", and its methods
  take no credentials. To be exact about what carries over: the seam was shaped so a second
  **provider** (Stripe) fits (`app/models.py`'s docstring). It was not shaped for a second
  **payee**. What carries over are the hard parts: lookup-before-create against a provider Order
  (ADR-0004 addendum D3), bounded calls, signature-first webhooks, and the idempotent append-only
  inbox.

Two consequences the plan must build, not discover:

- **A merchant's failure must never suspend anyone's subscription.** A captured or failed
  merchant charge emits its own event types, never `commerce.payment.*`. Operations consumes them
  to mark an order paid, and finance records them against its own `payments` /
  `payment_allocations`. The `payee` column is what keeps a tenant's customer's failed UPI
  payment from switching off the tenant's OpsGrids products.
- **Webhook verification needs the merchant's secret before it trusts the body.** Today the
  tenant is read from the provider's echoed `notes` *after* the signature verifies against the
  platform's one secret. With one secret per merchant, the delivery URL must name the merchant
  account, and the secret lookup happens before any tenant is bound. That needs a narrow
  pre-tenant lookup path, which the plan must specify under ADR-0002's bypass rules.

**Rejected:**

- **A new `collections` service.** It needs another container on an over-budget box (§1), a
  second copy of the adapter, the lease/sweep machinery and the webhook inbox. All of that exists
  once already.
- **`finance`.** It has `payments` but no provider seam, so the adapter would be duplicated.
- **`operations`.** Payment-provider secrets have no place in the stock service.

**Open question: whether this warrants a split later.** `payment` is small today: 4,254 lines of
app code against `operations`' 8,800 (both counted 2026-09-28 over `services/<name>/app/**/*.py`).
Revisit when either collections outgrows platform billing inside the service, or a regulatory or
contractual requirement demands that tenant money flows be isolated from OpsGrids' own. The
`payee` column and a separate module make that split a move, not a rewrite.

## 9. Assignment, routing and allocation are deterministic — no AI/ML in the first release

Nothing in the platform makes a runtime business decision through a model. This is inferred, not
proven, from the dependencies: the repository has two `pyproject.toml` files (the root and
`libs/platform-core`), and neither declares an ML, numeric or LLM-client dependency. There is no
numpy, pandas, scikit-learn, torch, ONNX, OR-Tools, OpenAI or Anthropic client. ADR-0010 set the
same posture for delivery: "no geocoder, no map API".

**Decision:** every fulfilment decision is a pure function in the owning module's `domain.py`,
with a **total order** so the same inputs always give the same answer:

- **Worker assignment.** Eligibility by role, zone and shift; ranking by fixed keys (for example
  open-task count, then idle time); a final tie-break on id. Offers run in rounds under
  `fulfilment_settings`' timeout (§4).
- **Pick routing.** The stored location sequence (§5's tree), not a solver.
- **Reservation allocation.** FEFO for items with `track_expiry`, else FIFO by receipt, else
  location sequence; a final tie-break on id.

The plan fixes the exact keys. This ADR fixes that they are deterministic, testable without a
database, and explainable to a warehouse lead asking "why did Ravi get this task?"

Why: there is no fulfilment history to learn from; the same answer can be reproduced from the
audit trail; no new dependency; no outbound call past the egress guard (ADR-0016); and no
inference container on a box already over budget.

**Rejected:** ML ranking, LLM dispatch, and third-party route optimisation APIs, for the first
release. **Revisit when** production history exists and a deterministic rule is *measured* to be
the bottleneck.

## Revisit when

- The owner refuses any of A1–A4. The dependent sections re-open with it.
- A second price for one item is needed (customer groups, channels). §3's single column is then
  outgrown and a price-list ADR supersedes it.
- Indoor positioning or any background capability is chosen (§7).
- Collections needs regulatory isolation or outgrows platform billing (§8).
- `operations` can no longer be reviewed or deployed as one unit (see Consequences).

## Consequences

- **`operations` becomes the platform's centre of gravity, and that is a real risk, not a
  footnote.** ADR-0014 predicted it would become the largest service once manufacturing landed.
  It is not yet: at 8,800 lines of app code it trails `notification` (30,869) and `finance`
  (17,136) (counted 2026-09-28). But it now carries the committed growth of two plans at once
  (inventory Phases 2–10 and fulfilment's four modules), and every one of them writes through
  one kernel. The module split and the dependency rules in §1 are therefore required, not
  stylistic. Its single API container (`kinds.api`, 256 MB `mem_limit`) is the first thing to
  watch as sweeps fold into it.
- **Irreversible after the first fulfilment task exists in production:**
  - the location-kind vocabulary (§5);
  - the reservation-transfer semantics, where pick and pack transfer and dispatch consumes (§6);
  - the order line's milli-unit quantity and snapshotted price (§2, §3).
- **ADR-0014's closing dependency is resolved.** ADR-0014 said sales dispatch "depends on a
  sales-order source (storefront orders today)", and inventory plan risk 6 said dispatch would
  start from "storefront orders and a manual dispatch note". Storefront has no orders, only
  enquiries (§2). The order is now `operations`' own document. Storefront enquiries, quotations
  and CRM integrate by creating orders there, and a manual dispatch is an order on a manual
  channel. **Dispatch starts only from an order.**
- **`payment` has two payees.** Every consumer of payment events must tell them apart. Merchant
  secrets are a new class of sealed tenant credential, and the webhook path gains a pre-tenant
  lookup (§8).
- **ADR-0001's "Mobile" section no longer describes the worker surface.** The consumer and admin
  surfaces are unaffected (§7).
- **No new container** from this ADR. If any of A1 or A4 is refused, the memory-budget
  consequence of that refusal must be priced in `memory-budget.json`'s `planned` block before the
  first slice.
