# Dashboard "Pay now" for platform invoices: design

Status: **implemented on branch, pending review** (`feat/dashboard-pay-now`, frontend only, not
deployed). Base: `main` @ `bbe6cf09`. The backend it calls is built separately in
`feat/payments-phase-a`. The first cut of this page was coded against an assumed contract. The
review round (§8) **reconciled §3 with that branch's real routes** (`services/payment/app/api/
checkout.py`, `checkout/services.py`, `schemas.py` @ `016b4683`), and the refusal tests now use
the problem bodies the service actually emits.

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
- **Phone prompt**: shown when the server answers `phone_required` (Cashfree needs a phone number
  for its PG orders). The page then retries with `customer: {name, email, phone}`. The name and
  email come from the signed-in user, and the dialog tells the user they will be sent.
- **Gate**: the controls appear only for `core.product.activation.write` at ORG scope, the same
  permission the subscribe and change-plan actions check (`Subscriptions.tsx`,
  `services/subscription/app/api/routes.py:44`). The server enforces it on every call.

## 2. Flow

```
click Pay now ─► POST /payment/checkouts ─► launcher(client_action) ─► poll GET /checkouts/{id}
                  │ 409 coded already-paid       │ openable: false          ─► "not available", nothing launched
                  │ 409 other ─► server's words  │ razorpay: handler fields ─► POST …/confirm (ignored result)
                  │ 404 unknown invoice          │ razorpay: dismissed      ─► one delayed status read ("glance")
                  │ 422 phone ─► prompt          │ cashfree: closed/error   ─► one delayed status read ("glance")
                  │ 422 other codes ─► message   │ cashfree: paymentDetails ─► full wait
                  │                              │ script blocked/timeout   ─► "could not be opened"
