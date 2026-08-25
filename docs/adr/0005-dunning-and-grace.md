# ADR-0005 — Dunning and the grace window: what happens between a failed payment and a switched-off product

**Status: Accepted 2026-08-25.** Implemented in `subscription-service` (the state machine, the
dunning cycle and the sweep) and `payment-service` (the retry debit) in the same change.

Answers the first item [ADR-0004](0004-commercial-model.md) left Open, and changes nothing else
about the commercial model. Builds on ADR-0004 §4 (the lifecycle is subscription states, every
transition is an event), §5 (entitlement is the switch commerce sells through) and §6 (Razorpay
first, because it is the more constrained provider), and on
[ADR-0000](0000-phase-0-decisions.md) §8 (money in minor units) and §9 (append-only).

---

## Context

ADR-0004 built the whole entitlement loop and then declined to say when to pull the lever:

> **Dunning and the grace window** — how many retries, over how long, and whether a failed renewal
> suspends immediately or after a grace period, is a subscription-state question this ADR does not
> answer. The states exist (`suspended`); the *policy* for entering them does not.

In the absence of a policy the code took the only default that needed no decision, and said so out
loud (`services.py`, before this ADR): *"**No-dunning MVP.** A failed payment suspends
immediately."* One `commerce.payment.failed` moved the subscription straight to `suspended`, which
product-service projected to a de-entitled activation, which the gateway turned into a 402. The
loop was correct end to end. Its *policy* was that a customer whose bank declined one debit loses
their product before they have been told a debit was attempted.

That is not a defensible default on the rails this platform actually bills on, and the reasons are
already documented in this repository rather than being matters of opinion. ADR-0004 §6 chose
Razorpay first precisely because it is the constrained provider, and `payment/app/models.py` writes
the constraints into the schema:

- **A mandate has a cap.** `Mandate.max_amount_minor` — no single debit may exceed it. On UPI
  Autopay that ceiling is around ₹15,000 per cycle in practice; above it the customer must
  authenticate the debit by hand every time. A "retry" of such a debit is not a retry at all: it is
  a request for the customer to do something, and it fails until they do.
- **A mandate takes days to become debitable.** `Mandate.activated_at` is *"days after creation for
  a multi-day eMandate — the mandate exists but cannot be debited until the bank confirms it."* A
  brand-new customer's first failure is very often this and nothing else.
- **A debit needs advance notice.** e-mandate and UPI Autopay debits require a pre-debit
  notification to the customer ahead of the attempt. A ladder of retries an hour apart is not
  "aggressive collection"; it is a ladder of debits the rails will refuse.
