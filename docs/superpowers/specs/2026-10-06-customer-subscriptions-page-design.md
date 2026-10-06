# Customer Subscriptions page — plan change that says when, plans on offer, and a status-aware table

**Status:** built on `fix/customer-subscriptions-page` (stacked on `main`), not deployed
**Decision record:** [ADR-0032](../../adr/0032-customer-plan-change-and-plan-visibility.md)
**Services:** `subscription`, `catalog`, `frontend/opsgrids-dashboard` (`routes/Subscriptions.tsx`)

Fixes `app.opsgrids.com/subscriptions` as observed on 2026-10-06 in the owner's organization, after the
billing run converted its ended trials to `active`. Extends the staff plan-switch work (ADR-0029); replaces
nothing on the staff path.

---

## 1. What was wrong, and what closes it

| Observed (verified in code) | Closed by |
|---|---|
| Dialog: "Takes effect immediately". The customer `change_plan` defers a paid downgrade to the period end (no credit), prorates a paid upgrade now, moves a trial now and free, moves an equal price now | A customer preview from the write's own evaluation; the confirm shows its sentence (§3, §5.2) |
| Picker listed "Revenue Growth" twice plus "Revenue": every published version from catalog's tenant `/plans`, superseded ones included; no price, cadence or current marker | `plan-options`: catalog's offerable set, each version once, plan in force first and disabled, version label when names collide, cadence always, negotiated price where allowed (§4.1, §5.1) |
| The write accepted any published version of the product, superseded or retired-plan included | 422 `not_offerable` on the customer write (§3.3) |
| "Trial ends 5 Oct 2026 (0 days)" on `active` subscriptions | A "Renewal" column by state (§5.3) |
| No plan, price, interval or scheduled change per row; subtitle about trials only | A "Plan" column from `plan-summaries`; new subtitle (§4.2, §5.3) |

## 2. Timing rules — the customer path (unchanged; now said out loud)

From `services._evaluate_change_plan` with `forced_timing=None`:

