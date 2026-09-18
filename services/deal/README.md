# Deal service

The pipeline half of the **CRM** product ("OpsGrids Revenue"). Owns the `deal` schema:
deals moving through the six-stage pipeline and the append-only activity timeline. Serves
the `product-crm` SPA together with customer-service, whose companies, contacts and reps
these rows point at by plain UUID — never a cross-schema foreign key.

```bash
make run SERVICE=deal PORT=8131        # API (the gateway routes /api/v1/deals here)
make worker SERVICE=deal               # event consumer (none registered this slice)
make relay SERVICE=deal                # outbox relay
```

## Scope of this slice — reads first

The CRM SPA is read-only today (no create affordances), so the HTTP surface is the reads
it renders. The write path (`services.create_deal/move_stage/log_activity`) exists to
finalise the domain rules — stage-transition legality, the staleness clock, the
append-only timeline — and to drive the demo seed through the real, RLS-bound path; it is
not yet exposed as public routes (the finance-service precedent).

## Owns

- **`deals`** — one opportunity each: `value_minor` (integer minor units + ISO currency,
  no floats), stage `lead | qualified | proposal | negotiation | closed_won |
  closed_lost`, owning rep, probability, expected `close_date`, and `last_activity_at` —
  the staleness clock, advanced whenever the timeline is written. Stage changes go
  through `services.move_stage`, which enforces `domain.can_transition`: open deals move
  freely between open stages and close either way; **a closed deal is a record and moves
  nowhere**.
- **`activities`** — the timeline (call | email | meeting | note | task), **append-only**:
  a database trigger refuses UPDATE and DELETE, so who touched a deal and when is a
  record, not a draft. An entry usually names a deal and may also name a company/contact
  (plain UUIDs across the schema boundary).

Every table carries `organization_id` with an RLS policy (enable + FORCE, USING and
WITH CHECK on the tenant GUC); `processed_events` is deliberately unpolicied (no
organization column — a policy would silently defeat consumer idempotency).

## Endpoints (`/api/v1/deals/...`)

Match the SPA's `client.ts` call shapes (its `/activities` call maps onto
`/deals/activities` — the timeline lives with the pipeline on the prefix already routed):

| Method & path | Returns | Permission |
| --- | --- | --- |
| `GET /deals?q=&stage=&owner_id=` | `Page<Deal>` | `crm.pipeline.deal.read` |
| `GET /deals/summary` | `Summary` | `crm.pipeline.deal.read` |
| `GET /deals/activities?deal_id=&company_id=&contact_id=&type=` | `Page<Activity>` | `crm.pipeline.activity.read` |
| `GET /deals/{id}` | `Deal` | `crm.pipeline.deal.read` (+ object-level scope) |

`/deals/summary` is the Overview rollup — open pipeline value, weighted forecast, open
deal count, win rate over decided deals, won value, activities in the trailing seven
days, the by-stage funnel, and the pipeline-over-time series. The headline figures are
computed live from the deals and activities with the same arithmetic as the SPA's
`data/derive.ts`; the trend's earlier months are representative history mirroring the SPA
samples (the seam a reporting service replaces — the finance-service precedent), with the
final point always the live open pipeline. Every route also requires the `crm` product
entitlement.

## Permissions and roles (`crm` product, pipeline slice)

OWN means "the deals I run" (the owner column is the rep id); TEAM and LOCATION are not
offered. This service declares the `crm.pipeline.*` slice of the CRM vocabulary;
customer-service declares `crm.book.*` in its own process — each process owns the
catalogue for the permissions it serves, exactly as the HRMS services do.

- `crm.pipeline.deal.read` / `crm.pipeline.deal.write`
- `crm.pipeline.activity.read` / `crm.pipeline.activity.write`

Role templates: **CRM_PIPELINE_ADMIN** (everything at ORG) and **CRM_DEAL_REP** (default:
reads at ORG — sales works one shared board — writes at OWN).

## Publishes

- `crm.pipeline.deal.created` — a deal entered the pipeline.
- `crm.pipeline.deal.stage_changed` — a deal moved (from → to).
- `crm.pipeline.activity.logged` — a touch was logged; carries `contact_id`, the natural
  feed for customer-service's `contacts.last_activity_at`.

## Consumes

Nothing yet.

## Demo seed

`tools/demo/seed_crm.py` seeds **both** CRM services and mirrors the SPA fixtures exactly
(18 deals across all six stages and 20 timeline activities — plus the book in
customer-service) into the "OpsGrids Demo" org via the real write paths, cross-linked by
deterministic UUIDs. Idempotent. `DATABASE_URL` must point at `opsgrids_wt`.
