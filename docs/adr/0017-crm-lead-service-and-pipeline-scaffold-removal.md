# ADR-0017 — CRM greenfield: `lead` joins the `crm` product; `pipeline` and `activity` are removed

Status: **accepted** · Date: 2026-09-23

Builds on [ADR-0000](0000-phase-0-decisions.md) §10 (no cross-schema foreign keys, `org_id` a
plain UUID) and the existing `crm` product split between `customer` (the account book) and
`deal` (the pipeline and its activity timeline).

## Context

`docs/ecosystem-gaps.md` §2 audited the CRM story and found `lead` and `pipeline` both still the
generated 187-line scaffold, while `customer` (companies, contacts) and `deal` (deals, an
append-only activity timeline, `crm.pipeline.*` events) are real and already wired: `deal`
publishes `crm.pipeline.deal.created`, `crm.pipeline.deal.stage_changed` and
`crm.pipeline.activity.logged`; `customer` binds the last of those in `app/consumers.py` to
advance `contacts.last_activity_at`. `compose.prod.yml`'s own comment already calls `lead`,
`pipeline` and `activity` "helper services that are still scaffolds" and deliberately leaves them
out of the `crm` profile. ecosystem-gaps' recommendation is explicit: build `lead`, delete
`pipeline` — "two services claiming one bounded context is how a second source of truth is born."

Two onboarding documents disagree with that plan and with each other's premise, and both predate
it: `docs/onboarding/cards-dev-a-lead.md` and `cards-dev-b-pipeline.md` describe a five-card
fortnight in which Dev A builds `services/lead` and Dev B builds `services/pipeline` as two
*new* halves of a product the cards call `salescrm` — a separate product from `crm`, with its own
manifest (`libs/contracts/permissions/salescrm.json`), its own role templates
(`SALESCRM_ADMIN`/`SALESCRM_MEMBER`), and its own pipeline tables (`pipelines`,
`pipeline_stages`) duplicating what `deal` already owns. Neither card's author appears to have
known `customer` and `deal` existed as real services already serving `crm.book.*` and
`crm.pipeline.*` — the cards read as written against an empty `services/` directory.

Building `salescrm` as written would create exactly the second source of truth ecosystem-gaps
warns against — a second pipeline, a second product manifest with its own admin role, a second
entitlement (`salescrm`) an organization would have to buy alongside `crm` for features that
already exist under `crm` — and would leave `deal`'s already-published `crm.pipeline.*` events
and `customer`'s already-registered `crm` scope resolver stranded.

## Decision

**Ecosystem-gaps wins.** Concretely:

1. **`lead` is built**, as its own service, but as a third process in the existing `crm`
   product — the same shape `customer` and `deal` already share (`PRODUCT = "crm"` in its own
   `app/catalogue.py`, contributing the `crm.lead.*` module to the one `crm` manifest, the same
   way `customer` contributes `crm.book.*` and `deal` contributes `crm.pipeline.*`). There is no
   `salescrm` product. Card A4's `SALESCRM_LEADS`/`SALESCRM_ADMIN`/`SALESCRM_MEMBER` block is not
   used; `lead` instead adds `CRM_LEADS_ROLE_TEMPLATES` following `deal`'s and `customer`'s
   `CRM_*_ROLE_TEMPLATES` naming, and updates the existing `CRM_BOOK_ADMIN` / `CRM_PIPELINE_ADMIN`
   pattern with a `CRM_LEADS_ADMIN`, while `CRM_SALES_REP` (customer) and `CRM_DEAL_REP` (deal)
   each pick up the lead grants a working rep needs.
2. **`services/pipeline` and `services/activity` are deleted outright**, not built. `deal`
   already owns `crm.pipeline.*` (deals and the activity timeline) end to end, migrated,
   RLS-enabled, catalogued and tested. `cards-dev-b-pipeline.md` is stale from this decision
   forward.
3. Both onboarding cards get a banner at the top pointing here, rather than being silently left
   to mislead the next person who opens them looking for onboarding work.
4. **`deal.app.domain.STAGE_ORDER` stays hardcoded** (six fixed stages, `services/deal/app/
   domain.py:38`). A configurable, per-organization pipeline — which is what
   `cards-dev-b-pipeline.md`'s `Pipeline`/`PipelineStage` tables were actually reaching for — is
   a real, larger feature (multiple named pipelines, stages an org edits) and is explicitly
   **out of scope for this slice**. It is recorded here as accepted debt rather than silently
   dropped: a future slice that wants configurable stages should extend `deal`'s own schema
   (`deal.pipelines` / `deal.pipeline_stages`, same schema as `deals`, no cross-schema key),
   never resurrect a separate `pipeline` service.
