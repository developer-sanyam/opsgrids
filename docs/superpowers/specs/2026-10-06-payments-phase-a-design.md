# Payments Phase A: platform checkout on Razorpay or Cashfree, and the merchant-connect P0 fixes

Status: **implemented on branch, pending owner review** (`feat/payments-phase-a`; not deployed, not
merged). This file is the implementation contract and records every place the build departs from the
brief. Decisions of record: [ADR-0029](../../adr/0029-payment-provider-seam-and-checkout.md), which
amends the [ADR-0004](../../adr/0004-commercial-model.md) §6 addendum. Base: `main` @ `bbe6cf09`,
payment Alembic head `a8c1d2e3f4b5` before this work, `c7d8e9f0a1b2` after.

## 1. Goal

Let a person who owns an organization pay one of OpsGrids' **own** invoices (the platform billing an
organization for its subscription) in a provider-hosted checkout, on **Razorpay or Cashfree**,
switchable by configuration, with the money safety the existing seam already gives Razorpay
(lookup-before-create, claim → call → record, a lease, a final lookup) carried over unchanged.

Out of scope, on purpose:

* **Saved mandates and automatic recurring debit (Phase B).** The shapes are declared
  (`DirectDebitContract`, `ProviderCapabilities.debit_shape`, `supports_mandate_debit`); nothing
  debits a mandate. Until Phase B, a platform debit with no usable mandate fails as `no_mandate`
  with **no provider call** (ADR-0005 Q4).
* **Any public webhook path.** The platform's own provider webhooks stay private and the platform
  learns outcomes by polling (section 7).
* **The frontend.** The three endpoints below are the contract a later UI builds on.
* **Currencies other than INR.** Refused before any call.

## 2. Decisions (owner, final)

| # | Decision | Where it lands |
|---|---|---|
| D1 | Phase A only: a **customer-present checkout**. No saved mandates, no automatic recurring debit. | Sections 3, 5; `supports_mandate_debit=False` on every live adapter |
| D2 | Platform webhooks stay **private and poll-only**. No public path, no edit to the app-gateway's public lists. A neutral merchant webhook path is a later release. | Section 7; no `routes*.json` or gateway change |
| D3 | **INR only**, minimum **100 paise**, refused before any provider call. | `ProviderCapabilities.currencies / min_amount_minor`; `services.debit_refusal`; checkout claim |
| D4 | Razorpay **and** Cashfree, switchable. `PAYMENT_PROVIDER` (default `razorpay`) picks the provider for **new** charges only; an existing charge, mandate or event keeps its provider for life. `PAYMENT_WIRE=stub|live` (default `stub`) and `CASHFREE_ENV=stub|sandbox|production` (default `stub`) say whether a socket is ever opened; keys never do. | Section 6 |
| D5 | Hardening the critiques asked for (section 4 and the ADR). | Sections 4, 5 |
| D6 | **Merchant-connect P0**: provider and mode are immutable per `merchant_accounts` row; the collections wire is switched per provider; provider secrets are redacted from the audit; provider-scoped unique indexes. | Section 9 |
| D7 | Frontend out of scope. | |

## 3. The seam

`ProviderContract` (the lookup-before-create statement of ADR-0004 §6) is **byte-identical**. New
behaviour arrives as siblings, so an adapter is held only to what it claims:

* **`ProviderCapabilities`**: a frozen declaration an adapter publishes (`currencies`,
  `min_amount_minor`, `debit_shape` = `order_then_charge | direct`, `requires_mandate_for_debit`,
  `supports_mandate_debit`, `supports_checkout`, `requires_customer_details`,
  `checkout_confirmation`, `signs_headers`, `max_debit_without_afa_minor`, `wire`). The service layer
  reads it **before** a call. An adapter that declares nothing gets the legacy defaults (no limits),
  which is what the in-process stub and every pre-existing test double are.
* **`CheckoutProvider`** and its contract (`tests/checkout_contract.py`): `ensure_checkout` is
  idempotent by the charge's own id, never leaks the customer into the client action, refuses a
  conflicting amount, and answers the browser a provider-shaped `client_action` (Razorpay: public key
  id and Order id; Cashfree: `payment_session_id`).
