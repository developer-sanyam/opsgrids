# Subscription service

Part of the OpsGrids platform. Owns the `subscription` schema.

```bash
make run SERVICE=subscription PORT=8110    # API
make worker SERVICE=subscription           # event consumer
make relay SERVICE=subscription            # outbox relay
make cron SERVICE=subscription             # the dunning sweep (ADR-0005)
```

Four processes, not three: the sweep is its own, because a poll loop must not share a pool with
request traffic and its failure mode is quiet — a sweep that is not running does not error, it just
never suspends anybody.

## Owns

The per-customer commercial lifecycle (ADR-0004 §4) — *what this customer has and where they are in
its life*. Catalog holds the immutable definition of what can be sold; this service holds one
customer's mutable pointer into it.

- **`subscriptions`** — one per organization per product, pinned to a catalog `plan_version_id`.
  `trial | active | past_due | suspended | cancelled`. This state is the authority for whether the
  `product.activations` row entitles.
- **`subscription_items`** — the priced components of the pin, by catalog `price_id`.
- **`subscription_transitions`** — **append-only**. Every accepted move writes exactly one, and the
  stream is what the operator console rebuilds MRR, trial-conversion and churn from.
- **`dunning_cycles`** — one failed collection and the grace window it opened (ADR-0005): when it
  opened, when entitlement stops, which rung of the retry ladder it has reached, and how it ended.

The lifecycle rules are a closed table of legal edges in `app/domain.py`, which also holds the
dunning ladder as a validated `DunningPolicy` — pure arithmetic over stored timestamps, no clock.

## Publishes

Every accepted transition emits exactly one `commerce.subscription.*` event, in the same
transaction as the state change and its append-only transition row.

| Event | Means |
|---|---|
| `commerce.subscription.trial_started` | A subscription opened in `trial`, with `trial_ends_at` |
| `commerce.subscription.activated` | Now `active` — a new paid subscription, a trial converted, or a **dunning recovery** (`reason: dunning_recovered`) |
| `commerce.subscription.renewed` | Renewed into a new period |
| `commerce.subscription.upgraded` / `.downgraded` | Re-pinned to a dearer / cheaper published version |
| `commerce.subscription.past_due` | A payment failed; the grace window is open and **the product still works**. Carries the `dunning` block: window, next attempt, attempts made |
| `commerce.subscription.suspended` | Entitlement stops — the grace window closed (`reason: dunning_exhausted`) or an operator said so |
| `commerce.subscription.cancelled` | Terminal. Never emitted by a timer |
| `commerce.subscription.payment_retry_due` | **Not a lifecycle transition.** The sweep is asking payment for the next dunning debit; carries the invoice, amount and the per-attempt idempotency key |

`product-service` binds the lifecycle events and projects them onto `Activation`; `past_due` maps to
an activation that still entitles, `suspended` and `cancelled` do not. Nothing binds
`payment_retry_due` except payment.

## Consumes

| Event | What it does |
|---|---|
| `commerce.payment.failed` | Opens a dunning cycle (`active → past_due`) or counts a failure against the one already open. **Does not suspend** — that is the sweep's job, at the window's close |
| `commerce.payment.captured` | Closes the cycle and returns the subscription to `active`, from `past_due` or from `suspended` |

Both are idempotent twice over: `processed_events` catches a redelivery, and the domain catches two
genuinely distinct failures (a second one does not open a second window).
