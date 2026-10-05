# Warehouse map, find-stock and storage put-away — design

Status: **implemented on branch, pending owner review** (`feat/warehouse-map-putaway`; not
deployed). Approved by the owner section by section (2026-10-05); this file is the implementation
contract, and the amendments marked below record where review changed what was built. Base: `main` @
`fab648e3`, single Alembic head `c4a8e1f6d273` (operations).

## 1. Goal

Let a storekeeper find where stock is without asking anyone:

1. Show warehouses on a Google map (site level).
2. Show a floor's racks and bins from the **published** layout, in 2-D and 3-D, and highlight where
   a searched item/lot/roll sits.
3. When a worker stores stock ("storage put-away"), record the destination bin by **scanning the
   bin's barcode**, plus the worker's GPS fix at that moment (audit/proof only — the bin is the
   location of record, never the GPS).

## 2. Decisions (owner, 2026-10-05)

| # | Decision |
|---|---|
| D1 | Storage put-away is allowed **only from `receiving` or `staging` bins** (422 otherwise). Limitation (c) is documented: the order engine reserves only at `item.home_location_id` (`orders/services.py` accept), so stock moved to a rack bin is invisible to order accept until multi-location allocation exists. |
| D2 | No CSP change on `ops`/`worker`. Google Cloud project, billing, spend cap and the key in the box `.env` are handled by the owner separately. **The map must be fully disabled — never broken — when `VITE_GOOGLE_MAPS_API_KEY` is absent.** |
| D3 | Only **ADMIN** may reveal worker coordinates. Captures are kept **30 days**, then purged by an admin-run retention/erasure procedure (not an in-app job). Review added a one-time-code requirement to the reveal beyond this text: §4.5, **OWNER DECISION**. |
| D4 | Terminology "storage put-away" is approved; amend ADR-0014 §12 (move reference vocabulary) to add `putaway`. ADR-0025's "putaway task" (order-cancel reversal) is left untouched. |

Standing constraints: no production deploy without explicit approval; `bin_transfer` behaviour and
the `operations.inventory.stock.transferred` event stay byte-for-byte backward-compatible;
coordinates appear **nowhere** except the audited reveal response (and the inbound POST body).

## 3. Data model (one Alembic revision on top of head `c4a8e1f6d273`)

Its `down_revision` is that head. Carry a `# RE-PARENT HERE` comment (two unmerged inventory stacks
share this head; whichever merges second re-parents). Follow `c4a8e1f6d273` / `b7d41e92c05a` conventions: `SET LOCAL lock_timeout`,
named CHECKs, local `_rls()` helper (ENABLE + FORCE + `tenant_isolation`), explicit grants.

- `warehouses`: add nullable `lat_micro BIGINT`, `lng_micro BIGINT`. CHECKs: both NULL or both set;
  `lat_micro BETWEEN -90000000 AND 90000000`; `lng_micro BETWEEN -180000000 AND 180000000`.
  **No `address_text`** — `warehouses.location` already holds the address.
- New `putaway_captures` (append-only; in `APPEND_ONLY_TABLES` in `migrations/env.py`; in
  `tests/conftest.py` `TENANT_TABLES`; `REVOKE UPDATE, DELETE` from the app role):
  `id`, `organization_id` (NOT NULL, indexed), `move_id` (FK `inventory_moves.id` RESTRICT),
  `location_id` (FK `locations.id` RESTRICT — the scanned destination bin),
  `suggested_location_id` (nullable FK), `worker_id` (nullable FK `workers.id`; managers without a
  worker row must still be able to put away), `created_by` (UUID NOT NULL, actor sub),
  `lat_micro`/`lng_micro` BIGINT nullable, `accuracy_m` INTEGER nullable, `captured_at`
  (client-claimed, NOT NULL), `created_at` (server default now()), `matched_suggestion` BOOLEAN
  nullable. CHECKs: coords paired + ranges, `accuracy_m >= 0`. Indexes: unique
  `(organization_id, move_id)`, `(organization_id, location_id)`. Single-column in-schema FKs only
  (ADR-0000 §10: no cross-schema FKs).
