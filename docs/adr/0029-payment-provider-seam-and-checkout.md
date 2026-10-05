# ADR-0029 — Payment provider seam: two providers, one checkout, no mandate debit yet

**Status:** Accepted by the owner, 2026-10-06 (decisions D1-D7 of the design) — built on
`feat/payments-phase-a`, not merged, not deployed ·
**Date:** 2026-10-06 · **Service:** `payment` (and the tenant-collections path inside it) ·
**Design:** [Payments Phase A](../superpowers/specs/2026-10-06-payments-phase-a-design.md)

Builds on [ADR-0004](0004-commercial-model.md) §6 and its 2026-09-24 addenda (lookup before create; the
webhook inbox), which it **amends** (see "Amendments to ADR-0004" below);
[ADR-0005](0005-dunning-and-grace.md) Q4 (a debit with no mandate must not reach the provider);
[ADR-0000](0000-phase-0-decisions.md) §9-§10 (money is integer minor units; invoices, refunds and the
webhook inbox are append-only; every table carries `organization_id`; no cross-schema foreign keys);
[ADR-0007](0007-permission-rollout-to-live-tenants.md) (why no new permission is added);
[ADR-0024](0024-fulfilment-boundaries.md) §8 (tenant collections, which share the Razorpay adapter);
and the production edge's [RUNBOOK](../../infrastructure/edge/RUNBOOK.md) (the box, and why its `.env`
is the only place a key lives).

## Context

`payment` was built Razorpay-first and stubbed: no key, no socket, every adapter answer deterministic.
That bought a contract worth keeping (lookup before create, claim → call → record, a lease, a final
lookup), but the platform could not collect a rupee, and the only customer-facing surface was none:
an invoice was charged by the machine (a debit against a saved mandate) or not at all.

Two things changed. The owner decided to collect through a **customer-present checkout** first (a
person pays the invoice in the provider's hosted page), deferring saved mandates and automatic
recurring debit to a second phase, and to support **Cashfree as well as Razorpay**, switchable. The
existing seam could not carry either as it stood: it had one provider's error vocabulary, no way to say
"this adapter cannot debit without a mandate", no per-row provider routing, and a webhook inbox that
stored the provider's body verbatim (a payer's email, phone and UPI id in an append-only table).

## Decision

1. **Phase A is a checkout, and only that.** The platform never debits a saved mandate in this phase.
   A platform debit whose adapter needs a mandate and has none usable fails as `no_mandate` **before
   any provider call** (ADR-0005 Q4), with `attempts = 0`, and emits `commerce.payment.failed` so
   dunning proceeds. The direct-debit shape Cashfree needs (`DirectDebitProvider`,
   `ProviderCapabilities.debit_shape = "direct"`) is declared and contract-tested against a fake but
   **wired to no caller**. INR only, 100 paise minimum, refused before any call.

2. **The seam grows by siblings; `ProviderContract` is not touched.** `ProviderCapabilities` is what an
   adapter declares (currencies, minimum, debit shape, mandate needs, checkout support, header-signed
   webhooks, wire); the service layer reads it **before** a call, so a charge that could only fail at
   the provider fails here, with no call and no bank fee. `CheckoutProvider` and `DirectDebitProvider`
   have their own contracts. An adapter that declares nothing gets the legacy defaults (no limits, no
   mandate needed), which is what the in-process stub and every older test double are: the stub is
   tolerant, a live adapter is restrictive.

3. **One neutral wire layer** (`providers/wire.py`) for both providers: `HttpWire` (no redirects, no
   logging, a 1 MiB streaming cap, injectable transport, `from None`), a three-way error taxonomy, an
   **overall monotonic call deadline**, and a credentials latch.

   | Raised | Means | Effect |
   |---|---|---|
   | `ProviderTransient` | unknown outcome (timeout, 5xx, 429, dropped) | charge stays `created`; the lease and the sweep's lookup-before-create settle it |
   | `ProviderRejected` | provably nothing was created (a 4xx naming a request defect) | the charge fails `provider_rejected:<code>`, a stable provider code, never a message |
   | `ProviderMisconfigured` | the platform's own key is wrong | the attempt is returned with **no failure recorded** (the customer did nothing wrong); the latch trips for 900 s; one page |

   `provider_call_timeout_seconds` is both the service's wait and the wire's hard bound (a POST refuses
   to start with less than one full timeout left). Its default rose from 30 to **45 s**: a live
   Razorpay charge is up to five requests of (2 s connect + 5 s read), 35 s, and 30 s left two seconds
   of slack. A validator refuses anything under 35 s.

4. **Per-row provider routing.** `PAYMENT_PROVIDER` (default `razorpay`) chooses the provider for
   **new** charges only. A charge, mandate or inbox event keeps the provider it was created under for
   life (`charges.provider`), because a provider cannot find, debit or refund another's payment; the
   sweep, the poller, the webhook and `finalize_charge` resolve the adapter from the row, and finalize
   refuses a mismatch. Flipping the switch is not a migration; flipping it back is the rollback.
   Five unique keys (`charges` ×2, `mandates`, `refunds`, the `provider_events` inbox) are widened to
   include `provider`, so one provider's id is never a conflict with another's.

