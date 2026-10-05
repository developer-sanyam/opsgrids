# Admin plan switch — staff plan control, first release

**Status:** design, owner decisions final 2026-10-06 · built on `feat/admin-plan-switch`
**Decision record:** [ADR-0029](../../adr/0029-staff-plan-control.md)
**Services:** `subscription`, `catalog`, `console`, `frontend/opsgrids-internal`

Extends the operator change-plan that subscription already had (ADR-0004, ADR-0005 addendum §4) and the console
pricing flow (`POST .../pricing`). Replaces nothing.

---

## 1. Why, and what was wrong

Staff could not change a plan from admin. The design review verified, in code, the reasons the obvious button was
unsafe; each is closed by this release.

| Finding | Closed by |
|---|---|
| `suspend` and operator `subscribe` accepted a token from any service holding one for subscription | `ConsoleCallerDep` on both (§3.1) |
| No operator reason travelled downstream; the transition `reason` is `VARCHAR(60)` and types the event; suspend/reactivate/cancel accepted 200 characters (a 500) | `note` through `_transition`; `RESERVED_REASONS` on every write (§3.2) |
| Every refusal a 422; "billing is catching up" fired before the state refusal | Stable codes and precedence (§3.3) |
| A deferred operator change lost its operator; an applied price increase was labelled `downgraded`; an immediate change cleared a waiting one silently | `acting_as`, `change_superseded`, direction-labelled apply (§3.4) |
| No dry-run anywhere; no expected-state guard | Read-only preview and `expected` (§4.2) |
| Console's `Idempotency-Key` optional, lookup-only, written after the call, no unique index | `console.idempotency_keys` claim (§5) |
| Console cannot read catalog's owner-only authoring router; `PlanVersionOut` lacks plan identity | Console-only offerable-versions read (§4.1) |
| The Subscriptions card has no subscription id; the page reload unmounts dialogs | A card driven by the live pricing composite, hosting its own dialogs (§6) |

## 2. The rules (owner decisions, final)

1. **Trial:** switches immediately and free.
2. **Paying subscription:** switches at the **next billing period**. Staff cannot force "now, prorated".
3. **Equal-price paying change:** applies now at no charge. **The server computes direction**; the UI shows what the
   preview says.
4. **Goodwill** for a customer who should have the new plan sooner: the existing comp-credit action.
5. **Roles:** `owner` and `billing_ops`. Not `support`.
6. **Every action** has a reason (stored as a separate note), a confirm screen with a server-computed preview, a
   required `Idempotency-Key` with a claim written before the downstream call, an expected-state guard (409
   `state_changed`) and an audit row.
7. **Later releases, not built, not blocked:** cancel, add product, extend trial, the Plans page.

## 3. Subscription hardening (slice 0)