* **`DirectDebitProvider`** and its contract (`tests/direct_debit_contract.py`): the shape a Cashfree
  subscription debit will take (no Order step). Declared and tested against the fake; **not wired to
  any caller**, because Phase B owns the mandate that feeds it.
* **`providers/wire.py`**: one neutral module for what every wire needs: the error taxonomy
  (section 4), the call deadline, the credentials latch, a base `HttpWire` (no redirects, 1 MiB
  streaming cap, injectable transport, nothing logged, `from None`).
* **`providers/registry.py`**: `BUILDERS` by provider name; `deps.provider_for(name)` caches one
  adapter per name; `deps.get_provider()` is the default for new charges.

The Razorpay adapter is rewritten, not forked: `platform_billing` selects `payment_capture: true` on
the platform's Orders, and the tenant-collections adapter is the same class with the flag off.

## 4. Failure handling at the wire

Three outcomes, never a bare exception:

| Exception | Meaning | What the service does |
|---|---|---|
| `ProviderTransient` | Unknown outcome: a timeout, a 5xx, a dropped connection, a 429. The provider may have created the thing. | The charge stays `created`; the lease and the sweep's lookup-before-create find whatever happened. |
| `ProviderRejected` | **Provably nothing was created**: a 4xx that names a request defect. | The charge fails `provider_rejected:<code>` (the code is Razorpay's or Cashfree's stable code, never a message). |
| `ProviderMisconfigured` | The **platform's own key** is wrong (401/403, Razorpay's invalid-key 400). | The attempt is returned, **no failure is recorded** (the customer did nothing wrong), the credentials latch trips for 900 s and one page goes out. |

Every call runs under one **monotonic deadline** (`call_deadline`): per-request timeouts are clamped
to what is left, a POST refuses to start with less than one full timeout left, and a streamed body
read checks the deadline and a 1 MiB cap. `provider_call_timeout_seconds` is therefore both the
service's wait and the wire's hard bound; its default rose **30 → 45 s** (a Razorpay charge is up to
five requests of 2 s + 5 s = 35 s) and a validator refuses anything under 35 s.

Razorpay answers a duplicate `receipt` with a 400. The adapter treats **any** 400 on an Order create
as "look once more": the Order may exist (a retry after an unknown outcome) and the lookup is what
decides; only a lookup that finds nothing is a rejection.

## 5. Safety guards before the wire

* **`no_mandate`** (`services.debit_refusal`): a platform debit for an adapter that
  `requires_mandate_for_debit` and has no usable mandate for that **provider** is born `failed` with
  `attempts = 0` and `failure_reason = no_mandate`, emits `commerce.payment.failed` (so dunning
  proceeds), and never reaches a wire. It also covers an adapter that cannot debit under a mandate
  yet (`supports_mandate_debit=False`): a mandate does not make a charge debitable until Phase B.
* **Money guard**, in the same function, ahead of the mandate check: a currency the adapter does not
  accept, or an amount under its minimum, fails the charge with a stable reason, **no call made, no
  bank fee paid**. Order: currency, then amount, then mandate.
* **Provider routing is by the row.** `stage_charge` stamps `charges.provider` from the default at
  creation; `finalize_charge`, the sweep, the poller and the webhook resolve the adapter from the row
  (`services.provider_for_charge`), and `finalize_charge` refuses a provider mismatch. **The stub never
  judges a live Order**: asked about an Order it did not mint, it says it cannot answer rather than
  "no payments" (section 10, rollback).

## 6. Provider switches and configuration

