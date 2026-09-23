# ADR-0015 — A scheduler service, and finance's `not_before` as its first consumer

Status: **accepted** · Date: 2026-09-23

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
| `status` | `pending` → `fired`. No `cancelled` in this slice — nothing needs to withdraw a schedule yet; straightforward to add. |
| `target_event_type`, `target_aggregate_type`, `target_aggregate_id`, `target_payload`, `target_actor` | the event to publish when `due_at` arrives — literally the arguments `publish_event` would have taken, deferred. |
| `requested_event_id` | the `platform.schedule.requested` event that created this row; becomes the fired event's `causation_id`, so the provenance chain survives the wait. |
| `fired_at`, `fired_event_id` | when it fired and what it produced, for support and the retention sweep below. |

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

### 3. Discovery is cross-tenant, action is per-tenant — the house pattern, a third time

Same shape as `approval/app/cron.py`'s SLA sweep and `subscription/app/cron.py`'s dunning sweep:

* **Discovery** (`app/selectors.py:due_jobs`) runs on `opsgrids_outbox` (BYPASSRLS), because
  finding out *which organizations* have due work has no tenant to bind while asking. Column-level
  `GRANT SELECT (organization_id, id, due_at, status, fired_at)` on `scheduled_jobs` — the
  discovery and retention reads' own columns only, nothing from `target_payload` — the same
  narrowing `approval/migrations/env.py`'s `grant_sla_sweep_reader` uses, so a bypass role that
  can find rows still cannot read what a document said.
* **Action** re-locks `SELECT ... FOR UPDATE SKIP LOCKED ... AND status = 'pending' AND due_at <=
  now()` inside `session_scope(organization_id=...)` on the app role. Two concurrent drivers race
  the same row; the loser skips it. The re-check under the lock (not trusting discovery's
  possibly-stale read) is what makes firing exactly-once even though discovery ran moments
  earlier against a slightly stale snapshot.

No `claimed_at` lease column: the lock only needs to live as long as the one transaction that
both re-checks and fires the row, and that transaction is short (an `INSERT` into the outbox and
an `UPDATE` of one row) — a lease would add a column whose only job is to be reset if a firing
transaction dies, which `FOR UPDATE SKIP LOCKED` already handles by releasing the lock the instant
the transaction ends, committed or not.

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

### 5. Defence in depth: the consumer still refuses to act early

`file_requested_invoice` parses `not_before` out of the payload — as it always has, just unused
until now — and if it names a moment still in the future, does **not** call `autofile_invoice`.
This should never happen in normal operation (the scheduler is what is supposed to hold the
message), so it is a guard against an operator redelivering a message by hand, a stuck message
replayed from the DLQ, or a scheduler that fired early on a misconfigured clock. Rather than
silently ack-and-drop (loses the retry) or nack-and-requeue (RabbitMQ has no delay of its own, so
this hot-loops the exact bug the scheduler exists to fix), it re-arms a fresh schedule for what
remains and marks *this* message processed. The re-armed schedule is keyed
`einvoice-filing:{invoice_id}:{attempt}:early-arrival:{event_id}` — not the original key, which
already belongs to a job that has already fired (this message *is* that firing) and would collide
under the unique constraint instead of arming a new wait.

### 6. Delivery guarantees

* **At-least-once**, same as every other event in the platform. A crash between a job firing and
  its relay row publishing cannot happen (same transaction); a crash between publish and
  `published_at` being recorded redelivers, and the target consumer is already idempotent (that is
  what "idempotent consumer" has meant everywhere else in this codebase from day one).
* **Clock skew**: `due_at` is compared against `now()` read inside the discovery query, i.e. the
  database's clock — one clock, per ADR-0006 §8's own amendment, not the scheduler pod's. A pod a
  few seconds fast or slow changes nothing.
* **Scheduler downtime**: late, never lost. `scheduled_jobs` rows are durable, ordinary Postgres
  rows in the same cluster as everything else; a scheduler outage means jobs pile up `pending`
  past their `due_at` and fire in a burst the moment it comes back, in `due_at` order. Nothing
  times out and nothing is dropped. This is the same trade the outbox itself makes for every other
  event in the platform.

### 7. Retention

A fired row is evidence — "this retry did happen, at this time, and here is what it published" —
and worth keeping for a support conversation for a while, not forever. The same sweep process
also runs a retention pass: cross-tenant discovery (`orgs_with_stale_fired_jobs`, same outbox-role
column grant) of organizations with `fired` rows older than `SCHEDULED_JOB_RETENTION` (7 days,
configurable), then a per-tenant, per-org `DELETE ... WHERE status = 'fired' AND fired_at < :cutoff
LIMIT :batch` under the app role — the same role split as firing itself, so no new privilege is
needed. `pending` rows are never touched by retention regardless of age; only a fired job's own
audit trail ages out.

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
  consistent with ADR-0013 §5's "the profile is the operational switch." The day a second
  producer needs it, moving it to core (alongside account/product) is a one-line change, not a
  redesign.
