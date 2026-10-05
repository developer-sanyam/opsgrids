# Merchant connect: Cashfree — design (as built)

**Date:** 2026-10-06 · **Branch:** `feat/merchant-cashfree`, stacked on `feat/payments-phase-a` ·
**Decision:** [ADR-0030](../../adr/0030-merchant-connect-collections-seam.md) ·
**Runbook:** `infrastructure/edge/RUNBOOK.md` → "Merchant connect: Cashfree for tenant online payments"

This is the rollout's P1–P3 (the P0 fixes — provider and mode immutable per account row, the
`_account_for` provider assertion, the per-provider collections wire with the legacy `razorpay` value,
audit redaction of secret names, provider-scoped unique indexes — landed on the base branch and are reused,
not repeated). Every Cashfree fact here comes from Cashfree's public documentation; nothing was called. The
list a live sandbox must settle is at the end.

## Scope (owner decisions)

- **v1 is staff-opened checkout inside Operations only.** No payment links, no storefront buyer checkout.
- **One live account per organization**, its provider chosen in the connect form. Switching is disconnect, then
  connect. Disconnect is refused (409, with the count) while a payment taken into the account is open.
- **Connecting, replacing, checking and disconnecting need a one-time-code session** (`amr ∋ otp`), with a
  sentence in the UI that says how to get one.
- **Cashfree starts Sandbox-only**; Production stays behind `COLLECTIONS_ALLOW_LIVE_KEYS`.
- **The customer phone** Cashfree requires comes from the order, server to server, through an optional payer
  block — never persisted, logged, audited or published, never invented (missing → 422 `phone-required`).
- **`payment_session_id` is never stored**: fetched from Cashfree for each (re)open.

## The seam

`CollectionsProvider` (`services/payment/app/providers/base.py`) is standalone — not a `PaymentProvider`:

| Method | Razorpay | Cashfree |
|---|---|---|
| `ensure_order(receipt, amount, currency, notes, payer?, expires_at?, notify_url?)` | `GET /orders?receipt=` then `POST /orders`; payer/expiry/notify ignored | `GET /pg/orders/{receipt}` then `POST /pg/orders` with `order_id = receipt` (the collection id), the payer (`customer_id = c<charge hex>`, phone), `order_expiry_time`, `order_meta.notify_url` when the platform base is HTTPS; `409` → GET; found Order compared exactly; `checkout_token = payment_session_id` |
| `find_order(receipt)` | lookup only | `GET /pg/orders/{receipt}` |
| `order_payments(order_id)` | `GET /orders/{id}/payments` | `GET /pg/orders/{id}/payments`: `SUCCESS`→captured, `FAILED`→failed, `PENDING`→created (no observation), `USER_DROPPED`/`NOT_ATTEMPTED`/`VOID`/`CANCELLED` dropped |
| `refund(key, payment_id, amount, currency, provider_order_id?)` | `POST /payments/{id}/refund` + `X-Refund-Idempotency` (`rfd-` + 40 hex, unchanged) | `POST /pg/orders/{order}/refunds` with `refund_id = rfd + 36 hex` (39 alphanumerics), `refund_speed: STANDARD`, `x-idempotency-key`; `409` → `GET …/refunds/{refund_id}`, refused if the amount differs |
| `verify_webhook(headers, body)` | HMAC-SHA256 hex of the body (`x-razorpay-signature`) under the webhook secret | `base64(HMAC-SHA256(timestamp + body))` (`x-webhook-signature`, `x-webhook-timestamp`) under the Secret Key; constant time, fail closed, non-ASCII → False |
| `parse_webhook` / `webhook_facts` | `payment.authorized|captured|failed`, `order.paid`→captured; `refund.*` | `PAYMENT_SUCCESS`→captured, `PAYMENT_FAILED`→failed (bound to its own `cf_payment_id`, stable `error_code` as the reason); `REFUND_STATUS` keyed by our `refund_id`; `USER_DROPPED`, auto-refunds, settlements → recorded `ignored`; amount from the Order when the payment covers it |
| `webhook_delivery_id(headers)` | `x-razorpay-event-id` | `x-idempotency-header` (or `x-idempotency-key`) |
| `probe()` | `GET /orders?count=1` (no request on the stub) | `GET /pg/orders/probe<random>`: 404 = authenticated, 401/403 = `ProviderMisconfigured` |

Stored inbox bodies are allow-lists (`razorpay.redact_webhook_body`, `cashfree_codec.redact_webhook_body` —
the latter now also keeps a refund entity's ids/amount/status and a failed payment's stable `error_code`), and
`parse_webhook` reads them back to the same identity. Amounts cross the wire only through the codec (`Decimal`,
never a float).

## Data

