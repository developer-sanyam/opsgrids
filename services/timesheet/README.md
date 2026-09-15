# Timesheet service

Part of the OpsGrids platform. Owns the `timesheet` schema — PulseBoard's weekly time grid,
behind the gateway's `/api/v1/timesheets` prefix.

```bash
make run SERVICE=timesheet PORT=8141    # API
make worker SERVICE=timesheet           # event consumer
make relay SERVICE=timesheet            # outbox relay
```

## Owns

- `timesheets` — one person's week: Monday `week_start`, approval `status`
  (`draft|submitted|approved`, moved as a unit), and `capacity_minutes` — a snapshot of the
  person's weekly billable target, so a past week's utilization divides by what capacity was
  then. The person is the project service's roster person, carried as a plain UUID + ref.
- `timesheet_entries` — recorded slots: date, optional engagement code (`PB-01`, a plain
  string), `entered_minutes`/`billable_minutes` (integers — no float ever touches the
  arithmetic; the API serialises decimal hours). A CHECK keeps billable within entered.

`/timesheets` returns the SPA's grid rows (seven cells per person, day sums);
`/timesheets/summary` returns the week's entered/billable/capacity hours, the utilization
percentage and the utilization-by-week series.

## Publishes

- `pulseboard.time.timesheet.recorded` — a person's week was recorded or re-recorded.

## Consumes

Nothing yet.
