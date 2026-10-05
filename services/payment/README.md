# Payment service

Part of the OpsGrids platform. Owns the `payment` schema — the provider-facing edge of commerce
(ADR-0004 §1, §6). It carries the provider-specific truth (*what was actually collected*, and the
messy reality behind it) and keeps it isolated, so no other service knows which processor took the
money — only that a charge was captured or failed.

```bash
make run SERVICE=payment PORT=8163    # API
make worker SERVICE=payment           # event consumer (charges from finalized invoices)
make relay SERVICE=payment            # outbox relay
```

## Owns

- **`mandates`** — a customer's standing authorization to be debited, with the Razorpay cap
  (`max_amount_minor`) and multi-day eMandate activation (`activated_at`) as first-class fields.
- **`charges`** — one collection attempt against an invoice. Idempotent on a **platform-supplied**
  `idempotency_key` (Razorpay has none). Small state machine: `created → authorized → captured |
  failed`. `provider` is the provider the charge was created under, for life; `flow` is `debit` (the
  machine debits a mandate) or `checkout` (a person pays in the provider's hosted page; ADR-0029).
- **`refunds`** — append-only (a correction is a new row).
- **`provider_events`** — the append-only webhook inbox and the reconciliation source of truth,
  idempotent on `provider_event_id` — Razorpay's `X-Razorpay-Event-Id` **header** (its body has no
  `id`), else a digest of the body's event name, entity id and timestamp; never empty. Each row
  states its `disposition`: `processed`, or `ignored` / `conflict` with a stable
  `disposition_reason` (ADR-0004, PAY-A addendum).

Append-only on `refunds` and `provider_events` is enforced below the app role by a trigger that
survives the post-migration grant re-apply. Every business table is tenant-scoped with RLS.

## The provider seam (ADR-0004 §6)

`app/providers/base.py` is a `PaymentProvider` Protocol designed against the **more constrained**
provider, Razorpay — mandate caps, a pre-debit notice, multi-day activation, and no native
idempotency key — so another adapter fits inside it. Two adapters exist, **Razorpay** and
**Cashfree**, and `ProviderContract` (lookup before create) is the contract for the order-then-charge
shape; a checkout and a direct (mandate) debit have sibling contracts (ADR-0029).

* **What an adapter can do is declared, not guessed**: `ProviderCapabilities` (currencies, minimum,
  debit shape, mandate needs, checkout support, header-signed webhooks, wire) is read before a
  call, so a charge that could only fail at the provider fails here, with no call.
