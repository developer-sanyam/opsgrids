# ADR-0015 — A scheduler service, and finance's `not_before` as its first consumer

Status: **accepted** · Date: 2026-09-23 · Amended 2026-09-23 (review)

## Context

`EventConsumer` (`libs/platform-core/src/platform_core/events.py`) redelivers a failed handler
once, immediately, and RabbitMQ has no notion of "not before T" on a message it has not yet
failed. Nothing in the platform can say "publish this later" — only "publish this now" (the
outbox) or "try this one more time, right now" (a nack).

Finance's e-Invoice auto-filing already wants the missing primitive. `request_filing`
(`services/finance/app/services.py`) stages `finance.compliance.invoice.filing_requested` with an
`attempt` and a `not_before` computed from `domain.filing_retry_delay(attempt)` — an exponential
backoff so a struggling IRP portal is not hammered. But nothing reads `not_before` before today:
the outbox relay publishes the row the moment it is due to be *sent* (immediately, since staging
and sending are the same transaction boundary as every other event), and the consumer
(`app/consumers.py:file_requested_invoice`) acts on it the moment it arrives. The backoff a retry
chose for a reason is currently theatre — the retry fires back-to-back with the attempt before it.
The backlog also names e-Way as wanting the same thing "more urgently — the goods are already
loaded" once its own automatic retry exists (it does not yet; e-Way filing today is manual-only,
see "Deliberately out of scope" below).

## Decision

### 1. A new, small service: `scheduler`

`scheduler` owns one table, `scheduled_jobs`, and answers exactly one question: *"deliver this
event, but not before T."* It has no HTTP surface beyond the scaffold's health/info routes —
nothing outside the platform calls it, so there is nothing to authorize beyond the platform's own
event contract, and CLAUDE.md's "prefer none" wins.

`scheduled_jobs` (schema `scheduler`, `TenantBase` — `id`, `organization_id`, `created_at`,
`updated_at`, `deleted_at`):

| column | purpose |
|---|---|
| `idempotency_key` | caller-supplied, unique with `organization_id`. The **domain**-level idempotency guard — event-id idempotency alone is not enough, the same lesson finance's own `live_registration` already teaches (two distinct event ids can ask for the same delayed delivery: a redelivered `platform.schedule.requested`, or a caller that asks twice for the same reason). |
| `due_at` | when the target event should be published. Indexed with `status` for discovery. |
| `status` | `pending` → `fired`, or `pending` → `parked` if firing keeps raising (§3). No `cancelled` in this slice — nothing needs to withdraw a schedule yet; straightforward to add. |
| `target_event_type`, `target_aggregate_type`, `target_aggregate_id`, `target_payload`, `target_actor` | the event to publish when `due_at` arrives — literally the arguments `publish_event` would have taken, deferred. |
| `requested_event_id` | the `platform.schedule.requested` event that created this row; becomes the fired event's `causation_id`, so the provenance chain survives the wait. |
| `fired_at`, `fired_event_id` | when it fired and what it produced, for support and the retention sweep below. |
| `attempts`, `last_error` | how many times *firing* this row has raised, and why, most recently. §3 below. |

### 2. The request travels as an event, the delivery travels through the shared exchange

A producer stages `platform.schedule.requested` on its **own** outbox — `finance`'s own outbox in
this case, not a call to the scheduler. `scheduler`'s consumer picks it up, parses it, and inserts
one `scheduled_jobs` row keyed on `(organization_id, idempotency_key)` with `INSERT ... ON
CONFLICT DO NOTHING` — belt over the event-id idempotency ledger every consumer already has.

When `due_at` arrives, `scheduler`'s own cron sweep republishes the *target* event on
**`scheduler`'s own outbox**, with `target_event_type` as the `event_type` and hence the topic
exchange routing key. The relay publishes it to `opsgrids.events` exactly as if the original
producer had published it directly, and the original producer's own queue binding (already bound
to that routing key, because it is that producer's own event) receives it — with no new binding,
no direct RPC from scheduler to finance, and no dependency of the scheduler on finance's queue
name. This is what makes the primitive generic: any service can ask for a delayed delivery of any
event type it owns, and `scheduler` never needs to know what the event means.

