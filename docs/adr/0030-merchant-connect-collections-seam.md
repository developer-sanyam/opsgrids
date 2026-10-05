# ADR-0030 — Merchant connect: a standalone collections seam, and Cashfree as a second provider

**Status:** Proposed — built on `feat/merchant-cashfree` (stacked on `feat/payments-phase-a`), not deployed ·
**Date:** 2026-10-06 · **Service:** `payment` (with `operations`, `app-gateway`, the Operations app) ·
**Design:** [merchant connect: Cashfree](../superpowers/specs/2026-10-06-merchant-cashfree-design.md)

Builds on [ADR-0024](0024-fulfilment-boundaries.md) §8 (tenant collections live in `payment`, one adapter per
merchant account, built from that account's sealed keys), [ADR-0019](0019-statutory-portal-credentials.md) §2–3
(sealed tenant secrets: masks only, never audited, never cached), [ADR-0023](0023-member-step-up.md) (`amr`
one-time-code requirements) and [ADR-0002](0002-row-level-security.md) (the webhook's narrow pre-tenant lookup).
It changes a seam [ADR-0004](0004-commercial-model.md) §6 drew for platform billing: tenant collections no
longer borrow it. Platform billing's own provider work (the shared Cashfree wire, codec and fake this reuses) is
recorded by the payments Phase A decision, which this does not change.

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
   create, with an optional in-memory `payer`, `expires_at`, `notify_url`; `OrderResult.checkout_token` carries
   a browser token that is never stored), `find_order`, `order_payments`, `refund` (with the provider Order id),
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
   under the row lock (refusing to record a verdict about keys that changed meanwhile), audited.
7. **Changing where money goes needs a one-time-code session.** `PUT`, `DELETE` and `verify` require
   `amr ∋ otp` (the put-away reveal's rule; not the `stepup` marker, which the Operations app cannot obtain yet).
8. **Disconnect is refused (409, with the count) while a collection taken into the account is open**
   (`created`, `authorized`, or `failed` — a failed attempt's Order still takes a retry).
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
