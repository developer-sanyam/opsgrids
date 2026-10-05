# Dashboard "Pay now" for platform invoices: design

Status: **implemented on branch, pending review** (`feat/dashboard-pay-now`, frontend only, not
deployed). Base: `main` @ `bbe6cf09`. The backend it calls is being built separately in
`feat/payments-phase-a` and was not available here. This page is coded against the **assumed
contract in §3**, behind one client module, and every test mocks that module.

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
                  │ 409 already paid             │ razorpay: handler fields ─► POST …/confirm (ignored result)
                  │ 404 unknown invoice          │ razorpay: dismissed      ─► one status read ("glance")
                  │ 422 phone_required ─► prompt │ cashfree: SDK result discarded
                  │ 422 other codes ─► message   │ script blocked/timeout   ─► "could not be opened"
```

- **Nothing the browser receives counts as proof of payment.** The Razorpay `handler` fields go
  only to `/confirm`, and the confirm answer is not used either. The Cashfree `checkout()` result
  is discarded unread, and so is a rejection from it. A redirect to `/billing/return` proves
  nothing. The only thing that moves the screen is `GET /checkouts/{id}`.
- **Poll** (`lib/checkout/poll.ts`):
  - Reads once immediately, then every 3 s, up to 20 reads (about 60 s).
  - Pauses on a hidden tab. Hidden time does not use up the budget, and the next read happens as
    soon as the tab is visible again.
  - Stops on `captured`, `failed` or `expired`. `authorized` keeps it going.
  - Stops on any 4xx (never retried) and on a response it cannot parse. A 5xx or a network error
    uses up one read and the poll continues.
  - Stops when the screen unmounts.
  - If the budget runs out: "Still waiting for confirmation — don't pay again", with a
    **Check again** button.
- **Razorpay dismissal** is used only as a hint about *how long* to wait:
  - After a dismissal the page makes one status read. If that read shows `authorized`, the full
    poll follows.
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
  - Each button's accessible name includes the invoice ("Pay now invoice INV-0042"), and the
    overdue link's name includes the product.
  - The row's own click and Enter/Space (which open the invoice drawer) are stopped at the
    button.
- **Phone width**: below 768 px the invoice table becomes cards, and Pay now sits in the card's
  action slot (tested).

## 3. Assumed API contract (reconcile at integration)

All shapes are read in **one function**, `fromWire(kind, raw)`, and all refusals in **one
function**, `checkoutRefusal(error)`. Both live in
`frontend/opsgrids-dashboard/src/api/payment.ts`. If the real service differs, the fix belongs in
those two functions.

| Call | Request | Response |
|---|---|---|
| `POST /api/v1/payment/checkouts` | `{invoice_id, customer?: {name, email, phone}}`; `customer` only after `phone_required`; `phone` is 10 digits, with any `+91`/`0` prefix removed | **201** `{charge_id, provider: "razorpay"\|"cashfree", client_action, amount_minor, currency, expires_at}` |
| | | `client_action` = `{type: "razorpay_checkout", key_id, order_id}` or `{type: "cashfree_checkout", payment_session_id, mode: "sandbox"\|"production"}` |
| | | **409** already paid · **404** unknown or another org's invoice · **403** permission · **422** coded refusal |
| `GET /api/v1/payment/checkouts/{charge_id}` | — | `{charge_id, state: "created"\|"authorized"\|"captured"\|"failed"\|"expired", provider, amount_minor, currency}` |
| `POST /api/v1/payment/checkouts/{charge_id}/confirm` (Razorpay only) | `{razorpay_order_id, razorpay_payment_id, razorpay_signature}` | `{state}`; **400** on a signature mismatch |

Assumptions the backend must match, or that must be changed in `payment.ts`:

1. **Coded 422s.** The code is read from `problem.errors[].code` first, then from the last segment
   of `problem.type` (hyphens and underscores are treated as the same). Known codes:
   `phone_required`, `currency_unsupported`, `amount_below_minimum`. Any other 422 shows the
   server's own `detail`. If the 422 has field errors on `customer.phone` or `phone` and the
   request included `customer`, the dialog reopens with that message on the field.
2. **The mapper is strict.** It throws `PaymentContractError`, and nothing is opened, if any of
   these hold:
   - `amount_minor` is not a non-negative safe integer;
   - `currency` is not three upper-case letters;
   - the state or provider is unknown;
   - the `client_action.type` does not match `provider`;
   - the Razorpay `key_id` does not match `^rzp_(test|live)_[A-Za-z0-9]+$`. A key **secret** has
     no such prefix, so a server misconfiguration cannot get one rendered into a page.
3. **Redirect return URL.** The server sets Cashfree's `order_meta.return_url` to
   `https://app.opsgrids.com/billing/return?charge_id=<charge uuid>`. The page accepts only a UUID
   there, and with anything else it makes no request.
4. **Re-opening an invoice** with an open checkout returns the same order or session (the
   `checkout:<invoice_id>[:n]` key in the Cashfree design). The UI sends no idempotency header.
5. **Status reads are org-scoped.** Another org's charge returns 404, and the return page says
   "We couldn't find this payment".

## 4. Which invoices are payable: a deviation from "unpaid invoices"

**Billing has no paid state.** `InvoiceOut.state` is `draft | finalized | void`
(`services/billing/app/models.py:146`). `InvoiceAdminOut`'s docstring says billing "never records
whether it was collected". No tenant endpoint reports whether an invoice has been paid.

The safe stand-in signal is subscription's dunning (`ui/billing/payable.ts`). An invoice gets
Pay now when all of these hold:

- it is `finalized`;
- `total_minor > 0`;
- its `subscription_id` belongs to a subscription with an open dunning cycle (`dunning !== null`);
- this screen has not already seen it paid (captured, or answered with 409).

If the subscriptions read fails, no Pay now is shown, and the invoice list itself is unaffected.

Under phase A's `no_mandate` fast-fail, every platform charge without a mandate opens dunning, so
every unpaid invoice is covered. The server's 409 still guards any invoice in a dunning
subscription that is already paid.

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
  - `routes/Invoices.test.tsx`: gating, 409/404/422, phone prompt, double click, live region,
    phone width;
  - `routes/BillingReturn.test.tsx`;
  - the overdue cases in `routes/Subscriptions.test.tsx`.
