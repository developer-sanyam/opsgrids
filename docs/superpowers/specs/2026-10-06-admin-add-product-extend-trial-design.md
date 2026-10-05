# Admin add product and extend trial — staff plan control, second release

**Status:** design, built on `feat/admin-add-product-trial` (stacked on `feat/admin-plan-switch`), not
deployed · **Decision record:** [ADR-0030](../../adr/0030-staff-add-product-and-trial-extension.md)
**Services:** `subscription`, `console`, `frontend/opsgrids-internal`; consumers in `product`, `console`,
`notification`

The design review's "Slice 4" (S3a, Add product) and "Slice 5" (S4, Extend trial), on top of the first
release ([admin plan switch](2026-10-06-admin-plan-switch-design.md), ADR-0029). Reuses its claim table,
coded refusals, note, reserved-reason guard, outcome-unknown reconcile and `operator:<sub>`. Cancel stays
out of scope.

---

## 1. Owner decisions this builds to

| | Add product | Extend trial |
|---|---|---|
| Who | owner, billing_ops | **owner only** (`OperatorOwner`) |
| Default | **trial** | — |
| Money | a paid add claims the first period now, no mandate check → explicit acknowledgement (`acknowledge_immediate_charge`, a UI checkbox) | none — the trial stays free |
| Refused | `org_currency_mismatch`, `one_time`, a row for the product in **any** state incl. cancelled (409) | not a trial, a pending lifecycle claim (`claim_in_flight`), past the cap (`trial_cap`) |
| Shape | reason + required `Idempotency-Key` | absolute `new_trial_ends_at` + `expected_trial_ends_at`, reason, key |
| Picker | hides products with a row in any state; bundles allowed | IST date with the zone named, cap shown |
| Event | the existing `trial_started` / `activated` | **new** `commerce.subscription.trial_extended` |

## 2. Subscription

### 2.1 Operator subscribe (`POST /internal/v1/subscriptions`, console-only)
Order: reserved-word note → advisory lock `{org}:operator-subscribe` → existing row (409
`already_subscribed`, any state; a cancelled one says it cannot be reopened yet) → version (404
`not_found` / 422 `not_published`) → `product_mismatch` → **`one_time`** → **`org_currency_mismatch`**
(live = every state but `cancelled`) → bundle → `trial_not_offered`. A lost race on
`uq_subscription_org_product` is caught at the flush and answered as the same coded 409. The customer
path shares the existence check and the race fix (coded now), not the two new refusals.

### 2.2 Extend trial (`POST /internal/v1/subscriptions/{id}/extend-trial`, console-only)
Body `{organization_id, operator_id, new_trial_ends_at, expected_trial_ends_at, note}` — both instants
`AwareDatetime`, the note required (1–500), `extra="forbid"`. Under `lock_subscription`:

1. `trial_ends_at != expected` → 409 `state_changed` (a replay lands here: no time added twice)
2. not `trial` → 422 `not_trial`
3. `pending_lifecycle_claim` → 422 `claim_in_flight` (the run voids a claimed conversion whose end moved)
4. `domain.trial_extension_refusal`: new ≤ current or ≤ now → `trial_not_extended`; no start recorded or
   new > `current_period_start + MAX_TOTAL_TRIAL_DAYS` → `trial_cap`

Then `trial_ends_at = new` (UTC) and one `trial -> trial` transition, reason `trial_extended` (reserved;
`decide` accepts it only on that self-edge), event `commerce.subscription.trial_extended`: the lifecycle
payload + `previous_trial_ends_at`. The operator's note is the audit row's reason only — never on
the event (the plan switch's review fix: a customer org's notification templates render the whole
payload). The audit row's before/after name the two ends.
`current_period_end` is not touched — a trial's period end was never its conversion point.

`MAX_TOTAL_TRIAL_DAYS = 90` (owner-adjustable default). The operator listing carries
`trial_max_ends_at = current_period_start + 90 days` for a trial, `null` otherwise.

## 3. Consumers

| Service | Binds `trial_extended` | Does |
|---|---|---|
| product | yes (`SUBSCRIPTION_EVENTS`) | the projection takes the new `trial_ends_at`; the per-source ordering guard drops a stale one |
| console | yes (`domain.SUBSCRIPTION_EVENTS`) | the fold moves the snapshot's trial end; `revenue_events` kind `trial_extended` — never a trial start or a conversion |
| notification | listed in `SUBSCRIBABLE_EVENTS` | nothing unless a rule is authored for it |
| account | no | an extension installs nothing |

## 4. Console (`/api/v1/admin/organizations/{id}`)

| Route | Role | Purpose |
|---|---|---|
| `GET /add-product-options` | billing | products from catalog's public-flat-plans with **no row in any state**, each with its offerable versions; `blocked_code` `org_currency_mismatch` / `one_time`; `offers_trial`; `overlapping_products` for a bundle; `entitled_through` (per product: the live bundles that already entitle it); the org's live `currency`; `existing` |
| `POST /subscriptions` + `Idempotency-Key` | billing | `{product_key, plan_version_id, trial=true, acknowledge_immediate_charge=false, reason}` → 201 `{subscription_id, product_key, state, plan_version_id, currency, trial_ends_at, current_period_start, current_period_end, entitled_products, charge_claimed, replayed}` |
| `POST /subscriptions/{product_key}/extend-trial` + `Idempotency-Key` | **owner** | `{new_trial_ends_at, expected_trial_ends_at, reason}` → `{subscription_id, product_key, state, trial_ends_at, previous_trial_ends_at, replayed}` |

