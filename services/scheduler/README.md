# Scheduler service

Part of the OpsGrids platform. Owns the `scheduler` schema.

```bash
make run SERVICE=scheduler PORT=8110    # API (info/health only — see "Owns")
make worker SERVICE=scheduler           # event consumer alone
make cron SERVICE=scheduler             # the due-job sweep alone
make relay SERVICE=scheduler            # outbox relay
```

Production runs the consumer and the sweep **together**, in one process (`bin/worker`, i.e.
`app/worker.py`) — see ADR-0015 for why. `make worker`/`make cron` above still run them apart,
which is what a test or a local debugging session wants.

## Owns

`scheduled_jobs` — the delayed-delivery primitive (ADR-0015): "deliver this event, but not
before T." One row per outstanding (or recently fired) request, keyed
`(organization_id, idempotency_key)` so a redelivered or duplicate request produces exactly one
row. No HTTP surface beyond the scaffold's `/scheduler/info` — nothing outside the platform calls
this service; it is driven entirely by events.

## Publishes

* The **target event** named in a fired job — literally whatever event the original producer
  asked to have delivered later, republished unchanged with the producer's own `event_type` as
  the routing key. Consumers of that event type see it exactly as if the producer had published
  it directly; they do not know or care that scheduler was involved.

## Consumes

* `platform.schedule.requested` — a producer's own request to hold an event until `due_at`.
  Maintains `scheduled_jobs`. See `platform_core.events.schedule_event`, the function every
  producer calls to emit this.

## First consumer

Finance's e-Invoice auto-filing retries (`services/finance/app/services.py:request_filing`): a
retry's exponential backoff (`domain.filing_retry_delay`) now actually waits, instead of the
retry being redelivered back-to-back with the attempt before it. See ADR-0015.
