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

## Known limitations (recorded during security review, not fixed here)

- **`customer.contacts.email` stays plaintext.** `lead.leads.email` is sealed, masked and
  blind-indexed (a lead is unvetted, self-reported intake); once a lead converts, its email
  reaches `customer.contacts.email` as plaintext business contact data, matching every other
  contact created directly through `POST /contacts`. This is a deliberate downgrade in
  protection at the moment of conversion, not an oversight — `customer`'s own model docstring
  already records the reasoning ("work email stays plaintext: it is business contact data the
  book exists to show, not personal PII"). Sealing a contact's email end-to-end is a `customer`
  schema change out of scope for this slice; noted here because conversion is the one path
  that visibly narrows a field's protection, and the next person touching this deserves the
  explicit callout rather than finding it by diffing columns.
- **The PII blind-index key is platform-wide, not per-tenant.** `platform_core.pii.blind_index`
  derives its key from the single `PII_ENCRYPTION_KEY` passphrase every service shares — the
  same key that already seals and indexes `customer.contacts.phone_index`. A blind index leaks
  *equality* by design (`platform_core/pii.py`'s own docstring says as much for phone), and
  because the key is not scoped per organization, two different tenants sealing the same email
  or phone number produce the same index value — indistinguishable from either tenant's own
  point of view (RLS still isolates the rows), but a break of the shared key exposes every
  tenant's equality graph at once rather than one. This is an existing platform-wide property
  `lead`'s email index inherits, not something introduced by this slice; a per-tenant HKDF
  salt on top of the shared passphrase would close it and is a `platform_core.pii` change, not
  a `lead` one.
- **A company name's case-insensitive uniqueness is enforced by the application, not the
  database, and only going forward.** `customer.companies` kept its original, case-sensitive
  unique index (`uq_companies_org_name`) rather than replacing it with one on `lower(name)` —
  a database that already holds two companies differing only by case ("Acme Ltd" and "ACME
  LTD") would fail that migration outright, and there is no data to silently merge or delete on
  someone else's behalf. `services.create_company`'s `pg_advisory_xact_lock` (keyed on the
  organization and the lowercased name) is what refuses a new case-insensitive duplicate now;
  an organization that already has one from before this existed keeps it, and
  `get_company_by_name`'s lookup resolves it deterministically to whichever row is older.
  Reconciling an existing duplicate, if one is ever found, is a decision for whoever can see
  both rows, not a migration.
- **A lead's phone never overwrites a reused contact's existing number.** When `:convert`
  matches an existing contact by email — the same person surfacing under a different lead,
  possibly for a different company — `services._reconcile_reused_contact_phone` fills in the
  contact's phone only if it has none yet; if the contact already carries a different number,
  that number is kept and the lead's own (masked) number is recorded on a
  `contact.reused_for_lead` audit row instead of applied. There is no way from the conversion
  path alone to know which of two differing numbers is actually current, and guessing wrong in
  either direction is worse than surfacing both to whoever later looks at that contact's
  history. The contact's `company_id` is never reassigned by a reuse either, for the same
  reason: the book already has an opinion about which company this person belongs to, and a
  converting lead is not grounds to override it.
- **An aborted conversion leaves orphaned records behind, and nothing cleans them up yet.**
  `:convert` calls customer and deal *before* it records anything (claim, call, record), so when
  `finalize_conversion` refuses to write "converted" — the lead's status or owner changed in
  the gap — the company, contact and deal those calls already created stay in customer and deal.
  Lead-service cannot take them back (it has no write path into either schema), so it does the
  two things it can: the `lead.conversion_aborted` audit row names all three ids, and it stages
  `crm.lead.conversion_aborted` (contract: `libs/contracts/events/crm.lead.conversion_aborted/`)
  as the hook. **Nothing consumes it today** — deal has a consumer scaffold with no handler and
  no deployed worker process, customer's worker handles other events — so the orphans are
  visible in the audit trail and the outbox but stay in the book until a person deletes them. A
  future deal-side consumer that marks the orphaned deal lost, and a customer-side one that
  removes the company and contact if nothing else references them, would subscribe to that
  event; the idempotent-by-id design means a retried conversion of the same lead reuses them
  rather than duplicating them in the meantime.
- **A deleted record no longer permanently blocks a lead's conversion.** The ids a lead
  converts into are derived from its own id, so a company or contact an earlier attempt created
  and someone then deleted would refuse every later attempt on the same ids. `Lead
  .conversion_generation` is folded into that derivation (generation `0` derives exactly the
  ids it always has, so nothing already converted changes); `fail_conversion` bumps it when
  customer answers `company-deleted` or `contact-deleted`, and also on a `finalize_conversion`
  abort caused by the lead being reassigned mid-conversion (the records the abandoned attempt
  opened belong to the old owner, so the retry must not reuse them), and the next `:convert`
  derives a fresh set. The deal's *name* carries the generation too (`… · a5065d10/1`;
  generation 0 names are unchanged), because deal-service refuses a second live deal with the
  same name. **Earlier generations' records that were not themselves deleted — typically the
  deal — are left live as orphans**; nothing removes them, and `crm.lead.conversion_aborted`
  (above) is the hook a future cleanup would subscribe to. A deal-name 409 that still happens
  (someone else already used that exact name) is refused with a specific 422 rather than the
  generic "check the lead's details".
- **Databases that ran an earlier version of this branch's migrations must be recreated.**
  customer's `e230c025337e` and lead's whole migration chain were rewritten in place while the
  branch was unmerged and undeployed; a dev or branch database that applied the earlier versions
  is silently wrong (customer) or cannot resolve its own revision (lead). `DROP SCHEMA customer
  CASCADE`, `DROP SCHEMA lead CASCADE`, `make migrate` — see the runbook's "Development
  databases" section (`infrastructure/edge/RUNBOOK.md`).

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
