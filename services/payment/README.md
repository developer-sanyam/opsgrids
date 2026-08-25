# Payment service

Part of the Atmora platform. Owns the `payment` schema — the provider-facing edge of commerce
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
  failed`.
- **`refunds`** — append-only (a correction is a new row).
- **`provider_events`** — the append-only webhook inbox and the reconciliation source of truth,
  idempotent on `provider_event_id`.

Append-only on `refunds` and `provider_events` is enforced below the app role by a trigger that
survives the post-migration grant re-apply. Every business table is tenant-scoped with RLS.

## The provider seam (ADR-0004 §6)

`app/providers/base.py` is a `PaymentProvider` Protocol designed against the **more constrained**
provider, Razorpay — mandate caps, a pre-debit notice, multi-day activation, and no native
idempotency key — so a later Stripe adapter fits inside it. `app/providers/razorpay.py` is the
adapter: request/response mapping, signature verification, the state machine and idempotency are
**real and tested**; only the outbound HTTP is **stubbed** (no keys in dev — deterministic fake
ids, state driven by injected webhook fixtures). A test asserts no keys or network are needed.

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

- `POST /api/v1/webhooks/razorpay` — the provider webhook. **Signature-guarded, not token-guarded**
  (a provider carries no platform token); admin-gateway routes it as a public path and payment
  verifies the HMAC signature before recording or acting on anything.
- `POST /internal/v1/charges` — service-to-service, guarded by a short-lived service token. Not in
  the public OpenAPI document.
