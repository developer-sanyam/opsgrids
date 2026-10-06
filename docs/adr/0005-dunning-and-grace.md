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

## Decisions on open questions (2026-09-24)

The four items in "Open, for later" below are decided here. The list is left as written so the
history reads. The billing-run addendum at the end of this document (backlog R2) is in flight and
settles other questions: trial end, cadence, plan changes, the interim tax rule, and a narrower
recovery rule for an open window. Nothing below contradicts it. The register of every ADR question
is [open-questions-register.md](open-questions-register.md).

### Q1. Per-plan grace windows — **DECIDED: none; one platform ladder**

**Decision.** The ladder stays one platform constant (`DunningPolicy` in
`subscription/app/domain.py`). No plan version, subscription or customer carries a grace window.
If a signed contract ever needs a different window, it becomes a catalog **plan-version feature**,
`dunning_grace_days`, restricted to 7–14 days. The subscription reads it from the pinned version
**when a cycle opens** and stores the result in `grace_ends_at`, as today. It never goes above
half the billing period, which is what keeps cycles from overlapping (§1). The ladder's retry
offsets stay platform-wide.

**Why.** §1 already rejected a column on the subscription. A contract term is a commercial promise,
and ADR-0004 §2 puts those in plan versions. Until a contract asks, one constant is one thing to
reason about.

**Rejected.** A `grace_days` column on subscriptions or organizations: the side-door shortcut §1
names. A window longer than half a period: two cycles could then be open at once.

**Consequences.** Nothing is built. The trigger is a signed contract clause. When it arrives, the
work is one catalog feature key and one read at cycle open.

### Q2. Immediate de-entitlement on suspension — **DECIDED: stays eventual**

**Decision.** De-entitlement driven by a subscription (dunning suspension, cancellation, an
operator's suspend override) stays **eventual**. The gateway gates on the `ent` claim in the access
token (`platform_core/gateway.py`, the 402 check), so a suspension takes effect at the next refresh,
within 900 seconds. `product-service`'s projection does not bump `pv`, and this decision keeps it
that way.

**Why.** A dunning suspension comes after fourteen days of notices, and a cancellation is the
customer's own request. Fifteen more minutes costs nothing in either case. A `pv` bump is
per-organization (ADR-0007 §5, ADR-0008 §7), so bumping on every de-entitlement would force every
member of the tenant to refresh their token. It would also give product's consumer a Redis
dependency that fails loudly, on a path that works today without one. ADR-0000 §4's asymmetry
(revocations must be immediate) is about *authorization*. Being refused a product you stopped
paying for is commercial, and the data behind it is still the tenant's.

**Rejected.** Bumping `pv` on every entitling → non-entitling projection. Keeping a live
entitlement lookup at the gateway: that is a network hop on every request to save fifteen minutes.

**Consequences.** A stop that must take effect *now* — abuse, or a compromised tenant — uses the
authorization levers in account, not commerce: revoking the membership bumps `pv` and the refresh
then fails (`account/app/services.py`, `revoke_member`); the user-level kill is ADR-0008's Q2
decision. **Trigger to revisit:** an incident in which a subscription suspension had to take effect
within minutes. The fix then is a `pv` bump in `project_subscription` for reason `operator_suspend`
only. Nothing is built now.

### Q3. Long-suspended subscriptions — **DECIDED: an operator view plus a human, audited cancel; never automatic**

**Decision.** §4 stands: no timer ever cancels. The operator console lists subscriptions that
have been `suspended` for **30 days or more**. R2 adds `subscriptions.suspended_at`; the console's
projection keeps it per subscription, and `/admin/subscriptions` gains a
`suspended_for_days_gte` filter. An operator with the `owner` or `billing_ops` role (ADR-0003's
operator matrix) cancels **one subscription at a time** through a new `cancel` override kind. The
override is delegated to subscription-service's operator routes, like the existing `suspend`, and
writes a reason and an audit row. If a bulk clean-up is ever needed, it is a reviewed one-off
script with an explicit id list, in the same spirit as ADR-0007 §1's revocation rule. It is never
a scheduled job.

**Why.** Cancellation is terminal (§4). A wrongly cancelled subscription means recreating it and
explaining a gap in the customer's history; a subscription left suspended too long costs a row.
Thirty days is roughly one further billing period beyond the fourteen-day window, which is when a
human decision is actually warranted.

**Rejected.** Automatic cancellation after N days (§4). A bulk "cancel all long-suspended" button:
that is a machine decision with a human click on top.

**Consequences.** It needs a console filter, the `cancel` override kind and an operator cancel
route in subscription, which does not exist today. The billing-run addendum notes "there is no
operator cancel route to special-case". R2's 409 while billing lags applies to this route as it
does to a customer's cancel. **Not covered by any ledger item. New backlog item needed** (after R2
and Q1 merge).