- `inventory_moves` is unchanged (`reference_type` is a free `String(30)` with no CHECK).
- `downgrade()` raises `RuntimeError` when any `putaway_captures` row exists or any warehouse has
  coordinates, unless `OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE=1` (precedent `b7d41e92c05a`). On an
  empty DB it fully reverses, so `make migration-roundtrip` stays green.

## 4. Backend contract (`services/operations`)

All paths under `/operations`. Every handler declares its permission, every query is scoped with
`ctx.apply()`, every id is resolved inside the caller's organization (a foreign or missing id is the
same 404). Register `locate` and `putaway*` in `app/inventory/api.py` (mounted ahead of the
catch-all `GET /operations/inventory/{item_id}`); register `/warehouses/map` before
`/warehouses/{warehouse_id}`.

**Permission (new):** `operations.inventory.putaway.location_read` — ORG min scope, 4 segments
(catalogue grammar; precedent `catalogue.py` reveal codes), granted via `all()` to the operations
ADMIN role **only**. Regenerate `libs/contracts/permissions/operations.json`.

### 4.1 Ledger refactor (backward compatible)
Extract a private `_transfer(...)` helper in `app/inventory/stock_ops.py` taking `reference_type`
and `reference_id`. `transfer_stock` keeps calling it with `"bin_transfer"` and still stages the
`stock.transferred` audit+event exactly as today. Put-away calls the helper with
`reference_type="putaway"` and stages **instead** `operations.inventory.putaway.recorded` (new
`libs/contracts/events/operations.inventory.putaway.recorded/v1.schema.json`, closed schema,
mirror the `stock.transferred` payload conventions, add `matched_suggestion` and `has_location`;
**no coordinates**). `post_move` still stages `inventory_move.posted` / `stock.posted`.
Put-away must call `check_tracking` (transfer_stock does not today).

### 4.2 `POST /operations/inventory/putaway` — perm `operations.inventory.stock.transfer` (ORG)
Header `Idempotency-Key` required; stored as `putaway:<key>` (≤128 total). Body (`extra="forbid"`):
`inventory_item_id`, `from_location_id`, `to_barcode` (existing barcode shape), `quantity`
(decimal string in item UOM), `lot_id?`, `roll_id?`, `suggested_location_id?`, `captured_at?`,
`lat_micro?`, `lng_micro?`, `accuracy_m?` (integers — never floats), `occurred_on?`, `note?`.
Rules (422 unless noted): source location kind ∈ {`receiving`,`staging`} (D1; error code
`source_not_receiving_or_staging`); barcode resolved **server-side** (workers lack
`location.read`) to a live same-org bin — unknown/foreign/deleted → 404 identical body; destination
kind ∈ `STORABLE_KINDS = ("bin","shelf")` (constant in `domain.py`); same warehouse; from≠to
(catch the `ValueError`, return 422); tracking via `check_tracking`; reserved stock → 409.
`reference_id` must be deterministic (uuid5 of the idempotency key) or None (it is in the request
hash). Replays (200) write no second capture, audit row or event. Geo is **not** in the request hash
— document that a retry with different geo replays the original.

Geo handling — validate in Python **before** posting (a DB CHECK failure after `post_move` would
roll back the stock move): paired, lat within ±90e6, lng within ±180e6, `accuracy_m >= 0`.
Invalid → drop geo, `warnings += ["geo_dropped:<reason>"]`, still 201. `captured_at` more than 5 min
in the future or older than 7 days → replaced by receipt time, `warnings += ["captured_at_adjusted"]`.
Capture insert runs in `begin_nested()`; failure → `warnings += ["geo_not_recorded"]`, move stays.
`matched_suggestion` is computed **server-side** from `domain.suggest_putaway_bin` (client value is
only `suggested_location_id`).

Response `PutawayOut`: `{move_id, replayed, from_location:{id,code,name}, to_location:{id,code,name,kind},
matched_suggestion: bool|null, geo_recorded: bool, warnings: [str]}` — **no coordinates**, 201 fresh /
200 replay. `warnings` is a new field: flow it into OpenAPI.

