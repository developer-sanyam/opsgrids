# ADR-0033 — Merchant connect: a standalone collections seam, and Cashfree as a second provider

**Status:** Proposed — built on `feat/merchant-cashfree` (on top of payments Phase A, now on `main`), not deployed ·
**Date:** 2026-10-06 · **Service:** `payment` (with `operations`, `app-gateway`, the Operations app) ·
**Design:** [merchant connect: Cashfree](../superpowers/specs/2026-10-06-merchant-cashfree-design.md)

Builds on [ADR-0024](0024-fulfilment-boundaries.md) §8 (tenant collections live in `payment`, one adapter per
merchant account, built from that account's sealed keys), [ADR-0019](0019-statutory-portal-credentials.md) §2–3
(sealed tenant secrets: masks only, never audited, never cached), [ADR-0023](0023-member-step-up.md) (`amr`
one-time-code requirements) and [ADR-0002](0002-row-level-security.md) (the webhook's narrow pre-tenant lookup).
It changes a seam [ADR-0004](0004-commercial-model.md) §6 drew for platform billing: tenant collections no
longer borrow it. Platform billing's own provider work (the shared Cashfree wire, codec and fake this reuses) is
recorded by the payments Phase A decision ([ADR-0032](0032-payment-provider-seam-and-checkout.md)), which this
does not change. This record was drafted as 0030 and renumbered to 0033 when it was brought up to date with
`main`, where 0030–0032 had been taken.

## Context

Tenant collections (an organization's own customers paying it, into its own provider account) shipped
Razorpay-only. A merchant who banks with Cashfree could not use it. Adding a second provider exposed four things
that only worked because there had been one:

1. `CollectionsProvider` *was* `PaymentProvider` — the platform's debit seam, with `create_mandate`, `charge`,
   `find_payment` — so a Cashfree merchant adapter would have needed dead stubs, and `verify_webhook(signature,
   body)` had no room for Cashfree's signed timestamp header.
2. Razorpay's vocabulary (event names, refund statuses, the refund key's shape, what of a webhook body is kept)
   lived in `app/collections/domain.py`, so collections "knew" Razorpay.
3. The database demanded two sealed secrets on every live account; a Cashfree account has one (its Secret Key
   also signs webhooks).
4. The only browser surface is staff-opened checkout in Operations; Cashfree needs the paying customer's phone on
   every Order, and hands back a short-lived `payment_session_id` rather than letting the browser open an Order by id.

## Decision

1. **`CollectionsProvider` is a standalone Protocol** (`app/providers/base.py`): `ensure_order` (lookup before
   create, with an optional in-memory `payer`, `expires_at`, `notify_url`, and the `recorded_order_id` a reopen
   fetches by its id and never makes again; `OrderResult.checkout_token` carries a browser token that is never
   stored), `find_order`, `order_payments`, `refund` (with the provider Order id),
   `verify_webhook(headers, body)`, `parse_webhook`, `webhook_delivery_id`, `webhook_facts` (the collection
   outcome, refund facts, failure code and the allow-listed body to store — in the platform's vocabulary) and
   `probe`. `CollectionsContract` (`tests/collections_contract.py`) is its executable definition, run against
   every adapter and against adapters broken the way each check guards against.
2. **Each provider's words live with its adapter.** `RazorpayCollectionsProvider` (a `RazorpayProvider`
   subclass — no behaviour change for a Razorpay merchant) and `CashfreeCollectionsProvider` (built on the
   shared `CashfreeHttpWire`, `cashfree_codec` and `CashfreeFakeWire`; nothing parallel to them). What
   collections must know before an adapter exists — has a webhook secret, needs a payer, refetches its browser
   token, the refund key's shape, the path to show — is a small per-provider `ProviderSpec`.
3. **The provider is the account row's, for life.** One live account per organization; provider and mode are
   fixed per row (switching is disconnect, then connect: a new row, a new webhook URL); the webhook handler takes
   the provider from the account row, never from the path or the body.
4. **One neutral public webhook path**, `POST /api/v1/webhooks/collections?account=<id>`, with the original
   `/api/v1/webhooks/razorpay/collections` kept forever as an alias onto the same handler. A Razorpay account
   keeps showing the path its merchants already registered.
5. **Credentials per provider.** Razorpay: key id (mode from its prefix), key secret, webhook secret. Cashfree:
   App ID, Secret Key, and Sandbox/Production chosen by the merchant (an App ID does not say), no webhook secret;
   CHECKs enforce one secret for Cashfree and two for a live Razorpay account. Production needs
   `COLLECTIONS_ALLOW_LIVE_KEYS`; each provider's wire has its own switch (`COLLECTIONS_WIRE=razorpay` keeps its
   meaning; `COLLECTIONS_CASHFREE_WIRE=stub|live`).
6. **Saving is database-only; verifying is separate.** `PUT` validates shape and seals; the account is
   `unverified` until `POST …/merchant-account/verify` claims, probes the provider off the event loop within
   `collections_call_timeout_seconds` with no transaction open, and records `verified_at` or `last_error_code`
   under the row lock (refusing to record a verdict about keys that changed meanwhile), audited. **Verification
   is refused (409 `verification-unavailable`, nothing opened, recorded or audited) while the account's provider
   runs on the stub wire**: the stub holds no credentials and accepts any, so a "verified" recorded there would
   claim — and audit — that the provider accepted keys it never saw, and would outlive the switch to live.
7. **Changing where money goes needs a one-time-code session.** `PUT`, `DELETE` and `verify` require
   `amr ∋ otp` (the put-away reveal's rule; not the `stepup` marker, which the Operations app cannot obtain yet).
8. **Disconnect is refused (409 `disconnect-blocked`, saying what is outstanding) while the provider may still
   move money for the account** — once the keys are wiped, nothing it reports can be asked about or verified:
   - a collection that is **open** (`created`, `authorized`, or `failed` — a failed attempt's Order still takes
     a retry);
   - a collection this platform **cancelled or expired whose provider Order may still be paid**: cancelling here
     does not close the provider's Order. A Cashfree Order takes payment until its own expiry (the checkout's
     lifetime from creation); any provider can complete an attempt already in flight (a UPI request approved
     late) for `LATE_PAYMENT_GRACE` (20 minutes) after the close (`domain.payable_until`);
   - a **refund still pending** at the provider: only its webhook says whether the customer got the money back,
     and a failed one gives the order its paid fact back.

   `DELETE …?force=true` (same permission and one-time-code rule) disconnects anyway — an explicit choice in the
   UI, which states the consequence — and the audit row records exactly what was outstanding; those are then
   settled in the provider's own dashboard. Without it, an `authorized` payment nothing captures, or a refund
   whose webhook never arrives, would lock the account to its provider for good.

   **Each collection records which wire made its Order** (`charges.order_wire`: `stub` | `live`, NULL before it
   was kept — treated as possibly real). An Order the stub made can never be paid, so: it is never asked about
   once the wire is live (the provider would refuse a lookup of an Order it never made), a stub that no longer
   remembers it (merchant adapters are built per call; another process; a restart) is "no payments", it is never
   reopened against a live provider (that would quietly mint a real Order nobody recorded), and once closed it
   blocks nothing. That is what keeps a stub test checkout from wedging an account on the default wire. The
   converse holds too: a reopen names the Order the collection records (`ensure_order(recorded_order_id=…)`,
   the payments Phase A recorded-Order rule), so an Order made live is never minted again by the stub after
   the wire is turned back off — the reopen is refused "try again" (`recorded_order_not_found`) instead.
9. **The payer travels only when the provider asks.** Operations opens a checkout with no customer; a provider
   that needs one answers 422 `payer-required` before anything is written, and operations asks again with the
   order's name and phone. Payment passes the phone to the provider and nowhere else (a pseudonymous
   `customer_id`); a missing or unusable phone is 422 `phone-required` — never invented.
