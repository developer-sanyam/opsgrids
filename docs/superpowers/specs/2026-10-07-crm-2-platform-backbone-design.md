# CRM overhaul — sub-project 2: platform backbone — design

Status: **proposed, design only — pending owner review.** No code, migration, tool run or deploy is part of this
document. Base: `main` @ `caa7f112`; every `path:line` below was re-read at working-tree `HEAD` `3bc7f6ca`, where the
CRM paths are identical. Alembic heads today: deal `e94d7a2c5f18`, customer `e230c025337e`, lead `91dd3ca663fd`
(re-verified from the `revision` / `down_revision` pairs under each `services/<name>/migrations/versions`). Builds on
the programme contract `2026-10-07-crm-overhaul-programme-design.md` (cited **P§n**; its owner decisions **D-n**, its
facts **P§3 F1–F11**) and on the binding interface registry `2026-10-07-crm-overhaul-interface-registry.md` (cited by
the registry's own handles: `A6`, `B20`, `C55`, `EV13`, `F2`, `H1`, `E.2`, `L17`). Its plan is
`docs/superpowers/plans/2026-10-07-crm-2-platform-backbone.md`.

## 0. How to read this document

- **Names are the registry's.** A name, route, column, event, flag, code or file that the registry fixes is used here
  exactly. The places where this spec's first draft added to or tightened a registry row have been folded into the
  registry (its §J.2 holds the handle map and the ledger; §16 here only points there), so this document edits no
  registry row. `C14` and `C15` written without the word "registry" are the open-questions register's slice ids (its
  row 135; ADR-0038 names C15, SP1 §4.3), not rows of the interface registry, whose own `C14` and `C15` are routes.
- **Markers.** `(pending D-n)` is an owner decision still open; the recommended default is what the text builds on and
  moves only when the owner answers. `(inference)` is a conclusion not observed in code. `DD-n` is a design decision
  this spec makes where the programme and registry are silent (§2.2). `AC-r.n` is an acceptance criterion a reviewer
  can check without judgement.
- **Owner-run** means an act only the owner can take (a production read, a box `.env` edit, a fleet write, a deploy).
  An agent builds and tests; it never runs one of these (P§3 F11, ADR-0036 decision 1). §13 collects them.
- **Every release section has the same ten parts:** purpose; data model; backend contract; domain rules; consumers
  and idempotency; UI; tests; rollout; rollback; acceptance. A part that has nothing to say says so in one line.
- **Evidence.** Citations are `path:line` at `HEAD`. Line numbers in `libs/platform-core/src/platform_core/auth.py`
  shift when `fix/ci-failures` (which edits that file) merges; the plan builds against symbols, not lines.

### 0.1 Release map

| Release | One line | Touches | Migration | Needs | Deploy shape | Owner action |
|---|---|---|---|---|---|---|
| R2.1 | Lead hardening | `services/lead`, one event schema | none | R1.1 | `lead` image only | none |
| R2.2 | Membership subject in platform-core | `libs/platform-core`, `tests/infra`, docs | none | R1.1 | **full rebuild** (`libs/` changed: `infrastructure/edge/lib/rebuild-decision.sh:37-38`) | none |
| R2.3 | Configurable stages, expand | `services/deal` (migrations, and `migrations/env.py` for the session `lock_timeout`, DD-52), `services/customer` (360 leg), contracts, visual fixtures | deal: adopted `a1c9e2f7b3d4`, then the stages revision | R1.1, **R1.2a** (the 360 path fix: §3.3, DD-16), D-12 | `deal`, `customer` | a read-only pre-check of deal's sessions and size (§6.8) |
| R2.4 | Aborted-conversion consumers, log-only first | `services/deal`, `services/customer`, `libs/platform-core` (DLQ list), `infrastructure/edge`, docs | none | R2.1, D-1/2/3 | **full rebuild** (the DLQ list lives in `libs/`) | raise `EDGE_BUDGET_ACCEPTED_MB` by 1; D-3 count |
| R2.5 | Stage authoring API and `crm.pipeline.stage.write` | `services/deal`, `services/notification`, contracts, `frontend/product-crm` (render a stage outside the legacy six: §8.6, DD-54, registry `G26`), `remote.sh` and `Makefile` (`ORG=` on the reconcile), visual fixtures | none (tables from R2.3) | R2.3 | the SPA first, then the `deal` and `notification` images in one deploy; "filter before route" met by the reconcile (DD-50) | SPA publish, reconcile on one designated tenant (dry run, then apply), then on the fleet (dry run, then apply) |
| R2.6 | C15 expand | `services/account`, `customer`, `deal`, `lead`, `tools/`, `remote.sh`, `Makefile`, `frontend/product-crm` (owner display only), visual fixtures | journal revision in each of customer, deal, lead; `owners.membership_id` | R2.2, **R2.3** (deal's migration chain: §6.2, DD-51), D-7/8/10 | four images, then the SPA | census, plan review (`show`, `accept`, `link`), SPA publish, plan destruction |
| R2.7 | C15 flip | `services/customer`, `deal`, `lead`, `frontend/product-crm`, `opsgrids-client`, seeds, e2e | none | R2.6, data applied, D-9 | the three CRM images together; SPA last | apply data, sign the security review |
| R2.8 | Role consolidation and permission rollout | the three `catalogue.py`, manifest, tests (including two under `services/account/tests`) | none | R2.5, R2.7, D-20, D-21 | **four** images: the three CRM ones and `account`, whose tests sit in its build context (§11.9) | reconcile (dry run, then apply) |
| R2.9 | C15 contract (optional) | `customer`, SPA; optional email sealing (Part C) | Part C only | R2.7 soaked; Part A also needs R3.4 | per part | optional; D-26 for Part C |

## 1. Goal and non-goals

**Goal.** Build, as nine independently reversible releases (P§2 M2), the identity and lifecycle backbone that
sub-projects 3 and 4 stand on:

1. An aborted lead conversion becomes safe to act on (R2.1) and is then acted on, destructively only after the owner
   flips a flag (R2.4).
2. "Who is the rep" becomes the **membership** for CRM scope: a platform capability that changes nothing by default
   (R2.2), the data that must move first (R2.6), and the flip of customer, deal and lead together (R2.7), so a rep in a
   CRM-only tenant can work (P§1 criterion 1, as reworded by registry J19, pending D-38).
3. The pipeline stops being six literals: tables with the legacy six as the default (R2.3), then an ORG-floored
   authoring API (R2.5).
4. Roles become legible: four templates built by same-code merging, with the permission rollout that gets them to
   live tenants (R2.8).
5. Optionally, the transitional code is removed (R2.9).

**Success is checkable.**

| # | Claim | Proven by |
|---|---|---|
| S1 | After any abort, a retried `:convert` derives ids no earlier attempt used, and the abort names the generation that identifies its ids | AC-2.1-1..7 |
| S2 | CRM compares an owner column with exactly one value, chosen by the resolver; no raw `subject_ref` read remains in `customer`, `deal`, `lead` | AC-2.2-1..9, AC-2.7-2 |
| S3 | No stage-key literal is compared outside the default-pipeline definition, and the legacy six behave as they do today | AC-2.3-1..13 |
| S4 | An orphan is removed only when it is provably the aborted attempt's own, untouched since, and only when the owner has set `CRM_CONVERSION_CLEANUP_MODE=on` | AC-2.4-1..14 |
| S5 | An ORG-scope admin can add, rename, reorder and archive stages; nobody else can; invariants hold under concurrency; and a stage outside the legacy six cannot break the published SPA | AC-2.5-1..15 |
| S6 | Every roster row that evidence supports is linked to a membership, every record that carried a legacy owner id is rewritten with a reversible journal, nothing is guessed, and what is still legacy is counted and recorded | AC-2.6-1..16 |
| S7 | In a CRM-only tenant an OWN-scoped rep sees and edits exactly the rows whose `owner_id` is their membership id, and the owner has signed the review of every OWN route | AC-2.7-1..14 |
| S8 | `CRM_ADMIN`, `CRM_MANAGER`, `CRM_REP`, `CRM_VIEWER` exist in the manifest with the exact grants of §11.3 and reach live tenants through ADR-0007's reconcile | AC-2.8-1..10 |
| S9 | (optional) the one-click owner claim and the `sref`-era residue in `product-crm` are gone, `customer.owners` is kept (L16), and contact-email sealing exists only if the owner said yes | AC-2.9-1..6 |

**Non-goals.** Each is out of this sub-project and is named so a reviewer does not look for it.

- Any redesigned screen, the settings hub, the stage editor screen and saved views (SP3; R3.4 consumes C1–C5). R2.5's hardening of the existing
  board and pill so a stage outside the legacy six cannot break them (§8.6) is a safety change, not a redesign, and SP3 deletes it.
- TEAM scope on CRM rows (R3.8 wires `team_col`; R2.2 only fixes what TEAM *means* for a membership subject).
- A second pipeline per organization, reopening a closed deal, per-stage "hot" flags, custom objects (P§5 scope traps).
- Any link from another product to a company or contact, consent, erasure and fan-out (SP4).
- Opting ITSM, projects or PulseBoard into the membership subject (NEW-8, C14: their own slices; §5.5 lists their sites).
- Hardening the gateway hop token to bind `mem` (DD-9).
- Seat enforcement, a plan change in code, a new container, a new product key (P§5, D-21).
- Rewriting `deal.activities.owner_id`, or lifting its append-only trigger (DD-32).
- The insights service (D-12 fixes only the contract it will consume).

## 2. Decisions

### 2.1 Owner decisions this sub-project needs

Each row's recommendation is the default used throughout this document and the registry (§K). The programme's blocking
column is kept: a plan task that depends on an open row does not start before the owner answers it (P§11).

| ID | Decision | Default this spec builds on | Status | Blocks | Used in |
|---|---|---|---|---|---|
| D-1 | What happens to the orphan **deal** | Soft-delete with an audit row naming the consumer; no `stage_changed` | (pending D-1) | R2.4 | §7 |
| D-2 | How far customer cleanup may go | Only records provably created by the aborted attempt; never a reused record; **log-only** on first deploy | (pending D-2) | R2.4 | §7 |
| D-3 | Do orphans from past aborts exist | The owner counts `lead.audit_logs` rows read-only; the sweep tool is built only if the count is non-zero | (pending D-3) | R2.4 | §7.4, §13 |
| D-4 | Lead bumps `conversion_generation` on every abort and announces the deleted-record bump | Yes to both | (pending D-4) | R2.1 | §4 |
| D-5 | Stage model | One pipeline per org (tables shaped for more); immutable key; editable label, order, tone, probability; outcome `open\|won\|lost`; at least one won and one lost; archive, never delete; closed deals immutable, built as the no-move rule of §6.4 (whether `PATCH` may still edit a closed deal's figures is D-41, which SP3 owns) | (pending D-5) | R2.3 | §6 |
| D-6 | Who edits stages | New `crm.pipeline.stage.write`, route floor ORG; read stays under `crm.pipeline.deal.read`; no new admin template | (pending D-6) | R2.5 | §8 |
| D-7 | What a rep is after C15 | `owners` gains `membership_id`; the Team screen activates org members; free-form rep creation retires after cutover | (pending D-7) | R2.6 | §9 |
| D-8 | Owner-id mapping rule | A reviewed plan; employee-link evidence for employee-valued rows; email evidence needs acceptance; unmapped rows untouched and listed; never guess | (pending D-8) | R2.6 | §9 |
| D-9 | Sign-off for the access widening C15 causes | Approve, with the security review of every OWN route (§10.7) before the flip | (pending D-9) | R2.7 | §10 |
| D-10 | Production CRM census | The owner runs a read-only count: orgs with CRM data, roster sizes, employee links, `lead.outbox` backlog | (pending D-10) | R2.6 | §9, §13 |
| D-12 | Insights vs stages landing order | Stages first; `a1c9e2f7b3d4` adopted byte for byte; insights rebases onto `outcome` | (pending D-12) | R2.3 | §6 |
| D-13 | Memory ceiling | Amended by registry `L17`: every consumer fold is +1 MB. R2.4's deal fold is the first; the owner raises `EDGE_BUDGET_ACCEPTED_MB` by one before it deploys | (pending D-13) | R2.4 deploy | §7.8 |
| D-20 | Role consolidation | The four codes of §11; the six legacy codes and the 17 permission codes frozen | (pending D-20) | R2.8 | §11 |
| D-21 | Plan packaging | Stay flat; retire or fix the INR 299 per-seat `crm/growth` plan by a staff action in the admin console; no code in this programme | (pending D-21) | R2.8 | §11.7 |
| D-24 | TEAM scope for managers | Include; R2.2 fixes its semantics for a membership subject, R3.8 wires it | (pending D-24) | R2.2 semantics | §5 |
| D-25 | May managers reveal the sealed phone or email of their whole team (registry `L20`) | No: `CRM_MANAGER` holds the two reveals at OWN | (pending D-25) | R2.8 | §11.3 |
| D-26 | Seal `contacts.email` at rest (raised by this spec; registry `B56`) | No: R2.9 Part C is built only on a yes and only when its gates hold | (pending D-26) | R2.9 Part C only | §12.8 |
| D-38 | Should a rep read only their own records? (registry `L18`, `J19`) | No: reps read at ORG and write at OWN, as today (P§3 F2); the templates of §11.3 hold the ORG reads | (pending D-38) | R2.8 | §1, §10.1, §11.3 |

D-11 (fold `lead` into `customer`) is context only: the default keeps three processes, and NEW-9 is designed for
three (a deal consumer and a customer consumer, R2.4).

**How an answer is read.** An owner decision is answered when the **State** cell of its row in ADR-0038
(`docs/adr/0038-crm-programme-decisions.md`, registry `I1`; the row grammar is SP1 §4.3) says so, and a release that needs
the answer checks it with one command. For D-9, which needs `approved` alone:
`grep -E '^\| D-9 \|.*\| approved [0-9]{4}-[0-9]{2}-[0-9]{2} \|$' docs/adr/0038-crm-programme-decisions.md` prints the row once D-9
is approved and nothing while it is open. For D-20 and D-21 the same command with `(approved|amended)` in place of `approved`.
Evidence that travels with an answer (D-9's signed security review, a staff plan action) is one line under **Sign-offs**,
`D-n: <what was signed or done> on <date>`, and is never itself the answer. Elsewhere this document says "D-n is approved" and
means this command.

### 2.2 Design decisions made here

Where the programme and registry are silent this spec chooses. Each row names the alternative it rejected.

| # | Decision | Rejected alternative | Why |
|---|---|---|---|
| DD-1 | `finalize_conversion` reads the **pre-bump** generation from the lead row it already holds locked, and keeps its signature | A new `generation` parameter | The claim id match at `lead/app/services.py:1104` proves the row's generation is the claim's (only the holder of the live claim can bump, and a bump clears the claim: `:1116-1117`, `:1260-1261`), so the parameter would be redundant and would change every call site in `test_lead_lifecycle_races.py:215-224` |
| DD-2 | One private payload builder, `_stage_conversion_aborted`, stages the event from both abort paths | Two inline `publish_event` calls | The schema is closed (`additionalProperties:false`); one builder is one place to keep it true, and one place a producer-side test can call |
| DD-3 | The deleted-record event carries the ids **derived** at the pre-bump generation; `contact_id` is the derived id only if the lead holds an email (`email_enc IS NOT NULL`, no decrypt) | Carry the ids customer last returned | In `fail_conversion` there are none: the call failed. The derivation is the same function consumers re-run, so the ids cannot disagree |
| DD-4 | The abort branch bumps whenever `abort_reason is not None`; the `if/elif` at `lead/app/services.py:1108-1113` makes a both-changed abort `status_changed`, bumped once | Prefer `owner_changed` when both changed | Bumping once is what makes ids disjoint; which reason is reported is cosmetic |
| DD-5 | The membership subject is **declared at registration, snapshotted onto `ScopeContext` by `require()`, and read through one `own_ref` accessor** | (a) a per-call `subject=` argument, (b) a lookup inside `apply`, (c) a polymorphic `subject_ref`, (d) the gateway swapping `X-Subject-Ref` per route product | (a) an omitted argument silently reverts to the employee link, and writes and separation-of-duties checks then go wrong; (b) hides a process-global in the security core; (c) makes HR code ambiguous and does not reach `ctx.principal.subject_ref`; (d) moves an authorization decision into the proxy and contradicts ADR-0008 Q5 |
| DD-6 | The registry keeps `(factory, owner_subject)`; re-registering a product with the same subject is a no-op, with a different one raises `ValueError` | Last write wins | `customer` registers `crm` twice, from `api/routes.py:52` and `api/c1_customer360.py:32`; a silent flip of the scope subject is a scope defect |
| DD-7 | The static guard (registry `A11`) has a **self-test** and is **tied to the registration set**: the opted-in service set must equal the set of services whose `scope.py` passes `owner_subject=OwnerSubject.MEMBERSHIP` | An allowlist that starts empty and is trusted | An empty set scans nothing; without the tie a service could opt in and escape the guard |
| DD-8 | Membership TEAM is implemented as registered (`A8`): own rows **or** direct reports, with `or_`; the employee-link TEAM path is byte-for-byte unchanged | Keep TEAM identical for both subjects | A manager with a TEAM write grant could otherwise not touch their own rows (`scope_for` returns the widest grant only: `auth.py:72-83`); nothing reaches TEAM until R3.8 wires `team_col` (pending D-24) |
| DD-9 | Binding `mem` into the gateway hop token is **not** part of R2.2 | Add it now | It touches the gateway and every service's header trust; the forged-header risk is the one `X-Perms` and `X-Subject-Ref` already carry (`gateway.py:659-664` binds `uid` and `org` only); raised with the owner as optional hardening (§14, §15) |
| DD-10 | The stage catalogue is a **virtual default**: an organization with no `pipelines` row reads `DEFAULT_PIPELINE`; the first stage-configuration write materialises the six rows under an organization advisory lock | Seed per organization in the migration, with lazy seeding for organizations without deals | Seeding reaches only organizations that already hold deals (deal has no organization table), so two code paths remain anyway; the migration would carry data the owner cannot rehearse; bare-org tests, fixtures and `e2e/edge_product_crud.py:401-425` need no provisioning |
| DD-11 | `pipeline_stages` points at `pipelines` by a **composite organization-scoped foreign key**; active labels are unique case-insensitively by a partial index; an `UPDATE`-only trigger freezes `key`, `pipeline_id` and `organization_id` (and `outcome`, DD-42). There is **no** delete trigger | Single-column FK (the first text of registry `B20`); no label rule; a delete-blocking trigger | The composite key is the operations precedent (`b7d41e92c05a:106-112`) and stops a cross-tenant pointer the single column would allow (RI checks bypass RLS); unique labels are what lets an import resolve a label to one key; a delete trigger would enlarge ADR-0036's guarded-table set for a path no code takes (a static test bans deleting a stage) |
| DD-12 | Domain functions take an optional `spec: PipelineSpec = DEFAULT_PIPELINE`; `STAGE_ORDER`, `OPEN_STAGES`, `TERMINAL_STAGES`, `STAGE_LABELS` stay as aliases derived from the default | Rewrite every test and caller at once | The pure tests (`test_domain.py:47-113`, `test_stage_transitions.py:23-46`, `test_seed.py:48`) keep calling the domain with no spec, which is how parity is proven |
| DD-13 | Writes that bind a deal to a stage take a **shared** advisory lock on the organization's pipeline; stage-configuration writes take it **exclusively** | A row lock on the pipeline row | While the default is virtual there is no row to lock, and a move racing an archive is exactly the race to close |
| DD-14 | An unknown stage key stays **422** (`ValidationProblem`, field `stage`) | The service-layer 409 `create_deal` raises today (`services.py:126-127`) | Pinned by `test_write_routes.py:195,216` and relied on by the import runner (`runner.ts:93-96,198`: a 409 containing "already" would turn a refused row into a skip) |
| DD-15 | A closing move whose `probability` differs from the pin (won 100, lost 0) is a **422** field error; omitted means the pin | Silently override | The code stores any supplied figure today (`services.py:314-316`) against its own docstring (`:305-308`); every SPA caller already sends none or the pin (`deals.ts:264-294`). This is the one deliberate behaviour change of R2.3 (§6.4) |
| DD-16 | The 360 reads `stage_outcome`, `stage_position` and `probability` and has **no key-literal fallback**: a deal row without `stage_outcome` fails parsing and the CRM section degrades to `null` and is named in `unavailable` | Keep a tuple of the six keys as a fallback | A fallback is the literal this release exists to delete; the two images ship in one deploy, either order of recreation is safe (a new customer against an old deal degrades that one section, an old customer ignores the extra fields), and the degrade path already exists: the failed parse raises (`c1_360.py:171-173`), `_guarded` turns any exception into `None` (`:209-228`) and `assemble` names `crm` in `unavailable` (`:301-302`). Until R1.2a deploys, the leg never reaches deal at all (§3.3), so R2.3 needs R1.2a for its own verification to mean anything |
| DD-17 | Entry-stage failure (no active open stage) is a **422** `BusinessRuleProblem`, never a 409 | A `ConflictProblem` | Lead's client turns **every** deal-side 409 into a permanent "name conflict" (`lead/app/clients.py:215-217`) |
| DD-18 | Orphan **provenance** is: the id equals the id re-derived from `(lead_id, generation)`, and the entity's own creation audit row has `acting_as='lead'`, and that row is the **only** audit row for the entity (a contact must also have `last_activity_at IS NULL`; a deal must have no activity rows). A reuse writes a marker audit row where it writes none today (a company reused by name; a contact reused by email when the lead brings no phone or the phone on file is the same), under a row lock; a contact reuse that already writes `contact.updated` or `contact.reused_for_lead` (`customer/app/services.py:1096-1156`) writes that row and no second | (a) a created-vs-reused flag in the payload, (b) `updated_by IS NULL` alone | (a) needs a customer route, schema and client change and trusts the event; (b) misses adoption by company name and adoption of a contact by email without a phone change, which change nothing on the row and write no audit row (`customer/app/services.py:1193-1195`, `:1111-1112`, `:1144-1145`); a contact adopted with a phone gap-fill does set `updated_by` (`:1123`) and writes `contact.updated`, which the single-audit-row rule already catches |
| DD-19 | The customer handler is **three-phase**: a read-only plan transaction, the deal probe with no transaction open, then the act transaction that re-verifies everything under row locks. A transient probe failure **raises**; `log_only` writes nothing, not even the ledger row | One transaction (`tests/infra/test_io_outside_transaction.py` forbids it); acknowledge on failure | Raising earns the one redelivery and then the DLQ (`events.py:496-516`); acknowledging would lose the cleanup silently |
| DD-20 | `exclude_deal_id` on the references probe removes the excluded deal **only when it is disposable** (already deleted, or live and pristine); the response also carries activity counts (registry `C55`) | Remove it unconditionally (the first text of registry `C55`) | If deal keeps a touched orphan, customer would otherwise delete the company that deal still points at; this makes the answer identical in either consumer order |
| DD-21 | One setting, `CRM_CONVERSION_CLEANUP_MODE`, read by both services, default `log_only` in code and compose; `off` acknowledges and ignores | A deal-only switch | A mixed mode would delete a company while keeping the deal that references it |
| DD-22 | `crm.lead.conversion_aborted` is **not** added to `_GUARANTEED_ROUTING_EVENT_TYPES` | Guarantee routing | A relay that raises on an unroutable event would stall lead's whole outbox when a consumer queue is missing (a rollback to `split-workers` misconfigured), and ops-monitor does not watch lead's outbox until R1.2b |
| DD-23 | Deploy order is **R2.1, then R2.4** | R2.4 first (the consumers-first rule, registry §D) | P§10 lists R2.1 as R2.4's prerequisite; consumers ignore events without a generation, nothing acts before the owner flips `on`, and an event dropped for want of a queue before R2.4 loses nothing a log-only handler would have done |
| DD-24 | Stage keys are generated server-side: slugified label, at most 20 characters, suffixed `_2`, `_3` on collision with **any** key of the pipeline including archived ones; the six legacy keys are reserved | Client-chosen keys; reuse of an archived key | A key is a published identifier (events, audit, tenant automations); reuse would silently re-aim a rule |
| DD-25 | Archiving an open stage moves its live deals to the named stage **keeping each deal's own probability**, bounded at 2,000 deals per call | Reset to the target's default; unbounded | An archive is not a statement about win likelihood; a bound keeps the exclusive lock short (`409` `too_many_deals` tells the admin to move them first) |
| DD-26 | Materialising the default pipeline writes an audit row `pipeline.materialised` (ids only) and **no** stage events | Emit `crm.pipeline.stage.created` six times | Nothing about the pipeline changed; consumers already know the default |
| DD-27 | Notification's skip of `reason = 'stage_archived'` sits in `fold()` before `apply_rules` and `start_runs`; the ledger row is still written | Filter inside each of the two | One decision point, and a redelivery stays idempotent (`consumers.py:94-130`) |
| DD-28 | In **R2.6** every roster lookup resolves a reference by `membership_id` **or** the legacy `id`; after **R2.7** assignment and `owners:validate` accept membership ids only | Switch resolution only at R2.7 | The data step (apply) runs before the code step (flip): between the two, mapped rows carry membership ids while old code still validates by `Owner.id`, which would 404 every edit and every conversion of a mapped row |
| DD-29 | The mapping artifact is three things: the append-only **journal** in each schema (the reversible record), the owner-reviewed **plan** file, and a **run** file the customer apply writes for deal and lead to follow. `revert` writes counter-journal rows (`evidence = 'revert'`) | Overwrite the plan; a journal row updated on revert | Journals are append-only by trigger; the run file makes the three services apply the same subset, in order |
| DD-30 | The tool is a parent with no service import plus one child process per service (`--service customer\|deal\|lead`), and the **customer apply runs first**: it re-validates every accepted membership against account and sets `owners.membership_id` before deal and lead rewrite anything | Three independent applies | `import app` is ambiguous across services (the repo's root rules); the roster must resolve a membership id before any record carries it |
| DD-31 | `apply` skips a lead whose claim was taken within **2 × `CONVERSION_LEASE_SECONDS`** and locks lead rows with `FOR UPDATE SKIP LOCKED` | Exactly the lease; waiting on locks | `finalize_conversion` still honours a claim id the lease has already expired (`services.py:1104`), so a straggler could abort on the rewritten owner; a locked row is in flight by definition |
| DD-32 | `deal.activities.owner_id` is **left untouched**; historical rows keep legacy ids and resolve through the roster's legacy arm | Lift the append-only trigger for one run (ADR-0036 decision 6) | It would bend a declared invariant for a case default roles do not exercise (`CRM_REP`, `CRM_MANAGER`, `CRM_VIEWER` read activities at ORG) |
| DD-33 | The tool stages **no** outbox events and does not touch `updated_at` | Emit `*.updated` per rewritten row | `crm.lead.updated` becomes subscribable in R1.2b (registry `EV39`); a mass event would fire tenant automations |
| DD-34 | R2.7 keeps the **first-run self-claim** working by posting the caller's own `membership_id`; R2.9 removes the button | Break it in R2.7 | A new organization starts with zero reps and `OwnerPicker.tsx:55-90` is how it gets its first; refusing free-form creation without a replacement would strand it for the whole soak |
| DD-35 | New role templates carry **per-slice description sentences** that read as one paragraph when the exporter concatenates them | Identical text in three catalogues | `_merge_template` appends each service's description unless it is already contained (`export_permissions.py:123-124`) |
| DD-36 | The `†` rows of registry `E.2` ship as **OWN** in R2.8 and the `‡` rows stay OWN | Ship TEAM now | `ScopeContext.apply` refuses TEAM when the selector passes no `team_col` (`auth.py:342-347`); R3.8's reconcile raises the scope (ADR-0007 §1) |
| DD-37 | R2.9 sealing of `contacts.email` is **off by default** and specified as a conditional, separately gated part | Seal in R2.9 unconditionally | ADR-0017 accepted plaintext business contact data (register row 95); sealing needs R3.1's nullable email and `email_index` first |
| DD-38 | The R2.2 static guard matches `subject_ref` on **any** object and the string spellings (`"subject_ref"`, `"sref"`, `"X-Subject-Ref"`, `getattr`, subscripts), requires every hand-built `ScopeContext` in an opted-in service to be `Scope.ORG` and the first argument of every `require(...)` there to start with `crm.` (a bare name is resolved through the file's module-level string constants, and an argument it cannot resolve is itself a finding), allows exactly one string spelling, by file **and** constant name (`"X-Subject-Ref"` in `c1_360.py`'s `_IDENTITY_HEADERS`, kept on purpose: §5.5), and **ratchets the six known raw reads and that one spelling** while the opted-in set is empty | The first text of registry `A11`: three object names, an empty set, no ratchet; scanning string-literal `require(...)` arguments only; dropping the header from the 360's forwarded identity | An empty set scans nothing and a seventh raw read could land before R2.7; a hand-built OWN context or a prefix miss would silently keep one route on the employee link; the two reveal guards, the most OWN-sensitive routes in customer and lead, call `require(REVEAL_PERMISSION, …)` with a module constant that a literal-only scan skips; and the 360 forwards the caller's identity to upstreams that may still key OWN on the employee link, so the header must keep travelling |
| DD-39 | `apply` and `assert_can_touch` on an employee-link context are proven unchanged by a **frozen oracle**: verbatim copies of the pre-change bodies compared with the new methods over the full grid of scope, subject, membership, column presence and owner inputs | Relying on the 26 existing tests | They cover single paths; "changes nothing by default" is a claim about the whole grid |
| DD-40 | The 360's "hot" rule becomes `probability >= 70 or stage_is_last_open`, and `DealOut` gains `stage_position` and `stage_is_last_open` | Dropping the `negotiation` clause; a second upstream call to `GET /deals/stages` | Identical to today's rule on the default pipeline (parity, S3), generalises to custom ones, and costs no extra call in a 2-second fan-out |
| DD-41 | Row-local stage facts (probability pin, tone, key pattern, non-negative position, label and key uniqueness, the tenant pointer) are database constraints; **set-level** facts (at least one active open, won and lost stage; closed stages last; dense positions) are `pipeline.validate` under the exclusive lock | Cross-row triggers or deferred constraints for the set rules | A cross-row trigger is racy without the lock and enlarges the purge tool's trigger set; the lock already serialises every writer |
| DD-42 | The identity trigger also freezes `outcome` | The first text of registry `B20`, "immutable once a deal has sat in the stage" (a trigger that reads `deals`) | The API never changes an outcome, so the stronger rule costs nothing and needs no cross-table read; a wrong outcome is archived and replaced |
| DD-43 | `materialise_default_pipeline` is a service function with **no route** before R2.5, and a `GET` never writes | Materialise on the first `GET /deals/stages`, or in the migration | A read that writes would run for every organization that merely opens a screen; the migration case is DD-10 |
| DD-44 | A stored stage key the spec cannot resolve renders open, with the key as its label and position -1, logs `deal.stage.unresolved` (ids only) and never raises | Failing the request | One bad row must not blank a list; the 360 deliberately does the opposite (DD-16) because its fallback would be a literal |
| DD-45 | A stage label may not equal, lower-cased or slugified, a **different** stage's key | Allowing it and letting the importer prefer the key | The importer accepts a label or a key; the server must make that unambiguous |
| DD-46 | The three copies of `conversion_ids` (lead, deal, customer) are held equal by 24 golden vectors in `libs/contracts/vectors/conversion_ids.v1.json` (registry `A22`), asserted by each service's own tests | A shared library function; a subprocess parity test | `import app` is ambiguous across services and platform-core is not the home of a CRM algorithm; vectors are polyglot, like the schemas |
| DD-47 | An unknown `CRM_CONVERSION_CLEANUP_MODE` falls back to `log_only` with an error log | A `Literal` that fails at start | A typo would crash-loop customer and deal, and a case slip must never be read as `on` |
| DD-48 | The historical sweep (H3) replays audit rows through the **same handlers** with synthetic envelopes | A second implementation in SQL | The handlers already hold the safety rules; one decision procedure |
| DD-49 | R2.4 does not extend `e2e/`; the cross-service contract is proven by schema-validated envelopes on both sides | Booting lead, the relay and both consumers in the harness (registry `L14`) | A multi-process broker journey for one near-zero-rate event is slow and brittle; validating the same committed schema on the producer side (R2.1) and the consumer side pins the contract |
| DD-50 | R2.5 deploys `notification` and `deal` together, and "the filter before the route" is enforced by the **reconcile**, which the owner runs after a verified deploy | Two releases or two deploys | Until the reconcile no existing user holds the code, so the route is unreachable; a second deploy buys no safety |
| DD-51 | R2.3 is a **hard prerequisite of R2.6's deal revision**: `d8a35c17e920` is written with `down_revision = "f3b8d1a65c02"` and never against `e94d7a2c5f18`, and the plan merges R2.3 first | An Alembic merge revision (a `down_revision` tuple) for the case where R2.6 merges first; re-parenting the adopted `a1c9e2f7b3d4` | `a1c9e2f7b3d4` is byte-pinned and parented on `e94d7a2c5f18`; any other revision parented there makes two heads, and the second merger could only repair that by editing the pinned file, which K-7, §6.2 and AC-2.3-1 forbid. `alembic upgrade head` (`infrastructure/edge/remote.sh:298`), `make migration-roundtrip` and `make migration-check` all fail on two heads. A merge revision would work but leaves two branches in the chain and makes a downgrade ambiguous, and buys nothing: the release train already puts R2.3 before R2.6 |
| DD-52 | Deal's migration connection runs under a session `lock_timeout` of 5 s, set in `services/deal/migrations/env.py` through `connect_args={"server_settings": {…}}`, so the adopted file's `ADD COLUMN` is bounded without editing it | One added `SET LOCAL lock_timeout` line in `a1c9e2f7b3d4` (breaks the byte identity, the blob-SHA assertion and the insights rebase); `PGOPTIONS` (asyncpg does not read it) | Alembic runs the pending deal revisions in one transaction, so the stages revision's own `SET LOCAL` runs after the statement it would need to bound; a session setting bounds every statement of the chain, the adopted ones included, and a refusal fails the migrate step before `up` with the old containers still serving (`remote.sh:27,539-540`) |
| DD-53 | `GET /deals/stages` has no scope floor on the bare list (stage rows are tenant configuration, like the owner roster), but `include=counts` is honoured **only for a caller whose `crm.pipeline.deal.read` grant is ORG**, and `deal_count` counts **live** deals only; for any other scope the parameter is ignored and no stage carries `deal_count` | Org-wide counts for every reader, soft-deleted deals included (the first text of registry `C1`); counts narrowed through `ctx.apply` | A count is deal-derived data. `GET /deals/summary`, which the same OWN reader gets, is scoped, so an unscoped count beside it would contradict it, and a count that includes soft-deleted deals discloses what no read route shows. A scoped count would give the organization-wide settings table a different number per caller, and `ctx.apply` refuses a TEAM grant while a selector passes no `team_col` (`auth.py:342-347`). The bare list's missing floor departs from P§7 and is recorded in §17 (registry `J32`, `C1`) |
| DD-54 | R2.5 ships a **small SPA hardening and publishes it first** (registry `G26`), so no stage key can crash a screen, and its first reconcile is aimed at **one designated tenant** (`ORG=`), the fleet only after | Waiting for SP3's stage-driven board before any reconcile; trusting that nobody creates a custom stage early | The code reaches users three ways: the fleet reconcile, R2.8's reconcile (`CRM_ADMIN` carries all 22 codes) and the manifest's bind mount for a new activation. Only the first is the owner's to delay, and SP3's board (R3.4) itself needs R2.5 and R2.8 first. The published bundle indexes a six-key record by the deal's stage (`frontend/product-crm/src/data/derive.ts:70`), so the first custom stage on any tenant blanks that tenant's Deals screen. The reconcile wrapper hard-wires `--all` (`remote.sh:456`), which made "exercise it on a designated tenant first" impossible; the tool already takes `--org` (`tools/reconcile_permissions.py:59,328`) |
| DD-55 | The mapping tool is reviewed and amended through its own subcommands (`show`, `accept`, `reject`, `link`, `forget-plan`), and its state directory belongs to the image's own user | The owner hand-edits `plan.json`; a directory owned by the deploy user | The tool runs in a throwaway container as the image user (uid discovered, not assumed: `ensure_ops_monitor_state_dir`, `remote.sh:313-340`, exists for exactly this mismatch). A 700 directory of 600 files that user created can be neither read nor edited by the deploy user; a file the deploy user could read would hold roster emails readable by anyone holding that account |
| DD-56 | `verify` reports what is **still legacy**, not only what is stray: per table and service, `legacy_rows_remaining`, `unconvertible_leads` and `unnamed` members; the owner records the numbers, and what was done with each, before the flip. A hand-entered mapping is a plan row of evidence `manual` (`link`), never a free edit | Reporting strays only; activation that adopts a legacy roster row on the same email | A legacy roster id is not a stray (it resolves in the roster), yet after the flip it matches no OWN scope, `owners:validate` calls it invalid and a lead it owns cannot convert, so "zero strays" can hold with the whole book still legacy-owned. Activation-time adoption would need a new public contract and a new review line for what `plan` already offers on email evidence; `link` also covers what it could not (a roster email that differs from the account email) |
| DD-57 | The access-matrix test **walks each app's live route table** (public routes with permission and floor, internal routes with caller allow-list) and fails on any route the matrix does not list; the matrix gains a row in the release that adds a route | A frozen "33 routes" matrix | 33 is the count at `HEAD` and wrong on the day R2.7 deploys: R2.3 adds `GET /deals/stages`, R2.5 four stage routes and R2.6 `GET /customers/owners/candidates`, so the owner would sign a stale list |

## 3. Current state

What this document builds on, each re-verified at `HEAD`. Where a discovery digest and the code disagreed, the code
won and the line says so.

### 3.1 Scope enforcement (P§3 F1)

- `Principal.membership_id` (`auth.py:68`) is filled from the `mem` claim (`:182`) or `X-Membership-Id` (`:248`);
  `subject_ref` (`:69-70`) from `sref` (`:183`) or `X-Subject-Ref` (`:249`). Auth mints `mem` from the membership
  (`services/auth/app/tokens.py:108-109`) and `sref` from `membership.employee_id` (`services/auth/app/services.py:378,462`;
  `tokens.py:110-111`). The gateway copies both claims to headers (`libs/platform-core/src/platform_core/gateway.py:665-672`)
  and strips both from inbound requests (`:97-117`). **No token, header or gateway change is needed.**
- `register_scope_resolver` (`auth.py:300-301`) writes `_resolvers` (`:297`), which nothing reads: the only other
  `_resolvers` hits in the repo's Python are an unrelated attribute in `services/storefront/app/probes.py`, and no test
  references either symbol (`libs/platform-core/tests`, `services/*/tests`). `ScopeResolver` (`:284-294`) is a structural
  `Protocol` checked by strict mypy in sixteen services, so a new required member would break the build everywhere.
- `ScopeContext.apply` (`:321-355`) and `assert_can_touch` (`:357-381`) hard-wire `principal.subject_ref` (`:337,341,345,350,372`).
  `ORG` returns before any subject is read (`:334-335`, `:370-371`). `require()` builds the context at `:414`.
- `register_scope_resolver` and `ScopeResolver` are exported from `platform_core.auth` (`:509-524`) but **not** from the
  package root: `platform_core/__init__.py:9-13,74-75` re-exports `Scope` and `ScopeContext` only, and every service imports the
  registrar from `platform_core.auth` (`services/customer/app/scope.py:26`).
- Sixteen resolvers register (`services/{attendance,customer,deal,employee,finance,itsm,lead,leave,marketing,notification,operations,project,projects,standup,support,timesheet}/app/scope.py`)
  through seventeen `scope.register()` statements: customer registers `crm` twice, from `api/routes.py:52` and
  `api/c1_customer360.py:32`.
- Every hand-built context is `Scope.ORG`: `customer/app/services.py:155,182`, `deal/app/services.py:90`,
  `finance/app/services.py:203,228`, `operations/app/orders/services.py:1390`, the demo seeds and `e2e/_seed_crm.py:82`.
- CRM enforcement sites: `ctx.apply` ×5 (`customer/app/selectors.py:152,262`; `deal/app/selectors.py:50,122`;
  `lead/app/selectors.py:39`) and `assert_can_touch` ×22 (customer 9: `api/routes.py:251,271,288,352,372,389,416`,
  `services.py:1072`, `api/c1_customer360.py:61`; deal 7: `api/routes.py:115,253,283,303,320,341`, `services.py:209`;
  lead 6: `services.py:449,592,598,845,971`, `api/routes.py:215`). None passes `team_col` or `location_col`.
- Test helpers build `membership_id=uuid.uuid4()` beside a chosen `subject_ref` (`services/customer/tests/helpers.py:108-145`,
  `services/deal/tests/helpers.py:18-50`, `services/lead/tests/helpers.py:108-158`); the tests that pin today's meaning
  are `services/lead/tests/test_authz.py:528-575` (`TestRevealOwnScopeIsKeyedOnSubjectRef`) and
  `services/customer/tests/test_contact_phone_pii.py:383-395`, both of which say they change with C15.

### 3.2 Lead conversion (P§3 F3)

- Claim, call, record: `claim_conversion` (`lead/app/services.py:965-1054`) derives the three ids from the lead id and its
  generation (`:1038`; `lead/app/domain.py:118-145`, namespace `:118`), `finalize_conversion` (`:1057-1223`) records the
  outcome, `fail_conversion` (`:1226-1279`) clears a claim that will not be finalized, `convert_lead` (`:1282-1409`) runs the
  three in order with no transaction open across the two HTTP calls.
- `finalize_conversion` aborts with `status_changed` or `owner_changed` (`:1107-1113`, an `if`/`elif`, so a both-changed
  abort is `status_changed`) and **bumps `conversion_generation` only for `owner_changed`** (`:1125-1132`). It stages
  `crm.lead.conversion_aborted` naming the ids customer and deal **returned** (`:1119-1123`, `:1156-1161`), which may be
  pre-existing records customer reused. The payload carries no generation and no claim id.
- `fail_conversion(bump_generation=True)` runs only when customer answers company-deleted or contact-deleted
  (`:1355-1368`); it writes one audit row and **stages no event** (`:1264-1279`).
- The committed contract has six required fields and a two-value reason enum
  (`libs/contracts/events/crm.lead.conversion_aborted/v1.schema.json`). A repository-wide search finds no consumer of it
  (the only code hits are lead's producer and tests; `docs/insights/events.md:134` records that insights ignores it).
- An abort is rare by construction: `update_lead` refuses a status or owner patch while the 60 s lease is live
  (`:600-615`; `CONVERSION_LEASE_SECONDS = 60`, `lead/app/domain.py:93`), upstream timeouts are 5 s each
  (`lead/app/config.py:22,25`), and the tests reach an abort with a raw `UPDATE` standing in for "a writer these guards
  did not anticipate" (`services/lead/tests/test_lead_lifecycle_races.py:153-162`).
- Customer reuses rather than creates: `ensure_company_and_contact_for_lead` (`customer/app/services.py:1159-1238`) falls
  from the caller's id to the company by name (`:1193-1195`) and the contact by email (`:1216-1223`). Reusing a company by
  name writes **no audit row**. Reusing a contact by email writes one only when the lead brings a phone
  (`_reconcile_reused_contact_phone`, `:1096-1156`): `contact.updated` with `acting_as='lead'`, and `updated_by` set, when the
  contact had no phone (`:1116-1136`), or `contact.reused_for_lead` when a different one is on file (`:1146-1156`); a lead with
  no phone, or one whose masked phone equals the one on file, writes nothing (`:1111-1112`, `:1144-1145`). Rows it creates
  carry `acting_as="lead"` (`:1204`, `:1235`) and exactly one audit row each
  (`:463-474`, `:676-687`; `create_contact` writes none other with a raw phone, `:654-687`).
- Deal's internal route reads the deal by id in **any state** (`deal/app/api/internal.py:123`) and returns a soft-deleted
  deal as a successful replay (pinned by `services/deal/tests/test_lead_conversion_internal.py:97-121`); it opens new deals
  at the literal stage `"lead"` (`:135`). Lead's client turns **every** deal-side 409 into "a deal with that name already
  exists" (`lead/app/clients.py:215-217`).
- A queue exists only once its consumer has run, and an event published with no bound queue is confirmed by the broker
  and marked published, so it is gone (`libs/platform-core/src/platform_core/events.py:250-262`, `:453-471`). Every
  `crm.lead.conversion_aborted` ever staged was therefore dropped; only `lead.audit_logs` (action
  `lead.conversion_aborted`, `lead/app/services.py:1139`) and `lead.outbox` keep a trace, and `tools/outbox.py` cannot
  replay a published row.

### 3.3 Stages (P§3 F4)

- The pipeline is six literals: `deal/app/domain.py:21-39` (`STAGE_ORDER`, `STAGE_LABELS`, `OPEN_STAGES`,
  `TERMINAL_STAGES`), compared by key in `is_open` (`:42-43`), `can_transition` (`:46-56`), `win_rate_pct` (`:81-87`),
  `won_value_minor` (`:90-92`) and `stage_funnel` (`:95-117`). Default probabilities are a dict
  (`deal/app/services.py:49-58`) used at `:143` and `:314-316`; `create_deal` checks the key at `:126-127`; `move_stage`
  (`:296-347`) is the only stage mutation (`DealPatch` has no `stage`: `schemas.py:75-111`).
- The wire pins the six: `DealStage` (`schemas.py:37`) types `DealIn.stage` (`:69`), `StageMoveIn.stage` (`:120`),
  `DealOut.stage` (`:135`) and `StageRollupOut.stage` (`:226`); the committed OpenAPI repeats the enum four times
  (`libs/contracts/openapi/deal.json`). `deals.stage` is `String(20)` with no CHECK (`models.py:73-74`), indexed with the
  organization (`:92`).
- The committed event schemas for `created`, `updated` and `stage_changed` are **ahead of the producer**: each carries a
  `$comment` saying so, and each requires fields the producer does not emit (`services.py:175-183`, `:247-251`,
  `:339-344`). No producer-side test validates a deal payload (`grep validate_event_schema services/deal/tests` is empty).
- The 360 hard-codes the open stages and "hot": `customer/app/c1_360.py:94-97`; `fetch_crm` (`:157-179`) filters open deals
  by the tuple (`:175`), picks the furthest by tuple index (`:176`) and sets hot on `negotiation` or probability ≥ 70
  (`:177`); its tests pin keys (`services/customer/tests/test_c1_customer360.py:134,190`).
- **The 360's deal leg does not work in production today (P§3 F6).** `fetch_crm` requests `{deal_internal_url}/deals`
  (`c1_360.py:167`) while `create_app` mounts every router under `/api/v1` (`libs/platform-core/src/platform_core/app.py:362-363`),
  so deal answers 404 (an inference from the route table, as SP1 §3.3 records; R1.2a's owner read before and after confirms
  it), `fetch_crm` raises (`c1_360.py:171-172`), `_guarded` returns `None` (`:209-228`) and `assemble` names
  `crm` in `unavailable` (`:301-302`): the CRM section is `null` for every contact until the path is fixed. The only
  assertion on the path is the wrong one (`test_c1_customer360.py:154` asserts `/deals`). **SP1's R1.2a owns the fix**
  (registry `C23`: `{deal_internal_url}/api/v1/deals`, proved by a mount-aware fake and a real-path assertion), and R2.3
  rewrites the same function and the same test file, so **R2.3 needs R1.2a** (§0.1, §6.8, §17).
- Seeds and tests pin the keys: `tools/demo/seed_crm.py` (18 deals across the six), `e2e/_seed_crm.py:124-135`,
  `e2e/edge_product_crud.py:401-425` (a fresh organization creates at `lead` and moves to `qualified`), about ten deal test
  files (`test_domain.py`, `test_stage_transitions.py`, `test_crud.py`, `test_write_routes.py`, `test_authz.py`,
  `test_seed.py`, `test_lead_conversion_internal.py`, `conftest.py`).
- The unmerged commit `a4b248f1` (reachable from `sprint/insights-q2`) adds `services/deal/migrations/versions/a1c9e2f7b3d4_add_closed_at.py`
  (`down_revision = "e94d7a2c5f18"`; git blob `be9f7b9f66102b70346d178c8a84299912a5245d`) and the producer fields the committed
  schemas already require: `company_id`, `close_date`, `probability`, `closed_at`, `created_at` on `created`; the full state on
  `updated`; `name`, `value_minor`, `currency`, `owner_id`, `probability`, `closed_at` on `stage_changed`.
- The deal catalogue holds five codes (`deal/app/catalogue.py:104-126`); `CRM_PIPELINE_ADMIN` is `CRM_PIPELINE.all("ORG")`
  (`:139`) and `CRM_DEAL_REP` picks four (`:150-155`). The SPA's tones and defaults that the stage rows must reproduce are
  `frontend/product-crm/src/ui/StatusPill.tsx:61-68` (lead, qualified, proposal `info`; negotiation `warning`; closed won
  `success`; closed lost `danger`) and `data/stages.ts:25-38` (probabilities 20, 40, 55, 70, 100, 0).

### 3.4 Consumers, folding and memory (P§3 F7)

- `customer` is the template. `Settings.run_consumers_in_api` (`customer/app/config.py:23`); the lifespan hooks
  `_start_background_worker` / `_stop_background_worker` build a 2-connection bulkhead engine and start the consumer as a
  supervised task (`customer/app/main.py:24-65`); handlers open their transaction through a swappable seam
  (`customer/app/consumers.py:34-51`); `compose.prod.yml:94` defaults `RUN_CONSUMERS_IN_API` to **true** for every service;
  the rollback is `customer-worker` behind `split-workers` (`:1449-1454`). `deal` and `lead` are listed among the services
  that ignore the flag (`:88-90`): `deal/app/consumers.py:15-29` declares queue `deal.events` with no handler, `deal/app/main.py`
  and `deal/app/config.py` have no fold wiring, and `lead` has no `consumers.py` at all.
- Budget, recomputed read-only with `python3 infrastructure/edge/lib/memory_budget_totals.py infrastructure/edge/memory-budget.json --profiles …`:
  core 1772 MB; `crm` profile 2067 MB; the ten sellable profiles 3747 MB (97.0% of the 3862 MB reference); the ten plus
  `admin` 3944 MB (102.1%). Kinds: `api` 98, `api_with_worker` 99, `background` 90 (`memory-budget.json`); `deal` and `lead`
  are `api`, `customer` is `api_with_worker`. The box's `.env` is recorded as `EDGE_BUDGET_ACCEPTED_MB=3944`, exactly the
  ten-plus-`admin` total (owner memory note, 2026-09-24; **not verifiable from the repo**), so +1 MB needs an owner edit.
- ops-monitor knows one CRM queue: `PROFILE_DLQ_QUEUES["crm"] = ("customer.events",)`
  (`libs/platform-core/src/platform_core/ops_monitor/config.py:101-102`), pinned by
  `libs/platform-core/tests/test_ops_monitor_config.py:75-85`; `tests/infra/test_ops_monitor_dlq_queue_sync.py:36-129`
  derives workers from compose `./bin/worker` services in both directions.
- Rule D3: no network call while a transaction is open (`docs/io-outside-transactions.md`;
  `tests/infra/test_io_outside_transaction.py`, markers `.begin(`, `session_scope(`, `_work_scope(` at `:109`).
- `EventConsumer._handle` redelivers a failing message once and dead-letters the second failure
  (`events.py:496-516`); a handler may therefore raise to earn a second attempt (precedent:
  `notification/app/consumers.py:85-97,257`, `ContactResolutionUnavailable`).

### 3.5 Rep identity (P§3 F2)

- `customer.owners` (`customer/app/models.py:65-92`) is a per-organization roster: `id` is a uuid4 the API cannot set
  (`OwnerIn`, `schemas.py:50-58`), `uq_owners_org_email` is case-sensitive (`:91`), and no column anywhere references it by
  foreign key. Companies, contacts and leads carry roster ids; deals and activities carry roster ids **or the caller's
  employee id**, because `POST /deals` defaults `owner_id` to `ctx.subject_ref` (`deal/app/api/routes.py:112`) and
  `POST /deals/activities` forces it for OWN callers (`:240-243`); deal validates no owner
  (`deal/app/api/internal.py:14-21`); lead validates through customer's `owners:validate`
  (`customer/app/api/internal.py:351-380`, caller `lead` only) with a 60 s process-local cache (`lead/app/services.py:215-336`);
  `lead.assignment_rules.owner_ids` is a `UUID[]` with an integer cursor (`lead/app/models.py:175-178`).
- Every roster lookup is by `Owner.id`: `get_owner` (`customer/app/selectors.py:44-52`), `get_owner_for_update` (`:64-86`, used by
  `_assert_owner_assignable`, `services.py:187-206`), `active_owner_ids` (`:89-102`), `count_open_records_for_owner`
  (`:105-134`, which only counts customer's own companies and contacts), the 360's owner name
  (`customer/app/api/c1_customer360.py:63`) and the SPA's `ownerById` (`frontend/product-crm/src/data/lookups.ts:13`).
- `deal.activities` is append-only by `trg_activities_append_only` (`deal/migrations/versions/e94d7a2c5f18:88-107`); that
  trigger raises for every role.
- Default rep templates read at ORG and write, convert and reveal at OWN (`customer/app/catalogue.py:158-175`,
  `deal/app/catalogue.py:141-156`, `lead/app/catalogue.py:162-178`). With `sref` null an OWN write is refused today
  (`customer/app/services.py:205`; `assert_can_touch` with no subject, `auth.py:370-376`), so a deploy does not empty a rep's list;
  C15 **newly enables** OWN writes, convert and reveal on a rep's own rows (P§3 F2, decision D-9).
- Account holds the evidence: `Membership.employee_id` (`account/app/models.py:334`, unique per organization,
  `:372-380`), `is_owner` (`:329`), `manager_membership_id` (`:341`), and the user's email (`citext`, `:279`, unique among
  live users by `uq_users_email`). There is **no** organization-wide membership list for CRM: the one per-id route answers only to `notification`
  (`account/app/api/internal.py:308-346`).
- The SPA reads the wrong id for CRM: `getMySubjectRef()` returns `decodeAccessToken()?.sref`
  (`frontend/product-crm/src/lib/subject.ts:21`), `AccessClaims` has `sref` and no `mem`
  (`frontend/opsgrids-client/src/auth/token.ts:13-27`), Overview's "My pipeline" uses it (`routes/Overview.tsx:164`),
  and so does the import default owner (`data/importExport/shared.ts:233`). `MeOut.membership_id` is already typed
  (`opsgrids-client/src/auth/types.ts:62`). The owner picker offers `o.id` (`forms/OwnerPicker.tsx:118`) and a one-click
  "make me the owner" (`:46-90`).

### 3.6 Permissions and roles (P§3 F10)

- `libs/contracts/permissions/crm.json` holds 17 codes and six templates from sources `customer`, `deal`, `lead`: three
  admins built with `.all("ORG")` (`CRM_BOOK_ADMIN` 8 grants, `CRM_PIPELINE_ADMIN` 5, `CRM_LEADS_ADMIN` 4) and three default
  reps, all named "Sales representative" (`CRM_SALES_REP` 5, `CRM_DEAL_REP` 4, `CRM_LEAD_REP` 4).
- The exporter merges same-code templates by union with the wider scope winning and requires `name`, `is_default` and
  `cross_product` to agree (`tools/export_permissions.py:116-137`); it **concatenates** descriptions that are not already
  contained (`:123-124`); and it flags **one** admin-most template per contributing service, choosing by a strictly greater
  grant count (`:224-249`, `len(template["grants"]) > len(current["grants"])`). Once a merged `CRM_ADMIN` exists it has more
  grants than any one legacy admin, so it becomes the only `is_admin` template for all three services and the three legacy
  admins stop being flagged. Flagged templates are what owners are granted on activation and on reconcile.
- `install_registration` creates a missing role with the template's `is_default`, widens an existing **system** role by
  union, never edits a name, description or `is_default`, and skips customer-authored roles
  (`account/app/services.py:2032-2135`). `RegistrationDiff.changed` is true only for `grants_added` or `scopes_widened`
  (`:1985-2030`): **creating roles alone does not bump `pv` and is reported unchanged**. `reconcile_registration` also grants
  the flagged templates to active owners of organizations where the product is active (`:2284-2352`;
  `tools/reconcile_permissions.py:200-244`). `_default_role_ids` hands every `is_default` role to a new member (`:575-587`).
- The fleet command is `make edge-reconcile PRODUCTS=crm [APPLY=1]` → `remote.sh reconcile-permissions` (`Makefile:584-587`;
  `infrastructure/edge/remote.sh:434-458`); with `--apply` the wrapper runs the dry run and the apply in one invocation, and it
  hard-wires the fleet (`--all`, `:456`) although the tool it runs already accepts `--org` (`tools/reconcile_permissions.py:59,328`):
  there is no way to give one tenant a new code first, which R2.5 changes (§8.3).
- Pins that change in R2.8: `services/{customer,deal,lead}/tests/test_catalogue.py` (for example
  `customer/tests/test_catalogue.py` asserts exactly one default role, `CRM_SALES_REP`),
  `services/account/tests/test_bundle_install.py:33,86` and `e2e/test_journey.py:36-44,274-277`.

### 3.7 Release machinery (P§3 F11)

- Deploy order: assert-host, checkout, preflight, build, migrate (every service the box's profiles run, **before** the new
  containers serve), `up`, health (`infrastructure/edge/remote.sh:243-301`). Old code therefore runs against the new schema for
  the length of the deploy, so migrations are expand-only; a rollback skips a service whose database is ahead of the image
  (`infrastructure/edge/lib/migration-revisions.sh`, pinned by `tests/infra/test_migration_revisions.py`).
- A diff under `libs/` (other than `libs/contracts`), `pyproject.toml` or `uv.lock` forces a full rebuild and recreate of
  every image (`infrastructure/edge/lib/rebuild-decision.sh:37-38`; the file's own comment, `:18-24`, records the 502 an invitation got
  during one). Applies to R2.2 and R2.4.
- Migration conventions: `SET LOCAL lock_timeout`, named CHECKs, a local `_rls()`, a guarded downgrade
  (`services/operations/migrations/versions/b7d41e92c05a_lots_rolls_genealogy.py:55-118`, downgrade guard `:496-519`,
  `OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE`). `apply_schema_grants` re-grants DML on every table after each migration
  (`libs/platform-core/src/platform_core/db.py:56-87`), so deal's append-only guarantee is a **trigger**
  (`e94d7a2c5f18:88-107`), not a revoke.
- Fleet tools: `tools/reconcile_membership_links.py` enumerates the fleet from the directory DSN, writes on the NOBYPASSRLS
  app role with the tenant GUC bound, one transaction per organization, and its `--dry-run` is an **opt-in** flag
  (`:445-457`); `tools/purge_test_orgs.py:1892-1910` defaults to a read-only dry run and gates a write behind `--apply`,
  `--confirm-count`, `--i-took-a-backup` and `--i-know-this-is-the-live-database`. Registry §H adopts the purge gating.
- Actions billing is blocked: `make gate` locally is the only full verification (`Makefile:525-531`). Merges do not deploy;
  the owner merges and names the ref.

### 3.8 Corrections made while verifying

1. The registry's `A1` re-exports `OwnerSubject` at the package root. `register_scope_resolver` is **not** exported there
   today (§3.1), so R2.2 leaves its import path (`platform_core.auth`) alone and adds `OwnerSubject` to both `auth.__all__`
   and the root. Registry `A2` now records the same.
2. Seventeen `scope.register()` statements feed sixteen registrations. The registry's "16 existing `scope.register()` call
   sites" is the registration count; the idempotence rule (DD-6) is what keeps the seventeenth harmless.
3. There are **22** executable `assert_can_touch` calls and **5** `ctx.apply` calls in the CRM services; "about 27" counted docstrings.
4. R2.2 **and R2.4** are full-rebuild deploys, because R2.4's DLQ-list edit is in `libs/platform-core` (§3.7). The registry's
   first text did not say so for R2.4; its `J31` now records both.
5. A reconcile that only creates roles is reported `changed: false` and bumps nothing (§3.6). R2.8's dry run therefore
   shows `roles_created` for most organizations and a `pv` bump only where grants widen or an owner receives `CRM_ADMIN`.
6. Registry `H1` builds the census in R2.6, but D-3 (R2.4) needs the aborted-conversion count earlier. R2.4's count is a
   read-only SQL the owner runs by hand (§7.4); R2.6's `census --aborted-conversions` repeats it.
7. The roster lookups are **seven** call sites, not three (`get_owner`, `get_owner_for_update`, `active_owner_ids`,
   `count_open_records_for_owner`, the 360's owner read, the SPA's `ownerById`, and the picker's option values); R2.6's
   dual-resolving change (DD-28) has to reach all seven.

## 4. R2.1 — Lead hardening

### 4.1 Purpose

Make an aborted conversion's ids disjoint from every later attempt's ids, make every abort announce the generation and
claim that identify those ids, and give the deleted-record bump the event ADR-0017 says it has. This is the precondition
of every destructive consumer (R2.4): until it ships, a retry after a `status_changed` abort derives the **same** ids
(`lead/app/services.py:1125-1132` bumps only for `owner_changed`), so a cleanup could kill the records of a retry that
already succeeded. Lead only, no migration (decision D-4, pending).

### 4.2 Data model

None. `lead.leads.conversion_generation` (`INTEGER NOT NULL DEFAULT 0`) already exists
(`lead/migrations/versions/91dd3ca663fd_conversion_generation.py:20-25`; `lead/app/models.py:132-134`). No table, column,
index, grant, RLS or trigger changes; lead's head stays `91dd3ca663fd`. There is no downgrade to design.

### 4.3 Backend contract

No route and no OpenAPI change. One event contract changes (registry `EV13`), in the same commit as its producer.

**`crm.lead.conversion_aborted` v1** — producer `lead`, `aggregate_type` `lead`, `aggregate_id` the lead id,
schema `libs/contracts/events/crm.lead.conversion_aborted/v1.schema.json`.

| Field | Type | Meaning |
|---|---|---|
| `organization_id` | uuid | |
| `lead_id` | uuid | |
| `reason` | `status_changed` \| `owner_changed` \| `record_deleted` | `record_deleted` is new (D-4's second bump) |
| `generation` | integer ≥ 0 | **new, required.** The generation the attempt's three ids were derived under: the value **before** the bump (`ConversionClaim.generation`, `services.py:927-929`) |
| `claim_id` | uuid | **new, required.** The claim of the attempt that ended |
| `company_id` | uuid | finalize aborts: the id customer **returned** (it may be a reused record); `record_deleted`: derived at `generation` |
| `contact_id` | uuid \| null | as today; `record_deleted`: derived at `generation` when the lead holds an email, else null |
| `deal_id` | uuid | finalize aborts: the id deal returned (always equal to the derived id: deal creates at the caller's id); `record_deleted`: derived |

The schema after R2.1 (the whole file, because the shape is the contract):

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "opsgrids://contracts/crm.lead.conversion_aborted/v1",
  "title": "crm.lead.conversion_aborted",
  "description": "A lead's conversion attempt ended without writing 'converted'. The lead's status or owner changed after the conversion was claimed (status_changed, owner_changed), or customer reported that a record an earlier attempt created had since been deleted (record_deleted). Every one of these bumps the lead's conversion generation, so a retried conversion derives ids no aborted attempt used. `generation` is the value before that bump: a consumer re-derives the attempt's company, contact and deal ids from (lead_id, generation) and must ignore an event that carries none. The ids named in a finalize abort are the ones customer and deal returned, which may be records customer matched by name or email rather than created; they prove nothing about provenance. contact_id is null when the lead carried no email.",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "organization_id", "lead_id", "reason", "generation", "claim_id",
    "company_id", "contact_id", "deal_id"
  ],
  "properties": {
    "organization_id": { "type": "string", "format": "uuid" },
    "lead_id": { "type": "string", "format": "uuid" },
    "reason": { "enum": ["status_changed", "owner_changed", "record_deleted"] },
    "generation": { "type": "integer", "minimum": 0 },
    "claim_id": { "type": "string", "format": "uuid" },
    "company_id": { "type": "string", "format": "uuid" },
    "contact_id": { "type": ["string", "null"], "format": "uuid" },
    "deal_id": { "type": "string", "format": "uuid" }
  }
}
```

Adding two **required** properties is a tightening, not an additive change; it is safe here because no code consumes this
event (§3.2) and the producer and the schema change in one commit. Consumers added in R2.4 fail closed on an event with no
`generation`.

**What each path does after R2.1.**

| Path | Trigger | Bump | Event | `reason` | `generation` |
|---|---|---|---|---|---|
| `finalize_conversion` abort | status moved off `qualified` | +1, once | yes | `status_changed` | before the bump |
| `finalize_conversion` abort | owner differs from the claim's | +1, once | yes | `owner_changed` | before the bump |
| `finalize_conversion` abort | both moved | +1, once | yes | `status_changed` (DD-4) | before the bump |
| `fail_conversion(bump_generation=True)` | customer answers `company-deleted` / `contact-deleted` (`lead/app/services.py:1355-1368`) | +1 | **yes (new)** | `record_deleted` | before the bump |
| `fail_conversion()` | rejection, name conflict, unavailable, unknown owner | none | none | — | — |
| `finalize_conversion` with a stale claim | claim taken over | none | none | — | — |

**Audit rows** keep their keys and gain two: the `lead.conversion_aborted` row's `after` keeps `conversion_generation`
(now always present, the post-bump value) and gains `claim_id` and `aborted_generation` (the pre-bump value); the
`lead.conversion_failed` row gains the same two when it bumps. Historical rows have neither new key (an old `owner_changed`
row already carries `conversion_generation`, an old `status_changed` row carries no generation at all), which the sweep of §7.4
handles by re-reading the lead.

### 4.4 Domain rules

1. Every abort branch of `finalize_conversion` bumps `Lead.conversion_generation` by exactly one (DD-4). The branch's
   `if abort_reason == "owner_changed"` guard at `:1125` becomes unconditional inside `if abort_reason is not None`.
2. The event's `generation` is read from the locked lead row **before** the increment (DD-1). The claim id match at `:1104`
   guarantees it equals the claim's generation.
3. After an abort, the next `claim_conversion` derives `domain.conversion_ids(lead.id, generation + 1)` and a deal name
   suffixed `/{generation + 1}` (`lead/app/domain.py:140`, `:172`). Company and contact are still found by **name and
   email** (`customer/app/services.py:1193-1223`); the parked **deal** is therefore the record that is left as an
   orphan, which is exactly what R2.4 removes. Until R2.4 is switched on, one extra live orphan deal per abort accumulates;
   the volume is near zero (§3.2).
4. `fail_conversion(bump_generation=True)` stages the event in the same transaction as the bump and its audit row;
   `bump_generation=False` stages nothing. The derived contact id is included only when `lead.email_enc IS NOT NULL`
   (no decryption; DD-3).
5. A private `_stage_conversion_aborted(session, ctx, lead, *, reason, claim_id, generation, company_id, contact_id, deal_id)`
   is the only caller of `publish_event` for this event type (DD-2).
6. Nothing else changes: the 60 s lease, the claim, the short-circuit of an already-converted lead, the idempotent
   replay of ids.

### 4.5 Consumers and idempotency

None in R2.1. The producer side is idempotent by construction: both functions return without writing when the row no
longer holds the claim (`:1104`, `:1257-1258`), so a duplicate or late call stages no second event and bumps nothing.

### 4.6 UI

None.

### 4.7 Tests

All run in lead's own process (`import app` is ambiguous across services; the repo's root rules).

- `services/lead/tests/test_lead_lifecycle_races.py`, in `TestAnAbortedConversionLeavesATrail` (`:203-299`):
  `test_a_status_abort_bumps_the_generation_and_names_it`, `test_an_owner_abort_bumps_exactly_once_and_names_the_old_generation`,
  `test_an_abort_where_status_and_owner_both_moved_bumps_once_and_reports_status_changed`,
  `test_the_payload_carries_the_claim_id_of_the_attempt_that_ended`,
  `test_a_retried_convert_after_any_abort_derives_different_ids` (calls `claim_conversion` again, asserts the three ids differ
  from the first claim's, equal `conversion_ids(lead_id, 1)`, and that `deal_name` gains `/1`),
  `test_a_stale_claim_stages_no_second_event_and_bumps_nothing` (attempt A claims, its lease is back-dated, attempt B takes
  over and aborts, A's `finalize_conversion` answers 409 and the outbox holds one event). Every payload goes through
  `validate_event_schema("crm.lead.conversion_aborted", 1, payload, contracts_root=CONTRACTS_ROOT)`; the two existing
  assertions on the old payload are updated in the same commit.
- `services/lead/tests/test_convert.py`, in `TestGenerationsAndDealNames` (`:596-700`):
  `test_a_deleted_company_bumps_and_announces_record_deleted`, `test_a_deleted_contact_bumps_and_announces_record_deleted`
  (the derived ids at the pre-bump generation, validated against the schema),
  `test_record_deleted_has_a_null_contact_id_when_the_lead_has_no_email`, and
  `test_a_failure_that_does_not_bump_stages_no_aborted_event` (parametrised over `OwnerUnknown`, `DealNameConflict`,
  `ConversionRejected`, `ConversionUnavailable`).
- `services/lead/tests/test_aborted_event_builder.py` (new): an AST test that `services/lead/app/services.py` calls
  `publish_event` with `EVT_LEAD_CONVERSION_ABORTED` in exactly one place, `_stage_conversion_aborted`.
- `libs/contracts/tests/test_event_schemas.py` (extend `:94-104`):
  `test_the_lead_abort_schema_requires_a_generation_and_a_claim_and_names_three_reasons` (static JSON assertions, so it runs
  in the contracts job without opening a database). The existing existence test stays.
- Gates: `make contracts-check`, `make openapi-check` (unchanged), `make hygiene`, per-service `pytest` and `mypy` for lead.

### 4.8 Rollout

1. Review, owner squash-merge.
2. Owner-named `make deploy HOST=… CONFIRM=… REF=<sha>`. The diff touches `services/lead` and `libs/contracts` only, and
   `libs/contracts` is excluded from the full-rebuild rule (`rebuild-decision.sh:38`, `:18-24`), so only the `lead` image rebuilds.
   The migrate step finds nothing to run for lead.
3. Verify: `make edge-status` (lead healthy, `RestartCount` zero), `make edge-logs SERVICE=lead`. The schema file is not
   in the image (`.dockerignore`), so there is nothing to check at runtime.
4. Deploy order against R2.4 is DD-23: R2.1 first. Events staged before R2.4 reaches the box are dropped for want of a
   queue, which loses nothing: no consumer acts until the owner sets `CRM_CONVERSION_CLEANUP_MODE=on`.

### 4.9 Rollback

Code-only: redeploy the previous ref. There is no schema to undo. Generations already bumped stay bumped; the previous
code reads the same column and simply derives the next set of ids. Events already staged sit in the outbox and are
dropped by the relay. **Stop conditions:** a rise in `lead.conversion_failed` audit rows, any lead that cannot convert after
an abort, or any `:convert` 5xx that the previous release did not produce.

### 4.10 Acceptance

- **AC-2.1-1** The committed schema requires `generation` and `claim_id` and enumerates the three reasons (static test in `libs/contracts/tests`).
- **AC-2.1-2** After a `status_changed` abort the lead's `conversion_generation` is its previous value plus one (test).
- **AC-2.1-3** For each of the three reasons the payload the real write path stages validates against the committed schema (tests).
- **AC-2.1-4** A `claim_conversion` after any abort returns ids that differ from the aborted attempt's and equal `conversion_ids(lead_id, generation + 1)` (test).
- **AC-2.1-5** `fail_conversion` without a bump stages no `crm.lead.conversion_aborted` row (test).
- **AC-2.1-6** `publish_event` for this event type has one call site (AST test).
- **AC-2.1-7** `services/lead/migrations/versions` gains no file, so lead's one head stays `91dd3ca663fd` (`make migration-roundtrip`, review).

## 5. R2.2 — The membership subject in platform-core

### 5.1 Purpose

Give platform-core the ability to key OWN (and TEAM) on `Principal.membership_id` instead of `Principal.subject_ref`,
**without any service using it yet** (registry NEW-7, `A1`–`A12`). Today the resolver registry is written and never read
(`auth.py:297-301`), and `apply` and `assert_can_touch` hard-wire the employee link (`:337-350`, `:372-376`), which is why
a CRM-only tenant's rep (no employee link, `sref` null) can neither be listed nor touch a row at OWN. R2.2 ships the
mechanism, its fail-closed semantics, the proof that every product that does not opt in behaves byte for byte as before,
and the static guard that R2.7 arms. No product behaviour changes; that is the whole point of releasing it alone.

### 5.2 Data model

None. No table, column, migration, grant, RLS policy or trigger. There is nothing to downgrade.

### 5.3 Backend contract

A library change in `libs/platform-core/src/platform_core/auth.py`. No route, permission, event, OpenAPI file or
settings key changes (`make openapi-check` and `make contracts-check` pass with no regeneration).

```python
class OwnerSubject(enum.StrEnum):                                   # A1, in auth.__all__ and re-exported at the package root
    EMPLOYEE_LINK = "employee_link"     # Principal.subject_ref: today's behaviour, the default everywhere
    MEMBERSHIP = "membership"           # Principal.membership_id

_resolvers: dict[str, tuple[Callable[[], ScopeResolver], OwnerSubject]] = {}

def register_scope_resolver(                                        # A2 (DD-6)
    product: str,
    factory: Callable[[], ScopeResolver],
    *,
    owner_subject: OwnerSubject = OwnerSubject.EMPLOYEE_LINK,
) -> None:
    existing = _resolvers.get(product)
    if existing is not None:
        if existing[1] is not owner_subject:
            raise ValueError(
                f"scope resolver for {product!r} is already registered with owner subject "
                f"{existing[1].value!r}; refusing to re-register it as {owner_subject.value!r}"
            )
        return                                                      # same subject: a no-op, the first factory is kept
    _resolvers[product] = (factory, owner_subject)

def owner_subject_for(permission: str) -> OwnerSubject:             # A3
    entry = _resolvers.get(permission.partition(".")[0])
    return entry[1] if entry is not None else OwnerSubject.EMPLOYEE_LINK

def reset_scope_resolvers() -> None:                                # A4, a test hook beside reset_jwks_cache
    _resolvers.clear()

class ScopeContext(BaseModel):
    principal: Principal
    scope: Scope
    permission: str
    owner_subject: OwnerSubject = OwnerSubject.EMPLOYEE_LINK        # A5

    @property
    def own_ref(self) -> uuid.UUID | None:                          # A6
        if self.owner_subject is OwnerSubject.MEMBERSHIP:
            return self.principal.membership_id
        return self.principal.subject_ref
```

`require()` passes `owner_subject=owner_subject_for(permission)` into the `ScopeContext` it builds (`auth.py:414`). The call
sits **inside** `_dependency`, so it runs per request, after every module has imported and registered, and a route
declared before its service's `scope.register()` line still resolves correctly (DD-5). `ScopeResolver`
(`:284-294`) is not changed (a new required member breaks strict mypy in sixteen services).

`ScopeContext.apply` and `assert_can_touch` keep their signatures and read `own_ref` instead of `principal.subject_ref`.
`ScopeContext.subject_ref` (`:317-319`) keeps meaning the raw employee link for HR code (`A7`); the docstring-only half of `A7` that
lives in this file (the module docstring `:3-8`, the `Principal.subject_ref` comment `:69`, the `ScopeResolver` docstring `:285-290`)
is rewritten here to say "employee link" and to describe the membership subject, and `docs/architecture.md:261,302-306` follows; the
three CRM `scope.py` headers change with the flip (§10.3). The exact behaviour is the truth table of §5.4.

Messages and statuses are unchanged: a missing permission is `403 Missing permission: <code>`, a missing floor
`403 <code> requires ORG scope; this grant is OWN`, a refused rung `403 TEAM scope is not supported on this resource`, an
out-of-scope object `403 Out of scope for this resource`.

### 5.4 Domain rules

`apply(stmt, *, owner_col, team_col=None, location_col=None)`:

| Scope | `owner_subject` | `own_ref` | `team_col` / `location_col` | Result |
|---|---|---|---|---|
| ORG | either | any | any | the statement, unchanged (the same object), before any subject is read |
| OWN | either | set | — | `owner_col = own_ref` |
| OWN | either | `None` | — | `WHERE false` (never `owner_col = NULL`) |
| TEAM | either | any | `team_col is None` | `ForbiddenProblem("TEAM scope is not supported on this resource")` |
| TEAM | `EMPLOYEE_LINK` | set | set | `team_col = own_ref` (exactly today's) |
| TEAM | `MEMBERSHIP` | set | set | `owner_col = own_ref OR team_col = own_ref` (`A8`, DD-8, pending D-24) |
| TEAM | either | `None` | set | `WHERE false` (never `team_col IS NULL`) |
| LOCATION | either | any | `location_col is None` | `ForbiddenProblem("LOCATION scope is not supported on this resource")` |
| LOCATION | either | any | set | `location_col IN principal.locations` |

`assert_can_touch(*, owner_id, team_owner_id=None, location_code=None)`:

| Scope | `owner_subject` | Passes when |
|---|---|---|
| ORG | either | always |
| OWN | either | `own_ref is not None and owner_id == own_ref` |
| TEAM | `EMPLOYEE_LINK` | `own_ref is not None and team_owner_id == own_ref` |
| TEAM | `MEMBERSHIP` | `own_ref is not None and (team_owner_id == own_ref or owner_id == own_ref)` |
| LOCATION | either | `location_code is not None and location_code in principal.locations` |
| otherwise | either | `ForbiddenProblem("Out of scope for this resource")` |

Rules that follow:

1. **Opting in is registration and nothing else.** A service passes `owner_subject=OwnerSubject.MEMBERSHIP`; no token,
   header or gateway change is needed (`Principal.membership_id` is filled from `mem` or `X-Membership-Id`, §3.1).
2. **Fail closed.** A principal with no `own_ref` matches no row at OWN and TEAM, and passes no object check. A
   deployment skew (a service opted in, a token minted without `mem`) therefore narrows access and never widens it.
3. **The default is the employee link for every product that does not opt in**, and the employee-link branch of every row
   above is byte for byte the code that ships today (proven by the parity test, §5.8).
4. **Re-registration.** The same product with the same subject is a no-op (the first factory is kept); with a different
   subject it raises `ValueError` (DD-6). Customer registers `crm` twice from one process and relies on this.
5. **`reset_scope_resolvers()` is for tests.** No file under `services/*/app` calls it (a test greps for it, §5.8).
6. **Hand-built contexts are ORG.** The three in CRM (`customer/app/services.py:155,182`, `deal/app/services.py:90`) pass
   `scope=Scope.ORG`, which returns before any subject is read, so the default subject on them is harmless; the static
   guard (§5.8) keeps it that way.
7. **TEAM for a membership includes the caller's own rows** (pending D-24). If the owner answers no, only the two
   `MEMBERSHIP` TEAM rows above and the tests that name them change; the registry's `L6` records that R3.8 then stamps
   `owner_manager_id` differently.

### 5.5 Every direct read of `subject_ref`, and what becomes of it

An AST scan (`Attribute` nodes named `subject_ref`; docstrings and comments excluded) finds **six** reads in the CRM
services, none in `lead`. Each is replaced in R2.7 by `ctx.own_ref`; R2.2 changes none of them and **pins** them (§5.8).

| # | Site | What it does | R2.7 replacement | Pinned by today |
|---|---|---|---|---|
| 1 | `services/customer/app/services.py:205`, `_assert_owner_assignable` | An OWN caller may assign a company only to themselves: `ctx.subject_ref != owner_id` | `ctx.own_ref != owner_id`, where the posted id is the membership id (DD-28) | `customer/tests/test_crud.py:176` `test_an_own_scoped_rep_can_only_assign_to_themselves` |
| 2 | `services/deal/app/api/routes.py:112`, `create_deal` | Default `owner_id` is the caller; none to default to is a 422 | `ctx.own_ref` | `deal/tests/test_write_routes.py:60,149,172` |
| 3 | `.../routes.py:163`, `summary` with `owner=me` | A caller with no id gets the all-zero rollup, never the book | `ctx.own_ref is None` | `deal/tests/test_authz.py:234` `test_owner_equals_me_with_no_subject_ref_is_the_all_zero_rollup` |
| 4 | `.../routes.py:175`, `summary` | The resolved "mine" id | `ctx.own_ref` | `deal/tests/test_authz.py:187` |
| 5 | `.../routes.py:241`, `log_activity` | An OWN caller's activity is forced to themselves | `ctx.own_ref` | `deal/tests/test_write_routes.py:363` `test_an_own_writer_cannot_log_against_another_reps_deal` |
| 6 | `.../routes.py:243`, `log_activity` | Default owner for a non-OWN caller | `ctx.own_ref` | `deal/tests/test_write_routes.py:306` |

The 27 sites that enforce through the platform (5 `ctx.apply`, 22 `assert_can_touch`, listed in §3.1) read `own_ref`
automatically and need no edit. The three reads of `ctx.principal.membership_id` (`customer/app/services.py:131`,
`deal/app/services.py:65`, `lead/app/services.py:90`) put the caller into an audit row; they are attribution, not scope,
and stay.

**One string spelling that is not a read, and is kept (DD-38).** `services/customer/app/c1_360.py:83` carries the string
`"X-Subject-Ref"` as one element of `_IDENTITY_HEADERS` (`:77-92`), the allow-list of caller-identity headers the 360 copies
verbatim to the tenant-scoped upstream it calls (`forwarded_identity`, `:131-133`; its one caller is
`api/c1_customer360.py:70`). It is not an attribute read, so the six-row inventory above does not list it, but the string
rule of §5.8 would flag it the moment `OPTED_IN` is filled. **The decision is to keep forwarding the header.** Deal, once on
the membership subject, no longer reads it, but the 360 sends the same identity to every upstream it calls, and registry
`C65` and `C73` (R4.4) add support, project and projects to that list: an upstream that is still on the employee link
rebuilds `Principal.subject_ref` from `X-Subject-Ref` (`auth.py:249`) and applies the caller's OWN scope with it
(`projects` reads it for `assignee=me`, `services/projects/app/api/routes.py:378,380`), so dropping the header would
silently narrow that upstream's answer for an HR-linked rep. The guard therefore allow-lists this one spelling by file **and**
module-level name, which cannot excuse any other site, and a self-test proves it is the only string hit in the three
services.

**Outside CRM (listed so nobody searches for them; none changes in this programme).** The same scan finds executable
`subject_ref` reads in `approval` 13, `itsm` 5 (`app/services.py:253,685,1016,1042,1102`: requester and owner defaults and
two self-approval checks), `operations` 4 (`fulfilment/services.py:134,137`, `inventory/stock_ops.py:604`, `services.py:481`),
`attendance` 2, `leave` 2, `projects` 2 (`app/api/routes.py:378,380`) and `timesheet` 1 (`app/services.py:209`).
`metering`'s three are a usage event's own `subject_ref` field, unrelated to the principal. ITSM (NEW-8) and projects
(C14) are separate slices that would each replace their own sites, with their own migration of existing rows; every
product other than the three CRM services stays on the employee link permanently (`approval` and account share the `core`
prefix, which has no resolver; the registry's `A3` explains why a prefix lookup is safe for them).

### 5.6 Consumers and idempotency

None. The registry is process-global and idempotent at import (rule 4), which is the only idempotency this release has.

### 5.7 UI

None.

### 5.8 Tests

All new unit tests need no database. They run in `make test-libs` (`uv run pytest libs`) and, for the static guard,
`make test-infra`, which is a row of `make gate` (`GATE_STATIC`).

**`libs/platform-core/tests/test_scope_subject.py` (new, `A12`).** An autouse fixture calls `reset_scope_resolvers()`
before and after each test.

- `TestOwnerSubject`: `test_values_are_the_wire_strings`; `test_it_is_exported_from_auth_and_the_package_root`.
- `TestRegistry`: `test_two_positional_arguments_still_register_the_employee_link` (the sixteen existing call shapes);
  `test_a_membership_registration_is_recorded`; `test_re_registering_the_same_subject_is_a_no_op_that_keeps_the_first_factory`;
  `test_re_registering_a_different_subject_raises_value_error_naming_both`; `test_reset_empties_the_registry`.
- `TestOwnerSubjectFor`: `test_uses_the_text_before_the_first_dot`; `test_an_unregistered_product_is_the_employee_link`;
  `test_a_prefix_miss_is_the_employee_link` (register `notification` as membership, ask for `engage.campaign.campaign.send`);
  `test_a_permission_with_no_dot_is_looked_up_whole`.
- `TestRequireSnapshot`: `test_the_dependency_reads_the_registry_when_it_runs_not_when_it_is_built` (build
  `require("crm.pipeline.deal.read")`, then register `crm` as membership, then call the dependency: the context says
  `MEMBERSHIP`; reset and call again: `EMPLOYEE_LINK`); `test_a_missing_permission_is_refused_before_any_subject_is_read`;
  `test_the_scope_floor_still_applies_to_a_membership_context`.
- `TestHandBuiltContexts`: `test_a_hand_built_context_defaults_to_the_employee_link`.
- `TestPrincipalMapping`: `test_mem_and_sref_claims_fill_membership_id_and_subject_ref`;
  `test_forwarded_x_membership_id_and_x_subject_ref_fill_the_same_fields` (with `verify_gateway_token` monkeypatched to
  return the matching `uid` and `org`); `test_a_malformed_x_membership_id_is_a_401_naming_the_header`.
- `TestEmployeeLinkParity` (DD-39): `test_every_combination_matches_the_pre_change_logic`. The test file carries verbatim copies
  of `apply` and `assert_can_touch` as they read at `main@caa7f112` (`_legacy_apply`, `_legacy_assert_can_touch`) and
  compares them with the new methods over the full grid (scope x `subject_ref` set or null x `membership_id` set or null x
  `team_col` set or null x `location_col` set or null; for the object check, `owner_id`, `team_owner_id` and
  `location_code` each matching or not): equal compiled SQL text and bound parameters, or equal exception type and
  message. `membership_id` varies so that the test proves it is ignored on the employee-link branch.
- `TestNoProductionReset`: `test_no_service_calls_reset_scope_resolvers` (an AST scan of `services/*/app`).

**`libs/platform-core/tests/test_auth_scope.py` (extended, additions only).** A `TestMembershipSubject` class builds
contexts with `owner_subject=OwnerSubject.MEMBERSHIP` and a principal whose `subject_ref` and `membership_id` differ:

- `apply`: `test_own_binds_only_the_callers_membership_id` (the compiled parameters equal `[membership_id]`; the employee
  link, `sub` and the colleague's id appear nowhere); `test_own_with_no_membership_id_matches_nothing_even_with_an_employee_link`;
  `test_team_is_owner_or_team_column_for_a_membership` (the SQL contains `OR`, both parameters equal `membership_id`);
  `test_team_with_no_membership_id_matches_nothing` (renders `false`, never `is null`);
  `test_team_without_a_team_column_is_still_refused`; `test_org_returns_the_same_statement`;
  `test_location_is_untouched_by_the_subject`.
- `assert_can_touch` (the negative cases first): `test_own_refuses_a_row_owned_by_the_employee_link_value`;
  `test_own_refuses_a_row_owned_by_a_colleague`; `test_own_with_no_membership_id_refuses_everything`;
  `test_own_allows_exactly_the_callers_membership_id`; `test_team_allows_a_direct_report_or_the_callers_own_row`;
  `test_team_refuses_a_row_with_neither`; `test_team_refuses_when_both_ids_are_none`.
- The 26 existing tests in the file are not edited (AC-2.2-4).

**`tests/infra/test_no_raw_subject_ref.py` (new, `A11`; modelled on `test_io_outside_transaction.py`).**
`OPTED_IN: frozenset[str]` names the services the guard enforces; R2.2 ships it **empty**, R2.7 sets it to
`{"customer", "deal", "lead"}`. The scan reads every `*.py` under `services/<name>/app` (not `migrations`), by AST only, so a
docstring that says the word is never a finding (the committed OpenAPI mentions it too: `libs/contracts/openapi/deal.json:1166,1208`).

- `test_an_opted_in_service_never_reads_the_employee_link` for each name in `OPTED_IN`: no `Attribute` named
  `subject_ref` on **any** object (not only `ctx`, `ctx.principal` and `principal`, which was the first text of registry `A11`: DD-38), no
  string constant `"subject_ref"`, `"sref"` or `"X-Subject-Ref"` outside a docstring **except the one allow-listed
  spelling** (§5.5), and no keyword argument `subject_ref=` to `Principal(...)`. The allow-list is
  `ALLOWED_STRING_SPELLINGS = {("customer/app/c1_360.py", "_IDENTITY_HEADERS"): {"X-Subject-Ref"}}`, matched by file **and**
  the module-level name that holds the string, so it excuses nothing else.
- `test_the_only_string_spelling_in_the_crm_services_is_the_allow_listed_one`: a scan of `customer`, `deal` and `lead`
  finds exactly one string spelling and it is in `ALLOWED_STRING_SPELLINGS`; a second hit anywhere, or the same string
  under another file or name, is a finding.
- `test_hand_built_contexts_in_an_opted_in_service_are_org_scope`: every `ScopeContext(...)` call passes the literal
  `scope=Scope.ORG` (the three in CRM do; DD-38).
- `test_every_permission_an_opted_in_service_requires_starts_with_its_product`: the first argument of each `require(...)`
  call is **resolved**, a string literal as written and a bare name through the module-level `NAME = "..."` assignments of
  the same file (customer's and lead's `REVEAL_PERMISSION`: `customer/app/api/routes.py:72,80`,
  `lead/app/api/routes.py:70,78`), and must start with `crm.` (a prefix miss would put that one route back on the employee
  link, `A3`). An argument that cannot be resolved to a string (a call, an attribute, a name with no module-level constant)
  is itself a finding, so the two reveal routes, the most OWN-sensitive in the app, are checked and never skipped.
- `test_the_opted_in_set_is_exactly_the_set_of_services_that_register_the_membership_subject` (DD-7): the services whose
  `app/scope.py` calls `register_scope_resolver` with `owner_subject=OwnerSubject.MEMBERSHIP` equal `OPTED_IN`.
- `test_the_scan_is_not_vacuous`: each opted-in service yields at least five parsed files.
- Self-tests on in-test source strings: `test_the_scanner_flags_every_spelling` (attribute on `ctx`, on `ctx.principal`, on
  a local alias, `getattr(ctx, "subject_ref")`, a subscript `["subject_ref"]`, a `ScopeContext(...)` with `Scope.OWN`);
  `test_the_scanner_ignores_docstrings_and_comments`; `test_the_scanner_accepts_own_ref_and_org_contexts`;
  `test_the_scanner_resolves_a_module_constant_passed_to_require` (a snippet with `REVEAL = "lead.lead.read_sensitive"` and
  `require(REVEAL, ...)` is flagged, the same with `"crm.lead.lead.read_sensitive"` passes);
  `test_an_unresolvable_require_argument_is_flagged`;
  `test_an_allow_listed_string_under_another_file_or_name_is_flagged` (the same `"X-Subject-Ref"` tuple element in a file or
  constant the allow-list does not name).
- **The ratchet**, until R2.7: `test_the_known_raw_reads_are_exactly_the_documented_six` compares the per-function count
  of raw reads in `customer`, `deal` and `lead` with `KNOWN_RAW_READS = {("customer/app/services.py",
  "_assert_owner_assignable"): 1, ("deal/app/api/routes.py", "create_deal"): 1, ("deal/app/api/routes.py", "summary"): 2,
  ("deal/app/api/routes.py", "log_activity"): 2}`, and the count of string spellings with
  `KNOWN_STRING_SPELLINGS = {("customer/app/c1_360.py", "_IDENTITY_HEADERS"): 1}`. A seventh read, a second spelling, or
  either vanishing before the flip, fails the test. R2.7 deletes `KNOWN_RAW_READS`, `KNOWN_STRING_SPELLINGS` and the ratchet
  test and fills `OPTED_IN`; `ALLOWED_STRING_SPELLINGS` and its only-hit test stay, as the guard's permanent exemption.

### 5.9 Rollout

1. Owner merge. `make gate` locally first (Actions billing is blocked, §3.7): the rows that matter are `typecheck`
   (every service imports `platform_core`, so strict mypy per service must still pass), `test-libs`, `test-infra`,
   `test-services` (every product's OWN and TEAM tests run on the rewritten methods), `openapi-check`.
2. `make edge-preflight HOST=… CONFIRM=…` (read-only), then the owner-named `make deploy HOST=… CONFIRM=… REF=<sha>`.
   `libs/platform-core` changed, so `needs_full_rebuild` is true (§3.7): **every image rebuilds and recreates**, the
   same window class as any library change. The owner picks a quiet moment (the script's own comment records a 502 for an
   invitation accepted mid-deploy). No migration runs for any service.
3. Verify (owner-run reads, no data written): `make edge-status` lists every container healthy; one OWN-scoped HR read (an
   employee's own leave requests) and one CRM list as a rep return `200` with the same rows they returned before the deploy.
4. Memory: no container is added or resized. `python3 infrastructure/edge/lib/memory_budget_totals.py
   infrastructure/edge/memory-budget.json --profiles …` is unchanged: core 1772 MB, `crm` 2067 MB, ten profiles 3747 MB,
   ten plus `admin` 3944 MB (§3.4). `EDGE_BUDGET_ACCEPTED_MB` is not touched.
5. Order against the rest: independent of R2.1; R2.7 needs it.

### 5.10 Rollback

Redeploy the previous ref (`make edge-rollback HOST=… CONFIRM=…`), which is a full rebuild again. There is no data,
schema or flag to undo. **Stop conditions:** any container that does not become healthy; a rise in `403 Out of scope`
for an OWN-scoped HR or ITSM route (the employee-link branch must be byte for byte unchanged, so this means the parity
test missed something); any 5xx from a route that builds a `ScopeContext`.

### 5.11 Acceptance

- **AC-2.2-1** `OwnerSubject` has exactly the values `employee_link` and `membership`, and imports from `platform_core.auth` and `platform_core` (test).
- **AC-2.2-2** `register_scope_resolver(product, factory)` registers the employee link; the same subject twice is a no-op; a different one raises `ValueError` (tests).
- **AC-2.2-3** `require()` reads the registry when the dependency runs; an unregistered product and a prefix miss get the employee link; `reset_scope_resolvers()` empties it (tests).
- **AC-2.2-4** Over the full grid, `apply` and `assert_can_touch` on an employee-link context equal the frozen pre-change logic, and `git diff --numstat` shows zero deleted lines in `libs/platform-core/tests/test_auth_scope.py`.
- **AC-2.2-5** On a membership context, OWN filters and admits exactly `membership_id`, a missing `membership_id` matches nothing, and the employee-link value is never compared (tests).
- **AC-2.2-6** On a membership context, TEAM is `owner OR team` for the list and `team_owner_id or owner_id` for the object, fails closed without an id, and never renders `IS NULL` (tests; flips as a unit if D-24 is answered no).
- **AC-2.2-7** The static guard flags every spelling in its self-test, its opted-in set equals the registered set, hand-built contexts obey §5.8, every `require(...)` argument (a literal or a module-level constant, `REVEAL_PERMISSION` included) resolves to a `crm.` permission or is a finding, and the one allow-listed string spelling is the only string hit in the three services.
- **AC-2.2-8** The ratchet equals the six sites and the one string spelling of §5.5, so neither a new nor a vanished raw read or spelling passes.
- **AC-2.2-9** The diff touches only `libs/platform-core/**`, `tests/infra/**` and `docs/**` (`git diff --name-only <base>..<head>`); `docs/architecture.md` names `OwnerSubject` and `own_ref` and no longer says "a rep id for CRM" (grep); register rows 20 and 120 are updated.

## 6. R2.3 — Configurable stages, expand

### 6.1 Purpose

Turn the six stage literals into data without changing what a single organization sees: tables for the pipeline and
its stages, a **virtual default** equal to today's six, a pure `PipelineSpec` that every domain rule and rollup
reads instead of a constant, `outcome` (`open`, `won`, `lost`) as the thing consumers test, `closed_at`, the deal
events brought up to their committed schemas, and the 360's CRM leg freed of key literals. There is **no authoring
route** here (R2.5) and no screen (SP3, R3.4): in production every organization stays on the virtual default until R2.5
ships, which is what makes this release safe to deploy on its own and to roll back.

### 6.2 Data model

**Migration chain** (deal's single head moves `e94d7a2c5f18` → `a1c9e2f7b3d4` → `f3b8d1a65c02`):

1. **`a1c9e2f7b3d4` "add closed_at", adopted unchanged** (D-12 default). The file is copied from commit `a4b248f1` with
   `git show a4b248f1:services/deal/migrations/versions/a1c9e2f7b3d4_add_closed_at.py` (git blob
   `be9f7b9f66102b70346d178c8a84299912a5245d`, `down_revision = "e94d7a2c5f18"`), **not cherry-picked**: the commit also
   carries the whole insights stack. It adds `deal.deals.closed_at TIMESTAMPTZ NULL` and backfills it from the deal's own
   audit rows by the `closed_won`/`closed_lost` literals, which is correct because only the legacy six can exist at this
   point. The matching model column (`Deal.closed_at`) and the producer fields come from the same commit's `models.py` and
   `services.py` hunks, re-expressed through `outcome` (§6.4). If the insights stack has already merged when R2.3 is
   built, the adoption step is skipped and the stages revision keeps `down_revision = "a1c9e2f7b3d4"`; if R2.3 merges
   first the insights branch rebases onto an identical blob and git sees no conflict.
2. **`f3b8d1a65c02` "configurable stages"** (this release's own revision id, verified unused across `services/`,
   `libs/`, `tools/`, `docs/`, `e2e/` and `infrastructure/` at `HEAD`). `down_revision = "a1c9e2f7b3d4"`. It carries **no**
   `# RE-PARENT HERE` marker (the precedent, `services/operations/migrations/versions/d7a3c9e5b184_*.py:49`, is for a revision
   whose parent may legitimately move): nothing may merge ahead of it, and re-parenting it would orphan the pinned
   `a1c9e2f7b3d4` as a second head, so the marker would invite exactly the edit that breaks the chain.
3. **The ordering rule (DD-51; registry §B.2's chain note, `J30`): R2.3 merges before R2.6's deal revision exists on `main`.** `a1c9e2f7b3d4` is parented on
   `e94d7a2c5f18` and is byte-pinned; a deal revision written against `e94d7a2c5f18` by R2.6 would make **two heads**, and
   whichever merged second could only fix that by editing the pinned file, which the byte-identity rule above, K-7 and
   AC-2.3-1 forbid. Two heads break `alembic upgrade head` (`infrastructure/edge/remote.sh:298`), `make migration-roundtrip` and
   `make migration-check`, and fail `test_the_deal_chain_has_one_head_and_orders_the_three_revisions` (§6.7) first.
   R2.6's `d8a35c17e920` is therefore written with `down_revision = "f3b8d1a65c02"` (§9.2) and **R2.6 needs R2.3**
   (§0.1); the plan orders the two, and an R2.6 branch that is ready first waits for R2.3 or is stacked on it and merged after
   it. `d8a35c17e920` keeps the registry's `# RE-PARENT HERE` marker for a deal revision of a later sub-project that merged
   first, and may only ever move *forward* from `f3b8d1a65c02`. An Alembic merge revision for the other order was rejected
   (DD-51).

DDL of `f3b8d1a65c02` (registry `B19` and `B20`; the migration opens with `SET LOCAL lock_timeout = '5s'`, then):

```sql
CREATE TABLE deal.pipelines (
    id              uuid        NOT NULL,
    organization_id uuid        NOT NULL,
    key             varchar(40) NOT NULL,
    name            varchar(120) NOT NULL,
    is_default      boolean     NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    created_by      varchar(64),
    updated_by      varchar(64),
    deleted_at      timestamptz,
    CONSTRAINT pk_pipelines PRIMARY KEY (id),
    CONSTRAINT uq_pipelines_org_key UNIQUE (organization_id, key),
    CONSTRAINT uq_pipelines_org_id  UNIQUE (organization_id, id)   -- the target of the composite foreign key
);
CREATE INDEX ix_deal_pipelines_organization_id ON deal.pipelines (organization_id);
CREATE UNIQUE INDEX uq_pipelines_org_default ON deal.pipelines (organization_id)
    WHERE is_default AND deleted_at IS NULL;

CREATE TABLE deal.pipeline_stages (
    id                  uuid         NOT NULL,
    organization_id     uuid         NOT NULL,
    pipeline_id         uuid         NOT NULL,
    key                 varchar(20)  NOT NULL,
    label               varchar(80)  NOT NULL,
    position            integer      NOT NULL,
    outcome             varchar(8)   NOT NULL,
    default_probability smallint     NOT NULL,
    tone                varchar(10)  NOT NULL,
    archived_at         timestamptz,
    created_at          timestamptz  NOT NULL DEFAULT now(),
    updated_at          timestamptz  NOT NULL DEFAULT now(),
    created_by          varchar(64),
    updated_by          varchar(64),
    deleted_at          timestamptz,
    CONSTRAINT pk_pipeline_stages PRIMARY KEY (id),
    CONSTRAINT fk_pipeline_stages_pipeline FOREIGN KEY (organization_id, pipeline_id)
        REFERENCES deal.pipelines (organization_id, id) ON DELETE RESTRICT,
    CONSTRAINT uq_pipeline_stages_org_pipeline_key UNIQUE (organization_id, pipeline_id, key),   -- not partial: a key is never reused
    CONSTRAINT ck_pipeline_stages_key         CHECK (key ~ '^[a-z0-9_]{1,20}$'),
    CONSTRAINT ck_pipeline_stages_outcome     CHECK (outcome IN ('open', 'won', 'lost')),
    CONSTRAINT ck_pipeline_stages_probability CHECK (default_probability BETWEEN 0 AND 100),
    CONSTRAINT ck_pipeline_stages_closing_pin CHECK (outcome = 'open'
        OR (outcome = 'won'  AND default_probability = 100)
        OR (outcome = 'lost' AND default_probability = 0)),
    CONSTRAINT ck_pipeline_stages_tone        CHECK (tone IN ('neutral', 'info', 'success', 'warning', 'danger')),
    CONSTRAINT ck_pipeline_stages_position    CHECK (position >= 0)
);
CREATE INDEX ix_deal_pipeline_stages_organization_id ON deal.pipeline_stages (organization_id);
CREATE INDEX ix_pipeline_stages_org_pipeline_position
    ON deal.pipeline_stages (organization_id, pipeline_id, position);
CREATE UNIQUE INDEX uq_pipeline_stages_org_pipeline_label_active
    ON deal.pipeline_stages (organization_id, pipeline_id, lower(label))
    WHERE archived_at IS NULL AND deleted_at IS NULL;

CREATE FUNCTION deal.pipeline_stage_identity_frozen() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.key IS DISTINCT FROM OLD.key
       OR NEW.pipeline_id IS DISTINCT FROM OLD.pipeline_id
       OR NEW.organization_id IS DISTINCT FROM OLD.organization_id
       OR NEW.outcome IS DISTINCT FROM OLD.outcome THEN
        RAISE EXCEPTION 'pipeline_stages: key, pipeline_id, organization_id and outcome are immutable';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_pipeline_stages_identity BEFORE UPDATE ON deal.pipeline_stages
    FOR EACH ROW EXECUTE FUNCTION deal.pipeline_stage_identity_frozen();

-- for each of pipelines, pipeline_stages (a local _rls(), as b7d41e92c05a:73-84):
ALTER TABLE deal.<t> ENABLE ROW LEVEL SECURITY;
ALTER TABLE deal.<t> FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON deal.<t> FOR ALL TO opsgrids_deal
    USING      (organization_id = deal.current_org())
    WITH CHECK (organization_id = deal.current_org());
```

Notes that are part of the contract:

- **No grants are written.** `apply_schema_grants` re-grants DML on every table after each migration
  (`libs/platform-core/src/platform_core/db.py:56-87`), so the immutability of a stage's identity is a **trigger**, not a
  revoke (DD-11, DD-42). There is deliberately **no** delete trigger (DD-11): a static test bans deleting a stage.
- **Both tables carry `organization_id`, are indexed on it first, are RLS-forced and are added to `TENANT_TABLES`** in
  `services/deal/tests/conftest.py:45-51`. The purge tool reaches them with no change: it discovers tables by
  `organization_id` and orders deletes by the discovered foreign keys, composite ones included
  (`tools/purge_test_orgs.py:84,603-643`).
- **No cross-schema foreign key.** The only key is inside `deal`, and it is organization-scoped: a stage cannot point at
  another tenant's pipeline even though the referential-integrity check itself bypasses RLS (DD-11).
- **Row-local facts live in the database; set-level facts do not** (DD-41): at least one active open, one won and one
  lost stage, closed outcomes ordered last, dense positions among active stages. Those are enforced by the pure
  `pipeline.validate` function and the lock-holding service, because a cross-row trigger would be racy and would enlarge
  the purge tool's trigger set.
- **`deals.stage` is unchanged**: `VARCHAR(20)`, no foreign key, no CHECK (`models.py:73-74`). It keeps holding the stage
  **key**; the stage tables are reference data keyed by `(organization_id, pipeline_id, key)`.
- **No data migration and no seeding.** An organization with no `pipelines` row reads the code default (DD-10). Seeding in
  the migration would reach only organizations that already hold deals (deal has no organization table), leaving two code
  paths anyway, and would put data in a migration the owner cannot rehearse. Seeds are untouched: `tools/demo/seed_crm.py` (18 deals across the six),
  `e2e/_seed_crm.py:124-135` and the production smoke `e2e/edge_product_crud.py:401-425` (a fresh organization creates at `lead` and moves to `qualified`) keep
  naming the legacy keys, which stay valid because they are the default pipeline's keys.
- **Locking, stated as it is (DD-52).** Alembic runs every pending deal revision in one transaction (`migrations/env.py`
  opens one `context.begin_transaction()`), so the adopted `ADD COLUMN`'s `ACCESS EXCLUSIVE` lock on `deal.deals` is held to the
  commit, **through its backfill `UPDATE`**; and the adopted file sets no `lock_timeout` (it is byte-pinned). A session that
  holds any lock on `deal.deals`, a long or idle-in-transaction one, makes that request queue, and every later read and write
  of `deal.deals` queues behind it, while the old code still serves (migrations run before `up`, §3.7). The stages revision's own
  `SET LOCAL lock_timeout = '5s'` runs *after* the statement it would need to bound. The bound therefore comes from outside the
  file: `services/deal/migrations/env.py` builds its engine with `connect_args={"server_settings": {"lock_timeout":
  "5s"}}`, so every statement of deal's chain, the adopted ones included, fails with `lock_not_available` after 5 s instead of
  queueing; the migrate step fails, `set -e` stops the deploy before `up` (`remote.sh:27,539-540`), nothing is applied
  (the DDL is transactional) and the old containers keep serving. `PGOPTIONS` is not an option: asyncpg does not read it. The
  hook does not bound the backfill's own run time, which §6.8 has the owner size first.

**The default pipeline** (registry `B22`; `DEFAULT_PIPELINE` in the new pure module `services/deal/app/pipeline.py`; key `sales`, name
`Sales pipeline`), reproducing the code constants (`domain.py:21-39`, `services.py:49-58`) and the SPA's tones
(`frontend/product-crm/src/ui/StatusPill.tsx:61-68`, `data/stages.ts:25-38`):

| position | key | label | outcome | default probability | tone |
|---|---|---|---|---|---|
| 0 | `lead` | Lead | open | 20 | info |
| 1 | `qualified` | Qualified | open | 40 | info |
| 2 | `proposal` | Proposal | open | 55 | info |
| 3 | `negotiation` | Negotiation | open | 70 | warning |
| 4 | `closed_won` | Closed Won | won | 100 | success |
| 5 | `closed_lost` | Closed Lost | lost | 0 | danger |

**Downgrade** (guarded, `OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE`, as `b7d41e92c05a:496-519`): drops the trigger, function,
policies and both tables, but **refuses** unless both tables hold nothing beyond the six untouched default rows (or are
empty) **and** every deal's `stage` is one of the six legacy keys; any other state would orphan labels or strand a deal in
a stage the previous code cannot name. In production before R2.5 both tables are empty, so the guard passes. The adopted
`a1c9e2f7b3d4` downgrade (`drop_column closed_at`) is its own and unchanged.

### 6.3 Backend contract

**New pure module `services/deal/app/pipeline.py`** (no I/O, importable by tests with no database):

```python
@dataclass(frozen=True)
class StageSpec:
    key: str; label: str; position: int
    outcome: Literal["open", "won", "lost"]
    default_probability: int; tone: str
    archived: bool = False; id: uuid.UUID | None = None

@dataclass(frozen=True)
class PipelineSpec:
    stages: tuple[StageSpec, ...]            # every stage, archived included, ordered by (position, key)
    id: uuid.UUID | None = None; key: str = "sales"; name: str = "Sales pipeline"; materialised: bool = False
    def get(self, key: str) -> StageSpec | None
    def active(self) -> tuple[StageSpec, ...]
    def entry_stage(self) -> StageSpec | None                 # first active open stage by position
    def outcome_of(self, key: str) -> str                      # an unknown key reads "open" (parity with is_open)
    def is_last_open(self, key: str) -> bool                   # the active open stage with the highest position

DEFAULT_PIPELINE: PipelineSpec                                 # the table above
def validate(stages: Sequence[StageSpec]) -> None              # raises PipelineInvalid(code); R2.5 calls it before every write
```

`domain.py` keeps its public names (DD-12): `is_open`, `can_transition`, `open_deals`, `pipeline_value_minor`,
`weighted_pipeline_minor`, `open_count`, `win_rate_pct`, `won_value_minor`, `stage_funnel` each gain an optional
`spec: PipelineSpec = DEFAULT_PIPELINE` argument; `STAGE_ORDER`, `OPEN_STAGES`, `TERMINAL_STAGES`, `STAGE_LABELS` stay as
aliases **derived** from `DEFAULT_PIPELINE`. `services.STAGE_DEFAULT_PROBABILITY` becomes a derived alias too.

**New in `selectors.py`:** `load_pipeline_spec(session, organization_id) -> PipelineSpec` (the live default pipeline's
non-deleted stages, or `DEFAULT_PIPELINE` when there is none; one or two indexed point queries, **each filtered on
`organization_id`** in addition to the forced row-level-security policy, as every query in this service is) and
`deal_out(deal, spec)`. **New in `services.py`:** `lock_pipeline_shared(session, org)`, `lock_pipeline_exclusive(session,
org)` (DD-13) and `materialise_default_pipeline(session, org, actor)` (DD-26, DD-43). The lock key is
`hashtextextended('deal.pipeline:' || :organization_id, 0)` passed to `pg_advisory_xact_lock_shared` (every write that
binds a deal to a stage) or `pg_advisory_xact_lock` (every stage-configuration write). No code path takes the shared
lock and later the exclusive one in one transaction (a lock upgrade can deadlock; R2.5 adds the AST test).

**Routes.**

| Route | Permission | Scope floor | Change |
|---|---|---|---|
| `GET /deals/stages` (C1) | `crm.pipeline.deal.read` | none on the bare list by design: stage rows are tenant configuration, organization filter only (like `GET /customers/owners`); `include=counts` needs an ORG grant (DD-53) | **new**, declared before `GET /deals/{deal_id}` |
| `GET /deals` (C6) | `crm.pipeline.deal.read` | unchanged (`ctx.apply` on `owner_id`) | adds `outcome` filter; `stage` filter stays an unvalidated string (an unknown key returns an empty page, as today) |
| `POST /deals` | `crm.pipeline.deal.write` | unchanged | `stage` optional, default the entry stage; an explicit stage must exist and be active |
| `POST /deals/{deal_id}/stage` (C8) | `crm.pipeline.deal.write` | unchanged (`assert_can_touch`) | key validated against the spec; closing pin enforced |
| `GET /deals/summary` (C9) | `crm.pipeline.deal.read` | unchanged | rollups by `outcome`; `funnel[]` gains `outcome` |
| `POST /internal/v1/organizations/{id}/leads:convert` | service token, caller `lead`, org claim | — | opens at the entry stage instead of the literal `"lead"` |

`GET /deals/stages` query: `include_archived: bool = false`, `include: "counts" | null`. `include=counts` adds `deal_count`,
the number of **live** deals in the stage (`WHERE organization_id = :org AND deleted_at IS NULL`, one grouped query), **only
when the caller's `crm.pipeline.deal.read` grant is ORG** (`ctx.scope is Scope.ORG`). For an OWN or TEAM grant the parameter is
ignored and no stage carries `deal_count` (absent, never `0`: a zero would be a lie). The count is deal-derived data, so it
follows the deal read's scope as `GET /deals/summary` does, and it never counts a soft-deleted deal, which no read route shows
(R2.5's archive route counts them itself, §8.4.6); SP3's R3.4 shows its count column only when the API returns the field (registry
`C1`). `include_archived` returns reference rows only, the same class as the active ones, and is honoured at any scope
(a deal in an archived stage already carries its `stage_label` in `DealOut`). Response:

```
StageListOut { pipeline: {id: uuid, key: str, name: str} | null, materialised: bool, stages: StageOut[] }
StageOut { id: uuid | null, key: str, label: str, position: int, outcome: "open"|"won"|"lost",
           default_probability: int, tone: "neutral"|"info"|"success"|"warning"|"danger",
           archived: bool, deal_count?: int }
```

`materialised = false` is the virtual default (`pipeline` and every `id` are `null`). The route is read-only: **a `GET`
never materialises** (DD-43).

**Wire types.** `DealStage` (the six-value `Literal`, `schemas.py:37`) is replaced by `StageKey`, a string with the pattern
`^[a-z0-9_]{1,20}$`, on `DealIn.stage` (now `StageKey | None = None`), `StageMoveIn.stage`, `DealOut.stage` and
`StageRollupOut.stage`; the committed OpenAPI loses its four six-value enums (`libs/contracts/openapi/deal.json`).
`DealOut` gains (registry `C7`), additively and with every existing field frozen (`deal/tests/test_authz.py:255-266` pins the exact key
set and changes on purpose):

| New field | Type | Meaning |
|---|---|---|
| `stage_label` | string | the stage's label; an unresolvable stored key reads as itself (DD-44) |
| `stage_outcome` | `open` \| `won` \| `lost` | an unresolvable key reads `open` |
| `stage_position` | integer | the stage's position; `-1` for an unresolvable key |
| `stage_is_last_open` | boolean | the stage is the pipeline's last active open stage (DD-40) |
| `closed_at` | datetime \| null | set once when the deal first reaches a closed outcome (§6.4) |

`StageRollupOut` (`schemas.py:221-230`) gains `outcome`; the nine legacy `SummaryOut` fields stay frozen.

**Error shapes** (RFC 7807 bodies from `platform_core.errors`; the `errors[]` entries carry `field`, `code`, `message`):

| Condition | Status, `type` | `errors[0]` |
|---|---|---|
| a well-formed key the pipeline does not contain, on create or move | 422 `validation` (DD-14) | `{field: "stage", code: "unknown_stage"}` |
| an archived stage as the target | 422 `validation` | `{field: "stage", code: "stage_archived"}` |
| a closing move whose `probability` differs from the pin | 422 `validation` (DD-15) | `{field: "probability", code: "probability_pinned"}` |
| the pipeline has no active open stage (entry stage) | 422 `business-rule` (DD-17) | `{code: "no_entry_stage"}` |
| a closed deal, or a move to the stage the deal is in | 409 `conflict` | unchanged text |
| no token / no `crm.pipeline.deal.read` / no `crm` entitlement | 401 / 403 / 402 | unchanged |

**Events** (producer `deal`; every change lands with its committed schema and a producer-side test in the same commit;
payloads carry ids and enumerations only). The three schemas stop being "ahead of the producer": the `$comment` is
removed, `stage` (and `from`, `to`) change from the six-value enum to the key pattern, and the outcome fields are added.

| Event | Schema file | Fields added or changed in R2.3 |
|---|---|---|
| `crm.pipeline.deal.created` (EV6) | `libs/contracts/events/crm.pipeline.deal.created/v1.schema.json` | the producer now emits `company_id`, `close_date`, `probability`, `closed_at`, `created_at` (the adopted field set); adds **required** `stage_outcome` |
| `crm.pipeline.deal.updated` (EV7) | `.../crm.pipeline.deal.updated/v1.schema.json` | emits the full state after the patch (`name`, `stage`, `value_minor`, `currency`, `owner_id`, `company_id`, `close_date`, `probability`, `closed_at`); adds **required** `stage_outcome` |
| `crm.pipeline.deal.stage_changed` (EV8) | `.../crm.pipeline.deal.stage_changed/v1.schema.json` | emits `name`, `value_minor`, `currency`, `owner_id`, `probability`, `closed_at`; adds **required** `from_outcome`, `to_outcome` and optional `to_label` (string, 1-80). `from` and `to` stay **keys** and never change meaning. R2.5 adds optional `reason` |
| `crm.pipeline.deal.deleted` (EV9) | unchanged | none |

`closed_at` is an RFC 3339 string or `null`; `stage_outcome`, `from_outcome` and `to_outcome` are `open`, `won` or `lost`.
Adding required properties to a committed schema is safe here for the reason R2.1's is (§4.3): no consumer validates
these three schemas today (`grep validate_event_schema services/deal/tests` is empty; insights is unmerged), and producer
and schema change in one commit. Audit rows keep their keys (`deal.created` after: `name, stage, value_minor,
currency`; `deal.stage_changed` before: `stage`, after: `stage, probability`), so the adopted `closed_at` backfill, which
reads `after->>'stage'`, stays valid.

### 6.4 Domain rules and the deliberate behaviour changes

1. **Spec resolution.** Every write that binds a deal to a stage takes the shared pipeline lock, then loads the spec in the
   same transaction (DD-13); a read loads it without a lock. A bare organization reads `DEFAULT_PIPELINE`.
2. **Entry stage.** `create_deal` with no `stage`, and the internal lead conversion, use `spec.entry_stage()`: the first
   active open stage by position. None is a 422 `no_entry_stage` (DD-17), which lead's client maps to a permanent
   `ConversionRejected` (`lead/app/clients.py:221-223`), never to a name conflict (`:215-217`).
3. **Targets.** A create or move target must exist and be active. A deal may *sit* in an archived stage (a closed deal in an
   archived won or lost stage; R2.5 never touches closed deals) and still be read, summed and shown.
4. **`can_transition(current, new, spec)`.** Allowed iff `current` exists in the spec, `outcome_of(current)` is `open`,
   `new` is active and `new != current`. For the legacy six this equals today's rule over the full 6 x 6 grid.
5. **Closing.** A move into a `won` stage pins `probability` to 100, into `lost` to 0 (DD-15); a supplied different value is
   a 422 `probability_pinned`; omitted means the pin. A move into an `open` stage uses the stage's `default_probability`
   when none is supplied.
6. **`closed_at`.** Set to the move time when a deal enters a stage whose outcome is `won` or `lost`; set to `created_at`
   (or now) when a deal is created directly in one; never changed afterwards and never cleared (a closed deal moves
   nowhere).
7. **Rollups by outcome.** `open_deals`, `pipeline_value_minor`, `weighted_pipeline_minor` and `open_count` take every deal
   whose `outcome_of(stage)` is `open` (an unknown key counts as open, as `is_open` does today); `win_rate_pct` and
   `won_value_minor` count deals whose outcome is `won` (and `won` plus `lost` for the rate's denominator); `stage_funnel`
   lists the active open stages in position order with the same `pct` arithmetic.
8. **Unresolvable stored key** (DD-44): `deal_out` renders `stage_label = key`, `stage_outcome = "open"`,
   `stage_position = -1`, `stage_is_last_open = false` and logs `deal.stage.unresolved` with ids only. It never raises.
9. **Materialising** (`materialise_default_pipeline`, no route before R2.5): take the exclusive lock, re-read, and if a live
   default pipeline exists return it; otherwise insert `pipelines(key='sales', name='Sales pipeline', is_default=true)`
   and the six rows above, write one audit row `pipeline.materialised` (ids and counts only) and stage **no event**
   (DD-26). Idempotent; two concurrent first writes produce one pipeline.
10. **Stage labels are unique among active stages, case-insensitively** (the partial index), which is what lets an import
    resolve a label to exactly one key. A label may not equal a *different* stage's key (R2.5 enforces, DD-45).

**The deliberate behaviour changes of R2.3** (everything else is parity, proven in §6.7):

| # | Change | Why | Who can see it |
|---|---|---|---|
| 1 | A closing move whose `probability` is not the pin is a 422 (DD-15) | the code stores any supplied figure today (`services.py:314-316`) against its own docstring (`:305-308`); won at 37% corrupts the rollups | nobody on the SPA: every caller sends none or the pin (`frontend/product-crm/src/data/deals.ts:264-294`) |
| 2 | An unknown well-formed stage key on create or move answers 422 `unknown_stage` from the service, where Pydantic's six-value `Literal` answered 422 first (DD-14) | the `Literal` is replaced by a pattern | none: the status is the same; the body now names `code: unknown_stage` |
| 3 | The deal wire gains `stage_label`, `stage_outcome`, `stage_position`, `stage_is_last_open`, `closed_at` and `funnel[].outcome`; `DealIn.stage`'s default moves from `"lead"` to "the entry stage" | additive; the default is `lead` on every organization until R2.5 | additive fields; none for the SPA |

### 6.5 Consumers and idempotency

R2.3 adds **no consumer** and no handler. The events change shape additively; what reads them or the wire:

- **notification** (exists): the rule and automation fan-out matches `payload.to` on `crm.pipeline.deal.stage_changed`
  (subscribable key, `notification/app/domain.py:151`); `to` is still a key, so every tenant rule keeps matching. R2.5 adds the
  `stage_archived` skip.
- **The customer 360** (`customer/app/c1_360.py`, `fetch_crm`): the module's `_OPEN_STAGES` tuple and the `negotiation` literal
  go (DD-16, DD-40). `_DealRow` reads `stage`, `stage_outcome`, `stage_position`, `stage_is_last_open`, `probability`,
  `created_at`. `open_deals` are the rows with `stage_outcome == "open"`; `stage` is the key of the open deal with the
  highest `stage_position`; `hot` is `any(open deal: probability >= 70 or stage_is_last_open)` (identical to today's
  `negotiation or >= 70` on the default pipeline; DD-40). A row **without** `stage_outcome` fails `model_validate`, so the CRM
  section degrades to `null` and is named in `unavailable` through the existing path (the parse raises at `c1_360.py:171-173`,
  `_guarded` turns it into `None`, `:209-228`, and `assemble` names it, `:301-302`); there is no
  key-literal fallback. The two images ship in one deploy and either order of recreation is safe: a new customer against an old deal degrades that one section for
  the seconds of skew, and an old customer simply ignores the extra fields. **This leg reaches deal only once R1.2a has fixed
  its path** (§3.3): before then the section is `null` for every contact whatever this release does, so R2.3 needs R1.2a, builds
  its customer tests on R1.2a's mount-aware fake, and leaves R1.2a's path assertion (`test_c1_customer360.py:154`) as it finds it.
- **Import and export** (`frontend/product-crm/src/data/importExport/deals.ts:86-89,157,264-296`, `runner.ts:93-96,198`): the
  importer resolves a label or a key through its own constants, which are correct while every organization is on the six.
  The server contract that keeps it safe once stages are editable is already in place: 422 (never 409) for an unknown key
  (DD-14), active labels unique case-insensitively (DD-11), keys never reused (DD-24), a label never equal to another
  stage's key (DD-45). The importer reading `GET /deals/stages` is SP3's (R3.4, G11).
- **Insights** (unmerged, `sprint/insights-q2`): no code here. The contract it rebases onto is fixed by this release: it
  decides won and lost by `to_outcome` / `stage_outcome` (never by the six keys), drops its `deal_facts` CHECK on the
  `stage` values, and treats `to_label` as display only (registry `L10`). `docs/insights/events.md`, `data-model.md` and
  `metrics.md` change in this release (registry `I7`).
- **Idempotency:** the producers are as idempotent as today (one outbox row per write in the write's transaction);
  `materialise_default_pipeline` is idempotent by construction (§6.4.9).

### 6.6 UI

No SPA change ships. Every legacy key and field is intact and the additions are optional to a reader, so the published
SPA keeps working against the new deal image. That holds only because no organization can leave the legacy six before R2.5:
the published bundle cannot render any other stage key (`data/derive.ts:70`), and R2.5 is where it is hardened and published
first (§8.6, DD-54). `frontend/opsgrids-visual/fixtures/api/crm/deals.json`,
`deals-list.json` and `summary.json` gain the new fields (`contracts.test.ts` validates each fixture against its
operation's response schema and would fail otherwise); no pixel changes, so no `visual:update` is needed. The stage
catalogue in the SPA, the board columns and the importer's lookup column moving to `GET /deals/stages` are SP3's (R3.4,
registry `G1`, `G11`).

### 6.7 Tests

Deal and customer tests run in their own processes (`import app` is ambiguous across services). Database tests use the
suites' existing real-Postgres fixtures and `pytest.mark.integration`.

**`services/deal/tests/test_pipeline_domain.py` (new, pure, no database).**

- `TestDefaultPipeline`: `test_the_default_equals_the_frozen_legacy_constants` (the test file holds verbatim copies of the
  pre-change `STAGE_ORDER`, `STAGE_LABELS`, `OPEN_STAGES`, `TERMINAL_STAGES` and `STAGE_DEFAULT_PROBABILITY` and compares them
  with the aliases derived from `DEFAULT_PIPELINE`); `test_the_default_passes_validate`; `test_the_entry_stage_is_lead`;
  `test_the_tones_and_probabilities_are_the_spas` (the table of §6.2).
- `TestValidate` (one test per rule, each asserting the `PipelineInvalid` code): `test_no_active_open_stage`,
  `test_no_active_won_stage`, `test_no_active_lost_stage`, `test_a_closed_stage_ordered_before_an_open_one`,
  `test_duplicate_keys`, `test_duplicate_active_labels_compare_case_insensitively`,
  `test_an_archived_stage_may_repeat_an_active_label`, `test_a_won_stage_must_carry_100_and_a_lost_stage_0`,
  `test_positions_must_be_dense_among_active_stages`, `test_a_key_outside_the_pattern`.
- `TestTransitions`: `test_the_6x6_grid_equals_the_legacy_can_transition` (frozen legacy copy over all 36 pairs plus three
  unknown keys); `test_a_custom_pipeline_moves_between_its_own_open_stages_and_closes_either_way`;
  `test_an_archived_target_is_refused`; `test_a_closed_deal_moves_nowhere_in_a_custom_pipeline`;
  `test_an_unknown_current_stage_is_not_a_transition`.
- `TestRollups`: `test_every_rollup_equals_the_legacy_function_over_a_seeded_book` (a `random.Random(20261007)` book of 300
  deals over the six keys and mixed probabilities, compared with frozen legacy copies of the seven functions);
  `test_win_rate_counts_every_won_and_lost_outcome_stage` (two won stages and one lost); `test_the_funnel_lists_active_open_stages_in_position_order`;
  `test_a_deal_in_an_unknown_stage_counts_as_open_and_is_not_in_the_funnel`; `test_is_last_open_is_the_highest_active_open_position`.

**`services/deal/tests/test_pipeline_db.py` (new).**

- `TestSchema`: `test_rls_is_enabled_and_forced_on_both_tables`; `test_each_table_has_the_tenant_isolation_policy_with_a_check`;
  `test_a_session_bound_to_another_organization_sees_no_pipeline_and_no_stage` (cross-tenant);
  `test_a_session_bound_to_another_organization_cannot_insert_into_this_ones` (the `WITH CHECK`);
  `test_a_stage_cannot_reference_another_organizations_pipeline` (the composite foreign key, executed as the migration role,
  which bypasses RLS, so the constraint is what refuses); `test_a_stage_key_is_never_reusable_even_after_archive`;
  `test_active_labels_are_unique_case_insensitively`; `test_an_archived_stage_may_share_an_active_label`;
  `test_key_pipeline_organization_and_outcome_are_frozen_by_the_trigger` (four updates, each refused; a label update
  allowed); `test_a_closing_stage_must_carry_its_pin`; `test_tone_key_and_position_checks`;
  `test_no_code_path_deletes_a_stage` (an AST scan of `services/deal/app` for `delete(PipelineStage)` and `DELETE FROM`).
- `TestMaterialise`: `test_it_writes_the_six_rows_one_audit_row_and_no_outbox_event`; `test_a_second_call_changes_nothing`;
  `test_two_concurrent_first_writes_make_one_pipeline` (two sessions, `asyncio.gather`); `test_the_materialised_spec_equals_the_virtual_default`
  (field for field, ids ignored); `test_organization_b_stays_virtual_while_a_is_materialised`.
- `TestLoad`: `test_a_bare_organization_reads_the_virtual_default`; `test_a_soft_deleted_stage_is_not_loaded`;
  `test_one_organizations_pipeline_never_reaches_another`.
- `TestLocks`: `test_a_stage_move_waits_for_an_open_stage_configuration_transaction` (session A holds
  `lock_pipeline_exclusive`; session B's `move_stage` under `SET LOCAL lock_timeout = '300ms'` raises, then succeeds after A
  commits); `test_two_moves_do_not_block_each_other` (both hold the shared lock).

**`services/deal/tests/test_migration_stages.py` (new, modelled on `customer/tests/test_migration_case_insensitive_company_name.py`).**
`test_the_adopted_closed_at_revision_is_byte_identical_to_the_insights_blob` (the git blob SHA-1 of the file equals
`be9f7b9f66102b70346d178c8a84299912a5245d`, computed in Python as `sha1(b"blob %d\0" % len(content) + content)`);
`test_upgrade_from_the_rls_revision_adds_closed_at_backfilled_from_audit_and_both_tables`;
`test_downgrade_is_clean_when_the_tables_are_empty`; `test_downgrade_is_clean_after_an_untouched_materialisation`;
`test_downgrade_refuses_when_a_custom_stage_exists`; `test_downgrade_refuses_when_a_deal_sits_in_a_custom_stage`;
`test_downgrade_with_the_override_drops_everything`; `test_the_deal_chain_has_one_head_and_orders_the_three_revisions` (exactly one head; `e94d7a2c5f18` has exactly one child, `a1c9e2f7b3d4`; and `e94d7a2c5f18`, `a1c9e2f7b3d4`, `f3b8d1a65c02` appear in that order along the chain, so the test stays true after R2.6 and later releases extend the chain and fails if any other revision is parented on `e94d7a2c5f18`: DD-51);
`test_the_deal_migration_session_runs_under_a_5s_lock_timeout` (static: `migrations/env.py` passes `server_settings` with `lock_timeout` `5s` to the engine it migrates through, and the adopted file is still byte-identical);
`test_the_adopted_revision_fails_fast_behind_a_held_lock` (a second connection holds `LOCK TABLE deal.deals IN ACCESS SHARE MODE` in an open transaction; an upgrade from `e94d7a2c5f18` through the real `env.py` raises `lock_not_available`, SQLSTATE 55P03, within about 5 s and leaves `alembic_version` at `e94d7a2c5f18` and `deals` without `closed_at`; once the holder commits, the same upgrade succeeds).

**`services/deal/tests/test_stages_read.py` (new, HTTP).** The negative-authz and cross-tenant set for `GET /deals/stages`:
`test_unauthenticated_is_401`; `test_missing_deal_read_is_403`; `test_without_the_crm_entitlement_is_402`;
`test_an_own_scoped_reader_may_read_the_stage_list` (explicit: tenant configuration, no row floor on the bare list);
`test_another_tenants_custom_label_is_never_returned` (organization B materialised with a renamed stage by a direct
insert; organization A's request returns A's own); the counts set (DD-53): `test_an_org_scoped_reader_gets_live_deal_counts_per_stage`,
`test_counts_never_include_a_soft_deleted_deal`, `test_an_own_or_team_scoped_reader_asking_for_counts_gets_the_stages_and_no_deal_count`
(parametrised over OWN and TEAM; the **negative-authz case for the counts**: the request succeeds, no stage carries
`deal_count`, and the response gives no way to infer one), `test_counts_never_cross_tenants` (organization B holds deals in the same
stage keys, including soft-deleted ones; A's counts cover A's rows only); plus
`test_a_bare_organization_gets_the_virtual_default_unmaterialised`, `test_a_materialised_organization_gets_its_rows`,
`test_include_archived_lists_archived_stages_at_any_scope`, `test_a_get_never_materialises` (row counts before and after),
`test_the_route_is_declared_before_the_detail_route`.

**`services/deal/tests/test_stage_writes.py` (new, HTTP and service).** `test_create_defaults_to_the_entry_stage`;
`test_create_in_an_unknown_stage_is_422_unknown_stage_with_field_stage`; `test_create_in_an_archived_stage_is_422_stage_archived`;
`test_a_move_into_an_archived_stage_is_422`; `test_closing_with_a_wrong_probability_is_422_probability_pinned`;
`test_closing_with_the_pin_or_none_succeeds_and_pins`; `test_a_closed_deal_moves_nowhere_409`;
`test_closed_at_is_set_once_and_never_changes`; `test_closed_at_is_set_for_a_deal_created_in_a_closed_stage`;
`test_the_outcome_filter_selects_by_outcome_not_by_key`; `test_no_entry_stage_is_422_no_entry_stage`;
`test_lead_conversion_with_no_entry_stage_answers_422_not_409` (the internal route); `test_the_audit_after_keys_are_unchanged`;
`test_a_custom_pipeline_works_end_to_end` (a materialised pipeline with `discovery`, `scoping`, `quote`, `won`, `lost`
inserted directly: create, move, close, rollups, funnel, `DealOut` fields, events); `test_an_unresolvable_stored_key_renders_open_and_never_raises`.

**`services/deal/tests/test_deal_events_contract.py` (new, producer-side).** Builds each of `created`, `updated`,
`stage_changed` through the real write path in a real transaction and calls
`validate_event_schema(event_type, 1, payload, contracts_root=CONTRACTS_ROOT)`, in the virtual mode, the materialised mode
and the custom-pipeline mode; `test_deleted_is_unchanged`; `test_the_payloads_carry_no_free_text_and_no_labels_beyond_to_label`.

**`services/deal/tests/test_no_stage_literals.py` (new, static).** An AST scan: no string constant equal to one of the six
legacy keys in any `.py` under `services/deal/app` **except** `pipeline.py` (docstrings and comments are not constants), and
none in `services/customer/app/c1_360.py` (customer's other `qualified` and `lead` constants are contact and company status
vocabularies, a different list). A self-test feeds the scanner a snippet containing each key and expects a finding.

**Existing deal tests edited on purpose** (and only these, beside SP1's `test_event_contracts.py` below): `conftest.py` (the `pipeline_mode` fixture, params `virtual` and
`materialised`, applied by `pytestmark` to `test_crud.py`, `test_write_routes.py`, `test_stage_transitions.py`, `test_authz.py`
and `test_lead_conversion_internal.py`; and the two new tables in `TENANT_TABLES`); `test_authz.py:255-266` (the exact key
set); `test_write_routes.py:195-216` (the unknown-stage cases assert `unknown_stage`); `test_lead_conversion_internal.py`
(the entry stage). `test_domain.py:47-113`, `test_stage_transitions.py:23-46` and `test_seed.py:48` keep calling the domain
with no spec, which is how DD-12 proves parity.

**`services/deal/tests/test_event_contracts.py` (SP1's file, created in R1.2b and edited here: SP1 §9.2.2).** In this
release's closing commit the `AHEAD_OF_PRODUCER` rows for `crm.pipeline.deal.created`, `.updated` and `.stage_changed`
(`EV6` to `EV8`) are deleted; the three events get the file's plain conformance tests (`test_deal_created_matches_its_schema`,
`test_deal_updated_matches_its_schema`, `test_deal_stage_changed_matches_its_schema`), built through the real write path like
the file's other events; and `libs/contracts/tests/test_event_schemas.py` loses the three names from
`test_the_ahead_of_producer_schemas_are_exactly_the_pinned_eight` (8 to 5), in the same commit that removes the three
`$comment`s. `test_deal_events_contract.py` above is the wider form of the same builds, run in all three pipeline modes. R1.2b
is not one of R2.3's prerequisites: if it has not merged, there is no pin to delete, R1.2b writes its table and its pinned set
without the deal events, and whichever merges second adapts (§17, item 12 (a)).

**Customer (`services/customer/tests/test_c1_customer360.py`, as R1.2a leaves it).** R2.3 builds on R1.2a's mount-aware
`RealDealRoutes` fake (SP1 §5.3.2), so the CRM leg is proved against the real route table (the committed
`libs/contracts/openapi/deal.json`, which R2.3 regenerates for `GET /deals/stages` and the widened stage types). The fake deal
responses carry the new fields; the pins at `:134,190` (`HEAD` line numbers, which R1.2a moves) stay and are fed through them;
the path assertion at `:154` is R1.2a's (`/api/v1/deals`) and R2.3 does not touch it. New: `test_the_furthest_open_stage_is_the_highest_position`;
`test_hot_when_a_deal_is_in_the_last_open_stage_or_at_or_above_70` (the legacy six: identical to the old rule over the grid
of stage x probability); `test_closed_deals_are_not_open_opportunities_by_outcome`;
`test_a_custom_stage_key_is_reported_verbatim`; `test_a_deal_row_without_stage_outcome_degrades_the_crm_section_and_names_it`.

**Contracts and frontend.** `libs/contracts/tests/test_event_schemas.py` gains
`test_the_deal_stage_events_use_the_key_pattern_and_require_outcomes` (static JSON: no six-value enum on `stage`, `from` or
`to`; `stage_outcome` required on `created` and `updated`; `from_outcome` and `to_outcome` required on `stage_changed`; no
`$comment` containing "Ahead of the producer"). `make openapi` regenerates `libs/contracts/openapi/deal.json` and
`customer.json` and `make openapi-check` proves it. `frontend/opsgrids-visual/fixtures/contracts.test.ts` passes with the three
updated fixtures; `make ui-visual` shows no baseline difference. Gates: `make gate` (static rows, `test-libs`,
`test-services`, `migration-roundtrip`, `migration-check`), including `tests/infra/test_migration_revisions.py`, which must
stay green with no edit. `tests/infra/test_purge_test_orgs_db.py` must also stay green with no edit, but it is **opt-in**
(`pytest.mark.skipif(..., reason=...)` on `OPSGRIDS_PURGE_ORGS_TEST_DATABASE_URL`, `tests/infra/test_purge_test_orgs_db.py:40-45`)
and the gate skips it: the plan runs it by hand against a migrated throwaway database for this release (the tables are new)
and pastes the pass count into the pull request.

### 6.8 Rollout

0. **Preconditions.** R1.2a is deployed (its path fix is what lets the 360's CRM leg reach deal at all, §3.3, so step 5's 360
   check can mean something), and the owner has run the **pre-check** of step 2.
1. Review; owner squash-merge; `make gate` locally (Actions billing is blocked).
2. **Owner, pre-check (read-only).** The census of D-10 is built in R2.6 and blocks R2.6, so it cannot size this release's backfill;
   these counts do. The same `psql` pattern as §7.4's count:

   ```
   docker compose -f compose.prod.yml --env-file .env exec -T postgres psql -U opsgrids -d opsgrids -At -v ON_ERROR_STOP=1 \
     -c "SET default_transaction_read_only = on" \
     -c "SELECT (SELECT count(*) FROM deal.deals) AS deals,
                (SELECT count(*) FROM deal.audit_logs WHERE action IN ('deal.stage_changed', 'deal.created')) AS stage_audit_rows,
                (SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid()
                    AND state = 'idle in transaction') AS idle_in_transaction,
                (SELECT count(DISTINCT l.pid) FROM pg_locks l
                    JOIN pg_class c ON c.oid = l.relation
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    JOIN pg_stat_activity a ON a.pid = l.pid
                  WHERE n.nspname = 'deal' AND c.relname = 'deals' AND l.granted AND l.pid <> pg_backend_pid()
                    AND l.database = (SELECT oid FROM pg_database WHERE datname = current_database())
                    AND a.xact_start < now() - interval '30 seconds') AS deal_lock_holders_older_than_30s"
   ```

   Expect the first two small (the backfill is one pass over them) and the last two zero: the last is the one that matters, a
   transaction open for over 30 s that holds a lock on `deal.deals`, which is exactly what the adopted `ADD COLUMN` would queue
   behind; the idle count is wider (any table) and is context. If either is not zero, find out why before deploying
   (`pg_stat_activity` shows `pid`, `state` and `xact_start` without the query text): the migration would otherwise fail on the 5 s
   bound below, which is safe and costs only a re-run, but the cause is worth knowing.
3. Owner-named `make deploy HOST=… CONFIRM=… REF=<sha>`. The diff touches `services/deal`, `services/customer`,
   `libs/contracts` and `frontend/opsgrids-visual`; `libs/contracts` is excluded from the full-rebuild rule, so only the `deal`
   and `customer` images rebuild. **Migrations run before `up`** (§3.7): deal moves `e94d7a2c5f18` → `a1c9e2f7b3d4` →
   `f3b8d1a65c02` in one transaction while the old deal code still serves. That is safe for the code: the old ORM never selects
   `closed_at`, and nothing references the new tables. For the **table** it is a bounded, not a brief, lock: the adopted
   revision's `ADD COLUMN` needs `ACCESS EXCLUSIVE` on `deal.deals` and holds it to the commit, through the backfill (a one-pass
   `UPDATE` over the deals' own audit rows), so deal reads and writes wait for as long as that takes, which at the volumes the
   pre-check shows is seconds. If the lock cannot be had within 5 s (DD-52) the migrate step fails, `set -e` stops the deploy
   before `up`, nothing is applied and the old containers keep serving; re-run once the holder is gone.
4. `deal` and `customer` recreate together and either order is safe: if the new customer image starts before the new deal image, it reads fields
   the old deal image does not emit, so for the seconds of skew the 360's CRM section degrades to `null` and is named in `unavailable`; nothing fails,
   and an old customer against a new deal ignores the extra fields.
5. Verify (owner-run reads, nothing written): `make edge-status`; `GET /api/v1/deals/stages` for a CRM tenant returns
   `materialised: false` and the six rows (and, for an ORG reader, `?include=counts` adds a live-deal count per stage); `GET /api/v1/deals`
   rows carry `stage_label`, `stage_outcome`, `stage_position`, `stage_is_last_open`, `closed_at`; **once R1.2a is deployed**, a
   contact with a deal shows its CRM section in the 360 (before R1.2a the section is `null` for every contact, with or without
   this release, and this check proves nothing); the SPA board still loads.
   `closed_at` is populated for deals closed before the deploy where the audit trail allows (the migration's own rule).
6. **Memory:** no container is added or resized, so `memory_budget_totals.py` is unchanged: core 1772 MB, `crm` 2067 MB,
   ten profiles 3747 MB, ten plus `admin` 3944 MB; `deal` stays `api` (98), `customer` stays `api_with_worker` (99);
   `EDGE_BUDGET_ACCEPTED_MB` is not touched. Cost at run time: one or two indexed point queries per deal list, detail and
   summary request, and one shared advisory lock per stage-binding write.
7. Documentation in the same release: `docs/insights/events.md`, `data-model.md`, `metrics.md` and `docs/architecture.html`
   (registry `I7`: stage vocabulary moves from keys to `outcome`; planned nodes are marked superseded, `architecture.html:677-686,739-754`),
   `docs/crm.md`'s event rows for the three deal events, and the register row 98 in `docs/adr/open-questions-register.md`.

### 6.9 Rollback

`make edge-rollback HOST=… CONFIRM=…` redeploys the previous ref for both images (roll them back **together**: customer
alone would keep reading fields deal no longer emits). The database stays ahead; `remote.sh migrate` skips a service whose
revision the image does not know (`infrastructure/edge/lib/migration-revisions.sh`), and the old code runs unharmed on the new
schema: it ignores the two empty tables and the nullable column. Events revert to the old payloads, which no consumer
validates. The migration downgrade is owner-run only and guarded (§6.2). **Stop conditions:** any 5xx from a `/deals` route;
a `ValidationProblem` on a stage move the legacy six allowed; any `leads:convert` answering 422; the 360 losing its CRM
section for contacts that have deals once both images are current **and R1.2a is deployed** (before R1.2a the section is already
`null`, so its absence is not evidence). A migrate step that fails on `lock_not_available` is a stop of the deploy, not a
rollback: nothing was applied and the old containers still serve; find the holder (§6.8.2) and re-run.

### 6.10 Acceptance

- **AC-2.3-1** Deal has exactly one Alembic head and its chain holds `e94d7a2c5f18`, `a1c9e2f7b3d4` and `f3b8d1a65c02` in that order, `e94d7a2c5f18` having exactly one child (`f3b8d1a65c02` is the head when R2.3 merges, and R2.6's revision follows it: §6.2, DD-51); the adopted file's blob SHA-1 is `be9f7b9f66102b70346d178c8a84299912a5245d`; `make migration-roundtrip` and `make migration-check` pass (tests; `git hash-object`).
- **AC-2.3-2** Both tables have RLS enabled and forced, the `tenant_isolation` policy with `WITH CHECK`, an organization index, and are in `TENANT_TABLES`; a session for another organization sees and writes nothing (tests); `tests/infra/test_purge_test_orgs_db.py` is green unedited (opt-in: run by hand against a migrated throwaway database, §6.7).
- **AC-2.3-3** The database refuses a cross-organization stage, a reused key, a duplicate active label, a mutated `key`/`pipeline_id`/`organization_id`/`outcome`, a closing stage without its pin, and a bad tone or key (tests).
- **AC-2.3-4** `DEFAULT_PIPELINE` equals the frozen legacy constants; the 6 x 6 transition grid and all seven rollups equal the legacy functions; the five stage-writing deal test files pass in both `pipeline_mode` values (tests).
- **AC-2.3-5** No legacy stage key appears as a string constant in `services/deal/app` outside `pipeline.py`, nor in `services/customer/app/c1_360.py` (static test with self-test).
- **AC-2.3-6** `GET /deals/stages` returns the virtual default or the materialised rows, never materialises, is declared before the detail route, and its 401, 403, 402, OWN-reader-allowed and cross-tenant tests pass; `include=counts` returns live-deal counts to an ORG reader only, never counts a soft-deleted deal, returns no `deal_count` to an OWN or TEAM reader and never counts another tenant's deals (DD-53); the `outcome` filter works (tests).
- **AC-2.3-7** Create and move answer 422 `unknown_stage`, 422 `stage_archived`, 422 `probability_pinned` and 422 `no_entry_stage` exactly as §6.3; a closed deal is 409; `closed_at` is set once (tests); lead conversion with no entry stage answers 422 and lead maps it to a rejection (test).
- **AC-2.3-8** Two concurrent first writes materialise once, and a move waits for an open exclusive lock (tests).
- **AC-2.3-9** The payloads of `created`, `updated` and `stage_changed`, built through the real write path in all three pipeline modes, validate against schemas that have the key pattern, require the outcome fields and carry no "Ahead of the producer" comment; `deal/tests/test_event_contracts.py` pins none of the three (its `AHEAD_OF_PRODUCER` has no deal row) and the pinned-eight test lists five names (tests; static contract test).
- **AC-2.3-10** The 360 reads only `stage`, `stage_outcome`, `stage_position`, `stage_is_last_open`, `probability`, `created_at`; its hot rule equals today's over the legacy grid; a row without `stage_outcome` degrades the section and names it (tests, on R1.2a's mount-aware fake; R2.3 does not edit R1.2a's path assertion).
- **AC-2.3-11** `make openapi-check`, `make contracts-check` and the visual fixture contract test pass; `make ui-visual` reports no baseline change.
- **AC-2.3-12** The only existing tests edited are those named in §6.7 plus SP1's `deal/tests/test_event_contracts.py` and `libs/contracts/tests/test_event_schemas.py` (`git diff --name-only` on `services/*/tests` and `libs/contracts/tests`); `docs/insights/*.md` and `docs/architecture.html` carry the `outcome` vocabulary (grep for `to_outcome`).
- **AC-2.3-13** Deal's migration connection runs under a 5 s `lock_timeout` set in `services/deal/migrations/env.py`, the adopted revision fails fast behind a held lock and applies nothing, and the adopted file is still byte-identical (tests); the rollout carries the owner's pre-check (§6.8.2).

## 7. R2.4 — Aborted-conversion consumers, log-only first

### 7.1 Purpose

Give `crm.lead.conversion_aborted` its first consumers (registry NEW-9): **deal** soft-deletes the aborted attempt's
orphan deal, **customer** soft-deletes the orphan contact and company, each only when it can prove the record is the
attempt's own and untouched since. Nothing acts on first deploy: both services start with
`CRM_CONVERSION_CLEANUP_MODE=log_only`, compute and log what they *would* do, and write nothing until the owner flips the
flag (pending D-1, D-2). The release also folds deal's first consumer into its API process, which is why it costs +1 MB and
needs the owner to raise `EDGE_BUDGET_ACCEPTED_MB` by one before it deploys (§7.8).

### 7.2 Data model

No table, column, migration, grant or RLS change; no service gains an Alembic revision. The release changes configuration
and infrastructure files instead (the tables below), plus one new committed test vector file.

| Artifact | Change |
|---|---|
| `services/deal/app/config.py` | `run_consumers_in_api: bool = False` (F1); `crm_conversion_cleanup_mode: Literal["off","log_only","on"] = "log_only"` (F2) |
| `services/customer/app/config.py` | `crm_conversion_cleanup_mode` (same); `deal_timeout_seconds: float = 3.0` beside `deal_internal_url` (F3) |
| `infrastructure/edge/compose.prod.yml` | `x-service-env`: `CRM_CONVERSION_CLEANUP_MODE: ${CRM_CONVERSION_CLEANUP_MODE:-log_only}`; a `deal-worker` service (`<<: *background`, `profiles: ['split-workers']`, `image: opsgrids-edge/deal`, `command: ['./bin/worker']`, `environment: *service-env`), the rollback shape of `customer-worker` (`:1449-1454`); the comment at `:88-90` stops listing `deal` among the services that ignore `RUN_CONSUMERS_IN_API` |
| `infrastructure/edge/memory-budget.json` | `containers.deal.kind`: `api` to `api_with_worker` with a note; `containers["deal-worker"] = {kind: background, profile: split-workers, note}` (priced 0 while its profile is off) |
| `libs/platform-core/src/platform_core/ops_monitor/config.py` | `PROFILE_DLQ_QUEUES["crm"] = ("customer.events", "deal.events")` and the stale comment above it (F5) |
| `libs/contracts/vectors/conversion_ids.v1.json` (new) | the namespace, the name templates and 24 golden vectors `(lead_id, generation) -> (company_id, contact_id, deal_id)`; read only by tests (DD-46, registry `A22`) |
| `.env.example` | **not edited**: the compose default is the safe state, and a key added there would become a preflight prerequisite (`RUNBOOK.md:530-535`) |

### 7.3 Backend contract

**The event.** `crm.lead.conversion_aborted` v1 as R2.1 defines it (`libs/contracts/events/crm.lead.conversion_aborted/v1.schema.json`).
No schema changes here. Bindings: `customer.events` gains the routing key `crm.lead.conversion_aborted` beside
`crm.pipeline.activity.logged`; `deal.events` is declared for the first time in production (today no process runs deal's
handler-less consumer, so the queue does not exist) with `x-dead-letter-exchange` and its `deal.events.dlq`
(`platform_core/events.py:462-471`). The event stays out of `_GUARANTEED_ROUTING_EVENT_TYPES` (DD-22).

**The new internal route (registry `C55`), deal, `GET /internal/v1/organizations/{organization_id}/references`.**

| Part | Contract |
|---|---|
| Auth | `require_service_token`, caller allow-list `customer` (`_require_caller`), organization claim must equal the path (`_require_org_claim`), tenant GUC bound from the path; the same guards as `leads:convert` (`deal/app/api/internal.py:64-80`) |
| Query | `company_id` (uuid, required), `contact_id` (uuid, optional), `exclude_deal_id` (uuid, optional) |
| Response | `{company_deal_count, contact_deal_count, company_activity_count, contact_activity_count}`, four non-negative integers, **counts only, no ids** |
| Deals counted | live (`deleted_at IS NULL`) deals with `company_id = :company_id` and, for the contact, `primary_contact_id = :contact_id` |
| `exclude_deal_id` | removed from the two deal counts **only when that deal is disposable**: absent, soft-deleted, or live and pristine (exactly one audit row, `deal.created` with `acting_as = 'lead'`, and no activity). A touched orphan is counted (DD-20). The predicate is one function, `services.is_pristine_lead_deal`, shared with deal's own handler, so the two can never disagree |
| Activities counted | rows of `deal.activities` whose `company_id` (resp. `contact_id`) equals the query value; the table is append-only, so these are every activity ever logged against that company or contact |
| Errors | 401 no token; 403 wrong caller or wrong organization claim; 422 malformed uuid. No 404: an unknown id counts zero |

Registry `C55` carries all four counts and the disposable-only exclusion (folded from this spec's first draft, whose
response held two counts); the two activity counts are there so that customer never deletes a company or contact that
history still names.

**Customer's client.** A new `services/customer/app/clients.py` with `DEAL_AUDIENCE = "deal"` and
`deal_references(settings, organization_id, *, company_id, contact_id, exclude_deal_id, client=None)`: a service token with
`claims={"org": str(organization_id)}` (`internal_auth.mint_service_token`), timeout `deal_timeout_seconds`, any transport error, timeout or
non-200 raises `ReferencesProbeUnavailable`. It is called only from the handler's second phase (§7.5). Its call (W13, `C55`)
is registered in `tests/infra/test_internal_client_paths.py` `CALLS` in the same commit, with a driver that returns the token
it sent, because that guard flags a new internal client until it is registered (SP1 §5.3.2; §17, item 12 (b)).

**Service changes.**

- `deal/app/services.py`: `delete_deal` gains optional `acting_as` and `reason`, passed to `record_audit`
  (`platform_core/audit.py:153-185` supports both); new `service_ctx(organization_id)` mirroring
  `customer/app/services.py:141-155` (all-zero `sub`, ORG scope, `membership_id=None`, a permission string only to build the
  context), so `own_ref` is `None` and nothing OWN-scoped can run under it; new `is_pristine_lead_deal`.
- `customer/app/services.py`: `delete_company` and `delete_contact` gain the same two optional arguments. **Reuse leaves a
  trace** (DD-18): `ensure_company_and_contact_for_lead` now looks up the company by name and the contact by email
  `FOR UPDATE` (after the existing `_lock_company_name`), and a reuse **by name or by email** writes one marker audit row,
  `company.reused_for_lead` or `contact.reused_for_lead`, `acting_as = 'lead'`, `after = {"matched_by": "name"|"email"}`
  (ids and a fixed word only), **where today's code writes none**: a company reused by name, and a contact reused by email
  when the lead brings no phone or the same masked phone (§3.2). A contact reuse that already writes `contact.updated` (the
  contact had no phone: gap-fill) or `contact.reused_for_lead` (a different phone is on file: today's row, with the two masked
  phones) writes exactly that one row and no second; a reuse **by id**
  (a same-generation replay of the attempt's own record) writes none. Without this, a record a *later* attempt adopted would
  still look untouched.
- `lead`: no change (R2.1 already ships the event).

**Audit rows the handlers write** (ids only): `deal.deleted`, `contact.deleted`, `company.deleted` (the existing actions) with
`acting_as = 'crm-conversion-cleanup'` and `reason = 'lead_conversion_aborted:<lead_id>:g<generation>'`. Events staged are the
existing `crm.pipeline.deal.deleted`, `crm.book.contact.deleted`, `crm.book.company.deleted` with unchanged payloads (so no
schema changes). **No `crm.pipeline.deal.stage_changed` is ever staged** by the cleanup (pending D-1: a tenant automation must
not fire for it).

**Settings semantics** (DD-21, DD-47). `CRM_CONVERSION_CLEANUP_MODE` is read by both services, one name, default `log_only` in
code and in compose. A validator strips and lower-cases the value; anything other than `off`, `log_only` or `on` becomes
`log_only` and logs `crm.conversion_cleanup.mode_unknown` once at start (a typo in a box `.env` must neither crash the two
services nor, worse, arm the cleanup). `off` acknowledges and ignores; `log_only` plans and logs; `on` acts.

### 7.4 Domain rules, and the historical sweep (D-3)

**Ids are re-derived, never read from the payload.** For an event `(organization_id, lead_id, generation)` each handler
computes `(company_id, contact_id, deal_id) = conversion_ids(lead_id, generation)` with its own copy of lead's function
(`lead/app/domain.py:118-145`: uuid5 in namespace `6f2e6f3a-6d1e-4b6a-9c8e-2f7a5b1d4c90`, names `"{lead_id}:company"`,
`"{lead_id}:contact"`, `"{lead_id}:deal"`, each suffixed `":{generation}"` for every generation after 0). The payload's
three ids may be records customer or deal *returned* (a company matched by name), so they are logged for the owner and
never acted on. Three copies of a ten-line function are held equal by the committed vectors, not by an import (DD-46). The
contact id is derived only when the payload's `contact_id` is not null (the lead had an email).

**An event is ignored, with a log line and an acknowledgement, when:** `generation` is absent or not an integer >= 0, or
`claim_id` is absent (fail closed: an event from a producer older than R2.1 acts on nothing); a uuid does not parse; the
envelope's top-level `organization_id` (`build_envelope`, `events.py:179`) disagrees with the payload's; `reason` is not one of the three known values; or the
mode is `off`.

**Provenance, the rule for any record the cleanup may delete** (DD-18):

| Record | Deletable only if all hold |
|---|---|
| orphan **deal** | the row exists, is live, and its id is the derived deal id; its audit history is exactly one row, `deal.created` with `acting_as = 'lead'`; no `deal.activities` row names it |
| orphan **contact** | the id is the derived contact id; live; audit history is exactly one row, `contact.created` with `acting_as = 'lead'`; `last_activity_at IS NULL` |
| orphan **company** | the id is the derived company id; live; audit history is exactly one row, `company.created` with `acting_as = 'lead'`; **no live contact other than the derived contact** names it; the references probe answers zero deals and zero activities for the company and for the derived contact |

Every other state is **kept**, with a reason code in the log (`absent`, `already_deleted`, `not_derived_id`,
`not_created_by_attempt`, `touched`, `adopted`, `other_contacts`, `referenced`). A record created by a different mechanism,
reused by name or email, edited, moved, given an activity or adopted by a later attempt is never deleted. The deletion order is
**contact, then company** in one transaction (`delete_company` refuses while the company has a live contact,
`customer/app/services.py:573-577`). Deleting is always a soft delete.

**The two services decide independently and agree whichever runs first** (DD-20): customer's probe excludes the aborted
attempt's deal only if it is disposable, so a deal that deal keeps (touched) keeps the company too, and a deal that deal is
about to delete does not hold the company back.

**Closing the adoption race.** A later attempt (generation + 1) finds the orphan company *by name* and adopts it
(`customer/app/services.py:1194-1195`); with the marker row that writes, a cleanup that runs afterwards sees two audit rows and
keeps it. A cleanup that runs first commits the soft delete under the row lock, and the adoption's locked re-read no longer
finds the company (its predicate includes `deleted_at IS NULL`), so the later attempt creates a fresh one. The residual window
is the probe-to-commit gap of one cleanup (milliseconds to seconds) in which a rep could create a deal for that very company
by hand; the consequence is a soft-deleted company id on that deal, which renders as the dash the book already shows for
a deleted company (`customer/app/services.py:569-572`) and is reversible by clearing `deleted_at`. It is listed in §14.

**The historical sweep (pending D-3).** Every `crm.lead.conversion_aborted` ever staged was dropped for want of a queue
(§3.2), so aborts before R2.1 left records no code will ever clean. The evidence is `lead.audit_logs`. The rule for what is
sweepable follows from R2.1's generation bump:

- An abort whose audit row records `conversion_generation` (every `owner_changed` abort, and every `lead.conversion_failed`
  row that bumped for a deleted record) bumped the generation, so the aborted attempt's ids are
  `conversion_ids(lead_id, recorded_generation - 1)` and no later attempt can reuse them: **sweepable**.
- A pre-R2.1 `status_changed` abort recorded no generation and did not bump, so the records it left sit at the generation
  the lead *still derives its next conversion from*: a re-qualified lead adopts them by id, so they are not orphans and **no code
  path deletes them** (the worst case if one were deleted is one failed attempt that then bumps; it is not worth the risk).

The owner-run, read-only count (D-3, D-10; `make edge-*` has no wrapper for it, so it is the RUNBOOK's own `psql` pattern,
`RUNBOOK.md:748,1000`):

```
docker compose -f compose.prod.yml --env-file .env exec -T postgres psql -U opsgrids -d opsgrids -At -v ON_ERROR_STOP=1 \
  -c "SET default_transaction_read_only = on" \
  -c "SELECT a.organization_id,
             count(*) FILTER (WHERE a.action = 'lead.conversion_aborted' AND a.after ? 'conversion_generation') AS owner_changed_aborts,
             count(*) FILTER (WHERE a.action = 'lead.conversion_aborted' AND NOT (a.after ? 'conversion_generation')) AS status_changed_aborts,
             count(*) FILTER (WHERE a.action = 'lead.conversion_failed'  AND a.after ? 'conversion_generation') AS deleted_record_bumps
        FROM lead.audit_logs a
       WHERE a.action IN ('lead.conversion_aborted', 'lead.conversion_failed')
       GROUP BY a.organization_id ORDER BY a.organization_id"
```

**Decision rule.** If `owner_changed_aborts + deleted_record_bumps` is zero for every organization, **no sweep tool is built**
and the count is recorded in `docs/crm.md` and register row 122. If it is non-zero, `tools/sweep_aborted_conversions.py` (H3) is
built in this release's plan, designed as follows:

- **It replays history through the same handlers.** For each sweepable audit row it builds a synthetic envelope (`event_type`
  `crm.lead.conversion_aborted`, `event_id = uuid5(sweep namespace, "{organization_id}:{lead_id}:{generation}")`, the payload of
  R2.1's schema with `reason` taken from the row) and feeds it to the customer and deal handlers in `on` mode, one child process
  per service (`--service customer|deal`; `import app` is ambiguous across services). Nothing in the tool decides what is safe;
  the handlers do (DD-48). The deterministic event id makes a re-run idempotent through each service's ledger.
- `report` (the default, read-only, `SET TRANSACTION READ ONLY`): for each candidate it **re-reads the lead's current state**
  (`converted_company_id`, `converted_contact_id`, `converted_deal_id`, `conversion_generation`) and drops any candidate whose
  derived ids equal the lead's converted ids or whose generation is not strictly below the lead's current one; it prints counts
  per organization and per decision code, ids only.
- `apply` (gated like the purge tools: dry run unless `--apply` **and** `--confirm-count N`, `--i-took-a-backup` and
  `--i-know-this-is-the-live-database`; `tools/purge_test_orgs.py:1892-1910`): one transaction per organization per service on
  the NOBYPASSRLS app role with the tenant GUC bound, stop at the first failing organization, pace `--pace` (default 250 ms),
  output counts and ids only, one audit row per organization per service naming the tool.
- Its tests: report equals apply's would-act set; a lead converted since is never swept; a re-run is a no-op; the three gates
  refuse without their flags; a touched orphan is kept (§7.7 says which run in the gate and which are opt-in).

### 7.5 Consumers and idempotency

**Deal's handler** (`deal/app/consumers.py`, `deal_conversion_aborted`, bound on `deal.events`): one transaction, no outbound
call.

1. Parse and ignore rules of §7.4. 2. `log_only`: open a read-only scope, evaluate, log `crm.conversion_cleanup.would_apply` or
`would_keep` (ids and counts), write **nothing, not even the ledger row** (a ledger row written during the soak would make a
later replay of the same event a no-op after the flip), acknowledge. 3. `on`: `_session_scope(organization_id)`; if
`already_processed` return; `SELECT ... FOR UPDATE` the derived deal in any state; apply the provenance rule; if deletable call
`services.delete_deal(session, ctx, deal, acting_as='crm-conversion-cleanup', reason=reason)`; `mark_processed` in the same transaction.

**Customer's handler** (`customer/app/consumers.py`, `customer_conversion_aborted`, bound on `customer.events`) is
**three-phase**, because the references probe is a network call and D3 forbids one inside a transaction (DD-19):

1. **Plan** (read-only transaction): `already_processed` check; read the derived company and contact in any state; evaluate every
   provenance rule that needs no other service. The transaction ends before anything else happens.
2. **Probe** (no transaction open): only if the plan contemplates deleting the company or the contact, call
   `deal_references(settings, organization_id, company_id=<derived>, contact_id=<derived or None>, exclude_deal_id=<derived deal id>)`. Any failure **raises** `ReferencesProbeUnavailable`, which earns
   the one redelivery and then the dead-letter queue (`events.py:496-516`); acknowledging would lose the cleanup silently, and
   writing nothing on a half-answer is the only safe state. In `log_only` the probe still runs, because "would apply" is only
   honest with the deal side's answer; an unreachable deal in `log_only` therefore also redelivers, and the resulting
   dead-letter entry is itself the signal the owner should see before flipping to `on`.
3. **Act** (`on` only): a new transaction; `already_processed` again; `FOR UPDATE` the company and contact; **re-verify every
   rule under the locks** (a rep or a later attempt may have changed them since phase 1); if the plan still holds, soft-delete
   the contact then the company with `acting_as` and `reason`; `mark_processed`. In `on` mode a decision to keep also writes the
   ledger row, so an event is decided once.

**Redelivery and duplicates.** A message that fails is redelivered once, then dead-lettered; both handlers are idempotent by
`processed_events` (written with the effect in `on` mode) and by state (an already-deleted record is `already_deleted`, a no-op).
A duplicate publish with a new `event_id` for the same `(lead, generation)` finds the records deleted or kept and changes
nothing. A handler that raises leaves no ledger row. `prefetch=16` with sequential handling (`events.py:440,457,478-480`) means one
message at a time per consumer.

**Fold wiring (deal).** `deal/app/main.py` gains `_start_background_worker` and `_stop_background_worker` copied from
`customer/app/main.py:24-65` and passed as `on_startup` and `on_shutdown`: when `Settings.run_consumers_in_api` is true it builds a
2-connection bulkhead engine (`create_bulkhead_engine(cfg, name="worker")`), points the consumer's `_session_scope` at
`bulkhead_scope(engine)` and starts `consumers.consumer.run()` as a supervised task; off, nothing starts and `bin/worker`
(`python -m app.consumers`, which already exists) runs the same consumer standalone. `deal/app/consumers.py` gains the
`_session_scope` and `use_session_scope` seam, the handler and `consumer.on("crm.lead.conversion_aborted")(...)`. The compose
default is `RUN_CONSUMERS_IN_API=true`, so **the consumer starts on the box at deploy**; that is why the cleanup mode defaults to
`log_only`. Cost: up to two more Postgres connections per deal process, opened lazily (`max_connections=400`,
`compose.prod.yml:263`). The customer consumer is already folded and only gains a handler and a binding.

**DLQ visibility.** `deal.events.dlq` and `customer.events.dlq` are watched by ops-monitor through
`PROFILE_DLQ_QUEUES["crm"]`; `tests/infra/test_ops_monitor_dlq_queue_sync.py` demands the `deal-worker` compose service for
`deal.events` in both directions, which is why the rollback container ships in the same commit.

### 7.6 UI

None. The only user-visible effect, once the owner flips `on`, is that an orphan deal, contact or company disappears from the
lists, and each carries an audit row whose `acting_as` names `crm-conversion-cleanup`. There is no settings screen for the mode:
it is an owner decision (D-2), taken on the box.

### 7.7 Tests

Each service tests in its own process. The consumers' tests call the handler directly with an envelope, the process engine
stood in by an autouse fixture, assertions read back through the tenant-bound `session` fixture after `expire_all()`
(`customer/tests/test_consumers.py:1-60` is the shape). The broker is faked, as in every service suite. **Every envelope in
these tests is built by one helper, `aborted_envelope(...)`, that validates its payload with `validate_event_schema(
"crm.lead.conversion_aborted", 1, payload, contracts_root=CONTRACTS_ROOT)` before returning it**, so a consumer's test input
cannot drift from the schema lead's producer-side tests (R2.1) are pinned to; that pairing is the cross-service contract proof
(DD-49: R2.4 does not extend `e2e/`, registry `L14`).

**Shared vectors.** `libs/contracts/vectors/conversion_ids.v1.json` is asserted by `lead/tests/test_domain.py`
(`test_conversion_ids_match_the_committed_vectors`), `deal/tests/test_conversion_cleanup_ids.py` and
`customer/tests/test_conversion_cleanup_ids.py`: all 24 vectors, generations 0, 1, 2 and 7, and `test_generation_zero_has_no_suffix`.

**`services/deal/tests/test_consumers.py` (new).**

- Ignore rules: `test_an_event_without_a_generation_is_ignored_and_acknowledged`, `test_an_event_without_a_claim_id_is_ignored_and_acknowledged`,
  `test_a_non_integer_or_negative_generation_is_ignored`, `test_an_envelope_whose_org_disagrees_with_its_payload_is_ignored`,
  `test_an_unknown_reason_is_ignored`, `test_mode_off_acknowledges_and_changes_nothing`.
- `log_only`: `test_log_only_logs_would_apply_and_writes_nothing_not_even_the_ledger` (no audit row, no outbox row, no
  `processed_events` row, the `crm.conversion_cleanup.would_apply` line carries ids and counts only).
- `on`: `test_a_pristine_orphan_deal_is_soft_deleted_with_an_audit_row_naming_the_consumer` (asserts `acting_as`,
  `reason`, exactly one `crm.pipeline.deal.deleted` outbox row, **no** `stage_changed` row, one ledger row);
  `test_a_deal_with_an_activity_is_kept`; `test_a_deal_that_was_moved_is_kept`; `test_a_deal_edited_by_a_rep_is_kept`;
  `test_a_deal_not_created_by_lead_is_kept`; `test_an_absent_deal_is_a_no_op`; `test_an_already_deleted_deal_is_a_no_op`;
  `test_a_live_deal_at_the_payloads_id_but_not_the_derived_id_is_never_touched`;
  `test_the_derived_id_at_the_events_generation_is_used_not_the_current_one` (generation 1 event, a pristine deal at the
  generation-0 id survives); `test_redelivery_is_idempotent` (the same envelope twice: one delete, one outbox row, one ledger
  row); `test_a_new_event_id_for_the_same_lead_and_generation_changes_nothing`; `test_another_organizations_deal_at_the_derived_id_is_untouched`
  (cross-tenant: the row is inserted for organization B with the same id through the admin session); `test_a_handler_error_leaves_no_ledger_row`.
- Probe route: `test_references_counts_live_deals_by_company_and_by_contact`; `test_exclude_deal_id_removes_only_a_disposable_deal`
  (live pristine: removed; live touched: counted; soft-deleted: not counted either way); `test_activity_counts_include_every_activity_naming_the_company_or_contact`;
  `test_references_without_a_token_is_401`; `test_references_from_lead_or_notification_is_403`; `test_references_with_another_organizations_claim_is_403`;
  `test_references_never_counts_another_tenants_deals`; `test_the_two_services_predicates_agree` (the same fixture decided by
  `is_pristine_lead_deal` through the handler and through the route).
- `services/deal/tests/test_main_worker_wiring.py` (new, copy of customer's): the consumer starts as a supervised task when
  `run_consumers_in_api` is on, stays off otherwise, builds a bulkhead engine named `worker`, and is cancelled and disposed on shutdown.
- `services/deal/tests/test_config_mode.py`: `test_the_mode_defaults_to_log_only`, `test_an_unknown_value_falls_back_to_log_only_and_logs`,
  `test_a_value_is_stripped_and_lower_cased`.

**`services/customer/tests/test_conversion_cleanup.py` (new).**

- The three phases: `test_the_probe_runs_with_no_transaction_open` (the fake probe asserts the session scope is closed);
  `test_a_probe_failure_raises_so_the_message_is_redelivered` (timeout, connection error, 500, 403 each); `test_log_only_runs_the_probe_and_writes_nothing`;
  `test_off_does_not_call_the_probe`; `test_nothing_to_delete_never_calls_the_probe`; `test_the_act_phase_reverifies_under_locks`
  (the company is edited between the phases: kept).
- Provenance: `test_a_company_and_contact_created_by_the_attempt_are_soft_deleted_contact_first` (audit `acting_as`, `reason`,
  outbox order `contact.deleted` then `company.deleted`); `test_a_company_reused_by_name_is_kept`;
  `test_a_contact_reused_by_email_is_kept` (all three reuse shapes: no phone, gap-fill, differing phone);
  `test_a_company_edited_after_creation_is_kept`; `test_a_contact_with_activity_is_kept`;
  `test_a_company_with_another_live_contact_is_kept`; `test_a_company_referenced_by_a_live_deal_is_kept`;
  `test_a_company_referenced_by_an_activity_is_kept`; `test_a_deal_that_deal_keeps_keeps_the_company` (probe returns the touched
  deal as counted); `test_an_id_that_is_not_the_derived_id_is_never_touched`; `test_absent_and_already_deleted_are_no_ops`;
  `test_a_lead_without_an_email_deletes_only_the_company`; `test_redelivery_is_idempotent`;
  `test_another_organizations_rows_at_the_derived_ids_are_untouched` (cross-tenant).
- The adoption race (service level, two sessions): `test_a_reuse_by_name_writes_a_marker_audit_row_and_locks_the_row`;
  `test_a_contact_reuse_by_email_writes_exactly_one_audit_row_whatever_the_phone_shape` (no phone and the same masked phone:
  the marker; a phone gap-fill: `contact.updated` only; a differing phone: `contact.reused_for_lead` only);
  `test_a_reuse_by_id_writes_no_marker`; `test_a_cleanup_then_an_adoption_creates_a_fresh_company` (cleanup commits first, the
  adoption's locked re-read finds nothing); `test_an_adoption_then_a_cleanup_keeps_the_company` (the marker is the second audit row);
  `test_cleanup_and_adoption_serialise_on_the_row_lock` (the second waits under `lock_timeout`).
- `test_the_deal_client_sends_a_service_token_for_audience_deal_with_the_org_claim` and `test_the_client_parses_the_four_counts`;
  and `tests/infra/test_internal_client_paths.py` (SP1) gains the `deal_references` call in `CALLS` (W13, `C55`), so its probe
  finds the route deal serves and its token names the organization.
- Existing `test_lead_conversion_internal.py` gains the marker-row cases; `test_main_worker_wiring.py` is unchanged and green.

**Infrastructure and platform tests.**

- `libs/platform-core/tests/test_ops_monitor_config.py` (edited on purpose): `test_a_profile_with_no_worker_adds_containers_but_no_queues`
  (`:75-85`) is replaced by `test_crm_runs_customers_and_deals_workers_and_leads_none`, asserting
  `expected_dlq_queues(["crm"]) == [*CORE_DLQ_QUEUES, "customer.events", "deal.events"]`.
- `tests/infra/test_ops_monitor_dlq_queue_sync.py`, `test_ops_monitor_profile_containers.py`, `test_compose_memory_budget.py`,
  `test_memory_budget_totals_containers.py`, `test_planned_headroom.py` (including
  `test_runbook_ceiling_example_is_the_number_preflight_would_print`: the RUNBOOK must contain `EDGE_BUDGET_ACCEPTED_MB=3748`) and
  `test_io_outside_transaction.py` pass with **no ALLOWLIST entry added**.
- `tests/infra/test_conversion_cleanup_wiring.py` (new): `x-service-env` carries `CRM_CONVERSION_CLEANUP_MODE` with the default
  `log_only`; deal's and customer's `Settings` both declare the field with that default; `.env.example` does not name the key;
  `deal-worker` exists with `profiles: ['split-workers']` and `command: ['./bin/worker']`; `memory-budget.json` prices it
  `background` under `split-workers` and prices `deal` as `api_with_worker`; the comment at `compose.prod.yml:88-90` no longer
  lists `deal`.
- `tests/infra/test_remote_rebuild_decision.py` gains `test_the_r2_4_diff_forces_a_full_rebuild` (a throwaway repo whose diff
  touches `libs/platform-core/src/platform_core/ops_monitor/config.py` makes `needs_full_rebuild` return 0).

**The sweep tool (only if built, §7.4).** Two files, split by what they need, because `make test-infra` is the gate's
**static** row (no database, no broker: `Makefile:177-182`, `GATE_STATIC`, `Makefile:525-526`):

- `tests/infra/test_sweep_aborted_conversions.py` (static, runs in the gate): each gate flag is refused without its partner;
  the dry run is the default; the output contains ids and counts only; the synthetic envelope validates against the committed
  schema.
- `tests/infra/test_sweep_aborted_conversions_db.py` (**opt-in**, the pattern of `test_purge_test_orgs_db.py`:
  `pytestmark = pytest.mark.skipif(not REAL_DSN, reason="set OPSGRIDS_SWEEP_TEST_DATABASE_URL to a migrated throwaway database (as opsgrids_admin)")`,
  which `make hygiene` accepts because it names a reason): report equals the apply set; a lead converted since is never swept;
  a re-run is a no-op; a touched orphan is kept. The gate skips it with that reason, so it is **a manual step of the plan's
  R2.4 verification**: `make migrate` into a throwaway database, then `OPSGRIDS_SWEEP_TEST_DATABASE_URL=… uv run pytest
  tests/infra/test_sweep_aborted_conversions_db.py`, the pass count pasted into the pull request. The decision logic itself is
  the handlers' (DD-48) and is proved by the handler tests above, which do run in `make test-services` (`GATE_DB`).

Gates: `make gate` (static rows including `typecheck` per service, `test-libs`, `test-services`, `contracts-check`,
`openapi-check` for deal's new internal route not appearing in the public OpenAPI: internal routers are mounted outside it).

### 7.8 Rollout

**Preconditions.** R2.1 has been deployed (DD-23). The owner has answered D-1 and D-2 or accepted their defaults, has run the D-3
count (§7.4) and recorded it, and has agreed to raise the memory ceiling (D-13, registry `L17`).

**Memory-budget arithmetic** (`python3 infrastructure/edge/lib/memory_budget_totals.py infrastructure/edge/memory-budget.json
--profiles …`, read-only; reference box 3862 MB, soft line 85 percent = 3282, hard line 100 percent = 3862, accepted-ceiling cap
125 percent = 4827):

| Quantity | Before R2.4 | After R2.4 | Why |
|---|---|---|---|
| `deal` kind | `api`, 98 MB | `api_with_worker`, 99 MB | the first handler folds into the API process (`kinds.api_with_worker`) |
| `deal-worker` | none | `background`, profile `split-workers`, priced 0 while off | the rollback definition the DLQ-sync test demands; it never starts by default, so **no container runs that did not run before** |
| core | 1772 | 1772 | unchanged |
| `crm` profile | 2067 | **2068** | +1 |
| the ten sellable profiles | 3747 (97.0 percent) | **3748** (97.0 percent) | +1; under the hard line, over the soft one, as today |
| the ten plus `admin` (what the box runs) | 3944 (102.1 percent) | **3945** (102.1 percent) | +1; over the hard line, so it needs the owner's ceiling |
| `EDGE_BUDGET_ACCEPTED_MB` in the box `.env` | 3944 | **must be at least 3945 before the deploy** | owner-only edit; preflight refuses a deploy over the ceiling and changes nothing |

Postgres connections: up to two more per deal process, lazily (`max_connections=400`). Later folds (lead, support, marketing,
storefront) each add another +1 MB and own their own ceiling step (registry `F8`, `L17`); none is in this release.

**Steps.**

1. Review; owner squash-merge; `make gate` locally (Actions billing is blocked).
2. **Owner, on the box:** raise `EDGE_BUDGET_ACCEPTED_MB` to 3945 in the box `.env` (mode 600, as the RUNBOOK requires). An agent never
   edits it.
3. Owner-named `make deploy HOST=… CONFIRM=… REF=<sha>`. `libs/platform-core` changed, so `needs_full_rebuild` is true: **every
   image rebuilds and recreates** (same window caveat as §5.9; run it quiet). The deploy's own preflight runs after checkout and
   refuses, before any build, if the ceiling is still 3944. No migration runs for any service.
4. Verify (owner-run, read-only): `make edge-status` (`deal` healthy); `make edge-logs SERVICE=deal` shows
   `consumer.started` with `queue=deal.events` and `bindings=['crm.lead.conversion_aborted']`; `make edge-logs SERVICE=customer`
   shows the second binding on `customer.events`; the broker lists both bindings; ops-monitor's queue list includes
   `deal.events.dlq`; `docker stats --no-stream deal` is about 1 MB above its previous idle figure (an arm64 measurement
   priced it; the box is amd64, so read the delta, not the absolute).
5. **Soak in `log_only`.** The owner reads `crm.conversion_cleanup.would_apply` and `would_keep` lines (ids and counts only) with
   `make edge-logs SERVICE=customer` and `SERVICE=deal`, and checks any against the lead's audit rows. Aborts are rare by
   construction (§3.2), so the soak may see none; the owner decides how long to wait and what absence of evidence is worth.
6. **Flip (owner only, pending D-2):** set `CRM_CONVERSION_CLEANUP_MODE=on` in the box `.env`, then recreate the two services with
   the RUNBOOK's pattern (`docker compose -f compose.prod.yml --env-file .env up -d customer deal`, as `RUNBOOK.md:1162`
   does for payment); the start-up log line states the effective mode. The deal side and the customer side flip together
   (DD-21): never one without the other.
7. Documentation in the release (registry `I8`): `docs/deploy/ENV_SOURCE_MATRIX.md` (the `crm` queue and container rows, the 12
   queues and the container count each gain one, the lead relay gap note), `docs/deploy/env-reference.md:542`,
   `infrastructure/edge/RUNBOOK.md` (the split-workers rollback list at about `:2518` and `:3018`; the literals `:2521-2525`,
   `:2980`, `:2996-3011`, `:3029`, `:3042-3049`, `:3063` each moved to this release's own totals, the ten-profile example to
   `EDGE_BUDGET_ACCEPTED_MB=3748`; a new section for the flip, the soak and the rollback below), `docs/crm.md`, and register rows 97
   and 122 in `docs/adr/open-questions-register.md`.

### 7.9 Rollback

In order of preference, each independent of the others:

1. **Stop acting without a deploy:** set `CRM_CONVERSION_CLEANUP_MODE=log_only` (or `off`) in the box `.env` and recreate `customer`
   and `deal`. Takes effect for the next message.
2. **Undo what was done:** every deletion is soft and carries an audit row with `acting_as = 'crm-conversion-cleanup'` and the
   lead and generation in `reason`. Restoring a record is owner-run SQL driven by those rows (clear `deleted_at`); no code does
   it. The `deleted` events already published are not recalled; no subscribable key reacts to them today.
3. **Code rollback:** `make edge-rollback HOST=… CONFIRM=…` redeploys the previous ref (a full rebuild again). `deal.events` and its
   binding stay on the broker as an inert queue: aborted events are near-zero volume, so it collects nothing meaningful, and the
   owner may delete it with the broker's own tooling if the rollback is long-lived. No schema changed, so nothing is skipped.
4. **Un-fold without un-shipping** (`RUN_CONSUMERS_IN_API=false` plus `split-workers` in `EDGE_PROFILES`): this starts
   `deal-worker`, a background container priced at 90 MB, which the box's ceiling probably cannot absorb. Prefer step 1.

**Stop conditions:** any `consumer.handler.dead_lettered` line or any depth on `deal.events.dlq` or `customer.events.dlq`; any
`crm.conversion_cleanup.applied` for records the owner did not expect; a `409 company-deleted` rise on `leads/{id}:convert`
after the flip; either container restarting.

### 7.10 Acceptance

- **AC-2.4-1** The diff adds no file under `services/*/migrations/versions`, and no Alembic head moves (`git diff --name-only`).
- **AC-2.4-2** Lead, deal and customer each match all 24 vectors of `libs/contracts/vectors/conversion_ids.v1.json` (three tests).
- **AC-2.4-3** Deal's handler soft-deletes only a pristine derived deal, with an audit row naming `crm-conversion-cleanup`, one `crm.pipeline.deal.deleted` outbox row and no `stage_changed`; every other state is kept or a no-op (tests).
- **AC-2.4-4** Customer's handler deletes the contact then the company only when every provenance rule of §7.4 holds, including the probe's four zeros, and keeps every other state (tests).
- **AC-2.4-5** The probe runs with no transaction open; any probe failure raises (redelivery then DLQ); `log_only` runs the probe and writes nothing, not even a ledger row; `off` does nothing; `tests/infra/test_io_outside_transaction.py` is green with no ALLOWLIST change; the customer-to-deal call is registered in `tests/infra/test_internal_client_paths.py` `CALLS` (tests).
- **AC-2.4-6** The references route counts as §7.3 states, decides disposability with the same function the handler uses, answers 401, 403 (wrong caller), 403 (wrong organization claim) and never counts another tenant's rows (tests).
- **AC-2.4-7** Redelivery, a duplicate event id and a duplicate `(lead, generation)` each produce at most one effect and a handler error leaves no ledger row (tests).
- **AC-2.4-8** `CRM_CONVERSION_CLEANUP_MODE` defaults to `log_only` in both `Settings` and compose, an unknown value falls back to `log_only` with an error log, and `.env.example` does not name it (tests).
- **AC-2.4-9** Deal folds its consumer exactly as customer does, `deal-worker` exists behind `split-workers`, `PROFILE_DLQ_QUEUES["crm"]` is `("customer.events", "deal.events")`, and the infra sync tests pass (tests).
- **AC-2.4-10** `memory_budget_totals.py` prints 2068 for `crm`, 3748 for the ten profiles and 3945 for the ten plus `admin`; the RUNBOOK contains `EDGE_BUDGET_ACCEPTED_MB=3748`; `test_planned_headroom.py` passes.
- **AC-2.4-11** An event for organization A never changes organization B's rows, in either service (cross-tenant tests).
- **AC-2.4-12** Cleanup and adoption serialise on the company row in both orders, and a later attempt's adoption is never undone (tests).
- **AC-2.4-13** The diff contains `libs/platform-core/src/platform_core/ops_monitor/config.py`, and the rebuild-decision test shows `needs_full_rebuild` true for it (so the rollout states a full rebuild).
- **AC-2.4-14** The D-3 count is recorded in `docs/crm.md` and register row 122; either `tools/sweep_aborted_conversions.py` exists with the tests of §7.7 (count non-zero) or the file does not exist and the recorded count is zero for every `owner_changed_aborts` and `deleted_record_bumps`.

## 8. R2.5 — The stage authoring API and `crm.pipeline.stage.write`

### 8.1 Purpose

Let an ORG-scope admin add, rename, reorder and archive pipeline stages through four routes, behind one new permission
(`crm.pipeline.stage.write`, default ORG, route floor ORG; pending D-6), with every invariant the stage tables cannot hold
by themselves (§6.2) enforced under one exclusive lock. It is the first release in which an organization can leave the
virtual default, so it also carries the notification rule that stops an archive-driven mass move from firing tenant
automations, **and the one SPA change that lets the published bundle survive a stage it has never heard of** (§8.6, DD-54).
No stage-editing screen ships (the settings hub and stage editor are SP3's, R3.4): the routes are the API those screens
will call, and the owner exercises them on a designated tenant first, which is possible only because the reconcile can be
aimed at that one tenant (§8.3, §8.8).

### 8.2 Data model

No table, column, migration or grant change: R2.3's `pipelines` and `pipeline_stages` are the storage. The first write in an
organization materialises the default pipeline (R2.3 §6.4.9) under the exclusive lock. Positions are dense `0..n-1` among
**active** stages after every create, archive and reorder (one `UPDATE ... row_number()` in the same transaction); an
archived stage keeps the position it had and is excluded from the density rule.

### 8.3 Backend contract

**Permission and catalogue.** `deal/app/catalogue.py` gains
`PermissionSpec("pipeline", "stage", "write", "Create, edit, reorder and archive pipeline stages", "ORG", "ORG")` (registry `E18`:
default ORG, max ORG, not dangerous). `CRM_PIPELINE_ADMIN` (`CRM_PIPELINE.all("ORG")`) takes it automatically; `CRM_DEAL_REP`
is an explicit `pick` and does not. `make openapi` regenerates `libs/contracts/permissions/crm.json` (18 codes) and
`libs/contracts/openapi/deal.json` (four operations). `GET /deals/stages` stays under `crm.pipeline.deal.read`.

**The reconcile, aimed at one tenant (DD-54, registry `H6`).** `make edge-reconcile` takes an optional `ORG=<uuid>`:
`remote.sh reconcile-permissions <product[,product…]> [--apply] [--org <uuid>]` passes the tool's existing `--org <uuid>`
(`tools/reconcile_permissions.py:59,328`) in place of the `--all` the wrapper hard-wires today (`infrastructure/edge/remote.sh:456`),
and the Make target forwards it (`Makefile:584-587`). A value that is not a uuid is refused before any container starts; without
`ORG` the call is exactly today's. This is what lets the owner give one designated tenant the new code, exercise it there and only
then reconcile the fleet (§8.8).

**Routes** (all four: `Entitled` = `require_product("crm")`, `require("crm.pipeline.stage.write", minimum=Scope.ORG)`, the
exclusive pipeline lock as the first statement, `SET LOCAL lock_timeout = '5s'` and `SET LOCAL statement_timeout = '30s'`,
declared before `/deals/{deal_id}`):

| Route (registry) | Body | Success | Audit and events |
|---|---|---|---|
| `POST /deals/stages` (C2) | `StageIn {label: str 1-80, outcome: "open"\|"won"\|"lost", default_probability: int 0-100 \| null, tone: Tone \| null, after_stage_id: uuid \| null}`, `extra = forbid` (no `key`) | 201 `StageOut` | `pipeline.stage_created`; `crm.pipeline.stage.created` |
| `PATCH /deals/stages/{stage_id}` (C3) | `StagePatch {label?, tone?, default_probability?}`, `extra = forbid`, at least one field, no explicit null | 200 `StageOut` | `pipeline.stage_updated` (before and after of the changed fields); `crm.pipeline.stage.updated` |
| `POST /deals/stages:reorder` (C4) | `StageReorderIn {stage_ids: uuid[] (1-100)}` | 200 `StageListOut` | `pipeline.stage_reordered`; `crm.pipeline.stage.reordered` |
| `POST /deals/stages/{stage_id}:archive` (C5) | `StageArchiveIn {move_open_deals_to: uuid \| null}` | 200 `StageArchiveOut {stage: StageOut, moved_deals: int}` | `pipeline.stage_archived`; `crm.pipeline.stage.archived`; and, per moved deal, `deal.stage_changed` plus `crm.pipeline.deal.stage_changed` with `reason = "stage_archived"` |

`Tone` is `neutral\|info\|success\|warning\|danger` (`frontend/opsgrids-ui/src/lib/status.ts:19`); an omitted tone is `info` for
`open`, `success` for `won`, `danger` for `lost`. The archive route is idempotent: archiving an archived stage answers 200 with the
stage and writes no audit row and no event.

**Errors** (RFC 7807, `errors[]` entries `{field, code, message}`; the organization of a stage id is checked by RLS plus the
organization filter, so another tenant's id is a 404):

| Status | `type` | `code` | When |
|---|---|---|---|
| 401, 403, 402 | `unauthorized`, `forbidden`, `product-not-entitled` | — | no token; missing permission or a grant below ORG; no `crm` entitlement |
| 404 | `not-found` | `stage_not_found` | unknown, other-organization or deleted stage id |
| 409 | `conflict` | `duplicate_label` | another active stage has that label (case-insensitive) |
| 409 | `conflict` | `stage_archived` | PATCH of an archived stage |
| 409 | `conflict` | `last_stage_of_outcome` | the archive would leave no active open, no active won or no active lost stage |
| 409 | `conflict` | `too_many_deals` | more than 2,000 live deals would move (DD-25); the admin moves them first |
| 409 | `conflict` | `pipeline_busy` | `lock_timeout` while waiting for the pipeline lock; `Retry-After: 2` |
| 422 | `validation` | `label_invalid` | empty after trimming, over 80 characters, or a control character |
| 422 | `validation` | `label_collides_with_key` | the label, lower-cased or slugified, equals a *different* stage's key (DD-45) |
| 422 | `validation` | `probability_pinned` | a probability other than 100 on `won` or 0 on `lost` |
| 422 | `validation` | `probability_required` | `default_probability` missing on an `open` stage |
| 422 | `validation` | `after_stage_invalid` | `after_stage_id` unknown or archived, or the placement would put a closed stage before an open one |
| 422 | `validation` | `stage_set_mismatch` | reorder list is not exactly the active stage ids, once each |
| 422 | `validation` | `closed_stage_before_open` | reorder list puts a `won` or `lost` stage before an `open` one |
| 422 | `validation` | `move_target_required` | archiving an open stage that holds any deal (soft-deleted ones count) with no `move_open_deals_to` |
| 422 | `validation` | `invalid_move_target` | the target is not an active open stage, or is the archived stage itself |
| 422 | `validation` | `move_target_not_allowed` | a target given for a `won` or `lost` archive |

**Events** (producer `deal`; ids, keys, labels and enumerations only; configuration events, not subscribable by tenant rules;
each with its committed schema, `additionalProperties: false`, and a producer-side test through the real write path):

| Event | Schema file | Payload |
|---|---|---|
| `crm.pipeline.stage.created` | `libs/contracts/events/crm.pipeline.stage.created/v1.schema.json` | `organization_id, pipeline_id, stage_id, key, label, outcome, position, archived` |
| `crm.pipeline.stage.updated` | `.../crm.pipeline.stage.updated/v1.schema.json` | the same plus **required** `changed[]` (a subset of `label`, `tone`, `default_probability`) |
| `crm.pipeline.stage.archived` | `.../crm.pipeline.stage.archived/v1.schema.json` | the same as `created` (`archived: true`) |
| `crm.pipeline.stage.reordered` | `.../crm.pipeline.stage.reordered/v1.schema.json` | `organization_id, pipeline_id, stage_ids[]` (the new full order of active stages) |

Registry `EV14` carries `changed[]` on `.updated` alone: a created or archived stage has nothing that changed, so only
`.updated` carries it. `crm.pipeline.deal.stage_changed` gains the optional `reason` (`"stage_archived"`, a one-value enum that
later releases may widen) in the same commit that first emits it: its committed schema changes with the producer (EV8).

**Notification.** `services/notification/app/consumers.py` `fold()`: a `crm.pipeline.deal.stage_changed` envelope whose
`payload.reason` equals `stage_archived` skips `apply_rules` and `start_runs`; the ledger row (`mark_processed`) is still
written, in the same transaction, so a redelivery stays idempotent (DD-27). Every other event, and the same event without that
reason or with any other reason, is untouched.

### 8.4 Domain rules

Every stage-configuration write: (1) opens with the exclusive pipeline lock and the two `SET LOCAL` timeouts; (2) materialises the
default pipeline if the organization has none (once, under that lock); (3) loads the spec, builds the *proposed* stage set in
memory, runs `pipeline.validate` (R2.3) on it and only then writes. A refusal at step 3 writes nothing.

1. **Key generation** (DD-24, server-side, never client-supplied). `slugify(label)`: NFKD-normalise, drop non-ASCII, lower-case,
   collapse runs of anything outside `[a-z0-9]` to `_`, trim `_`, cut to 20 characters, and use `stage` when nothing is left.
   If the key is in the pipeline's key set (**archived stages included**) or is one of the six legacy keys, append `_2`, and
   `_3`, `_4` in turn while the result is also taken, cutting the base so the whole key stays within 20 characters. A key is
   never reused, which is what makes it a safe identifier for events, audit rows and tenant automations.
2. **Labels.** Trimmed, internal whitespace collapsed to single spaces; unique among **active** stages case-insensitively (the
   partial unique index is the backstop; the service answers 409 `duplicate_label` first); never equal, lower-cased or slugified,
   to a *different* stage's key (DD-45), so an importer that accepts a label or a key cannot meet an ambiguity. An archived
   stage's label may be reused by a new stage.
3. **Create.** `won` forces probability 100 and `lost` 0 (a supplied different figure is a 422, DD-15's posture); `open`
   requires one. Placement: without `after_stage_id` an `open` stage goes after the last open stage and a closed stage at the
   very end; with it, the new stage goes immediately after that active stage, and a placement that would put a closed stage
   before an open one is a 422 `after_stage_invalid`. The legacy keys' stages are ordinary stages once materialised.
4. **Patch.** Label, tone and, for an `open` stage only, `default_probability`. `key`, `outcome`, `position` and `archived` are not
   patchable (`extra = forbid` answers 422); an archived stage is read-only (409). A default-probability change affects only
   *future* moves into the stage; no existing deal's probability changes.
5. **Reorder.** The list must be exactly the active stage ids; every `open` stage before every `won` or `lost` stage.
6. **Archive of an `open` stage.** If the stage holds any deal, **live or soft-deleted** (the route counts both itself; the
   `deal_count` of `GET /deals/stages` counts live deals only and only for an ORG reader, DD-53, so a dialog told `0` can still be
   answered `move_target_required` when only soft-deleted deals remain, and the stage editor maps that onto its target field like any
   other field error), a target is required: an active `open` stage other than itself. The **live** deals move there in batches of 200 rows per
   statement inside the same transaction, **keeping each deal's own probability** (DD-25), each with its own `deal.stage_changed`
   audit row (`acting_as = 'pipeline-stage-archive'`, `reason = 'stage_archived'`) and its own outbox event carrying
   `reason = "stage_archived"`; more than 2,000 live deals is 409 `too_many_deals`. Soft-deleted deals stay where they are and keep
   rendering through the archived stage's label. Deals never move into a closed outcome by an archive.
7. **Archive of a `won` or `lost` stage.** Allowed only while another active stage of the same outcome remains; **no deal is touched**
   (closed deals are never moved by an archive and stay in the archived stage, which they keep reading through); a target is refused.
8. **Archiving never deletes and cannot be undone through the API.** There is no `unarchive` route and no delete route; to bring a
   stage back an admin creates a new one (the archived label is reusable), and the old key stays reserved for ever (§15).
9. **Locks.** Writers that bind a deal to a stage hold the shared lock; `lock_pipeline_shared` now runs `SET LOCAL lock_timeout =
   '5s'` immediately before the lock request and `RESET lock_timeout` right after it is granted, so only the pipeline-lock wait
   can time out, and that wait is 409 `pipeline_busy` with `Retry-After: 2` rather than a hung request. No transaction takes the
   shared lock and then the exclusive one (a lock upgrade can deadlock); an AST test asserts no function calls both.
10. **Concurrency outcomes** (all proven by two-session tests): two archives that together would remove the last two open stages
    leave one and answer the second 409; a deal move racing the archive of its target either lands first and is moved on by the
    archive, or loses and gets 422 `stage_archived`; two creates with one label produce one stage and one 409; the first write in an
    organization materialises exactly once.

### 8.5 Consumers and idempotency

- **notification** (changed here): the skip above; idempotent through the unchanged ledger.
- **insights** (unmerged, registry `L10`): would take stage labels from `crm.pipeline.stage.*`; nothing consumes them in this release.
- **Route retries.** `PATCH`, `:reorder` and `:archive` are idempotent by construction. `POST /deals/stages` is not idempotent by
  itself, but a repeated submission meets 409 `duplicate_label`, which a client treats as "already created". No idempotency-key
  header is introduced.

### 8.6 UI

**One small SPA change ships, and it is published before anything else in this release (DD-54, registry `G26`).** The stage routes are the first
way for an organization to hold a deal in a stage outside the legacy six, and the published `product-crm` bundle cannot render
one. Nothing in the SPA *calls* the new routes, but it *reads every deal*, so the first custom stage on a tenant breaks that
tenant's screens: `dealsByStage` groups deals into a record keyed by the six legacy keys and does
`groups[deal.stage].push(deal)` (`frontend/product-crm/src/data/derive.ts:70`), which throws a `TypeError` on any other key, and
`routes/Deals.tsx:175` calls it whichever view is showing, so the Deals screen blanks for every user of the tenant;
`stageMeta` falls back to the first stage, so a label would read "Lead" (`data/stages.ts:45-46`); `StagePill` indexes a six-key
tone record (`ui/StatusPill.tsx:61-72`) and passes an undefined tone; and the board, the deal detail's stage select
(`routes/DealDetail.tsx:216`) and the CSV export (`data/importExport/deals.ts:467`) draw their stage lists from the fixed `STAGES`.
Only the fleet reconcile is the owner's to time; R2.8's reconcile (`CRM_ADMIN` carries every code) and a new activation (the
manifest is a bind mount) reach the same routes, so the bundle, not the owner's care, has to be made safe. The hardening is the
smallest change that does it. SP3 replaces it with the stage-driven board (R3.4, whose rule "an unknown stage key renders under
Other" this one matches) and deletes it. In `frontend/product-crm`, each item a vitest case (§8.7):

1. **Read the server's words.** The mapper (`api/mappers.ts`) carries `stage_label`, `stage_outcome` and `stage_position` from
   `DealOut` (R2.3) onto `Deal`, and `Deal.stage` is a `string`. When the fields are absent (an older deal image) they are
   derived from the legacy table, so one bundle works against either image. The six-key `DealStage` type stays where only a
   legacy key is ever named (the create form's options, the importer, which keeps resolving the legacy six until R3.4, §6.5).
2. **`dealsByStage` never throws.** A key it did not build goes into an `other` group; the legacy six group exactly as before.
3. **The board.** The six legacy columns render as today; when `other` is not empty, one trailing "Other stages" column renders
   after them. A card there shows its `stage_label`, and its stage select lists the deal's own stage as a disabled current
   option. The column is a drop source and never a drop target; a card whose `stage_outcome` is `won` or `lost` is not draggable
   and its select is disabled (the server refuses a closed deal's move with a 409). It reuses the column's existing classes and
   tokens, so the token baseline does not grow.
4. **The pill.** `StagePill` receives the deal's `stage`, `stage_label` and `stage_outcome`. A legacy key keeps today's label and
   tone. Any other key shows `stage_label` (the key itself when the label is absent, never the first stage's name) on the tone
   the server gives an omitted tone (§8.3): `info` for `open`, `success` for `won`, `danger` for `lost`. Nothing indexes a fixed
   record by an unknown key.
5. **Open and won mean the outcome.** `isOpen`, `openDeals` and the "won this month" count (`routes/Overview.tsx:188`) read
   `stage_outcome` when present, so a deal in a custom won stage is neither "open" nor missing from the won figure; an unknown
   stage with no outcome is open, as the server's `outcome_of` says (§6.4.7).
6. **Detail and export.** The deal detail's "Move stage" select lists the deal's own stage (disabled) when it is not a legacy key,
   and the CSV export writes `stage_label` for a stage the fixed option list does not know.
7. **Fixtures.** `frontend/opsgrids-visual/fixtures/api/crm/deals.json`, `deals-list.json` and `summary.json` gain a deal in a
   custom open stage, one in a custom won stage and a funnel row for the custom open stage (`contracts.test.ts` validates them
   against the response schemas R2.3 widened); `make ui-visual` shows the reviewed baseline change.

The settings hub and the stage editor (reorder control: Up and Down through `RowActions`, registry `G3`) remain SP3's (R3.4);
they must carry the same `minimum` as the route in `useCan` and the new code in the visual fixtures' grants
(`frontend/opsgrids-visual/fixtures/**/grants.json`) when they ship (registry §E.4 step 5). Nothing in the SPA calls the four
routes, so no `MockRoute` is needed.

### 8.7 Tests

All in deal's process unless named.

**`services/deal/tests/test_stage_routes.py` (new, HTTP).** For each of the four routes the negative-authz set, parametrised:
`test_no_token_is_401`, `test_a_principal_without_stage_write_is_403` (holding `crm.pipeline.deal.write:ORG`),
`test_stage_write_at_own_scope_is_403_below_the_floor`, `test_a_non_entitled_organization_is_402`,
`test_another_organizations_stage_id_is_404` and `test_a_write_in_organization_a_never_changes_organization_b`; then the behaviour:
`test_create_generates_the_key_server_side_and_refuses_a_client_key`, `test_create_places_open_and_closed_stages_correctly`,
`test_create_refuses_a_closed_stage_before_an_open_one`, `test_won_and_lost_pin_their_probability_and_a_wrong_figure_is_422`,
`test_an_open_stage_without_a_probability_is_422`, `test_duplicate_active_label_is_409_case_insensitively`,
`test_an_archived_label_can_be_reused`, `test_a_label_equal_to_another_stages_key_is_422`, `test_patch_changes_label_tone_and_open_probability_only`,
`test_patch_of_key_or_outcome_is_422`, `test_patch_of_an_archived_stage_is_409`, `test_patch_probability_on_a_closed_stage_is_422`,
`test_reorder_requires_exactly_the_active_set`, `test_reorder_keeps_closed_stages_last`, `test_reorder_renumbers_densely`,
`test_archive_an_empty_open_stage_needs_no_target`, `test_archive_with_deals_requires_a_target_even_if_only_soft_deleted_ones_remain`,
`test_archive_moves_live_deals_keeping_probability_and_writing_one_audit_row_and_one_event_each`,
`test_archive_moves_carry_reason_stage_archived_in_the_event_and_no_activity_is_written`,
`test_archive_refuses_an_invalid_target`, `test_archive_over_2000_deals_is_409_and_moves_nothing`,
`test_archiving_the_last_open_won_or_lost_stage_is_409`, `test_archiving_a_won_stage_moves_no_deal`,
`test_archive_is_idempotent_and_writes_nothing_the_second_time`, `test_the_first_write_materialises_the_default_exactly_once`,
`test_stage_keys_are_never_reused_even_after_archive`, `test_the_six_legacy_keys_are_reserved`,
`test_get_stages_reflects_every_write`, `test_a_refused_write_changes_nothing` (row counts, audit and outbox before and after).

**`services/deal/tests/test_stage_concurrency.py` (new, two sessions).** `test_two_archives_of_the_last_two_open_stages_leave_one`;
`test_a_deal_move_racing_an_archive_of_its_target_never_lands_in_an_archived_stage` (both interleavings);
`test_two_creates_with_one_label_make_one_stage`; `test_a_move_waits_for_a_configuration_write_and_times_out_as_pipeline_busy`;
`test_a_configuration_write_times_out_as_pipeline_busy_behind_a_long_move`; `test_no_function_takes_the_shared_and_then_the_exclusive_lock`
(AST).

**`services/deal/tests/test_stage_invariants.py` (new, property style).** `test_random_operation_sequences_keep_every_invariant`:
a seeded `random.Random(20261007)` runs 500 create, patch, reorder and archive operations (some invalid) against a real
organization; after every operation it reloads the persisted rows and asserts `pipeline.validate` passes, keys are unique and
never reused, positions are dense among active stages, closed stages are last, and no live deal sits in an archived `open` stage.

**`services/deal/tests/test_stage_events_contract.py` (new).** Builds each of the four events and the archive-driven
`stage_changed` through the real routes and validates them with `validate_event_schema`; `test_the_updated_event_carries_changed_and_the_others_do_not`;
`test_stage_changed_reason_is_absent_on_an_ordinary_move_and_present_on_an_archive_move`.

**`services/deal/tests/test_catalogue.py` (edited on purpose):** the admin template holds the new code at ORG; `CRM_DEAL_REP` does
not; the catalogue has 6 codes.

**`services/notification/tests/test_stage_archived_skip.py` (new).** `test_a_stage_changed_event_with_reason_stage_archived_starts_no_rule_and_no_run`
(no staged message, no run, the ledger row written); `test_the_same_event_without_a_reason_behaves_as_before`;
`test_another_reason_value_is_not_skipped`; `test_a_redelivered_archive_move_is_dropped_by_the_ledger`;
`test_another_event_type_carrying_a_reason_is_unaffected`.

**Contracts and gates.** `libs/contracts/tests/test_event_schemas.py` gains
`test_the_four_stage_events_have_committed_closed_schemas` (static: `additionalProperties: false`, required lists as the table
above, `changed` only on `updated`) and extends the stage-changed assertion with the optional one-value `reason`. `make openapi`
and `make openapi-check`; `python tools/export_permissions.py --check` (18 codes); `make gate`.

**The reconcile wrapper (`tests/infra/test_remote_reconcile_org.py`, new, static).** The real `reconcile_permissions` function
sliced out of `remote.sh`, run with a fake `docker` first on `PATH` that records its arguments (the technique of
`tests/infra/test_remote_state_dir.py`): `test_org_passes_org_and_not_all`, `test_without_org_the_call_is_exactly_todays`
(`--all`, `--dry-run` and `--apply` as before), `test_a_non_uuid_org_is_refused_before_any_container_runs`,
`test_org_is_passed_to_every_product_of_a_list`, `test_the_make_target_forwards_org` (`make -n edge-reconcile ... ORG=<uuid>`).

**The SPA (`frontend/product-crm`, vitest)**, one case per surface of §8.6, each putting a custom stage key through it:
`data/derive.test.ts` (new): `test_deals_by_stage_puts_a_stage_outside_the_six_in_other_and_never_throws`,
`test_the_legacy_six_group_exactly_as_before`, `test_open_deals_and_won_count_follow_the_outcome_not_the_key`;
`ui/StatusPill.test.tsx` (new): `test_a_legacy_stage_keeps_its_label_and_tone`,
`test_a_custom_stage_shows_its_label_on_the_outcome_tone` (open info, won success, lost danger),
`test_a_missing_label_falls_back_to_the_key_never_to_lead`; `ui/KanbanBoard.test.tsx` (new):
`test_the_other_stages_column_renders_only_when_needed`, `test_a_custom_closed_deal_is_not_draggable_and_its_select_is_disabled`,
`test_a_custom_open_deal_can_be_moved_into_a_legacy_column`, `test_the_other_stages_column_is_not_a_drop_target`;
`routes/Deals.stages.test.tsx` (new): the board and the list both render a custom open, a custom won and a custom lost deal
without throwing, and the Overview funnel renders a custom `funnel[].stage`; `routes/DealDetail` renders the pill and the
stage select, and `CompanyDetail` the pill and its open-value total, for a custom stage; `data/importExport/deals.test.ts` (extended): the export writes `stage_label`;
`api/mappers.test.ts` (extended): the new fields map, and absent ones derive the legacy values;
`frontend/opsgrids-visual/fixtures/contracts.test.ts` with the three updated fixtures; `make ui-visual` with the reviewed baseline.

### 8.8 Rollout

1. Review (CODEOWNERS applies to `libs/contracts/`); owner squash-merge; `make gate` locally.
2. **Owner: publish the hardened SPA first** (DD-54). Check out the merged `main` (the bundle is built from the local checkout, not
   the deployed ref), run `make edge-web HOST=… CONFIRM=… APP=crm`, then grep the published bundle for the new board column's
   accessible name ("Other stages") to prove the right tree was built. It is safe before anything else in this release: it reads
   fields R2.3's deal image already emits and, where they are absent, derives them from the legacy six.
3. Owner-named `make deploy HOST=… CONFIRM=… REF=<sha>`: `libs/contracts` is excluded from the full-rebuild rule, so the `deal` and
   `notification` images rebuild and nothing else (`remote.sh` and the Makefile are not images). No migration. The registry's "filter
   before the route" rule (the consumer-first rule, registry §D) is met by the **reconcile** below, not by the order of two image
   recreations: until the reconcile, no existing tenant's user holds `crm.pipeline.stage.write`, so the four routes answer 403 to
   all of them, while a brand-new activation that reaches the new code through the manifest's bind mount has no tenant automations
   to fire (DD-50) and, with the SPA already hardened, no screen to break.
4. Verify (owner-run reads): `make edge-status`; the deal and notification logs are clean.
5. **Reconcile, one tenant first, owner-run** (registry §E.4 step 4): `make edge-reconcile HOST=… CONFIRM=… PRODUCTS=crm
   ORG=<designated tenant uuid>` prints that tenant's dry run only; the owner reads it (expected: `grants_added` of one code on
   `CRM_PIPELINE_ADMIN`, a `pv` bump, no `roles_created`), then repeats it with `APPLY=1`. Among existing tenants only that one now
   holds the code (an organization that activates CRM from now on gets it with its roles, step 3).
   (`ORG=` is forwarded by the Makefile in the owner's checkout of the merged `main`, the one used for step 2, and handled by
   `remote.sh` on the box, which step 3's deploy brought up to date.)
6. Smoke (owner-run, **a write**, on that tenant): `GET /deals/stages` (virtual), `POST /deals/stages` with a label
   (materialises), `PATCH` it, `:reorder`, `:archive` it; read the audit rows and the outbox; confirm no WhatsApp or email
   automation fired for the archive's moved deals (use a tenant with a `stage_changed` rule and an archive that moves a test deal).
   Then, **as a user of that tenant, open the Deals screen** (board, list, a deal's detail, the CSV export) with a deal in the
   custom stage: it renders by its label and nothing throws.
7. **Reconcile, the fleet, owner-run, only after step 6 passed**: `make edge-reconcile HOST=… CONFIRM=… PRODUCTS=crm` prints the dry
   run (the designated tenant shows an empty diff; every other organization shows `grants_added` of one code on `CRM_PIPELINE_ADMIN`
   and a `pv` bump, no `roles_created`), then `APPLY=1`, off-peak, paced. The reconcile bumps `pv` and refreshes tokens
   (`tools/reconcile_permissions.py:200-244`).
8. Memory: no container changes; `memory_budget_totals.py` is unchanged from R2.4's figures.
9. Docs: `docs/crm.md` (stage routes, the permission, the events), edited **in the same commit** as the registry rows that add
   them (`crm.pipeline.stage.*`, `EV14`; `crm.pipeline.stage.write`, `E18`), because `tests/infra/test_crm_doc_matches_contracts.py`
   (SP1, R1.2b) fails any release that adds, renames or removes a CRM event type, a permission code, a role template or a
   gateway prefix without it; `docs/api-reference.md`'s deal section if it lists routes,
   `infrastructure/edge/RUNBOOK.md` (the `ORG=` option on `make edge-reconcile`, and its row in the command table).

### 8.9 Rollback

Redeploy the previous ref (`make edge-rollback`); the deal image reverts to R2.3's, which still *reads* the tables, so organizations
that customised their stages keep working and simply cannot edit them. The permission stays in the manifest and in the reconciled
roles (the platform has no way to remove a code; an inert code is harmless). **Do not roll back below R2.3 while any organization has
a custom stage or a deal in one**: the R2.3 downgrade guard refuses it for exactly that reason (§6.2). **The SPA is the same
one-way door:** `make edge-web-rollback HOST=… CONFIRM=… APP=crm` restores the previous bundle, which cannot render a stage outside
the legacy six, so **do not roll the SPA back below this release's bundle while any organization holds one**. The owner's
read-only check (counts only, the `psql` pattern of §7.4): `SELECT organization_id, count(*) FROM deal.pipeline_stages WHERE key
NOT IN ('lead', 'qualified', 'proposal', 'negotiation', 'closed_won', 'closed_lost') GROUP BY 1`, and the same filter on
`deal.deals.stage`. Stop conditions: any 5xx on the four routes; any `pipeline_busy` that persists after the transaction that caused
it has ended; a tenant automation firing for an archive-driven move; a deal whose `stage` is an archived `open` stage; a Deals
screen that fails to render for a tenant holding a custom stage (a defect in the hardening: fix forward, and **pause the fleet
reconcile** until it is fixed).

### 8.10 Acceptance

- **AC-2.5-1** The four routes exist exactly as §8.3 states, each with `require("crm.pipeline.stage.write", minimum=Scope.ORG)` and `Entitled`, declared before `/deals/{deal_id}` (tests).
- **AC-2.5-2** Each route answers 401, 403 (missing permission), 403 (OWN grant below the floor), 402 and 404 for another tenant's stage id, and a write in organization A never changes organization B (tests).
- **AC-2.5-3** Keys are generated server-side per DD-24, never reused, the six legacy keys are reserved, and a client-supplied key is refused (tests).
- **AC-2.5-4** Labels are unique among active stages case-insensitively and never equal another stage's key (tests; the database index as backstop).
- **AC-2.5-5** After every operation of the 500-step random sequence all invariants hold (property test).
- **AC-2.5-6** Archive moves keep each deal's probability, write one audit row and one event per deal, never touch closed deals, and are refused over 2,000 deals (tests).
- **AC-2.5-7** The concurrency outcomes of §8.4.10 hold in both interleavings (two-session tests), and `pipeline_busy` is produced rather than a hang.
- **AC-2.5-8** The four stage events and the archive-driven `stage_changed` validate against committed schemas built through the real routes; `changed` is on `.updated` only (tests).
- **AC-2.5-9** Notification skips `reason = stage_archived` before `apply_rules` and `start_runs`, still writes the ledger row, and leaves every other event untouched (tests).
- **AC-2.5-10** The catalogue and manifest carry `crm.pipeline.stage.write` at default and max ORG in `CRM_PIPELINE_ADMIN` only; `export_permissions.py --check` passes with 18 codes; `docs/crm.md` carries the four stage events and the code, edited in the same commit, and `tests/infra/test_crm_doc_matches_contracts.py` passes.
- **AC-2.5-11** No file under `services/*/migrations/versions` is added; `make openapi-check` and `make contracts-check` pass.
- **AC-2.5-12** No function calls both lock helpers (AST test), and `lock_pipeline_shared` sets its `lock_timeout` (test).
- **AC-2.5-13** A deal in a stage outside the legacy six (open, won and lost) renders without throwing in the board, the list, the pill, the deal and company detail, the export and the Overview funnel, labelled by `stage_label` on the outcome's tone, and the legacy six render as before (vitest, one case per surface; `make ui-visual` baseline reviewed).
- **AC-2.5-14** `make edge-reconcile` accepts `ORG=<uuid>`: the wrapper then passes `--org <uuid>` and not `--all`, refuses a non-uuid before any container runs, and without `ORG` the call is exactly today's (tests with a fake `docker`).
- **AC-2.5-15** The plan's R2.5 rollout lists, in this order: SPA publish with the bundle grep, deploy, reconcile on the designated tenant (dry run, apply), smoke including the Deals screen, reconcile of the fleet (dry run, apply); and its rollback forbids rolling the SPA back while a custom stage exists (the plan's task list; owner-recorded).

## 9. R2.6 — C15 expand: the roster knows memberships, and the mapping tool exists

### 9.1 Purpose

Prepare, **without changing who can see or edit anything**, the move of CRM's "who is the rep" from a roster id (or an
employee id) to the **membership id** (the programme's C15, ADR-0008 Q5, decision D-7). R2.6 adds the data the flip needs and the
tool that fills it, and makes every roster lookup understand both kinds of id, so the data step and the code step of R2.7 can
happen at any time apart without breaking a screen:

1. `customer.owners.membership_id` and the reversible mapping journal in each of customer, deal and lead (§9.2).
2. A bulk read of an organization's memberships in `account`, the only place that knows who the members are (§9.3).
3. Team activation: a roster row can be created **from a member** instead of typed by hand, and a candidates list shows who is
   not yet a rep (registry C25-C27).
4. Dual resolution at every roster lookup (DD-28): a reference is a membership id **or** the legacy roster id.
5. `tools/map_crm_owners.py`: `census`, `plan`, `apply`, `verify`, `revert`, built and tested here, **run by the owner** (§9.4).
6. The SPA resolves owner names by membership id first, then roster id, and offers the membership id when an owner has one
   (§9.6), so a rewritten row never renders as a blank owner.

R2.6 deliberately leaves `ScopeContext`, every OWN comparison and every default owner on the employee link (R2.7 flips them).
Nothing is rewritten until the owner runs `apply`.

### 9.2 Data model

**Customer, revision `b6d20f94e1a7`** (`down_revision = "e230c025337e"`; verified unused across the repo at `HEAD`).
It opens with `SET LOCAL lock_timeout = '5s'`:

```sql
ALTER TABLE customer.owners ADD COLUMN membership_id uuid;                       -- no foreign key: account is another schema
CREATE UNIQUE INDEX uq_owners_org_membership ON customer.owners (organization_id, membership_id)
    WHERE membership_id IS NOT NULL;

CREATE TABLE customer.owner_rekey_journal (                                      -- the same shape in customer, deal and lead
    id              uuid        NOT NULL,
    organization_id uuid        NOT NULL,
    run_id          uuid        NOT NULL,
    table_name      varchar(40) NOT NULL,
    row_id          uuid        NOT NULL,
    old_owner_id    uuid        NOT NULL,
    new_owner_id    uuid        NOT NULL,
    evidence        varchar(12) NOT NULL,
    applied_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_owner_rekey_journal PRIMARY KEY (id),
    CONSTRAINT ck_owner_rekey_journal_evidence CHECK (evidence IN ('link', 'email', 'manual', 'revert')),
    CONSTRAINT ck_owner_rekey_journal_distinct CHECK (old_owner_id <> new_owner_id),
    CONSTRAINT ck_owner_rekey_journal_table CHECK (table_name IN ('owners', 'companies', 'contacts'))   -- deal: ('deals'); lead: ('leads', 'assignment_rules')
);
CREATE INDEX ix_customer_owner_rekey_journal_organization_id ON customer.owner_rekey_journal (organization_id);
CREATE INDEX ix_owner_rekey_journal_org_run ON customer.owner_rekey_journal (organization_id, run_id);

CREATE FUNCTION customer.forbid_journal_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'owner_rekey_journal is append-only: % is not permitted', TG_OP; END $$;
CREATE TRIGGER trg_owner_rekey_journal_append_only BEFORE UPDATE OR DELETE ON customer.owner_rekey_journal
    FOR EACH ROW EXECUTE FUNCTION customer.forbid_journal_mutation();

ALTER TABLE customer.owner_rekey_journal ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer.owner_rekey_journal FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON customer.owner_rekey_journal FOR ALL TO opsgrids_customer
    USING (organization_id = customer.current_org()) WITH CHECK (organization_id = customer.current_org());
```

**Deal, revision `d8a35c17e920`** and **lead, revision `a47e3b9d1c68`** create the same journal table in their own schema (own
function, trigger, policy for `opsgrids_deal` and `opsgrids_lead`, own `table_name` CHECK) and nothing else. Deal's
`down_revision` is `f3b8d1a65c02`, always: **R2.3 is a hard prerequisite of this revision** (§6.2, DD-51), because a revision
parented on `e94d7a2c5f18` beside the byte-pinned `a1c9e2f7b3d4` would give deal two heads, which the second merger could only fix
by editing the pinned file. The revision carries the registry's `# RE-PARENT HERE` marker for a later sub-project's deal
revision that merged first, and may only move forward from `f3b8d1a65c02`; lead's `down_revision` is `91dd3ca663fd`.

Rules that are part of the contract:

- The journal is **append-only by trigger** and carries ids only, no PII (registry B2). The purge tool lifts its delete trigger
  by name inside each organisation's own transaction (ADR-0036 decision 6), so test-organisation purge needs no change.
- One journal row per changed id: for an array column (`assignment_rules.owner_ids`) one row per changed element, with
  `row_id` the rule's id. For `owners` the row records the activation: `old_owner_id` is the roster row's own id, `new_owner_id`
  the membership id set.
- `evidence` is the proof class the plan offered (`link`, `email`) or a hand entry (`manual`); `revert` is written only by
  `revert`, as a **counter-journal row**, never by updating the original (DD-29).
- Every query the tool and the activation path run on these tables carries `organization_id = :org` as well as the bound tenant GUC (the
  application-level filter beside RLS; a test asserts it for each statement).
- Every table is added to its service's test `TENANT_TABLES` (the truncate list): `services/customer/tests/conftest.py:43-50`,
  `services/deal/tests/conftest.py:45-51`, `services/lead/tests/conftest.py:43-49`.
- Each revision is expand-only and old code ignores it. **Guarded downgrades** (`OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE`): a journal
  that holds any row, or customer's `owners.membership_id` set on any row, refuses to drop unless the override is set, because
  the journal is the only reversible record of a mapping run.

`owners.id` is **never rekeyed** and `owners.membership_id` is never edited after it is set (§9.4 rule 3). No other column
changes. The owner-id **columns keep their names**: `companies.owner_id`, `contacts.owner_id`, `deals.owner_id`,
`deal.activities.owner_id`, `leads.owner_id` and `assignment_rules.owner_ids` simply hold membership ids after `apply`
(registry B3).

### 9.3 Backend contract

**Account (registry C57), `GET /internal/v1/organizations/{organization_id}/memberships`.**

| Part | Contract |
|---|---|
| Auth | `require_service_token`; caller allow-list **`customer` only in this release** (registry `C57` adds each caller with its first use: `deal` and `lead` join with SP3/R3.8's second deploy, `approval` with SP4/R4.5); the token's `org` claim must equal the path organization (403 otherwise) |
| Session | `get_directory_session` (the BYPASSRLS, SELECT-only directory reader), with an explicit `Membership.organization_id == :organization_id` predicate; never a write |
| Query | `after_id` (uuid, optional keyset), `limit` (1-500, default 200), `ids` (comma list, at most 100; when present the result is exactly those that belong to the organization) |
| Response | `{items: [{membership_id, full_name, email, status, is_owner, employee_id, manager_membership_id}], next_after_id: uuid \| null}`, ordered by `Membership.id`; `full_name` is `coalesce(nullif(display_name, ''), users.full_name)`; **every status is returned, `removed` included**, so a plan can report why a roster email matches nobody |
| Errors | 401 no token; 403 wrong caller or wrong or missing organization claim; 422 malformed uuid, `limit` out of range or more than 100 ids |
| Fields never returned | credentials, sessions, MFA state, the user's phone (the directory holds none), roles |

It sits beside the notification-only single read (`account/app/api/internal.py:304-346`), which is unchanged, in the same router
(mounted at the root, excluded from the public OpenAPI). New: `selectors.list_directory_members`, `_require_caller` and
`_require_org_claim` helpers (the shape of `deal/app/api/internal.py:64-80`), schema `DirectoryMemberOut`.

**Customer (registry C25-C27, C49).** All four use existing permissions; none adds a code.

| Route | Permission, floor | Change |
|---|---|---|
| `GET /customers/owners` (C25) | `crm.book.company.read`, no floor, organization filter only (reference data) | `OwnerOut` gains `membership_id: str \| null` |
| `POST /customers/owners` (C26) | `crm.book.owner.write`, `minimum=Scope.ORG` | `OwnerIn` gains `membership_id: uuid \| null`. **With it**, `full_name` and `email` become optional and are copied from the account row; **without it** the body is as today (`full_name` and `email` required) until R2.7 |
| `GET /customers/owners/candidates` (C27) | `crm.book.owner.write`, `minimum=Scope.ORG` | **new**; declared before `/customers/owners/{owner_id}`; response `{items: [{membership_id, full_name, email, status, is_owner, manager_membership_id}]}`: active members with no roster row, assembled from the account read |
| `PATCH /customers/owners/{owner_id}` (C28) | `crm.book.owner.write`, ORG | unchanged; `membership_id` is **not** patchable (`extra = forbid`) |
| `POST /internal/v1/organizations/{id}/owners:validate` (C49) | service token, caller `lead` | accepts either kind of id (dual resolution); request and response shapes unchanged |

**Activation, step by step** (claim, call, record; DD-28; the account call is **never** inside a transaction):

1. *Claim* (read-only transaction): confirm no roster row already holds this `membership_id` (409 `already_activated`).
2. *Call* (no transaction): `account.memberships?ids=<membership_id>` with `ACCOUNT_SERVICE_URL`/`account_timeout_seconds`
   (F4). Unreachable, timeout or 5xx is 503 `upstream-unavailable` and nothing is written. Unknown id for this organization is 404
   `membership_not_found`; any `status` other than `active` is 422 `membership_not_active`.
3. *Record* (transaction): re-check `already_activated` under the partial unique index (a race answers 409); refuse with 409
   `owner_email_exists`, naming that roster row's id, when any roster row of the organization already has the member's email,
   compared case-insensitively (the owner links that row with the mapping tool instead of creating a second person: `plan` offers
   it on email evidence, `link` maps it by hand, §9.4); insert the
   roster row with `membership_id`, `full_name` (cut to 160), `email`, `role` (the body's, default empty), `is_active = true`; audit
   `owner.created` with `after = {membership_id, activated: true}`; the existing outbox behaviour of `create_owner` is unchanged.

The candidates route calls account the same way (call first, then one read-only transaction for the roster set), pages through the
account read internally up to 5,000 members and answers 503 rather than a partial list if account fails midway.

**New settings** (customer): `account_service_url: str = "http://localhost:8111"` and `account_timeout_seconds: float = 5.0`
(registry F4), reusing the already-shared `ACCOUNT_SERVICE_URL` of `x-service-env` (`compose.prod.yml:103`), so no compose line
changes. The registry's `L3` warns that notification's `account_internal_url` has no compose line; this release does not repeat
that name.

**Registered with the guard.** The customer-to-account call (W17, `C57`; the shared `ACCOUNT_SERVICE_URL`) is added to `CALLS`
in `tests/infra/test_internal_client_paths.py` in the same commit as the client, with a driver that returns the token it sent:
the guard then proves that the request reaches a route account serves and that the token names the organization, and a client
in a new file fails it until it is registered (SP1 §9.2.2).

**Dual resolution, the seven lookups** (DD-28; registry B3). One shared rule: a reference `r` resolves to the roster row
`ORDER BY (membership_id = r) DESC LIMIT 1` over `membership_id = r OR id = r`, so a membership id wins if both could match
(they cannot in practice, both are random uuids; the order only makes the rule deterministic).

| # | Site | Behaviour after R2.6 |
|---|---|---|
| 1 | `selectors.get_owner` (`customer/app/selectors.py:44-52`) | dual |
| 2 | `selectors.get_owner_for_update` (`:64-86`; `_assert_owner_assignable`, `services.py:187-206`) | dual, row-locked |
| 3 | `selectors.active_owner_ids` (`:89-102`; `owners:validate`) | returns the set of **references as posted** that resolve to an active row: for each matching row both its `id` and its `membership_id` are in the set |
| 4 | `selectors.count_open_records_for_owner` (`:105-134`; the deactivation guard) | counts companies and contacts whose `owner_id` is the row's `id` **or** its `membership_id` |
| 5 | the 360's owner name (`customer/app/api/c1_customer360.py:63`) | dual (calls `get_owner`) |
| 6 | the SPA's `ownerById` (`frontend/product-crm/src/data/lookups.ts:13`) | membership id first, then id (§9.6) |
| 7 | the picker's option values (`forms/OwnerPicker.tsx:118`) | `membership_id ?? id` (§9.6) |

Sites 1-5 are server, 6-7 are the SPA. Writes store the reference **as posted** (no server-side canonicalisation: the SPA now
posts the membership id for any owner that has one, and strays are what `verify` lists).

**Events and OpenAPI.** No event changes (`crm.book.company.*`, `crm.book.contact.*` are untouched). `make openapi` regenerates
`libs/contracts/openapi/customer.json` (`OwnerOut`, `OwnerIn`, the candidates path); account's route is internal and not in any
OpenAPI document.

### 9.4 Domain rules, and the mapping tool

**Roster rules.**

1. `membership_id` is unique per organization (the partial index) and **immutable once set**.
2. Activation copies identity from account and never trusts the body's name or email when `membership_id` is present.
3. A roster row with a `membership_id` is a **rep activated from a member**; a row without one is a legacy row, valid until R2.7
   refuses free-form creation. Deactivation (`PATCH is_active=false`) keeps the row and its `membership_id`; re-activating a
   deactivated member is the existing `owner.reactivated` path, never a second row.
4. The deactivation guard counts both kinds of id (site 4), so a rep whose records were rewritten cannot be deactivated while they
   still own live ones.

**The mapping tool, `tools/map_crm_owners.py`** (registry H1; gating copies the purge tools, DD-29, DD-30, DD-31, DD-32, DD-33).
It exists in two roles inside one file: a **pure core** (classification, plan file, digest, gates; no service import, unit-testable)
and **three child entry points**, one per service (`--child --service customer|deal|lead`), because `import app` is ambiguous across
services and each service's own NOBYPASSRLS app role may touch only its own schema. Sequencing across the three is the host-side
wrapper `remote.sh map-crm-owners <subcommand>` (the shape of `reconcile_permissions`, `remote.sh:434-458`): one throwaway container
per child, `docker compose run --rm --no-deps -T -e PYTHONPATH=/app -v <repo>/tools:/opt/tools:ro -v $STATE_DIR/map-crm-owners:/state
<service> python /opt/tools/map_crm_owners.py --child --service <service> …`. The Make target is
`edge-map-crm-owners: edge-guard` (`HOST`, `CONFIRM` required; `SUB=census|plan|show|accept|reject|link|apply|verify|revert|forget-plan`,
`ARGS=…`), owner-gated exactly like `edge-reconcile`. **An agent builds and tests all of it and never runs it against a box.**

Common behaviour: `--org <uuid>` or `--all`; `--pace <ms>` (default 250); one transaction per organization per service on the
service's own app role with the tenant GUC bound for that transaction; stop at the first failing organization; idempotent and
re-runnable; stdout and stderr carry **counts and ids only, never an email or a name** (the one exception is `show --print-emails`,
below).

**The state directory (DD-55).** `$STATE_DIR/map-crm-owners/` and every file in it belong to the **image's own user**, the non-root
`opsgrids` system user the service images run as. Before the first child runs, the wrapper asks a throwaway container for that
user's `id -u` and `id -g`, validates both as plain digits (`infrastructure/edge/lib/numeric-id.sh`), refuses a symlinked
directory, creates it and `chown -R -h`s it, exactly as `ensure_ops_monitor_state_dir` does for ops-monitor
(`remote.sh:313-340`): the deploy user's uid is not the image user's, so neither can use a directory the other made. Directory
mode **700**, files **600**. The deploy user therefore cannot read or edit anything in it, on purpose: the roster emails in
`plan.json` are personal data at rest, reachable only through the tool, which runs as that user. Nothing else under `$STATE_DIR`
changes owner.

| File | Written by | Holds | Notes |
|---|---|---|---|
| `values-<service>.json` | `plan` (deal and lead children first) | per organization, the distinct owner-id values that service holds, with row counts, ids only | `leads.owner_id` and `assignment_rules.owner_ids` elements; `deals.owner_id` |
| `snapshot.json` | `plan`, `apply`, `verify` (customer child) | per organization, every membership id and employee id from the account read, ids only | the only place deal and lead children learn the member set, so neither calls account |
| `manual-pairs.json` | `link` (customer child) | per organization, hand-entered `(roster owner id, membership id)` pairs | ids only; read by every `plan`, which validates each pair afresh |
| `plan.json` | `plan` (customer child); `accept` and `reject` rewrite `accepted` only | the roster's emails and the proposed mappings | **contains emails**; never printed except by `show --print-emails`, never in the repo, never in shell history; destroyed by `forget-plan` once the final `verify` is recorded |
| `run-<run_id>.json` | `apply --service customer` | per organization, the accepted `old -> new, evidence` pairs actually applied | ids only; deal and lead follow exactly this subset |

Subcommands:

- **`census`** (read-only; `SET TRANSACTION READ ONLY`; pending D-10 and D-3). Per organization, from each child: roster size and
  how many rows carry a `membership_id`; the distinct `owner_id` counts per table (`companies`, `contacts`, `deals`, `activities`,
  `leads`, `assignment_rules.owner_ids`) and how many of them are in the roster; roster emails equal to an active member's email;
  owner values equal to any membership's `employee_id`; leads with a live conversion claim; `lead.outbox` rows not yet published; and
  with `--aborted-conversions` the three counts of §7.4. Counts only.
- **`plan`** classifies every legacy owner id in the data (never fuzzy; DD-8): `link` (the value equals exactly one membership's
  `employee_id`: an audited human assertion), `email` (a roster row whose email equals, case-insensitively and with nothing else
  folded, exactly one **active** member's email: needs explicit per-row acceptance), `manual` (a pair the owner entered with `link`,
  read from `manual-pairs.json` and validated afresh: the roster row exists and carries no `membership_id`, the membership is
  `active` in this organization and neither side is claimed by another row; it replaces an offered `email`, `conflict` or `unmapped`
  row for the same roster id), `conflict` (several candidates, a member already
  claimed by another roster row, or a value that is both a roster id and another member's employee id) or `unmapped`. Removed,
  suspended and invited members are report-only. Each row has `accepted`, default `true` for `link` and `false` for `email` and
  `manual`; a `conflict` or `unmapped` row can never be accepted. The plan carries a **digest over every field except `accepted`**;
  `apply` recomputes it and refuses a plan whose immutable fields differ, so the owner can accept what was offered and can add a
  mapping only through `link`, which names ids, is validated against account and the roster, is labelled `manual` and starts unaccepted.
- **`show --org <uuid>`** (read-only). Per plan row, ids and counts only: the plan row id (the legacy owner value: a roster id, or an
  employee id for a `link` row), its class and evidence, `accepted`, and how many records per table carry that value. With
  **`--print-emails`** it also prints the roster email and the matched member's email side by side, **to the invoking terminal
  only** (stdout; never written under the state directory, never to a log, never tee'd by the wrapper). That flag is the one place any
  subcommand prints an email, and the test that none does exempts exactly it.
- **`accept --org <uuid> --ids <id,id,…>`** and **`reject …`**: set `accepted` on the named plan rows and nothing else; a `conflict` or
  `unmapped` row is refused. The digest is unchanged by construction. This is how the owner reviews: the file is never edited by
  hand, because the deploy user cannot open it.
- **`link --org <uuid> --owner-id <roster id> --membership-id <uuid>`**: records one hand-entered pair in `manual-pairs.json` after
  validating it as `plan` will, for the person the planner could not match (typically a roster email that differs from the account
  email). It maps nothing by itself: the next `plan` carries it as a `manual` row, unaccepted, and `apply` journals it with
  `evidence = 'manual'` (§9.2), the hand entry that column always allowed for.
- **`apply --service S --plan plan.json`** (dry run unless `--apply` **and** `--confirm-count N`, `--i-took-a-backup` and
  `--i-know-this-is-the-live-database`; `N` is the row count the dry run printed for that service; `tools/purge_test_orgs.py:1892-1910`).
  - **customer first** (DD-30): re-validates every accepted membership against account (a membership removed or suspended since the
    plan is skipped and listed), sets `owners.membership_id`, rewrites `companies.owner_id` and `contacts.owner_id`, writes the run file.
    **deal and lead refuse to apply** for an organization whose plan has no customer run file.
  - Every rewrite is `UPDATE ... SET owner_id = :new WHERE owner_id = :old` with `updated_at` set to itself (no mass touch, DD-33),
    so a row a rep changed concurrently no longer matches and is counted `changed_concurrently`; each changed id gets one journal row;
    **no outbox event is staged**; one audit row per organization per service (`acting_as = 'map-crm-owners-cli'`, counters only).
  - **deal** rewrites `deals.owner_id` for both roster-valued and employee-valued rows and **leaves `deal.activities` untouched**
    (its append-only trigger stays: DD-32). Consequence (registry `L7`): historical activity rows keep legacy ids, resolved by the
    roster's legacy arm for display; a custom role that reads activities at OWN loses its history, while `CRM_REP`, `CRM_MANAGER` and
    `CRM_VIEWER` read them at ORG.
  - **lead** rewrites `leads.owner_id` and `assignment_rules.owner_ids` element by element, **preserving order and the integer
    cursor**. It selects candidates `FOR UPDATE SKIP LOCKED` (a locked row is in flight by definition) and **skips every lead whose
    conversion claim was taken within `2 x CONVERSION_LEASE_SECONDS`** (120 s; `lead/app/domain.py:93-101`), listing the ids,
    because rewriting `owner_id` under a live claim forces an `owner_changed` abort and `finalize_conversion` still honours a claim id
    the lease has already expired (`lead/app/services.py:1104`, DD-31). A later re-run picks them up.
- **`verify [--org | --all]`** per service, ids and counts only, per organization. First the **strays**: owner ids that are neither a
  membership of the organization (from `snapshot.json`) nor a known legacy roster id. Then what is **still legacy** (DD-56), which a
  stray count hides because a legacy roster id resolves in the roster and is not a stray: `legacy_rows_remaining` per table
  (`companies`, `contacts`, `deals`, `leads`, `assignment_rules` elements) whose owner value is a legacy roster id or an unmapped
  employee id; `unconvertible_leads`, the leads not yet `converted` whose owner is not a membership with an active roster row (after
  the flip each fails `:convert` with `OwnerUnknown` until it is reassigned: §10.4.5); `unnamed` members, memberships that own
  records and have no roster row; and the leads `apply` skipped. **Exits non-zero on any stray**; the legacy counts do not change the
  exit status, because D-8 leaves unmapped rows untouched on purpose, so the owner **records them** and, for each non-zero number,
  maps it (`link`, `accept`, a re-run of `apply`), reassigns the records (the ORG-floored `PATCH`; the bulk reassign is R3.7) or
  accepts it in writing before the flip (§10.8, AC-2.7-14).
- **`revert --service S --run-id U`** (gated like `apply`): for each journal row of the run whose table's current value still equals the
  journaled `new_owner_id`, writes the old value back and appends a counter-journal row (`evidence = 'revert'`); a row changed since is
  skipped and counted. For `owners` it nulls `membership_id` where it still equals the journaled value.
- **`forget-plan --org <uuid> | --all`**: overwrites `plan.json` with zero bytes of the same length and unlinks it (best effort on the
  box's filesystem: the aim is that no copy of the roster emails outlives the release), and removes nothing else. The journal (in the
  databases), `run-*.json` and `manual-pairs.json` (all ids only) stay, because `revert` works from the journal and names a run by
  its run file. Run after the final `verify` is recorded (§10.8).

**Why activities stay untouched, and what drifts.** Lifting the append-only trigger for one run would bend a declared invariant for
a case the default roles do not exercise (DD-32). **Insights drift** (inference: nothing drifts today because the insights stack is
unmerged): events published before the flip carry roster ids and later ones membership ids, and because the tool stages no events
and leaves `updated_at` alone (DD-33), an insights export keyed on `(updated_at, id)` never re-sends rewritten rows. Whoever lands
insights must treat `owner_id` as membership-keyed from the flip date and re-run its backfill after R2.7; nothing in this programme
depends on it (registry `L10`).

### 9.5 Consumers and idempotency

No event consumer is added or changed. Idempotency lives in the tool and the activation: `apply` rewrites only the exact legacy id
the plan names with `WHERE owner_id = :old`, so a second run finds nothing to change; the journal and the run file are keyed by
`run_id`; activation is idempotent through `uq_owners_org_membership` (a replayed request is 409 `already_activated`, which a client
treats as done). `tests/infra/test_io_outside_transaction.py` must stay green with **no ALLOWLIST entry**: the account call is made
before any transaction opens, in both activation and candidates.

### 9.6 UI

One small SPA change ships here, because it is what makes the data step safe whenever the owner chooses to run it (without it, a
rewritten row's owner renders as "Unassigned" until R2.7): **owner display and owner value resolve both kinds of id.**
`frontend/product-crm`:

- `api/types.ts` `Owner` (and `api/wire.ts`, `api/mappers.ts`) gain `membership_id: string | null`; a new `ownerKey(o) =
  o.membership_id ?? o.id` in `data/lookups.ts`.
- `data/lookups.ts` `ownerById` matches `membership_id` first, then `id` (`ownerName`, `OwnerBadge` and every board, timeline and
  detail use it, so they follow).
- `forms/OwnerPicker.tsx:118`: option values are `ownerKey(o)`, and the current value is normalised through `ownerById` so a record
  that still carries a legacy id selects the right option instead of showing the "Choose an owner" prompt.
- `data/importExport/shared.ts:79` (`entry`): the importer's `owners` lookup values each rep as `ownerKey(o)`; `ownersAll.labelOf`
  answers for the legacy id as well (an alias map beside the lookup, so neither lookup becomes ambiguous under `makeLookup`'s
  "one label, two values" rule, `opsgrids-client/src/dataexchange/lookups.ts:20`).

**Not in this release (R2.7):** `getMySubjectRef()` keeps reading `sref`, "My pipeline" and the import's self-owner keep using it,
`AccessClaims` gains `mem` later. The Team screen that lists candidates and activates them is SP3's (R3.4).

States and fixtures: `frontend/opsgrids-visual/fixtures/api/crm/owners.json` gains `membership_id` on every row (some null, some
set, so both resolution paths are drawn); the loading, empty, error and forbidden states of the pickers are unchanged.

### 9.7 Tests

Each service in its own process.

**`services/account/tests/test_internal_memberships.py` (new).** Behaviour: `test_a_customer_token_lists_the_organizations_members_with_the_documented_fields`,
`test_after_id_and_limit_page_in_id_order_and_next_after_id_ends_with_null`, `test_limit_above_500_is_422`, `test_ids_returns_exactly_the_named_members_of_this_organization`,
`test_more_than_100_ids_is_422`, `test_every_status_is_returned_including_removed`, `test_display_name_wins_over_the_users_full_name`,
`test_the_response_carries_no_credential_session_or_role_field`, `test_the_route_is_absent_from_the_public_openapi`. Negative-authz and
cross-tenant: `test_no_token_is_401`, `test_a_non_customer_caller_is_403` (parametrised over `deal`, `lead`, `notification`,
`finance`), `test_a_token_for_another_organization_is_403`, `test_a_token_with_no_org_claim_is_403`,
`test_the_path_organization_is_the_only_one_read` (a member of organization B never appears under A's path).

**`tests/infra/test_internal_client_paths.py` (SP1's guard, edited here).** `CALLS` gains the customer-to-account call (W17,
`C57`): the driver runs customer's real client against an `httpx.MockTransport` and returns the token it sent, so
`test_every_registered_call_reaches_a_route_the_callee_serves` finds `GET /internal/v1/organizations/{id}/memberships` on
account's app (it fails if the route is absent or mounted elsewhere) and `test_every_registered_call_names_its_organization_in_its_token`
decodes the token and asserts `iss` is `customer` and the `org` claim is the organization in the path.

**`services/customer/tests/test_migration_owner_membership.py` (new, modelled on `test_migration_case_insensitive_company_name.py`).**
Upgrade adds the column, the partial unique index and the journal table with its trigger, policy and RLS; `test_the_journal_is_append_only`
(an UPDATE and a DELETE as the app role both fail); `test_two_roster_rows_cannot_share_a_membership_but_many_may_have_none`;
`test_downgrade_is_clean_when_empty`; `test_downgrade_refuses_when_the_journal_has_rows`; `test_downgrade_refuses_when_a_membership_id_is_set`;
`test_downgrade_with_the_override_drops_everything`; `test_the_customer_chain_has_one_head_and_places_the_revision_directly_after_e230c025337e`. Deal and lead each get
`test_migration_owner_rekey_journal.py` with the journal-only subset, plus RLS cross-tenant: `test_a_session_for_another_organization_sees_no_journal_row`;
deal's also has `test_the_deal_journal_revision_directly_follows_the_stages_revision` (exactly one head, `d8a35c17e920`'s `down_revision` is
`f3b8d1a65c02`, and no revision other than `a1c9e2f7b3d4` is parented on `e94d7a2c5f18`: DD-51), and lead's the one-head and `91dd3ca663fd` placement.

**`services/customer/tests/test_owners.py` (extended).** Activation: `test_activating_a_member_copies_name_and_email_from_account` (a
fake account client at the HTTP boundary), `test_the_account_call_runs_with_no_transaction_open`, `test_a_non_active_membership_is_422_membership_not_active`,
`test_an_unknown_membership_is_404_membership_not_found`, `test_a_second_activation_is_409_already_activated`,
`test_an_email_already_on_the_roster_is_409_owner_email_exists_case_insensitively`, `test_account_unreachable_is_503_and_writes_nothing`,
`test_free_form_creation_still_works_in_r2_6`, `test_the_body_name_is_ignored_when_a_membership_id_is_given`, `test_membership_id_is_immutable_on_patch`.
Negative-authz and cross-tenant for `POST /customers/owners` and `GET /customers/owners/candidates`: `test_no_token_is_401`,
`test_a_reader_without_owner_write_is_403`, `test_owner_write_at_own_scope_is_403_below_the_floor`, `test_a_non_entitled_organization_is_402`,
`test_candidates_never_list_another_organizations_members`, `test_a_membership_of_another_organization_cannot_be_activated` (account answers
404 for it). Candidates behaviour: `test_candidates_are_active_members_with_no_roster_row`, `test_candidates_is_declared_before_the_owner_detail_route`,
`test_a_failing_account_page_is_503_not_a_partial_list`.

**Dual resolution (`services/customer/tests/test_owner_dual_resolution.py`, new).** One parametrised test per lookup of the table in
§9.3, each run with a legacy reference and a membership reference: `get_owner`, `get_owner_for_update`, `active_owner_ids`,
`count_open_records_for_owner` (a rep whose rows carry either id), `_assert_owner_assignable`, the 360's owner name, `owners:validate`
(which keeps its three negative tests: no token 401, wrong caller 403, wrong organization claim 403); plus
`test_a_membership_reference_wins_when_both_could_match`, `test_deactivation_is_refused_while_records_carry_either_id`,
`test_an_unknown_reference_resolves_to_nothing` and `test_another_organizations_membership_id_resolves_to_nothing` (cross-tenant).

**The tool, in three places, because `make test-infra` is the gate's static row** (no database, no broker: `Makefile:177-182`,
`GATE_STATIC`, `Makefile:525-526`), and each child imports its own service's `app`, which cannot share a process with another's.

*(a) Static, no database: `tests/infra/test_map_crm_owners.py`, in `make test-infra`.* The planner (`test_a_roster_email_equal_to_one_active_members_email_is_an_email_candidate_not_accepted_by_default`,
`test_an_employee_id_value_is_a_link_candidate_accepted_by_default`, `test_two_roster_rows_for_one_member_are_a_conflict`,
`test_one_roster_row_matching_two_members_is_a_conflict`, `test_removed_suspended_and_invited_members_are_report_only`,
`test_a_manual_pair_is_a_manual_row_that_is_not_accepted_by_default`, `test_a_manual_pair_for_a_claimed_or_inactive_member_is_refused`,
`test_nothing_is_ever_fuzzy_matched` over case-folded local parts, plus-addressing, trailing spaces and domain aliases, each unmatched
except the case-insensitive exact match); the plan file (`test_the_digest_covers_every_immutable_field`,
`test_apply_refuses_a_plan_whose_immutable_fields_were_edited`, `test_a_conflict_or_unmapped_row_can_never_be_accepted`,
`test_accept_and_reject_change_only_accepted_and_leave_the_digest`, `test_the_plan_file_is_mode_600_and_never_printed`,
`test_show_prints_emails_only_with_its_flag_and_writes_them_nowhere`,
`test_forget_plan_removes_plan_json_and_keeps_the_run_files_and_manual_pairs`); the gates (`test_without_the_three_flags_apply_is_a_dry_run`, `test_each_gate_flag_is_required`,
`test_confirm_count_must_equal_the_dry_run_count`); the wrapper (`test_the_wrapper_runs_children_in_the_documented_order_and_stops_on_failure`,
`test_apply_requires_an_explicit_service`, `test_the_make_target_depends_on_edge_guard`,
`test_the_wrapper_creates_the_state_directory_owned_by_the_images_uid_and_gid_with_mode_700`,
`test_the_wrapper_refuses_a_symlinked_state_directory`, `test_a_non_numeric_uid_is_refused_before_anything_runs_as_root`, with the real `remote.sh`
function sliced out and a fake `docker` first on `PATH`, the technique of `tests/infra/test_remote_state_dir.py`).

*(b) Per child, with a database: each service's own suite, in `make test-services` (`GATE_DB`).* The precedent is
`services/account/tests/test_reconcile_permissions.py`, which tests `tools/reconcile_permissions.py` in account's own suite. Each file
reaches `tools/map_crm_owners.py` the way that file reaches its tool (`tools/` is not a package: its directory goes on `sys.path`,
`test_reconcile_permissions.py:66-70`), uses the suite's real-Postgres fixtures and app role, and fakes account at the HTTP boundary
where it is needed.
`services/customer/tests/test_map_crm_owners_child.py`: `test_apply_sets_membership_id_and_rewrites_companies_and_contacts_with_one_journal_row_per_id`,
`test_apply_revalidates_each_membership_against_account`, `test_a_membership_removed_since_the_plan_is_skipped_and_listed`,
`test_a_manual_row_applies_with_evidence_manual`. `services/deal/tests/test_map_crm_owners_child.py`:
`test_apply_rewrites_roster_valued_and_employee_valued_deals_and_leaves_activities_untouched`, `test_deal_refuses_before_the_customer_run_file_exists`.
`services/lead/tests/test_map_crm_owners_child.py`: `test_apply_rewrites_owner_and_array_elements_preserving_order_and_cursor`,
`test_apply_skips_a_lead_claimed_within_two_leases_and_lists_it`, `test_apply_skips_a_lead_locked_by_a_concurrent_writer`,
`test_lead_refuses_before_the_customer_run_file_exists`, `test_verify_counts_unconvertible_leads`. **Every one of the three files also
carries**, for its own tables: `test_apply_does_not_touch_updated_at`, `test_apply_stages_no_outbox_event`,
`test_apply_writes_one_audit_row_per_organization_with_counters_only`, `test_a_rerun_changes_nothing`,
`test_a_row_changed_concurrently_is_skipped_not_overwritten`, `test_verify_lists_strays_and_exits_non_zero`,
`test_verify_reports_legacy_rows_remaining_without_failing_on_them` (the count of rows still carrying a legacy roster id, per table; exit status
zero when there is no stray), `test_revert_restores_only_where_the_current_value_equals_the_journaled_value_and_appends_counter_rows`,
`test_revert_skips_a_row_changed_since`, `test_one_failing_organization_stops_the_run`,
`test_apply_for_organization_a_never_changes_organization_b` (cross-tenant), `test_no_subcommand_prints_an_email_or_a_name` (stdout and stderr of
every subcommand except `show --print-emails`, over a world full of emails, contain no `@`),
`test_every_statement_on_a_tenant_table_filters_on_organization_id` (a `before_cursor_execute` listener captures the tool's SQL and, in customer's
file, the activation path's too, and asserts each statement that touches a tenant table names `organization_id`).

*(c) Across the three schemas, opt-in: `tests/infra/test_map_crm_owners_e2e_db.py`.* One organization through `plan`, then `apply` for
customer, deal and lead in that order as three subprocesses, each in its own service directory (the technique of
`tests/infra/test_route_table_parity.py::_in_service`), then `verify`, then `revert` in the reverse order:
`test_the_three_children_apply_in_order_and_verify_is_clean`, `test_revert_in_the_reverse_order_restores_every_table`,
`test_a_failing_child_stops_the_sequence`. It is **opt-in**, the pattern of `test_purge_test_orgs_db.py`:
`pytestmark = pytest.mark.skipif(not REAL_DSN, reason="set OPSGRIDS_MAP_OWNERS_TEST_DATABASE_URL to a migrated throwaway database (as opsgrids_admin)")`,
which `make hygiene` accepts because it names a reason; the gate's static row skips it with that reason. It is **a manual step of the
plan's R2.6 verification**: `make migrate` into a throwaway database, then `OPSGRIDS_MAP_OWNERS_TEST_DATABASE_URL=… uv run pytest
tests/infra/test_map_crm_owners_e2e_db.py`, the pass count pasted into the pull request.

**SPA (`frontend/product-crm`, vitest).** `lookups.test.ts` (`ownerById` matches the membership id first, then the id, and is null-safe;
`ownerKey`), `OwnerPicker.test.tsx` (options carry the key; a legacy-id value selects the right option; the one-click claim still works),
`importExport/shared.test.ts` and `companies.test.ts` (the owners lookup resolves an email to the key; `ownersAll.labelOf` answers both ids; no
lookup is ambiguous); `frontend/opsgrids-visual/fixtures/contracts.test.ts` with the updated `owners.json`; `make ui-visual` shows no baseline change.

**Command lines name their app (`tests/infra/test_crm_edge_web_commands_name_app.py`, new, static).** Every `make edge-web` and
`make edge-web-rollback` command line in the RUNBOOK sections whose heading names CRM and in the fenced command blocks of
`docs/superpowers/plans/2026-10-07-crm-*.md` carries `APP=`. `make edge-web-rollback` defaults to the anonymous storefront's `buyer`
bundle (`Makefile:625`, `infrastructure/edge/publish-web.sh:53`), so a rollback typed from a CRM section without `APP=crm` would swap
the storefront's bundle back during an incident and leave the CRM SPA where it is. The scan is scoped to this programme's sections
because the RUNBOOK's own buyer-bundle lines rightly rely on the default (`RUNBOOK.md:242,2641`), which is also why making the
Makefile refuse an unset `APP` was rejected. Self-tests: a line without `APP=` is flagged, one with it passes, a line in another
section is ignored.

**Gates.** `make openapi` and `make openapi-check`; `make contracts-check`; `make migration-roundtrip` and `make migration-check` (customer,
deal, lead); `tests/infra/test_migration_revisions.py` and `test_io_outside_transaction.py` unmodified and green;
`tests/infra/test_purge_test_orgs_db.py` unmodified and, because it is opt-in (§6.7), run by hand against a migrated throwaway
database with the pass count pasted into the pull request, since this release adds three append-only journal tables (one per
schema) that it must still reach; `make gate`.

### 9.8 Rollout

R2.6 changes four images (`account`, `customer`, `deal`, `lead`) and ships three migrations; nothing in it reads or rewrites an owner column
of existing rows, so a deploy of R2.6 alone changes no user's access. **Precondition: R2.3 has merged** (DD-51): deal's revision here
follows R2.3's in the chain. R2.3 should have been deployed and verified on its own first (the release train's order); if it was
not, one `make deploy` walks `alembic upgrade head` through both in order, which is safe but forfeits R2.3's own verification.

1. Review (CODEOWNERS applies to `libs/contracts/`); owner squash-merge; `make gate` locally.
2. Owner-named `make deploy HOST=… CONFIRM=… REF=<sha>`. `libs/contracts` is excluded from the full-rebuild rule, so the four images
   rebuild and nothing else. **Migrations run before `up`** (§3.7): customer, deal and lead each gain one additive revision while their old
   code serves; old code ignores a nullable column and an empty table, so the skew is safe.
3. Verify (owner-run reads): `make edge-status`; the three `alembic current` checks the RUNBOOK uses (`docker compose … run --rm --no-deps -T
   <service> alembic current`) show `b6d20f94e1a7`, `d8a35c17e920` and `a47e3b9d1c68`;
   `GET /api/v1/customers/owners` carries `membership_id: null` on every row; the account log shows no `403` for `customer`.
4. **Owner: publish the SPA** (`make edge-web HOST=… CONFIRM=… APP=crm` **from the checked-out merged main**: the bundle is
   built from the local checkout, and the published bundle should be grepped for `membership_id` to prove the right tree was built; the
   edge-web build reads the checkout, not the deployed ref).
5. **Owner: census, then plan** (reads only, no write): `make edge-map-crm-owners HOST=… CONFIRM=… SUB=census` (add
   `ARGS=--aborted-conversions` for §7.4's count), read it (D-10); then `SUB=plan`, which writes `plan.json` into the box's state
   directory (owned by the image's user, mode 600, so the deploy user cannot open it: DD-55). The owner reviews it **through the
   tool**: `SUB=show ARGS="--org <uuid>"` for ids and counts, adding `--print-emails` to see the roster and member emails side by
   side on their own terminal, then `SUB=accept` or `SUB=reject` with `ARGS="--org <uuid> --ids <id,id,…>"` for each `email` or
   `manual` row, and `SUB=link` for a person the planner could not match (a roster email that differs from the account email),
   followed by a fresh `SUB=plan`. The plan stays on the box until `forget-plan` (§10.8). **`apply` is not run
   in this release**; it belongs to the R2.7 window (§10.8), though dual resolution means the owner *may* run it earlier.
6. Memory: no container is added or resized; `memory_budget_totals.py` is unchanged from R2.4's figures (crm 2068, ten profiles 3748, ten
   plus `admin` 3945), `account` stays `api_with_worker`. Postgres connections are unchanged.
7. Docs: `docs/crm.md` (roster semantics, the tool), `infrastructure/edge/RUNBOOK.md` (a new section for the tool: census, plan, the
   review subcommands, the state directory's owner and mode, the three gates, the order, the revert, `forget-plan`), register rows 20 and 135 in
   `docs/adr/open-questions-register.md`.

### 9.9 Rollback

Redeploy the previous ref (`make edge-rollback`); the three revisions stay (expand-only, ignored by old code; `remote.sh migrate` skips a
service whose database is ahead). The SPA rolls back by `make edge-web-rollback HOST=… CONFIRM=… APP=crm` (one symlink); **`APP=crm` is
required**: the target defaults to the anonymous storefront's `buyer` bundle (`Makefile:625`, `infrastructure/edge/publish-web.sh:53`), so
an omitted `APP` would roll the wrong site back and leave the CRM bundle where it is. Nothing is rewritten by R2.6, so there is no
data to restore. **If `apply` has been run**, code from before R2.6 knows only roster ids and cannot name a membership-valued row (the owner
column would read "Unassigned"), so roll the data back first with `revert --run-id` (§9.4) **before** rolling the code below R2.6. Stop conditions: any 5xx on
the owners routes; `account` answering 403 to `customer`; an owner column rendering "Unassigned" for a record that has an owner; the
candidates list failing for an organization with members.

### 9.10 Acceptance

- **AC-2.6-1** Each of customer, deal and lead has exactly one Alembic head; customer's chain holds `b6d20f94e1a7` directly after `e230c025337e`, deal's holds `d8a35c17e920` directly after `f3b8d1a65c02` (R2.3's revision: there is no other order, DD-51), lead's holds `a47e3b9d1c68` directly after `91dd3ca663fd`; `make migration-roundtrip` and `make migration-check` pass; each downgrade guard refuses as §9.2 states (tests).
- **AC-2.6-2** In all three schemas the journal has RLS enabled and forced, the `tenant_isolation` policy with `WITH CHECK`, an organization index, the append-only trigger and its CHECKs; a session for another organization sees nothing (tests).
- **AC-2.6-3** `owners.membership_id` is unique per organization where set, immutable, and has no foreign key (tests).
- **AC-2.6-4** The account membership read honours §9.3, is reachable by `customer` only, and answers 401, 403 (wrong caller), 403 (wrong or missing organization claim), and never returns another organization's member; customer's call to it is registered in `tests/infra/test_internal_client_paths.py` `CALLS` and passes that guard (tests).
- **AC-2.6-5** Activation validates outside any transaction and answers 404, 422, 409 (two codes), 503 and success exactly as §9.3, copying name and email from account (tests).
- **AC-2.6-6** `GET /customers/owners/candidates` and `POST /customers/owners` answer 401, 403, 403 below the ORG floor, 402 and refuse cross-tenant ids (tests).
- **AC-2.6-7** All five server lookups of §9.3 resolve a legacy reference and a membership reference, the membership wins a tie, and the deactivation guard counts both (tests); `tests/infra/test_io_outside_transaction.py` is green with no ALLOWLIST change.
- **AC-2.6-8** The planner classifies as §9.4 states, never fuzzy, report-only for non-active members, `accepted` defaults as stated, refuses an edited plan, and takes a hand mapping only through `link` (a validated, unaccepted `manual` row); `accept` and `reject` change only `accepted` (tests).
- **AC-2.6-9** `apply` and `revert` are dry runs without `--apply`, `--confirm-count N`, `--i-took-a-backup` and `--i-know-this-is-the-live-database`, and `N` must equal the dry run's count (tests).
- **AC-2.6-10** Customer applies first and deal and lead refuse without its run file; every rewrite is journaled, touches no `updated_at`, stages no outbox event and writes one counters-only audit row per organization per service (tests).
- **AC-2.6-11** Lead rewrite preserves array order and cursor, skips claims inside two leases and rows locked by a concurrent writer, and lists them (tests).
- **AC-2.6-12** `deal.activities` is untouched (its trigger refuses any rewrite) and a re-run changes nothing (tests).
- **AC-2.6-13** `verify` exits non-zero on a stray and reports `legacy_rows_remaining` per table, `unconvertible_leads` and `unnamed` members without failing on them, and `revert` restores only rows still holding the journaled value, appending counter rows (tests); no subcommand but `show --print-emails` prints an email or a name.
- **AC-2.6-14** The SPA resolves and offers both kinds of id, the visual fixture contract test passes, `make ui-visual` shows no baseline change, and the published bundle contains `membership_id` (grep, owner-run); the wrapper and Make target exist and are owner-gated (tests).
- **AC-2.6-15** The state directory is created for the image's own uid and gid with mode 700 (files 600), a symlinked directory and a non-numeric id are refused before anything runs as root, `plan.json` is reviewed only through `show`, `accept`, `reject` and `link`, and `forget-plan` removes `plan.json` while the journal, the run files and `manual-pairs.json` stay (tests); the plan's rollout task runs `forget-plan` after the final `verify` (owner-recorded).
- **AC-2.6-16** Each child's database tests are in its own service's suite and run in `make test-services`; the cross-schema end-to-end test is opt-in with `skipif(..., reason=...)`, passes `make hygiene`, and its pass count is in the pull request; every `make edge-web` and `make edge-web-rollback` line in the CRM RUNBOOK sections and the CRM plan names `APP=` (static test with self-test).

## 10. R2.7 — C15 flip: CRM scope keys on the membership

### 10.1 Purpose

Switch `customer`, `deal` and `lead` together from "OWN means the caller's employee link" to "OWN means the caller's membership id",
so that a rep in a CRM-only tenant (no employee record, `sref` null) can create, edit, convert and reveal **their own** rows, which
today are refused for everyone without an employee link (§3.5). This is the programme's one access widening (decision D-9): reads
stay at ORG for reps (registry `J19`, pending D-38); what newly works is OWN writes, convert and reveal on rows whose `owner_id` is the caller's
own membership id. It is therefore also the one release that needs the owner's **signed security review** of every OWN route
(§10.7) before it deploys. Everything else is the cutover of what R2.2 (the mechanism) and R2.6 (the roster and the data) prepared.

### 10.2 Data model

None: no migration, no DDL. The data moved in R2.6's `apply` (owner-run), which must have completed with `verify` clean before this
release deploys (§10.8). A row that still carries a legacy id after the flip is **fail-closed**: it matches no caller's OWN scope and is
visible at ORG exactly as before.

### 10.3 Backend contract

No new route, permission, event, setting or table. What changes:

1. **Registration, all three in one release** (registry `A13`; a skew fails closed, §10.4 rule 4):
   `services/customer/app/scope.py:47`, `services/deal/app/scope.py:48` and `services/lead/app/scope.py:44` become
   `register_scope_resolver(PRODUCT, <Resolver>, owner_subject=OwnerSubject.MEMBERSHIP)`. The module headers that say "their rep id is
   `subject_ref`" are rewritten to say membership id (the docstring half of registry `A7`, placed here because until now the header is true);
   `docs/architecture.md`'s CRM sentence follows.
2. **The six raw reads of §5.5 become `ctx.own_ref`** (`customer/app/services.py:205`; `deal/app/api/routes.py:112,163,175,241,243`).
3. **Roster, assignment and validation** (registry `C26`, `C49`, `C76`), the legacy arm of DD-28 retires for writes:
   - `_assert_owner_assignable` resolves `owner_id` through `owners.membership_id` **only**; an OWN caller may assign only to their own
     membership id.
   - `POST /customers/owners` without `membership_id` is **422** `membership_required` (a free-form body is refused); with it, as R2.6.
     **The one-click "make me the owner" keeps working** by posting the caller's **own** `membership_id` (DD-34), so a brand-new
     organization with zero reps is not stranded; R2.9 removes the button.
   - `POST /internal/v1/organizations/{id}/owners:validate` answers "invalid" for a legacy roster id (an id is valid only as an active
     row's `membership_id`); its request and response shapes are unchanged.
   - The read-side lookups keep resolving **both** arms (display names, the 360, the deactivation guard): historical `deal.activities` rows
     and any stray still carry legacy ids and must keep naming a person.
   - Deal validates no owner (unchanged: `deal/app/api/internal.py:14-21` says why); strays are `verify`'s job.
4. **Defaults** (registry `C76`): `owner_id` omitted on a deal or an activity defaults to `own_ref`; a caller whose `own_ref` is `None`
   gets the existing 422 (`deal/app/api/routes.py:114,245`) instead of a silent employee id.
5. **Service-level helpers** in each service's tests take the subject as a parameter (registry `A13`): `ctx_for` in
   `services/customer/tests/helpers.py` and `services/lead/tests/helpers.py` (`:134`) and `services/deal/tests/helpers.py` (`:40`).
6. **The static guard arms** (registry `A11`): `OPTED_IN = {"customer", "deal", "lead"}` in `tests/infra/test_no_raw_subject_ref.py`, the
   `KNOWN_RAW_READS` and `KNOWN_STRING_SPELLINGS` ratchet and its test are deleted, and the tie test of DD-7 now compares three names.
   The allow-list for the one string spelling stays (§5.5, DD-38): the 360 keeps forwarding `X-Subject-Ref` in `_IDENTITY_HEADERS`
   to upstreams that may still be on the employee link, and a self-test proves it is the only string hit in the three services. Arming
   the guard also checks the two reveal routes' `require(REVEAL_PERMISSION, …)` through the resolved constant, which a literal-only
   scan would have skipped.

OpenAPI changes only in prose and in the 422 of `POST /customers/owners`; `make openapi` regenerates `customer.json` and `deal.json`.

### 10.4 Domain rules

1. **OWN** (and, once R3.8 wires it, TEAM) compares a row's `owner_id` with `principal.membership_id`; ORG short-circuits first; with no
   `membership_id` OWN matches nothing (`WHERE false`) and passes no object check (R2.2 rules, §5.4).
2. **What a `CRM_REP` can newly do**: create, edit, assign (only to themselves), convert, set a phone on, and reveal (with step-up) the
   phone or email of **rows whose `owner_id` is their own membership id**. What they could already do is unchanged: read every row at ORG.
3. **What no template gains**: no ORG write, no delete, no roster write (`crm.book.owner.write` stays ORG-floored and admin-only), no
   assignment rule write (the service-level ORG check of `lead/app/services.py:181-182`), no reveal of anyone else's data.
4. **Skew fails closed.** If one service has flipped and another has not, the un-flipped one compares `owner_id` (now a membership id) with
   the employee link: OWN matches nothing there. Access narrows; it never widens. The same holds for a code rollback after `apply`
   (§10.9).
5. **Lead conversion needs an activated rep, and a mapped lead.** `convert` runs as the caller at OWN (claim re-asserts
   `assert_can_touch`, `lead/app/services.py:971`), but customer's `leads:convert` validates the owner against the roster
   (`_assert_owner_assignable`, membership-only after this release) and lead maps customer's 404 to the existing `OwnerUnknown`
   rejection (`lead/app/clients.py:135-137`). That fails for **two** populations, not one: a lead owned by a member with no roster
   row (`unnamed`), and a lead still owned by a **legacy roster id** that no mapping reached, which resolves in the roster for display
   (so it is not a stray) but is invalid for `owners:validate`, matches no OWN scope and cannot be converted by anyone. Until it is
   reassigned (an ORG admin's `PATCH`; the bulk `POST /leads:reassign` is R3.7), every ORG reader still sees it, no OWN caller can
   touch it and nobody can convert it.
   `verify` reports both (`unnamed`, `legacy_rows_remaining`, `unconvertible_leads`: §9.4, DD-56); before the flip the owner
   activates the members, maps the rest (`accept`, `link`) or reassigns the leads, and records the numbers that remain.
6. **Tenant-visible side effect.** `owner_id` on events (`crm.lead.created`, `crm.pipeline.deal.created`, ...) is now a membership id; a
   tenant automation that renders `{{payload.owner_id}}` shows the new value. Event shapes are unchanged (§14).

### 10.5 Consumers and idempotency

No consumer changes. The release is idempotent in the only way it needs to be: the registration is import-time, and re-running the data
tool is a no-op (§9.5).

### 10.6 UI

`frontend/opsgrids-client` and `frontend/product-crm` (registry `G16`); **the SPA is published last, by the owner** (§10.8):

- `AccessClaims` (`opsgrids-client/src/auth/token.ts:13-27`) gains `mem?: string | undefined` (the claim already travels:
  `services/auth/app/tokens.py:108-109`).
- `getMySubjectRef()` (`product-crm/src/lib/subject.ts:21`) keeps its name, reads `mem`, and its fixture identity returns the fixture rep's
  `membership_id`; its callers `routes/Overview.tsx:164` ("My pipeline") and `data/importExport/shared.ts:233` (`ownerIdOrSelf`) follow.
  `product-hrms/src/lib/subject.ts`, `worker-app` and the dashboard's `EmployeeWorkspace` keep reading `sref` (the employee link).
- **The owner picker**: the one-click claim posts `{membership_id: <the caller's own>}` (from `MeOut.membership_id`,
  `opsgrids-client/src/auth/types.ts:62`); the free-form `OwnerFormDialog` is replaced by an "Add a team member" dialog that lists
  `GET /customers/owners/candidates` and posts `{membership_id, role?}`. States: loading, empty ("Everyone in your organization is
  already on the team"), error, and no-permission (the control is not shown without `crm.book.owner.write`; the server still decides).
- `frontend/opsgrids-visual/fixtures/api/crm/me.json` carries `membership_id`; the dialog gets a story; any pixel change is a reviewed
  `visual:update` baseline; the token baseline does not grow.

### 10.7 Tests, and the security review D-9 requires

**Tests.** Every OWN test of the three services moves from `subject_ref=` to `membership_id=` (the subject becomes a parameter of
`ctx_for`); the two tests that say they pin today's meaning change on purpose: `lead/tests/test_authz.py:528-575`
`TestRevealOwnScopeIsKeyedOnSubjectRef` becomes `TestRevealOwnScopeIsKeyedOnMembershipId`, and
`customer/tests/test_contact_phone_pii.py:383-395` `test_an_own_grant_with_no_subject_ref_reveals_nothing` becomes
`test_an_own_grant_with_no_membership_id_reveals_nothing`. New, per service in its own process:

- **`test_access_matrix.py`** (one per service; DD-57): a table-driven test that first **walks the service's live route table**
  (`app.routes`: every public route with its permission and scope floor, and every `/internal/v1` route with its caller allow-list and
  whether it demands the organization claim) and **fails on any route the matrix below does not list**, then asserts the **after**
  column for every listed public route, for three principals (a `CRM_REP` with `mem` only; a rep with `mem` **and** an unrelated
  `sref`; a principal with neither), against rows owned by the caller, by a colleague, and by a different organization's member of the
  same id value. The count is not the guard: it is 16, 9 and 8 public routes at `HEAD` and 17, 10 to 14 and 8 on the day this release
  deploys (rows 34-39), and the walk is what keeps the review honest when the next release adds one.
- **Negative-authz, one test per OWN-sensitive route** (the 14 marked `●` below): `test_<route>_refuses_a_colleagues_row` (403, or 404
  where the route hides existence), `test_<route>_refuses_a_principal_with_no_membership_id`, and, where a body names an owner,
  `test_<route>_refuses_assigning_to_a_colleague`. **Every ORG-floored route** in the three apps (`POST` and `PATCH /customers/owners`,
  `GET /customers/owners/candidates`, `PUT /leads/assignment-rule` and, when R2.5 has deployed, the four stage routes) keeps its
  `test_<route>_refuses_an_own_grant`.
- **Cross-tenant** (each service): `test_a_membership_id_that_belongs_to_another_organization_matches_nothing` (organization B's rows
  carry organization B's member ids; a principal of organization A presenting any id sees none of B's rows, and presenting B's id sees
  none either: RLS and the organization filter both hold) and `test_the_same_id_value_in_two_organizations_never_crosses`.
- **Reveal** (customer `POST /contacts/{id}/phone:reveal`, lead `POST /leads/{id}:reveal`): own row with step-up succeeds and writes
  one `entity.sensitive_read` audit row per reveal; a colleague's row is refused and logged `out_of_scope`; no step-up is refused; the
  budget (30 per 10 minutes, 200 per day) is spent only by a request that would reveal; none of the existing reveal tests is weakened.
- **Skew and rollback (fail closed)**: `test_a_service_still_on_the_employee_link_matches_nothing_for_membership_valued_rows` (the resolvers are
  registered with the employee link while the rows carry membership ids: every OWN list is empty and every OWN object check is 403, for every
  principal, so a partial deploy or a code rollback narrows access and never widens it).
- **Defaults and `owner=me`**: `test_a_deal_defaults_to_own_ref`, `test_an_activity_by_an_own_caller_is_forced_to_own_ref`,
  `test_owner_me_with_no_membership_id_is_the_all_zero_rollup`, `test_a_caller_with_no_own_ref_gets_422_not_an_employee_id`.
- **Roster**: `test_free_form_roster_creation_is_422_membership_required`, `test_the_self_claim_with_my_own_membership_id_succeeds_for_an_admin`,
  `test_a_legacy_roster_id_is_not_assignable`, `test_owners_validate_marks_a_legacy_roster_id_invalid_and_a_membership_id_valid`.
- **Static**: `tests/infra/test_no_raw_subject_ref.py` with `OPTED_IN` filled is green (§5.8); a test that the three registrations pass
  `OwnerSubject.MEMBERSHIP`.
- **Seeds and e2e**: `tools/demo/seed_crm.py` and `e2e/_seed_crm.py` (`:82`, `:124-135`) create reps through the activation path so their
  owner ids are membership ids a token can match (no random uuid that merely looks like one); `e2e/edge_product_crud.py:337-350` is
  **rewritten**: it reads `GET /customers/owners/candidates`, activates a member with `POST /customers/owners {membership_id}`, and uses
  that **membership id** as `owner_id` for the company, contact and deal. The owner runs it as the post-deploy verify step (§10.8); an
  agent never does. `tests/infra` gains `test_seeds_never_invent_owner_ids.py`, an AST scan of the two seed files. **It cannot be a
  scan for `uuid4()`: neither seed uses one.** Both derive every id, owner ids included, with `uuid.uuid5` (`demo_id`,
  `tools/demo/seed_crm.py:51-54`; `seed_id`, `e2e/_seed_crm.py:42-44`) and pass them as `owner_id=`
  (`seed_crm.py:1022-1024,1053,1074,1121,1147`; `_seed_crm.py:110,116,129`), so a `uuid4` rule would pass before and after this
  release and enforce nothing; those deterministic look-alikes are exactly the "fake membership-looking ids" registry `H7` rules out.
  The scan therefore fails on any `owner_id=` or `owner_ids=` argument whose expression, or a local name assigned from it in the
  same function, contains a call to `demo_id`, `seed_id`, `uuid.uuid5`, `uuid.uuid4` or `uuid.UUID(...)`, or a literal id; it
  passes only an expression rooted in the result of the activation helper (`create_owner(..., membership_id=…)` of §9.3, or the
  `membership_id` of the member the seed was given) and `None`. Self-tests: `test_a_uuid5_flow_into_owner_id_is_flagged` (a snippet
  binds `x = uuid.uuid5(...)` and calls with `owner_id=x`), `test_demo_id_and_seed_id_calls_are_flagged`, `test_a_uuid4_is_flagged`,
  `test_a_membership_id_parameter_and_an_activation_result_pass`, `test_the_scan_is_not_vacuous` (each seed file yields at least
  one `owner_id=` argument).
- **SPA** (`vitest`): `subject.test.ts` (`getMySubjectRef` reads `mem`, never `sref`), `Overview` "My pipeline", the importer's self-owner
  rule, the dialog's four states, `AccessClaims` parsing with and without `mem`; the visual fixture contract test; `make ui-visual`.

**The access matrix.** Every public route of the three apps (the three `*/info` routes carry no permission and are out of scope): the 33
that exist at `HEAD` (customer 16 including the 360, deal 9, lead 8; rows 1-33), plus the six that R2.3, R2.5 and R2.6 add (rows 34-39:
`GET /customers/owners/candidates` from R2.6, which R2.7 needs; `GET /deals/stages` from R2.3, which R2.6 needs; and the four stage routes
from R2.5, which exist only **if R2.5 has merged**, since R2.7 does not need it), so **35 to 39** on the day R2.7 deploys, and the matrix
test walks the route tables rather than trusting this number (DD-57). *Before* and *after* describe the default `CRM_REP` (reads ORG; writes, convert and reveal OWN) with `mem` and
no `sref`. `●` marks the 14 OWN-sensitive routes the owner signs for (none of rows 34-39 is one). "Own" means `owner_id` equals the caller's
membership id.

| # | Route | Permission, floor | What the scope decides | Before (CRM-only rep) | After (CRM-only rep) |
|---|---|---|---|---|---|
| 1 | `GET /customers` | `crm.book.company.read` | list filter, only for an OWN grant | ORG read: all rows | unchanged; a custom OWN grant sees own rows |
| 2 ● | `POST /customers` | `crm.book.company.write` | assign check: OWN may assign only to self | 403: no subject | allowed with `owner_id` = own membership of an activated rep; any other owner 403 |
| 3 | `GET /customers/owners` | `crm.book.company.read` | none: reference data | unchanged | unchanged (adds `membership_id`) |
| 4 | `POST /customers/owners` | `crm.book.owner.write`, ORG | none | admin only, free-form | admin only; 422 `membership_required` without a member |
| 5 | `PATCH /customers/owners/{id}` | `crm.book.owner.write`, ORG | none | admin only | unchanged |
| 6 | `GET /customers/{company_id}` | `crm.book.company.read` | object check | ORG read | unchanged |
| 7 ● | `PATCH /customers/{company_id}` | `crm.book.company.write` | object check; assign check | 403 | own companies only; reassign only to self |
| 8 | `DELETE /customers/{company_id}` | `crm.book.company.delete` | object check | not held by `CRM_REP`: 403 | unchanged |
| 9 | `GET /contacts` | `crm.book.contact.read` | list filter, OWN grant only | ORG read | unchanged |
| 10 ● | `POST /contacts` | `crm.book.contact.write` | assign check | 403 | own contacts; owner must be self |
| 11 | `GET /contacts/{contact_id}` | `crm.book.contact.read` | object check | ORG read | unchanged |
| 12 ● | `PATCH /contacts/{contact_id}` | `crm.book.contact.write` | object check; assign check | 403 | own contacts only |
| 13 | `DELETE /contacts/{contact_id}` | `crm.book.contact.delete` | object check | not held: 403 | unchanged |
| 14 ● | `PUT /contacts/{contact_id}/phone` | `crm.book.contact.write` | object check | 403 | own contacts only; returns the masked form |
| 15 ● | `POST /contacts/{contact_id}/phone:reveal` | `crm.book.contact.read_sensitive` + step-up + budget | object check inside the service | refused, logged `out_of_scope` | own contacts only, step-up, one audit row per reveal, 30 per 10 minutes and 200 per day |
| 16 | `GET /customers/{contact_id}/360` | `crm.book.contact.read` + `Entitled` | object check | ORG read | unchanged |
| 17 | `GET /deals` | `crm.pipeline.deal.read` | list filter, OWN grant only | ORG read | unchanged |
| 18 ● | `POST /deals` | `crm.pipeline.deal.write` | default owner; assign check | 422 (no owner) or 403 | owner defaults to own membership; any other owner 403 |
| 19 | `GET /deals/summary` | `crm.pipeline.deal.read` | `owner=me` (and the list filter for an OWN grant) | `owner=me` is the all-zero rollup | `owner=me` is the caller's own numbers; `owner_id` still narrows only |
| 20 | `GET /deals/activities` | `crm.pipeline.activity.read` | list filter, OWN grant only | ORG read | unchanged (historical rows keep legacy ids, named through the roster) |
| 21 ● | `POST /deals/activities` | `crm.pipeline.activity.write` | owner forced to self for OWN | 422 (no owner) | owner forced to own membership; a deal outside scope 403 |
| 22 | `GET /deals/{deal_id}` | `crm.pipeline.deal.read` | object check | ORG read | unchanged |
| 23 ● | `PATCH /deals/{deal_id}` | `crm.pipeline.deal.write` | object check; reassign check | 403 | own deals only; reassign only to self |
| 24 | `DELETE /deals/{deal_id}` | `crm.pipeline.deal.delete` | object check | not held: 403 | unchanged |
| 25 ● | `POST /deals/{deal_id}/stage` | `crm.pipeline.deal.write` | object check | 403 | own deals only |
| 26 | `GET /leads/assignment-rule` | `crm.lead.lead.read` | none | ORG read | unchanged |
| 27 | `PUT /leads/assignment-rule` | `crm.lead.lead.write` + service-level ORG check | ORG check | refused for OWN | unchanged; elements must be activated membership ids |
| 28 | `GET /leads` | `crm.lead.lead.read` | list filter, OWN grant only | ORG read | unchanged |
| 29 ● | `POST /leads` | `crm.lead.lead.write` | named owner: assign check; omitted: round robin | omitted works; named 403 | omitted works; named = self only, an active rep |
| 30 | `GET /leads/{lead_id}` | `crm.lead.lead.read` | object check | ORG read | unchanged |
| 31 ● | `PATCH /leads/{lead_id}` | `crm.lead.lead.write` | object check under lock; reassign check | 403 | own leads only |
| 32 ● | `POST /leads/{lead_id}:convert` | `crm.lead.lead.convert` | claim re-asserts | 403 | own leads only; the lead's owner must be an activated rep, so a lead still owned by an unmapped legacy roster id answers the existing `OwnerUnknown` rejection to every caller, an ORG admin included, until it is reassigned (§10.4.5) |
| 33 ● | `POST /leads/{lead_id}:reveal` | `crm.lead.lead.read_sensitive` + step-up + budget | object check inside the service | refused, logged `out_of_scope` | own leads only, step-up, audited, shared budget |
| 34 | `GET /customers/owners/candidates` (R2.6) | `crm.book.owner.write`, ORG floor | none: the account read is the data | new in R2.6: admin only | unchanged; admin only, 403 for an OWN grant |
| 35 | `GET /deals/stages` (R2.3) | `crm.pipeline.deal.read`, no floor (stage rows are tenant configuration: DD-53) | none on the list; `include=counts` only for an ORG grant | new in R2.3: any reader; counts ORG only | unchanged |
| 36 | `POST /deals/stages` (R2.5, if merged) | `crm.pipeline.stage.write`, ORG floor | none | new in R2.5: admin only | unchanged; 403 for an OWN grant |
| 37 | `PATCH /deals/stages/{stage_id}` (R2.5, if merged) | `crm.pipeline.stage.write`, ORG floor | none | new in R2.5: admin only | unchanged |
| 38 | `POST /deals/stages:reorder` (R2.5, if merged) | `crm.pipeline.stage.write`, ORG floor | none | new in R2.5: admin only | unchanged |
| 39 | `POST /deals/stages/{stage_id}:archive` (R2.5, if merged) | `crm.pipeline.stage.write`, ORG floor | none | new in R2.5: admin only | unchanged |

**Internal routes** (service token, not scope; the review reads their allow-lists, and the matrix test lists the same set from each app's
route table). Customer: `GET .../contacts/{contact_id}/messaging-target` and `POST .../contacts:resolve` carry **no** caller allow-list and
no organization-claim check today (registry `C45` and `C46` add them in R3.1); `GET .../contacts/{contact_id}` allows `finance`;
`POST .../leads:convert` and `POST .../owners:validate` allow `lead` and demand the organization claim (`owners:validate` answers
membership ids only after this release). Deal: `POST .../leads:convert` allows `lead` with the organization claim, and R2.4's
`GET .../references` allows `customer` with the organization claim. None is changed by R2.7 except the meaning of `owners:validate`.

For a rep who **has** an employee link (an HR tenant), *before* OWN matched rows whose owner id equalled that employee id (deals and
activities default to it) and nothing else; *after* it matches membership-keyed rows, which is what `apply` produced. The two sets
coincide only for rows `apply` rewrote by `link` evidence.

**The security review (D-9).** The owner signs each line. The signature is one line under **Sign-offs** in the ADR of registry `I1`
(`docs/adr/0038-crm-programme-decisions.md`), `D-9: approved on <date> against checklist S-1 to S-12 of the R2.7 spec`, which is
evidence and never the answer; the answer is the State cell of the D-9 row, which
`grep -E '^\| D-9 \|.*\| approved [0-9]{4}-[0-9]{2}-[0-9]{2} \|$' docs/adr/0038-crm-programme-decisions.md` prints (§2.1). The
plan's rollout task is blocked until both exist:

| # | Check | Evidence |
|---|---|---|
| S-1 | Every route the three apps serve on the day of the deploy, public and internal, is in the matrix (the test walks the route tables and fails on an unlisted one: DD-57), and every `●` route has a named negative-authz test | `test_access_matrix.py` (green with its route-table walk) and the 14 per-route tests |
| S-2 | The widening is exactly "OWN write, convert and reveal on rows owned by the caller's membership id"; no ORG-floored route changed | rows 4, 5, 27 and 34-39 unchanged; no template changed in this release |
| S-3 | No route lets an OWN caller assign a row to someone else | the "refuses assigning to a colleague" tests of routes 2, 7, 10, 12, 18, 21, 23, 29, 31 |
| S-4 | Fail-closed: no `mem` matches nothing; skew and rollback narrow, never widen | the no-`membership_id` tests; §10.4 rule 4 |
| S-5 | `mem` cannot be chosen by the client | auth mints it from the membership (`services/auth/app/tokens.py:108-109`); the gateway strips inbound `X-Membership-Id` and `X-Subject-Ref` (`gateway.py:97-117`); the hop token binds `uid` and `org` only, a risk shared with `X-Perms` and recorded as DD-9 |
| S-6 | Cross-tenant: ids cannot cross organizations | RLS plus the organization filter; the per-service cross-tenant tests |
| S-7 | Reveal keeps its step-up, budget and per-reveal audit row; D-25 keeps managers at OWN | the reveal tests; §11.3 |
| S-8 | Rows still carrying a legacy id are not widened, and the number of them is known and accepted | `verify` output with the strays (zero) and the legacy counts (`legacy_rows_remaining` per table, `unconvertible_leads`, `unnamed`) recorded, each non-zero one mapped, reassigned or accepted in writing; the fail-closed rule |
| S-9 | The data step is rehearsed and reversible | `revert` rehearsed on a copy; the journal exists in all three schemas |
| S-10 | No fake ids in seeds or smokes | `test_seeds_never_invent_owner_ids.py`, which flags any `demo_id`, `seed_id`, `uuid5` or `uuid4` value reaching an `owner_id=` argument (its self-test proves a `uuid5` flow is caught), and the rewritten smoke, which posts no `{full_name, email}` alone |
| S-11 | The one-click claim cannot be used to make someone else an owner | the claim posts the caller's own `membership_id`; `POST /customers/owners` verifies it against account |
| S-12 | The undo is known and tested | §10.9, and the skew and rollback fail-closed test |

### 10.8 Rollout

**Preconditions (all owner-recorded):** R2.2 is deployed; R2.6 is deployed and its SPA published; the census and plan have been read; D-7, D-8 and
D-9 are answered and the security review above is signed; a database backup exists (`make edge-backup`); a fresh `plan` shows
no unexpected `conflict`.

**Order: data before code, the SPA last** (registry `H1`, `G16`). Run steps 1 to 3 in one sitting: between the data step and the deploy, a
rep with an employee link cannot OWN-edit their rewritten rows with the old code (fail closed), which is the cost of this order.

1. **Owner, data step.** Re-run `plan` (the roster may have moved), review and accept rows through `SUB=show`, `SUB=accept` and `SUB=link` (§9.8.5), then
   per service, each preceded by its dry run so the owner has the
   count for `--confirm-count`: `make edge-map-crm-owners … SUB=apply ARGS="--service customer --plan plan.json …"`, then `deal`, then `lead`. Then
   `SUB=verify`: **zero strays** (the output is recorded) **and the legacy counts recorded** (§9.4, DD-56): `legacy_rows_remaining` per table,
   `unconvertible_leads` and `unnamed` members, each non-zero one mapped (`link`, `accept`, a re-run), reassigned or accepted in writing before the
   deploy, because after the flip a lead still owned by a legacy roster id cannot be converted by anyone (§10.4.5); `unnamed` members are activated or
   accepted; skipped leads are re-run after their lease.
2. Review and owner squash-merge of R2.7; `make gate` locally.
3. **Owner, deploy**: `make deploy HOST=… CONFIRM=… REF=<sha>`. The diff touches `services/{customer,deal,lead}`, the frontend and the e2e files, not
   `libs/` (other than the contracts directory), so **only the three CRM images rebuild**, and they recreate together. No migration runs.
4. **Owner, verify**: `make edge-status`; then `e2e/edge_product_crud.py` (the rewritten smoke) and one OWN-rep session check: `GET /deals/summary?owner=me`
   returns that rep's own numbers, a colleague's deal `PATCH` answers 403, their own answers 200.
5. **Owner, publish the SPA last**: check out the merged `main` first (the bundle is built from the local checkout), `make edge-web HOST=… CONFIRM=… APP=crm`,
   then grep the published bundle for the new dialog's candidates path to prove the right tree was built.
6. **Owner, catch-up**: `SUB=apply` again and `SUB=verify` once more for rows written between steps 1 and 3; record the final counts,
   legacy counts included.
7. **Owner, destroy the plan**: once the final `verify` is recorded, `SUB=forget-plan ARGS="--all"` removes `plan.json` (the roster emails) from the box;
   the journal, the run files and `manual-pairs.json` stay for `revert`.
8. Memory: no container changes; the figures stay at R2.4's (crm 2068, ten profiles 3748, ten plus `admin` 3945).
9. Docs: `docs/crm.md`, `docs/architecture.md` (scope paragraph), the RUNBOOK tool section, register row 135 (C15) and row 20.

### 10.9 Rollback

- **Code**: `make edge-rollback HOST=… CONFIRM=…` for the three CRM images together (a partial rollback is the skew of §10.4 rule 4). The data
  stays membership-valued, so after a code rollback **OWN writes are refused** for reps with an employee link (they were matched by employee id
  before) and still refused for CRM-only reps; **nothing widens**; reads at ORG are unaffected. To restore the old OWN matches run `revert` in
  the reverse order of `apply` (lead, deal, customer), each with its dry run and three gates, using the `run_id` of the apply.
- **SPA**: `make edge-web-rollback HOST=… CONFIRM=… APP=crm` (one symlink); the R2.6 SPA still resolves both ids.
- **Stop conditions**: any OWN rep unable to edit their own row after `verify` was clean; any edit of a colleague's row succeeding (this
  would be a defect and an incident: roll back first); any 5xx on the OWN-sensitive routes; the stray count rising after the SPA publish.

### 10.10 Acceptance

- **AC-2.7-1** The three registrations pass `OwnerSubject.MEMBERSHIP`; `OPTED_IN` equals `{customer, deal, lead}`; the six-read ratchet is gone and the string allow-list stays; the static guard passes with no raw `subject_ref` read, the one allow-listed `"X-Subject-Ref"` spelling as the only string hit, ORG-only hand-built contexts and a `crm.` permission in every `require(...)` argument, `REVEAL_PERMISSION` included (tests).
- **AC-2.7-2** Each of the six sites of §5.5 reads `ctx.own_ref`, each pinned by its existing test moved to membership terms (tests).
- **AC-2.7-3** In each service a custom OWN list grant returns exactly the rows whose `owner_id` equals the caller's membership id and none for a principal with none (tests).
- **AC-2.7-4** Each of the 14 `●` routes has tests proving a colleague's row is refused, the caller's own row is allowed, and a missing `membership_id` matches nothing (tests).
- **AC-2.7-5** Every ORG-floored route in the three apps (`POST` and `PATCH /customers/owners`, `GET /customers/owners/candidates`, `PUT /leads/assignment-rule` and, if R2.5 has deployed, the four stage routes) refuses an OWN grant; no ORG-floored route changed (tests).
- **AC-2.7-6** A membership id of another organization, and the same id value in two organizations, never crosses tenants, in all three services (tests).
- **AC-2.7-7** Own-row reveal with step-up succeeds and writes one audit row each time; a colleague's row and a missing step-up are refused; the budget behaves (tests).
- **AC-2.7-8** `owner_id` defaults and `owner=me` use `own_ref`; a caller with none gets 422 (tests).
- **AC-2.7-9** Free-form roster creation is 422 `membership_required`; the self-claim with the caller's own membership succeeds for an admin; assignment and `owners:validate` are membership-only (tests).
- **AC-2.7-10** `test_access_matrix.py` in each service walks that app's live route table, fails on any public or internal route the matrix does not list, and asserts the after-column for every listed public route (tests); at `HEAD` that is 16, 9 and 8 public routes, and on the deploy day the rows 34-39 are in as well.
- **AC-2.7-11** Seeds create reps through activation, no seed derives an owner id (static test: any `demo_id`, `seed_id`, `uuid5` or `uuid4` value reaching an `owner_id=` argument is flagged, with a `uuid5`-flow self-test), and `e2e/edge_product_crud.py` uses the activation path (grep: it no longer posts `{full_name, email}` alone).
- **AC-2.7-12** `getMySubjectRef()` reads `mem`; `AccessClaims` carries it; the dialog has its four states; the visual fixture contract test passes; `make ui-visual` shows only reviewed baseline changes (tests).
- **AC-2.7-13** The security review S-1 to S-12 is signed (one Sign-offs line in `docs/adr/0038-crm-programme-decisions.md`: `D-9: approved on <date> against checklist S-1 to S-12 of the R2.7 spec`) and D-9 is approved (`grep -E '^\| D-9 \|.*\| approved [0-9]{4}-[0-9]{2}-[0-9]{2} \|$' docs/adr/0038-crm-programme-decisions.md` prints its row).
- **AC-2.7-14** The owner has recorded `verify` output with zero strays per service before the deploy and again after the SPA publish, together with the per-table `legacy_rows_remaining`, the `unconvertible_leads` and the `unnamed` counts and, for each non-zero one, what was done (mapped, reassigned or accepted in writing), and has run `forget-plan` afterwards (an owner-run check; the plan's rollout task is blocked on it).

## 11. R2.8 — Role consolidation and the permission rollout

### 11.1 Purpose

Replace six confusing role templates (three admins, and three reps that are all named "Sales representative") with **four legible roles** that
every organization can assign: `CRM_ADMIN`, `CRM_MANAGER`, `CRM_REP`, `CRM_VIEWER` (decision D-20, registry §E.2). Each of customer, deal and lead
declares all four, each with only its own slice; the exporter unions them. The release also catalogues the four codes the later releases
need (`E19`-`E22`) **admin-first**, so they reach every live tenant through one reviewed `make edge-reconcile` instead of arriving after the routes
that need them. It changes no route and no scope check. It does not rename, narrow or remove anything: the 17 codes and the six legacy role
codes are customer data and stay exactly as they are (D-20; there is no rename mechanism and no revoke tool, ADR-0007).

### 11.2 Data model

None: no migration, no table. The data this release moves is **catalogue data**: three `app/catalogue.py` files, the regenerated
`libs/contracts/permissions/crm.json` (bind-mounted on the box, so it is live at checkout: registry §E.4 step 4), and, per organization, role and
grant rows that `account`'s installer writes when the owner runs the reconcile.

### 11.3 Backend contract: the catalogue, the four templates, the manifest

**New permission codes** (admin-first; their routes ship later, so the codes are inert until then). Each service adds its own:

| Service | `PermissionSpec` | Code | Default / max scope | Dangerous | Route floor when it ships |
|---|---|---|---|---|---|
| deal | `("pipeline", "settings", "write", "Change pipeline settings and win/loss reasons", "ORG", "ORG")` | `crm.pipeline.settings.write` (E19) | ORG / ORG | no | ORG (R3.4) |
| deal | `("pipeline", "follow_up", "read", "See follow-ups", "OWN")` | `crm.pipeline.follow_up.read` (E20) | OWN / ORG | no | none (R3.6) |
| deal | `("pipeline", "follow_up", "write", "Create, reschedule, complete and cancel follow-ups", "OWN")` | `crm.pipeline.follow_up.write` (E21) | OWN / ORG | no | none (R3.6) |
| lead | `("lead", "settings", "write", "Configure lead intake (capture endpoints, SLA, aging) and erase a captured lead", "ORG", "ORG")` | `crm.lead.settings.write` (E22) | ORG / ORG | no | ORG (R3.7a for C40 and C108, R3.7b for C39) |

With R2.5's `crm.pipeline.stage.write` (E18) the CRM manifest holds **22 permission codes**: customer 8, deal 9, lead 5.
The lead description is the registry's wording for `E22`, which also gates the administrator's per-lead erase (`C108`, SP3/R3.7a,
behind a step-up on that route only), so the text an owner reads when granting the code already names the erase. A permission
code has one owning service, so the rule below that a template's `name` and `is_default` agree across the three declarations is
unaffected.

**The four templates.** The `name` and `is_default` of one code must be identical in all three declarations or the exporter exits
(`tools/export_permissions.py:116-137`). Names and defaults:

| Code | `name` | `is_default` |
|---|---|---|
| `CRM_ADMIN` | CRM administrator | false |
| `CRM_MANAGER` | CRM sales manager | false |
| `CRM_REP` | CRM sales representative | **true** |
| `CRM_VIEWER` | CRM viewer | false |

**Exact grants per template, per service slice** (scope after each code; "all" is `CATALOGUE.all("ORG")`, which follows each slice as it grows). A
manager's `company.write`, `contact.write`, `deal.write`, `activity.write`, `lead.write` and `lead.convert` enter as **OWN** (DD-36, registry `†`: the
R3.8 reconcile widens them to TEAM, because `ScopeContext.apply` refuses a TEAM grant while a selector passes no `team_col`, `auth.py:342-347`);
the two reveals are **OWN** for managers (registry `‡`, pending D-25: a manager reveals only their own rows):

| Slice | `CRM_ADMIN` | `CRM_MANAGER` | `CRM_REP` | `CRM_VIEWER` |
|---|---|---|---|---|
| customer (8 codes) | all 8 at ORG | company.read ORG, company.write OWN, contact.read ORG, contact.write OWN, contact.read_sensitive OWN | the same five | company.read ORG, contact.read ORG |
| deal (9 codes) | all 9 at ORG | deal.read ORG, deal.write OWN, activity.read ORG, activity.write OWN | the same four | deal.read ORG, activity.read ORG |
| lead (5 codes) | all 5 at ORG | lead.read ORG, lead.write OWN, lead.convert OWN, lead.read_sensitive OWN | the same four | lead.read ORG |
| **merged** | **22 at ORG** | **13** | **13** (exactly the union of the three legacy rep templates) | **5** |

So at R2.8 `CRM_MANAGER` and `CRM_REP` hold the same grants; they differ by name and by what R3.8 will widen. `CRM_VIEWER` holds no write, no
convert, no reveal and nothing commercial. None of the four holds `crm.book.company.delete`, `crm.book.contact.delete`, `crm.pipeline.deal.delete`,
`crm.book.owner.write`, `crm.pipeline.stage.write`, `crm.pipeline.settings.write`, `crm.lead.settings.write` except `CRM_ADMIN`. The follow-up codes
join `CRM_MANAGER` and `CRM_REP` "after R3.6 ships its routes" (registry §E.2), not here.

**Descriptions** (DD-35: one sentence per slice; the exporter appends a service's text to the merged description unless it is already contained,
`export_permissions.py:123-124`, so the three read as one paragraph in the order customer, deal, lead):

| Code | customer sentence | deal sentence | lead sentence |
|---|---|---|---|
| `CRM_ADMIN` | Administers the account book and the rep directory across the organization. | Administers the pipeline, its stages and settings, follow-ups and the activity timeline. | Administers lead intake, its settings and the assignment rule. |
| `CRM_MANAGER` | Reads the whole book and maintains the companies and contacts they own. | Works the shared pipeline and the deals and activities they own. | Works and converts the leads they own. |
| `CRM_REP` | Every person with a CRM seat reads the whole book and maintains the companies and contacts they own. | Reads the whole pipeline and moves and logs against the deals they own. | Reads every lead and works and converts the leads they own. |
| `CRM_VIEWER` | Reads the account book and changes nothing. | Reads the pipeline and the activity timeline and changes nothing. | Reads leads and changes nothing. |

**Legacy templates, frozen** (registry §E.3, D-20): `CRM_BOOK_ADMIN`, `CRM_PIPELINE_ADMIN`, `CRM_LEADS_ADMIN`, `CRM_SALES_REP`, `CRM_DEAL_REP`,
`CRM_LEAD_REP` stay declared with **code, name and description unchanged**. The three legacy reps keep their explicit `pick(...)` lists and never gain a
code; they lose `is_default` (set false) in the same commit that sets it true on `CRM_REP`. The three legacy admins keep `.all("ORG")`, so they widen
with their own slice (an existing admin can configure stages and settings without being moved to `CRM_ADMIN`) and nothing narrows. The legacy
mapping, recorded in `docs/crm.md` and the ADR and **not** as runtime code: the three admins map to `CRM_ADMIN`, the three reps to `CRM_REP`.

**What the exporter produces** (`python tools/export_permissions.py` through `make openapi`; `--check` is a row of the gate): `libs/contracts/permissions/crm.json`
with `sources: [customer, deal, lead]`, 22 permissions, **ten** role templates, `is_default` true on `CRM_REP` only, and `is_admin` true on `CRM_ADMIN` only.
That last fact is a **consequence to understand, not an accident**: `_flag_admin` marks, per contributing service, the template with a strictly greater
grant count (`export_permissions.py:224-249`), and the merged `CRM_ADMIN` (22) outranks every legacy admin (9 at most) for **all three** services, so the three
legacy admins stop being flagged. Flagged templates are what owners are granted on activation and on reconcile.

### 11.4 Domain rules, and what the reconcile does to a live tenant

1. **Same-code merging** is by union, wider scope wins; `name`, `is_default`, `cross_product` must agree; `cross_product` is refused outright
   (`export_permissions.py:191-221`). A service that forgets to declare one of the four leaves a legacy admin flagged for that service; a test prevents it (§11.8).
2. **A new activation** (after checkout) installs all ten roles, grants `CRM_ADMIN` to the organization's active owners (the only flagged template), and
   hands a new member exactly the `is_default` roles: `CRM_REP` (`_default_role_ids`, `account/app/services.py:575-587`).
3. **The reconcile** (`reconcile_registration`, `account/app/services.py:2284-2352`; `tools/reconcile_permissions.py`) in an organization that activated CRM
   earlier, **union only, nothing removed or narrowed** (ADR-0007 §1):
   - creates the four missing roles with the template's `is_default` (so the new `CRM_REP` is created `is_default = true`);
   - widens the three legacy admin roles with the new codes of their slice (`CRM_PIPELINE_ADMIN` gains E19-E21, plus E18 if R2.5's reconcile has not run;
     `CRM_LEADS_ADMIN` gains E22; `CRM_BOOK_ADMIN` gains nothing);
   - grants `CRM_ADMIN` to each **active owner** (the flagged template), through `grant_admin_roles_to_owners`, skipping an owner who already holds it;
   - **never** edits an existing role's name, description or `is_default`, never touches a customer-authored role (it is listed as `skipped_customer_roles`), and
     grants nothing automatically to anyone but owners (no auto-grant, ADR-0007 §4).
4. **What is a "change"** (`changed = diff.changed or bool(admin_grant.membership_ids)`, `:2284`): creating roles alone is **not** a change and bumps nothing;
   adding a grant, widening a scope, or granting `CRM_ADMIN` to an owner is: it writes the `permissions.reconciled` audit row, publishes `core.grants.changed`
   (per organization, and per owner granted), and bumps the organization's permission version `pv` (`_invalidate_grants`; a Redis outage fails that
   organization loudly and rolls it back). The dry run therefore shows `roles_created` for every organization and a `pv` bump only where grants widen or an owner receives `CRM_ADMIN`.
5. **Pv burst.** A `pv` bump makes every signed-in member of that organization refresh their token on their next request. The fleet run is sequential, one
   transaction per organization, with `--pace 250` ms between organizations (`tools/reconcile_permissions.py:75-82`, ADR-0007 §5); it stops at the first
   organization that fails, leaving earlier ones committed; a clean re-run computes an empty diff. Run it off-peak.
6. **Lag, in four places** (state them so nobody is surprised): (a) between checkout and the reconcile a live organization has none of the new roles; (b) a
   customer-authored role never receives a code, ever (the owner edits it by hand); (c) tokens refresh lazily after a `pv` bump; (d) **default roles in a live
   tenant** (a known consequence, inference from `install_registration`'s "never updates `is_default`"): the three legacy rep roles stay `is_default = true` there
   and the new `CRM_REP` is created `is_default = true`, so a **new member of a reconciled tenant receives four rep roles** (three named "Sales representative"
   and "CRM sales representative"), the union of which equals `CRM_REP`. It is harmless to access and untidy to look at. Correcting it needs a reviewed, bespoke
   one-off script (the ADR-0007 pattern for anything the reconcile does not do); that script is **not built in this programme** (§15), and a test pins today's
   behaviour so a later fix is a conscious change (§11.8).
7. **Frozen** (D-20): the 17 permission codes, their default and max scopes and dangerous flags, and the six legacy role codes with their names, descriptions and
   grant lists. Tests hold frozen copies.

### 11.5 Consumers and idempotency

`account`'s `install_registration_on_activation` consumer (`core.product.activated` and entitling subscription events) installs the manifest for a newly
activated organization and is idempotent (`install_registration` upserts; `processed_events`). The reconcile is idempotent by construction: a second run
computes an empty diff and bumps nothing. `core.grants.changed` consumers (the gateways' permission caches) are existing and unchanged.

### 11.6 UI

None ships. The admin console already lists roles by name and lets an owner assign them; the four new roles appear there after the reconcile. No SPA control
uses the new codes yet, so no `useCan` change and no fixture grant change is needed (registry §E.4 step 5 applies when R3.4, R3.6 and R3.7 ship their screens).

### 11.7 Plan packaging (D-21)

**The question.** The CRM product's commercial plans are outside the permission model: ADR-0035 left one open (`docs/adr/0035-list-prices-2026-10-06.md:40,167-168`)
— the per-seat `crm/growth` plan (INR 299) and the suite bundles were not repriced, and the owner was asked to confirm that leaving them is intended. Four role
templates, one of them a read-only `CRM_VIEWER`, make "what is a seat" a visible question in the same release.

**The default and what this release does about it.** D-21's default is: **stay flat**; fix or retire the per-seat `crm/growth` plan by a **staff action in the
admin console**; **no code in this programme**, no seat enforcement, no entitlement change, no plan table change, no new product key (P§5). R2.8 therefore
contains no packaging change at all. It is **blocked on the owner's answer** (the plan's rollout task for R2.8 does not start until the answer is recorded in
`docs/adr/0038-crm-programme-decisions.md`, registry `I1`), because shipping a "viewer" role beside an unresolved per-seat plan invites a billing
question the platform cannot yet answer. If the owner answers otherwise (a seat-priced viewer tier, say), that is a separate, larger project and R2.8 is unaffected
in code.

**Acceptance for this part** is a recorded decision, not a test: the State cell of the D-21 row in `docs/adr/0038-...` reads `approved` or
`amended` (the command of §2.1 with `(approved|amended)`) and, if the answer is "retire or fix", one line under **Sign-offs** names the staff
action taken, `D-21: <the staff action> on <date>`. The agent never performs the staff action.

### 11.8 Tests

No endpoint is added, so there is no new negative-authz route test; the access tests that matter are the grant sets themselves (a viewer holds no write).

**`tests/infra/test_crm_role_templates.py` (new, static, no database; it reads the committed manifest).** `test_the_manifest_has_22_permissions_and_10_templates`;
`test_the_four_templates_hold_exactly_the_grants_of_the_contract_table` (the full merged table of §11.3 as data: 22, 13, 13 and 5 grants with their scopes);
`test_only_crm_admin_is_admin_and_only_crm_rep_is_default`; `test_crm_manager_equals_crm_rep_at_r2_8_and_both_hold_no_delete_owner_stage_or_settings_code`;
`test_crm_viewer_holds_no_write_convert_reveal_or_delete`; `test_the_union_of_the_three_legacy_reps_equals_crm_rep_for_the_17_original_codes`;
`test_the_17_original_codes_are_frozen` (a frozen list of code, default scope, max scope, dangerous flag); `test_the_six_legacy_templates_are_frozen_except_is_default`
(frozen copies of their name, description and grants; the three reps are `is_default` false, the three admins `.all("ORG")` of their own slice); `test_each_description_is_three_sentences_in_service_order`;
`test_sources_are_customer_deal_lead`.

**Per service, `services/{customer,deal,lead}/tests/test_catalogue.py` (edited on purpose).** Each service: declares the four codes; the `name` and `is_default` equal the
contract table; its slice of grants equals §11.3 row for row; the admin is `CRM.all("ORG")` of that slice alone; exactly one default role in the whole manifest is `CRM_REP` (the customer
test that asserted `CRM_SALES_REP` as the only default changes to `CRM_REP`); the new codes (E19-E22) are in the owning slice; `validate()` still refuses a foreign grant. The existing
`test_a_foreign_product_grant_is_refused_at_validation` and `test_the_registration_validates_at_construction` stay.

**`services/account/tests/test_reconcile_crm_templates.py` (new, database).** The org is seeded from `fixtures/crm_manifest_pre_r2_8.json`, a frozen copy of the manifest at
`main@caa7f112` (six legacy templates, 17 codes, plus whatever R2.5 added), with a customer-authored role cloned from `CRM_SALES_REP`, two active owners, one non-owner member and
one removed member. Then, driving `reconcile_registration` with the real new manifest: `test_the_dry_run_reports_four_roles_created_and_changes_nothing`;
`test_apply_creates_exactly_the_four_new_roles_with_the_contract_grants`; `test_the_legacy_admins_gain_only_the_new_codes_of_their_slice`;
`test_crm_admin_is_granted_to_each_active_owner_and_to_no_one_else`; `test_a_second_run_is_an_empty_diff_and_bumps_nothing`; `test_an_owner_grant_alone_bumps_pv_and_publishes_grants_changed`;
`test_creating_roles_alone_does_not_bump_pv` (an org already holding every grant and every owner grant); `test_legacy_rep_roles_keep_their_name_description_and_is_default`;
`test_the_customer_authored_role_is_untouched_and_listed_as_skipped`; `test_no_existing_grant_is_narrowed_or_removed` (scopes_widened is zero and every pre-state grant is still held);
`test_a_redis_outage_fails_the_organization_and_rolls_it_back` (the existing fail-loud test's shape); `test_another_organizations_roles_are_never_touched` (cross-tenant);
`test_known_consequence_a_reconciled_tenants_new_member_receives_every_is_default_role` (pins four default rep roles; its name says it documents behaviour, not intent).

**`services/account/tests/test_bundle_install.py` (edited):** `test_a_trio_installs_...` also asserts the ten CRM roles exist; `ADMIN_ROLES` keeps the legacy codes (a subset check) and a new
`test_a_crm_activation_hands_the_owner_crm_admin_only` and `test_a_new_member_of_a_fresh_activation_receives_crm_rep_and_no_legacy_rep`. **`e2e/test_journey.py:36-44,274-277` (edited):** the journey's
admin-role pin becomes `{"CRM_ADMIN"}` for the owner of a fresh activation, and its permission expectation is read from the manifest instead of a literal pair. Gates: `make openapi` and
`make contracts-check` (the permissions manifest is regenerated and checked), `make gate`, and `make e2e` run locally by an agent against the local stack only.

### 11.9 Rollout

All of it is **owner-run except the merge**; nothing here is a code or schema change to a running service beyond four image rebuilds (the
three CRM ones, and `account` for the reason in step 2).

0. **Precondition: D-20 and D-21 are answered** (the State cell of each row in `docs/adr/0038-crm-programme-decisions.md` reads `approved` or
   `amended`: `grep -E '^\| D-20 \|.*\| (approved|amended) [0-9]{4}-[0-9]{2}-[0-9]{2} \|$' docs/adr/0038-crm-programme-decisions.md`, and the same for D-21), R2.5 and R2.7 are deployed (R2.5's hardened SPA published, and its designated-tenant reconcile and smoke done: §8.8 steps 2 to 6, because this release's `CRM_ADMIN` reaches every owner with the stage code), and the owner has read the dry run of R2.5's fleet reconcile (§8.8 step 7).
1. Review (CODEOWNERS applies to `libs/contracts/`); owner squash-merge; `make gate` locally.
2. Owner-named `make deploy HOST=… CONFIRM=… REF=<sha>`: the diff touches `services/{customer,deal,lead}`, `services/account/tests` and `libs/contracts`
   (`libs/contracts` is excluded from the full-rebuild rule), so **four images rebuild and recreate: `customer`, `deal`, `lead` and `account`**, and
   nothing else; no migration runs. `account` is rebuilt although none of its running code changes, because `services/account/Dockerfile` copies the whole
   `services/account/` directory (its two test files included; `.dockerignore` does not exclude them) and `remote.sh build` rebuilds any service whose directory
   has a diff (`infrastructure/edge/remote.sh:189-197`); compose then recreates the container on the new image. `account` answers the gateways' grant
   lookups (`libs/platform-core/src/platform_core/gateway.py:454-465`) and auth's directory and provisioning calls (`services/auth/app/main.py:30,33`), and
   runs its own consumer in-process (`api_with_worker`), so for the seconds of the recreate a sign-in, a token refresh or a grant lookup can fail (inference
   from those call sites, not an observed outage); the owner picks a quiet moment, as for any account restart. The alternative, keeping the two account test
   files out of this release, was rejected: the reconcile test is what proves the roles are created and never narrowed. The manifest is a bind mount of the checkout, so it is **live as soon as the box checks out the ref**, before the images are up: an organization
   that activates CRM in that gap gets the new roles and codes, which are inert, so the ordering hazard is cosmetic.
3. Verify (owner-run reads): `make edge-status` (**`account` recreated and healthy**, with the three CRM images; one sign-in and one token refresh succeed); read the manifest at the checked-out ref on the box (`jq '[.role_templates[] | {code, is_admin, is_default}]' libs/contracts/permissions/crm.json`):
   ten templates, `CRM_ADMIN` the only `is_admin`, `CRM_REP` the only `is_default`.
4. **Reconcile dry run:** `make edge-reconcile HOST=… CONFIRM=… PRODUCTS=crm` prints the dry run only. Read it per organization: expect `roles_created` of 4; `grants_added` on the legacy admins (zero
   `scopes_widened`); `owners_granted` naming each active owner; `skipped_customer_roles`; `memberships_without`. **Stop on any `scopes_widened`, any owner grant to a non-owner, or any organization the
   owner does not recognise.**
5. **Reconcile apply:** `make edge-reconcile HOST=… CONFIRM=… PRODUCTS=crm APPLY=1`, off-peak. The wrapper runs the dry run and the apply in one invocation (`remote.sh:434-458`); it is paced (250 ms) and stops at
   the first failing organization (Redis or product-service unreachable), after which the owner fixes the cause and re-runs: earlier organizations are already committed and a clean re-run changes nothing. Members
   refresh their tokens lazily afterwards (§11.4.5).
6. **Re-run the dry run**: every organization must show an empty diff.
7. **Moving people is the owner's deliberate act** in the admin console (no auto-grant): `CRM_VIEWER` first for read-only seats, `CRM_REP` for sales, `CRM_MANAGER` for team leads (it equals `CRM_REP` until R3.8
   widens it). The three legacy rep roles remain assignable and remain default in a reconciled tenant (§11.4.6d).
8. Memory: no container changes (figures as R2.4's: crm 2068, ten profiles 3748, ten plus `admin` 3945).
9. Docs: `docs/crm.md` (the role table and the legacy mapping), edited **in the same commit** as the rows that add the four templates and
   `E19` to `E22` (`tests/infra/test_crm_doc_matches_contracts.py` fails any release that adds, renames or removes a CRM event type, a
   permission code, a role template or a gateway prefix without it); `docs/adr/0038-...` (the State cells of D-20 and D-21, and a Sign-offs
   line for a staff plan action), and the RUNBOOK's reconcile note naming this release's expected dry-run shape.

### 11.10 Rollback

R2.8 is **forward-only in a live tenant by the platform's own design**: ADR-0007 has no revoke tool, a role the reconcile created stays, and a code granted stays. What can be undone:

- The **code** (the three catalogues and the manifest): `make edge-rollback` redeploys the previous ref, which rebuilds and recreates the same four images,
  `account` included, so it carries the same few seconds of account restart as the deploy. The previous manifest lacks the four templates, but the roles already created in live organizations remain, keep working
  for any member already assigned to them, and are never removed or narrowed by a later reconcile.
- **Assignments** the owner made: reversed in the admin console, member by member.
- **`CRM_ADMIN` granted to owners**: the owner removes it in the admin console if unwanted; owners also keep their legacy admin roles, so nobody loses access at any point.

Stop conditions during the rollout: any non-zero `scopes_widened`; a reconcile that fails twice for the same organization; an owner granted a role they did not already outrank; any member reporting lost access (a
defect, because the reconcile never narrows).

### 11.11 Acceptance

- **AC-2.8-1** `crm.json` has 22 permissions and ten templates, `is_default` only on `CRM_REP`, `is_admin` only on `CRM_ADMIN`, sources `customer, deal, lead`; `python tools/export_permissions.py --check` passes; `docs/crm.md`'s role table carries the four templates and `E19` to `E22`, edited in the same commit, and `tests/infra/test_crm_doc_matches_contracts.py` passes (static tests; gate row).
- **AC-2.8-2** The four templates hold exactly the merged grant sets of §11.3 (22, 13, 13, 5), and each service's slice matches its row (tests).
- **AC-2.8-3** All three services declare all four templates with identical `name` and `is_default`, and each description is three sentences in service order (tests).
- **AC-2.8-4** The 17 original codes (scopes, dangerous flags) and the six legacy templates (names, descriptions, grants) are unchanged, except `is_default` false on the three legacy reps (frozen-copy tests).
- **AC-2.8-5** The union of the three legacy reps equals `CRM_REP`'s 13 grants; `CRM_MANAGER` equals `CRM_REP` at this release; `CRM_VIEWER` holds no write, convert, reveal or delete (tests).
- **AC-2.8-6** The reconcile creates exactly four roles, widens only the legacy admins by their own slice's new codes, grants `CRM_ADMIN` to active owners only, never narrows, leaves customer-authored roles and legacy `is_default` alone, and a re-run is an empty diff (database tests).
- **AC-2.8-7** `pv` is bumped and `core.grants.changed` published exactly for organizations with a grant change or an owner grant, and a Redis failure rolls that organization back (tests).
- **AC-2.8-8** A fresh activation gives the owner `CRM_ADMIN` and a new member exactly `CRM_REP`; the known default-role consequence of a reconciled tenant is pinned by a test (tests).
- **AC-2.8-9** D-20 and D-21 are answered in `docs/adr/0038-crm-programme-decisions.md` (`grep -E '^\| D-20 \|.*\| (approved|amended) [0-9]{4}-[0-9]{2}-[0-9]{2} \|$' docs/adr/0038-crm-programme-decisions.md` prints a row, and the same for D-21), and the plan packaging staff action, if any, is one Sign-offs line recorded as done by the owner (grep; owner-recorded).
- **AC-2.8-10** The only existing tests edited are the three `test_catalogue.py`, `account/tests/test_bundle_install.py` and `e2e/test_journey.py` (`git diff --name-only` on test paths); no route, scope check or migration file is in the diff, and the only files under `services/account` are the edited `tests/test_bundle_install.py`, the new `tests/test_reconcile_crm_templates.py` and its fixture, so the release's rebuilt images are exactly `customer`, `deal`, `lead` and `account` (`git diff --name-only <base>..<head> -- services`).

## 12. R2.9 — C15 contract (optional)

### 12.1 Purpose

Remove what R2.6 and R2.7 deliberately kept so the cutover could be gradual, **only after R2.7 has soaked**, and settle the one question the registry leaves
to this release (`L16`). Nothing here is needed for a rep to work; every part is independently gated, may be skipped, and is safe to ship alone. The release
has three parts and a decision:

- **Part A, retire the one-click claim** (registry `C26`, DD-34): the SPA's "make me the owner" button and the free-form owner dialog code go.
- **Part B, retire the residue**: the SPA's `sref`-era naming and any remaining employee-id reading in `product-crm`.
- **Part C, optional sealing of `contacts.email`** (registry `B56`, `B10`; DD-37): specified to the level the registry fixes, **off by default**, built only if the owner says yes and its gates hold.
- **Decision L16:** `customer.owners` **stays**. No release in the programme drops it: it is the roster that `owners:validate` and rep activation read.

### 12.2 Data model

Parts A and B change no schema. **Part C only**, a customer revision (its own id allocated when the part is planned, with `down_revision` the customer head at that
time; expand-only, `SET LOCAL lock_timeout = '5s'`):

```sql
ALTER TABLE customer.contacts ADD COLUMN email_enc bytea, ADD COLUMN email_masked varchar(254);
ALTER TABLE customer.contacts ADD CONSTRAINT ck_contacts_email_sealed_pair
    CHECK ((email_enc IS NULL) = (email_masked IS NULL));
```

No new table, so no new RLS statement (the table's policy covers the new columns); the existing `ix_contacts_org_email_index` of R3.1 is the matching key, never a cross-service value.
The **plaintext `email` column is not touched** and is nulled only in a later contractive release outside this programme (registry `B56`). Guarded downgrade: refuses while any
`email_enc` is set, unless `OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE=1`.

### 12.3 Backend contract

**Part A.** Customer's API is unchanged: `POST /customers/owners` already answers 422 `membership_required` without a member (R2.7) and keeps working with one. What goes is **code**:
`frontend/product-crm/src/forms/OwnerPicker.tsx` loses the claim mutation and the "Make … the owner" button (`:46-90`), and the free-form fields of the owner form are deleted
(R2.7 already replaced the dialog by the member picker); `api` helpers `createOwner` accept only `{membership_id, role?}`.

**Part B.** `getMySubjectRef()` is renamed `getMyMembershipId()` (`product-crm/src/lib/subject.ts:21`) with its two callers; `product-crm` no longer imports or reads `sref`
(`opsgrids-client`'s `AccessClaims.sref` stays: HR apps read it). The server side has nothing left to remove: after R2.7 no CRM code reads the employee link
(`tests/infra/test_no_raw_subject_ref.py`), and the roster's **display arm** (a legacy roster id resolving to a name) **stays on purpose** (DD-32): historical `deal.activities` rows carry legacy ids for ever.

**Part C (conditional).** If built: `ContactOut` gains `email_masked: string | null` additively; `create_contact` and `update_contact` seal the address (`platform_core.pii.seal(kind="email")`),
write `email_enc`, `email_masked` and `email_index`, **and still write the plaintext `email`**; a new route
`POST /contacts/{contact_id}/email:reveal` mirrors `phone:reveal` (`customer/app/api/routes.py:423-424`): `require("crm.book.contact.read_sensitive", amr={"otp","stepup"})`, the object check
at OWN or wider, the shared reveal budget (30 per 10 minutes, 200 per day), one `entity.sensitive_read` audit row naming the field `email` and never its value, `Cache-Control: no-store`;
errors as the phone route (403 out of scope or no step-up, 404, 429, 503). An owner-run backfill seals the existing plaintext (an extension of the registry's H8,
`tools/backfill_pii_indexes.py --target email_seal`: dry run by default, the three purge gates, one audit row per organization naming the field and count, `updated_at` untouched, no events).

### 12.4 Domain rules

1. Part A refuses nothing new: it removes UI that R2.7 had already made redundant. **It may not ship before the Team screen of R3.4 exists**, because that screen is the only
   first-run path that remains: an organization with zero reps and an admin must be able to activate its first member without an API call.
2. Part B is a rename and a deletion with no behaviour.
3. Part C's sealing changes **who may read a contact's email**: today every holder of `crm.book.contact.read` sees it. It is therefore **off by default** and gated (§12.8): sealing is safe
   only after every reader of the plaintext email has a sealed-aware path or has been consciously left on the plaintext column, and those readers are finance (`GET .../contacts/{contact_id}`
   returns the email for invoicing, `ContactSummaryOut.email`), notification (`messaging-target` with `channel=email`, R4.1), the Engage projection export (C53) and the SPA's list, 360 and
   import/export. Until the gate holds, the plaintext column remains the source of truth and the sealed columns are a shadow.

### 12.5 Consumers and idempotency

None added or changed. The Part C backfill is idempotent: rows already holding `email_enc` are skipped, and a re-run changes nothing.

### 12.6 UI

Part A: the picker's empty state when the caller **cannot** activate anyone (no `crm.book.owner.write`) keeps its informational alert ("Ask an administrator to add the team on the Team page"); when
the caller can, it shows the member picker only. States unchanged: loading, empty, error, forbidden. Part B: none. Part C: the list and detail show `email_masked` with a reveal control built on the
shared `RevealableField` (registry `G6`), `kind="email"`, `canReveal` from the grant; no raw email is rendered or cached.

### 12.7 Tests

**Part A:** `OwnerPicker.test.tsx` (`test_with_zero_owners_and_the_permission_the_picker_offers_the_member_list_only`, `test_the_claim_button_is_gone`, `test_without_the_permission_it_shows_the_informational_alert`);
a text-scan vitest over `frontend/product-crm/src` that `createOwner` has no `full_name` or `email` argument. **Part B:** `subject.test.ts` renamed; a static test that no file under
`frontend/product-crm/src` mentions `sref`. **Decision L16:** `tests/infra/test_owners_table_is_never_dropped.py` asserts no migration under `services/customer/migrations/versions` drops or renames
`owners`. **Part C (only if built):** migration round trip and guarded downgrade; `test_sealing_writes_the_shadow_columns_and_leaves_the_plaintext`; the reveal route's negative-authz set (no token 401,
no `read_sensitive` 403, no step-up 403, a colleague's contact 403 at OWN, another organization's contact 404, budget 429, Redis down 503) and its audit row; `test_the_backfill_is_dry_run_by_default_and_idempotent`;
`test_no_event_carries_an_email` (the events contain none, as before). Gates: `make gate`; the access matrix tests of R2.7 pass **unmodified**.

### 12.8 Rollout

- **Part A** needs R2.7 soaked **and** R3.4 shipped. Owner publishes the SPA last (`make edge-web … APP=crm` from the merged checkout).
- **Part B** rides with Part A or alone; same SPA publish.
- **Part C** is built only when all of these hold, each recorded by the owner: the owner's explicit yes (a new owner decision, **D-26**, raised by this release: "seal contact emails at rest?", default **no**); R3.1 has shipped
  (nullable `email`, `email_index`) and the owner has run H8 for `email_index`; R4.1 and R4.2's readers of the plaintext email are either sealed-aware or consciously left on the plaintext column; the reveal route's negative-authz set is
  green. The order is then: deploy the expand (columns and dual-write), owner runs the seal backfill dry run then apply, then publish the SPA that shows `email_masked`. The **contractive** release that nulls the plaintext is a separate
  programme item.
- Memory: no container changes in any part.

### 12.9 Rollback

Parts A and B: `make edge-web-rollback HOST=… CONFIRM=… APP=crm` restores the previous bundle (`APP=crm` is required: the target defaults to the
anonymous storefront's `buyer` bundle, `Makefile:625`); the server was never changed. Part C: redeploy the previous ref; the shadow columns stay (expand-only, ignored by old code); the plaintext column is still the source
of truth, so no data is lost; the guarded downgrade drops the columns only with the override. Stop conditions: any blank or unmasked email in a list; a reveal that does not write its audit row.

### 12.10 Acceptance

- **AC-2.9-1** The SPA has no claim button and no free-form owner fields; `createOwner` accepts only a member; the release is not scheduled before R3.4 (test; the plan task records the dependency).
- **AC-2.9-2** An organization with zero reps and an admin can activate its first member from the member picker (SPA test), and the informational alert remains for a caller without the permission.
- **AC-2.9-3** `getMyMembershipId()` replaces `getMySubjectRef()`; no file under `frontend/product-crm/src` reads `sref` (static test).
- **AC-2.9-4** No migration drops or renames `customer.owners`; `docs/crm.md` and register row 135 record L16 (static test; grep).
- **AC-2.9-5** If Part C is built: the migration is expand-only with a guarded downgrade, sealing dual-writes and leaves the plaintext, the reveal route passes its negative-authz set and writes one audit row per reveal, and the backfill is gated and idempotent. If Part C is not built: no `email_enc` appears in `services/customer/app/models.py` and the owner's decision D-26 (default no) is recorded (grep).
- **AC-2.9-6** The access matrix tests of R2.7 pass unmodified, and the diff contains no route permission or scope-check change (tests; `git diff`).

## 13. Owner-run steps

An agent builds, tests and reviews. It **never** runs a production read or write, a deploy, a box `.env` edit, a fleet tool, a reconcile or a
publish of the SPA (P§3 F11, ADR-0036 decision 1; registry §H). Merges are the owner's; nothing deploys on merge (Actions billing is blocked, so
`make gate` run locally is the evidence the owner merges on). The decisions behind these steps are in §2.1, with **D-26** (§12.8) added.

| # | Release | Act (the owner's) | How | Evidence the plan waits for |
|---|---|---|---|---|
| O-1 | R2.1 | Answer D-4 (default: yes to both bumps); merge; deploy the `lead` image | `make deploy HOST=… CONFIRM=… REF=<sha>`; `make edge-status`; `make edge-logs SERVICE=lead` | the answer in `docs/adr/0038-...` |
| O-2 | R2.2 | Pick a quiet window (full rebuild); `make edge-preflight`; deploy; read one OWN-scoped HR list and one CRM list | `make edge-preflight HOST=… CONFIRM=…`, `make deploy …` | `make edge-status` all healthy |
| O-3 | R2.3 | Answer D-5 and D-12; **confirm R1.2a is deployed** (the 360 check means nothing before it); run the read-only **pre-check** of §6.8.2 (deal size and long or idle-in-transaction sessions); deploy `deal` and `customer` together; read `GET /deals/stages` and the 360 | the `psql` command of §6.8.2; `make deploy …` | `materialised: false` with six stages; the 360's `crm` section present once R1.2a is live |
| O-4 | R2.4, before | Answer D-1, D-2, D-3, D-13; **run the D-3 count** (read-only SQL, §7.4) and record the three counts per organization | the `psql` command of §7.4 | counts in `docs/crm.md` and register row 122 |
| O-5 | R2.4 | **Raise `EDGE_BUDGET_ACCEPTED_MB` to 3945 in the box `.env`** (mode 600) **before** the deploy | edit on the box | `make edge-preflight` finds the ceiling |
| O-6 | R2.4 | Deploy (full rebuild, quiet window); read the consumer bindings and the new queue; read idle memory | `make deploy …`; `make edge-logs SERVICE=deal`, `SERVICE=customer` | `consumer.started queue=deal.events` |
| O-7 | R2.4 | Soak in `log_only`; read `crm.conversion_cleanup.*` lines; **flip to `on`** only after reading them (D-2): edit the box `.env`, recreate `customer` and `deal` | `docker compose -f compose.prod.yml --env-file .env up -d customer deal` | the start-up log line shows the mode |
| O-8 | R2.5 | **Publish the hardened SPA first** (from the merged checkout, grep the bundle); deploy `deal` and `notification`; **reconcile one designated tenant (dry run, read it, then `APPLY=1`)**; exercise the four routes there and open that tenant's Deals screen; only then **reconcile the fleet (dry run, read it, then `APPLY=1`)** off-peak | `make edge-web HOST=… CONFIRM=… APP=crm`; `make edge-reconcile HOST=… CONFIRM=… PRODUCTS=crm ORG=<uuid>` then `… ORG=<uuid> APPLY=1`; then `… PRODUCTS=crm` then `… PRODUCTS=crm APPLY=1` | the bundle contains "Other stages"; the designated tenant's screens render; an empty re-run dry run on the fleet |
| O-9 | R2.6 | Deploy four images; check three Alembic heads; **publish the SPA** from the merged checkout; `census`; `plan`; **review the plan through `show`, accept it through `accept` and hand-map who the planner missed through `link`** | `make edge-web … APP=crm`; `make edge-map-crm-owners … SUB=census`, `SUB=plan`, `SUB=show`, `SUB=accept`, `SUB=link` | bundle contains `membership_id`; the reviewed plan on the box (owned by the image user, mode 600) |
| O-10 | R2.7, before | Answer D-7, D-8, D-9; **sign the security review S-1 to S-12**; `make edge-backup`; activate the members that own leads or deals (the `unnamed` list) | the admin console; `make edge-backup …` | the D-9 row's State cell reads `approved` and one Sign-offs line carries the signed checklist (§10.7) |
| O-11 | R2.7 | **The data step**: for `customer`, then `deal`, then `lead`: dry run, read the count, `apply` with its three gates; then `verify` with zero strays **and the legacy counts recorded**, each non-zero one mapped, reassigned or accepted in writing | `make edge-map-crm-owners … SUB=apply ARGS="--service … --plan … --apply --confirm-count N --i-took-a-backup --i-know-this-is-the-live-database"`; `SUB=verify` | `verify` output, recorded, with the legacy counts and what was done with each |
| O-12 | R2.7 | Deploy the three CRM images; run `e2e/edge_product_crud.py` (the rewritten smoke); one OWN-rep check; **publish the SPA last**; catch-up `apply` and `verify`; **destroy the plan** | `make deploy …`; `make edge-web … APP=crm`; `make edge-map-crm-owners … SUB=forget-plan ARGS="--all"` | the second `verify`, recorded; `plan.json` gone |
| O-13 | R2.8 | Answer D-20 and D-21 (and take the **staff plan action** in the admin console if the answer is to retire or fix `crm/growth`); deploy (**four images, `account` included**; a quiet moment); read the manifest; **reconcile dry run, apply, dry run again**; move people between roles deliberately | `make edge-reconcile …` twice; the admin console | empty second dry run; the State cells of D-20 and D-21 read `approved` or `amended` (§2.1); `account` healthy |
| O-14 | R2.9 | Optional. Answer D-26 (default no); publish the SPA once R3.4 exists; if D-26 is yes, run the seal backfill dry run then apply | `make edge-web … APP=crm`; `tools/backfill_pii_indexes.py --target email_seal` through its wrapper | the decision; backfill counts |
| O-15 | any release | Roll back when a stop condition of that release's rollback section fires: stop acting (R2.4 mode), `make edge-rollback`, `make edge-web-rollback … APP=crm` (never without `APP`: it defaults to the storefront's bundle), `revert` | the section's own steps | the incident note |

Every owner act that writes (O-5, O-7, O-8, O-9's SPA publish and plan, O-11, O-12, O-13, O-14) has a read-only precursor in the same row and an explicit stop condition in its release section.

## 14. Risks

Likelihood and impact are the author's judgement (L low, M medium, H high); `inference` marks a risk reasoned from the code rather than observed.

| # | Risk | Release | L | I | Mitigation | Residual |
|---|---|---|---|---|---|---|
| K-1 | A full-rebuild deploy recreates every container; an invitation accepted mid-deploy got a 502 once (`rebuild-decision.sh:18-24`) | R2.2, R2.4 | M | L | quiet window; the two are separate deploys | seconds of 5xx on a quiet box |
| K-2 | The memory ceiling is not raised before R2.4, so preflight refuses | R2.4 | M | L | refusal changes nothing; O-5 comes before O-6 | a delayed deploy |
| K-3 | The cleanup deletes a record it should not | R2.4 | L | H | log-only first; provenance by derived id, creation audit row and single audit row; soft delete only; marker row on adoption; a row lock on both sides; every deletion audited with the consumer's name | a restore is owner SQL driven by the audit rows |
| K-4 | A rep creates a deal for the orphan company between the probe and the commit | R2.4 | L | L | one near-zero-rate event; the window is milliseconds; soft delete is reversible | a deal pointing at a soft-deleted company id (`inference`: it renders as a dash) |
| K-5 | Historical `status_changed` aborts leave records no code cleans | R2.4 | M | L | not orphans in the strict sense (a re-qualified lead adopts them by id); accepted | a few untouched records |
| K-6 | The `deal.events` queue exists but no consumer after a code rollback | R2.4 | L | L | near-zero volume; the owner may delete the queue | an idle queue |
| K-7 | `a1c9e2f7b3d4` is edited by the insights branch before R2.3 merges | R2.3 | L | M | a byte-identity test fails the build | a rebase task |
| K-8 | The materialised-stage read path never runs in production until R2.5 | R2.3 | M | L | parity tests run the whole stage-writing suites twice; R2.5's smoke is its first production exercise | one smoke |
| K-9 | The 360's CRM section is empty for seconds while `deal` and `customer` recreate out of step | R2.3 | H | L | existing degrade path names it in `unavailable`; either recreation order is safe | seconds |
| K-10 | A 2,000-deal archive holds the exclusive pipeline lock for seconds | R2.5 | L | L | bounded; `lock_timeout` 5 s with a clean `pipeline_busy` | a retry |
| K-11 | The mapping data step is a mass write to production | R2.6, R2.7 | M | H | dry run default, three gates, plan digest, journal, `revert`, customer-first, per-organization transactions, no events, no `updated_at`, backup first | rehearsed reversibility |
| K-12 | The owner accepts an `email` mapping that is the wrong person, giving a rep another person's rows | R2.6 | L | H | `email` rows are never accepted by default; case-insensitive exact equality only; `revert` | one organization's rows until reverted |
| K-13 | Between `apply` and the deploy an HR rep cannot OWN-edit rewritten rows | R2.7 | H | M | run steps O-11 and O-12 in one sitting; fail closed, never widens | minutes |
| K-14 | The flip widens access (D-9): every rep gains OWN write, convert and reveal | R2.7 | H | H | signed review S-1 to S-12; 14 named negative-authz tests; fail-closed everywhere | the widening is the decision |
| K-15 | `mem` rides the hop token unbound (DD-9): a caller that reaches a service directly, bypassing the gateways, could forge it, exactly as it could forge `X-Perms` and `X-Subject-Ref` | R2.7 | L | H | services are reachable only on the `back` network; the gateway strips inbound identity headers; hardening the hop token is listed out of scope | the accepted platform-wide gap |
| K-16 | Tenant automations that render `{{payload.owner_id}}` start showing membership ids | R2.7 | L | L | event shapes unchanged; documented in `docs/crm.md` | a few templates |
| K-17 | A lead owned by a member with no roster row, or by a legacy roster id that no mapping reached, cannot convert (nor can anyone else convert it) | R2.7 | M | M | `verify` reports `unnamed`, `legacy_rows_remaining` and `unconvertible_leads`; before O-12 the owner activates the members, maps the rest (`accept`, `link`) or reassigns the leads, and records what is left (DD-56) | a rejection until the lead is reassigned |
| K-18 | Reconciled tenants hand a new member four rep roles | R2.8 | H | L | pinned by a test; a bespoke script (not built) can correct it | untidy, not unsafe |
| K-19 | The reconcile bumps `pv`, so signed-in members refresh tokens | R2.5, R2.8 | H | L | sequential, paced, off-peak; fail-loud on Redis | a brief re-auth wave |
| K-20 | D-21 is unanswered and R2.8 waits | R2.8 | M | L | the plan blocks R2.8 only; every other release is independent | schedule |
| K-21 | R2.2 changes `auth.py` for every product | R2.2 | L | H | the frozen-oracle parity test over the full grid; the whole suite per service; full rebuild | none expected |
| K-22 | Local `make gate` is the only full verification while Actions billing is blocked | all | M | M | the owner merges on recorded local gate output | a missed row |
| K-23 | `fix/ci-failures` shifts `auth.py` line numbers | R2.2 | H | L | the plan builds against symbols (§0) | rework of citations |
| K-24 | Documentation counts are already stale (`ENV_SOURCE_MATRIX.md` says 47 containers; `memory-budget.json` holds 50) | R2.4 | H | L | the R2.4 doc edits recount from the files | none |
| K-25 | R2.6's deal revision is written against `e94d7a2c5f18` beside the byte-pinned `a1c9e2f7b3d4`, so deal has two heads and `alembic upgrade head` fails | R2.3, R2.6 | L | H | R2.3 is a hard prerequisite of the revision (DD-51); the one-head and one-child chain tests; `make migration-roundtrip` and `make migration-check` | a rebase of an unmerged branch |
| K-26 | The adopted `ADD COLUMN` queues behind a long or idle-in-transaction session and stalls every deal read and write while old code still serves | R2.3 | L | M | a 5 s session `lock_timeout` from `env.py` (DD-52) fails the migrate step before `up`; the owner's pre-check (§6.8.2); the fixed-lock test | a failed deploy, re-run once the holder is gone |
| K-27 | A deal in a stage outside the legacy six blanks the published SPA's Deals screen for that tenant (the permission also reaches users through R2.8's reconcile and a new activation, not only R2.5's) | R2.5 | M | H | the hardening is published before anything else in the release (DD-54, §8.6) with a vitest case per surface; the reconcile goes to one designated tenant first (`ORG=`); the SPA is not rolled back below it while a custom stage exists (§8.9) | a stale browser tab on an older bundle until it reloads |
| K-28 | The mapping plan holds roster emails at rest on the box | R2.6, R2.7 | L | M | a directory owned by the image user, mode 700, files 600 (DD-55); `show --print-emails` is the only subcommand that prints one and writes none anywhere; `forget-plan` after the final `verify` | the file exists between `plan` and `forget-plan` |
| K-29 | `GET /deals/stages?include=counts` leaks deal-derived data beyond a caller's scope, or the existence of deleted deals | R2.3 | L | M | counts only for an ORG `crm.pipeline.deal.read` grant, live deals only (DD-53); an OWN-reader negative test and a cross-tenant count test | none |

## 15. Out of scope

Each item is named so a reviewer does not look for it.

- A second pipeline per organization, reopening a closed deal, per-stage "hot" flags, custom objects (P§5), **un-archiving a stage** (an admin creates a new one), a delete route for stages.
- The stage editor, the settings hub, saved views, tags and every redesigned screen (SP3, R3.3-R3.8); wiring TEAM scope to `team_col` and stamping `owner_manager_id` (R3.8; this programme's R2.2 only fixes what TEAM means for a membership).
- Opting ITSM (NEW-8), projects (C14) or PulseBoard into the membership subject; every other product stays on the employee link.
- Binding `mem` into the gateway hop token (DD-9), or any gateway change.
- Seat enforcement, a plan change in code, a new product key, a new container (D-21, P§5). The bespoke script that would flip `is_default` on the three legacy rep roles in live tenants (§11.4.6d).
- The insights service, its tables and its `deal_facts` (D-12 fixes only the contract it will consume); an owner-id re-export for it.
- Rewriting `deal.activities.owner_id` or lifting its append-only trigger (DD-32); sweeping historical `status_changed` aborts (§7.4); extending `e2e/` to boot lead, the relay and the CRM consumers (DD-49).
- Folding `lead` into `customer` (D-11), and lead's own consumer fold (R3.8).
- Removing or renaming any of the 17 permission codes or six legacy role codes; automatic role assignment; retiring the legacy roles (no retire tool exists).
- Dropping `customer.owners` (L16), nulling the plaintext `contacts.email` (a later contractive release), any change to what finance, notification and Engage read from a contact.
- Rewriting the lead's `conversion_ids` into a shared library (DD-46 uses golden vectors).
- Changing the default app of `make edge-web` and `make edge-web-rollback` (the buyer bundle's own RUNBOOK lines rely on it; §9.7 scopes a static check to the CRM sections instead), an activation-time "adopt a legacy roster row" path (DD-56: `link` covers it), and a merge revision for deal's chain (DD-51: R2.3 goes first).

## 16. Registry amendments

The registry amendments this spec proposed in its first draft, 32 rows, have been folded into the registry. Its section
J.2, "Folded amendments: the handle map and the ledger", lists each one as "SP2 n" with the row it changed: rows 6, 15
and 25 are confirmed with no change, row 18 became the new row `A22`, row 32 became the new row `G26`, and the other 27 are
folded into the rows they named. This document therefore carries no table of them and edits no registry row; wherever a
section depends on a folded row it cites the row's handle (`A22`, `C1`, `C55`, `C57`, `EV14`, `G26` and so on), and the
registry is the one place that states the row's final text.

**Rejected: none.** Every amendment this spec proposed was folded or confirmed (registry §J.2). The one detail in which
this spec still goes beyond a registry row is recorded in §17, item 12 (c).

## 17. Deviations from the programme spec

The programme (`2026-10-07-crm-overhaul-programme-design.md`, P§) is the contract this document builds on. Where the registry has already amended it (`J1`, `J10`, `J17`-`J22`, `J30`-`J32`) this
spec follows the registry. The deviations that remain are mine:

1. **R2.3 is not strictly behaviour-preserving.** P§10's row says "behaviour-preserving refactor". One deliberate change ships: a closing move whose probability is not the pin is a 422 (DD-15, §6.4; registry `C8`). It is listed
   and tested as the single exception, because storing won at 37 percent corrupts every rollup.
2. **R2.6 publishes an SPA.** P§ puts all SPA work in R2.7 ("SPA subject"). The owner-name and picker change is moved up so the data step is safe at any time (registry `G16`); `mem` and "My pipeline" stay in R2.7.
3. **R2.9 is smaller than P§ says in one place.** P§: "retire employee-id fallbacks and the one-click owner claim". The write-side fallbacks (default owner from the employee link, assignment and validation by roster id) already retire
   in R2.7 as registry `C76` states, and the read-side display arm stays for ever because `deal.activities` keeps legacy ids (DD-32). R2.9 therefore retires the claim button and the naming residue, settles L16, and carries the optional sealing under a new decision.
4. **The D-3 count runs a release earlier than the census tool.** P§ and the registry build the census in R2.6; NEW-9's decision needs the count in R2.4, so it is a hand-run read-only SQL there and `census --aborted-conversions` repeats it later (§3.8.6).
5. **R2.4 and R2.2 are full-rebuild deploys, and R2.4 needs the memory ceiling raised first.** P§ D-13 assumed no memory increase; the registry's `L17` amends that, `J32` records that D-13 first blocks R2.4, and `J31` records that R2.2 and R2.4 are full-rebuild deploys (§3.8.4).
6. **R2.5's "notification first" is met by the reconcile, not by two deploys** (DD-50). The programme and the registry require the filter to be live before the route is enabled; for an existing tenant the route is enabled by the reconcile, which this spec orders after a verified deploy of both images.
7. **One new owner decision, D-26**, for the optional sealing (§12.8), default no. The programme's register (P§8) ends at D-24; the registry records D-26 in its section K with the other decisions raised after it.
8. **Two prerequisites P§10 does not list** (registry `J30`). R2.3 needs **R1.2a**: R1.2a owns the 360's path fix (P§3 F6), and R2.3 rewrites the same function and test
   file and verifies through the same leg (§3.3, DD-16). R2.6 needs **R2.3**, for deal's migration chain only: its revision follows R2.3's, because the
   adopted `a1c9e2f7b3d4` is byte-pinned and cannot be re-parented by a second merger (§6.2, DD-51).
9. **R2.5 ships an SPA change, and publishes it first** (DD-54, §8.6; registry `J31`, `G26`). P§ puts no SPA work in R2.5, but the stage routes are the first way a tenant can hold a stage
   the published bundle cannot render, and SP3's stage-driven board (R3.4) itself needs R2.5 and R2.8 first. R2.5's reconcile also gains an `ORG=` option.
10. **`GET /deals/stages` has no scope floor on the bare list**, against P§7's "every new route needs an explicit floor" (DD-53; registry `J32`, `C1`). The rows are tenant
    configuration, like the owner roster (`GET /customers/owners`), readable by any holder of `crm.pipeline.deal.read` at any scope, because an OWN-scoped
    custom role still has to label the stages its deals sit in; a floor would deny it that and protect nothing. The one deal-derived field, `deal_count`, is
    the part that needs a scope, and it needs an ORG grant.
11. **R2.8 rebuilds four images, not three**: `account`'s two test files sit in its build context (§11.9), so the release recreates `account` too.
12. **Three details in which this spec makes a choice of its own where SP1's hand-offs (SP1 §9.2.2, §9.2.3) or the registry are silent.**
    (a) *The pins SP1 writes.* R2.3 edits SP1's `AHEAD_OF_PRODUCER` table for deal and the pinned-eight test (§6.7), which R1.2b
    creates. R1.2b is not among R2.3's prerequisites (registry `J30`), so whichever of the two merges second adapts: if R2.3 merges
    first, R1.2b writes its table and its pinned set without the three deal events and R2.3 has nothing to delete.
    (b) *The guard's registrations.* SP1's list of releases that register an internal client's calls in
    `tests/infra/test_internal_client_paths.py` names R2.6 (W17) and not R2.4, because W13 is marked `exists`; but R2.4's new
    `services/customer/app/clients.py` reads `deal_internal_url`, which that guard's scan flags until the call is registered, so
    R2.4 registers `deal_references` as well (§7.3, §7.7).
    (c) *The journal's `evidence` values.* Registry `B2` lists `link`, `email` and `manual`; `revert` writes counter-journal rows
    with a fourth value, `revert` (DD-29, §9.2). `B2` needs that value added; until it is, this is a departure from the registry row.

Everything else in P§ §2 (M2: nine independently reversible releases), §8 (decision defaults) and §10 (the dependency order) is followed as written.