5. **No schema was ever created for `pipeline` or `activity` in any environment reachable from
   this repository.** Both services' `migrations/versions/` hold only the generator's own
   `.gitkeep`; `compose.prod.yml` never enabled them (see the "helper services... deliberately
   absent" comment predating this ADR) and no `opsgrids_pipeline`/`opsgrids_activity`-owned
   schema was ever migrated against a shared dev or prod database from this codebase. There is
   therefore **no manual `DROP SCHEMA` step** for this change — `RUNBOOK.md` is not updated with
   one, because there is nothing to drop. If a fork or a long-lived local database *did* run
   `alembic upgrade head` against either service's empty initial migration, the same manual
   cleanup applies as any deleted service: `DROP SCHEMA IF EXISTS pipeline CASCADE;` /
   `DROP SCHEMA IF EXISTS activity CASCADE;` as the Postgres superuser, followed by
   `DROP ROLE IF EXISTS opsgrids_pipeline;` / `DROP ROLE IF EXISTS opsgrids_activity;` — worth
   stating here even without a runbook entry, since nothing else in the repository will say it
   once the migrations are gone.

## What is removed, and where

Both services are deleted wholesale (`services/pipeline/`, `services/activity/`), plus every
place that enumerated them:

- `tools/dev-bootstrap/init-db.sql` — the `opsgrids_pipeline` / `opsgrids_activity` roles and
  their two `GRANT USAGE`/ownership list mentions.
- `tools/shard-weights.txt` — their weight rows (`shard.sh` itself partitions
  `services/*/` directly and needs no edit).
- `services/app-gateway/routes.json` — the `/api/v1/pipelines` and `/api/v1/activities` route
  entries, and the `salescrm`-product `/api/v1/leads` entry is repointed to `crm` (see below).
- `infrastructure/edge/compose.prod.yml` and `infrastructure/edge/memory-budget.json` — neither
  ever listed them (see point 5), so nothing to remove there; the comment naming them as
  "deliberately absent" scaffolds is updated since `lead` is no longer one of the three.
- `infrastructure/edge/routes.prod.json` — never listed them either; `lead` is added (below).
- `EDGE_RELAY_SCHEMAS` (`compose.prod.yml`) and `SERVICES_WITH_SCHEMA`
  (`infrastructure/edge/remote.sh`) — neither ever listed `pipeline`/`activity`/`lead`; `lead` is
  added to both by this slice.

`lead` is added, minimally, everywhere a live service is enumerated: `SERVICES_WITH_SCHEMA`,
`EDGE_RELAY_SCHEMAS` (it has an outbox but no consumer, so it needs the shared `edge-relay`
drain, not a dedicated relay container), `tools/dev-bootstrap/init-db.sql`'s `opsgrids_lead` role
(already present on this base branch), `services/app-gateway/routes.json` and
`infrastructure/edge/routes.prod.json` under the `crm` product, `compose.prod.yml`'s `crm`
profile (one `api` container — no dedicated worker; conversion is synchronous and lead consumes
nothing), and `memory-budget.json`.

## `customer-worker`

Unrelated to the pipeline decision but found while auditing the `crm` profile for this slice:
`services/customer/app/consumers.py` binds `crm.pipeline.activity.logged` to advance
`contacts.last_activity_at`, and the code has existed since `deal` started publishing that event
— but no `customer-worker` container was ever added to `compose.prod.yml`. The consumer has never
run in production; the outbox message is published, drained by `edge-relay`, and delivered to a
queue nothing consumes, so `contacts.last_activity_at` has been silently stale since `deal`
shipped. This slice adds `customer-worker` (`./bin/worker`, i.e. `python -m app.consumers`) to
the `crm` profile and to `memory-budget.json`.

## Consequences

- The `crm` product now has three contributing processes (`customer`, `deal`, `lead`) instead of
  two, each still validated at import by its own `catalogue.validate()` — a wrong-product grant
  in `lead`'s templates fails the same way it would in `customer`'s or `deal`'s.
- `docs/onboarding/cards-dev-a-lead.md` is followed for cards A1–A3 and A5 (the table, RLS,
  endpoints, tests) essentially as written, since those cards do not depend on the `salescrm`
  premise. Card A4 (permissions) is *not* followed as written; see decision point 1.
  `cards-dev-b-pipeline.md` is not followed at all.
- Memory budget: `lead` (api, +85 MB) and `customer-worker` (background, +42 MB) bring the
  full-profile expected total from 3,462 MB to 3,589 MB against a 3,800 MB reference host —
  91.1% to 94.4% of the hard threshold. Still under the hard 100% gate
  `tests/infra/test_compose_memory_budget.py` and `preflight.sh` enforce, but tight enough that
  the next CRM addition to this box should measure before assuming there is headroom.