> **Amendment 2026-10-05 (review) — an unresolvable `suggested_location_id` is ignored, not a 404.**
> `suggested_location_id` is only what the device *showed*; the move does not depend on it. A
> put-away queued offline can be replayed after the suggested bin was deleted, and answering 404 would
> park a valid move. So an id that is **unknown, soft-deleted or another tenant's** no longer fails the
> request: the move posts, `putaway_captures.suggested_location_id` is stored `NULL`, and
> `warnings += ["suggestion_ignored"]` — **the same warning for all three causes**, so the response
> never says whether an id exists in another tenant. `matched_suggestion` is still computed
> server-side from `suggest_putaway_bin`, independent of the ignored id. The warning is raised only on
> a fresh 201; a replay (200) reports `warnings: []` like every other replay and writes nothing. A
> malformed (non-UUID) value is still a 422 at the schema. The 404 for an unknown or foreign
> `inventory_item_id`, `from_location_id`, `lot_id`, `roll_id` and `to_barcode` is unchanged. A
> resolvable same-organization id is stored as before, with no warning. Order of warnings:
> `suggestion_ignored` first, then `geo_dropped:*`, `captured_at_adjusted`, `geo_not_recorded`.

### 4.3 `GET /operations/inventory/putaway/suggest` — perm `…stock.transfer`
Query `inventory_item_id`, `warehouse_id`, `lot_id?`. Pure rule in `domain.py`
(`suggest_putaway_bin`), over the storable bins that have a barcode (first amendment below): (1) a
bin in that warehouse already holding the item (same lot if lot given); (2) else the empty bin with
lowest `pick_sequence` then code; (3) else null. No capacity logic (capacity fields are unused
today). Response
`{suggestion: {location_id, code, barcode: string, name, path, reason: "holds_item"|"empty_bin"} | null}`.

> **Amendment 2026-10-05 (review) — the suggestion carries the bin's own `barcode`, and a bin with no
> barcode is never suggested.** `suggestion` gains `barcode`, the bin's own scannable label. Workers
> hold `stock.transfer` but not `location.read`, so they cannot resolve a bin's barcode themselves; the
> suggestion is how the worker app learns the label it is asking the worker to scan. The caller already
> holds the permission this route requires, and a bin's barcode is a printed label, not a secret. The
> server resolves a scanned label by a bin's barcode **and by nothing else**, so a bin with no barcode
> is a suggestion nobody could act on: **neither rule offers one**, so a warehouse whose storable bins
> have no barcodes gets `suggestion: null`, and every suggestion the server returns carries a barcode
> (the OpenAPI type stays `string | null` for clients built against the earlier contract). The worker
> app's amber "different bin" check matches the scanned label against the suggestion's `barcode`
> **only**, case-insensitively. **A bin's `code` is not a label:** typing it sends a barcode the server
> cannot resolve, or one that resolves to some other bin whose barcode happens to read the same, so a
> code never counts as a match. The server's `matched_suggestion` remains the recorded verdict. No
> coordinates are involved.

> **Amendment 2026-10-05 (review, second round) — stock rows carry `location_kind`; the worker picker
> uses it (D1).** `StockRowOut` (`GET /operations/inventory/stock`) gains a nullable `location_kind`
> (string, one of the existing `LocationKind` values): the kind of the row's location. Workers already
> hold `stock.read`, so this exposes nothing new. The worker app's source-stock picker offers only rows
> whose `location_kind` is `receiving` or `staging`, so a worker is not shown stock the server would
> refuse at submit (422 `source_not_receiving_or_staging`, D1). The filter is a convenience, not a
> control: the rule in §4.2 stays the authority.

### 4.4 `GET /operations/inventory/putaway/{move_id}` — perm `…stock.read`
`{move_id, to_location:{id,code,name,kind}, worker_id|null, captured_at, matched_suggestion,
has_location: bool}` or 404 when the move has no capture (including purged captures). Never
coordinates.

### 4.5 `POST /operations/inventory/putaway/{move_id}/location:reveal` — perm
`operations.inventory.putaway.location_read` (ORG), response header `Cache-Control: no-store`
Calls `platform_core.audit.record_sensitive_read` (first Operations call site) — **one audit row per
successful call, field names only**, also when the capture has no geo (returns nulls). 403/404 write
nothing. No GET variant, no other code path returns coordinates. Response
`{lat_micro, lng_micro, accuracy_m, captured_at}` (nullable geo).