```

- **Nothing the browser receives counts as proof of payment.** The Razorpay `handler` fields go
  only to `/confirm`, and the confirm answer is not used either. A redirect to `/billing/return`
  proves nothing. The only thing that moves the screen to "paid" is `GET /checkouts/{id}`.
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

## 3. API contract (reconciled with `feat/payments-phase-a`)

All shapes are read in **one function**, `fromWire(kind, raw)`, and all refusals in **one
function**, `checkoutRefusal(error)`. Both live in
`frontend/opsgrids-dashboard/src/api/payment.ts`. If the service changes, the fix belongs in
those two functions.

| Call | Request | Response |
|---|---|---|
| `POST /api/v1/payment/checkouts` | `{invoice_id, customer?: {name, email, phone}}`; `customer` only after a phone refusal; `phone` is 10 digits, with any `+91`/`0` prefix removed | **201** `PlatformCheckoutOut` `{charge_id, provider: "razorpay"\|"cashfree", client_action, amount_minor, currency, expires_at}` |
| | | `client_action` = `{type: "razorpay_checkout", key_id, order_id, openable}` or `{type: "cashfree_checkout", payment_session_id, mode: "sandbox"\|"production", openable}`. `openable: false` means a stub provider, and nothing is launched. |
| | | **409** `conflict` (several meanings, below) · **404** unknown or another org's invoice · **403** permission · **422** `validation` refusal |
| `GET /api/v1/payment/checkouts/{charge_id}` | — | `PlatformCheckoutStatusOut` `{charge_id, state: "created"\|"authorized"\|"captured"\|"failed", failure_reason, provider, amount_minor, currency, …}`. `failed` with `failure_reason: "checkout_expired"` is mapped to the page's `expired`. |
| `POST /api/v1/payment/checkouts/{charge_id}/confirm` (Razorpay only) | `{razorpay_order_id, razorpay_payment_id, razorpay_signature}` | `PlatformCheckoutStatusOut`; **400** on a signature mismatch |

How the refusals are read:

1. **409 is not "paid".** The route answers `ConflictProblem` (type `conflict`, no `errors`) for:
   - already paid;
   - a checkout another click is preparing (the lease is held);
   - an expired checkout that could not be closed yet;
   - a provider without checkout.

   The page reads a 409 as paid only when it carries the code `invoice_already_paid`, in
   `errors[].code` or as the type slug `invoice-already-paid`. Every other 409 shows the server's
   own sentence, and the invoice stays payable. The service does not send that code yet (§6, 5),
   so until it does, an already-paid invoice shows "This invoice has already been paid" as a
   warning and keeps its button. The server refuses the button's next click the same way.
2. **422 codes.** Codes are read from `problem.errors[].code` first, then from the `type` slug
   (hyphens and underscores are treated as the same). Both this page's names and the service's
   own `_REQUEST_CODES` names are accepted:
   - `phone_required`, `customer_phone_required` and `customer_details_required` open the phone
     prompt;
   - `currency_unsupported` and `unsupported_currency`;
   - `amount_below_minimum`.

   **Stopgap:** the service raises these as a bare `ValidationProblem(message)` with no code
   (§6, 5). So an uncoded `validation` 422 whose `detail` is exactly one of the four
   `_REQUEST_CODES` sentences maps to its code. A reworded sentence falls through to the server's
   own words and never to a wrong prompt. Any other 422 shows the server's `detail`. If the 422
   has field errors on `customer.phone` or `phone` and the request included `customer`, the
   dialog shows that message on the field.
3. **The mapper is strict.** It throws `PaymentContractError`, and nothing is opened, if any of
   these hold:
   - `amount_minor` is not a non-negative safe integer;
   - `currency` is not three upper-case letters;
   - the state or provider is unknown;
   - `client_action.openable` is missing or not a boolean;
   - the `client_action.type` does not match `provider`;
   - the Razorpay `key_id` does not match `^rzp_(test|live)_[A-Za-z0-9]+$`. A key **secret** has
     no such prefix, so a server misconfiguration cannot get one rendered into a page.
4. **Redirect return URL.** The server sets Cashfree's `order_meta.return_url` to
   `{app_base_url}/billing/return?charge_id=<charge uuid>` (`_return_url`). The page accepts only
   a UUID there, and with anything else it makes no request. Arriving proves nothing; a cancelled
   checkout lands here too.
5. **Re-opening an invoice** with an open checkout returns the same order or session (the
   `checkout:<invoice_id>[:n]` key). The UI sends no idempotency header.
6. **Status reads are org-scoped.** Another org's charge returns 404, and the return page says
   "We couldn't find this payment".

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
- drop it if this screen has already seen it paid (captured, or a coded already-paid 409). This
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
4. **Gateway**: `/api/v1/payment` is already routed, authenticated and with no product key, in both
   `services/app-gateway/routes.json:101` and `infrastructure/edge/routes.prod.json:83`. So
   `/api/v1/payment/checkouts` needs no route change.
5. **Machine codes are owed by payment-service (follow-up for `feat/payments-phase-a`).** Its
   checkout refusals carry no code: every 409 is a bare `conflict`, and every 422 is a bare
   `validation` whose only distinguishing mark is its sentence. This branch does not touch the
   service, because that branch is in flight. Two asks:
   - (a) Raise the already-paid refusal (`claim_checkout`, "This invoice has already been paid")
     with `errors=[{"field": "invoice_id", "code": "invoice_already_paid", "message": …}]`, or
     as a dedicated problem type `invoice-already-paid`. The page then marks the invoice paid and
     drops its button.
   - (b) Raise each `_REQUEST_CODES` refusal with its key as `errors[].code`, for example
     `{"field": "customer.phone", "code": "customer_phone_required", …}`. The page already
     accepts those names, and once they arrive the sentence table (`UNCODED_REFUSALS` in
     `payment.ts`) can be deleted.

   Until then, the page matches those four sentences whole. This goes against the client
   library's "never the detail" rule (`opsgrids-client/src/problem.ts`). It is fail-safe: a
   reworded sentence shows the server's own words. A 409 is never matched by its sentence,
   because a wrong "paid" stops a customer paying an overdue invoice.
6. **429/408 during the status poll** end the wait as *unconfirmed* rather than being retried.
   That keeps frontend/CLAUDE.md's "never retry a 4xx" and still keeps the "don't pay again"
   warning up.

## 7. Files

- `src/api/payment.ts`: the calls, `fromWire`, and `checkoutRefusal`.
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
  - `api/payment.test.ts`: mapping and refusals;
  - `lib/checkout/launch.test.ts`: both launchers against fake SDKs, dismiss, failure, blocked
    script, confirm path;
  - `lib/checkout/poll.test.ts`: stop conditions, hidden tab, 4xx/5xx, abort;
  - `routes/Invoices.test.tsx`: gating, one invoice per cycle, the service's real 409 and 422
    bodies, phone prompt (kept open, number kept), double click, closed window (delayed glance,
    row Check status, Pay again), glance to authorized to captured, run-out then Check again,
    stub checkout, focus, live region, phone width;
  - `routes/BillingReturn.test.tsx`;
  - the overdue cases in `routes/Subscriptions.test.tsx`.

## 8. Review round (2026-10-06)

Confirmed findings, each fixed at its cause with a regression test that fails on the first cut:

| Finding | Fix |
|---|---|
| Every 409 read as "already paid": marked paid, button removed | Only a coded `invoice_already_paid` is paid; other 409s show the server's words (§3.1) |
| Phone prompt never opened against the real service (Cashfree dead end) | Service code names accepted, plus whole-sentence stopgap (§3.2, §6.5) |
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
