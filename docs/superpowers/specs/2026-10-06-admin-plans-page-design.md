# Admin Plans & prices page — design

**Date:** 2026-10-06 · **Branch:** `feat/admin-plans-page` · **Decision record:** [ADR-0029](../../adr/0029-catalog-operator-writes.md)
**Source:** admin plan-control critique, slices 2 (read-only) and 3 (writes), findings H2, H3, H5, B6.

## Goal

Staff can see what every customer can buy, and the owner can change it safely, from the operator console
(`admin.opsgrids.com/plans`), over catalog's own `/api/v1/admin/catalog` surface. No new gateway prefix.

## Owner decisions (2026-10-06)

- Reads: `owner` and `billing_ops`. **Not `support`** — bespoke versions expose negotiated prices.
- Writes: `owner` only. Every write needs a reason and a **required** `Idempotency-Key`, claimed in a new
  `catalog.idempotency_keys` table (unique key, fingerprint, response) written before the change.
- Publish is immutable, and the confirm copy says so.

## API (catalog)

| Route | Role | Notes |
|---|---|---|
| `GET /admin/catalog/plans` | owner, billing_ops | `product_key`, `audience=public\|restricted\|all` (default `public`), `state`, `cursor`, `limit` ≤ 100. Rows carry version counts, the current public published version with its sheet, and the latest version number. Keyset on `(product_key, sort_order, code, id)`. |
| `GET /admin/catalog/plans/{id}` | owner, billing_ops | Plan + counts + one page of version **summaries** (no sheet), newest first, per `audience` (default `public`), cursor on `version_no`. |
| `GET /admin/catalog/versions/{id}` | owner, billing_ops | Full sheet. `plan_id`, `plan_name`, `product_key`, `plan_state`; prices with `id`, sorted flat → per-seat → usage (ties by id); features by key. |
| `POST /admin/catalog/plans` | owner | `{product_key, code, name, sort_order, reason}`; product must be registered and not a platform product. 201. |
| `PATCH /admin/catalog/plans/{id}` | owner | `{name?, sort_order?, reason}`. |
| `POST /admin/catalog/plans/{id}/retire` · `/reactivate` | owner | `{reason}`. Retire emits `catalog.plan.retired`, reactivate `catalog.plan.reactivated`. |
| `POST /admin/catalog/plans/{id}/versions` | owner | The **full** sheet + `reason`; `extra="forbid"`. Savepoint retry on the version-number race. 201. |
| `POST /admin/catalog/versions/{id}/publish` | owner | `{reason, allow_zero_amounts=false}`; validated first, every problem in `errors[]`. |
| `POST /admin/catalog/versions/{id}/withdraw` | owner | `{reason}`; `published -> grandfathered`; audit + `catalog.plan_version.grandfathered`. |
| `DELETE /admin/catalog/versions/{id}` | owner | `{reason}`; drafts only (children deleted first; `seat_classes.price_id` is RESTRICT). |

All writes: `Idempotency-Key` 8–128 chars. Replay → recorded status and body + `Idempotent-Replayed: true`.
Different body or operator → 409. Missing → 422. Refused request → claim rolled back (key reusable).
State-changing writes lock the row (`SELECT … FOR UPDATE`) and judge state under the lock.
The product registry (plan creation, bundle members at draft and publish) is read in a dependency declared
before the session one, so no HTTP call runs inside the transaction (D3 #10, fixed); services take a
`ProductSnapshot`, and an unreachable registry fails (503) only the checks that need it.

### Publish validation (`domain.publish_problems`)

| Problem | Code |
|---|---|
| no prices | `no_prices` |
| currency outside `SELLABLE_CURRENCIES` (INR USD EUR GBP AED SGD AUD CAD JPY KWD) | `currency_not_sellable` |
| price currency ≠ version currency | `currency_mismatch` |
| flat / per-seat with no amount | `amount_missing` |
| zero amount or zero rate without `allow_zero_amounts` | `zero_amount` |
| per-seat with no unit | `unit_missing` |
| usage with no meter / no rate | `meter_missing` / `rate_missing` |
| `trial_days` not a whole number 0–365 | `trial_days_invalid` |
| bundle member today's registry rejects / no registry | `bundle_member` / `bundle_unverifiable` |

### Migration `b7e4d2a9c613`

- `forbid_published_version_update()` now: grandfathered → refuse any UPDATE; published → allow only
  `state = 'grandfathered'` with the rest of the row identical (`probe := NEW; probe.state := OLD.state;
  probe IS NOT DISTINCT FROM OLD`); draft → free. Withdraw sets `updated_at` to itself so the ORM's
  `onupdate` does not count as a second change.
- `catalog.idempotency_keys` (+ RLS enable/force, `tenant_isolation` policy for `opsgrids_catalog`).
- Down: drop the table, restore the original function body.

## Frontend (`opsgrids-internal`)

- `routes/Plans.tsx` replaces the stub. Support: a notice, no call. billing_ops: read-only. Owner: actions.
- **Table** (`plans/PlanList.tsx`): DataTable with phone cards, FilterBar (product, audience, state), "Load
  more" on the cursor, live region with the row count, empty/error/loading states.
- **Detail** (`plans/PlanDetail.tsx`, `?plan=<id>`): header with counts and state, audience tabs, version
  summaries; each version's sheet loads when opened, with the actions its state allows. Dialogs are hosted
  outside the reloading subtree.
- **Writes**: `ReasonDialog` (publish, withdraw, delete draft, retire, reactivate) and `DraftVersionDialog`,
  `CreatePlanDialog` — all `@opsgrids/ui` `FormDialog`, reason required, one `Idempotency-Key` per open reused
  on retry, a synchronous latch against double clicks, `errors[]` and `request_id` shown verbatim, results
  announced in a `role="status"` region. A 5xx says the outcome is unknown and to reload.
- **Draft editor** (`plans/draftSheet.ts`): prefilled from a whole version and always sends the whole sheet;
  typed `trial_days` and `bundled_products`; other features carried verbatim (remove-only); seat classes
  re-pointed to their price by position.
- **Money** (`lib/minorUnits.ts`): digits-as-text + BigInt, the currency's own exponent (JPY 0, KWD 3), micros
  for usage rates; a JSON number only once proven safe. No `Number(x) * 100`.
- Publish confirm: "Publishing is permanent." plus an explicit zero-amount switch when the sheet has a zero.

## Tests

- catalog `tests/test_admin_plans.py`: negative-authz loop over every route (support, no-otp, product audience,
  unauthenticated; billing_ops reads 200 / writes 403; registry never called; nothing changed), reads
  (audience default, filters, cursor walk, invalid cursor, lazy detail, deterministic sheet), idempotency matrix
  (replay, different body, other operator, missing/short key, refused request keeps key, retried publish,
  concurrent claims execute once, RLS on claims), publish validation table, trigger (withdraw allowed, every
  other change refused, grandfathered frozen, draft free), withdraw/delete/retire/reactivate, reason bounds,
  registry check at plan creation, draft race retry and give-up.
- migration up/down/up on an isolated database.
- console: `Plans.test.tsx` (role hints both ways, states, filters, paging, publish copy + reason + key reuse +
  `errors[]`, double click, full-sheet draft, withdraw, create plan), `draftSheet.test.ts`,
  `minorUnits.test.ts` (JPY/KWD exponents, no float).

## Not in this slice

- Subscription refusing new pins on a retired plan (`_validate_new_pin`) — needs a subscription change.
- A visual-harness entry for the page and its dialogs.
- Editing other feature values in the draft editor (they are carried over or removed, not edited).
