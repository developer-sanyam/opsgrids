# Sub-project 1: CRM foundations and cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Make the CRM understandable and honest before anything is built on it: one decision record and one CRM reference, the production Customer 360 defect fixed and guarded against its whole class, the CRM's event contracts complete and enforced, and everything the discovery confirmed dead removed, in three independently reversible releases that add no migration, container, permission or route.

**Architecture:** Nothing structural changes. R1.1 is documents plus three service READMEs (ADR-0038 recording the programme's decisions as proposals, the glossary and `docs/crm.md`, dated notes on the accepted ADRs, and the stale-document rewrite). R1.2a is a one-path fix in `customer`'s 360 with the guard tests that keep that defect class from coming back and the organization claim on three `notification` service tokens; R1.2b adds four missing event contracts with producer-side tests and pins on the eight schemas that are ahead of their producer, four subscribable lead keys with a repaired Engage recipe, ops-monitor coverage of the lead outbox, the class-1 removals and a test that ties `docs/crm.md` to the contracts. Every code task is test-first (a failing test, the minimal change, the passing run, a commit); every document task is test-first with a throwaway search check, because a sentence is the unit under test.

**Tech Stack:** Python (3.12 or later) services on FastAPI, SQLAlchemy (async) and Alembic over PostgreSQL with RabbitMQ events and `libs/platform-core`; pytest, mypy and ruff run per service through `uv`; JSON Schema contracts under `libs/contracts`; React 19, TypeScript, Vite 7, vitest 3 and pnpm on Node 22 for `product-crm`, `product-engage` and `opsgrids-client`; bash and python3 throwaway checks for documents; compose, the memory budget and ops-monitor under `infrastructure/edge`, and `tests/infra`.

**Spec:** `docs/superpowers/specs/2026-10-07-crm-1-foundations-and-cleanup-design.md` (cited `S§n`; its decisions `DD-n`, its scope items `R1.1-n` and `R1.2-n`, its acceptance criteria `AC-1.1.n` and `AC-1.2.n`). Companions: the programme contract `docs/superpowers/specs/2026-10-07-crm-overhaul-programme-design.md` (cited `P§n`; owner decisions `D-n`, facts `P§3 F1`-`F11`) and the interface registry `docs/superpowers/specs/2026-10-07-crm-overhaul-interface-registry.md`, whose names are binding and are cited by handle (`EV4`, `C23`, `B40`, `F6`, `G15`, `H8`, `I1`, `J13`, `L2`). Citations are `path:line` at HEAD `3bc7f6ca`, where the CRM paths are identical to `origin/main` at `caa7f112`.

## Global Constraints

- This sub-project adds no migration and moves no head: deal `e94d7a2c5f18`, customer `e230c025337e`, lead `91dd3ca663fd`.
- No migration, no table, no container, no setting or flag, no change to the memory totals (ten sellable profiles 3747 MB, ten plus `admin` 3944 MB, by `memory_budget_totals.py`).
- No feature, no UI redesign, no change to any permission code, role template, route, request or response shape, or event payload or name that exists today (one strike-able exception: an offset-less `occurred_at` is read as UTC, §5.3.4). Four event *schemas* are added for payloads that already exist.
- Neither R1.2 deploy touches anything under `libs/` except `libs/contracts`, or any `pyproject.toml`, `uv.lock` or `.dockerignore` (DD-20); `AC-1.2.14` checks it against the ref `make edge-status` reports as last good.
- No class-2 deletion (§5.4): fixtures mode, `Login.tsx`, the page-only CSV export, the `/deals/summary` fields and the per-card stage `Select` keep working until their replacements ship.
- The removal rule (DD-4): a thing may be deleted when it has no live user, where a live user is a referenced symbol, a test that exercises it, a fixture or contract that pins it, a pixel baseline, or an external caller; each deletion records its proof (the search that returned nothing) in the pull request description; a thing with a live user is replaced first.
- Each release is reversible on its own and follows review → merge → manual deploy → verify, stopping on any failure; R1.1 is merged and not deployed, R1.2 is two deploys (R1.2a, then R1.2b), and R1.2a's pull requests merge and deploy before R1.2b's.
- A proposal is never written as an outcome: in a document the owner has not yet ruled on (the two accepted ADRs, the register, `docs/crm.md`) what the programme *proposes* is worded "proposed in ADR-0038 (pending D-n)"; only what the code or the data shows is written as fact.
- **Owner-run** means a production read or write, a deploy, a `make edge-*` target, publishing a SPA bundle, a box `.env` edit, or the production smoke; an agent builds and tests these locally and never runs them against the box (P§3 F11, registry §H).
- Commit messages must not mention Claude, Claude Code, or any AI assistant: no generated-by footers, no co-author trailers; write as the engineer of record would, in short-lived `feat/*` branches with a pull request and review, squash-merged by the owner; never commit `.env` or secrets.
- Every table carries `organization_id` and every query filters on it; the tenant GUC is transaction-local; consumers need a per-message GUC or `BYPASSRLS`; no cross-schema foreign keys; enqueue only after commit via the outbox; money in integer minor units; append-only ledgers; PII sealed and masked by default with reveal audited; authorization server-side with a **negative-authz test per endpoint** (this sub-project adds no table, route or consumer, so these bind the releases that follow, and the 360's existing negative tests stay green).
- `import app` is ambiguous across services, so tests and mypy loop per service (`cd services/<name> && uv run pytest tests -q -p no:cacheprovider` and `uv run mypy app`); do not "simplify" that back into a single invocation.
- Migrations are expand-only and rollback-tolerant, with one Alembic head per service; `lead.leads.phone_index` is retired in two steps and the contractive drop is not built here (`S§5.2`, DD-15).
- Contract-first and additive within `v1`: every payload change and its committed schema land in one commit and are tested through the real write path (`platform_core.events.validate_event_schema`); events carry ids and enumerations only, and the legacy `full_name` on `crm.book.contact.created` is frozen.
- The eight "ahead of the producer" schemas are decided and pinned by tests, not closed; the closing release deletes its pin rows and the schema's `$comment` in the same commit (R2.3, R3.7, R4.1).
- Every token `notification` mints (two to customer, one to account) carries an `org` claim, and the guard asserts it for every call under `/internal/v1/organizations/{org}/`.
- The subscribable list is explicit, never a wildcard, and every `crm.*` key in it has a committed schema.
- `create_lead` never computes a phone blind index and the column is mapped `deferred`.
- Frontend: shared components live in `@opsgrids/ui` / `@opsgrids/client` and are never forked into `product-crm`; the token baseline forbids raw values (new code is held to zero offenders and the baseline only ever shrinks); every screen has loading, empty, error and forbidden states; SPA gating hides controls and protects nothing.
- Permissions are untouched: no new code, no role change, no `make edge-reconcile`, and `python tools/export_permissions.py --check` stays green; every new route would need an explicit floor so a customer-granted OWN rung cannot widen it.
- Production data is owner-run (P§3 F11): no production data operation is *required* to ship R1.1 or R1.2, and the box `.env` is not edited by either release.
- Verification per release: per-service `pytest` + `mypy` loop, `make check`, `make gate` locally (Actions billing is blocked), `make openapi` and the contract tests, the migration round-trip, the visual and a11y suites for any UI release, and the owner's dry-run evidence for data steps.
- The ADR number is claimed by the merge: before merging R1.1 the owner confirms 0038 is still free, and `docs/decisions.md` states the first number above the highest ADR on `main` (0039; 0041 if 0040 has merged).
- Every `path:line` in a document or a check is re-read at the base commit before it is used; `main` moves, and a failing `cite` line means the cited place changed.

## Review Focus

The five input classes or failure modes the spec implies and no acceptance criterion names, most likely first. Each has its pinning test in the task that owns the code; a test that needs PostgreSQL or Node 22 is marked inference in its task, like the tests beside it.

1. **A converted lead whose contact has no WhatsApp number** (the common case: customer answers `phone: null` with a 200 for every contact that never had one, `services/customer/app/api/internal.py:159-161`): the Engage recipe's run must end `failed` with `recipient_missing` and queue nothing, and a rule must skip without raising. Pinned in Task R1.2.11 by `test_a_rule_on_a_contact_with_no_number_stages_nothing_and_does_not_fail` and `test_a_converted_contact_with_no_number_is_a_visible_failure_and_sends_nothing`.
2. **A person retypes the "Send to" path in the Engage builder:** the first keystroke that leaves `…contact_id` drops `recipient_kind`, typing it back does not restore it, and the contact id is then read as a phone number; the run must fail `recipient_invalid` and message nobody. Pinned in Task R1.2.12 by the Builder test "loses the kind on the first edit that stops naming a contact id, and typing the path back does not restore it" and in Task R1.2.11 by `test_a_contact_id_read_as_an_address_is_refused_and_nothing_is_sent`.
3. **An activity timestamp from an Indian client:** `+05:30` must keep its instant, and a wall clock sent with no offset (read as UTC) that is ahead of UTC must be refused and never stored shifted by five and a half hours. Pinned in Task R1.2.8 by `test_a_timestamp_with_a_non_utc_offset_keeps_its_instant` and `test_a_timestamp_with_no_offset_that_is_ahead_of_utc_is_refused`.
4. **A bookmark to a placeholder route that is gone:** Reports, Integrations, Settings and Help must reach the app's own 404 page, and no rail entry may lead to a path the route table does not declare. Pinned in Task R1.2.22 by the four tests of `tests/infra/test_crm_spa_navigation.py`.
5. **The 360's deal leg answering with something it cannot use even though its path is right:** a `size` above the route's declared maximum is a 422 and one item missing or nulling a field the leg parses fails the whole page, and both read as the same silent `null` section as the old 404. Pinned in Task R1.2.2 by `test_the_page_size_the_leg_asks_for_is_within_the_bound_the_route_declares` and `test_the_fields_the_leg_reads_are_always_sent_by_the_route`.

## Plan status

**Every release of this sub-project is written at full step level. None is written as task cards.** R1.1 (16 tasks, R1.1.1 to R1.1.16), R1.2a (R1.2.1 to R1.2.5) and R1.2b (R1.2.6 to R1.2.25), which together are the 25 tasks of R1.2, carry the exact files, interfaces, test code, expected output, commands and commits, and can be executed in order without expanding anything first.

The later sub-projects' plans write a release as a task card when it consumes an interface that an earlier release produces: the exact names, citations and file contents it will meet exist only once the earlier release has merged, so the card is expanded to step level at the start of that release. Sub-project 1 has no such release. R1.1 consumes nothing (`P§10`). R1.2a consumes R1.1 only for `docs/crm.md`, whose sentences its edits quote as R1.1's own tasks write them; where the executor's wording differs, the task says how to find the sentence and its throwaway check is the judge. R1.2b consumes R1.2a's guard file and R1.1's page, both written in this plan. Everything else R1.2 meets exists at HEAD `3bc7f6ca` and is cited there. The three programme documents that ADR-0038 and `docs/crm.md` link to (the programme design, the interface registry and the foundations design) are untracked files when this plan is written: they must merge first or travel in the R1.1 pull request, and Task R1.1.16 refuses a branch where they are not committed.

**As built, R1.1 (2026-10-10).** R1.1 was built from this plan with all 41 owner decisions (D-1 to D-41, programme section 8), not 25. Wherever Tasks R1.1.1, R1.1.2 and R1.1.16 and the R1.1 release map say 25 or twenty-five decisions, read 41; D-30 and D-32 have no blocking release, so their Needed-by cell is an em dash and `check-adr-0038.sh` allows exactly those two. The as-built text is `docs/adr/0038-crm-programme-decisions.md`, and where it differs from a `create=` block in a task, the as-built file is the record. Tasks R1.2.1 to R1.2.25 are unchanged.

Two labels run through the tasks. **Observed** is a result produced while this plan was written, in a scratch copy outside the repository, against HEAD. **Inference** is what was derived from reading the code and not run, because the test needs PostgreSQL, Redis, the broker or Node 22; the executor's first red run is the proof. Five items are strike-able at review (owner step O-15): the `phone_index` stop (Task R1.2.15), the offset-less `occurred_at` fix (Task R1.2.8), the placeholders, the 404 page and the dead `derive.ts` rollups (Tasks R1.2.20 and R1.2.22), the repaired Engage recipe with its builder rules (Tasks R1.2.11 and R1.2.12) and the two dead gateway rows (Task R1.2.14); each is one self-contained task or pair, so striking one removes it and nothing else.

## Release map

| Release | Status | Prerequisites | Deploy position |
|---|---|---|---|
| R1.1 | full: Tasks R1.1.1 to R1.1.16 (documents and three READMEs) | None (`P§10`). No decision blocks it: ADR-0038 merges with all twenty-five rows open and each default marked as a proposal. The three programme documents committed on the branch. Owner steps O-1 to O-3 | Not deployed: review, then the owner merges (squash). The three READMEs make the next deploy rebuild customer, deal and lead with no code change; the memory totals do not move |
| R1.2a | full: Tasks R1.2.1 to R1.2.5 | R1.1 merged (Task R1.2.5 edits `docs/crm.md`). Owner baselines O-4 to O-7 before the deploy | The first code deploy. Per-service rebuild of customer and notification, and of deal and lead for R1.1's READMEs; no schema, container, permission or SPA publish. R1.2b does not start before O-9 and O-13 pass |
| R1.2b | full: Tasks R1.2.6 to R1.2.25 | R1.1 merged and R1.2a deployed and verified (O-9, O-13). Owner baselines O-4 to O-6 before the deploy | The second code deploy, after R1.2a. Rebuilds customer, deal, lead, notification and app-gateway and recreates ops-monitor, then two SPA publishes from a checkout of the deployed ref: `make edge-web APP=crm`, then `APP=engage` (O-12) |

Releases elsewhere in the programme that need these: R3.1, and anything that turns on the organization claim check, needs R1.2a; R2.3, R3.7 and the R4 releases need R1.2b for the pins, the guards and the subscribable keys (`S§5.1`). A release that adds an internal client in any service registers its calls in `tests/infra/test_internal_client_paths.py` in the same commit, and one that changes a CRM event, permission, role or gateway port updates `docs/crm.md` in the same commit (`S§9.2.2`; applied to the other specs by the orchestrator, not by a task here).

## File Structure

Created (C), modified (M), replaced (R), deleted (D) or regenerated (G), with the task that does it. Throwaway checks (`$R11/check-*.sh`, `$R12/check-*.sh`) are written in each task's first step and are never committed.

**Decision record, CRM reference and notes on accepted ADRs**
- C `docs/adr/0038-crm-programme-decisions.md` (R1.1.1; R1.1.2 inserts the D-11 costing)
- C `docs/crm.md` (R1.1.3 to R1.1.5); M (R1.2.5, R1.2.25)
- M `docs/decisions.md` (R1.1.1)
- M `docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md` (five dated notes and one citation, R1.1.7; three citations re-pointed, R1.2.25)
- M `docs/adr/0008-membership-employee-link.md` (one dated note) and `docs/adr/0020-insights-projection.md` (two dated notes) (R1.1.8)
- M `docs/adr/open-questions-register.md` (ten lines, R1.1.9)

**Stale documents corrected or removed**
- M `docs/features.md`, `docs/api-reference.md`, `docs/plan.md` (R1.1.10)
- M `docs/ecosystem-gaps.md` (R1.1.11; one clause re-pointed, R1.2.25)
- M `docs/architecture.md` (R1.1.12; `:143-145` in R1.2.14; `:351-352` in R1.2.18) and `docs/backlog.md` (R1.1.12)
- D `docs/onboarding/cards-dev-b-pipeline.md`; M `docs/onboarding/README.md` (R1.1.13), `docs/onboarding/cards-dev-a-lead.md` (R1.1.14), `docs/onboarding/cards-dev-d-crm.md` (R1.1.15)
- M `docs/deploy/ENV_SOURCE_MATRIX.md` and `docs/deploy/env-reference.md` (R1.2.13)
- R `services/customer/README.md`, `services/deal/README.md`, `services/lead/README.md` (R1.1.6)

**Service code**
- customer: M `services/customer/app/c1_360.py` (R1.2.2); M `services/customer/app/catalogue.py` (R1.2.24); D `services/customer/bin/cron` (R1.2.18)
- notification: M `services/notification/app/targets.py`, `contacts.py`, `memberships.py` (R1.2.3); M `services/notification/app/domain.py` (R1.2.11)
- deal: M `services/deal/app/services.py` (R1.2.8); M `services/deal/app/selectors.py` (R1.2.17); M `services/deal/app/schemas.py` (R1.2.23); M `services/deal/app/catalogue.py` (R1.2.24); D `services/deal/bin/cron` (R1.2.18)
- lead: M `services/lead/app/models.py` (R1.2.15, R1.2.16); M `services/lead/app/services.py` (R1.2.15); D `services/lead/bin/cron` (R1.2.18)
- app-gateway: M `services/app-gateway/routes.json` (R1.2.14)

**Contracts**
- C `libs/contracts/events/crm.book.contact.created/v1.schema.json`, `crm.book.contact.updated/v1.schema.json`, `crm.book.contact.deleted/v1.schema.json` (R1.2.6); C `libs/contracts/events/crm.pipeline.activity.logged/v1.schema.json` (R1.2.7)
- M `libs/contracts/tests/test_event_schemas.py` (R1.2.10)
- G `libs/contracts/openapi/deal.json` (R1.2.23)

**Tests in the services**
- customer: M `services/customer/tests/test_c1_customer360.py` (R1.2.2); C `services/customer/tests/test_event_contracts.py` (R1.2.6)
- notification: M `tests/test_targets.py`, `tests/test_contacts.py`, `tests/test_memberships.py` (R1.2.3); M `tests/test_m1_analytics.py` (R1.2.10); C `tests/test_crm_lead_keys.py` (R1.2.11), all under `services/notification/`
- deal: C `services/deal/tests/test_event_contracts.py` (R1.2.7); M `services/deal/tests/test_write_routes.py` (R1.2.8, R1.2.17), `tests/helpers.py`, `tests/test_activity_append_only.py`, `tests/test_seed.py` (R1.2.17)
- lead: C `services/lead/tests/test_event_contracts.py` (R1.2.9), `tests/test_phone_index_dormant.py` (R1.2.15), `tests/test_status_vocabulary.py` (R1.2.16)

**Repository tests (`tests/infra`)**
- C `test_internal_client_paths.py` (R1.2.1; edited by R1.2.2 and R1.2.3), `test_edge_smoke_covers_the_360.py` (R1.2.4), `test_engage_recipe_triggers.py` (R1.2.11), `test_crm_entrypoints.py` (R1.2.18), `test_crm_spa_navigation.py` (R1.2.22), `test_crm_doc_matches_contracts.py` (R1.2.25)
- M `test_ops_monitor_compose_env.py` (R1.2.13), `test_route_table_parity.py` (R1.2.14)

**Frontend: Engage (`frontend/product-engage/src/`)**
- M `api/automations.ts`, `routes/automations/graph.ts`, `routes/automations/SendSettings.tsx`, `routes/automations/recipes.ts` (R1.2.12)
- C `routes/automations/recipes.test.ts`; M `routes/automations/Automations.test.tsx`, `graph.test.ts`, `Builder.test.tsx` (R1.2.12)

**Frontend: CRM (`frontend/product-crm/src/`)**
- C `routes/NotFound.tsx`, `routes/NotFound.test.tsx`, `ui/useNavGroups.test.tsx`; M `main.tsx`, `ui/useNavGroups.ts`; D `routes/ComingSoon.tsx` (R1.2.22)
- R `data/derive.ts`; M `data/stages.ts`; C `data/derive.test.ts` (R1.2.20)
- M `api/client.ts`, `lib/format.ts`, `reveal/testKit.tsx`, `ui/index.ts`; D `api/index.ts` (R1.2.21)
- M `forms/OwnerFormDialog.tsx` (R1.2.24)
- G `frontend/opsgrids-config/token-baseline.json`; 18 PNG baselines under `frontend/opsgrids-visual/baselines/darwin/page/crm/` and, only if it improves, `frontend/opsgrids-visual/a11y-baseline.json` (R1.2.22)

**Frontend: shared client**
- M `frontend/opsgrids-client/src/platform/catalogue.ts` and `frontend/opsgrids-client/src/platform/platform.test.tsx` (R1.2.19)

**Infrastructure, tools and end-to-end**
- M `infrastructure/edge/compose.prod.yml` (R1.2.13, R1.2.24)
- M `e2e/edge_product_crud.py` (R1.2.4), `e2e/_seed_product.py` (R1.2.24)
- M `tools/dev-bootstrap/init-db.sql`, `tools/shard-weights.txt` (R1.2.24)

**Deliberately untouched** (each is checked by a release invariant): any file under `libs/` other than `libs/contracts`, any `pyproject.toml`, `uv.lock`, `.dockerignore`, any `migrations/` directory, `libs/contracts/permissions/*.json`, `infrastructure/edge/memory-budget.json`, `infrastructure/edge/routes.prod.json`, and the box `.env`.

---

## Release R1.1 - Decisions ADR, glossary, CRM doc and stale-doc rewrite (docs only)

Spec: `docs/superpowers/specs/2026-10-07-crm-1-foundations-and-cleanup-design.md` §4 (cited here as "the foundations design"); the programme contract is `docs/superpowers/specs/2026-10-07-crm-overhaul-programme-design.md` (`P§n`), and the names are the interface registry's (`docs/superpowers/specs/2026-10-07-crm-overhaul-interface-registry.md`). This release changes documents and three READMEs, and nothing else.

**How the tasks are tested.** A document has no unit test, so each task's test is a throwaway bash script, `$R11/check-<name>.sh` (`R11` is `${TMPDIR:-/tmp}/crm-r1-1-checks`; every Step 1 block begins with the `export` that sets it, so in a new shell run that line first), written in the task's Step 1, run to see it fail, and run again after the task's edit. The scripts are never committed (AC-1.1.10 allows no file outside `docs/` and the three READMEs). Each one asserts the task's acceptance criterion by `grep`, `awk` and `python3`, checks that every fact the new text states is still true at the cited `path:line` (the `cite FILE LINES NEEDLE` function: the needle must occur in those lines), that every `path:line` in the text resolves, that every relative link resolves, and that the repo's own secret hook accepts the file. A hex id (a migration revision, a SHA) is only ever written in backticks or bare, never inside quotes, because the secret scan flags a quoted hex string. Where the repo already has a test that reads the document (`tests/infra/test_purge_test_orgs.py` reads `docs/decisions.md`; `tests/infra/test_putaway_docs.py` scans `docs/backlog.md`), the task runs it too. Blocks tagged `create=`, `append=`, `old=` and `new=` carry the exact text to write or to replace; an `old=` block occurs exactly once in its file.

### Prerequisites

**Releases.** None: R1.1 needs nothing (`P§10`). R1.2a and R1.2b, R2.1, R2.2 and R2.3 need R1.1: their removals cite the glossary and ADR-0038, R1.2b's drift test reads `docs/crm.md`, and R2.3 reads D-12.

**Decisions.** None blocks R1.1. ADR-0038 is written with all twenty-five decisions open and each default marked as a proposal; where an owner answer would change a statement in this release it changes a document only, and the statement says `(pending D-n)`. D-11 and D-13 first matter at R2.4, not here (ADR-0038 "Open items").

**Environment.** An isolated git worktree on a short-lived branch, for example `git switch -c feat/crm-r1-1-docs origin/main` (the repository's root instructions file: short-lived `feat/*` branches, pull request, squash merge). `uv` and the repo's `.venv` (`make install`) for the secret hook and `pytest`, and `python3` for the memory CLI. No database, broker, container or network is needed: every check reads files. Commit messages state what changed and carry no attribution line.

**The programme documents must be tracked.** ADR-0038, `docs/crm.md` and `docs/decisions.md` link to the programme design, the interface registry and the foundations design under `docs/superpowers/specs/`. When this plan was written they were untracked files in the working tree. Either they are merged first or the same pull request carries them; the gate (Task R1.1.16) refuses a branch on which they are not committed.

**Re-verify before starting.** Every `path:line` in this release was read at `HEAD` `3bc7f6ca`, and over every path the release cites that tree is identical to `origin/main` at `caa7f112` (`git diff --stat 3bc7f6ca caa7f112` over those paths prints nothing). `main` moves. Each task's check carries `cite` lines that fail when a cited line has moved and a citation pass that fails on a dangling `path:line`: a failure there means the cited place changed, so re-read it, correct the citation in the task's text and keep the check. Run `git fetch origin` first and, if `git diff --stat caa7f112 origin/main -- services/customer services/deal services/lead services/notification libs/contracts infrastructure/edge docs` shows changes, expect to correct a few.

**OWNER RUN items.** An agent prepares each of these and never runs it.

- **OWNER RUN O-1, at review:** answer the decisions in ADR-0038 by editing each row's State cell (`approved <date>` or `amended <date>`; an amendment rewrites the default cell to the owner's wording in the same edit). D-11 and D-13 first, because they re-cut the platform-backbone sub-project. A row left open stays `open (default recommended)`; R1.1 and R1.2 proceed with rows open, and a later release whose Needed-by row is open does not start.
- **OWNER RUN O-2, before merging:** confirm the ADR numbers with `git fetch --all`, then `git log --all --diff-filter=A --name-only -- 'docs/adr/0038*' 'docs/adr/0039*' 'docs/adr/004*'` and `ls docs/adr | tail -3`. Expected: nothing for 0038 and 0039 on any other ref, `0040-faster-ci.md` (it exists on another ref) and nothing above it. If a parallel branch took 0038, renumber before merging; if 0040 or a higher number is already on `main`, the decisions sentence names the number above it (so `0041`, and the expected follow-up numbers shift with it).
- **OWNER RUN O-3, to merge:** squash-merge the pull request. There is no deploy. `uv run pytest tests/infra/test_purge_test_orgs.py -q -p no:cacheprovider -k next_free_number` is green on the merge commit.
- **OWNER RUN, optional and read-only:** `make edge-preflight HOST=… CONFIRM=…` prints the box's memory verdict against its own ceiling. ADR-0038 and `docs/crm.md` say the 3944 MB ceiling is the owner's note and cannot be verified from the repository; this is how the owner confirms it.

### Deploy order and verification

R1.1 is not deployed. There is no migration, no flag, no `make edge-reconcile`, no `.env` edit and no SPA publish. The owner merges (squash) and stops.

**The one runtime-adjacent effect.** A deploy rebuilds every service whose own directory changed between the last good ref and `HEAD` (`infrastructure/edge/remote.sh:191-196`, and each Dockerfile copies the whole service directory, `services/customer/Dockerfile:22`). The next deploy therefore rebuilds the `customer`, `deal` and `lead` images for their READMEs and recreates their containers with application code that has not changed (inference: compose recreates a container whose image id changed). In the normal order that deploy is R1.2a, which then carries a no-op rebuild of deal and lead beside its own customer and notification changes; an unrelated deploy in between pays the same three. Nothing is owed for it: no migration, no reconcile, no `.env` edit, and the memory totals do not move.

**Memory-budget effect: none.** `python3 infrastructure/edge/lib/memory_budget_totals.py infrastructure/edge/memory-budget.json --profiles storefront,inventory,hrms,crm,pulseboard,projects,support,marketing,finance,itsm` prints 3747 and, with `,admin` added, 3944, on both sides of the change; Task R1.1.16 checks it.

**CI.** `tools/affected-areas.sh` treats a `docs/` path as "run everything" (`tools/affected-areas.sh:54`): the three README paths alone would narrow to those services, but the `docs/` paths in the same change fail it open. With Actions billing blocked, the owner relies on a local `make gate` (`P§3` F11). R1.1 touches no code, so the gate rows that can see it are `make test-infra`, `make hygiene` and `make secrets`; the full `make gate` also needs `make infra` and `make migrate`.

**Verification** is Task R1.1.16: one script that runs the scope check, the index test, the memory totals and the fifteen task checks against the release base, then `make test-infra`, `make hygiene` and `make secrets`. The ADR number is claimed by the merge (O-2).

### Rollback

Revert the squash commit. Everything is a document, so the only irreversible fact is that ADR numbers are never reused (`docs/decisions.md`: "Numbers are allocated in order and never reused"): if 0038 has been referenced elsewhere after the merge, a revert leaves the number allocated and the file is replaced by a one-line "withdrawn" ADR instead of being deleted. Reverting also reverts the three READMEs, so the next deploy rebuilds the same three images once more, again with no code change. Stop condition: `tests/infra/test_purge_test_orgs.py::test_the_decisions_index_lists_the_adr_and_the_next_free_number_is_really_free` fails on the merge commit.

### Task R1.1.1: ADR-0038 and the decisions index

**Files:**
- Create: `docs/adr/0038-crm-programme-decisions.md`
- Modify: `docs/decisions.md:8-11` (the "next free number" sentence and the "apart from" range), `docs/decisions.md:43` (insert the 0038 row after the 0037 row)
- Test: `$R11/check-adr-0038.sh` (a throwaway script, not committed, created in Step 1) and the existing `tests/infra/test_purge_test_orgs.py::test_the_decisions_index_lists_the_adr_and_the_next_free_number_is_really_free` (it pins `docs/decisions.md`: the stated next free number must not be an ADR that exists and must exceed every one that does)

**Interfaces:**
- Consumes: nothing from an earlier task. Reads: `docs/superpowers/specs/2026-10-07-crm-overhaul-programme-design.md` (cited `P§n`: the facts `P§3` F1-F11 and the decisions `P§8`), the interface registry (`2026-10-07-crm-overhaul-interface-registry.md`: D-25 is its `L20`, the follow-up ADR numbers are its `I2`).
- Produces: `docs/adr/0038-crm-programme-decisions.md`, whose Decisions table is a contract that later releases `grep`. Rows `| M1 |` to `| M4 |` and `| D-1 |` to `| D-25 |`, one line each, six cells in this order: ID, Decision, Recommended default, Amends or builds on, Needed by (a release id `R1.1` to `R4.7` for a D row; `all`, `SP4` or `—` for an M row), State (the sixth and last cell: exactly `open (default recommended)`, `approved YYYY-MM-DD` or `amended YYYY-MM-DD`). The answer check later releases run, silent while a decision is open:
  `grep -E '^\| D-9 \|.*\| (approved|amended) [0-9]{4}-[0-9]{2}-[0-9]{2} \|$' docs/adr/0038-crm-programme-decisions.md`.
  Headings, in this order: `## Context`, `## Decisions`, `## What this amends`, `## C15, named`, `## Consequences`, `## Sign-offs`, `## Open items` (Task R1.1.2 inserts `## Costing of D-11` before `## Consequences`; the D-11 row already points at it). `docs/decisions.md` then states `next free number is **0039**` and links `[0038](adr/0038-crm-programme-decisions.md)`.

- [ ] **Step 1: Write the failing check**

```bash check=adr-0038
export R11="${TMPDIR:-/tmp}/crm-r1-1-checks"; mkdir -p "$R11"
cat > "$R11/check-adr-0038.sh" <<'SH'
#!/usr/bin/env bash
# AC-1.1.1 and AC-1.1.2: ADR-0038 and the decisions index. Run from the repository root.
set -u
FAILED=0
fail()  { printf 'FAIL: %s\n' "$*" >&2; FAILED=1; }
has()   { grep -qF -- "$2" "$1" 2>/dev/null || fail "$1 lacks: $2"; }
count() { local g; g="$(grep -cE -- "$2" "$1" 2>/dev/null)"; [ "${g:-0}" = "$3" ] || fail "$1: lines matching /$2/: want $3, got ${g:-0}"; }
cite()  { local a="${2%-*}" b="${2#*-}"; sed -n "${a},${b}p" "$1" 2>/dev/null | grep -qF -- "$3" || fail "$1:$2 does not contain: $3"; }
links() { local f="$1" d t missing=""; d="$(dirname "$f")"
  for t in $(grep -oE '\]\([^) ]+\)' "$f" | sed -E 's/^\]\(//; s/\)$//; s/#.*$//' | grep -vE '^(https?:|mailto:|$)' | sort -u); do
    [ -e "$d/$t" ] || missing="$missing $t"; done
  [ -z "$missing" ] || fail "$f links to missing:$missing"; }
secret_scan() { local b h; b="$(mktemp)"; cp .secrets.baseline "$b"
  if [ -x .venv/bin/detect-secrets-hook ]; then h=.venv/bin/detect-secrets-hook; else h="uv run detect-secrets-hook"; fi
  $h --baseline "$b" "$@" >/dev/null 2>&1 || fail "the repo secret scan flags: $* (a hex id inside quotes?)"; rm -f "$b"; }

ADR=docs/adr/0038-crm-programme-decisions.md
IDX=docs/decisions.md
if [ ! -f "$ADR" ]; then fail "$ADR does not exist"; echo FAILED; exit 1; fi

# AC-1.1.1: title, status line, row grammar
[ "$(head -n 1 "$ADR")" = '# ADR-0038 — CRM programme decisions' ] || fail "the title line is wrong"
[ "$(awk 'NR>1 && NF {print; exit}' "$ADR" | cut -c1-22)" = 'Status: **proposed** ·' ] || fail "the first line after the title is not 'Status: **proposed**'"
count "$ADR" '^\| D-[0-9]+ \|' 25
count "$ADR" '^\| M[0-9] \|' 4
bad="$(awk -F'|' '/^\| (M[0-9]|D-[0-9]+) \|/ { gsub(/^ +| +$/, "", $7); if (NF != 8 || $7 != "open (default recommended)") print $2 }' "$ADR")"
[ -z "$bad" ] || fail "rows without six cells and an open State:$bad"
bad="$(awk -F'|' '/^\| D-[0-9]+ \|/ { if ($6 !~ /R[1-4]\.[0-9]/) print $2 }' "$ADR")"
[ -z "$bad" ] || fail "D rows whose Needed-by cell names no release:$bad"
n="$(grep -cE '^\| D-9 \|.*\| (approved|amended) [0-9]{4}-[0-9]{2}-[0-9]{2} \|$' "$ADR")"
[ "$n" = 0 ] || fail "the answer check reads D-9 as answered while it is open"
n="$(sed -E 's/^(\| D-9 \|.*)\| open \(default recommended\) \|$/\1| approved 2026-10-08 |/' "$ADR" | grep -cE '^\| D-9 \|.*\| (approved|amended) [0-9]{4}-[0-9]{2}-[0-9]{2} \|$')"
[ "$n" = 1 ] || fail "the answer check does not see an approved D-9 row (matched $n)"

# the sections, in order
prev=0
for h in '## Context' '## Decisions' '## What this amends' '## C15, named' '## Consequences' '## Sign-offs' '## Open items'; do
  ln="$(grep -nxF -- "$h" "$ADR" | head -n 1 | cut -d: -f1)"
  if [ -z "$ln" ]; then fail "missing heading: $h"; elif [ "$ln" -le "$prev" ]; then fail "heading out of order: $h"; else prev="$ln"; fi
done
has "$ADR" 'a decision is answered only when its State cell says so'
has "$ADR" 'Human handoff'
has "$ADR" 'stage_outcome'
has "$ADR" 'a1c9e2f7b3d4'  # pragma: allowlist secret
has "$ADR" 'pending D-n'
if grep -qE 'DD-[0-9]+|R1\.2-[0-9]+|[^P]§[45]\.[0-9]' "$ADR"; then fail "$ADR cites the foundations spec by its internal numbering"; fi
links "$ADR"
secret_scan "$ADR" "$IDX"

# facts the rows and the C15 section rely on, at the cited lines
cite services/lead/app/services.py 1125-1132 'owner_changed'
cite services/lead/app/services.py 1226-1226 'async def fail_conversion'
cite libs/platform-core/src/platform_core/auth.py 297-301 'register_scope_resolver'
cite services/customer/app/c1_360.py 95-95 '_OPEN_STAGES'
cite docs/adr/0035-list-prices-2026-10-06.md 40-41 'per-seat'
cite docs/adr/0035-list-prices-2026-10-06.md 165-169 'per-seat'
cite docs/adr/0006-approval-engine.md 62-62 'discount approvals in CRM'
cite docs/adr/0006-approval-engine.md 1479-1479 'manager_membership_id'
cite docs/adr/0021-consent-ledger.md 61-61 'silence is not consent'
cite docs/superpowers/specs/2026-09-21-engage-email-platform-design.md 34-54 'Contacts are a'
cite docs/whatsapp/audit.md 95-95 'Human handoff'
cite docs/whatsapp/audit.md 239-239 'handoff'
[ -f docs/insights/events.md ] || fail "docs/insights/events.md does not exist"

# AC-1.1.2: the decisions index
has "$IDX" '[0038](adr/0038-crm-programme-decisions.md)'
has "$IDX" 'next free number is **0039**'
has "$IDX" '0035–0038 below'
links "$IDX"

[ "$FAILED" = 0 ] && echo PASS || { echo FAILED; exit 1; }
SH
```

- [ ] **Step 2: Run it and see it fail**

Run: `bash "$R11/check-adr-0038.sh"`
Expected: exit status 1; stderr says `FAIL: docs/adr/0038-crm-programme-decisions.md does not exist`, stdout says `FAILED`.

- [ ] **Step 3: Create the ADR**

Create `docs/adr/0038-crm-programme-decisions.md` with exactly this content. Every hex id stays in backticks and never inside quotes (the repo's secret scan flags a quoted hex string), and no table cell contains a pipe.

````markdown create=docs/adr/0038-crm-programme-decisions.md
# ADR-0038 — CRM programme decisions

Status: **proposed** · Date: 2026-10-07

Builds on [ADR-0000](0000-phase-0-decisions.md) (§5, §6, §7, §8, §10, §11),
[ADR-0006](0006-approval-engine.md), [ADR-0007](0007-permission-rollout-to-live-tenants.md),
[ADR-0008](0008-membership-employee-link.md) (Q5),
[ADR-0013](0013-self-serve-products-and-the-one-box-deployment.md),
[ADR-0015](0015-scheduler-and-not-before-delivery.md),
[ADR-0017](0017-crm-lead-service-and-pipeline-scaffold-removal.md),
[ADR-0020](0020-insights-projection.md), [ADR-0021](0021-consent-ledger.md),
[ADR-0022](0022-bulk-import-export.md), [ADR-0023](0023-member-step-up.md),
[ADR-0035](0035-list-prices-2026-10-06.md) and
[ADR-0036](0036-removing-test-organisations.md). **Amends** ADR-0017, ADR-0008 (Q5) and ADR-0020
(§6 and Risk 1) by dated notes only: no accepted decision is rewritten, and the one in-place text
edit is a corrected citation in ADR-0017 (`domain.py:38` is `domain.py:21`).

**This ADR is a proposal.** Every default below is the programme's recommendation and stays one
until the owner answers it in the State column. A document that wants to name a default says
"proposed in ADR-0038 (pending D-n)"; it never states one as a settled outcome.

## Context

The CRM overhaul is one programme of four sub-projects: foundations and cleanup, platform
backbone, core revamp and suite connection. Its contract is
[the programme design](../superpowers/specs/2026-10-07-crm-overhaul-programme-design.md) (cited
`P§n`), the names every release must use are in
[the interface registry](../superpowers/specs/2026-10-07-crm-overhaul-interface-registry.md), and
the first sub-project is
[the foundations design](../superpowers/specs/2026-10-07-crm-1-foundations-and-cleanup-design.md).

The facts behind the decisions are `P§3` F1 to F11 and are not restated here. Three of them
correct the record this ADR amends: F1 (OWN scope is empty for a tenant without HRMS), F2
(ADR-0008's migration recipe does not match the CRM's owner data) and F3 (the orphan cleanup
ADR-0017 sketches is unsafe as worded).

This ADR records the owner's answers once, one row each, so that a later release checks one cell
instead of reading a design document.

## Decisions

Each decision is one line of the table. A row has six cells, in this order: ID, Decision,
Recommended default, Amends or builds on, Needed by (a release id from `R1.1` to `R4.7` for a D
row; the M rows say `all`, `SP4` or `—`) and State. State is exactly one of
`open (default recommended)`, `approved YYYY-MM-DD` or `amended YYYY-MM-DD`; an amended row's
default cell is rewritten to the owner's wording in the same edit. The owner answers a decision by
editing that one cell, and a decision is answered only when its State cell says so.

| ID | Decision | Recommended default | Amends or builds on | Needed by | State |
|---|---|---|---|---|---|
| M1 | Four sub-projects: foundations and cleanup, platform backbone, core revamp, suite connection; order 1, 2, then 3 and 4 | As decided by the owner on 2026-10-07 | — | all | open (default recommended) |
| M2 | Independent releases: each is reversible on its own and follows review, merge, manual deploy, verify, stopping on any failure | As decided by the owner on 2026-10-07 | builds on ADR-0013 and ADR-0036 | all | open (default recommended) |
| M3 | All four connection loops matter: data reaches every product that holds a person or a company | As decided by the owner on 2026-10-07 | ADR-0000 §10 and §11 | SP4 | open (default recommended) |
| M4 | This pass produces specs and plans only; building starts after the owner approves them | As decided by the owner on 2026-10-07 | — | — | open (default recommended) |
| D-1 | NEW-9: what happens to the orphan deal | Soft-delete with an audit row naming the consumer; no `stage_changed` event | ADR-0017 Known limitations, "An aborted conversion leaves orphaned records behind"; register NEW-9 (its "marks the orphaned deal lost" is corrected: closing a deal as lost distorts the win rate and fires tenant automations on `stage_changed`, `P§3` F3) | R2.4 | open (default recommended) |
| D-2 | NEW-9: how far customer cleanup may go | Only records provably created by the aborted attempt, no other contacts, no live deal; never a reused record; **log-only on the first deploy** | register NEW-9 ("removes the orphaned company and contact if nothing else references them" is narrowed) | R2.4 | open (default recommended) |
| D-3 | Orphans from past aborts | The owner runs a read-only count of `lead.conversion_aborted` audit rows; a sweep (registry `H3`) is built only if the count is not zero | ADR-0017 Known limitations, "An aborted conversion leaves orphaned records behind" | R2.4 | open (default recommended) |
| D-4 | Generation bump and event | Bump on every abort; emit an event for the deleted-record bump | ADR-0017 Known limitations, "A deleted record no longer permanently blocks a lead's conversion" (today only `owner_changed` bumps, `services/lead/app/services.py:1125-1132`, and `fail_conversion` stages no event, `:1226-1280`, so "the event is the hook" is inaccurate there) | R2.1 | open (default recommended) |
| D-5 | Stage model | One pipeline per organization (tables shaped so more can be added); an immutable key with an editable label, order, tone and default probability; an outcome of `open`, `won` or `lost`; at least one won and one lost stage; stages are archived, never deleted; closed deals are immutable (reopening is a separate decision) | ADR-0017 decision 4 ("accepted debt" becomes scheduled work); register row for hard-coded stages; the stage event contract below | R2.3 | open (default recommended) |
| D-6 | Who edits stages | `crm.pipeline.stage.write` with a route floor of ORG; reading stays under `crm.pipeline.deal.read`; no new admin template | builds on ADR-0007 | R2.5 | open (default recommended) |
| D-7 | What a rep is | A rep is an `account` membership: `customer.owners` gains `membership_id`; the Team screen lists organization members and activates them as reps; free-form rep creation retires after the cutover | ADR-0008 Q5 (a column, not a rekey); register row 20 and NEW-7 | R2.6 | open (default recommended) |
| D-8 | Owner-id mapping rule | Email match from roster to member as a reviewed plan; the employee link for employee-valued rows; unmapped rows untouched and listed; an owner-run dry run per organization; never guess | ADR-0008 Q5's migration recipe, whose premise the data do not support (`P§3` F2); ADR-0008 Q1 rejected email matching for the HRMS link, so the plan is human-reviewed | R2.6 | open (default recommended) |
| D-9 | The access widening C15 causes | Approve, with a security review of every OWN route before the flip (reps gain OWN write, convert and reveal on their rows) | ADR-0008 Q5 Consequences (CRM's OWN semantics change for all three processes at once) | R2.7 | open (default recommended) |
| D-10 | Production CRM census | The owner runs read-only counts: organizations with CRM data, roster sizes, employee links, `lead.outbox` backlog | builds on ADR-0036 (the box is staging-like; production reads are the owner's) | R2.6 | open (default recommended) |
| D-11 | Fold `lead` into `customer`? | **Keep three processes** for this programme; revisit after R2 | would supersede ADR-0017 decision 1; costed in the section "Costing of D-11" below | R2.4 (only if D-13 holds the ceiling; R1.1 merges with this row open) | open (default recommended) |
| D-12 | Insights against stages | Stages land first; the deal migration `a1c9e2f7b3d4` is adopted unchanged; insights is rebased onto `outcome` | ADR-0020 §6 and Risk 1 notes; `docs/insights/events.md` (rebased in R2.3); the stage event contract below | R2.3 | open (default recommended) |
| D-13 | Memory ceiling | The owner chooses: raise `EDGE_BUDGET_ACCEPTED_MB` by one per fold, free memory (D-11), or hold the folds. First needed at R2.4 | ADR-0017 Consequences (stale memory figures); ADR-0020 Risk 1 | R2.4 | open (default recommended) |
| D-14 | Party identity | Company is the party and contact is the person; phone-only contacts are allowed (email nullable) | builds on ADR-0017; `P§4.2` | R3.1 | open (default recommended) |
| D-15 | Engage contact book | A projection of CRM for CRM-entitled organizations; standalone mode kept; the sync writes no consent | builds on ADR-0021 ("silence is not consent", `docs/adr/0021-consent-ledger.md:61`) and the Engage design §1.2 (`docs/superpowers/specs/2026-09-21-engage-email-platform-design.md:34-54`) | R4.2 | open (default recommended) |
| D-16 | Marketing against Engage | Engage owns sending and delivery analytics; Marketing owns planning and attribution and reads Engage | — | R4.6 | open (default recommended) |
| D-17 | `project` or `projects` as the target | The owner picks; the link columns and event fields are one shared contract | — | R4.4 | open (default recommended) |
| D-18 | Discount approvals | The approval engine, with a requester abstraction and a platform inbox | builds on ADR-0006 (its Context names discount approvals in CRM as a consumer, `docs/adr/0006-approval-engine.md:62`); supersedes, for tenants without HRMS, the employee-keyed requesters and HRMS-only inbox `P§6` records, in the follow-up ADR `approvals-for-tenants-without-hrms` | R4.5 | open (default recommended) |
| D-19 | Quote home and item master | Quotes in `deal`; `inventory_items` is the one item master; the `quotation` scaffold is deleted with the quote work | records the deletion on the ADR-0017 precedent | R4.5 | open (default recommended) |
| D-20 | Role consolidation | `CRM_ADMIN`, `CRM_REP`, `CRM_MANAGER` and `CRM_VIEWER` by same-code merging across the three catalogues; the six legacy role codes and all 17 permission codes are frozen, never renamed or removed | ADR-0017 decision 1 as built (`CRM_LEAD_REP`); builds on ADR-0007 | R2.8 | open (default recommended) |
| D-21 | Plan packaging | Stay flat; fix or retire the per-seat `crm/growth` plan | ADR-0035 open question (`docs/adr/0035-list-prices-2026-10-06.md:40-41,165-169`) | R2.8 | open (default recommended) |
| D-22 | API nouns | `/companies` canonical with `/customers` aliased; the 360 under `/contacts/{id}/360`; `/activities` aliased | — | R3.2 | open (default recommended) |
| D-23 | Removals that had live users | Replace, then delete: fixtures mode, `Login.tsx`, the page-only CSV export, the unrendered `/deals/summary` fields and the per-card stage select go only after their replacements ship | builds on ADR-0022 (the export engine) | R3.4 | open (default recommended) |
| D-24 | TEAM scope | TEAM scope for managers, sourced from `manager_membership_id` through a per-service `owner_team` projection | builds on ADR-0006 Q1 (`manager_membership_id` kept as core's TEAM rung, `docs/adr/0006-approval-engine.md:1479`) | R3.8 | open (default recommended) |
| D-25 | Managers revealing a sealed phone or email across their team | **No**: the reveal stays at OWN, the rep-equivalent | builds on ADR-0023 (step-up); raised by the interface registry (its `L20`), not by the programme's register | R2.8 | open (default recommended) |

Later releases read the table with one command. For D-9 it prints the row when D-9 is answered and
nothing while it is open; a release that needs one specific answer tests for `approved` alone:

```
grep -E '^\| D-9 \|.*\| (approved|amended) [0-9]{4}-[0-9]{2}-[0-9]{2} \|$' docs/adr/0038-crm-programme-decisions.md
```

Evidence that travels with an answer (D-9's signed security review, D-21's staff plan action) goes
on one line under Sign-offs, `D-n: <what was signed or done> on <date>`; a Sign-offs line is
evidence and never an answer. When the owner answers D-n, the same commit replaces the
`(pending D-n)` clause in each note that cites it (`grep -rn 'pending D-9' docs` lists them for D-9).

### The stage event contract (proposed under D-5 and D-12)

If D-5 and D-12 are answered as recommended, the CRM's deal events change in these ways and no
others. Every change is additive within `v1` and lands in the same commit as its committed schema.

- A stage has an **outcome**: `open`, `won` or `lost`. A consumer tests the outcome and never the
  key.
- The six stored keys `lead`, `qualified`, `proposal`, `negotiation`, `closed_won` and `closed_lost`
  are frozen. They become the default pipeline's keys and never change meaning, because tenant
  rules match `payload.to`, the Customer 360 tests them literally
  (`services/customer/app/c1_360.py:95`) and three event contracts carry them.
- `crm.pipeline.deal.created` and `crm.pipeline.deal.updated` gain `stage_outcome`;
  `crm.pipeline.deal.stage_changed` gains `from_outcome`, `to_outcome` and an optional `to_label`,
  while `from` and `to` stay keys. The six-value enumerations that carry a stage key become the key
  pattern `^[a-z0-9_]{1,20}$`.
- `deals.closed_at` is a stored column, set when a deal reaches a won or lost outcome. R2.3 creates
  it with the deal migration `a1c9e2f7b3d4`, which exists only on the unmerged branch
  `sprint/insights-q2` (parent `e94d7a2c5f18`) and is adopted unchanged; the committed deal event
  schemas already require `closed_at`.
- The insights projection (ADR-0020) is rebased onto `outcome` before custom stages are enabled in
  any organization that has insights, because its deal fold tests the literal keys and would
  dead-letter otherwise.

## What this amends

Accepted ADRs are amended by dated notes, never rewritten; the precedent is ADR-0008's own
"Amended 2026-09-17" notes. The notes this ADR adds, so that a reader of the older document can
find them (all dated 2026-10-07; each states what the code or the data shows as fact and what this
ADR only proposes as "proposed in ADR-0038 (pending D-n)"):

| Document | Where | What the note says |
|---|---|---|
| ADR-0017 | Decision 1 | As built, `lead` declares `CRM_LEADS_ADMIN` and a third default role `CRM_LEAD_REP`; neither older rep template holds a `crm.lead.*` grant. Merging the three rep templates is proposed in D-20 |
| ADR-0017 | Decision 4 | The one in-place edit: the citation `domain.py:38` is `domain.py:21` |
| ADR-0017 | The `customer-worker` section | The consumer now runs inside the customer API process; the container survives only behind `split-workers` |
| ADR-0017 | Known limitations, "An aborted conversion leaves orphaned records behind" | Idempotent-by-id reuse is why the sketched cleanup is unsafe; the cleanup is proposed in D-1 to D-3 |
| ADR-0017 | Known limitations, "A deleted record no longer permanently blocks a lead's conversion" | `fail_conversion` stages no event, so the event is not a hook for every earlier generation; bump and event are proposed in D-4 |
| ADR-0017 | Consequences | The memory figures are recomputed; the ceiling is D-13 |
| ADR-0008 | Q5 | CRM owner values are roster ids or employee ids, not one space, and the platform-core change is not small; the mapping plan and a `membership_id` column are proposed in D-7 and D-8 |
| ADR-0020 | Risk 1 | The +99 MB is an owner decision against today's totals (D-13) |
| ADR-0020 | Section 6 (Event contracts) | The stage vocabulary moves from keys to `outcome` (D-5, D-12) |

The open-questions register gains one clause naming this ADR in its row 20, in the three
ADR-0017 limitation rows for plaintext contact email, aborted conversions and hard-coded stages
and in the note under them, in NEW-7, NEW-8 and NEW-9 and in the C15/C14 scope note; its counts do
not change, and NEW-9's "marks the orphaned deal lost" is annotated, not replaced.

One scope move is recorded here because it changes where the registry and the core-revamp spec look. The
four placeholder routes of `product-crm` (Reports, Integrations, Settings and Help), their
navigation entries, `routes/ComingSoon.tsx`, the 404 page (`routes/NotFound.tsx`) and the seven
dead rollups of `data/derive.ts` are to be removed in R1.2b and not in R3.4, so the registry row `G15`
and the core-revamp spec's replace-then-delete ledger follow.

## C15, named

The open-questions register and ADR-0008 use "C15" for a CRM rebuild slice that exists in no other
document: the register's row 20, its limitation rows for plaintext contact email and for
hard-coded stages, NEW-7 to NEW-9 and the C15/C14 scope note, and ADR-0008 Q5 Consequences. This
ADR defines it so the label can be traced.

- **C15 is OWN on the membership.** R2.6 expands (`owners.membership_id`, the account bulk read,
  the owner-run mapping tool); R2.7 flips (CRM scope on the membership, with the data applied
  first); the optional R2.9 contracts (retire the employee-id fallbacks and the one-click owner
  claim).
- **C15 is configurable stages.** R2.3 expands; R2.5 adds the authoring API and
  `crm.pipeline.stage.write`.
- **NEW-7**, the platform-core membership subject, is R2.2. It is larger than the register says,
  because `register_scope_resolver` writes a dictionary that nothing reads
  (`libs/platform-core/src/platform_core/auth.py:297-301`).
- **NEW-9** is R2.1 (lead hardening) and R2.4 (the consumers).
- The optional sealing of `customer.contacts.email`, the register's "C15/Q2", is R2.9.

`docs/whatsapp/audit.md` (lines 95 and 239) uses `C15` for "Human handoff": an unrelated id in
another audit's own numbering.

## Consequences

The release train is `P§10` as amended by the registry. A release whose Needed-by row is still
open in the table above does not start: its plan task runs the answer check and stops. R1.1 (this
ADR) and R1.2 need no answer, and where an answer would change anything in them it changes
documents only.

| Release | Decisions that must be answered first |
|---|---|
| R1.1, R1.2, R2.2 | none |
| R2.1 | D-4 |
| R2.3 | D-5, D-12 |
| R2.4 | D-1, D-2, D-3, D-13 (and D-11 if D-13 is answered "no increase") |
| R2.5 | D-6 |
| R2.6 | D-7, D-8, D-10 |
| R2.7 | D-9 |
| R2.8 | D-20, D-21, D-25 |
| R3.1 | D-14 |
| R3.2 | D-22 |
| R3.4 | D-23 |
| R3.8 | D-24 |
| R4.2 | D-15 |
| R4.4 | D-17 |
| R4.5 | D-18, D-19 |
| R4.6 | D-16 |

Releases not listed need no decision beyond those of their prerequisites.

## Sign-offs

None yet. One line per signed or completed piece of evidence, `D-n: <what was signed or done> on
<date>`; a line here is evidence and never an answer.

## Open items

- **D-13 first bites at R2.4, not R4.6** (the date `P§8` gives). One consumer fold takes the ten
  sellable profiles plus `admin` from 3944 MB to 3945 MB, above the ceiling the owner noted; the
  registry's five folds reach 3949 MB (registry `F8` and `L17`).
- **D-11 first bites at R2.4 too**, and only if D-13 is answered "no increase": not at R1.1, which
  merges with D-11 open (`P§8` lists "R1.1 (ADR)").
- **A release-order constraint with no decision behind it: R3.4 deploys before R3.7.** The page-only
  CSV export of Companies does not neutralise a cell that begins with `=`, `+`, `-` or `@`
  (`frontend/product-crm/src/lib/csv.ts:8-11`). Today only a tenant's own users write company
  names; the first external writer is the public lead capture of R3.7. R3.4 deletes the export, so
  either R3.4 deploys first or a guard in `csv.ts` ships first. It is recorded and not fixed,
  because the export is replaced and not patched (`docs/crm.md` section 11).
- **Follow-up ADRs** the registry expects (its `I2`): `crm-consent-and-erasure` (0039, R4.1, draft),
  `crm-public-lead-capture` (0041, R3.7), `approvals-for-tenants-without-hrms` (0042, R4.5) and
  `crm-quotes-in-deal` (0043, R4.5). Each number is confirmed when its file is written; 0040 is
  `faster-ci`, claimed on another ref.
````

- [ ] **Step 4: Run the existing index test: it must now fail**

Creating the ADR without touching the index breaks the invariant the index test pins, which is why the index edit belongs to this task.

Run: `uv run pytest tests/infra/test_purge_test_orgs.py -q -p no:cacheprovider -k next_free_number`
Expected: `1 failed`, with `AssertionError: ADR 0038 exists: the number is not free`.

- [ ] **Step 5: Update the decisions index**

In `docs/decisions.md`, replace the sentence at lines 8-11 (it occurs once):

````text old=docs/decisions.md#sentence
to the ADR section that decides it and to the backlog item that builds it. Numbers are allocated in
order and never reused. The next free number is **0038** (0027 is allocated to the inventory Phase 9
work, which has not merged). This table was last brought up to date at 0019, apart from 0028–0033
and 0035–0037 below; [`adr/`](adr/) is the complete list.
````

with:

````text new=docs/decisions.md#sentence
to the ADR section that decides it and to the backlog item that builds it. Numbers are allocated in
order and never reused. The next free number is **0039** (0027 is allocated to the inventory Phase 9
work, which has not merged; 0040 is allocated to the faster-CI work, claimed on another ref; the CRM
programme expects 0039, 0041, 0042 and 0043 for its follow-up ADRs, row I2 of
[the interface registry](superpowers/specs/2026-10-07-crm-overhaul-interface-registry.md)). This
table was last brought up to date at 0019, apart from 0028–0033 and 0035–0038 below;
[`adr/`](adr/) is the complete list.
````

Then insert the 0038 row directly after the 0037 row (line 43); the anchor is the end of that row and the blank line after it:

````text old=docs/decisions.md#row
Read-only audit query for orgs that already hand-activated: RUNBOOK |

Several header status lines
````

````text new=docs/decisions.md#row
Read-only audit query for orgs that already hand-activated: RUNBOOK |
| [0038](adr/0038-crm-programme-decisions.md) | CRM programme decisions: the owner's answers to the overhaul's twenty-five decisions, and the costing of folding `lead` into `customer` | Proposed 2026-10-07 (for owner sign-off) | Documents only (R1.1 of the CRM overhaul): no code, migration, event or setting changes. Every decision is open with a recommended default until the owner edits its State cell. Amends ADR-0017, ADR-0008 and ADR-0020 by dated notes; `docs/crm.md` is the CRM's single reference |

Several header status lines
````

- [ ] **Step 6: Run the check and the index test and see them pass**

Run: `bash "$R11/check-adr-0038.sh" && uv run pytest tests/infra/test_purge_test_orgs.py -q -p no:cacheprovider -k next_free_number`
Expected: `PASS`, then `1 passed`.

- [ ] **Step 7: Commit**

```bash
git add docs/adr/0038-crm-programme-decisions.md docs/decisions.md
git commit -m "Add ADR-0038 recording the CRM programme's decisions as proposals"
```

### Task R1.1.2: The D-11 costing, reproducible

**Files:**
- Modify: `docs/adr/0038-crm-programme-decisions.md` (insert the section `## Costing of D-11` immediately before `## Consequences`, which is at the line that `grep -n '^## Consequences$'` prints)
- Test: `$R11/check-costing.sh` (a throwaway script, not committed, created in Step 1)

**Interfaces:**
- Consumes: Task R1.1.1's file with its headings and the D-11 row, which points at "the section 'Costing of D-11' below". The repo's own arithmetic: `infrastructure/edge/lib/memory_budget_totals.py` (CLI: `python3 infrastructure/edge/lib/memory_budget_totals.py <budget.json> --profiles <csv>` prints one integer, the sum of `expected_rss_mb`), `infrastructure/edge/memory-budget.json` (`kinds.api` 98, `kinds.api_with_worker` 99, `kinds.background` 90, `host.mem_total_mb_reference` 3862, `containers.lead`, `containers.deal`).
- Produces: the section `## Costing of D-11` with a scenario table whose rows begin with a scenario name or `**D-11` (never with `| D-` or `| M`, so the row grammar of Task R1.1.1 does not see them). `docs/crm.md` §10 (Task R1.1.5) summarises it and links here. The four commands print 3747, 3944, 3649 and 3846.

- [ ] **Step 1: Write the failing check**

```bash check=costing
export R11="${TMPDIR:-/tmp}/crm-r1-1-checks"; mkdir -p "$R11"
cat > "$R11/check-costing.sh" <<'SH'
#!/usr/bin/env bash
# AC-1.1.9: the costing table is reproducible with the repo's own CLI. Run from the repository root.
set -u
FAILED=0
fail() { printf 'FAIL: %s\n' "$*" >&2; FAILED=1; }
has()  { grep -qF -- "$2" "$1" 2>/dev/null || fail "$1 lacks: $2"; }
count() { local g; g="$(grep -cE -- "$2" "$1" 2>/dev/null)"; [ "${g:-0}" = "$3" ] || fail "$1: lines matching /$2/: want $3, got ${g:-0}"; }
secret_scan() { local b h; b="$(mktemp)"; cp .secrets.baseline "$b"
  if [ -x .venv/bin/detect-secrets-hook ]; then h=.venv/bin/detect-secrets-hook; else h="uv run detect-secrets-hook"; fi
  $h --baseline "$b" "$@" >/dev/null 2>&1 || fail "the repo secret scan flags: $* (a hex id inside quotes?)"; rm -f "$b"; }

ADR=docs/adr/0038-crm-programme-decisions.md
BUDGET=infrastructure/edge/memory-budget.json
CLI=infrastructure/edge/lib/memory_budget_totals.py
TEN=storefront,inventory,hrms,crm,pulseboard,projects,support,marketing,finance,itsm

# the four commands: today, today plus admin, and the same two without the lead container
t1="$(python3 "$CLI" "$BUDGET" --profiles "$TEN")"
t2="$(python3 "$CLI" "$BUDGET" --profiles "$TEN,admin")"
nolead="$(mktemp)"; nodeal="$(mktemp)"
python3 -c "import json,sys; b=json.load(open(sys.argv[1])); del b['containers']['lead']; json.dump(b, open(sys.argv[2],'w'))" "$BUDGET" "$nolead"
python3 -c "import json,sys; b=json.load(open(sys.argv[1])); del b['containers']['lead']; del b['containers']['deal']; json.dump(b, open(sys.argv[2],'w'))" "$BUDGET" "$nodeal"
t3="$(python3 "$CLI" "$nolead" --profiles "$TEN")"
t4="$(python3 "$CLI" "$nolead" --profiles "$TEN,admin")"
t5="$(python3 "$CLI" "$nodeal" --profiles "$TEN")"
t6="$(python3 "$CLI" "$nodeal" --profiles "$TEN,admin")"
rm -f "$nolead" "$nodeal"
[ "$t1,$t2,$t3,$t4" = "3747,3944,3649,3846" ] || fail "the memory CLI printed $t1,$t2,$t3,$t4, not 3747,3944,3649,3846: recompute the table"
bg="$(python3 -c "import json; print(json.load(open('$BUDGET'))['kinds']['background']['expected_rss_mb'])")"

if [ ! -f "$ADR" ] || ! grep -qxF '## Costing of D-11' "$ADR"; then
  fail "$ADR has no '## Costing of D-11' section"; echo FAILED; exit 1
fi
prev=0
for h in '## C15, named' '## Costing of D-11' '## Consequences'; do
  ln="$(grep -nxF -- "$h" "$ADR" | head -n 1 | cut -d: -f1)"
  if [ -z "$ln" ]; then fail "missing heading: $h"; elif [ "$ln" -le "$prev" ]; then fail "heading out of order: $h"; else prev="$ln"; fi
done

# every scenario row is recomputed from the four CLI numbers and the budget's own kinds
python3 - "$ADR" "$t1" "$t2" "$t3" "$t4" "$t5" "$t6" "$bg" <<'PY' || FAILED=1
import re, sys
adr, t1, t2, t3, t4, t5, t6, bg = sys.argv[1], *map(int, sys.argv[2:9])
ref = 3862
want = {
    "Today": (t1, t2),
    "Registry plan": (t1 + 5, t2 + 5),
    "**D-11 yes**:": (t3, t4),
    "**D-11 yes** and": (t3 + 4, t4 + 4),
    "For context: a standalone": (t1 + bg, t2 + bg),
    "For context: `deal` folded": (t5, t6),
    "For context: the planned insights": (t3 + 4 + 99, t4 + 4 + 99),
}
text = open(adr, encoding="utf-8").read()
section = text.split("\n## Costing of D-11\n", 1)[1].split("\n## ", 1)[0]
rows = [ln for ln in section.split("\n") if ln.startswith("| ") and not ln.startswith("| Scenario") and not ln.startswith("|---")]
bad = 0
seen = set()
for ln in rows:
    cells = [c.strip() for c in ln.strip().strip("|").split("|")]
    if len(cells) != 7:
        print(f"FAIL: a costing row has {len(cells)} cells, not 7: {ln[:60]}", file=sys.stderr); bad = 1; continue
    key = next((k for k in want if cells[0].startswith(k)), None)
    if key is None:
        print(f"FAIL: unexpected costing row: {cells[0][:60]}", file=sys.stderr); bad = 1; continue
    seen.add(key)
    num = lambda s: float(s.replace("**", "").replace("+", "").replace("−", "-"))
    ten, p1, adm, p2, v1, v2 = (num(c) for c in cells[1:])
    exp_ten, exp_adm = want[key]
    ok = (ten == exp_ten and adm == exp_adm and p1 == round(exp_ten * 100 / ref, 2)
          and p2 == round(exp_adm * 100 / ref, 2) and v1 == exp_adm - 3944 and v2 == exp_adm - ref)
    if not ok:
        print(f"FAIL: row '{cells[0][:40]}' is {ten, p1, adm, p2, v1, v2}, want {exp_ten, round(exp_ten*100/ref,2), exp_adm, round(exp_adm*100/ref,2), exp_adm-3944, exp_adm-ref}", file=sys.stderr); bad = 1
missing = set(want) - seen
if missing:
    print(f"FAIL: costing rows missing: {sorted(missing)}", file=sys.stderr); bad = 1
sys.exit(bad)
PY

for s in 'What it removes' 'What it costs' 'Recommendation' 'Keep three processes' 'supersedes ADR-0017 decision 1' 'XL' '3945'; do has "$ADR" "$s"; done
has docs/adr/0020-insights-projection.md 'One more always-on container (+99 MB)'
count "$ADR" '^\| D-[0-9]+ \|' 25
count "$ADR" '^\| M[0-9] \|' 4
grep -E '^\| D-11 \|' "$ADR" | grep -qF '| open (default recommended) |' || fail "the D-11 row is no longer open"
secret_scan "$ADR"

[ "$FAILED" = 0 ] && echo PASS || { echo FAILED; exit 1; }
SH
```

- [ ] **Step 2: Run it and see it fail**

Run: `bash "$R11/check-costing.sh"`
Expected: exit status 1; the first lines are `FAIL: docs/adr/0038-crm-programme-decisions.md has no '## Costing of D-11' section` and `FAILED` (the four CLI numbers already match, so nothing else is reported before the section check).

- [ ] **Step 3: Insert the costing section**

Insert the section before `## Consequences`. The anchor is that heading with the first words under it, which occur once:

````text old=docs/adr/0038-crm-programme-decisions.md#consequences
## Consequences

The release train is `P§10` as amended by the registry.
````

````text new=docs/adr/0038-crm-programme-decisions.md#consequences
## Costing of D-11

**What the fold is.** `lead`'s code and tables become part of the `customer` service and schema;
the `lead` container and the `lead` schema go away; `leads:convert` becomes a local transaction for
the company and contact, while the deal call stays an HTTP call outside any transaction.

**Memory, by the repo's own arithmetic.** The CLI that `preflight.sh` uses is
`python3 infrastructure/edge/lib/memory_budget_totals.py infrastructure/edge/memory-budget.json --profiles <csv>`;
the D-11 rows are computed on a copy of the budget with the `lead` entry removed from `containers`.
The reference MemTotal is 3862 MB. The box's accepted ceiling is 3944 MB according to the owner's
note (`EDGE_BUDGET_ACCEPTED_MB`); that figure cannot be verified from the repository, and
`make edge-preflight` prints the verdict. "Ten profiles" is `storefront,inventory,hrms,crm,pulseboard,projects,support,marketing,finance,itsm`.

| Scenario | Ten profiles | % of 3862 | Ten plus `admin` | % of 3862 | against 3944 | against 3862 |
|---|---|---|---|---|---|---|
| Today | 3747 | 97.02 | 3944 | 102.12 | 0 | +82 |
| Registry plan: five folds of +1 MB (deal R2.4, lead R3.8, support R4.4, marketing R4.6, storefront R4.7) | 3752 | 97.15 | 3949 | 102.25 | +5 | +87 |
| **D-11 yes**: the `lead` container removed, `customer` stays `api_with_worker` (99) | **3649** | 94.48 | **3846** | 99.59 | -98 | -16 |
| **D-11 yes** and the four remaining folds (no lead fold) | 3653 | 94.59 | 3850 | 99.69 | -94 | -12 |
| For context: a standalone `deal-worker` (+90) instead of a fold | 3837 | 99.35 | 4034 | 104.45 | +90 | +172 |
| For context: `deal` folded into `customer` too (not proposed) | 3551 | 91.95 | 3748 | 97.05 | -196 | -114 |
| For context: the planned insights container (+99) on top of the D-11 and four-fold row | 3752 | 97.15 | 3949 | 102.25 | +5 | +87 |

The first and third rows reproduce with four commands: the CLI over the repo's budget with the ten
profiles (3747) and with `,admin` added (3944), and over a copy without the `lead` container with
the same two lists (3649 and 3846). The other rows add the fold or container cost on top (a fold
moves a service from kind `api`, 98 MB, to kind `api_with_worker`, 99 MB; a standalone worker is
kind `background`, 90 MB).

`customer` would absorb `lead`'s modules. The kind's own measurements put `customer` at 91.7 MiB
and `lead` at 92.2 MiB (`memory-budget.json`, `kinds.api.measured_mib`), nearly all of it the
shared module graph, so the saving is 98 MB less whatever `lead`'s modules add to `customer`'s
resident set: small, but not measured (inference), and the 98 is itself a rounded mean. Every
ten-profile row sits between the soft line (85% of 3862, 3282.7 MB) and the hard line (3862 MB).
With `admin`, only the two D-11 rows and the deal-folded row fall below the hard line; every row is
far below the accepted maximum of 125% (4827.5 MB).

**What it removes.** The registry rows that would not be built: the lead fold `F9` with its
`lead-worker`, `lead.events` (`EV45`) and its addition to `PROFILE_DLQ_QUEUES["crm"]`; lead's copy
of `owner_team` (`B4`); the wiring rows `W2` and `W19`; the `owners:validate` route (`C49`) and
lead's process-local owner caches; three entries in
`tests/infra/test_io_outside_transaction.py` (lead's `put_assignment_rule`, `create_lead` and
`update_lead`); and the customer half of NEW-9 (D-2), because the company and contact are written
in one local transaction. The orphan deal remains (D-1).

**What it costs.**

1. It supersedes ADR-0017 decision 1 ("`lead` is a third process", accepted 2026-09-23).
2. An irreversible data move on live tenants. `lead.leads`, `assignment_rules`, `audit_logs`,
   `outbox` and `processed_events` become tables in the `customer` schema: customer migrations
   create them; an owner-run tool copies the rows per organization with the admin role (idempotent,
   counts only); the PII columns are sealed under the platform-wide key, so nothing is
   re-encrypted; the `tenant_isolation` policies are rebuilt on `customer.current_org()`;
   `opsgrids_customer` is granted on them; `lead.outbox` must be drained first because the relay
   entry for `lead` goes away. After the first write to the new tables there is no code-only
   rollback.
3. File and test churn: `lead`'s 3,103 lines of application code and 5,287 of tests move under
   `customer`; `/api/v1/leads` is re-pointed to `customer` in **both** route tables; the `lead`
   block leaves `compose.prod.yml`; the ten-profile literals in `RUNBOOK.md` (3747 becomes 3649)
   change in the same commit or
   `tests/infra/test_planned_headroom.py::test_runbook_ceiling_example_is_the_number_preflight_would_print`
   fails; `PROFILE_API_CONTAINERS["crm"]` (`libs/platform-core/src/platform_core/ops_monitor/config.py:83`)
   is a `libs/` change, so that deploy is a full rebuild; `tools/dev-bootstrap/init-db.sql` and
   `SERVICES_WITH_SCHEMA` in `infrastructure/edge/remote.sh` lose `lead`.
4. The event envelope's `producer` for `crm.lead.*` becomes `customer` (`services/lead/app/relay.py:38`
   takes it from the service name). Only the scheduler's ownership rule reads `producer`
   (`services/scheduler/app/consumers.py:90`), and no CRM event passes through it.
5. Size XL on the repo's scale (more than a week, several slices): moving the code and tests is L,
   the data-move tool with its runbook M, the gateway, compose and documents S.

**Recommendation.** Keep three processes for this programme. **Revisit when** (a) the owner answers
D-13 "no increase": D-11 must then be answered yes *before R2.4 deploys*, because R2.4's single
fold takes the ten profiles plus `admin` to 3945 MB, above 3944, and the D-11 rows are the only
registered way to stay under the ceiling; or (b) the owner holds the planned insights container
until memory is freed; or (c) R2.1 and R2.4 have shipped and NEW-9's customer half is already
hardened, which shrinks the fold's benefit to the 98 MB.

## Consequences

The release train is `P§10` as amended by the registry.
````

- [ ] **Step 4: Run the check and see it pass**

Run: `bash "$R11/check-costing.sh"`
Expected: `PASS`.

- [ ] **Step 5: Run the previous task's check as well, so the insertion broke nothing**

Run: `bash "$R11/check-adr-0038.sh" && uv run pytest tests/infra/test_purge_test_orgs.py -q -p no:cacheprovider -k next_free_number`
Expected: `PASS`, then `1 passed`.

- [ ] **Step 6: Commit**

```bash
git add docs/adr/0038-crm-programme-decisions.md
git commit -m "Record the D-11 costing in ADR-0038 with the commands that reproduce it"
```

### Task R1.1.3: `docs/crm.md`, part 1: what the CRM is, service map, vocabulary, identity, data model (§1-§5)

**Files:**
- Create: `docs/crm.md` (this task writes the title, the introduction and sections 1 to 5; Tasks R1.1.4 and R1.1.5 append sections 6 to 12)
- Test: `$R11/check-crm-a.sh` (a throwaway script, not committed, created in Step 1)

**Interfaces:**
- Consumes: `docs/adr/0038-crm-programme-decisions.md` from Task R1.1.1 (the document links to it as `adr/0038-crm-programme-decisions.md`). The authoritative glossary is the foundations design §4.4 (`docs/superpowers/specs/2026-10-07-crm-1-foundations-and-cleanup-design.md`); this task embeds it with every "planned" meaning reworded as "proposed in ADR-0038 (pending D-n)".
- Produces: `docs/crm.md` with the headings `## 1. What the CRM is`, `## 2. Service map`, `## 3. Vocabulary`, `## 4. Identity and scope today`, `## 5. Data model`. The §2 service table has exactly three rows whose first cells are `customer`, `deal` and `lead`, and whose third cell (Dev port) is exactly `8130`, `8131` and `8135`; the §3 glossary table has exactly 21 term rows. Both tables are the ones a later test (R1.2b's `tests/infra/test_crm_doc_matches_contracts.py`) ties to `services/app-gateway/routes.json`, so their cells stay plain.

- [ ] **Step 1: Write the failing check**

```bash check=crm-a
export R11="${TMPDIR:-/tmp}/crm-r1-1-checks"; mkdir -p "$R11"
cat > "$R11/check-crm-a.sh" <<'SH'
#!/usr/bin/env bash
# AC-1.1.4 (sections 1-5, the service table and the glossary) and the citation pass. Run from the repository root.
set -u
FAILED=0
fail()  { printf 'FAIL: %s\n' "$*" >&2; FAILED=1; }
has()   { grep -qF -- "$2" "$1" 2>/dev/null || fail "$1 lacks: $2"; }
hasi()  { grep -qiF -- "$2" "$1" 2>/dev/null || fail "$1 lacks (any case): $2"; }
lacks() { if grep -qF -- "$2" "$1" 2>/dev/null; then fail "$1 still has: $2"; fi; }
cite()  { local a="${2%-*}" b="${2#*-}"; sed -n "${a},${b}p" "$1" 2>/dev/null | grep -qF -- "$3" || fail "$1:$2 does not contain: $3"; }
section() { awk -v h="$2" '$0 ~ "^## " h {f=1; next} /^## /{f=0} f' "$1"; }
links() { local f="$1" d t missing=""; d="$(dirname "$f")"
  for t in $(grep -oE '\]\([^) ]+\)' "$f" | sed -E 's/^\]\(//; s/\)$//; s/#.*$//' | grep -vE '^(https?:|mailto:|$)' | sort -u); do
    [ -e "$d/$t" ] || missing="$missing $t"; done
  [ -z "$missing" ] || fail "$f links to missing:$missing"; }
cites_resolve() { python3 - "$@" <<'PY' || FAILED=1
import re, sys
from pathlib import Path
pat = re.compile(r"(?<![\w./-])((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.(?:py|ts|tsx|json|md|yml|yaml|sh|sql|toml|html|txt)):(\d+)(?:-(\d+))?")
bad = 0
for f in sys.argv[1:]:
    for n, line in enumerate(Path(f).read_text(encoding="utf-8").split("\n"), 1):
        for m in pat.finditer(line):
            p, a, b = m.group(1), int(m.group(2)), int(m.group(3) or m.group(2))
            q = Path(p)
            if not q.is_file():
                print(f"FAIL: {f}:{n} cites a file that does not exist: {p}", file=sys.stderr); bad = 1; continue
            total = sum(1 for _ in q.open(encoding="utf-8", errors="replace"))
            if a < 1 or b < a or b > total:
                print(f"FAIL: {f}:{n} cites {p}:{a}-{b} but the file has {total} lines", file=sys.stderr); bad = 1
sys.exit(bad)
PY
}
secret_scan() { local b h; b="$(mktemp)"; cp .secrets.baseline "$b"
  if [ -x .venv/bin/detect-secrets-hook ]; then h=.venv/bin/detect-secrets-hook; else h="uv run detect-secrets-hook"; fi
  $h --baseline "$b" "$@" >/dev/null 2>&1 || fail "the repo secret scan flags: $* (a hex id inside quotes?)"; rm -f "$b"; }

DOC=docs/crm.md
if [ ! -f "$DOC" ]; then fail "$DOC does not exist"; echo FAILED; exit 1; fi
[ "$(head -n 1 "$DOC")" = '# The CRM: OpsGrids Revenue' ] || fail "the title line is wrong"
prev=0
for h in '## 1. What the CRM is' '## 2. Service map' '## 3. Vocabulary' '## 4. Identity and scope today' '## 5. Data model'; do
  ln="$(grep -nxF -- "$h" "$DOC" | head -n 1 | cut -d: -f1)"
  if [ -z "$ln" ]; then fail "missing heading: $h"; elif [ "$ln" -le "$prev" ]; then fail "heading out of order: $h"; else prev="$ln"; fi
done

# the service table: three rows, the ports the gateway routes
sec2="$(section "$DOC" '2\.')"
[ "$(printf '%s\n' "$sec2" | grep -cE '^\| (customer|deal|lead) \|')" = 3 ] || fail "section 2 does not have exactly three service rows"
for row in 'customer|8130|/api/v1/customers' 'customer|8130|/api/v1/contacts' 'deal|8131|/api/v1/deals' 'lead|8135|/api/v1/leads'; do
  svc="${row%%|*}"; rest="${row#*|}"; port="${rest%%|*}"; prefix="${rest#*|}"
  printf '%s\n' "$sec2" | grep -E "^\| $svc \|" | awk -F'|' '{gsub(/ /,"",$4); print $4}' | grep -qx "$port" || fail "the $svc row does not have the dev port $port"
  printf '%s\n' "$sec2" | grep -E "^\| $svc \|" | grep -qF "\`$prefix\`" || fail "the $svc row does not name \`$prefix\`"
  grep -F "\"prefix\": \"$prefix\"" services/app-gateway/routes.json | grep -qF "localhost:$port" || fail "services/app-gateway/routes.json does not route $prefix to $port"
done

# the glossary: 21 terms
sec3="$(section "$DOC" '3\.')"
[ "$(printf '%s\n' "$sec3" | grep -cE '^\| ' )" = 22 ] || fail "section 3 is not a header row and 21 term rows"
for t in tenant lead company contact deal stage activity follow-up 'owner, rep' roster membership 'employee link' customer 'billed party' 'customer 360' book pipeline 'party, person, rep' scope 'ahead of the producer' 'OpsGrids Revenue, CRM'; do
  printf '%s\n' "$sec3" | grep -qF "| $t |" || fail "the glossary has no row for: $t"
done
lacks "$DOC" 'Planned (D-'
lacks "$DOC" 'will be removed'
hasi "$DOC" 'proposed in ADR-0038 (pending D-5)'
hasi "$DOC" 'proposed in ADR-0038 (pending D-7)'

# the facts the sections state, at the cited lines
cite frontend/product-crm/src/App.tsx 79-79 'OpsGrids Revenue'
cite frontend/opsgrids-client/src/platform/catalogue.ts 47-47 'displayName: "CRM"'
cite frontend/opsgrids-client/src/platform/catalogue.ts 71-71 'revenue.opsgrids.com'
cite services/customer/app/c1_360.py 9-26 'Entitlement-aware'
cite services/customer/app/config.py 9-15 'opsgrids_customer'
cite services/customer/app/config.py 17-23 'run_consumers_in_api'
cite services/customer/app/consumers.py 97-97 'crm.pipeline.activity.logged'
cite services/deal/app/consumers.py 15-15 'deal.events'
cite infrastructure/edge/memory-budget.json 310-312 'api_with_worker'
cite infrastructure/edge/memory-budget.json 320-320 '"deal"'
cite infrastructure/edge/memory-budget.json 321-321 '"lead"'
cite infrastructure/edge/routes.prod.json 106-109 'lead:8000'
cite services/app-gateway/routes.json 79-80 '8133'
cite libs/platform-core/src/platform_core/gateway.py 233-233 'sorted(routes'
cite libs/platform-core/src/platform_core/gateway.py 253-254 'startswith'
cite services/customer/app/schemas.py 50-58 'class OwnerIn'
cite services/customer/app/schemas.py 38-39 'ContactStatus'
cite services/auth/app/tokens.py 108-109 'claims["mem"]'
cite libs/platform-core/src/platform_core/gateway.py 665-672 'x-membership-id'
cite libs/platform-core/src/platform_core/auth.py 68-70 'subject_ref'
cite services/auth/app/services.py 378-378 'subject_ref=membership.employee_id'
cite libs/platform-core/src/platform_core/auth.py 337-341 'sa_false'
cite libs/platform-core/src/platform_core/auth.py 373-373 'owner_id == subject'
cite libs/platform-core/src/platform_core/auth.py 344-353 'is not supported on this resource'
cite libs/platform-core/src/platform_core/auth.py 297-301 'register_scope_resolver'
cite services/deal/app/api/routes.py 112-112 'ctx.subject_ref'
cite services/customer/app/catalogue.py 168-174 'CRM_BOOK.pick'
cite services/deal/app/catalogue.py 150-155 'CRM_PIPELINE.pick'
cite services/lead/app/catalogue.py 172-177 'CRM_LEADS.pick'
cite services/customer/app/models.py 171-171 'email: Mapped[str]'
cite services/customer/app/models.py 222-228 'uq_contacts_org_email'
cite services/customer/app/models.py 208-212 'ondelete="CASCADE"'
cite services/deal/app/domain.py 21-28 'closed_lost'
cite services/lead/app/schemas.py 26-27 'LeadStatus'
cite services/lead/app/services.py 465-468 'phone_index'
cite frontend/opsgrids-dashboard/src/api/index.ts 381-381 'countCrmCustomers'
cite services/customer/app/c1_schemas.py 105-113 'unavailable'
cite services/deal/migrations/versions/e94d7a2c5f18_enable_row_level_security.py 88-107 'trg_activities_append_only'
cite services/customer/migrations/versions/d17e94b06c3a_enable_row_level_security.py 35-50 'processed_events'
cite services/lead/migrations/versions/f2c8a41d9e33_enable_row_level_security.py 35-46 'processed_events'
cite services/lead/app/models.py 148-154 'uq_leads_org_email_index'
cite services/lead/app/models.py 184-184 'uq_assignment_rules_org'

links "$DOC"
cites_resolve "$DOC"
secret_scan "$DOC"
[ "$FAILED" = 0 ] && echo PASS || { echo FAILED; exit 1; }
SH
```

- [ ] **Step 2: Run it and see it fail**

Run: `bash "$R11/check-crm-a.sh"`
Expected: exit status 1; stderr says `FAIL: docs/crm.md does not exist`, stdout says `FAILED`.

- [ ] **Step 3: Create `docs/crm.md` with sections 1 to 5**

Create `docs/crm.md` with exactly this content. Paths are from the repository root, hex ids stay out of quotes, and no table cell contains a pipe.

````markdown create=docs/crm.md
# The CRM: OpsGrids Revenue

This is the one page that says what the CRM is, in the words the rest of the repository should use.
It describes the three CRM services as they are, says which release changes each thing that is
about to change, and ends with how to change it. The decisions behind the CRM overhaul are in
[ADR-0038](adr/0038-crm-programme-decisions.md); the programme contract is
[the programme design](superpowers/specs/2026-10-07-crm-overhaul-programme-design.md); the names a
release must use are in
[the interface registry](superpowers/specs/2026-10-07-crm-overhaul-interface-registry.md).

**How to read it.** What the code or the data shows is written as fact, with a `path:line` that can
be checked; every path is from the repository root. What the programme only proposes is worded
"proposed in ADR-0038 (pending D-n)" and stays a proposal until the owner answers that row: it is
never written as an outcome here. *Inference* marks a statement that no code or test observed.

## 1. What the CRM is

The CRM is one product: the entitlement key `crm`, sold as **OpsGrids Revenue** and labelled "CRM"
in the module switcher (`frontend/opsgrids-client/src/platform/catalogue.ts:47`). Its one front end
is `frontend/product-crm`, which names itself "OpsGrids Revenue"
(`frontend/product-crm/src/App.tsx:79`) and is served from `https://revenue.opsgrids.com`
(`frontend/opsgrids-client/src/platform/catalogue.ts:71`).

Three backend processes serve it. Each has its own Postgres schema and database role, and shares
nothing with the others but the platform libraries and plain UUIDs (ADR-0000 §10, ADR-0017):

- **customer** is the account book: companies, contacts, the rep directory and the Customer 360.
- **deal** is the pipeline: deals and the append-only activity timeline.
- **lead** is intake: leads, the assignment rule, and the conversion of a lead into a company, a
  contact and a deal.

What the CRM is not:

- A tenant is not a CRM company. OpsGrids' own billing customers are tenants
  (`account.Organization`), a different thing from a CRM company that must never be merged with it
  (`P§3` F5).
- ITSM is internal IT. It is deliberately not connected to the CRM for customer data (`P§6`).
- "Sales CRM" is not a product, and `pipeline` and `activity` are not services: ADR-0017 deleted
  them, and `lead` joined `crm` as its third process.
- It is sold on its own: ADR-0008 Q5 says "CRM is sold standalone", so an organization can hold
  `crm` without `hrms`. The Customer 360 treats a section it cannot produce, or whose product the
  organization does not hold, as `null` and lists it in `unavailable`
  (`services/customer/app/c1_360.py:9-26`).

## 2. Service map

| Service | Schema and role | Dev port | Dev gateway prefixes | Production upstream | Memory kind | Entrypoints in `bin/` | Consumer | Tables | ADRs |
|---|---|---|---|---|---|---|---|---|---|
| customer | `customer`, `opsgrids_customer` (`services/customer/app/config.py:9-15`) | 8130 | `/api/v1/customers`, `/api/v1/contacts` | `http://customer:8000` | `api_with_worker`, 99 MB (`infrastructure/edge/memory-budget.json:310`) | `api`, `relay`, `worker`, `cron` | folded into the API when `RUN_CONSUMERS_IN_API` is on (`services/customer/app/config.py:17-23`); one binding, `crm.pipeline.activity.logged` (`services/customer/app/consumers.py:97`) | `owners`, `companies`, `contacts` | ADR-0017, ADR-0008 Q5 |
| deal | `deal`, `opsgrids_deal` | 8131 | `/api/v1/deals` | `http://deal:8000` | `api`, 98 MB (`infrastructure/edge/memory-budget.json:320`) | `api`, `relay`, `worker`, `cron` | none: `services/deal/app/consumers.py` declares the queue `deal.events` and registers no handler, and no container runs it | `deals`, `activities` | ADR-0017 decision 4, ADR-0020 |
| lead | `lead`, `opsgrids_lead` | 8135 | `/api/v1/leads` | `http://lead:8000` | `api`, 98 MB (`infrastructure/edge/memory-budget.json:321`) | `api`, `relay`, `cron` (no `worker`) | none: there is no `app/consumers.py` | `leads`, `assignment_rules` | ADR-0017 |

Each schema also holds the platform's `outbox`, `processed_events` and `audit_logs`.

- **Routing.** The gateway carries `product: crm` on each CRM row, so the entitlement gate applies
  before any CRM code runs. Development rows are `services/app-gateway/routes.json:76-81`;
  production rows are `infrastructure/edge/routes.prod.json:106-109`. A request path is matched with
  `startswith(prefix)`, longest prefix first (`libs/platform-core/src/platform_core/gateway.py:233,253-254`).
- **Two dead development rows.** The development table also routes `/api/v1/campaigns` to port 8132
  and `/api/v1/tickets` to port 8133 under product `crm` (`services/app-gateway/routes.json:79-80`).
  No service of either name exists, nothing else mentions those ports, and the production table has
  neither prefix. R1.2b is planned to remove the two rows.
- **Memory.** The three CRM containers cost 295 MB: the budget prices the core alone at 1772 MB and
  core plus the `crm` profile at 2067 MB (commands in §10).
- **One product key.** All three services contribute to the single `crm` permission manifest,
  `libs/contracts/permissions/crm.json` (§9).

## 3. Vocabulary

"Stored" is what is on the wire or in a column today and is frozen where tenant data carries it;
"label" is what users see.

| Term | Means | Stored or on the wire today | Not to be used for |
|---|---|---|---|
| tenant | An OpsGrids customer organization (`account.Organization`); every row carries its `organization_id` | `organization_id`, claim `act_org` | the CRM's company; new copy never says "customer" for a tenant |
| lead | A pre-qualification intake record that may become a company, a contact and a deal; table `lead.leads` | status `new, working, qualified, disqualified, converted` and source `form, import, manual` (`services/lead/app/schemas.py:26-27`); `/api/v1/leads`; events `crm.lead.*` | a deal stage, a company status or a contact status as a *label*. The stored value `lead` stays on `company.status`, `contact.status` and as the first `deals.stage` key (frozen: events and tenant rules carry it); a later release relabels it in the UI and never rewrites the value |
| company | The **party**: a business or a one-person buyer; table `customer.companies` | status `customer, prospect, lead, churned` (`services/customer/app/schemas.py:38`); URL noun `customers` today (proposed in ADR-0038, pending D-22: `companies`, with the old prefix kept); events `crm.book.company.*` | "customer"; finance's billed party |
| contact | A **person** at a company; table `customer.contacts`. Today `email` is required and unique among live rows (`services/customer/app/models.py:171,222-228`); the phone is sealed (`phone_enc`), masked (`phone`) and blind-indexed (`phone_index`). Phone-only contacts are proposed in ADR-0038 (pending D-14) | status `customer, qualified, lead, dormant` (`services/customer/app/schemas.py:39`); `/api/v1/contacts`; events `crm.book.contact.*` | a lead |
| deal | An opportunity with a value in integer minor units and an ISO currency, a stage, a probability and an expected close date; table `deal.deals` | `/api/v1/deals`; events `crm.pipeline.deal.*` | a lead |
| stage | One step of a deal's pipeline. Today six fixed keys `lead, qualified, proposal, negotiation, closed_won, closed_lost` (`services/deal/app/domain.py:21-28`). Proposed in ADR-0038 (pending D-5): an immutable key with an editable label, order, tone and default probability and an **outcome** (`open`, `won`, `lost`); consumers would test the outcome, never the key | `deals.stage` String(20) | a company or contact status |
| activity | A logged past interaction (call, email, meeting, note, task). Append-only: a database trigger refuses UPDATE and DELETE. Table `deal.activities`; `last_activity_at` on a contact and on a deal is derived from it | `/api/v1/deals/activities` (proposed in ADR-0038, pending D-22: canonical `/api/v1/activities`); event `crm.pipeline.activity.logged` | a to-do; the insights feed (to be called "feed") |
| follow-up | A dated future task for a rep. Not an activity: activities are past-only and append-only. Proposed by the programme (R3.6), not built | none today; a table `deal.follow_ups` is proposed | — |
| owner, rep | The member responsible for a record. **Today** an `owner_id` is a `customer.owners.id` (a generated uuid the public route cannot choose) or, on deals and activities, the caller's `subject_ref`, an HR employee id: a mix (`P§3` F2). Proposed in ADR-0038 (pending D-7): the account membership id | `owner_id` on companies, contacts, deals, activities and leads; `assignment_rules.owner_ids` | an HR employee; the platform `ORG_OWNER` role |
| roster | `customer.owners`: the CRM-local rep directory (`/api/v1/customers/owners`); proposed to become a thin view of members after C15 (pending D-7) | — | — |
| membership | A user's per-organization link (`account.memberships`), carried as the `mem` claim (`Principal.membership_id`); the proposed rep identity (pending D-7) | `mem`, `X-Membership-Id` | — |
| employee link | `memberships.employee_id`, carried as `sref` (`Principal.subject_ref`). Only HRMS-shaped products key OWN on it. CRM compares `owner_id` with it today, which is why OWN is empty in a tenant without HRMS (`P§3` F1) | `sref`, `X-Subject-Ref` | CRM rep identity |
| customer | Reserved for the *relationship status* of a company. For historical reasons it is also the service and schema name, the URL noun for companies, the name of the 360 route and the dashboard "Customers" KPI (`countCrmCustomers`, `frontend/opsgrids-dashboard/src/api/index.ts:381`) | status value `customer`; `/api/v1/customers` | the table name for companies; a tenant |
| billed party | Finance's invoicing profile of a company (`finance.customers`; its `crm_contact_id` bridge is unwired, `P§3` F5) | `finance.customers` | a CRM company |
| customer 360 | The per-contact aggregate `GET /api/v1/customers/{contact_id}/360` (proposed in ADR-0038, pending D-22: `/api/v1/contacts/{id}/360`): sections `customer, crm, orders, finance, support, channels` and a timeline; a section that cannot be produced is `null` and named in `unavailable` | — | an account-level view (a company 360 is proposed for R4.3) |
| book | The module word of `crm.book.*`: customer-service's slice (companies, contacts, owners) | permission codes | a product |
| pipeline | The module word of `crm.pipeline.*`: deal-service's slice (deals, activities). Not a service: `services/pipeline` was deleted (ADR-0017) | permission codes, event types | a service |
| party, person, rep | The identity model (`P§4.2`): company is the party, contact the person, membership the rep. Other products link by `crm_company_id` and `crm_contact_id`, never `customer_id` (proposed in ADR-0038, pending D-14) | none today; the two column names are proposed | — |
| scope | The ladder OWN, TEAM, LOCATION, ORG. CRM implements OWN and ORG; a TEAM or LOCATION grant on a CRM code is accepted by account and refused at runtime (`libs/platform-core/src/platform_core/auth.py:344,353`); TEAM is proposed in ADR-0038 (pending D-24) | grants `code:SCOPE` | — |
| ahead of the producer | A contract term: a committed event schema whose required fields the producer does not emit yet. Eight CRM schemas are (§8) | the schema's `$comment` | — |
| OpsGrids Revenue, CRM | The product: entitlement key `crm`, marketplace and SPA name "OpsGrids Revenue", navigation label "CRM", host `revenue.opsgrids.com` | claim `ent` contains `crm` | — |

## 4. Identity and scope today

Three different ids are called "the owner" in this repository, and the CRM mixes them.

- **The roster id.** `customer.owners.id` is a uuid the model generates; the public route cannot
  choose it, because `OwnerIn` takes a name, an email and a role only
  (`services/customer/app/schemas.py:50-58`). It is the value of `owner_id` on companies, contacts
  and leads, and on deals and activities when a caller names one.
- **The membership id.** A user's per-organization link, carried as the `mem` claim and the
  `X-Membership-Id` header (`services/auth/app/tokens.py:108-109`,
  `libs/platform-core/src/platform_core/gateway.py:665-672`) and named `Principal.membership_id`
  (`libs/platform-core/src/platform_core/auth.py:68`). The CRM's scope does not use it.
- **The employee id.** `memberships.employee_id`, carried as `sref` and named
  `Principal.subject_ref` (`services/auth/app/services.py:378,462`,
  `libs/platform-core/src/platform_core/auth.py:70`). Only an HR link ever sets it.

OWN scope compares a row's `owner_id` with `subject_ref`: `ScopeContext.apply` filters on
`owner_col == subject_ref` and `assert_can_touch` compares the same two values
(`libs/platform-core/src/platform_core/auth.py:341,373`). With no `subject_ref`, an OWN reader
matches nothing (`:337-340`) and an OWN write or detail read is refused (`:372-381`). Deals and
activities default `owner_id` to the caller's `subject_ref` when the caller names none
(`services/deal/app/api/routes.py:112,241-243`).

**The consequence for a tenant without HRMS** (CRM sold on its own): no member has an employee
link, so `subject_ref` is null for everyone. Default reps read at ORG and write, convert and reveal
at OWN (`services/customer/app/catalogue.py:168-174`, `services/deal/app/catalogue.py:150-155`,
`services/lead/app/catalogue.py:172-177`), so their lists stay visible and their OWN actions answer
403. The owner values already on rows are a mix of roster ids and employee ids (`P§3` F2), so a
mapping from one space to another has no automatic basis: the only evidence that ties a roster row
to a person is its email.

The scope-resolver registry does not help. `register_scope_resolver` stores each product's resolver
in a dictionary that nothing reads (`libs/platform-core/src/platform_core/auth.py:297-301`), so the
resolvers registered by `services/customer/app/scope.py`, `services/deal/app/scope.py` and
`services/lead/app/scope.py` are never consulted; every selector passes its own owner column to
`ctx.apply`, which is the real enforcement point.

**What changes, proposed.** Proposed in ADR-0038 (pending D-7, D-8, D-9): R2.2 makes the scope
subject a per-product choice with the employee link as the default, so no other product changes;
R2.6 adds `membership_id` to the roster and an owner-run, human-reviewed mapping plan; R2.7 flips
CRM's scope onto the membership, with the data applied first. Until those ship, OWN in a CRM-only
tenant is empty.

## 5. Data model

Every business table carries `organization_id`, has `ENABLE` and `FORCE ROW LEVEL SECURITY` with a
`tenant_isolation` policy on the transaction-local tenant GUC, and no table holds a foreign key into
another service's schema: references across services are plain UUIDs (ADR-0000 §10).
`processed_events` is deliberately unpolicied because it has no organization column, and a policy
would silently defeat consumer idempotency. The policies are in
`services/customer/migrations/versions/d17e94b06c3a_enable_row_level_security.py:35-50`,
`services/deal/migrations/versions/e94d7a2c5f18_enable_row_level_security.py:41-86` and
`services/lead/migrations/versions/f2c8a41d9e33_enable_row_level_security.py:35-46`.

**customer** (`services/customer/app/models.py`)

- `owners` (`:65-92`): the rep directory, unique on `(organization_id, email)`. `is_active` goes
  false when a rep leaves; a rep is never deleted, because every row that carries the id must keep
  resolving to a name.
- `companies` (`:95-154`): `status` is `customer`, `prospect`, `lead` or `churned`; money is
  `annual_revenue_minor` with an ISO `currency`. The name is unique among live rows
  (`uq_companies_org_name`, case-sensitive); a case-insensitive duplicate is refused by the
  application under an advisory lock, not by the database (ADR-0017 Known limitations).
- `contacts` (`:157-235`): `email` is required, plaintext and unique among live rows. The phone is
  **sealed** (`phone_enc`), **masked** (`phone`) and **blind-indexed** (`phone_index`).
  `company_id` is a real foreign key inside the schema (`ON DELETE CASCADE`).
  `last_activity_at` is advanced from the pipeline's activity events.

**deal** (`services/deal/app/models.py`)

- `deals` (`:50-96`): `value_minor` (a big integer of minor units) with an ISO `currency`, `stage`
  (a string the service layer constrains; it is not an enum column), `probability` 0 to 100,
  `close_date`, `source` and `last_activity_at`, the staleness clock that `log_activity` advances.
  `company_id` and `primary_contact_id` are plain UUIDs into customer.
- `activities` (`:99-138`): **append-only**. The trigger `trg_activities_append_only` refuses UPDATE
  and DELETE (`services/deal/migrations/versions/e94d7a2c5f18_enable_row_level_security.py:88-107`).
  `activity_type` is `call`, `email`, `meeting`, `note` or `task` (the wire field is `type`);
  `direction` is `inbound` or `outbound` for calls and emails and null otherwise; `deal_id` is an
  in-schema foreign key (`ON DELETE CASCADE`) and may be null; `company_id` and `contact_id` are
  plain nullable UUIDs into customer.

**lead** (`services/lead/app/models.py`)

- `leads` (`:69-155`): email and phone are sealed, masked and blind-indexed (`email_enc`,
  `email_masked`, `email_index`, `phone_enc`, `phone_masked`, `phone_index`); `source` and `status`
  hold the vocabularies of §3; `owner_id` is a plain UUID; `converted_company_id`,
  `converted_contact_id`, `converted_deal_id` and `converted_at` are set once, by `:convert`, and
  never change. `conversion_claim_id`, `conversion_claimed_at` and `conversion_generation` make the
  conversion idempotent. The email is unique among live rows that have one (`uq_leads_org_email_index`).
  `phone_index` is written (`services/lead/app/services.py:465-468,496`) and read by nothing: no
  selector uses it and no index covers it.
- `assignment_rules` (`:158-185`): one row per organization (`uq_assignment_rules_org`) holding the
  mode (`round_robin` or `manual`), the ordered `owner_ids` and the `cursor`.

`owner_id` on every table above means what §4 says: a roster id or an employee id, never reliably
one or the other.
````

- [ ] **Step 4: Run the check and see it pass**

Run: `bash "$R11/check-crm-a.sh"`
Expected: `PASS`.

- [ ] **Step 5: Re-run the earlier checks, so nothing broke**

Run: `bash "$R11/check-adr-0038.sh" && bash "$R11/check-costing.sh"`
Expected: `PASS` twice.

- [ ] **Step 6: Commit**

```bash
git add docs/crm.md
git commit -m "Add docs/crm.md: what the CRM is, service map, vocabulary, identity and data model"
```

### Task R1.1.4: `docs/crm.md`, part 2: public API, internal API, events, permissions and roles (§6-§9)

**Files:**
- Modify: `docs/crm.md` (append sections 6 to 9 at the end of the file written by Task R1.1.3)
- Test: `$R11/check-crm-b.sh` (a throwaway script, not committed, created in Step 1)

**Interfaces:**
- Consumes: `docs/crm.md` from Task R1.1.3 (it ends with the sentence "`owner_id` on every table above means what §4 says: a roster id or an employee id, never reliably one or the other."). The committed contracts the tables mirror: `libs/contracts/events/crm.*/v1.schema.json` (11 directories), the `EVT_*` constants of `services/customer/app/services.py`, `services/deal/app/services.py` and `services/lead/app/services.py` (15), `SUBSCRIBABLE_EVENTS` in `services/notification/app/domain.py` (the CRM group is five keys), and `libs/contracts/permissions/crm.json` (17 permissions, 6 role templates).
- Produces: sections `## 6. Public API`, `## 7. Internal API`, `## 8. Events`, `## 9. Permissions and roles`. The §8 event table has 15 rows whose first cell is the event type in backticks (`` `crm.…` ``), and the §9 permission table has 17 rows that start the same way, so a count must be taken per section. The §9 role table has 6 rows whose first cell is the role code in backticks (`` `CRM_…` ``); the four proposed templates are prose, not table rows. R1.2b's `tests/infra/test_crm_doc_matches_contracts.py` will parse exactly these tables.

- [ ] **Step 1: Write the failing check**

```bash check=crm-b
export R11="${TMPDIR:-/tmp}/crm-r1-1-checks"; mkdir -p "$R11"
cat > "$R11/check-crm-b.sh" <<'SH'
#!/usr/bin/env bash
# AC-1.1.4 (event table 15, permission table 17, role table 6) and the citation pass. Run from the repository root.
set -u
FAILED=0
fail()  { printf 'FAIL: %s\n' "$*" >&2; FAILED=1; }
has()   { grep -qF -- "$2" "$1" 2>/dev/null || fail "$1 lacks: $2"; }
hasi()  { grep -qiF -- "$2" "$1" 2>/dev/null || fail "$1 lacks (any case): $2"; }
lacks() { if grep -qF -- "$2" "$1" 2>/dev/null; then fail "$1 still has: $2"; fi; }
cite()  { local a="${2%-*}" b="${2#*-}"; sed -n "${a},${b}p" "$1" 2>/dev/null | grep -qF -- "$3" || fail "$1:$2 does not contain: $3"; }
section() { awk -v h="$2" '$0 ~ "^## " h {f=1; next} /^## /{f=0} f' "$1"; }
links() { local f="$1" d t missing=""; d="$(dirname "$f")"
  for t in $(grep -oE '\]\([^) ]+\)' "$f" | sed -E 's/^\]\(//; s/\)$//; s/#.*$//' | grep -vE '^(https?:|mailto:|$)' | sort -u); do
    [ -e "$d/$t" ] || missing="$missing $t"; done
  [ -z "$missing" ] || fail "$f links to missing:$missing"; }
cites_resolve() { python3 - "$@" <<'PY' || FAILED=1
import re, sys
from pathlib import Path
pat = re.compile(r"(?<![\w./-])((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.(?:py|ts|tsx|json|md|yml|yaml|sh|sql|toml|html|txt)):(\d+)(?:-(\d+))?")
bad = 0
for f in sys.argv[1:]:
    for n, line in enumerate(Path(f).read_text(encoding="utf-8").split("\n"), 1):
        for m in pat.finditer(line):
            p, a, b = m.group(1), int(m.group(2)), int(m.group(3) or m.group(2))
            q = Path(p)
            if not q.is_file():
                print(f"FAIL: {f}:{n} cites a file that does not exist: {p}", file=sys.stderr); bad = 1; continue
            total = sum(1 for _ in q.open(encoding="utf-8", errors="replace"))
            if a < 1 or b < a or b > total:
                print(f"FAIL: {f}:{n} cites {p}:{a}-{b} but the file has {total} lines", file=sys.stderr); bad = 1
sys.exit(bad)
PY
}
secret_scan() { local b h; b="$(mktemp)"; cp .secrets.baseline "$b"
  if [ -x .venv/bin/detect-secrets-hook ]; then h=.venv/bin/detect-secrets-hook; else h="uv run detect-secrets-hook"; fi
  $h --baseline "$b" "$@" >/dev/null 2>&1 || fail "the repo secret scan flags: $* (a hex id inside quotes?)"; rm -f "$b"; }

DOC=docs/crm.md
[ -f "$DOC" ] || { fail "$DOC does not exist"; echo FAILED; exit 1; }
prev=0
for h in '## 5. Data model' '## 6. Public API' '## 7. Internal API' '## 8. Events' '## 9. Permissions and roles'; do
  ln="$(grep -nxF -- "$h" "$DOC" | head -n 1 | cut -d: -f1)"
  if [ -z "$ln" ]; then fail "missing heading: $h"; elif [ "$ln" -le "$prev" ]; then fail "heading out of order: $h"; else prev="$ln"; fi
done
if ! grep -qxF '## 8. Events' "$DOC"; then echo FAILED; exit 1; fi

# the three tables, counted per section
[ "$(section "$DOC" '8\.' | grep -cE '^\| `crm\.')" = 15 ] || fail "section 8 does not have 15 event rows"
[ "$(section "$DOC" '9\.' | grep -cE '^\| `crm\.')" = 17 ] || fail "section 9 does not have 17 permission rows"
[ "$(section "$DOC" '9\.' | grep -cE '^\| `CRM_')" = 6 ] || fail "section 9 does not have 6 role rows"
[ "$(section "$DOC" '7\.' | grep -cE '^\| A[1-9] \|')" = 9 ] || fail "section 7 does not have the nine audited calls A1 to A9"

# the tables agree with the committed contracts and the code (a small version of R1.2b's drift test)
python3 - <<'PY' || FAILED=1
import json, re, sys
from pathlib import Path

doc = Path("docs/crm.md").read_text(encoding="utf-8")

def section(num: str) -> str:
    m = re.search(rf"^## {num}\. .*?$(.*?)(?=^## |\Z)", doc, re.S | re.M)
    return m.group(1) if m else ""

def rows(text: str, first: str) -> list[list[str]]:
    out = []
    for ln in text.split("\n"):
        if ln.startswith("| ") and re.match(first, ln[2:]):
            out.append([c.strip() for c in ln.strip().strip("|").split("|")])
    return out

bad: list[str] = []
# --- events
declared: set[str] = set()
for svc in ("customer", "deal", "lead"):
    declared |= set(re.findall(r'EVT_[A-Z_]+\s*=\s*"(crm\.[a-z_.]+)"', Path(f"services/{svc}/app/services.py").read_text()))
dom = Path("services/notification/app/domain.py").read_text()
tup = dom.split("SUBSCRIBABLE_EVENTS: Final[tuple[str, ...]] = (", 1)[1].split("\n)\n", 1)[0]
subscribable = set(re.findall(r'"(crm\.[a-z_.]+)"', tup))
ev = rows(section("8"), r"`crm\.")
seen = []
for c in ev:
    if len(c) != 7:
        bad.append(f"event row has {len(c)} cells: {c[0]}"); continue
    etype = c[0].strip("`")
    seen.append(etype)
    sp = Path(f"libs/contracts/events/{etype}/v1.schema.json")
    exists = sp.is_file()
    if (c[3] == "yes") != exists:
        bad.append(f"{etype}: schema cell {c[3]!r} but the committed file exists={exists}")
    m = re.match(r"`([^`]*)`", c[2])
    emitted = {x.strip() for x in m.group(1).split(",")} if m else set()
    state = c[4]
    if exists:
        sch = json.loads(sp.read_text())
        required = set(sch["required"])
        if sch.get("$comment", "").startswith("Ahead of the producer"):
            lm = re.search(r"`([^`]*)`", state)
            lacks = {x.strip() for x in lm.group(1).split(",")} if lm else set()
            if not state.startswith("ahead"):
                bad.append(f"{etype}: the schema is ahead of the producer but State says {state!r}")
            if lacks != required - emitted:
                bad.append(f"{etype}: State lacks {sorted(lacks)}, the schema requires beyond the payload {sorted(required - emitted)}")
        else:
            if not state.startswith("conforms"):
                bad.append(f"{etype}: the schema conforms but State says {state!r}")
            if required - emitted:
                bad.append(f"{etype}: the schema requires {sorted(required - emitted)} that the payload lacks")
    elif not state.startswith("no schema"):
        bad.append(f"{etype}: there is no schema but State says {state!r}")
    if (c[6] == "yes") != (etype in subscribable):
        bad.append(f"{etype}: Subscribable says {c[6]!r}, SUBSCRIBABLE_EVENTS says {etype in subscribable}")
if sorted(seen) != sorted(declared) or len(seen) != 15:
    bad.append(f"the event table lists {len(seen)} types and the services declare {len(declared)}: {sorted(set(seen) ^ declared)}")

# --- permissions and role templates
man = json.loads(Path("libs/contracts/permissions/crm.json").read_text())
svc_of = {"book": "customer", "pipeline": "deal", "lead": "lead"}
want = {f"crm.{p['module']}.{p['resource']}.{p['action']}": p for p in man["permissions"]}
got = {c[0].strip("`"): c for c in rows(section("9"), r"`crm\.")}
if set(got) != set(want) or len(want) != 17:
    bad.append(f"the permission table and the manifest differ: {sorted(set(got) ^ set(want))}")
for code, p in want.items():
    c = got.get(code)
    if c is None:
        continue
    expect = [p["module"], svc_of[p["module"]], p["default_scope"], p["max_scope"], "yes" if p["is_dangerous"] else "no", p["description"]]
    if c[1:] != expect:
        bad.append(f"{code}: row {c[1:]} differs from the manifest {expect}")
roles = {c[0].strip("`"): c for c in rows(section("9"), r"`CRM_")}
role_svc = {"CRM_BOOK_ADMIN": "customer", "CRM_SALES_REP": "customer", "CRM_PIPELINE_ADMIN": "deal", "CRM_DEAL_REP": "deal", "CRM_LEADS_ADMIN": "lead", "CRM_LEAD_REP": "lead"}
words = {8: "eight", 5: "five", 4: "four"}
if set(roles) != {t["code"] for t in man["role_templates"]}:
    bad.append(f"the role table and the manifest differ: {sorted(set(roles) ^ {t['code'] for t in man['role_templates']})}")
for t in man["role_templates"]:
    c = roles.get(t["code"])
    if c is None:
        continue
    if c[1:5] != [t["name"], role_svc[t["code"]], "yes" if t["is_admin"] else "no", "yes" if t["is_default"] else "no"]:
        bad.append(f"{t['code']}: name, service, admin or default differs from the manifest: {c[1:5]}")
    if t["is_admin"]:
        if words[len(t["grants"])] not in c[5] or "ORG" not in c[5]:
            bad.append(f"{t['code']}: the Grants cell does not say it holds {len(t['grants'])} codes at ORG")
    else:
        for g in t["grants"]:
            short = g["code"].split(".", 2)[2]
            if f"{short} {g['scope']}" not in c[5]:
                bad.append(f"{t['code']}: the Grants cell lacks {short} {g['scope']}")
    if "CRM_ADMIN" not in c[6] and "CRM_REP" not in c[6]:
        bad.append(f"{t['code']}: no planned mapping")
for b in bad:
    print("FAIL:", b, file=sys.stderr)
sys.exit(1 if bad else 0)
PY

# the facts the sections state, at the cited lines
cite services/customer/app/api/internal.py 69-74 'include_in_schema=False'
cite services/deal/app/api/internal.py 45-52 'include_in_schema=False'
cite services/customer/app/api/internal.py 95-107 '_require_caller'
cite services/customer/app/api/internal.py 110-121 '_require_org_claim'
cite services/customer/app/api/internal.py 138-139 'messaging-target'
cite services/customer/app/api/internal.py 197-198 'contacts:resolve'
cite services/customer/app/api/internal.py 244-245 '/contacts/{contact_id}'
cite services/customer/app/api/internal.py 271-272 '_require_caller(request, "finance")'
cite services/customer/app/api/internal.py 290-291 'leads:convert'
cite services/customer/app/api/internal.py 351-352 'owners:validate'
cite services/deal/app/api/internal.py 94-95 'leads:convert'
cite services/notification/app/api/c1_internal.py 81-86 'messaging-summary'
cite services/customer/app/c1_360.py 166-170 '/deals'
cite services/customer/app/c1_360.py 200-203 'messaging-summary'
cite services/lead/app/clients.py 104-105 'leads:convert'
cite services/lead/app/clients.py 184-185 'leads:convert'
cite services/lead/app/clients.py 249-250 'owners:validate'
cite services/finance/app/clients.py 66-67 '/contacts/'
cite services/finance/app/config.py 52-52 'customer_internal_url'
cite services/operations/app/orders/services.py 871-892 'return None'
cite services/notification/app/targets.py 73-80 'mint_service_token'
cite services/notification/app/contacts.py 74-81 'mint_service_token'
cite services/notification/app/memberships.py 48-55 'mint_service_token'
cite services/notification/app/memberships.py 62-64 'return False'
cite services/notification/app/config.py 91-91 'account_internal_url'
cite infrastructure/edge/compose.prod.yml 103-103 'ACCOUNT_SERVICE_URL'
cite services/account/app/api/internal.py 304-346 'membership_lookup'
cite services/app-gateway/routes.json 76-81 '/api/v1/leads'
cite libs/platform-core/src/platform_core/app.py 362-363 'prefix="/api/v1"'
cite libs/platform-core/src/platform_core/pagination.py 23-52 'le=200'
cite services/deal/app/api/routes.py 73-75 'static paths'
cite services/customer/app/api/routes.py 125-125 '/customer/info'
cite services/deal/app/api/routes.py 66-66 '/deal/info'
cite services/lead/app/api/routes.py 123-123 '/lead/info'
cite services/customer/app/api/c1_customer360.py 41-46 '/360'
cite services/customer/app/api/routes.py 61-61 'require_product("crm")'
cite services/deal/app/api/routes.py 57-57 'require_product("crm")'
cite services/lead/app/api/routes.py 65-65 'require_product("crm")'
cite services/customer/app/api/c1_customer360.py 37-37 'require_product("crm")'
cite libs/platform-core/src/platform_core/events.py 203-224 'def validate_event_schema'
cite libs/platform-core/src/platform_core/events.py 250-262 'silently dropped'
cite libs/platform-core/src/platform_core/events.py 400-400 'published_at'
cite libs/platform-core/src/platform_core/events.py 453-471 'queue.bind'
cite services/notification/app/consumers.py 324-325 'SUBSCRIBABLE_EVENTS'
cite services/notification/app/domain.py 146-151 'crm.pipeline.deal.stage_changed'
cite services/notification/app/domain.py 365-365 'SUBSCRIBABLE_EVENTS'
cite services/notification/app/a1_selectors.py 208-223 'find_contracts_dir'
cite services/notification/app/a1_selectors.py 260-284 'trigger_catalogue'
cite .dockerignore 29-32 'libs/contracts'
cite services/notification/app/domain.py 276-301 'Unresolved placeholders'
cite services/notification/app/services.py 841-850 'notification.rule.render_failed'
cite libs/contracts/tests/test_event_schemas.py 58-114 'conversion_aborted'
cite services/lead/tests/test_lead_lifecycle_races.py 269-269 'validate_event_schema'
cite services/lead/tests/test_lead_lifecycle_races.py 292-292 'validate_event_schema'
cite services/customer/app/services.py 482-488 '"status": status'
cite services/customer/app/services.py 552-556 '"changed": sorted(changes)'
cite services/customer/app/services.py 603-603 '"company_id": str(company.id)'
cite services/customer/app/services.py 695-701 '"full_name": full_name'
cite services/customer/app/services.py 763-767 '"changed": sorted(changes)'
cite services/customer/app/services.py 806-806 '"contact_id": str(contact.id)'
cite services/deal/app/services.py 175-183 '"value_minor": value_minor'
cite services/deal/app/services.py 247-251 '"changed": sorted(changes)'
cite services/deal/app/services.py 291-291 '"deal_id": str(deal.id)'
cite services/deal/app/services.py 339-344 '"to": new_stage'
cite services/deal/app/services.py 428-435 '"occurred_at": at.isoformat()'
cite services/lead/app/services.py 538-543 '"source": source'
cite services/lead/app/services.py 647-651 '"changed": sorted(changes)'
cite services/lead/app/services.py 1199-1205 '"deal_id": str(deal_id)'
cite services/lead/app/services.py 1156-1161 '**orphans'
cite services/customer/app/catalogue.py 104-143 'read_sensitive'
cite services/customer/app/catalogue.py 156-156 'CRM_BOOK.all("ORG")'
cite services/deal/app/catalogue.py 139-139 'CRM_PIPELINE.all("ORG")'
cite services/lead/app/catalogue.py 160-160 'CRM_LEADS.all("ORG")'
cite services/customer/app/catalogue.py 182-201 'def validate'
cite services/customer/app/api/routes.py 68-68 'minimum=Scope.ORG'
cite services/customer/app/api/routes.py 80-80 'STEP_UP_AMR'
cite services/lead/app/api/routes.py 78-78 'STEP_UP_AMR'
cite services/lead/app/services.py 181-182 'requires ORG scope'
cite services/customer/app/config.py 34-43 'reveal_daily_limit'

# wording rules: a proposal is never an outcome
hasi "$DOC" 'proposed in ADR-0038 (pending D-20)'
hasi "$DOC" 'proposed in ADR-0038 (pending D-22)'
lacks "$DOC" 'will be removed'

links "$DOC"
cites_resolve "$DOC"
secret_scan "$DOC"
[ "$FAILED" = 0 ] && echo PASS || { echo FAILED; exit 1; }
SH
```

- [ ] **Step 2: Run it and see it fail**

Run: `bash "$R11/check-crm-b.sh"`
Expected: exit status 1; the first lines are `FAIL: missing heading: ## 6. Public API`, `FAIL: missing heading: ## 7. Internal API`, `FAIL: missing heading: ## 8. Events`, `FAIL: missing heading: ## 9. Permissions and roles` and `FAILED` (the script stops after the heading check when `## 8. Events` is absent).

- [ ] **Step 3: Append sections 6 to 9 to `docs/crm.md`**

Append the following text to the end of `docs/crm.md` (it begins with a blank line, which separates it from section 5).

````markdown append=docs/crm.md

## 6. Public API

The contract is the three committed OpenAPI documents, `libs/contracts/openapi/customer.json`,
`libs/contracts/openapi/deal.json` and `libs/contracts/openapi/lead.json`. This file lists no
endpoint, because a table here would go stale at the next route. `make openapi` regenerates the
documents and the permission manifests from the code, and `make openapi-check` fails when one is
behind.

- **Prefixes.** Production routes four CRM prefixes: `/api/v1/customers` and `/api/v1/contacts` to
  customer, `/api/v1/deals` to deal and `/api/v1/leads` to lead
  (`infrastructure/edge/routes.prod.json:106-109`). The development table has the same four and the
  two dead rows of §2 (`services/app-gateway/routes.json:76-81`). Every public router is mounted
  under `/api/v1` by `create_app` (`libs/platform-core/src/platform_core/app.py:362-363`), and a new
  public path must start with a routed prefix or rows are added to both tables.
- **Entitlement.** Every route requires the `crm` entitlement, through `require_product("crm")` on
  each router (`services/customer/app/api/routes.py:61`,
  `services/customer/app/api/c1_customer360.py:37`, `services/deal/app/api/routes.py:57`,
  `services/lead/app/api/routes.py:65`), and a permission with its scope (§9).
- **Lists** answer `Page[T]`: `items`, `total`, `page`, `size` and `pages`. `page` is at least 1 and
  `size` is 1 to 200, 25 by default (`libs/platform-core/src/platform_core/pagination.py:23-52`).
- **Actions on one record** use a colon verb: `POST /api/v1/leads/{lead_id}:convert`,
  `POST /api/v1/leads/{lead_id}:reveal` and `POST /api/v1/contacts/{contact_id}/phone:reveal`. A
  static path such as `GET /api/v1/deals/summary` or `/api/v1/deals/activities` is declared before
  the `{id}` route so that it is never read as an id (`services/deal/app/api/routes.py:73-75`).
- **Nouns today.** The company URL is `/customers`, the Customer 360 is
  `GET /api/v1/customers/{contact_id}/360` (`services/customer/app/api/c1_customer360.py:41-46`) and
  the timeline lives at `/api/v1/deals/activities`. Proposed in ADR-0038 (pending D-22):
  `/companies` canonical with `/customers` aliased, the 360 under `/contacts/{id}/360`, and
  `/activities` aliased.
- **The `info` routes.** `GET /api/v1/customer/info`, `/api/v1/deal/info` and `/api/v1/lead/info`
  are in the documents but unreachable through the gateway, because `/api/v1/customer/info` does not
  start with the prefix `/api/v1/customers` (`services/customer/app/api/routes.py:125`,
  `services/deal/app/api/routes.py:66`, `services/lead/app/api/routes.py:123`). They are one
  instance of a scaffold that 32 services carry (32 `ServiceInfo` schemas and 32 `/info` routes): a
  platform item, not the CRM's to remove.

## 7. Internal API

Service-to-service calls carry a short-lived service token addressed to the callee by name. They
reach `/internal/v1/...` routes that are mounted at the root, excluded from the OpenAPI documents
(`include_in_schema=False`) and guarded by `require_service_token`
(`services/customer/app/api/internal.py:69-74`, `services/deal/app/api/internal.py:45-52`). Two
more checks exist on some routes: `_require_caller`, an allow-list of calling services
(`services/customer/app/api/internal.py:95-107`), and `_require_org_claim`, which demands that the
token's `org` claim names the organization in the path (`:110-121`).

**The routes the CRM serves**, all under `/internal/v1/organizations/{organization_id}`:

| Route | Served by | Caller allow-list | Org-claim check | Called by |
|---|---|---|---|---|
| `GET /contacts/{contact_id}/messaging-target` (`services/customer/app/api/internal.py:138`) | customer | none | none | notification (A7) |
| `POST /contacts:resolve` (`services/customer/app/api/internal.py:197`) | customer | none | none | notification (A8) |
| `GET /contacts/{contact_id}` (`services/customer/app/api/internal.py:244`) | customer | `finance` | yes | finance (A6) |
| `POST /leads:convert` (`services/customer/app/api/internal.py:290`) | customer | `lead` | yes | lead (A3) |
| `POST /owners:validate` (`services/customer/app/api/internal.py:351`) | customer | `lead` | yes | lead (A5) |
| `POST /leads:convert` (`services/deal/app/api/internal.py:94`) | deal | `lead` | yes | lead (A4) |

The first two are the only CRM routes with neither check: any validly signed service token reaches a
contact's sealed number through them. Hardening both is registered for R3.1 and must follow the
organization claim that R1.2a is planned to add to notification's tokens (registry `J13`).

**Every place a CRM service, finance or notification calls another service** (found with
`grep "_internal_url\|_service_url" services/{customer,deal,lead,finance,notification}/app`,
leaving out `config.py`; `deal` builds none). R1.2a is planned to add a test that registers these
nine calls and probes each against the real callee.

| # | Caller | Calls | Client code | Verdict |
|---|---|---|---|---|
| A1 | customer's 360 | deal `GET /api/v1/deals`, forwarding the caller's identity | `services/customer/app/c1_360.py:166-170` builds `{deal_internal_url}/deals` | **path defect: 404** (inference from the route table, §11); fix planned for R1.2a |
| A2 | customer's 360 | notification `GET /internal/v1/contacts/{contact_id}/messaging-summary` | `services/customer/app/c1_360.py:200-203`; route `services/notification/app/api/c1_internal.py:81-86` | correct |
| A3 | lead | customer `POST …/leads:convert` | `services/lead/app/clients.py:104-105` | correct |
| A4 | lead | deal `POST …/leads:convert` | `services/lead/app/clients.py:184-185` | correct |
| A5 | lead | customer `POST …/owners:validate` | `services/lead/app/clients.py:249-250` | correct |
| A6 | finance | customer `GET …/contacts/{contact_id}` | `services/finance/app/clients.py:66-67` | path correct; **unwired**: finance has no `CUSTOMER_INTERNAL_URL` in compose, so it falls to `http://localhost:8130` (`services/finance/app/config.py:52`); latent, because nothing calls it (`services/operations/app/orders/services.py:871-892` returns `None`) |
| A7 | notification | customer `GET …/contacts/{contact_id}/messaging-target` | `services/notification/app/targets.py:73-76`; its token carries no `org` claim (`:78-80`) | correct; claim planned for R1.2a |
| A8 | notification | customer `POST …/contacts:resolve` | `services/notification/app/contacts.py:74-77`; no `org` claim (`:79-81`) | correct; claim planned for R1.2a |
| A9 | notification | account `GET …/memberships/{membership_id}` | `services/notification/app/memberships.py:48-51`; no `org` claim (`:53-55`); route `services/account/app/api/internal.py:304-346` | path correct; **unwired** (inference): compose sets no `ACCOUNT_INTERNAL_URL` (it sets `ACCOUNT_SERVICE_URL`, `infrastructure/edge/compose.prod.yml:103`) and the default is `http://localhost:8111` (`services/notification/app/config.py:91`), and `membership_is_active` answers `False` on a connection error (`services/notification/app/memberships.py:62-64`), so in production an Engage conversation assignment would be refused; claim planned for R1.2a |

Seventeen further files in nine other services build internal URLs from the same settings; nobody
has audited them (the list is the baseline that R1.2a's guard test is planned to record).

## 8. Events

Fifteen event types are published (`services/customer/app/services.py:119-124`,
`services/deal/app/services.py:43-47`, `services/lead/app/services.py:71-77`). Eleven have a
committed schema, `libs/contracts/events/<type>/v1.schema.json`; eight of those are **ahead of the
producer**: their own `$comment` names the required fields the producer does not emit yet. Four have
no schema. In the table, *Emitted today* is the payload the producer builds, *State* compares it with
the schema's required list (`conforms`, `ahead` with the missing fields, or `no schema`), and
*Subscribable* says whether notification's rule engine lists the key.

| Event | Producer | Emitted today | Schema | State | Consumers today | Subscribable |
|---|---|---|---|---|---|---|
| `crm.book.company.created` | customer | `organization_id, company_id, name, status, owner_id` (`services/customer/app/services.py:482-488`) | yes | ahead: lacks `created_at` | notification | yes |
| `crm.book.company.updated` | customer | `organization_id, company_id, changed` (`services/customer/app/services.py:552-556`) | yes | ahead: lacks `name, status, owner_id` | none | no |
| `crm.book.company.deleted` | customer | `organization_id, company_id` (`services/customer/app/services.py:603`) | yes | conforms | none | no |
| `crm.book.contact.created` | customer | `organization_id, contact_id, company_id, full_name, status` (`services/customer/app/services.py:695-701`) | none | no schema (R1.2b plans one) | notification | yes |
| `crm.book.contact.updated` | customer | `organization_id, contact_id, changed` (`services/customer/app/services.py:763-767`) | none | no schema (R1.2b plans one) | none | no |
| `crm.book.contact.deleted` | customer | `organization_id, contact_id` (`services/customer/app/services.py:806`) | none | no schema (R1.2b plans one) | none | no |
| `crm.pipeline.deal.created` | deal | `organization_id, deal_id, name, stage, value_minor, currency, owner_id` (`services/deal/app/services.py:175-183`) | yes | ahead: lacks `company_id, close_date, probability, closed_at, created_at` | notification | yes |
| `crm.pipeline.deal.updated` | deal | `organization_id, deal_id, changed` (`services/deal/app/services.py:247-251`) | yes | ahead: lacks `name, stage, value_minor, currency, owner_id, company_id, close_date, probability, closed_at` | none | no |
| `crm.pipeline.deal.deleted` | deal | `organization_id, deal_id` (`services/deal/app/services.py:291`) | yes | conforms | none | no |
| `crm.pipeline.deal.stage_changed` | deal | `organization_id, deal_id, from, to` (`services/deal/app/services.py:339-344`) | yes | ahead: lacks `name, value_minor, currency, owner_id, probability, closed_at` | notification | yes |
| `crm.pipeline.activity.logged` | deal | `organization_id, activity_id, type, deal_id, contact_id, occurred_at` (`services/deal/app/services.py:428-435`) | none | no schema (R1.2b plans one) | customer, notification | yes |
| `crm.lead.created` | lead | `organization_id, lead_id, source, owner_id` (`services/lead/app/services.py:538-543`) | yes | ahead: lacks `status` | none | no |
| `crm.lead.updated` | lead | `organization_id, lead_id, changed` (`services/lead/app/services.py:647-651`) | yes | ahead: lacks `status, source, owner_id, converted_at` | none | no |
| `crm.lead.converted` | lead | `organization_id, lead_id, company_id, contact_id, deal_id` (`services/lead/app/services.py:1199-1205`) | yes | ahead: lacks `owner_id, converted_at` | none | no |
| `crm.lead.conversion_aborted` | lead | `organization_id, lead_id, reason, company_id, contact_id, deal_id` (`services/lead/app/services.py:1156-1161`) | yes | conforms; tested against its producer (`services/lead/tests/test_lead_lifecycle_races.py:269,292`) | none | no |

- **Publishing.** A producer stages the event in its `outbox` in the same transaction as the change,
  and the relay publishes it after commit. An event with no bound queue is silently dropped while its
  outbox row is marked published (`libs/platform-core/src/platform_core/events.py:250-262,400`), and
  a queue exists only once its consumer has run (`:453-471`). Every `crm.lead.*` event emitted so far
  was therefore dropped by the broker: no consumer binds them.
- **Notification.** It binds every key of `SUBSCRIBABLE_EVENTS` unconditionally when it starts
  (`services/notification/app/consumers.py:324-325`), and a rule may name only a key of that list
  (`services/notification/app/domain.py:365`). The CRM group is five keys with no lead key
  (`services/notification/app/domain.py:146-151`).
- **Contracts.** Schemas are additive within `v1` and use `additionalProperties: false`;
  `platform_core.events.validate_event_schema` is the validator
  (`libs/platform-core/src/platform_core/events.py:203-224`). Only `lead` has a producer-side schema
  test, for the abort event; customer and deal have none. The static contracts test requires a
  schema only for the `engage.*` events and for `crm.lead.conversion_aborted`
  (`libs/contracts/tests/test_event_schemas.py:58-114`). R1.2b is planned to close both gaps for the
  CRM.
- **Variables.** In a checkout, Engage's automation builder lists a trigger's variables from the
  committed schema's `properties` (`services/notification/app/a1_selectors.py:260-284`), so an
  "ahead" schema offers variables the producer does not send. On the box it lists none for any
  trigger: `libs/contracts` is excluded from every image (`.dockerignore:29-32`), so
  `find_contracts_dir` finds nothing (`services/notification/app/a1_selectors.py:208-223`). The
  committed schemas document and test the contract; they do not feed the production builder.
  Rendering is strict: a placeholder that resolves to nothing raises, the rule logs
  `notification.rule.render_failed` and stages nothing (`services/notification/app/domain.py:276-301`,
  `services/notification/app/services.py:841-850`).
- **Frozen.** The event types and, on `crm.book.contact.created`, the legacy `full_name`: a tenant
  automation may already render it, so it is documented as frozen and a new consumer reads
  `contact_id` and asks customer for anything else.

## 9. Permissions and roles

Seventeen permission codes, declared in each service's catalogue
(`services/customer/app/catalogue.py:104-143`, `services/deal/app/catalogue.py:104-126`,
`services/lead/app/catalogue.py:113-147`) and merged into the one committed manifest
`libs/contracts/permissions/crm.json`. The default scope is what a role template gets when it names
the code without a scope; the maximum is a ladder ceiling, not an allowed set, so a grant below it is
accepted.

| Code | Slice | Service | Default scope | Maximum scope | Dangerous | Description |
|---|---|---|---|---|---|---|
| `crm.book.company.read` | book | customer | OWN | ORG | no | See companies and the rep directory |
| `crm.book.company.write` | book | customer | OWN | ORG | no | Create and update companies |
| `crm.book.company.delete` | book | customer | OWN | ORG | yes | Delete a company |
| `crm.book.contact.read` | book | customer | OWN | ORG | no | See contacts |
| `crm.book.contact.write` | book | customer | OWN | ORG | no | Create and update contacts |
| `crm.book.contact.delete` | book | customer | OWN | ORG | yes | Delete a contact |
| `crm.book.contact.read_sensitive` | book | customer | OWN | ORG | yes | Reveal a contact's real phone number, audited |
| `crm.book.owner.write` | book | customer | ORG | ORG | no | Add a rep to the directory |
| `crm.pipeline.deal.read` | pipeline | deal | OWN | ORG | no | See deals and the pipeline summary |
| `crm.pipeline.deal.write` | pipeline | deal | OWN | ORG | no | Create deals and move them through stages |
| `crm.pipeline.deal.delete` | pipeline | deal | OWN | ORG | yes | Delete a deal |
| `crm.pipeline.activity.read` | pipeline | deal | OWN | ORG | no | See the activity timeline |
| `crm.pipeline.activity.write` | pipeline | deal | OWN | ORG | no | Log calls, emails, meetings and notes |
| `crm.lead.lead.read` | lead | lead | OWN | ORG | no | See leads |
| `crm.lead.lead.write` | lead | lead | OWN | ORG | no | Create and update leads |
| `crm.lead.lead.convert` | lead | lead | OWN | ORG | no | Convert a qualified lead into a company and deal |
| `crm.lead.lead.read_sensitive` | lead | lead | OWN | ORG | yes | Reveal a lead's real phone number or email, audited |

- `crm.book.owner.write` is floored at ORG on its route: an OWN grant is refused outright
  (`services/customer/app/api/routes.py:68`). Configuring the lead assignment rule is refused unless
  the caller's scope is ORG (`services/lead/app/services.py:181-182`).
- The two `read_sensitive` codes open a sealed value only on a stepped-up request: the route demands
  both the `otp` method and a step-up marker (`services/customer/app/api/routes.py:80`,
  `services/lead/app/api/routes.py:78`), writes an `entity.sensitive_read` audit row naming the field
  and never its value, and is bounded by a per-user reveal budget that customer and lead share
  (`services/customer/app/config.py:34-43`). `is_dangerous` is metadata, not a control: what
  constrains a reveal is the step-up, the scope applied to the one record, and the audit row
  (ADR-0000 §5, note added 2026-09-17).
- Every code is frozen: codes are customer data once tenants author roles, there is no rename
  mechanism, and the removal tool ADR-0007 specifies (`tools/revoke_permission.py`, open-questions
  register row 14) does not exist. A new code reaches live tenants admin-first through the
  reconcile of ADR-0007 (`make edge-reconcile`, dry run first), and only then does a UI show its
  control.

**Role templates.** Each service declares its own slice, and an administrator template is built from
that slice alone, never from every permission that exists (ADR-0000 §5);
`validate()` refuses a template that holds another product's code
(`services/customer/app/catalogue.py:182-201`).

| Code | Name | Service | Admin | Default | Grants | Planned mapping (proposed in ADR-0038, pending D-20) |
|---|---|---|---|---|---|---|
| `CRM_BOOK_ADMIN` | CRM administrator (book) | customer | yes | no | all eight `crm.book.*` codes at ORG (`services/customer/app/catalogue.py:156`) | `CRM_ADMIN` |
| `CRM_SALES_REP` | Sales representative | customer | no | yes | company.read ORG, company.write OWN, contact.read ORG, contact.write OWN, contact.read_sensitive OWN | `CRM_REP` |
| `CRM_PIPELINE_ADMIN` | CRM administrator (pipeline) | deal | yes | no | all five `crm.pipeline.*` codes at ORG (`services/deal/app/catalogue.py:139`) | `CRM_ADMIN` |
| `CRM_DEAL_REP` | Sales representative | deal | no | yes | deal.read ORG, deal.write OWN, activity.read ORG, activity.write OWN | `CRM_REP` |
| `CRM_LEADS_ADMIN` | CRM administrator (leads) | lead | yes | no | all four `crm.lead.lead.*` codes at ORG (`services/lead/app/catalogue.py:160`) | `CRM_ADMIN` |
| `CRM_LEAD_REP` | Sales representative | lead | no | yes | lead.read ORG, lead.write OWN, lead.convert OWN, lead.read_sensitive OWN | `CRM_REP` |

The three representative templates read the shared book at ORG and write, convert and reveal at OWN,
which is why OWN matters (§4). The three administrator templates are `is_admin`, so a new
activation hands each to the organization's owners.

**Proposed in ADR-0038 (pending D-20)**, the legacy templates stay and four templates are added by
same-code merging across the three catalogues:

- `CRM_ADMIN`: everything in the catalogue at ORG, and the only template that configures anything.
- `CRM_MANAGER`: works the shared book and edits what their team owns (TEAM scope is proposed
  separately, pending D-24).
- `CRM_REP`: works the shared book and edits what they own; the default for a new member.
- `CRM_VIEWER`: read-only, with no writes, no reveal and no money.

The six legacy role codes and the 17 permission codes stay frozen. The three legacy representative
templates would stop being the default for new activations only; a live tenant's existing roles are
not rewritten, because a reconcile creates missing roles and widens grants and never narrows
(ADR-0007).
````

- [ ] **Step 4: Run the check and see it pass**

Run: `bash "$R11/check-crm-b.sh"`
Expected: `PASS`.

- [ ] **Step 5: Re-run Task R1.1.3's check, so the append broke nothing**

Run: `bash "$R11/check-crm-a.sh"`
Expected: `PASS`.

- [ ] **Step 6: Commit**

```bash
git add docs/crm.md
git commit -m "Extend docs/crm.md with the public and internal API, the event table and the permission and role tables"
```

### Task R1.1.5: `docs/crm.md`, part 3: runtime and operations, known limitations, how to change this (§10-§12)

**Files:**
- Modify: `docs/crm.md` (append sections 10 to 12 at the end of the file, after Task R1.1.4's section 9)
- Test: `$R11/check-crm-c.sh` (a throwaway script, not committed, created in Step 1)

**Interfaces:**
- Consumes: `docs/crm.md` from Tasks R1.1.3 and R1.1.4 (it ends with the sentence "not rewritten, because a reconcile creates missing roles and widens grants and never narrows" followed by "(ADR-0007)."). The D-11 costing section of `docs/adr/0038-crm-programme-decisions.md` from Task R1.1.2 (§10 summarises it and links to it). The repo's memory CLI `infrastructure/edge/lib/memory_budget_totals.py`.
- Produces: sections `## 10. Runtime and operations`, `## 11. Known limitations` and `## 12. How to change this`, which complete `docs/crm.md` at twelve `## ` sections (AC-1.1.4). Later releases edit the row that their change affects and cite §12's checklist.

- [ ] **Step 1: Write the failing check**

```bash check=crm-c
export R11="${TMPDIR:-/tmp}/crm-r1-1-checks"; mkdir -p "$R11"
cat > "$R11/check-crm-c.sh" <<'SH'
#!/usr/bin/env bash
# AC-1.1.4 in full (twelve sections and the five table counts) and the citation pass. Run from the repository root.
set -u
FAILED=0
fail()  { printf 'FAIL: %s\n' "$*" >&2; FAILED=1; }
has()   { grep -qF -- "$2" "$1" 2>/dev/null || fail "$1 lacks: $2"; }
hasi()  { grep -qiF -- "$2" "$1" 2>/dev/null || fail "$1 lacks (any case): $2"; }
lacks() { if grep -qF -- "$2" "$1" 2>/dev/null; then fail "$1 still has: $2"; fi; }
cite()  { local a="${2%-*}" b="${2#*-}"; sed -n "${a},${b}p" "$1" 2>/dev/null | grep -qF -- "$3" || fail "$1:$2 does not contain: $3"; }
section() { awk -v h="$2" '$0 ~ "^## " h {f=1; next} /^## /{f=0} f' "$1"; }
links() { local f="$1" d t missing=""; d="$(dirname "$f")"
  for t in $(grep -oE '\]\([^) ]+\)' "$f" | sed -E 's/^\]\(//; s/\)$//; s/#.*$//' | grep -vE '^(https?:|mailto:|$)' | sort -u); do
    [ -e "$d/$t" ] || missing="$missing $t"; done
  [ -z "$missing" ] || fail "$f links to missing:$missing"; }
cites_resolve() { python3 - "$@" <<'PY' || FAILED=1
import re, sys
from pathlib import Path
pat = re.compile(r"(?<![\w./-])((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.(?:py|ts|tsx|json|md|yml|yaml|sh|sql|toml|html|txt)):(\d+)(?:-(\d+))?")
bad = 0
for f in sys.argv[1:]:
    for n, line in enumerate(Path(f).read_text(encoding="utf-8").split("\n"), 1):
        for m in pat.finditer(line):
            p, a, b = m.group(1), int(m.group(2)), int(m.group(3) or m.group(2))
            q = Path(p)
            if not q.is_file():
                print(f"FAIL: {f}:{n} cites a file that does not exist: {p}", file=sys.stderr); bad = 1; continue
            total = sum(1 for _ in q.open(encoding="utf-8", errors="replace"))
            if a < 1 or b < a or b > total:
                print(f"FAIL: {f}:{n} cites {p}:{a}-{b} but the file has {total} lines", file=sys.stderr); bad = 1
sys.exit(bad)
PY
}
secret_scan() { local b h; b="$(mktemp)"; cp .secrets.baseline "$b"
  if [ -x .venv/bin/detect-secrets-hook ]; then h=.venv/bin/detect-secrets-hook; else h="uv run detect-secrets-hook"; fi
  $h --baseline "$b" "$@" >/dev/null 2>&1 || fail "the repo secret scan flags: $* (a hex id inside quotes?)"; rm -f "$b"; }

DOC=docs/crm.md
[ -f "$DOC" ] || { fail "$DOC does not exist"; echo FAILED; exit 1; }
if ! grep -qxF '## 10. Runtime and operations' "$DOC"; then fail "missing heading: ## 10. Runtime and operations"; echo FAILED; exit 1; fi
prev=0
for h in '## 9. Permissions and roles' '## 10. Runtime and operations' '## 11. Known limitations' '## 12. How to change this'; do
  ln="$(grep -nxF -- "$h" "$DOC" | head -n 1 | cut -d: -f1)"
  if [ -z "$ln" ]; then fail "missing heading: $h"; elif [ "$ln" -le "$prev" ]; then fail "heading out of order: $h"; else prev="$ln"; fi
done

# AC-1.1.4 in full
[ "$(grep -c '^## ' "$DOC")" = 12 ] || fail "$DOC does not have twelve '## ' sections"
[ "$(section "$DOC" '2\.' | grep -cE '^\| (customer|deal|lead) \|')" = 3 ] || fail "the service table does not have 3 rows"
[ "$(section "$DOC" '3\.' | grep -cE '^\| ')" = 22 ] || fail "the glossary is not a header row and 21 terms"
[ "$(section "$DOC" '8\.' | grep -cE '^\| `crm\.')" = 15 ] || fail "the event table does not have 15 rows"
[ "$(section "$DOC" '9\.' | grep -cE '^\| `crm\.')" = 17 ] || fail "the permission table does not have 17 rows"
[ "$(section "$DOC" '9\.' | grep -cE '^\| `CRM_')" = 6 ] || fail "the role table does not have 6 rows"

# section 10: the numbers are the repo's own arithmetic, and the relay lists are what the compose file says
BUDGET=infrastructure/edge/memory-budget.json
CLI=infrastructure/edge/lib/memory_budget_totals.py
TEN=storefront,inventory,hrms,crm,pulseboard,projects,support,marketing,finance,itsm
core="$(python3 "$CLI" "$BUDGET" --profiles '')"
withcrm="$(python3 "$CLI" "$BUDGET" --profiles crm)"
ten="$(python3 "$CLI" "$BUDGET" --profiles "$TEN")"
tenadmin="$(python3 "$CLI" "$BUDGET" --profiles "$TEN,admin")"
[ "$core,$withcrm,$ten,$tenadmin" = "1772,2067,3747,3944" ] || fail "the memory CLI printed $core,$withcrm,$ten,$tenadmin, not 1772,2067,3747,3944"
sec10="$(section "$DOC" '10\.')"
for s in "$core MB" "$withcrm MB" "$((withcrm - core)) MB" "$ten MB" "$tenadmin MB" '97.0%' '102.1%' '3862 MB'; do
  printf '%s\n' "$sec10" | grep -qF -- "$s" || fail "section 10 lacks the figure: $s"
done
python3 - <<'PY' || FAILED=1
import re, sys
from pathlib import Path
lines = Path("infrastructure/edge/compose.prod.yml").read_text().split("\n")
def names(n: int) -> list[str]:
    m = re.search(r"EDGE_RELAY_SCHEMAS:\s*\$\{EDGE_RELAY_SCHEMAS:-([^}]*)\}", lines[n - 1])
    return m.group(1).split(",") if m else []
relay, mon = names(791), names(851)
ok = len(relay) == 29 and len(mon) == 28 and set(relay) - set(mon) == {"lead"}
if not ok:
    print(f"FAIL: edge-relay lists {len(relay)} schemas and ops-monitor {len(mon)} (difference {sorted(set(relay) ^ set(mon))}); section 10 says 29, 28 and lead", file=sys.stderr)
sys.exit(0 if ok else 1)
PY
[ "$(printf '%s\n' "$sec10" | grep -cE '^ *\| `(deal|lead|scheduler|support)\.events`')" = 4 ] || fail "the dead-letter schedule in section 10 does not have 4 rows"
has "$DOC" 'adr/0038-crm-programme-decisions.md#costing-of-d-11'
for s in 3846 'supersedes ADR-0017 decision 1' 'keep three processes'; do hasi "$DOC" "$s"; done

# section 11: each limitation names the release that fixes it, or says none is scheduled
sec11="$(section "$DOC" '11\.')"
[ "$(printf '%s\n' "$sec11" | grep -cE '^\| ')" = 11 ] || fail "section 11 is not a header row and ten limitations"
bad="$(printf '%s\n' "$sec11" | grep -E '^\| ' | tail -n +2 | awk -F'|' '$4 !~ /R[1-4]\.[0-9]|no release/ {print $2}')"
[ -z "$bad" ] || fail "limitations without a fixing release:$bad"
for s in 'OWN scope is empty' 'Customer 360' 'aborted lead conversion' 'plaintext' 'no email' 'across currencies' 'ahead of their producers' 'no committed schema' 'No `crm.lead.*` event' 'a spreadsheet may read' 'R3.4 deploys before R3.7'; do
  printf '%s\n' "$sec11" | grep -qF -- "$s" || fail "section 11 lacks the limitation: $s"
done

# section 12: the checklist
sec12="$(section "$DOC" '12\.')"
for s in 'make openapi' 'make contracts-check' 'make edge-reconcile PRODUCTS=crm' 'tests/infra/test_internal_client_paths.py' 'tests/infra/test_crm_doc_matches_contracts.py' 'uv run pytest tests -q -p no:cacheprovider' 'uv run mypy app' 'pending D-9' 'make gate'; do
  printf '%s\n' "$sec12" | grep -qF -- "$s" || fail "section 12 lacks: $s"
done

# the facts the sections state, at the cited lines
cite infrastructure/edge/compose.prod.yml 1424-1424 'customer:'
cite infrastructure/edge/compose.prod.yml 1449-1454 "split-workers"
cite infrastructure/edge/compose.prod.yml 94-94 'RUN_CONSUMERS_IN_API'
cite infrastructure/edge/compose.prod.yml 791-791 'EDGE_RELAY_SCHEMAS'
cite infrastructure/edge/compose.prod.yml 851-851 'EDGE_RELAY_SCHEMAS'
cite services/customer/app/config.py 23-23 'run_consumers_in_api: bool = False'
cite libs/platform-core/src/platform_core/ops_monitor/config.py 96-100 'run no consumer'
cite libs/platform-core/src/platform_core/ops_monitor/config.py 102-102 'customer.events'
cite tests/infra/test_ops_monitor_dlq_queue_sync.py 83-129 'PROFILE_DLQ_QUEUES'
cite infrastructure/edge/RUNBOOK.md 103-103 'revenue.opsgrids.com'
cite infrastructure/edge/RUNBOOK.md 524-524 '## Deploying'
cite infrastructure/edge/RUNBOOK.md 2204-2204 'Removing test organisations'
cite infrastructure/edge/RUNBOOK.md 2615-2615 'Roll back a bad deploy'
cite infrastructure/edge/RUNBOOK.md 2941-2941 'The memory budget'
cite infrastructure/edge/RUNBOOK.md 3020-3021 'split-workers'
cite infrastructure/edge/RUNBOOK.md 3444-3444 'Development databases'
cite Makefile 569-569 'deploy:'
cite Makefile 578-578 'edge-rollback:'
cite Makefile 585-585 'edge-reconcile:'
cite Makefile 600-600 'edge-status:'
cite Makefile 615-615 'edge-web:'
cite libs/platform-core/src/platform_core/auth.py 337-341 'sa_false'
cite libs/platform-core/src/platform_core/auth.py 372-381 'Out of scope for this resource'
cite services/customer/app/c1_360.py 166-170 '/deals'
cite services/customer/tests/test_c1_customer360.py 154-154 '"/deals"'
cite services/customer/tests/test_c1_customer360.py 68-74 'request.url.port == 8131'
cite services/lead/app/services.py 1125-1132 'owner_changed'
cite services/lead/app/services.py 1226-1280 'lead.conversion_failed'
cite services/customer/app/models.py 171-171 'email: Mapped[str]'
cite services/customer/app/services.py 1182-1185 'a lead with no email becomes a'
cite services/lead/app/clients.py 103-103 'carried no email'
cite services/deal/app/domain.py 67-74 'sum('
cite services/deal/app/domain.py 90-92 'sum('
cite services/deal/app/domain.py 101-103 'sum('
cite services/deal/app/schemas.py 68-68 'currency'
cite frontend/product-crm/src/lib/csv.ts 8-11 'function csvCell'
cite frontend/product-crm/src/routes/Companies.tsx 232-232 'downloadCsv'
cite services/notification/app/domain.py 146-151 'crm.pipeline.deal.created'
cite libs/platform-core/src/platform_core/events.py 250-262 'silently dropped'

lacks "$DOC" 'will be removed'
links "$DOC"
cites_resolve "$DOC"
secret_scan "$DOC"
[ "$FAILED" = 0 ] && echo PASS || { echo FAILED; exit 1; }
SH
```

- [ ] **Step 2: Run it and see it fail**

Run: `bash "$R11/check-crm-c.sh"`
Expected: exit status 1; stderr says `FAIL: missing heading: ## 10. Runtime and operations`, stdout says `FAILED`.

- [ ] **Step 3: Append sections 10 to 12 to `docs/crm.md`**

Append the following text to the end of `docs/crm.md` (it begins with a blank line).

````markdown append=docs/crm.md

## 10. Runtime and operations

- **Containers.** The `crm` compose profile runs three containers, `customer`, `deal` and `lead`
  (`infrastructure/edge/compose.prod.yml:1424-1484`). `customer-worker` exists only behind the
  `split-workers` profile, as the rollback for the consumer fold
  (`infrastructure/edge/compose.prod.yml:1449-1454`).
- **The consumer fold.** `RUN_CONSUMERS_IN_API` defaults to true in compose
  (`infrastructure/edge/compose.prod.yml:94`) and to false in the service
  (`services/customer/app/config.py:23`), so on the box customer's one consumer runs as a supervised
  task inside its API process. `deal` and `lead` have no such setting and ignore the variable. The
  rollback is `RUN_CONSUMERS_IN_API=false` with `split-workers` added to `EDGE_PROFILES`
  (`infrastructure/edge/RUNBOOK.md:3020-3021`).
- **Outbox relays.** The shared `edge-relay` drains the CRM outboxes: its `EDGE_RELAY_SCHEMAS`
  default has 29 schemas, `customer`, `deal` and `lead` among them
  (`infrastructure/edge/compose.prod.yml:791`). ops-monitor reuses the same list for its outbox-lag
  check, but its default has 28: it lacks `lead` (`infrastructure/edge/compose.prod.yml:851`), so a
  stalled lead outbox raises no alert. R1.2b is planned to add `lead` to ops-monitor's default.
- **Dead-letter queues.** For the `crm` profile ops-monitor watches `customer.events.dlq` and no
  other CRM queue (`libs/platform-core/src/platform_core/ops_monitor/config.py:102`); the comment
  above that table, which says `crm` runs no consumer, is stale (`:96-100`). A queue is declared only
  when its consumer first runs, and `tests/infra/test_ops_monitor_dlq_queue_sync.py` fails when a
  `./bin/worker` service has no queue in the table or the reverse (`:83-129`), so each queue is added
  in the release that adds its consumer. The schedule the registry proposes (registry `F5`), none of
  which is built:

  | Queue | Profile key | Added by | Because |
  |---|---|---|---|
  | `deal.events` | `crm` | R2.4 | deal's consumer fold and its rollback container |
  | `lead.events` | `crm` | R3.8 | lead's consumer fold |
  | `scheduler.events` | moves from `finance` to the core list | R3.6 | the scheduler serves CRM follow-ups |
  | `support.events`, `marketing.events`, `storefront.events` | new profile keys | R4.4, R4.6, R4.7 | their consumer folds |

- **Memory.** The numbers are the repo's own arithmetic:
  `python3 infrastructure/edge/lib/memory_budget_totals.py infrastructure/edge/memory-budget.json --profiles <csv>`.
  The core alone is 1772 MB (`--profiles ''`) and core plus the `crm` profile is 2067 MB
  (`--profiles crm`), so the three CRM containers cost 295 MB. The ten sellable profiles
  (`storefront,inventory,hrms,crm,pulseboard,projects,support,marketing,finance,itsm`) total 3747 MB,
  97.0% of the 3862 MB reference, and 3944 MB, 102.1%, with `admin` added. The box's accepted ceiling
  is the owner's setting, `EDGE_BUDGET_ACCEPTED_MB`, which is 3944 MB by the owner's note; the
  repository cannot verify that, and `make edge-preflight` prints the verdict. The "94.4%" figure in
  ADR-0017 predates the re-pricing.
- **Folding `lead` into `customer` (D-11).** The costing is in
  [ADR-0038, "Costing of D-11"](adr/0038-crm-programme-decisions.md#costing-of-d-11): removing the
  `lead` container would take the ten profiles plus `admin` from 3944 MB to 3846 MB, but it
  supersedes ADR-0017 decision 1, moves live tenant data between schemas and cannot be rolled back
  by code alone. The recommendation is to keep three processes for this programme, proposed in
  ADR-0038 (pending D-11); the decision first matters at R2.4, and only if D-13 is answered "no
  increase".
- **The RUNBOOK** (`infrastructure/edge/RUNBOOK.md`): "Deploying" (`:524`), "Roll back a bad deploy"
  (`:2615`), "The memory budget" (`:2941`), "Removing test organisations" (`:2204`; every end-to-end
  and smoke run leaves one behind, and the owner removes them with `tools/purge_test_orgs.py`),
  "Development databases (not the box)" (`:3444`), and the DNS row for `revenue.opsgrids.com`
  (`:103`).
- **Commands the owner runs.** `make deploy HOST=… CONFIRM=… [REF=…]` (`Makefile:569`),
  `make edge-status` (`:600`), `make edge-rollback` (`:578`; the only rollback is the whole ref, for
  every service), `make edge-reconcile PRODUCTS=crm` (`:585`) and `make edge-web APP=crm` (`:615`).
  Merges do not deploy: a deploy is manual and names a ref, and production reads, `.env` edits and
  data operations are the owner's (`P§3` F11).

## 11. Known limitations

| Limitation | Evidence | Fixed by |
|---|---|---|
| OWN scope is empty for a tenant without HRMS: OWN compares `owner_id` with an HR employee id that such a tenant never has | §4; `libs/platform-core/src/platform_core/auth.py:337-341,372-381` | proposed in ADR-0038 (pending D-7, D-8, D-9): R2.2, R2.6, R2.7 |
| The Customer 360's `crm` section is `null` in production: its deal leg calls `/deals`, which deal serves only under `/api/v1` (inference from the route table; not observed on a running stack) | `services/customer/app/c1_360.py:166-170`; the one test that checks the path asserts the wrong value, and its fake deal service answers by port whatever the path (`services/customer/tests/test_c1_customer360.py:154`, `:68-74`) | R1.2a |
| An aborted lead conversion leaves an orphaned company, contact and deal; nothing consumes `crm.lead.conversion_aborted`, and the generation bump has no event for a deleted record | ADR-0017 Known limitations; `services/lead/app/services.py:1125-1132,1226-1280` | proposed in ADR-0038 (pending D-1 to D-4): R2.1, R2.4 |
| `customer.contacts.email` is plaintext (a lead's email is sealed, so conversion narrows the protection) | `services/customer/app/models.py:171`; ADR-0017 Known limitations | no release scheduled; optional sealing proposed as R2.9 (pending D-7) |
| A lead with a phone and no email converts to a company with no contact: the person and the number are dropped | `services/customer/app/services.py:1182-1185`, `services/lead/app/clients.py:103` | proposed in ADR-0038 (pending D-14): R3.1 |
| Deal summaries add `value_minor` across currencies, though a deal's currency is free | `services/deal/app/domain.py:67-74,90-92,101-103`, `services/deal/app/schemas.py:68` | R3.2 (registry `C9`: per-currency splitting, additive) |
| Eight committed event schemas are ahead of their producers (§8) | the `$comment` of each schema | R2.3 (deal), R3.7 (lead), R4.1 (company) |
| Four events have no committed schema (§8) | `libs/contracts/events` holds 11 CRM directories for 15 types | R1.2b |
| No `crm.lead.*` event reaches anyone: none is subscribable and none has a bound queue, so the broker drops them | `services/notification/app/domain.py:146-151`; `libs/platform-core/src/platform_core/events.py:250-262` | R1.2b makes four lead keys subscribable |
| The page-only CSV export on Companies quotes a cell for a quote, a comma or a newline and does nothing about a first character of `=`, `+`, `-` or `@`, so a spreadsheet may read a company name as a formula. Today only a tenant's own users write company names; the first external writer is the public lead capture of R3.7 | `frontend/product-crm/src/lib/csv.ts:8-11`, `frontend/product-crm/src/routes/Companies.tsx:232` | R3.4 deletes the export. R3.4 deploys before R3.7, or a guard in `csv.ts` ships first. Recorded here and not fixed: the export is replaced, not patched |

## 12. How to change this

Change the code and this page in the same commit, and run the check that names the thing you changed.

- **An event.** Change the payload and its committed schema, `libs/contracts/events/<type>/v1.schema.json`,
  in one commit, and prove it through the real write path with
  `platform_core.events.validate_event_schema`; `make contracts-check` runs the contracts tests.
  Update the row in §8. A new subscribable key is a one-line edit to `SUBSCRIBABLE_EVENTS` in
  `services/notification/app/domain.py`, in the same commit as its schema. R1.2b is planned to pin
  the eight ahead-of-producer schemas: the release that closes one deletes its pin row and the
  schema's `$comment` in the same commit.
- **A permission or a role.** Add the `PermissionSpec` to the owning service's `catalogue.py`, run
  `make openapi` (it regenerates `libs/contracts/permissions/crm.json`), deploy the enforcing
  service, then reconcile with `make edge-reconcile PRODUCTS=crm` (the dry run first, then
  `APPLY=1`), admin roles first and the UI last (ADR-0007). Update §9.
- **An internal client.** Register its calls in `tests/infra/test_internal_client_paths.py` (R1.2a is
  planned to add that guard; until it exists, extend the table in §7 by hand), and put the `org`
  claim on every token for a path under `/internal/v1/organizations/{org}/`.
- **A route prefix or a port.** Add the row to both `services/app-gateway/routes.json` and
  `infrastructure/edge/routes.prod.json`, and update §2 and §6.
- **A decision.** The owner edits the State cell of its row in ADR-0038. The same commit replaces
  the `(pending D-n)` clause in every note that cites it; `grep -rn 'pending D-9' docs` lists them
  for D-9.
- **This page.** R1.2b is planned to add `tests/infra/test_crm_doc_matches_contracts.py`, which
  fails when an event type, a permission code, a role code or a gateway port in §2, §8 or §9 differs
  from `libs/contracts` and `services/app-gateway/routes.json`.
- **The checks.** Tests and mypy run per service, because `import app` is ambiguous across services:
  `cd services/customer && uv run pytest tests -q -p no:cacheprovider && uv run mypy app`, and the
  same for `deal` and `lead`. `make check` is lint, typecheck and test; `make gate` is the whole
  gate CI runs, and with Actions billing blocked it is the only full verification.
- **Where the programme lives.** Its specs are under `docs/superpowers/specs/` (their names start
  `2026-10-07-crm-`) and its plans under `docs/superpowers/plans/`.
````

- [ ] **Step 4: Run the check and see it pass**

Run: `bash "$R11/check-crm-c.sh"`
Expected: `PASS`.

- [ ] **Step 5: Re-run the two earlier crm checks and the ADR checks**

Run: `bash "$R11/check-crm-a.sh" && bash "$R11/check-crm-b.sh" && bash "$R11/check-adr-0038.sh" && bash "$R11/check-costing.sh"`
Expected: `PASS` four times.

- [ ] **Step 6: Commit**

```bash
git add docs/crm.md
git commit -m "Complete docs/crm.md with runtime and operations, known limitations and the change checklist"
```

### Task R1.1.6: The three service READMEs become run instructions

**Files:**
- Modify (replace the whole file): `services/customer/README.md` (84 lines today, `:11,15-20,44-50,62-66,68-77` are stale), `services/deal/README.md` (90 lines, `:10,14-20,45-59,68-72,74-83`), `services/lead/README.md` (21 lines, the generator stub, `:1-21`)
- Test: `$R11/check-readmes.sh` (a throwaway script, not committed, created in Step 1)

**Interfaces:**
- Consumes: `docs/crm.md` from Tasks R1.1.3 to R1.1.5 (each README points at it and at its §2 and §10). The settings of `services/{customer,lead}/app/config.py` and the Makefile targets `infra` (`Makefile:57`), `migrate` (`:71`), `run` (`:107`), `worker` (`:112`), `relay` (`:117`) and `e2e-db` (`:266`).
- Produces: three READMEs of at most 60 lines each, one paragraph of role, then Run, Settings, Verify, Demo data and a pointer line. They list no endpoint, permission, event or role: those live in `docs/crm.md` and the committed contracts. A README sits inside its service's Docker build context (`services/customer/Dockerfile:22` copies the whole directory), so the next deploy rebuilds the customer, deal and lead images with no code change; nothing else about those services changes.

- [ ] **Step 1: Write the failing check**

```bash check=readmes
export R11="${TMPDIR:-/tmp}/crm-r1-1-checks"; mkdir -p "$R11"
cat > "$R11/check-readmes.sh" <<'SH'
#!/usr/bin/env bash
# AC-1.1.5: the three READMEs are run instructions. Run from the repository root.
set -u
FAILED=0
fail()  { printf 'FAIL: %s\n' "$*" >&2; FAILED=1; }
has()   { grep -qF -- "$2" "$1" 2>/dev/null || fail "$1 lacks: $2"; }
lacks() { if grep -qF -- "$2" "$1" 2>/dev/null; then fail "$1 still has: $2"; fi; }
cite()  { local a="${2%-*}" b="${2#*-}"; sed -n "${a},${b}p" "$1" 2>/dev/null | grep -qF -- "$3" || fail "$1:$2 does not contain: $3"; }
links() { local f="$1" d t missing=""; d="$(dirname "$f")"
  for t in $(grep -oE '\]\([^) ]+\)' "$f" | sed -E 's/^\]\(//; s/\)$//; s/#.*$//' | grep -vE '^(https?:|mailto:|$)' | sort -u); do
    [ -e "$d/$t" ] || missing="$missing $t"; done
  [ -z "$missing" ] || fail "$f links to missing:$missing"; }
secret_scan() { local b h; b="$(mktemp)"; cp .secrets.baseline "$b"
  if [ -x .venv/bin/detect-secrets-hook ]; then h=.venv/bin/detect-secrets-hook; else h="uv run detect-secrets-hook"; fi
  $h --baseline "$b" "$@" >/dev/null 2>&1 || fail "the repo secret scan flags: $* (a hex id inside quotes?)"; rm -f "$b"; }

for svc in customer deal lead; do
  f="services/$svc/README.md"
  [ -f "$f" ] || { fail "$f does not exist"; continue; }
  n="$(wc -l < "$f" | tr -d ' ')"
  [ "$n" -le 60 ] || fail "$f has $n lines (at most 60)"
  has "$f" 'docs/crm.md'
  for s in 'not yet exposed' 'none registered this slice' 'Nothing yet' 'PORT=8110' '_Describe'; do lacks "$f" "$s"; done
  for s in 'crm.book.' 'crm.pipeline.' 'crm.lead.' 'CRM_' 'Method & path'; do lacks "$f" "$s"; done
  for s in 'make infra' 'make migrate' "make relay SERVICE=$svc" 'uv run pytest tests -q -p no:cacheprovider' 'uv run mypy app' 'make e2e-db'; do has "$f" "$s"; done
  links "$f"
done
has services/customer/README.md 'make run SERVICE=customer PORT=8130'
has services/customer/README.md 'make worker SERVICE=customer'
has services/deal/README.md 'make run SERVICE=deal PORT=8131'
has services/deal/README.md 'no handler'
has services/lead/README.md 'make run SERVICE=lead PORT=8135'
lacks services/lead/README.md 'make worker'
has services/lead/README.md 'no `app/consumers.py`'
grep -F '"prefix": "/api/v1/customers"' services/app-gateway/routes.json | grep -qF 'localhost:8130' || fail "routes.json no longer routes customers to 8130"
grep -F '"prefix": "/api/v1/deals"' services/app-gateway/routes.json | grep -qF 'localhost:8131' || fail "routes.json no longer routes deals to 8131"
grep -F '"prefix": "/api/v1/leads"' services/app-gateway/routes.json | grep -qF 'localhost:8135' || fail "routes.json no longer routes leads to 8135"

# every setting a README documents exists in the service's config with the default the README states
python3 - <<'PY' || FAILED=1
import re, sys
from pathlib import Path
bad = []
for svc in ("customer", "deal", "lead"):
    cfg = {}
    for ln in Path(f"services/{svc}/app/config.py").read_text().split("\n"):
        m = re.match(r"^    ([a-z0-9_]+): [\w \[\]|]+ = (.+?)\s*$", ln)
        if m:
            cfg[m.group(1)] = m.group(2).strip('"').lower()
    for ln in Path(f"services/{svc}/README.md").read_text().split("\n"):
        m = re.match(r"^\| `([A-Z0-9_]+)` \| `([^`]*)` \|", ln)
        if not m:
            continue
        name, default = m.group(1).lower(), m.group(2).lower()
        if name not in cfg:
            bad.append(f"{svc}: README documents {name.upper()} but app/config.py has no such field")
        elif cfg[name] != default:
            bad.append(f"{svc}: README says {name.upper()} defaults to {default}, app/config.py says {cfg[name]}")
for b in bad:
    print("FAIL:", b, file=sys.stderr)
sys.exit(1 if bad else 0)
PY
has services/customer/README.md 'DEAL_INTERNAL_URL'
has services/customer/README.md 'NOTIFICATION_INTERNAL_URL'
has services/customer/README.md 'CUSTOMER360_UPSTREAM_TIMEOUT_SECONDS'
has services/lead/README.md 'CUSTOMER_INTERNAL_URL'
has services/lead/README.md 'DEAL_INTERNAL_URL'

# the facts the READMEs state, at the cited lines
cite Makefile 57-57 'infra:'
cite Makefile 71-71 'migrate:'
cite Makefile 107-107 'run:'
cite Makefile 112-112 'worker:'
cite Makefile 117-117 'relay:'
cite Makefile 266-266 'e2e-db:'
cite services/deal/app/consumers.py 15-15 'queue_name="deal.events"'
cite services/customer/Dockerfile 22-22 'services/customer/'
cite tools/demo/activate_product.py 25-26 'opsgrids_wt'
[ ! -e services/lead/app/consumers.py ] || fail "services/lead/app/consumers.py exists now: the lead README is wrong"
[ -z "$(grep -rn 'httpx\|_internal_url\|_service_url' services/deal/app --include='*.py' | grep -v 'config.py' | head -n 1)" ] || fail "deal calls another service now: the deal README is wrong"

secret_scan services/customer/README.md services/deal/README.md services/lead/README.md
[ "$FAILED" = 0 ] && echo PASS || { echo FAILED; exit 1; }
SH
```

- [ ] **Step 2: Run it and see it fail**

Run: `bash "$R11/check-readmes.sh"`
Expected: exit status 1, with lines such as `FAIL: services/customer/README.md lacks: docs/crm.md`, `FAIL: services/customer/README.md still has: not yet exposed`, `FAIL: services/customer/README.md still has: none registered this slice`, `FAIL: services/deal/README.md still has: Nothing yet`, `FAIL: services/lead/README.md still has: _Describe` and `FAIL: services/lead/README.md lacks: make run SERVICE=lead PORT=8135`.

- [ ] **Step 3: Replace the customer README**

````markdown create=services/customer/README.md
# Customer service

The account-book half of the **CRM** product ("OpsGrids Revenue"): companies, contacts, the rep
directory and the Customer 360. It owns the `customer` schema and is one of three CRM processes,
beside `deal` (the pipeline) and `lead` (intake). What it holds, serves, publishes and consumes is
written down once, in [`docs/crm.md`](../../docs/crm.md); this file only says how to run it.

## Run

```bash
make infra                              # postgres, redis, rabbitmq, minio, mailhog (once)
make migrate                            # every service's migrations
make run SERVICE=customer PORT=8130     # the API; the gateway routes /api/v1/customers and
                                        # /api/v1/contacts here
make relay SERVICE=customer             # the outbox relay: its own process, always
make worker SERVICE=customer            # the event consumer, standalone
```

On the edge there is no separate worker container: the consumer runs inside the API process when
`RUN_CONSUMERS_IN_API` is on, and `make worker` is the rollback shape. It has a single binding;
`docs/crm.md` §2 says which.

## Settings that matter

Each setting is read from the environment under its upper-cased name (`app/config.py`).

| Setting | Default | What it does |
|---|---|---|
| `RUN_CONSUMERS_IN_API` | `false` | runs the consumer inside the API process (compose turns it on) |
| `DEAL_INTERNAL_URL` | `http://localhost:8131` | where the Customer 360 asks deal for opportunities |
| `NOTIFICATION_INTERNAL_URL` | `http://localhost:8151` | where it asks notification for channel standing |
| `CUSTOMER360_UPSTREAM_TIMEOUT_SECONDS` | `2.0` | the budget of each 360 section and the ceiling of the whole fan-out |
| `REVEAL_BURST_LIMIT` | `30` | real phone numbers one user may reveal per window; shared with `lead` |
| `REVEAL_BURST_WINDOW_SECONDS` | `600` | the length of that window |
| `REVEAL_DAILY_LIMIT` | `200` | real phone numbers one user may reveal per UTC day |

## Verify

Tests and mypy run per service, because `import app` is ambiguous across services:

```bash
cd services/customer
uv run pytest tests -q -p no:cacheprovider
uv run mypy app
```

## Demo data

`tools/demo/seed_crm.py` seeds customer and deal through the real write paths into a demo
organization (its header shows the arguments). Point `DATABASE_URL` at an isolated development
database such as `opsgrids_wt` (`make e2e-db` creates it), never at `opsgrids` or the box.

## Read next

[`docs/crm.md`](../../docs/crm.md) ·
[ADR-0017](../../docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md) ·
[ADR-0038](../../docs/adr/0038-crm-programme-decisions.md) · the contract,
[`libs/contracts/openapi/customer.json`](../../libs/contracts/openapi/customer.json).
````

- [ ] **Step 4: Replace the deal README**

````markdown create=services/deal/README.md
# Deal service

The pipeline half of the **CRM** product ("OpsGrids Revenue"): deals and the append-only activity
timeline. It owns the `deal` schema and is one of three CRM processes, beside `customer` (the
account book) and `lead` (intake). What it holds, the routes it serves, the events it publishes and
the permissions it declares are written down once, in [`docs/crm.md`](../../docs/crm.md); this file
only says how to run it.

## Run

```bash
make infra                          # postgres, redis, rabbitmq, minio, mailhog (once)
make migrate                        # every service's migrations
make run SERVICE=deal PORT=8131     # the API; the gateway routes /api/v1/deals here
make relay SERVICE=deal             # the outbox relay: its own process, always
make worker SERVICE=deal            # starts app/consumers.py
```

The consumer is a scaffold today: `app/consumers.py` declares the queue `deal.events` and registers
no handler, and no container runs it. The CRM overhaul registry puts its first handler in R2.4,
proposed in ADR-0038 (pending D-1); `docs/crm.md` §10 has the schedule.

## Settings

`app/config.py` adds nothing to the platform's shared settings, and deal calls no other service.

## Verify

Tests and mypy run per service, because `import app` is ambiguous across services:

```bash
cd services/deal
uv run pytest tests -q -p no:cacheprovider
uv run mypy app
```

## Demo data

`tools/demo/seed_crm.py` seeds customer and deal through the real write paths into a demo
organization (its header shows the arguments). Point `DATABASE_URL` at an isolated development
database such as `opsgrids_wt` (`make e2e-db` creates it), never at `opsgrids` or the box.

## Read next

[`docs/crm.md`](../../docs/crm.md) ·
[ADR-0017](../../docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md) ·
[ADR-0038](../../docs/adr/0038-crm-programme-decisions.md) · the contract,
[`libs/contracts/openapi/deal.json`](../../libs/contracts/openapi/deal.json).
````

- [ ] **Step 5: Replace the lead README**

````markdown create=services/lead/README.md
# Lead service

The intake half of the **CRM** product ("OpsGrids Revenue"): leads, the assignment rule, and the
conversion of a lead into a company, a contact and a deal. It owns the `lead` schema and is one of
three CRM processes, beside `customer` (the account book) and `deal` (the pipeline). What it holds,
the routes it serves, the events it publishes and the permissions it declares are written down once,
in [`docs/crm.md`](../../docs/crm.md); this file only says how to run it.

## Run

```bash
make infra                          # postgres, redis, rabbitmq, minio, mailhog (once)
make migrate                        # every service's migrations
make run SERVICE=lead PORT=8135     # the API; the gateway routes /api/v1/leads here
make relay SERVICE=lead             # the outbox relay: its own process, always
```

Lead consumes no events: there is no `app/consumers.py` and no worker entrypoint. A conversion is
synchronous and needs `customer` and `deal` running, because `:convert` calls both over the internal
API.

## Settings that matter

Each setting is read from the environment under its upper-cased name (`app/config.py`).

| Setting | Default | What it does |
|---|---|---|
| `CUSTOMER_INTERNAL_URL` | `http://localhost:8130` | where `:convert` asks customer for the company and contact, and where owner ids are validated |
| `CUSTOMER_TIMEOUT_SECONDS` | `5.0` | the budget of each call to customer |
| `DEAL_INTERNAL_URL` | `http://localhost:8131` | where `:convert` asks deal for the deal |
| `DEAL_TIMEOUT_SECONDS` | `5.0` | the budget of each call to deal |
| `REVEAL_BURST_LIMIT` | `30` | real phone numbers or emails one user may reveal per window; shared with `customer` |
| `REVEAL_BURST_WINDOW_SECONDS` | `600` | the length of that window |
| `REVEAL_DAILY_LIMIT` | `200` | real values one user may reveal per UTC day |

## Verify

Tests and mypy run per service, because `import app` is ambiguous across services:

```bash
cd services/lead
uv run pytest tests -q -p no:cacheprovider
uv run mypy app
```

## Demo data

There is no lead seed: `tools/demo/seed_crm.py` seeds customer and deal only. When you create leads
by hand, point `DATABASE_URL` at an isolated development database such as `opsgrids_wt`
(`make e2e-db` creates it), never at `opsgrids` or the box.

## Read next

[`docs/crm.md`](../../docs/crm.md) ·
[ADR-0017](../../docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md) ·
[ADR-0038](../../docs/adr/0038-crm-programme-decisions.md) · the contract,
[`libs/contracts/openapi/lead.json`](../../libs/contracts/openapi/lead.json).
````

- [ ] **Step 6: Run the check and see it pass**

Run: `bash "$R11/check-readmes.sh"`
Expected: `PASS`.

- [ ] **Step 7: Commit**

```bash
git add services/customer/README.md services/deal/README.md services/lead/README.md
git commit -m "Rewrite the customer, deal and lead READMEs as run instructions"
```

### Task R1.1.7: Dated notes on ADR-0017

**Files:**
- Modify: `docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md` (five notes, inserted at decision 1 after `:49`, at the `customer-worker` section after `:114`, at the orphans limitation after `:174`, at the generation limitation after `:189`, at the Consequences memory bullet after `:210`; plus one citation corrected in place at `:57`; line numbers are those of the base commit, and each step anchors on text, not on a number)
- Test: `$R11/check-adr-0017.sh` (a throwaway script, not committed, created in Step 1)

**Interfaces:**
- Consumes: `docs/adr/0038-crm-programme-decisions.md` from Tasks R1.1.1 and R1.1.2 (rows D-1 to D-4, D-13 and D-20, which the notes cite as `pending D-n`). The note format is ADR-0008's precedent, `**Amended 2026-09-17 — …**` (`docs/adr/0008-membership-employee-link.md`).
- Produces: five `**Amended 2026-10-07` markers in ADR-0017, each starting its own paragraph and each stating what the code shows as fact and what ADR-0038 only proposes as `(pending D-n)`. `git diff --numstat` for the file shows exactly one deleted line: the one that carries the `domain.py:38` citation, which becomes `domain.py:21`. No accepted decision is rewritten.

- [ ] **Step 1: Write the failing check**

```bash check=adr-0017
export R11="${TMPDIR:-/tmp}/crm-r1-1-checks"; mkdir -p "$R11"
cat > "$R11/check-adr-0017.sh" <<'SH'
#!/usr/bin/env bash
# AC-1.1.3 for ADR-0017. Run from the repository root, with the task's work uncommitted in the tree
# (the release gate sets R11_DIFF_BASE to the release base to run it after the commit).
set -u
FAILED=0
export R11_DIFF_BASE="${R11_DIFF_BASE:-HEAD}"
fail()  { printf 'FAIL: %s\n' "$*" >&2; FAILED=1; }
has()   { grep -qF -- "$2" "$1" 2>/dev/null || fail "$1 lacks: $2"; }
lacks() { if grep -qF -- "$2" "$1" 2>/dev/null; then fail "$1 still has: $2"; fi; }
cite()  { local a="${2%-*}" b="${2#*-}"; sed -n "${a},${b}p" "$1" 2>/dev/null | grep -qF -- "$3" || fail "$1:$2 does not contain: $3"; }
links() { local f="$1" d t missing=""; d="$(dirname "$f")"
  for t in $(grep -oE '\]\([^) ]+\)' "$f" | sed -E 's/^\]\(//; s/\)$//; s/#.*$//' | grep -vE '^(https?:|mailto:|$)' | sort -u); do
    [ -e "$d/$t" ] || missing="$missing $t"; done
  [ -z "$missing" ] || fail "$f links to missing:$missing"; }
cites_resolve() { python3 - "$@" <<'PY' || FAILED=1
import re, sys
from pathlib import Path
pat = re.compile(r"(?<![\w./-])((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.(?:py|ts|tsx|json|md|yml|yaml|sh|sql|toml|html|txt)):(\d+)(?:-(\d+))?")
bad = 0
for f in sys.argv[1:]:
    for n, line in enumerate(Path(f).read_text(encoding="utf-8").split("\n"), 1):
        for m in pat.finditer(line):
            p, a, b = m.group(1), int(m.group(2)), int(m.group(3) or m.group(2))
            q = Path(p)
            if not q.is_file():
                print(f"FAIL: {f}:{n} cites a file that does not exist: {p}", file=sys.stderr); bad = 1; continue
            total = sum(1 for _ in q.open(encoding="utf-8", errors="replace"))
            if a < 1 or b < a or b > total:
                print(f"FAIL: {f}:{n} cites {p}:{a}-{b} but the file has {total} lines", file=sys.stderr); bad = 1
sys.exit(bad)
PY
}
secret_scan() { local b h; b="$(mktemp)"; cp .secrets.baseline "$b"
  if [ -x .venv/bin/detect-secrets-hook ]; then h=.venv/bin/detect-secrets-hook; else h="uv run detect-secrets-hook"; fi
  $h --baseline "$b" "$@" >/dev/null 2>&1 || fail "the repo secret scan flags: $* (a hex id inside quotes?)"; rm -f "$b"; }

cites_resolve_added() { # the citation pass over the lines this task added only (git diff against HEAD)
  python3 - "$@" <<'PY' || FAILED=1
import os, re, subprocess, sys
from pathlib import Path
pat = re.compile(r"(?<![\w./-])((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.(?:py|ts|tsx|json|md|yml|yaml|sh|sql|toml|html|txt)):(\d+)(?:-(\d+))?")
bad = 0
for f in sys.argv[1:]:
    text = Path(f).read_text(encoding="utf-8").split("\n")
    diff = subprocess.run(["git", "diff", "--no-color", "-U0", os.environ.get("R11_DIFF_BASE", "HEAD"), "--", f], capture_output=True, text=True).stdout
    keep = set()
    for m in re.finditer(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", diff, re.M):
        start, count = int(m.group(1)), int(m.group(2) or 1)
        keep.update(range(start, start + count))
    for n in sorted(keep):
        if n > len(text):
            continue
        for m in pat.finditer(text[n - 1]):
            p, a, b = m.group(1), int(m.group(2)), int(m.group(3) or m.group(2))
            q = Path(p)
            if not q.is_file():
                print(f"FAIL: {f}:{n} cites a file that does not exist: {p}", file=sys.stderr); bad = 1; continue
            total = sum(1 for _ in q.open(encoding="utf-8", errors="replace"))
            if a < 1 or b < a or b > total:
                print(f"FAIL: {f}:{n} cites {p}:{a}-{b} but the file has {total} lines", file=sys.stderr); bad = 1
sys.exit(bad)
PY
}

F=docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md
[ "$(grep -c '\*\*Amended 2026-10-07' "$F")" = 5 ] || fail "$F does not carry exactly five '**Amended 2026-10-07' markers"
[ "$(grep -c 'pending D-' "$F")" -ge 4 ] || fail "$F has fewer than four lines saying 'pending D-n'"
del="$(git diff --numstat "$R11_DIFF_BASE" -- "$F" | awk '{print $2}')"
[ "$del" = 1 ] || fail "git diff --numstat shows ${del:-no} deleted lines for $F, not exactly 1 (the domain.py citation)"
has "$F" 'domain.py:21`). A configurable, per-organization pipeline'
lacks "$F" 'domain.py:38'

# each note sits in the place the decision names, in this order
ln() { grep -nF -- "$1" "$F" | head -n 1 | cut -d: -f1; }
m="$(grep -nF '**Amended 2026-10-07' "$F" | cut -d: -f1 | tr '\n' ' ')"
set -- $m
if [ "$#" = 5 ]; then
  d1="$(ln '1. **`lead` is built**')"; d2="$(ln '2. **`services/pipeline` and `services/activity` are deleted outright**')"
  cw="$(ln '## `customer-worker`')"; kl="$(ln '## Known limitations')"
  orph="$(ln '- **An aborted conversion leaves orphaned records behind')"; gen="$(ln "- **A deleted record no longer permanently blocks a lead's conversion.**")"
  dbs="$(ln "- **Databases that ran an earlier version of this branch's migrations")"; mem="$(ln '- Memory budget:')"
  [ "$d1" -lt "$1" ] && [ "$1" -lt "$d2" ] || fail "the decision 1 note is not between decision 1 and decision 2"
  [ "$cw" -lt "$2" ] && [ "$2" -lt "$kl" ] || fail "the customer-worker note is not inside that section"
  [ "$orph" -lt "$3" ] && [ "$3" -lt "$gen" ] || fail "the orphans note is not inside that limitation"
  [ "$gen" -lt "$4" ] && [ "$4" -lt "$dbs" ] || fail "the generation note is not inside that limitation"
  [ "$mem" -lt "$5" ] || fail "the memory note does not follow the memory bullet"
fi

# the facts and the proposals the notes name
for s in 'CRM_LEAD_REP' 'RUN_CONSUMERS_IN_API' 'split-workers' 'idempotent-by-id reuse' 'fail_conversion' '3747 MB' '3944 MB' '295 MB' 'ADR-0038'; do has "$F" "$s"; done
for s in 'pending D-20' 'pending D-1, D-2, D-3' 'pending D-4' 'pending D-13'; do has "$F" "$s"; done
cite services/lead/app/catalogue.py 150-179 'CRM_LEAD_REP'
cite infrastructure/edge/compose.prod.yml 94-94 'RUN_CONSUMERS_IN_API'
cite infrastructure/edge/compose.prod.yml 1449-1454 'split-workers'
cite infrastructure/edge/memory-budget.json 310-312 'api_with_worker'
cite services/deal/app/api/internal.py 123-142 'get_deal_by_id_any_state'
cite services/deal/tests/test_lead_conversion_internal.py 97-121 'soft_deleted_deal'
cite services/lead/app/services.py 1149-1163 'EVT_LEAD_CONVERSION_ABORTED'
cite services/lead/app/services.py 1125-1132 'owner_changed'
cite services/lead/app/services.py 1226-1280 'lead.conversion_failed'
TEN=storefront,inventory,hrms,crm,pulseboard,projects,support,marketing,finance,itsm
t="$(python3 infrastructure/edge/lib/memory_budget_totals.py infrastructure/edge/memory-budget.json --profiles "$TEN"),$(python3 infrastructure/edge/lib/memory_budget_totals.py infrastructure/edge/memory-budget.json --profiles "$TEN,admin")"
[ "$t" = "3747,3944" ] || fail "the memory CLI printed $t, not 3747,3944"

links "$F"
cites_resolve_added "$F"
secret_scan "$F"
[ "$FAILED" = 0 ] && echo PASS || { echo FAILED; exit 1; }
SH
```

- [ ] **Step 2: Run it and see it fail**

Run: `bash "$R11/check-adr-0017.sh"`
Expected: exit status 1; stderr says `FAIL: docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md does not carry exactly five '**Amended 2026-10-07' markers`, followed by the failures for the missing `pending D-` lines, the deleted-line count (`no deleted lines`), the missing `domain.py:21` text and the still-present `domain.py:38`, and stdout says `FAILED`.

- [ ] **Step 3: Insert the decision 1 note**

In `docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md`, between the end of decision 1 and decision 2 (the two lines below occur once):

````text old=docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md#decision1
   each pick up the lead grants a working rep needs.
2. **`services/pipeline` and `services/activity` are deleted outright**, not built. `deal`
````

````text new=docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md#decision1
   each pick up the lead grants a working rep needs.

   **Amended 2026-10-07 — as built, the last clause of this point did not happen.** `lead` declares
   its own `CRM_LEADS_ADMIN` and a third default role, `CRM_LEAD_REP`; neither `CRM_SALES_REP` nor
   `CRM_DEAL_REP` holds any `crm.lead.*` grant (`services/lead/app/catalogue.py:150-179`;
   `libs/contracts/permissions/crm.json` holds six role templates, three admin and three default).
   The decision stays as written because it records what was decided. Merging the three rep
   templates into one `CRM_REP` is proposed in [ADR-0038](0038-crm-programme-decisions.md)
   (pending D-20); the six legacy role codes and all 17 permission codes would stay.

2. **`services/pipeline` and `services/activity` are deleted outright**, not built. `deal`
````

- [ ] **Step 4: Correct the one citation in place**

````text old=docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md#citation
   domain.py:38`). A configurable, per-organization pipeline — which is what
````

````text new=docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md#citation
   domain.py:21`). A configurable, per-organization pipeline — which is what
````

- [ ] **Step 5: Insert the `customer-worker` note**

````text old=docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md#worker
the `crm` profile and to `memory-budget.json`.

## Known limitations (recorded during security review, not fixed here)
````

````text new=docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md#worker
the `crm` profile and to `memory-budget.json`.

**Amended 2026-10-07 — the worker is no longer a container of the `crm` profile.** The consumer
this section describes now runs as a supervised task inside `customer`'s own API process when
`RUN_CONSUMERS_IN_API` is on, which is the compose default
(`infrastructure/edge/compose.prod.yml:94`; `services/customer/app/config.py:17-23`).
`customer-worker` survives only behind the `split-workers` profile, as the rollback
(`infrastructure/edge/compose.prod.yml:1449-1454`), and `memory-budget.json` prices `customer` as
`api_with_worker` (`infrastructure/edge/memory-budget.json:310-312`). The decision to run the
consumer stands; where it runs has changed.

## Known limitations (recorded during security review, not fixed here)
````

- [ ] **Step 6: Insert the orphans note**

````text old=docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md#orphans
  rather than duplicating them in the meantime.
- **A deleted record no longer permanently blocks a lead's conversion.** The ids a lead
````

````text new=docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md#orphans
  rather than duplicating them in the meantime.

  **Amended 2026-10-07 — "idempotent-by-id reuse" is why the cleanup sketched above is unsafe as
  worded.** The event names the ids customer and deal *returned*, which can be records that already
  existed and were reused by name or email, so it carries no provenance; and deal's internal convert
  route treats a soft-deleted deal at the derived id as a successful replay
  (`services/deal/app/api/internal.py:123-142`, pinned by
  `services/deal/tests/test_lead_conversion_internal.py:97-121`), so a cleanup followed by a retry
  could convert a lead into a dead deal. ADR-0038 proposes a different cleanup: soft-delete the
  orphan deal with an audit row naming the consumer and emit no `stage_changed`, remove only records
  provably created by the aborted attempt, and run log-only on the first deploy
  (pending D-1, D-2, D-3). Until the owner answers, nothing consumes the event and nothing is
  cleaned up.
- **A deleted record no longer permanently blocks a lead's conversion.** The ids a lead
````

- [ ] **Step 7: Insert the generation note**

````text old=docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md#generation
  generic "check the lead's details".
- **Databases that ran an earlier version of this branch's migrations must be recreated.**
````

````text new=docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md#generation
  generic "check the lead's details".

  **Amended 2026-10-07 — the event is not a hook for every earlier generation.**
  `crm.lead.conversion_aborted` is staged only by `finalize_conversion`'s abort
  (`services/lead/app/services.py:1149-1163`). `fail_conversion`, which bumps the generation when
  customer reports a deleted company or contact, stages no event (`:1226-1280`), and a
  `status_changed` abort leaves the generation alone, because only `owner_changed` bumps it
  (`:1125-1132`). ADR-0038 proposes bumping the generation on every abort and emitting an event for
  the deleted-record bump (pending D-4); until then the leftovers of a `fail_conversion` bump have
  no hook.
- **Databases that ran an earlier version of this branch's migrations must be recreated.**
````

- [ ] **Step 8: Insert the memory note**

````text old=docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md#memory
  `tests/infra/test_compose_memory_budget.py` and `preflight.sh` enforce, but tight enough that
  the next CRM addition to this box should measure before assuming there is headroom.
````

````text new=docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md#memory
  `tests/infra/test_compose_memory_budget.py` and `preflight.sh` enforce, but tight enough that
  the next CRM addition to this box should measure before assuming there is headroom.

  **Amended 2026-10-07 — these figures predate the memory re-pricing.** Computed with
  `infrastructure/edge/lib/memory_budget_totals.py` over `infrastructure/edge/memory-budget.json`,
  the ten sellable profiles now total 3747 MB (97.0% of the 3862 MB reference) and the ten plus
  `admin` total 3944 MB (102.1%); the three `crm` containers cost 295 MB (the core alone is 1772 MB,
  core plus `crm` is 2067 MB). The box's accepted ceiling is the owner's setting,
  `EDGE_BUDGET_ACCEPTED_MB`, and cannot be verified from the repository. Whether it rises or memory
  is freed is ADR-0038 D-13 (pending D-13).
````

- [ ] **Step 9: Run the check and see it pass**

Run: `bash "$R11/check-adr-0017.sh"`
Expected: `PASS`.

- [ ] **Step 10: Commit**

```bash
git add docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md
git commit -m "Amend ADR-0017 with dated notes where the code and the data differ from it"
```

### Task R1.1.8: Dated notes on ADR-0008 and ADR-0020

**Files:**
- Modify: `docs/adr/0008-membership-employee-link.md` (one note after the Q5 decision paragraph that ends at `:837`, before the `**Why.**` paragraph at `:839`)
- Modify: `docs/adr/0020-insights-projection.md` (one note after Risk 1, which ends at `:641`, and one at the end of §6, `:342`, before `### 7. API` at `:344`; line numbers are those of the base commit, and each step anchors on text, not on a number)
- Test: `$R11/check-adr-notes.sh` (a throwaway script, not committed, created in Step 1)

**Interfaces:**
- Consumes: rows D-5, D-7, D-8, D-12 and D-13 of `docs/adr/0038-crm-programme-decisions.md` (Task R1.1.1), cited as `pending D-n`.
- Produces: one `**Amended 2026-10-07` marker in ADR-0008 and two in ADR-0020, all pure insertions (`git diff --numstat` shows no deleted line in either file).

- [ ] **Step 1: Write the failing check**

```bash check=adr-notes
export R11="${TMPDIR:-/tmp}/crm-r1-1-checks"; mkdir -p "$R11"
cat > "$R11/check-adr-notes.sh" <<'SH'
#!/usr/bin/env bash
# AC-1.1.3 for ADR-0008 (one note) and ADR-0020 (two notes). Run from the repository root
# (the release gate sets R11_DIFF_BASE to the release base to run it after the commit).
set -u
FAILED=0
export R11_DIFF_BASE="${R11_DIFF_BASE:-HEAD}"
fail()  { printf 'FAIL: %s\n' "$*" >&2; FAILED=1; }
has()   { grep -qF -- "$2" "$1" 2>/dev/null || fail "$1 lacks: $2"; }
cite()  { local a="${2%-*}" b="${2#*-}"; sed -n "${a},${b}p" "$1" 2>/dev/null | grep -qF -- "$3" || fail "$1:$2 does not contain: $3"; }
links() { local f="$1" d t missing=""; d="$(dirname "$f")"
  for t in $(grep -oE '\]\([^) ]+\)' "$f" | sed -E 's/^\]\(//; s/\)$//; s/#.*$//' | grep -vE '^(https?:|mailto:|$)' | sort -u); do
    [ -e "$d/$t" ] || missing="$missing $t"; done
  [ -z "$missing" ] || fail "$f links to missing:$missing"; }
cites_resolve() { python3 - "$@" <<'PY' || FAILED=1
import re, sys
from pathlib import Path
pat = re.compile(r"(?<![\w./-])((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.(?:py|ts|tsx|json|md|yml|yaml|sh|sql|toml|html|txt)):(\d+)(?:-(\d+))?")
bad = 0
for f in sys.argv[1:]:
    for n, line in enumerate(Path(f).read_text(encoding="utf-8").split("\n"), 1):
        for m in pat.finditer(line):
            p, a, b = m.group(1), int(m.group(2)), int(m.group(3) or m.group(2))
            q = Path(p)
            if not q.is_file():
                print(f"FAIL: {f}:{n} cites a file that does not exist: {p}", file=sys.stderr); bad = 1; continue
            total = sum(1 for _ in q.open(encoding="utf-8", errors="replace"))
            if a < 1 or b < a or b > total:
                print(f"FAIL: {f}:{n} cites {p}:{a}-{b} but the file has {total} lines", file=sys.stderr); bad = 1
sys.exit(bad)
PY
}
secret_scan() { local b h; b="$(mktemp)"; cp .secrets.baseline "$b"
  if [ -x .venv/bin/detect-secrets-hook ]; then h=.venv/bin/detect-secrets-hook; else h="uv run detect-secrets-hook"; fi
  $h --baseline "$b" "$@" >/dev/null 2>&1 || fail "the repo secret scan flags: $* (a hex id inside quotes?)"; rm -f "$b"; }

cites_resolve_added() { # the citation pass over the lines this task added only (git diff against HEAD)
  python3 - "$@" <<'PY' || FAILED=1
import os, re, subprocess, sys
from pathlib import Path
pat = re.compile(r"(?<![\w./-])((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.(?:py|ts|tsx|json|md|yml|yaml|sh|sql|toml|html|txt)):(\d+)(?:-(\d+))?")
bad = 0
for f in sys.argv[1:]:
    text = Path(f).read_text(encoding="utf-8").split("\n")
    diff = subprocess.run(["git", "diff", "--no-color", "-U0", os.environ.get("R11_DIFF_BASE", "HEAD"), "--", f], capture_output=True, text=True).stdout
    keep = set()
    for m in re.finditer(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", diff, re.M):
        start, count = int(m.group(1)), int(m.group(2) or 1)
        keep.update(range(start, start + count))
    for n in sorted(keep):
        if n > len(text):
            continue
        for m in pat.finditer(text[n - 1]):
            p, a, b = m.group(1), int(m.group(2)), int(m.group(3) or m.group(2))
            q = Path(p)
            if not q.is_file():
                print(f"FAIL: {f}:{n} cites a file that does not exist: {p}", file=sys.stderr); bad = 1; continue
            total = sum(1 for _ in q.open(encoding="utf-8", errors="replace"))
            if a < 1 or b < a or b > total:
                print(f"FAIL: {f}:{n} cites {p}:{a}-{b} but the file has {total} lines", file=sys.stderr); bad = 1
sys.exit(bad)
PY
}

A8=docs/adr/0008-membership-employee-link.md
A20=docs/adr/0020-insights-projection.md
[ "$(grep -c '\*\*Amended 2026-10-07' "$A8")" = 1 ] || fail "$A8 does not carry exactly one '**Amended 2026-10-07' marker"
[ "$(grep -c '\*\*Amended 2026-10-07' "$A20")" = 2 ] || fail "$A20 does not carry exactly two '**Amended 2026-10-07' markers"
[ "$(grep -c 'pending D-' "$A8")" -ge 1 ] || fail "$A8 has no line saying 'pending D-n'"
[ "$(grep -c 'pending D-' "$A20")" -ge 2 ] || fail "$A20 has fewer than two lines saying 'pending D-n'"
for f in "$A8" "$A20"; do
  del="$(git diff --numstat "$R11_DIFF_BASE" -- "$f" | awk '{print $2}')"
  [ "${del:-0}" = 0 ] || fail "$f has $del deleted lines: the notes are pure insertions"
done

# placement
ln() { grep -nF -- "$2" "$1" | head -n 1 | cut -d: -f1; }
m8="$(ln "$A8" '**Amended 2026-10-07')"; q5="$(ln "$A8" '### Q5. `sref` as one slot for ten products')"; why="$(ln "$A8" '**Why.** It has already happened.')"
[ -n "$m8" ] && [ "$q5" -lt "$m8" ] && [ "$m8" -lt "$why" ] || fail "the ADR-0008 note is not between the Q5 decision and its Why paragraph"
first20="$(grep -nF '**Amended 2026-10-07' "$A20" | head -n 1 | cut -d: -f1)"; last20="$(grep -nF '**Amended 2026-10-07' "$A20" | tail -n 1 | cut -d: -f1)"
r2="$(ln "$A20" '2. **Two definitions of one number.**')"; r1="$(ln "$A20" '1. **Box memory.**')"; s7="$(ln "$A20" '### 7. API')"; s6="$(ln "$A20" '### 6. Event contracts')"
[ -n "$first20" ] && [ "$s6" -lt "$first20" ] && [ "$first20" -lt "$s7" ] || fail "the first ADR-0020 note is not at the end of section 6"
[ -n "$last20" ] && [ "$r1" -lt "$last20" ] && [ "$last20" -lt "$r2" ] || fail "the second ADR-0020 note is not after Risk 1"

# the facts and the proposals the notes name
has "$A8" 'register_scope_resolver'
has "$A8" 'pending D-7, D-8'
has "$A8" 'human-reviewed plan'
has "$A20" 'pending D-13'
has "$A20" 'pending D-5, D-12'
has "$A20" '3747 MB'
has "$A20" 'closed_at'
cite services/customer/app/schemas.py 50-58 'class OwnerIn'
cite services/deal/app/api/routes.py 112-112 'ctx.subject_ref'
cite libs/platform-core/src/platform_core/auth.py 341-341 'owner_col == self.principal.subject_ref'
cite libs/platform-core/src/platform_core/auth.py 373-373 'owner_id == subject'
cite libs/platform-core/src/platform_core/auth.py 297-301 'register_scope_resolver'
TEN=storefront,inventory,hrms,crm,pulseboard,projects,support,marketing,finance,itsm
t="$(python3 infrastructure/edge/lib/memory_budget_totals.py infrastructure/edge/memory-budget.json --profiles "$TEN"),$(python3 infrastructure/edge/lib/memory_budget_totals.py infrastructure/edge/memory-budget.json --profiles "$TEN,admin")"
[ "$t" = "3747,3944" ] || fail "the memory CLI printed $t, not 3747,3944"

links "$A8"; links "$A20"
cites_resolve_added "$A8" "$A20"
secret_scan "$A8" "$A20"
[ "$FAILED" = 0 ] && echo PASS || { echo FAILED; exit 1; }
SH
```

- [ ] **Step 2: Run it and see it fail**

Run: `bash "$R11/check-adr-notes.sh"`
Expected: exit status 1; stderr says `FAIL: docs/adr/0008-membership-employee-link.md does not carry exactly one '**Amended 2026-10-07' marker`, `FAIL: docs/adr/0020-insights-projection.md does not carry exactly two '**Amended 2026-10-07' markers`, then the `pending D-` and placement failures; stdout says `FAILED`.

- [ ] **Step 3: Insert the ADR-0008 note**

````text old=docs/adr/0008-membership-employee-link.md#q5
mapped (§8's discipline). Metering records the membership id beside `subject_ref` from the same
change.

**Why.** It has already happened.
````

````text new=docs/adr/0008-membership-employee-link.md#q5
mapped (§8's discipline). Metering records the membership id beside `subject_ref` from the same
change.

**Amended 2026-10-07 — two statements in this decision do not match the code or the data.**
(1) *The migration recipe.* CRM owner values are not employee ids that can be carried across
"through account's link". They are `customer.owners.id`, a uuid the model generates and the public
route cannot choose (`services/customer/app/schemas.py:50-58`), except on deals and activities,
which default `owner_id` to the caller's `subject_ref`
(`services/deal/app/api/routes.py:112,241-243`), so a CRM organization holds a mix of roster ids and
employee ids. The only evidence that ties a roster row to a person is its email, and Q1 above
rejected email matching for the HRMS link, so a CRM mapping would be a human-reviewed plan, never an
automatic rewrite. [ADR-0038](0038-crm-programme-decisions.md) proposes that plan and a
`membership_id` column on the roster rather than a rekey (pending D-7, D-8). (2) *"The platform-core
change is small."* `ScopeContext.apply` and `assert_can_touch` compare against
`principal.subject_ref` only (`libs/platform-core/src/platform_core/auth.py:341,373`), and
`register_scope_resolver` writes a dictionary that nothing reads
(`libs/platform-core/src/platform_core/auth.py:297-301`), so a resolver cannot declare a membership
subject today and the work is larger than stated here.

**Why.** It has already happened.
````

- [ ] **Step 4: Insert the ADR-0020 note at the end of §6**

````text old=docs/adr/0020-insights-projection.md#section6
payment that allocates across currencies. Whether to add the column is finance's call.

### 7. API
````

````text new=docs/adr/0020-insights-projection.md#section6
payment that allocates across currencies. Whether to add the column is finance's call.

**Amended 2026-10-07 — the stage vocabulary moves from keys to `outcome`.** The `crm.pipeline.deal.*`
schemas this section lists carry six literal stage keys, and §9's metric definitions test
`closed_won` and a "closed stage" by key (the "Deals won" and "Pipeline value" definitions).
[ADR-0038](0038-crm-programme-decisions.md) proposes (pending D-5, D-12) that stages become
configurable, with an immutable key and an `outcome` of `open`, `won` or `lost`, that consumers test
the outcome and never the key, and that this ADR's projection is rebased onto `outcome` when the
stages land (R2.3); the deal migration that adds `closed_at` and exists only on
`sprint/insights-q2` is adopted unchanged. Until the owner answers, the six keys stay the contract.

### 7. API
````

- [ ] **Step 5: Insert the ADR-0020 note after Risk 1**

````text old=docs/adr/0020-insights-projection.md#risk1
   decision (ADR-0013).
2. **Two definitions of one number.**
````

````text new=docs/adr/0020-insights-projection.md#risk1
   decision (ADR-0013).

   **Amended 2026-10-07 — the +99 MB is an owner decision against today's totals.** Computed with
   `infrastructure/edge/lib/memory_budget_totals.py`, the ten sellable profiles total 3747 MB and the
   ten plus `admin` total 3944 MB against the 3862 MB reference, and the box's accepted ceiling is
   the owner's setting (`EDGE_BUDGET_ACCEPTED_MB`), so the +99 MB above is not covered by it and the
   CRM programme's own consumer folds (+1 MB each, the first at R2.4) draw on the same ceiling.
   Whether the ceiling rises, memory is freed or the folds wait is
   [ADR-0038](0038-crm-programme-decisions.md) D-13 (pending D-13); the programme design's row D-12
   (`docs/superpowers/specs/2026-10-07-crm-overhaul-programme-design.md:315`) says the insights
   container's +99 MB needs that answer.
2. **Two definitions of one number.**
````

- [ ] **Step 6: Run the check and see it pass**

Run: `bash "$R11/check-adr-notes.sh"`
Expected: `PASS`.

- [ ] **Step 7: Commit**

```bash
git add docs/adr/0008-membership-employee-link.md docs/adr/0020-insights-projection.md
git commit -m "Amend ADR-0008 and ADR-0020 with dated notes on owner ids, the scope resolver, memory and stage keys"
```

### Task R1.1.9: The open-questions register names ADR-0038

**Files:**
- Modify: `docs/adr/open-questions-register.md` (ten lines, none added or removed: `:22-23` the counts paragraph, `:56` row 20, `:95`, `:97`, `:98` the ADR-0017 limitation rows, `:103` the note under them, `:120`, `:121`, `:122` NEW-7 to NEW-9, `:135` the C15/C14 scope note)
- Test: `$R11/check-register.sh` (a throwaway script, not committed, created in Step 1)

**Interfaces:**
- Consumes: `docs/adr/0038-crm-programme-decisions.md` (Task R1.1.1): D-1 to D-5, D-7 and the "C15, named" section, whose release ids the clauses repeat (C15 is R2.6 and R2.7 for OWN on the membership and R2.3 and R2.5 for configurable stages; NEW-7 is R2.2; NEW-9 is R2.1 and R2.4; the optional email sealing is R2.9).
- Produces: the same 154-line register with each of lines 56, 95, 97, 98, 120, 121, 122 and 135, and each of the paragraphs at 19-23 and 101-103, naming `ADR-0038`, worded as a proposal (`pending D-n`). Every edit stays on its own line, so no line number in the register, or in a document that cites one, moves. NEW-9's "marks the orphaned deal lost" is annotated, not replaced.

- [ ] **Step 1: Write the failing check**

```bash check=register
export R11="${TMPDIR:-/tmp}/crm-r1-1-checks"; mkdir -p "$R11"
cat > "$R11/check-register.sh" <<'SH'
#!/usr/bin/env bash
# AC-1.1.8: the register rows and notes name ADR-0038, on the lines they were on. Run from the repository root
# (the release gate sets R11_DIFF_BASE to the release base to run it after the commit).
set -u
FAILED=0
export R11_DIFF_BASE="${R11_DIFF_BASE:-HEAD}"
fail()  { printf 'FAIL: %s\n' "$*" >&2; FAILED=1; }
has()   { grep -qF -- "$2" "$1" 2>/dev/null || fail "$1 lacks: $2"; }
links() { local f="$1" d t missing=""; d="$(dirname "$f")"
  for t in $(grep -oE '\]\([^) ]+\)' "$f" | sed -E 's/^\]\(//; s/\)$//; s/#.*$//' | grep -vE '^(https?:|mailto:|$)' | sort -u); do
    [ -e "$d/$t" ] || missing="$missing $t"; done
  [ -z "$missing" ] || fail "$f links to missing:$missing"; }
secret_scan() { local b h; b="$(mktemp)"; cp .secrets.baseline "$b"
  if [ -x .venv/bin/detect-secrets-hook ]; then h=.venv/bin/detect-secrets-hook; else h="uv run detect-secrets-hook"; fi
  $h --baseline "$b" "$@" >/dev/null 2>&1 || fail "the repo secret scan flags: $* (a hex id inside quotes?)"; rm -f "$b"; }

R=docs/adr/open-questions-register.md
[ "$(wc -l < "$R" | tr -d ' ')" = 154 ] || fail "$R is not 154 lines any more: every edit must stay on its own line"
for n in 56 95 97 98 120 121 122 135; do
  sed -n "${n}p" "$R" | grep -qF 'ADR-0038' || fail "$R:$n does not name ADR-0038"
done
sed -n '101,103p' "$R" | grep -qF 'ADR-0038' || fail "$R:101-103 does not name ADR-0038"
sed -n '19,23p' "$R" | grep -qF 'ADR-0038' || fail "$R:19-23 does not name ADR-0038"
sed -n 56p "$R" | grep -qF 'pending D-7' || fail "$R:56 does not say pending D-7"
sed -n 56p "$R" | grep -qF 'pending D-5' || fail "$R:56 does not say pending D-5"
sed -n 56p "$R" | grep -qF 'R2.2' || fail "$R:56 does not name R2.2 for NEW-7"
sed -n 95p "$R" | grep -qF 'R2.9' || fail "$R:95 does not name R2.9"
sed -n 97p "$R" | grep -qF 'R2.1 and R2.4' || fail "$R:97 does not name R2.1 and R2.4"
sed -n 98p "$R" | grep -qF 'R2.3 and R2.5' || fail "$R:98 does not name R2.3 and R2.5"
sed -n 98p "$R" | grep -qF 'pending D-5' || fail "$R:98 does not say pending D-5"
sed -n 122p "$R" | grep -qF 'marks the orphaned deal lost' || fail "$R:122 lost NEW-9's original text, which is annotated and never replaced"
sed -n 122p "$R" | grep -qF 'distorts win rate' || fail "$R:122 lacks the win-rate annotation"
sed -n 122p "$R" | grep -qF 'pending D-1' || fail "$R:122 does not say pending D-1"
# exactly ten lines changed, each replaced by a longer line
added="$(git diff --numstat "$R11_DIFF_BASE" -- "$R" | awk '{print $1}')"; deleted="$(git diff --numstat "$R11_DIFF_BASE" -- "$R" | awk '{print $2}')"
[ "$added" = 10 ] && [ "$deleted" = 10 ] || fail "git diff --numstat shows +${added:-0} -${deleted:-0} for $R, not +10 -10"
# no proposal is written as an outcome
if grep -n 'ADR-0038' "$R" | grep -vqiE 'propos|pending D-|defines|settle'; then fail "a line names ADR-0038 without saying it is a proposal"; fi
links "$R"
secret_scan "$R"
[ "$FAILED" = 0 ] && echo PASS || { echo FAILED; exit 1; }
SH
```

- [ ] **Step 2: Run it and see it fail**

Run: `bash "$R11/check-register.sh"`
Expected: exit status 1; stderr says `FAIL: docs/adr/open-questions-register.md:56 does not name ADR-0038` and the same for lines 95, 97, 98, 120, 121, 122 and 135, then the 101-103 and 19-23 failures and `FAIL: git diff --numstat shows +  -  for …`, and stdout says `FAILED`.

- [ ] **Step 3: Edit the counts paragraph**

````text old=docs/adr/open-questions-register.md#counts
from ADR-0017. A further 5 limitations are recorded by ADR-0017 (Q3, merged); they are listed
but not decided again here.
````

````text new=docs/adr/open-questions-register.md#counts
from ADR-0017. A further 5 limitations are recorded by ADR-0017 (Q3, merged); they are listed
but not decided again here. The CRM overhaul's decisions are proposed in [ADR-0038](0038-crm-programme-decisions.md) and settle as the owner answers them; the counts above do not change.
````

- [ ] **Step 4: Edit row 20**

````text old=docs/adr/open-questions-register.md#row20
**C15** (CRM), **C14/Q11** (projects, standup, timesheet), **NEW-7** (platform-core resolver), **NEW-8** (ITSM)
````

````text new=docs/adr/open-questions-register.md#row20
**C15** (CRM; [ADR-0038](0038-crm-programme-decisions.md) proposes it as R2.6 and R2.7 for OWN on the membership (pending D-7) and R2.3 and R2.5 for configurable stages (pending D-5)), **C14/Q11** (projects, standup, timesheet), **NEW-7** (platform-core resolver; ADR-0038 proposes it as R2.2 and finds it larger than this row says, because the resolver registry is written and never read, `libs/platform-core/src/platform_core/auth.py:297-301`), **NEW-8** (ITSM)
````

- [ ] **Step 5: Edit the three ADR-0017 limitation rows and the note under them**

````text old=docs/adr/open-questions-register.md#row95
If it is ever sealed, that is a `customer` schema change for **C15/Q2** |
````

````text new=docs/adr/open-questions-register.md#row95
If it is ever sealed, that is a `customer` schema change for **C15/Q2**; [ADR-0038](0038-crm-programme-decisions.md) proposes the optional sealing as R2.9, after the C15 flip (pending D-7) |
````

````text old=docs/adr/open-questions-register.md#row97
`crm.lead.conversion_aborted` has no consumer | **NEW-9** | **no** |
````

````text new=docs/adr/open-questions-register.md#row97
`crm.lead.conversion_aborted` has no consumer | **NEW-9** (proposed in [ADR-0038](0038-crm-programme-decisions.md) as R2.1 and R2.4; pending D-1, D-2, D-3, D-4) | **no** |
````

````text old=docs/adr/open-questions-register.md#row98
| **C15** (in deal's own schema, never a separate `pipeline` service) | yes |
````

````text new=docs/adr/open-questions-register.md#row98
| **C15** (in deal's own schema, never a separate `pipeline` service; proposed in [ADR-0038](0038-crm-programme-decisions.md) as R2.3 and R2.5, pending D-5) | yes |
````

````text old=docs/adr/open-questions-register.md#note20
The ADR-0008 Q5 decision moves all three CRM processes to the membership together, in C15. It does
not contradict ADR-0017's current state.
````

````text new=docs/adr/open-questions-register.md#note20
The ADR-0008 Q5 decision moves all three CRM processes to the membership together, in C15. It does
not contradict ADR-0017's current state. [ADR-0038](0038-crm-programme-decisions.md) (proposed) defines C15 and names the releases that build it (pending D-7).
````

- [ ] **Step 6: Edit NEW-7, NEW-8 and NEW-9**

````text old=docs/adr/open-questions-register.md#new7
tests | before C15 and C14 |
````

````text new=docs/adr/open-questions-register.md#new7
tests | before C15 and C14 (ADR-0038, proposed: R2.2, pending D-7) |
````

````text old=docs/adr/open-questions-register.md#new8
| after NEW-7 |
````

````text new=docs/adr/open-questions-register.md#new8
| after NEW-7 (ADR-0038, proposed, schedules only NEW-7, as R2.2; ITSM's own switch is outside the CRM programme) |
````

````text old=docs/adr/open-questions-register.md#new9
| Deal marks the orphaned deal lost; customer removes the orphaned company and contact if nothing else references them; both bound to `crm.lead.conversion_aborted` | any time; Q3 has merged (fits with C15) |
````

````text new=docs/adr/open-questions-register.md#new9
| Deal marks the orphaned deal lost (annotation: closing as lost distorts win rate and fires tenant automations, programme design §3 fact F3, so [ADR-0038](0038-crm-programme-decisions.md) D-1 proposes a soft-delete instead, pending D-1); customer removes the orphaned company and contact if nothing else references them (narrowed in D-2, pending D-2); both bound to `crm.lead.conversion_aborted` (proposed as R2.1 and R2.4) | any time; Q3 has merged (fits with C15) |
````

- [ ] **Step 7: Edit the C15/C14 scope note**

````text old=docs/adr/open-questions-register.md#scope
- **C15/C14**: OWN on the membership.
````

````text new=docs/adr/open-questions-register.md#scope
- **C15/C14**: OWN on the membership. [ADR-0038](0038-crm-programme-decisions.md) (proposed) names the C15 releases: R2.6 and R2.7 (pending D-7), with configurable stages in R2.3 and R2.5 (pending D-5).
````

- [ ] **Step 8: Run the check and see it pass**

Run: `bash "$R11/check-register.sh"`
Expected: `PASS`.

- [ ] **Step 9: Commit**

```bash
git add docs/adr/open-questions-register.md
git commit -m "Name ADR-0038 in the open-questions register rows and notes it re-schedules"
```

### Task R1.1.10: `features.md`, `api-reference.md` and `plan.md` stop describing a CRM that does not exist

**Files:**
- Modify: `docs/features.md:57-60` (the Products paragraph) and `:88-92` (the consumer row and the paragraph under it)
- Modify: `docs/api-reference.md:114-118` (the "Not yet implemented" section)
- Modify: `docs/plan.md:11` (the product list) and `:39` (phase 7)
- Test: `$R11/check-docs-top.sh` (a throwaway script, not committed, created in Step 1)

**Interfaces:**
- Consumes: `docs/crm.md` (Tasks R1.1.3 to R1.1.5; the new text links to it and to its §6 and §8) and `docs/decisions.md` (it names R35, the stale-docs sweep, in the paragraph under its table).
- Produces: AC-1.1.7. `grep -n 'No product API exists' docs/api-reference.md`, `grep -n 'None of them exist' docs/features.md` and `grep -n 'Sales CRM' docs/features.md docs/plan.md` print nothing. The text states for the CRM what the code shows, and for every other product says only that the stale-docs sweep owns the correction: this task does not claim anything about a product it did not verify.

- [ ] **Step 1: Write the failing check**

```bash check=docs-top
export R11="${TMPDIR:-/tmp}/crm-r1-1-checks"; mkdir -p "$R11"
cat > "$R11/check-docs-top.sh" <<'SH'
#!/usr/bin/env bash
# AC-1.1.7. Run from the repository root.
set -u
FAILED=0
fail()  { printf 'FAIL: %s\n' "$*" >&2; FAILED=1; }
has()   { grep -qF -- "$2" "$1" 2>/dev/null || fail "$1 lacks: $2"; }
lacks() { if grep -qF -- "$2" "$1" 2>/dev/null; then fail "$1 still has: $2"; fi; }
cite()  { local a="${2%-*}" b="${2#*-}"; sed -n "${a},${b}p" "$1" 2>/dev/null | grep -qF -- "$3" || fail "$1:$2 does not contain: $3"; }
secret_scan() { local b h; b="$(mktemp)"; cp .secrets.baseline "$b"
  if [ -x .venv/bin/detect-secrets-hook ]; then h=.venv/bin/detect-secrets-hook; else h="uv run detect-secrets-hook"; fi
  $h --baseline "$b" "$@" >/dev/null 2>&1 || fail "the repo secret scan flags: $* (a hex id inside quotes?)"; rm -f "$b"; }

lacks docs/features.md 'None of them exist'
lacks docs/features.md 'Sales CRM'
lacks docs/features.md '**Absent** — zero handlers registered'
has docs/features.md 'crm.md'
has docs/features.md 'R35'
has docs/features.md 'crm.pipeline.activity.logged'
lacks docs/api-reference.md 'No product API exists'
has docs/api-reference.md 'libs/contracts/openapi/customer.json'
has docs/api-reference.md '/api/v1/leads'
has docs/api-reference.md 'R35'
lacks docs/plan.md 'Sales CRM'
lacks docs/plan.md 'customer, sales, marketing, support'
has docs/plan.md 'one product, `crm`'
has docs/plan.md 'adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md'
for t in docs/crm.md docs/decisions.md docs/backlog.md docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md; do [ -e "$t" ] || fail "a link target is missing: $t"; done
# the facts the new text states
cite services/customer/app/consumers.py 97-97 'crm.pipeline.activity.logged'
cite services/notification/app/domain.py 146-151 'crm.pipeline.deal.stage_changed'
has docs/decisions.md 'stale-docs sweep (R35)'
[ -f libs/contracts/openapi/customer.json ] && [ -f libs/contracts/openapi/deal.json ] && [ -f libs/contracts/openapi/lead.json ] || fail "a committed CRM OpenAPI document is missing"
secret_scan docs/features.md docs/api-reference.md docs/plan.md
[ "$FAILED" = 0 ] && echo PASS || { echo FAILED; exit 1; }
SH
```

- [ ] **Step 2: Run it and see it fail**

Run: `bash "$R11/check-docs-top.sh"`
Expected: exit status 1; stderr says `FAIL: docs/features.md still has: None of them exist`, `FAIL: docs/features.md still has: Sales CRM`, `FAIL: docs/features.md still has: **Absent** — zero handlers registered`, `FAIL: docs/api-reference.md still has: No product API exists`, `FAIL: docs/plan.md still has: Sales CRM` and the `lacks` failures for the new text; stdout says `FAILED`.

- [ ] **Step 3: Correct the Products paragraph in `docs/features.md`**

````text old=docs/features.md#products
**None of them exist.** HRMS, CRM, PulseBoard, Sales CRM, Project Management, IT Management,
Marketing, Finance and Operations are named in the marketing site's catalogue and in the plan.
No product service has been written. The platform can currently create an organization, invite
people, sign them in, and authorize them against products that are not there yet.
````

````text new=docs/features.md#products
**The CRM is built; the rest of this section predates it.** CRM ("OpsGrids Revenue") is three
services, `customer`, `deal` and `lead`, and the `product-crm` app; [crm.md](crm.md) says what it is
and what it does today. HRMS, PulseBoard, Project Management, IT Management, Marketing, Finance and
Operations are named in the marketing site's catalogue and in the plan, and the sentences about them
on this page are older than their services: the stale-docs sweep (R35, [decisions.md](decisions.md))
owns correcting them. The platform can create an organization, invite people, sign them in, and
authorize them against products.
````

- [ ] **Step 4: Correct the consumer row in `docs/features.md`**

````text old=docs/features.md#consumer
| **Any consumer handling any event** | **Absent** — zero handlers registered |

So events are written and drained, and nothing reacts to them. The traced flows in the plan —
an employee joining causing five services to do work — describe machinery that is built at the
producing end and empty at the consuming end.
````

````text new=docs/features.md#consumer
| **Any consumer handling any event** | **Built** — consumers exist; for the CRM, customer consumes `crm.pipeline.activity.logged` and notification binds the five `crm.*` keys it lists ([crm.md](crm.md) §8) |

This page was written before the consumers, so the paragraph that stood here (events are written and
drained and nothing reacts to them) is withdrawn for the CRM and left to the stale-docs sweep (R35,
[decisions.md](decisions.md)) for the other products.
````

- [ ] **Step 5: Correct `docs/api-reference.md`**

````text old=docs/api-reference.md#notyet
No product API exists. HRMS, CRM, PulseBoard and the rest have route prefixes reserved in the
gateway route tables and **no service behind them** — a request to one resolves and then fails
to connect. See [backlog.md](backlog.md).
````

````text new=docs/api-reference.md#notyet
This page predates the product services and does not list their routes. The CRM is served: the
prefixes `/api/v1/customers`, `/api/v1/contacts`, `/api/v1/deals` and `/api/v1/leads` are answered
by customer, deal and lead, and the contract is the committed OpenAPI documents
`libs/contracts/openapi/customer.json`, `deal.json` and `lead.json` ([crm.md](crm.md) §6). For the
other products, which prefixes have a service behind them is not recorded here, and the stale-docs
sweep (R35, [decisions.md](decisions.md)) owns the correction. See [backlog.md](backlog.md).
````

- [ ] **Step 6: Correct `docs/plan.md`**

````text old=docs/plan.md#goal
directory**, and activates products against it — HRMS, CRM, PulseBoard, Sales CRM, Project
````

````text new=docs/plan.md#goal
directory**, and activates products against it — HRMS, CRM, PulseBoard, Project
````

````text old=docs/plan.md#phase7
| 7 | CRM greenfield | customer, sales, marketing, support | |
````

````text new=docs/plan.md#phase7
| 7 | CRM | customer, deal, lead: one product, `crm`, three services ([ADR-0017](adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md)); marketing and support are separate products | |
````

- [ ] **Step 7: Run the check and see it pass**

Run: `bash "$R11/check-docs-top.sh"`
Expected: `PASS`.

- [ ] **Step 8: Commit**

```bash
git add docs/features.md docs/api-reference.md docs/plan.md
git commit -m "Correct features, api-reference and plan: the CRM is built, and Sales CRM is not a product"
```

### Task R1.1.11: `ecosystem-gaps.md` records that `lead` is built and the CRM events are consumed

**Files:**
- Modify: `docs/ecosystem-gaps.md:18-20` (the "No product event is consumed by anything" paragraph), `:35` (the CRM row of the claims table), `:77-87` (section 2, heading and first two paragraphs), `:107-116` (section 4)
- Test: `$R11/check-gaps.sh` (a throwaway script, not committed, created in Step 1)

**Interfaces:**
- Consumes: `docs/crm.md` (§8, the events table), `docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md`, `docs/adr/0015-scheduler-and-not-before-delivery.md` and `docs/adr/0009-uploaded-images-and-object-storage.md` (the links the new text uses).
- Produces: a document that says `customer` consumes `crm.pipeline.activity.logged` and notification binds five CRM keys today (nine after R1.2b), that `lead` is built and `pipeline` was removed, and that `scheduler` and `document` are built and `activity` was deleted. The paragraph under section 2 that explains why a lead is not a small deal stays as it is.

- [ ] **Step 1: Write the failing check**

```bash check=gaps
export R11="${TMPDIR:-/tmp}/crm-r1-1-checks"; mkdir -p "$R11"
cat > "$R11/check-gaps.sh" <<'SH'
#!/usr/bin/env bash
# AC-1.1.7 for docs/ecosystem-gaps.md. Run from the repository root.
set -u
FAILED=0
fail()  { printf 'FAIL: %s\n' "$*" >&2; FAILED=1; }
has()   { grep -qF -- "$2" "$1" 2>/dev/null || fail "$1 lacks: $2"; }
lacks() { if grep -qF -- "$2" "$1" 2>/dev/null; then fail "$1 still has: $2"; fi; }
cite()  { local a="${2%-*}" b="${2#*-}"; sed -n "${a},${b}p" "$1" 2>/dev/null | grep -qF -- "$3" || fail "$1:$2 does not contain: $3"; }
secret_scan() { local b h; b="$(mktemp)"; cp .secrets.baseline "$b"
  if [ -x .venv/bin/detect-secrets-hook ]; then h=.venv/bin/detect-secrets-hook; else h="uv run detect-secrets-hook"; fi
  $h --baseline "$b" "$@" >/dev/null 2>&1 || fail "the repo secret scan flags: $* (a hex id inside quotes?)"; rm -f "$b"; }

G=docs/ecosystem-gaps.md
lacks "$G" 'No product event is consumed by anything'
lacks "$G" 'Lead and pipeline (planned)'
lacks "$G" '`lead` is a scaffold'
lacks "$G" '`lead` needs: capture'
lacks "$G" 'are all still the'
lacks "$G" '| Planned — §2 |'
has "$G" 'Lead and pipeline (done)'
has "$G" 'customer consumes `crm.pipeline.activity.logged`'
has "$G" 'nine after R1.2b'
has "$G" 'The reason it is worth building rather than folding into `deal`: a lead is not a small deal.'
has "$G" 'R35'
has "$G" '`activity` was deleted'
for t in docs/crm.md docs/decisions.md docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md docs/adr/0015-scheduler-and-not-before-delivery.md docs/adr/0009-uploaded-images-and-object-storage.md; do [ -e "$t" ] || fail "a link target is missing: $t"; done
for s in '(adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md)' '(adr/0015-scheduler-and-not-before-delivery.md)' '(adr/0009-uploaded-images-and-object-storage.md)' '(crm.md)'; do has "$G" "$s"; done
# the facts the new text states
cite services/customer/app/consumers.py 97-97 'crm.pipeline.activity.logged'
cite services/notification/app/domain.py 146-151 'crm.book.company.created'
cite services/lead/app/schemas.py 26-27 'LeadSource'
cite services/lead/app/schemas.py 26-27 'LeadStatus'
cite services/lead/app/clients.py 104-105 'leads:convert'
cite services/lead/app/clients.py 184-185 'leads:convert'
[ ! -e services/activity ] && [ ! -e services/pipeline ] || fail "services/activity or services/pipeline exists again"
[ -f services/scheduler/app/consumers.py ] && [ -f services/document/app/main.py ] || fail "scheduler or document is not built any more"
secret_scan "$G"
[ "$FAILED" = 0 ] && echo PASS || { echo FAILED; exit 1; }
SH
```

- [ ] **Step 2: Run it and see it fail**

Run: `bash "$R11/check-gaps.sh"`
Expected: exit status 1; stderr says `FAIL: docs/ecosystem-gaps.md still has: No product event is consumed by anything`, `FAIL: docs/ecosystem-gaps.md still has: Lead and pipeline (planned)`, `FAIL: docs/ecosystem-gaps.md still has: \`lead\` is a scaffold`, `FAIL: docs/ecosystem-gaps.md still has: are all still the` and the `lacks` failures for the new text; stdout says `FAILED`.

- [ ] **Step 3: Correct the "no product event is consumed" paragraph**

````text old=docs/ecosystem-gaps.md#consumed
**No product event is consumed by anything.** `hrms.*`, `crm.*`, `support.*`, `itsm.*`,
`operations.*`, `projects.*`, `pulseboard.*` and `marketing.*` — every one of them is published
carefully, drained faithfully, and delivered to nobody.
````

````text new=docs/ecosystem-gaps.md#consumed
**This paragraph predates the consumers, and the stale-docs sweep (R35,
[decisions.md](decisions.md)) owns re-checking it for the other products. For the CRM it is no
longer true:** customer consumes `crm.pipeline.activity.logged`, and notification binds the five
`crm.*` keys it lists as subscribable (nine after R1.2b); `deal` and `lead` consume nothing
([crm.md](crm.md) §8). The original claim was that no product event was consumed by anything:
`hrms.*`, `support.*`, `itsm.*`, `operations.*`, `projects.*`, `pulseboard.*` and `marketing.*`
were published carefully, drained faithfully, and delivered to nobody.
````

- [ ] **Step 4: Correct the CRM row of the claims table**

````text old=docs/ecosystem-gaps.md#claim
| CRM & lead management | **Partial.** `customer` and `deal` are real; `lead` and `pipeline` were scaffolds. | Planned — §2 |
````

````text new=docs/ecosystem-gaps.md#claim
| CRM & lead management | **Partial.** `customer` and `deal` are real; `lead` and `pipeline` were scaffolds. | Built — §2 |
````

- [ ] **Step 5: Correct section 2**

````text old=docs/ecosystem-gaps.md#section2
## 2. Lead and pipeline (planned)

`lead` is a scaffold. `pipeline` is also a scaffold — and should be **deleted rather than built**:
`deal` already owns `crm.pipeline.*` and publishes `crm.pipeline.deal.created` and
`crm.pipeline.deal.stage_changed`. Two services claiming one bounded context is how a second
source of truth is born. The plan is one service, `lead`, and a removal.

`lead` needs: capture (web form, imported list, manual), assignment with a round-robin or
owner-scoped rule, qualification status, and conversion — which emits `crm.lead.converted`
carrying the created customer and deal ids so `customer` and `deal` can fold it without `lead`
knowing either exists.
````

````text new=docs/ecosystem-gaps.md#section2
## 2. Lead and pipeline (done)

`lead` is built, as the third process of the `crm` product beside `customer` and `deal`, and
`pipeline` was deleted rather than built
([ADR-0017](adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md)): `deal` already owns
`crm.pipeline.*` and publishes `crm.pipeline.deal.created` and `crm.pipeline.deal.stage_changed`.
Two services claiming one bounded context is how a second source of truth is born, so there is one
service, `lead`, and a removal. [crm.md](crm.md) describes what exists.

`lead` captures a lead (web form, import, or by hand), assigns it with a round-robin rule, tracks a
qualification status, and converts a qualified lead into a company, a contact and a deal by calling
`customer` and `deal` over their internal API; it publishes `crm.lead.converted` with the three ids.
````

- [ ] **Step 6: Correct section 4**

````text old=docs/ecosystem-gaps.md#section4
`document`, `scheduler`, `quotation`, `activity`, `config` and `eventbridge` are all still the
generated template. None of them is load-bearing for a sales claim, so none is urgent, but two
are worth naming:

- `scheduler` is what turns "send a reminder three days before the invoice is due" into a thing
  the platform can do. Notification rules react to events; a reminder reacts to *time passing*,
  which no event describes. The notification service is deliberately built without a scheduler
  and gains recurring sends the day one exists.
- `document` is the attachment story for both email and WhatsApp. Until it exists, notifications
  carry text and links, not files.
````

````text new=docs/ecosystem-gaps.md#section4
`quotation`, `config` and `eventbridge` are still the generated template. `activity` was deleted
([ADR-0017](adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md)), and `scheduler` and
`document` are built ([ADR-0015](adr/0015-scheduler-and-not-before-delivery.md),
[ADR-0009](adr/0009-uploaded-images-and-object-storage.md)). None of the scaffolds is load-bearing
for a sales claim, so none is urgent, but two of the built services are worth naming:

- `scheduler` is what turns "send a reminder three days before the invoice is due" into a thing
  the platform can do. Notification rules react to events; a reminder reacts to *time passing*,
  which no event describes. It is built; whether notification's recurring sends use it is not
  recorded here.
- `document` is the attachment story for both email and WhatsApp. It is built; what notifications
  attach is not recorded here.
````

- [ ] **Step 7: Run the check and see it pass**

Run: `bash "$R11/check-gaps.sh"`
Expected: `PASS`.

- [ ] **Step 8: Commit**

```bash
git add docs/ecosystem-gaps.md
git commit -m "Correct ecosystem-gaps: lead is built, pipeline and activity are gone, CRM events are consumed"
```

### Task R1.1.12: `architecture.md` and `backlog.md` stop misstating the CRM

**Files:**
- Modify: `docs/architecture.md:514-515` (the "Current wiring" sentence)
- Modify: `docs/backlog.md:503-504` (the end-to-end journey's item (c))
- Test: `$R11/check-docs-arch.sh` (a throwaway script, not committed, created in Step 1) and the existing `tests/infra/test_putaway_docs.py::test_the_put_away_documents_pass_the_repo_secret_scan` (it scans `docs/backlog.md` with the repo's own secret hook)

**Interfaces:**
- Consumes: `frontend/product-crm/src/api/client.ts:84` (fixtures only when a development build sets `VITE_USE_FIXTURES=true`) and `e2e/test_journey.py:44` (the two admin roles the journey asserts).
- Produces: two corrected statements. `docs/architecture.md:143-145` (the dead `/campaigns` and `/tickets` prefixes) and `:351-360` (the "three entrypoints" claim) are not touched here: they describe code that R1.2b changes, so R1.2b corrects them in its own release. The counts at `docs/architecture.md:369-381` and every non-CRM claim in these paragraphs belong to the stale-docs sweep (R35).

- [ ] **Step 1: Write the failing check**

```bash check=docs-arch
export R11="${TMPDIR:-/tmp}/crm-r1-1-checks"; mkdir -p "$R11"
cat > "$R11/check-docs-arch.sh" <<'SH'
#!/usr/bin/env bash
# R1.1-9: the two remaining CRM statements. Run from the repository root.
set -u
FAILED=0
fail()  { printf 'FAIL: %s\n' "$*" >&2; FAILED=1; }
has()   { grep -qF -- "$2" "$1" 2>/dev/null || fail "$1 lacks: $2"; }
lacks() { if grep -qF -- "$2" "$1" 2>/dev/null; then fail "$1 still has: $2"; fi; }
cite()  { local a="${2%-*}" b="${2#*-}"; sed -n "${a},${b}p" "$1" 2>/dev/null | grep -qF -- "$3" || fail "$1:$2 does not contain: $3"; }
secret_scan() { local b h; b="$(mktemp)"; cp .secrets.baseline "$b"
  if [ -x .venv/bin/detect-secrets-hook ]; then h=.venv/bin/detect-secrets-hook; else h="uv run detect-secrets-hook"; fi
  $h --baseline "$b" "$@" >/dev/null 2>&1 || fail "the repo secret scan flags: $* (a hex id inside quotes?)"; rm -f "$b"; }

lacks docs/architecture.md '`product-crm`, `product-pulseboard` and'
has docs/architecture.md '`product-crm` talks to the real gateway by default'
has docs/architecture.md 'VITE_USE_FIXTURES=true'
has docs/architecture.md 'frontend/product-crm/src/api/client.ts:84'
has docs/architecture.md 'R35'
lacks docs/backlog.md 'CRM is the only product with two admin templates'
has docs/backlog.md 'CRM ships three admin templates'
has docs/backlog.md '`CRM_LEADS_ADMIN`'
has docs/backlog.md 'e2e/test_journey.py:44'
cite frontend/product-crm/src/api/client.ts 84-84 'VITE_USE_FIXTURES === "true"'
cite e2e/test_journey.py 44-44 'CRM_PIPELINE_ADMIN'
cite services/lead/app/catalogue.py 150-153 'CRM_LEADS_ADMIN'
cite docs/backlog.md 484-484 'customer, deal, app-gateway'
[ -e docs/decisions.md ] || fail "docs/decisions.md is missing"
secret_scan docs/architecture.md docs/backlog.md
[ "$FAILED" = 0 ] && echo PASS || { echo FAILED; exit 1; }
SH
```

- [ ] **Step 2: Run it and see it fail**

Run: `bash "$R11/check-docs-arch.sh"`
Expected: exit status 1; stderr says `FAIL: docs/architecture.md still has: \`product-crm\`, \`product-pulseboard\` and`, `FAIL: docs/backlog.md still has: CRM is the only product with two admin templates` and the `lacks` failures for the new text; stdout says `FAILED`.

- [ ] **Step 3: Correct the wiring sentence in `docs/architecture.md`**

````text old=docs/architecture.md#wiring
**Current wiring:** six product apps default to live data; `product-crm`, `product-pulseboard` and
`opsgrids-internal` still default to fixtures. Several live apps carry named sample islands — an
````

````text new=docs/architecture.md#wiring
**Current wiring:** `product-crm` talks to the real gateway by default and renders fixtures only
when a development build sets `VITE_USE_FIXTURES=true` (`frontend/product-crm/src/api/client.ts:84`);
the claim that other apps still default to fixtures predates their rebuilds and is left to the
stale-docs sweep (R35, [decisions.md](decisions.md)). Several live apps carry named sample islands — an
````

- [ ] **Step 4: Correct item (c) in `docs/backlog.md`**

````text old=docs/backlog.md#journey
- **(c) a product spanning two services** — CRM is the only product with two admin templates.
  Step 7 asserts the owner holds both, and step 9 proves it with data from the second service.
````

````text new=docs/backlog.md#journey
- **(c) a product spanning services** — CRM ships three admin templates (`CRM_BOOK_ADMIN`,
  `CRM_PIPELINE_ADMIN` and `CRM_LEADS_ADMIN`); the suite boots two of its three services, so step 7
  asserts the owner holds the two it can see (`e2e/test_journey.py:44`) and step 9 proves it with
  data from the second service. `lead` is not booted by the harness.
````

- [ ] **Step 5: Run the check and see it pass**

Run: `bash "$R11/check-docs-arch.sh"`
Expected: `PASS`.

- [ ] **Step 6: Run the repo's own scan of the documents that include `docs/backlog.md`**

Run: `uv run pytest tests/infra/test_putaway_docs.py -q -p no:cacheprovider -k secret_scan`
Expected: `1 passed`.

- [ ] **Step 7: Commit**

```bash
git add docs/architecture.md docs/backlog.md
git commit -m "Correct the CRM fixtures default in architecture and the CRM admin templates in the backlog"
```

### Task R1.1.13: Delete the superseded pipeline onboarding card and its index row

**Files:**
- Delete: `docs/onboarding/cards-dev-b-pipeline.md` (460 lines, wholly superseded by ADR-0017: `services/pipeline` was deleted rather than built)
- Modify: `docs/onboarding/README.md:10` (remove the one row that links the deleted file; the paragraph at `:54-58` that says there is no lane B stays)
- Test: `$R11/check-onboarding-b.sh` (a throwaway script, not committed, created in Step 1)

**Interfaces:**
- Consumes: nothing from an earlier task. ADR-0017 mentions the file by name four times, as plain text and not as a link (`:22`, `:52`, `:58`, `:205` at the base commit, before Task R1.1.7 adds its notes); those mentions stay, because they record what the decision said.
- Produces: AC-1.1.6 for the deleted card. Before this task `grep -rn 'cards-dev-b-pipeline.md)' docs --exclude-dir=superpowers` prints exactly `docs/onboarding/README.md:10`, the one markdown link; after it, nothing. (A pattern ending `.md]` would never match that link, because a backtick sits between `.md` and `]`.) The `--exclude-dir=superpowers` keeps the design documents, which quote the file name, out of the search.

- [ ] **Step 1: Write the failing check**

```bash check=onboarding-b
export R11="${TMPDIR:-/tmp}/crm-r1-1-checks"; mkdir -p "$R11"
cat > "$R11/check-onboarding-b.sh" <<'SH'
#!/usr/bin/env bash
# AC-1.1.6, the deleted card. Run from the repository root.
set -u
FAILED=0
fail()  { printf 'FAIL: %s\n' "$*" >&2; FAILED=1; }
has()   { grep -qF -- "$2" "$1" 2>/dev/null || fail "$1 lacks: $2"; }
lacks() { if grep -qF -- "$2" "$1" 2>/dev/null; then fail "$1 still has: $2"; fi; }
links() { local f="$1" d t missing=""; d="$(dirname "$f")"
  for t in $(grep -oE '\]\([^) ]+\)' "$f" | sed -E 's/^\]\(//; s/\)$//; s/#.*$//' | grep -vE '^(https?:|mailto:|$)' | sort -u); do
    [ -e "$d/$t" ] || missing="$missing $t"; done
  [ -z "$missing" ] || fail "$f links to missing:$missing"; }

[ ! -e docs/onboarding/cards-dev-b-pipeline.md ] || fail "docs/onboarding/cards-dev-b-pipeline.md still exists"
hits="$(grep -rn 'cards-dev-b-pipeline.md)' docs --exclude-dir=superpowers || true)"
[ -z "$hits" ] || fail "a markdown link to the deleted card remains: $hits"
lacks docs/onboarding/README.md 'cards-dev-b-pipeline'
has docs/onboarding/README.md '| [`cards-dev-a-lead.md`](cards-dev-a-lead.md) |'
has docs/onboarding/README.md '| [`cards-dev-c-hrms.md`](cards-dev-c-hrms.md) |'
has docs/onboarding/README.md 'There is no lane B.'
has docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md 'cards-dev-b-pipeline.md'
[ "$(grep -c '^| \[`cards-dev-' docs/onboarding/README.md)" = 3 ] || fail "docs/onboarding/README.md should list exactly the three remaining cards files"
links docs/onboarding/README.md
[ "$FAILED" = 0 ] && echo PASS || { echo FAILED; exit 1; }
SH
```

- [ ] **Step 2: Run it and see it fail**

Run: `bash "$R11/check-onboarding-b.sh"`
Expected: exit status 1; stderr says `FAIL: docs/onboarding/cards-dev-b-pipeline.md still exists`, `FAIL: a markdown link to the deleted card remains: docs/onboarding/README.md:10:…`, `FAIL: docs/onboarding/README.md still has: cards-dev-b-pipeline` and the count failure; stdout says `FAILED`.

- [ ] **Step 3: Remove the index row**

````text old=docs/onboarding/README.md#rowb
| [`cards-dev-b-pipeline.md`](cards-dev-b-pipeline.md) | Historical — `services/pipeline` was deleted rather than built, [ADR-0017](../adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md) |
| [`cards-dev-c-hrms.md`](cards-dev-c-hrms.md) | Dev C — `frontend/product-hrms` |
````

````text new=docs/onboarding/README.md#rowb
| [`cards-dev-c-hrms.md`](cards-dev-c-hrms.md) | Dev C — `frontend/product-hrms` |
````

- [ ] **Step 4: Delete the card**

```bash apply
git rm docs/onboarding/cards-dev-b-pipeline.md
```

- [ ] **Step 5: Run the check and see it pass**

Run: `bash "$R11/check-onboarding-b.sh"`
Expected: `PASS`.

- [ ] **Step 6: Commit**

```bash
git add docs/onboarding/README.md
git commit -m "Delete the pipeline onboarding card ADR-0017 superseded and its index row"
```

### Task R1.1.14: Card A4 and the `salescrm` framing of `cards-dev-a-lead.md`

**Files:**
- Modify: `docs/onboarding/cards-dev-a-lead.md:13-18` (the framing paragraph), `:280-371` (card A4), `:439-443` (the closing paragraph). Cards A1 to A3 and A5, and the banner at `:1-4`, are not touched.
- Test: `$R11/check-cards-a.sh` (a throwaway script, not committed, created in Step 1)

**Interfaces:**
- Consumes: `services/lead/app/catalogue.py:150-179` (the two role templates as built) and `libs/contracts/permissions/crm.json` (the manifest `lead` contributes to). The file is cited by `.github/pull_request_template.md:9` and `services/lead/app/catalogue.py:13`, so it stays.
- Produces: AC-1.1.6 for this file: it has no `SALESCRM_` and no "Sales CRM". Card A4 describes the slice as built (`crm.lead.lead.read`, `.write`, `.convert`, `.read_sensitive`, `CRM_LEADS_ADMIN`, `CRM_LEAD_REP`) and tells the reader that cards A3 and A5, which this task leaves alone, still show the original `salescrm` strings.

- [ ] **Step 1: Write the failing check**

```bash check=cards-a
export R11="${TMPDIR:-/tmp}/crm-r1-1-checks"; mkdir -p "$R11"
cat > "$R11/check-cards-a.sh" <<'SH'
#!/usr/bin/env bash
# AC-1.1.6, cards-dev-a-lead.md: card A4 and the framing only. Run from the repository root
# (the release gate sets R11_DIFF_BASE to the release base to run it after the commit).
set -u
FAILED=0
export R11_DIFF_BASE="${R11_DIFF_BASE:-HEAD}"
fail()  { printf 'FAIL: %s\n' "$*" >&2; FAILED=1; }
has()   { grep -qF -- "$2" "$1" 2>/dev/null || fail "$1 lacks: $2"; }
lacks() { if grep -qF -- "$2" "$1" 2>/dev/null; then fail "$1 still has: $2"; fi; }
cite()  { local a="${2%-*}" b="${2#*-}"; sed -n "${a},${b}p" "$1" 2>/dev/null | grep -qF -- "$3" || fail "$1:$2 does not contain: $3"; }
links() { local f="$1" d t missing=""; d="$(dirname "$f")"
  for t in $(grep -oE '\]\([^) ]+\)' "$f" | sed -E 's/^\]\(//; s/\)$//; s/#.*$//' | grep -vE '^(https?:|mailto:|$)' | sort -u); do
    [ -e "$d/$t" ] || missing="$missing $t"; done
  [ -z "$missing" ] || fail "$f links to missing:$missing"; }
secret_scan() { local b h; b="$(mktemp)"; cp .secrets.baseline "$b"
  if [ -x .venv/bin/detect-secrets-hook ]; then h=.venv/bin/detect-secrets-hook; else h="uv run detect-secrets-hook"; fi
  $h --baseline "$b" "$@" >/dev/null 2>&1 || fail "the repo secret scan flags: $* (a hex id inside quotes?)"; rm -f "$b"; }

F=docs/onboarding/cards-dev-a-lead.md
lacks "$F" 'SALESCRM_'
lacks "$F" 'Sales CRM'
lacks "$F" 'libs/contracts/permissions/salescrm.json'
lacks "$F" 'Dev B is writing the same two'
lacks "$F" 'against `services/activity`'
has "$F" 'crm.lead.lead.read_sensitive'
has "$F" '`CRM_LEADS_ADMIN`'
has "$F" '`CRM_LEAD_REP`'
has "$F" 'libs/contracts/permissions/crm.json'
has "$F" 'This card was rewritten on 2026-10-07'
has "$F" 'read them as the `crm.lead.lead.*` codes'
has "$F" 'so this lane has no next card'
# the banner and the other cards are untouched
has "$F" '> **Superseded in part by [ADR-0017]'
for c in '# CARD A1 — the table' '# CARD A2' '# CARD A3' '# CARD A5 — the tests'; do has "$F" "$c"; done
has "$F" '`salescrm.leads.lead.read`'
# only the framing, card A4 and the closing paragraph changed
bad=""
for r in $(git diff -U0 "$R11_DIFF_BASE" -- "$F" | grep -E '^@@' | sed -E 's/^@@ -([0-9]+)(,([0-9]+))? .*/\1:\3/'); do
  a="${r%%:*}"; b="${r##*:}"; b="${b:-1}"
  [ "$b" = 0 ] && { s=$((a + 1)); e=$((a)); } || { s="$a"; e=$((a + b - 1)); }
  ok=0
  { [ "$s" -ge 13 ] && [ "$e" -le 18 ]; } && ok=1
  { [ "$s" -ge 280 ] && [ "$e" -le 371 ]; } && ok=1
  { [ "$s" -ge 439 ] && [ "$e" -le 443 ]; } && ok=1
  [ "$ok" = 1 ] || bad="$bad $s-$e"
done
[ -z "$bad" ] || fail "$F changed outside the framing (13-18), card A4 (280-371) and the closing paragraph (439-443): old lines$bad"
# the facts card A4 states
cite services/lead/app/catalogue.py 119-145 'read_sensitive'
cite services/lead/app/catalogue.py 150-179 'CRM_LEAD_REP'
cite services/lead/app/scope.py 26-26 'PRODUCT = "crm"'
python3 -c "
import json, sys
m = json.load(open('libs/contracts/permissions/crm.json'))
codes = sorted(f\"crm.{p['module']}.{p['resource']}.{p['action']}\" for p in m['permissions'] if p['module'] == 'lead')
want = ['crm.lead.lead.convert', 'crm.lead.lead.read', 'crm.lead.lead.read_sensitive', 'crm.lead.lead.write']
sys.exit(0 if codes == want else 1)
" || fail "the manifest's lead permissions are not the four codes card A4 lists"
[ ! -e libs/contracts/permissions/salescrm.json ] || fail "libs/contracts/permissions/salescrm.json exists"
links "$F"
secret_scan "$F"
[ "$FAILED" = 0 ] && echo PASS || { echo FAILED; exit 1; }
SH
```

- [ ] **Step 2: Run it and see it fail**

Run: `bash "$R11/check-cards-a.sh"`
Expected: exit status 1; stderr says `FAIL: docs/onboarding/cards-dev-a-lead.md still has: SALESCRM_`, `FAIL: docs/onboarding/cards-dev-a-lead.md still has: Sales CRM`, `FAIL: docs/onboarding/cards-dev-a-lead.md still has: libs/contracts/permissions/salescrm.json` and the `lacks` failures for the new text; stdout says `FAILED`.

- [ ] **Step 3: Correct the framing paragraph**

````text old=docs/onboarding/cards-dev-a-lead.md#framing
OpsGrids sells several products. One of them is **Sales CRM** (`salescrm`), and it is currently
broken: the gateway already knows how to route `/api/v1/leads` to a service, and the database
already has a login role waiting called `opsgrids_lead` — but the service itself is an empty
shell that the code generator produced and nobody filled in. Your five cards fill it in.
````

````text new=docs/onboarding/cards-dev-a-lead.md#framing
OpsGrids sells several products. `lead` is the third service of the CRM product, **`crm`**
("OpsGrids Revenue"), beside `customer` (the account book) and `deal` (the pipeline). When these
cards were written, the gateway already routed `/api/v1/leads` to a service and the database
already had a login role called `opsgrids_lead`, but the service itself was an empty shell that the
code generator produced and nobody filled in. The five cards fill it in; the service exists now,
and card A4 below is rewritten to describe what was built.
````

- [ ] **Step 4: Rewrite card A4**

Replace the whole of card A4, from its heading to the line before card A5's rule, with the text below. The `old` block is the current card exactly (92 lines).

````text old=docs/onboarding/cards-dev-a-lead.md#a4
# CARD A4 — permissions

**BRANCH** `feat/a4-lead-catalogue`

**GOAL** Declare what a person is allowed to do with leads. This card is what finally creates
the `salescrm` product manifest, which does not exist today.

### REFERENCE

`services/standup/app/catalogue.py`. Lines 27–95 and 140–181 are **copied verbatim** — the same
block appears in every product service on purpose. Only the middle section changes.

### FILES YOU WILL TOUCH

```
services/lead/app/catalogue.py          ← new file
libs/contracts/openapi/lead.json        ← regenerated by a command, then committed
libs/contracts/permissions/salescrm.json ← created by that same command, then committed
```

Those last two are the one exception to "never touch `libs/contracts/`" — you are not editing
them, you are running `make openapi` and committing what it produced.

### THE CONTRACT

`PRODUCT = "salescrm"`.

Two permissions:

```python
PermissionSpec("leads", "lead", "read", "See the organization's leads", "OWN")
PermissionSpec("leads", "lead", "write", "Create and update leads", "OWN")
```

Two role templates. **Copy this block character for character.** Dev B is writing the same two
role codes in a different service, and the build hard-fails if the two of you disagree on the
`name` or `is_default` of a shared template:

```python
SALESCRM_LEADS_ROLE_TEMPLATES: list[RoleTemplate] = [
    RoleTemplate(
        code="SALESCRM_ADMIN",
        name="Sales administrator",
        product=PRODUCT,
        description=(
            "Administers the sales pipeline across the organization. Built from the "
            "salescrm catalogue alone."
        ),
        grants=SALESCRM_LEADS.all("ORG"),
    ),
    RoleTemplate(
        code="SALESCRM_MEMBER",
        name="Sales-team member",
        product=PRODUCT,
        description="Every person with a Sales CRM seat. Reads the book and works their own leads.",
        is_default=True,
        grants=SALESCRM_LEADS.pick(
            ("salescrm.leads.lead.read", "ORG"),
            ("salescrm.leads.lead.write", "OWN"),
        ),
    ),
]
```

### STEPS

1. Copy `services/standup/app/catalogue.py` to `services/lead/app/catalogue.py`.
2. Change the module docstring to describe leads.
3. Replace the catalogue constant, the permissions and the role templates with the contract
   above. Rename `PULSEBOARD_STANDUP` → `SALESCRM_LEADS` throughout, and
   `PULSEBOARD_STANDUP_REGISTRATION` → `SALESCRM_LEADS_REGISTRATION`.
4. Leave the `validate()` function and the `ProductRegistration` dataclass exactly as they are.
5. From the repository root: `make openapi`
6. `git status` — you should see `libs/contracts/openapi/lead.json` changed and
   `libs/contracts/permissions/salescrm.json` **created**. If `salescrm.json` did not appear,
   your registration is not being found; stop and comment.

### DONE WHEN

```bash
cd services/lead && uv run mypy app
cd services/lead && uv run pytest tests -q
make openapi && make openapi-check
ls libs/contracts/permissions/salescrm.json
uv run ruff check services/lead && uv run ruff format --check services/lead
```

### DO NOT TOUCH

Any other service's `catalogue.py`. Any manifest other than the two `make openapi` regenerates.
````

````text new=docs/onboarding/cards-dev-a-lead.md#a4
# CARD A4 — permissions

**BRANCH** `feat/a4-lead-catalogue`

> **This card was rewritten on 2026-10-07 to describe what was built.** It originally specified a
> separate `salescrm` product; [ADR-0017](../adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md)
> rejected that, and `lead` joined the existing `crm` product. Cards A3 and A5, which are not
> rewritten, still show the `salescrm.leads.lead.read` code and `PRODUCT = "salescrm"` of the
> original plan in their endpoint, scope and test tables: read them as the `crm.lead.lead.*` codes
> and `PRODUCT = "crm"` of this card.

**GOAL** Declare what a person is allowed to do with leads, as the third slice of the `crm`
product's catalogue. No new product manifest is created: `lead` contributes to the one
`crm` manifest, beside `customer` (`crm.book.*`) and `deal` (`crm.pipeline.*`).

### REFERENCE

`services/lead/app/catalogue.py` is the result, and its docstring says it mirrors the catalogues of
`customer` and `deal` (`services/customer/app/catalogue.py`, `services/deal/app/catalogue.py`). The
guard, `validate()` and the `ProductRegistration` dataclass are **copied verbatim** — the same
block appears in every product service on purpose. Only the middle section changes.

### FILES YOU WILL TOUCH

```
services/lead/app/catalogue.py            ← new file
libs/contracts/openapi/lead.json          ← regenerated by a command, then committed
libs/contracts/permissions/crm.json       ← regenerated by that same command (lead's slice joins it), then committed
```

Those last two are the one exception to "never touch `libs/contracts/`" — you are not editing
them, you are running `make openapi` and committing what it produced.

### THE CONTRACT

`PRODUCT = "crm"`.

Four permissions, all in module `lead`, resource `lead`, with a default scope of OWN and a maximum
of ORG:

| Code | What it allows | Dangerous |
|---|---|---|
| `crm.lead.lead.read` | See leads | no |
| `crm.lead.lead.write` | Create and update leads | no |
| `crm.lead.lead.convert` | Convert a qualified lead into a company and deal | no |
| `crm.lead.lead.read_sensitive` | Reveal a lead's real phone number or email, audited | yes |

Two role templates, built from this slice alone and never from "all permissions":

- `CRM_LEADS_ADMIN` ("CRM administrator (leads)"): every `crm.lead.*` code at ORG.
- `CRM_LEAD_REP` ("Sales representative", the default): `read` at ORG, and `write`, `convert` and
  `read_sensitive` at OWN.

The older rep templates, `CRM_SALES_REP` (customer) and `CRM_DEAL_REP` (deal), hold no `crm.lead.*`
grant. The exact declarations are `services/lead/app/catalogue.py:150-179`.

### STEPS

1. Copy `services/deal/app/catalogue.py` to `services/lead/app/catalogue.py`.
2. Change the module docstring to describe leads, and replace the catalogue constant, the
   permissions and the role templates with the contract above.
3. Leave the `validate()` function and the `ProductRegistration` dataclass exactly as they are.
4. From the repository root: `make openapi`
5. `git status` — you should see `libs/contracts/openapi/lead.json` and
   `libs/contracts/permissions/crm.json` changed (the `lead` slice added to the one manifest). No
   other manifest is created: if one appears, your `PRODUCT` is wrong; stop and comment.

### DONE WHEN

```bash
cd services/lead && uv run mypy app
cd services/lead && uv run pytest tests -q
make openapi && make openapi-check
uv run ruff check services/lead && uv run ruff format --check services/lead
```

### DO NOT TOUCH

Any other service's `catalogue.py`. Any manifest other than the two `make openapi` regenerates.
````

- [ ] **Step 5: Correct the closing paragraph**

````text old=docs/onboarding/cards-dev-a-lead.md#after
The lead half of Sales CRM is done. The next card in this lane is the same five cards again
against `services/activity` — the third and last skeleton in this product. By then you will
have done it twice and it will take you half the time.
````

````text new=docs/onboarding/cards-dev-a-lead.md#after
The lead service is done, as the third process of the `crm` product. `services/activity` was
deleted rather than built ([ADR-0017](../adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md)),
so this lane has no next card.
````

- [ ] **Step 6: Run the check and see it pass**

Run: `bash "$R11/check-cards-a.sh"`
Expected: `PASS`.

- [ ] **Step 7: Commit**

```bash
git add docs/onboarding/cards-dev-a-lead.md
git commit -m "Rewrite card A4 and the framing of the lead onboarding cards to describe the built crm slice"
```

### Task R1.1.15: The stale statements of `cards-dev-d-crm.md`

**Files:**
- Modify: `docs/onboarding/cards-dev-d-crm.md:10-13` (the "exactly one test file" paragraph), `:56` (the reference test), `:277-278,282` (the number of screens) and `:317` (the table in the pull request)
- Test: `$R11/check-cards-d.sh` (a throwaway script, not committed, created in Step 1)

**Interfaces:**
- Consumes: the repository as it is: `ls services` (34 directories), the frontend test files and the screens in `frontend/product-crm/src/routes/`.
- Produces: AC-1.1.6 for this file: none of "exactly **one** test file", "the only existing test in the frontend" or "seven screens" remains. The counts are a snapshot dated 2026-10-07 and the check compares them with the live counts, so a later run that finds different numbers replaces the two figures and the date with the live ones.

- [ ] **Step 1: Write the failing check**

```bash check=cards-d
export R11="${TMPDIR:-/tmp}/crm-r1-1-checks"; mkdir -p "$R11"
cat > "$R11/check-cards-d.sh" <<'SH'
#!/usr/bin/env bash
# AC-1.1.6, cards-dev-d-crm.md. Run from the repository root
# (the release gate sets R11_DIFF_BASE to the release base to run it after the commit).
set -u
FAILED=0
export R11_DIFF_BASE="${R11_DIFF_BASE:-HEAD}"
fail()  { printf 'FAIL: %s\n' "$*" >&2; FAILED=1; }
has()   { grep -qF -- "$2" "$1" 2>/dev/null || fail "$1 lacks: $2"; }
lacks() { if grep -qF -- "$2" "$1" 2>/dev/null; then fail "$1 still has: $2"; fi; }
secret_scan() { local b h; b="$(mktemp)"; cp .secrets.baseline "$b"
  if [ -x .venv/bin/detect-secrets-hook ]; then h=.venv/bin/detect-secrets-hook; else h="uv run detect-secrets-hook"; fi
  $h --baseline "$b" "$@" >/dev/null 2>&1 || fail "the repo secret scan flags: $* (a hex id inside quotes?)"; rm -f "$b"; }

F=docs/onboarding/cards-dev-d-crm.md
lacks "$F" 'exactly **one** test file'
lacks "$F" 'the only existing test in the frontend'
lacks "$F" 'seven screens'
lacks "$F" 'seven-row'
lacks "$F" '33 backend services'
has "$F" '34 backend services'
has "$F" 'ten screens'
has "$F" 'ten-row table'
has "$F" 'an existing example'
has "$F" 'qr.test.ts'
# the dated figures are today's counts
live_services="$(ls services | wc -l | tr -d ' ')"
live_tests="$(find -H frontend -path '*/node_modules' -prune -o \( -name '*.test.ts' -o -name '*.test.tsx' \) -type f -print | wc -l | tr -d ' ')"
live_crm="$(find -H frontend/product-crm -path '*/node_modules' -prune -o \( -name '*.test.ts' -o -name '*.test.tsx' \) -type f -print | wc -l | tr -d ' ')"
has "$F" "$live_services backend services"
has "$F" "$live_tests on 2026-10-07, $live_crm of them in \`product-crm\`"
# the ten screens are the route components that render a screen
screens="$(ls frontend/product-crm/src/routes | grep -vE 'test|ComingSoon|Login|NotFound' | wc -l | tr -d ' ')"
[ "$screens" = 10 ] || fail "frontend/product-crm/src/routes has $screens screens, not 10: update the card"
for s in Overview Leads LeadDetail Deals DealDetail Companies CompanyDetail Contacts Activities Team; do
  [ -f "frontend/product-crm/src/routes/$s.tsx" ] || fail "frontend/product-crm/src/routes/$s.tsx does not exist"
  has "$F" "\`$s\`"
done
[ -f frontend/opsgrids-ui/src/lib/qr.test.ts ] || fail "the reference test qr.test.ts does not exist"
# only the stale statements changed
bad=""
for r in $(git diff -U0 "$R11_DIFF_BASE" -- "$F" | grep -E '^@@' | sed -E 's/^@@ -([0-9]+)(,([0-9]+))? .*/\1:\3/'); do
  a="${r%%:*}"; b="${r##*:}"; b="${b:-1}"
  [ "$b" = 0 ] && { s=$((a + 1)); e=$((a)); } || { s="$a"; e=$((a + b - 1)); }
  ok=0
  { [ "$s" -ge 10 ] && [ "$e" -le 13 ]; } && ok=1
  { [ "$s" -ge 56 ] && [ "$e" -le 57 ]; } && ok=1
  { [ "$s" -ge 277 ] && [ "$e" -le 282 ]; } && ok=1
  { [ "$s" -ge 317 ] && [ "$e" -le 317 ]; } && ok=1
  [ "$ok" = 1 ] || bad="$bad $s-$e"
done
[ -z "$bad" ] || fail "$F changed outside the stale statements (10-13, 56-57, 277-282, 317): old lines$bad"
secret_scan "$F"
[ "$FAILED" = 0 ] && echo PASS || { echo FAILED; exit 1; }
SH
```

- [ ] **Step 2: Run it and see it fail**

Run: `bash "$R11/check-cards-d.sh"`
Expected: exit status 1; stderr says `FAIL: docs/onboarding/cards-dev-d-crm.md still has: exactly **one** test file`, `FAIL: docs/onboarding/cards-dev-d-crm.md still has: the only existing test in the frontend`, `FAIL: docs/onboarding/cards-dev-d-crm.md still has: seven screens` and the `lacks` failures for the new text; stdout says `FAILED`.

- [ ] **Step 3: Correct the opening paragraph**

````text old=docs/onboarding/cards-dev-d-crm.md#intro
repository has 33 backend services with thousands of lines of tests, and a frontend with
exactly **one** test file in it. The functions you will be testing calculate money and pipeline
value. Nothing currently notices if someone breaks them.
````

````text new=docs/onboarding/cards-dev-d-crm.md#intro
repository has 34 backend services, each with its own tests, and a frontend whose test suite has
grown to hundreds of files (521 on 2026-10-07, 37 of them in `product-crm`; count them with
`find frontend -name '*.test.ts*' -not -path '*/node_modules/*'`). Not every function these cards
name has a test yet, so check whether a card's test file already exists before you start it. The
functions you will be testing calculate money and pipeline value; nothing notices if someone
breaks one that is untested.
````

- [ ] **Step 4: Correct the reference test**

````text old=docs/onboarding/cards-dev-d-crm.md#reference
`frontend/opsgrids-ui/src/lib/qr.test.ts` — the only existing test in the frontend. Copy its
shape: a docstring explaining why, then `describe` / `it` blocks.
````

````text new=docs/onboarding/cards-dev-d-crm.md#reference
`frontend/opsgrids-ui/src/lib/qr.test.ts` — an existing example. Copy its
shape: a docstring explaining why, then `describe` / `it` blocks.
````

- [ ] **Step 5: Correct card D4's screens and table**

````text old=docs/onboarding/cards-dev-d-crm.md#screens
Only files under `frontend/product-crm/src/routes/`. There are seven screens: `Overview`,
`Deals`, `DealDetail`, `Companies`, `CompanyDetail`, `Contacts`, `Activities`.

### THE CONTRACT

For each of the seven screens, check all four states and fix what is missing:
````

````text new=docs/onboarding/cards-dev-d-crm.md#screens
Only files under `frontend/product-crm/src/routes/`. There are ten screens: `Overview`, `Leads`,
`LeadDetail`, `Deals`, `DealDetail`, `Companies`, `CompanyDetail`, `Contacts`, `Activities`, `Team`.

### THE CONTRACT

For each of the ten screens, check all four states and fix what is missing:
````

````text old=docs/onboarding/cards-dev-d-crm.md#table
and the seven-row table is in the pull request.
````

````text new=docs/onboarding/cards-dev-d-crm.md#table
and the ten-row table is in the pull request.
````

- [ ] **Step 6: Run the check and see it pass**

Run: `bash "$R11/check-cards-d.sh"`
Expected: `PASS`. If it reports a different live count for the services or the test files, replace the figures and the date in Step 3's text with the live ones and run it again.

- [ ] **Step 7: Commit**

```bash
git add docs/onboarding/cards-dev-d-crm.md
git commit -m "Correct the test-file, service and screen counts in the CRM frontend onboarding card"
```

### Task R1.1.16: The release gate: scope, index, memory, and every task check against the release base

**Files:**
- No file is created or changed. This task is the agent-run evidence that the fifteen tasks above together satisfy AC-1.1.1 to AC-1.1.10, and the text that goes into the pull request description.
- Test: `$R11/check-release.sh` (a throwaway script, not committed, created in Step 1)

**Interfaces:**
- Consumes: the fifteen scripts `$R11/check-adr-0038.sh`, `check-costing.sh`, `check-crm-a.sh`, `check-crm-b.sh`, `check-crm-c.sh`, `check-readmes.sh`, `check-adr-0017.sh`, `check-adr-notes.sh`, `check-register.sh`, `check-docs-top.sh`, `check-gaps.sh`, `check-docs-arch.sh`, `check-onboarding-b.sh`, `check-cards-a.sh` and `check-cards-d.sh`, written by Step 1 of Tasks R1.1.1 to R1.1.15 (a script that is missing is re-created from its task). Five of them read `R11_DIFF_BASE` (the git ref their diff is taken against, `HEAD` by default); this task sets it to the release base so that they run after their commits.
- Produces: one `PASS` from `check-release.sh`, and the evidence block of Step 6.

- [ ] **Step 1: Write the gate script**

```bash check=release
export R11="${TMPDIR:-/tmp}/crm-r1-1-checks"; mkdir -p "$R11"
cat > "$R11/check-release.sh" <<'SH'
#!/usr/bin/env bash
# AC-1.1.10 (scope), AC-1.1.2 (index), the memory totals before and after, and every task check against
# the release base. Run from the repository root with every task committed.
set -u
FAILED=0
fail() { printf 'FAIL: %s\n' "$*" >&2; FAILED=1; }
if [ -z "${R11_BASE:-}" ]; then
  git fetch origin >/dev/null 2>&1 || fail "git fetch origin failed"
  R11_BASE="$(git merge-base origin/main HEAD)"
fi
BASE="$R11_BASE"

# AC-1.1.10: only documents, plus the three READMEs
out="$(git diff --name-only "$BASE"...HEAD -- . ':!docs' ':!services/customer/README.md' ':!services/deal/README.md' ':!services/lead/README.md')"
[ -z "$out" ] || fail "paths outside docs/ and the three READMEs changed: $out"
want="docs/adr/0008-membership-employee-link.md
docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md
docs/adr/0020-insights-projection.md
docs/adr/0038-crm-programme-decisions.md
docs/adr/open-questions-register.md
docs/api-reference.md
docs/architecture.md
docs/backlog.md
docs/crm.md
docs/decisions.md
docs/ecosystem-gaps.md
docs/features.md
docs/onboarding/README.md
docs/onboarding/cards-dev-a-lead.md
docs/onboarding/cards-dev-b-pipeline.md
docs/onboarding/cards-dev-d-crm.md
docs/plan.md
services/customer/README.md
services/deal/README.md
services/lead/README.md"
got="$(git diff --name-only "$BASE"...HEAD | grep -v '^docs/superpowers/' | sort)"
[ "$got" = "$want" ] || fail "the changed paths are not the release's twenty: $(diff <(printf '%s\n' "$want") <(printf '%s\n' "$got") | tr '\n' ' ')"

# the documents the new ones link to are committed on this branch
for f in docs/superpowers/specs/2026-10-07-crm-overhaul-programme-design.md docs/superpowers/specs/2026-10-07-crm-overhaul-interface-registry.md docs/superpowers/specs/2026-10-07-crm-1-foundations-and-cleanup-design.md; do
  git cat-file -e "HEAD:$f" 2>/dev/null || fail "$f is not committed on this branch: ADR-0038 and docs/crm.md link to it, so it must merge first or travel in this pull request"
done

# the ADR number is free on every ref
taken="$(git log --all --diff-filter=A --name-only --format= -- 'docs/adr/0038*' 'docs/adr/0039*' | sort -u)"
[ "$taken" = "docs/adr/0038-crm-programme-decisions.md" ] || fail "another ref added 0038 or 0039 (or ours is missing): $taken"

# the index invariant and the repo's own scan of the documents that include docs/backlog.md
if [ -x .venv/bin/pytest ]; then PYT=".venv/bin/pytest"; else PYT="uv run pytest"; fi
$PYT tests/infra/test_purge_test_orgs.py -q -p no:cacheprovider -k next_free_number >/dev/null 2>&1 || fail "the decisions index test fails"
$PYT tests/infra/test_putaway_docs.py -q -p no:cacheprovider -k secret_scan >/dev/null 2>&1 || fail "the put-away documents secret scan fails"

# memory: nothing priced changed
TEN=storefront,inventory,hrms,crm,pulseboard,projects,support,marketing,finance,itsm
CLI=infrastructure/edge/lib/memory_budget_totals.py
old="$(mktemp)"; git show "$BASE:infrastructure/edge/memory-budget.json" > "$old"
before="$(python3 "$CLI" "$old" --profiles "$TEN"),$(python3 "$CLI" "$old" --profiles "$TEN,admin")"
after="$(python3 "$CLI" infrastructure/edge/memory-budget.json --profiles "$TEN"),$(python3 "$CLI" infrastructure/edge/memory-budget.json --profiles "$TEN,admin")"
rm -f "$old"
[ "$before" = "$after" ] || fail "the memory totals moved: $before before, $after after"
echo "memory totals (ten profiles, ten plus admin) before and after: $before / $after"

# every task check, against the release base
n=0
for c in "$R11"/check-*.sh; do
  case "$c" in */check-release.sh) continue ;; esac
  n=$((n + 1))
  if ! R11_DIFF_BASE="$BASE" bash "$c" >/dev/null 2>"$R11/last.err"; then
    fail "$(basename "$c") fails against the release base: $(head -n 3 "$R11/last.err" | tr '\n' ' ')"
  fi
done
[ "$n" -ge 15 ] || fail "only $n task checks found in $R11: re-create the missing ones from Step 1 of their tasks"
[ "$FAILED" = 0 ] && echo PASS || { echo FAILED; exit 1; }
SH
```

- [ ] **Step 2: Run it and see it pass**

All fifteen tasks are committed by now, so the gate is expected to pass at once. Its first run on a branch where a task is missing is the failing run.

Run: `bash "$R11/check-release.sh"`
Expected: a line `memory totals (ten profiles, ten plus admin) before and after: 3747,3944 / 3747,3944` and then `PASS`. If a line starts `FAIL:`, it names the task check, path or ref to fix; fix it in the task that owns it and commit there.

- [ ] **Step 3: Run the infrastructure tests**

Run: `make test-infra`
Expected: no failure that names a document or a README. The only tests that read a file this release changes are `tests/infra/test_purge_test_orgs.py` (it reads `docs/decisions.md` and ADR-0036) and `tests/infra/test_putaway_docs.py` (it scans `docs/backlog.md`), and the gate already ran both. A failure that is also on the base commit is not this release's. When this plan was written there was one: `tests/infra/test_step_up_marker.py::test_start_session_is_only_ever_told_plain_methods` fails with `AssertionError: OAUTH_AMR` on the unmodified tree (the OAuth work on `services/auth`, no document involved); record such a failure in the pull request description instead of fixing it here. To prove a failure is not yours, `git worktree add "$TMPDIR/r11-base" "$R11_BASE"` (or the release base commit), run the same test there, and `git worktree remove "$TMPDIR/r11-base"` afterwards.

- [ ] **Step 4: Run the hygiene row**

Run: `make hygiene`
Expected: both commands exit 0; no Python file was touched, so it has nothing to find.

- [ ] **Step 5: Run the secret scan over every tracked file**

Run: `make secrets`
Expected: exit 0 and no output.

- [ ] **Step 6: Put the evidence in the pull request description**

Paste the output of this block (the counts are the foundations design's acceptance criteria):

```bash
echo "AC-1.1.1 D rows:  $(grep -cE '^\| D-[0-9]+ \|' docs/adr/0038-crm-programme-decisions.md) (want 25)"
echo "AC-1.1.1 M rows:  $(grep -cE '^\| M[0-9] \|' docs/adr/0038-crm-programme-decisions.md) (want 4)"
echo "AC-1.1.3 ADR-0017 markers: $(grep -c '\*\*Amended 2026-10-07' docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md) (want 5); ADR-0008: $(grep -c '\*\*Amended 2026-10-07' docs/adr/0008-membership-employee-link.md) (want 1); ADR-0020: $(grep -c '\*\*Amended 2026-10-07' docs/adr/0020-insights-projection.md) (want 2)"
echo "AC-1.1.4 crm.md sections: $(grep -c '^## ' docs/crm.md) (want 12)"
echo "AC-1.1.5 README lines: $(wc -l < services/customer/README.md) $(wc -l < services/deal/README.md) $(wc -l < services/lead/README.md) (each at most 60)"
echo "AC-1.1.6 links to the deleted card: $(grep -rn 'cards-dev-b-pipeline.md)' docs --exclude-dir=superpowers | wc -l) (want 0)"
echo "AC-1.1.7 stale phrases: $(grep -c 'No product API exists' docs/api-reference.md) $(grep -c 'None of them exist' docs/features.md) $(grep -c 'Sales CRM' docs/features.md docs/plan.md | tr '\n' ' ') (want 0)"
echo "AC-1.1.10 paths outside the scope: $(git diff --name-only origin/main...HEAD -- . ':!docs' ':!services/customer/README.md' ':!services/deal/README.md' ':!services/lead/README.md' | wc -l) (want 0)"
```

Expected: every figure equals its "want". There is no commit in this task: it changes no file.

### Release R1.1 self-review

**Spec acceptance criteria** (foundations design §4.12), each with the task whose check proves it. Task R1.1.16 re-runs every check against the release base.

- [ ] **AC-1.1.1** ADR-0038 exists with `Status: **proposed**`, 25 `| D-n |` rows and 4 `| Mn |` rows of six cells, a release in every D row's Needed-by cell, `open (default recommended)` in every State cell, and an answer check that is silent while a row is open: Task R1.1.1.
- [ ] **AC-1.1.2** `docs/decisions.md` links `[0038](adr/0038-crm-programme-decisions.md)`, states `next free number is **0039**`, and `tests/infra/test_purge_test_orgs.py -k next_free_number` passes: Task R1.1.1.
- [ ] **AC-1.1.3** ADR-0017 carries five `**Amended 2026-10-07` markers and exactly one deleted line, with at least four `pending D-` lines; ADR-0008 has one marker and a `pending D-` line; ADR-0020 has two: Tasks R1.1.7 and R1.1.8.
- [ ] **AC-1.1.4** `docs/crm.md` has twelve `## ` sections, 15 event rows, 17 permission rows, 6 role rows, 3 service rows and the 21 glossary terms: Task R1.1.3 (service table and glossary), Task R1.1.4 (event, permission and role tables), Task R1.1.5 (twelve sections and every count).
- [ ] **AC-1.1.5** each README is at most 60 lines, contains `docs/crm.md`, and has none of `not yet exposed`, `none registered this slice`, `Nothing yet`, `PORT=8110`, `_Describe`: Task R1.1.6.
- [ ] **AC-1.1.6** `cards-dev-b-pipeline.md` is gone and no markdown link to it remains; `cards-dev-a-lead.md` has no `SALESCRM_` and no "Sales CRM"; `cards-dev-d-crm.md` has none of "exactly **one** test file", "the only existing test in the frontend", "seven screens": Tasks R1.1.13, R1.1.14 and R1.1.15.
- [ ] **AC-1.1.7** `No product API exists`, `None of them exist` and `Sales CRM` are gone from `docs/api-reference.md`, `docs/features.md` and `docs/plan.md`: Task R1.1.10; `docs/ecosystem-gaps.md` is corrected with them in Task R1.1.11.
- [ ] **AC-1.1.8** the register lines `:56`, `:95`, `:97`, `:98`, `:120`, `:121`, `:122` and the notes at `:101-103` and `:135` contain `ADR-0038`, on the same line numbers: Task R1.1.9.
- [ ] **AC-1.1.9** the four commands print 3747, 3944, 3649 and 3846, and every row of the costing table is recomputed from them: Task R1.1.2.
- [ ] **AC-1.1.10** `git diff --name-only <base>...HEAD` lists only paths under `docs/` and the three READMEs, and in fact exactly the twenty paths of Task R1.1.16: Task R1.1.16.

**Spec scope items** (foundations design §4.1): R1.1-1 and R1.1-2 are Task R1.1.1; R1.1-3 is Task R1.1.7; R1.1-4 and R1.1-5 are Task R1.1.8; R1.1-6 is Task R1.1.9; R1.1-7 is Tasks R1.1.3 to R1.1.5; R1.1-8 is Task R1.1.6; R1.1-9 is Tasks R1.1.10, R1.1.11 and R1.1.12; R1.1-10 is Task R1.1.13; R1.1-11 is Tasks R1.1.14 and R1.1.15; R1.1-12 is Tasks R1.1.2 (ADR-0038) and R1.1.5 (`docs/crm.md` §10).

**Review focus for this release.** The input classes the spec implies and no unit test could exercise, each pinned by a check in the task that owns it.

1. A cited `path:line` that still resolves but no longer says what the sentence claims, because `main` moved between planning and execution: the `cite` needles in every task's check, and the citation pass over the lines a task adds.
2. A hex id (a migration revision, a SHA) inside quotes tripping the repo's secret scan: `secret_scan` runs the repo's own `detect-secrets-hook` against a throwaway copy of `.secrets.baseline` in every check, and every id in the texts is bare or in backticks.
3. A proposal written as an outcome: the wording checks (`proposed in ADR-0038 (pending D-n)` present, `Planned (D-` and `will be removed` absent in `docs/crm.md`; every register line that names ADR-0038 says it is proposed), and the `pending D-` line counts in the three ADRs.
4. A table cell containing a pipe, which silently breaks ADR-0038's six-cell row grammar or a `docs/crm.md` table: the `awk` field count of Task R1.1.1 and the table parsing of Task R1.1.4.
5. A `docs/crm.md` table drifting from what the code and the contracts say: Task R1.1.4's `python3` cross-check of the event table against the `EVT_*` constants, the committed schemas' `required` lists and `SUBSCRIBABLE_EVENTS`, of the permission and role tables against `libs/contracts/permissions/crm.json`, and Task R1.1.3's check of the ports against `services/app-gateway/routes.json`.

**Not applicable to this release.** The foundations design §4.2 (`docs/superpowers/specs/2026-10-07-crm-1-foundations-and-cleanup-design.md:561-568`) says R1.1 creates, changes or removes no table, column, migration, route, permission, role, event, flag, setting or container. The platform's rules for those things therefore have no task here: an `organization_id` column with RLS and a grant, a negative-authorization test per endpoint, a migration round trip, an event schema proved against its producer, and consumer redelivery. They are vacuous in a documents-only release and are carried by R1.2a and R1.2b, which change code. The three Alembic heads stay `e230c025337e`, `e94d7a2c5f18` and `91dd3ca663fd`, and Task R1.1.16's scope check (only `docs/` and the three READMEs) is what keeps them there.

**Where this plan departs from, or adds to, the foundations design.** Each is a correction the plan verified against the code, or text the design leaves to the plan, and the owner may strike any of them.

- **ADR-0020's two notes** (Task R1.1.8) are worded here; the design names only their places and subjects.
- **D-18's "Amends or builds on" cell** (Task R1.1.1) cites `docs/adr/0006-approval-engine.md:62`, where the ADR's Context names discount approvals in CRM as a consumer, and the employee-keyed requesters of `P§6`. The design and the registry say "ADR-0006 Q1", but Q1 there is the `manager_membership_id` decision (open-questions register row 9), which D-24 cites correctly at `:1479`.
- **D-21's ADR-0035 lines** are `:40-41,165-169`, not `:166-168`: the heading "Not changed" is at `:165` and its text at `:167-169`.
- **The demo-seed database.** The design says the header of `tools/demo/seed_crm.py` names the isolated database; it does not (it shows only the arguments). The READMEs say `opsgrids_wt`, which `tools/demo/activate_product.py:25` names for the same family of seeds and `make e2e-db` creates.
- **`docs/architecture.md:514-515`** is replaced rather than shortened: `product-pulseboard` also defaults to live data (`frontend/product-pulseboard/src/api/client.ts:57`), so the design's wording would have kept a false clause. The remaining non-CRM claims are left to R35.
- **`docs/backlog.md:503`** says the e2e suite boots two of the three CRM services (`e2e/test_journey.py:44` asserts two admin roles), so "three admin templates" is stated beside what the suite can see.
- **Card A4's note** tells the reader that cards A3 and A5 still show the original `salescrm.leads.lead.read` code and `PRODUCT = "salescrm"`, because the design leaves A3 and A5 untouched and they contradict the corrected A4. **Card D's opening paragraph** says "check whether a card's test file already exists", because `frontend/product-crm/src/lib/format.test.ts` (card D2's target) already does.
- **Decision count.** The 25 D rows follow the design. The specs for R2 to R4 raise further decisions (a D-26 and later, and SP2 and SP4 each use D-26 for a different question). If the owner adds them to ADR-0038, Task R1.1.1's counts (25 and 4), the Consequences table and Task R1.1.2's `count` lines change with them.
- **`docs/README.md`** is not edited: the design does not list it, and `docs/crm.md` is reached from the corrected `docs/features.md`, `docs/api-reference.md` and `docs/ecosystem-gaps.md`, from the three READMEs and from `docs/decisions.md`. Add an index row if the owner wants one.
- **The CSV exposure is recorded where a later release will read it.** The foundations design keeps it in DD-19 and its risk table, which no later release is told to open. Tasks R1.1.1 (a bullet under ADR-0038's "Open items") and R1.1.5 (the tenth row of `docs/crm.md` section 11, with the two citations) carry the ordering constraint: R3.4 deploys before R3.7, or a guard in `csv.ts` ships first. Nothing is changed in `csv.ts`; the design rejects patching an export that R3.4 deletes.
- **A test that fails before this release.** `tests/infra/test_step_up_marker.py::test_start_session_is_only_ever_told_plain_methods` fails on the unmodified tree (`OAUTH_AMR`); Task R1.1.16 says how to prove that and not to fix it here.

---

## Release R1.2 - Safe fixes, confirmed removals and event-contract hygiene

R1.2 is one scope shipped as **two deploys**. The box's only rollback is the whole ref
(`Makefile:578-583`), so the one production defect fix must not roll back together with a hygiene
change that misbehaves.

- **R1.2a** (Tasks R1.2.1 to R1.2.5): the Customer 360 deal-leg path fix, the guard tests that keep that
  defect class from coming back, the organization claim on notification's three service tokens, the
  production-smoke step, and the `docs/crm.md` sentences that call this work planned. It rebuilds
  customer and notification, and deal and lead with no code change (R1.1's READMEs sit inside those two
  images). It leaves app-gateway and ops-monitor alone and publishes no SPA.
- **R1.2b** (Tasks R1.2.6 to R1.2.25): the four missing event contracts and the pins on the eight
  ahead-of-producer schemas, subscribable lead keys and the repaired Engage recipe, ops-monitor's relay
  list, every class-1 removal, `phone_index` stops being written, `make openapi` regeneration and the
  doc-drift test. It rebuilds customer, deal, lead, notification and app-gateway, recreates ops-monitor
  and is followed by two SPA publishes (CRM, Engage).

**Spec:** `docs/superpowers/specs/2026-10-07-crm-1-foundations-and-cleanup-design.md` section 5 (cited
`S§5.n`), whose acceptance criteria `AC-1.2.1` to `AC-1.2.16` this release satisfies; the registry
`docs/superpowers/specs/2026-10-07-crm-overhaul-interface-registry.md` (handles `C23`, `C77`, `EV4`,
`EV5`, `EV39`, `F6`, `B40`, `H6`, `G15`, `J13`) is binding for every name below. Citations are
`path:line` at HEAD `3bc7f6ca`.

### How to read this release

- **observed** marks a result produced while this plan was written, in a scratch copy of the repository
  outside the worktree (an in-process ASGI call, a vitest run, a compiled statement), against HEAD
  `3bc7f6ca`. **inference** marks what was derived from reading the code and not run, because the test
  needs PostgreSQL, Redis or a broker (the `customer`, `deal`, `lead` and `notification` suites do). An
  executor who sees a different result stops and reads the task; it is the plan that is wrong.
- Every command runs from the repository root unless a `cd` is shown. Python tests and mypy run **per
  service** (`import app` is ambiguous across services): `cd services/<name> && uv run pytest tests/<file>
  -q -p no:cacheprovider` and `uv run mypy app`. The database suites need `make infra` and `make migrate`
  once. Frontend commands run from `frontend/` with Node 22 and pnpm 11 on the PATH.
- A step that says **OWNER RUN** is a production read or write, a deploy, a `make edge-*` target, a box
  `.env` edit or a published SPA bundle. An agent builds and tests locally and never runs those.
- Commit messages are plain sentences about the change and carry no attribution lines.
- **Re-verify before starting.** Every `path:line` was read at HEAD `3bc7f6ca`, and `main` moves. Run `git fetch origin`
  and `git diff --stat 3bc7f6ca origin/main -- services/customer services/deal services/lead services/notification
  services/app-gateway libs/contracts infrastructure/edge tests/infra e2e tools docs frontend/product-crm
  frontend/product-engage frontend/opsgrids-client`. If it prints changes, re-read the lines a task cites before using
  it: every search test and every `cite` check in this release names the line it expects, so a moved line fails loudly instead
  of being copied. Frontend results marked observed were taken under Node 22.14; under Node 18, which the repository's
  `engines` rule out, a few unrelated WebCrypto tests (the Excel export's baseline sheet) fail on the unmodified tree.
- One branch per deploy (`feat/crm-r1-2a-360-path`, `feat/crm-r1-2b-contract-hygiene`), short-lived, one pull
  request per task or task group (each is independently revertible), squash-merged by the owner. R1.2a's
  pull requests merge and deploy before any R1.2b one.

### Scope reconciliation (read before starting)

1. **No DLQ queue is added in R1.2.** The programme's line for R1.2 says "ops-monitor coverage" (`P§10`), which can be
   read as extending ops-monitor's DLQ list with the registry's queues. The spec decides otherwise (DD-12, `S§5.3.6`, deviation 5 in `S§10`),
   and the code agrees: `tests/infra/test_ops_monitor_dlq_queue_sync.py:83-129` fails when a queue is listed
   without a `./bin/worker` compose service and the reverse; a queue exists only once its consumer has run
   (`libs/platform-core/src/platform_core/events.py:465-471`); and the lists live in
   `libs/platform-core/src/platform_core/ops_monitor/config.py:62-109`, so touching them makes the deploy a
   full image rebuild (DD-20). Registry `F5` gives each queue to the release that adds its consumer:
   `deal.events` R2.4, `lead.events` R3.8, `scheduler.events` R3.6, `support.events` R4.4,
   `marketing.events` R4.6, `storefront.events` R4.7. R1.2 adds the relay-list change only (Task R1.2.13),
   and the DLQ schedule is already pinned in `docs/crm.md` section 10 by R1.1.
2. **The release also carries four spec items the programme's line for R1.2 does not list**, because the acceptance criteria
   name them: the organization claim on notification's three tokens (R1.2-3, Task R1.2.3), the repaired
   Engage recipe with `recipient_kind` (R1.2-5, Task R1.2.12), the production-smoke 360 step (R1.2-13, Task
   R1.2.4) and the doc-drift test (R1.2-14, Task R1.2.25).
3. **Strike-able items** (owner decision O-15, `S§6`): each is one self-contained task, so striking it removes
   the task and nothing else. Task R1.2.15 (`phone_index` stops, DD-15), Task R1.2.8 (offset-less `occurred_at`),
   Tasks R1.2.20 and R1.2.22 (derive rollups and placeholders, DD-5), Tasks R1.2.11 and R1.2.12 (the Engage
   recipe and its keys: if struck, the four keys still ship, the broken recipe is dropped and
   `recipient_kind` is not typed), Task R1.2.14 (the two dead dev gateway rows).

### Task index

| Task | Deploy | What | Spec item | Registry |
|---|---|---|---|---|
| R1.2.1 | a | Internal-client guard in `tests/infra` | R1.2-2 | `C23` |
| R1.2.2 | a | The 360 deal leg calls `/api/v1/deals`, with a mount-aware test | R1.2-1, R1.2-2, R1.2-10 | `C23` |
| R1.2.3 | a | Notification's three tokens carry the organization | R1.2-3 | `C77`, `J13` |
| R1.2.4 | a | The production smoke reads the 360 | R1.2-13 | |
| R1.2.5 | a | `docs/crm.md` records the R1.2a facts | | `I3` |
| R1.2.6 | b | Contact event schemas and customer's producer-side tests | R1.2-4 | `EV4` |
| R1.2.7 | b | Activity event schema and deal's producer-side tests | R1.2-4 | `EV5` |
| R1.2.8 | b | An offset-less `occurred_at` is read as UTC | R1.2-4 | `EV5` |
| R1.2.9 | b | Lead's three ahead-of-producer pins | R1.2-4 | `EV10`-`EV12` |
| R1.2.10 | b | Static CRM contract coverage and the phantom event names | R1.2-4, R1.2-8 | `EV4`, `EV5` |
| R1.2.11 | b | Four lead keys are subscribable, with the consent and no-number cases pinned | R1.2-5 | `EV39` |
| R1.2.12 | b | The repaired Engage recipe, `recipient_kind` and two builder rules | R1.2-5 | `EV39` |
| R1.2.13 | b | ops-monitor watches `lead`'s outbox | R1.2-6 | `F6` |
| R1.2.14 | b | Two dead dev gateway rows go; CRM prefixes pinned equal | R1.2-7 | `C71` |
| R1.2.15 | b | `leads.phone_index` stops being computed | R1.2-11 | `B40` |
| R1.2.16 | b | `STATUSES` | R1.2-8 | |
| R1.2.17 | b | `activities_for_deal` moves to the tests | R1.2-8 | |
| R1.2.18 | b | Three `bin/cron` relay copies | R1.2-8 | `F7` |
| R1.2.19 | b | The `sales` entitlement alias | R1.2-9 | `G15` |
| R1.2.20 | b | Seven dead `derive.ts` rollups and their orphans | R1.2-9 | `G15` |
| R1.2.21 | b | Other dead `product-crm` exports | R1.2-9 | `G15` |
| R1.2.22 | b | Four placeholder routes, their nav entries, the `NotFound` page, and a guard that keeps the rail and the route table agreeing | R1.2-9 | `G15` |
| R1.2.23 | b | `ActivityIn` description and `make openapi` | R1.2-10, R1.2-12 | `H6` |
| R1.2.24 | b | Stale comments in code and configuration | R1.2-10 | |
| R1.2.25 | b | `docs/crm.md` states what R1.2b delivered; the doc-drift test | R1.2-14 | `I3` |

### Prerequisites

**Releases.** R1.1 merged to `main` (P§10): R1.2a uses nothing from it except that Task R1.2.5 edits
`docs/crm.md`, which R1.1 creates; R1.2b's Task R1.2.25 reads that document. R1.2b needs R1.2a merged and
deployed and its verification passed (`S§5.9`): the drift test and the guard share files with R1.2a.

**Decisions.** None blocks R1.2. Every `D-n` is `open (default recommended)` in `docs/adr/0038-crm-programme-decisions.md`
and changes only documents if answered otherwise (`S§2.1`). The one owner decision that touches R1.2 is O-15,
the strike list above.

**Owner-run steps** (all **OWNER RUN**; `HOST=<ssh target> CONFIRM=<the box's EDGE_HOST>` as the owner
already uses them; the rows are `S§6`'s O-4 to O-15):

| # | When | Step | Expected, and the stop rule |
|---|---|---|---|
| O-4 | before each deploy | `make edge-status HOST=… CONFIRM=…` | the live ref, and whether `main` carries other unreleased work; read those releases' RUNBOOK notes first (R1.1's READMEs make deal and lead rebuild at R1.2a, which is expected) |
| O-5 | before each deploy | `make edge-preflight HOST=… CONFIRM=…` | the same memory total as today (3944 with `admin`, the owner's note) and the ceiling unchanged; R1.2 asks for **no** `.env` edit |
| O-6 | before each deploy | record the baselines the stop rules compare against (counts and names only): (a, R1.2b only) on the box database `SELECT count(*) FILTER (WHERE published_at IS NULL), min(created_at) FILTER (WHERE published_at IS NULL), count(*) FILTER (WHERE published_at IS NULL AND attempts >= 50) FROM lead.outbox;`; (b) the `dlq.notification.events` and `dlq.customer.events` lines of ops-monitor's status from `make edge-status`; (c, R1.2b only) `docker compose -f compose.prod.yml --env-file .env exec -T rabbitmq rabbitmqctl list_bindings source_name destination_name routing_key \| grep notification.events`; (d) `docker inspect --format '{{.Name}} {{.RestartCount}}' $(docker compose -f compose.prod.yml --env-file .env ps -q <service>)` for each container the deploy will recreate (customer and notification at R1.2a; customer, deal, lead, notification, app-gateway and ops-monitor at R1.2b); (e) the number of `request.completed` log lines with status 500 or above for `/api/v1/customers/{id}/360` (`make edge-logs … SERVICE=customer`) and for `POST /api/v1/deals/activities` (`SERVICE=deal`) over the last fifteen minutes | an existing dead letter, backlog, exhausted row or crash-looping container is recorded, never acted on. Without these numbers do not deploy |
| O-7 | before R1.2a | as a user holding `crm.book.contact.read` and `crm.pipeline.deal.read` at ORG (the default rep roles; an OWN reader in a tenant without HRMS gets a 403 by design and proves nothing), `GET /api/v1/customers/{contact_id}/360` through the gateway for a contact whose company has a deal | `crm` is **in** `unavailable`, confirming the diagnosis. If it already answers, stop: Task R1.2.2 is wrong. The same read answered 200 through app-gateway is R1.2b's gateway probe |
| O-8a | R1.2a | `make deploy HOST=… CONFIRM=… REF=<R1.2a merged sha>` | stop on the first failure; `make edge-rollback HOST=… CONFIRM=…` |
| O-9 | after R1.2a | repeat O-7 | `crm` is **absent** from `unavailable` and the section reads `{stage, open_opportunities, hot}` |
| O-13 | after R1.2a | `uv run python e2e/edge_product_crud.py --product crm`; it signs up one throwaway organization and one roster owner and removes only its own company, contact and deal (`e2e/edge_lib.py:165-290`, `e2e/edge_product_crud.py:435-450`), so run it **once**; the organization and owner stay until `tools/purge_test_orgs.py` (ADR-0036, RUNBOOK "Removing test organisations") | every step passes, including "the 360 shows the deal and its CRM section"; one new throwaway organization now exists, which is a count to note, not a failure |
| O-8b | R1.2b, after O-9 and O-13 | `make deploy HOST=… CONFIRM=… REF=<R1.2b merged sha>` | stop on the first failure; `make edge-rollback` returns the box to the R1.2a ref |
| O-10 | after R1.2b | the O-6(c) command | `notification.events` still has every binding O-6(c) recorded and exactly four more: `crm.lead.created`, `.updated`, `.converted` and `.conversion_aborted` |
| O-11 | after R1.2b | `make edge-status`; then repeat O-7 as the gateway probe | an `outbox.lead` check is listed and its pending and at-cap counts are no higher than O-6(a)'s (a baseline backlog may still alert: that is not a failure); the two DLQ depths are no deeper than O-6(b)'s; the 360 answers 200 through app-gateway with `crm` still absent from `unavailable` |
| O-12 | after R1.2b | from a checkout of the **deployed** ref: `make edge-web HOST=… CONFIRM=… APP=crm`, then `APP=engage` (Engage after notification, so the recipe's trigger is already in the catalogue); then on the box, with `W` the `EDGE_WEB_ROOT` in its `.env`, `grep -rlE "Exportable pipeline, forecast\|connectors will plug in here\|workspace preferences will live here\|setting up your revenue workspace are on the way" $W/crm/current` and `grep -rl "Welcome a converted lead" $W/engage/current` | the first prints nothing, the second prints the bundle's files; `make edge-web-rollback HOST=… CONFIRM=… APP=crm` (or `APP=engage`) if wrong |
| O-14 | optional | search the notification logs for `memberships.unreachable` (`make edge-logs … SERVICE=notification`) | if present it confirms that account's internal URL is unwired in production (inference, registry `L3`); wiring it is outside this programme and not part of R1.2 |
| O-15 | R1.2 review | decide the strike-able items above | each, if struck, is one task removed |

### Deploy order and verification

1. **Agent-run, before a pull request is marked ready.** R1.2a: tasks R1.2.1 to R1.2.5 each green by their own
   commands, then `uv run pytest tests/infra -q -p no:cacheprovider`, `make lint`, `make typecheck-services`,
   the per-service suites `cd services/customer && uv run pytest tests -q -p no:cacheprovider` and
   `cd services/notification && uv run pytest tests -q -p no:cacheprovider`, `make openapi-check`,
   `make contracts-check`, and the release invariants below. R1.2b: the same plus deal and lead suites, `make
   frontend-gate`, `make ui-visual`, `make migration-roundtrip` and `make migration-check`. Finally `make
   gate` locally, since Actions billing is blocked and a green local gate is the only full verification
   (P§3 F11).
2. **Release invariants** (`AC-1.2.11`, `AC-1.2.14`, `AC-1.2.15`, `AC-1.2.16`), each run at the end of both
   deploys:
   - `git diff --name-only <base>...HEAD -- libs ':!libs/contracts' pyproject.toml uv.lock .dockerignore`
     prints nothing, where `<base>` is the ref `make edge-status` reports as last good (DD-20:
     `needs_full_rebuild`, `infrastructure/edge/lib/rebuild-decision.sh:25-39`, is then false and each deploy
     is a per-service rebuild; with no last-good ref it is true, which is why O-4 reads the ref first).
   - `python3 infrastructure/edge/lib/memory_budget_totals.py infrastructure/edge/memory-budget.json
     --profiles storefront,inventory,hrms,crm,pulseboard,projects,support,marketing,finance,itsm` prints
     `3747` and with `,admin` appended `3944` (observed at HEAD; no container is added, removed or re-kinded).
   - `make migration-roundtrip` and `make migration-check` pass, and the three heads are unchanged:
     deal `e94d7a2c5f18`, customer `e230c025337e`, lead `91dd3ca663fd` (R1.2 adds no revision).
3. **R1.2a** order: merge, then OWNER RUN O-4, O-5, O-6 (b, d, e), O-7, O-8a, O-9, O-13. Inside the deploy the
   order does not matter: the corrected path already exists in the deployed deal, and the claim is additive
   and read by nobody yet. **R1.2b does not start before O-9 and O-13 pass.**
4. **R1.2b** order: merge, then O-4, O-5, O-6 (all), O-8b, O-10, O-11, O-12. Inside the deploy: lead's events
   were already being published, ops-monitor reads a table that exists, and notification binds its four new
   keys when it starts. A gateway probe is part of the stop conditions because app-gateway is recreated.
5. **No migration, no flag, no `make edge-reconcile`, no box `.env` edit** in either deploy
   (`python tools/export_permissions.py --check` stays green: no permission or role changes).

### Rollback

- **Code-only, whole ref.** `make edge-rollback HOST=… CONFIRM=…` redeploys the previous good ref for every
  service; there is no per-service rollback. Rolling R1.2b back returns the box to the R1.2a ref and keeps the
  360 fix; rolling R1.2a back returns it to the ref before. `make edge-web-rollback HOST=… CONFIRM=… APP=crm`
  (and `APP=engage`) points each bundle back one symlink. No schema moved.
- **What a rollback leaves behind.** Leads created while R1.2b was live carry `NULL` in `phone_index` (never
  read; the owner-run backfill `H8` rebuilds it if R3.7 resumes the index). Lead events folded by notification
  are `processed_events` rows (harmless). A rule or automation a tenant authored on a lead key while it was
  subscribable stays in its table and goes dormant when notification stops binding the key; the owner can count
  them with a read of `notification.rules` and the automation definitions whose event type starts `crm.lead.`.
- **Ordering constraint.** The organization claim (R1.2a, Task R1.2.3) is rolled back only after R3.1's
  enforcement, never before (registry `J13`); while R3.1 is not shipped there is no constraint.
- **Stop conditions** (any one stops the rollout and rolls back the ref of the deploy in hand; each compares
  with the O-6 baseline, never with zero). Both deploys: the deploy's own `health` step does not end with
  "health: every container is up" (`infrastructure/edge/remote.sh:386-412`); a recreated container's restart
  count is above its baseline; a DLQ depth is above its baseline; the 5xx count on the 360 or on `POST
  /deals/activities` in the minutes after exceeds the same-length window before. R1.2a: the O-9 probe still
  lists `crm` in `unavailable` for an entitled organization holding `crm.pipeline.deal.read` at ORG. R1.2b:
  `notification.events` is missing a baseline binding or has a binding beyond the four lead keys (O-10);
  `outbox.lead` pending or at-cap rows exceed the baseline; the gateway probe no longer answers 200.

### Files shared with the unmerged insights stack, and how to rebase

`a4b248f1` ("insights: deal, lead and customer facts") is on `sprint/insights-q2` and `ui/sprint-integration`
and is not an ancestor of HEAD (`git merge-base --is-ancestor a4b248f1 HEAD` exits 1; observed). It
changes four files this release also edits, each in a different, non-adjacent hunk, and it closes the eight
gaps Tasks R1.2.6, R1.2.7 and R1.2.9 pin. Whichever stack merges second re-applies its hunk on top of the
other (precedent: the `RE-PARENT HERE` marker):

| File | R1.2 edits | `a4b248f1` edits | Task |
|---|---|---|---|
| `services/deal/app/selectors.py` | removes `activities_for_deal` (`:138-151`) | the import block, and an export selector after `deal_out` | R1.2.17 |
| `services/deal/app/services.py` | reads an offset-less `occurred_at` as UTC after `:376` | the payloads of `create_deal`, `update_deal` and `move_stage` | R1.2.8 |
| `services/deal/app/schemas.py` | rewrites the `ActivityIn` docstring (`:157-160`) | the export models, appended at the end | R1.2.23 |
| `services/lead/app/services.py` | removes the `phone_index` lines (`:465-468,496`) | fields added to three payloads, from `:536` | R1.2.15 |

R1.2 edits none of the eight schema files and its new test files have different names from the insights
ones (`test_event_contracts.py` here, `test_insights_event_contracts.py` there). When `a4b248f1`'s stack
merges first, its merge turns the pin tests red on purpose; that merge deletes the rows it closes.

---

### Task R1.2.1: Internal-client guard

**Deploy:** R1.2a. **Spec:** `S§5.3.2` layer 2, `DD-7`, `AC-1.2.2`. **Registry:** `C23`.

**Files:**
- Create: `tests/infra/test_internal_client_paths.py`
- Test: `tests/infra/test_internal_client_paths.py`

**Interfaces:**
- Consumes (the real client functions, each already accepting an injected `httpx.AsyncClient`):
  `app.c1_360.fetch_crm(client, cfg, caller: CallerContext, company_id: uuid.UUID)` and
  `fetch_channels(client, cfg, caller, contact_id: uuid.UUID, email: str | None)` in customer
  (`services/customer/app/c1_360.py:157,182`); `app.clients.ensure_company_and_contact(settings, organization_id,
  *, company_id, contact_id, company_name, owner_id, contact_full_name, contact_email, contact_raw_phone,
  actor_id=None, client=None)`, `ensure_deal(settings, organization_id, *, deal_id, company_id, contact_id,
  owner_id, name, close_date, source, actor_id=None, client=None)` and `invalid_owner_ids(settings,
  organization_id, owner_ids, *, client=None)` in lead (`services/lead/app/clients.py:88,168,237`);
  `app.clients.fetch_crm_contact(settings, organization_id, crm_contact_id, *, client=None)` in finance
  (`services/finance/app/clients.py:56`); `app.targets.resolve_contact_target(settings, organization_id: str,
  contact_id: str, channel: str, *, client=None)`, `app.contacts.resolve_contact_by_phone(settings,
  organization_id: str, phone: str, *, client=None)` and `app.memberships.membership_is_active(settings,
  organization_id: str, membership_id: str, *, client=None)` in notification (`targets.py:59`, `contacts.py:65`,
  `memberships.py:36`).
- Produces: `Call(id, caller, callee, source, driver)` (`NamedTuple`); `CALLS: tuple[Call, ...]` with ids `A1` to
  `A9`; `UNAUDITED_BASELINE: frozenset[str]` (17 paths relative to `services/`); `KNOWN_UNREACHABLE` and
  `KNOWN_UNCLAIMED` (`dict[str, str]`, the ratchet that Tasks R1.2.2 and R1.2.3 empty and delete);
  `_audit(services_root: Path) -> tuple[list[str], list[str]]` (files that build an internal URL and are neither
  registered nor baselined; baselined files that no longer do). **Rule for every later release: a new internal
  client registers its calls in `CALLS` in the same commit, wherever in `services/` it lives** (SP4 adds about
  twenty, eleven of them outside the five audited services).

The nine rows are the audit of `S§3.4`: A1 customer to deal, A2 customer to notification, A3 and A5 lead to
customer, A4 lead to deal, A6 finance to customer, A7 and A8 notification to customer, A9 notification to
account. The three tests below are the three layers of `DD-7`: the real client builds the request, the real
callee app answers it over ASGI without credentials (401 or 403 means the route exists; 404 means it is not
mounted there; 405 means the wrong method), and the token the client minted is decoded and its `org` claim is
compared with the organization the client was given.

- [ ] **Step 1: Write the guard (with an empty ratchet)**

Create `tests/infra/test_internal_client_paths.py`:

```python
"""Every internal call the CRM-adjacent services make reaches a route its callee serves, names its
organization in its service token, and cannot be added without being registered here.

Why this file exists. `create_app` mounts every public router under `/api/v1` and every internal
router at the root (`libs/platform-core/src/platform_core/app.py:362-363`). The customer 360 asked
deal for `{deal_internal_url}/deals`, a path nobody serves, and its only test faked the upstream by
port, so the 404 was invisible: the `crm` section came back `null` and the failure was one log line.
This file is what keeps that class of defect from coming back unseen.

Three layers.

1. Each registered call is made by the REAL client function against an `httpx.MockTransport` that
   records the request, in a subprocess with the caller's service directory as cwd (`import app` is
   ambiguous across services, the same reason `test_route_table_parity.py` uses `_in_service`).
2. The recorded request is sent, without credentials, to the REAL callee app over ASGI. A route that
   exists answers 401 or 403 (authentication runs first); 404 means "not mounted there" and 405
   "wrong method".
3. The service token the client minted is decoded, and its `org` claim is compared with the
   organization the client was given.

A scan keeps the registry honest: every service file that builds an internal URL must be registered
in `CALLS` or named in `UNAUDITED_BASELINE`.

**Adding an internal client? Register its calls in `CALLS` in the same commit, wherever in
`services/` it lives.**
"""

from __future__ import annotations

import functools
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, NamedTuple

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES = REPO_ROOT / "services"

# --------------------------------------------------------------------------------- drivers

#: Runs first in every driver subprocess. Stable, valid UUIDs, so a recorded path can be sent to the
#: callee exactly as the client built it. `emit` prints the one recorded request as the last line.
_PRELUDE = """
import asyncio
import json
import uuid

import httpx
import jwt

ORG = "11111111-1111-4111-8111-111111111111"
USER = "33333333-3333-4333-8333-333333333333"
COMPANY = "44444444-4444-4444-8444-444444444444"
CONTACT = "22222222-2222-4222-8222-222222222222"
OWNER = "55555555-5555-4555-8555-555555555555"
DEAL = "66666666-6666-4666-8666-666666666666"
MEMBERSHIP = "77777777-7777-4777-8777-777777777777"

SEEN = []


def handler(request):
    SEEN.append(
        {
            "method": request.method,
            "path": request.url.path,
            "token": request.headers.get("X-Internal-Token"),
        }
    )
    return httpx.Response(200, json={})


def make_client():
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def attempt(awaitable):
    # Only the request matters here. Whether a client parses a stub answer is its own suite's job.
    try:
        await awaitable
    except Exception:
        pass


def emit(organization, settings=None, audience=None):
    (request,) = SEEN
    claims = None
    if request["token"] is not None:
        claims = jwt.decode(
            request["token"],
            settings.internal_service_token.get_secret_value(),
            algorithms=["HS256"],
            audience=audience,
        )
    print(
        json.dumps(
            {
                "method": request["method"],
                "path": request["path"],
                "claims": claims,
                "organization": organization,
            }
        )
    )
"""

# A1: customer -> deal. The caller's own identity is forwarded, so no service token is sent.
_A1 = """
from app import c1_360
from app.config import Settings
from platform_core.auth import Principal

org = uuid.UUID(ORG)
principal = Principal(sub=uuid.UUID(USER), organization_id=org, entitlements=["crm"])
caller = c1_360.CallerContext(
    organization_id=org, user_id=principal.sub, principal=principal, forward_headers={}
)


async def main():
    async with make_client() as client:
        await attempt(c1_360.fetch_crm(client, Settings(), caller, uuid.UUID(COMPANY)))


asyncio.run(main())
emit(ORG)
"""

# A2: customer -> notification. The path names no organization; the token's `org` claim does.
_A2 = """
from app import c1_360
from app.config import Settings
from platform_core.auth import Principal

org = uuid.UUID(ORG)
cfg = Settings()
principal = Principal(sub=uuid.UUID(USER), organization_id=org, entitlements=["engage"])
caller = c1_360.CallerContext(
    organization_id=org, user_id=principal.sub, principal=principal, forward_headers={}
)


async def main():
    async with make_client() as client:
        await attempt(
            c1_360.fetch_channels(client, cfg, caller, uuid.UUID(CONTACT), "jo@example.com")
        )


asyncio.run(main())
emit(ORG, cfg, "notification")
"""

# A3: lead -> customer `leads:convert`.
_A3 = """
from app import clients
from app.config import Settings

cfg = Settings()


async def main():
    async with make_client() as client:
        await attempt(
            clients.ensure_company_and_contact(
                cfg,
                uuid.UUID(ORG),
                company_id=uuid.UUID(COMPANY),
                contact_id=uuid.UUID(CONTACT),
                company_name="Acme Traders",
                owner_id=uuid.UUID(OWNER),
                contact_full_name="Jo Rivera",
                contact_email="jo@example.com",
                contact_raw_phone=None,
                client=client,
            )
        )


asyncio.run(main())
emit(ORG, cfg, "customer")
"""

# A4: lead -> deal `leads:convert`.
_A4 = """
from app import clients
from app.config import Settings

cfg = Settings()


async def main():
    async with make_client() as client:
        await attempt(
            clients.ensure_deal(
                cfg,
                uuid.UUID(ORG),
                deal_id=uuid.UUID(DEAL),
                company_id=uuid.UUID(COMPANY),
                contact_id=uuid.UUID(CONTACT),
                owner_id=uuid.UUID(OWNER),
                name="Acme Traders: first order",
                close_date="2026-12-01",
                source="form",
                client=client,
            )
        )


asyncio.run(main())
emit(ORG, cfg, "deal")
"""

# A5: lead -> customer `owners:validate`.
_A5 = """
from app import clients
from app.config import Settings

cfg = Settings()


async def main():
    async with make_client() as client:
        await attempt(
            clients.invalid_owner_ids(cfg, uuid.UUID(ORG), [uuid.UUID(OWNER)], client=client)
        )


asyncio.run(main())
emit(ORG, cfg, "customer")
"""

# A6: finance -> customer contact read.
_A6 = """
from app import clients
from app.config import Settings

cfg = Settings()


async def main():
    async with make_client() as client:
        await attempt(
            clients.fetch_crm_contact(cfg, uuid.UUID(ORG), uuid.UUID(CONTACT), client=client)
        )


asyncio.run(main())
emit(ORG, cfg, "customer")
"""

# A7: notification -> customer `messaging-target`.
_A7 = """
from app import targets
from app.config import Settings

cfg = Settings()


async def main():
    async with make_client() as client:
        await attempt(targets.resolve_contact_target(cfg, ORG, CONTACT, "whatsapp", client=client))


asyncio.run(main())
emit(ORG, cfg, "customer")
"""

# A8: notification -> customer `contacts:resolve`.
_A8 = """
from app import contacts
from app.config import Settings

cfg = Settings()


async def main():
    async with make_client() as client:
        await attempt(contacts.resolve_contact_by_phone(cfg, ORG, "+917976603446", client=client))


asyncio.run(main())
emit(ORG, cfg, "customer")
"""

# A9: notification -> account membership lookup.
_A9 = """
from app import memberships
from app.config import Settings

cfg = Settings()


async def main():
    async with make_client() as client:
        await attempt(memberships.membership_is_active(cfg, ORG, MEMBERSHIP, client=client))


asyncio.run(main())
emit(ORG, cfg, "account")
"""

# --------------------------------------------------------------------------------- the registry


class Call(NamedTuple):
    id: str
    caller: str  # the calling service's directory under services/
    callee: str  # the called service's directory; also the audience of its service token
    source: str  # the one file that builds the URL, relative to services/
    driver: str


CALLS: tuple[Call, ...] = (
    Call("A1", "customer", "deal", "customer/app/c1_360.py", _A1),
    Call("A2", "customer", "notification", "customer/app/c1_360.py", _A2),
    Call("A3", "lead", "customer", "lead/app/clients.py", _A3),
    Call("A4", "lead", "deal", "lead/app/clients.py", _A4),
    Call("A5", "lead", "customer", "lead/app/clients.py", _A5),
    Call("A6", "finance", "customer", "finance/app/clients.py", _A6),
    Call("A7", "notification", "customer", "notification/app/targets.py", _A7),
    Call("A8", "notification", "customer", "notification/app/contacts.py", _A8),
    Call("A9", "notification", "account", "notification/app/memberships.py", _A9),
)

#: Files that build an internal URL and were **not** audited (the audit read customer, deal, lead,
#: finance and notification). Unaudited is not clean: these may carry the path or claim defects the
#: five audited services had. The list may only shrink by registering a file's calls in `CALLS`, and
#: only grow on purpose, with the reason in the commit message.
UNAUDITED_BASELINE: frozenset[str] = frozenset(
    {
        # Build `/internal/v1/...` paths in place.
        "console/app/catalog_client.py",
        "console/app/delegation.py",
        "console/app/price.py",
        "console/app/subscription_client.py",
        "operations/app/orders/payment_client.py",
        # Hand a `*_service_url` to an `Http*` client defined elsewhere.
        "admin-gateway/app/main.py",
        "app-gateway/app/main.py",
        "approval/app/backfill.py",
        "auth/app/main.py",
        "billing/app/api/internal.py",
        "catalog/app/bundle_audit.py",
        "catalog/app/main.py",
        "subscription/app/backfill.py",
        "subscription/app/billing_run.py",
        "subscription/app/cron.py",
        "subscription/app/main.py",
        "subscription/app/worker.py",
    }
)

#: Defects this guard found on the base commit, each fixed by a named task of the plan, which
#: deletes its entry in the same commit as the fix. `strict=True` is the ratchet: once the fix
#: lands the test passes, the xfail becomes a failure, and the entry has to go.
KNOWN_UNREACHABLE: dict[str, str] = {}
KNOWN_UNCLAIMED: dict[str, str] = {}


def _params(calls: list[Call] | tuple[Call, ...], known: dict[str, str]) -> list[Any]:
    return [
        pytest.param(
            call,
            id=call.id,
            marks=pytest.mark.xfail(strict=True, reason=known[call.id]) if call.id in known else (),
        )
        for call in calls
    ]


# --------------------------------------------------------------------------------- the machinery


def _run(service: str, script: str, stdin: str | None = None) -> dict[str, Any]:
    """Run `script` with `services/<service>` as the import root; read the JSON on its last line."""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=SERVICES / service,
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"{service} subprocess failed:\n{result.stderr[-2000:]}"
    return dict(json.loads(result.stdout.strip().splitlines()[-1]))


@functools.cache
def _recorded(call_id: str) -> dict[str, Any]:
    call = next(c for c in CALLS if c.id == call_id)
    return _run(call.caller, _PRELUDE + call.driver)


_PROBE = """
import asyncio
import json
import sys

import httpx

from app.main import app

requests = json.loads(sys.stdin.read())


async def main():
    answers = {}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://probe"
    ) as client:
        for key, (method, path) in requests.items():
            answers[key] = (await client.request(method, path)).status_code
    return answers


print(json.dumps(asyncio.run(main())))
"""


def _probe(callee: str, requests: dict[str, tuple[str, str]]) -> dict[str, int]:
    """Send each `(method, path)` to the callee's real app, over ASGI, with no credentials."""
    answers = _run(callee, _PROBE, json.dumps(requests))
    return {key: int(status) for key, status in answers.items()}


@functools.cache
def _probe_registered(callee: str) -> dict[str, int]:
    requests = {
        call.id: (_recorded(call.id)["method"], _recorded(call.id)["path"])
        for call in CALLS
        if call.callee == callee
    }
    return _probe(callee, requests)


# --------------------------------------------------------------------------------- the tests


def test_the_probe_tells_a_route_that_exists_from_one_that_does_not() -> None:
    """Without this, a probe that answered 401 to everything would pass every call below. These
    four are facts about the real deal app: the 360's old path is a 404, its new one is a route."""
    answers = _probe(
        "deal",
        {
            "old-360-path": ("GET", "/deals"),
            "mounted": ("GET", "/api/v1/deals"),
            "wrong-method": ("DELETE", "/api/v1/deals"),
            "nowhere": ("GET", "/api/v1/nowhere"),
        },
    )
    assert answers["old-360-path"] == 404
    assert answers["mounted"] not in (404, 405)
    assert answers["wrong-method"] == 405
    assert answers["nowhere"] == 404


@pytest.mark.parametrize("call", _params(CALLS, KNOWN_UNREACHABLE))
def test_every_registered_call_reaches_a_route_the_callee_serves(call: Call) -> None:
    recorded = _recorded(call.id)
    status = _probe_registered(call.callee)[call.id]
    assert status not in (404, 405), (
        f"{call.id}: {call.caller} -> {call.callee}: {recorded['method']} {recorded['path']} "
        f"answered {status} ({'not mounted there' if status == 404 else 'wrong method'}). "
        "Public routers are mounted under /api/v1, internal routers at the root "
        "(libs/platform-core/src/platform_core/app.py:362-363)."
    )


#: A1 forwards the caller's identity headers and sends no service token.
TOKEN_CALLS = [call for call in CALLS if call.id != "A1"]


@pytest.mark.parametrize("call", _params(TOKEN_CALLS, KNOWN_UNCLAIMED))
def test_every_registered_call_names_its_organization_in_its_token(call: Call) -> None:
    recorded = _recorded(call.id)
    claims = recorded["claims"]
    assert claims is not None, f"{call.id} sent no X-Internal-Token"
    assert claims["iss"] == call.caller
    assert claims["aud"] == call.callee
    in_path = re.search(r"/internal/v1/organizations/([^/]+)/", recorded["path"])
    expected = in_path.group(1) if in_path else recorded["organization"]
    assert expected == recorded["organization"], "the driver and the URL disagree"
    assert claims.get("org") == expected, (
        f"{call.id}: the token {call.caller} minted for {call.callee} carries "
        f"org={claims.get('org')!r}, not the organization {expected} it was asked about"
    )


def test_the_nine_audited_calls_stay_registered() -> None:
    assert {f"A{n}" for n in range(1, 10)} <= {call.id for call in CALLS}


_URL_SETTING = re.compile(r"_internal_url|_service_url")


def _files_building_internal_urls(services_root: Path) -> set[str]:
    found: set[str] = set()
    for path in sorted(services_root.glob("*/app/**/*.py")):
        relative = path.relative_to(services_root)
        if relative.parts[1:] == ("app", "config.py"):
            continue  # the settings that name the URLs are not a client
        if _URL_SETTING.search(path.read_text(encoding="utf-8")):
            found.add(relative.as_posix())
    return found


def _audit(services_root: Path) -> tuple[list[str], list[str]]:
    """(files that build an internal URL and are neither registered nor baselined, baselined
    files that no longer do)."""
    registered = {call.source for call in CALLS}
    found = _files_building_internal_urls(services_root)
    return sorted(found - registered - UNAUDITED_BASELINE), sorted(UNAUDITED_BASELINE - found)


def test_every_internal_url_in_these_services_is_a_registered_call() -> None:
    unknown, stale = _audit(SERVICES)
    assert not unknown, (
        f"{unknown} build an internal URL and are not registered: register the call in CALLS "
        "(a client), or name the file in UNAUDITED_BASELINE and say why in the commit message"
    )
    assert not stale, (
        f"{stale} no longer build an internal URL: remove them from UNAUDITED_BASELINE"
    )


def test_the_guard_flags_an_unregistered_file(tmp_path: Path) -> None:
    app = tmp_path / "billing" / "app"
    app.mkdir(parents=True)
    (app / "new_client.py").write_text("url = f'{settings.finance_internal_url}/internal/v1/x'\n")
    (app / "config.py").write_text("finance_internal_url = 'http://finance:8000'\n")
    (app / "quiet.py").write_text("print('no internal url here')\n")
    unknown, stale = _audit(tmp_path)
    assert unknown == ["billing/app/new_client.py"]
    assert stale == sorted(UNAUDITED_BASELINE)  # nothing in this tree matches the baseline
```

- [ ] **Step 2: Run it and see the four real failures**

Run: `uv run pytest tests/infra/test_internal_client_paths.py -q -p no:cacheprovider`

Expected: FAIL, exactly four tests (observed at HEAD in a scratch copy, 6.3 s): `test_every_registered_call_reaches_a_route_the_callee_serves[A1]` with
`A1: customer -> deal: GET /deals answered 404 (not mounted there). Public routers are mounted under /api/v1, ...`,
and `test_every_registered_call_names_its_organization_in_its_token[A7]`, `[A8]` and `[A9]` with
`the token notification minted for customer carries org=None, not the organization 11111111-1111-4111-8111-111111111111 it was asked about`
(A9's says `minted for account`). The other seventeen pass, which also proves the probe is not vacuous:
`test_the_probe_tells_a_route_that_exists_from_one_that_does_not` pins `GET /deals` = 404 and `GET /api/v1/deals`
not 404 on the real deal app. This is the production defect, reproduced in-process with no stack.

- [ ] **Step 3: Record the four known defects in the ratchet**

The suite must stay green on the way to the fixes without hiding them. In `tests/infra/test_internal_client_paths.py`
replace

```python
KNOWN_UNREACHABLE: dict[str, str] = {}
KNOWN_UNCLAIMED: dict[str, str] = {}
```

with

```python
KNOWN_UNREACHABLE: dict[str, str] = {
    "A1": "customer asks deal for /deals; deal serves /api/v1/deals (Task R1.2.2)",
}
KNOWN_UNCLAIMED: dict[str, str] = {
    "A7": "notification mints its token to customer without an org claim (Task R1.2.3)",
    "A8": "notification mints its token to customer without an org claim (Task R1.2.3)",
    "A9": "notification mints its token to account without an org claim (Task R1.2.3)",
}
```

- [ ] **Step 4: Run it and see it pass, then prove the four are real**

Run: `uv run pytest tests/infra/test_internal_client_paths.py -q -p no:cacheprovider`
Expected: `17 passed, 4 xfailed` (observed).

Run: `uv run pytest tests/infra/test_internal_client_paths.py -q -p no:cacheprovider --runxfail`
Expected: `4 failed, 17 passed`, naming A1, A7, A8, A9 as in Step 2. This is `AC-1.2.2`'s "fails four times on the
base commit"; record the output in the pull request description.

- [ ] **Step 5: Lint**

Run: `uv run ruff check tests/infra/test_internal_client_paths.py && uv run ruff format --check tests/infra/test_internal_client_paths.py`
Expected: `All checks passed!` and `1 file already formatted`. (`tests/infra/**` ignores `S603`.)

- [ ] **Step 6: Commit**

```bash
git add tests/infra/test_internal_client_paths.py
git commit -m "Guard internal clients: every registered call reaches a served route and names its organization"
```

---

### Task R1.2.2: The 360 deal leg calls `/api/v1/deals`, with a mount-aware test

**Deploy:** R1.2a. **Spec:** `S§5.3.1`, `S§5.3.2` layer 1, `DD-6`, `AC-1.2.1`. **Registry:** `C23`.

**Files:**
- Modify: `services/customer/app/c1_360.py:100` (add the constant after `_TIMELINE_LIMIT`), `:69-70` (the `MISSING_UPSTREAMS` comment, two lines for two lines, so `_OPEN_STAGES` stays at `:95` where ADR-0038 cites it), `:166-170` (the call)
- Modify: `services/customer/tests/test_c1_customer360.py:1-7` (docstring), `:13-17` (imports), `:32` (the route table, after `Handler`), `:68-74` (`Fake.__call__`), `:154` (the path assertion), `:209` (two new classes before `class TestPartialResults`)
- Modify: `tests/infra/test_internal_client_paths.py` (`KNOWN_UNREACHABLE`: delete the A1 entry)
- Test: `services/customer/tests/test_c1_customer360.py`, `tests/infra/test_internal_client_paths.py`

**Interfaces:**
- Consumes: `libs/contracts/openapi/deal.json`, the committed `app.openapi()` of the real deal app, whose `paths`
  keys carry the `/api/v1` mount (`/api/v1/deals` has `get` and `post`; `get` declares the query parameters `q`,
  `stage`, `owner_id`, `company_id`, `page`, `size`: observed); `Settings.deal_internal_url` (`http://localhost:8131`
  by default, `http://deal:8000` in compose, no path); `KNOWN_UNREACHABLE` from Task R1.2.1.
- Produces: `app.c1_360.DEAL_LIST_PATH: Final = "/api/v1/deals"`; the test-local `RealDealRoutes` class
  (`serves(method: str, path: str) -> bool`, `query_parameters(method: str, template: str) -> set[str]`,
  `query_parameter(method: str, template: str, name: str) -> dict[str, Any]`) and its instance `DEAL_ROUTES`; two
  database-free tests that hold the leg to the route's parameter bound and to the fields the route always sends
  (the Review Focus line on silent upstream refusals). Nothing else changes: no route, no permission, no schema.

The route table is `libs/platform-core/src/platform_core/app.py:362-363` (`app.include_router(router,
prefix="/api/v1")`); deal's list route is `@router.get("/deals", ...)` (`services/deal/app/api/routes.py:78-84`),
so its real path is `/api/v1/deals`. Observed in-process against the real deal app: `GET /deals` answers 404 and
`GET /api/v1/deals` answers 401 (the route exists; authentication runs first). The diagnosis is no longer only
an inference from the route table; the owner's O-7 read still confirms it on the box.

- [ ] **Step 1: Write the failing tests**

In `tests/infra/test_internal_client_paths.py` delete A1 from the ratchet so the guard sees the defect:

```python
KNOWN_UNREACHABLE: dict[str, str] = {}
```

In `services/customer/tests/test_c1_customer360.py` make these edits.

1. Docstring (lines 1-7): append, after the sentence ending `like the rest of this suite.`:

```python

**The deal fake is mount-aware.** It answers only the routes the committed OpenAPI document of
the real deal app declares (`libs/contracts/openapi/deal.json`, whose keys carry the `/api/v1`
mount), and a 404 for anything else, exactly as production does. A fake that answered by port
alone let the 360 ask deal for `/deals`, a path nobody serves, while every test passed.
```

2. Imports (lines 13-17): add `import re` between `import json` and `import time`, and `from pathlib import Path`
between `from collections.abc import Callable, Iterator` and `from typing import Any`.

3. After `Handler = Callable[[httpx.Request], Any]` (line 32) add:

```python

#: `test_c1_customer360.py`, `tests/`, `customer`, `services`, ROOT.
DEAL_OPENAPI = Path(__file__).resolve().parents[3] / "libs" / "contracts" / "openapi" / "deal.json"
HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})


class RealDealRoutes:
    """The deal service's real route table: the committed OpenAPI document of its real app.

    `libs/contracts/openapi/deal.json` is `app.openapi()` of the deal app, byte-checked by `make
    openapi-check`, and its path keys carry the `/api/v1` mount (`/api/v1/deals`,
    `/api/v1/deals/{deal_id}`). A fake upstream that answers only what this table serves cannot
    hide a wrong mount path: the call gets the 404 the real service would give."""

    def __init__(self) -> None:
        self.document: dict[str, Any] = json.loads(DEAL_OPENAPI.read_text(encoding="utf-8"))
        self._routes: list[tuple[re.Pattern[str], frozenset[str]]] = []
        for template, operations in self.document["paths"].items():
            segments = [
                "[^/]+" if segment.startswith("{") else re.escape(segment)
                for segment in template.split("/")
            ]
            self._routes.append(
                (
                    re.compile("^" + "/".join(segments) + "$"),
                    frozenset(method.upper() for method in operations if method in HTTP_METHODS),
                )
            )

    def serves(self, method: str, path: str) -> bool:
        return any(
            pattern.match(path) is not None and method.upper() in methods
            for pattern, methods in self._routes
        )

    def query_parameters(self, method: str, template: str) -> set[str]:
        operation = self.document["paths"][template][method.lower()]
        return {p["name"] for p in operation.get("parameters", []) if p["in"] == "query"}

    def query_parameter(self, method: str, template: str, name: str) -> dict[str, Any]:
        """One query parameter as the document describes it, its `schema` and bounds included."""
        operation = self.document["paths"][template][method.lower()]
        return next(
            p for p in operation.get("parameters", []) if p["in"] == "query" and p["name"] == name
        )


DEAL_ROUTES = RealDealRoutes()
```

4. `Fake.__call__` (lines 68-74): replace

```python
    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        handler = self._deal if request.url.port == 8131 else self._notification
```

with

```python
    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.url.port == 8131 and not DEAL_ROUTES.serves(request.method, request.url.path):
            # What the real deal app answers for a path it does not route.
            return httpx.Response(404, json={"detail": "Not Found"})
        handler = self._deal if request.url.port == 8131 else self._notification
```

5. Line 154: replace `assert deal_call.url.path == "/deals"` with `assert deal_call.url.path == "/api/v1/deals"`.

6. Before `class TestPartialResults:` (line 209) insert:

```python
class TestDealLegPath:
    """The deal leg asks for the path deal really serves. These fail on a mount mistake the old
    fake could not see: it answered by port, whatever the path."""

    async def test_the_crm_section_comes_back_when_the_route_exists(
        self, client, org_id, data, fake
    ) -> None:
        fake()
        with as_principal(_reader(org_id)):
            body = (await _get(client, data["contact_a_id"], IDENTITY)).json()
        assert "crm" not in body["unavailable"]
        assert body["crm"] == {"stage": "negotiation", "open_opportunities": 1, "hot": True}

    def test_the_fake_would_have_refused_the_old_path(self) -> None:
        """Proof the fake is not vacuous: it is the real table, so `/deals` is a 404 in it."""
        assert not DEAL_ROUTES.serves("GET", "/deals")
        assert DEAL_ROUTES.serves("GET", "/api/v1/deals")

    def test_the_query_parameters_the_leg_sends_are_declared_by_the_route(self) -> None:
        declared = DEAL_ROUTES.query_parameters("GET", "/api/v1/deals")
        assert {"company_id", "size"} <= declared

    def test_the_page_size_the_leg_asks_for_is_within_the_bound_the_route_declares(self) -> None:
        """A declared parameter is not enough. Deal refuses a `size` above its maximum with a 422,
        and a 422 reads exactly like the 404 above: the section goes `null`, one log line says why
        and the page looks as it did before the fix. The bound is a literal in each service
        (`PageQuery`, `libs/platform-core/src/platform_core/pagination.py:33`, and
        `_DEAL_PAGE_SIZE`), so only the committed document can say whether they still agree."""
        size = DEAL_ROUTES.query_parameter("GET", "/api/v1/deals", "size")["schema"]
        assert size["minimum"] <= c1_360._DEAL_PAGE_SIZE <= size["maximum"]

    def test_the_fields_the_leg_reads_are_always_sent_by_the_route(self) -> None:
        """The leg parses `stage`, `probability` and `created_at` from every item
        (`c1_360._DealRow`), and one item without any of them fails the whole page: the section
        reads `null` for a reason no fake that returns a hand-written item can show. In the
        committed `DealOut`, which is what the route sends, each field must be required and
        never null."""
        deal_out = DEAL_ROUTES.document["components"]["schemas"]["DealOut"]
        for field in c1_360._DealRow.model_fields:
            assert field in deal_out["required"], field
            assert "null" not in json.dumps(deal_out["properties"][field]), field


class TestOwnWithoutSubjectRef:
    """A reader whose only contact grant is OWN, in a tenant with no HRMS, has no `subject_ref`
    (programme fact F1). The two cases below are the whole of what that means for the 360: one
    never reaches deal, the other reaches it and gets an honest empty answer."""

    async def test_an_own_contact_reader_without_a_subject_ref_is_forbidden(
        self, client, org_id, data, fake
    ) -> None:
        upstream = fake()
        principal = principal_for(
            org_id, ["crm.book.contact.read:OWN"], entitlements=["crm", "engage"]
        )
        with as_principal(principal):
            response = await _get(client, data["contact_a_id"], IDENTITY)
        assert response.status_code == 403
        assert upstream.calls == []  # the deal leg is never reached

    async def test_a_mixed_grant_reader_without_a_subject_ref_gets_an_empty_crm_section(
        self, client, org_id, data, fake
    ) -> None:
        """Characterization of customer's half only. Contact read at ORG passes; deal answers an
        empty list for a deal grant at OWN with no subject (`deal/tests/test_authz.py:56-58`), so
        the section reads empty rather than `null`."""
        fake(deal=lambda r: httpx.Response(200, json={"items": []}))
        principal = principal_for(
            org_id,
            ["crm.book.contact.read:ORG", "crm.pipeline.deal.read:OWN"],
            entitlements=["crm", "engage"],
        )
        with as_principal(principal):
            response = await _get(client, data["contact_a_id"], IDENTITY)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["crm"] == {"stage": None, "open_opportunities": 0, "hot": False}
        assert "crm" not in body["unavailable"]


```

The negative-authz set for this route already exists and stays green: 401 `TestAuthorization::test_unauthenticated_is_rejected`
(`:321`), 403 missing permission `::test_missing_the_permission_is_forbidden` (`:326`), 403 OWN scope on another
rep's contact `::test_own_scope_cannot_read_another_reps_contact` (`:333`), **404 cross-tenant**
`::test_another_organizations_contact_is_not_found` (`:351`), 404 for an unknown id or a company id
`::test_an_unknown_id_and_a_company_id_are_not_found` (`:360`), and 402 without the `crm` entitlement
`TestEntitlements::test_no_crm_entitlement_is_payment_required` (`:312`). The two new classes add the OWN
case that never reaches deal, and the mixed-grant case.

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/infra/test_internal_client_paths.py -q -p no:cacheprovider -k "reaches_a_route and A1"`
Expected: FAIL with `A1: customer -> deal: GET /deals answered 404 (not mounted there)` (observed).

Run: `cd services/customer && uv run pytest tests/test_c1_customer360.py -q -p no:cacheprovider`
Expected: FAIL (inference from reading; the suite needs PostgreSQL). The mount-aware fake answers 404 for `/deals`,
so `fetch_crm` raises `UpstreamUnavailable`, the section is `null` and `"crm"` is in `unavailable`. Seven tests
fail: `TestAggregation::test_full_picture`, `::test_deal_is_asked_for_this_account_under_the_callers_identity`
(the path assertion), `::test_open_stage_and_hotness_are_derived_from_open_deals_only`,
`::test_an_account_with_no_deals_is_an_empty_section_not_a_missing_one`,
`TestEntitlements::test_a_product_the_org_lacks_is_null_and_never_called`,
`TestDealLegPath::test_the_crm_section_comes_back_when_the_route_exists` and
`TestOwnWithoutSubjectRef::test_a_mixed_grant_reader_without_a_subject_ref_gets_an_empty_crm_section`. The four
database-free tests (`test_the_fake_would_have_refused_the_old_path`,
`test_the_query_parameters_the_leg_sends_are_declared_by_the_route`,
`test_the_page_size_the_leg_asks_for_is_within_the_bound_the_route_declares` and
`test_the_fields_the_leg_reads_are_always_sent_by_the_route`) and the OWN 403 test pass already: they are
characterizations of what HEAD already gets right (the two route-table tests were observed passing in a scratch
collection of this exact module: 26 tests collected; the bound and the shape checks were observed against the real
`libs/contracts/openapi/deal.json` and `c1_360.py`: `size` is `200` against a declared `maximum` of `200`, and
`stage`, `probability` and `created_at` are required and never null in `DealOut`).

- [ ] **Step 3: Write the minimal implementation**

In `services/customer/app/c1_360.py`, after `_TIMELINE_LIMIT: Final = 10` (line 100) add:

```python

#: Deal's public list route as the platform mounts it. `create_app` includes every router under
#: `/api/v1` (`libs/platform-core/src/platform_core/app.py:362-363`) and the route is declared
#: `@router.get("/deals", ...)` (`services/deal/app/api/routes.py:78-84`), so the real path is this
#: one. `DEAL_INTERNAL_URL` names a host and a port and no path.
DEAL_LIST_PATH: Final = "/api/v1/deals"
```

Replace lines 166-170 of `fetch_crm`:

```python
    response = await client.get(
        f"{cfg.deal_internal_url}/deals",
        params={"company_id": str(company_id), "size": _DEAL_PAGE_SIZE},
        headers=dict(caller.forward_headers),
    )
```

with

```python
    response = await client.get(
        f"{cfg.deal_internal_url}{DEAL_LIST_PATH}",
        params={"company_id": str(company_id), "size": _DEAL_PAGE_SIZE},
        headers=dict(caller.forward_headers),
    )
```

Correct the stale `MISSING_UPSTREAMS` comment. Its finance entry says finance has "no link to a CRM contact or
company"; `finance.customers.crm_contact_id` exists (`services/finance/app/models.py:111`) but no caller sets it
yet, because nothing calls finance's `customers:resolve-for-crm-contact` route
(`services/operations/app/orders/services.py:871-892` returns `None`). Replace lines 69-70

```python
#:   finance  finance keys invoices by its own `finance.customers` id, with no link to a CRM
#:            contact or company, and no internal read route
```

with two lines, so that no line below moves:

```python
#:   finance  finance keys invoices by its own `finance.customers` id; a row can carry a
#:            `crm_contact_id`, but no caller sets it yet and no route says what a contact owes
```

The module docstring paragraph at `:28-30` ("no endpoint that can be asked about a CRM contact today") stays: it
is still true, and it is not the sentence that states the false fact.

After the three edits the call is at `:172-176`, `fetch_channels`'s call at `:206-209` and `_OPEN_STAGES` is
still at `:95` (observed in a scratch copy; `ruff check` and `ruff format --check` are clean on it). Task R1.2.5
uses those numbers in `docs/crm.md`.

- [ ] **Step 4: Run them and see them pass**

Run: `uv run pytest tests/infra/test_internal_client_paths.py -q -p no:cacheprovider`
Expected: `18 passed, 3 xfailed` (A1 now passes; A7, A8, A9 are still ratcheted).

Run: `cd services/customer && uv run pytest tests/test_c1_customer360.py -q -p no:cacheprovider`
Expected: all 26 tests pass (inference; 19 existing plus 7 new).

Run: `cd services/customer && uv run mypy app`
Expected: `Success: no issues found`.

Run: `uv run ruff check services/customer tests/infra/test_internal_client_paths.py && uv run ruff format --check services/customer tests/infra/test_internal_client_paths.py`
Expected: `All checks passed!`.

- [ ] **Step 5: Commit**

```bash
git add services/customer/app/c1_360.py services/customer/tests/test_c1_customer360.py tests/infra/test_internal_client_paths.py
git commit -m "Customer 360: ask deal for /api/v1/deals, and test the leg against deal's real route table"
```

- [ ] **Step 6: Mutation check (not committed; paste the output in the pull request)**

The constant reverted must break the 360 and the guard, which is the evidence the tests are not vacuous
(`AC-1.2.1`).

Run: `sed -i.bak 's|DEAL_LIST_PATH: Final = "/api/v1/deals"|DEAL_LIST_PATH: Final = "/deals"|' services/customer/app/c1_360.py`
Run: `cd services/customer && uv run pytest tests/test_c1_customer360.py -q -p no:cacheprovider`
Expected: FAIL, including `test_full_picture` and `test_deal_is_asked_for_this_account_under_the_callers_identity`.
Run: `uv run pytest tests/infra/test_internal_client_paths.py -q -p no:cacheprovider -k "reaches_a_route and A1"`
Expected: FAIL with `GET /deals answered 404`.
Restore: `git checkout -- services/customer/app/c1_360.py && rm -f services/customer/app/c1_360.py.bak`
Run: `git status --short services/customer` Expected: no output.

The same for the parameter bound (the silent refusal the path test cannot see): a page size above the route's
declared maximum is a 422 in production and reads as `null` in the section.

Run: `sed -i.bak 's|_DEAL_PAGE_SIZE: Final = 200|_DEAL_PAGE_SIZE: Final = 250|' services/customer/app/c1_360.py`
Run: `cd services/customer && uv run pytest tests/test_c1_customer360.py -q -p no:cacheprovider -k "page_size"`
Expected: FAIL, `assert 1 <= 250 <= 200` raised from `test_the_page_size_the_leg_asks_for_is_within_the_bound_the_route_declares`.
Restore: `git checkout -- services/customer/app/c1_360.py && rm -f services/customer/app/c1_360.py.bak`
Run: `git status --short services/customer` Expected: no output.

---

### Task R1.2.3: Notification's three service tokens carry the organization

**Deploy:** R1.2a. **Spec:** `S§5.3.3`, `AC-1.2.2` (its last sentence). **Registry:** `C77`, `J13`.

**Files:**
- Modify: `services/notification/app/targets.py:77-81` (the `headers` block of `resolve_contact_target`)
- Modify: `services/notification/app/contacts.py:78-82` (`resolve_contact_by_phone`)
- Modify: `services/notification/app/memberships.py:52-56` (`membership_is_active`)
- Modify: `services/notification/tests/test_targets.py:11-19,32-34`, and a new test after `:65`
- Modify: `services/notification/tests/test_contacts.py:13-22,35-37`, and a new test after `:64`
- Modify: `services/notification/tests/test_memberships.py:10-14`, and a new test after `:54`
- Modify: `tests/infra/test_internal_client_paths.py` (empty `KNOWN_UNCLAIMED`)
- Test: the three notification files and `tests/infra/test_internal_client_paths.py`

**Interfaces:**
- Consumes: `mint_service_token(settings: ServiceSettings, *, audience: str, caller: str | None = None,
  claims: dict[str, Any] | None = None) -> str` (`libs/platform-core/src/platform_core/internal_auth.py:67-97`):
  `claims` are copied into the payload first and `iss`, `aud`, `iat`, `exp`, `jti` and `typ` are written after,
  so a caller cannot override them. The guard's `A7`, `A8` and `A9` from Task R1.2.1.
- Produces: the token on A7 (`GET .../contacts/{contact_id}/messaging-target`), A8 (`POST .../contacts:resolve`)
  and A9 (`GET .../memberships/{membership_id}`) carries `org` equal to the `organization_id` argument, the same
  string that is in the URL path. No signature changes: `resolve_contact_target(settings, organization_id: str,
  contact_id: str, channel: str, *, client=None)`, `resolve_contact_by_phone(settings, organization_id: str,
  phone: str, *, client=None)` and `membership_is_active(settings, organization_id: str, membership_id: str, *,
  client=None)` keep their parameter lists.

Customer's 360 already sends `org` and `sub` to notification (A2, `fetch_channels` in
`services/customer/app/c1_360.py`), and finance (`services/finance/app/clients.py:73`) and lead
(`services/lead/app/clients.py:106-111`) already send `org`. Customer reads no claim on `messaging-target` and
`contacts:resolve` today, and account's route checks only `iss` (`services/account/app/api/internal.py:304-346`), so
**this deploy changes no behaviour**. It is the first half of
an ordering rule (registry `J13`): the claim ships and soaks before R3.1 turns customer's `_require_org_claim` on
for those two routes, and a rollback of this change goes after R3.1's rollback, never before.

- [ ] **Step 1: Write the failing tests**

In `tests/infra/test_internal_client_paths.py` empty the ratchet's second table so the guard sees the three defects:

```python
KNOWN_UNCLAIMED: dict[str, str] = {}
```

In `services/notification/tests/test_targets.py`:

1. Replace the imports and the first helper (lines 11-19)

```python
import httpx
import pytest

from app import targets
from app.config import Settings


def _settings() -> Settings:
```

with

```python
import httpx
import jwt
import pytest

from app import targets
from app.config import Settings

ORG_ID = "11111111-1111-4111-8111-111111111111"


def _settings() -> Settings:
```

2. In `_resolve`, replace the call (lines 32-34)

```python
    return await targets.resolve_contact_target(
        _settings(), "11111111-1111-4111-8111-111111111111", "c0ffee", "whatsapp"
    )
```

with

```python
    return await targets.resolve_contact_target(_settings(), ORG_ID, "c0ffee", "whatsapp")
```

3. After `test_the_request_carries_a_token_and_the_channel` (after line 65, the last `assert` of that test) add:

```python


async def test_the_token_names_the_organization(monkeypatch: pytest.MonkeyPatch) -> None:
    """The `org` claim says which organization the call is about. Customer does not check it on
    this route yet; it has to be on the token before it can be checked without locking this
    caller out."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["token"] = request.headers.get("X-Internal-Token")
        return httpx.Response(200, json={"phone": "+917976603446"})

    await _resolve(handler, monkeypatch)
    claims = jwt.decode(
        str(seen["token"]),
        _settings().internal_service_token.get_secret_value(),
        algorithms=["HS256"],
        audience="customer",
    )
    assert claims["iss"] == "notification"
    assert claims.get("org") == ORG_ID
```

In `services/notification/tests/test_contacts.py`:

1. Replace the imports (lines 13-22)

```python
import httpx
import pytest

from app import contacts
from app.config import Settings
from app.contacts import ContactResolution


def _settings() -> Settings:
```

with

```python
import httpx
import jwt
import pytest

from app import contacts
from app.config import Settings
from app.contacts import ContactResolution

ORG_ID = "11111111-1111-4111-8111-111111111111"


def _settings() -> Settings:
```

2. In `_resolve`, replace the call (lines 35-37)

```python
    return await contacts.resolve_contact_by_phone(
        _settings(), "11111111-1111-4111-8111-111111111111", "+917976603446"
    )
```

with

```python
    return await contacts.resolve_contact_by_phone(_settings(), ORG_ID, "+917976603446")
```

3. After `test_the_request_carries_the_number_in_the_body_not_the_url` (after its last `assert`, line 64) add:

```python


async def test_the_token_names_the_organization(monkeypatch: pytest.MonkeyPatch) -> None:
    """See `test_targets.py`: the claim goes on the token first, and customer checks it later."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["token"] = request.headers.get("X-Internal-Token")
        return httpx.Response(200, json={"contact_id": None})

    await _resolve(handler, monkeypatch)
    claims = jwt.decode(
        str(seen["token"]),
        _settings().internal_service_token.get_secret_value(),
        algorithms=["HS256"],
        audience="customer",
    )
    assert claims["iss"] == "notification"
    assert claims.get("org") == ORG_ID
```

In `services/notification/tests/test_memberships.py` (it already has `ORG_ID`):

1. Replace `import httpx` / `import pytest` (lines 10-11) with

```python
import httpx
import jwt
import pytest
```

2. After `test_it_carries_a_service_token` (after its last line, `assert seen["token"]`, line 54) add:

```python


async def test_the_token_names_the_organization(monkeypatch: pytest.MonkeyPatch) -> None:
    """Account checks only the calling service on this route today; the organization claim is on
    the token so that checking it later cannot lock this caller out."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["token"] = request.headers.get("X-Internal-Token")
        return httpx.Response(200, json={"exists": True, "active": True})

    await _check(handler, monkeypatch)
    claims = jwt.decode(
        str(seen["token"]),
        _settings().internal_service_token.get_secret_value(),
        algorithms=["HS256"],
        audience="account",
    )
    assert claims["iss"] == "notification"
    assert claims.get("org") == ORG_ID
```

- [ ] **Step 2: Run them and see them fail**

Run: `cd services/notification && uv run pytest tests/test_targets.py tests/test_contacts.py tests/test_memberships.py -q -p no:cacheprovider`
Expected: FAIL, `3 failed, 31 passed` (observed on scratch copies of these exact three files at HEAD; they open no
database, so `make infra` is not needed for them). Each failure reads
`AssertionError: assert None == '11111111-1111-4111-8111-111111111111'`: the token carries `iss`, `aud`, `iat`,
`exp`, `jti` and `typ` and no `org`.

Run: `uv run pytest tests/infra/test_internal_client_paths.py -q -p no:cacheprovider`
Expected: FAIL, `3 failed, 18 passed` (observed with Task R1.2.2 applied): `test_every_registered_call_names_its_organization_in_its_token[A7]`,
`[A8]` and `[A9]`, with `the token notification minted for customer carries org=None, not the organization
11111111-1111-4111-8111-111111111111 it was asked about` (A9: `minted for account`).

- [ ] **Step 3: Write the minimal implementation**

In each of the three clients replace the `headers` block with the same block plus the claim and a comment.

`services/notification/app/targets.py` (lines 77-81, in `resolve_contact_target`) and
`services/notification/app/contacts.py` (lines 78-82, in `resolve_contact_by_phone`); the old text is identical in
both files:

```python
    headers = {
        "X-Internal-Token": mint_service_token(
            settings, audience=CUSTOMER_AUDIENCE, caller=settings.service_name
        )
    }
```

becomes, in both:

```python
    # The `org` claim names the organization this call is about. Customer does not check it on
    # this route yet; it goes on the token first so that checking it cannot lock this caller out.
    headers = {
        "X-Internal-Token": mint_service_token(
            settings,
            audience=CUSTOMER_AUDIENCE,
            caller=settings.service_name,
            claims={"org": organization_id},
        )
    }
```

`services/notification/app/memberships.py` (lines 52-56, in `membership_is_active`):

```python
    headers = {
        "X-Internal-Token": mint_service_token(
            settings, audience=ACCOUNT_AUDIENCE, caller=settings.service_name
        )
    }
```

becomes:

```python
    # The `org` claim names the organization this call is about. Account checks only the calling
    # service on this route today; the claim goes on the token first so that checking it later
    # cannot lock this caller out.
    headers = {
        "X-Internal-Token": mint_service_token(
            settings,
            audience=ACCOUNT_AUDIENCE,
            caller=settings.service_name,
            claims={"org": organization_id},
        )
    }
```

The `url = (...)` lines above each block do not move, so the call sites cited elsewhere
(`targets.py:73-76`, `contacts.py:74-77`, `memberships.py:48-51`) stay valid; the connection-error branch of
`memberships.py` moves from `:62-64` to `:68-70`.

- [ ] **Step 4: Run them and see them pass**

Run: `cd services/notification && uv run pytest tests/test_targets.py tests/test_contacts.py tests/test_memberships.py -q -p no:cacheprovider`
Expected: `34 passed` (observed with the clients patched in a scratch copy).

Run: `uv run pytest tests/infra/test_internal_client_paths.py -q -p no:cacheprovider`
Expected: `21 passed`, no xfail left: all nine calls reach a served route and eight name their organization (observed
with the Task R1.2.2 and R1.2.3 edits applied to a scratch copy of the services tree).

Run: `cd services/notification && uv run mypy app`
Expected: `Success: no issues found`.

Run: `uv run ruff check services/notification tests/infra && uv run ruff format --check services/notification tests/infra`
Expected: `All checks passed!` (observed on the scratch copies of the six files).

Run: `cd services/notification && uv run pytest tests -q -p no:cacheprovider` (needs `make infra` and `make migrate`)
Expected: the same pass count as before the change plus the three new tests; nothing else reads a claim these
clients send (inference).

- [ ] **Step 5: Commit**

```bash
git add services/notification/app/targets.py services/notification/app/contacts.py services/notification/app/memberships.py services/notification/tests/test_targets.py services/notification/tests/test_contacts.py services/notification/tests/test_memberships.py tests/infra/test_internal_client_paths.py
git commit -m "Notification: put the organization claim on its three service tokens"
```

---

### Task R1.2.4: The production smoke reads the 360

**Deploy:** R1.2a. **Spec:** `S§5.3.2` (production smoke), `DD-18`, `R1.2-13`, `AC-1.2.12`.

**Files:**
- Modify: `e2e/edge_product_crud.py` (insert after the stage-move step, ending at line 426, in `flow_crm` which starts at line 333)
- Create: `tests/infra/test_edge_smoke_covers_the_360.py`
- Test: `tests/infra/test_edge_smoke_covers_the_360.py`

**Interfaces:**
- Consumes: the locals of `flow_crm(c: httpx.Client, log: StepLog, h: dict[str, str], slug: str)`: `c` (the
  authenticated client), `h` (the auth headers), `contact` (the created contact, `contact["id"]`), `deal` (moved to
  `qualified` just before); `_require(log: StepLog, name: str, ok: bool, detail: str = "") -> None`
  (`e2e/edge_product_crud.py:54-58`), which logs a step and raises `Abort` when `ok` is false.
- Produces: one step named exactly `"the 360 shows the deal and its CRM section"`, between
  `"POST /deals/{id}/stage"` and `"delete the deal"`. Nothing else in the script changes.

The step reads data the flow has just made. The deal was created at stage `lead` and moved to `qualified`, whose
default probability is 40 (`services/deal/app/services.py:51-58`), so the 360's rule in `fetch_crm` (hot is
negotiation or a probability of 70 and over; open means one of the four open stages) gives
`{"stage": "qualified", "open_opportunities": 1, "hot": False}`. The throwaway
organization's owner holds the CRM admin roles, which read both contacts and deals at ORG. Each run of the script
leaves one throwaway organization and one roster owner on the box (`DD-18`); that is why the owner runs it once
(O-13).

The script cannot be imported by a test (it signs up real organizations over ssh and http), so the test reads it
as source with `ast`. This is a static check that the step exists, sits in the right place and asserts the section,
not only the status. The owner's run is the proof it works.

- [ ] **Step 1: Write the failing test**

Create `tests/infra/test_edge_smoke_covers_the_360.py`:

```python
"""The production CRUD smoke reads the Customer 360 between the stage move and the first delete.

`e2e/edge_product_crud.py::flow_crm` is the owner's standard post-deploy check for the CRM: it
creates a company, a contact and a deal on the box and removes them. The 360's deal leg was a 404
in production for a long time and every other check passed, because nothing asked the 360 for its
`crm` section. One read-only step does, on data the flow has just made.

The script is not importable here (it signs up real organisations over ssh and http), so it is read
as source: this pins that the step exists, where it sits, and what it asserts. The owner runs the
script; an agent never does.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SMOKE = REPO_ROOT / "e2e" / "edge_product_crud.py"

STEP = "the 360 shows the deal and its CRM section"


def _flow_crm() -> ast.FunctionDef:
    tree = ast.parse(SMOKE.read_text(encoding="utf-8"))
    return next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "flow_crm"
    )


def _steps(flow: ast.FunctionDef) -> list[tuple[str, ast.Call]]:
    """`(step name, the _require call)` in source order. `ast.walk` is breadth-first, so sort."""
    steps: list[tuple[str, ast.Call]] = []
    for node in ast.walk(flow):
        is_require = (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_require"
            and len(node.args) >= 3
        )
        if not is_require:
            continue
        assert isinstance(node, ast.Call)
        name = node.args[1]
        if isinstance(name, ast.Constant) and isinstance(name.value, str):
            steps.append((name.value, node))
    steps.sort(key=lambda step: (step[1].lineno, step[1].col_offset))
    return steps


def test_the_crm_flow_reads_the_360_after_the_stage_move_and_before_the_first_delete() -> None:
    names = [name for name, _call in _steps(_flow_crm())]
    assert STEP in names, f"flow_crm has no step named {STEP!r}; its steps are {names}"
    assert (
        names.index("POST /deals/{id}/stage") < names.index(STEP) < names.index("delete the deal")
    )


def test_the_360_step_asserts_the_crm_section_and_not_just_the_status() -> None:
    (call,) = [call for name, call in _steps(_flow_crm()) if name == STEP]
    condition = ast.unparse(call.args[2])
    assert "r.status_code == 200" in condition
    assert "'crm' not in" in condition and "'unavailable'" in condition
    assert "'open_opportunities': 1" in condition
```

- [ ] **Step 2: Run it and see it fail**

Run: `uv run pytest tests/infra/test_edge_smoke_covers_the_360.py -q -p no:cacheprovider`
Expected: FAIL, `2 failed` (observed against the unmodified script). The first with
`AssertionError: flow_crm has no step named 'the 360 shows the deal and its CRM section'; its steps are
['create an owner (POST /customers/owners)', 'create company', ..., 'POST /deals/{id}/stage', 'log an activity',
'delete the deal', ...]`, the second with `ValueError: not enough values to unpack (expected 1, got 0)`.

- [ ] **Step 3: Write the minimal implementation**

In `e2e/edge_product_crud.py`, `flow_crm`, immediately after the stage-move step

```python
    r = c.post(f"/api/v1/deals/{deal['id']}/stage", headers=h, json={"stage": "qualified"})
    _require(
        log,
        "POST /deals/{id}/stage",
        r.status_code == 200 and r.json().get("stage") == "qualified",
        f"{r.status_code} {r.text[:160]}",
    )
```

(lines 420-426) and before `r = c.post("/api/v1/deals/activities", ...)` (line 428), insert:

```python

    r = c.get(f"/api/v1/customers/{contact['id']}/360", headers=h)
    card = r.json() if r.status_code == 200 else {}
    _require(
        log,
        "the 360 shows the deal and its CRM section",
        r.status_code == 200
        and "crm" not in card.get("unavailable", [])
        and card.get("crm") == {"stage": "qualified", "open_opportunities": 1, "hot": False},
        f"{r.status_code} crm={card.get('crm')!r} unavailable={card.get('unavailable')!r}",
    )
```

- [ ] **Step 4: Run it and see it pass**

Run: `uv run pytest tests/infra/test_edge_smoke_covers_the_360.py -q -p no:cacheprovider`
Expected: `2 passed` (observed against a patched scratch copy of the script).

Run: `uv run ruff check e2e tests/infra && uv run ruff format --check e2e tests/infra`
Expected: `All checks passed!` (observed for the patched script under its repository path).

Run: `uv run python -c "import ast, pathlib; ast.parse(pathlib.Path('e2e/edge_product_crud.py').read_text())"`
Expected: no output (the script still parses). **Do not run the script.**

- [ ] **Step 5: Commit**

```bash
git add e2e/edge_product_crud.py tests/infra/test_edge_smoke_covers_the_360.py
git commit -m "Production smoke: read the Customer 360 and require its CRM section"
```

**OWNER RUN (O-13, after the R1.2a deploy and the O-9 read):** `uv run python e2e/edge_product_crud.py --product crm`.
Expected: every step passes, the new one included. The run leaves one throwaway organization and one roster owner on
the box; note the count and remove them with `tools/purge_test_orgs.py` when convenient. If the new step fails with
`crm=None unavailable=['crm', ...]`, the deploy has not taken effect or the diagnosis was wrong: stop and do not
start R1.2b.

---

### Task R1.2.5: `docs/crm.md` records what R1.2a did

**Deploy:** R1.2a. **Spec:** `S§5.3.2`, `S§5.3.3`, registry `I3` (the page and the code change in one release).

**Files:**
- Modify: `docs/crm.md` (R1.1 creates it): section 7's last two paragraphs and the A1, A2, A7, A8 and A9 rows of
  its table, section 11's row about the 360, and the "An internal client" bullet of section 12
- Test: `${TMPDIR:-/tmp}/crm-r1-2-checks/check-doc-a.sh` (a throwaway search test, created in Step 1, not committed)

**Interfaces:**
- Consumes: `docs/crm.md` from R1.1 (sections 7, 11, 12 as R1.1's plan writes them), and the line numbers Tasks
  R1.2.2 and R1.2.3 produce: `services/customer/app/c1_360.py:172-176` (the deal call) and `:206-209` (the channels
  call), `services/notification/app/memberships.py:68-70` (the connection-error branch).
- Produces: a page in which no sentence says R1.2a "is planned"; the A1 row says the path defect was fixed and by
  what is checked; A7 to A9 say the token carries the `org` claim; section 11 no longer lists the 360 as broken.
  The page's R1.2b forward references (`R1.2b is planned ...`) are untouched here and corrected by Task R1.2.25.

A search test is the honest test for a page: the unit under test is a sentence. The script fails while a sentence
still says the work is planned or cites a line that moved.

- [ ] **Step 1: Write the failing search test**

```bash
export R12="${TMPDIR:-/tmp}/crm-r1-2-checks"; mkdir -p "$R12"
cat > "$R12/check-doc-a.sh" <<'SH'
#!/usr/bin/env bash
# R1.2a: docs/crm.md states what R1.2a did, in the past tense. Run from the repository root.
set -u
FAILED=0
fail()  { printf 'FAIL: %s\n' "$*" >&2; FAILED=1; }
has()   { grep -qF -- "$2" "$1" 2>/dev/null || fail "$1 lacks: $2"; }
lacks() { if grep -qF -- "$2" "$1" 2>/dev/null; then fail "$1 still has: $2"; fi; }
cite()  { local a="${2%-*}" b="${2#*-}"; sed -n "${a},${b}p" "$1" 2>/dev/null | grep -qF -- "$3" || fail "$1:$2 does not contain: $3"; }

D=docs/crm.md
[ -f "$D" ] || { echo "docs/crm.md does not exist: R1.1 has not merged" >&2; exit 2; }
# the sentences that called this work planned
lacks "$D" 'R1.2a is planned'
lacks "$D" "R1.2a's guard test is planned"
lacks "$D" 'fix planned for R1.2a'
lacks "$D" 'claim planned for R1.2a'
lacks "$D" 'builds `{deal_internal_url}/deals`'
lacks "$D" 'its token carries no `org` claim'
lacks "$D" "The Customer 360's \`crm\` section is \`null\` in production"
# what the page says instead
has "$D" 'tests/infra/test_internal_client_paths.py'
has "$D" '{deal_internal_url}{DEAL_LIST_PATH}'
has "$D" 'UNAUDITED_BASELINE'
has "$D" 'its token carries the `org` claim (R1.2a)'
# the facts it states, at the lines it cites
cite services/customer/app/c1_360.py 172-176 'DEAL_LIST_PATH'
cite services/customer/app/c1_360.py 206-209 'messaging-summary'
cite services/customer/app/c1_360.py 95-95 '_OPEN_STAGES'
cite services/notification/app/targets.py 73-76 'messaging-target'
cite services/notification/app/contacts.py 74-77 'contacts:resolve'
cite services/notification/app/memberships.py 48-51 'memberships'
cite services/notification/app/memberships.py 68-70 'memberships.unreachable'
grep -qF 'DEAL_LIST_PATH: Final = "/api/v1/deals"' services/customer/app/c1_360.py || fail 'c1_360.py has no DEAL_LIST_PATH'
for f in targets contacts memberships; do
  grep -qF 'claims={"org": organization_id}' "services/notification/app/$f.py" || fail "$f.py does not send the org claim"
done
[ "$FAILED" = 0 ] && echo PASS || { echo FAILED; exit 1; }
SH
```

- [ ] **Step 2: Run it and see it fail**

Run: `bash "$R12/check-doc-a.sh"`
Expected: exit status 1; stderr lists `FAIL: docs/crm.md still has: R1.2a is planned`,
`... still has: fix planned for R1.2a`, `... still has: claim planned for R1.2a`,
`... still has: builds \`{deal_internal_url}/deals\``, `... still has: its token carries no \`org\` claim`,
`... still has: The Customer 360's \`crm\` section is \`null\` in production`, `... lacks: {deal_internal_url}{DEAL_LIST_PATH}`,
`... lacks: UNAUDITED_BASELINE` and `... lacks: its token carries the \`org\` claim (R1.2a)`; stdout says `FAILED`.
If it prints `docs/crm.md does not exist`, R1.1 has not merged: stop.

- [ ] **Step 3: Edit section 7**

Apply each pair below to `docs/crm.md`. If R1.1's wording differs from an `old` block, find the sentence with
`grep -n "R1.2a" docs/crm.md` and rewrite it in the same sense; the script is the judge.

````text old=docs/crm.md#hardening
contact's sealed number through them. Hardening both is registered for R3.1 and must follow the
organization claim that R1.2a is planned to add to notification's tokens (registry `J13`).
````

````text new=docs/crm.md#hardening
contact's sealed number through them. Hardening both is registered for R3.1. The organization claim
it needs is already on notification's tokens (R1.2a, registry `J13`), so the check can be turned on
without locking notification out.
````

````text old=docs/crm.md#nine-calls
leaving out `config.py`; `deal` builds none). R1.2a is planned to add a test that registers these
nine calls and probes each against the real callee.
````

````text new=docs/crm.md#nine-calls
leaving out `config.py`; `deal` builds none). `tests/infra/test_internal_client_paths.py` registers
these nine calls, makes each with the real client, sends the recorded request to the real callee app
and decodes the service token it carried.
````

````text old=docs/crm.md#a1
| A1 | customer's 360 | deal `GET /api/v1/deals`, forwarding the caller's identity | `services/customer/app/c1_360.py:166-170` builds `{deal_internal_url}/deals` | **path defect: 404** (inference from the route table, §11); fix planned for R1.2a |
````

````text new=docs/crm.md#a1
| A1 | customer's 360 | deal `GET /api/v1/deals`, forwarding the caller's identity | `services/customer/app/c1_360.py:172-176` builds `{deal_internal_url}{DEAL_LIST_PATH}`, where `DEAL_LIST_PATH` is `/api/v1/deals` | was a path defect (it asked for `/deals`, which deal answers 404); fixed in R1.2a, and the 360's own test and the guard now check the path against deal's real route table |
````

````text old=docs/crm.md#a2
| A2 | customer's 360 | notification `GET /internal/v1/contacts/{contact_id}/messaging-summary` | `services/customer/app/c1_360.py:200-203`; route `services/notification/app/api/c1_internal.py:81-86` | correct |
````

````text new=docs/crm.md#a2
| A2 | customer's 360 | notification `GET /internal/v1/contacts/{contact_id}/messaging-summary` | `services/customer/app/c1_360.py:206-209`; route `services/notification/app/api/c1_internal.py:81-86` | correct |
````

````text old=docs/crm.md#a7
`services/notification/app/targets.py:73-76`; its token carries no `org` claim (`:78-80`) | correct; claim planned for R1.2a |
````

````text new=docs/crm.md#a7
`services/notification/app/targets.py:73-76`; its token carries the `org` claim (R1.2a) | correct |
````

````text old=docs/crm.md#a8
`services/notification/app/contacts.py:74-77`; no `org` claim (`:79-81`) | correct; claim planned for R1.2a |
````

````text new=docs/crm.md#a8
`services/notification/app/contacts.py:74-77`; its token carries the `org` claim (R1.2a) | correct |
````

For A9 make two replacements inside the row:

````text old=docs/crm.md#a9-claim
`services/notification/app/memberships.py:48-51`; no `org` claim (`:53-55`); route
````

````text new=docs/crm.md#a9-claim
`services/notification/app/memberships.py:48-51`; its token carries the `org` claim (R1.2a); route
````

````text old=docs/crm.md#a9-lines
(`services/notification/app/memberships.py:62-64`), so in production an Engage conversation assignment would be refused; claim planned for R1.2a |
````

````text new=docs/crm.md#a9-lines
(`services/notification/app/memberships.py:68-70`), so in production an Engage conversation assignment would be refused; wiring the URL is outside this programme (registry `L3`) |
````

````text old=docs/crm.md#seventeen
Seventeen further files in nine other services build internal URLs from the same settings; nobody
has audited them (the list is the baseline that R1.2a's guard test is planned to record).
````

````text new=docs/crm.md#seventeen
Seventeen further files in nine other services build internal URLs from the same settings; nobody
has audited them. They are `UNAUDITED_BASELINE` in `tests/infra/test_internal_client_paths.py`: the
list may only shrink, by registering a file's calls, and the next client in any service has to be
registered or named there on purpose.
````

- [ ] **Step 4: Edit sections 11 and 12**

Delete this whole row from the table in section 11 (the defect is fixed; section 7's A1 row keeps the history):

````text old=docs/crm.md#limitation-360
| The Customer 360's `crm` section is `null` in production: its deal leg calls `/deals`, which deal serves only under `/api/v1` (inference from the route table; not observed on a running stack) | `services/customer/app/c1_360.py:166-170`; the one test that checks the path asserts the wrong value, and its fake deal service answers by port whatever the path (`services/customer/tests/test_c1_customer360.py:154`, `:68-74`) | R1.2a |
````

In section 12 replace the "An internal client" bullet:

````text old=docs/crm.md#internal-client
- **An internal client.** Register its calls in `tests/infra/test_internal_client_paths.py` (R1.2a is
  planned to add that guard; until it exists, extend the table in §7 by hand), and put the `org`
  claim on every token for a path under `/internal/v1/organizations/{org}/`.
````

````text new=docs/crm.md#internal-client
- **An internal client.** Register its calls in `tests/infra/test_internal_client_paths.py` in the
  same commit and extend the table in §7. The guard fails when a service file builds an internal URL
  that no registered call or baseline entry names, sends each registered request to the real callee
  app, and decodes the token it carried: put the `org` claim on every token for a path under
  `/internal/v1/organizations/{org}/`.
````

- [ ] **Step 5: Run the search test and see it pass, then look for stale citations**

Run: `bash "$R12/check-doc-a.sh"`
Expected: `PASS`.

Run: `grep -rn "c1_360.py:" docs --include='*.md' | grep -v superpowers`
Expected: every line printed cites a range that still holds: `:9-26` (the docstring), `:95` (`_OPEN_STAGES`, in
ADR-0038), `:172-176` and `:206-209` (the two rows just edited). Any other range (for example `:166-170` or
`:200-203`) is stale: fix it from the numbers above.

Run: `grep -n "R1.2a" docs/crm.md`
Expected: every remaining line says what R1.2a *did* (past tense or a fact), none says it will.

- [ ] **Step 6: Commit**

```bash
git add docs/crm.md
git commit -m "CRM page: record the 360 path fix, the organization claim and the internal-client guard"
```

---

### Task R1.2.6: Contact event contracts, and customer's producer-side tests

**Deploy:** R1.2b. **Spec:** `S§5.3.4` (i) and (iv), `AC-1.2.3`, `AC-1.2.4`. **Registry:** `EV4`.

**Files:**
- Create: `libs/contracts/events/crm.book.contact.created/v1.schema.json`
- Create: `libs/contracts/events/crm.book.contact.updated/v1.schema.json`
- Create: `libs/contracts/events/crm.book.contact.deleted/v1.schema.json`
- Create: `services/customer/tests/test_event_contracts.py`
- Test: `services/customer/tests/test_event_contracts.py`; the two existing tests of `libs/contracts/tests/test_event_schemas.py` that walk every committed schema

**Interfaces:**
- Consumes: `platform_core.events.validate_event_schema(event_type: str, version: int, payload: dict[str, Any], *,
  contracts_root: Path) -> None` (raises `FileNotFoundError` when no schema is committed, `jsonschema.ValidationError`
  when the payload does not match) and `event_schema_path(event_type, version, *, contracts_root) -> Path`
  (`libs/platform-core/src/platform_core/events.py:192-229`); customer's `app.services` writers and constants
  `EVT_COMPANY_CREATED`, `EVT_COMPANY_UPDATED`, `EVT_COMPANY_DELETED`, `EVT_CONTACT_CREATED`, `EVT_CONTACT_UPDATED`,
  `EVT_CONTACT_DELETED` (`services/customer/app/services.py:119-124`) and `create_company`, `update_company`,
  `delete_company`, `create_contact`, `update_contact`, `delete_contact`, `ensure_company_and_contact_for_lead`,
  `lead_conversion_ctx`; the fixtures `session`, `org_id` and `data` (keys `company_a`, `company_a_id`, `contact_a`,
  `contact_a_id`, `contact_b`, `contact_b_id`, `subject_a`) of `services/customer/tests/conftest.py:88-207` and
  `tests.helpers.ctx_for(organization_id, permission, scope)`.
- Produces: three committed schemas; `AHEAD_OF_PRODUCER: dict[str, tuple[frozenset[str], str]]` in customer's test
  module, mapping `crm.book.company.created` to `({"created_at"}, "R4.1")` and `crm.book.company.updated` to
  `({"name", "status", "owner_id"}, "R4.1")`; the guard that a schema starts with the comment `Ahead of the producer`
  exactly when its event is in that table (the same shape in deal and lead). Task R1.2.10 reads the comment and the
  eight names.

The contact events are the only customer events without a schema, though a producer publishes each
(`services/customer/app/services.py:695-701`, `:763-767`, `:806`) and notification binds `crm.book.contact.created`.
Each new schema describes **exactly what the producer emits today**, so it is conformant and carries no
`$comment`: `additionalProperties: false`, and the required list is the payload's key list. The legacy `full_name`
on `created` is documented as frozen. `status` is the contact's lifecycle value
(`services/customer/app/schemas.py:39`), `changed` a unique list of field names that carries no values.

- [ ] **Step 1: Write the failing test**

Create `services/customer/tests/test_event_contracts.py`:

```python
"""Every `crm.book.*` event, built through the real function that publishes it and validated
against the JSON Schema committed for it under `libs/contracts/events/`.

The static half (every schema is valid JSON Schema, one exists for every `crm.*` event a service
declares) is `libs/contracts/tests/test_event_schemas.py`. The deeper half lives here, because
checking a real *payload* needs a transaction to stage one through: the payload is read back from
the outbox row the service wrote, the same row the relay would publish, never a hand-written dict.

Two shapes of test, because two shapes of contract exist today:

- `TestConformant`: the schema and the producer agree, so the payload validates as it is.
- `TestAheadOfProducer`: the schema is committed ahead of its producer (its own `$comment` says
  which fields the producer does not emit yet). The test pins exactly what is missing, so the gap
  cannot drift silently and the commit that closes it cannot forget its test. The closing commit
  changes the producer, removes the schema's `$comment`, deletes the row below, deletes the
  event's name from `PINNED_AHEAD_OF_PRODUCER` in `libs/contracts/tests/test_event_schemas.py`,
  and moves the event to `TestConformant`.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import select

from app import services
from app.models import OutboxEvent
from app.schemas import CompanyPatch, ContactPatch
from platform_core.auth import Scope
from platform_core.events import event_schema_path, validate_event_schema
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration

#: `test_event_contracts.py`, `tests/`, `customer`, `services`, ROOT.
CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"

#: Event type -> (required properties the producer does not emit yet, the release that closes it).
AHEAD_OF_PRODUCER: dict[str, tuple[frozenset[str], str]] = {
    services.EVT_COMPANY_CREATED: (frozenset({"created_at"}), "R4.1"),
    services.EVT_COMPANY_UPDATED: (frozenset({"name", "status", "owner_id"}), "R4.1"),
}


def _schema(event_type: str) -> dict[str, Any]:
    path = event_schema_path(event_type, 1, contracts_root=CONTRACTS_ROOT)
    return dict(json.loads(path.read_text(encoding="utf-8")))


async def _staged(session: Any, event_type: str, aggregate_id: uuid.UUID) -> dict[str, Any]:
    """The payload of the one `event_type` event staged for this aggregate, read back the way the
    relay would read it."""
    async with session.begin():
        rows = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == event_type,
                        OutboxEvent.aggregate_id == aggregate_id,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1, f"expected one {event_type} for {aggregate_id}, found {len(rows)}"
    return dict(rows[0].payload)


def _conforms(event_type: str, payload: dict[str, Any]) -> None:
    validate_event_schema(event_type, 1, payload, contracts_root=CONTRACTS_ROOT)


def _gap(event_type: str, payload: dict[str, Any]) -> tuple[frozenset[str], list[str]]:
    """What the schema requires that the payload lacks, and every *other* thing wrong with it
    (a type or enum error on a field that is emitted). The second list must stay empty."""
    schema = _schema(event_type)
    missing = frozenset(schema["required"]) - payload.keys()
    others = [
        error.message
        for error in Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(
            payload
        )
        if error.validator != "required"
    ]
    return missing, others


def _assert_pinned(event_type: str, payload: dict[str, Any]) -> None:
    pinned, release = AHEAD_OF_PRODUCER[event_type]
    missing, others = _gap(event_type, payload)
    assert missing == pinned, (
        f"{event_type}: the schema requires {sorted(missing)} that the producer does not emit; "
        f"the pin says {sorted(pinned)} (closes in {release}). If the producer now emits them, "
        "this is the closing commit: remove the schema's $comment, delete the pin row and move the "
        "event to TestConformant."
    )
    assert others == [], f"{event_type}: a field the producer does emit is wrong: {others}"


def _write(org_id: uuid.UUID) -> Any:
    return ctx_for(org_id, "crm.book.company.write", Scope.ORG)


class TestConformant:
    async def test_company_deleted_matches_its_schema(self, session, org_id, data) -> None:
        async with session.begin():
            company = await services.create_company(
                session, _write(org_id), name="Short-lived Co", owner_id=data["subject_a"]
            )
            await services.delete_company(session, _write(org_id), company)
        _conforms(
            services.EVT_COMPANY_DELETED,
            await _staged(session, services.EVT_COMPANY_DELETED, company.id),
        )

    async def test_contact_created_matches_its_schema(self, session, org_id, data) -> None:
        async with session.begin():
            contact = await services.create_contact(
                session,
                _write(org_id),
                full_name="Meera Nair",
                email="meera.nair@northwind.co",
                company_id=data["company_a_id"],
                owner_id=data["subject_a"],
                status="qualified",
            )
        payload = await _staged(session, services.EVT_CONTACT_CREATED, contact.id)
        _conforms(services.EVT_CONTACT_CREATED, payload)
        assert payload["status"] == "qualified"
        assert payload["company_id"] == str(data["company_a_id"])

    async def test_contact_created_by_a_lead_conversion_matches_its_schema(
        self, session, org_id, data
    ) -> None:
        """The same event, from the other writer: a lead's conversion reuses `create_contact`
        (`ensure_company_and_contact_for_lead`), so a tenant automation sees one shape."""
        contact_id = uuid.uuid4()
        async with session.begin():
            _company, contact = await services.ensure_company_and_contact_for_lead(
                session,
                services.lead_conversion_ctx(org_id, None),
                company_id=uuid.uuid4(),
                contact_id=contact_id,
                company_name="Converted Traders",
                owner_id=data["subject_a"],
                contact_full_name="Lee Converted",
                contact_email="lee.converted@converted.example",
                contact_raw_phone=None,
            )
        assert contact is not None
        payload = await _staged(session, services.EVT_CONTACT_CREATED, contact_id)
        _conforms(services.EVT_CONTACT_CREATED, payload)
        assert payload["status"] == "customer"

    async def test_contact_updated_matches_its_schema(self, session, org_id, data) -> None:
        async with session.begin():
            await services.update_contact(
                session, _write(org_id), data["contact_a"], ContactPatch(title="Head of Ops")
            )
        payload = await _staged(session, services.EVT_CONTACT_UPDATED, data["contact_a_id"])
        _conforms(services.EVT_CONTACT_UPDATED, payload)
        assert payload["changed"] == ["title"]

    async def test_contact_deleted_matches_its_schema(self, session, org_id, data) -> None:
        async with session.begin():
            await services.delete_contact(session, _write(org_id), data["contact_b"])
        _conforms(
            services.EVT_CONTACT_DELETED,
            await _staged(session, services.EVT_CONTACT_DELETED, data["contact_b_id"]),
        )


class TestAheadOfProducer:
    async def test_company_created_lacks_exactly_the_pinned_fields(
        self, session, org_id, data
    ) -> None:
        payload = await _staged(session, services.EVT_COMPANY_CREATED, data["company_a_id"])
        _assert_pinned(services.EVT_COMPANY_CREATED, payload)

    async def test_company_updated_lacks_exactly_the_pinned_fields(
        self, session, org_id, data
    ) -> None:
        async with session.begin():
            await services.update_company(
                session, _write(org_id), data["company_a"], CompanyPatch(city="Pune")
            )
        payload = await _staged(session, services.EVT_COMPANY_UPDATED, data["company_a_id"])
        _assert_pinned(services.EVT_COMPANY_UPDATED, payload)


def test_the_ahead_comment_and_the_pin_table_agree() -> None:
    """A schema carries the "Ahead of the producer" comment exactly when its event is pinned, for
    every event this service declares: a new gap must be pinned, and a closed one must lose both."""
    declared = sorted(
        value
        for name, value in vars(services).items()
        if name.startswith("EVT_") and isinstance(value, str)
    )
    assert declared, "expected app.services to declare its EVT_* constants"
    for event_type in declared:
        ahead = str(_schema(event_type).get("$comment", "")).startswith("Ahead of the producer")
        assert ahead == (event_type in AHEAD_OF_PRODUCER), event_type
```

- [ ] **Step 2: Run it and see it fail**

Run: `cd services/customer && uv run pytest tests/test_event_contracts.py -q -p no:cacheprovider` (needs `make infra`
and `make migrate`)
Expected: FAIL, `5 failed, 3 passed` (inference for the database-backed tests; the last one observed, see below). The
four contact tests fail with `FileNotFoundError: No committed schema for crm.book.contact.created v1 at
.../libs/contracts/events/crm.book.contact.created/v1.schema.json - add one under libs/contracts/events/.` (and the
`updated` and `deleted` siblings), and `test_the_ahead_comment_and_the_pin_table_agree` fails with `FileNotFoundError:
[Errno 2] No such file or directory: '.../crm.book.contact.created/v1.schema.json'`. The company deleted test and the
two pin tests pass already: those schemas exist and the gaps are today's. The agreement test needs no database; it
was observed failing that way against HEAD and passing against a tree with the four schemas.

- [ ] **Step 3: Write the schemas**

Create `libs/contracts/events/crm.book.contact.created/v1.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "opsgrids://contracts/crm.book.contact.created/v1",
  "title": "crm.book.contact.created",
  "description": "A person was added to a company in the account book, by hand, by import or by a lead's conversion. `full_name` is carried for tenant automations that already render it and is frozen as legacy: a new consumer reads `contact_id` and asks customer for anything else. `status` is the contact's lifecycle status at creation.",
  "type": "object",
  "additionalProperties": false,
  "required": ["organization_id", "contact_id", "company_id", "full_name", "status"],
  "properties": {
    "organization_id": { "type": "string", "format": "uuid" },
    "contact_id": { "type": "string", "format": "uuid" },
    "company_id": { "type": "string", "format": "uuid" },
    "full_name": { "type": "string", "minLength": 1, "maxLength": 160 },
    "status": { "enum": ["customer", "qualified", "lead", "dormant"] }
  }
}
```

Create `libs/contracts/events/crm.book.contact.updated/v1.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "opsgrids://contracts/crm.book.contact.updated/v1",
  "title": "crm.book.contact.updated",
  "description": "A contact's plain fields were patched (never the phone, which has its own route). `changed` names the patched fields and carries no values.",
  "type": "object",
  "additionalProperties": false,
  "required": ["organization_id", "contact_id", "changed"],
  "properties": {
    "organization_id": { "type": "string", "format": "uuid" },
    "contact_id": { "type": "string", "format": "uuid" },
    "changed": { "type": "array", "items": { "type": "string" }, "uniqueItems": true }
  }
}
```

Create `libs/contracts/events/crm.book.contact.deleted/v1.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "opsgrids://contracts/crm.book.contact.deleted/v1",
  "title": "crm.book.contact.deleted",
  "description": "A contact was soft-deleted. The row stays and nothing cascades; activities that name it keep the id.",
  "type": "object",
  "additionalProperties": false,
  "required": ["organization_id", "contact_id"],
  "properties": {
    "organization_id": { "type": "string", "format": "uuid" },
    "contact_id": { "type": "string", "format": "uuid" }
  }
}
```

- [ ] **Step 4: Run it and see it pass**

Run: `cd services/customer && uv run pytest tests/test_event_contracts.py -q -p no:cacheprovider`
Expected: `8 passed` (inference for the database-backed tests; the agreement test observed passing).

Run: `uv run pytest libs/contracts -q -p no:cacheprovider`
Expected: all pass: `test_every_committed_schema_is_valid_json_schema` and
`test_every_schema_sits_where_schema_uri_resolves_it` now also walk the three new files (their `$id` is
`opsgrids://contracts/<type>/v1`).

Run: `ls libs/contracts/events | grep -c '^crm\.'`
Expected: `14` (11 before, three added; the fourth missing schema arrives with Task R1.2.7).

Run: `cd services/customer && uv run mypy app && cd ../.. && uv run ruff check services/customer libs/contracts && uv run ruff format --check services/customer`
Expected: `Success: no issues found`, then `All checks passed!` and `already formatted` (the new test module was
observed clean under the repository's ruff configuration).

- [ ] **Step 5: Commit**

```bash
git add libs/contracts/events/crm.book.contact.created libs/contracts/events/crm.book.contact.updated libs/contracts/events/crm.book.contact.deleted services/customer/tests/test_event_contracts.py
git commit -m "Add contracts for the contact events and test customer's events against their schemas"
```

- [ ] **Step 6: Mutation check (not committed; paste the output in the pull request)**

A conformance test that cannot fail proves nothing. Tighten a schema beyond what the producer emits and see the test
fail, then restore it.

Run: `sed -i.bak 's|"required": \["organization_id", "contact_id", "company_id", "full_name", "status"\]|"required": ["organization_id", "contact_id", "company_id", "full_name", "status", "created_at"]|' libs/contracts/events/crm.book.contact.created/v1.schema.json`
Run: `cd services/customer && uv run pytest tests/test_event_contracts.py -q -p no:cacheprovider -k contact_created`
Expected: FAIL twice, `jsonschema.exceptions.ValidationError: 'created_at' is a required property` (inference).
Restore: `git checkout -- libs/contracts/events/crm.book.contact.created/v1.schema.json && rm -f libs/contracts/events/crm.book.contact.created/v1.schema.json.bak`
Run: `git status --short libs/contracts` Expected: no output.

---

### Task R1.2.7: The activity event contract, and deal's producer-side tests

**Deploy:** R1.2b. **Spec:** `S§5.3.4` (i) and (iv), `AC-1.2.3`, `AC-1.2.4`. **Registry:** `EV5`.

**Files:**
- Create: `libs/contracts/events/crm.pipeline.activity.logged/v1.schema.json`
- Create: `services/deal/tests/test_event_contracts.py`
- Test: `services/deal/tests/test_event_contracts.py`; the walking tests of `libs/contracts/tests/test_event_schemas.py`

**Interfaces:**
- Consumes: `validate_event_schema` and `event_schema_path` (as in Task R1.2.6); deal's `app.services` constants
  `EVT_DEAL_CREATED`, `EVT_DEAL_UPDATED`, `EVT_DEAL_DELETED`, `EVT_DEAL_STAGE_CHANGED`, `EVT_ACTIVITY_LOGGED`
  (`services/deal/app/services.py:43-47`) and `delete_deal`, `update_deal`, `move_stage(session, ctx, deal, *,
  new_stage, ...)`, `log_activity(session, ctx, *, activity_type, subject, owner_id, body=None, deal_id=None,
  company_id=None, contact_id=None, direction=None, occurred_at=None, activity_id=None, request=None)`
  (`:353-368`); `app.schemas.DealPatch`; the fixtures `session`, `org_id`, `data` (keys `deal_a`, `deal_a_id`, `deal_b`,
  `deal_b_id`, `contact_a`, `subject_a`; `services/deal/tests/conftest.py:115-183`); `tests.helpers.ctx_for`.
- Produces: the schema; `AHEAD_OF_PRODUCER` in deal's test module for `crm.pipeline.deal.created` (`{company_id,
  close_date, probability, closed_at, created_at}`), `.updated` (`{name, stage, value_minor, currency, owner_id,
  company_id, close_date, probability, closed_at}`) and `.stage_changed` (`{name, value_minor, currency, owner_id,
  probability, closed_at}`), each closing in `R2.3`; the same `$comment`/pin agreement test as customer's.

`crm.pipeline.activity.logged` is the one event another service consumes (customer advances a contact's
`last_activity_at` from it, `services/customer/app/consumers.py:97`) and it had no contract. The producer
(`services/deal/app/services.py:428-435`) emits `organization_id, activity_id, type, deal_id, contact_id,
occurred_at` and nothing else, so the schema requires exactly those six keys, allows `null` for the two optional
ids, and carries no `subject` or `body`: ids and an enumeration only. `occurred_at` is `format: date-time`; the
validator does **not** machine-check that format in this environment (`rfc3339-validator` is not installed), so the
offset is asserted by hand in the test.

- [ ] **Step 1: Write the failing test**

Create `services/deal/tests/test_event_contracts.py`:

```python
"""Every `crm.pipeline.*` event, built through the real function that publishes it and validated
against the JSON Schema committed for it under `libs/contracts/events/`.

The static half (every schema is valid JSON Schema, one exists for every `crm.*` event a service
declares) is `libs/contracts/tests/test_event_schemas.py`. The deeper half lives here, because
checking a real *payload* needs a transaction to stage one through: the payload is read back from
the outbox row the service wrote, the same row the relay would publish, never a hand-written dict.

- `test_deal_deleted_*` and `test_activity_logged_*`: the schema and the producer agree.
- `TestAheadOfProducer`: the deal schemas are committed ahead of their producer (each schema's
  `$comment` says which fields the producer does not emit yet). The test pins exactly what is
  missing, so the gap cannot drift silently and the commit that closes it cannot forget its test.
  The closing commit (the stage work, R2.3) changes the producer, removes the schema's `$comment`,
  deletes the row below, deletes the event's name from `PINNED_AHEAD_OF_PRODUCER` in
  `libs/contracts/tests/test_event_schemas.py`, and validates the event with
  `validate_event_schema` like the two below.

`date-time` is not machine-checked in this environment (`rfc3339-validator` is not installed, so
`validate_event_schema` accepts any string for it): the activity test asserts the offset by hand.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import select

from app import services
from app.models import OutboxEvent
from app.schemas import DealPatch
from platform_core.auth import Scope
from platform_core.events import event_schema_path, validate_event_schema
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration

#: `test_event_contracts.py`, `tests/`, `deal`, `services`, ROOT.
CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"

#: Event type -> (required properties the producer does not emit yet, the release that closes it).
AHEAD_OF_PRODUCER: dict[str, tuple[frozenset[str], str]] = {
    services.EVT_DEAL_CREATED: (
        frozenset({"company_id", "close_date", "probability", "closed_at", "created_at"}),
        "R2.3",
    ),
    services.EVT_DEAL_UPDATED: (
        frozenset(
            {
                "name",
                "stage",
                "value_minor",
                "currency",
                "owner_id",
                "company_id",
                "close_date",
                "probability",
                "closed_at",
            }
        ),
        "R2.3",
    ),
    services.EVT_DEAL_STAGE_CHANGED: (
        frozenset({"name", "value_minor", "currency", "owner_id", "probability", "closed_at"}),
        "R2.3",
    ),
}


def _schema(event_type: str) -> dict[str, Any]:
    path = event_schema_path(event_type, 1, contracts_root=CONTRACTS_ROOT)
    return dict(json.loads(path.read_text(encoding="utf-8")))


async def _staged(session: Any, event_type: str, aggregate_id: uuid.UUID) -> dict[str, Any]:
    """The payload of the one `event_type` event staged for this aggregate, read back the way the
    relay would read it."""
    async with session.begin():
        rows = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == event_type,
                        OutboxEvent.aggregate_id == aggregate_id,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1, f"expected one {event_type} for {aggregate_id}, found {len(rows)}"
    return dict(rows[0].payload)


def _conforms(event_type: str, payload: dict[str, Any]) -> None:
    validate_event_schema(event_type, 1, payload, contracts_root=CONTRACTS_ROOT)


def _assert_pinned(event_type: str, payload: dict[str, Any]) -> None:
    """The schema requires exactly the pinned fields the payload lacks, and nothing a field that
    *is* emitted breaks (a type or enum error is a different defect from a missing field)."""
    pinned, release = AHEAD_OF_PRODUCER[event_type]
    schema = _schema(event_type)
    missing = frozenset(schema["required"]) - payload.keys()
    others = [
        error.message
        for error in Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(
            payload
        )
        if error.validator != "required"
    ]
    assert missing == pinned, (
        f"{event_type}: the schema requires {sorted(missing)} that the producer does not emit; "
        f"the pin says {sorted(pinned)} (closes in {release}). If the producer now emits them, "
        "this is the closing commit: remove the schema's $comment, delete the pin row and "
        "validate the event with validate_event_schema."
    )
    assert others == [], f"{event_type}: a field the producer does emit is wrong: {others}"


def _write(org_id: uuid.UUID) -> Any:
    return ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG)


async def test_deal_deleted_matches_its_schema(session, org_id, data) -> None:
    async with session.begin():
        await services.delete_deal(session, _write(org_id), data["deal_b"])
    _conforms(
        services.EVT_DEAL_DELETED,
        await _staged(session, services.EVT_DEAL_DELETED, data["deal_b_id"]),
    )


class TestActivityLogged:
    """The one event consumed by another service (customer advances `last_activity_at` from it)
    and the one with no committed schema before R1.2b."""

    async def _log(self, session: Any, org_id: uuid.UUID, **named: Any) -> uuid.UUID:
        async with session.begin():
            activity = await services.log_activity(
                session,
                ctx_for(org_id, "crm.pipeline.activity.write", Scope.ORG),
                activity_type="call",
                subject="Pricing follow-up",
                body="Agreed to a 30-vehicle pilot.",
                **named,
            )
        return activity.id

    async def test_an_entry_naming_only_a_deal_matches_its_schema(
        self, session, org_id, data
    ) -> None:
        activity_id = await self._log(
            session, org_id, owner_id=data["subject_a"], deal_id=data["deal_a_id"]
        )
        payload = await _staged(session, services.EVT_ACTIVITY_LOGGED, activity_id)
        _conforms(services.EVT_ACTIVITY_LOGGED, payload)
        assert payload["deal_id"] == str(data["deal_a_id"])
        assert payload["contact_id"] is None

    async def test_an_entry_naming_only_a_contact_matches_its_schema(
        self, session, org_id, data
    ) -> None:
        activity_id = await self._log(
            session, org_id, owner_id=data["subject_a"], contact_id=data["contact_a"]
        )
        payload = await _staged(session, services.EVT_ACTIVITY_LOGGED, activity_id)
        _conforms(services.EVT_ACTIVITY_LOGGED, payload)
        assert payload["deal_id"] is None
        assert payload["contact_id"] == str(data["contact_a"])

    async def test_an_entry_naming_both_matches_its_schema(self, session, org_id, data) -> None:
        activity_id = await self._log(
            session,
            org_id,
            owner_id=data["subject_a"],
            deal_id=data["deal_a_id"],
            contact_id=data["contact_a"],
        )
        _conforms(
            services.EVT_ACTIVITY_LOGGED,
            await _staged(session, services.EVT_ACTIVITY_LOGGED, activity_id),
        )

    async def test_the_payload_carries_no_subject_or_body(self, session, org_id, data) -> None:
        """Ids and an enumeration only: the schema forbids extra keys, so a subject or a body added
        to the payload fails `_conforms`; this names the rule in the test report as well."""
        activity_id = await self._log(
            session, org_id, owner_id=data["subject_a"], deal_id=data["deal_a_id"]
        )
        payload = await _staged(session, services.EVT_ACTIVITY_LOGGED, activity_id)
        assert set(payload) == {
            "organization_id",
            "activity_id",
            "type",
            "deal_id",
            "contact_id",
            "occurred_at",
        }

    async def test_occurred_at_carries_an_offset(self, session, org_id, data) -> None:
        """Checked by hand, because the validator does not check `format: date-time` here."""
        backdated = datetime(2026, 9, 1, 10, 30, tzinfo=UTC)
        for named in ({}, {"occurred_at": backdated}):
            activity_id = await self._log(
                session, org_id, owner_id=data["subject_a"], deal_id=data["deal_a_id"], **named
            )
            payload = await _staged(session, services.EVT_ACTIVITY_LOGGED, activity_id)
            published = datetime.fromisoformat(payload["occurred_at"])
            assert published.utcoffset() == timedelta(0), payload["occurred_at"]
            if named:
                assert published == backdated


class TestAheadOfProducer:
    async def test_deal_created_lacks_exactly_the_pinned_fields(
        self, session, org_id, data
    ) -> None:
        payload = await _staged(session, services.EVT_DEAL_CREATED, data["deal_a_id"])
        _assert_pinned(services.EVT_DEAL_CREATED, payload)

    async def test_deal_updated_lacks_exactly_the_pinned_fields(
        self, session, org_id, data
    ) -> None:
        async with session.begin():
            await services.update_deal(
                session, _write(org_id), data["deal_a"], DealPatch(name="Fleet telematics v2")
            )
        payload = await _staged(session, services.EVT_DEAL_UPDATED, data["deal_a_id"])
        _assert_pinned(services.EVT_DEAL_UPDATED, payload)

    async def test_deal_stage_changed_lacks_exactly_the_pinned_fields(
        self, session, org_id, data
    ) -> None:
        async with session.begin():
            await services.move_stage(session, _write(org_id), data["deal_a"], new_stage="proposal")
        payload = await _staged(session, services.EVT_DEAL_STAGE_CHANGED, data["deal_a_id"])
        _assert_pinned(services.EVT_DEAL_STAGE_CHANGED, payload)


def test_the_ahead_comment_and_the_pin_table_agree() -> None:
    """A schema carries the "Ahead of the producer" comment exactly when its event is pinned, for
    every event this service declares: a new gap must be pinned, and a closed one must lose both."""
    declared = sorted(
        value
        for name, value in vars(services).items()
        if name.startswith("EVT_") and isinstance(value, str)
    )
    assert declared, "expected app.services to declare its EVT_* constants"
    for event_type in declared:
        ahead = str(_schema(event_type).get("$comment", "")).startswith("Ahead of the producer")
        assert ahead == (event_type in AHEAD_OF_PRODUCER), event_type
```

- [ ] **Step 2: Run it and see it fail**

Run: `cd services/deal && uv run pytest tests/test_event_contracts.py -q -p no:cacheprovider` (needs `make infra` and
`make migrate`)
Expected: FAIL, `4 failed, 6 passed` (inference for the database-backed tests; the agreement test observed). The three
`..._matches_its_schema` tests of `TestActivityLogged` fail with `FileNotFoundError: No committed schema for
crm.pipeline.activity.logged v1 at .../libs/contracts/events/crm.pipeline.activity.logged/v1.schema.json - add one
under libs/contracts/events/.`, and `test_the_ahead_comment_and_the_pin_table_agree` with `FileNotFoundError: [Errno 2]
No such file or directory: '.../crm.pipeline.activity.logged/v1.schema.json'` (observed). The no-subject test and the
offset test read the payload only, so they pass already; the deal-deleted test and the three pin tests pass because
those schemas exist and their gaps are today's.

- [ ] **Step 3: Write the schema**

Create `libs/contracts/events/crm.pipeline.activity.logged/v1.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "opsgrids://contracts/crm.pipeline.activity.logged/v1",
  "title": "crm.pipeline.activity.logged",
  "description": "One interaction was appended to the activity timeline, which is append-only. Ids and an enumeration only: the subject and the body never travel on the bus. `deal_id` and `contact_id` are null when the entry does not name one. `occurred_at` is when the interaction happened (it may be backdated, never in the future), not when it was logged, and always carries a UTC offset. Customer binds this event to advance the named contact's `last_activity_at`.",
  "type": "object",
  "additionalProperties": false,
  "required": ["organization_id", "activity_id", "type", "deal_id", "contact_id", "occurred_at"],
  "properties": {
    "organization_id": { "type": "string", "format": "uuid" },
    "activity_id": { "type": "string", "format": "uuid" },
    "type": { "enum": ["call", "email", "meeting", "note", "task"] },
    "deal_id": { "type": ["string", "null"], "format": "uuid" },
    "contact_id": { "type": ["string", "null"], "format": "uuid" },
    "occurred_at": { "type": "string", "format": "date-time" }
  }
}
```

The `"always carries a UTC offset"` sentence is a promise the producer keeps only after Task R1.2.8 (an `occurred_at`
sent without an offset used to be published without one).

- [ ] **Step 4: Run it and see it pass**

Run: `cd services/deal && uv run pytest tests/test_event_contracts.py -q -p no:cacheprovider`
Expected: `10 passed` (inference for the database-backed tests; the agreement test observed passing).

Run: `uv run pytest libs/contracts -q -p no:cacheprovider`
Expected: all pass.

Run: `ls libs/contracts/events | grep -c '^crm\.'`
Expected: `15` (`AC-1.2.3`).

Run: `cd services/deal && uv run mypy app && cd ../.. && uv run ruff check services/deal && uv run ruff format --check services/deal`
Expected: `Success: no issues found`, `All checks passed!`, `already formatted`.

- [ ] **Step 5: Commit**

```bash
git add libs/contracts/events/crm.pipeline.activity.logged services/deal/tests/test_event_contracts.py
git commit -m "Add the contract for the activity event and test deal's events against their schemas"
```

- [ ] **Step 6: Mutation check (not committed)**

Rename a key in the producer and see the contract catch it, then restore.

Run: `sed -i.bak 's|            "type": activity_type,|            "kind": activity_type,|' services/deal/app/services.py`
Run: `cd services/deal && uv run pytest tests/test_event_contracts.py -q -p no:cacheprovider -k activity`
Expected: FAIL (inference): the three schema tests with `jsonschema.exceptions.ValidationError: Additional properties
are not allowed ('kind' was unexpected)` (or `'type' is a required property`), and the no-subject test with a set
mismatch.
Restore: `git checkout -- services/deal/app/services.py && rm -f services/deal/app/services.py.bak`
Run: `git status --short services/deal` Expected: no output.

---

### Task R1.2.8: An activity timestamp with no offset is read as UTC

**Deploy:** R1.2b. **Spec:** `S§5.3.4` (iv, "found while writing the activity schema"), `R1.2-4`, strike-able (O-15).
**Registry:** `EV5`.

**Files:**
- Modify: `services/deal/app/services.py:376` (`log_activity`)
- Modify: `services/deal/tests/test_write_routes.py:16-28` (imports) and `TestLogActivity` (three new tests before
  `test_a_reader_cannot_log_a_touch`, line 343)
- Test: `services/deal/tests/test_write_routes.py::TestLogActivity::test_a_timestamp_without_an_offset_is_taken_as_utc`,
  `::test_a_timestamp_with_a_non_utc_offset_keeps_its_instant` and
  `::test_a_timestamp_with_no_offset_that_is_ahead_of_utc_is_refused` (the last two are the Review Focus line on
  activity timestamps from an Indian client)

**Interfaces:**
- Consumes: `POST /api/v1/deals/activities` (`ActivityIn.occurred_at: datetime | None`), whose
  `_not_in_the_future` validator already reads an offset-less value as UTC
  (`services/deal/app/schemas.py`, `ActivityIn._not_in_the_future`) but leaves the field naive; the fixtures
  `client`, `session`, `org_id`, `data`; `app.models.Activity`, `OutboxEvent`; `tests.helpers.principal_for`.
- Produces: `log_activity` accepts a naive `occurred_at` and treats it as UTC: the stored `occurred_at`, the deal's
  `last_activity_at` comparison and the published `occurred_at` all carry the UTC offset. No signature change.

A client may send `"2026-09-02T09:15:00"` with no offset. The request model accepts it; the service then reaches
`at > deal.last_activity_at` (`services/deal/app/services.py:401`) with a naive value against an aware column
value, which raises `TypeError: can't compare offset-naive and offset-aware datetimes` (an HTTP 500) whenever the
deal already has an activity, and a payload written without an offset would break the schema Task R1.2.7 commits.
The fix is local and keeps the validator's own convention: a naive instant is UTC.

- [ ] **Step 1: Write the failing test**

In `services/deal/tests/test_write_routes.py` make three edits.

1. Add the datetime import after `from collections.abc import Iterator` (line 18):

```python
from datetime import UTC, datetime, timedelta
```

2. Replace `from app.models import AuditLog, Deal, OutboxEvent` (line 26) with:

```python
from app.models import Activity, AuditLog, Deal, OutboxEvent
```

3. Add this test to `TestLogActivity`, immediately before `test_a_reader_cannot_log_a_touch`:

```python
    async def test_a_timestamp_without_an_offset_is_taken_as_utc(
        self, client, session, org_id, data
    ) -> None:
        """A client may send `occurred_at` with no offset. It is read as UTC, the way the request
        model's own future check already reads it, instead of being compared with an aware
        timestamp, and the event carries the offset its schema promises."""
        principal = principal_for(
            org_id, ["crm.pipeline.activity.write:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/deals/activities",
                json={
                    "deal_id": str(data["deal_a_id"]),
                    "type": "note",
                    "subject": "Naive stamp",
                    "occurred_at": "2026-09-02T09:15:00",
                },
            )
        assert response.status_code == 201, response.text
        activity_id = uuid.UUID(response.json()["id"])

        expected = datetime(2026, 9, 2, 9, 15, tzinfo=UTC)
        async with session.begin():
            stored = (
                await session.execute(
                    select(Activity.occurred_at).where(Activity.id == activity_id)
                )
            ).scalar_one()
            published = (
                await session.execute(
                    select(OutboxEvent.payload).where(
                        OutboxEvent.event_type == "crm.pipeline.activity.logged",
                        OutboxEvent.aggregate_id == activity_id,
                    )
                )
            ).scalar_one()
        assert stored == expected
        stamp = datetime.fromisoformat(published["occurred_at"])
        assert stamp.utcoffset() == timedelta(0)
        assert stamp == expected

    async def test_a_timestamp_with_a_non_utc_offset_keeps_its_instant(
        self, client, session, org_id, data
    ) -> None:
        """A client in India sends `+05:30`. The instant is the same one in UTC: stored as that
        instant, published as that instant (the offset it was sent with is kept, and it is still
        an offset) and the deal's staleness clock moves to it. Nothing is shifted by five and a
        half hours."""
        principal = principal_for(
            org_id, ["crm.pipeline.activity.write:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/deals/activities",
                json={
                    "deal_id": str(data["deal_a_id"]),
                    "type": "note",
                    "subject": "Kolkata stamp",
                    "occurred_at": "2026-09-02T14:45:00+05:30",
                },
            )
        assert response.status_code == 201, response.text
        activity_id = uuid.UUID(response.json()["id"])

        expected = datetime(2026, 9, 2, 9, 15, tzinfo=UTC)
        async with session.begin():
            stored = (
                await session.execute(
                    select(Activity.occurred_at).where(Activity.id == activity_id)
                )
            ).scalar_one()
            published = (
                await session.execute(
                    select(OutboxEvent.payload).where(
                        OutboxEvent.event_type == "crm.pipeline.activity.logged",
                        OutboxEvent.aggregate_id == activity_id,
                    )
                )
            ).scalar_one()
        assert stored == expected
        assert datetime.fromisoformat(published["occurred_at"]) == expected
        assert (await _reload(session, org_id, data["deal_a_id"])).last_activity_at == expected

    async def test_a_timestamp_with_no_offset_that_is_ahead_of_utc_is_refused(
        self, client, session, org_id, data
    ) -> None:
        """No offset means UTC: the request model's reading, and the service's since this task. A
        client in India that sends its own wall clock for "now" is up to five and a half hours
        ahead of UTC, so it is refused as a time that has not happened and nothing is stored,
        instead of being kept as a time that is wrong by the offset."""
        ahead = datetime.now(UTC) + timedelta(hours=5, minutes=30)
        principal = principal_for(
            org_id, ["crm.pipeline.activity.write:ORG"], subject_ref=data["subject_a"]
        )
        async with session.begin():
            before = (
                await session.execute(select(func.count()).select_from(Activity))
            ).scalar_one()
        with as_principal(principal):
            response = await client.post(
                "/api/v1/deals/activities",
                json={
                    "deal_id": str(data["deal_a_id"]),
                    "type": "note",
                    "subject": "Wall clock in Kolkata",
                    "occurred_at": ahead.replace(tzinfo=None).isoformat(timespec="seconds"),
                },
            )
        assert response.status_code == 422, response.text
        async with session.begin():
            after = (await session.execute(select(func.count()).select_from(Activity))).scalar_one()
        assert after == before
```

(The deal fixture already logs one activity on `deal_a`, so `deal_a.last_activity_at` is set and the comparison is
reached. The second and third tests are characterizations of what HEAD already does with an offset and with a
future stamp: they pass before the fix and must still pass after it. They exist because the fix is a convention
about what a missing offset means, and the two inputs beside it are the ones an Indian client sends. Observed
against HEAD without a database: the request model keeps `2026-09-02T14:45:00+05:30` as the instant
`2026-09-02T09:15:00Z`, refuses a naive stamp five and a half hours ahead of UTC with `occurred_at cannot be in
the future`, and accepts a naive stamp in the past and leaves it naive.)

- [ ] **Step 2: Run it and see it fail**

Run: `cd services/deal && uv run pytest tests/test_write_routes.py -q -p no:cacheprovider -k offset` (needs `make infra`
and `make migrate`)
Expected: FAIL (inference): `TypeError: can't compare offset-naive and offset-aware datetimes`, raised at
`services/deal/app/services.py:401` through the ASGI transport (or, if the transport turns it into a response,
`assert 500 == 201`). The other tests of the module still pass, and so do the two characterizations the `-k offset`
selection also picks up (`test_a_timestamp_with_a_non_utc_offset_keeps_its_instant` and
`test_a_timestamp_with_no_offset_that_is_ahead_of_utc_is_refused`): an aware stamp never reached the comparison with a
naive value, and the request model already refuses a naive stamp that is ahead of UTC.

If `test_a_timestamp_without_an_offset_is_taken_as_utc` already passes on the base commit, the diagnosis above is wrong
(`S§5.3.4`): drop this task's code change, keep the test as a plain characterization, and keep the offset assertion of
`TestActivityLogged::test_occurred_at_carries_an_offset` in Task R1.2.7. The owner may also strike the whole task (O-15).

- [ ] **Step 3: Write the minimal implementation**

In `services/deal/app/services.py`, `log_activity`, replace line 376

```python
    at = occurred_at or datetime.now(UTC)
```

with

```python
    at = occurred_at or datetime.now(UTC)
    if at.tzinfo is None:
        # `ActivityIn` already reads an offset-less value as UTC for its future check
        # (`schemas.py`). Keep reading it that way here: a naive instant cannot be compared
        # with the deal's aware `last_activity_at` below, and it would reach the event with no
        # offset, where a consumer comparing it with its own aware clock would fail.
        at = at.replace(tzinfo=UTC)
```

- [ ] **Step 4: Run it and see it pass**

Run: `cd services/deal && uv run pytest tests/test_write_routes.py tests/test_event_contracts.py tests/test_activity_append_only.py -q -p no:cacheprovider`
Expected: all pass (inference; the three new tests and the existing activity tests).

Run: `cd services/deal && uv run mypy app && cd ../.. && uv run ruff check services/deal && uv run ruff format --check services/deal`
Expected: `Success: no issues found`, `All checks passed!`, `already formatted` (observed on a scratch copy of the module
and of the app; the two added tests were checked with `ruff format` and `ruff check` under the repository's
configuration).

- [ ] **Step 5: Commit**

```bash
git add services/deal/app/services.py services/deal/tests/test_write_routes.py
git commit -m "Deal: read an activity timestamp with no offset as UTC"
```

---

### Task R1.2.9: Lead's three ahead-of-producer pins

**Deploy:** R1.2b. **Spec:** `S§5.3.4` (ii) to (iv), `AC-1.2.4`. **Registry:** `EV10`, `EV11`, `EV12` (the three lead
gaps R3.7 closes).

**Files:**
- Create: `services/lead/tests/test_event_contracts.py`
- Test: `services/lead/tests/test_event_contracts.py`

**Interfaces:**
- Consumes: lead's `app.services` constants `EVT_LEAD_CREATED`, `EVT_LEAD_UPDATED`, `EVT_LEAD_CONVERTED`
  (`services/lead/app/services.py:71-77`); `update_lead(session, ctx, lead_id, payload: LeadPatch, *, request=None)`
  (`:549`), `convert_lead(session, ctx, lead_id, *, request=None)` (`:1282`); `app.clients.ensure_company_and_contact`
  and `ensure_deal` (monkeypatched: conversion calls customer and deal over HTTP, which this test does not need);
  `app.schemas.LeadPatch`; the fixtures `session`, `org_id`, `data` (`lead_a_id`, a lead the fixture qualified, and
  `lead_b_id`, a lead that is still `new` with no event but its creation; `services/lead/tests/conftest.py:127-161`);
  `tests.helpers.ctx_for`; `platform_core.events.event_schema_path`.
- Produces: `AHEAD_OF_PRODUCER` in lead's test module: `crm.lead.created` lacks `{status}`, `crm.lead.updated` lacks
  `{status, source, owner_id, converted_at}`, `crm.lead.converted` lacks `{owner_id, converted_at}`, each closing in
  `R3.7`; the same `$comment`/pin agreement test as customer's and deal's.

The three schemas already exist and are already ahead of the producer; this task adds no schema. The fourth lead
event, `crm.lead.conversion_aborted`, conforms and is tested against its producer
(`services/lead/tests/test_lead_lifecycle_races.py:269,292`): it is cited here, not copied. This is a
**characterization test**: it passes against HEAD, because it records today's gaps, so the proof that it can fail is
the mutation in Step 3 (a producer that closes a gap must trip the pin). The `lead.outbox` event for `lead_a_id` is
read once (`crm.lead.created`, `crm.lead.converted`); `crm.lead.updated` is read on `lead_b_id` because the fixture
updates `lead_a_id` twice to qualify it.

- [ ] **Step 1: Write the test**

Create `services/lead/tests/test_event_contracts.py`:

```python
"""The three `crm.lead.*` events committed ahead of their producer, pinned.

`crm.lead.created`, `.updated` and `.converted` have schemas that require fields `lead-service`
does not emit yet (each schema's `$comment` says which). This file pins exactly what is missing,
built through the real functions, so the gap cannot drift silently and the commit that closes it
(the lead-capture work, R3.7) cannot forget its test: that commit changes the producer, removes
the schema's `$comment`, deletes the row below, deletes the event's name from
`PINNED_AHEAD_OF_PRODUCER` in `libs/contracts/tests/test_event_schemas.py`, and validates the
event with `validate_event_schema`.

The fourth event, `crm.lead.conversion_aborted`, conforms and is already tested against its
producer: `test_lead_lifecycle_races.py` (the two `validate_event_schema` calls at `:269` and
`:292`). It is cited here, not copied.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import select

from app import clients, services
from app.models import OutboxEvent
from app.schemas import LeadPatch
from platform_core.auth import Scope
from platform_core.events import event_schema_path
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration

#: `test_event_contracts.py`, `tests/`, `lead`, `services`, ROOT.
CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"

#: Event type -> (required properties the producer does not emit yet, the release that closes it).
AHEAD_OF_PRODUCER: dict[str, tuple[frozenset[str], str]] = {
    services.EVT_LEAD_CREATED: (frozenset({"status"}), "R3.7"),
    services.EVT_LEAD_UPDATED: (
        frozenset({"status", "source", "owner_id", "converted_at"}),
        "R3.7",
    ),
    services.EVT_LEAD_CONVERTED: (frozenset({"owner_id", "converted_at"}), "R3.7"),
}


def _schema(event_type: str) -> dict[str, Any]:
    path = event_schema_path(event_type, 1, contracts_root=CONTRACTS_ROOT)
    return dict(json.loads(path.read_text(encoding="utf-8")))


async def _staged(session: Any, event_type: str, aggregate_id: uuid.UUID) -> dict[str, Any]:
    """The payload of the one `event_type` event staged for this lead, read back the way the relay
    would read it."""
    async with session.begin():
        rows = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == event_type,
                        OutboxEvent.aggregate_id == aggregate_id,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1, f"expected one {event_type} for {aggregate_id}, found {len(rows)}"
    return dict(rows[0].payload)


def _assert_pinned(event_type: str, payload: dict[str, Any]) -> None:
    """The schema requires exactly the pinned fields the payload lacks, and nothing a field that
    *is* emitted breaks (a type or enum error is a different defect from a missing field)."""
    pinned, release = AHEAD_OF_PRODUCER[event_type]
    schema = _schema(event_type)
    missing = frozenset(schema["required"]) - payload.keys()
    others = [
        error.message
        for error in Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(
            payload
        )
        if error.validator != "required"
    ]
    assert missing == pinned, (
        f"{event_type}: the schema requires {sorted(missing)} that the producer does not emit; "
        f"the pin says {sorted(pinned)} (closes in {release}). If the producer now emits them, "
        "this is the closing commit: remove the schema's $comment, delete the pin row and "
        "validate the event with validate_event_schema."
    )
    assert others == [], f"{event_type}: a field the producer does emit is wrong: {others}"


class TestAheadOfProducer:
    async def test_lead_created_lacks_exactly_the_pinned_fields(
        self, session, org_id, data
    ) -> None:
        payload = await _staged(session, services.EVT_LEAD_CREATED, data["lead_a_id"])
        _assert_pinned(services.EVT_LEAD_CREATED, payload)

    async def test_lead_updated_lacks_exactly_the_pinned_fields(
        self, session, org_id, data
    ) -> None:
        async with session.begin():
            await services.update_lead(
                session,
                ctx_for(org_id, "crm.lead.lead.write", Scope.ORG),
                data["lead_b_id"],
                LeadPatch(notes="Called back, asked for a quote."),
            )
        payload = await _staged(session, services.EVT_LEAD_UPDATED, data["lead_b_id"])
        _assert_pinned(services.EVT_LEAD_UPDATED, payload)

    async def test_lead_converted_lacks_exactly_the_pinned_fields(
        self, session, org_id, data, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _company_and_contact(
            settings: Any, organization_id: Any, **kwargs: Any
        ) -> tuple[uuid.UUID, uuid.UUID]:
            return uuid.uuid4(), uuid.uuid4()

        async def _deal(settings: Any, organization_id: Any, **kwargs: Any) -> uuid.UUID:
            return uuid.uuid4()

        monkeypatch.setattr(clients, "ensure_company_and_contact", _company_and_contact)
        monkeypatch.setattr(clients, "ensure_deal", _deal)
        await services.convert_lead(
            session, ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG), data["lead_a_id"]
        )
        payload = await _staged(session, services.EVT_LEAD_CONVERTED, data["lead_a_id"])
        _assert_pinned(services.EVT_LEAD_CONVERTED, payload)


def test_the_ahead_comment_and_the_pin_table_agree() -> None:
    """A schema carries the "Ahead of the producer" comment exactly when its event is pinned, for
    every event this service declares: a new gap must be pinned, and a closed one must lose both."""
    declared = sorted(
        value
        for name, value in vars(services).items()
        if name.startswith("EVT_") and isinstance(value, str)
    )
    assert declared, "expected app.services to declare its EVT_* constants"
    for event_type in declared:
        ahead = str(_schema(event_type).get("$comment", "")).startswith("Ahead of the producer")
        assert ahead == (event_type in AHEAD_OF_PRODUCER), event_type
```

- [ ] **Step 2: Run it and see it pass (characterization)**

Run: `cd services/lead && uv run pytest tests/test_event_contracts.py -q -p no:cacheprovider` (needs `make infra` and
`make migrate`)
Expected: `4 passed` (inference for the three database-backed tests; the agreement test was observed passing against
HEAD).

- [ ] **Step 3: Prove the pin trips when a gap closes (mutation, not committed)**

In `services/lead/app/services.py`, in the `crm.lead.created` payload (the dict passed to `publish_event` with
`event_type=EVT_LEAD_CREATED`, lines 538-543), add one line after `"source": source,`:

```python
            "status": "new",
```

Run: `cd services/lead && uv run pytest tests/test_event_contracts.py -q -p no:cacheprovider -k lead_created`
Expected: FAIL (inference): `AssertionError: crm.lead.created: the schema requires [] that the producer does not
emit; the pin says ['status'] (closes in R3.7). If the producer now emits them, this is the closing commit: remove the
schema's $comment, delete the pin row and validate the event with validate_event_schema.`
Restore: `git checkout -- services/lead/app/services.py`
Run: `git status --short services/lead` Expected: only `?? services/lead/tests/test_event_contracts.py`.

- [ ] **Step 4: Lint and typecheck**

Run: `cd services/lead && uv run mypy app && cd ../.. && uv run ruff check services/lead && uv run ruff format --check services/lead`
Expected: `Success: no issues found`, `All checks passed!`, `already formatted` (observed on the scratch copy of the module).

- [ ] **Step 5: Commit**

```bash
git add services/lead/tests/test_event_contracts.py
git commit -m "Lead: pin the three event schemas that are ahead of their producer"
```

---

### Task R1.2.10: Static CRM contract coverage, and the phantom event names

**Deploy:** R1.2b. **Spec:** `S§5.3.4` (iv), `R1.2-4`, `R1.2-8`, `AC-1.2.3`. **Registry:** `EV4`, `EV5`.

**Files:**
- Modify: `libs/contracts/tests/test_event_schemas.py:1-13` (module docstring), `:17-18` (imports), `:31` (constants),
  `:94-97` (one stale docstring), and the end of the file (four new tests)
- Modify: `services/notification/tests/test_m1_analytics.py:258,266,274` (three invented event names)
- Test: `libs/contracts/tests/test_event_schemas.py`

**Interfaces:**
- Consumes: the module's `_schema_files()`, `EVENTS`, `NOTIFICATION`, `LEAD`, `ROOT`; the 15 committed `crm.*`
  schemas (Tasks R1.2.6 and R1.2.7 add the last four); `app.domain.SUBSCRIBABLE_EVENTS` of notification; the `EVT_*`
  string constants of `app.services` in customer, deal and lead, each read from a fresh interpreter in that service's
  directory (`import app` is ambiguous across services).
- Produces: `CUSTOMER`, `DEAL` (`pathlib.Path`), `_declared_crm_events(service: Path) -> list[str]`,
  `_notification_subscribable_events() -> list[str]`, `PINNED_AHEAD_OF_PRODUCER: frozenset[str]` (the eight names) and
  the four tests `test_every_crm_event_a_service_declares_has_a_committed_schema`,
  `test_every_crm_subscribable_key_has_a_committed_schema`,
  `test_the_ahead_of_producer_schemas_are_exactly_the_pinned_eight` and
  `test_no_service_test_names_a_crm_event_that_has_no_committed_schema`. Together with the per-service pin tables of
  Tasks R1.2.6, R1.2.7 and R1.2.9 they make the three-way agreement: a schema is "ahead" exactly when its `$comment`
  says so, its service pins it, and this set names it.

The static test is what stops a sixteenth CRM event being published without a contract, and what keeps an
invented event out of a fixture: `services/notification/tests/test_m1_analytics.py` seeds rules on `crm.deal.won`
and `crm.deal.lost`, which nothing publishes (the real keys are `crm.pipeline.deal.*`), and a rule seeded on a key
that cannot fire documents an event that does not exist.

- [ ] **Step 1: Write the failing tests**

In `libs/contracts/tests/test_event_schemas.py` make five edits.

1. In the module docstring replace

```python
`platform_core.events.build_envelope`'s `schema_uri` expects, and covers every `engage.*` event
notification actually declares.
```

with

```python
`platform_core.events.build_envelope`'s `schema_uri` expects, and covers every `engage.*` event
notification declares and every `crm.*` event customer, deal and lead declare.
```

and, a few lines below, replace

```python
`services/notification/tests/test_event_contracts.py`, part of the ordinary sharded backend
test run. This file is what catches a schema that is not even valid JSON Schema, or an event
nobody wrote one for at all, before that test ever gets to run.
```

with

```python
`services/<service>/tests/test_event_contracts.py` (notification, customer, deal and lead), part
of the ordinary sharded backend test run. This file is what catches a schema that is not even
valid JSON Schema, or an event nobody wrote one for at all, before that test ever gets to run.
```

2. Replace `import json` / `import subprocess` (lines 17-18) with:

```python
import json
import re
import subprocess
```

3. After `LEAD = ROOT / "services" / "lead"` (line 31) add:

```python
CUSTOMER = ROOT / "services" / "customer"
DEAL = ROOT / "services" / "deal"
```

4. In `test_the_lead_conversion_abort_event_has_a_committed_schema` replace the docstring (lines 95-97)

```python
    """The one `crm.lead.*` event with a committed contract so far — it is the hook a later
    consumer would clean up orphaned records from, so its shape is a promise before anything
    reads it. (`crm.lead.created`/`updated`/`converted` predate this and have none yet.)"""
```

with

```python
    """The `crm.lead.*` event a later consumer would clean up orphaned records from, so its
    shape is a promise before anything reads it. Its siblings (`created`, `updated`,
    `converted`) have committed schemas too, ahead of their producer: the pinned set further
    down holds that list."""
```

5. Append to the end of the file:

```python


def _declared_crm_events(service: Path) -> list[str]:
    """Every `crm.*` event type a CRM service declares as an `EVT_*` constant in `app.services`,
    read from a fresh interpreter with the service directory as cwd — the trick
    `_notification_engage_event_types` uses, for the same reason."""
    script = (
        "import app.services as s\n"
        "print('\\n'.join(sorted({\n"
        "    v for k, v in vars(s).items()\n"
        "    if k.startswith('EVT_') and isinstance(v, str) and v.startswith('crm.')\n"
        "})))\n"
    )
    result = subprocess.run(  # noqa: S603 - this interpreter, a literal script, a path we chose
        [sys.executable, "-c", script],
        cwd=service,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _notification_subscribable_events() -> list[str]:
    """Notification's `SUBSCRIBABLE_EVENTS`, read from a fresh interpreter in its own directory."""
    script = (
        "import json\n"
        "from app.domain import SUBSCRIBABLE_EVENTS\n"
        "print(json.dumps(list(SUBSCRIBABLE_EVENTS)))\n"
    )
    result = subprocess.run(  # noqa: S603 - this interpreter, a literal script, a path we chose
        [sys.executable, "-c", script],
        cwd=NOTIFICATION,
        check=True,
        capture_output=True,
        text=True,
    )
    return [str(key) for key in json.loads(result.stdout)]


def test_every_crm_event_a_service_declares_has_a_committed_schema() -> None:
    """A sixteenth CRM event cannot be published without its contract: customer, deal and lead
    each declare their event types as `EVT_*` constants, and every one needs a schema."""
    committed = {path.parent.name for path in _schema_files()}
    missing: dict[str, list[str]] = {}
    for service in (CUSTOMER, DEAL, LEAD):
        declared = _declared_crm_events(service)
        assert declared, f"expected services/{service.name}/app/services.py to declare crm.* EVT_*"
        gap = sorted(set(declared) - committed)
        if gap:
            missing[service.name] = gap
    assert not missing, (
        f"no committed schema for {missing} — add "
        "libs/contracts/events/<event_type>/v1.schema.json in the same commit as the producer"
    )


def test_every_crm_subscribable_key_has_a_committed_schema() -> None:
    """A tenant may author a rule or an automation on any key notification lists, and a
    checkout's builder offers a trigger's variables from the committed schema: a key with none
    offers nothing."""
    committed = {path.parent.name for path in _schema_files()}
    keys = [key for key in _notification_subscribable_events() if key.startswith("crm.")]
    assert keys, "expected notification's SUBSCRIBABLE_EVENTS to list crm.* keys"
    missing = sorted(set(keys) - committed)
    assert not missing, (
        f"no committed schema for the subscribable keys {missing} — add "
        "libs/contracts/events/<event_type>/v1.schema.json"
    )


#: The `crm.*` schemas committed ahead of their producer (their `$comment` says which fields the
#: producer does not emit yet), each with the release that closes the gap. A ninth cannot appear,
#: and a closed one leaves this set in the commit that closes it — the same commit deletes its row
#: from the service's own `AHEAD_OF_PRODUCER` pin (`services/<service>/tests/
#: test_event_contracts.py`) and removes the schema's `$comment`. Once the set is empty the test
#: below asserts it is empty.
PINNED_AHEAD_OF_PRODUCER: frozenset[str] = frozenset(
    {
        "crm.book.company.created",  # R4.1
        "crm.book.company.updated",  # R4.1
        "crm.lead.created",  # R3.7
        "crm.lead.updated",  # R3.7
        "crm.lead.converted",  # R3.7
        "crm.pipeline.deal.created",  # R2.3
        "crm.pipeline.deal.updated",  # R2.3
        "crm.pipeline.deal.stage_changed",  # R2.3
    }
)


def test_the_ahead_of_producer_schemas_are_exactly_the_pinned_eight() -> None:
    ahead = {
        path.parent.name
        for path in _schema_files()
        if path.parent.name.startswith("crm.")
        and str(json.loads(path.read_text(encoding="utf-8")).get("$comment", "")).startswith(
            "Ahead of the producer"
        )
    }
    assert ahead == PINNED_AHEAD_OF_PRODUCER, (
        f"ahead of their producer but not pinned: {sorted(ahead - PINNED_AHEAD_OF_PRODUCER)}; "
        f"pinned but no longer ahead: {sorted(PINNED_AHEAD_OF_PRODUCER - ahead)}. A gap is closed "
        "by changing the producer, removing the schema's $comment, deleting its row from the "
        "service's AHEAD_OF_PRODUCER and its name above, in one commit."
    )


_EVENT_TYPE_KWARG = re.compile(r"""event_type\s*=\s*["'](crm\.[A-Za-z0-9_.]+)["']""")


def test_no_service_test_names_a_crm_event_that_has_no_committed_schema() -> None:
    """A fixture that invents `crm.deal.won` documents an event nobody publishes, and a rule
    seeded on it proves nothing. Every `event_type="crm...."` a service test writes must be real."""
    committed = {path.parent.name for path in _schema_files()}
    phantom: dict[str, list[str]] = {}
    for path in sorted((ROOT / "services").glob("*/tests/**/*.py")):
        for event_type in _EVENT_TYPE_KWARG.findall(path.read_text(encoding="utf-8")):
            if event_type not in committed:
                phantom.setdefault(event_type, []).append(str(path.relative_to(ROOT)))
    assert not phantom, f"service tests name crm events that no producer publishes: {phantom}"
```

- [ ] **Step 2: Run them and see the failure**

Run: `uv run pytest libs/contracts -q -p no:cacheprovider`
Expected: FAIL, `1 failed, 7 passed` (observed against a tree holding the four schemas of Tasks R1.2.6 and R1.2.7):
`test_no_service_test_names_a_crm_event_that_has_no_committed_schema` fails with `service tests name crm events that no
producer publishes: {'crm.deal.won': ['services/notification/tests/test_m1_analytics.py',
'services/notification/tests/test_m1_analytics.py'], 'crm.deal.lost': ['services/notification/tests/test_m1_analytics.py']}`.
The two coverage tests and the pinned-eight test pass, as they should once the schemas exist; Step 5 proves they can
fail. (Run against the base commit, before Tasks R1.2.6 and R1.2.7, the module fails three times: the declared-events
test names the four missing schemas, the subscribable-keys test names `crm.book.contact.created` and
`crm.pipeline.activity.logged`, and the phantom test fails as above; observed.)

- [ ] **Step 3: Write the minimal implementation**

In `services/notification/tests/test_m1_analytics.py` give the three seeded rules real event types. The rules only
need distinct, plausible keys; nothing else in the test reads the event type.

1. Line 258, rule `wa_active`: replace `event_type="crm.deal.won",` with `event_type="crm.pipeline.deal.stage_changed",`.
2. Line 266, rule `wa_inactive`: replace `event_type="crm.deal.lost",` with `event_type="crm.pipeline.deal.created",`.
3. Line 274, rule `mail_active`: replace `event_type="crm.deal.won",` with `event_type="crm.pipeline.deal.stage_changed",`.

(The first and third rules differ by channel, the second by key and `active`, exactly as before.)

- [ ] **Step 4: Run them and see them pass**

Run: `uv run pytest libs/contracts -q -p no:cacheprovider`
Expected: `8 passed` (observed against the four schemas and the three edited names).

Run: `cd services/notification && uv run pytest tests/test_m1_analytics.py -q -p no:cacheprovider` (needs `make infra`
and `make migrate`)
Expected: the same results as before the edit (inference): the test counts rules by channel and activity, never by key.

Run: `uv run ruff check libs/contracts services/notification && uv run ruff format --check libs/contracts services/notification`
Expected: `All checks passed!` and `already formatted` (observed for the module as edited here).

- [ ] **Step 5: Prove each guard can fail (mutations, not committed)**

1. Hide a schema: `mv libs/contracts/events/crm.pipeline.activity.logged /tmp/hidden-activity-schema`, then
   `uv run pytest libs/contracts -q -p no:cacheprovider`.
   Expected: FAIL twice (observed): the declared-events test names `{'deal': ['crm.pipeline.activity.logged']}` and the
   subscribable-keys test names `['crm.pipeline.activity.logged']`.
   Restore: `mv /tmp/hidden-activity-schema libs/contracts/events/crm.pipeline.activity.logged`.
2. Close a gap without telling the pins: `sed -i.bak 's|"\$comment": "Ahead of the producer|"\$comment": "Level with the producer|' libs/contracts/events/crm.lead.created/v1.schema.json`, then
   `uv run pytest libs/contracts -q -p no:cacheprovider`.
   Expected: FAIL (inference): `pinned but no longer ahead: ['crm.lead.created']`, and in
   `cd services/lead && uv run pytest tests/test_event_contracts.py -q -p no:cacheprovider -k agree` the lead agreement
   test fails too.
   Restore: `git checkout -- libs/contracts/events/crm.lead.created/v1.schema.json && rm -f libs/contracts/events/crm.lead.created/v1.schema.json.bak`.
Run: `git status --short libs/contracts` Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add libs/contracts/tests/test_event_schemas.py services/notification/tests/test_m1_analytics.py
git commit -m "Contracts: require a schema for every CRM event and pin the eight that are ahead of their producer"
```

---

### Task R1.2.11: Four lead keys are subscribable

**Deploy:** R1.2b. **Spec:** `S§5.3.5`, `AC-1.2.5`. **Registry:** `EV39`.

**Files:**
- Modify: `services/notification/app/domain.py:146-151` (the `crm` group of `SUBSCRIBABLE_EVENTS`)
- Create: `services/notification/tests/test_crm_lead_keys.py`
- Create: `tests/infra/test_engage_recipe_triggers.py`
- Test: those two files

**Interfaces:**
- Consumes: `domain.SUBSCRIBABLE_EVENTS: Final[tuple[str, ...]]`, which `consumers.py:324-325` binds key by key on the
  `notification.events` queue and `domain.validate_rule` / `a1_pure.validate_graph` check at authoring
  (`services/notification/app/domain.py:365`, `services/notification/app/a1_services.py:122`);
  `consumers.fold(envelope: dict[str, Any], resolve_target: TargetResolver | None = None)` (the per-message
  transaction the real consumer runs); `services.upsert_provider_template`, `services.upsert_rule`,
  `services.apply_rules`; `a1_services.create_automation`, `publish`; `consent.append`, `consent.subject_for`,
  `consent.MARKETING`, `consent.OPTED_IN`; the committed schemas of the four lead events (they exist: three ahead of
  their producer, `conversion_aborted` conformant); the fixtures `session`, `admin_session`, `client`, `org_id` and the
  helpers `ctx_for`, `envelope_for`, `principal_for` of `services/notification/tests`.
- Produces: the four keys `crm.lead.conversion_aborted`, `crm.lead.converted`, `crm.lead.created`,
  `crm.lead.updated` in `SUBSCRIBABLE_EVENTS`, alphabetical between `crm.book.contact.created` and
  `crm.pipeline.activity.logged` (the CRM group is then nine keys); `GET /api/v1/notifications/subscribable-events`
  lists them. Nothing else in the service changes: the consumer already folds any bound key.

Every `crm.lead.*` event was published to a broker with no queue bound for it and dropped, and a tenant could not
author a rule on one (`domain.validate_rule` refuses a key outside the list). The effect of this change on a tenant
that authored nothing is only that each lead event is now recorded as processed (a `processed_events` row) and does
nothing else; the tests below pin that, and the redelivery, tenant and consent behaviour of the first thing a tenant
will build on it. No lead event carries an address: the one contact id on any of them is `converted`'s, so a rule
reaches the contact through `recipient_contact_path` and an automation through `recipient_kind: "contact_id"`
(Task R1.2.12). Events published before the deploy were dropped and are **not** replayed.

- [ ] **Step 1: Write the failing tests**

Create `services/notification/tests/test_crm_lead_keys.py`:

```python
"""The four `crm.lead.*` routing keys a rule or an automation may now react to (registry EV39).

Until R1.2b notification subscribed to five CRM keys and no lead key, so every `crm.lead.*` event
was published to a broker with no queue bound for it and silently dropped, and a tenant could not
even author a rule on one: `domain.validate_rule` and the automation builder refuse a key that is
not in `SUBSCRIBABLE_EVENTS`. These tests prove the four keys are listed, have contracts, and (end
to end through the real consumer) that a rule or an automation on `crm.lead.converted` messages
the converted contact, once, in its own tenant, and only when consent allows it, and that a contact
with no number (the common case) is a skipped rule or a visible `recipient_missing` failure and
never a message.

What no lead event carries is an address: the only contact id on one is `converted`'s (a contact
that exists), so the contact is resolved through customer's messaging target
(`recipient_contact_path` for a rule, `recipient_kind: "contact_id"` for an automation).

Real PostgreSQL as the app role, like the rest of this service's suite.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app import a1_services, consent, consumers, domain, services
from app.a1_models import AutomationRun, AutomationRunStep
from app.config import settings
from app.main import app
from app.models import SCHEMA, Message, OutboxEvent, ProcessedEvent
from platform_core.auth import Principal, Scope, ScopeContext, get_principal
from platform_core.db import dispose_engine, init_engine
from platform_core.events import event_schema_path
from tests.helpers import ctx_for, envelope_for, principal_for

pytestmark = pytest.mark.integration

#: `test_crm_lead_keys.py`, `tests/`, `notification`, `services`, ROOT.
CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"

LEAD_KEYS = (
    "crm.lead.conversion_aborted",
    "crm.lead.converted",
    "crm.lead.created",
    "crm.lead.updated",
)
CRM_KEYS = (
    "crm.book.company.created",
    "crm.book.contact.created",
    *LEAD_KEYS,
    "crm.pipeline.activity.logged",
    "crm.pipeline.deal.created",
    "crm.pipeline.deal.stage_changed",
)
CONVERTED = "crm.lead.converted"
CONTACT_ID = "c0ffee00-0000-4000-8000-000000000001"
PHONE = "+917976603446"

T_READ = "notification.delivery.template.read"
T_WRITE = "notification.delivery.template.write"
WRITE = "engage.campaign.campaign.write"
SEND = "engage.campaign.campaign.send"
A1_TABLES = ("a1_run_steps", "a1_runs", "a1_versions", "a1_automations")


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


@pytest_asyncio.fixture(autouse=True)
async def _clean(session: Any) -> AsyncIterator[None]:
    """The shared fixture truncates the older tables; the automation engine's are ours."""
    admin = create_async_engine(
        settings().admin_dsn, connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}}
    )
    async with admin.begin() as conn:
        tables = ", ".join(f'"{SCHEMA}"."{t}"' for t in A1_TABLES)
        await conn.execute(text(f"TRUNCATE {tables}"))
    await admin.dispose()
    yield


@pytest_asyncio.fixture
async def worker_engine(session: Any) -> AsyncIterator[None]:
    """The process-wide engine `consumers.fold` opens its per-message transaction on."""
    init_engine(settings())
    yield
    await dispose_engine()


def _converted(org_id: uuid.UUID, contact_id: str | None = CONTACT_ID) -> dict[str, Any]:
    """`crm.lead.converted` as lead-service publishes it today: ids only, no address."""
    return envelope_for(
        org_id,
        CONVERTED,
        {
            "organization_id": str(org_id),
            "lead_id": str(uuid.uuid4()),
            "company_id": str(uuid.uuid4()),
            "contact_id": contact_id,
            "deal_id": str(uuid.uuid4()),
        },
    )


async def _resolver(org: str, contact: str, channel: str) -> str | None:
    """Customer's messaging target, standing in: the contact in the event has this number."""
    assert contact == CONTACT_ID and channel == "whatsapp"
    return PHONE


async def _refusing_resolver(org: str, contact: str, channel: str) -> str | None:
    raise AssertionError("another tenant's event must never ask for a contact's address")


async def _no_number_resolver(org: str, contact: str, channel: str) -> str | None:
    """Customer's messaging target for a contact that never had a number: `phone: null` with a
    200, which the client reads as `None` (`services/customer/app/api/internal.py:159-161`).
    Most contacts are in this state, and a lead that came in with an email only converts to one."""
    assert contact == CONTACT_ID and channel == "whatsapp"
    return None


async def _no_lookup_resolver(org: str, contact: str, channel: str) -> str | None:
    raise AssertionError("a step that reads its path as an address must not look a contact up")


async def _template(session: Any, org_id: uuid.UUID, *, category: str = "utility") -> None:
    """An approved WhatsApp template with no variables, registered at Meta as `category`."""
    async with session.begin():
        await services.upsert_provider_template(
            session,
            ctx_for(org_id, T_WRITE, Scope.ORG),
            code="welcome_wa",
            channel="whatsapp",
            provider_name="welcome_converted_lead",
            language="en_US",
            category=category,
            parameter_format="positional",
            status="approved",
            status_reason=None,
            variables=[],
            preview_text="Welcome aboard.",
        )


async def _rule(session: Any, org_id: uuid.UUID) -> None:
    async with session.begin():
        await services.upsert_rule(
            session,
            ctx_for(org_id, T_WRITE, Scope.ORG),
            code="welcome_converted_lead",
            event_type=CONVERTED,
            channel="whatsapp",
            provider_template_code="welcome_wa",
            recipient_contact_path="contact_id",
        )


def _ctx(org_id: uuid.UUID, *perms: str) -> ScopeContext:
    principal = principal_for(org_id, [f"{p}:ORG" for p in perms], entitlements=["engage"])
    return ScopeContext(principal=principal, scope=Scope.ORG, permission=perms[0])


def _recipe_graph(**config: Any) -> dict[str, Any]:
    """The shape the Engage recipe builds: trigger on a conversion, one WhatsApp send whose
    recipient is the contact id on the event. `config` overrides or adds keys of the send step."""
    send: dict[str, Any] = {
        "channel": "whatsapp",
        "template": {"ref": "welcome_wa"},
        "recipient_path": "payload.contact_id",
        "recipient_kind": "contact_id",
        "variable_map": {},
    }
    send.update(config)
    return {
        "nodes": [
            {"id": "t", "type": "trigger", "config": {"event_type": CONVERTED}},
            {"id": "send", "type": "send_message", "config": send},
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [{"from": "t", "to": "send"}, {"from": "send", "to": "end"}],
    }


async def _go_live(session: Any, org_id: uuid.UUID, graph: dict[str, Any]) -> None:
    ctx = _ctx(org_id, WRITE, SEND)
    async with session.begin():
        row = await a1_services.create_automation(
            session, ctx, name="Welcome", description=None, graph=graph
        )
        await a1_services.publish(session, ctx, automation_id=row.id)


async def _runs(session: Any, org_id: uuid.UUID) -> list[AutomationRun]:
    async with session.begin():
        return list(
            (
                await session.execute(
                    select(AutomationRun)
                    .where(AutomationRun.organization_id == org_id)
                    .order_by(AutomationRun.started_at)
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )


async def _send_step(session: Any, run_id: uuid.UUID) -> AutomationRunStep:
    async with session.begin():
        steps = (
            (
                await session.execute(
                    select(AutomationRunStep)
                    .where(AutomationRunStep.run_id == run_id)
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
    return next(step for step in steps if step.type == "send_message")


async def _messages(session: Any, org_id: uuid.UUID) -> list[Message]:
    async with session.begin():
        return list(
            (
                await session.execute(
                    select(Message)
                    .where(Message.organization_id == org_id)
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )


async def _count(session: Any, model: Any) -> int:
    async with session.begin():
        return int((await session.execute(select(func.count()).select_from(model))).scalar_one())


# ------------------------------------------------------------------------------ the keys


def test_the_four_lead_keys_are_subscribable_and_have_schemas() -> None:
    for key in LEAD_KEYS:
        assert key in domain.SUBSCRIBABLE_EVENTS, key
        assert event_schema_path(key, 1, contracts_root=CONTRACTS_ROOT).is_file(), key


def test_the_crm_group_is_exactly_the_nine_expected_keys() -> None:
    """Explicit, never a wildcard, and alphabetical like the rest of the list."""
    crm = [key for key in domain.SUBSCRIBABLE_EVENTS if key.startswith("crm.")]
    assert crm == sorted(CRM_KEYS)
    assert len(crm) == 9


async def test_the_subscribable_events_route_lists_the_lead_keys(client, org_id) -> None:
    with as_principal(principal_for(org_id, [f"{T_READ}:ORG"])):
        response = await client.get("/api/v1/notifications/subscribable-events")
    assert response.status_code == 200
    assert set(LEAD_KEYS) <= set(response.json())


async def test_a_rule_on_an_unlisted_lead_key_is_still_a_422(client, org_id) -> None:
    """`crm.lead.assigned` is a key a later release adds (EV41, R4.2). Until then a rule on it is
    refused at authoring like any other typo, so the list stays the whole of what can fire."""
    body = {
        "code": "r",
        "event_type": "crm.lead.assigned",
        "template_code": "t",
        "channel": "email",
        "recipient_path": "requester_email",
    }
    with as_principal(principal_for(org_id, [f"{T_WRITE}:ORG"])):
        response = await client.put("/api/v1/notifications/rules", json=body)
    assert response.status_code == 422
    # Refused for the event type, not for some other defect of the body.
    assert "is not an event this service subscribes to" in response.text


# ------------------------------------------------------------------------------ rules


async def test_a_rule_on_lead_converted_messages_the_converted_contact(session, org_id) -> None:
    await _template(session, org_id)
    await _rule(session, org_id)
    async with session.begin():
        staged = await services.apply_rules(
            session,
            organization_id=org_id,
            envelope=_converted(org_id),
            resolve_target=_resolver,
        )
    assert len(staged) == 1
    assert staged[0].to_address == PHONE
    assert staged[0].channel == "whatsapp"


async def test_a_rule_on_a_contact_with_no_number_stages_nothing_and_does_not_fail(
    session, org_id
) -> None:
    """The common case, not an edge: the contact exists and has never had a number. The rule is
    skipped with a log line (`notification.rule.contact_has_no_address`) and not raised, so the
    other rules on the event, and the event itself, are untouched."""
    await _template(session, org_id)
    await _rule(session, org_id)
    async with session.begin():
        staged = await services.apply_rules(
            session,
            organization_id=org_id,
            envelope=_converted(org_id),
            resolve_target=_no_number_resolver,
        )
    assert staged == []


async def test_a_redelivered_lead_event_is_folded_once(session, org_id, worker_engine) -> None:
    """Redelivery is normal: the ledger row and the message share one transaction, so the second
    delivery of the same event id changes nothing."""
    await _template(session, org_id)
    await _rule(session, org_id)
    envelope = _converted(org_id)

    await consumers.fold(envelope, _resolver)
    await consumers.fold(envelope, _resolver)

    assert len(await _messages(session, org_id)) == 1
    assert await _count(session, ProcessedEvent) == 1


async def test_every_lead_event_is_folded_and_does_nothing_without_a_rule(
    session, org_id, worker_engine
) -> None:
    """What binding four more keys changes on a tenant that authored nothing: each event is
    recorded as processed and nothing else happens. This is the production effect of the change."""
    for key in LEAD_KEYS:
        await consumers.fold(envelope_for(org_id, key, {"organization_id": str(org_id)}))
    assert await _count(session, ProcessedEvent) == len(LEAD_KEYS)
    assert await _messages(session, org_id) == []


async def test_a_lead_event_never_reaches_another_organizations_rule(
    session, admin_session, org_id, worker_engine
) -> None:
    await _template(session, org_id)
    await _rule(session, org_id)

    # Another tenant's conversion: it binds that tenant's GUC, finds none of this tenant's rules,
    # and never asks anyone for an address.
    await consumers.fold(_converted(uuid.uuid4()), _refusing_resolver)
    async with admin_session.begin():
        total = (
            await admin_session.execute(select(func.count()).select_from(Message))
        ).scalar_one()
    assert total == 0

    # The rule is live: the same kind of event for its own tenant does fire it.
    await consumers.fold(_converted(org_id), _resolver)
    assert len(await _messages(session, org_id)) == 1


# ------------------------------------------------------------------------------ automations


async def test_an_automation_shaped_like_the_recipe_messages_the_converted_contact(
    session, org_id, worker_engine
) -> None:
    await _template(session, org_id)
    await _go_live(session, org_id, _recipe_graph())

    await consumers.fold(_converted(org_id), _resolver)
    (run,) = await _runs(session, org_id)
    assert run.state == "completed"
    (message,) = await _messages(session, org_id)
    assert message.to_address == PHONE and message.state == "pending"

    # A phone-only lead converts with no contact: the step cannot find a recipient and says so.
    await consumers.fold(_converted(org_id, contact_id=None), _resolver)
    failed = [r for r in await _runs(session, org_id) if r.state == "failed"]
    assert len(failed) == 1 and failed[0].failure_code == "recipient_missing"
    assert len(await _messages(session, org_id)) == 1


async def test_a_converted_contact_with_no_number_is_a_visible_failure_and_sends_nothing(
    session, org_id, worker_engine
) -> None:
    """The same case through the recipe's automation. The run ends `failed` with
    `recipient_missing`, which the tenant sees in the run history, and nothing is queued. A
    redelivery of the event changes nothing."""
    await _template(session, org_id)
    await _go_live(session, org_id, _recipe_graph(purpose="marketing"))
    envelope = _converted(org_id)

    await consumers.fold(envelope, _no_number_resolver)
    await consumers.fold(envelope, _no_number_resolver)

    (run,) = await _runs(session, org_id)
    assert run.state == "failed" and run.failure_code == "recipient_missing"
    assert await _messages(session, org_id) == []


async def test_a_contact_id_read_as_an_address_is_refused_and_nothing_is_sent(
    session, org_id, worker_engine
) -> None:
    """What a person gets who retypes the builder's "Send to" path (Task R1.2.12): the kind is
    dropped, the path still names the contact id, and the id is read as a phone number. It is not
    an E.164 number, so the step fails `recipient_invalid`; no contact is looked up and no one is
    messaged."""
    await _template(session, org_id)
    graph = _recipe_graph(purpose="marketing")
    del graph["nodes"][1]["config"]["recipient_kind"]
    await _go_live(session, org_id, graph)

    await consumers.fold(_converted(org_id), _no_lookup_resolver)

    (run,) = await _runs(session, org_id)
    assert run.state == "failed" and run.failure_code == "recipient_invalid"
    assert await _messages(session, org_id) == []


# ------------------------------------------------------------------------------ consent


async def test_the_recipe_shaped_send_is_skipped_without_a_marketing_opt_in(
    session, org_id, worker_engine
) -> None:
    """The recipe sets `purpose: "marketing"`, so silence is not consent whatever category the
    template is registered under at Meta: the step is a recorded skip and nothing is queued."""
    await _template(session, org_id, category="utility")
    await _go_live(session, org_id, _recipe_graph(purpose="marketing"))

    await consumers.fold(_converted(org_id), _resolver)

    (run,) = await _runs(session, org_id)
    assert run.state == "skipped"
    step = await _send_step(session, run.id)
    assert step.state == "skipped" and step.detail == "consent:no_opt_in"
    (message,) = await _messages(session, org_id)
    assert message.state == "suppressed"
    async with session.begin():
        queued = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == services.EVT_MESSAGE_QUEUED)
                )
            )
            .scalars()
            .all()
        )
    assert queued == []


async def test_the_recipe_shaped_send_goes_out_after_an_opt_in(
    session, org_id, worker_engine
) -> None:
    await _template(session, org_id, category="utility")
    await _go_live(session, org_id, _recipe_graph(purpose="marketing"))
    async with session.begin():
        await consent.append(
            session,
            organization_id=org_id,
            subject=consent.subject_for("whatsapp", PHONE),
            purposes=(consent.MARKETING,),
            status=consent.OPTED_IN,
            source="admin",
            evidence="test fixture",
        )

    await consumers.fold(_converted(org_id), _resolver)

    (run,) = await _runs(session, org_id)
    assert run.state == "completed"
    (message,) = await _messages(session, org_id)
    assert message.state == "pending" and message.to_address == PHONE


@pytest.mark.parametrize(
    ("category", "message_state", "step_state"),
    [("utility", "pending", "done"), ("marketing", "suppressed", "skipped")],
)
async def test_without_a_purpose_consent_follows_the_template_category(
    session, org_id, worker_engine, category: str, message_state: str, step_state: str
) -> None:
    """Characterization of the default the recipe avoids: with no `purpose`, whether a contact with
    no opt-in on file is messaged depends on how the tenant registered the template at Meta, which
    this service cannot know. That is why the recipe sets the purpose itself."""
    await _template(session, org_id, category=category)
    await _go_live(session, org_id, _recipe_graph())

    await consumers.fold(_converted(org_id), _resolver)

    (run,) = await _runs(session, org_id)
    step = await _send_step(session, run.id)
    (message,) = await _messages(session, org_id)
    assert (message.state, step.state) == (message_state, step_state)
    if step_state == "skipped":
        assert step.detail == "consent:no_opt_in"
```

Create `tests/infra/test_engage_recipe_triggers.py`:

```python
"""Every CRM event an Engage starter recipe triggers on is one notification lets a rule subscribe to.

A recipe is a starting point offered on an empty automation list. Its trigger is a routing key, and
the builder, the publish check and the consumer all work from notification's explicit list
(`domain.SUBSCRIBABLE_EVENTS`): a recipe that names a key outside it opens with a trigger the
service refuses, and nothing in the frontend suite can see that, because the list lives in Python.
"Welcome a new lead" triggered on `crm.lead.created` while no lead key was subscribable, and shipped
that way.

The recipes are read as text and the list from a fresh interpreter in `services/notification`
(`import app` is ambiguous across services). Only `crm.*` triggers are held to the list: the other
products' recipes are those products' to keep, and one of them ("Follow up a storefront enquiry")
is known to wait for its key (registry `EV42`).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RECIPES = (
    REPO_ROOT / "frontend" / "product-engage" / "src" / "routes" / "automations" / "recipes.ts"
)
NOTIFICATION = REPO_ROOT / "services" / "notification"

_TRIGGER = re.compile(r'emptyGraph\(\s*"([^"]+)"\s*\)')


def _subscribable_events() -> set[str]:
    script = (
        "import json\n"
        "from app.domain import SUBSCRIBABLE_EVENTS\n"
        "print(json.dumps(list(SUBSCRIBABLE_EVENTS)))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=NOTIFICATION,
        check=True,
        capture_output=True,
        text=True,
    )
    return {str(key) for key in json.loads(result.stdout)}


def test_every_crm_recipe_trigger_is_subscribable() -> None:
    triggers = _TRIGGER.findall(RECIPES.read_text(encoding="utf-8"))
    assert triggers, "found no emptyGraph(...) trigger in recipes.ts: this test would be vacuous"
    crm = sorted({trigger for trigger in triggers if trigger.startswith("crm.")})
    assert crm, "expected at least one crm.* recipe trigger in recipes.ts"
    missing = sorted(set(crm) - _subscribable_events())
    assert not missing, (
        f"Engage recipes trigger on {missing}, which notification's SUBSCRIBABLE_EVENTS does not "
        "list: add the key in services/notification/app/domain.py (with its schema under "
        "libs/contracts/events), or change the recipe"
    )
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/infra/test_engage_recipe_triggers.py -q -p no:cacheprovider`
Expected: FAIL (observed): `AssertionError: Engage recipes trigger on ['crm.lead.created'], which notification's
SUBSCRIBABLE_EVENTS does not list: add the key in services/notification/app/domain.py ...`. This is the shipped
defect: the starter "Welcome a new lead" opens on a key the service refuses.

Run: `cd services/notification && uv run pytest tests/test_crm_lead_keys.py -q -p no:cacheprovider` (needs `make infra`
and `make migrate`)
Expected: FAIL, `14 failed, 2 passed` (inference): the two key tests fail on the missing keys, the route test on the
response, and every test that authors a rule or publishes an automation on `crm.lead.converted` (the three no-number
and contact-id-as-address tests included) fails at authoring
(`ValidationProblem: 'crm.lead.converted' is not an event this service subscribes to` for a rule; the trigger problem
`trigger_event: choose an event this platform publishes` for an automation). The 422 test and the no-rule fold test
pass already: `crm.lead.assigned` is unlisted before and after, and `consumers.fold` records any event it is handed.

- [ ] **Step 3: Write the minimal implementation**

In `services/notification/app/domain.py`, in the `# crm` group of `SUBSCRIBABLE_EVENTS` (lines 146-151), replace

```python
    # crm
    "crm.book.company.created",
    "crm.book.contact.created",
    "crm.pipeline.activity.logged",
```

with

```python
    # crm
    "crm.book.company.created",
    "crm.book.contact.created",
    # Lead events carry ids and no address: only `converted` names a contact that exists, so a
    # rule reaches it through `recipient_contact_path` (customer resolves the number).
    "crm.lead.conversion_aborted",
    "crm.lead.converted",
    "crm.lead.created",
    "crm.lead.updated",
    "crm.pipeline.activity.logged",
```

(`crm.pipeline.deal.created` and `crm.pipeline.deal.stage_changed` follow, unchanged. The edited tuple was observed
clean under `ruff check` and `ruff format --check`: the longest new line is exactly 100 characters.)

- [ ] **Step 4: Run them and see them pass**

Run: `uv run pytest tests/infra/test_engage_recipe_triggers.py -q -p no:cacheprovider`
Expected: `1 passed` (observed against a scratch tree with this edit, with the base recipe and with the repaired one).

Run: `cd services/notification && uv run pytest tests/test_crm_lead_keys.py tests/test_domain.py -q -p no:cacheprovider`
Expected: `16 passed` for the new module and every `test_domain.py` test still passing (inference): the catalogue
tests there (`test_the_catalogue_covers_every_product_line`, `..._never_includes_this_service_s_own_events`,
`..._holds_no_fragments`) accept the longer list.

Run: `uv run pytest libs/contracts -q -p no:cacheprovider`
Expected: `8 passed`: the subscribable-keys test now also holds the four lead keys to a committed schema, and they
have one.

Run: `cd services/notification && uv run mypy app && cd ../.. && uv run ruff check services/notification tests/infra && uv run ruff format --check services/notification tests/infra`
Expected: `Success: no issues found`, `All checks passed!`, `already formatted`.

Run: `cd services/notification && uv run pytest tests -q -p no:cacheprovider`
Expected: the whole service suite passes (inference).

- [ ] **Step 5: Commit**

```bash
git add services/notification/app/domain.py services/notification/tests/test_crm_lead_keys.py tests/infra/test_engage_recipe_triggers.py
git commit -m "Notification: subscribe to the four lead events and test them through the consumer"
```

**OWNER RUN (O-10, after the R1.2b deploy):** list the bindings of `notification.events` and compare with the baseline
O-6(c) recorded: every earlier binding is still there and exactly four are new, `crm.lead.created`,
`crm.lead.updated`, `crm.lead.converted` and `crm.lead.conversion_aborted`. A lead event published from now on reaches
notification, is recorded and, with no rule or automation, does nothing.

---

### Task R1.2.12: The repaired Engage recipe, `recipient_kind` and two builder rules

**Deploy:** R1.2b (the SPA bundle is published after the deploy, O-12). **Spec:** `S§5.3.5`, `S§5.7`, `AC-1.2.5`,
`AC-1.2.9`. **Registry:** `EV39`. Strike-able (O-15): if struck, the starter recipe is removed and `recipient_kind` is
not typed.

**Files:**
- Modify: `frontend/product-engage/src/api/automations.ts:25,60-67` (a type and one optional field)
- Modify: `frontend/product-engage/src/routes/automations/graph.ts` (a helper before `replaceNode`, line 280)
- Modify: `frontend/product-engage/src/routes/automations/SendSettings.tsx:4,35,44-49,127-129`
- Modify: `frontend/product-engage/src/routes/automations/recipes.ts:21-26`
- Create: `frontend/product-engage/src/routes/automations/recipes.test.ts`
- Modify: `frontend/product-engage/src/routes/automations/Automations.test.tsx:52-53`, `graph.test.ts:7-17` and its end,
  `Builder.test.tsx:1-3` and its end
- Test: those four test files

**Interfaces:**
- Consumes: the A1 engine's existing support for `recipient_kind: "contact_id"` and `purpose`
  (`_send` at `services/notification/app/a1_services.py:928`; `recipient_kind` at `:947`, `purpose` at `:972`: it looks
  the contact up through customer's messaging target and applies marketing consent), which Task R1.2.11 makes
  reachable from a lead key; `addStep`, `emptyGraph`,
  `replaceNode`, `withoutKey` of `routes/automations/graph.ts`; the existing `Builder.test.tsx` helpers `openDraft`,
  `AUTOMATION_DRAFT`, `api` and the `Graph` type.
- Produces: `export type RecipientKind = "address" | "contact_id"` and the optional field
  `recipient_kind?: RecipientKind` on `SendMessageConfig` (`api/automations.ts`); `export function
  namesAContactId(path: string): boolean` (`graph.ts`: true when the last dot-separated segment is `contact_id`); the
  recipe `{ id: "welcome-converted-lead", name: "Welcome a converted lead" }` replacing `welcome-new-lead`; the helper
  line "The CRM contact on the event. The WhatsApp number is looked up from the contact when the message is sent." under
  "Send to". The wire shape is unchanged (the field is optional and absent means `address`).

The starter "Welcome a new lead" was broken twice: its trigger `crm.lead.created` was not subscribable (fixed by Task
R1.2.11) and no lead event carries a phone number, so its "Send to" path could not name an address. The repaired
recipe triggers on `crm.lead.converted`, whose payload carries the contact id, sends to that contact with
`recipient_kind: "contact_id"` and `purpose: "marketing"`: **silence is not consent**, so a contact with no marketing
opt-in on file is skipped (`consent:no_opt_in`) whatever category the tenant registered the template under at Meta
(the notification tests of Task R1.2.11 pin both outcomes). Two builder rules keep the kind honest and there is no
control to choose it: it is dropped when the channel becomes email (a contact lookup is WhatsApp only until customer
can answer an email address) and is not brought back; it is kept while the "Send to" path still ends in `contact_id`
and dropped when the path becomes anything else. That holds one keystroke at a time too: a person who deletes a letter
of `contact_id` and types it back has lost the kind, the helper text flips to the address wording, and the service
refuses the id as a phone number (`recipient_invalid`, never a message to the wrong person). The last Builder test pins
that as a characterization of the rule, and the matching notification test is in Task R1.2.11; restoring the kind on
retyping is a reviewer's choice (the test's comment says what it costs).

- [ ] **Step 1: Write the failing tests**

Create `frontend/product-engage/src/routes/automations/recipes.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { findRecipe, RECIPES } from "./recipes";

/**
 * The starter recipes are what an empty automation list offers, so what each one builds is a
 * promise to the person who picks it. The lead recipe is pinned whole, `purpose` included: it is
 * the first starter that messages a CRM contact straight from a CRM event, and it must only send
 * to a contact who has opted in.
 */

describe("the lead recipe", () => {
  it("is the converted-lead welcome, and the only recipe that starts on a lead event", () => {
    const leadRecipes = RECIPES.filter((recipe) => {
      const trigger = recipe.build().nodes.find((node) => node.type === "trigger");
      return trigger?.type === "trigger" && trigger.config.event_type.startsWith("crm.lead.");
    });
    expect(leadRecipes.map((recipe) => recipe.id)).toEqual(["welcome-converted-lead"]);
    const recipe = findRecipe("welcome-converted-lead");
    expect(recipe?.name).toBe("Welcome a converted lead");
    expect(recipe?.description).toBe(
      "When a lead is converted, send the new contact a WhatsApp welcome message, if they have opted in.",
    );
  });

  it("starts on lead conversion and messages the converted contact as a marketing send", () => {
    expect(findRecipe("welcome-converted-lead")?.build()).toEqual({
      nodes: [
        { id: "trigger", type: "trigger", config: { event_type: "crm.lead.converted" } },
        {
          id: "send_message_1",
          type: "send_message",
          config: {
            channel: "whatsapp",
            template: { ref: "" },
            recipient_path: "payload.contact_id",
            recipient_kind: "contact_id",
            variable_map: {},
            purpose: "marketing",
          },
        },
      ],
      edges: [{ from: "trigger", to: "send_message_1" }],
    });
  });
});
```

In `frontend/product-engage/src/routes/automations/Automations.test.tsx` replace lines 52-53

```tsx
    const link = screen.getByRole("link", { name: /Welcome a new lead/ });
    expect(link.getAttribute("href")).toBe("/automations/new?recipe=welcome-new-lead");
```

with

```tsx
    const link = screen.getByRole("link", { name: /Welcome a converted lead/ });
    expect(link.getAttribute("href")).toBe("/automations/new?recipe=welcome-converted-lead");
```

In `frontend/product-engage/src/routes/automations/graph.test.ts` add `namesAContactId,` to the import list, between
`moveStep,` and `nodeById,` (line 16):

```ts
  moveStep,
  namesAContactId,
  nodeById,
```

and append at the end of the file:

```ts

describe("namesAContactId", () => {
  it("is true when the last segment of the path is contact_id", () => {
    expect(namesAContactId("payload.contact_id")).toBe(true);
    expect(namesAContactId("payload.customer.contact_id")).toBe(true);
  });

  it("is false for anything else, including a path that only contains the words", () => {
    expect(namesAContactId("")).toBe(false);
    expect(namesAContactId("payload.customer.phone")).toBe(false);
    expect(namesAContactId("payload.contact_id.phone")).toBe(false);
    expect(namesAContactId("payload.not_contact_id")).toBe(false);
    expect(namesAContactId("payload.contact_id.")).toBe(false);
  });
});
```

In `frontend/product-engage/src/routes/automations/Builder.test.tsx` replace the first three import lines

```tsx
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
```

with

```tsx
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MockInstance } from "vitest";
```

and append at the end of the file:

```tsx

describe("a send step that looks a contact up", () => {
  const CONTACT_HELPER =
    "The CRM contact on the event. The WhatsApp number is looked up from the contact when the message is sent.";
  const ADDRESS_HELPER = "Where the phone number is read from on the event.";

  const LOOKUP_GRAPH: Graph = {
    nodes: [
      { id: "trigger", type: "trigger", config: { event_type: "crm.lead.converted" } },
      {
        id: "send_message_1",
        type: "send_message",
        config: {
          channel: "whatsapp",
          template: { ref: "" },
          recipient_path: "payload.contact_id",
          recipient_kind: "contact_id",
          variable_map: {},
          purpose: "marketing",
        },
      },
    ],
    edges: [{ from: "trigger", to: "send_message_1" }],
  };

  /** Open the draft and select its send step. Returns the spy the save goes through. */
  async function openStep() {
    const update = vi.spyOn(api, "updateAutomation").mockResolvedValue(AUTOMATION_DRAFT);
    openDraft(LOOKUP_GRAPH);
    const flow = await screen.findByRole("region", { name: "Automation flow" });
    await userEvent.click(within(flow).getByRole("button", { name: /2 · Send message/ }));
    return update;
  }

  /** Save, then read the send step's config the way the service would receive it. */
  async function savedConfig(update: MockInstance<typeof api.updateAutomation>) {
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    const send = update.mock.calls[0]?.[1].graph?.nodes.find((n) => n.type === "send_message");
    if (send?.type !== "send_message") throw new Error("the saved graph has no send step");
    return send.config;
  }

  it("says where the number comes from, and keeps the kind through a template pick and a purpose change", async () => {
    const update = await openStep();
    expect(await screen.findByText(CONTACT_HELPER)).toBeTruthy();
    expect((screen.getByLabelText(/^Send to/) as HTMLInputElement).placeholder).toBe("payload.contact_id");

    await userEvent.selectOptions(await screen.findByLabelText(/^Template/), "payment_reminder");
    await userEvent.selectOptions(screen.getByLabelText(/^Purpose/), "transactional");

    expect(screen.getByText(CONTACT_HELPER)).toBeTruthy();
    const config = await savedConfig(update);
    expect(config.recipient_kind).toBe("contact_id");
    expect(config.template.ref).toBe("payment_reminder");
    expect(config.purpose).toBe("transactional");
  });

  it("drops the kind when the channel becomes email and does not bring it back with WhatsApp", async () => {
    const update = await openStep();
    await userEvent.selectOptions(await screen.findByLabelText(/^Channel/), "email");
    expect(await screen.findByText("Where the email address is read from on the event.")).toBeTruthy();
    expect(screen.queryByText(CONTACT_HELPER)).toBeNull();

    await userEvent.selectOptions(screen.getByLabelText(/^Channel/), "whatsapp");
    expect(await screen.findByText(ADDRESS_HELPER)).toBeTruthy();
    expect(screen.queryByText(CONTACT_HELPER)).toBeNull();

    const config = await savedConfig(update);
    expect(config).not.toHaveProperty("recipient_kind");
    expect(config.channel).toBe("whatsapp");
  });

  it("keeps the kind for another path that names a contact id, and drops it for one that does not", async () => {
    const update = await openStep();
    const sendTo = await screen.findByLabelText(/^Send to/);

    fireEvent.change(sendTo, { target: { value: "payload.customer.contact_id" } });
    expect(screen.getByText(CONTACT_HELPER)).toBeTruthy();

    fireEvent.change(sendTo, { target: { value: "payload.customer.phone" } });
    expect(screen.getByText(ADDRESS_HELPER)).toBeTruthy();
    expect(screen.queryByText(CONTACT_HELPER)).toBeNull();

    const config = await savedConfig(update);
    expect(config).not.toHaveProperty("recipient_kind");
    expect(config.recipient_path).toBe("payload.customer.phone");
  });

  /* Characterization of the design's second rule under the edit a person really makes: typing in the
     field. Every keystroke is one change, so deleting a letter of `contact_id` and typing it back
     passes through a path that names no contact id, and the kind is gone for good (there is no
     control to choose it). The helper text flips at once, which is the only signal, and the service
     refuses the id as an address (`test_a_contact_id_read_as_an_address_is_refused_and_nothing_is_sent`
     in `test_crm_lead_keys.py`), so nothing is sent to the wrong person. A reviewer who prefers to
     restore the kind when the path names a contact id again flips this one test and gives
     `SendSettings` a ref that remembers the kind the step was opened with (and `StepSettings` a
     `key={node.id}` so it is not shared between steps). */
  it("loses the kind on the first edit that stops naming a contact id, and typing the path back does not restore it", async () => {
    const update = await openStep();
    const sendTo = await screen.findByLabelText(/^Send to/);

    fireEvent.change(sendTo, { target: { value: "payload.contact_i" } });
    expect(screen.getByText(ADDRESS_HELPER)).toBeTruthy();

    fireEvent.change(sendTo, { target: { value: "payload.contact_id" } });
    expect(screen.getByText(ADDRESS_HELPER)).toBeTruthy();
    expect(screen.queryByText(CONTACT_HELPER)).toBeNull();

    const config = await savedConfig(update);
    expect(config.recipient_path).toBe("payload.contact_id");
    expect(config).not.toHaveProperty("recipient_kind");
  });
});
```

- [ ] **Step 2: Run them and see them fail**

Run: `cd frontend/product-engage && pnpm exec vitest run src/routes/automations/recipes.test.ts src/routes/automations/graph.test.ts src/routes/automations/Automations.test.tsx src/routes/automations/Builder.test.tsx`
Expected: FAIL, `Test Files 4 failed (4)`, `Tests 9 failed | 65 passed (74)` (8 failed and 73 in all were observed
against the unmodified sources before the typing test below was added; the ninth failure is inference): the two recipe
tests (`expected [ 'welcome-new-lead' ] to deeply equal [ 'welcome-converted-lead' ]`, and
`expected undefined to deeply equal {...}`), the two `namesAContactId` tests (the export does not exist), the
`Automations` list test (the link name) and the four `a send step that looks a contact up` tests (the typing test fails
on `expect(config).not.toHaveProperty("recipient_kind")`: the unmodified step keeps the kind through any path edit).

- [ ] **Step 3: Write the minimal implementation**

`frontend/product-engage/src/api/automations.ts`: after `export type MessagePurpose = "transactional" | "marketing";`
(line 24) add

```ts
/** What `recipient_path` points at: an address (the default) or a CRM contact id, which the
    service turns into an address when the message is sent. */
export type RecipientKind = "address" | "contact_id";
```

and in `SendMessageConfig`, between `recipient_path: string;` and the `/** Template variable key ... */` comment add

```ts
  /** Absent means `address`. `contact_id` makes the service look the contact up when the message
   *  is sent; it is valid on WhatsApp only, until customer can answer an email address. */
  recipient_kind?: RecipientKind;
```

`frontend/product-engage/src/routes/automations/graph.ts`: before `/** Replace a node by id with a whole new node. */`
(line 280) add

```ts
/** Whether a "Send to" path names a CRM contact id: its last segment is `contact_id`
 *  (`payload.contact_id`, `payload.customer.contact_id`). Anything else is read as an address. */
export function namesAContactId(path: string): boolean {
  const segments = path.split(".");
  return segments[segments.length - 1] === "contact_id";
}

```

`frontend/product-engage/src/routes/automations/SendSettings.tsx`, four edits.

1. Line 4: `import { withoutKey } from "./graph";` becomes `import { namesAContactId, withoutKey } from "./graph";`.
2. Line 35: replace

```tsx
  const { channel, template, recipient_path, variable_map, purpose } = node.config;
```

with

```tsx
  const { channel, template, recipient_path, recipient_kind, variable_map, purpose } = node.config;
  const looksUpAContact = recipient_kind === "contact_id";
```

3. In `chooseChannel` (lines 44-49) replace

```tsx
    /* A template belongs to one channel, so switching drops it and the mapping that went with it.
       The recipient path stays: it names where an address is read from, not what is sent. */
    patch({ channel: next, template: { ref: "" }, variable_map: {} });
  }
```

with

```tsx
    /* A template belongs to one channel, so switching drops it and the mapping that went with it.
       The recipient path stays: it names where an address is read from, not what is sent. A contact
       lookup is WhatsApp only until customer can answer an email address, so the kind goes when
       the channel becomes email, and is not brought back when it returns to WhatsApp. */
    const rest = next === "email" ? withoutKey(node.config, "recipient_kind") : node.config;
    onChange({ ...node, config: { ...rest, channel: next, template: { ref: "" }, variable_map: {} } });
  }

  function changeRecipientPath(value: string) {
    /* Only a path that names a contact id can be a contact lookup; any other path is read as an
       address, so editing it to one drops the kind. There is no control to choose the kind. */
    const rest = namesAContactId(value) ? node.config : withoutKey(node.config, "recipient_kind");
    onChange({ ...node, config: { ...rest, recipient_path: value } });
  }
```

4. In the "Send to" `PathField` (lines 127-129) replace

```tsx
        placeholder="payload.customer.phone"
        helper={`Where the ${channel === "email" ? "email address" : "phone number"} is read from on the event.`}
        onChange={(value) => patch({ recipient_path: value })}
```

with

```tsx
        placeholder={looksUpAContact ? "payload.contact_id" : "payload.customer.phone"}
        helper={
          looksUpAContact
            ? "The CRM contact on the event. The WhatsApp number is looked up from the contact when the message is sent."
            : `Where the ${channel === "email" ? "email address" : "phone number"} is read from on the event.`
        }
        onChange={changeRecipientPath}
```

`frontend/product-engage/src/routes/automations/recipes.ts` (lines 21-26): replace the first recipe

```ts
  {
    id: "welcome-new-lead",
    name: "Welcome a new lead",
    description: "When a lead is created, send a WhatsApp welcome message.",
    build: () => addStep(emptyGraph("crm.lead.created"), { from: "trigger" }, "send_message").graph,
  },
```

with

```ts
  {
    id: "welcome-converted-lead",
    name: "Welcome a converted lead",
    description: "When a lead is converted, send the new contact a WhatsApp welcome message, if they have opted in.",
    build: () => {
      const graph = addStep(emptyGraph("crm.lead.converted"), { from: "trigger" }, "send_message").graph;
      return replaceNode(graph, {
        id: "send_message_1",
        type: "send_message",
        config: {
          channel: "whatsapp",
          template: { ref: "" },
          recipient_path: "payload.contact_id",
          recipient_kind: "contact_id",
          variable_map: {},
          purpose: "marketing",
        },
      });
    },
  },
```

(`recipes.ts` already imports `addStep`, `emptyGraph` and `replaceNode`.)

- [ ] **Step 4: Run them and see them pass**

Run: `cd frontend/product-engage && pnpm exec vitest run src/routes/automations/recipes.test.ts src/routes/automations/graph.test.ts src/routes/automations/Automations.test.tsx src/routes/automations/Builder.test.tsx`
Expected: the four files pass.

Run: `cd frontend && pnpm --filter @opsgrids/product-engage typecheck && pnpm --filter @opsgrids/product-engage lint && pnpm --filter @opsgrids/product-engage test`
Expected: all clean; `Test Files 69 passed (69)`, `Tests 907 passed (907)` (906 were observed against a scratch copy of
the app with exactly these edits before the typing test was added, and `tsc --noEmit` and `eslint . --max-warnings=0`
printed nothing; the 907th is inference).

Run (the acceptance greps):
`grep -rn 'welcome-new-lead\|Welcome a new lead' frontend/product-engage/src` Expected: no output.
`grep -n recipient_kind frontend/product-engage/src/api/automations.ts` Expected: one line, `recipient_kind?: RecipientKind;`.
`grep -n 'purpose: "marketing"' frontend/product-engage/src/routes/automations/recipes.ts` Expected: one line.

- [ ] **Step 5: Commit**

```bash
git add frontend/product-engage/src/api/automations.ts frontend/product-engage/src/routes/automations/graph.ts frontend/product-engage/src/routes/automations/SendSettings.tsx frontend/product-engage/src/routes/automations/recipes.ts frontend/product-engage/src/routes/automations/recipes.test.ts frontend/product-engage/src/routes/automations/Automations.test.tsx frontend/product-engage/src/routes/automations/graph.test.ts frontend/product-engage/src/routes/automations/Builder.test.tsx
git commit -m "Engage: repair the lead recipe to welcome converted contacts who have opted in"
```

**OWNER RUN (O-12):** after the R1.2b deploy, from a checkout of the deployed ref, `make edge-web HOST=... CONFIRM=...
APP=engage` (Engage after notification, so the recipe's trigger is already in the catalogue), then confirm on the box
that the bundle contains the new recipe (`grep -rl "Welcome a converted lead" <EDGE_WEB_ROOT>/engage/current`).

---

### Task R1.2.13: ops-monitor watches `lead`'s outbox

**Deploy:** R1.2b. **Spec:** `S§5.3.6`, `AC-1.2.6`. **Registry:** `F6`.

**Files:**
- Modify: `infrastructure/edge/compose.prod.yml:851` (ops-monitor's `EDGE_RELAY_SCHEMAS` default)
- Modify: `tests/infra/test_ops_monitor_compose_env.py` (append one helper and one test)
- Modify: `docs/deploy/ENV_SOURCE_MATRIX.md:245,837,925-926` and `docs/deploy/env-reference.md:330,828-829`
- Test: `tests/infra/test_ops_monitor_compose_env.py`

**Interfaces:**
- Consumes: `COMPOSE_FILE`, `re` and `yaml` already imported by the test module; the compose services `edge-relay`
  (`:786-791`, 29 names, `lead` among them) and `ops-monitor` (`:846-851`, 28 names); ops-monitor's own reading of the
  variable (`libs/platform-core/src/platform_core/ops_monitor/config.py:3,290`: it reuses `EDGE_RELAY_SCHEMAS`
  verbatim for the outbox-lag check, one `outbox.<schema>` alert per name).
- Produces: `_relay_schemas_default(service: str) -> list[str]` (the names of a compose service's
  `${EDGE_RELAY_SCHEMAS:-a,b,c}` default) and `test_ops_monitor_watches_every_schema_edge_relay_drains`. After the
  edit both defaults are the same 29 names.

`lead`'s outbox is drained by `edge-relay` (its name is in that default) and, until now, watched by nobody: a stalled
lead outbox raised no `outbox.lead` alert. **No DLQ queue is added here** (see "Scope reconciliation", item 1): the
DLQ lists live in `libs/platform-core`, whose change would make the deploy a full image rebuild (DD-20), and each queue
exists only once its consumer has run. This task changes one `compose.prod.yml` line, so the deploy recreates
ops-monitor and nothing else in compose.

- [ ] **Step 1: Write the failing test**

Append to `tests/infra/test_ops_monitor_compose_env.py` (after the last test, leaving two blank lines):

```python


def _relay_schemas_default(service: str) -> list[str]:
    """The `${EDGE_RELAY_SCHEMAS:-a,b,c}` default one compose service carries, as a list."""
    raw = yaml.safe_load(COMPOSE_FILE.read_text())
    value = raw["services"][service]["environment"]["EDGE_RELAY_SCHEMAS"]
    match = re.fullmatch(r"\$\{EDGE_RELAY_SCHEMAS:-([^}]*)\}", str(value))
    assert match, f"{service}'s EDGE_RELAY_SCHEMAS is not a ${{EDGE_RELAY_SCHEMAS:-...}} default"
    return match.group(1).split(",")


def test_ops_monitor_watches_every_schema_edge_relay_drains() -> None:
    """`edge-relay` drains the outbox of every schema in its list; ops-monitor raises an
    `outbox.<schema>` lag alert only for the schemas in its own. Two lists that differ are a schema
    whose outbox can stall with nobody told, which is what `lead` was: drained, never watched."""
    relay = _relay_schemas_default("edge-relay")
    monitor = _relay_schemas_default("ops-monitor")
    assert monitor == relay, (
        f"ops-monitor does not watch {sorted(set(relay) - set(monitor))} and watches "
        f"{sorted(set(monitor) - set(relay))} that edge-relay does not drain"
    )
    assert "lead" in monitor
```

- [ ] **Step 2: Run it and see it fail**

Run: `uv run pytest tests/infra/test_ops_monitor_compose_env.py -q -p no:cacheprovider`
Expected: FAIL, `1 failed, 8 passed` (observed): `AssertionError: ops-monitor does not watch ['lead'] and watches []
that edge-relay does not drain`.

- [ ] **Step 3: Write the minimal implementation**

In `infrastructure/edge/compose.prod.yml`, ops-monitor's environment (line 851), replace `customer,deal,project,timesheet`
with `customer,deal,lead,project,timesheet` in

```yaml
      EDGE_RELAY_SCHEMAS: ${EDGE_RELAY_SCHEMAS:-auth,account,product,catalog,subscription,billing,metering,payment,notification,storefront,document,operations,approval,employee,attendance,leave,customer,deal,project,timesheet,standup,projects,support,marketing,finance,scheduler,itsm,console}
```

so that it reads

```yaml
      EDGE_RELAY_SCHEMAS: ${EDGE_RELAY_SCHEMAS:-auth,account,product,catalog,subscription,billing,metering,payment,notification,storefront,document,operations,approval,employee,attendance,leave,customer,deal,lead,project,timesheet,standup,projects,support,marketing,finance,scheduler,itsm,console}
```

(`customer,deal,project` occurs once in the file, and `customer,deal,lead,project` then twice, `AC-1.2.6`.)

Correct the five statements in the deploy documents that say the two defaults differ. Apply each pair; the old text
occurs exactly once (observed).

`docs/deploy/ENV_SOURCE_MATRIX.md`, line 245, inside the `EDGE_RELAY_SCHEMAS` row:

````text old=docs/deploy/ENV_SOURCE_MATRIX.md#245
The two compose defaults DIFFER: edge-relay's list contains `lead` (INT:cp:708), ops-monitor's does not (INT:cp:768), so its outbox-lag check does not cover the `lead` schema.
````

````text new=docs/deploy/ENV_SOURCE_MATRIX.md#245
One compose default (29 names, `lead` included) serves both containers, so ops-monitor's outbox-lag check covers every schema edge-relay drains; `tests/infra/test_ops_monitor_compose_env.py` keeps the two defaults equal.
````

Line 837:

````text old=docs/deploy/ENV_SOURCE_MATRIX.md#837
`EDGE_RELAY_SCHEMAS` (ops-monitor's own default list has 27 names and omits `lead`; section 4).
````

````text new=docs/deploy/ENV_SOURCE_MATRIX.md#837
`EDGE_RELAY_SCHEMAS` (ops-monitor's default list is the same 29 names as edge-relay's, `lead` included; section 4).
````

Lines 925-926:

````text old=docs/deploy/ENV_SOURCE_MATRIX.md#925
11. **Two `EDGE_RELAY_SCHEMAS` defaults differ:** edge-relay's includes `lead`, ops-monitor's does not, so ops-monitor's outbox check does not
    watch the `lead` schema.
````

````text new=docs/deploy/ENV_SOURCE_MATRIX.md#925
11. **The two `EDGE_RELAY_SCHEMAS` defaults used to differ and no longer do (R1.2b):** both are the same 29 names, `lead` included, so
    ops-monitor's outbox check watches the `lead` schema.
````

`docs/deploy/env-reference.md`, line 330:

````text old=docs/deploy/env-reference.md#330
two compose defaults: edge-relay 28 schemas (incl. `lead`); ops-monitor 27 (no `lead`) - see cross-slice note
````

````text new=docs/deploy/env-reference.md#330
one compose default of 29 schema names (incl. `lead`) in both edge-relay and ops-monitor, kept equal by `tests/infra/test_ops_monitor_compose_env.py`
````

Lines 828-829:

````text old=docs/deploy/env-reference.md#828
- *Real, small.* Two compose defaults for `EDGE_RELAY_SCHEMAS` differ on INT: edge-relay's list includes `lead`
  (`INT:compose.prod.yml:710`), ops-monitor's does not (`:770`), so its outbox check does not cover the `lead` schema.
````

````text new=docs/deploy/env-reference.md#828
- *Fixed in R1.2b.* The two compose defaults for `EDGE_RELAY_SCHEMAS` are now the same 29 names, `lead` included, so
  ops-monitor's outbox check covers the `lead` schema (`tests/infra/test_ops_monitor_compose_env.py` keeps them equal).
````

- [ ] **Step 4: Run it and see it pass**

Run: `uv run pytest tests/infra/test_ops_monitor_compose_env.py -q -p no:cacheprovider`
Expected: `9 passed` (observed against the patched compose file).

Run: `grep -c 'customer,deal,lead,project' infrastructure/edge/compose.prod.yml`
Expected: `2`.

Run: `grep -nE "27 names|ops-monitor 27|ops-monitor.s does not" docs/deploy/ENV_SOURCE_MATRIX.md docs/deploy/env-reference.md`
Expected: no output (five lines before the edit).

Run: `uv run pytest tests/infra -q -p no:cacheprovider`
Expected: the whole infra suite passes (inference): the other tests that read this file
(`test_compose_memory_budget.py`, `test_ops_monitor_dlq_queue_sync.py`) look at services and queues, not at this default.

Run: `uv run ruff check tests/infra && uv run ruff format --check tests/infra`
Expected: `All checks passed!`.

- [ ] **Step 5: Commit**

```bash
git add infrastructure/edge/compose.prod.yml tests/infra/test_ops_monitor_compose_env.py docs/deploy/ENV_SOURCE_MATRIX.md docs/deploy/env-reference.md
git commit -m "ops-monitor: watch the lead outbox, the same schema list edge-relay drains"
```

**OWNER RUN (part of O-11, after the R1.2b deploy):** `make edge-status HOST=... CONFIRM=...` lists an `outbox.lead`
check beside the others. The default only applies when the box's `.env` does not set `EDGE_RELAY_SCHEMAS` (it is
optional and not in `.env.example`): read-only, `grep -c '^EDGE_RELAY_SCHEMAS=' <the box's .env>` should print `0`; if it
prints `1`, ops-monitor watches whatever that value lists, so check that `lead` is in it and tell the author.

---

### Task R1.2.14: Two dead dev gateway rows go, and the CRM prefixes are pinned equal

**Deploy:** R1.2b. **Spec:** `S§5.3.7`, class-1 row 1, `AC-1.2.7`. **Registry:** `C71`. Strike-able (O-15): the change
rebuilds and recreates app-gateway for no runtime effect (production loads `routes.prod.json`); if struck, R3.2's gateway
commit removes the rows and adds the same test.

**Files:**
- Modify: `services/app-gateway/routes.json:79-80`
- Modify: `tests/infra/test_route_table_parity.py` (append one test)
- Modify: `docs/architecture.md:143-145`
- Test: `tests/infra/test_route_table_parity.py`

**Interfaces:**
- Consumes: `DEV_ROUTES`, `PROD_ROUTES` and `_routes(path) -> list[dict[str, object]]` of the test module
  (`tests/infra/test_route_table_parity.py:34-45`); the route tables `services/app-gateway/routes.json` and
  `infrastructure/edge/routes.prod.json`, which stays untouched.
- Produces: `test_the_crm_prefixes_are_identical_in_dev_and_prod`. The CRM prefixes are then `/api/v1/customers`,
  `/api/v1/contacts`, `/api/v1/deals` and `/api/v1/leads` in both tables. The rule for every later release: add a CRM
  prefix to both tables in one commit.

The two rows route `/api/v1/campaigns` to port 8132 and `/api/v1/tickets` to port 8133 under product `crm`. No service
of either name exists, nothing else mentions those ports, the production table has neither prefix and no SPA calls
them (`tests/infra/test_frontend_paths_are_routed.py` holds every literal `apiRequest` to the production table).

- [ ] **Step 1: Write the failing test**

Append to `tests/infra/test_route_table_parity.py` (after the last test, leaving two blank lines):

```python


def test_the_crm_prefixes_are_identical_in_dev_and_prod() -> None:
    """The dev table once routed `/api/v1/campaigns` and `/api/v1/tickets` to ports no service ever
    listened on, and the prod table never had them. A CRM prefix in one table and not the other is
    a route that is dead where it is listed or missing where it is needed."""

    def crm_prefixes(path: Path) -> set[str]:
        return {str(route["prefix"]) for route in _routes(path) if route.get("product") == "crm"}

    dev, prod = crm_prefixes(DEV_ROUTES), crm_prefixes(PROD_ROUTES)
    assert dev == prod, f"in dev only: {sorted(dev - prod)}; in prod only: {sorted(prod - dev)}"
    assert dev, "no CRM prefixes found: this test would pass vacuously"
```

- [ ] **Step 2: Run it and see it fail**

Run: `uv run pytest tests/infra/test_route_table_parity.py -q -p no:cacheprovider`
Expected: FAIL, `1 failed, 18 passed` (observed): `AssertionError: in dev only: ['/api/v1/campaigns',
'/api/v1/tickets']; in prod only: []`.

- [ ] **Step 3: Write the minimal implementation**

Proof of no live user (paste the output in the pull request):

Run: `ls services | grep -c "campaigns\|tickets"` Expected: `0`.
Run: `grep -rn "8132\|8133" services tools infrastructure e2e Makefile .github frontend/*/src` Expected: exactly the two
lines `services/app-gateway/routes.json:79` and `:80`.

In `services/app-gateway/routes.json` delete these two lines (79-80), between the `/api/v1/deals` and `/api/v1/leads`
rows:

```json
    { "prefix": "/api/v1/campaigns", "upstream": "http://localhost:8132", "product": "crm" },
    { "prefix": "/api/v1/tickets", "upstream": "http://localhost:8133", "product": "crm" },
```

In `docs/architecture.md` replace lines 143-145

```text
Five further prefixes route to services that do not exist yet — `/leave` (8122), `/payroll`
(8123), `/recruit` (8124), `/campaigns` (8132) and `/tickets` (8133). They are either reserved
intent or dead entries; either way a request to one reaches nothing.
```

with

```text
Two further prefixes route to services that do not exist yet — `/payroll` (8123) and `/recruit`
(8124). They are either reserved intent or dead entries; either way a request to one reaches
nothing. (`/leave` (8122) is served by `leave`, and the `/campaigns` and `/tickets` rows are gone.)
```

- [ ] **Step 4: Run it and see it pass**

Run: `uv run pytest tests/infra/test_route_table_parity.py -q -p no:cacheprovider`
Expected: `19 passed` (observed against the patched table).

Run: `grep -n 'api/v1/campaigns\|api/v1/tickets' services/app-gateway/routes.json`
Expected: no output (`AC-1.2.7`).

Run: `uv run pytest tests/infra/test_frontend_paths_are_routed.py -q -p no:cacheprovider && cd services/app-gateway && uv run pytest tests -q -p no:cacheprovider`
Expected: both pass (inference: several of the gateway's tests load the real dev table; none names the two prefixes).

Run: `cd services/app-gateway && uv run mypy app && cd ../.. && uv run ruff check tests/infra && uv run ruff format --check tests/infra`
Expected: `Success: no issues found`, `All checks passed!`.

- [ ] **Step 5: Commit**

```bash
git add services/app-gateway/routes.json tests/infra/test_route_table_parity.py docs/architecture.md
git commit -m "Gateway: remove two dev rows that route to services that never existed, and pin the CRM prefixes equal"
```

**OWNER RUN:** the gateway is recreated by the deploy, so the stop conditions carry a gateway probe (O-11): the 360 read
through app-gateway still answers 200.

---

### Task R1.2.15: `leads.phone_index` stops being computed

**Deploy:** R1.2b. **Spec:** `S§5.2`, `DD-15`, `R1.2-11`, `AC-1.2.4`. **Registry:** `B40`, `L2`. Strike-able (O-15): if
struck, `create_lead` keeps computing the index and nothing else here changes.

**Files:**
- Modify: `services/lead/app/models.py:95` (`phone_index` becomes `deferred`)
- Modify: `services/lead/app/services.py:465,468,496`
- Create: `services/lead/tests/test_phone_index_dormant.py`
- Test: `services/lead/tests/test_phone_index_dormant.py`

**Interfaces:**
- Consumes: `selectors.leads_stmt(ctx: ScopeContext) -> Select[Any]` (the statement every lead read goes through);
  `services.create_lead(session, ctx, *, full_name, company, email, phone, source, owner_id, notes, ...)`;
  `services.blind_index(plaintext, *, kind, settings)` (imported into `services` from `platform_core.pii`);
  `tests.helpers.ctx_for`; the fixtures `session`, `org_id`.
- Produces: `Lead.phone_index` mapped `deferred=True`, so no statement the service issues names the column; `create_lead`
  computes the email blind index (it needs it for the duplicate check) and **no phone blind index**. The column and its
  migration stay. Phone-based duplicate detection (R3.7) resumes writing it with an owner-run backfill (`H8`) for the leads
  captured in between; if R3.7 drops phone dedupe instead, one change drops the attribute and the column, safe only
  because the running code no longer references it.

The column was written on every capture with a phone and read by nothing: no selector, no index, no test. Each such lead
minted a never-used equality value under the platform-wide blind-index key. Two-step on purpose (stop the write and
unmap the column, drop later): a column drop and a code change cannot be deployed atomically, and the box's rollback is
the whole ref.

- [ ] **Step 1: Write the failing test**

Create `services/lead/tests/test_phone_index_dormant.py`:

```python
"""`leads.phone_index` is dormant: the column exists, nothing computes it and nothing reads it.

It was written on every capture with a phone and read by no selector and no index, so each lead
minted a never-used equality value under the platform-wide blind-index key. R1.2b stops computing it
and maps the column `deferred`, so no statement this service issues names it. The column stays:
phone-based duplicate detection (R3.7) resumes writing it, with an owner-run backfill for the leads
captured in between. If R3.7 drops phone dedupe instead, one change removes the attribute and drops
the column; it is safe only because the running code no longer references the column.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

from app import selectors, services
from platform_core.auth import Scope
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration


def test_select_lead_does_not_name_phone_index() -> None:
    """The compiled statements the service reads leads with. `email_index` is asserted present so
    the check is not vacuous: its sibling column is still selected."""
    ctx = ctx_for(uuid.uuid4(), "crm.lead.lead.read", Scope.ORG)
    list_sql = str(selectors.leads_stmt(ctx).compile(dialect=postgresql.dialect()))
    assert "email_index" in list_sql
    assert "phone_index" not in list_sql


async def test_a_lead_created_with_a_phone_stores_no_index(session: Any, org_id: uuid.UUID) -> None:
    ctx = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
    async with session.begin():
        lead = await services.create_lead(
            session,
            ctx,
            full_name="Dormant Index",
            company=None,
            email=None,
            phone="+919876543210",
            source="manual",
            owner_id=uuid.uuid4(),
            notes="",
        )
    async with session.begin():
        stored = (
            await session.execute(
                text("SELECT phone_index, phone_masked, phone_enc FROM lead.leads WHERE id = :id"),
                {"id": lead.id},
            )
        ).one()
    assert stored.phone_index is None
    # The phone itself is still captured, masked and sealed: only the unused index is gone.
    assert stored.phone_masked is not None
    assert stored.phone_enc is not None


async def test_create_lead_never_computes_a_phone_blind_index(
    session: Any, org_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    kinds: list[str] = []
    real = services.blind_index

    def spy(plaintext: str, *, kind: str, settings: Any) -> str:
        kinds.append(kind)
        return real(plaintext, kind=kind, settings=settings)

    monkeypatch.setattr(services, "blind_index", spy)
    ctx = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
    async with session.begin():
        await services.create_lead(
            session,
            ctx,
            full_name="Both Details",
            company=None,
            email="both.details@example.com",
            phone="+919876543211",
            source="manual",
            owner_id=uuid.uuid4(),
            notes="",
        )
    assert kinds == ["email"]
```

- [ ] **Step 2: Run it and see it fail**

Run: `cd services/lead && uv run pytest tests/test_phone_index_dormant.py -q -p no:cacheprovider` (needs `make infra` and
`make migrate`)
Expected: FAIL, `3 failed` (inference for the two database-backed tests; the first observed against HEAD):
`test_select_lead_does_not_name_phone_index` fails with `assert 'phone_index' not in 'SELECT lead.leads...,
lead.leads.phone_index, lead.leads.source, ...'`; `test_a_lead_created_with_a_phone_stores_no_index` fails with
`assert '<the stored value>' is None` (a 64-character blind-index digest is there); and
`test_create_lead_never_computes_a_phone_blind_index` fails with `assert ['email', 'phone'] == ['email']`.

- [ ] **Step 3: Write the minimal implementation**

`services/lead/app/models.py`, line 95: replace

```python
    phone_index: Mapped[str | None] = mapped_column(String(64))
```

with

```python
    #: Dormant: nothing has computed it since R1.2b and nothing reads it (no selector, no index).
    #: The column stays, because phone-based duplicate detection (R3.7) resumes writing it, with
    #: an owner-run backfill for the leads captured in between. `deferred` keeps it out of every
    #: statement this service issues, so a later change can drop the column and this attribute
    #: together without breaking the code that is running.
    phone_index: Mapped[str | None] = mapped_column(String(64), deferred=True)
```

`services/lead/app/services.py`, in `create_lead`, three deletions. Replace lines 463-468

```python
    phone_masked: str | None = None
    phone_enc: bytes | None = None
    phone_index: str | None = None
    if phone:
        phone_enc, phone_masked = seal(phone, kind="phone", settings=cfg)
        phone_index = blind_index(phone, kind="phone", settings=cfg)
```

with

```python
    phone_masked: str | None = None
    phone_enc: bytes | None = None
    if phone:
        phone_enc, phone_masked = seal(phone, kind="phone", settings=cfg)
```

and in the `Lead(...)` constructor remove the line `        phone_index=phone_index,` (line 496, between `phone_enc=phone_enc,` and `source=source,`).

- [ ] **Step 4: Run it and see it pass**

Run: `cd services/lead && uv run pytest tests/test_phone_index_dormant.py -q -p no:cacheprovider`
Expected: `3 passed` (the compile test observed passing against the edited app; the other two inference).

Run: `grep -rn 'phone_index' services/lead/app/services.py`
Expected: no output (`AC-1.2.4`).

Run: `cd services/lead && uv run pytest tests -q -p no:cacheprovider && uv run mypy app`
Expected: the whole lead suite passes (inference) and `Success: no issues found` (observed in a scratch copy of the app).

Run: `make migration-check`
Expected: passes: the model still maps the column, so there is nothing to generate.

Run: `uv run ruff check services/lead && uv run ruff format --check services/lead`
Expected: `All checks passed!`.

- [ ] **Step 5: Commit**

```bash
git add services/lead/app/models.py services/lead/app/services.py services/lead/tests/test_phone_index_dormant.py
git commit -m "Lead: stop computing a phone blind index nothing reads, and keep the column out of every statement"
```

**Held for later, not built here (the contractive step, `S§5.2` B2).** It ships only if R3.7 drops phone duplicate
detection instead of resuming the index; the registered default (`B40`, `L2`) is the opposite, and then the drop is
cancelled rather than deferred. It is one change in the first lead migration after that decision, safe only because the
running code no longer references the column:

```
revision:      <generated by make makemigration>      down_revision: <the lead head at that time; today 91dd3ca663fd>
upgrade():     SET LOCAL lock_timeout = '5s';   ALTER TABLE lead.leads DROP COLUMN phone_index;
downgrade():   ALTER TABLE lead.leads ADD COLUMN phone_index VARCHAR(64) NULL;
```

The downgrade does not restore values (derived data; `H8` rebuilds them). It may not ship until this task has been live for
one release and the owner no longer wants to roll back to code from before it, because that code selects the column.
Row-level security, grants, `TENANT_TABLES` and `tools/purge_test_orgs.py` are untouched by a column drop. This task adds
no revision: the three heads stay as they are.

---

### Task R1.2.16: The unused `STATUSES` tuple goes

**Deploy:** R1.2b. **Spec:** class-1 row 3, `AC-1.2.8`.

**Files:**
- Modify: `services/lead/app/models.py:64-67` (the tuple and its comment) and the comment at `:99` (line numbers at HEAD;
  Task R1.2.15 adds a few lines above `:95`)
- Create: `services/lead/tests/test_status_vocabulary.py`
- Test: `services/lead/tests/test_status_vocabulary.py`

**Interfaces:**
- Consumes: `app.domain.STATUS_ORDER` (the rules' one list, `services/lead/app/domain.py:17`) and `app.schemas.LeadStatus`
  (the wire's, `services/lead/app/schemas.py:27`).
- Produces: a model module with no `STATUSES`; a test that pins the agreement of the two real lists.

`models.STATUSES` is a third copy of the status vocabulary that nothing reads
(`grep -rnw STATUSES services/lead` finds the definition and one comment). The two real lists stay, and a test now holds
them equal, which is the property that matters.

- [ ] **Step 1: Write the failing test**

Create `services/lead/tests/test_status_vocabulary.py`:

```python
"""A lead's status vocabulary has one home for the rules (`domain.STATUS_ORDER`) and one for the
wire (`schemas.LeadStatus`). A third copy, a tuple in `models.py` that nothing read, was deleted:
what stays true is that the two real ones agree, and that the model does not grow another.
"""

from __future__ import annotations

from typing import get_args

from app import domain, models, schemas


def test_the_rules_and_the_wire_agree_on_the_status_vocabulary() -> None:
    assert tuple(get_args(schemas.LeadStatus)) == domain.STATUS_ORDER


def test_the_model_carries_no_third_copy_of_the_vocabulary() -> None:
    assert not hasattr(models, "STATUSES")
```

- [ ] **Step 2: Run it and see it fail**

Run: `cd services/lead && uv run pytest tests/test_status_vocabulary.py -q -p no:cacheprovider`
Expected: FAIL, `1 failed, 1 passed` (observed against HEAD): `test_the_model_carries_no_third_copy_of_the_vocabulary`
with `assert not True`. The agreement test passes already, as it should.

- [ ] **Step 3: Write the minimal implementation**

Proof of no live user (paste the output in the pull request):

Run: `grep -rnw STATUSES services/lead`
Expected: exactly `services/lead/app/models.py:66` (the definition) and the comment at `:99`.

In `services/lead/app/models.py` delete the tuple and its two comment lines (and one of the three blank lines around it,
so two remain before `class Lead`):

```python
#: The lifecycle, in order. `domain.can_transition` is the only enforcement point — this
#: constant lives here because the model docstring above is where a reader looks first.
STATUSES: tuple[str, ...] = ("new", "working", "qualified", "disqualified", "converted")
```

and replace the comment above `status` (`:99`)

```python
    #: new | working | qualified | disqualified | converted. See `STATUSES`.
```

with

```python
    #: new | working | qualified | disqualified | converted. `domain.STATUS_ORDER` is the one
    #: list of them and `domain.can_transition` the only enforcement point.
```

- [ ] **Step 4: Run it and see it pass**

Run: `cd services/lead && uv run pytest tests/test_status_vocabulary.py -q -p no:cacheprovider`
Expected: `2 passed` (observed).

Run: `grep -rnw STATUSES services/lead`
Expected: no output (`AC-1.2.8`).

Run: `cd services/lead && uv run mypy app && cd ../.. && uv run ruff check services/lead && uv run ruff format --check services/lead`
Expected: `Success: no issues found`, `All checks passed!`, `already formatted` (observed in a scratch copy of the app).

- [ ] **Step 5: Commit**

```bash
git add services/lead/app/models.py services/lead/tests/test_status_vocabulary.py
git commit -m "Lead: delete the status tuple nothing reads and pin that the two real vocabularies agree"
```

---

### Task R1.2.17: `activities_for_deal` moves to the tests

**Deploy:** R1.2b. **Spec:** class-1 row 4, `AC-1.2.8`.

**Files** (line numbers at HEAD, before Task R1.2.8 adds lines to `test_write_routes.py`):
- Modify: `services/deal/app/selectors.py:138-151` (delete the function)
- Modify: `services/deal/tests/helpers.py` (imports and a new helper)
- Modify: `services/deal/tests/test_write_routes.py:28,332,360,381,410`, `test_activity_append_only.py:21,32,42`,
  `test_seed.py:19,83`
- Test: the three modified test modules

**Interfaces:**
- Consumes: `app.models.Activity`; `selectors.activities_stmt` and `selectors.all_activities` stay (they apply the
  caller's scope and are what the service reads timelines with).
- Produces: `tests.helpers.activities_for_deal(session: AsyncSession, org_id: uuid.UUID, deal_id: uuid.UUID) ->
  list[Activity]` with an explicit organization filter, the same rows the selector returned (newest first). `selectors`
  has no `activities_for_deal`.

The selector was an unscoped read inside the module that holds the scoped ones, used by tests only (its own docstring
said "the write path and the seed checks"; the write path never called it). Nothing in the app imports it.

- [ ] **Step 1: Repoint the seven call sites at the helper that does not exist yet**

In `services/deal/tests/test_activity_append_only.py` replace `from tests.helpers import ctx_for` with

```python
from tests.helpers import activities_for_deal, ctx_for
```

and replace both `selectors.activities_for_deal(` (lines 32 and 42) with `activities_for_deal(`:

```python
            before = len(await activities_for_deal(session, org_id, deal.id))
```

```python
        after = await activities_for_deal(session, org_id, deal.id)
```

In `services/deal/tests/test_seed.py` replace `from tests.helpers import ctx_for` with
`from tests.helpers import activities_for_deal, ctx_for` and line 83

```python
        timeline = await selectors.activities_for_deal(session, org_id, deal_id)
```

with

```python
        timeline = await activities_for_deal(session, org_id, deal_id)
```

In `services/deal/tests/test_write_routes.py` replace `from tests.helpers import principal_for` with
`from tests.helpers import activities_for_deal, principal_for`, and replace each of the four
`rows = await selectors.activities_for_deal(session, org_id, data["deal_x_id"])` (the calls in `test_a_writer_logs_a_touch`,
`test_a_reader_cannot_log_a_touch`, `test_an_own_writer_cannot_log_against_another_reps_deal` and
`test_an_invalid_body_is_refused`) with `rows = await activities_for_deal(session, org_id, data["deal_x_id"])`, keeping
the deal key each already uses. Every one of the three files still uses `selectors` for other reads (`get_deal`,
`all_deals`), so no import is left unused.

- [ ] **Step 2: Run them and see them fail**

Run: `cd services/deal && uv run pytest tests/test_activity_append_only.py tests/test_seed.py tests/test_write_routes.py -q -p no:cacheprovider`
Expected: FAIL at collection, `3 errors` (inference: the modules do not import): `ImportError: cannot import name
'activities_for_deal' from 'tests.helpers'`.

- [ ] **Step 3: Write the minimal implementation**

In `services/deal/tests/helpers.py` replace the imports

```python
import uuid

from platform_core.auth import Principal, Scope, ScopeContext
```

with

```python
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Activity
from platform_core.auth import Principal, Scope, ScopeContext
```

and append at the end of the file:

```python


async def activities_for_deal(
    session: AsyncSession, org_id: uuid.UUID, deal_id: uuid.UUID
) -> list[Activity]:
    """One deal's timeline, newest first, for assertions. It lived in `app/selectors.py` until
    nothing but tests called it; the service reads timelines through `selectors.activities_stmt`,
    which applies the caller's scope."""
    stmt = (
        select(Activity)
        .where(
            Activity.organization_id == org_id,
            Activity.deal_id == deal_id,
            Activity.deleted_at.is_(None),
        )
        .order_by(Activity.occurred_at.desc())
    )
    return list((await session.execute(stmt)).scalars().all())
```

Proof of no live user (paste the output in the pull request): `grep -rn activities_for_deal services libs tools e2e`
before this step prints the definition and the seven call sites, no app caller.

In `services/deal/app/selectors.py` delete the function and the two blank lines after it (lines 138-153), so
`all_activities` follows `activities_stmt`'s tail directly:

```python
async def activities_for_deal(
    session: AsyncSession, org_id: uuid.UUID, deal_id: uuid.UUID
) -> list[Activity]:
    """One deal's timeline, newest first. Used by the write path and the seed checks."""
    stmt = (
        select(Activity)
        .where(
            Activity.organization_id == org_id,
            Activity.deal_id == deal_id,
            Activity.deleted_at.is_(None),
        )
        .order_by(Activity.occurred_at.desc())
    )
    return list((await session.execute(stmt)).scalars().all())
```

- [ ] **Step 4: Run them and see them pass**

Run: `cd services/deal && uv run pytest tests/test_activity_append_only.py tests/test_seed.py tests/test_write_routes.py -q -p no:cacheprovider`
Expected: all pass (inference: the helper returns the rows the selector did).

Run: `grep -rn activities_for_deal services/deal/app`
Expected: no output (`AC-1.2.8`).

Run: `cd services/deal && uv run mypy app && uv run pytest tests -q -p no:cacheprovider && cd ../.. && uv run ruff check services/deal && uv run ruff format --check services/deal`
Expected: `Success: no issues found` (observed on a scratch copy of the app: `select`, `AsyncSession` and `Activity` are
still used by the rest of `selectors.py`), the whole deal suite passes (inference), `All checks passed!` and `already
formatted` (observed for `helpers.py` and the three modules).

- [ ] **Step 5: Commit**

```bash
git add services/deal/app/selectors.py services/deal/tests/helpers.py services/deal/tests/test_activity_append_only.py services/deal/tests/test_seed.py services/deal/tests/test_write_routes.py
git commit -m "Deal: move the unscoped timeline read out of the selectors, where only tests used it"
```

---

### Task R1.2.18: Three `bin/cron` relay copies go

**Deploy:** R1.2b. **Spec:** class-1 row 7, `DD-13`, `AC-1.2.8`. **Registry:** `F7` (the `bin/cron` clause).

**Files:**
- Delete: `services/customer/bin/cron`, `services/deal/bin/cron`, `services/lead/bin/cron`
- Create: `tests/infra/test_crm_entrypoints.py`
- Modify: `docs/architecture.md:351-352`
- Test: `tests/infra/test_crm_entrypoints.py`

**Interfaces:**
- Consumes: `infrastructure/edge/compose.prod.yml` (read as YAML: services whose `image` is
  `opsgrids-edge/<service>` and their `command` list); `services/<svc>/bin/relay`.
- Produces: `CRM_SERVICES = ("customer", "deal", "lead")` and three tests (below). The entrypoints become `bin/api`,
  `bin/relay` and, for customer and deal, `bin/worker`; lead has `bin/api` and `bin/relay`.

All three files are the same three lines, `exec python -m app.relay`: a copy of the real `bin/relay` that none of the
services needs. No compose service, `make` target or test runs them (`make cron` runs `python -m app.cron`, which these
services lack; `tests/infra/test_sweeps_run_somewhere.py` reads only crons that run `app.cron`). A `bin/cron` that starts
the relay is a trap: whoever runs "the cron" of a CRM service starts a second relay on the same outbox. The test checks
that the files are not relay copies, which is what matters; it does not forbid a future real sweep.

- [ ] **Step 1: Write the failing test**

Create `tests/infra/test_crm_entrypoints.py`:

```python
"""The CRM services' entrypoints: a relay of their own, and no second copy of it under another name.

`customer`, `deal` and `lead` each carried a `bin/cron` that was a byte-for-byte copy of the
scaffold default, `exec python -m app.relay`. None of them has a scheduled sweep (there is no
`app/cron.py`), and the outbox relay already has its own entrypoint, `bin/relay`, and runs in the
shared `edge-relay` container on the edge. A `bin/cron` that starts the relay is a trap: whoever
runs "the cron" of one of these services starts a second relay on the same outbox. The files were
removed; this pins that they do not come back as relay copies.

It does not say that a CRM service may never have a `bin/cron`: one with a real sweep runs
`app.cron`, and the relay-copy check lets that through. The other services' `bin/cron` files
are not this test's business.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "infrastructure/edge/compose.prod.yml"

CRM_SERVICES = ("customer", "deal", "lead")

_EXEC_RELAY = re.compile(r"^\s*exec\s+python\s+-m\s+app\.relay\b", re.MULTILINE)


@pytest.mark.parametrize("service", CRM_SERVICES)
def test_no_crm_service_has_a_bin_cron_that_is_a_second_copy_of_the_relay(service: str) -> None:
    cron = REPO_ROOT / "services" / service / "bin" / "cron"
    is_a_relay_copy = cron.is_file() and _EXEC_RELAY.search(cron.read_text(encoding="utf-8"))
    assert not is_a_relay_copy, (
        f"services/{service}/bin/cron starts the outbox relay: the relay is bin/relay, and "
        "this service has no scheduled sweep. Delete the file, or point it at a real app/cron.py."
    )


@pytest.mark.parametrize("service", CRM_SERVICES)
def test_every_crm_service_keeps_its_own_relay_entrypoint(service: str) -> None:
    relay = REPO_ROOT / "services" / service / "bin" / "relay"
    assert relay.is_file(), f"services/{service}/bin/relay is gone: the outbox has no entrypoint"
    assert _EXEC_RELAY.search(relay.read_text(encoding="utf-8")), service


def test_no_crm_container_runs_a_bin_cron() -> None:
    """Nothing in the production compose file starts `./bin/cron` from a CRM image, so removing the
    files changes no container. The relay for all three runs in `edge-relay`, by schema name."""
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))["services"]
    for name, spec in compose.items():
        image = str(spec.get("image", ""))
        if image not in {f"opsgrids-edge/{service}" for service in CRM_SERVICES}:
            continue
        command = spec.get("command") or []
        words = command if isinstance(command, list) else str(command).split()
        assert "./bin/cron" not in words, f"compose service {name} runs ./bin/cron from {image}"
```

- [ ] **Step 2: Run it and see it fail**

Run: `uv run pytest tests/infra/test_crm_entrypoints.py -q -p no:cacheprovider`
Expected: FAIL, `3 failed, 4 passed` (observed): the three `test_no_crm_service_has_a_bin_cron_that_is_a_second_copy_of_the_relay`
cases, each with `AssertionError: services/<service>/bin/cron starts the outbox relay: the relay is bin/relay, and this
service has no scheduled sweep. Delete the file, or point it at a real app/cron.py.`

- [ ] **Step 3: Write the minimal implementation**

Proof of no live user (paste the output in the pull request):

Run: `grep -rn "customer/bin/cron\|deal/bin/cron\|lead/bin/cron" . --include='*' | grep -v node_modules | grep -v docs/superpowers`
Expected: no output.
Run: `grep -n "bin/cron" infrastructure/edge/compose.prod.yml | grep -n "customer\|deal\|lead"`
Expected: no output.

Delete the three files:

```bash
git rm services/customer/bin/cron services/deal/bin/cron services/lead/bin/cron
```

In `docs/architecture.md` (lines 351-352) replace

```text
**Three entrypoints** per service: `bin/api` (uvicorn), `bin/worker` (consumers), `bin/cron`
(scheduled sweeps). Each is its own process, so a slow broker cannot block an API request.
```

with

```text
**Three entrypoints** per service: `bin/api` (uvicorn), `bin/worker` (consumers), `bin/cron`
(scheduled sweeps). Each is its own process, so a slow broker cannot block an API request.
`customer`, `deal` and `lead` have no `bin/cron`: they run no scheduled sweep, and the copy each
once carried only started the outbox relay a second time (the relay is `bin/relay`).
```

- [ ] **Step 4: Run it and see it pass**

Run: `uv run pytest tests/infra/test_crm_entrypoints.py -q -p no:cacheprovider`
Expected: `7 passed` (observed against a tree without the three files).

Run: `ls services/customer/bin/cron services/deal/bin/cron services/lead/bin/cron 2>/dev/null`
Expected: no output (`AC-1.2.8`).

Run: `uv run pytest tests/infra/test_sweeps_run_somewhere.py tests/infra/test_sweeps_in_api.py -q -p no:cacheprovider`
Expected: pass (they read only crons that run `app.cron`, and the services that keep theirs).

Run: `uv run ruff check tests/infra && uv run ruff format --check tests/infra`
Expected: `All checks passed!`.

- [ ] **Step 5: Commit**

```bash
git add tests/infra/test_crm_entrypoints.py docs/architecture.md
git commit -m "CRM services: remove three bin/cron files that only started the relay a second time"
```

(`git rm` has already staged the three deletions.)

---

### Task R1.2.19: The `sales` entitlement alias goes

**Deploy:** R1.2b (the shared package ships inside each SPA bundle at its next publish). **Spec:** class-1 row 2,
`AC-1.2.8`. **Registry:** `G15`.

**Files:**
- Modify: `frontend/opsgrids-client/src/platform/platform.test.tsx:119-127`
- Modify: `frontend/opsgrids-client/src/platform/catalogue.ts:11-14` (doc comment) and `:47` (the CRM entry)
- Test: `frontend/opsgrids-client/src/platform/platform.test.tsx`

**Interfaces:**
- Consumes: `PLATFORM_PRODUCTS: readonly PlatformProduct[]` and the `usePlatformModules` hook that resolves a user's
  entitlements and grants to the product entries; the test file's `Wrapper`, `ModulesProbe` and `meWith`.
- Produces: the CRM entry answers to `crm` only (`entitlementKeys: ["crm"]`). `operations` still also answers to
  `inventory`.

No service mints `ent: sales` and no grant code starts `sales.` (`grep -rn '"code": "sales\.' libs/contracts/permissions`
prints nothing; `storefront.sales.*` is a *module* inside `storefront`, `services/storefront/app/catalogue.py:195-214`);
ADR-0017: there is no `sales` product. The alias resolved an entitlement nothing grants.

- [ ] **Step 1: Write the failing test**

In `frontend/opsgrids-client/src/platform/platform.test.tsx` replace the test at lines 119-127

```tsx
  it("resolves the sales entitlement to the CRM entry", () => {
    render(
      <Wrapper user={meWith()} grants={["sales.lead.record.read:ORG"]} entitlements={["sales"]}>
        <ModulesProbe />
      </Wrapper>,
    );
    expect(screen.getByText("CRM — https://revenue.opsgrids.com")).toBeTruthy();
  });
```

with

```tsx
  it("a sales entitlement no longer resolves to CRM", () => {
    render(
      <Wrapper user={meWith()} grants={["sales.lead.record.read:ORG"]} entitlements={["sales"]}>
        <ModulesProbe />
      </Wrapper>,
    );
    expect(screen.queryByText(/^CRM/)).toBeNull();
  });
```

- [ ] **Step 2: Run it and see it fail**

Run: `cd frontend/opsgrids-client && pnpm exec vitest run src/platform/platform.test.tsx`
Expected: FAIL, `1 failed | 12 passed (13)` (observed under Node 22): `usePlatformModules > a sales entitlement no longer resolves
to CRM` with `AssertionError: expected <li></li> to be null`.

- [ ] **Step 3: Write the minimal implementation**

Proof of no live user (paste the output in the pull request): `grep -rn '"code": "sales\.' libs/contracts/permissions`
prints nothing.

In `frontend/opsgrids-client/src/platform/catalogue.ts` replace the comment lines 11-14

```ts
 * Two product keys fold into one entry on purpose:
 * - `crm` also answers to the `sales` entitlement/grant prefix some services still mint under.
 * - `operations` also answers to `inventory` — Operations and Inventory are one console
 *   (`ops.opsgrids.com`), not two nav entries.
```

with

```ts
 * One product key folds into another entry on purpose: `operations` also answers to
 * `inventory` — Operations and Inventory are one console (`ops.opsgrids.com`), not two nav
 * entries.
```

and the CRM entry (line 47)

```ts
  { key: "crm", displayName: "CRM", iconKey: "briefcase", entitlementKeys: ["crm", "sales"] },
```

with

```ts
  { key: "crm", displayName: "CRM", iconKey: "briefcase", entitlementKeys: ["crm"] },
```

- [ ] **Step 4: Run it and see it pass**

Run: `cd frontend && pnpm --filter @opsgrids/client typecheck && pnpm --filter @opsgrids/client lint && pnpm --filter @opsgrids/client test`
Expected: clean, then `Test Files 57 passed (57)`, `Tests 1109 passed (1109)` (observed under Node 22 against a scratch copy of the
package with these edits; `tsc --noEmit` and `eslint . --max-warnings=0` printed nothing).

Run: `grep -n sales frontend/opsgrids-client/src/platform/catalogue.ts`
Expected: no output (`AC-1.2.8`).

- [ ] **Step 5: Commit**

```bash
git add frontend/opsgrids-client/src/platform/catalogue.ts frontend/opsgrids-client/src/platform/platform.test.tsx
git commit -m "Client: the CRM product answers to the crm entitlement only, not a sales alias nothing grants"
```

The apps that bundle this package pick the change up at their next publish; nothing depends on it, so none is published
for it.

---

### Task R1.2.20: Seven dead `derive.ts` rollups and their orphans

**Deploy:** R1.2b (CRM SPA publish, O-12). **Spec:** class-1 row 5, `DD-5`, `AC-1.2.8`, `AC-1.2.9`. **Registry:** `G15`.
Strike-able (O-15): R3.4 deletes the same code when it rebuilds Overview and Deals.

**Files:**
- Modify (replace the whole file): `frontend/product-crm/src/data/derive.ts`
- Modify: `frontend/product-crm/src/data/stages.ts:40-42` (`OPEN_STAGES`) and `:56-59` (`isWon`)
- Create: `frontend/product-crm/src/data/derive.test.ts`
- Test: `frontend/product-crm/src/data/derive.test.ts`

**Interfaces:**
- Consumes: `isOpen`, `STAGES` from `data/stages.ts`; the types `AttentionItem`, `Deal`, `DealStage` of `@/api/types`;
  `Overview.tsx` (imports `needsAttention`) and `Deals.tsx` (imports `dealsByStage`).
- Produces: `derive.ts` exports exactly `openDeals(deals: readonly Deal[]): Deal[]`, `dealsByStage(deals: readonly Deal[]):
  Record<DealStage, Deal[]>` and `needsAttention(deals: readonly Deal[], today: string): AttentionItem[]`, signatures and
  behaviour unchanged. Removed: `pipelineValueMinor`, `weightedPipelineMinor`, `openCount`, `winRatePct`, `wonValueMinor`,
  `stageFunnel`, `dealsOwnedBy`, and from `stages.ts` `OPEN_STAGES` and `isWon` (their last users).

The Overview reads `GET /deals/summary` now, so these seven client-side copies of the KPIs and the funnel are dead:
`grep -rlw` for each name over `product-crm/src` finds `derive.ts` only, and there is no `derive.test.ts`. The three
survivors are kept (they serve `Overview.tsx` and `Deals.tsx`) and gain tests instead; R3.4 deletes them with the file.

- [ ] **Step 1: Write the failing test**

Create `frontend/product-crm/src/data/derive.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import type { Deal } from "@/api/types";
import * as derive from "./derive";
import { dealsByStage, needsAttention, openDeals } from "./derive";

/**
 * What is left of the deal rollups now that the Overview reads `GET /deals/summary`: the open-deal
 * filter, the board's grouping and the "needs attention" list. The KPIs and the funnel are the
 * server's, so the rollups that duplicated them are gone, and the export list is pinned so a dead
 * copy cannot quietly come back. (The rebuild of Overview and Deals deletes this file with
 * `derive.ts`.)
 */

function deal(over: Partial<Deal>): Deal {
  return {
    id: "deal-1",
    name: "Fleet telematics rollout",
    company_id: "comp-1",
    primary_contact_id: null,
    value_minor: 100_000,
    currency: "INR",
    stage: "lead",
    owner_id: "own-1",
    probability: 20,
    close_date: "2026-12-31",
    created_at: "2026-08-01T00:00:00Z",
    last_activity_at: null,
    source: "Referral",
    ...over,
  };
}

describe("the export list", () => {
  it("is the three rollups still in use", () => {
    expect(Object.keys(derive).sort()).toEqual(["dealsByStage", "needsAttention", "openDeals"]);
  });
});

describe("openDeals", () => {
  it("keeps the four open stages and drops the two closed ones", () => {
    const deals = [
      deal({ id: "a", stage: "lead" }),
      deal({ id: "b", stage: "negotiation" }),
      deal({ id: "c", stage: "closed_won" }),
      deal({ id: "d", stage: "closed_lost" }),
    ];
    expect(openDeals(deals).map((d) => d.id)).toEqual(["a", "b"]);
  });
});

describe("dealsByStage", () => {
  it("has a column for every stage, in board order, richest deal first", () => {
    const grouped = dealsByStage([
      deal({ id: "small", stage: "proposal", value_minor: 10 }),
      deal({ id: "big", stage: "proposal", value_minor: 99 }),
      deal({ id: "won", stage: "closed_won" }),
    ]);
    expect(Object.keys(grouped)).toEqual([
      "lead",
      "qualified",
      "proposal",
      "negotiation",
      "closed_won",
      "closed_lost",
    ]);
    expect(grouped.proposal.map((d) => d.id)).toEqual(["big", "small"]);
    expect(grouped.closed_won.map((d) => d.id)).toEqual(["won"]);
    expect(grouped.lead).toEqual([]);
  });
});

describe("needsAttention", () => {
  const today = "2026-09-20";

  it("orders overdue, then closing soon, then stale, and says why", () => {
    const items = needsAttention(
      [
        deal({ id: "stale", close_date: "2026-12-31", last_activity_at: "2026-08-30T00:00:00Z" }),
        deal({ id: "soon", close_date: "2026-09-25" }),
        deal({ id: "late", close_date: "2026-09-10" }),
      ],
      today,
    );
    expect(items.map((i) => [i.deal.id, i.reason, i.detail])).toEqual([
      ["late", "overdue", "Close date 10d overdue"],
      ["soon", "closing_soon", "Closes in 5d"],
      ["stale", "stale", "No activity in 21d"],
    ]);
  });

  it("says a deal closing today closes today, and ignores closed deals", () => {
    const items = needsAttention(
      [
        deal({ id: "today", close_date: today }),
        deal({ id: "won", stage: "closed_won", close_date: "2026-09-01" }),
      ],
      today,
    );
    expect(items.map((i) => [i.deal.id, i.detail])).toEqual([["today", "Closes today"]]);
  });

  it("puts the larger deal first within one reason", () => {
    const items = needsAttention(
      [
        deal({ id: "small", close_date: "2026-09-25", value_minor: 1 }),
        deal({ id: "large", close_date: "2026-09-26", value_minor: 9 }),
      ],
      today,
    );
    expect(items.map((i) => i.deal.id)).toEqual(["large", "small"]);
  });
});
```

- [ ] **Step 2: Run it and see it fail**

Run: `cd frontend/product-crm && pnpm exec vitest run src/data/derive.test.ts`
Expected: FAIL, `1 failed | 5 passed (6)` (observed): `the export list > is the three rollups still in use` with
`AssertionError: expected [ Array(10) ] to deeply equal [ 'dealsByStage', …(2) ]`. The five behaviour tests pass already:
they pin what the three survivors do today.

- [ ] **Step 3: Write the minimal implementation**

Proof of no live user (paste the output in the pull request): for each of `pipelineValueMinor`, `weightedPipelineMinor`,
`openCount`, `winRatePct`, `wonValueMinor`, `stageFunnel`, `dealsOwnedBy`, `isWon`: `grep -rnw <name> frontend/product-crm/src`
prints one line, its definition in `derive.ts` (or `stages.ts` for `isWon`); `OPEN_STAGES` prints its definition and the two
uses inside the removed `stageFunnel` and import line.

Replace the whole of `frontend/product-crm/src/data/derive.ts` with (the diff is deletions plus the header comment and the
import lines; `openDeals`, `dealsByStage`, `needsAttention` and the private `daysBetween` are byte-for-byte as they were):

```ts
/**
 * What is left of the pure rollups over the deal list: the open-deal filter, the kanban columns and
 * the "needs attention" list. The Overview's KPIs and funnel are `GET /deals/summary` now, so the
 * client-side copies of them are gone. No I/O, no React, so it is trivially testable.
 */
import type { AttentionItem, Deal, DealStage } from "@/api/types";
import { STAGES, isOpen } from "./stages";

/** Deals still in flight (not closed-won or closed-lost). */
export function openDeals(deals: readonly Deal[]): Deal[] {
  return deals.filter((d) => isOpen(d.stage));
}

/** Every deal grouped by stage, in board order — the kanban columns (all six stages). */
export function dealsByStage(deals: readonly Deal[]): Record<DealStage, Deal[]> {
  const groups = Object.fromEntries(STAGES.map((s) => [s.stage, [] as Deal[]])) as Record<
    DealStage,
    Deal[]
  >;
  for (const deal of deals) groups[deal.stage].push(deal);
  for (const stage of STAGES) {
    groups[stage.stage].sort((a, b) => b.value_minor - a.value_minor);
  }
  return groups;
}

/** Whole days from `fromIso` to `toIso` (both `YYYY-MM-DD…`). Positive when `to` is later. */
function daysBetween(fromIso: string, toIso: string): number {
  const from = Date.parse(fromIso.slice(0, 10));
  const to = Date.parse(toIso.slice(0, 10));
  return Math.round((to - from) / 86_400_000);
}

const CLOSING_SOON_DAYS = 10;
const STALE_DAYS = 14;

/**
 * Open deals that need a nudge, most urgent first: overdue close dates, then deals closing within
 * ten days, then deals with no activity in the last fortnight. `today` keeps this pure.
 */
export function needsAttention(deals: readonly Deal[], today: string): AttentionItem[] {
  const items: AttentionItem[] = [];
  for (const deal of openDeals(deals)) {
    const toClose = daysBetween(today, deal.close_date);
    if (toClose < 0) {
      items.push({ deal, reason: "overdue", detail: `Close date ${-toClose}d overdue` });
      continue;
    }
    if (toClose <= CLOSING_SOON_DAYS) {
      items.push({
        deal,
        reason: "closing_soon",
        detail: toClose === 0 ? "Closes today" : `Closes in ${toClose}d`,
      });
      continue;
    }
    if (deal.last_activity_at) {
      const idle = daysBetween(deal.last_activity_at, today);
      if (idle >= STALE_DAYS) {
        items.push({ deal, reason: "stale", detail: `No activity in ${idle}d` });
      }
    }
  }
  const rank: Record<AttentionItem["reason"], number> = { overdue: 0, closing_soon: 1, stale: 2 };
  return items.sort(
    (a, b) => rank[a.reason] - rank[b.reason] || b.deal.value_minor - a.deal.value_minor,
  );
}
```

In `frontend/product-crm/src/data/stages.ts` delete the two orphans. First (lines 40-42)

```ts
/** The four open (in-flight) stages, in order — the working columns of the pipeline board. */
export const OPEN_STAGES: DealStage[] = ["lead", "qualified", "proposal", "negotiation"];

```

(the blank line after it goes too, so one blank line remains before `const META`), then (lines 56-59, at the end of the file)

```ts

export function isWon(stage: DealStage): boolean {
  return stage === "closed_won";
}
```

- [ ] **Step 4: Run it and see it pass**

Run: `cd frontend && pnpm --filter @opsgrids/product-crm typecheck && pnpm --filter @opsgrids/product-crm lint && pnpm --filter @opsgrids/product-crm test`
Expected: clean, and every test passes. After Tasks R1.2.20 to R1.2.22 the whole package reports `Test Files 40 passed (40)`,
`Tests 700 passed (700)` (observed under Node 22 in a scratch copy holding all three tasks; the unmodified package has 37
files and 691 tests). `typecheck` proves nothing imports a removed name.

Run: `grep -nE 'export function (pipelineValueMinor|weightedPipelineMinor|openCount|winRatePct|wonValueMinor|stageFunnel|dealsOwnedBy)' frontend/product-crm/src/data/derive.ts`
Expected: no output (`AC-1.2.8`).

- [ ] **Step 5: Commit**

```bash
git add frontend/product-crm/src/data/derive.ts frontend/product-crm/src/data/derive.test.ts frontend/product-crm/src/data/stages.ts
git commit -m "CRM app: delete the deal rollups the Overview no longer reads, and test the three that remain"
```

---

### Task R1.2.21: Other dead `product-crm` exports

**Deploy:** R1.2b (CRM SPA publish, O-12). **Spec:** class-1 row 6, `AC-1.2.9`. **Registry:** `G15`.

**Files:**
- Modify: `frontend/product-crm/src/api/client.ts:86-87` (`LIVE`)
- Modify: `frontend/product-crm/src/lib/format.ts:89-102` (`formatDateTime`)
- Modify: `frontend/product-crm/src/reveal/testKit.tsx:22` (`SECOND_STEP_UP_TOKEN`)
- Modify: `frontend/product-crm/src/ui/index.ts:38-39` (`KpiTone`, `MetricTrend`)
- Delete: `frontend/product-crm/src/api/index.ts` (a barrel nothing imports)
- Test: the search below and `pnpm --filter @opsgrids/product-crm typecheck lint test`

**Interfaces:**
- Consumes: nothing; each name has no user beyond its definition.
- Produces: the five removals. `getContact` (`api/client.ts`) is **kept**: R3.5's contact page needs it (class 3).

A removal task has no behaviour to pin, so the tests are the search that proves no live user and the typecheck and test
suite that would fail on a leftover import.

- [ ] **Step 1: Run the search that proves each name unused (the failing check)**

```bash
cd frontend/product-crm/src
for n in LIVE formatDateTime SECOND_STEP_UP_TOKEN KpiTone MetricTrend; do printf "%s: " "$n"; grep -rnw "$n" . | cut -d: -f1 | sort | uniq -c | tr '\n' ' '; echo; done
grep -rn 'from "@/api"\|from "\./api"\|from "\.\./api"' . | wc -l
cat api/index.ts
cd ../../..
```

Expected (observed at HEAD): each name prints exactly one file, its own (`LIVE: 1 api/client.ts`,
`formatDateTime: 1 lib/format.ts`, `SECOND_STEP_UP_TOKEN: 1 reveal/testKit.tsx`, `KpiTone: 1 ui/index.ts`,
`MetricTrend: 1 ui/index.ts`); the barrel-importer count is `0`; `api/index.ts` is the two lines `export * from "./types";` and
`export * from "./client";`. Paste this output in the pull request. After Step 2 the same loop prints a blank count for
every name.

- [ ] **Step 2: Remove them**

`frontend/product-crm/src/api/client.ts`: delete (lines 85-87, with the blank line before them)

```ts

/** True when the app is talking to the real gateway. */
export const LIVE = !USE_FIXTURES;
```

`frontend/product-crm/src/lib/format.ts`: delete the function and its comment (lines 89-101 and the blank line after)

```ts
/** "2026-08-22T11:20:00Z" -> "22 Aug, 4:50 PM" (times shown in the org's IST wall clock). */
export function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const date = d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", timeZone: "Asia/Kolkata" });
  const time = d.toLocaleTimeString("en-IN", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
    timeZone: "Asia/Kolkata",
  });
  return `${date}, ${time}`;
}

```

`frontend/product-crm/src/reveal/testKit.tsx`: delete line 22

```ts
export const SECOND_STEP_UP_TOKEN = "second-step-up-access-token";
```

`frontend/product-crm/src/ui/index.ts`: delete lines 38-39 from the `export type { ... } from "@opsgrids/ui"` block, leaving

```ts
export type {
  BarListItem,
  Column,
  DonutSlice,
  RowAction,
  TableSelection,
} from "@opsgrids/ui";
```

Delete the barrel: `git rm frontend/product-crm/src/api/index.ts`.

- [ ] **Step 3: Run the checks and see them pass**

Run: `cd frontend && pnpm --filter @opsgrids/product-crm typecheck && pnpm --filter @opsgrids/product-crm lint && pnpm --filter @opsgrids/product-crm test`
Expected: clean and passing (observed under Node 22: nothing imported any of the five names, so `tsc --noEmit` and
`eslint . --max-warnings=0` print nothing and the suite is green).

- [ ] **Step 4: Commit**

```bash
git add frontend/product-crm/src/api/client.ts frontend/product-crm/src/lib/format.ts frontend/product-crm/src/reveal/testKit.tsx frontend/product-crm/src/ui/index.ts
git commit -m "CRM app: delete five exports nothing imports, and the empty api barrel"
```

(`git rm` has already staged the barrel's deletion.)

---

### Task R1.2.22: Four placeholder routes, their nav entries, and a real 404 page

**Deploy:** R1.2b (CRM SPA publish, O-12). **Spec:** class-1 row 8, `S§5.7`, `DD-5`, `AC-1.2.8`, `AC-1.2.9`.
**Registry:** `G15`. Strike-able (O-15): R3.4 removes the same routes when it rebuilds the shell.

**Files:**
- Create: `frontend/product-crm/src/routes/NotFound.tsx`, `frontend/product-crm/src/routes/NotFound.test.tsx`
- Create: `frontend/product-crm/src/ui/useNavGroups.test.tsx`
- Create: `tests/infra/test_crm_spa_navigation.py`
- Modify: `frontend/product-crm/src/main.tsx:6,22,69-88,90-119`
- Modify: `frontend/product-crm/src/ui/useNavGroups.ts:5-17,20-23,55,61`
- Delete: `frontend/product-crm/src/routes/ComingSoon.tsx`
- Modify: `frontend/opsgrids-config/token-baseline.json` (regenerated), 18 PNG baselines under
  `frontend/opsgrids-visual/baselines/darwin/page/crm/` and, only if it improves, `frontend/opsgrids-visual/a11y-baseline.json`
- Test: the two new frontend test files, `tests/infra/test_crm_spa_navigation.py`, `pnpm --filter @opsgrids/product-crm test`,
  `make frontend-gate`, `make ui-visual`

**Interfaces:**
- Consumes: `Card` from `@/ui`; `Link` from `react-router-dom`; `useNavGroups(): { groups: NavGroup[]; pinned: NavEntry[] }`
  (the hook `AppShell` reads; its `pinned` prop is optional, `opsgrids-ui/src/components/AppShell.tsx:78`); the routes table
  in `main.tsx` (the Team route at `:89` stays).
- Produces: `NotFoundPage(): JSX.Element` exported from `routes/NotFound.tsx` (eyebrow "Not found", heading "That page
  doesn't exist", the text "Check the address, or head back to the overview.", a link "Back to overview" to `/`), mounted
  on the catch-all `*` route; seven nav entries (`/`, `/leads`, `/deals`, `/activities`, `/companies`, `/contacts`,
  `/team`) in three groups and nothing pinned. The routes `/reports`, `/integrations`, `/settings` and `/help` no longer
  exist: a visit to one reaches the 404 page. SP3's `NotFoundPage` is this component (registry `G15`).
  `tests/infra/test_crm_spa_navigation.py` (four tests, the Review Focus line on stale bookmarks) reads `main.tsx` and
  `useNavGroups.ts` as source, because `main.tsx` mounts the app when imported: no removed path is declared, no rail
  entry leads to an undeclared path, and the last route is the catch-all that renders `<NotFoundPage />`.

Four routes (Reports, Integrations, Settings, Help) render a "coming soon" card and each has a nav entry; the 404 route
reuses the same placeholder component. Nothing links to them from another app (Engage links `/contacts`,
`product-engage/src/lib/dashboard.ts:33`). The token-baseline entry of `ComingSoon.tsx` (colour 1, spacing 3) goes with the
file, taking `product-crm`'s 101 offenders in 15 files to 97 in 14.

- [ ] **Step 1: Write the failing tests**

Create `frontend/product-crm/src/routes/NotFound.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { NotFoundPage } from "./NotFound";

/**
 * The route table's catch-all. Nothing loads, so there is no loading, empty, error or forbidden
 * state to pin: what matters is that a person who followed a dead link is told so and offered the
 * way back, in the words the Overview link uses.
 */

describe("NotFoundPage", () => {
  it("says the page does not exist and offers the way back to the overview", () => {
    render(
      <MemoryRouter>
        <NotFoundPage />
      </MemoryRouter>,
    );
    expect(screen.getByText("Not found")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "That page doesn't exist" })).toBeTruthy();
    expect(screen.getByText("Check the address, or head back to the overview.")).toBeTruthy();
    const back = screen.getByRole("link", { name: "Back to overview" });
    expect(back.getAttribute("href")).toBe("/");
  });
});
```

Create `frontend/product-crm/src/ui/useNavGroups.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  listDeals: () => Promise.resolve({ items: [], total: 12, page: 1, size: 1, pages: 12 }),
}));

import { useNavGroups } from "./useNavGroups";

/**
 * The CRM's navigation is seven entries that each lead to a screen that exists: the placeholder
 * routes (Reports, Integrations, Settings, Help) are gone, and nothing is pinned to the rail's
 * foot. The Deals count is the real total from the deals list.
 */

function NavProbe() {
  const { groups, pinned } = useNavGroups();
  const entries = groups.flatMap((group) => group.items);
  return (
    <div>
      <ul aria-label="entries">
        {entries.map((entry) => (
          <li key={entry.to}>
            {entry.to}|{entry.label}|{entry.count ?? ""}
          </li>
        ))}
      </ul>
      <p data-testid="pinned">{pinned.length}</p>
      <p data-testid="groups">{groups.map((group) => group.title).join(",")}</p>
    </div>
  );
}

describe("useNavGroups", () => {
  it("lists the seven screens that exist, in three groups, and pins nothing", async () => {
    render(<NavProbe />);
    expect(screen.getByTestId("groups").textContent).toBe("Pipeline,Records,Workspace");
    expect(screen.getByTestId("pinned").textContent).toBe("0");
    const items = screen.getAllByRole("listitem").map((li) => li.textContent?.split("|")[0]);
    expect(items).toEqual(["/", "/leads", "/deals", "/activities", "/companies", "/contacts", "/team"]);
  });

  it("shows the real number of deals beside Deals once it has loaded, and no other count", async () => {
    render(<NavProbe />);
    await waitFor(() => expect(screen.getByText("/deals|Deals|12")).toBeTruthy());
    expect(screen.getByText("/activities|Activities|")).toBeTruthy();
  });
});
```

Create `tests/infra/test_crm_spa_navigation.py`. Someone bookmarked Reports, Integrations, Settings or Help while they were
rail entries; what they must find after this task is the app's own 404 page, and nothing may ever again put a rail
entry where the route table has no route. `main.tsx` mounts the app as a side effect of being imported, so the test reads
both files as source, the way `tests/infra/test_engage_recipe_triggers.py` reads the Engage recipes:

```python
"""The CRM rail leads only to screens that exist, and a dead address reaches the 404 page.

Four of the eleven rail entries led to a "coming soon" card for months, and each had a route a
person could bookmark: Reports, Integrations, Settings and Help. R1.2b removes them. Someone who
kept the bookmark must land on the app's own "That page doesn't exist" page, which is the route
table's last entry, and no rail entry may ever again point at a path the route table does not
declare. The route table is `main.tsx` and the rail is `ui/useNavGroups.ts`; a test cannot import
`main.tsx` (it mounts the app as a side effect), so both are read as source, the way
`test_engage_recipe_triggers.py` reads the Engage recipes.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "frontend" / "product-crm" / "src"
MAIN = SRC / "main.tsx"
NAV = SRC / "ui" / "useNavGroups.ts"

#: The routes that rendered a "coming soon" card and were removed together with their rail entries.
REMOVED = ("reports", "integrations", "settings", "help")

_ROUTE_PATH = re.compile(r'\bpath:\s*"([^"]+)"')
_NAV_TARGET = re.compile(r'\bto:\s*"([^"]+)"')


def _declared_paths() -> list[str]:
    return _ROUTE_PATH.findall(MAIN.read_text(encoding="utf-8"))


def _nav_targets() -> list[str]:
    return _NAV_TARGET.findall(NAV.read_text(encoding="utf-8"))


def test_the_scan_finds_what_it_is_meant_to_check() -> None:
    """A pattern that matched nothing would make every test below pass."""
    assert {"leads", "deals", "companies", "contacts", "activities", "team"} <= set(
        _declared_paths()
    )
    assert len(_nav_targets()) >= 7


def test_no_removed_placeholder_route_is_declared() -> None:
    back = sorted(set(_declared_paths()) & set(REMOVED))
    assert not back, f"placeholder routes are declared again: {back}"


def test_every_rail_entry_leads_to_a_declared_route() -> None:
    declared = set(_declared_paths())
    dead = [t for t in _nav_targets() if t != "/" and t.lstrip("/") not in declared]
    assert not dead, f"rail entries that lead nowhere: {dead}"


def test_the_last_route_is_the_not_found_page() -> None:
    source = MAIN.read_text(encoding="utf-8")
    routes = list(_ROUTE_PATH.finditer(source))
    assert routes and routes[-1].group(1) == "*", "the catch-all must be the last route"
    assert "<NotFoundPage />" in source[routes[-1].end() :], "the catch-all must be the 404 page"
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/infra/test_crm_spa_navigation.py -q -p no:cacheprovider`
Expected: FAIL, `2 failed, 2 passed` (observed against the unmodified `main.tsx` and `useNavGroups.ts`, in a scratch tree
of exactly the two files): `test_no_removed_placeholder_route_is_declared` with `placeholder routes are declared again:
['help', 'integrations', 'reports', 'settings']` and `test_the_last_route_is_the_not_found_page` with `the catch-all must
be the 404 page` (the catch-all renders `ComingSoonPage` today). The scan test and the rail test pass already: today every
rail entry has a route, which is the point of the second, a guard that stays after the removal.

Run: `cd frontend/product-crm && pnpm exec vitest run src/routes/NotFound.test.tsx src/ui/useNavGroups.test.tsx`
Expected: FAIL (observed): `src/routes/NotFound.test.tsx` fails to load, `Error: Failed to resolve import "./NotFound" from
"src/routes/NotFound.test.tsx". Does the file exist?`; in `useNavGroups.test.tsx` the first test fails with `AssertionError:
expected '2' to be '0'` (two entries are pinned today) and the second passes.

- [ ] **Step 3: Write the minimal implementation**

Proof of no live user (paste the output in the pull request):

Run: `grep -rn ComingSoon frontend/product-crm/src`
Expected: `main.tsx` (the import and five `ComingSoonPage` uses) and `routes/ComingSoon.tsx` only.
Run: `grep -rnE '"/(reports|integrations|settings|help)"' frontend/product-crm/src`
Expected: `ui/useNavGroups.ts` only (four lines).

Create `frontend/product-crm/src/routes/NotFound.tsx`:

```tsx
import { Link } from "react-router-dom";

import { Card } from "@/ui";

/**
 * The route table's catch-all: an address nothing serves. It sits inside the signed-in shell, so
 * the way back is the overview. Nothing loads here, so it has no loading, empty, error or
 * forbidden state — a dead link is the whole of its job.
 */
export function NotFoundPage() {
  return (
    <div className="rise flex justify-center pt-8">
      <Card className="flex w-full max-w-lg flex-col items-center gap-4 p-8 text-center">
        <div className="flex flex-col gap-2">
          <span className="text-sm font-semibold uppercase tracking-wide text-muted">Not found</span>
          <h1 className="display text-3xl font-extrabold">That page doesn't exist</h1>
          <p className="mx-auto max-w-sm text-sm leading-relaxed text-muted">
            Check the address, or head back to the overview.
          </p>
        </div>
        <Link
          to="/"
          className="inline-flex h-11 items-center rounded-full bg-accent px-6 font-semibold text-on-accent transition-colors hover:bg-accent-strong"
        >
          Back to overview
        </Link>
      </Card>
    </div>
  );
}
```

(The classes use only the token scale the lint rule allows: the token classifier reports no offender for any of these class
lists, observed.)

`frontend/product-crm/src/main.tsx`, five edits.

1. Line 6: replace

```tsx
import { FileTextIcon, LifeBuoyIcon, PlugIcon, SettingsIcon, ThemeProvider, Toaster } from "@opsgrids/ui";
```

with

```tsx
import { ThemeProvider, Toaster } from "@opsgrids/ui";
```

2. Line 22: replace `import { ComingSoonPage } from "./routes/ComingSoon";` with

```tsx
import { NotFoundPage } from "./routes/NotFound";
```

3. Delete the Reports and Integrations routes (lines 69-88), which sit between `activities` and `team`:

```tsx
          {
            path: "reports",
            element: (
              <ComingSoonPage
                icon={<FileTextIcon className="size-6" />}
                title="Reports"
                description="Exportable pipeline, forecast and win/loss reports are on the roadmap. The Overview already breaks pipeline down by stage."
              />
            ),
          },
          {
            path: "integrations",
            element: (
              <ComingSoonPage
                icon={<PlugIcon className="size-6" />}
                title="Integrations"
                description="Email, calendar and telephony connectors will plug in here to log activity automatically."
              />
            ),
          },
```

4. Replace the Settings, Help and catch-all routes (lines 90-119, after `{ path: "team", element: <TeamPage /> },`):

```tsx
          {
            path: "settings",
            element: (
              <ComingSoonPage
                icon={<SettingsIcon className="size-6" />}
                title="Settings"
                description="Pipeline stages, deal fields and workspace preferences will live here."
              />
            ),
          },
          {
            path: "help",
            element: (
              <ComingSoonPage
                icon={<LifeBuoyIcon className="size-6" />}
                title="Help & Getting Started"
                description="Guides and support for setting up your revenue workspace are on the way."
              />
            ),
          },
          {
            path: "*",
            element: (
              <ComingSoonPage
                eyebrow="Not found"
                title="That page doesn't exist"
                description="Check the address, or head back to the overview."
              />
            ),
          },
```

with the single line

```tsx
          { path: "*", element: <NotFoundPage /> },
```

`frontend/product-crm/src/ui/useNavGroups.ts`, four edits.

1. Replace the import block (lines 5-17)

```ts
import {
  ActivityIcon,
  BuildingIcon,
  FileTextIcon,
  HomeIcon,
  KanbanIcon,
  LifeBuoyIcon,
  PlugIcon,
  SettingsIcon,
  TargetIcon,
  UserPlusIcon,
  UsersIcon,
} from "@opsgrids/ui";
```

with

```ts
import {
  ActivityIcon,
  BuildingIcon,
  HomeIcon,
  KanbanIcon,
  TargetIcon,
  UserPlusIcon,
  UsersIcon,
} from "@opsgrids/ui";
```

2. Replace the pinned list (lines 20-23)

```ts
const PINNED: NavEntry[] = [
  { to: "/settings", label: "Settings", icon: SettingsIcon },
  { to: "/help", label: "Help & Getting Started", icon: LifeBuoyIcon },
];
```

with

```ts
/** Nothing is pinned to the rail's foot: every entry above it leads to a screen that exists. */
const PINNED: NavEntry[] = [];
```

3. In the "Records" group delete the line `        { to: "/reports", label: "Reports", icon: FileTextIcon },` (line 55).
4. In the "Workspace" group delete the line `        { to: "/integrations", label: "Integrations", icon: PlugIcon },` (line 61), leaving only the Team entry.

Delete the placeholder component: `git rm frontend/product-crm/src/routes/ComingSoon.tsx`.

- [ ] **Step 4: Run them and see them pass**

Run: `cd frontend/product-crm && pnpm exec vitest run src/routes/NotFound.test.tsx src/ui/useNavGroups.test.tsx`
Expected: `Test Files 2 passed (2)`, `Tests 3 passed (3)`.

Run: `uv run pytest tests/infra/test_crm_spa_navigation.py -q -p no:cacheprovider && uv run ruff check tests/infra/test_crm_spa_navigation.py && uv run ruff format --check tests/infra/test_crm_spa_navigation.py`
Expected: `4 passed`, `All checks passed!` and `1 file already formatted` (observed against a scratch tree whose `main.tsx` and
`useNavGroups.ts` carry exactly the edits above).

Run: `cd frontend && pnpm --filter @opsgrids/product-crm typecheck && pnpm --filter @opsgrids/product-crm lint && pnpm --filter @opsgrids/product-crm test`
Expected: clean; `Test Files 40 passed (40)`, `Tests 700 passed (700)` with Tasks R1.2.20 and R1.2.21 applied (observed under
Node 22 in a scratch copy; `tsc --noEmit` and `eslint . --max-warnings=0` print nothing).

Run: `grep -rn ComingSoon frontend/product-crm/src`
Expected: no output (`AC-1.2.8`).

- [ ] **Step 5: Lower the token baseline (the ratchet)**

Run: `cd frontend && pnpm tokens:baseline`
Expected: it rewrites `opsgrids-config/token-baseline.json` and only lowers it: the key `product-crm/src/routes/ComingSoon.tsx`
(`"colour": 1, "spacing": 3`) is gone, and `product-crm` has 14 entries totalling 97 offenders (15 and 101 before).
Check: `git diff --stat frontend/opsgrids-config/token-baseline.json` shows one removed 4-line entry and nothing else.
(`pnpm tokens:baseline` refuses to raise a count: an increase means a new offender, which is a defect to fix.)

- [ ] **Step 6: Re-baseline the three CRM surfaces and run the gates**

The navigation changes on every CRM page, so the pixel baselines of the three CRM surfaces change
(`frontend/opsgrids-visual/baselines/darwin/page/crm/{overview,deals-list,new-deal-dialog}/{light,dark}-{375,768,1280}.png`,
18 files). The committed baselines are macOS's: run this on macOS, or hand the step to the owner (**OWNER RUN**) and
do not commit a re-baseline taken on another platform.

Run: `cd frontend && pnpm --filter @opsgrids/visual run visual:browsers && pnpm visual:check`
Expected: FAIL, naming exactly the three CRM surfaces in both themes and the three widths and no other surface (the
rail lost its Reports and Integrations links and its pinned Settings and Help).
Review the images in the report: the rail shows seven entries and no footer links, and the page content is unchanged.
Run: `cd frontend && pnpm visual:update`
Expected: it rewrites the 18 PNGs above and no other PNG; `a11y-baseline.json` may change only by **lowering** counts (an
improvement), never by raising one. `git status --short frontend/opsgrids-visual` shows only those files.
Run: `make ui-visual` and `make frontend-gate`
Expected: both pass.

- [ ] **Step 7: Commit**

```bash
git add frontend/product-crm/src/routes/NotFound.tsx frontend/product-crm/src/routes/NotFound.test.tsx frontend/product-crm/src/ui/useNavGroups.test.tsx tests/infra/test_crm_spa_navigation.py frontend/product-crm/src/main.tsx frontend/product-crm/src/ui/useNavGroups.ts frontend/opsgrids-config/token-baseline.json frontend/opsgrids-visual
git commit -m "CRM app: drop four coming-soon routes and their nav entries, give the 404 its own page, re-baseline the shell"
```

(`git rm` has already staged the deletion of `ComingSoon.tsx`.)

---

### Task R1.2.23: The `ActivityIn` description is true, and `make openapi` is regenerated

**Deploy:** R1.2b. **Spec:** `R1.2-10`, `R1.2-12`, `AC-1.2.10`. **Registry:** `H6`.

**Files:**
- Modify: `services/deal/app/schemas.py:157-160` (the `ActivityIn` docstring)
- Regenerate: `libs/contracts/openapi/deal.json` (one line, `:6`)
- Test: `make openapi-check`

**Interfaces:**
- Consumes: `ActivityIn` (`POST /api/v1/deals/activities` request model); `tools/export_openapi.py` and
  `tools/export_permissions.py` (`make openapi` runs both, `make openapi-check` runs both with `--check`).
- Produces: a docstring that matches the field list. `owner_id` is a field of `ActivityIn` (`schemas.py:175`), optional:
  an ORG-scoped caller may attribute the entry to another rep, an OWN-scoped caller can only ever log against themselves
  and the route forces it (`app/api/routes.py`). The docstring is rendered into the OpenAPI description of `ActivityIn`,
  so `deal.json` changes in exactly one line; no route, field or permission changes.

The docstring says `owner_id` is "deliberately absent" from the contract while the model declares it two screens below.
It reads as a decision and is not one; the consumers of the OpenAPI document (the generated clients, the docs) read it.

- [ ] **Step 1: Run the search that finds the statement (the failing check)**

Run: `grep -n "deliberately absent" services/deal/app/schemas.py libs/contracts/openapi/deal.json | cut -c1-120`
Expected: two lines, `services/deal/app/schemas.py:157` and `libs/contracts/openapi/deal.json:6`.
Run: `grep -n "owner_id: uuid.UUID | None = None" services/deal/app/schemas.py`
Expected: the field, at `:175`, inside the same class: the statement is false.

- [ ] **Step 2: Correct the docstring and see the committed document fall behind**

In `services/deal/app/schemas.py` replace lines 157-160

```python
    `owner_id` is deliberately absent here: an OWN-scoped caller can only ever log against
    themselves, and the route forces it; only an ORG-scoped caller may attribute the entry
    to someone else, which is why the field lives on the model but not on this contract —
    see `app/api/routes.py`.
```

with

```python
    `owner_id` is optional: an ORG-scoped caller may attribute the entry to another rep, while
    an OWN-scoped caller can only ever log against themselves and the route forces it, whatever
    the body says — see `app/api/routes.py`.
```

Run: `make openapi-check`
Expected: FAIL, naming `libs/contracts/openapi/deal.json` as behind the code (the committed description still carries the
old paragraph; observed in a scratch copy: the regenerated document differs from the committed one in exactly one line).

- [ ] **Step 3: Regenerate**

Run: `make openapi`
Run: `git diff --stat`
Expected: `libs/contracts/openapi/deal.json | 2 +-` and the docstring's file, and nothing under
`libs/contracts/permissions/` (`AC-1.2.10`). `git diff libs/contracts/openapi/deal.json` shows the one `description` line
with the new paragraph.

- [ ] **Step 4: Run the checks and see them pass**

Run: `make openapi-check && make contracts-check`
Expected: both pass.

Run: `cd services/deal && uv run mypy app && uv run pytest tests/test_schemas.py -q -p no:cacheprovider && cd ../.. && uv run ruff check services/deal && uv run ruff format --check services/deal`
Expected: `Success: no issues found`, the schema tests pass (inference: a docstring does not change behaviour),
`All checks passed!`.

Run: `grep -rn "deliberately absent" services/deal/app/schemas.py libs/contracts/openapi/deal.json`
Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add services/deal/app/schemas.py libs/contracts/openapi/deal.json
git commit -m "Deal: correct the ActivityIn description of owner_id and regenerate the OpenAPI document"
```

---

### Task R1.2.24: Stale comments in code and configuration

**Deploy:** R1.2b. **Spec:** `R1.2-10` (the R1.2b half; the `c1_360.py` text is Task R1.2.2, `ActivityIn` is Task R1.2.23, the
contracts-test docstring is Task R1.2.10, the architecture and deploy documents are Tasks R1.2.13, R1.2.14 and R1.2.18).

**Files:**
- Modify: `frontend/product-crm/src/forms/OwnerFormDialog.tsx:14-18`
- Modify: `services/customer/app/catalogue.py:3-8`, `services/deal/app/catalogue.py:3-8`
- Modify: `infrastructure/edge/compose.prod.yml:1467-1471`
- Modify: `e2e/_seed_product.py:20-24`
- Modify: `tools/dev-bootstrap/init-db.sql:103-104`
- Modify: `tools/shard-weights.txt:37`
- Test: `${TMPDIR:-/tmp}/crm-r1-2-checks/check-stale.sh` (a throwaway search test, created in Step 1, not committed)

**Interfaces:**
- Consumes: the facts each new sentence states: `PATCH /customers/owners/{owner_id}` exists
  (`services/customer/app/api/routes.py:212-238`, summary "Edit a rep, or deactivate one"); three processes make up the `crm`
  product (customer, deal, lead); lead publishes four event types; `libs/contracts/permissions/crm.json` holds three admin
  role templates; `marketing` and `support` are not CRM services.
- Produces: comments, docstrings and one measured number. No behaviour changes: a docstring, comments, a SQL comment and
  one weight.

Each is a statement that is false today and costs a reader time. None is a code path, so the test is a search that fails
while the false sentence is there.

- [ ] **Step 1: Write the failing search test**

```bash
export R12="${TMPDIR:-/tmp}/crm-r1-2-checks"; mkdir -p "$R12"
cat > "$R12/check-stale.sh" <<'SH'
#!/usr/bin/env bash
# R1.2-10 (R1.2b): each stale statement is gone and what replaced it is true. Run from the repository root.
set -u
FAILED=0
fail()  { printf 'FAIL: %s\n' "$*" >&2; FAILED=1; }
has()   { grep -qF -- "$2" "$1" 2>/dev/null || fail "$1 lacks: $2"; }
lacks() { if grep -qF -- "$2" "$1" 2>/dev/null; then fail "$1 still has: $2"; fi; }

O=frontend/product-crm/src/forms/OwnerFormDialog.tsx
lacks "$O" "the backend doesn't offer it"
has   "$O" 'PATCH /customers/owners/{owner_id}'
has   services/customer/app/api/routes.py 'summary="Edit a rep, or deactivate one"'
lacks services/customer/app/catalogue.py 'one of two processes'
lacks services/deal/app/catalogue.py 'one of two processes'
has   services/customer/app/catalogue.py 'one of three processes'
has   services/deal/app/catalogue.py 'one of three processes'
lacks infrastructure/edge/compose.prod.yml 'Publishes only'
has   infrastructure/edge/compose.prod.yml 'Publishes four event types'
lacks e2e/_seed_product.py '**two** admin'
has   e2e/_seed_product.py '**three** admin'
[ "$(grep -c '"code": "CRM_[A-Z_]*_ADMIN"' libs/contracts/permissions/crm.json)" = 3 ] || fail 'crm.json no longer has three admin role templates'
lacks tools/dev-bootstrap/init-db.sql "'opsgrids_lead', 'opsgrids_marketing', 'opsgrids_support',"
has   tools/dev-bootstrap/init-db.sql '-- Marketing and support'
[ "$(awk '$1=="lead" {print $2}' tools/shard-weights.txt)" -gt 1 ] 2>/dev/null || fail 'tools/shard-weights.txt still says lead 1: re-measure it'
[ "$FAILED" = 0 ] && echo PASS || { echo FAILED; exit 1; }
SH
```

- [ ] **Step 2: Run it and see it fail**

Run: `bash "$R12/check-stale.sh"`
Expected: exit status 1; stderr lists `FAIL: ... OwnerFormDialog.tsx still has: the backend doesn't offer it`, `... lacks:
PATCH /customers/owners/{owner_id}`, the two `still has: one of two processes`, `... compose.prod.yml still has: Publishes
only`, `... _seed_product.py still has: **two** admin`, `... init-db.sql still has: 'opsgrids_lead', ...`, `... lacks: -- Marketing
and support` and `tools/shard-weights.txt still says lead 1`; stdout says `FAILED`.

- [ ] **Step 3: Correct each statement**

`frontend/product-crm/src/forms/OwnerFormDialog.tsx`, the component comment (lines 13-18):

````text old=frontend/product-crm/src/forms/OwnerFormDialog.tsx
/**
 * Add a rep to the directory — `POST /customers/owners`. The prerequisite write: a production
 * org starts with zero owners, and every company / contact / deal create needs one. There is no
 * edit or deactivate — the backend doesn't offer it, so this dialog only ever creates.
 */
````

````text new=frontend/product-crm/src/forms/OwnerFormDialog.tsx
/**
 * Add a rep to the directory — `POST /customers/owners`. The prerequisite write: a production
 * org starts with zero owners, and every company / contact / deal create needs one. The backend
 * also offers `PATCH /customers/owners/{owner_id}` to edit or deactivate a rep; this dialog does
 * not use it yet, so it only ever creates.
 */
````

`services/customer/app/catalogue.py`, the module docstring (lines 3-8):

````text old=services/customer/app/catalogue.py
This service is one of two processes in the `crm` product — deal-service is the other —
and it declares its own slice of that product's vocabulary (the `crm.book.*` permissions)
rather than importing the pipeline's catalogue across a service boundary the platform
exists to keep clean. Registering the `crm` product here is independent of, and consistent
with, deal-service registering it there: each process owns the catalogue for the
permissions it serves, exactly as the HRMS services do.
````

````text new=services/customer/app/catalogue.py
This service is one of three processes in the `crm` product — deal-service and lead-service
are the others — and it declares its own slice of that product's vocabulary (the `crm.book.*`
permissions) rather than importing theirs across a service boundary the platform exists to
keep clean. Registering the `crm` product here is independent of, and consistent with, the
other two registering it: each process owns the catalogue for the permissions it serves,
exactly as the HRMS services do.
````

`services/deal/app/catalogue.py`, the module docstring (lines 3-8):

````text old=services/deal/app/catalogue.py
This service is one of two processes in the `crm` product — customer-service is the
other — and it declares its own slice of that product's vocabulary (the `crm.pipeline.*`
permissions) rather than importing the book's catalogue across a service boundary the
platform exists to keep clean. Registering the `crm` product here is independent of, and
consistent with, customer-service registering it there: each process owns the catalogue
for the permissions it serves, exactly as the HRMS services do.
````

````text new=services/deal/app/catalogue.py
This service is one of three processes in the `crm` product — customer-service and
lead-service are the others — and it declares its own slice of that product's vocabulary
(the `crm.pipeline.*` permissions) rather than importing theirs across a service boundary
the platform exists to keep clean. Registering the `crm` product here is independent of, and
consistent with, the other two registering it: each process owns the catalogue for the
permissions it serves, exactly as the HRMS services do.
````

`infrastructure/edge/compose.prod.yml`, the comment above the `lead` service (lines 1467-1471; it is five lines before and
after, so no later line moves):

````text old=infrastructure/edge/compose.prod.yml
  # The lead half of the `crm` product (ADR-0017). Publishes only — `crm.lead.converted` — and
  # consumes nothing, so no dedicated worker: `edge-relay` drains its outbox like every other
  # service that has no consumer of its own. Calls customer's and deal's `/internal/v1` on
  # `:convert`, under a short-lived service token, the same shape notification uses to call
  # customer today.
````

````text new=infrastructure/edge/compose.prod.yml
  # The lead half of the `crm` product (ADR-0017). Publishes four event types, `crm.lead.created`,
  # `.updated`, `.converted` and `.conversion_aborted`, and consumes nothing, so no dedicated
  # worker: `edge-relay` drains its outbox like every other service that has no consumer of its
  # own. Calls customer's and deal's `/internal/v1` on `:convert`, under a short-lived service
  # token, the same shape notification uses to call customer today.
````

`e2e/_seed_product.py`, the paragraph of the module docstring (lines 20-24):

````text old=e2e/_seed_product.py
CRM is the only product in the catalogue that spans two services (customer-service owns the
account book, deal-service owns the pipeline) and therefore the only one shipping **two** admin
role templates. That is exactly the shape of the defect the CRM journey exists to catch:
activation used to grant only the last admin template parsed, so CRM's owners could read
contacts and not deals.
````

````text new=e2e/_seed_product.py
CRM is the only product in the catalogue that spans three services (customer-service owns the
account book, deal-service owns the pipeline, lead-service owns lead capture) and therefore the
only one shipping **three** admin role templates. That is exactly the shape of the defect the
CRM journey exists to catch: activation used to grant only the last admin template parsed, so
CRM's owners could read contacts and not deals.
````

`tools/dev-bootstrap/init-db.sql`, the role array (lines 103-104; comments only, the role list is unchanged):

````text old=tools/dev-bootstrap/init-db.sql
    -- CRM
    'opsgrids_customer', 'opsgrids_deal', 'opsgrids_lead', 'opsgrids_marketing', 'opsgrids_support',
````

````text new=tools/dev-bootstrap/init-db.sql
    -- CRM
    'opsgrids_customer', 'opsgrids_deal', 'opsgrids_lead',
    -- Marketing and support
    'opsgrids_marketing', 'opsgrids_support',
````

`tools/shard-weights.txt`, line 37 (`lead 1`, measured when `lead` was a scaffold with no tests). Re-measure with the file's own
header procedure, for `lead` only (needs `make infra` and `make migrate`):

```bash
t0=$(date +%s); (cd services/lead && uv run pytest tests -q -p no:cacheprovider >/dev/null 2>&1); echo "lead $(($(date +%s)-t0))"
```

and write the line it prints in place of `lead 1`. The value is machine-specific and only its ratio to the others matters
(the file's header says so); the number is whatever the run prints.

- [ ] **Step 4: Run it and see it pass**

Run: `bash "$R12/check-stale.sh"`
Expected: `PASS`.

Run: `make shard-verify`
Expected: passes (the balancer still places every service).

Run: `uv run ruff check services/customer services/deal e2e && uv run ruff format --check services/customer services/deal e2e && cd frontend && pnpm --filter @opsgrids/product-crm lint`
Expected: `All checks passed!`, `already formatted`, and a clean lint (all six edits are comments or a docstring).

Run: `uv run pytest tests/infra -q -p no:cacheprovider`
Expected: passes (inference: no test pins any of these comments; the compose file still parses and its rendered configuration
is unchanged because only comments moved).

- [ ] **Step 5: Commit**

```bash
git add frontend/product-crm/src/forms/OwnerFormDialog.tsx services/customer/app/catalogue.py services/deal/app/catalogue.py infrastructure/edge/compose.prod.yml e2e/_seed_product.py tools/dev-bootstrap/init-db.sql tools/shard-weights.txt
git commit -m "Correct six stale statements: owner edit exists, CRM has three processes, lead publishes four events, and the rest"
```

---

### Task R1.2.25: `docs/crm.md` says what R1.2b delivered, and a test keeps it true

**Deploy:** R1.2b. **Spec:** `R1.2-14`, `S§4.5`, `AC-1.2.13`. **Registry:** `I3`. This task runs **last** in R1.2b: it states the
final code and re-points every citation the release moved.

**Files:**
- Create: `tests/infra/test_crm_doc_matches_contracts.py`
- Modify: `docs/crm.md` (R1.1's page): sections 1, 2, 5, 8, 10, 11 and 12
- Modify: `docs/ecosystem-gaps.md` (one clause R1.1 wrote), `docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md`
  (three citations in the amendment note R1.1 wrote)
- Test: `tests/infra/test_crm_doc_matches_contracts.py` and `${TMPDIR:-/tmp}/crm-r1-2-checks/check-doc-b.sh` (a throwaway
  search test, created in Step 1, not committed)

**Interfaces:**
- Consumes: R1.1's page as its plan writes it: twelve `## ` sections; `## 2. Service map` whose table has one row per CRM
  service with the first cell `customer`, `deal` or `lead`; `## 8. Events` whose table's first cells are the event types in
  backticks; `## 9. Permissions and roles` whose permission table's first cells are the codes and whose role table's first
  cells are the role codes. The committed contracts: `libs/contracts/events/crm.*/v1.schema.json` (15 directories),
  `libs/contracts/permissions/crm.json` (17 permissions, 6 role templates), `services/app-gateway/routes.json` and
  `infrastructure/edge/routes.prod.json`.
- Produces: four tests that fail, naming the row, when a table and its source disagree:
  `test_the_event_table_lists_exactly_the_crm_schemas`, `test_the_permission_table_lists_exactly_the_manifest_codes`,
  `test_the_role_table_lists_exactly_the_manifest_templates` and `test_the_service_map_ports_match_the_gateway` (which also
  fails when the dev and prod tables carry different CRM prefixes). The rule for every later release: **change a CRM event,
  permission, role or gateway prefix, and update `docs/crm.md` in the same commit.** And a page that says what R1.2b did.

The test reads sections by the words in their `## ` headings (`service map`, `event`, `permission`) and tables by the shape of
their first cell, so prose around them is free. If R1.1's executor renamed a heading, the three `SECTION_*` constants are the
one place to follow it.

- [ ] **Step 1: Write the failing checks**

Create `tests/infra/test_crm_doc_matches_contracts.py`:

```python
"""`docs/crm.md` says what the committed contracts and the route tables say.

The document holds four tables a person copies from: the service map (ports and gateway prefixes),
the event table, the permission table and the role table. They are hand-written prose around
generated facts, and a hand-written table drifts the day a release adds an event, a permission or a
route. This test is the tripwire: it fails, naming the row, when a table and its source disagree.

Sources of truth, never the other way round:

    events        libs/contracts/events/crm.*/v1.schema.json
    permissions   libs/contracts/permissions/crm.json      (`permissions`)
    roles         libs/contracts/permissions/crm.json      (`role_templates`)
    ports         services/app-gateway/routes.json         (dev port) and
                  infrastructure/edge/routes.prod.json     (the service behind each prefix)

**Changing a CRM event, permission, role or gateway prefix? Update `docs/crm.md` in the same
commit.**

The document's layout is R1.1's (`docs/superpowers/specs/2026-10-07-crm-1-foundations-and-cleanup-
design.md` section 4.5): twelve `## ` sections, with the service map, the events and the
permissions-and-roles sections found here by their headings. If a heading is renamed, the three
`SECTION_*` words below are the one place to follow it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CRM_DOC = REPO_ROOT / "docs" / "crm.md"
EVENTS_DIR = REPO_ROOT / "libs" / "contracts" / "events"
CRM_MANIFEST = REPO_ROOT / "libs" / "contracts" / "permissions" / "crm.json"
DEV_ROUTES = REPO_ROOT / "services" / "app-gateway" / "routes.json"
PROD_ROUTES = REPO_ROOT / "infrastructure" / "edge" / "routes.prod.json"

#: Lower-case words that identify each section by its `## ` heading.
SECTION_SERVICES = "service map"
SECTION_EVENTS = "event"
SECTION_PERMISSIONS = "permission"

_EVENT_TYPE = re.compile(r"^crm\.(?:(?:book|pipeline)\.[a-z_]+\.[a-z_]+|lead\.[a-z_]+)$")
_PERMISSION_CODE = re.compile(r"^crm\.[a-z_]+\.[a-z_]+\.[a-z_]+$")
_ROLE_CODE = re.compile(r"^CRM_[A-Z_]+$")
_GATEWAY_PREFIX = re.compile(r"/api/v1/[a-z-]+")


def _sections() -> dict[str, list[str]]:
    """Heading text -> the lines under it, for every `## ` heading of the document."""
    assert CRM_DOC.is_file(), "docs/crm.md does not exist: R1.1 writes it, and R1.2b needs R1.1"
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in CRM_DOC.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            current = sections.setdefault(line[3:].strip(), [])
        elif current is not None:
            current.append(line)
    return sections


def _section(word: str) -> list[str]:
    matches = {title: body for title, body in _sections().items() if word in title.lower()}
    assert len(matches) == 1, (
        f"expected exactly one `## ` heading containing {word!r} in docs/crm.md, "
        f"found {sorted(matches)} among {sorted(_sections())}"
    )
    return next(iter(matches.values()))


def _rows(lines: list[str]) -> list[list[str]]:
    """Every markdown table row under a heading as its cells, backticks and padding removed."""
    rows = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            rows.append([cell.strip().strip("`") for cell in stripped.strip("|").split("|")])
    return rows


def _first_cells(lines: list[str], pattern: re.Pattern[str]) -> set[str]:
    return {row[0] for row in _rows(lines) if row and pattern.match(row[0])}


def _delta(
    found: set[str], expected: set[str], left: str = "the document", right: str = "the contracts"
) -> str:
    return (
        f"in {left} only: {sorted(found - expected)}; in {right} only: {sorted(expected - found)}"
    )


def test_the_event_table_lists_exactly_the_crm_schemas() -> None:
    committed = {
        path.name for path in EVENTS_DIR.glob("crm.*") if (path / "v1.schema.json").is_file()
    }
    found = _first_cells(_section(SECTION_EVENTS), _EVENT_TYPE)
    assert found == committed, "docs/crm.md event table drifted: " + _delta(found, committed)


def test_the_permission_table_lists_exactly_the_manifest_codes() -> None:
    manifest = json.loads(CRM_MANIFEST.read_text(encoding="utf-8"))
    expected = {
        f"{manifest['product']}.{p['module']}.{p['resource']}.{p['action']}"
        for p in manifest["permissions"]
    }
    found = _first_cells(_section(SECTION_PERMISSIONS), _PERMISSION_CODE)
    assert found == expected, "docs/crm.md permission table drifted: " + _delta(found, expected)


def test_the_role_table_lists_exactly_the_manifest_templates() -> None:
    manifest = json.loads(CRM_MANIFEST.read_text(encoding="utf-8"))
    expected = {template["code"] for template in manifest["role_templates"]}
    found = _first_cells(_section(SECTION_PERMISSIONS), _ROLE_CODE)
    assert found == expected, "docs/crm.md role table drifted: " + _delta(found, expected)


def _crm_prefixes_by_service() -> dict[str, dict[str, str]]:
    """Service -> {gateway prefix -> dev port}: the dev table gives the port, the prod table the
    service behind the same prefix (`http://customer:8000`)."""
    dev = {
        route["prefix"]: route["upstream"].rsplit(":", 1)[1]
        for route in json.loads(DEV_ROUTES.read_text(encoding="utf-8"))["routes"]
        if route.get("product") == "crm"
    }
    prod = {
        route["prefix"]: route["upstream"].split("//", 1)[1].split(":", 1)[0]
        for route in json.loads(PROD_ROUTES.read_text(encoding="utf-8"))["routes"]
        if route.get("product") == "crm"
    }
    assert set(dev) == set(prod), (
        "the dev and prod route tables disagree on the CRM prefixes: "
        + _delta(set(dev), set(prod), "the dev table", "the prod table")
    )
    by_service: dict[str, dict[str, str]] = {}
    for prefix, port in dev.items():
        by_service.setdefault(prod[prefix], {})[prefix] = port
    return by_service


def test_the_service_map_ports_match_the_gateway() -> None:
    expected = _crm_prefixes_by_service()
    rows = {
        row[0]: " ".join(row)
        for row in _rows(_section(SECTION_SERVICES))
        if row and row[0] in expected
    }
    assert set(rows) == set(expected), (
        f"docs/crm.md service map should have one row per CRM service {sorted(expected)}, "
        f"has {sorted(rows)}"
    )
    for service, prefixes in expected.items():
        text = rows[service]
        ports = set(prefixes.values())
        assert len(ports) == 1, f"{service} is routed to more than one dev port: {sorted(ports)}"
        (port,) = ports
        assert re.search(rf"\b{port}\b", text), f"service map: {service} should name port {port}"
        mentioned = set(_GATEWAY_PREFIX.findall(text))
        assert mentioned == set(prefixes), f"service map: {service} prefixes drifted: " + _delta(
            mentioned, set(prefixes)
        )
```

Then the page check:

```bash
export R12="${TMPDIR:-/tmp}/crm-r1-2-checks"; mkdir -p "$R12"
cat > "$R12/check-doc-b.sh" <<'SH'
#!/usr/bin/env bash
# R1.2b: docs/crm.md states what R1.2b delivered, in the past tense, and every citation it moved is re-pointed. Run from the repository root.
set -u
FAILED=0
fail()  { printf 'FAIL: %s\n' "$*" >&2; FAILED=1; }
has()   { grep -qF -- "$2" "$1" 2>/dev/null || fail "$1 lacks: $2"; }
lacks() { if grep -qF -- "$2" "$1" 2>/dev/null; then fail "$1 still has: $2"; fi; }
cite()  { local a="${2%-*}" b="${2#*-}"; sed -n "${a},${b}p" "$1" 2>/dev/null | grep -qF -- "$3" || fail "$1:$2 does not contain: $3"; }

D=docs/crm.md; G=docs/ecosystem-gaps.md; A=docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md
[ -f "$D" ] || { echo "docs/crm.md does not exist: R1.1 has not merged" >&2; exit 2; }
# the sentences that called this work planned
lacks "$D" 'R1.2b is planned'
lacks "$D" 'R1.2b plans one'
lacks "$D" 'Two dead development rows'
lacks "$D" '`api`, `relay`, `worker`, `cron`'
lacks "$D" '`api`, `relay`, `cron` (no `worker`)'
lacks "$D" 'Eleven have a'
lacks "$D" 'five keys with no lead key'
lacks "$D" "its default has 28"
lacks "$D" 'Four events have no committed schema'
lacks "$D" 'No `crm.lead.*` event reaches anyone'
lacks "$D" 'is written (`services/lead/app/services.py:465-468,496`)'
lacks "$G" 'nine after R1.2b'
# what the page says instead
has "$D" 'All fifteen have a committed schema'
has "$D" 'tests/infra/test_crm_doc_matches_contracts.py'
has "$D" 'PINNED_AHEAD_OF_PRODUCER'
has "$D" 'tests/infra/test_route_table_parity.py'
has "$D" '`phone_index` is dormant'
has "$G" 'nine `crm.*` keys'
# the facts it states, at the lines it cites
cite services/app-gateway/routes.json 76-79 '/api/v1/leads'
cite services/lead/app/models.py 65-65 'class Lead'
cite services/lead/app/models.py 160-160 'class AssignmentRule'
cite services/notification/app/domain.py 146-157 'crm.lead.converted'
cite services/notification/app/domain.py 371-371 'SUBSCRIBABLE_EVENTS'
cite services/notification/app/domain.py 282-307 'Unresolved placeholders'
cite services/deal/app/services.py 434-441 'occurred_at'
cite services/lead/app/services.py 535-540 'source'
cite services/lead/app/services.py 644-648 'changed'
cite services/lead/app/services.py 1153-1158 'reason'
cite services/lead/app/services.py 1196-1202 'deal_id'
cite services/lead/app/services.py 1122-1129 'owner_changed'
cite services/lead/app/services.py 1146-1160 'EVT_LEAD_CONVERSION_ABORTED'
cite services/lead/app/services.py 1223-1277 'lead.conversion_failed'
cite frontend/opsgrids-client/src/platform/catalogue.ts 46-46 'entitlementKeys: ["crm"]'
cite frontend/opsgrids-client/src/platform/catalogue.ts 70-70 'revenue.opsgrids.com'
cite infrastructure/edge/compose.prod.yml 791-791 'customer,deal,lead,project'
cite infrastructure/edge/compose.prod.yml 851-851 'customer,deal,lead,project'
[ ! -e services/customer/bin/cron ] && [ ! -e services/deal/bin/cron ] && [ ! -e services/lead/bin/cron ] || fail 'a CRM bin/cron is back'
for adr in "$A"; do has "$adr" '`services/lead/app/services.py:1146-1160`'; lacks "$adr" '1149-1163'; done
[ "$FAILED" = 0 ] && echo PASS || { echo FAILED; exit 1; }
SH
```

- [ ] **Step 2: Run them and see the page check fail**

Run: `uv run pytest tests/infra/test_crm_doc_matches_contracts.py -q -p no:cacheprovider`
Expected: `4 passed` if R1.1's tables are accurate (observed against a synthetic page of R1.1's layout and the post-Task-R1.2.14
route tables). This is the one characterization test of the release: it is meant to pass the moment it lands, because Tasks
R1.2.6, R1.2.7 and R1.2.14 made the contracts and the tables agree with the page. **If it fails, the failure names the
drifted row: fix the page, never the test.** If it fails with `expected exactly one \`## \` heading containing ...`, R1.1
renamed a heading: follow it in the `SECTION_*` constants.

Run: `bash "$R12/check-doc-b.sh"`
Expected: exit status 1; stderr lists a `still has` for every `R1.2b is planned`, `R1.2b plans one`, `Two dead development rows`,
the two old entrypoint cells, `Eleven have a`, `five keys with no lead key`, `its default has 28`, the two §11 rows and
`nine after R1.2b`, a `lacks` for each new sentence, and `does not contain` for every citation the release moved.

- [ ] **Step 3: Update the page**

Apply each pair to `docs/crm.md` unless the heading says otherwise. If an `old` block does not match because R1.1's wording
differs, find the sentence with `grep -n "R1.2b" docs/crm.md` and rewrite it in the same sense: the script is the judge.

**Citations that moved** (each is a bare substring replacement, in the place named; the new number is what the file has once
Tasks R1.2.8, R1.2.11, R1.2.14, R1.2.15, R1.2.16 and R1.2.19 are applied, and the script above checks it):

| Where | Old | New | Moved by |
|---|---|---|---|
| §1, first paragraph | `catalogue.ts:47` | `catalogue.ts:46` | R1.2.19 |
| §1, first paragraph | `catalogue.ts:71` | `catalogue.ts:70` | R1.2.19 |
| §2, Routing bullet | `routes.json:76-81` | `routes.json:76-79` | R1.2.14 |
| §5, `leads` bullet | `(:69-155)` | `(:65-157)` | R1.2.15, R1.2.16 |
| §5, `assignment_rules` bullet | `(:158-185)` | `(:160-187)` | R1.2.15, R1.2.16 |
| §8 table, `activity.logged` | `services.py:428-435` | `services.py:434-441` | R1.2.8 |
| §8 table, `lead.created` | `services.py:538-543` | `services.py:535-540` | R1.2.15 |
| §8 table, `lead.updated` | `services.py:647-651` | `services.py:644-648` | R1.2.15 |
| §8 table, `lead.converted` | `services.py:1199-1205` | `services.py:1196-1202` | R1.2.15 |
| §8 table, `lead.conversion_aborted` | `services.py:1156-1161` | `services.py:1153-1158` | R1.2.15 |
| §8, Variables bullet | `domain.py:276-301` | `domain.py:282-307` | R1.2.11 |
| §11, aborted-conversion row | `services.py:1125-1132,1226-1280` | `services.py:1122-1129,1223-1277` | R1.2.15 |
| `docs/adr/0017-...md`, R1.1's amendment | `services.py:1149-1163`, `:1226-1280`, `:1125-1132` | `services.py:1146-1160`, `:1223-1277`, `:1122-1129` | R1.2.15 |

(If a strike-able task was struck at review, O-15, its moves do not happen: leave that row's old number.)

Section 2, the dead-rows bullet and the routing line:

````text old=docs/crm.md#dead-rows
- **Two dead development rows.** The development table also routes `/api/v1/campaigns` to port 8132
  and `/api/v1/tickets` to port 8133 under product `crm` (`services/app-gateway/routes.json:79-80`).
  No service of either name exists, nothing else mentions those ports, and the production table has
  neither prefix. R1.2b is planned to remove the two rows.
````

````text new=docs/crm.md#dead-rows
- **No dead development rows.** The development table once also routed `/api/v1/campaigns` (port
  8132) and `/api/v1/tickets` (port 8133) under product `crm`, to services that never existed.
  R1.2b removed both, and `tests/infra/test_route_table_parity.py` now fails if the development and
  production tables carry different CRM prefixes.
````

Section 2, the three entrypoint cells (the rows are long; replace the cell with its neighbours as context):

````text old=docs/crm.md#entrypoints-customer
99 MB (`infrastructure/edge/memory-budget.json:310`) | `api`, `relay`, `worker`, `cron` | folded into the API
````

````text new=docs/crm.md#entrypoints-customer
99 MB (`infrastructure/edge/memory-budget.json:310`) | `api`, `relay`, `worker` | folded into the API
````

````text old=docs/crm.md#entrypoints-deal
98 MB (`infrastructure/edge/memory-budget.json:320`) | `api`, `relay`, `worker`, `cron` | none: `services/deal/app/consumers.py`
````

````text new=docs/crm.md#entrypoints-deal
98 MB (`infrastructure/edge/memory-budget.json:320`) | `api`, `relay`, `worker` | none: `services/deal/app/consumers.py`
````

````text old=docs/crm.md#entrypoints-lead
98 MB (`infrastructure/edge/memory-budget.json:321`) | `api`, `relay`, `cron` (no `worker`) | none: there is no
````

````text new=docs/crm.md#entrypoints-lead
98 MB (`infrastructure/edge/memory-budget.json:321`) | `api`, `relay` (no `worker`) | none: there is no
````

Section 5, the lead model bullets (`R1.2.15` stopped writing `phone_index`):

````text old=docs/crm.md#leads-columns
- `leads` (`:69-155`): email and phone are sealed, masked and blind-indexed (`email_enc`,
  `email_masked`, `email_index`, `phone_enc`, `phone_masked`, `phone_index`); `source` and `status`
````

````text new=docs/crm.md#leads-columns
- `leads` (`:65-157`): the email is sealed, masked and blind-indexed (`email_enc`, `email_masked`,
  `email_index`) and the phone is sealed and masked (`phone_enc`, `phone_masked`); `source` and `status`
````

````text old=docs/crm.md#phone-index
  `phone_index` is written (`services/lead/app/services.py:465-468,496`) and read by nothing: no
  selector uses it and no index covers it.
````

````text new=docs/crm.md#phone-index
  `phone_index` is dormant: since R1.2b `create_lead` no longer computes it and the column is mapped
  `deferred`, so no statement the service issues names it; no selector uses it and no index covers
  it. It stays for phone-based duplicate detection (R3.7).
````

Section 8, the introduction and the four rows without a schema:

````text old=docs/crm.md#events-intro
`services/deal/app/services.py:43-47`, `services/lead/app/services.py:71-77`). Eleven have a
committed schema, `libs/contracts/events/<type>/v1.schema.json`; eight of those are **ahead of the
producer**: their own `$comment` names the required fields the producer does not emit yet. Four have
no schema. In the table, *Emitted today* is the payload the producer builds, *State* compares it with
the schema's required list (`conforms`, `ahead` with the missing fields, or `no schema`), and
*Subscribable* says whether notification's rule engine lists the key.
````

````text new=docs/crm.md#events-intro
`services/deal/app/services.py:43-47`, `services/lead/app/services.py:71-77`). All fifteen have a
committed schema, `libs/contracts/events/<type>/v1.schema.json`; eight of them are **ahead of the
producer**: their own `$comment` names the required fields the producer does not emit yet. In the
table, *Emitted today* is the payload the producer builds, *State* compares it with the schema's
required list (`conforms`, or `ahead` with the missing fields), and *Subscribable* says whether
notification's rule engine lists the key.
````

For each of these rows replace the tail shown (the producer cell's citation and the four cells after it):

````text old=docs/crm.md#row-contact-created
(`services/customer/app/services.py:695-701`) | none | no schema (R1.2b plans one) | notification | yes |
````

````text new=docs/crm.md#row-contact-created
(`services/customer/app/services.py:695-701`) | yes | conforms; tested against its producer | notification | yes |
````

````text old=docs/crm.md#row-contact-updated
(`services/customer/app/services.py:763-767`) | none | no schema (R1.2b plans one) | none | no |
````

````text new=docs/crm.md#row-contact-updated
(`services/customer/app/services.py:763-767`) | yes | conforms; tested against its producer | none | no |
````

````text old=docs/crm.md#row-contact-deleted
(`services/customer/app/services.py:806`) | none | no schema (R1.2b plans one) | none | no |
````

````text new=docs/crm.md#row-contact-deleted
(`services/customer/app/services.py:806`) | yes | conforms; tested against its producer | none | no |
````

````text old=docs/crm.md#row-activity-logged
(`services/deal/app/services.py:428-435`) | none | no schema (R1.2b plans one) | customer, notification | yes |
````

````text new=docs/crm.md#row-activity-logged
(`services/deal/app/services.py:434-441`) | yes | conforms; tested against its producer | customer, notification | yes |
````

and the four lead rows (their last two cells become `notification | yes`, and their citations move as in the table above):

````text old=docs/crm.md#row-lead-created
(`services/lead/app/services.py:538-543`) | yes | ahead: lacks `status` | none | no |
````

````text new=docs/crm.md#row-lead-created
(`services/lead/app/services.py:535-540`) | yes | ahead: lacks `status` | notification | yes |
````

````text old=docs/crm.md#row-lead-updated
(`services/lead/app/services.py:647-651`) | yes | ahead: lacks `status, source, owner_id, converted_at` | none | no |
````

````text new=docs/crm.md#row-lead-updated
(`services/lead/app/services.py:644-648`) | yes | ahead: lacks `status, source, owner_id, converted_at` | notification | yes |
````

````text old=docs/crm.md#row-lead-converted
(`services/lead/app/services.py:1199-1205`) | yes | ahead: lacks `owner_id, converted_at` | none | no |
````

````text new=docs/crm.md#row-lead-converted
(`services/lead/app/services.py:1196-1202`) | yes | ahead: lacks `owner_id, converted_at` | notification | yes |
````

````text old=docs/crm.md#row-lead-aborted
(`services/lead/app/services.py:1156-1161`) | yes | conforms; tested against its producer (`services/lead/tests/test_lead_lifecycle_races.py:269,292`) | none | no |
````

````text new=docs/crm.md#row-lead-aborted
(`services/lead/app/services.py:1153-1158`) | yes | conforms; tested against its producer (`services/lead/tests/test_lead_lifecycle_races.py:269,292`) | notification | yes |
````

Section 8, three bullets:

````text old=docs/crm.md#publishing
a queue exists only once its consumer has run (`:453-471`). Every `crm.lead.*` event emitted so far
  was therefore dropped by the broker: no consumer binds them.
````

````text new=docs/crm.md#publishing
a queue exists only once its consumer has run (`:453-471`). Every `crm.lead.*` event emitted before
  R1.2b's deploy was therefore dropped by the broker, and none is replayed; notification now binds
  the four lead keys when it starts, so a lead event reaches its queue from that deploy on.
````

````text old=docs/crm.md#notification-bullet
  (`services/notification/app/domain.py:365`). The CRM group is five keys with no lead key
  (`services/notification/app/domain.py:146-151`).
````

````text new=docs/crm.md#notification-bullet
  (`services/notification/app/domain.py:371`). The CRM group is nine keys, the four lead keys among
  them (`services/notification/app/domain.py:146-157`).
````

````text old=docs/crm.md#contracts-bullet
  (`libs/platform-core/src/platform_core/events.py:203-224`). Only `lead` has a producer-side schema
  test, for the abort event; customer and deal have none. The static contracts test requires a
  schema only for the `engage.*` events and for `crm.lead.conversion_aborted`
  (`libs/contracts/tests/test_event_schemas.py:58-114`). R1.2b is planned to close both gaps for the
  CRM.
````

````text new=docs/crm.md#contracts-bullet
  (`libs/platform-core/src/platform_core/events.py:203-224`). Customer, deal and lead each build
  their events through the real writers and check the payload against its schema
  (`services/<service>/tests/test_event_contracts.py`, the eight ahead-of-producer schemas pinned
  there), and the static contracts test requires a schema for every `crm.*` event a service declares
  and every `crm.*` key notification lists (`libs/contracts/tests/test_event_schemas.py`).
````

Section 10, the outbox relays bullet:

````text old=docs/crm.md#relays
  (`infrastructure/edge/compose.prod.yml:791`). ops-monitor reuses the same list for its outbox-lag
  check, but its default has 28: it lacks `lead` (`infrastructure/edge/compose.prod.yml:851`), so a
  stalled lead outbox raises no alert. R1.2b is planned to add `lead` to ops-monitor's default.
````

````text new=docs/crm.md#relays
  (`infrastructure/edge/compose.prod.yml:791`). ops-monitor reuses the same list for its outbox-lag
  check and, since R1.2b, its default is the same 29 names, `lead` included
  (`infrastructure/edge/compose.prod.yml:851`); `tests/infra/test_ops_monitor_compose_env.py` fails
  if the two differ.
````

Section 11: delete these two rows (the 360 row went with Task R1.2.5):

````text old=docs/crm.md#limitation-no-schema
| Four events have no committed schema (§8) | `libs/contracts/events` holds 11 CRM directories for 15 types | R1.2b |
````

````text old=docs/crm.md#limitation-no-lead-events
| No `crm.lead.*` event reaches anyone: none is subscribable and none has a bound queue, so the broker drops them | `services/notification/app/domain.py:146-151`; `libs/platform-core/src/platform_core/events.py:250-262` | R1.2b makes four lead keys subscribable |
````

Section 12, two bullets:

````text old=docs/crm.md#how-event
  `services/notification/app/domain.py`, in the same commit as its schema. R1.2b is planned to pin
  the eight ahead-of-producer schemas: the release that closes one deletes its pin row and the
  schema's `$comment` in the same commit.
````

````text new=docs/crm.md#how-event
  `services/notification/app/domain.py`, in the same commit as its schema. The eight
  ahead-of-producer schemas are pinned (`PINNED_AHEAD_OF_PRODUCER` in
  `libs/contracts/tests/test_event_schemas.py` and each service's `AHEAD_OF_PRODUCER` in its
  `tests/test_event_contracts.py`): the release that closes one deletes its pin rows and the
  schema's `$comment` in the same commit.
````

````text old=docs/crm.md#how-page
- **This page.** R1.2b is planned to add `tests/infra/test_crm_doc_matches_contracts.py`, which
  fails when an event type, a permission code, a role code or a gateway port in §2, §8 or §9 differs
  from `libs/contracts` and `services/app-gateway/routes.json`.
````

````text new=docs/crm.md#how-page
- **This page.** `tests/infra/test_crm_doc_matches_contracts.py` fails when an event type, a
  permission code, a role code or a gateway port in §2, §8 or §9 differs from `libs/contracts` and
  `services/app-gateway/routes.json`.
````

`docs/ecosystem-gaps.md` (R1.1's note under "No product event is consumed by anything"):

````text old=docs/ecosystem-gaps.md#consumed
notification binds the five
`crm.*` keys it lists as subscribable (nine after R1.2b); `deal` and `lead` consume nothing
````

````text new=docs/ecosystem-gaps.md#consumed
notification binds the nine
`crm.*` keys it lists as subscribable (the four lead keys joined in R1.2b); `deal` and `lead` consume nothing
````

`docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md`, the three citations of R1.1's amendment note (the code they
point at moved up three lines when Task R1.2.15 removed three lines above it): `services/lead/app/services.py:1149-1163`
becomes `services/lead/app/services.py:1146-1160`, `(:1226-1280)` becomes `(:1223-1277)` and `(:1125-1132)` becomes
`(:1122-1129)`.

- [ ] **Step 4: Run them and see them pass**

Run: `bash "$R12/check-doc-b.sh"`
Expected: `PASS`.

Run: `grep -n "R1.2b" docs/crm.md docs/ecosystem-gaps.md`
Expected: every remaining line says what R1.2b *did* ("since R1.2b", "R1.2b removed", "before R1.2b's deploy", "joined in
R1.2b"); none says it will.

Run: `uv run pytest tests/infra/test_crm_doc_matches_contracts.py -q -p no:cacheprovider`
Expected: `4 passed`.

Prove each table can fail (not committed): with a scratch edit to `docs/crm.md`, each of these makes exactly one test fail and
names the row (observed on a synthetic page of R1.1's layout), then `git checkout -- docs/crm.md` undoes it:
- delete the `crm.lead.updated` row from the §8 table: `docs/crm.md event table drifted: in the document only: []; in the
  contracts only: ['crm.lead.updated']`;
- add a `crm.book.owner.purge` row to the §9 permission table: `docs/crm.md permission table drifted: in the document only:
  ['crm.book.owner.purge']; in the contracts only: []`;
- delete the `CRM_LEAD_REP` row of the role table: `docs/crm.md role table drifted: ... in the contracts only: ['CRM_LEAD_REP']`;
- change `8131` to `8132` in the `deal` row of the service map: `service map: deal should name port 8131`;
- delete `, \`/api/v1/contacts\`` from the `customer` row: `service map: customer prefixes drifted: in the document only: []; in
  the contracts only: ['/api/v1/contacts']`;
- rename `## 8. Events` to `## 8. Contracts`: `expected exactly one \`## \` heading containing 'event' in docs/crm.md`.

Run: `uv run ruff check tests/infra && uv run ruff format --check tests/infra && uv run pytest tests/infra -q -p no:cacheprovider`
Expected: `All checks passed!`, `already formatted` (observed for the test module), and the whole infra suite passes.

- [ ] **Step 5: Commit**

```bash
git add tests/infra/test_crm_doc_matches_contracts.py docs/crm.md docs/ecosystem-gaps.md docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md
git commit -m "CRM page: state what the contract and cleanup release delivered, and add a test that keeps its tables true"
```

---

### Release R1.2 self-review

Run once, after the last task of each deploy, before the pull requests are marked ready. The release has 25 tasks: R1.2.1 to
R1.2.5 ship as R1.2a, R1.2.6 to R1.2.25 as R1.2b.

**Spec acceptance criteria** (foundations design §5.11), each with the task whose step shows it.

- [ ] **AC-1.2.1** [a] `cd services/customer && uv run pytest tests/test_c1_customer360.py -q` passes with the two `TestOwnWithoutSubjectRef` tests; with `DEAL_LIST_PATH` set to `"/deals"`, `test_full_picture` and `test_deal_is_asked_for_this_account_under_the_callers_identity` fail: Task R1.2.2 (Step 1 writes the tests, Step 4 passes them, Step 6 is the mutation whose output goes in the pull request).
- [ ] **AC-1.2.2** [a] `tests/infra/test_internal_client_paths.py` passes with nine registered calls (A1 to A9) and 17 baseline files, fails four times on the base commit (A1 at 404; A7, A8, A9 without `org`), and the three notification `test_the_token_names_the_organization` pass: Tasks R1.2.1 (the guard; `--runxfail` shows the four), R1.2.2 (A1) and R1.2.3 (A7 to A9 and the three tests; `21 passed`).
- [ ] **AC-1.2.3** [b] `ls libs/contracts/events | grep -c '^crm\.'` prints 15, `make contracts-check` passes, eight `crm.*` schemas say `Ahead of the producer`: Tasks R1.2.6 (three schemas, 14), R1.2.7 (the fourth, 15) and R1.2.10 (the contracts tests, the pinned eight).
- [ ] **AC-1.2.4** [b] the three `test_event_contracts.py` files and `test_phone_index_dormant.py` pass, and `grep -rn 'phone_index' services/lead/app/services.py` prints nothing: Tasks R1.2.6, R1.2.7, R1.2.9 and R1.2.15.
- [ ] **AC-1.2.5** [b] the four lead keys are in `domain.py`; `test_crm_lead_keys.py` passes, the three consent tests included; the old recipe name and id are gone from `frontend/product-engage/src`, `recipes.test.ts` passes, `recipient_kind` is typed, `purpose: "marketing"` occurs once in `recipes.ts`: Tasks R1.2.11 and R1.2.12 (its Step 4 runs each grep).
- [ ] **AC-1.2.6** [b] `tests/infra/test_ops_monitor_compose_env.py` passes, `grep -c 'customer,deal,lead,project'` on the compose file prints 2, and the stale counts are gone from the two deploy documents: Task R1.2.13, Step 4.
- [ ] **AC-1.2.7** [b] the two dev rows are gone and `tests/infra/test_route_table_parity.py` passes: Task R1.2.14, Step 4.
- [ ] **AC-1.2.8** [b] each of the six searches prints nothing: `STATUSES` (Task R1.2.16), `activities_for_deal` (R1.2.17), the three `bin/cron` files (R1.2.18), `ComingSoon` (R1.2.22), `sales` in `catalogue.ts` (R1.2.19) and the seven rollup definitions (R1.2.20); the closing step of each task runs its search.
- [ ] **AC-1.2.9** [b] the typecheck, lint and tests of `@opsgrids/product-crm`, `@opsgrids/product-engage` and `@opsgrids/client`, `make frontend-gate`, the token baseline at 97 offenders in 14 files, and `make ui-visual` with only the three CRM surfaces re-baselined: Tasks R1.2.12 and R1.2.19 to R1.2.22 (the baseline and the visual steps are in R1.2.22, Steps 5 and 6).
- [ ] **AC-1.2.10** [b] `make openapi` leaves a diff only in `libs/contracts/openapi/deal.json`, the permissions manifests are unchanged and `make openapi-check` passes: Task R1.2.23, Steps 3 and 4.
- [ ] **AC-1.2.11** [a+b] `make migration-roundtrip` and `make migration-check` pass and the heads are `e230c025337e`, `e94d7a2c5f18`, `91dd3ca663fd`: no task adds a revision; "Release invariants" above runs both commands at the end of each deploy.
- [ ] **AC-1.2.12** [a] `e2e/edge_product_crud.py` has the step "the 360 shows the deal and its CRM section" between the stage move and the first delete: Task R1.2.4 (the static test; the owner runs the script, O-13).
- [ ] **AC-1.2.13** [b] `tests/infra/test_crm_doc_matches_contracts.py` passes: Task R1.2.25, Step 4.
- [ ] **AC-1.2.14** [a+b] `git diff --name-only <base>...HEAD -- libs ':!libs/contracts' pyproject.toml uv.lock .dockerignore` prints nothing: "Release invariants" (no task edits any of those paths; every `libs/` path in the tasks is under `libs/contracts`).
- [ ] **AC-1.2.15** [a+b] the memory CLI prints 3747 and 3944 before and after: "Release invariants" (no container is added, removed or re-kinded; the compose edits are one environment default and comments).
- [ ] **AC-1.2.16** [a+b] `make gate` is green locally: "Deploy order and verification", step 1.

**Spec scope items** (foundations design §5.1): R1.2-1 is Task R1.2.2; R1.2-2 is Tasks R1.2.1 and R1.2.2; R1.2-3 is Task R1.2.3; R1.2-4 is Tasks R1.2.6 to R1.2.10; R1.2-5 is Tasks R1.2.11 and R1.2.12; R1.2-6 is Task R1.2.13; R1.2-7 is Task R1.2.14; R1.2-8 is Tasks R1.2.16 to R1.2.18; R1.2-9 is Tasks R1.2.19 to R1.2.22; R1.2-10 is Tasks R1.2.2 (the 360's comment), R1.2.10 (the contracts test docstring), R1.2.23 and R1.2.24; R1.2-11 is Task R1.2.15; R1.2-12 is Task R1.2.23; R1.2-13 is Task R1.2.4; R1.2-14 is Task R1.2.25. Registry rows: `C23` R1.2.1, R1.2.2; `C77` and `J13` R1.2.3; `EV4`, `EV5` R1.2.6 to R1.2.8; `EV39` R1.2.11, R1.2.12; `F6` R1.2.13; `C71` R1.2.14; `B40` R1.2.15; `F7` (the `bin/cron` clause) R1.2.18; `G15` R1.2.19 to R1.2.22; `H6` R1.2.23; `I3` R1.2.5 and R1.2.25.

**Review focus for this release.** The input classes the spec implies and no acceptance criterion names, most likely first, each pinned by a test in the task that owns it unless it says otherwise. The plan's header names the five that matter most across the sub-project and points each at its pin; this list is the release's own, and several of its items are the same inputs seen from the task.

1. **An `occurred_at` with no offset, with `+05:30`, or ahead of UTC.** A client that sends local time (`"2026-09-02T09:15:00"`) used to get a 500 from the service whenever the deal already had an activity, and a payload with no offset when it did not. Expected: 201, stored and published as UTC. An Indian client that sends `+05:30` must see the same instant kept, and one that sends its own wall clock for "now" without an offset is up to five and a half hours ahead of UTC and is refused, never stored shifted. Pinned by Task R1.2.8, the offset-less case as the red test and the other two as characterizations (strike-able; the base commit's behaviour for the first is inference, the tests are the proof).
2. **A converted lead with no contact, a contact with no number, and a contact with no opt-in.** A phone-only lead converts with `contact_id: null`; a lead that came in with an email only converts to a contact with no number, which is the common case (`phone: null`, an ordinary state, `services/customer/app/api/internal.py:159-161`); and a contact the tenant never asked to market to must not receive a marketing welcome. Expected: a visible failed run (`recipient_missing`) in the first two cases (a skipped rule with a log line for a rule) and a recorded skip (`consent:no_opt_in`, no message queued) in the third. Pinned by Task R1.2.11 (the automation test, the two no-number tests, the two consent tests and the characterization of the no-purpose default that the recipe's `purpose: "marketing"` exists to avoid). A customer outage during the lookup is the A1 engine's retryable `contact_lookup_unavailable`, already covered (`services/notification/tests/test_a1_automations.py:552`).
3. **A reader with only OWN grants in a tenant without HRMS opening a 360.** Such a reader has no `subject_ref`, so OWN matches nothing. Expected: 403 with deal never asked, and for a mixed grant a `crm` section that reads empty rather than `null`. Pinned by Task R1.2.2 (`TestOwnWithoutSubjectRef`); the underlying scope problem is R2.2's, not fixed here.
4. **Typing a "Send to" path character by character.** The builder drops `recipient_kind` when the path stops ending in `contact_id` and does not bring it back when it ends in `contact_id` again, so a person who deletes one letter and retypes it ends up with a contact id read as a phone number (the helper text changes at once, which is the only signal). Task R1.2.12 pins the design's rule for single changes (`fireEvent.change`) and, as a characterization, the keystroke sequence (delete a letter, type it back: the kind is gone and the helper says address); Task R1.2.11 pins what the service does with the result (the id is refused as a phone number, `recipient_invalid`, and nothing is sent). Restoring the kind when the path is retyped is the reviewer's choice, and the typing test's comment says what it costs.
5. **A rule or automation saved on a lead key, then notification rolled back.** While the keys are subscribable a tenant may author on them; after a rollback the four keys are unbound, the saved rule stays and never fires, and lead events go back to being dropped. Expected: nothing breaks and nothing is lost that was not already unreachable. Not testable inside one checkout (it needs two code versions); recorded under "Rollback" and checked by O-10 on the way out.

**Not applicable to this release.** No database change: R1.2 adds no revision and the heads do not move (`phone_index` stays a column, mapped `deferred`); no new or changed route, permission code or role template (`make openapi` changes one description); no feature flag; no `make edge-reconcile`; no box `.env` edit; no `libs/` change outside `libs/contracts`.

**Where this plan departs from, or adds to, the foundations design.** Each is a correction the plan verified, or text the design leaves to the plan; the owner may strike any of them.

- **No DLQ queue** (Scope reconciliation, item 1): the programme's one-line "ops-monitor coverage" can be read as listing the registry's queues; the design (DD-12, deviation 5) and the code say a queue ships with its fold.
- **No test asserts that `activities_for_deal` is absent from the selectors**: its red step is the import of the helper that does not exist yet, and the absence is the closing `grep`. A permanent absence test would stand in the way of any later release that reads a timeline with a differently shaped selector. The one absence test is `models.STATUSES`, because a third copy of the status vocabulary is a design rule.
- **The `bin/cron` test forbids a relay copy, not a `bin/cron`**, so a CRM service that later gets a real sweep is not blocked.
- **`recipes.test.ts` does not name the old recipe**: `AC-1.2.5` greps the app's `src` for its id and name, so the test pins "the only recipe that starts on a lead event is the converted-lead welcome" instead.
- **The `catalogue.ts` comment does not mention the removed alias**: `AC-1.2.8` greps that file for the word, so the history lives in the commit message and the test name.
- **`DEAL_LIST_PATH` sits after `_TIMELINE_LIMIT`, and the finance comment of `c1_360.py` is rewritten at its own length**, so `_OPEN_STAGES` stays at `:95` where ADR-0038 cites it and the 360's docstring citations stay valid.
- **`test_crm_lead_keys.py`'s 422 test also asserts the refusal's reason**, so it cannot pass on some other defect of the body; the notification token tests assert `claims.get("org")`, so a missing claim fails as `None == '<org>'` and not as a `KeyError`.
- **Citations that the release moves are re-pointed** in R1.1's page, in `docs/ecosystem-gaps.md` and in ADR-0017's amendment note (Tasks R1.2.5 and R1.2.25, with a `cite` check for each new number); the design names the doc-drift test but not these edits.
- **`tools/shard-weights.txt`** (Task R1.2.24) is re-measured for `lead` alone with the file's own header procedure, an agent step that needs local infrastructure.
- **Twelve tests the design does not list**, added when the plan was assembled so that each of the plan header's five Review Focus lines has its pin in the task that owns the code: two in Task R1.2.2 (the leg's page size within the route's declared maximum, and the fields the leg reads always sent), two in Task R1.2.8 (`+05:30` keeps its instant, a naive stamp ahead of UTC is refused), three in Task R1.2.11 (a rule and an automation on a contact with no number, and a contact id read as an address), one in Task R1.2.12 (typing the path back) and the four tests of the new `tests/infra/test_crm_spa_navigation.py` in Task R1.2.22. Each is a characterization or a guard that passes once its task's own change lands, none changes behaviour, and the owner may strike any of them.
- **One paragraph added to Task R1.2.15** records the contractive step of `S§5.2` (B2) with its DDL, which the design specifies and does not build.

**Checks run on this release's tasks.**

- **Placeholder scan.** Searched the release's tasks for the words and phrases that mark unfinished work: none. Every step that changes code shows the code or the exact old and new text, and every command is literal.
- **Type and name consistency.** `DEAL_LIST_PATH`, `Call`, `CALLS`, `UNAUDITED_BASELINE`, `KNOWN_UNREACHABLE`, `KNOWN_UNCLAIMED` (R1.2.1 to R1.2.3); `AHEAD_OF_PRODUCER` per service and `PINNED_AHEAD_OF_PRODUCER` (R1.2.6, R1.2.7, R1.2.9, R1.2.10); the `activities_for_deal` helper (R1.2.17); `namesAContactId`, `RecipientKind`, `recipient_kind` (R1.2.12) are spelled the same wherever they appear, and each task's Interfaces block names what the next one consumes.
- **Code blocks equal the validated files.** Every test module, schema and component pasted in this release was compared with the scratch file it was validated in (all equal); every `old=` block was matched exactly once against R1.1's planned page text or the repository at HEAD (46 of 46); every `lacks` line of the throwaway checks names text that exists today, so each check is red before its edit.
- **Observed, not inferred.** Everything marked observed was run against HEAD `3bc7f6ca` in scratch copies outside the repository (the guard under `/tmp` with the patched services tree, the frontend suites under Node 22.14, `ruff`, `mypy`, `tsc` and `eslint` on the edited files). Everything that needs PostgreSQL, Redis or the broker (the `customer`, `deal`, `lead` and `notification` suites) is marked inference and is the executor's first red-to-green check.
- **The twelve added tests.** `ruff format` and `ruff check` accept the Python ones under the repository's configuration. The 360's two checks and the timestamp conventions were run against the real `libs/contracts/openapi/deal.json`, `c1_360.py` and `ActivityIn` (observed: `size` 200 against a declared maximum of 200; `stage`, `probability` and `created_at` required and never null; `+05:30` kept as the same instant; a naive stamp ahead of UTC refused; a naive stamp in the past accepted and left naive). The navigation test was run on a scratch tree holding HEAD's `main.tsx` and `useNavGroups.ts` (2 failed, 2 passed) and on one holding the edits of Task R1.2.22 (4 passed). The notification, deal and Builder tests need PostgreSQL or Node 22 and are inference, like the tests beside them.

---

## Spec coverage

Every section and requirement of the foundations design (`S§n`), with the task or section of this plan that carries it. A requirement the spec hands to the orchestrator or to another sub-project has no task here and says so. "Header" means a section of this document above the first release; "R1.1 self-review" and "R1.2 self-review" are the closing sections of each release, which map the acceptance criteria one by one.

**§0 and §1: conventions, goal, non-goals**

| Spec | Requirement | Where |
|---|---|---|
| §0 | A proposal is never written as an outcome; `(pending D-n)` marks a default that moves only when the owner answers | Header (Global Constraints); R1.1.1, R1.1.3, R1.1.4, R1.1.5, R1.1.7, R1.1.8, R1.1.9 (wording checks and the `pending D-` counts) |
| §0 | Owner-run steps are prepared by an agent and never run by one | R1.1 "OWNER RUN items" (O-1 to O-3); R1.2 owner-run table (O-4 to O-15); the OWNER RUN lines of R1.2.4, R1.2.11, R1.2.12, R1.2.13 and R1.2.14 |
| §1 | Three independently reversible releases; R1.2 is two deploys so the 360 fix never rolls back with a hygiene change | Release map; R1.1 "Rollback"; R1.2 "Deploy order and verification" and "Rollback" |
| §1 | No migration, table, container, setting, flag or memory change; no change to any permission, role template, route, shape, event payload or event name that exists today | Header (Global Constraints); R1.1.16 (scope check of exactly twenty paths and the memory CLI before and after); R1.2 "Release invariants" (`AC-1.2.11`, `AC-1.2.14`, `AC-1.2.15`) |
| §1 | The eight ahead-of-producer schemas are decided and pinned, not closed | R1.2.6, R1.2.7, R1.2.9 (the per-service `AHEAD_OF_PRODUCER` pins) and R1.2.10 (`PINNED_AHEAD_OF_PRODUCER`, the pinned-eight test) |
| §1 | No class-2 deletion; no change under `libs/` except `libs/contracts` | R1.2.14 to R1.2.22 delete class-1 items only; R1.2 "Release invariants" (`AC-1.2.14`) |
| §1 | No production data operation is required; read-only owner checks accompany each R1.2 deploy | R1.2 owner-run table (O-6, O-7, O-9 to O-11, O-14) |
| §1 | What SP1 delivers against `P§1` (criteria 2, 3 and 6, not 1, 4 or 5) | Header (Goal); R1.1 and R1.2 "Not applicable" sections |

**§2: decisions**

| Spec | Requirement | Where |
|---|---|---|
| §2.1 | All twenty-five decisions recorded with their recommended defaults and `Needed by` releases; the rows that change SP1's own content (D-5, D-7, D-11, D-12, D-13, D-14, D-19, D-20, D-22, D-23, D-24) read as proposals and move documents only | R1.1.1 (the table, the "stage event contract" section, "Open items"); R1.1.3 (glossary rows `stage`, `owner, rep`, `roster`, `membership`, `employee link`, `company`, `contact`, `party, person, rep`, `scope`); R1.1.4 (§9 planned role mapping); R1.2.14 (the `/api/v1/quotations` dev row is product `pulseboard` and stays) |
| DD-1 | One programme ADR, `proposed`, numbered 0038, with the later numbers reserved by a note | R1.1.1 (ADR and the `docs/decisions.md` sentence); R1.1.16 (the number is free on every ref); O-2 |
| DD-2 | Accepted ADRs are amended by dated notes; the only in-place edit is the citation `domain.py:38` to `domain.py:21` | R1.1.7 (five notes, one deleted line), R1.1.8 (ADR-0008 one note, ADR-0020 two) |
| DD-3 | `docs/crm.md` is prose plus tables, and a test ties the tables to the contracts | R1.1.3 to R1.1.5 (the page); R1.2.25 (`tests/infra/test_crm_doc_matches_contracts.py`) |
| DD-4 | The removal rule: no live user, proof in the pull request, replace first otherwise | Header (Global Constraints); the "proof of no live user" step of R1.2.14, R1.2.16 to R1.2.22 |
| DD-5 | The four placeholder routes go now and the 404 gets a real page; SP1 owns the removal | R1.2.22 (routes, nav entries, `NotFound.tsx`, `ComingSoon.tsx`), R1.2.20 (the dead `derive.ts` rollups); recorded in ADR-0038 "What this amends" (R1.1.1); strike-able (O-15) |
| DD-6 | The 360 fix is a path correction in `c1_360.py`, with no new route and no platform-core constant | R1.2.2 (`DEAL_LIST_PATH`) |
| DD-7 | Two tests and a guard close the path-bug class, and the guard watches every service | R1.2.1 (probe, token claim, scan with `UNAUDITED_BASELINE`), R1.2.2 (the mount-aware fake), R1.2.3 (the claim) |
| DD-8 | The four missing contracts match today's payloads exactly | R1.2.6 (three contact schemas), R1.2.7 (the activity schema) |
| DD-9 | The producer moves and the schema never relaxes; R1.2 pins each of the eight gaps | R1.2.6, R1.2.7, R1.2.9, R1.2.10 |
| DD-10 | CRM contract coverage is enforced statically | R1.2.10 (`test_every_crm_event_a_service_declares_has_a_committed_schema`, `test_every_crm_subscribable_key_has_a_committed_schema`) |
| DD-11 | Four lead keys become subscribable and the starter recipe is retargeted, with `purpose: "marketing"` | R1.2.11 (keys, consent trio, redelivery, cross-tenant), R1.2.12 (recipe, `recipient_kind`, two builder rules) |
| DD-12 | The relay list gains `lead`; DLQ queues follow their workers; the stale platform-core comment waits for R2.4 | R1.2.13; the DLQ schedule is pinned in `docs/crm.md` §10 (R1.1.5); no DLQ queue is added (R1.2 "Scope reconciliation") |
| DD-13 | The three `bin/cron` relay copies are deleted, not stubbed | R1.2.18 |
| DD-14 | `activities_for_deal` moves to the tests | R1.2.17 |
| DD-15 | `phone_index` is retired in two steps and the second is conditional | R1.2.15 (step A and the held contractive step B2 with its DDL) |
| DD-16 | D-11 defaults to "keep three processes" with real numbers in front of the owner | R1.1.2 (costing), R1.1.1 (the D-11 row), R1.1.5 (§10) |
| DD-17 | The three READMEs become run instructions only | R1.1.6 |
| DD-18 | The production smoke gains one read-only 360 step | R1.2.4 (the step and its static test); O-13 |
| DD-19 | The CSV formula-injection exposure is recorded, not fixed, with the constraint R3.4 deploys before R3.7 | R1.1.1 (a bullet under "Open items"), R1.1.5 (the tenth row of `docs/crm.md` §11, with the two citations); nothing in `csv.ts` changes |
| DD-20 | Neither R1.2 deploy touches `libs/` except `libs/contracts`, `pyproject.toml`, `uv.lock` or `.dockerignore` | Header (Global Constraints); R1.2 "Release invariants" (`AC-1.2.14`) |

**§3: current state.** Informational; each fact the plan relies on is re-read by a `cite` check (R1.1: 210 lines, all observed passing at HEAD) or labelled observed or inference (R1.2). §3.1 processes, gateway and memory: R1.1.3 (service map), R1.1.5 (§10). §3.2 events: R1.1.4 (§8 table), R1.2.6 to R1.2.10. §3.3 the 360 deal leg: R1.2.2, R1.2.5, O-7. §3.4 the nine internal calls and the 17 unaudited files: R1.2.1, R1.1.4 (§7). §3.5 scaffolds: R1.2.18 (`bin/cron`); the `/info` routes are class 4 and not done. §3.6 stale documents: R1.1.6 to R1.1.15. §3.7 deploy mechanics: R1.1.1 (index sentence), R1.1 and R1.2 deploy sections, O-4, O-5. §3.8 the unmerged insights stack: R1.2 "Files shared with the unmerged insights stack, and how to rebase".

**§4: R1.1**

| Spec | Requirement | Where |
|---|---|---|
| §4.1 | R1.1-1 ADR-0038; R1.1-2 `docs/decisions.md`; R1.1-12 the D-11 costing | R1.1.1; R1.1.1; R1.1.2 (and `docs/crm.md` §10 in R1.1.5) |
| §4.1 | R1.1-3 ADR-0017 notes; R1.1-4 ADR-0008 note; R1.1-5 ADR-0020 notes; R1.1-6 register | R1.1.7; R1.1.8; R1.1.8; R1.1.9 |
| §4.1 | R1.1-7 `docs/crm.md`; R1.1-8 READMEs; R1.1-9 six documents; R1.1-10 delete the pipeline card; R1.1-11 cards A and D | R1.1.3 to R1.1.5; R1.1.6; R1.1.10, R1.1.11, R1.1.12; R1.1.13; R1.1.14, R1.1.15 |
| §4.2 | No table, column, migration, route, permission, role, event, flag, setting or container; the three heads stay | R1.1.16 (scope check); R1.1 "Not applicable" |
| §4.3 | ADR-0038 outline: Context, Decisions, What this amends, C15 named, Costing of D-11, Consequences, Sign-offs, Open items; the row grammar and the answer check | R1.1.1 (and R1.1.2 for the costing section) |
| §4.4 | The glossary of 21 terms | R1.1.3 |
| §4.5 | `docs/crm.md` outline, twelve sections | R1.1.3 (§1 to §5), R1.1.4 (§6 to §9), R1.1.5 (§10 to §12) |
| §4.6 | The three READMEs: run, settings, verify, seed, pointers; no endpoint, permission or event listed | R1.1.6 |
| §4.7 | Corrections to existing documents, one row each | `features.md`, `api-reference.md`, `plan.md`: R1.1.10; `ecosystem-gaps.md`: R1.1.11; `architecture.md:514-515` and `backlog.md:503`: R1.1.12; `architecture.md:143-145` and `:351-360`: R1.2.14 and R1.2.18; onboarding: R1.1.13 to R1.1.15; ADR-0017 rows: R1.1.7; ADR-0008: R1.1.8; register rows: R1.1.9 |
| §4.8 | Costing D-11, reproducible with the repo's own CLI | R1.1.2 (four commands: 3747, 3944, 3649, 3846) |
| §4.9 | Tests and verification: `make test-infra`, `make hygiene`, the searches, the citation pass, the memory totals | R1.1 "How the tasks are tested"; R1.1.16 (steps 2 to 6) |
| §4.10, §4.11 | Rollout and rollback | R1.1 "Deploy order and verification" and "Rollback" |
| §4.12 | `AC-1.1.1` to `AC-1.1.10` | R1.1 self-review, one line each, with the task that proves it |

**§5: R1.2**

| Spec | Requirement | Where |
|---|---|---|
| §5.1 | R1.2-1 the 360 path; R1.2-2 the guard tests; R1.2-3 the organization claim; R1.2-13 the smoke step | R1.2.2; R1.2.1, R1.2.2; R1.2.3; R1.2.4 |
| §5.1 | R1.2-4 contracts, producer tests, pins, offset-less `occurred_at`; R1.2-14 the doc-drift test | R1.2.6 to R1.2.10; R1.2.25 |
| §5.1 | R1.2-5 lead keys and the recipe; R1.2-6 ops-monitor; R1.2-7 two dev rows; R1.2-8 backend removals; R1.2-9 frontend removals | R1.2.11, R1.2.12; R1.2.13; R1.2.14; R1.2.16, R1.2.17, R1.2.18; R1.2.19 to R1.2.22 |
| §5.1 | R1.2-10 stale comments; R1.2-11 `phone_index`; R1.2-12 `make openapi` | R1.2.2, R1.2.10, R1.2.23, R1.2.24; R1.2.15; R1.2.23 |
| §5.2 | No DDL; `phone_index` step A now, B1 in R3.7, B2 held | R1.2.15 (with the held B2 DDL); R1.2 "Release invariants" (`make migration-roundtrip`, `make migration-check`) |
| §5.3.1 | No route added or changed; the 360's existing negative tests stay and two new ones pin the OWN cases | R1.2.2 (`TestOwnWithoutSubjectRef`; the six existing tests listed) |
| §5.3.2 | Layer 1 (mount-aware fake, path assertion, `TestDealLegPath`, mutation check), layer 2 (probe, claim assertion, scan, ratchet) and the smoke step | R1.2.2; R1.2.1 (with the four expected failures and `--runxfail`); R1.2.4 |
| §5.3.3 | The organization claim on notification's three tokens and its three tests | R1.2.3 |
| §5.3.4 | (i) four schemas; (ii) producer-side tests and the offset-less fix; (iii) static coverage; (iv) the eight pins | R1.2.6, R1.2.7; R1.2.6, R1.2.7, R1.2.8, R1.2.9; R1.2.10; R1.2.6, R1.2.7, R1.2.9, R1.2.10 |
| §5.3.5 | Four lead keys, the repaired recipe, consent, Engage typing and the tests | R1.2.11 (keys and the consent trio), R1.2.12 (recipe, `recipient_kind`, two rules, helper line) |
| §5.3.6 | The relay list equal in both containers; the deploy documents corrected; no DLQ queue; the DLQ schedule pinned | R1.2.13; R1.1.5 (§10) |
| §5.3.7 | Two dev rows removed; the CRM prefixes pinned equal | R1.2.14 |
| §5.4 | Class 1, rows 1 to 9 | Row 1: R1.2.14; row 2: R1.2.19; row 3: R1.2.16; row 4: R1.2.17; row 5: R1.2.20; row 6: R1.2.21; row 7: R1.2.18; row 8: R1.2.22; row 9: R1.2.10 |
| §5.4 | Stale text corrected in the same release | R1.2.2 (`c1_360.py`), R1.2.10 (contracts-test docstring), R1.2.13 (deploy documents), R1.2.14 and R1.2.18 (`architecture.md`), R1.2.23 (`ActivityIn`), R1.2.24 (six statements) |
| §5.4 | Classes 2, 3 and 4 are recorded and not done | Spec only; nothing in this plan deletes a class-2 item, and R1.1.1 and R1.1.5 record the one constraint that crosses releases (the CSV export) |
| §5.5, §5.6 | Domain rules; consumers and idempotency (redelivery, cross-tenant) | R1.2.2, R1.2.8, R1.2.11 (`test_a_redelivered_lead_event_is_folded_once`, `test_a_lead_event_never_reaches_another_organizations_rule`) |
| §5.7 | UI: seven rail entries, `NotFound`, no raw tokens, three re-baselined surfaces; Engage recipe and helper; the `sales` alias | R1.2.22, R1.2.12, R1.2.19 |
| §5.8 | The tests table, area by area | 360 path and authz: R1.2.2; internal clients and token claim: R1.2.1, R1.2.3; producer-side events and static coverage: R1.2.6 to R1.2.10; offset-less time: R1.2.8; subscribable keys and recipe: R1.2.11, R1.2.12; ops-monitor: R1.2.13; gateway: R1.2.14; `phone_index`: R1.2.15; docs against contracts: R1.2.25; frontend: R1.2.19 to R1.2.22; visual: R1.2.22; gate: R1.2 "Deploy order and verification" |
| §5.9, §5.10 | Rollout, rollback, stop conditions and ordering | R1.2 "Deploy order and verification", "Rollback", owner-run table |
| §5.11 | `AC-1.2.1` to `AC-1.2.16` | R1.2 self-review, one line each, with the task whose step shows it |

**§6 to §10**

| Spec | Requirement | Where |
|---|---|---|
| §6 | Owner-run steps O-1 to O-15 and their order of use | R1.1 "OWNER RUN items" (O-1 to O-3); R1.2 owner-run table (O-4 to O-15) and "Deploy order and verification"; the box `.env` is not edited by either release (Header) |
| §7 | Risks and their mitigations | The 360 starts returning data: O-7, O-9, R1.2.2. Diagnosis is inference: O-7, the observed probe of R1.2.1. Insights collisions: "Files shared with the unmerged insights stack". Lead events reach notification: R1.2.11. Recipe and consent: R1.2.11 (consent trio), R1.2.12. `phone_index` backfill: R1.2.15 (strike-able). Baselines change twice: R1.2.22 (strike-able). A removal had a live user: the proof step of each removal. Guards couple later work: R1.2.1 rule, `docs/crm.md` §12 (R1.1.5). Whole-ref rollback: two deploys. `date-time` unchecked: R1.2.7 (offset asserted by hand), R1.2.8. ADR-number race: R1.1.1, R1.1.16, O-2. CSV exposure: R1.1.1, R1.1.5. A9 and A6 unwired: R1.1.4 (§7 rows A6 and A9), O-14. The 17 unaudited files: `UNAUDITED_BASELINE`. A deploy from `main` ships more: O-4. The box ceiling is the owner's note: O-5 |
| §8 | Out of scope | R1.1 and R1.2 "Not applicable" sections; no task builds any of it |
| §9.1 | Registry amendments (`G15`, `C77`, `J13`, `C71`, `F14`, `I5`, `I6`, `B40`, `L2`, `EV39`, `EV1`, `EV2`, `EV6` to `EV8`, `EV10` to `EV12`, `EV5`, `C45`, `M`, `N`) | The orchestrator edits the registry; no task does. The code each amendment describes is built by: `G15` R1.2.20 to R1.2.22; `C77` and `J13` R1.2.3 (registry text `str(organization_id)`; the clients' parameter is already a `str`, so `claims={"org": organization_id}` is the same value); `C71` R1.2.14; `F14` R1.2.1, R1.2.6 to R1.2.10, R1.2.13, R1.2.14, R1.2.25; `I5` R1.1.9 to R1.1.12 and R1.2.13; `I6` R1.1.8; `B40` and `L2` R1.2.15; `EV39` R1.2.11 and R1.2.12; the pin rows R1.2.6, R1.2.7, R1.2.9, R1.2.10; `EV5` R1.2.8; `C45`, `M` and `N` need no task |
| §9.2.1 to §9.2.3 | Hand-offs to SP2, SP3, SP4 and the registry | The orchestrator applies them before the specs are approved together; SP1 supplies the files the hand-offs name (`tests/infra/test_internal_client_paths.py`, `test_crm_doc_matches_contracts.py`, the pin tables) |
| §10 | Nine deviations from the programme spec | Each is carried where it bites: 1 and 6 in ADR-0038 (R1.1.1, "Open items" and the D-11 and D-13 rows), 2 in the spec only (no server field is deleted), 3 in the glossary (R1.1.3), 4 in the tasks it names, 5 in R1.2 "Scope reconciliation", 7 in R1.1.6, 8 in the Release map, 9 in R1.2.3 |

**Names the tasks introduce.** The registry says a name used inside one sub-project only and not listed there is that sub-project's to choose, and these are: `DEAL_LIST_PATH`, `RealDealRoutes`, `DEAL_ROUTES` (R1.2.2); `Call`, `CALLS`, `UNAUDITED_BASELINE`, `KNOWN_UNREACHABLE`, `KNOWN_UNCLAIMED` (R1.2.1 to R1.2.3); `AHEAD_OF_PRODUCER` per service and `PINNED_AHEAD_OF_PRODUCER` (R1.2.6, R1.2.7, R1.2.9, R1.2.10); `namesAContactId`, `RecipientKind` and the recipe id `welcome-converted-lead` (R1.2.12); `CRM_SERVICES` (R1.2.18); `NotFoundPage` in `routes/NotFound.tsx`, the name SP3 also uses (R1.2.22). Test names inside a task are the plan's own where the spec describes a group (for example the deal activity tests are `TestActivityLogged::test_an_entry_naming_only_a_deal_matches_its_schema` and its two siblings where the spec says `test_activity_logged_matches_its_schema` for three entries).

---

## Assembly record

What was checked when this plan was assembled from its two release parts (the parts were consumed and deleted), all at HEAD `3bc7f6ca`. **Observed** means run in a scratch copy outside the repository.

- **Placeholder scan.** The plan carries no unfinished-work marker: no to-do words, no deferred-work phrases, no cross-reference that stands in for steps ("see the earlier task"), no vague filler where a value or a command belongs. The only ellipses are code fragments quoted from the repository (`@router.get("/deals", ...)`), and `HOST=…` and `CONFIRM=…` are the owner's own deploy arguments.
- **Task references and numbers.** Every `Task R1.x.y` named anywhere is one of the 41 tasks (R1.1.1 to R1.1.16 and R1.2.1 to R1.2.25). The three Alembic heads were re-derived from the `revision` and `down_revision` pairs and are `e94d7a2c5f18` (deal, three revisions), `e230c025337e` (customer, eight) and `91dd3ca663fd` (lead, five); the memory CLI prints 3747 and, with `admin`, 3944 (observed).
- **Citations.** 367 `path:line` citations resolve to a file and a line range that exist at HEAD; one was corrected (`rebuild-decision.sh:25-39`, the function ends at line 39), and the one that does not resolve is a post-release number by design (`services/deal/app/services.py:434-441` in the R1.2b edit of `docs/crm.md`, once Task R1.2.8 has added lines). All 210 `cite` lines of R1.1 pass against HEAD; the 14 of R1.2 that do not are the post-change line numbers of files the release itself edits, which its tasks check after the edit.
- **Edit blocks.** The 51 `old=` blocks whose file exists at HEAD match it exactly once; 35 edit `docs/crm.md` and ADR-0038, which R1.1 itself creates, and one (`docs/ecosystem-gaps.md#consumed` in Task R1.2.25) edits R1.1's planned wording.
- **R1.1 simulated.** Applying R1.1's create, append, old and new blocks to a scratch copy of the repository (no problems, every `old=` block matched once) and running the ten task checks that do not read git: all ten pass (observed). The five that read `git diff` (ADR-0017, the ADR notes, the register and the two onboarding cards) and the gate were not simulated; the repository's secret hook needs git, so it was stubbed for that run and `detect-secrets scan` was run on the nineteen new or changed documents instead (no finding).
- **`docs/crm.md` simulated across both releases.** The page R1.1.3 to R1.1.5 write, with the thirty-four old and new pairs of Tasks R1.2.5 and R1.2.25 applied in order (each matched exactly once), passes the four tests of `tests/infra/test_crm_doc_matches_contracts.py` against the contracts and route tables as R1.2 leaves them (observed: the four new schemas, the two dead rows removed).
- **Review Focus.** Each of the five lines has its pin in the owning task (names in the Review Focus section). The Python tests pass `ruff format` and `ruff check` under the repository's configuration; the 360's parameter and shape checks and the timestamp conventions were run against the real `deal.json`, `c1_360.py` and `ActivityIn` (observed); the navigation test was run on HEAD's two files (2 failed, 2 passed) and on the files as Task R1.2.22 edits them (4 passed); the notification, deal and Builder tests need PostgreSQL or Node 22 and are inference, like the tests beside them.
- **Names.** `DEAL_LIST_PATH`, `CALLS`, `UNAUDITED_BASELINE`, `KNOWN_UNREACHABLE`, `KNOWN_UNCLAIMED`, `AHEAD_OF_PRODUCER`, `PINNED_AHEAD_OF_PRODUCER`, `recipient_kind`, `RecipientKind`, `namesAContactId`, `NotFoundPage`, `welcome-converted-lead` and the test file names are spelled one way wherever they appear; every registry handle the tasks cite (for example `C23`, `C77`, `C71`, `EV4`, `EV5`, `EV10` to `EV12`, `EV39`, `F5` to `F9`, `B40`, `H6`, `H8`, `I1`, `I3`, `J13`, `G15`, `L2`, `L3`) exists in the registry. The registry has not yet taken the amendments of `S§9.1`; until the orchestrator applies them, the spec's §9.1 and this plan are the authority for the rows it names.