| Variable | Default | Effect |
|---|---|---|
| `PAYMENT_PROVIDER` | `razorpay` | Provider for **new** platform charges. Unknown value: the service does not start. Flipping it is not a migration; flipping it back is the rollback. |
| `PAYMENT_WIRE` | `stub` | `stub`: the Razorpay adapter never opens a socket, **whatever keys are present**. `live`: HTTPS to Razorpay. |
| `PAYMENT_ALLOW_LIVE_KEYS` | `false` | Without it `rzp_live_` keys and a Cashfree `production` environment are refused. |
| `CASHFREE_ENV` | `stub` | `stub` (an in-process stateful fake), `sandbox`, `production`. |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | empty | Optional. Both or neither. |
| `CASHFREE_APP_ID` / `CASHFREE_SECRET_KEY` | empty | Optional. Both or neither. `SecretStr`. The secret also verifies every Cashfree webhook (Cashfree has no separate webhook secret). |
| `CHECKOUT_TTL_SECONDS` | `86400` | How long an unpaid checkout stays payable. |
| `CHARGE_POLL_INTERVAL_SECONDS` | `30` | The poller's tick and the minimum gap between two questions about one charge. |

Start-up validators (variable names in the message, never a value): a half-set key pair; a live-shaped
key without `PAYMENT_ALLOW_LIVE_KEYS`; `PAYMENT_WIRE=live` without a Razorpay pair or with a
`rzp_test_` pair when `ENVIRONMENT=production` (real invoices would be payable with a public test
card); `CASHFREE_ENV=sandbox` or `production` without both credentials; `sandbox` when
`ENVIRONMENT=production`; `production` without `PAYMENT_ALLOW_LIVE_KEYS` or with test-shaped keys;
`PAYMENT_PROVIDER=cashfree` on a production box without `CASHFREE_ENV=production`;
`provider_call_timeout_seconds` under 35 s; the lease floor of `2 × call timeout + 60` that already
existed. `infrastructure/edge/lib/payment-switches.sh` mirrors these for preflight and prints names
only. **The consequence for rollout** (section 10): the box cannot be run against test or sandbox
keys. Test-mode checkouts are exercised locally and with the smoke tools; the first thing the box
does with a key is real.

## 7. Checkout, polling and webhooks

### The three endpoints

All under `/api/v1/payment/checkouts`, all `core.product.activation.write` at `ORG` (the permission
that already guards subscribe, cancel and change-plan: paying is the same spend decision; **no new
permission**, so no ADR-0007 fleet rollout).

* `POST /payment/checkouts` `{invoice_id, customer?}`: opens, or **reopens**, the checkout for an
  invoice. The amount is the invoice's own charge's, never the body's. Answers
  `{charge_id, provider, client_action, amount_minor, currency, expires_at}`; `client_action` is
  `{"type": "razorpay_checkout", key_id, order_id, openable}` or
  `{"type": "cashfree_checkout", payment_session_id, mode, openable}`. `openable` is false on a stub.
  Cashfree needs a customer phone (10 digits, or `+` and 8-15 digits); a request without a usable one
  is a 422 before any call, and a phone is never invented. A Cashfree session token is **never
  stored**: reopening fetches it again.
* `GET /payment/checkouts/{charge_id}`: read-through status. While the checkout is open, the
  provider is asked and the answer reconciled through the path a signed webhook takes. Answers
  `{state, paid, failure_reason, provider_checked, …}`. `paid` is true only for a **captured**
  payment; `provider_checked=false` means the provider could not be asked this time.
* `POST /payment/checkouts/{charge_id}/confirm`: Razorpay only. The three fields Checkout's callback
  hands the browser; the server checks `HMAC(order_id|payment_id)` in constant time as a
  **precondition** (400 on a mismatch or a missing field, nothing marked paid) and then asks Razorpay.

**The browser is never evidence.** No route accepts "the payment succeeded" as input. A charge
becomes `captured` only from `order_payments` (these paths, the poller) or a signature-verified
webhook. Another organization's charge id is a 404 on every route (the reads filter on the
organization; RLS is the net).

### The flow column