> **Amendment 2026-10-05 (review) — the reveal also needs a session that gave a one-time code. OWNER
> DECISION: keep it, or revert to permission + audit only.** This is a security hardening added during
> review **beyond the owner's literal D3 text**, which is the permission (ADMIN role only) plus an audit
> row on every call. The route is `require("operations.inventory.putaway.location_read",
> minimum=Scope.ORG, amr={"otp"})` (`PutawayLocationRead`, `app/inventory/api.py`), the pairing the
> platform's other reveal routes use (ADR-0023): the permission says the role may; `otp` says this token
> went through a second factor. Without it a stolen or unattended administrator token is a standing
> route to every worker's position in the 30-day window, and the audit row records that pull rather
> than stopping it. A token with no `otp` in its `amr` is answered **403** (`Additional authentication
> required: otp`) before the capture is looked up, so it also confirms nothing about which move ids hold
> a fix, and it writes no audit row. **The cost:** an administrator who has not enrolled an
> authenticator, whose session opened before they did, or who signed in through single sign-on or a
> social login (those sessions carry `sso` / `oauth`, never `otp`) gets that 403 and cannot reveal until
> they sign in with a code. It is `otp` alone, **not** the `{otp, stepup}` pair the CRM reveal routes
> take, because the operations SPA has no step-up UI (ADR-0023 risk 5) and the pair would leave the
> button unusable; wiring a step-up flow into the SPA, then adding the marker, is a follow-up
> (ADR-0028). **To revert** to the literal D3: delete `amr={"otp"}` from `PutawayLocationRead` in
> `services/operations/app/inventory/api.py` (one line) and flip the assertions in
> `services/operations/tests/test_putaway_authz.py::TestTheRevealNeedsASessionThatGaveAOneTimeCode`;
> the sentences that mention the code in the catalogue and service comments, ADR-0028 (3) and the RUNBOOK
> then go with it.

### 4.6 `GET /operations/inventory/locate` — perm `…stock.read`
Query `inventory_item_id` (required), `lot_id?`, `roll_id?`, `warehouse_id?`. Returns bins holding
positive balances: `{results:[{warehouse_id, warehouse_name, floor_id|null, floor_name|null,
location_id, code, name, kind, path, quantity (decimal str), status, placement|null,
placement_reason: null|"unplaced"|"no_published_layout"|"no_permission"}], truncated: bool}`.
`placement =
{layout_version_id, x_mm,y_mm,z_mm|null,width_mm,depth_mm,height_mm|null, source:"own"|"ancestor",
ancestor_location_id|null}`. Join `LocationPlacement`→`LayoutVersion` with `status='published'`
(**not** `Location.floor_id`, which is never cleared); dedupe multiple published placements; fall
back to the nearest placed **ancestor** (new upward recursive CTE; only a downward one exists).
Geometry is returned only if the caller also holds `operations.warehouse.layout.read`, else null with
`placement_reason="no_permission"`. Empty `results` is valid.

> **Amendment 2026-10-05 (review) — `locate` is bounded: at most 500 bins, and it says when it cut.**
> One query returns at most `LOCATE_MAX_ROWS = 500` bins (`putaway_selectors.py`). It fetches one more
> than that to learn whether the cap bit, and answers `truncated: true` when it did (`false` otherwise,
> and always present). Rows are ordered warehouse name, bin code, status, so what is cut is the tail of
> a stable order, not an arbitrary subset. 500 is far above how widely one item spreads in a real
> warehouse; the cap bounds the query, not the answer, and a caller that sees `truncated` narrows by
> `warehouse_id`, `lot_id` or `roll_id`.

### 4.7 `GET /operations/floors/{floor_id}/published-layout` — perm `operations.warehouse.layout.read`
`{floor_id, warehouse_id, floor_name, published: bool, layout_version_id|null,
placements:[{location_id, code, name, kind, parent_id|null, x_mm,y_mm,z_mm|null,width_mm,depth_mm,
height_mm|null}]}`. Published version only; skip soft-deleted locations; `published:false` with empty
placements when none. 404 for a foreign/missing floor.