10. **The browser handle is a union by provider**: Razorpay `{key_id, provider_order_id}`, Cashfree
    `{payment_session_id, mode}`; the session id is fetched from Cashfree on every (re)open and never stored.

## Consequences

- A Razorpay merchant sees no change except the new verification state, the one-time-code requirement and the
  disconnect guard. The legacy webhook path and its URL are unchanged.
- Rolling the `payment` image back once a Cashfree account exists is unsafe (the old code would build a Razorpay
  adapter from Cashfree keys); the migration's downgrade refuses while one exists.
- The P0 test that pinned "no second provider value" is superseded; the test that disconnected an account with a
  checkout open now cancels it first.
- A Cashfree checkout cancelled the same day holds up a disconnect until its Order expires (up to the checkout
  lifetime, 24 hours by default), unless the operator forces it. Terminating the Order at Cashfree on cancel
  (`PATCH /pg/orders/{id}`) would shorten that; it is not built, because nothing here can verify that call
  against a live account yet.
- Inbox storage keeps only the platform's own `order_tags` (`organization_id`, `charge_id`, `subscription_id`,
  `reference_id` as UUIDs; `reference_type`, `product_key`, `flow` as slugs): the account-wide dashboard webhook
  delivers the merchant's other integrations' Orders too, whose tags are free-form and can carry their buyers.