5. **Switches that never rely on keys.** `PAYMENT_WIRE=stub|live` (default `stub`) and
   `CASHFREE_ENV=stub|sandbox|production` (default `stub`) are the only things that open a socket.
   **Keys present never switch the network on**: a developer's `.env` with a real key still talks to
   nothing. Keys are optional-empty (`SecretStr`, both-or-neither), live-shaped keys need
   `PAYMENT_ALLOW_LIVE_KEYS=true`, and start-up refuses the combinations that would be wrong on a
   production box: a `rzp_test_` pair on a live wire, Cashfree `sandbox`, `PAYMENT_PROVIDER=cashfree`
   without `CASHFREE_ENV=production`. The reason is concrete: a public test card paying a real invoice
   is a free subscription. **A consequence the owner should know:** the box cannot be pointed at test
   or sandbox keys at all. Test mode is exercised on a developer's machine and with the smoke tools;
   the first key the box ever holds is a live one.

6. **A platform checkout surface, behind an existing permission.** Three routes under
   `/api/v1/payment/checkouts` (open or reopen; read-through status; Razorpay's signed confirm), all
   `core.product.activation.write` at `ORG`, the permission that already guards subscribe, cancel and
   change-plan. **No new permission**: that would be an ADR-0007 fleet rollout for a gate the existing
   one expresses. The amount is the invoice's own charge's, never the body's. **The browser is never
   evidence**: a charge becomes `captured` only from the provider's own answer (a status check, the
   poller) or a signature-verified webhook; Razorpay's confirm checks `HMAC(order_id|payment_id)` as a
   **precondition** and then asks Razorpay. Every route 404s another organization's charge.

7. **`charges.flow` (`debit | checkout`).** A checkout charge sits `created` with no provider payment
   until the customer pays, which is precisely the shape the stuck-charge sweep resumes and eventually
   fails. So the flow is a column and the sweep filters on it. One open checkout per invoice (a partial
   unique index). A failed *attempt* never moves a checkout (the customer may try another card); only
   `captured` does. An unpaid checkout past its expiry whose provider was **successfully asked**, and
   reports no attempt still in flight, closes as `failed` / `checkout_expired`, a local reason that
   emits no `commerce.payment.failed`. An attempt in flight (a payment in progress, an authorization
   whose auto-capture has not run, a capture that could not be applied) keeps it open for
   `CHECKOUT_INFLIGHT_GRACE_SECONDS` (an hour) past its expiry. A closed checkout is **still asked
   about** for `CHECKOUT_LATE_CAPTURE_WINDOW_SECONDS` (five days; a Razorpay Order never expires) — by
   the poller at a longer gap, by the status read-through and by a signature-verified confirm — so a
   capture that arrives afterwards is recorded (`captured_after_local_failure`); and before a new
   checkout is opened beside it, it is asked about first (paid meanwhile: "already paid"; a payment
   still in flight: a 409). A checkout past its expiry that **never recorded an Order** (every open
   of it failed at the provider) is closed on the next open: no Order id reached a browser, so nothing
   can have been paid on it. A reopened checkout that already records an Order is only ever looked up
   at the provider, never re-created, so a stub after a rollback cannot mint a stand-in Order under the
   live one's id.

8. **The platform learns outcomes by polling, because the webhooks stay private.** `app/checkout/cron.py`
   asks the provider about every platform charge with an Order and no terminal state, with the same
   claim → call → record split as everything else. Discovery is cross-tenant on the BYPASSRLS outbox
   role through the sweep's column-scoped grant (extended by `flow`); every write is on the app role in
   the organization's session. **A charge is never expired on a question that could not be asked**, and
   the Razorpay stub, asked about an Order it never minted, says it cannot answer instead of "no
   payments" (otherwise rolling back to the stub with a checkout open would expire a paid one).

9. **Webhooks are built ready and left private.** No public path and no gateway edit (owner decision).
   The Razorpay route stays `ROUTED_BUT_NOT_PUBLIC`; the Cashfree route exists in the service, is 404
   unless Cashfree is enabled, and has no gateway route at all. For the day one does: header-signed
   adapters, a 256 KiB cap before the HMAC, a fail-closed signature, and **redaction before the inbox
   insert**. The inbox is append-only, so what it stores is an allow-list (no name, email, phone, VPA,
   card or bank detail) with `_meta.webhook_version`, and amounts as canonical decimal strings.

10. **Cashfree's money codec is exact.** Cashfree speaks rupees as JSON numbers; the platform speaks
    paise. The conversion is one module: `Decimal` parsing, bare-number output, **floats refused**, an
    inbound amount accepted only as an exact whole number of paise. The signature is
    `base64(HMAC-SHA256(secret, timestamp + body))`, verified in constant time and failing closed.

