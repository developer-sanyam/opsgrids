# Project service

Part of the OpsGrids platform. Owns the `project` schema — PulseBoard's engagements and board,
behind the gateway's `/api/v1/projects` prefix.

```bash
make run SERVICE=project PORT=8140    # API
make worker SERVICE=project           # event consumer
make relay SERVICE=project            # outbox relay
```

## Owns

- `people` — the delivery-team roster (`ref` `p01`…, name, role, location, weekly
  `capacity_hours`). The timesheet and standup services carry a person's UUID as a plain
  column; the roster is the authority on who that is.
- `projects` — client engagements: `code` (`PB-01`), client, `status`
  (`on_track|at_risk|blocked|done`), progress, dates, `budget_minor`/`spent_minor` in an
  ISO currency (integer minor units), owner, billable flag, open-work headline.
- `tasks` — board cards: `key` (`PB-231`), stage (`backlog|in_progress|review|done`),
  assignee, priority, hour estimate, tags, due date.

`/projects/summary` derives the Overview's engagement-side rollups (active projects, tasks due
this week, health breakdown, needs-attention) from these tables; the time-side rollups live on
the timesheet service.

## Publishes

- `pulseboard.engagements.project.created` — a new engagement entered the portfolio.
- `pulseboard.board.task.created` — a new card landed on the board.

## Consumes

Nothing yet.