### 3.1 Console-only
`suspend`, operator `subscribe`, `change-plan` and `change-plan/preview` take `ConsoleCallerDep`
(`request.state.service_caller == "console"`, the verified token's `iss`). A valid token for subscription minted by
any other service, or none, is 401 and writes nothing.

### 3.2 The note
`_transition(..., note=)`: the audit row's `reason` is the note (else the machine reason). The note is **not** put
on the event payload: the event carries the customer's organization and notification renders a customer org's own
templates from the whole payload, so `{{note}}` would mail staff's internal reason to the customer. The transition's `reason` stays a machine code — now `operator_suspend`, `operator_reactivate`,
`customer_cancel` for the three that used to store free text there (existing rows are unchanged; the words are in
`audit_logs.reason` either way). `domain.assert_free_text_allowed` checks `RESERVED_REASONS` on change-plan,
subscribe, suspend, reactivate and the customer cancel → 422 `reserved_reason`. `note` is 1–500 characters, optional
on the internal schemas so a bundle rung change and console's price change keep working; console always sends one.

### 3.3 Codes and precedence
Business refusals carry `errors: [{"code", "message"}]`. In `_evaluate_change_plan` the order is: `state` →
version resolved (`not_found` / `not_published`) → `already_pinned` (or the withdraw) → `product_mismatch` →
`bundle` → `currency` → `cadence` → **`billing_catching_up`** → money. With `expected`, `state_changed` is decided
first of all, under the row lock.

### 3.4 Deferred changes
`pending_change` stores `reason` (`upgrade`/`downgrade`) and `note`. The scheduled and cleared audit rows carry
`acting_as` and the note; the scheduled row's `before` names the change it replaced; an apply-now over a waiting
change writes `change_superseded`. `apply_pending_change` emits `upgraded` or `downgraded` by the stored direction
(none stored: `downgraded`, as before). Consumers (product, account, console, notification) fold both identically.

## 4. Slice 1 — the contracts

### 4.1 Catalog (service token, `caller=console`)
`GET /internal/v1/organizations/{org_id}/offerable-versions?product_key=` → `{versions: [...]}`. Each row:
`plan_id, plan_code, plan_name, product_key, plan_version_id, version_no, state, billing_interval, currency,
restricted_to_org_id, published_at, prices[{id, kind, amount_minor, currency, unit, unit_price_micros, meter_key}],
features[]`. Active plans only; published versions that are public or restricted to exactly `org_id`; a version is
dropped when a higher-numbered published version of the same plan has the same audience, cadence and currency.
Include-in-schema false; no migration.

### 4.2 Subscription (internal, console-only)
* `POST /internal/v1/subscriptions/{id}/change-plan` gains `note` and `expected {state, plan_version_id,
  pending_plan_version_id}`.
* `POST /internal/v1/subscriptions/{id}/change-plan/preview` `{organization_id, plan_version_id}` → `{expected,
  action: switch|withdraw_scheduled, direction: upgrade|downgrade|equal|null, effective: now|next_period|null,
  effective_at, would_clear_pending, entitled_added, entitled_removed, usage_priced, usage_closed_now, refusal_code,
  refusal_detail}`. Read-only: no row written, no row lock taken (the billing-claim check reads the claim). A refusal
  is returned as data; only 404 and an unreachable catalog raise. Evaluated as the staff path is
  (`timing="next_period"`). `usage_closed_now`: applying now closes the usage metered so far at the current pin's
  usage rates (an active subscription, or a trial whose conversion is claimed, on a current version with a usage
  price) — the apply branch's own `schedule_closing_usage` rule — so an equal-price switch is "no proration", not
  "nothing charged".
* Every operator route (`suspend`, `reactivate`, `change-plan`, `change-plan/preview`) looks the subscription up
  **filtered on the payload's `organization_id`** as well as under the tenant binding: RLS is the net, not the only
  guard.
* Direction is `domain.plan_change_direction` over `_list_price_minor` — the exact rule the write uses.

### 4.3 Console (`/api/v1/admin/organizations/{id}`, role `OperatorBilling`)
| Route | Purpose |
|---|---|
| `GET /plan-options?product_key=` | The live subscription and the offerable versions, each marked `is_current`, `is_pending` and `blocked_code` (`currency` / `cadence`) |
| `POST /subscriptions/{product_key}/plan-change:preview` `{plan_version_id, timing: auto\|next_period}` | Subscription's preview, passed on; a target it would accept but catalog no longer offers this organization is refused as data (`not_offerable`) |
| `POST /subscriptions/{product_key}/plan-change` `{plan_version_id, timing, reason, expected}` + `Idempotency-Key` | The change — the target must be in catalog's offerable set *now* (re-read before the claim; enforced after the replay branches), except a withdrawal back to the current pin |

The subscription is resolved server-side from `(organization, product_key)`; the body never names it. Response of
the confirm: `{effective: now|next_period, state, plan_version_id, pending_plan_version_id, effective_at, replayed}`,
read back off subscription's own answer. `timing` has no `now` value.

**Errors** (`errors[0].code`; the detail is subscription's own sentence, shown verbatim with `request_id`):

| Status | Codes |
|---|---|
| 404 | `no_subscription`, `not_found` (also another organization's bespoke version) |
| 403 | role / second factor (`require_operator`) |
| 409 | `state_changed`, `billing_catching_up`, `request_in_progress`, `idempotency_key_reused` |
| 422 | `state`, `cadence`, `currency`, `already_pinned`, `product_mismatch`, `not_published`, `not_offerable`, `reserved_reason`, `bundle`; and the body's own validation (reason 1–500, no extra fields, no `timing: now`, `Idempotency-Key` 8–255) |
| 503 | `outcome_unknown` (a confirm's timeout / 5xx — the change may or may not have been made), `upstream_refused` (console's own credentials; nothing was changed), `catalog_unavailable` (the offerable re-read failed; this request changed nothing), `preview_unavailable` (the preview only — a read, so never `outcome_unknown`) |

## 5. The claim — `console.idempotency_keys`

`id, organization_id, key, action, fingerprint, status, response JSONB, created_at, updated_at, …` (TenantBase);
unique `(organization_id, key)`; RLS ENABLE + FORCE with the standard `tenant_isolation` policy; app-role DML;
not a directory-reader table.

1. **Claim** (own transaction, committed before any call): `INSERT … ON CONFLICT DO NOTHING`; the winner also writes
   the intent audit row `subscription.plan_change.requested` (reason = the note, `acting_as = operator:<sub>`).
2. **Call**: subscription's change-plan, `timing="next_period"`, `note`, `expected`, `operator_id = operator:<sub>`.
   No database connection is held.
3. **Record** (one transaction): status + replayable response + `subscription.plan_change.applied` /
   `.refused` / `.outcome_unknown`.

| Same key arrives… | Result |
|---|---|
| after success | the recorded result, `replayed: true`, no call |
| after a definitive refusal (404 / 409 / 422) | the same refusal, no call; a new key is a new request |
| while another request holds it (fresh `in_progress`) | 409 `request_in_progress` |
| with a different fingerprint | 409 `idempotency_key_reused`, no call |
| after `unknown` or a stale `in_progress` | reconcile: read the live subscription; landed → record and return it; not landed → compare-and-swap the claim and make the call (if that call meets `state_changed`, read the live subscription again first: a late first attempt that landed meanwhile is recorded as succeeded, not refused) |
| for another organization | an independent request |

## 6. Frontend — `opsgrids-internal`

A **Change plan** card directly beneath Subscriptions, driven by `getOrganizationPricing` (which now carries
`pending_plan_version_id/name`, `cancel_at`, `entitled_products`, `plan_name`) and reloaded **without unmounting**
(`useLivePricing` keeps the data while it refetches), so the opening control survives and focus returns to it.
`FormDialog` (pick a plan — blocked ones listed disabled with the server's reason — and give a reason) →
`ConfirmDialog` (the server's preview in a sentence, stacked so *Back* keeps what was typed) → an `aria-live=polite`
result region in the card. The idempotency key is minted per preview; reused by *Try again* after an unknown
outcome; replaced after a refusal (which returns to the form with the server's sentence, its reference and the next
step, and reloads the live state). The ConfirmDialog latches a double click; the flow adds its own latch. Copy says,
before the confirm: nothing is charged before the next period for a paying subscription, access follows within about
15 minutes, and a scheduled change needs the billing run; an equal-price switch says "nothing is charged" only when
`usage_closed_now` is false, and otherwise that usage so far is invoiced at the current plan's rates. The plan names
in the form and the confirm come from the dialog's own fresh reads (plan-options, and the preview's `expected` pins
mapped through it), never the card's snapshot, which can be stale. A `not_offerable` refusal reloads the picker and
clears the choice. A 503 that says nothing was changed (`upstream_refused`, `catalog_unavailable`,
`preview_unavailable`) is never shown as "may or may not have been made". The reason's helper says it stays on the
staff audit trail. A role hint (owner and billing ops) and a state hint sit beside a disabled button. Dates are UTC
and say so. `ApiError` now exposes `code` and `requestId`.

## 7. Tests (what pins this)

* **Subscription** (`test_operator_plan_change.py`, plus reactivate/bundle updates): console-only on suspend,
  subscribe, change-plan and preview (no token, `tests`, `billing`, `catalog`); long notes do not 500; the note is the
  audit reason and never on the event; reserved words refused on every write incl. customer cancel; stable codes;
  state before `billing_catching_up`; `acting_as` on scheduled/withdrawn/superseded rows; `upgraded`/`downgraded` by
  direction and the legacy label; preview direction/timing/bundle diff/usage flag/`usage_closed_now` agreeing with
  the close the write inserts/refusals-as-data/read-only; another org's *unpublished* bespoke is the same 404;
  **cross-tenant** (org B's name on org A's subscription id is 404 on every route and writes nothing — also with the
  RLS net removed); the expected-state guard and its precedence.
* **Catalog** (`test_offerable_versions.py`): console-only; audience (another org's bespoke is never returned);
  drafts, retired plans and other products excluded; supersession per audience/cadence/currency.
* **Console** (`test_plan_change.py`): negative-authz on all three routes (product audience 404, no-otp 403,
  support 403, no role 403, 401, owner and billing_ops admitted) with `downstream == []` and no claim; the picker;
  the preview; the claim → call → record contract; the idempotency matrix (replay, different body, per-org scope,
  in-flight, **concurrent requests make one call**); every coded refusal surfaced, recorded and replayed; **only what
  is on offer** (a superseded or retired target refused in the preview and the confirm with no downstream call, a
  withdrawal to a superseded pin allowed, a recorded success still replayed, a catalogue outage claiming nothing);
  outcome unknown and its reconcile paths, including a late first attempt met as `state_changed`; a failed preview
  never `outcome_unknown`; the claim table's RLS and `WITH CHECK`.
* **Frontend**: copy table tests; the card's states; role and state hints; the form's states; preview → confirm →
  result; **double click sends once**; key lifetime; every failure; focus return; fixtures that obey the live rules.

## 8. Rollout

1. Verify `BILLING_RUN_ENABLED` and `BILLING_RUN_NOT_BEFORE` on the box (RUNBOOK, "Staff plan switching"). With the
   run off, a change scheduled for the next period never applies.
2. Deploy **catalog** and **subscription**, then **console** (runs the migration) and the **opsgrids-internal**
   frontend. The admin-gateway is not recreated.
3. `make openapi` regenerates `libs/contracts/openapi/console.json` (the other two routes are not in a schema).
4. Rollback: the console and frontend images. Everything else is additive; written rows stay.

## 9. Not in this release

Cancel; add product; extend trial; the Plans page; an end-of-period cancel; reopening after a cancel; gating owner
hand-activation; per-action operator step-up; withdrawing a scheduled change as an explicit control (it works as a
change back to the current plan and is previewed as `withdraw_scheduled`, but the picker does not offer it); an
`operator:<sub>` id on the older suspend/reactivate delegation.