11. **Merchant-connect P0 (tenant collections).** Provider and mode are **immutable** per
    `merchant_accounts` row (a change is a 409: disconnect and reconnect); `_account_for` asserts the
    provider matches; the collections wire is switched **per provider**
    (`Settings.collections_wire_for`), with the legacy `COLLECTIONS_WIRE=razorpay` still meaning
    Razorpay live and no second collections provider value; `key_secret`, `webhook_secret` and
    `secret_key` join the audit's `REDACTED_KEYS`.

12. **Nothing here is verified against a provider.** It was built with no keys. Two owner-run tools,
    `tools/payments_smoke_razorpay.py` and `tools/payments_smoke_cashfree.py`, settle what the design
    assumed. They are opt-in (`--confirm`), refuse CI, pytest and any deployed `ENVIRONMENT`, take
    **test or sandbox keys from the process environment only**, address only the provider's test host,
    and print ids, statuses, codes, counts, booleans and field **names**: never a body, a message or a
    credential. They are never run by `make`, CI or a test.

## Consequences

* Merging and deploying changes **nothing at runtime**: every switch defaults to the stub. Turning a
  provider on is a deliberate sequence of owner steps (RUNBOOK, "Payments: turning a provider on"), and
  rolling back is two variables and a restart.
* One additive migration, `c7d8e9f0a1b2`. Its downgrade **refuses** while a checkout charge exists: the
  previous code would resume it as a stuck debit and suspend a customer who has not yet paid.
* The default of `provider_call_timeout_seconds` changed (30 → 45 s) and the lease floor moves with it
  (`charge_reclaim_after_seconds` ≥ 2 × timeout + 60; the default 300 s still clears it).
* Until Phase B, an organization is billed by checkout only, and a platform debit with no mandate
  fails visibly as `no_mandate`; dunning's retry rungs for such a charge will fail the same way. That
  is the intended, safe behaviour (nothing reaches a provider), and Phase B is what changes it.
* The first call to a real provider will find things the docs did not say. They are listed in design
  section 12 with the smoke-tool step that answers each; the adapters were written to **fail safe on
  each unknown** (classify by status class rather than by an assumed code; re-look-up after any 400;
  match a receipt exactly rather than trust a filter).

## Amendments to ADR-0004

The 2026-09-24 addendum ("§6 in practice: lookup before create") and its "For the live adapter (B5g)"
note are amended as follows. The history above them is left as written; each correction is also
recorded there as a dated block.

1. *"The live adapter's test module subclasses `ProviderContract` and passes it unchanged before it
   can merge."* `ProviderContract` is the contract for an adapter of the **order-then-charge** shape
   (Razorpay). It is unchanged and Razorpay's live adapter passes it. An adapter of another shape is
   held to **its own** sibling contract (`CheckoutContract`, `DirectDebitContract`), and a shape-wide
   rule that every adapter must pass every contract would have forced Cashfree's direct debit to fake
   an Order step it does not have.
2. *Overall deadline.* The note said a live adapter "needs an overall per-call deadline in addition to
   per-request timeouts". It is built: `call_deadline` in `providers/wire.py`.
3. *`payment_capture`.* The platform's Razorpay Orders are created with `payment_capture: true`
   (an authorized-but-uncaptured payment is refunded by Razorpay after three days, and a platform
   charge must be captured when the customer pays). Tenant-collection Orders are unchanged.
4. *A duplicate `receipt`.* The addendum said Razorpay "does not enforce [receipt] uniqueness". Whether
   it rejects a duplicate with a 400 is **unverified** (reports disagree); the adapter looks the
   receipt up once more after **any** 400 on an Order create and uses the Order it finds, and the
   smoke tool prints what Razorpay does.
5. *Timeouts.* "The lease is validated to be at least twice the provider call timeout plus a minute"
   still holds; the call timeout's default is 45 s and a validator keeps it at or above the live
   wires' declared worst case (35 s).

## Rejected

* **Merging the two providers' errors into the adapters' own.** Each adapter would have invented its
  own timeout/rejection vocabulary and the service would have branched on provider name. The neutral
  taxonomy keeps `services.py` free of provider names.
* **A new permission for paying an invoice.** A fleet-wide ADR-0007 rollout for a gate that
  `core.product.activation.write` already is.
* **Letting keys switch the network on.** A key in a developer's `.env` would then talk to a real
  provider from a laptop, a CI run or a test.
* **Trusting the browser's "success" callback.** The only thing it is good for is a precondition
  before the server asks the provider.
* **Expiring a checkout when the provider could not be asked, or when the stub knows nothing.**
  Money may have moved.
* **A public webhook path in this release.** Owner decision; the code is ready and the polling path
  is what Phase A relies on.
* **A second collections provider value** (`COLLECTIONS_WIRE=cashfree`). Merchant-connect's own work
  adds a Cashfree collections adapter later; this release only makes the switch per provider.
* **Redacting the webhook inbox after the fact.** It is append-only, so it is redacted before the
  insert or not at all.
