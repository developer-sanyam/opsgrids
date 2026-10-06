# Dashboard "Pay now" for platform invoices: design

Status: **implemented on branch, pending review** (`feat/dashboard-pay-now`, frontend only, not
deployed). The backend it calls is built separately in `feat/payments-phase-a`. The first cut of
this page was coded against an assumed contract; the review round (§8) reconciled it with an
early cut of that branch. The **contract round (§9)** matched it to the finished API @
`7853e654`: `services/payment/app/api/checkout.py`, `app/schemas.py`, `app/checkout/services.py`
and `libs/contracts/openapi/payment.json`. §3 is that contract, and every test fixture is built
from it (`src/test-utils/payment.ts`).

Sources: the Razorpay design critique (§2: "New endpoints" and the dashboard pay button) and the
Cashfree design critique (§2.10 customer side, §2.4(b) one-time checkout).

## 1. What it does

- **Invoices** (`/invoices`) has a **Pay now** button on each invoice that is still payable (rule
  in §4). It opens a checkout on the server, then the provider's own payment window. After that it
  polls the server, and says "Payment received" only when the server confirms it.
- **Subscriptions** (`/subscriptions`): the overdue line ("Payment overdue — access ends …") gets a
  **Pay now** link to Invoices. See §6, deviation 2, for why it is a link and not a button.