`charges.flow` is `debit | checkout`. A checkout charge sits `created` with no provider payment until
the customer pays, exactly the shape the stuck-charge sweep resumes, so the sweep **filters
`flow='debit'`**. One open checkout per invoice (a partial unique index plus the service check).
A failed **attempt** never moves a checkout (the customer may try another card); only `captured`
does. An unpaid checkout past `checkout_expires_at`, whose provider was successfully asked and
reports no attempt still in flight, closes as `failed` with `checkout_expired`, a local reason (no
`commerce.payment.failed`: nobody was debited and dunning has no business with it). An attempt in
flight keeps it open for `checkout_inflight_grace_seconds` past its expiry. A closed checkout is
still asked about for `checkout_late_capture_window_seconds` — the poller (at
`checkout_late_capture_poll_interval_seconds`), the status read-through and a verified confirm — so a
capture that arrives after that lands (`captured_after_local_failure`), and it is asked about again
before a new checkout is opened for the same invoice. A stale checkout that never recorded an Order
is closed by the next open; a reopen of one that did is a lookup only, never a create.

### The poller

`app/checkout/cron.py` runs in `payment-worker` (or the API's background group) beside the sweep. It
asks the provider about every platform charge that has an Order and no terminal state: a checkout
the customer may have paid, and a debit the provider authorized and has not yet reported captured.
Claim → call → record; the provider is asked outside any transaction under the call deadline;
`next_attempt_at` is the poll gap. A provider that cannot be reached changes nothing: **a charge is
never expired on a question that could not be asked**. Discovery runs on the BYPASSRLS outbox role
through the same column-scoped grant as the sweep; every write runs on the app role inside the
organization's session.

### Webhooks (private, ready)

* `POST /webhooks/razorpay` exists and is **routed but not public** (`ROUTED_BUT_NOT_PUBLIC`
  unchanged). `POST /webhooks/cashfree` exists in the service and is **404 unless Cashfree is
  enabled**; there is **no gateway route entry for it at all**, so it is unreachable from outside.
* Built ready for the day a public path exists: header-signed adapters (`signs_headers`), a 256 KiB
  cap **before** the HMAC, a signature that **fails closed** (missing secret, header or timestamp,
  non-ASCII header: `False`, never an exception), and **redaction before the inbox insert**: the
  inbox is append-only, so what a webhook stores is an allow-list (no name, email, phone, VPA, card
  or bank detail), with `_meta.webhook_version`. `redact_webhook_body` moved to
  `providers/razorpay.py` and is re-exported by `collections/domain.py`.
* A webhook whose order id conflicts with the charge it names is `conflict` /
  `order_id_conflict`, not applied.

## 8. Cashfree specifics

* **Amounts.** Cashfree speaks rupees as JSON numbers, the platform paise. The codec parses with
  `Decimal` (`parse_float=Decimal`), writes a bare number, **refuses a float**, and accepts only an
  exact whole number of paise (`0.105` is `None`, never an amount). Known-answer vectors are pinned
  independently in `tests/test_cashfree_codec.py`.
* **Signature**: `base64(HMAC-SHA256(secret, timestamp + raw_body))`, constant time over ASCII bytes.
* **Version**: `x-api-version` is a code constant, `2025-01-01` (section 11, deviation 1).
* **Fake**: `CashfreeFakeWire`, a stateful in-process Cashfree (orders, sessions, payments,
  idempotency) that the adapter tests and `CASHFREE_ENV=stub` use.

## 9. Merchant-connect P0 (tenant collections)

Four fixes on the existing tenant-collections path:

1. **Provider and mode are immutable per `merchant_accounts` row.** A reconnect with another provider
   or mode is a **409**; the owner disconnects and reconnects. (An account whose keys were issued for
   one provider can no longer silently be pointed at the other.)
2. **`_account_for` asserts the provider** of the row matches the adapter it builds.
3. **A collections wire switch per provider** (`Settings.collections_wire_for(provider)`). The legacy
   `COLLECTIONS_WIRE=razorpay` keeps meaning *Razorpay live*; the OpenAPI literals are unchanged;
   there is **no** second collections provider value.
4. **`key_secret`, `webhook_secret` and `secret_key` join `REDACTED_KEYS`**
   (`libs/platform-core/src/platform_core/audit.py`), so a connect call can never write a tenant's
   secret into `audit_logs`. The provider-scoped unique indexes of section 10 close the loop on
   `charges`, `mandates`, `refunds` and `provider_events`.

## 10. Data, operations, rollout

### Migration `c7d8e9f0a1b2` (payment; `down_revision a8c1d2e3f4b5`)

One additive revision, no rewrite, no backfill: `charges.flow` (default `debit`) and
`checkout_expires_at`; CHECKs `flow` and `checkout_flow_shape`; `ix_charges_poll`;
`uq_charges_open_checkout`; and five unique keys **widened** to include `provider` (names kept, because
`services._PROVIDER_ID_INDEXES` recognizes a collision by name). The sweep reader's column grant gains
`flow` in `migrations/env.py`. **Downgrade refuses while a checkout charge exists**: the previous code
would resume it as a stuck debit and suspend a customer who has not yet paid. Any other payment
migration in flight shares the head `a8c1d2e3f4b5`; whichever merges second re-points its
`down_revision`, and `alembic heads` must show **one** head at release time.

### Wiring

`compose.prod.yml` (`&payment-env`): the six variables with safe defaults. `.env.example`: commented
blocks, keys optional-empty. `preflight.sh`: the keys are `optional_empty`, a consistency check
mirrors the validators (names only), and the stub warning now says what to do. The deploy docs
(`ENV_SETUP_CHECKLIST`, `env-reference`, `ENV_SOURCE_MATRIX`) describe the keys as live-capable.

### Rollout and rollback (RUNBOOK: "Payments: turning a provider on")

1. Merge, deploy (migrate is additive). Nothing changes: `stub` everywhere.
2. Owner steps at the provider (account, keys, webhook left unregistered), then the **smoke tools on
   the owner's machine** with test/sandbox keys, answering section 12.
3. Set the keys, `PAYMENT_WIRE=live` (or `CASHFREE_ENV=production`) and `PAYMENT_ALLOW_LIVE_KEYS=true`,
   restart `payment` and `payment-worker`, pay one small real invoice from a test organization.
4. **Rollback**: `PAYMENT_PROVIDER=razorpay`, `PAYMENT_WIRE=stub` (and `CASHFREE_ENV=stub`), restart
   both. Rows keep their provider. A checkout still open at the real provider **stays payable there**,
   and while the wire is the stub nothing can tell whether it was paid: the stub answers "I hold no
   such Order" for an Order it never minted (`ProviderTransient`, `stub_holds_no_such_order`), the
   poller records "could not be asked", and **no checkout is expired on that**. A customer reopening
   it meanwhile is refused (503, `recorded_order_not_found`) rather than handed a stand-in Order the
   stub would mint under the same id (Cashfree's `order_id` is the receipt). It is reconciled when
   the wire comes back. Money taken in that window is not lost, but the invoice stays unpaid until the
   wire is back or the owner reconciles at the provider's dashboard: prefer letting open checkouts
   resolve (a day at most, `CHECKOUT_TTL_SECONDS`) before turning the wire off.

## 11. Deviations from the brief

1. **`x-api-version` is `2025-01-01`, not `2026-01-01`.** The critique's 2026 value was unverified;
   `2025-01-01` is the version the webhook documentation the adapter was designed from names. It is a
   code constant, and `payments_smoke_cashfree.py probe` prints whether the account accepts it.
2. **Cashfree PG `failed` / `dropped` webhook events are recorded as `processed` with outcome
   unknown**, not `ignored`: a failed *attempt* is not a failed order, and the poller decides.
3. **Refund key shape.** Platform refunds use `rfd` + 36 hex for both providers (39 characters, inside
   Razorpay's 40-character limit); collections keep their existing `rfd` + 40 hex.
4. **The `debit` seam passes the local mandate id** in the direct shape; Phase B must carry the
   provider's mandate id. Nothing calls it today.
5. **The stub is tolerant, a live adapter is restrictive**: the stub declares legacy capabilities (any
   currency, no mandate needed) so no existing test or dev flow changes; only a live adapter narrows.
6. **No gateway edit.** The Cashfree webhook has no route entry and Razorpay's stays routed but not
   public. This is D2, recorded because a reader may expect the route table to change.
7. **`make boundaries` fails on `main` before this work** (`Module app.orders does not exist`). The
   layers contract was verified for `services/payment` with a trimmed config instead; the failure is
   left alone.
8. **ADR number 0029**, because 0027 is reserved by an unmerged branch.
9. **Known failures left untouched** (they fail on `main` too): `tests/infra` `test_step_up_marker`
   (OAUTH_AMR), `test_route_table_parity` (E501), ruff format on `app/consumers.py` and `app/schemas.py`
   hunks in `services/operations`, two frontend `PurchaseOrderForm` date-fixture tests,
   `make frontend-audit`, and `make secrets` findings in files this work does not touch.

## 12. What this design could not verify, and how it gets settled

Every Razorpay and Cashfree fact above comes from documentation, not from a call: this work was built
with no keys. The owner-run tools (`tools/payments_smoke_razorpay.py`, `tools/payments_smoke_cashfree.py`,
`probe`, `payments`, `refund`, `webhook-listen`) print the answers, as statuses, codes, booleans and
field **names** only:

| Question | Where it is answered |
|---|---|
| The real HTTP status and code of a bad key (the classification does not depend on it) | `bad_key` (both) |
| How Razorpay refuses a duplicate `receipt` | `duplicate_receipt` |
| Whether `payment_capture: true` is honored and echoed | `order_capture_true` / `order_capture_unset` |
| Whether the `receipt` filter is "contains" (the adapter matches exactly regardless) | `lookup_exact` / `lookup_prefix` |
| Cashfree's 404 shape for a missing order | `credentials` (Cashfree) |
| Whether `2025-01-01` is accepted | `api_version_pinned` / `api_version_bogus` |
| `order_tags` limit; whether an international phone is accepted | `tags_15` / `tags_16`, `phone_international` |
| Whether a duplicate order id is a 409 and whether `x-idempotency-key` replays | `order_duplicate`, `order_idempotency_key` |
| Whether `payment_session_id` is returned on a re-fetch | `order_refetch` |
| Which fields a payment entity and a webhook carry (the redaction allow-list) | `payments`, `webhook-listen` |
| Webhook header names, timestamp unit, signature | `webhook-listen` |

Test mode cannot prove real bank behaviour: mandate activation, the pre-debit notice, UPI cut-offs,
issuer declines, webhook retry cadence, fee-bearer amount differences, or rate limits. None of those
is needed for a customer-present checkout; all are Phase B questions.

## 13. Tests

Every test passes with **no network and no real keys**. `conftest.py` pins `PAYMENT_PROVIDER`,
`PAYMENT_WIRE`, `CASHFREE_ENV` and `COLLECTIONS_WIRE` before the app imports; an autouse fixture
replaces `httpx`'s real transports with one that fails the test; adapters run against the stateful
fakes (`razorpay_fake.py`, `CashfreeFakeWire`) or `httpx.MockTransport`. New suites: `test_wire`,
`test_cashfree_codec` (known answers), `test_cashfree_adapter`, `test_razorpay_live`,
`test_checkout_contract`, `test_direct_debit_contract`, `test_config_payment`, `test_checkout_api`
and `test_checkout_status` (each endpoint has a negative-authz and a cross-tenant test),
`test_charge_poller`, `test_no_mandate_guard`, `test_error_taxonomy`, `test_provider_routing`,
`test_platform_webhook_phase_a`, `test_merchant_connect_p0`, `test_phase_a_schema` (and the migration
round trip), `test_refund_key_shape`; in `tests/infra`: `test_payment_env_wiring`,
`test_payment_secret_shapes` (no `cfsk_`, `TEST<digits>` or 14-character `rzp_` key shape in any
tracked file), `test_payment_smoke_tools`, and the I/O-outside-transaction scanner now knows the new
client types.

## 14. Not done

Phase B (saved mandates, recurring debit, the mandate-activation journey, the pre-debit notice);
the frontend; a public webhook path and the neutral merchant webhook path; the provider-side webhook
registration; a refund endpoint for platform charges (still no caller of `create_refund`); the
provider dashboards' configuration (owner); and every item in section 12 until the owner runs the tools.