Migration `d9e0f1a2b3c4` (payment, after `c7d8e9f0a1b2`): the two-secret CHECK becomes
`key_secret_present_iff_active` + `razorpay_active_has_webhook_secret` + `cashfree_has_no_webhook_secret` +
`provider in ('razorpay','cashfree')`; `verified_at`, `last_error_code` added. Downgrade refuses while a Cashfree
row exists. The account's `mode` keeps the `test`/`live` vocabulary; Cashfree Sandbox/Production map onto it.

## API

Payment (public, `operations` entitlement): `GET|PUT|DELETE /payment/collections/merchant-account`,
`POST /payment/collections/merchant-account/verify`, `GET /payment/collections/reconciliation`. Writes need
`operations.payments.merchant_account.write` at ORG **and** `amr ∋ otp`. `MerchantAccountOut` gains
`connection_state`, `verified_at`, `last_error_code`, `webhook_url` (built from `APP_BASE_URL`, never the
browser's origin), `last_webhook_at` and `wires` (per provider); `wire` keeps its `stub | razorpay` literal.

Payment (provider): `POST /api/v1/webhooks/collections?account=<id>` and the alias
`/api/v1/webhooks/razorpay/collections`, one handler, provider from the account row. Public in the app gateway
(POST only, 256 KiB cap), in `WEBHOOK_PUBLIC_PATHS`, `routes.json`, `routes.prod.json` and both pinned tests.

Payment (internal, `operations` only): `CheckoutIn.payer` (`name`, `email`, `phone`), 422 `payer-required` /
`phone-required` before anything is written; `CheckoutOut.checkout` a union by provider;
`ReferenceCollectionsOut` gains `provider`, `provider_wire`. Operations: the checkout route asks once without a
payer and again with the order's own on `payer-required`; `OrderCheckoutSessionOut` is the matching union;
`OrderPaymentsOut` gains `provider`, `provider_wire`.

## UI (Operations)

Payment settings: a provider `RadioGroup` (read-only once connected); Razorpay fields as before; Cashfree's App
ID, Secret Key (password) and Environment (Sandbox / Production, Production disabled unless allowed; fixed when
replacing); "Check connection" with a state badge and a provider-worded hint; a webhook panel with the shared
`CopyField` (new in `@opsgrids/ui`), provider-specific steps (Cashfree: version 2025-01-01 or later, no webhook
secret, Production domain whitelisting) and "last webhook received"; provider-aware reconciliation wording; the
one-time-code 403 and the disconnect 409 as sentences. Order payment card: dispatch on `session.provider`
(`lib/cashfreeCheckout.ts` loads `https://sdk.cashfree.com/js/v3/cashfree.js`, opens
`Cashfree({mode}).checkout({paymentSessionId, redirectTarget: "_modal"})`, discards the result), then
`syncOrderPayment`; a pending payment is polled every 3 s for about a minute (paused on a hidden tab, stopped on
a settled state or a 4xx) under an `aria-live` "Payment pending — don't take payment again".

## Tests (offline only)

`CollectionsContract` over the Razorpay stub, a stateful fake Razorpay over HTTP, the Cashfree fake wire and the
real `CashfreeHttpWire` over an `httpx.MockTransport` answering as that fake, plus twelve broken adapters;
Cashfree adapter unit tests (request shapes, codec edge amounts 1/99/100/10005, signature incl. tampered
timestamp/body and non-ASCII headers, status/event/refund maps, redaction, refund-id format, probe); API
integration (connect/rotate/switch, one-time code, verify incl. timeout, unreachable, rejection, no transaction
during the probe and a verdict on stale keys, negative-authz and cross-tenant for GET/PUT/DELETE/verify/
reconciliation, payer/phone, reopen, webhooks on both paths, refunds, the disconnect guard, reconciliation);
schema CHECKs; the "collections never read platform credentials" scan; gateway path tests and route parity;
operations negotiation and union tests; frontend tests with a mocked SDK.

## Needs a live Cashfree sandbox (owner-run, never CI)

Signature timestamp format and a dashboard test delivery; the delivery-id header name and stability;
`notify_url` with no dashboard endpoint (delivered? query string kept?); probe 404 vs 401; Get Order returning
`payment_session_id` (if not, a reopen answers 409 "cancel it and start a new one"); `refund_id` charset and
refund statuses; `x-api-version` 2025-01-01; fee-bearing amounts; `POST /pg/simulate` outcomes; the `_modal`
checkout and Production domain whitelisting; `+` international phones.

## Deliberately not built

Payment links and buyer checkout; Cashfree partner onboarding, sub-merchants and split; a previous-secret
rotation window; per-account throttles; Prometheus counters (the log events named in the runbook are the
signal for now); a `close_order` on cancel (local cancel plus `order_expiry_time` covers v1).