`platform_core.events.schedule_event` is the one function every future producer calls; it stages
`platform.schedule.requested` unconditionally; **the caller decides whether "now" is soon enough
to skip it** (finance's `request_filing` does this — see §4).

**Ownership, enforced at ingestion (review finding).** Scheduler stays blind to what an event
*means* — it never inspects `target_payload` beyond carrying it — but not to who may ask for one
delivered. `app/consumers.py:_rejection_reason` refuses a request whose `target_event_type` is not
namespaced under the requesting envelope's own `producer` (the same `product.module....`
convention every queue binding already trusts), and refuses one whose `target_payload` embeds an
`organization_id` that disagrees with the envelope's. Both are logged as
`scheduler.schedule_requested.rejected` and dropped, never raised — the same discipline a
malformed payload gets, because a rejected request does not become acceptable by redelivering it.
Without this, any producer could ask the scheduler to deliver an event under another service's
name, or address a different organization's aggregate than the one that asked — the scheduler has
no authorization layer of its own to catch that later, and by the time the fired event reaches its
real consumer it looks exactly like that consumer's own, trusted traffic.

### 3. Discovery is cross-tenant, action is per-tenant — the house pattern, a third time

Same shape as `approval/app/cron.py`'s SLA sweep and `subscription/app/cron.py`'s dunning sweep:

* **Discovery** (`app/selectors.py:due_jobs`) runs on `opsgrids_outbox` (BYPASSRLS), because
  finding out *which organizations* have due work has no tenant to bind while asking. Column-level
  `GRANT SELECT (organization_id, id, due_at, status, fired_at)` on `scheduled_jobs` — the
  discovery and retention reads' own columns only, nothing from `target_payload` — the same
  narrowing `approval/migrations/env.py`'s `grant_sla_sweep_reader` uses, so a bypass role that
  can find rows still cannot read what a document said.
* **Action** re-locks `SELECT ... FOR UPDATE SKIP LOCKED ... AND status = 'pending'` (**not**
  `due_at`, deliberately — `app/selectors.py:get_job_for_firing`'s own re-check is `status`
  alone) inside `session_scope(organization_id=...)` on the app role. Two concurrent drivers race
  the same row; the loser skips it. `due_at` needs no re-check because nothing ever mutates it
  after creation: if discovery's own `due_at <= now()` held a moment ago, it still holds now, on
  a clock that only moves forward — the re-check that matters, and the one the lock actually
  buys, is `status`, since that *is* mutated (by whichever driver fires it first).

No `claimed_at` lease column: the lock only needs to live as long as the one transaction that
both re-checks and fires the row, and that transaction is short (an `INSERT` into the outbox and
an `UPDATE` of one row) — a lease would add a column whose only job is to be reset if a firing
transaction dies, which `FOR UPDATE SKIP LOCKED` already handles by releasing the lock the instant
the transaction ends, committed or not.

**Parking a row that will not fire (review finding).** `fire_job` firing is expected to be
reliable — it is one `INSERT` and one `UPDATE` — but "expected" is not "guaranteed", and a row
that raises every tick forever is indistinguishable from a busy, healthy sweep unless something
counts it. `app/cron.py:Sweep._fire` catches an exception from `fire_job`, then — in a **fresh**
transaction, because the one `fire_job` ran in already rolled back — calls
`services.record_fire_failure`, which bumps `attempts` and `last_error` on the row and, once
`services.MAX_FIRE_ATTEMPTS` (10) is spent, flips `status` to `parked`. `due_jobs` only ever
selects `status = 'pending'`, so a parked row stops being retried and starts being what
`scheduler.job.parked` (logged at `error`) tells an operator to look at. Not yet auto-purged —
see §7.