| Subscription | Change | Lands | Money now |
|---|---|---|---|
| Trial, conversion not claimed | any direction | now | nothing |
| Trial, conversion already claimed (paid time) | upgrade | now | prorated charge for the rest of the claimed period |
| Trial, conversion already claimed | downgrade | end of the claimed period | none, no credit |
| Active | upgrade | now | prorated charge (`_claim_proration`), priced and invoiced by the billing run |
| Active | downgrade | `current_period_end` (or a pending claim's end) | none, no credit |
| Active | equal price | now | none; usage so far closed at the old pin's rates when it has a usage price |
| Active or trial with a change waiting | the plan in force | now (withdraws the waiting change) | none |
| Past due, suspended, cancelled | — | refused `state` | — |

Every row is pinned by `tests/test_customer_plan_page.py::TestTimingMatrix`, which asserts the preview's
answer **and** that the write then does exactly that (pin, pending pin, proration row).

## 3. Subscription API (customer surface, `/api/v1/subscriptions`, gateway prefix unchanged)

### 3.1 `POST /subscriptions/{id}/change-plan/preview` — `core.product.activation.write` at ORG

Body `{plan_version_id}` (`extra="forbid"`). Answer `ChangePlanPreviewOut`: `expected`, `action`
(`switch` | `withdraw_scheduled`), `direction`, `effective` (`now` | `next_period`), `effective_at`,
`would_clear_pending`, `entitled_added`/`removed`, `usage_priced`, `usage_closed_now`, and two new fields
shared with the staff preview: `paid` and `prorated_charge_now`. A refusal is a 200 with `refusal_code`
and `refusal_detail`: `state`, `cadence`, `currency`, `already_pinned`, `product_mismatch`,
`not_published`, `not_offerable`, `bundle`, `billing_catching_up`. 404 for another organization's
subscription or for a version that does not exist or is another organization's (no disclosure); 503
`catalog_unavailable`. Writes nothing, takes no lock (counts of transitions, audit, outbox and billing
periods are asserted unchanged).

### 3.2 `POST /subscriptions/{id}/change-plan` — gains `expected`

Optional `expected {state, plan_version_id, pending_plan_version_id}`, compared under the row lock before
anything else: 409 `state_changed`. The dashboard always sends the preview's. The bundle refusal stays a
403 on the write.

### 3.3 Offerable, enforced

`_evaluate_change_plan(enforce_offerable=True)` on the customer path asks catalog's
`/internal/v1/organizations/{org}/offerable-versions` and refuses anything outside it with 422
`not_offerable` — after state, version, product, bundle, currency and cadence; before
`billing_catching_up`. The plan in force is exempt (withdraw). Catalog admits `subscription` as a caller
of that read (`TRUSTED_CALLERS = {"console", "subscription"}`); every other caller is still 401.

### 3.4 `GET /subscriptions/{id}/plan-options` — `core.product.activation.write` at ORG

`{subscription_id, product_key, state, change_blocked_code (state|bundle|null), current, pending,
pending_effective_at, options[]}`. Each option: plan id/name, version id/no, billing interval,
`negotiated`, `amount_visible`, and — only when visible — `currency`, `recurring_minor`, `prices`; plus
`is_current`, `is_pending`, `blocked_code` (`current`, `cadence`, `currency`, `bundle`) and a customer
sentence. The plan in force is first and blocked; when it is no longer offered it is still listed first.

### 3.5 `GET /subscriptions/plan-summaries` — `core.product.activation.read` at ORG

One row per subscription `GET /subscriptions` returns (same `ctx.apply` selector): `current`, `pending`,
`pending_effective_at` (`selectors.scheduled_change_effective_at`: a pending claim's end, else
`current_period_end` — the boundary the write and the billing run use). Registered before
`/subscriptions/{subscription_id}`. One catalog lookup per distinct pin.

### 3.6 Which amounts a tenant sees (ADR-0032 §5)

`plan_options.amount_visible`: the version is restricted to the caller's organization **and** the caller
holds `core.billing.invoice.read` at ORG. A public version's amount is never sent — including the public
version the organization is on (every trial is pinned to one). Tests assert the raw JSON contains no
public amount, and none at all without the invoice permission.

## 4. Catalog

Only the caller set of the offerable read changes (§3.3). The tenant `/plans` and `/catalog/plans/{id}`
reads stay amount-free (#59 / `33baa3eb`).

## 5. Dashboard

### 5.1 Change plan dialog (`routes/subscriptions/ChangePlanDialog.tsx`)

* **Pick** (`FormDialog`, mounted only while open): "Now on X · Billed monthly" and, when a change waits,
  "A change to Y is scheduled for D. Choosing another plan replaces it." A `RadioGroup` (legend "Move to")
  from `plan-options`: each version once (`uniqueOptions`), labels from `optionLabels` (plan name, plus
  "· vN" only where names collide, plus cadence where name and version both collide), the plan in force
  tagged "Current plan" and disabled, a scheduled one tagged "Scheduled", a negotiated one tagged
  "Negotiated" with its price, a blocked one disabled with the server's reason. Helper text: prices are
  shown only for plans agreed for your organisation.
* **Review change** calls the preview. A refusal stays on the picker with the server's sentence and
  `refusalGuidance` (`not_offerable` reloads the options and clears the choice). Nothing is said about
  timing before the server answers.
* **Confirm** (`ConfirmDialog`, primary, stacked so Back keeps the choice): title "Schedule X's move to
  Y?" or "Move X to Y now?", body `previewSentence` (§2's rows in words), notes (replaces the scheduled
  change, adds/removes access, fixed-charges-only comparison, ~15 min access lag), button "Schedule
  change" or "Change plan". The write carries `expected`; `state_changed`, `not_offerable`,
  `already_pinned` and `state` return to the picker with fresh options; anything else stays with the
  server's sentence and its reference.

### 5.2 Keep current plan (`routes/subscriptions/KeepPlanDialog.tsx`)

Verified supported: the customer `change_plan` to the pin in force with a change waiting takes the
`withdraw` branch (`_validate_new_pin` accepts the current pin even when no longer published or offered).
The row action appears only when the plan line shows a waiting change and the subscription can change
plan; the confirm previews first, proceeds only on `withdraw_scheduled`, and sends `expected`.

### 5.3 The table

* **Plan** column: plan name; `priceLine` (negotiated price "₹1,800.00 per month · agreed for your
  organisation", otherwise "Billed monthly"); "Changes to Y on D" when a change waits. "—" when the plan
  line could not be read (the page loads it separately and survives its failure).
* **Renewal** column (was "Trial ends"), `statusLine`: trial "Trial ends D" + "N days left" ("Trial ended
  D" once past and not yet converted — never "(0 days)"); active "Renews D"; past_due "Awaiting payment"
  (the product cell's dunning line already says "Payment overdue" with the date access ends);
  suspended "Access suspended" with the way back; cancelled "Cancelled D".
* "Current period" stays on desktop and is hidden from phone cards.
* Subtitle: "Each product your organisation has started: its plan, its state, and when it renews or its
  trial ends."
* Row actions: Change plan (disabled with the reason for past_due, suspended and bundles; hidden without
  the write permission or when cancelled), Keep current plan, Cancel.

### 5.4 Bulk selection — decision

The checkboxes drive one bulk action, **Export selected** (a client-side CSV of rows already on screen;
no server call). There is no bulk cancel and none is added: cancel is final and per-subscription, and a
plan change needs its own preview. Kept, with Plan and scheduled-change columns added to the CSV and the
trial end exported only for trials.

### 5.5 Cancel

`services.cancel` is immediate and terminal, and a cancelled row blocks a new subscription for that
product (`already_subscribed`). The confirm now says so by state: a trial "ends now … can't be started
again from this dashboard"; a paid subscription "Cancellation is immediate and final: access ends now,
not at the end of the billing period, and there is no automatic refund for the time left. Any usage-based
charges so far this period are still invoiced." Plus "The scheduled change to Y will not happen." when one
waits.

## 6. Tests

* **Backend** (`services/subscription/tests/test_customer_plan_page.py`, real PostgreSQL): negative authz
  for the preview and picker (none, read-only, OWN-scoped write, invoices-only) with no catalog call and
  nothing written; the plan line's read permission; cross-tenant 404 on every new route and the write,
  another organization's subscription absent from the plan line, another organization's negotiated
  version neither offered nor priced; the timing matrix (§2) preview-then-write; withdraw (including a
  no-longer-offered plan in force); refusals as answers with the write's same code (currency, cadence,
  not_published, state for past_due/suspended, superseded and retired `not_offerable`, billing catching
  up, bundle subscription and bundle rung); stale `expected` → 409 with nothing written; catalog down →
  503 with nothing written; picker de-dup, order, marks and blocks; amount visibility with and without
  the invoice permission. Catalog: only console and subscription may read offerable versions.
* **Frontend** (`Subscriptions.test.tsx`, `lib/subscriptionCopy.test.ts`): statuses for trial, active,
  past_due, suspended, cancelled; plan line, scheduled change and negotiated price, no `₹` for public
  plans; plan line failure; subtitle; read-only member sees no write actions; disabled Change plan with
  its reason; phone width renders cards with plan and renewal; picker de-dup, collision labels, current
  marker and disabled blocked rows; preview-driven copy for next-period and prorated-now (and never
  "immediately"); refusal shown with guidance and no confirm; stale confirm back to the picker; Keep
  current plan previews then sends `expected`; cancel copy; money formatted from minor units including a
  zero-exponent currency.

## 7. Deploy and rollback

Catalog first (the caller set), then subscription, then the dashboard. No migration. Until catalog is
deployed, customer change-plan and the plan reads answer 503 `catalog_unavailable` and change nothing;
the table renders without the plan line. Rollback: the images. `make openapi` regenerated
`libs/contracts/openapi/subscription.json`; catalog's change is to an internal route (not in OpenAPI).

## 8. Merge note

`feat/dashboard-pay-now` (#107) also edits `Subscriptions.tsx` (an import after `PageHeader`, a link in
the product cell's dunning line) and appends tests. This branch leaves both regions untouched and
`git merge-tree` reports no conflict; its "Payment overdue" text assertion stays unique because the
Renewal column says "Awaiting payment".