- **There is no idempotency key.** `Charge.idempotency_key` is *"platform-supplied because Razorpay
  has no native one"*, unique per organization, and the create path inserts `ON CONFLICT DO
  NOTHING` against it. This has a consequence for retries specifically that is easy to get wrong:
  re-raising a failed charge under its original key is deduplicated into the charge that already
  failed. It looks like a retry, writes no row, calls no provider, and debits nobody.

Against that, the opposite failure is just as real and costs actual money: a grace window is
**unbilled service**. Every day a past-due customer keeps their product is a day the platform
delivers and does not collect, and a window generous enough to be kind is generous enough to be
farmed.

So the question this ADR settles is narrow and concrete: how many attempts, how far apart, how long
the customer keeps working, what they and the operator see at each step, and what the subscription
finally becomes if nobody ever pays.

---

## Decision

### 1. The ladder: three retries over twelve days, inside a fourteen-day window

A failed collection opens a **dunning cycle**. Everything that follows is a function of the instant
it opened:

| Moment | What happens | Subscription state | Entitled? |
|---|---|---|---|
| **T+0** — first failure | Cycle opens; grace window recorded; `commerce.subscription.past_due` | `active` → `past_due` | **yes** |
| **T+3d** | Retry 1 | `past_due` | yes |
| **T+7d** | Retry 2 | `past_due` | yes |
| **T+12d** | Retry 3 — the last automatic attempt | `past_due` | yes |
| **T+12d → T+14d** | Nothing. The deliberate quiet gap | `past_due` | yes |
| **T+14d** — window closes | `commerce.subscription.suspended` | `past_due` → `suspended` | **no — 402** |
| any time | A captured payment | → `active` | yes again |

**Why days and not hours.** The rails settle the spacing before taste gets a vote: a debit needs its
pre-debit notice a clear day ahead, a fresh eMandate is not debitable for days, and a debit over the
Autopay ceiling needs the customer to act. An hourly ladder would burn all three attempts inside a
window in which nothing *could* have changed, and each failed presentation is a sponsor-bank fee.
Days are the smallest honest unit here.

**Why 3, 7 and 12 specifically.** Day 3 is the first attempt that can plausibly succeed: it clears
the notice period with room, and covers the largest single class of failure — a temporary shortfall,
a bank-side decline, a mandate that activated a day late. Day 7 is a week on: the customer has now
had two notifications and a weekend, and this is the attempt that catches "I meant to fix the card".
Day 12 is the last automatic attempt, placed **two days before the window closes on purpose** — see
§3. Three attempts, not five, because after the third the failure is very rarely transient and each
further presentation costs a fee to learn the same thing.

**Why the window is 14 days.** It is half a monthly period, and that is the whole argument. The most
unbilled service a customer can accumulate is capped below one cycle, so the platform's exposure per
past-due subscription is bounded and knowable. A 30-day window on a monthly plan would let the *next*
invoice finalize before the previous one resolved, which means two dunning cycles for one
subscription and a ladder with no defined behaviour. Fourteen days makes overlapping cycles
structurally impossible rather than a case to handle.

The ladder is a `DunningPolicy` value in `subscription/app/domain.py` — a frozen dataclass validated
at import (offsets strictly increasing, all positive, the last one inside the window). It is one
constant, in one place, and a test drives the whole sweep through a compressed policy measured in
seconds. It is deliberately **not** per-plan or per-customer configuration: a customer-specific
grace window is a commercial promise, and commercial promises belong in the catalogue as a plan
version (ADR-0004 §2), not as a column somebody edits.

### 2. `past_due` means "still working, and we are trying"

`past_due` already existed as a state and as a legal edge from `active`; what it did not have was a
meaning. It has one now: **the product keeps working**.

This is not a new mechanism. `product-service`'s projection already maps `past_due` onto an
entitling activation — the mapping comment reads *"active, past_due, and the renew/upgrade/downgrade
moves that stay active"* — so the grace window rides the switch that already exists and no new gate
is invented (ADR-0004 §5). Only `suspended` and `cancelled` de-entitle, and only `suspended` is
reachable by this ladder.

A grace period that takes the product away is not a grace period; it is a suspension with a nicer
name and a worse audit trail. If entitlement stopped at T+0 there would be no reason to retry at all
— the customer's incentive to pay would already be maximal and the retries would be theatre. The
retries only make sense *because* the product is still working: they are the platform's attempt to
fix the problem before the customer has to notice it.

### 3. Suspension is a scheduled act, at the window's close and nowhere else

Suspension happens when `now >= grace_ends_at`, evaluated by a sweep, on a stored timestamp. Not on
a failure, not on the last retry, not on a counter reaching three.

Two boundaries could plausibly end a cycle — *the window closed* and *the attempts are exhausted* —
and this ADR makes the window dominate, with the ladder arranged so the second always happens first.
The last attempt lands at T+12 and the window closes at T+14, so **the outcome of the final attempt
is known while there are still 48 hours left**. That gap is the point. It is what makes the last
notification honest: "we could not collect; your access ends on Thursday" is a sentence the platform
can only send if it knows the answer and Thursday has not happened yet. Suspending the instant the
third attempt fails would save two days of unbilled service and make that message impossible to
send truthfully.

The reverse rule is not symmetric, deliberately: if the window closes early — an operator shortening
it, a policy edit, a clock — suspension happens regardless of how much ladder is left. The window is
the promise made to the customer; the ladder is only how the platform spends it.

**Every boundary is computed from stored timestamps.** The cycle stores `opened_at` (the anchor) and
`grace_ends_at` (the promise) at open, and every rung is `opened_at + offset`. Nothing is ever
`now + interval`. This matters more than it looks: a sweep that runs six hours late must not push
the remaining ladder six hours out, and a sweep that misses a day must not turn fourteen days of
grace into fifteen. Storing `grace_ends_at` rather than deriving it from the policy at read time is
the same discipline applied to the policy itself — editing the ladder is a reasonable thing to do,
and it must not move a window under a customer who has already been told when their access ends.

### 4. Recovery at any rung; the machine never cancels

**A captured payment returns the subscription to `active` from either rung** — from `past_due`
during grace, and from `suspended` after it. `suspended → active` is a legal edge and always was;
this is what it was for. The subscription keeps its id, its pinned plan version and its whole
transition history, so a customer who pays in week six is the same customer, not a new row that has
forgotten what they used to have.

Recovery is bound to **every** `commerce.payment.captured` for the subscription, not only to the
retries dunning asked for. Customers pay through links, through a re-run of the original invoice,
and through support; the platform must not keep a paid-up customer suspended because the money
arrived by a route it did not initiate.

**A subscription is never automatically cancelled.** The ladder ends at `suspended` and stops there,
however long that lasts. `cancelled` is terminal in this state machine — it has no outgoing edges,
by design — and an irreversible commercial act must not be taken by a timer. The cost is real and is
accepted: suspended-and-never-recovered subscriptions accumulate, and something eventually has to
look at them. That something is the operator console's list of long-suspended subscriptions and a
human decision, not a job. Reversing a wrong auto-cancellation means re-creating a subscription and
explaining a gap in the customer's history; reversing a wrong suspension is one payment.

### 5. What the customer and the operator see

The dunning facts ride **on the lifecycle events that already exist** rather than on a parallel
notification stream, because "tell the customer we will retry on Tuesday" and "keep their product
switched on" are the same fact arriving in two services, and two events would let one land without
the other. Every dunning transition carries a `dunning` block: the cycle id, the window, the next
attempt, attempts made and the ladder's length, and the amount.

| Step | Customer | Operator |
|---|---|---|
| Cycle opens | `commerce.subscription.past_due` → "we could not collect ₹X. We will try again on the 14th. Your access continues until the 25th." The product keeps working; `GET /subscriptions` returns a `dunning` block the SPA renders as a banner. | A `past_due` transition row, and an open cycle with its schedule |
| Each retry | The provider's own pre-debit notice, raised by payment when the charge is created | `commerce.subscription.payment_retry_due` on the stream; an audit row naming the attempt and its idempotency key |
| Last attempt fails | The 48-hour notice, with a date that is true | `attempts_made` equals the ladder's length, window still open |
| Window closes | 402 at the gateway, which the SPA already turns into an upgrade/fix-payment prompt rather than an error | `commerce.subscription.suspended`, reason `dunning_exhausted` |
| Payment captured | Access restored on the next token refresh (≤15 min — de-entitlement and re-entitlement are both eventual, ADR-0004) | `commerce.subscription.activated`, reason `dunning_recovered`; the cycle closes `recovered` |

The `dunning` block on the read API is the same stored schedule the sweep acts on, not a second
computation — so the banner and the sweep cannot disagree about when access ends.

### 6. The mechanism: a due-work table drained by a sweep, not a scheduler and not the broker

The retry schedule needs work that happens *at a time*. Three mechanisms were available:

- **`services/scheduler`** is an empty scaffold — no tables, no logic, not one caller. Making
  dunning its first customer would mean designing a general cross-service scheduler as a side effect
  of a billing decision, and betting the money path on something with no operational history. This
  is deferred rather than rejected: when a real scheduler exists, this loop becomes one of its jobs
  and the schedule does not move, because the schedule lives on the cycle row and not in the runner.
- **A delay on the broker** — a delayed-exchange plugin, or per-message TTL into a dead-letter
  ladder. Rejected: it puts the schedule inside RabbitMQ, where it cannot be queried, corrected, or
  explained. "When does my access end?" would be answerable only by draining a queue, an operator
  could not shorten or extend a window, and a broker that lost its durable store would silently
  forget every open grace window in the platform.
- **A due-work table drained on a poll**, which is what was built. `dunning_cycles.next_action_at`
  is an indexed column in Postgres: queryable, correctable, backed up with the money it defends, and
  restart-safe because the sweep holds no state of its own.

The sweep (`subscription/app/cron.py`, the `bin/cron` entrypoint's first real job) is deliberately
**the outbox relay's own shape**, because it is the same problem: discover work across every tenant,
claim a batch with `FOR UPDATE SKIP LOCKED` so replicas divide it safely, act, sleep. It uses two
engines, and the split is the decision:

- **Discovery** runs on the BYPASSRLS outbox role. Its query exists to find out *which*
  organizations have work due, so there is no tenant to bind while asking — exactly the relay's
  situation, and exactly the failure ADR-0002 records for it: as the app role it selects nothing,
  logs nothing, and every past-due subscription in the platform keeps its product forever. That
  silent stall is why the role is asserted at startup outside local. It is granted `SELECT` on one
  table and nothing else.
- **Every decision and every write** runs back on the NOBYPASSRLS app role inside
  `session_scope(organization_id=...)`, under the tenant policy. The bypass buys discovery, never
  authority.

The retry itself crosses services as an event, `commerce.subscription.payment_retry_due`:
subscription decides *when* to retry because it owns the lifecycle and the window; payment decides
*how* to debit because it owns the provider. It carries a platform idempotency key derived from the
cycle and the attempt number — `dunning:{cycle_id}:{n}` — which is not a detail: reusing the failed
charge's key would be deduplicated into the charge that already failed and debit nobody (§Context).
One key per attempt is what makes the ladder actually debit.

---

## What this rejects

- **Immediate suspension on the first failure** (the MVP default this replaces). On these rails the
  most common first failure is an eMandate that has not activated yet, a debit over the Autopay
  ceiling that needs the customer to authenticate by hand, or a bank-side decline — none of which
  is a customer refusing to pay. Suspending on the first of those is suspending a paying customer
  for the provider's latency, and the support cost of "why did my product stop?" exceeds the two
  weeks of service it saves.
- **A grace window that stops entitling.** Then it is a suspension with a nicer name, the retries
  are theatre, and the platform has taken the product away while still asking for money.
- **Hourly or minutely retries.** They are not legal on the rails this platform bills on, they cost
  a fee each, and they exhaust the ladder inside a window in which nothing could have changed.
- **Automatic cancellation after the window.** `cancelled` is terminal; a machine must not take an
  irreversible commercial action on a timer. The accepted cost is that suspended subscriptions
  accumulate and a human must eventually look at them.
- **A per-customer or per-plan grace window as configuration.** A different window is a different
  commercial promise, and ADR-0004 §2 already has the mechanism for a commercial promise made to one
  customer: a plan version. A `grace_days` column on a subscription is the "edit the plan for this
  customer" shortcut arriving through a side door.
- **Rescheduling the ladder on each new failure.** A cycle's schedule is fixed at open. Re-deriving
  it from the latest failure would let a customer's window be extended by exactly the failures it
  exists to survive.
- **Computing due moments as `now + interval`.** A sweep that runs late would push the remaining
  ladder out by however late it was, and fourteen days of grace would drift into twenty with nothing
  in the logs to show it. Every rung is anchored to the stored `opened_at`.
- **Putting the schedule in the broker**, and **building a general scheduler first** (§6).

## Consequences

- **The entitlement loop is unchanged; only its timing moved.** `payment.failed` →
  subscription state → `commerce.subscription.*` → activation → 402 is the same chain ADR-0004 §5
  describes and product-service already projects. No new enforcement point, no new claim, no change
  in product-service at all — `past_due` was already mapped to an entitling activation, and this
  ADR is the thing that finally makes that mapping reachable.
- **The platform now carries scheduled work.** `bin/cron` was a scaffold placeholder pointing at the
  relay; subscription is the first service to give it a job, and the first to run three distinct
  processes. That is one more thing to deploy, monitor and alert on, and its failure mode is quiet:
  a sweep that is not running does not error, it just never suspends anybody. The outbox-role
  assertion at startup catches the misconfigured half of that; the un-run half needs a liveness
  alarm like the relay's.
- **Unbilled service is now a deliberate, bounded cost.** Up to 14 days per failed subscription,
  capped below one monthly period by construction. That is a number the business can price, which
  the previous behaviour — zero days, and an unknown number of wrongly-suspended customers — was
  not.
- **`suspended` becomes a population, not an event.** Nothing cleans it up automatically, so the
  operator console needs a "suspended for more than N days" view and someone whose job it is to look
  at it. That work is deferred, and it is the honest cost of refusing to let a timer cancel.
- **Dunning state is queryable, which is the payoff of not using the broker.** "Which customers are
  past due, how far up the ladder, and when does each window close?" is one indexed `SELECT`, at any
  moment, including historically through the transition stream. An operator can shorten or extend a
  window by changing a row, and the sweep will honour it on its next pass.
- **The cycle table is mutable, and that is a deliberate exception worth naming.** ADR-0000 §9's
  append-only rules cover invoices, usage events and the credit ledger; a dunning cycle holds no
  financial fact — money is billing's, attempts are payment's — and it is the live aggregate the
  same way `subscriptions.state` is. The permanent record of what happened is where it already was:
  the append-only transition stream, the audit log and the outbox.

## Open, for later

- **Per-plan grace windows**, if a plan is ever sold with a contractual grace different from the
  platform default. The mechanism is settled (a plan version, §1); only the demand is missing.
- **Immediate de-entitlement on suspension.** Today it is eventual — a customer holding an access
  token keeps it until the token refreshes (≤15 min). ADR-0004 already records the permission-version
  bump as the immediate-cutover option; the trade-off is a global re-check on every suspension, and
  a two-week ladder makes fifteen more minutes a strange thing to optimise.
- **What to do with long-suspended subscriptions.** A console view and a human decision for now
  (§4); whether that ever becomes a governed, audited bulk action is a separate question.
- **Retrying against a different instrument.** The ladder retries the same mandate. Asking the
  customer for a new one mid-cycle, and retrying against that instead, is a payment-service and
  customer-experience question this ADR does not open.