### Q4. Retrying against a different payment instrument — **DECIDED: every debit uses the organization's current usable mandate**

**Decision.** A cycle's schedule never changes (§"What this rejects": no rescheduling). But the
**instrument is resolved when each debit is made, not pinned when the cycle opens.** Every charge
payment raises — a first attempt from `commerce.invoice.finalized`, a trial conversion, or a
`payment_retry_due` rung — debits the organization's **current usable mandate**. That is the most
recently activated mandate that meets the four conditions payment's charge guard and the billing
run's mandate question already use:
- its state is `active`;
- it is bank-confirmed (`activated_at` is set);
- it is in the charge's currency;
- its cap (`max_amount_minor`) covers the amount.

If a customer sets up a new mandate during a cycle, the next rung debits it with no other change.
With no usable mandate, the charge fails as `no_mandate` **without a provider call**, so no bank
fee is paid for an attempt that cannot succeed. A customer who wants to pay immediately uses a
payment link or checkout, and any captured payment for the window's invoice recovers the
subscription (§4, as narrowed by the billing-run addendum's review fix 3).

**Why.** Today `charge_on_dunning_retry` (`payment/app/consumers.py`) raises a charge that names no
mandate, and the billing-run addendum defers "attach the mandate to the charge". Resolving the
instrument at debit time is the smallest rule that makes "fix your card" work without moving the
schedule, and it keeps the choice of instrument inside payment, which owns the provider (§6).

**Rejected.** Pinning the mandate when the cycle opens: a customer who fixed their payment method
would still be retried against the broken one. Adding a rung or restarting the ladder when a new
mandate arrives: the window would stretch by exactly the failures it is meant to cover.

**Consequences.** Payment needs a "current usable mandate" selector, and every charge path must
record the mandate it used. This is covered by **R3** (mandates route and trial→active charge) and
**B5 (5g)** (live Razorpay recurring debit, which needs the mandate token on the charge).

## Open, for later (as written 2026-08-25; every item is decided in the section above)

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

---

## Addendum, 2026-09-23 — the billing run: trial end, recurring invoices, and plan changes

**Status: Accepted 2026-09-23.** Implemented in `subscription-service` (`app/billing_run.py`, the
`billing_periods` table, the new edges in `app/domain.py`), `billing-service`
(`POST /internal/v1/subscription-invoices`, `app/billing_run.py`), `metering-service` (the
internal per-period usage read) and `payment-service` (the mandate question, and no zero debits).

### The defect this closes

Nothing ever looked at `trial_ends_at` or `current_period_end`. Trials never converted: the
activation's read-time expiry switched the product off and no invoice was ever raised. Nothing
recurred: `current_period_end` passed and nothing happened. Billing's
`POST /internal/v1/invoices:finalize` had no caller anywhere in the repository, and billing's
README said so ("a billing run is a deliberately deferred concern"). This ADR's ladder was
therefore reachable only by an invoice somebody finalized by hand.

### What the earlier decisions already specified, and what this addendum had to choose

ADR-0013 §6 had specified trial end and nothing else: *trials that have ended with no mandate move
to `past_due` and ADR-0005's grace window starts; a trial that ends with a mandate is charged and
becomes `active`.* ADR-0004 and this ADR specified neither the invoicing cadence nor what a plan
change does to money. The rules below implement §6 as written and choose the rest; each choice is
the conservative one and each is reversible without a data migration.

### 1. Trial end (ADR-0013 §6, implemented as specified)

- A trial whose `trial_ends_at` has passed is invoiced for its **first paid period, in advance**,
  and the period opens **at `trial_ends_at`**, never at the moment the sweep noticed — the same
  stored-timestamp rule §3 applies to the ladder.
- If the invoice total is zero, or payment answers that a mandate can collect it — `active`,
  bank-confirmed (`activated_at` set), same currency, cap covers the amount; exactly the four
  conditions payment's charge guard already enforces — the subscription moves
  **`trial → active`**, reason `trial_convert`, `commerce.subscription.activated`.
- Otherwise it moves **`trial → past_due`**, reason `trial_ended_unpaid`, and a dunning cycle opens
  **anchored at `trial_ends_at`** carrying the invoice id and amount: the product keeps working for
  §1's fourteen days, the ladder asks payment to retry *that invoice*, and a captured payment
  recovers it exactly as §4 says. A mandate over its cap counts as no mandate — on UPI Autopay
  that debit needs the customer to act (Context above).
- Nothing is ever cancelled by this (§4). Two edges are added to the state machine:
  `trial → past_due`, and `past_due → past_due` (a renewal while a window is open). Because an
  unpaid trial takes a different edge with a different reason, ADR-0004 §4's trial-conversion
  metric (`trial → active` over trial starts) does not count it as converted.

### 2. The billing run: when a period is invoiced, and for what (chosen)

- **Recurring prices (`flat`, `per_seat`) bill in advance** for the period that opens; **usage
  prices bill in arrears** for the period that has just closed. A period is invoiced when it
  opens, which is also what keeps §1's "fourteen days is half a period, so cycles never overlap"
  true: the failure that opens a window lands at the start of a period.
- **Trial usage is free**: the first invoice after a trial carries no usage window.
- **Period length is what `subscribe` always used** — 30 days for `month`, 365 for `year` — now
  recorded as `subscriptions.billing_interval` at subscribe and change-plan (and read off the
  period's length for rows older than the column). Calendar-month anchoring is a separate
  decision, not taken here. `one_time` plans have no period and are **not billed by the run**;
  one due is reported as a failure, never invoiced by guess (none is sold today).
- **Every boundary comes from the stored one**: a period opens at the previous period's end, one
  period per pass. A run that was down for two months issues two invoices with two keys, oldest
  first — it does not collapse them or re-anchor to `now`.
- **Which subscriptions**: `active` and `past_due` are invoiced (both are served — §2).
  `suspended` **rolls its period without an invoice**, so a customer who pays in week six resumes
  on a current period rather than being billed for the months they were switched off.
  `cancelled` is never touched. A **bundle is one subscription and one invoice**, whatever it
  entitles.
- **Line items** come from the **pinned** plan version's own prices, read by billing from
  catalog — the caller never sends an amount: `flat` is `amount × quantity`, `per_seat` is
  `amount × seats` from `subscription_items`, `usage` is `Money.rate(unit_price_micros, billable
  quantity)` rounded half-up once per line. Integer minor units throughout, in the subscription's
  currency; a price in another currency, or an item that names a price not on the pin, is refused
  rather than skipped.
- **Usage** is metering's **billable** quantity (`is_billable = true`) per `meter_key` for the
  organization over `[usage_start, usage_end)`. Per organization is per subscription, because a
  meter belongs to one product and an organization holds one subscription per product. Usage that
  arrives after its period was invoiced is not billed (see Deferred).

### 3. Exactly once: claim → call → record (chosen)

No network call runs inside a database transaction, so the run cannot "lock, call, write" in one
transaction. Instead:

1. **Discover** on the outbox role (BYPASSRLS; it now also has `SELECT` on `subscriptions` and
   `billing_periods`, granted in `migrations/env.py` beside `dunning_cycles`), ids only.
2. **Claim** on the app role under the tenant: `FOR UPDATE SKIP LOCKED` on the subscription,
   re-decide from its stored timestamps, and write or re-find a `billing_periods` row —
   `UNIQUE (organization_id, subscription_id, period_start)` — snapshotting the pin and the items,
   with a 10-minute lease.
3. **Call**, no transaction open: billing prices and finalizes the invoice under the key
   `subscription:{id}:{period_start}` (a partial-unique index, `ON CONFLICT DO NOTHING`, so a
   repeated or racing ask returns the one invoice and stages one `commerce.invoice.finalized`);
   for a trial conversion that owes money, payment answers the mandate question.
4. **Record** on the app role under the tenant: write the answer, advance the period, make the
   transition with its outbox event — one transaction.

A kill between 2 and 4 leaves a `pending` row; after the lease the next pass sends the identical
request and gets the same invoice back. A failed call keeps the claim, records `last_error`, and
backs off (5 minutes doubling to 6 hours). A claim whose subscription moved on before the record
(cancelled mid-flight) is closed `superseded` and logged for a person, because billing may already
hold an invoice for it. Only `subscription` may ask billing for a billing-run invoice, only
`billing` may read metering's usage, only `subscription` may ask payment the mandate question —
each route checks the verified caller as well as the service token.

### 4. Plan changes mid-period: upgrades prorate now, downgrades wait (revised 2026-09-24)

ADR-0004 gives billing "proration" as a responsibility and billing's `app/domain.py` has an exact
`prorate`, but no ADR said when money moves on a change. *The first version of this section chose
"everything from the next period", which left an upgrade unbilled for the rest of the period; the
review replaced it with the rule below.*

- **Only a trial or an active subscription changes plan** — an explicit check in
  `_change_plan`, not left to the state machine, because `past_due -> past_due` is now a legal
  edge for the renewal and would otherwise let a customer who owes money move up a plan.
- **One cadence for life** (round 2): a change to a version with a different billing interval, or
  to `one_time`, is refused with a 422 — cancel and subscribe again. Monthly and yearly periods do
  not line up, so no proration can price one against the other honestly.
- **Direction is per day, in integers**: `new × old_days` against `old × new_days` (30 for a month,
  365 for a year). Greater is an upgrade, less a downgrade, **equal moves no money** — re-pinned now,
  no proration, usage so far still closed at the old pin.
- **On a trial with nothing claimed**, either direction re-pins immediately and moves no money.
  A trial whose conversion is already claimed is priced like an active subscription (round 2, B2).
- **An upgrade** (the new version's recurring list price per day above the old one's) re-pins and
  re-entitles immediately, and **charges now the prorated difference for the rest of the current
  period**: a `proration` claim over `[change, period end)` as a fraction of the whole period,
  billed by the run like any other claim. Billing prices both versions from catalog, prorates each
  full-period recurring price by whole seconds remaining with `prorate` (largest remainder,
  integer minor units), and bills the positive difference — never a credit — with the same tax
  rule as §5. The key is `subscription:{id}:proration:{change_id}`: one upgrade, one invoice.
- **A downgrade** takes effect **at the next period start**: the version, its cadence, its items
  and its members are pinned as `pending_change`; nothing is emitted and entitlement does not
  change now. The run applies it when it claims the next period — that transition emits
  `commerce.subscription.downgraded` — and prices that period at the lower plan. Changing back to
  the current pin withdraws it; an upgrade supersedes it. *(Amended by ADR-0029, 2026-10-06: the
  event is labelled by the direction stored with the pending change — a staff change scheduled for
  the next period that raises the price emits `upgraded` when applied. A change scheduled before
  that keeps `downgraded`. Every consumer folds the two identically.)*
- **Up then down within one period**: the upgrade's proration stands and is billed; the downgrade
  waits for the next period like any other.

### 5. Tax (chosen; ADR-0004 "Open: Tax" is still open)

Jurisdiction resolution is still unsettled, so the interim rule is one deployment-wide choice:
billing's `BILLING_TAX_CODE` (and optional `BILLING_TAX_JURISDICTION`) names the `tax_rates` row;
the rate **in force on the period's start date** applies (versioned by `effective_from`, so a
re-run uses the period's own rate); tax is **exclusive**, computed **once on the subtotal** with
`Money.percent_bps` (half-up) and written as its own line, so the lines still foot. Unset means no
tax line. Set with no rate in force, the run **refuses** to invoice and reports it failing,
because an untaxed invoice under-collects a statutory amount.

### 6. Observability

`billing_run.pass` (issued / activated / past_due / renewed / skipped / failed counts, logged when
a pass did anything), `billing_run.recorded` and `billing_run.failed` per subscription, and
`GET /health/billing-run` on subscription-service: `overdue`, `pending`, `failing`,
`oldest_pending_seconds`, `invoiced_24h`, `skipped_24h`, `last_recorded_at` — counts only, counted
on the outbox role so a stall cannot hide its own backlog. **Alert on `overdue > 0`**: a stopped
run does not error. The edge runbook has the procedure. (Review fixes add `failing_permanent`
and `void_pending`; see below.)

Because nothing ever billed before, the first passes on a box with history convert every expired
trial and invoice every overdue period, one period per pass. The run therefore has a kill switch
(`BILLING_RUN_ENABLED`, **off by default** since the review) and a preview
(`python -m app.billing_run --dry-run`, writes nothing), and the runbook stages a first deploy
through both.

### Deferred, deliberately

- **Credit and prepaid packs are not drawn** by the run; an invoice is raised at full price and a
  pack (commerce-packaging.md) must still be applied by hand.
- **payment does not attach the mandate** to the charge it raises on `commerce.invoice.finalized`;
  the stub accepts that, a live Razorpay recurring debit will need the token. A payment-slice
  change.
- **The three-day `commerce.subscription.trial_ending` notice** ADR-0013 §6 also names is not
  emitted yet.
- **Cancel at period end** has no API (cancellation is immediate), so there is nothing for the
  run to honour; when it exists, the run's claim is where it is checked.
- **Late usage** (recorded after its period was invoiced) is never billed; a watermark or a grace
  before closing the usage window would fix it.
- **Dunning a second invoice**: a renewal invoiced while a window is open keeps that window's
  schedule, and the ladder retries the first invoice only.
- **A meter priced by both a bundle and a standalone subscription** for the same organization
  would be billed by both; no seeded catalog version does this today.
- **Refunds and credit notes** are unchanged by this addendum.


### Review fixes, 2026-09-24

The first billing run was reviewed before merge; these are the decisions that came out of it,
each implemented with a test that fails without it.

1. **A conversion charge that fails before the record step is not dropped.** Billing's
   `commerce.invoice.finalized` can reach payment, and payment's failure reach subscription, before
   the run's record step commits — while the subscription is still `trial`, which the failure
   handler used to ignore. The failure is now written onto the pending `trial_conversion` claim
   (`payment_failed_at`, reason); the record step then takes `trial -> past_due` with a dunning
   cycle carrying that invoice, whatever payment said about the mandate. If the conversion was
   already recorded `active`, the ordinary failure path opens the window.
2. **No head-of-line blocking.** Discovery excludes (`NOT EXISTS`) any subscription whose lifecycle
   claim is leased or backing off (`next_attempt_at`, now a column apart from the lease), and
   orders the rest by when each next becomes workable. A `one_time` cadence is a persisted
   failure row (permanent, backed off), not a log line rediscovered every pass.
   `/health/billing-run` gains `failing_permanent` (a data defect, or five failures): alert on it.
3. **`past_due` cannot change plan** (see §4). **A capture closes the open window only when it is
   for the window's invoice** (`capture.invoice_id == cycle.invoice_id`): money for another invoice
   settles nothing the window is about. *This narrows §4 above ("recovery is bound to every
   capture") for an open window; recovery from `suspended`, with no open window, is unchanged.*
4. **Upgrades prorate now; downgrades wait** — §4, rewritten.
5. **Closing usage.** A cancellation or a suspension claims a usage-only `close` period
   `[period start, stop)` — key `subscription:{id}:{period_start}:close` — billed by the run with no
   recurring lines (those were billed in advance). `usage_billed_through` then moves to the stop,
   so the renewal after a recovered suspension bills usage from there and never twice.
6. **Kill switch off by default** — `config.py`, `compose.prod.yml`
   (`${BILLING_RUN_ENABLED:-false}`), documented commented-out in `.env.example`, and preflight warns
   whenever it is not `true`.
7. **Two clocks.** Due-ness is decided against the pass's `now`; leases, backoff and `recorded_at`
   use the wall clock.
8. A failure report is written only while the claim is still work (`pending` / `void_pending`).
9. Every lock and billing's key lookup filter `organization_id` explicitly, beside RLS.
10. **The outbox role reads columns, not tables**: on `subscriptions` the id, organization, state,
    trial end and period end; on `billing_periods` the ids, kind, state, lease, backoff, attempts,
    error flags, invoice id and timestamps the discovery and the probe read. Never an amount, a
    pin or an item. (The one-line CLAUDE.md rule naming what `opsgrids_outbox` may read is left
    for the owner to update — agents do not edit it.)
11. **Superseded invoices are voided.** A period the subscription moved past between claim and
    record — or a stale claim whose subscription moved on — goes to `void_pending`, and the run
    asks billing's `POST /internal/v1/subscription-invoices:void` to void whatever stands under the
    claim's key (void is a state on the frozen invoice, never a delete; it stages
    `commerce.invoice.voided`). The void is made immediately after the record, but payment may
    already have raised a charge from the finalized event; payment does not yet consume `voided`
    (deferred, below).
12. **Invoice numbers cannot wedge a claim.** Numbers derive from a hash of the whole key (a
    period, its proration and its close differ), and if a hand-finalized invoice holds the number
    the next suffix is used.
13. **The dry-run** splits periods owed into invoiced and suspended-roll, and counts trials whose
    grace window — anchored at the trial's end — has already expired.

Added to *Deferred*: payment consuming `commerce.invoice.voided` to cancel an unsettled charge for
a voided invoice. *(Round 2's finalize-after-record makes this moot for the billing run: a
superseded period is now a voided draft that was never announced.)*

### Review fixes, round 2 (2026-09-24)

- **B1 — one cadence.** See §4: a different interval or `one_time` is refused (422); direction is
  compared per day with integer cross-multiplication.
- **B2 — changes against a claim.** When a renewal or a trial conversion is claimed and not yet
  recorded, an upgrade is prorated against **the claim's** period `[change, claim end)` from the
  pin it moved off, and the record step checks the claim's pin against the subscription's and
  claims the same proration again under a change id derived from the claim and the new pin — a
  unique index makes that a no-op when the change-plan path already did it. A downgrade scheduled
  after a claim is applied only when a period **not yet claimed** is claimed.
- **M3 — usage at the old pin.** Whenever a paid subscription's pin changes, usage up to the change
  is closed at the old pin: an immediate change claims `close [floor, change)`; a deferred downgrade
  claims `close [floor, period end)` before it is applied, and that renewal carries no usage window.
  An equal-price switch closes usage too, and raises no proration.
- **M4 — stops against a claim.** A closing window starts at the latest of the period start,
  `usage_billed_through`, and the usage end of any lifecycle claim already made. A claimed renewal
  is never pre-empted by a suspended roll; if the subscription was suspended between claim and
  record, the renewal's draft is voided (no advance charge for an unserved period), the period
  still rolls, and the usage it carried is re-claimed as a closing period of its own — the same
  happens to any superseded renewal. Cancel, suspend and change-plan take the subscription row
  lock the run's claim and record take. Any failure while billing a subscription backs its pending
  claim off.
- **M5 — finalize after record.** Billing raises a run invoice as a **draft**, with no event.
  Subscription records the claim (`finalize_pending`), then — a fourth step, keyed and idempotent —
  asks `POST /internal/v1/subscription-invoices:finalize`, which moves `draft -> finalized`, freezes
  the invoice and stages `commerce.invoice.finalized` exactly once (`finalized`). The freeze
  trigger already allowed a draft to take lines and change; it freezes at finalize as before.
  A superseded or stale period is therefore a **draft** voided — as a state, with an audit row and
  no event — before anything could charge it. Customers never see a draft. Discovery picks up
  `finalize_pending` with the same lease and backoff as a call; `/health/billing-run` counts it.
- **m6** — the column-grant hook grants only columns the current revision has, so a partial
  downgrade works. **m7** — a trial conversion is stamped with a failed charge only when the failure
  names the conversion's own invoice. **m8** — a proration that nets to nothing writes no invoice.
- **m9 — deferred:** a closing-period charge that fails after the customer cancelled is not dunned
  (there is nothing left to suspend). It is stamped on the closing period and counted by
  `/health/billing-run` as `close_charges_failed`; collecting it is a person's decision.
- **Worker supervision.** The worker's loops are supervised (`app/supervise.py`): a sweep pass that
  raises is logged and retried with capped exponential backoff, a dunning cycle whose action raises
  is pushed back 15 minutes and the pass carries on, and any loop that raises **or returns** stops
  the process non-zero, cancelling the others — no zombie worker without its payment consumer.
  `CancelledError` always propagates.

### Review fixes, round 3 (2026-09-24)

- **B1 — a proration belongs to its claim.** A proration raised while a renewal or trial
  conversion is claimed and unrecorded records that claim (`against_period_id`, a plain uuid in
  the same schema). It is not billed until the claim is **finalized**; and whatever moves the
  claim to `void_pending` or `skipped` (a cancel, a suspension the claimed period was not served
  into, a stale or superseded claim) moves its prorations too: pending → `skipped`
  (`parent_voided`), a recorded draft → `void_pending`. A finalized proration on a voided parent
  cannot arise; if it ever does it is logged and counted (`prorations_on_voided_parent`).
- **B2 — the last pin is the baseline.** The record step's backstop prices a missed change from
  the **last pin already prorated against the claim**, not the claim's own pin, and does nothing
  if the subscription is on that pin. An equal-price switch against a claim writes a settled
  marker row (`skipped`, `equal_price`, never billed) so the last pin is always known; an
  equal-price switch after an upgrade therefore charges exactly one proration, whichever door
  (customer or operator) it came through.
- **M3 — no money decisions while the run lags.** A customer or operator **plan change** is
  refused **409 "billing is catching up on this subscription — try again shortly"** while a period
  end or trial end is due and unclaimed, or while a claim is pending for a period that has already
  ended. The run claims within one pass, so the wait is minutes. While `BILLING_RUN_ENABLED=false`
  it means plan changes on every overdue subscription are refused — the runbook's enable step says
  so. We chose refusal over "claim first, then change" for the operator too: it is one rule in one
  place. *(Amended by the final review: a customer cancel is **not** refused — see below.)*
- **Opening charges (found by the property test).** A subscription that started `active` without
  a trial was never charged its first period: the run charged the *next* period in advance at each
  roll, and nothing charged the first. It now claims an **opening** charge at subscribe —
  `[start, period end)` at the pin, billed by billing as a proration from nothing (keyed on its own
  change id). The same kind charges the rest of a period on **resumption** after a suspension that
  crossed a roll, when no base charge covers the resumption instant. Existing subscriptions are not
  back-charged.
- **A suspension that came after the period opened does not make it free.**
  `subscriptions.suspended_at` records when service stopped (an operator's suspension now; a
  dunning suspension at its grace end). A claimed or due period that opened **before** that instant
  is charged in advance like any other — the run being late to roll it must not make it free —
  and a period that opened after it is voided or skipped as before (`served_into`).
- **m4** — billing reads the invoice under its key `FOR UPDATE` (with `populate_existing`) for
  finalize and void: two racing finalizes serialise, the second answers `(invoice, False)`.
  **m5** — every `*_for_update` selector in subscription sets `populate_existing`. **m6** —
  `failing` / `failing_permanent` count claims in `pending`, `finalize_pending` and
  `void_pending`; billing's `/health/billing-drafts` counts drafts, and drafts older than an hour.
  **m7** — a failed charge on a close, proration or opening row of a cancelled or suspended
  subscription is stamped on that row and counted (`close_charges_failed`,
  `proration_charges_failed`); the dead conversion branch in `record_payment_failure` is gone.
  **m8** — the model's state comments name every state; the runbook covers the `f5a7b9c1d3e6`
  downgrade leaving billing drafts un-finalized.
- **Deferred, deliberately:** a **cancel** against a claim whose period opened a little before the
  cancel (the run was late) skips the claim — the served time between the period start and the
  cancel is not charged. It is bounded by the run's lateness, it favours the customer, and
  charging a full period in arrears to someone who has just cancelled is worse than the leak. A
  pro-rata charge for that sliver is the right fix and is not built.
- **The property test.** `services/subscription/tests/test_billing_run_properties.py` drives a
  subscription through random sequences of trial, upgrade, downgrade, equal-price switch,
  suspension, resumption, failed and recovered payments, crashes between every step of the run,
  billing outages and passes of the run, on a controlled clock, then cancels it and lets the run
  catch up. Seeds are deterministic (`BILLING_PROPERTY_SEEDS`; 25 by default since the final review). Over what billing
  finalized: base charges never overlap, never start before the paid start or at/after the
  cancellation, and cover every served instant; usage windows never overlap, stay inside paid
  time and cover every served instant; each is priced at the pin in force at its start and no pin
  change falls inside a usage window; nothing is charged on a voided or skipped parent and no
  finalized invoice is voided; no upgrade is charged twice; totals are integers. On the round-2
  head it failed 171 of 200 seeds.

### Final review (2026-09-24)

- **Blocker — a payment is not a key to every suspension.** Both suspension paths close the cycle
  first, so a capture on a suspended subscription used to find no open cycle and resume it — any
  capture: the run's closing-usage invoice, raised *by* the suspension and captured moments later,
  lifted an operator's fraud hold, and a customer dunned for a ₹1,000 invoice resumed by paying a
  ₹40 usage invoice. Now, on `suspended` with no open cycle, a capture resumes only if the last move
  into `suspended` was dunning's (written by the sweep's service actor, read off the transition
  log — amended below: the actor, not the reason) **and** the
  latest cycle closed `grace_expired` for exactly the captured invoice. Anything else logs
  `dunning.capture.no_resume` and moves nothing. **An operator's suspension is never lifted by a
  payment**; there is no resume route yet, so a person lifts it with a reviewed data change.
- **Major 2 — a cancel is never refused.** With the run off by default, M3's refusal would have
  answered 409 to every cancel in production. A customer cancel always succeeds; a period that was
  due and unclaimed at the cancel is never opened, and the run bills the cancellation's closing
  usage only. (The served time since that period's boundary is not charged — the same deliberate,
  customer-favouring deferral as round 3's late-run cancel.) Plan changes keep the 409.
- **Major 3 — the billing cutover.** `BILLING_RUN_NOT_BEFORE`, a timezone-aware instant,
  **required** when `BILLING_RUN_ENABLED=true`: the worker refuses to start the run's loop without
  it (`billing_run.refused`; the payment consumer and dunning keep running), and preflight fails.
  The operator sets it to the moment they turn the run on and never moves it later. Chosen:
  * recurring money — a renewal, a trial conversion, a proration, an opening — for a period that
    **began before** the cutover is never invoiced: the row is written or settled `skipped` with
    outcome `pre_cutover` and the period rolls. An existing subscription starts paying at its
    first period that begins after the cutover; the period straddling it is free;
  * a trial that **ended before** the cutover converts to `active` with no invoice, no mandate
    question and no dunning (the conversion's `activated` event says `invoiced: false`); its first
    paid period opens at the trial's end and is that free straddling period. A trial ending after
    the cutover converts and is dunned exactly as before;
  * **no usage from before the cutover is billed**: a renewal's usage window starts no earlier
    than it, a closing window straddling it is billed from it (only the usage window moves — the
    row's `period_start`, billing's key, stays), and one that ended by it is skipped. We clamp
    rather than skip straddling usage because usage after the cutover is consumption billing is
    live for;
  * `python -m app.billing_run --dry-run [--not-before …]` splits each due subscription's periods
    into `periods_pre_cutover` / `periods_invoiced` / `periods_suspended_roll`, and reports the
    backlog of rows that are their own work by kind (`opening`, `close`, `proration`,
    `finalize_pending`, `void_pending`) with how many would be invoiced, clamped or skipped.
- **A stop while the run lags applies a lapsed downgrade first** (found by the property test once
  dunning suspensions joined it). A deferred downgrade takes effect at the first unclaimed
  boundary, which the run applies when it claims that roll. If the run is behind and the
  subscription is cancelled or suspended (by an operator, or by dunning at its window's end)
  after that boundary, the stop's closing usage ran across it at the old, dearer pin. The stop now
  applies the downgrade at the boundary first: usage to it closes at the old pin, the rest at the
  new one, and later rolls price at the new pin as they would have.
- **Minors.** Billing's drafts probe reads `invoices(state, created_at)` only — `id` and
  `organization_id` are revoked on every migrate. The property test tolerates only the refusals a
  customer can meet (`BillingCatchingUp`, and 409/422/403 problems) — anything else fails the
  seed; it now suspends through dunning as well as by an operator, checks every resumption against
  the rule above, and checks that every charged proration prices from the pin in force just
  before it. It runs 25 seeds in the ordinary suite; `make test-billing-properties` runs 500
  (nightly, and by hand before changing the run). The runbook notes that a permanently failing
  claim freezes its subscription's plan changes (409) once its own period has ended too, until a
  person fixes it — `failing_permanent` is the alert.

### Final money review, second fixup (2026-09-24)

- **Major — the payment handlers lock first.** `record_payment_captured` and
  `record_payment_failure` are handed a subscription the consumer read unlocked, and every other
  writer (cancel, suspend, change-plan, the run's claim and record) locks the row before reading
  it. Three races followed: a fraud hold racing a capture of the owed invoice ended `active`; a
  cancel of a dunning-suspended subscription racing a capture ended `active` with `cancel_at`
  set; a cancel racing a failure ended `past_due` — dunning a cancelled customer. Both handlers
  now take the row lock (`populate_existing`) as their first statement and decide from what the
  last committed writer left. The **dunning sweep** locked the cycle, then the subscription — the
  reverse of a cancel, and a 40P01 deadlock when they met; it now locks the subscription (skip
  locked: a held row waits for the next pass), then the cycle. Every path is subscription →
  cycle. (This predates the billing run; it was found by its review.)
- **Which suspension is in force is read from the transition's actor** — the sweep's service
  actor — never from its reason, which is free text an operator supplies. An operator may not use
  a reason only the machinery writes (`dunning_exhausted`, `grace_expired`, `payment_failed`, …):
  422. `suspended -> suspended` is illegal, so a fraud hold cannot be layered on a dunning
  suspension; paying the owed invoice lifts it (the runbook says what to do).
- **The cutover setting refuses bad values softly.** Accepted, exactly:
  `YYYY-MM-DDTHH:MM[:SS[.f]]` then `Z` or `±HH:MM`. pydantic would read `0` or `2026` as epoch
  seconds — 1970, back-billing everything — and a hard validation error would crash-loop the whole
  worker, taking the payment consumer and dunning with it. So anything else (and an impossible
  date) is dropped with its reason kept (`billing_run_not_before_invalid`), the settings load, and
  the run's loop refuses to start (`billing_run.refused`). Preflight checks the same pattern, that
  GNU date reads it as a real instant, and warns when it is in the future; the runbook's enable
  step runs preflight and prints the parsed setting before recreating the worker.
- **A closing invoice is dated, taxed and numbered on its usage window.** A close bills usage
  only; its invoice period, the day the tax rate is read and its number now come from the usage
  window, which differs from the claim's start only when the cutover moved it. Taxing on the
  pre-cutover day found no rate when the rate row starts at go-live, and failed the close
  permanently. The key still names the claim, so a retry lands on the same invoice.
- **The cutover decides a row once**, before its first lease: a proration, opening or close
  already sent to billing is retried exactly as sent — never skipped or re-clamped — so the retry
  lands on the invoice billing priced.
- **A resumption inside the straddling period charges no opening** — that period is free, known
  from the configured cutover or from a `pre_cutover` row covering the instant.
- **The reviewer's randomised cutover variant is permanent** (cutover 0-60 days into each life,
  run on throughout or off until the cutover; 25 seeds by default, 500 by
  `make test-billing-properties`), as are its edge probes: an opening or a trial end exactly at the
  cutover is billed, moving the cutover earlier re-bills nothing, a future cutover rolls once and
  waits, a close retried after the cutover moved later keeps its first window.
- **Known limitation — moving the cutover later.** Documented, not detected: rows not yet handled
  that begin between the old and the new cutover become free while the same kind handled earlier
  was invoiced. A probe field was considered and not built — the health probe runs in the API
  process, which does not carry the cutover, and the column it would need is not in the outbox
  role's grant.
- **Open:** an internal operator **resume** route. Until it exists, lifting an operator's
  suspension is a reviewed one-off that must write the state, the transition row, the audit row,
  the `activated` outbox event (or product-service never re-entitles) and the opening for the
  rest of the period — all listed in the runbook.