The pricing composite's subscription gains `current_period_start` and `trial_max_ends_at`.

**Claim, call, record** — exactly ADR-0029 §3/§9, actions `add_product` / `extend_trial`, audit actions
`subscription.add_product.{requested,applied,refused,outcome_unknown}` and
`subscription.trial_extension.{…}`. Fingerprints: add = `(org, product, version, trial)`; extend =
`(org, product, subscription, new end, expected end)` with instants normalised to UTC (the same moment in
IST and UTC is one request). The reason and the acknowledgement are excluded. Reconcile: add landed when a
row for the product is pinned to the target and trials exactly when a trial was asked for; extend landed
when the live trial ends at the target.

| Status | Codes |
|---|---|
| 404 | `not_found` (add), `no_subscription` (extend) |
| 409 | `already_subscribed`, `state_changed`, `request_in_progress`, `idempotency_key_reused` |
| 422 | `charge_not_acknowledged` (console, before any claim), `not_offerable` (console, recorded), `one_time`, `org_currency_mismatch`, `trial_not_offered`, `not_published`, `product_mismatch`, `bundle`, `not_trial`, `claim_in_flight`, `trial_cap`, `trial_not_extended`, `reserved_reason`; body validation |
| 503 | `outcome_unknown`, `upstream_refused`, `catalog_unavailable` (add: the offer could not be re-read; nothing was claimed or changed) |

**Only what is on offer** (the plan switch's review fix, applied to the add): the confirm re-reads
catalog's offerable set for the product before the claim and, after the replay branches, refuses any
other version with 422 `not_offerable` — recorded and replayed like any refusal, subscription never
called. **A late first attempt** (also the plan switch's): a reconciling retry whose call is refused
`already_subscribed` (add) or `state_changed` (extend) looks at the live subscription again and, if the
lost attempt is what landed, records the success instead of replaying a refusal. When that second look
fails, the 409 cannot be told from a real refusal, so it is recorded `outcome_unknown` (503) — never a
permanent `refused` — and the next retry with the same key reconciles.

## 5. Frontend — `opsgrids-internal`

Two more cards under Change plan, each hosting its own dialogs (the page reload would unmount them):

* **Add product** — FormDialog: product (Select, with the not-offered products named), plan (RadioGroup;
  blocked versions disabled with the server's reason), start (Trial — default — or Paid from today; Trial
  disabled when the plan sells none), the acknowledgement checkbox for a paid start, a bundle-overlap
  warning either way (a bundle over members held separately; a product a live bundle already includes),
  reason → ConfirmDialog (the sentence; a paid start says no mandate is checked and what a failed
  charge does) → `aria-live=polite` result. Role hint for support.
* **Extend trial** — a table of trialling subscriptions from the live composite (`useLivePricing`, now in
  `lib/`), with the end and the cap in IST. FormDialog: the trial's new last day (`type=date`, `min`/`max`
  computed in IST from the end, now and the cap), "ends at 23:59 IST (UTC+05:30)", reason →
  ConfirmDialog → result. Sends `…T23:59:59+05:30` and the end exactly as read. Disabled with a hint for
  non-owners and for a trial at its cap. Re-reads when Add product reports a change — as do Change plan
  and Client pricing, so a product just added shows on every card without a page reload.

Both: key minted per confirm, reused by *Try again* after an unknown outcome, replaced after a refusal
(which returns to the form with the server's sentence, its reference and the next step); a double click
sends once; focus returns to the opening control — or, after an extension to the last day the cap
allows (which disables that control), to the result's Dismiss. The Extend trial dialog works from the
card's live row, so a refusal's re-read updates the end, the window and the next request's expected end;
the request a key was minted for is fixed at Review, so *Try again* sends it unchanged.

## 6. Tests

* **Subscription** (`test_operator_add_product_extend_trial.py`): any-state existing row (409, writes
  nothing), `one_time`, currency mismatch incl. past_due/suspended setting it and cancelled not, the
  currency check scoped to the named organization, trial default and no claim, paid claims an opening
  period, `trial_not_offered`, bundle allowed, the unique-index race → coded 409; extend-trial console-only
  (no token, `tests`, `billing`, `catalog`), **cross-tenant** 404 writing nothing, the event/transition/
  audit shape and no second `trial_started`, replay adds nothing, stale view, not trial, claim in flight,
  the cap to the second, not later, lapsed trial → future only and no longer due, malformed bodies,
  reserved words; the pure rule and `decide`'s guard.
* **Product**: binds the key; a lapsed trial is entitled again; a stale extension does not undo a later
  conversion.
* **Console**: negative-authz loops (product audience 404, no otp 403, support 403, no role 403,
  billing_ops 403 on the owner-only route, 401) with nothing downstream and no claim; the picker; the
  acknowledgement; the idempotency matrix (replay, different body, cross-action key, per-org, in flight,
  concurrent → one call); every coded refusal surfaced/recorded/replayed; outcome unknown and both
  reconcile paths; IST vs UTC is one request; the composite carries the cap; the fold does not count a
  trial start (metrics and series).
* **Frontend**: copy and IST arithmetic tables; client paths; fixtures obey the rules; both cards' states,
  role hints, validation, the acknowledgement gate, request bodies, double click, refusals, unknown
  outcomes and focus return.

## 7. Rollout

* Deploy product and console (the consumers) first, then subscription, then opsgrids-internal (and
  notification). See the RUNBOOK section "Staff add product and extend trial".
* `make openapi` regenerates `console.json`. No migration.
