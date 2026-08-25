# Marketing service

Part of the Atmora platform. Owns the `marketing` schema — campaigns, audiences and the
attribution rollups behind the Marketing product's Overview, Campaigns, Channels and Audiences
screens.

```bash
make run SERVICE=marketing PORT=8134    # API  (gateway routes /api/v1/marketing here)
make worker SERVICE=marketing           # event consumer
make relay SERVICE=marketing            # outbox relay
```

## Owns

- **`campaigns`** — one marketing campaign: channel (`email`/`social`/`search`/`events`), status
  (`draft`/`live`/`paused`/`ended`), budget / spend / attributed revenue / generated pipeline (all
  integer **minor units** of an ISO currency), qualified leads and won conversions, the target
  audience, flight window and owner.
- **`audiences`** — a saved segment a campaign can target: name, reachable size and a one-line
  criteria summary. "Used by N campaigns" is derived from the campaign list, never stored.
- **`revenue_spend_points`** — the org-wide monthly revenue-vs-spend series behind the Overview
  chart, a maintained rollup keyed by `(organization_id, position)`.

ROAS, cost-per-lead and CAC are **never stored** — they are pure functions of the amounts and
counts above (`app.domain`), computed in one place so the campaign row, the channel rollup and the
Overview headline cannot disagree.

### Endpoints (all under the `/api/v1/marketing` gateway prefix)

| Method & path | Returns | Backs |
| --- | --- | --- |
| `GET /campaigns` | `Page[Campaign]` | Campaigns table; Overview & Channels derive from it |
| `GET /campaigns/{id}` | `Campaign` | Campaign detail |
| `GET /campaigns/{id}/performance` | `RevenueSpendPoint[]` | Campaign detail weekly chart |
| `GET /audiences` | `Page[Audience]` | Audiences screen |
| `GET /analytics/revenue-vs-spend` | `RevenueSpendPoint[]` | Overview chart |
| `GET /overview` | `OverviewSummary` | Overview KPIs + series + channel breakdown (server rollup) |
| `GET /channels` | `ChannelPerformance[]` | Channels table (server rollup) |
| `POST /campaigns` | `Campaign` | Create a campaign |
| `POST /audiences` | `Audience` | Create an audience |

Reads go through `selectors` (scope enforced by `ctx.apply`), writes through `services`
(transaction + audit + outbox). Every route declares its permission and is gated by
`require_product("marketing")`.

## Publishes

- `marketing.analytics.campaign.created` — a campaign was created; carries id, name, channel,
  status and the target audience id.
- `marketing.analytics.audience.created` — an audience segment was created; carries id, name, size.

## Consumes

_None yet — this service is the authority for its own data and reads nothing from others._