## Alternatives rejected

- **Keep `CollectionsProvider` as a `PaymentProvider`** — dead `charge`/`create_mandate` stubs in every merchant
  adapter, and a webhook signature with no headers.
- **Persist `payment_session_id`** — a bearer-ish token in the database for no gain; Get Order returns it.
- **Always send the customer's phone to payment** — data minimisation: Razorpay does not need it.
- **One `COLLECTIONS_WIRE` value per combination** (`razorpay,cashfree`) — changes the legacy literal's meaning.

## Open, settled only by a live sandbox (owner-run)

The real timestamp format and a dashboard test delivery; the delivery-id header name; `notify_url` with no
dashboard endpoint; the probe's 404 vs 401; whether Get Order returns `payment_session_id`; `refund_id` charset;
`x-api-version`; fee-bearing amounts; the `_modal` checkout and domain whitelisting; international phones.

## Addendum (2026-10-16): redirect-checkout providers — PhonePe first

Wave 1 of the integrations plan adds tenant collections adapters for PhonePe, PayU and Paytm, one PR
each. PhonePe (Standard Checkout **V2**, OAuth client credentials) is the first and settles what the
other two reuse:

1. **A redirect handle.** `OrderResult.redirect` (`CheckoutRedirect`: a URL, `GET` or `POST`, and form
   fields) is the browser's handle for a provider whose checkout is its own page. In memory only, like
   Cashfree's `payment_session_id`; fetched again on every (re)open. The Operations app opens it in a
   **new tab** on a real click, so the order page stays open and keeps asking the provider.
2. **One return page.** `GET|POST /api/v1/pay/return` is a static "you can close this
   tab" page — public on the app gateway for exactly those two methods. It reads nothing from the
   request: a browser return is never evidence, and the page cannot be steered (no redirect target).
3. **`config` carries the non-secret settings** (PhonePe: `client_version`, `webhook_username`), and the
   factory hands it to the adapter. The webhook password is the account's sealed webhook secret.
4. **PhonePe's webhook credential does not sign the body.** Its documented scheme (and its SDK) is an
   `Authorization` header equal to `hex(SHA256(username:password))` — a static shared credential over
   TLS. It is checked in constant time and fails closed, and it is **never stored**
   (`ProviderSpec.signature_header` is None). The contract's two per-body-signature checks are replaced,
   for PhonePe only, by what that scheme can promise. PhonePe's HMAC option names no algorithm or signed
   string; adopting it is an open question for the owner's sandbox run.

**PayU** (Hosted Checkout) follows: the same redirect handle (a form `POST` of the request and its
hash to PayU's `_payment` page) and the same return page (PayU posts its response there; it is never
read). PayU holds no Order until the customer posts the form, so `ensure_order` creates nothing — it
asks `verify_payment` first and never hands out a transaction that is captured, authorised or pending.
`txnid` is the collection id in base 36 (PayU allows 25 characters); the return URL must fit PayU's
documented 50-character `surl`. Payment webhooks carry PayU's reverse hash (verified, failing closed;
it covers `status`, `amount`, `txnid`, `key`, the udfs and the customer fields — not `mihpayid`).
PayU's refund webhooks carry no hash, so they are refused: a PayU refund stays `created` here until a
refund-status poll is built.

**Paytm** follows the same shape: Initiate Transaction (looked up first with Transaction Status) gives a
`txnToken`, and the redirect handle is a form `POST` of `mid`, `orderId` and `txnToken` to Paytm's
`showPaymentPage`; Paytm's callback goes to the same return page. Requests and webhooks are signed with
Paytm's checksum (its `paytmchecksum` library's AES-CBC scheme, reproduced and pinned by tests); the
Payment Status webhook's `CHECKSUMHASH` covers every field. Paytm's refund webhooks (JSON, a signature
over a re-serialised body, none at all on its failure sample) are refused like PayU's.