* **One wire layer for both** (`providers/wire.py`): a three-way error taxonomy
  (`ProviderTransient` unknown outcome, `ProviderRejected` nothing created, `ProviderMisconfigured`
  the platform's own key), an overall monotonic call deadline, and a credentials latch.
* **Switches, all defaulting to the stub**: `PAYMENT_PROVIDER` (`razorpay` | `cashfree`; **new**
  charges only, an existing row keeps its provider), `PAYMENT_WIRE` (`stub` | `live`) and
  `CASHFREE_ENV` (`stub` | `sandbox` | `production`). Keys never switch the network on; start-up
  refuses wrong combinations (a test key on a production box among them). Without a switch the adapters
  are the in-process stubs: deterministic fake ids, state driven by injected fixtures, no network.
* **Phase A has no mandate debit.** A debit whose adapter needs a mandate and has none usable fails as
  `no_mandate` before any call; the direct-debit shape is declared and tested against a fake, wired to
  no caller. INR only, 100 paise minimum.

Nothing here has been run against a real provider. `tools/payments_smoke_razorpay.py` and
`tools/payments_smoke_cashfree.py` are the owner-run, opt-in tools that settle what the design assumed
(RUNBOOK, "Payments: turning a provider on"); every automated test needs no network and no key.

## Publishes

- `commerce.payment.captured` — a charge was captured (money collected). Subscription consumes it
  to close a dunning cycle and put a `past_due` or `suspended` subscription back to `active`.
- `commerce.payment.failed` — a charge failed. This is what starts the **dunning ladder** and,
  eventually, de-entitles a product (ADR-0004 §5, ADR-0005): the failure event carries
  `subscription_id`, the subscription service consumes it and opens a grace window (`past_due`,
  still entitling), and only when that window closes does it emit `suspended` — which
  product-service projects to a de-entitled `Activation` and the gateway turns into a **402**.

## Consumes

- `commerce.invoice.finalized` — billing finalized an invoice; raise a charge for it (idempotent on
  the invoice id). The internal `POST /internal/v1/charges` is the imperative equivalent, for when
  billing prefers to call rather than emit.
- `commerce.subscription.payment_retry_due` — subscription's dunning sweep has reached the next rung
  of its ladder (ADR-0005); raise the retry debit. subscription decides *when* to retry because it
  owns the grace window; payment decides *how* because it owns the provider. The idempotency key is
  per **attempt** (`dunning:{cycle_id}:{n}`), not per invoice — reusing the failed charge's key
  would be deduplicated into the charge that already failed and debit nobody.

## Endpoints

- `POST /api/v1/payment/checkouts`, `GET /api/v1/payment/checkouts/{charge_id}`,
  `POST /api/v1/payment/checkouts/{charge_id}/confirm` — the platform checkout (ADR-0029): open or
  reopen the checkout for one invoice, read its status (the provider is asked while it is open), and
  Razorpay's signed confirm. All need `core.product.activation.write` at `ORG`; the amount is the
  invoice's own; **the browser is never evidence**; another organization's charge is a 404. A poller
  (`app/checkout/cron.py`) resolves what nobody is told, because the webhooks below are private.
- `POST /api/v1/webhooks/razorpay` — the provider webhook. **Signature-guarded, not token-guarded**
  (a provider carries no platform token); it is **routed to payment but deliberately not public** in
  app-gateway in this release (owner decision, ADR-0029), and payment verifies the HMAC signature
  before recording or acting on anything. A Cashfree sibling (`/webhooks/cashfree`) exists in the
  service, is a 404 unless Cashfree is enabled, and has no gateway route at all. Both store a
  **redacted** body (no name, email, phone, VPA or card detail): the inbox is append-only. A permanent condition in a
  signed webhook (a charge that is not ours, an outcome the state machine forbids, another amount
  or currency, a payment id other than the charge's) is recorded, audited `payment.webhook.ignored`
  and answered **200** — a retry could not change it. `failed → captured` is legal for a failure the
  platform decided and for the **same payment** the provider had failed (a bank's late
  authorisation; ADR-0004, owner decision). Every capture also checks whether the same invoice is
  *already* collected by another charge — a dunning retry and the original charge's own late
  capture can both land — and if so logs `payment.invoice.collected_twice` and audits it; nothing
  is refunded automatically, an operator handles it by hand. Non-2xx is exactly: a bad signature or
  an unidentifiable body (**400**), a transient database failure (**503**), an unrelated
  `IntegrityError` (the platform's **409**), and a defect (**500**).
- `POST /internal/v1/charges` — service-to-service, guarded by a short-lived service token. Not in
  the public OpenAPI document.
- `GET /internal/v1/organizations/{org}/mandates:usable?currency=&amount_minor=` — could this
  organization be debited this amount now? The same four conditions the charge guard enforces
  (active, bank-confirmed, currency, cap). Asked by subscription's billing run when a trial ends
  (ADR-0013 §6, ADR-0005 addendum); **only subscription** may ask. Identifiers only.

`commerce.payment.failed` carries `failure_reason` (additive), which subscription records on a
trial conversion whose first charge failed before the billing run recorded the conversion.

A `commerce.invoice.finalized` whose total is zero (a free plan's period) raises no charge: a zero
debit is a provider call and a bank fee that moves no money.