**The sweep loop itself does not die on a failure (review finding).** `Sweep.run()` wraps its
call to `tick()` in the same shape `OutboxRelay.run()` wraps `_drain()`: an exception out of
discovery (a DB restart, a deadlock) is logged and backed off exponentially, never propagated.
This matters more here than it would alone, because production runs the sweep and the event
consumer in one process (`app/worker.py`, `asyncio.gather`) — an uncaught exception in either
would take both down. An idle tick (nothing due, nothing to purge) backs off the same way the
relay's idle poll does, capped at `max_idle_interval`, reset the moment either query finds
something; a productive tick sleeps not at all, draining a backlog at full speed exactly as the
relay does.

### 4. Finance: schedule instead of publish when `not_before` is in the future

`request_filing` now asks "is `not_before` after now?" instead of always publishing directly:

* **null or past `not_before`** — publishes `finance.compliance.invoice.filing_requested`
  directly, exactly as before. Byte-identical behaviour to today for the finalize trigger (which
  never sets `not_before`) and for a retry whose backoff has, for whatever reason, already
  elapsed by the time this transaction runs.
* **future `not_before`** — calls `schedule_event` instead, keyed
  `einvoice-filing:{invoice_id}:{attempt}`. `attempt` is already unique per invoice per chain
  (ADR: `request_filing`'s own docstring — the ledger's `attempt` rides the payload, not a count),
  so this key can never collide with an unrelated retry, and a redelivered
  `platform.schedule.requested` for the same retry lands on the same row twice and does nothing
  the second time.

### 5. Defence in depth: the consumer still refuses to act early, or on someone else's org

`file_requested_invoice` parses `not_before` out of the payload — as it always has, just unused
until now — and if it names a moment more than `domain.FILING_EARLY_ARRIVAL_TOLERANCE` (30
seconds) still in the future, does **not** call `autofile_invoice`. This should never happen in
normal operation (the scheduler is what is supposed to hold the message), so it is a guard
against an operator redelivering a message by hand, a stuck message replayed from the DLQ, or a
scheduler that fired early on a misconfigured clock. The tolerance exists so ordinary scheduling
latency between the scheduler's own clock and the pod running this consumer is never mistaken for
an early arrival — the guard is for a message arriving *meaningfully* early, not the last few
seconds a real dispatch can cost. Rather than silently ack-and-drop (loses the retry) or
nack-and-requeue (RabbitMQ has no delay of its own, so this hot-loops the exact bug the scheduler
exists to fix), it re-arms a fresh schedule for what remains and marks *this* message processed.
The re-armed schedule is keyed `einvoice-filing:{invoice_id}:{attempt}:early-arrival:{event_id}` —
not the original key, which already belongs to a job that has already fired (this message *is*
that firing) and would collide under the unique constraint instead of arming a new wait.

**The tenant is bound from the envelope, never the payload (review finding).** A
`filing_requested` message's *envelope* names an `organization_id` that is a column its
producer's own row-level security enforced when it staged the message — trustworthy by
construction, the same property every other consumer in this platform already leans on. Its
*payload* also carries an `organization_id`, because the payload is what gets embedded verbatim
in `scheduled_jobs.target_payload` and republished unchanged when a schedule fires — JSON along
for the ride, not something this consumer can prove nothing altered between staging and firing.
`file_requested_invoice` now compares the two before doing anything else, and refuses (logs
`finance.filing.organization_mismatch`, marks the message processed, acts on nothing) if they
disagree, rather than binding `session_scope` to whichever one a bug or a malformed message
happened to name. Under normal operation — direct publish or a schedule request scheduler's own
ownership check (§2) accepted — the two always agree, since both originate from the same
`ctx.organization_id` at `request_filing`'s own call site; this is the second, independent layer
behind that first one.

### 6. Delivery guarantees