- **`/billing/return?charge_id=<uuid>`**: the landing page for redirect flows. It polls the same
  status read and reports the result. Only the charge id is taken from the URL. Any other query
  parameters (for example a provider's `order_status=PAID`) are ignored.
- **Details prompt**: shown when the server answers `phone_required` or
  `customer_details_required` (Cashfree needs the paying customer, with a phone, on every order).
  The page then retries with `customer: {name, email, phone}`. The name and email come from the
  signed-in user, and the dialog tells the user they will be sent.
- **Gate**: the controls appear only for `core.product.activation.write` at ORG scope, the same
  permission the subscribe and change-plan actions check (`Subscriptions.tsx`,
  `services/subscription/app/api/routes.py:44`). The server enforces it on every call.

## 2. Flow

```
click Pay now ─► POST /payment/checkouts ─► launcher(client_action) ─► poll GET /checkouts/{id}
                  │ 409 invoice_already_paid ─► paid  │ openable: false          ─► "not available", nothing launched
                  │ 409 busy codes ─► wait, retry     │ razorpay: handler fields ─► POST …/confirm (ignored result)
                  │ 409 checkout_not_supported        │ razorpay: dismissed      ─► one delayed status read ("glance")
                  │ 404 unknown invoice               │ cashfree: closed/error   ─► one delayed status read ("glance")
                  │ 422 phone/details ─► prompt       │ cashfree: paymentDetails ─► full wait
                  │ 422 unpayable codes ─► message    │ script blocked/timeout   ─► "could not be opened"
```

- **Nothing the browser receives counts as proof of payment.** The Razorpay `handler` fields go
  only to `/confirm`, and the confirm answer is not used either. A redirect to `/billing/return`
  proves nothing. The only things that move the screen to "paid" are `paid: true` from
  `GET /checkouts/{id}` and a 409 `invoice_already_paid`.
- **The providers' own reports set only how long to wait.** Razorpay's `ondismiss` and Cashfree's
  `checkout()` result (`error`, `paymentDetails`, `redirect`) are the same kind of hint. A closed
  window gets one status read, one interval later. A finished one gets the full wait.
- **"Don't pay again" only when a payment may exist**, meaning the window reported one finishing
  or the server said `authorized`. Otherwise the wait shows a neutral "Checking the payment
  status…". A wait that runs out says "No payment has been confirmed yet" and offers **Pay
  again**. While its last checkout is closed or unconfirmed, the invoice's own row offers **Check
  status** instead of Pay now.
- **Poll** (`lib/checkout/poll.ts`):
  - Reads once immediately, then every 3 s, up to 20 reads (about 60 s).
  - Pauses on a hidden tab. Hidden time does not use up the budget, and the next read happens as
    soon as the tab is visible again.
  - Stops on `captured`, `failed` or `expired`. `authorized` keeps it going.
  - Stops on any 4xx (never retried) and on a response it cannot parse. A 429 or 408 ends the
    wait as *unconfirmed*, not refused, so the warning stays up. A 5xx or a network error uses up
    one read and the poll continues.
  - A failure after the window says "We couldn't confirm this payment", never the create-time
    "nothing was charged".
  - Stops when the screen unmounts.
  - If the budget runs out after a payment was seen: "Still waiting for confirmation — don't pay
    again", with a **Check again** button. If no payment was seen, it says "No payment has been
    confirmed yet" and offers **Check again** and **Pay again**.
- **A dismissal (Razorpay `ondismiss`, Cashfree `error`)** is used only as a hint about *how
  long* to wait:
  - After a dismissal the page makes one status read, one interval (3 s) later, so a UPI
    approval finished out of sight has a moment to land. If that read shows `authorized`, the
    full poll follows.
  - Otherwise the page says the window closed before payment finished, warns that a UPI approval
    can take a minute, and offers **Check status**.
  - If a `payment.failed` event fired before the dismissal, the page says the payment did not go
    through.
- **One payment at a time.**
  - A ref guard means a double click opens one checkout and one window.
  - Every Pay now button is disabled while a payment is being opened or waited on.
- **Accessibility.**
  - One polite live region (`role="status"`, `aria-live="polite"`) exists empty before anything
    happens. Every state change is announced through it, including "Payment pending — don't pay
    again".
  - When a state lands and the control that had focus is disabled or gone, focus moves to the
    notice (`ui/billing/focusIfLost.ts`), which also scrolls it into view on a phone. A
    re-check's button stays mounted (busy) while it runs. The phone dialog stays open while its
    number is tried.
  - Each button's accessible name includes the invoice ("Pay now invoice INV-0042"), and the
    overdue link's name includes the product.
  - The row's own click and Enter/Space (which open the invoice drawer) are stopped at the
    button.
- **Phone width**: below 768 px the invoice table becomes cards, and Pay now sits in the card's
  action slot (tested).

## 3. API contract (payment-service @ `7853e654`)

The source of truth is `libs/contracts/openapi/payment.json` and the route module
`services/payment/app/api/checkout.py`. The dashboard reads the wire in one module,
`frontend/opsgrids-dashboard/src/api/payment.ts`: the request bodies (`customerIn`), every answer
(`fromWire(kind, raw)`) and every refusal (`checkoutRefusal(error)`). If the service changes, the
fix belongs there. The test fixtures in `src/test-utils/payment.ts` are the schemas' own bodies
and the service's own problem documents, and every mapped value a test uses goes through
`fromWire`.

All three routes need `core.product.activation.write` at ORG. Another organization's charge or
invoice is a **404**, never a disclosure.

### 3.1 `POST /api/v1/payment/checkouts` → 201 `PlatformCheckoutOut`

Request `PlatformCheckoutIn` (extra keys are refused):

| Field | Type | Sent |
|---|---|---|
| `invoice_id` | uuid, required | always |
| `customer` | `CheckoutCustomerIn \| null`, optional | only after `phone_required` / `customer_details_required` |
| `customer.name` | `string \| null`, max 100 | the signed-in user's name; blank or over-long → `null` |
| `customer.email` | `string \| null`, max 254 | the signed-in user's email; blank or over-long → `null` |
| `customer.phone` | `string \| null`, max 16, `^(\d{10}\|\+\d{8,15})$` | 10 digits, any `+91`/`0` prefix removed |

Response `PlatformCheckoutOut` — every key required, none nullable:

| Field | Type | Page |
|---|---|---|
| `charge_id` | uuid | `chargeId` |
| `provider` | string (`razorpay` \| `cashfree`) | `provider`; anything else → `PaymentContractError` |
| `client_action` | object, tagged by `type` (below) | `action` + `openable` |
| `amount_minor` | integer | `amountMinor` (non-negative safe integer) |
| `currency` | string | `currency` (three upper-case letters) |
| `expires_at` | date-time, **not null** | `expiresAt` |

`client_action`:

- `{type: "razorpay_checkout", key_id, order_id, openable}` — the **public** key id and the Order
  id. When openable, `key_id` must match `^rzp_(test|live)_[A-Za-z0-9]+$`, so a key secret can
  never be rendered into a page.
- `{type: "cashfree_checkout", payment_session_id, mode: "sandbox"|"production", openable}`.
- `openable: false` is a stub provider (Razorpay's stub sends `key_id: ""`; Cashfree's stub a
  stand-in session in `sandbox` mode). The page maps it to `{openable: false, action: null}`,
  launches nothing, and says "Online payment isn't available right now". Its ids are not read.
- The `type` must be its `provider`'s, openable or not.

### 3.2 `GET /api/v1/payment/checkouts/{charge_id}` → 200 `PlatformCheckoutStatusOut`

The read the page polls; while the checkout is open (or inside its late-capture window after
closing as `checkout_expired`) the server asks the provider first.

| Field | Type | Page |
|---|---|---|
| `charge_id` | uuid, required | `chargeId` |
| `provider` | string, required | `provider` (read, not branched on: a status needs no launcher) |
| `state` | `created \| authorized \| captured \| failed`, required | `state`; `failed` + `failure_reason: "checkout_expired"` → `expired` |
| `paid` | boolean, required | `paid`; must equal `state == captured`, else `PaymentContractError` |
| `amount_minor`, `currency` | integer, string, required | as above |
| `expires_at` | `date-time \| null`, **required** | `expiresAt` (a missing key is a contract break, `null` is not) |
| `failure_reason` | `string \| null`, default `null` | `failureReason` |
| `provider_checked` | boolean, default `true` | `providerChecked` (false: the provider could not be asked this time) |

`paid: true` is what marks the invoice paid on screen. The poll stops on `captured`, `failed` or
`expired`.

### 3.3 `POST /api/v1/payment/checkouts/{charge_id}/confirm` → 200 `PlatformCheckoutStatusOut`

Razorpay only. Request `CheckoutConfirmIn` `{razorpay_payment_id, razorpay_order_id,
razorpay_signature}` (each `string | null` on the schema; the page sends the three strings
Razorpay's `handler` gave it, and does not call confirm when any is missing). The answer is
mapped like §3.2 and then ignored: a 400 (`invalid-checkout-confirmation`), a 409 (no browser
confirmation for this provider), a 503 or any other failure proves nothing either way, and the
status poll decides.

### 3.4 Refusals: `errors[0].code`

Problem documents are `platform_core.errors` RFC 7807 bodies. A coded refusal carries
`errors[0] = {field?, code, message}`; the busy 409s carry **no `field`**, so the code is read
from `errors[0]` directly, not through the client library's `fieldErrors` (which drops entries
without one). A code is honoured only at the status the contract gives it.

| Status | `errors[0].code` | `field` | Page |
|---|---|---|---|
| 409 | `invoice_already_paid` | `invoice_id` | **"This invoice is already paid."** — the only refusal that marks the invoice paid and drops its button |
| 409 | `checkout_in_progress` | — | "This payment is already being prepared." — wait a moment, try again |
| 409 | `previous_checkout_not_closable` | — | "The last payment window for this invoice is still being closed." — try again in a minute; if paid there, don't pay again |
| 409 | `previous_checkout_payment_in_flight` | — | "An earlier payment for this invoice is still being processed." — wait a few minutes before trying again |
| 409 | `checkout_not_supported` | — | "Online payment isn't available right now." |
| 422 | `phone_required` | `customer.phone` | opens the details prompt; after a prompt, its `message` under the field |
| 422 | `customer_details_required` | `customer` | opens the details prompt, the same way |
| 422 | `currency_unsupported` | `invoice_id` | "Online payment isn't available for this invoice's currency yet." |
| 422 | `amount_below_minimum` | `invoice_id` | "This amount is below the payment provider's minimum." |
| 422 | `nothing_to_pay` | `invoice_id` | "There's nothing to pay on this invoice." |

The busy and unpayable refusals keep the invoice's Pay now button and never mark it paid; the
server decides the next click. Everything else:

- **409 with no known code** (for example confirm's): the server's own `detail`, as a warning.
  Never "paid", whatever the sentence says.
- **422 with no refusal code** is the framework's own validation (`detail` "Request payload failed
  validation", `errors[]` with `field`, pydantic `code`, `message`). A message on
  `customer.phone` after a prompt goes under the field; otherwise the `detail` is shown.
- **404** "We couldn't find this invoice" (or, on the return page, "this payment"); **403** "You
  don't have permission to pay invoices"; anything else (503 `upstream-unavailable`, a dropped
  connection) shows the server's `detail` or a generic sentence.

### 3.5 Other contract points

1. **Redirect return URL.** The server sets Cashfree's `order_meta.return_url` to
   `{APP_BASE_URL}/billing/return?charge_id=<charge uuid>` (`_return_url`); production's
   `APP_BASE_URL` is `https://app.opsgrids.com`, the dashboard. The page accepts only a UUID
   there, and with anything else it makes no request. Arriving proves nothing; a cancelled
   checkout lands here too.
2. **Re-opening an invoice** with an open checkout returns that checkout, at the provider it was
   created under. The UI sends no idempotency header.
3. **Gateway.** `/api/v1/payment` is routed, authenticated and with no product key, in both
   `services/app-gateway/routes.json` and `infrastructure/edge/routes.prod.json` on `main`, and
   is not in any public-path list, so `/api/v1/payment/checkouts…` needs no route change.

## 4. Which invoices are payable: a deviation from "unpaid invoices"

**Billing has no paid state.** `InvoiceOut.state` is `draft | finalized | void`
(`services/billing/app/models.py:146`). `InvoiceAdminOut`'s docstring says billing "never records
whether it was collected". No tenant endpoint reports whether an invoice has been paid.

The safe stand-in signal is subscription's dunning (`ui/billing/payable.ts`). Each subscription
with an open dunning cycle offers Pay now on **one** invoice, picked like this:

- candidates are invoices of that subscription that are `finalized`, have `total_minor > 0`, and
  were issued (`finalized_at`) no later than the cycle opened (`dunning.opened_at`);
- of those, prefer the ones whose total and currency equal the cycle's `amount_minor` and
  `currency`, when the cycle has them;
- of what remains, take the latest issued;
- drop it if this screen has already seen it paid (`paid: true`, or a 409 `invoice_already_paid`). This
  happens after the pick, so hiding it never promotes an older, settled invoice.

If nothing qualifies, the subscription offers nothing rather than a guess. Every past invoice of a
subscription stays `finalized`, which is why the pick matters. Before the review round, each of
them carried a button. If the subscriptions read fails, no Pay now is shown, and the invoice list
itself is unaffected.

## 5. Security and data

- No keys or secrets in the frontend. The only provider identifier the browser receives is the
  public Razorpay `key_id`, which comes from the API inside a checkout and is shape-checked.
- The SDKs load from their own origins, `https://checkout.razorpay.com/v1/checkout.js` and
  `https://sdk.cashfree.com/js/v3/cashfree.js`. A script that errors, does not define its global,
  or does not load within 15 s is reported as blocked, and its element is removed so a retry
  starts fresh.
- **Nothing is persisted**: no `localStorage`, `sessionStorage` or URL state. The checkout lives in
  component state and is aborted on unmount.
- Money is always integer minor units from the server, formatted by `formatMoney`, which uses the
  currency's own exponent through `Intl`. The figure shown with a checkout is the server's
  `amount_minor`, not the invoice total the page already had.
- `app.opsgrids.com` sends no CSP today (`infrastructure/edge/caddy/Caddyfile` sets one only on the
  internal console). If one is added, it must allow:
  - `script-src`: `checkout.razorpay.com` and `sdk.cashfree.com`;
  - `frame-src`: `api.razorpay.com` and Cashfree's checkout origins.

## 6. Deviations and follow-ups

1. **"Unpaid" means "issued, positive, and its subscription is in dunning".** See §4. Follow-up for
   the payments or billing track: a tenant read of each invoice's payment state would replace this
   rule. The change would be in `payable.ts` only.
2. **The overdue banner links to Invoices** instead of opening a checkout itself. `DunningOut`
   (`services/subscription/app/schemas.py:221`) does not expose the cycle's `invoice_id`, although
   the `dunning_cycles` row has it (`models.py:334`). Follow-up for the subscription track: add
   `invoice_id` to `DunningOut`. `PayOverdueLink` then becomes a button that calls the same
   `usePayInvoice().start`.
3. **ADR-0004 §6**: the provider's SDK is provider-specific by nature. `lib/checkout/launch.ts` is
   the one place the dashboard branches on `client_action.type`. The ADR note belongs to the
   payments track (the Cashfree critique, §2.10). No ADR is written here.
4. **Gateway**: verified on `main` @ `d284d5d9` — `/api/v1/payment` is routed, authenticated
   and with no product key, in both `services/app-gateway/routes.json` (→ `localhost:8163`) and
   `infrastructure/edge/routes.prod.json` (→ `payment:8000`), and no public-path list in the
   gateway's `app/main.py` covers it. So `/api/v1/payment/checkouts…` needs no route change.
5. **Machine codes: delivered.** The review round asked payment-service for coded refusals; the
   finished API sends `errors[0].code` on every checkout refusal (§3.4). The sentence-matching
   stopgap, the type-slug fallback and the aliases (`customer_phone_required`,
   `unsupported_currency`, `already_paid`) are deleted: the page now branches on codes only, as
   the client library's "never the detail" rule asks.
6. **429/408 during the status poll** end the wait as *unconfirmed* rather than being retried.
   That keeps frontend/CLAUDE.md's "never retry a 4xx" and still keeps the "don't pay again"
   warning up.

## 7. Files

- `src/api/payment.ts`: the calls, the request bodies, `fromWire`, and `checkoutRefusal`.
- `src/test-utils/payment.ts`: the contract's bodies and problem documents, for every test.
- `src/lib/checkout/`:
  - `script.ts`: loader;
  - `razorpay.ts` and `cashfree.ts`: launchers;
  - `launch.ts`: dispatch and confirm;
  - `poll.ts`;
  - `phone.ts`.
- `src/ui/billing/`:
  - `usePayInvoice.ts`: the flow state machine;
  - `PaymentPanel.tsx`, `PaymentNotice.tsx` and `paymentCopy.ts`;
  - `PhoneDialog.tsx`;
  - `payable.ts`;
  - `PayOverdueLink.tsx`.
- `src/routes/BillingReturn.tsx` (lazy route); `Invoices.tsx` and `Subscriptions.tsx` (additive
  edits).
- Tests:
  - `api/payment.test.ts`: mapping, request bodies and URLs, and every refusal code;
  - `lib/checkout/launch.test.ts`: both launchers against fake SDKs, dismiss, failure, blocked
    script, confirm path;
  - `lib/checkout/poll.test.ts`: stop conditions, hidden tab, 4xx/5xx, abort;
  - `routes/Invoices.test.tsx`: gating, one invoice per cycle, every coded 409 and 422 and its
    screen, details prompt (kept open, number kept), double click, closed window (delayed glance,
    row Check status, Pay again), glance to authorized to captured, run-out then Check again,
    stub checkout, focus, live region, phone width;
  - `routes/BillingReturn.test.tsx`;
  - the overdue cases in `routes/Subscriptions.test.tsx`.

## 8. Review round (2026-10-06)

Confirmed findings, each fixed at its cause with a regression test that fails on the first cut:

| Finding | Fix |
|---|---|
| Every 409 read as "already paid": marked paid, button removed | Only a coded `invoice_already_paid` is paid; other 409s show the server's words (§3.4) |
| Phone prompt never opened against the real service (Cashfree dead end) | Service code names accepted, plus a whole-sentence stopgap — both retired in §9 once the codes shipped |
| Closing Cashfree held "don't pay again" for a minute and blocked Pay now | Cashfree's result read as a wait hint, like `ondismiss` (§2) |
| Pay now on every past invoice of a subscription in dunning | One invoice per cycle (§4) |
| Focus lost through the flow | `focusIfLost` on landing; busy buttons stay mounted; dialog stays open |
| "Don't pay again" off-screen while the row's Pay now is live again | Row shows Check status; focus scrolls the notice into view |
| `client_action.openable` ignored | Read; nothing launched on a stub (§3) |

Low findings fixed along the way:

- the glance is delayed one interval;
- the glance's "checking" copy;
- 429/408 handling;
- post-window refusal copy;
- `failed` + `checkout_expired` read as `expired`;
- the phone dialog keeps its number and focuses the field;
- "Finish paying in the payment window" waits for the window;
- the overdue link also needs `core.billing.invoice.read`;
- tests for glance to authorized, and for run-out then Check again.

## 9. Contract round (2026-10-06)

The finished payment API (`feat/payments-phase-a` @ `7853e654`) against the page, field by field.
Each mismatch is fixed in `src/api/payment.ts` and pinned by a test built from the contract's own
bodies (`src/test-utils/payment.ts`):

| Mismatch | Fix |
|---|---|
| A stub Razorpay checkout (`key_id: ""`, `openable: false`) failed the key-id check, so the page said "unexpected checkout" instead of "not available" | A non-openable `client_action` maps to `{openable: false, action: null}` and its ids are not read; `launchCheckout` only takes an `OpenableCheckout` |
| `PlatformCheckoutOut.expires_at` is required and never null; the page accepted it missing | Required non-empty string |
| `PlatformCheckoutStatusOut.paid`, `expires_at`, `failure_reason`, `provider_checked` were not mapped; `provider` (a plain string) was refused when unknown | All mapped with the schema's nullability and defaults; `paid` must agree with `state`, and is what marks an invoice paid; an unknown provider on a status read is kept |
| Confirm's answer was read as a bare `{state}` | Mapped as the full `PlatformCheckoutStatusOut` |
| `customer.name` / `customer.email` were sent as `""` when unknown | `null`, as `CheckoutCustomerIn` allows; blank or past the schema's limits (100 / 254) also `null` |
| Refusals were read from any `errors[].code` through `fieldErrors`, which drops the busy 409s (no `field`), then from the type slug and four sentences | `errors[0].code`, read directly, at its contract status only; slug, sentence and alias fallbacks deleted |
| `checkout_in_progress`, `previous_checkout_not_closable`, `previous_checkout_payment_in_flight` showed the server's sentence; `checkout_not_supported` and `nothing_to_pay` had no screen of their own | Each code has its own copy (§3.4): the busy ones say wait and retry, and only `invoice_already_paid` marks the invoice paid |
