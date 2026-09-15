# Standup service

Part of the OpsGrids platform. Owns the `standup` schema — PulseBoard's daily check-ins,
behind the gateway's `/api/v1/standups` prefix.

```bash
make run SERVICE=standup PORT=8142    # API
make worker SERVICE=standup           # event consumer
make relay SERVICE=standup            # outbox relay
```

## Owns

- `standup_entries` — one person's check-in for one day: yesterday, today, and `blockers`
  (null when nothing blocks — the flagged state the SPA renders is exactly non-null). One
  entry per person per day; reposting replaces. The person is the project service's roster
  person, carried as a plain UUID + ref.

## Publishes

- `pulseboard.standup.entry.posted` — a check-in was posted or reposted (payload flags
  whether it carries a blocker).

## Consumes

Nothing yet.