* **At-least-once, once the request reaches `scheduled_jobs`.** A crash between a job firing and
  its relay row publishing cannot happen (same transaction); a crash between publish and
  `published_at` being recorded redelivers, and the target consumer is already idempotent (that is
  what "idempotent consumer" has meant everywhere else in this codebase from day one). This
  guarantee is scoped to *after* ingestion on purpose — see the guaranteed-routing paragraph below
  for the leg before it, which is not an ordinary outbox publish and needed its own fix.
* **Clock skew**: `due_at` is compared against `now()` read inside the discovery query, i.e. the
  database's clock — one clock, per ADR-0006 §8's own amendment, not the scheduler pod's. A pod a
  few seconds fast or slow changes nothing.
* **Scheduler downtime**: late, never lost. `scheduled_jobs` rows are durable, ordinary Postgres
  rows in the same cluster as everything else; a scheduler outage means jobs pile up `pending`
  past their `due_at` and fire in a burst the moment it comes back, in `due_at` order. Nothing
  times out and nothing is dropped. This is the same trade the outbox itself makes for every other
  event in the platform.

**Guaranteed routing for the request itself (review finding, fixed).** `platform.schedule.requested`
is not an ordinary outbox publish: it is meaningless if the scheduler's own queue is not yet bound
to it — the first deploy, or any restart window, where finance's `finance-worker` has started
publishing before `scheduler`'s consumer has finished declaring `scheduler.events` and binding it.
A topic-exchange publish with nothing bound to the routing key is, by default, still confirmed by
the broker: `OutboxRelay._drain` would mark the row `published_at` and the request is gone, with
nothing anywhere noticing — an accepted trade for ordinary events (a consumer that has not
deployed yet loses events it was never going to see, until it deploys), unacceptable for a
schedule request, whose entire purpose is that it must actually arrive. `OutboxRelay.run()` now
opens a **second** channel with `on_return_raises=True`, used only for event types in
`_GUARANTEED_ROUTING_EVENT_TYPES` (`platform.schedule.requested` by default); an unroutable
publish on that channel raises, which `_drain`'s existing exception handling already treats as a
failed publish — `attempts` bumped, retried next pass, nothing marked `published_at`. Scoped to
one channel and one small, named set of event types: every other event in the platform keeps
today's silent-drop-if-unrouted behaviour completely unexamined and unchanged — this is a
guarantee for the one event type that needs one, not a platform-wide policy change.
`infrastructure/edge/compose.prod.yml`'s `finance-worker` also gets `depends_on: scheduler:
{condition: service_started}`, which only orders container creation (`scheduler` has no
healthcheck to report readiness with) — the guaranteed-routing channel above is what actually
protects the gap between "the container exists" and "its consumer has finished binding its
queue", not this ordering.

### 7. Retention

A fired row is evidence — "this retry did happen, at this time, and here is what it published" —
and worth keeping for a support conversation for a while, not forever. The same sweep process
also runs a retention pass: cross-tenant discovery (`orgs_with_stale_fired_jobs`, same outbox-role
column grant) of organizations with `fired` rows older than the retention window, then a
per-tenant, per-org `DELETE ... WHERE id IN (...) AND organization_id = :org` (ids from a bounded
`fired`-and-`fired_at < :cutoff` read) under the app role — the same role split as firing itself,
so no new privilege is needed. The `organization_id` repeated on the `DELETE` costs nothing (the
selected ids already came from that organization) and is belt over the row-level security net
that already enforces it, per CLAUDE.md: "RLS enforces it as a net; do not treat RLS as
permission to skip the filter." `pending` rows are never touched by retention regardless of age;
only a fired job's own audit trail ages out.

**Actually configurable (review fix).** The window is `Settings.scheduled_job_retention_days`
(default 7), read by both `app/cron.py:main` and `app/worker.py:main` and passed to `Sweep` as a
constructor argument — an operator who needs a longer support-lookback window, or a shorter one to
shrink the table faster, changes an environment variable, not code. `services.SCHEDULED_JOB_RETENTION`
still exists as `Sweep`'s own default, for a caller (a test, `make cron` with no `.env` override)
that constructs one directly.

**Parked rows are not yet covered.** A `parked` row (§3) has no automatic cleanup path — it has no
`fired_at` to measure retention against, and accumulating a handful of permanently-poisoned rows
an operator needs to see is a different failure mode from a healthy backlog of fired evidence. Left
for whenever a real parked row shows up in production and an operator has an opinion about how long
to keep it visible.

## Deliberately out of scope

* **e-Way outage retries.** e-Way filing today is manual-only (`generate_eway_bill` /
  `cancel_eway_bill`); there is no automatic retry consumer analogous to `file_requested_invoice`
  for it to redirect through the scheduler. The backlog is right that it wants the same mechanism
  "more urgently" once that automatic retry exists — `schedule_event` is already general enough
  for whoever builds it to call directly, with no scheduler change needed.
* **Cancelling a scheduled job.** Nothing today asks to withdraw one; adding `status = 'cancelled'`
  and an event or internal call to set it is additive whenever something does.
* **A permission-gated HTTP surface for scheduled jobs** (e.g. an admin "what's still pending"
  view). Nobody consumes it yet, and CLAUDE.md's authorization rules apply the moment one exists;
  today the service has no data any person or product reads directly.
* **Making `scheduler` platform "core"** in `infrastructure/edge/compose.prod.yml`. Its only
  producer today is finance, so it ships behind the `finance` profile like finance itself,
  consistent with ADR-0013 §5's "the profile is the operational switch." This is a real coupling,
  named honestly: turning the `finance` profile off turns `scheduler` off with it, and the day a
  second producer needs the primitive from a different profile, `scheduler` has to move to core
  (or gain its own profile every product depending on it enables) before that producer can use it
  — a one-line compose change, not a redesign, but a real one, not a hypothetical.
* **Retention for `parked` rows.** No automatic cleanup path yet — see §7.

## Decisions on open questions (2026-09-24)

Two items above were left open rather than deferred with a trigger. Both are decided here.
Cancelling a scheduled job and an HTTP surface for scheduled jobs keep their "when something asks"
triggers. The register of every ADR question is
[open-questions-register.md](open-questions-register.md).

### Q1. What happens to `parked` rows — **DECIDED: kept until a person acts; alerted on; unparked or discarded by a command**

**Decision.** A parked row is **never deleted automatically**. It is the only evidence that a
delivery which should have happened did not. Three things apply to it:
1. **It alerts.** A count of `parked` rows above zero is an ops-monitor alert (**R7**, through
   C10), counted on the outbox role's existing column grant.
2. **A person resolves it** with a tool:
   `uv run python tools/scheduler_jobs.py {list-parked | unpark <id> | discard <id> --reason …}`.
   - `unpark` resets `attempts`, clears `last_error` and sets `status = 'pending'` with
     `due_at = now()`.
   - `discard` sets a new terminal status, `discarded`, with the reason. It never deletes the row.

   The tool runs on the app role under `session_scope(organization_id=…)`, one transaction per
   row, with an audit row each time. It follows ADR-0007 §3's CLI discipline.
3. **Retention treats `discarded` like `fired`.** It ages out after
   `scheduled_job_retention_days`, measured from the discard. `pending` rows are still never
   touched.

**Why.** Deleting a parked row silently loses a delivery nobody knows about. It might be a filing
retry, and a missed one ends in a portal deadline. But a row that stays parked for ever with no
alarm is the same loss, only slower.

**Rejected.** Auto-purging parked rows after N days. Auto-unparking on a timer: a row that raised
ten times in a row will very likely raise an eleventh, so a person should look first.

**Consequences.** One status value, one CLI and one alert. **Implemented by PLAT-E**: sweep
finding S18 ("parked after ~10 immediate retries, no backoff/unpark"), which also adds backoff
between fire attempts. The alert is **R7**.

### Q2. When `scheduler` leaves the `finance` profile — **DECIDED: in the same change that adds its first producer outside finance**

**Decision.** `scheduler` stays under the `finance` profile until a service outside that profile
calls `schedule_event`. **The change that adds that producer moves `scheduler` (worker and relay
drain) to the core profile in the same commit.** It also adds a `preflight.sh` check that refuses
a deploy in which an enabled profile's service calls `schedule_event` while `scheduler` is not
running.

The first candidates are:
- **L2**, the leave accrual cron, if it is built on `schedule_event`;
- **D4b**'s idea of moving per-service sweeps onto the scheduler. The D4b addendum left this
  undone for the same reason.

C4, the automatic e-Way retry, is a finance producer and does not trigger the move.

Notification's send retry (NOTIF-A) was the third candidate and **deliberately does not call
`schedule_event`**, for exactly this reason: it would have forced the move above into a defect
fix. It keeps its own clock instead — `messages.retry_at` and the `DispatchRecovery` sweep in
`notification-worker` (see the Engage design's §4). The trigger is unchanged: the first service
outside the `finance` profile that does call `schedule_event` moves `scheduler` to core.

**Why.** Today the coupling is harmless, because turning finance off also turns off the only
producer. The failure it hides is quiet. A producer in another profile would stage
`platform.schedule.requested`. The guaranteed-routing channel (§6) would then refuse it, since
nothing is bound, so the event would sit retrying in that producer's outbox, and the outbox-lag
alert would be the first sign.

**Consequences.** Moving to core costs one background container (priced in `memory-budget.json`).
That is why the move waits for a real producer. No ledger item exists for the move itself. It is
scope for whichever of L2 or D4b introduces the second producer, and both are named in the
register.

## Addendum (2026-09-24) — portal calls leave the transaction (D3)

Every compliance-portal call in finance now runs as **claim / call / record**: a short
transaction that refuses what the ledger can refuse and commits what a resumed attempt needs, the
portal call with no transaction open, and a second short transaction that appends the outcome.
Registration (round 1) writes no claim row — the IRN is a pure function of the document. IRN
cancellation, e-Way generation and e-Way cancellation do: `cancelling` / `submitting` rows on the
existing append-only ledgers, carrying the reason and actor or the transport facts. IMS sync
fetches with no transaction, then ingests (idempotent at the database on `(organization_id,
source_ref)`) and reconciles in one.

This touches the scheduler in exactly one place. A `pending` e-invoice row's retry is scheduled
here (§4), and finance may not read the scheduler's table, so the reclaim sweep (`app/cron.py`,
run inside `finance-worker` beside the consumer — `app/worker.py`) infers a *lost* job from time
alone: a `pending` row older than the longest retry it could have scheduled plus grace, with no
`filing_requested` staged since that is still backing off, is re-queued at the next attempt
number. A redundant re-queue is harmless (the consumer's claim asks `live_registration` first).
Re-queues are counted by the outbox rows they stage, so an invoice nothing ever answers — an
organization without a registration is excluded outright — ends at `MAX_FILING_ATTEMPTS` in a
terminal `retry_exhausted` row instead of looping. The same sweep resumes stale claims under a
bounded backoff, a lease (each re-drive appends a fresh claim row; `FOR UPDATE SKIP LOCKED` on the
invoice) and a poisoned-step guard (`MAX_COMPLIANCE_RESUMES` drives, then a terminal
`retry_exhausted` row). The "e-Way outage retries" item under *Deliberately out of scope* is
therefore partly answered: a `pending` e-Way row is now re-driven by the sweep, not by the
scheduler.

The states, who resumes what, and the provider-idempotency contract (`IrpAlreadyCancelled`,
`EwayAlreadyGenerated` keyed by `domain.eway_idempotency_key`, `EwayAlreadyCancelled`) are in
`docs/io-outside-transactions.md`, alongside the platform-wide instance table.