### 4.8 Warehouses
- Extend existing `PATCH /operations/warehouses/{id}` (`WarehousePatch`, `extra="forbid"`),
  `WarehouseIn` and `WarehouseOut` with `lat_micro`, `lng_micro` (nullable ints). Validate the
  **merged** state (a patch setting only lat is checked against the persisted lng); explicit
  `null` for both clears; unpaired/out-of-range → 422. `update_warehouse` uses `setattr` from
  `model_dump(exclude_unset=True)` so wire names equal attribute names.
- `GET /operations/warehouses/map` — same permission as the existing warehouse list (verify which;
  record it in the report): `{sites:[{id,code,name,location,lat_micro,lng_micro,floor_count,
  stocked_location_count}], without_coordinates:[{id,code,name,location}]}`. These are *site*
  coordinates (approved), not worker coordinates.

### 4.9 Retention (D3)
Not an in-app job. Deliver `tools/purge_putaway_captures.py` (admin DB role; **dry-run by default**,
`--apply` to delete rows with `created_at < now() - interval '30 days'`, prints counts only, never
coordinates, optional `--organization-id`) and a RUNBOOK section. Reveal of a purged capture → 404,
UI says "capture expired". (If the owner prefers nulling coordinates only, it is a one-line change.)

## 5. Operations frontend (`frontend/product-operations`)

Hand-mirror new types in `src/api/types.ts` + `client.ts`; new fields on `WarehouseOut` are
`?: number | null` (it is constructed as a literal in ~12 files); update closed
`vi.mock('@/api/client')` factories.

1. **Sites map** `/warehouses/map`: `@vis.gl/react-google-maps` loaded lazily in its own chunk behind
   an error boundary. **No key / script error / quota → table of warehouses + inline notice** (the
   page must be fully usable). Marker popup: name, address, floors, stocked bins, "Open floors".
   Side list of warehouses without coordinates with "Set location". Key read from
   `import.meta.env.VITE_GOOGLE_MAPS_API_KEY` (declare in `vite-env.d.ts`); never log it.
2. **Warehouse dialog**: Places lookup via the design-system `Combobox` `loadOptions` (data API, not
   the legacy widget); fills `location`, sets lat/lng; manual lat/lng fields are the accessible
   fallback and the only path without a key. Convert degrees⇄micro ints at the boundary; paired and
   range validation client-side too.
3. **Floor view** (extend the existing floors route): 2-D SVG (default; read-only, pan/zoom, works
   without WebGL) + 3-D tab. Both fed by `getPublishedLayout`. 3-D: feed `Warehouse3DScene` real
   placements (mm→scene units), floor selector, multi-id highlight prop; floors with no published
   layout keep today's computed layout **and** its banner; remove the stale "not real" claims only
   when a published layout is in use. Skip placements with non-finite/zero sizes (list under "not on
   floor plan"). Bin drawer uses `listBalances` by location with its own error state.
4. **Find stock**: search on the stock page and on item/lot/roll detail → `/inventory/locate`;
   results grouped warehouse→floor with "Show on floor" (highlight, not colour-only, announced via
   `aria-live`) and "Show on map"; unplaced bins tagged "not on floor plan".
5. **Put-away detail** on move detail: bin, worker, time, matched suggestion; "Show worker
   location" visible only with `operations.inventory.putaway.location_read`; on click POSTs the
   reveal and shows the coordinates as text, plus (when the map is enabled) a pin + accuracy circle.
   With the map on, loading it sends the viewport's position to Google Maps (ADR-0028 says so); with
   the map disabled (no key) the reveal is text only and nothing but the reveal response carries the
   position. Add `putaway` to `RELOCATION_LABEL` (`trackingLabels.ts`).
6. Every failure in Maps / 3-D / locate / balances is local to its panel (error boundary + retry);
   slow loads show skeletons and a "taking longer than usual" note after 8 s; nothing else in
   Operations may break.

## 6. Worker app (`frontend/worker-app`)

New "Put away" screen (route + nav in both `useNavGroups.ts` and `App.tsx` `bottomNavExtra`; gate
`RequireGrant permission="operations.inventory.stock.transfer" scope="ORG"`).
Flow: pick item/lot/roll and source stock (via `GET /operations/inventory/stock`, which workers
hold; only rows whose `location_kind` is `receiving` or `staging` are offered, §4.3) → quantity →
suggested bin shown → scan bin label (existing `useBarcodeScanner`; manual entry fallback; trim
CR/LF) → amber warning + confirm if different from suggestion → submit. One
`navigator.geolocation.getCurrentPosition` (high accuracy, 5 s timeout) at submit; denied /
timeout / unsupported / offline ⇒ post without geo and show a non-blocking notice. Convert to
`Math.round(deg*1e6)`; omit non-finite values.
**New `putawayQueue`** (the existing `scanQueue` is for `scan_events` only and must not be reused):
persisted entries `{ownerSub, organizationId, idempotencyKey (random UUID at enqueue), frozen body,
captured_at, occurred_on?, status}`; network/5xx/408/429 ⇒ retry with the same key; any other 4xx ⇒
park as "needs attention" with the server message, offer discard, **continue the queue**; app-level
drainer mounted in `AppShell`; replays only for the matching signed-in user **and organization**; purge
on logout; coordinates kept only until success (and stripped from an entry that is parked). Do not
advertise PWA/offline-first (no service worker exists).

> **Amendment 2026-10-05 (review) — what the queue and the request actually do.**
> - **`occurred_on` is sent only when the put-away is confirmed offline.** The server refuses an
>   `occurred_on` after its own today, and a phone's clock can be a day ahead, so while the phone
>   believes it is online the field is left out and the server dates the put-away itself (a day-ahead
>   phone is never refused; a server-supplied date stays out of the idempotency hash, so a retry is
>   still the same request). Confirmed offline, there is no server to ask: the UTC date of the confirm
>   is frozen into the entry, so a replay days later still names the day the stock moved.
> - **A queue entry carries `organizationId` beside `ownerSub`.** An entry belongs to a person *in an
>   organization*: another account on the phone, or the same person signed in to another organization,
>   neither replays nor sees it.
> - **408 and 429 are retried, not parked.** They are the server asking for another go, so they take
>   the 5xx path (same key, later). A network failure ends a drain pass; a 5xx/408/429 moves on to the
>   next entry so one entry the server chokes on cannot hold the rest behind it. Every attempt is cut
>   off after 30 s and retried under the same key.
> - **Foreground refusal when the screen is gone is parked.** The entry the worker has just confirmed
>   gets its refusal (a 4xx) handed straight back to the screen so the bin can be rescanned, and leaves
>   the queue. That holds only while the screen is still there to show it; a request can take half a
>   minute on a weak signal, and a worker who has walked away would otherwise lose the refusal with the
>   entry. Then it is parked like any other and the attention banner tells them.

## 7. Infrastructure and docs

- `infrastructure/edge/publish-web.sh`: export `VITE_GOOGLE_MAPS_API_KEY` for the **operations**
  case only (never `worker`), read from the box `.env` via `_dotenv_value`; missing key ⇒ loud
  warning and publish map-less. Add to `.env.example` and `docs/deploy/ENV_SOURCE_MATRIX.md`.
  A `tests/infra` test (model: `test_publish_web_internal_app.py`) pins this. **No Caddy/CSP change.**
- RUNBOOK section modelled on "Inventory Phase 1": what migrates, code-only rollback
  (`make edge-rollback`; schema stays; never `alembic downgrade` past the new revision once captures
  exist), the 30-day purge procedure, Maps key note, limitation (c).
- ADR-0014 §12 amendment (add `putaway` to the move reference vocabulary; call the feature "storage
  put-away"); ADR-0025 untouched. Decision record for D3 (coordinates are not PII under ADR-0000, so
  plaintext + permission + audit row is stricter than precedent; erasure is admin-run).
- `docs/backlog.md`: move this item to delivered-on-branch with the open items.

## 8. Observability

Structured, id-and-count-only lines (never coordinates, never the Maps key): `putaway.geo_dropped`
(reason), `putaway.capture_failed`, `putaway.replayed`, `putaway.location_revealed` (actor, move id —
mirrors the audit row), `locate.failed`. Use whatever logging/metrics helpers the service already
uses (investigate; do not invent a metrics stack). Client: one report per session per failure type
(`maps_load_failed`, `floor_render_failed`, `webgl_unavailable`) via the apps' existing error
reporting if one exists, otherwise `console.warn` with no coordinates.

## 9. Test and gate requirements

Backend (per-service loop, isolated DB `opsgrids_wm`, serial): happy path; `bin_transfer` regression
(`/stock/transfers` still `bin_transfer` + `stock.transferred`); idempotency (replay = 1 capture /
1 audit / 1 event; same key different body = 409; same key different geo replays); geo table;
validation matrix (tracking, from==to, cross-warehouse, non-storable destination, non-receiving
source, reserved stock, barcode CR, reused deleted-bin barcode); negative-authz and cross-tenant
(`test_tracking_authz.py` / `test_cross_tenant_isolation.py` styles) for **every** new endpoint;
audit tests (N reveals ⇒ N rows; 403/404 ⇒ 0; recursive scan proves no lat/lng key in any audit
`after`, outbox payload, or non-reveal response); catalogue (4 segments, ADMIN only);
contracts (`make openapi-check`, permissions export `--check`, event schema); migration test (RLS,
privileges, CHECKs, downgrade refusal + override, empty round-trip, `alembic check`, single head);
EXPLAIN test for locate; `make boundaries`. Frontend: every error/fallback state above, 2-D
rendering from real placements, 3-D integration, search→highlight flow, no-key map fallback,
warehouse-without-coordinates, reveal gating, responsive (phone width), worker geo + queue
semantics, closed-mock updates, existing suites green; `pnpm typecheck && lint && test && build`
in both apps. Repo gates: `make check` pieces that apply, `make migration-roundtrip`.
Known pre-existing failures on main (do not "fix" silently): ruff format on
`consumers.py/schemas.py/models.py`, `test_route_table_parity` E501, `test_step_up_marker`
OAUTH_AMR, frontend `Warehouse3D` x2 and `PurchaseOrderForm` date fixtures x2.

## 10. Rollout (stage only — owner approves any deploy)

Backend PR → review/merge (squash) → owner-approved manual deploy (migrate is additive; old code
ignores the new columns/table) → verify (`alembic current`, privileges, synthetic put-away + audited
reveal, from a session that gave a one-time code, §4.5) → permission reconcile dry-run then `APPLY=1` (new routes 403 until it runs) →
`make edge-web APP=operations` then `APP=worker`. Rollback triggers: failed migration, put-away
error rate above baseline, any coordinate found in audit/outbox/logs, a reveal without an audit
row. (A *site's* position is approved data and legitimately appears in `warehouse.updated` audit
rows, entity `warehouse`; the trigger is a *worker's* coordinate anywhere else.) Rollback:
code-only; bundles by symlink swap; Maps off by rebuilding without the key.

## 11. Acceptance checklist

- [ ] Warehouses store coordinates; sites map works with a key and is cleanly disabled without one.
- [ ] Floor view (2-D/3-D) renders published placements only; draft data never appears.
- [ ] `locate` returns bins + placement-or-reason; highlight works; empty result handled.
- [ ] Put-away by bin scan from receiving/staging creates a `relocation` move (`reference_type
      putaway`) and one capture; replay creates nothing extra; `bin_transfer` unchanged.
- [ ] Bad GPS never blocks the move; offline put-aways replay safely under their own key.
- [ ] Reveal is POST-only, ADMIN-only, needs a one-time-code session (§4.5, owner to confirm), audited
      on every successful call; coordinates nowhere else.
- [ ] Tenant isolation + negative-authz for every endpoint; migration round-trip, contracts,
      typecheck, lint, build and existing suites green.
- [ ] 30-day purge procedure + RUNBOOK + ADR amendment delivered; limitation (c) documented.
- [ ] No deploy performed.

## 12. Known risks / open items

- On `main` today a goods receipt lands in the item's home (default, kind `bin`) location, so stock
  reaches a `receiving`/`staging` bin only through opening stock posted there, an explicit bin
  transfer, or Phase 2 receipts (unmerged). D1 is implemented strictly; this is flagged to the owner.
- Two unmerged inventory stacks share head `c4a8e1f6d273`; the second to merge re-parents.
- Plaintext GPS at rest + append-only storage: erasure is admin-run (D3).
- The Maps key ships in the bundle; it must be referrer- and API-restricted in the Google console.
