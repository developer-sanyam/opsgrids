# CRM overhaul 1 — foundations and cleanup — design

Status: **proposed, design only — pending owner review.** Writing this file changes no code, test,
configuration or other document. Base: `main` @ `caa7f112` (`origin/main`, #117); every path below
was re-read at the working tree's `HEAD` `3bc7f6ca`, where the CRM paths are identical. Alembic heads
today, re-derived from the `revision` and `down_revision` pairs in each `migrations/versions`: deal
`e94d7a2c5f18`, customer `e230c025337e`, lead `91dd3ca663fd`. **This sub-project adds no migration
and moves no head.** The one conditional contractive lead revision (`phone_index`) is specified in
§5.2 and is not part of R1.2.

Companion documents: the programme contract `2026-10-07-crm-overhaul-programme-design.md` (cited
`P§n`; its owner decisions `D-n`; its facts `P§3 F1`–`F11`) and the interface registry
`2026-10-07-crm-overhaul-interface-registry.md`, whose names are binding here and are cited by handle
(`EV4`, `C23`, `B40`, `F6`, `G15`, `H8`, `I1`, `J13`, `K`, `L2`). The task-by-task plan for this
sub-project is `plans/2026-10-07-crm-1-foundations-and-cleanup.md`, written separately.

Evidence base: the two discovery runs named in the programme (`wf_9a018e15-345`, `wf_4cf65cd6-720`),
every finding re-attacked by an independent skeptic; where a skeptic corrected a mapper, the
correction is used. A claim that rests on a digest and was not re-read is marked *(digest)*; a claim
no code or test observed is marked *inference*.

## 0. Conventions

- `D-n` is a programme owner decision, and P§8 numbers all forty-one: D-1 to D-24 are the original rows;
  D-25 to D-41 were raised later, by the registry (D-25 and D-35 to D-40), SP2 (D-26), SP3 (D-41) and SP4
  (D-27 to D-34, D-27 shared with SP3), and a D-id above 24 in an older text means the row of P§8.
  ADR-0038 (§4.3) holds all forty-one. `(pending D-n)` marks a default that moves only when the owner
  answers.
- `DD-n` is a decision this spec makes (§2.2), each with the alternative it rejected and why.
- `R1.1-n` and `R1.2-n` are scope items inside a release; `AC-1.1.n` and `AC-1.2.n` are acceptance
  criteria, each checkable by a command or a search. R1.2 is one scope shipped as two deploys,
  **R1.2a** (the 360 fix) and **R1.2b** (contract hygiene and removals; registry `J31` records the
  split); each item and each criterion names its deploy (§5.1, §5.11), and another document that says
  "R1.2" without a letter means both.
  `<base>` in a command is `origin/main` after `git fetch origin`, unless the line says otherwise.
- **A proposal is never written as an outcome.** In a document the owner has not yet ruled on (the
  two accepted ADRs, the register, `docs/crm.md`) what the programme *proposes* is worded "proposed in
  ADR-0038 (pending D-n)"; only what the code or the data shows is written as fact (§4.7).
- Citation prefixes follow the registry: `customer/…`, `deal/…`, `lead/…`, `notification/…`,
  `finance/…`, `account/…` are under `services/`; `product-crm/…`, `product-engage/…`,
  `opsgrids-client/…`, `opsgrids-visual/…`, `opsgrids-config/…` are under `frontend/`; `events.py`,
  `app.py`, `gateway.py`, `internal_auth.py` are under `libs/platform-core/src/platform_core/`;
  `compose.prod.yml`, `memory-budget.json` and `RUNBOOK.md` are under `infrastructure/edge/`.
- Local ids that look like registry handles: A1 to A9 (defined in §3.4, cited in §5 to §8) number this
  audit's nine internal-client call sites, and B1, B2 (§5.2) are the two steps of the `phone_index`
  change. They are not the registry's §A and §B rows: a registry handle stands alone in backticks
  (`A2`, `B1`), and these local ids are written plain (they appear inside backticks only as part of a
  test id).
- **Owner-run** means a production read or write, a deploy, a `make edge-*` target, publishing a
  SPA bundle, a box `.env` edit, or the production smoke. An agent builds and tests these locally and
  never runs them against the box (P§3 F11, registry §H).

## 1. Goal and non-goals

**Goal.** Make the CRM understandable and honest before anything is built on it. After SP1 there is
one place that says what the CRM is (the glossary, `docs/crm.md`, ADR-0038); nothing the discovery
confirmed dead is still shipped; the one production defect discovery found, the Customer 360's deal
leg, is fixed and cannot silently come back; the CRM's event contracts are complete and enforceable;
and the owner has the numbers for the one structural question that is theirs to answer (D-11: fold
`lead` into `customer`). It is three independently reversible releases. R1.2 is split so that the one
production defect fix neither waits for the hygiene changes nor rolls back with them: the box's only
rollback is a whole-ref `make edge-rollback` (`Makefile:577-583`).

| Release | Content | Runtime change |
|---|---|---|
| R1.1 | ADR-0038 (proposed), glossary, `docs/crm.md`, rewrite of the stale CRM documents (the three service READMEs among them), register and ADR text updates, the D-11 costing | none in behaviour; the three READMEs sit inside the customer, deal and lead images, so the next deploy rebuilds those three with no code change (§4.10) |
| R1.2a | The 360 path fix and its tests, the cross-service guard with its token-claim assertion, the organization claim on notification's three service tokens, the production-smoke step | small and reversible; rebuilds customer and notification (and deal and lead with no code change, because of R1.1's READMEs, §4.10); no schema, no container, no permission |
| R1.2b | Four missing event contracts with producer-side tests and the pins on the eight gaps, subscribable lead keys and the repaired recipe, ops-monitor relay coverage, the class-1 removals, `phone_index` stops being written, `make openapi` regeneration, the doc-drift test | small and reversible; rebuilds customer, deal, lead, notification and app-gateway and recreates ops-monitor, then two SPA publishes; no schema, no container, no permission |

**What SP1 delivers against P§1.** Criterion 2 (*one vocabulary*) and criterion 3 (*nothing dead is
shipped*) for everything CRM-owned that has no live user; criterion 6 (*no regression of the
platform's guarantees*) is held, not advanced. SP1 does not deliver criterion 1 (reps can work, SP2),
4 (the daily-use loop, SP3) or 5 (one customer identity, SP4).

**Non-goals.**

- No feature, no UI redesign, no change to any permission code, role template, route, request or
  response shape, or event payload or name that exists today (one strike-able exception: an
  offset-less `occurred_at` is read as UTC, §5.3.4). Four event *schemas* are added for payloads that
  already exist.
- No migration, no table, no container, no setting or flag, no change to the memory totals (ten
  sellable profiles 3747 MB, ten plus `admin` 3944 MB, by `memory_budget_totals.py`).
- The eight "ahead of the producer" schemas are **decided** (§5.3.4) and **pinned** by tests, not
  closed; the registry assigns each to its closing release (R2.3, R3.7, R4.1).
- No class-2 deletion (§5.4): fixtures mode, `Login.tsx`, the page-only CSV export, the
  `/deals/summary` fields and the per-card stage `Select` keep working until their replacements ship.
- No change under `libs/` except `libs/contracts`, so each deploy of R1.2 stays a per-service rebuild
  (`infrastructure/edge/lib/rebuild-decision.sh`, DD-20).
- No production data operation is *required* to ship R1.1 or R1.2; read-only owner checks accompany
  each R1.2 deploy (§6).

## 2. Decisions

### 2.1 Owner decisions this sub-project depends on

Each carries the recommended default and stays `(pending D-n)` until the owner answers. "SP1 use" is
the only thing the sub-project does with the answer; "if answered otherwise" says what changes.
ADR-0038 records M1–M4 and all forty-one decisions, D-1 to D-41 (§4.3); the rows below are the ones that
change SP1's own content. D-1 to D-4, D-6, D-8 to D-10, D-15 to D-18, D-21 and D-25 to D-41 are recorded
in the ADR and need no SP1 content. None of the rows blocks R1.1 or R1.2: where an answer would change
anything in SP1 it changes documents only, and each such statement is marked `(pending D-n)` (§6, O-1).

| # | Decision | Default | SP1 use | If answered otherwise |
|---|---|---|---|---|
| D-5 *(pending D-5)* | Stage model: one pipeline per org, immutable key, editable label, order, tone and probability, outcome `open`, `won` or `lost`, at least one won and one lost, archive never delete, closed deals immutable (the no-move rule; reopening is a separate decision) | as stated | The glossary defines `stage` and `outcome` as the *planned* vocabulary and says the stored keys are frozen; §5.3.4 assumes R2.3 adds `stage_outcome` to the deal events | Glossary rows and the deal rows of §5.3.4 change; R1.2 is unaffected |
| D-7 *(pending D-7)* | A rep is an `account` membership: `customer.owners` gains `membership_id`; Team activates members; free-form rep creation retires after cutover | as stated | Glossary rows `owner / rep`, `roster`, `membership`, `employee link` | Docs only |
| D-11 *(pending D-11)* | Fold `lead` into `customer`? | **Keep three processes** for this programme; revisit after R2 | Costed with the repo's own arithmetic (§4.8) and written into ADR-0038, with the supersession text for ADR-0017 decision 1 ready if the answer is yes | If yes: ADR-0017 decision 1 is superseded, `docs/crm.md`'s service map gains a "planned fold" note, and SP2, SP3 and SP4 are re-cut (the registry rows that change are listed in §4.8 and in registry §K). R1.2 is unaffected |
| D-12 *(pending D-12)* | Insights and stages landing order | Stages land first; `a1c9e2f7b3d4` is adopted unchanged; insights rebases onto `outcome` | ADR-0038 records the event contract (`outcome`, frozen keys, `closed_at`); §5.3.4's per-schema decisions assume it | If insights lands first, the pin rows for the three deal events are deleted by that merge (§5.3.4) |
| D-13 *(pending D-13)* | Memory ceiling | The programme assumed no increase | SP1 shows the assumption is already broken by R2.4's single fold (+1 MB over the box's accepted ceiling) and records the totals (§4.8); registry `L17` amends the decision to the five folds and the three further items SP4 adds. No SP1 release changes memory | If the owner holds the folds, SP2 re-plans R2.4; SP1 is unaffected |
| D-14 *(pending D-14)* | Party identity: company is the party, contact the person, phone-only contacts allowed | as stated | Glossary rows `company`, `contact`, `party` | Docs only |
| D-19 *(pending D-19)* | Quote home and item master: quotes in `deal`, `inventory_items` the one item master, the `quotation` scaffold deleted with the quote work | as stated | Class 3 keeps the `/api/v1/quotations` dev row (`routes.json:89`) and class 4 leaves `services/quotation` alone, because their deletion is R4.5's, with the quote work (§5.4); neither is touched here | If answered otherwise, the dev row and the scaffold are reclassified by the release that answers; R1.2 is unaffected |
| D-20 *(pending D-20)* | Role consolidation to `CRM_ADMIN`, `CRM_REP`, `CRM_MANAGER`, `CRM_VIEWER`; legacy codes and all 17 permission codes frozen | as stated | `docs/crm.md`'s role table carries a "planned mapping" column | Docs only |
| D-22 *(pending D-22)* | API nouns: `/companies` canonical (`/customers` aliased), 360 under `/contacts/{id}/360`, `/activities` aliased | as stated | The glossary names the target noun beside the current one | Glossary rows swap which noun is "current" |
| D-23 *(pending D-23)* | Removals that had live users are replaced first, then deleted | The class-2 list in §5.4 | R1.2 deletes only class 1; class 2 is recorded with its replacement and release | An item the owner wants gone earlier needs its replacement earlier; an item the owner wants kept moves to class 3 |
| D-24 *(pending D-24)* | TEAM scope from `manager_membership_id` through per-service `owner_team` | as stated | One glossary row (`scope`); one line in `docs/crm.md` | Docs only |

### 2.2 Design decisions made here

**DD-1 One programme ADR, `proposed`, number 0038; later numbers are reserved by a note.**
ADR-0000's rule is that a later ADR may refine but never quietly contradict it, and SP1 amends two
accepted ADRs (0017, 0008), so a decision record is required, not optional. One ADR holds M1–M4 and
D-1 to D-41 because the owner reviews them together and every one cross-references the others.
`docs/decisions.md` also records that 0039, 0041, 0042 and 0043 are expected by the registry's
follow-up ADRs (`crm-consent-and-erasure`, `crm-public-lead-capture`,
`approvals-for-tenants-without-hrms`, `crm-quotes-in-deal`; registry I2), so a parallel branch does
not take them. 0038 was confirmed free at write time: no ref carries a file numbered 0038 or 0039
(`git log --all --diff-filter=A -- 'docs/adr/003[7-9]*' 'docs/adr/004*'` lists only 0037 and 0040).
The index test requires the stated next number to exceed every ADR on `main` (§3.7), and
`0040-faster-ci.md` exists on another ref: if it reaches `main` before R1.1 does, the sentence reads
**0041** and the expected numbers shift with it (the registry confirms each number when its file is
written). *Rejected:* an ADR per decision (41 files, each depending on the others); no ADR, with the
decisions left in the programme spec (that file lives under `docs/superpowers/`, which neither
`docs/decisions.md` nor the open-questions register links).

**DD-2 Accepted ADRs are amended by dated notes, never rewritten.** The repo's precedent is
`**Amended 2026-09-17 — …**` inside ADR-0008 (`docs/adr/0008-membership-employee-link.md:25-26,143,253`)
and "Clarifying notes added" in ADR-0000. The only in-place text edits SP1 makes to an accepted ADR
are wrong citations (a line number is not a decision): `domain.py:38` becomes `domain.py:21` in
ADR-0017. *Rejected:* rewriting the limitation bullets, which would erase what was decided and why.

**DD-3 `docs/crm.md` is prose plus tables whose key sets a test ties to the contracts.** The event,
permission, role and port tables are hand-written; `tests/infra/test_crm_doc_matches_contracts.py`
(R1.2b) fails when an event type, permission code, role code or gateway port in the document differs
from `libs/contracts` and `services/app-gateway/routes.json`. *Rejected:* a generator
(`tools/export_crm_doc.py`): a second tool in the gate for about sixty table rows, and the prose would
still be hand-written.

**DD-4 The removal rule has four classes and a proof requirement.** A thing may be deleted when it has
no live user, where a live user is a referenced symbol, a test that exercises it, a fixture or
contract that pins it, a pixel baseline, or an external caller. Each deletion records its proof (the
search that returned nothing) in the pull request description. A thing with a live user is replaced
first. Class 1 is deleted now, class 2 is replaced then deleted, class 3 is kept, class 4 is not
CRM's to remove (§5.4). *Rejected:* delete-and-fix-forward. Discovery listed items as dead that
skeptics showed were live (fixtures mode, `Login.tsx`, the per-card `Select`, the `/deals/summary`
fields, most of `cards-dev-a-lead.md`), and 46 claims in total needed correction (the programme's
header).

**DD-5 The four placeholder routes go now; the 404 gets a real page.** Reports, Integrations, Settings
and Help render a "Coming soon" card and four of the eleven sidebar entries lead there. No code, test,
fixture, baseline or other app links to them (searches in §5.4), so nothing has to be replaced first
(the frontend rules file in `frontend/`: "zero placeholder or dead code"). The 404 route keeps working
through a new `NotFound` page, so `ComingSoon.tsx` has no user and is deleted. *Rejected:* waiting for
R3.4 as the first draft of registry `G15` listed it: it would re-baseline the same three visual surfaces
twice (now and in R3.4) and keep four dead ends in the product for months. This moved one `G15` item
earlier, and `G15` now says so.

**SP1 owns this removal and SP3 does not repeat it.** The same files were listed as SP3/R3.4 work in
the first draft of registry `G15` (since folded: `G15` names SP1/R1.2b for them) and in SP3 as first
written: its replace-then-delete ledger (§8.6.5: the `ComingSoon.tsx` and five-route row, and the
`derive.ts` row, which deletes a ten-export file whole and so would delete SP1's `derive.test.ts`
again) and its token-baseline table (§4.4.7: `ComingSoon.tsx`, 4 offenders, in a total of 101). Left
alone, R3.4's tasks would target files that are already gone, and the two specs name the 404 component
differently (`NotFound` here, `NotFoundPage` in SP3 §4.4.5 and §8.6.4). It is `routes/NotFound.tsx`,
named once. §9.2.1 lists the exact edits SP3 takes before the four specs are approved together, and
ADR-0038's "What this amends" records the move (§4.3). If the owner strikes DD-5 (O-15) the whole move
reverts as one unit (class-1 rows 5 and 8): the placeholders, the 404 page and the dead `derive.ts`
rollups stay with R3.4 (and registry `G15` is amended back), and R1.2b keeps only removals no other
sub-project touches.

**DD-6 The 360 fix is a path correction in `c1_360.py`; there is no new route and no platform-core
constant.** *Rejected:* a dedicated deal `/internal/v1` list route (the 360 deliberately forwards the
caller's own identity so deal applies the caller's scope, `customer/app/c1_360.py:18-22`; an internal
route would need a second read model and an allow-list for something the public route already serves);
a `platform_core.app.API_PREFIX` constant shared by `create_app` and the client (it touches
`libs/platform-core`, which makes the deploy a full image rebuild).

**DD-7 The path bug class is closed for the CRM-adjacent services by two tests and a guard, and the
guard watches every service.** Layer 1 (hermetic, in customer): the fake deal upstream answers only the
routes the committed OpenAPI document declares, so a wrong mount path is a 404 exactly as in
production. Layer 2 (`tests/infra`): every registered internal-client call is recorded from the real
client code, probed against the real callee app over ASGI, and its service token is decoded to check
the `org` claim against the organization in the path (a defect of the same family that a path check
cannot see, registry `C77`); the guard scans every `services/*/app` tree and fails when a file that
builds an internal URL is neither registered nor on an explicit baseline of unaudited files. The audit
behind the nine registered calls read five services (customer, deal, lead, finance, notification), so
"closed" is claimed for those five only: 17 files in nine other services use the same URL settings and
were not audited (§3.4), so they are the baseline, unaudited and not clean. Fourteen of the new
internal-call rows that SP4 owns sit in seven services outside the five (operations, support, project,
projects, storefront, marketing and approval: registry `W4`–`W8`, `W22`, `W25`, `W27`, `W34`, `W35`,
`W38`, `W39`, `W41`, `W42`) and eight more in the insights service of the unmerged stack
(`W44`–`W51`), which is why the scan is not limited to the five. *Rejected:* an end-to-end test alone
(the e2e harness boots neither `lead` nor the CRM consumers, registry `L14`, and agents never run e2e
against the box); a hand-listed expected path (the current bug *is* a hand-listed expected path,
`customer/tests/test_c1_customer360.py:154`); auditing the 17 baseline files here (they are not CRM's,
so reading their clients and callees is a platform item, §8).

**DD-8 The four missing contracts match today's payloads exactly.** `additionalProperties: false`,
nullable ids as `["string", "null"]`, no free text. The legacy `full_name` on
`crm.book.contact.created` is documented as frozen (registry `J12`). Fields the later releases add
(`crm_company_id`, `owner_membership_id`, `direction`, the new activity types) are added by those
releases in the same commit as their producers (§D rule).

**DD-9 For the eight ahead-of-producer schemas the producer moves, the schema never relaxes, and
R1.2 changes neither.** R1.2 pins each gap with a characterization test so it cannot drift silently and
the closing commit cannot forget its test. *Rejected:* closing the producers in R1.2 (the unmerged
`a4b248f1` on `sprint/insights-q2` and `ui/sprint-integration` already contains exactly these producer
changes and their tests, so R1.2 would collide with it; the deal triple also needs `closed_at`, the
stage-key pattern and `stage_outcome` from R2.3, and the lead triple needs the widened `source` from
R3.7); relaxing the required lists (the schemas are the forcing function insights consumes; relaxing
lets a fact projection fold incomplete state).

**DD-10 CRM contract coverage is enforced statically.** Two tests in `libs/contracts/tests`: every
`EVT_*` constant the three services declare has a committed schema, and every `crm.*` key in
notification's subscribable list has one. *Rejected:* every product at once (support, itsm,
marketing, pulseboard and projects lack schemas for reasons outside this programme; CRM first, as the
discovery recommended).

**DD-11 Four lead keys become subscribable and the starter recipe is retargeted, not dropped.**
Registry `EV39` lists `crm.lead.created`, `.updated`, `.converted` and `.conversion_aborted`. The
broken "Welcome a new lead" recipe cannot work as written (no lead event carries an address or a
contact id), so it becomes "Welcome a converted lead" on `crm.lead.converted`, whose payload already
carries `contact_id` (§5.3.5). The repaired recipe sets `purpose: "marketing"`. It is the first
shipped starter that messages a CRM contact straight from a CRM event, it lands before the consent and
erasure design (R4.1) and before a lead carries consent evidence (R3.7), and a contact created by a
conversion has no consent event: under the ledger a marketing send is allowed only after an `opted_in`
event (`notification/app/consent.py:1-30`), so with no opt-in on file the step is a recorded skip, not
a message. *Rejected:* making only `created` and `converted` subscribable (the registry fixes four);
deleting the recipe (the repair is one trigger, one recipient path and the optional `recipient_kind`
that Engage's type lacks and the engine already accepts; the owner may still strike it, O-15); leaving
the engine's default purpose `transactional` (`a1_services.py:972`), under which a conversion-created
contact is messaged unless the chosen template happens to be registered as MARKETING at Meta
(`consent.py:337-356`), a fact about each tenant that SP1 cannot know.

**DD-12 ops-monitor: the relay list gains `lead`; the DLQ queues follow their workers; the stale
platform-core comment waits.** `tests/infra/test_ops_monitor_dlq_queue_sync.py:83-129` fails if a
queue is listed without a `./bin/worker` compose service and the reverse, and a queue exists only once
its consumer has run (`events.py:465-471`), so each DLQ entry ships with its fold (registry `F5`).
Correcting the stale comment at `libs/platform-core/src/platform_core/ops_monitor/config.py:96-100`
touches `libs/`, which forces a full rebuild; it waits for R2.4, which edits that file anyway.

**DD-13 The three `bin/cron` relay copies are deleted, not stubbed.** `services/leave/bin/cron` and the
current `tools/new_service.py:439-444` template show the alternative: a stub that prints "no scheduled
work" and exits 78. It keeps the documented "three entrypoints" shape, but a file that exists only to
refuse to run is dead by this programme's own rule, and nothing runs it (`Makefile:121-126` runs
`app.cron`, which these services lack; compose has no CRM cron service). R3.6's daily review (`EV27`)
is armed through the scheduler and handled by deal's consumer (`EV44`), so it needs no cron entrypoint;
a release that does need one creates `app/cron.py` and `bin/cron` then.

**DD-14 `activities_for_deal` moves to the tests.** `deal/app/selectors.py:138-151` is an unscoped
read inside the module the service anatomy reserves for scoped reads (selectors "call
`ctx.apply()`"), used only by seven test call sites. *Rejected:* keeping it, or rewriting it as a
scoped selector no route needs.

**DD-15 `phone_index` is retired in two steps and the second is conditional.** R1.2b stops computing it
and maps the column `deferred=True`, so no statement the running code issues names it; the column
stays. The contractive step (remove the attribute and drop the column in one change, safe because the
running code no longer references the column) ships only if R3.7 does not resume the index; the
registered default (`B40`, `L2`) is that R3.7 resumes writing, adds the index and runs the owner-run
backfill `H8`, in which case the drop is cancelled, not deferred (§5.2). *Rejected:* dropping now
(R3.7 needs the index); never stopping (it keeps minting never-read equality values under the
platform-wide key, `docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md:128-138`). The
cost of stopping is that leads created between R1.2b and R3.7 carry `NULL` and need `H8`'s pass; the
owner can strike item R1.2-11 and keep computing.

**DD-16 D-11 defaults to "keep three processes".** The fold would save 98 MB but supersedes an
accepted ADR and needs an irreversible cross-schema data move (§4.8). SP1's job is to put real numbers
in front of the owner, not to pre-empt the answer.

**DD-17 The three service READMEs become run instructions only.** They never list endpoints,
permissions or events: those live in the committed contracts and in `docs/crm.md`. All three were
stale in every table (§3.6). *Rejected:* refreshing the tables, which would go stale again at the next
route.

**DD-18 The production smoke gains one read-only 360 step.** `e2e/edge_product_crud.py::flow_crm`
creates a company, contact and deal and deletes them; one `GET /customers/{contact_id}/360` between the
stage move and the deletes makes the owner's standard post-deploy check cover the fix. It is a read of
data the flow just created. The run itself is not free: `bootstrap_owner` signs up a throwaway
organization (`e2e/edge_lib.py:165-290`, the signup at `:192-200`) that nothing in either script deletes,
and the script's own docstring says it "creates real organisations on the production box"
(`edge_product_crud.py:3-20`). `flow_crm` removes only its own deal, contact and company (`:435-450`):
the organization and the roster owner the flow creates (`:337-347`) stay on the box until the owner runs
`tools/purge_test_orgs.py` (ADR-0036; RUNBOOK "Removing test organisations", which says every
end-to-end and smoke run leaves one behind, `RUNBOOK.md:2206-2207`). *Rejected:* extending the local
e2e harness (`L14`).

**DD-19 The CSV formula-injection exposure is recorded, not fixed, with an ordering constraint.**
`product-crm/src/lib/csv.ts` quotes cells but does not neutralise a leading `=`, `+`, `-` or `@`
(skeptic finding). The only writer of company names today is a tenant's own user; the first external
writer is R3.7's public capture. The page-only CSV export is deleted in R3.4 (class 2), so the
constraint is: **R3.4 deploys before R3.7**, or a three-line guard in `csv.ts` ships first. *Rejected:*
changing an export path in R1.2 that R3.4 deletes.

**DD-20 Neither R1.2 deploy touches anything under `libs/` except `libs/contracts`, or any
`pyproject.toml`, `uv.lock` or `.dockerignore`.** `needs_full_rebuild` returns true for any diff there
(`infrastructure/edge/lib/rebuild-decision.sh`, `git diff --quiet … -- libs ':!libs/contracts'
pyproject.toml uv.lock .dockerignore`), and a full recreate of "all ~60 containers" outlasts any retry
window on this box (the script's own comment, `rebuild-decision.sh:20-25`; the compose file declares
50 services). This is a constraint on content, checked by `AC-1.2.14`.

## 3. Current state (re-verified at `3bc7f6ca`)

### 3.1 Processes, gateway and memory

| | customer | deal | lead |
|---|---|---|---|
| Schema, role | `customer`, `opsgrids_customer` (`customer/app/config.py:9-15`) | `deal` | `lead`, `opsgrids_lead` (`lead/app/config.py:9-15`) |
| Dev port and prefixes | 8130: `/api/v1/customers`, `/api/v1/contacts` | 8131: `/api/v1/deals` | 8135: `/api/v1/leads` |
| Gateway rows | `services/app-gateway/routes.json:76-77`; prod `infrastructure/edge/routes.prod.json:106-107` | `routes.json:78`; prod `:108` | `routes.json:81`; prod `:109` |
| Alembic head (revisions) | `e230c025337e` (8) | `e94d7a2c5f18` (3) | `91dd3ca663fd` (5) |
| Memory kind (price) | `api_with_worker` (99 MB), `memory-budget.json:310` | `api` (98), `:320` | `api` (98), `:321` |
| Entrypoints in `bin/` | `api`, `relay`, `worker`, `cron` | `api`, `relay`, `worker`, `cron` | `api`, `relay`, `cron` (no `worker`, no `app/consumers.py`) |
| `bin/cron` | `exec python -m app.relay` | same | same |
| Consumer | folded into the API (`config.py:23`, `app/main.py`); one binding, `crm.pipeline.activity.logged` (`customer/app/consumers.py:97`) | `app/consumers.py:1-29` declares queue `deal.events` and registers **no handler**; no `deal-worker` in compose | none |

- The dev table also routes `/api/v1/campaigns` to 8132 and `/api/v1/tickets` to 8133 under product
  `crm` (`routes.json:79-80`). No service of either name exists (`ls services`), nothing else
  references ports 8132 or 8133, and the prod table has neither prefix. `RouteTable.resolve` matches
  `path.startswith(prefix)`, longest prefix first (`gateway.py:233,253-254`).
- One product key, `crm`; the marketplace and SPA name is "OpsGrids Revenue"
  (`product-crm/src/main.tsx:46`, `product-crm/src/App.tsx:79`); production host
  `https://revenue.opsgrids.com` (`opsgrids-client/src/platform/catalogue.ts:71`).
- `python3 infrastructure/edge/lib/memory_budget_totals.py infrastructure/edge/memory-budget.json
  --profiles …` prints: core only **1772**; core plus `crm` **2067** (so the three CRM containers cost
  295); all ten sellable profiles **3747 MB (97.02% of the 3862 MB reference)**; ten plus `admin`
  **3944 MB (102.12%)**. Soft line 85% = 3282, hard line 100% = 3862, accepted maximum 125% = 4827
  (`memory-budget.json`, `host`). The box's `EDGE_BUDGET_ACCEPTED_MB=3944` is the owner's note and
  cannot be verified from the repo (skeptic); the owner confirms it with `make edge-preflight`.
  ADR-0017's "94.4%" (`docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md:206-210`) is a
  figure from before the memory re-pricing.

### 3.2 Events and contracts

Fifteen event types are published (`customer/app/services.py:119-124`, `deal/app/services.py:43-47`,
`lead/app/services.py:71-77`). Eleven have a committed schema, eight of those are **ahead of the
producer** (their own `$comment` says which fields the producer does not emit), four have none:

| Event | Emitted today (file:line) | Schema | State | Bound consumers today |
|---|---|---|---|---|
| `crm.book.company.created` | `organization_id, company_id, name, status, owner_id` (`customer/app/services.py:482-488`) | yes | ahead: lacks `created_at` | notification (subscribable, `notification/app/domain.py:147`) |
| `crm.book.company.updated` | `organization_id, company_id, changed` (`:552-556`) | yes | ahead: lacks `name, status, owner_id` | none |
| `crm.book.company.deleted` | `organization_id, company_id` (`:603`) | yes | conforms | none |
| `crm.book.contact.created` | `organization_id, contact_id, company_id, full_name, status` (`:695-701`) | **none** | — | notification (`domain.py:148`) |
| `crm.book.contact.updated` | `organization_id, contact_id, changed` (`:763-767`) | **none** | — | none |
| `crm.book.contact.deleted` | `organization_id, contact_id` (`:806`) | **none** | — | none |
| `crm.pipeline.deal.created` | `organization_id, deal_id, name, stage, value_minor, currency, owner_id` (`deal/app/services.py:175-183`) | yes | ahead: lacks `company_id, close_date, probability, closed_at, created_at` | notification (`domain.py:150`) |
| `crm.pipeline.deal.updated` | `organization_id, deal_id, changed` (`:247-251`) | yes | ahead: lacks `name, stage, value_minor, currency, owner_id, company_id, close_date, probability, closed_at` | none |
| `crm.pipeline.deal.deleted` | `organization_id, deal_id` (`:291`) | yes | conforms | none |
| `crm.pipeline.deal.stage_changed` | `organization_id, deal_id, from, to` (`:339-344`) | yes | ahead: lacks `name, value_minor, currency, owner_id, probability, closed_at` | notification (`domain.py:151`) |
| `crm.pipeline.activity.logged` | `organization_id, activity_id, type, deal_id, contact_id, occurred_at` (`:428-435`) | **none** | — | customer (`customer/app/consumers.py:54-97`), notification (`domain.py:149`) |
| `crm.lead.created` | `organization_id, lead_id, source, owner_id` (`lead/app/services.py:538-543`) | yes | ahead: lacks `status` | none (not subscribable) |
| `crm.lead.updated` | `organization_id, lead_id, changed` (`:647-651`) | yes | ahead: lacks `status, source, owner_id, converted_at` | none |
| `crm.lead.converted` | `organization_id, lead_id, company_id, contact_id, deal_id` (`:1199-1205`) | yes | ahead: lacks `owner_id, converted_at` | none |
| `crm.lead.conversion_aborted` | `organization_id, lead_id, reason, company_id, contact_id, deal_id` (`:1156-1161`) | yes | conforms; tested against its producer (`lead/tests/test_lead_lifecycle_races.py:269,292`) | none |

The "lacks" lists were derived by comparing each schema's `required` array with the payload above; each
equals the schema's own `$comment`. Further facts:

- Only `lead` has a producer-side schema test; `customer` and `deal` have none (`grep
  validate_event_schema services/{customer,deal}/tests` finds nothing). The static contracts test
  requires a schema only for `engage.*` events and for `crm.lead.conversion_aborted`
  (`libs/contracts/tests/test_event_schemas.py:58-114`), and its docstring at `:94-97` still says the
  other lead events have no schema.
- In a checkout, the Engage automation builder lists a trigger's variables from the committed schema's
  `properties` (`notification/app/a1_selectors.py:260-284`), so an "ahead" schema offers variables the
  producer does not send and an event with no schema offers none. **On the box it lists none for any
  trigger:** `libs/contracts` is excluded from every image (`.dockerignore:29-32`), so
  `find_contracts_dir` finds nothing (`a1_selectors.py:208-223`). The committed schemas therefore
  document and test the contract; they do not feed the production builder. Rendering is strict: a
  placeholder that resolves to nothing raises, the rule logs `notification.rule.render_failed` and
  stages nothing (`domain.py:276-301`, `notification/app/services.py:841-850`).
- Notification binds **every** key of `SUBSCRIBABLE_EVENTS` unconditionally at start
  (`notification/app/consumers.py:324-325`); a rule may only name a key in that list
  (`domain.py:365`). The CRM group is `domain.py:146-151`: five keys, no lead key.
- An event with no bound queue is dropped while its outbox row is marked published
  (`events.py:250-262,400`); a queue exists only once its consumer has run (`events.py:453-471`).
  Every `crm.lead.*` event emitted so far was therefore dropped by the broker.
- The CRM services make HTTP calls to other services in exactly two places: the 360's two legs
  (customer) and lead's conversion and owner-validation calls; `deal` makes none (§3.4).

### 3.3 The Customer 360 deal leg

- `customer/app/c1_360.py:166-170` calls `f"{cfg.deal_internal_url}/deals"` with `company_id` and
  `size=200`, forwarding the caller's identity headers. `create_app` mounts every router under
  `/api/v1` (`app.py:362-363`); deal's public list route is `@router.get("/deals", …)`
  (`deal/app/api/routes.py:78-84`), so its real path is `/api/v1/deals`; deal's only root-mounted
  router is the internal one (`deal/app/main.py:10-19`). `DEAL_INTERNAL_URL` is
  `http://deal:8000` (`compose.prod.yml:1437`); the dev default is `http://localhost:8131`
  (`customer/app/config.py:29`); neither carries a path. `/deals` is therefore a 404. This is
  *inference from the route table*, not an observation on a running stack; §6 asks the owner to confirm
  it with one read before and after.
- The forwarding itself is sound: a gateway hop token's audience is the forwarding channel
  (`internal_auth.py:64,120-140`), `get_principal` accepts it at any service (`auth.py:208-244`), so
  the caller's permissions and scope reach deal once the path is right. The module docstring
  (`c1_360.py:18-22`) states that design.
- The only assertion on the path is wrong: `customer/tests/test_c1_customer360.py:154` asserts
  `deal_call.url.path == "/deals"`, and the `Fake` answers by port, whatever the path
  (`:68-74`), so every other 360 test passes with any path. The failure is silent: the section is
  `null` and named in `unavailable` with a warning `customer.360.section_unavailable`
  (`c1_360.py:222-227`).
- The route already has its negative tests: 401 unauthenticated, 403 missing `crm.book.contact.read`,
  403 OWN scope on another rep's contact, 404 another organization's contact, 404 unknown id or a
  company id, 402 without the `crm` entitlement
  (`customer/tests/test_c1_customer360.py:298-368`, `TestEntitlements` and `TestAuthorization`).

### 3.4 Internal clients: the same-class audit

Every place the CRM services, finance and notification build a URL for another service
(`grep "_internal_url\|_service_url" services/{customer,deal,lead,finance,notification}/app`, excluding
`config.py`; the internal routers carry neither name). `deal` builds none. Nine call sites in six files:

| # | Caller | Route the callee serves | URL the client builds | Path verdict | Wiring (`compose.prod.yml`) |
|---|---|---|---|---|---|
| A1 | `customer/app/c1_360.py:166-170` | `GET /api/v1/deals` (`deal/app/api/routes.py:78-84`, public router, `app.py:362-363`) | `{deal_internal_url}/deals` | **wrong: 404** | `DEAL_INTERNAL_URL` set, customer block `:1437` |
| A2 | `customer/app/c1_360.py:200-203` | `GET /internal/v1/contacts/{contact_id}/messaging-summary` (`notification/app/api/c1_internal.py:81-86`, root-mounted `notification/app/main.py:169`) | same path | correct | `NOTIFICATION_INTERNAL_URL` set `:1436` |
| A3 | `lead/app/clients.py:104-105` | `POST /internal/v1/organizations/{org}/leads:convert` (`customer/app/api/internal.py:290-296`) | same path | correct | `CUSTOMER_INTERNAL_URL` set, lead block `:1481` |
| A4 | `lead/app/clients.py:184-185` | `POST /internal/v1/organizations/{org}/leads:convert` (`deal/app/api/internal.py:94-100`) | same path | correct | `DEAL_INTERNAL_URL` set `:1482` |
| A5 | `lead/app/clients.py:249-250` | `POST …/owners:validate` (`customer/app/api/internal.py:351-357`) | same path | correct | as A3 |
| A6 | `finance/app/clients.py:66-67` | `GET …/contacts/{contact_id}` (`customer/app/api/internal.py:244-250`) | same path | correct | **none**: finance runs `environment: *service-env`, so `CUSTOMER_INTERNAL_URL` falls to `http://localhost:8130` (`finance/app/config.py:52`). Latent: nothing calls it, `operations/app/orders/services.py:871-892` returns `None` |
| A7 | `notification/app/targets.py:73-76` | `GET …/contacts/{contact_id}/messaging-target` (`customer/app/api/internal.py:138-144`) | same path | correct | `CUSTOMER_INTERNAL_URL` set, notification block `:1188` |
| A8 | `notification/app/contacts.py:74-77` | `POST …/contacts:resolve` (`customer/app/api/internal.py:197-203`) | same path | correct | as A7 |
| A9 | `notification/app/memberships.py:48-51` | `GET …/memberships/{membership_id}` (`account/app/api/internal.py:304-346`) | same path | correct | **none**: no `ACCOUNT_INTERNAL_URL` line; `notification/app/config.py:91` defaults to `http://localhost:8111`; compose sets only `ACCOUNT_SERVICE_URL` (`:103`), a different name |

Result: **one path defect (A1)**. Two wiring findings that are the same class of failure (an internal
call that cannot reach its callee in production) but not the same bug: A6 is latent and is fixed by
registry `W3` in R4.3; A9 is *inference*: `membership_is_active` answers `False` on any connection
error (`notification/app/memberships.py:62-64`), so in production every Engage conversation
assignment would be refused. The registry leaves it unscheduled (`L3`); §6 asks the owner to confirm
it from logs. Token claims: lead and finance mint with an `org` claim (`lead/app/clients.py:106-111,
186-191,256`; `finance/app/clients.py:73`); notification's two customer calls mint with **none**
(`notification/app/targets.py:78-80`, `contacts.py:79-81`), and so does its third mint, to account (A9,
`notification/app/memberships.py:53-55`); account's route reads no claim and checks only the caller's
`iss` (`account/app/api/internal.py:304-346`). Registry `C77` fixes all three in R1.2a, so that every
call under `/internal/v1/organizations/{org}/` names its organization and the guard of §5.3.2 can
assert it without an exception list (§5.3.3).

Outside these five services the same setting names appear in 17 files of nine services
(`grep -rl '_internal_url\|_service_url' services/*/app`, minus each `config.py`). Read from the
matching lines only: five build `/internal/v1/…` paths in place (`console/app/catalog_client.py`,
`delegation.py`, `price.py`, `subscription_client.py` and `operations/app/orders/payment_client.py`), and
twelve pass a `*_service_url` to an `Http*` client defined elsewhere (`admin-gateway/app/main.py`,
`app-gateway/app/main.py`, `approval/app/backfill.py`, `auth/app/main.py`, `billing/app/api/internal.py`,
`catalog/app/bundle_audit.py` and `main.py`, and `subscription/app/backfill.py`, `billing_run.py`,
`cron.py`, `main.py`, `worker.py`). This audit read none of those clients and probed none of their
paths: they are **unaudited, not clean**, and §5.3.2 lists them as the guard's baseline.

### 3.5 Dead and duplicate code

The per-item proofs live with the removal table (§5.4), where each is a search whose result the pull
request records. Two facts that are not tied to one deletion: the three `/…/info` routes
(`customer/app/api/routes.py:125`, `deal/app/api/routes.py:66`, `lead/app/api/routes.py:123`) are
unreachable through the gateway (`/api/v1/customer/info` is not prefixed by `/api/v1/customers`) and
are one instance of a scaffold carried by 32 services (`grep` counts 32 `ServiceInfo` schemas and 32
`/info"` routes); and `tools/new_service.py:439-444` already generates the "no scheduled work" form of
`bin/cron`, so the CRM copies are leftovers of an older template.

### 3.6 Documents that state things that are no longer true

- Service READMEs: `customer/README.md:11,15-20,44-50,62-66,68-77` ("none registered this slice",
  "read-only today … not yet exposed as public routes", five of seventeen operations, four of eight
  permissions, two of six events, "Consumes: Nothing yet"); `deal/README.md:10,14-20,45-59,68-72,
  74-83` (same shape, plus a pipeline-trend description the code dropped); `lead/README.md:1-21` (the
  generator stub, run on `PORT=8110`, which is auth's port, and `make worker` for a service with no
  worker).
- `docs/features.md:57-60` ("None of them exist … No product service has been written") and `:88-92`
  ("Any consumer handling any event: Absent"); `docs/api-reference.md:114-118` ("No product API
  exists"); `docs/ecosystem-gaps.md:18-20` ("No product event is consumed by anything"), `:35` (lead
  and pipeline "were scaffolds … Planned"), `:77-91` (§2 "Lead and pipeline (planned)"), `:105-116`
  (§4 lists `activity`, `scheduler` and `document` as generated templates); `docs/plan.md:11` and `:39`
  ("Sales CRM" as a product; "CRM greenfield: customer, sales, marketing, support");
  `docs/architecture.md:143-145` (campaigns and tickets "dead entries"), `:351-360` ("three
  entrypoints"), `:514-515` (product-crm defaults to fixtures); `docs/backlog.md:503`,
  `e2e/_seed_product.py:20-24`
  and `compose.prod.yml:1467-1471` (CRM has "two admin templates"; lead "publishes only
  `crm.lead.converted`").
- Onboarding: `cards-dev-b-pipeline.md` (460 lines, banner `:1-5`, entirely superseded);
  `cards-dev-a-lead.md:1-4` (banner, correct), `:13-31` and `:15` (the `salescrm` framing), card A4
  `:280-372` (`SALESCRM_*` roles), `:439-443` (a next card against the deleted `services/activity`);
  `cards-dev-d-crm.md:10-13` ("exactly one test file"), `:56` ("the only existing test in the
  frontend"), `:277,282` ("seven screens"). The frontend has 521 test files, 37 in `product-crm`.
  `docs/onboarding/README.md:9-10,54-58` already says the right thing about A4 and the missing lane B
  (skeptic), but its row for card B links a file R1.1 deletes.
- ADR text: ADR-0017 decision 1 (`:41-49`) says `CRM_SALES_REP` and `CRM_DEAL_REP` "each pick up the
  lead grants a working rep needs"; the code did the opposite: neither template holds any `crm.lead.*`
  grant and `lead` added a third default role `CRM_LEAD_REP` (`lead/app/catalogue.py:150-176`;
  `libs/contracts/permissions/crm.json`, six templates, three admin and three default). ADR-0017
  `:56-57` cites `domain.py:38`; it is `deal/app/domain.py:21`. ADR-0008 Q5 `:834-836` says owner
  values "are migrated from employee ids to membership ids through account's link", which does not
  match the data (P§3 F2). The register carries the same premises (`open-questions-register.md:56,95-98,
  101-103,120-122,135`), and a name collision: "C15" there is a CRM slice, while
  `docs/whatsapp/audit.md:95,239` use `C15` for "Human handoff".

### 3.7 Deploy mechanics that bind this sub-project

- Merges do not deploy; the owner merges (the permission gate stops agents merging their own pull
  requests) and names the ref: `make deploy HOST=… CONFIRM=… [REF=…]` (`Makefile:569`). With Actions
  billing blocked, a local `make gate` is the only full verification (P§3 F11). Production may lag
  `main`: a CRM deploy from `main` also ships everything merged since the live ref, so the owner reads
  `make edge-status` first.
- Migrations run before `up`, so old code runs against the new schema for the whole deploy; SP1 has
  none. `needs_full_rebuild` is true for any diff under `libs` other than `libs/contracts`, or in
  `pyproject.toml`, `uv.lock`, `.dockerignore` (`infrastructure/edge/lib/rebuild-decision.sh`), and a
  full recreate "outlasts any retry window" on this box (the script's own comment).
- A deploy rebuilds every service whose own directory changed between the last good ref and `HEAD`,
  and nothing narrower (`infrastructure/edge/remote.sh:191-196`; each Dockerfile copies the whole
  directory, `services/customer/Dockerfile:22`), so a README or a dead dev route in a service directory
  is still a rebuild and, because the image id changes, a recreate (inference: compose recreates a
  container whose image changed) of that service. The only rollback is the whole ref: `make
  edge-rollback` redeploys the previous good ref for every service (`Makefile:577-583`), which is why R1.2
  is two deploys (§5.9, §5.10). The deploy's own `health` step waits for every container to be up and
  healthy before it records the ref (`remote.sh:386-412`).
- `tests/infra/test_purge_test_orgs.py:1205-1219` pins `docs/decisions.md`: the stated "next free
  number" must not be an ADR that exists and must exceed every one that does. Adding
  `0038-crm-programme-decisions.md` therefore forces that sentence to read **0039** in the same change
  (`docs/adr` ends at 0037 here). `0027-inventory-scan-and-bulk-data.md` and `0040-faster-ci.md` exist on
  other refs only; if 0040 merges first the number must exceed it and reads **0041** (DD-1, O-2).
- The edge SPA bundle is built from the local checkout (`make edge-web`): the owner checks out the
  deployed ref first and greps the published bundle for the feature.

### 3.8 Unmerged work that touches the same files

`a4b248f1` ("insights: deal, lead and customer facts (slices I3, I4)") is on `sprint/insights-q2` and
`ui/sprint-integration` and is **not** an ancestor of `HEAD`. It contains exactly the producer changes
the eight ahead-of-producer schemas wait for (it deletes the `$comment` line from each of the eight
schema files), a producer-side test per service named `test_insights_event_contracts.py`, deal
migration `a1c9e2f7b3d4` (parent `e94d7a2c5f18`), and internal export routes. Of the 44 files it changes
(`git show --stat a4b248f1`), R1.2b edits four, each in a different, non-adjacent hunk:

| File | R1.2b edits | `a4b248f1` edits |
|---|---|---|
| `deal/app/selectors.py` | removes `activities_for_deal` (`:138-151`) | the import block, and an export selector added after `deal_out` |
| `deal/app/services.py` | reads an offset-less `occurred_at` as UTC in `log_activity` (after `:376`, §5.3.4) | the payloads of `create_deal`, `update_deal` and `move_stage`; its last hunk ends in `move_stage`, before `log_activity` |
| `deal/app/schemas.py` | rewrites the `ActivityIn` docstring (`:157-160`, R1.2-10 and R1.2-12) | the export models, appended at the end of the file |
| `lead/app/services.py` | removes the `phone_index` lines (`:465-468,496`) | fields added to the `create_lead`, `update_lead` and `finalize_conversion` payloads, from `:536` |

R1.2b does not touch any of the eight schema files, and its new test files have different names from
the insights ones. The plan lists all four files as rebase points: whichever stack merges second
re-applies its hunk on top of the other (precedent: the `RE-PARENT HERE` marker).

## 4. R1.1 — Decisions ADR, glossary, CRM doc and stale-doc rewrite (documents only)

### 4.1 Purpose and scope

**Purpose.** Give the programme one decision record, give the CRM one vocabulary and one document, and
stop the repository describing a CRM that no longer exists. Nothing is deployed and no behaviour
changes. Every changed file is a document, and the only ones outside `docs/` are the three service
READMEs (R1.1-8): they sit inside the customer, deal and lead images, so the next deploy rebuilds those
three with no code change (§3.7, §4.10). Needs: nothing (P§10). Size: M.

| Item | File | Action |
|---|---|---|
| R1.1-1 | `docs/adr/0038-crm-programme-decisions.md` | new; `Status: proposed`; outline §4.3 |
| R1.1-2 | `docs/decisions.md` | add the 0038 row; the sentence at `:9-11` becomes "The next free number is **0039**" (the first number above the highest ADR on `main`, §3.7), its parenthetical gains 0040 (`faster-ci`, claimed on another ref) and one clause naming 0039, 0041, 0042 and 0043 as expected by the CRM programme (registry I2), and the "apart from 0028–0033 and 0035–0037" range gains 0038. The invariant test pins the number |
| R1.1-3 | `docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md` | dated **Amended 2026-10-07** notes at the places in §4.7; one citation corrected in place |
| R1.1-4 | `docs/adr/0008-membership-employee-link.md` | one **Amended 2026-10-07** note after `:837`, the end of the paragraph that holds the migration recipe (`:834-836`) |
| R1.1-5 | `docs/adr/0020-insights-projection.md` | two dated notes: after Risk 1 (`:641`, D-13: memory) and at the end of §6 (`:343`, D-12: the stage contract) |
| R1.1-6 | `docs/adr/open-questions-register.md` | row updates in §4.7 |
| R1.1-7 | `docs/crm.md` | new; outline §4.5 |
| R1.1-8 | `services/{customer,deal,lead}/README.md` | rewritten to run instructions, §4.6; the only files outside `docs/` |
| R1.1-9 | `docs/features.md`, `docs/api-reference.md`, `docs/ecosystem-gaps.md`, `docs/plan.md`, `docs/architecture.md`, `docs/backlog.md` | CRM statements corrected, §4.7 |
| R1.1-10 | `docs/onboarding/cards-dev-b-pipeline.md` | **deleted**; `docs/onboarding/README.md:10` loses its row |
| R1.1-11 | `docs/onboarding/cards-dev-a-lead.md`, `docs/onboarding/cards-dev-d-crm.md` | card A4 and the `salescrm` framing only; the stale statements of card D, §4.7 |
| R1.1-12 | the D-11 costing | recorded in ADR-0038 and in `docs/crm.md` §10; computed in §4.8 |

### 4.2 Data model, backend contract, domain rules, consumers, UI

None. No table, column, migration, route, permission, role, event, flag, setting or container is
created, changed or removed. The three heads stay `e230c025337e`, `e94d7a2c5f18` and `91dd3ca663fd`.
The new rules ("every new table carries `organization_id`, RLS and an app-level org filter"; "every new
endpoint has a permission, a scope floor and a named negative-authz test") are vacuous here and are
stated for the release that follows. ADR-0038 records future DDL and routes by registry handle and
never restates them, so the registry stays the single source of names.

### 4.3 ADR-0038 outline

```
# ADR-0038 — CRM programme decisions
Status: **proposed** · Date: 2026-10-07
Builds on ADR-0000 (§5, §6, §7, §8, §10, §11), ADR-0007, ADR-0008 (Q5), ADR-0015, ADR-0017,
ADR-0020, ADR-0021, ADR-0022, ADR-0035, ADR-0036. Amends ADR-0017 and ADR-0008 (Q5) by dated notes.
```

Sections, in order: **Context** (five lines pointing at P§3's facts F1–F11 rather than restating
them); **Decisions** (the table below, with the row grammar that follows it); **What this amends**
(§4.7's list, so a reader of ADR-0017 or ADR-0008 can find the note, plus one line recording that the
four placeholder routes, the 404 page and the dead `derive.ts` rollups are removed in R1.2b and not in
R3.4, so registry `G15` already reads so and SP3's ledger follows, DD-5, §9.2); **C15, named** (below);
**Costing of D-11** (§4.8); **Consequences** (the release train, P§10, and which release each open
decision blocks); **Sign-offs** (empty until the owner signs something); **Open items** (D-13 first
bites at R2.4, not R4.6; D-11 first bites at R2.4 too, and only if D-13 is answered "no increase", not
at R1.1).

**C15, named.** The register and ADR-0008 use "C15" for a CRM rebuild slice that exists in no
document (`open-questions-register.md:56,98,120-122,135`; `0008-membership-employee-link.md:851,855`).
ADR-0038 defines it: C15 is *OWN on the membership* (R2.6 expand, R2.7 flip, optional R2.9 contract)
and *configurable stages* (R2.3, R2.5), with NEW-7 as R2.2, NEW-9 as R2.1 and R2.4, and the optional
sealing of `customer.contacts.email` ("C15/Q2", register `:95`) as R2.9 (Part C, pending D-26). It notes
that `docs/whatsapp/audit.md:95,239` use `C15` for "Human handoff", an unrelated id, and that the
interface registry's own row `C15` (`GET /deals/{deal_id}/history`) is another: this programme writes
the slice as "C15" and the registry row as "registry `C15`".

| ID | Decision | Recommended default | Amends or builds on | Needed by | State |
|---|---|---|---|---|---|
| M1 | Four sub-projects: foundations and cleanup, platform backbone, core revamp, suite connection; order 1, 2, then 3 and 4 | as decided 2026-10-07 | — | all | open (default recommended) |
| M2 | Independent releases; review, merge, manual deploy, verify; stop on any failure | as decided | builds on ADR-0013 and ADR-0036 | all | open (default recommended) |
| M3 | All four connection loops; data reaches every product that holds a person or a company | as decided | ADR-0000 §10, §11 | SP4 | open (default recommended) |
| M4 | This pass produces specs and plans only | as decided | — | — | open (default recommended) |
| D-1 | NEW-9: what happens to the orphan deal | Soft-delete with an audit row naming the consumer; no `stage_changed` event | ADR-0017 limitation `:161-174`; register `:122` ("marks the orphaned deal lost" is corrected: closing as lost distorts win rate and fires tenant automations, P§3 F3) | R2.4 | open (default recommended) |
| D-2 | NEW-9: how far customer cleanup may go | Only records provably created by the aborted attempt, no other contacts, no live deal; never a reused record; **log-only on first deploy** | register `:122` ("removes the orphaned company and contact if nothing else references them" is narrowed) | R2.4 | open (default recommended) |
| D-3 | Orphans from past aborts | Owner runs a read-only count of `lead.conversion_aborted` audit rows; a sweep (`H3`) is built only if non-zero | ADR-0017 `:161-174` | R2.4 | open (default recommended) |
| D-4 | Generation bump and event | Bump on every abort; emit an event for the deleted-record bump | ADR-0017 `:175-189` (today only `owner_changed` bumps, `lead/app/services.py:1125-1132`, and `fail_conversion` stages no event, `:1226-1280`, so "the event is the hook" is inaccurate there) | R2.1 | open (default recommended) |
| D-5 | Stage model | as §2.1 | ADR-0017 decision 4 `:56-63` ("accepted debt" becomes scheduled work); register `:98` | R2.3 | open (default recommended) |
| D-6 | Who edits stages | `crm.pipeline.stage.write`, route floor ORG; read stays under `deal.read`; no new admin template | builds on ADR-0007 | R2.5 | open (default recommended) |
| D-7 | What a rep is | as §2.1 | ADR-0008 Q5 (a column, not a rekey); register `:56`, `:120` | R2.6 | open (default recommended) |
| D-8 | Owner-id mapping rule | Email match from roster to member as a reviewed plan; employee link for employee-valued rows; unmapped rows untouched and listed; owner-run dry run per organization; never guess | **ADR-0008 Q5 recipe `:834-836`** (the data are not employee ids, P§3 F2); ADR-0008 Q1 rejected email matching for the HRMS link, so the plan is human-reviewed | R2.6 | open (default recommended) |
| D-9 | The access widening C15 causes | Approve, with a security review of every OWN route before the flip | ADR-0008 Q5 consequences `:851-855` | R2.7 | open (default recommended) |
| D-10 | Production CRM census | Owner runs read-only counts: organizations with CRM data, roster sizes, employee links, `lead.outbox` backlog | builds on ADR-0036 (the box is staging-like; production reads are the owner's) | R2.6 | open (default recommended) |
| D-11 | Fold `lead` into `customer`? | **Keep three processes** for this programme; revisit after R2 | would supersede ADR-0017 decision 1; costing §4.8, recorded in this ADR | R2.4 (only if D-13 holds the ceiling; R1.1 merges with this row open) | open (default recommended) |
| D-12 | Insights against stages | Stages first; `a1c9e2f7b3d4` adopted unchanged; insights rebases onto `outcome` | ADR-0020 §6 note; `docs/insights/events.md` (R2.3) | R2.3 | open (default recommended) |
| D-13 | Memory ceiling | The owner chooses: raise `EDGE_BUDGET_ACCEPTED_MB` by one per fold, free memory (D-11), or hold the folds. First needed at R2.4 | ADR-0017 `:206-210` (stale figures); ADR-0020 note after `:641`; registry `L17` (amends the decision to the five folds and the three further items SP4 adds) | R2.4 | open (default recommended) |
| D-14 | Party identity | as §2.1 | builds on ADR-0017; P§4.2 | R3.1 (also gates R4.1) | open (default recommended) |
| D-15 | Engage contact book | A projection of CRM for CRM-entitled organizations; standalone mode kept; the sync writes no consent | builds on ADR-0021 ("silence is not consent") and `docs/superpowers/specs/2026-09-21-engage-email-platform-design.md:33-52` | R4.2 | open (default recommended) |
| D-16 | Marketing against Engage | Engage owns sending and delivery analytics; Marketing owns planning and attribution and reads Engage | — | R4.6 | open (default recommended) |
| D-17 | `project` or `projects` as the target | The owner picks; link columns and event fields are one shared contract | — | R4.4 (kickoff only) | open (default recommended) |
| D-18 | Discount approvals | The approval engine, with a requester abstraction and a platform inbox | supersedes ADR-0006 Q1's narrow stance (register row 9) for tenants without HRMS | R4.5 | open (default recommended) |
| D-19 | Quote home and item master | Quotes in `deal`; `inventory_items` the one item master; the `quotation` scaffold deleted with the quote work | records the deletion on the ADR-0017 precedent | R4.5 | open (default recommended) |
| D-20 | Role consolidation | as §2.1 | ADR-0017 decision 1 as built (`CRM_LEAD_REP`); builds on ADR-0007 | R2.8 | open (default recommended) |
| D-21 | Plan packaging | Stay flat; fix or retire the per-seat `crm/growth` plan | ADR-0035 open question (`docs/adr/0035-list-prices-2026-10-06.md:40-41,166-168`) | R2.8 | open (default recommended) |
| D-22 | API nouns | as §2.1 | — | R3.2 | open (default recommended) |
| D-23 | Removals that had live users | Replace, then delete (§5.4 class 2) | builds on ADR-0022 (the export engine) | R3.4 | open (default recommended) |
| D-24 | TEAM scope | as §2.1 | builds on ADR-0006 Q1 (`manager_membership_id` kept as core's TEAM rung); registry `J10`, `L6` | R2.2 (the membership-TEAM semantics only); R3.8 for the wiring and the widening | open (default recommended) |
| D-25 | Managers revealing sealed phone or email across their team (raised by registry `L20`) | **No**: held at OWN, the rep-equivalent | builds on ADR-0023 (step-up) | R2.8 | open (default recommended) |
| D-26 | Seal `contacts.email` at rest (raised by SP2; registry `B56`) | **No**: R2.9 Part C is built only on an explicit yes and when its gates hold; a yes reopens R4.1 and R4.2 | registry `B56`, `B10`, `L16`; the "C15/Q2" of the register `:95` | R2.9 (Part C only; an optional release) | open (default recommended) |
| D-27 | What each store keeps after an erase, including what a lead erase keeps (raised by SP4, shared with SP3) | Identifiers are overwritten wherever a store keys a row by the contact id; invoices, consent-ledger rows, suppressions, free-text bodies and append-only deal text are retained and listed as residue; an administrator erases a lead one at a time, in place, behind a step-up, with no bulk erase and no new code | ADRs `crm-consent-and-erasure` (0039) and `crm-public-lead-capture` (0041), registry `I2`, `C108`, `L23` | R3.7 (R3.7b does not ship without the lead answer); R4.1 (ADR acceptance); R4.3 | open (default recommended) |
| D-28 | Accounting for an online refund of a captured invoice or order payment (raised by SP4) | **Record only**: finance stores the refund fact and an audit row, creates no credit note and no negative allocation; a person issues the credit note | registry `B82`, `C103`, `C105` | R4.3 | open (default recommended) |
| D-29 | Fulfilment billing settings surface, R4.3-S (raised by SP4) | R4.3 includes a minimal slice: `GET` and `PUT` of the organization-default row, one new permission (`E27`, operations' own) and one card on the existing payment-settings screen; the alternative is to ship the chain dark and set rows by owner SQL | registry `C102`, `E27`, `F13`, `F19` | R4.3 (chain enablement only) | open (default recommended) |
| D-30 | Customer-facing pay page (payment link) for invoices (raised by SP4) | **Defer**: no public payment surface exists, and R4.5-C ships staff-assisted collection | registry `C103`, `G25`, `EV38` | — (nothing waits on it) | open (default recommended) |
| D-31 | Marketing opt-in captured on a public form: the storefront buyer form and lead capture forms (raised by SP4) | **Single opt-in recorded with evidence**: an unticked box writes nothing and every marketing message carries the unsubscribe link; confirmed opt-in is deferred | registry `B39`, `B77`, `C58`; builds on ADR-0021 | R4.7 (storefront consent only) | open (default recommended) |
| D-32 | Insights v2: company-keyed cross-product facts (raised by SP4) | **Design only**: built when a dashboard needs a company-ranked board that the R4.3 party projection cannot serve | registry `I12`, `B72`–`B74` | — (design only) | open (default recommended) |
| D-33 | One stated rule for STOP and unsubscribe (raised by SP4, first drafted there as D-26) | **Confirm the code's rule**: both opt out of marketing only, and a transactional opt-out is a separate explicit act; R4.1 corrects the two stale texts, with no behaviour change | registry `I2`, `I11`; builds on ADR-0021 | R4.1 (ADR acceptance) | open (default recommended) |
| D-34 | Lifecycle automation: may CRM promote a company to customer on a commercial event? (raised by SP4) | A per-organization setting, **off** by default: `first_order` promotes on the first accepted order only, `first_commercial_event` also on the first won deal and the first paid invoice (the owner's explicit extension); backfills and replays never promote | registry `EV2`, `B81`, `B83`, `C106` | R4.3 (the setting; nothing promotes while it is off) | open (default recommended) |
| D-35 | Who owns a party created by `parties:ensure` (registry `L1`) | The caller's `owner_membership_id` when given and activatable, else the organization's single active `is_owner` membership (activated as a rep, with an audit row), else 422 `owner_required` | registry `L1`, `C51`, `C57` | R4.1 | open (default recommended) |
| D-36 | First-touch or last-touch campaign attribution (registry `L9`) | **First touch**, write-once: a lead's campaign id is set at capture, the deal copies it at conversion, and neither changes afterwards | registry `L9`, `B53` | R4.6 (R4.6-A) | open (default recommended) |
| D-37 | Permission for CRM-originated sends (registry `L13`) | `engage.inbox.message.reply`, so the rep also needs an Engage role (a CRM role template cannot hold a cross-product code); no new CRM code | registry `L13`, `C60` | R4.2 | open (default recommended) |
| D-38 | Should a rep read only their own records? (registry `L18`, `J19`) | **No**: reps read at ORG and write at OWN, which is today's behaviour; P§1 criterion 1 is worded to match | registry `L18`, `J19`; P§3 F2 | R2.8 | open (default recommended) |
| D-39 | Notes attachments and erasure (registry `L19`) | Until the consent and erasure ADR chooses, R4.5 ships the PDF rows but not attachments; deal binds `crm.book.contact.erased` unconditionally | registry `L19`, `B57`, `C81`–`C83` | R4.5 (attachments only, R4.5-B) | open (default recommended) |
| D-40 | Who may hand a quote off to finance, operations or projects (registry `L21`) | **No new permission code**: the ORG floor on `crm.pipeline.quote.write` plus the callee's entitlement re-check; the alternative is a dedicated dangerous `crm.pipeline.quote.handoff` | registry `L21`, `C20` | R4.5 | open (default recommended) |
| D-41 | May `PATCH` still edit a closed deal's value, currency, probability and close date? (SP3 question, registry `L22`) | **Yes**, no server change in this programme: D-5's "closed deals immutable" is the no-move rule, the deal page renders a closed deal read-only, and bulk update and import keep working | registry `L22`; D-5 | R3.5 | open (default recommended) |

D-1 to D-25 are this document's first rows; D-26 to D-41 were added when the programme's register was
consolidated, and P§8's ADR allocation lists ADR-0038 as recording M1 to M4 and D-1 to D-41. The
defaults and Needed-by cells of the added rows are P§8's, condensed; the detail of each decision lives
in the one spec P§8 names for it.

**Row grammar (binding: later releases read it with `grep`).** Each M and D decision is exactly one
line of the Decisions table, and the line begins `| D-9 |` or `| M2 |`; the costing table's rows begin
`| **D-11` or a scenario name, so a pattern anchored on the first cell never meets them. The table has
six columns, in this order: ID, Decision, Recommended default, Amends or builds on, **Needed by** (the
fifth cell: for a D row a release id from `R1.1` to `R4.7`, with a parenthetical where P§8 gives one,
or `—` for the two decisions nothing waits on, D-30 and D-32; the four M rows say `all`, `SP4` or `—`)
and **State** (the sixth and last cell). State
is exactly one of `open (default recommended)`, `approved YYYY-MM-DD` or `amended YYYY-MM-DD`; an
amended row's default cell is rewritten to the owner's wording in the same edit. The owner answers a
decision by editing that one cell, and a decision is *answered* only when its State cell says so. Every
later release checks it with one command, for D-9 for example:

```
grep -E '^\| D-9 \|.*\| (approved|amended) [0-9]{4}-[0-9]{2}-[0-9]{2} \|$' docs/adr/0038-crm-programme-decisions.md
```

which prints the row when D-9 is answered and nothing while it is open (a release that needs a specific
answer, such as SP2's D-9, tests for `approved` alone). Evidence that travels with an answer (D-9's
signed security review, D-21's staff plan action) goes on one line under **Sign-offs**, `D-n: <what was
signed or done> on <date>`; a Sign-offs line is evidence and never an answer. When the owner answers D-n,
the same commit also replaces the `(pending D-n)` clause in each note that cites it (§4.7;
`grep -rn 'pending D-9' docs` lists the ones to edit for D-9). SP2's acceptance criteria and owner
steps read the answers in this form (§9.2.3).

### 4.4 The glossary

Authoritative copy for `docs/crm.md` §3. "Stored" is what is on the wire or in a column today and is
frozen where tenant data carries it; "label" is what users see.

| Term | Means | Stored or on the wire today | Not to be used for |
|---|---|---|---|
| tenant | An OpsGrids customer organization (`account.Organization`); every row carries its `organization_id` | `organization_id`, claim `act_org` | the CRM's company; new copy never says "customer" for a tenant |
| lead | A pre-qualification intake record that may become a company, a contact and a deal; table `lead.leads` | status `new, working, qualified, disqualified, converted`; source `form, import, manual` (`lead/app/schemas.py:26-27`); `/api/v1/leads`; events `crm.lead.*` | a deal stage, a company status or a contact status as a *label*. The stored value `lead` stays on `company.status`, `contact.status` and as the first `deals.stage` key (frozen: events and tenant rules carry it); SP3 relabels, it never rewrites |
| company | The **party**: a business or a one-person buyer; table `customer.companies` | status `customer, prospect, lead, churned` (`customer/app/schemas.py:38`); URL noun `customers` today, `companies` from R3.2 with the old prefix kept; events `crm.book.company.*` | "customer"; finance's billed party |
| contact | A **person** at a company; table `customer.contacts`. Today `email` is required and unique among live rows (`customer/app/models.py:171,222-228`); the phone is sealed (`phone_enc`), masked (`phone`) and blind-indexed (`phone_index`). Phone-only contacts are planned (D-14) | status `customer, qualified, lead, dormant` (`schemas.py:39`); `/api/v1/contacts`; events `crm.book.contact.*` | a lead |
| deal | An opportunity with a value in integer minor units and an ISO currency, a stage, a probability and an expected close date; table `deal.deals` | `/api/v1/deals`; events `crm.pipeline.deal.*` | a lead |
| stage | One step of a deal's pipeline. Today six fixed keys `lead, qualified, proposal, negotiation, closed_won, closed_lost` (`deal/app/domain.py:21-28`). Planned (D-5): an immutable key with an editable label, order, tone and default probability and an **outcome** (`open`, `won`, `lost`); consumers test the outcome, never the key | `deals.stage` String(20) | a company or contact status |
| activity | A logged past interaction (call, email, meeting, note, task). Append-only: a database trigger refuses UPDATE and DELETE. Table `deal.activities`; `last_activity_at` on a contact and on a deal is derived from it | `/api/v1/deals/activities` (canonical `/api/v1/activities` from R3.2); event `crm.pipeline.activity.logged` | a to-do; the insights feed (to be called "feed") |
| follow-up | A dated future task for a rep (R3.6). Not an activity: activities are past-only and append-only | planned table `deal.follow_ups` | — |
| owner, rep | The member responsible for a record. **Today** an `owner_id` is a `customer.owners.id` (a random uuid4 the API cannot set) or, on deals and activities, the caller's `subject_ref`, an HR employee id: a mix (P§3 F2). **Target** (D-7): the account membership id | `owner_id` on companies, contacts, deals, activities, leads; `assignment_rules.owner_ids` | an HR employee; the platform `ORG_OWNER` role |
| roster | `customer.owners`: the CRM-local rep directory (`/api/v1/customers/owners`); after C15 a thin view of members | — | — |
| membership | A user's per-organization link (`account.memberships`), carried as the `mem` claim (`Principal.membership_id`); the planned rep identity | `mem`, `X-Membership-Id` | — |
| employee link | `memberships.employee_id`, carried as `sref` (`Principal.subject_ref`). Only HRMS-shaped products key OWN on it. CRM compares `owner_id` with it today, which is why OWN is empty in a tenant without HRMS (P§3 F1) | `sref`, `X-Subject-Ref` | CRM rep identity |
| customer | Reserved for the *relationship status* of a company. For historical reasons it is also the service and schema name, the URL noun for companies, the name of the 360 route and the dashboard "Customers" KPI (`countCrmCustomers`, `opsgrids-dashboard/src/api/index.ts:381`) | status value `customer`; `/api/v1/customers` | the table name for companies; a tenant |
| billed party | Finance's invoicing profile of a company (`finance.customers`; its `crm_contact_id` bridge is unwired, P§3 F5) | `finance.customers` | a CRM company |
| customer 360 | The per-contact aggregate `GET /api/v1/customers/{contact_id}/360` (canonical `/api/v1/contacts/{id}/360` from R3.2): sections `customer, crm, orders, finance, support, channels` and a timeline; a section that cannot be produced is `null` and named in `unavailable` | — | an account-level view (the company 360 is R4.3) |
| book | The module word of `crm.book.*`: customer-service's slice (companies, contacts, owners) | permission codes | a product |
| pipeline | The module word of `crm.pipeline.*`: deal-service's slice (deals, activities). Not a service: `services/pipeline` was deleted (ADR-0017) | permission codes, event types | a service |
| party, person, rep | The identity model (P§4.2): company is the party, contact the person, membership the rep. Other products link by `crm_company_id` and `crm_contact_id`, never `customer_id` | planned columns | — |
| scope | The ladder OWN, TEAM, LOCATION, ORG. CRM implements OWN and ORG; a TEAM or LOCATION grant on a CRM code is accepted by account and refused at runtime (`libs/platform-core/src/platform_core/auth.py:344,353`); TEAM arrives in R3.8 (D-24) | grants `code:SCOPE` | — |
| ahead of the producer | A contract term: a committed event schema whose required fields the producer does not emit yet. Eight CRM schemas are (§3.2) | the schema's `$comment` | — |
| OpsGrids Revenue, CRM | The product: entitlement key `crm`, marketplace and SPA name "OpsGrids Revenue", navigation label "CRM", host `revenue.opsgrids.com` | claim `ent` contains `crm` | — |

### 4.5 `docs/crm.md` outline

Prose and tables, one file, no endpoint table (the committed OpenAPI documents are the contract).
Event, permission and role tables are the ones a test ties to `libs/contracts` (DD-3, R1.2-14).

| § | Title | Holds | Source it reads from |
|---|---|---|---|
| 1 | What the CRM is | The product, its three processes and one SPA; what it is not (OpsGrids' own billing customers are tenants; ITSM is internal IT) | this file |
| 2 | Service map | One row per service: schema, role, dev port and prefixes, prod upstream, entrypoints, memory kind and price, consumer, tables, ADRs | `routes.json`, `routes.prod.json`, `memory-budget.json` |
| 3 | Vocabulary | The glossary of §4.4 including the stored-values-against-labels rows (company status has `lead`, no `dormant`; contact status has both, registry `L4`) | this file |
| 4 | Identity and scope today | OWN compares `owner_id` with `subject_ref`; the three identity spaces (roster id, membership id, employee id); the CRM-only-tenant defect and the releases that fix it | `auth.py`, ADR-0008 Q5, ADR-0038 |
| 5 | Data model | Per service: tables, the append-only trigger on `activities`, RLS, sealed fields, partial unique indexes, "no cross-schema foreign key", the meaning of `owner_id` | the `models.py` files |
| 6 | Public API | The three OpenAPI documents, the gateway prefixes in both route tables, the planned aliases (D-22) | `libs/contracts/openapi/*.json` |
| 7 | Internal API | The audit table of §3.4 and the six internal routes with caller allow-lists and org-claim checks; why none is in OpenAPI (`include_in_schema=False`) | `*/app/api/internal.py` |
| 8 | Events | The 15 types: producer, schema file, payload emitted today, state (conforms, ahead, new), consumers, subscribable; the rule that an event with no bound queue is dropped; the contract rules | `libs/contracts/events/crm.*` |
| 9 | Permissions and roles | The 17 codes (slice, default and maximum scope, dangerous), the six role templates with grants, the planned mapping to `CRM_ADMIN`, `CRM_REP`, `CRM_MANAGER`, `CRM_VIEWER`, and the rule that every code is frozen | `libs/contracts/permissions/crm.json` |
| 10 | Runtime and operations | Containers, the consumer fold, the relay list, ops-monitor coverage after R1.2b, the DLQ schedule, memory totals and the commands to recompute them, the D-11 costing, RUNBOOK pointers | `compose.prod.yml`, `memory-budget.json` |
| 11 | Known limitations | OWN for CRM-only tenants; the 360's CRM section until R1.2a; aborted conversions; plaintext contact email (R2.9 Part C, optional, pending D-26); phone-only leads; multi-currency sums; the eight ahead-of-producer schemas — each with the release that fixes it | ADR-0017, ADR-0038 |
| 12 | How to change this | The per-change checklist (schema and producer in one commit; update the table and run the drift test, from R1.2b; register a new internal client's calls in `tests/infra/test_internal_client_paths.py`, from R1.2a; delete the `AHEAD_OF_PRODUCER` row a release closes, from R1.2b; §9.2), and where the programme specs and plans live | — |

### 4.6 The three service READMEs

Each becomes about 40 lines: one paragraph of role; the run commands; the settings that matter; the
test, typecheck and migration commands; the demo seed; three pointers. No endpoint, permission, event
or role is listed.

- **Run.** `make run SERVICE=customer PORT=8130`, `make relay SERVICE=customer`, and
  `make worker SERVICE=customer` with the note that on the edge the consumer runs inside the API
  (`RUN_CONSUMERS_IN_API`, `customer/app/config.py:17-23`). `deal`: port 8131; `make worker` exists but
  registers no handler until R2.4. `lead`: port **8135**, no `make worker` (the stub's `PORT=8110` is
  auth's port).
- **Settings.** customer: `DEAL_INTERNAL_URL`, `NOTIFICATION_INTERNAL_URL`,
  `CUSTOMER360_UPSTREAM_TIMEOUT_SECONDS`, the reveal-budget settings; lead: `CUSTOMER_INTERNAL_URL`,
  `DEAL_INTERNAL_URL` and their timeouts; deal: none beyond the shared ones.
- **Verify.** `make infra`, `make migrate`, then `cd services/<name> && uv run pytest tests -q -p
  no:cacheprovider` and `uv run mypy app`; the per-service loop is the repo rule (`import app` is
  ambiguous across services).
- **Seed.** `tools/demo/seed_crm.py` seeds customer and deal through the real write paths;
  `DATABASE_URL` must name the isolated database (the file's own header says which).
- **Pointers.** `docs/crm.md`, ADR-0017, ADR-0038, `libs/contracts/openapi/<service>.json`.

### 4.7 Corrections to existing documents

Each "Will say" cell follows §0. What the code or the data shows is stated as fact. What the programme
only proposes is worded "proposed in ADR-0038 (pending D-n)", so an accepted ADR or the register never
carries an unratified proposal as if it were settled, even if R1.1 merges before the owner answers. The
owner's answer replaces the parenthesis in the same commit that sets the ADR row's State (§4.3).

| Where | Now says | Will say |
|---|---|---|
| `docs/features.md:57-60` | "None of them exist … No product service has been written" | CRM ("OpsGrids Revenue") is built: `customer`, `deal`, `lead` and `product-crm`; see `docs/crm.md`. "Sales CRM" leaves the list (there is no such product, ADR-0017). The other products in that list stay as written, with one sentence that the stale-docs sweep (R35, `docs/decisions.md:45-47`) owns them |
| `docs/features.md:88-92` | "Any consumer handling any event: Absent" | Consumers exist; for CRM, customer consumes `crm.pipeline.activity.logged` and notification binds the CRM routing keys it lists (`docs/crm.md` §8) |
| `docs/api-reference.md:114-118` | "No product API exists. HRMS, CRM, PulseBoard … have route prefixes reserved and no service behind them" | The CRM prefixes (`/api/v1/customers`, `/contacts`, `/deals`, `/leads`) are served by customer, deal and lead; the contract is `libs/contracts/openapi/{customer,deal,lead}.json`; the other products are unchanged |
| `docs/ecosystem-gaps.md:18-20` | "No product event is consumed by anything" | Corrected for CRM: customer consumes `crm.pipeline.activity.logged`; notification binds five CRM keys today (nine after R1.2b) |
| `docs/ecosystem-gaps.md:35`, `:77-91` | lead and pipeline "were scaffolds … Planned — §2"; "`lead` is a scaffold" | `lead` is built as a third process of the `crm` product and `pipeline` was removed (ADR-0017); §2 becomes "Lead and pipeline (done)", keeping the paragraph that explains why a lead is not a small deal |
| `docs/ecosystem-gaps.md:105-116` | `document`, `scheduler`, `quotation`, `activity`, `config`, `eventbridge` are all generated templates | `activity` was deleted (ADR-0017); `scheduler` and `document` are built (ADR-0015, ADR-0009); the rest stand |
| `docs/plan.md:11`, `:39` | "Sales CRM" as a product; "CRM greenfield: customer, sales, marketing, support" | CRM is one product `crm` with three services (ADR-0017); there is no "Sales CRM" product |
| `docs/architecture.md:143-145` | `/campaigns` and `/tickets` among five dead prefixes | Left until R1.2b removes the rows, then corrected in that release (§5.4) |
| `docs/architecture.md:514-515` | `product-crm` "still default[s] to fixtures" | product-crm uses the real gateway; fixtures only when a dev build sets `VITE_USE_FIXTURES=true` (`product-crm/src/api/client.ts:84`). The service counts at `:369-381` already list `lead` as built; their non-CRM errors belong to the R35 sweep |
| `docs/backlog.md:503` | "CRM is the only product with two admin templates" | three (`CRM_BOOK_ADMIN`, `CRM_PIPELINE_ADMIN`, `CRM_LEADS_ADMIN`) |
| `docs/onboarding/cards-dev-a-lead.md:13-31`, card A4 `:280-372`, `:439-443` | the product is "Sales CRM (`salescrm`)"; A4 builds `SALESCRM_*` roles; the next card is against `services/activity` | the product is `crm`; A4 describes the slice as built (`crm.lead.lead.read/write/convert/read_sensitive`, `CRM_LEADS_ADMIN`, `CRM_LEAD_REP`, `lead/app/catalogue.py:150-176`); the closing paragraph names no next card. A1–A3 and A5 are untouched (skeptic; ADR-0017 `:202-205`; `.github/pull_request_template.md:9`; `lead/app/catalogue.py:13` cite the file) |
| `docs/onboarding/cards-dev-d-crm.md:11-12`, `:56`, `:277,282` | 33 services and "exactly one test file"; qr.test.ts is "the only existing test in the frontend"; "seven screens" | the real counts (34 service directories, 521 frontend test files, 37 in `product-crm`); the reference test is "an existing example"; ten screens including Leads, LeadDetail and Team |
| `docs/onboarding/README.md:10` | a row linking `cards-dev-b-pipeline.md` | row removed; the paragraph at `:54-58` stays |
| ADR-0017 `:41-49` | `CRM_SALES_REP` and `CRM_DEAL_REP` "each pick up the lead grants" | **Amended**: as built, `lead` declares `CRM_LEADS_ADMIN` and a third default role `CRM_LEAD_REP`; neither older rep template holds a `crm.lead.*` grant (fact, `lead/app/catalogue.py:150-176`). Merging the three into one rep template in R2.8 is proposed in ADR-0038 D-20 (pending D-20) |
| ADR-0017 `:56-57` | `services/deal/app/domain.py:38` | `:21` (a citation, corrected in place) |
| ADR-0017 `:105-114` | this slice adds `customer-worker` | **Amended**: superseded by the fold (`RUN_CONSUMERS_IN_API`); the container survives only behind `split-workers` |
| ADR-0017 `:161-174` | orphans: "Nothing consumes it today … a future deal-side consumer that marks the orphaned deal lost" | **Amended**: "idempotent-by-id reuse" is the reason a naive cleanup is unsafe (fact, P§3 F3). The cleanup itself is proposed in ADR-0038 D-1 to D-3 as soft-delete, provenance-guarded and log-only first (pending D-1, D-2, D-3) |
| ADR-0017 `:175-189` | the event is "the hook a future cleanup would subscribe to" for earlier generations | **Amended**: `fail_conversion` stages no event today (fact), so "the event is the hook" is inaccurate for earlier generations. Bumping the generation on every abort and emitting an event for the deleted-record bump are proposed in ADR-0038 D-4 (pending D-4) |
| ADR-0017 `:206-210` | 3,589 of 3,800 MB, 94.4% | **Amended**: today's computed figures (§3.1, fact). Whether the box's ceiling rises or memory is freed is ADR-0038 D-13 (pending D-13) |
| ADR-0008 `:834-836` | owner values "migrated from employee ids to membership ids through account's link" | **Amended**: CRM owner values are roster ids or employee ids, not one space (fact, P§3 F2); ADR-0008 Q1 is why email matching is a reviewed heuristic here. Proposed in ADR-0038 (pending D-7, D-8): a human-reviewed mapping plan and a `membership_id` column rather than a rekey |
| Register `:56` (row 20) | Built by C15, NEW-7 | names ADR-0038 and the release ids as that ADR (proposed) defines them (C15 = R2.6, R2.7, R2.3, R2.5; NEW-7 = R2.2, larger than the register says: the resolver registry is written and never read, `auth.py:297-301`, a fact) |
| Register `:95`, `:97`, `:98` | limitations table | `:95` gains one clause naming R2.9 (the optional sealing) and ADR-0038; `:97` names D-1 to D-4 and R2.1, R2.4; `:98` names D-5 and R2.3, R2.5; each clause is worded "proposed in ADR-0038 (pending D-n)" |
| Register `:101-103`, `:120-122`, `:135` | the note on row 20; NEW-7, NEW-8, NEW-9; the C15/C14 scope note | each gains one clause naming ADR-0038 and its release, worded as a proposal (pending D-n). NEW-9's text "marks the orphaned deal lost" is **annotated, not replaced**: "closing as lost distorts win rate and fires tenant automations (P§3 F3); ADR-0038 D-1 proposes a soft-delete instead (pending D-1)"; the owner's answer replaces the annotation with the settled wording |
| Register `:19-23` | counts | counts unchanged; one added sentence: the programme's decisions are proposed in ADR-0038 and settle as the owner answers them |

### 4.8 Costing D-11 — fold `lead` into `customer`

What the fold is: lead's code and tables become part of the customer service and schema; the `lead`
container and `lead` schema go away; `leads:convert` becomes a local transaction for the company and
contact, while the deal call stays an HTTP call outside any transaction.

**Memory, by the repo's own arithmetic.** `python3 infrastructure/edge/lib/memory_budget_totals.py
infrastructure/edge/memory-budget.json --profiles <list>` (the CLI `preflight.sh` uses), with the
`lead` entry removed from a copy of `containers` for the D-11 rows. Reference MemTotal 3862 MB; the
box's accepted ceiling is 3944 per the owner's note (unverified from the repo, §3.1).

| Scenario | Ten profiles | % of 3862 | Ten plus `admin` | % | against 3944 | against 3862 |
|---|---|---|---|---|---|---|
| Today | 3747 | 97.02 | 3944 | 102.12 | 0 | +82 |
| Registry plan, five folds of +1 MB (deal R2.4, lead R3.8, support R4.4, marketing R4.6, storefront R4.7; `F8`) | 3752 | 97.15 | 3949 | 102.25 | +5 | +87 |
| **D-11 yes**: `lead` container removed, customer stays `api_with_worker` (99) | **3649** | 94.48 | **3846** | 99.59 | −98 | −16 |
| **D-11 yes** and the four remaining folds (no `lead` fold `F9`) | 3653 | 94.59 | 3850 | 99.69 | −94 | −12 |
| For context: a standalone `deal-worker` (+90) instead of a fold | 3837 | 99.35 | 4034 | 104.45 | +90 | +172 |
| For context: `deal` folded into `customer` too (not proposed) | 3551 | 91.95 | 3748 | 97.05 | −196 | −114 |
| For context: the planned insights container (+99) on top of the D-11 and four-fold row | 3752 | 97.15 | 3949 | 102.25 | +5 | +87 |

Customer would absorb lead's modules. The kind's own measurements put customer at 91.7 MiB and lead at
92.2 MiB (`memory-budget.json`, `kinds.api.measured_mib`), nearly all of it the shared module graph, so
the saving is 98 MB less whatever lead's modules add to customer's resident set: small, but not measured
(inference), and the 98 is itself a rounded mean. Every `ten` row sits between the soft line (3282) and
the hard line (3862). With `admin`, only the two D-11 rows and the deal-folded row fall below the hard
line; every row is far below the accepted maximum of 4827.

**What it removes** (registry rows that would not be built): the lead fold `F9` and its `lead-worker`,
`lead.events` (`EV45`) and its addition to `PROFILE_DLQ_QUEUES["crm"]`; lead's copy of `owner_team` (`B4`);
wiring rows `W2` and `W19`; the `owners:validate` route (`C49`) and lead's process-local owner caches;
three entries in `tests/infra/test_io_outside_transaction.py:243-257`; and the customer half of NEW-9
(D-2), because the company and contact are written in one local transaction. The orphan *deal*
remains (D-1). The registry's §K row for D-11 adds what a yes would move into customer instead of
dropping: `B86`, the lead sides of `C94` and `C104`, and the lead handlers that R4.7 binds on
`lead.events` (`EV45`), all of which assume `lead` stays its own process.

**What it costs.**

1. It supersedes ADR-0017 decision 1 ("`lead` is a third process", accepted 2026-09-23).
2. An irreversible data move on live tenants. `lead.leads`, `assignment_rules`, `audit_logs`, `outbox`
   and `processed_events` become tables in the `customer` schema: customer migrations create them;
   an owner-run tool copies the rows per organization with the admin role (idempotent, counts only);
   the PII columns are sealed under the platform-wide key, so nothing is re-encrypted; the
   `tenant_isolation` policies are rebuilt on `customer.current_org()`; `opsgrids_customer` is granted
   on them; `lead.outbox` must be drained first because the relay entry for `lead` goes away. After the
   first write to the new tables there is no code-only rollback.
3. File and test churn: lead's roughly 3,100 lines of code and 5,300 of tests (digest) move under
   customer; `/api/v1/leads` is re-pointed to customer in **both** route tables; the `lead` block leaves
   `compose.prod.yml`; the ten-profile literals in `RUNBOOK.md` (3747 becomes 3649) change in the same
   commit or `tests/infra/test_planned_headroom.py::test_runbook_ceiling_example_is_the_number_preflight_would_print`
   fails; `PROFILE_API_CONTAINERS["crm"]` (`ops_monitor/config.py:83`) is a `libs/` change, so that
   deploy is a full rebuild; `tools/dev-bootstrap/init-db.sql` and `SERVICES_WITH_SCHEMA` in
   `infrastructure/edge/remote.sh` lose `lead`.
4. The event envelope's `producer` for `crm.lead.*` becomes `customer` (`lead/app/relay.py:38` takes it
   from the service name). Only the scheduler's ownership rule reads `producer`
   (`scheduler/app/consumers.py:90`) and no CRM event passes through it.
5. Size XL on the repo's scale (more than a week, several slices): moving the code and tests is L, the
   data-move tool with its runbook M, gateway, compose and documents S.

**Recommendation.** Keep three processes for this programme (`DD-16`). **Revisit when** (a) the owner
answers D-13 "no increase": D-11 must then be answered yes *before R2.4 deploys*, because R2.4's single
fold takes ten plus `admin` to 3945, above 3944, and the D-11 rows are the only registered way to stay
under the ceiling; or (b) the owner holds the planned insights container until memory is freed; or (c)
R2.1 and R2.4 have shipped and NEW-9's customer half is already hardened, which shrinks the fold's
benefit to the 98 MB.

### 4.9 Tests and verification

Documents only (§4.1), so the evidence is searches and the repo's own docs-sensitive checks:

- `make test-infra` (it contains `test_the_decisions_index_lists_the_adr_and_the_next_free_number_is_really_free`,
  which must pass with 0039 stated and `0038-…md` present) and `make hygiene`; no Python or
  TypeScript file is touched.
- The searches of `AC-1.1.n` below.
- A reading pass that every `path:line` in the new documents resolves at the merge commit (the plan
  lists the greps).
- Memory: `memory_budget_totals.py` prints 3747 and 3944 before and after (nothing priced changed).

### 4.10 Rollout

Review, then the owner merges (squash). No deploy, no flag, no reconcile, no migration, no SPA
publish. **Memory-budget effect: none**; `python3 infrastructure/edge/lib/memory_budget_totals.py
infrastructure/edge/memory-budget.json --profiles storefront,inventory,hrms,crm,pulseboard,projects,support,marketing,finance,itsm`
prints 3747, and with `,admin` 3944, on both sides of the change. CI's classifier treats a `docs/`
path as "run everything" (`tools/affected-areas.sh`): the three README paths under `services/<name>/`
would narrow to those services on their own, but the `docs/` paths in the same change fail it open, so
every suite runs; with Actions blocked, the owner relies on a local `make gate`. The ADR number is
claimed by the merge: before merging, the owner confirms 0038 is still free (`ls docs/adr | tail -3`;
`git log --all --diff-filter=A -- 'docs/adr/0038*'` prints nothing).

**The one runtime-adjacent effect.** At the next deploy `remote.sh` finds `services/customer`,
`services/deal` and `services/lead` changed since the last good ref (their READMEs) and rebuilds those
three images (`infrastructure/edge/remote.sh:191-196`); the containers are then recreated with
application code that has not changed (inference: compose recreates a container whose image id
changed). In the normal order that deploy is R1.2a, which therefore carries a no-op rebuild of deal and
lead beside its own customer and notification changes; an unrelated deploy in between pays the same
three. Nothing is owed for it: no migration, no reconcile, no `.env` edit, and the memory totals do not
move.

### 4.11 Rollback

Revert the squash commit. Everything is a document, so the only irreversible fact is that **ADR
numbers are never reused** (`docs/decisions.md:8-9`): if 0038 has been referenced elsewhere after
merge, a revert leaves the number allocated and the file is replaced by a one-line "withdrawn" ADR
rather than deleted. Stop condition: the invariant test fails on the merge commit.

### 4.12 Acceptance criteria

- **AC-1.1.1** `docs/adr/0038-crm-programme-decisions.md` exists; the first line after its title reads
  `Status: **proposed**`; `grep -cE '^\| D-[0-9]+ \|'` over it prints 41 and `grep -cE '^\| M[0-9] \|'`
  prints 4 (anchored on the first cell, so the costing table's rows are not counted); every D row's fifth
  cell (Needed by) names a release (D-30 and D-32 say `—`) and its sixth and last cell (State) reads
  `open (default recommended)` until the owner answers, then `approved <date>` or `amended <date>`
  (§4.3); the answer check of §4.3 prints nothing for a row that is still open.
- **AC-1.1.2** `docs/decisions.md` contains `[0038](adr/0038-crm-programme-decisions.md)` and
  `next free number is **0039**` (the first number above the highest ADR on `main`; 0041 if 0040 merged
  first, DD-1); `uv run pytest tests/infra/test_purge_test_orgs.py -q -k next_free_number`
  passes.
- **AC-1.1.3** `git diff --numstat` for ADR-0017 shows exactly one deleted line (the line that carries
  the `domain.py:38` citation) and the `**Amended 2026-10-07` marker occurs five times (decision 1,
  `customer-worker`, orphans, generation, memory); ADR-0008 has one; ADR-0020 has two. No note states
  a proposal as a fact (§0): `grep -c 'pending D-'` prints at least 4 over ADR-0017 (decision 1,
  orphans, generation, memory) and at least 1 over ADR-0008.
- **AC-1.1.4** `docs/crm.md` has twelve `## ` sections; its event table has 15 rows beginning
  `| \`crm.`, its permission table 17, its role table 6, its service table 3; the glossary has the 21
  terms of §4.4.
- **AC-1.1.5** Each of the three READMEs is at most 60 lines, contains `docs/crm.md`, and none contains
  `not yet exposed`, `none registered this slice`, `Nothing yet`, `PORT=8110` or `_Describe`.
- **AC-1.1.6** `docs/onboarding/cards-dev-b-pipeline.md` does not exist and `grep -rn
  'cards-dev-b-pipeline.md)' docs --exclude-dir=superpowers` prints nothing. Before R1.1 it prints
  `docs/onboarding/README.md:10`, the one markdown link; a pattern ending `.md]` would never match it
  (a backtick sits between `.md` and `]`), and ADR-0017's four mentions (`:22`, `:52`, `:58`, `:205`)
  are plain text and stay. The exclusion keeps the design documents, which quote the file name, out of
  the search. `cards-dev-a-lead.md` has no `SALESCRM_` and no "Sales CRM"; `cards-dev-d-crm.md` has
  none of "exactly **one** test file", "the only existing test in the frontend", "seven screens".
- **AC-1.1.7** `grep -n 'No product API exists' docs/api-reference.md`, `grep -n 'None of them exist'
  docs/features.md` and `grep -n 'Sales CRM' docs/features.md docs/plan.md` print nothing, or print only
  a sentence that marks "Sales CRM" as a retired name. (`docs/architecture.html:752` also lists it as a
  planned node; registry `I7` gives that file to SP2.)
- **AC-1.1.8** The register rows at `:56`, `:95`, `:97`, `:98`, `:120`, `:121`, `:122` and the notes at
  `:101-103` and `:135` each contain `ADR-0038`.
- **AC-1.1.9** §4.8's table is reproducible: the four commands in the plan print 3747, 3944, 3649 and
  3846 (the last two from a copy of the budget without the `lead` container).
- **AC-1.1.10** `git diff --name-only <base>...HEAD` lists only paths under `docs/` and the three files
  `services/customer/README.md`, `services/deal/README.md` and `services/lead/README.md` (R1.1-8). The
  form that prints nothing when that holds is `git diff --name-only <base>...HEAD -- . ':!docs'
  ':!services/customer/README.md' ':!services/deal/README.md' ':!services/lead/README.md'`. The three
  READMEs make the next deploy rebuild those three images with no code change (§4.10); no file under
  `libs/`, `frontend/`, `infrastructure/` or `tools/`, and no `app/`, `tests/` or `migrations/`
  directory, is touched.

## 5. R1.2 — Safe fixes, confirmed removals and event-contract hygiene (R1.2a and R1.2b)

### 5.1 Purpose and scope

**Purpose.** Fix the one production defect discovery found, close the class of bug it belongs to,
make the CRM's contracts and monitoring complete and enforceable, and delete what has no live user, in
two deploys that are small, reversible and touch no schema, container or permission. Needs R1.1
(P§10): the removals cite the glossary and ADR-0038, and the drift test reads `docs/crm.md`. Size: M
to L (many small items; each is independently revertible).

**Two deploys.** The box's only rollback is the whole ref (`Makefile:577-583`), so one deploy would
roll the 360 fix back together with any hygiene change that misbehaves, and would bundle the only
defect fix with changes that alter which routing keys notification consumes, what tenants can author,
what Engage offers and what the front-door gateway runs. R1.2 is therefore one scope in two deploys
(column "Deploy" below):

- **R1.2a**, deployed first: R1.2-1, -2, -3 and -13 and the `c1_360.py` part of R1.2-10, the 360 path
  fix, the guard tests, the organization claim and the smoke step. It rebuilds customer and notification
  (and deal and lead with no code change, §4.10); it leaves app-gateway and ops-monitor alone and
  publishes no SPA.
- **R1.2b**: every other item. It rebuilds customer, deal, lead, notification and app-gateway, recreates
  ops-monitor and is followed by the CRM and Engage SPA publishes.

Neither deploy needs the other's code, but R1.2b waits for R1.2a to be deployed and verified (§5.9,
P§10); both need R1.1 (P§10), though R1.2a uses nothing from it. A later
release that needs the claim, the 360 path or the internal-client guard (R3.1, registry `J13`; every
release that adds an internal client, §9.2.2) needs R1.2a; one that needs the pins, the doc-drift test
or the subscribable keys (R2.3, R3.7, R4.x) needs R1.2b.

| Item | Deploy | What | Registry handle | Where |
|---|---|---|---|---|
| R1.2-1 | a | The 360's deal leg calls `/api/v1/deals` | `C23` | `customer/app/c1_360.py:166-170` |
| R1.2-2 | a | Tests that make the path class impossible to reintroduce silently: a mount-aware fake, a cross-service ASGI probe, a token-claim assertion, an unregistered-call guard over every service | `C23` | §5.3.2 |
| R1.2-3 | a | Notification's three service tokens (two to customer, one to account) carry the organization claim | `C77` (all three mints), `J13` (a), `J20` | `notification/app/targets.py:78-80`, `contacts.py:79-81`, `memberships.py:53-55` |
| R1.2-4 | b | Four missing event schemas with producer-side tests; static CRM coverage tests; a pin on the eight ahead-of-producer schemas; an offset-less `occurred_at` is taken as UTC | `EV4`, `EV5` | §5.3.4 |
| R1.2-5 | b | Four lead routing keys become subscribable; the starter recipe is repaired and sends only to opted-in contacts (with the optional `recipient_kind` on Engage's `SendMessageConfig`, two builder rules and one helper line) | `EV39` | `notification/app/domain.py:146-151`; `product-engage/src/routes/automations/recipes.ts:21-26`, `api/automations.ts:60-67`, `SendSettings.tsx` |
| R1.2-6 | b | ops-monitor watches `lead`'s outbox | `F6` | `compose.prod.yml:851` |
| R1.2-7 | b | Two dead dev gateway rows removed; the CRM prefixes are pinned equal in both tables. Strike-able (O-15): it rebuilds and recreates app-gateway for no runtime effect | `C71` | `services/app-gateway/routes.json:79-80` |
| R1.2-8 | b | Backend class-1 removals: `STATUSES`, `activities_for_deal`, three `bin/cron` copies | `F7` (the `bin/cron` clause) | §5.4 |
| R1.2-9 | b | Frontend class-1 removals: the `sales` alias, seven dead rollups and their orphans, other dead exports, four placeholder routes and their nav entries (404 gets a real page); SP1 owns all of it and SP3 drops it (DD-5) | `G15` (SP1/R1.2b for these items; SP3 follows, §9.2) | §5.4, §5.7 |
| R1.2-10 | a (the `c1_360.py` text), b (the rest) | Stale comments, docstrings and phantom fixtures corrected | — | §5.4 |
| R1.2-11 | b | `leads.phone_index` stops being computed and is mapped `deferred` | `B40`, `L2` | §5.2 |
| R1.2-12 | b | `make openapi` regenerated (one description changes) | `H6` | `libs/contracts/openapi/deal.json:6` |
| R1.2-13 | a | The production smoke gains one read-only 360 step; each run of the script leaves one throwaway organization and one roster owner on the box (DD-18, O-13) | — | `e2e/edge_product_crud.py` |
| R1.2-14 | b | A test ties `docs/crm.md`'s tables to the contracts and the gateway | `I3` | `tests/infra/test_crm_doc_matches_contracts.py` |

### 5.2 Data model (DDL level)

**R1.2 changes no DDL.** No table, column, constraint, index, RLS policy or grant is created, altered
or dropped; the three heads do not move; `make migration-roundtrip` and `make migration-check`
(`alembic check`) are unchanged and must stay green. The rule "every new table has `organization_id`,
`ENABLE` and `FORCE ROW LEVEL SECURITY`, the `tenant_isolation` policy and an app-level org filter" is
vacuous here because there is no new table.

**`lead.leads.phone_index`: expand and contract across two releases.** The column is
`phone_index VARCHAR(64) NULL` (`lead/app/models.py:95`; created by
`lead/migrations/versions/b110ff507df9_lead_domain_tables.py:48`). It is written at
`lead/app/services.py:465-468,496` and read by nothing: no selector, no index. Registry `B40`/`L2`:
R3.7's phone-based duplicate detection needs it again.

| Step | Release | Change | Reversible |
|---|---|---|---|
| A. stop using it | **R1.2b** | `create_lead` no longer calls `blind_index(phone, kind="phone", …)` and no longer passes `phone_index=` to `Lead(...)`; the model maps the column `deferred=True` so no statement the service issues names it. The column, its type and its nullability are untouched | Fully: the previous code writes it again. Leads written in between keep `NULL` |
| B1. resume (the registered default, R3.7) | R3.7 | remove `deferred`; compute at write time; add `ix_leads_org_phone_index` on `(organization_id, phone_index)` where `phone_index IS NOT NULL`; owner-run `H8 --service lead --target phone_index` (dry run first) fills rows with `phone_index IS NULL` and a sealed phone. The contractive drop is **cancelled**, not deferred | the index and the code |
| B2. contract (only if R3.7 drops phone dedupe) | the first lead migration after that decision | one change: remove the attribute and drop the column | the downgrade re-adds a nullable column; values are not restored (derived data, `H8` rebuilds them) |

DDL of B2, specified now so the choice is ready, **not built in R1.2**:

```
revision:      <generated by make makemigration>      down_revision: <the lead head at that time; today 91dd3ca663fd>
upgrade():     SET LOCAL lock_timeout = '5s';   ALTER TABLE lead.leads DROP COLUMN phone_index;
downgrade():   ALTER TABLE lead.leads ADD COLUMN phone_index VARCHAR(64) NULL;
```

RLS, policies, grants, `TENANT_TABLES` and `tools/purge_test_orgs.py` are untouched by a column drop.
Why this order is safe: migrations run before `up`, so for the whole deploy the previous code runs
against the new schema (`infrastructure/edge/remote.sh`); because step A maps the column `deferred`, no
running container issues SQL that names it, and the migration of B2 cannot break the old code during
that window. `alembic check` stays green because the model attribute and the revision change in the same
commit. B2 may not ship until step A has been live for one release and the owner no longer wants to
roll back to code from before A (that code would select the dropped column).

### 5.3 Backend contract

#### 5.3.1 Routes involved (none added, removed or changed)

| Route | Role in R1.2a | Permission and scope floor | Request keys | Response keys | Errors |
|---|---|---|---|---|---|
| `GET /api/v1/customers/{contact_id}/360` (`customer/app/api/c1_customer360.py:41-46`) | its `crm` section starts answering | `crm.book.contact.read` with `assert_can_touch`, plus the `crm` entitlement. No floor above the grant: an OWN reader gets their own contacts, an ORG reader all; nothing is widened | path `contact_id` | `Customer360 {customer, crm, orders, finance, support, channels, timeline, unavailable}` (`customer/app/c1_schemas.py:105-113`), unchanged | 401; 402 without `crm`; 403 without the permission or outside OWN scope; 404 unknown, another organization's, or a company id. An upstream failure never surfaces: the section is `null` and named in `unavailable` |
| `GET /api/v1/deals` (`deal/app/api/routes.py:78-84`) | the 360 calls it | `crm.pipeline.deal.read` plus the entitlement; scope applied by `ctx.apply` to the **caller's forwarded identity** | `company_id`, `size=200` | `items[].stage, .probability, .created_at` are read (`c1_360.py:139-152`) | any non-200 raises `UpstreamUnavailable`, so the section is `null` |

The `crm` section reflects what the *viewer* may see in deal, by design (`c1_360.py:18-22`). Who sees
what, by grant (`libs/contracts/permissions/crm.json`):

- **Default reps** (`CRM_SALES_REP`, `CRM_DEAL_REP`) read contacts and deals at ORG, so they see every
  deal on the contact's company and the full section. This is the case that starts changing in
  production (§7).
- **An OWN-scoped contact reader never reaches deal.** The route applies the contact-read scope first
  (`customer/app/api/c1_customer360.py:61`, `assert_can_touch`), which refuses an OWN grant when the
  principal has no `subject_ref` (`libs/platform-core/src/platform_core/auth.py:373-381`). So in a
  tenant without HRMS (P§3 F1) such a caller gets 403, as on every other contact route, and the deal
  leg is never called.
- **The empty section is the mixed-grant case only.** A customer-authored role that reads contacts at
  ORG and deals at OWN, held with no `subject_ref`, passes the contact check, and deal then answers an
  empty list for it (`deal/tests/test_authz.py:56-58`), so the section reads `{stage: null,
  open_opportunities: 0, hot: false}` instead of `null`. This is inference from those two code paths
  (no running stack; no 360 test pins it today): honest, and unchanged by SP2.

R1.2 adds no route and changes no grant, so negative-authz coverage is the existing set (§3.3) plus the
two tests of §5.3.2 that pin the OWN case and the mixed-grant case on customer's side.

#### 5.3.2 The path fix, and the tests that make it stick

**Code (R1.2-1).** In `customer/app/c1_360.py` a module constant `DEAL_LIST_PATH: Final =
"/api/v1/deals"`, commented with `libs/platform-core/src/platform_core/app.py:362-363` and
`deal/app/api/routes.py:78`, and `fetch_crm` requests `f"{cfg.deal_internal_url}{DEAL_LIST_PATH}"`.
Nothing else in the function changes. The module docstring and the `MISSING_UPSTREAMS` comment
(`:28-30,65-73`), which say finance has "no link to a CRM contact", are corrected to the facts
(`finance.customers.crm_contact_id` exists, unwired); behaviour is unchanged and the finance section
stays `null` until R4.3.

**Layer 1: hermetic, in customer** (`customer/tests/test_c1_customer360.py`, modified).

- A test-local `RealDealRoutes` loads `libs/contracts/openapi/deal.json` and answers
  `serves(method, path)` by compiling each `paths` key (`{param}` becomes `[^/]+`) with its HTTP
  methods. That document is `app.openapi()` of the real deal app, byte-checked by
  `tools/export_openapi.py --check` (`make openapi-check`), and its keys carry the `/api/v1` mount
  (`/api/v1/deals`, `/api/v1/deals/{deal_id}`, …). It is the real route table, committed.
- `Fake.__call__` consults `RealDealRoutes` for port 8131 first and answers
  `httpx.Response(404, json={"detail": "Not Found"})` for anything the real app would not route,
  exactly as production does.
- `test_deal_is_asked_for_this_account_under_the_callers_identity` asserts
  `deal_call.url.path == "/api/v1/deals"` (it asserted `"/deals"` at `:154`).
- New `TestDealLegPath`: `test_the_crm_section_comes_back_when_the_route_exists` (the `crm` key is not
  in `unavailable` and equals `{"stage": "negotiation", "open_opportunities": 1, "hot": true}` for the
  default fake deals); `test_the_fake_would_have_refused_the_old_path` (`serves("GET", "/deals")` is
  false and `serves("GET", "/api/v1/deals")` true, so the fake is provably not vacuous);
  `test_the_query_parameters_the_leg_sends_are_declared_by_the_route` (`company_id` and `size` are
  declared parameters of `GET /api/v1/deals` in the document).
- New `TestOwnWithoutSubjectRef`, the two cases §5.3.1 separates (neither exists today):
  `test_an_own_contact_reader_without_a_subject_ref_is_forbidden` (negative-authz: grant
  `crm.book.contact.read:OWN`, `subject_ref=None`; 403, and the fake saw no call to port 8131, like
  `test_missing_the_permission_is_forbidden`, `:330-331`) and
  `test_a_mixed_grant_reader_without_a_subject_ref_gets_an_empty_crm_section` (grants
  `crm.book.contact.read:ORG` and `crm.pipeline.deal.read:OWN`, no `subject_ref`; the fake deal answers
  `{"items": []}`, as deal does for such a caller, `deal/tests/test_authz.py:56-58`; 200, `crm ==
  {"stage": None, "open_opportunities": 0, "hot": False}` and `crm` not in `unavailable`; a
  characterization of customer's half only).
- Mutation check, recorded once in the pull request: with the constant reverted to `/deals`,
  `test_full_picture` and the path test fail.

**Layer 2: cross-service, in `tests/infra`** (`test_internal_client_paths.py`, new).

- `CALLS` holds the nine rows of §3.4 as `(id, caller service, callee service, driver)`. Each driver
  runs in a subprocess with the caller's service directory as cwd (`import app` is ambiguous across
  services; the pattern is `tests/infra/test_route_table_parity.py::_in_service`) and calls the **real
  client function** against an `httpx.MockTransport` that records the whole request (method, path and
  headers): `c1_360.fetch_crm` and `fetch_channels`, `clients.ensure_company_and_contact`,
  `ensure_deal` and `invalid_owner_ids` (lead), `clients.fetch_crm_contact` (finance),
  `targets.resolve_contact_target`, `contacts.resolve_contact_by_phone` and
  `memberships.membership_is_active` (notification). All accept an injected `client`. No path is typed
  into the test, and each driver also returns the organization it was given.
- The probe: one subprocess per callee (deal, customer, notification, account) imports `app.main:app`
  and sends every recorded request over `httpx.ASGITransport`, UUID segments substituted, **without
  credentials**. A route that exists answers 401 or 403 (authentication runs first); **404 means "not
  mounted there"** and **405 means "wrong method"**. A failure names caller, callee, method, path and
  status, and adds "public routers are mounted under `/api/v1`, internal routers at the root
  (`app.py:362-363`)".
- `test_every_registered_call_names_its_organization_in_its_token[A2…A9]`: each driver returns the
  `X-Internal-Token` it sent (A1 sends none: it forwards the caller's identity headers,
  `c1_360.py:77-92`), and the test decodes it with the shared secret and the callee's audience, the
  pattern of `customer/tests/test_c1_customer360.py:168-175`. `iss` equals the caller's service name;
  for every path under `/internal/v1/organizations/{org}/` (A3 to A9) `claims["org"] == org`; for A2,
  whose path names no organization, `claims["org"]` equals the organization the driver was given. This
  is the ordering invariant of registry `J13` made permanent: R3.1 can enforce the claim check without
  ever meeting a caller that mints without one, and a later client cannot drop it.
- `test_every_internal_url_in_these_services_is_a_registered_call` scans `services/*/app/**/*.py`,
  **every service**, for the tokens `_internal_url` and `_service_url`, excluding only `config.py` (an
  exclusion of the internal routers would match nothing in the five audited services at HEAD and would
  hide `billing/app/api/internal.py`, which wires clients). A matching file must be one of the six
  registered files `{customer/app/c1_360.py, lead/app/clients.py, finance/app/clients.py,
  notification/app/targets.py, notification/app/contacts.py, notification/app/memberships.py}` or be
  listed in `UNAUDITED_BASELINE`, the 17 files of §3.4 (five that build `/internal/v1/…` paths in place,
  twelve that wire an `Http*` client). A matching file in neither set fails with "register this call in
  `CALLS` (a client), or name the file in `UNAUDITED_BASELINE` and say why in the commit message"; a
  baseline file that no longer matches also fails, so the baseline shrinks and grows only on purpose.
  `test_the_guard_flags_an_unregistered_file` proves it with a temporary directory. **Rule for later
  sub-projects: a new internal client registers its calls here in the same commit, wherever in
  `services/` it lives** (SP4 owns most of the wiring rows `W3`–`W52` that are not `exists`, many of
  them in services outside the five audited ones; §9.2.2 lists the releases).
- On the base commit the path test fails once, on A1 (404), and the claim test fails three times, on A7,
  A8 and A9 (no `org` claim): four failures, which is the point, and R1.2a turns each green.

**Production smoke (R1.2-13).** `e2e/edge_product_crud.py::flow_crm` gains one step after the stage
move: `GET /api/v1/customers/{contact_id}/360` must answer 200 with `"crm" not in unavailable` and
`crm == {"stage": "qualified", "open_opportunities": 1, "hot": false}` (the flow created that deal,
moved it to `qualified` and the admin role reads it at ORG). It is a read of data the flow just made;
the owner runs it (§6, O-13). The run itself leaves one throwaway organization and one roster owner on
the box (DD-18).

#### 5.3.3 Notification's organization claim (`C77`)

`notification/app/targets.py:78-80` and `contacts.py:79-81` pass `claims={"org": organization_id}` to
`mint_service_token`, as finance (`finance/app/clients.py:73`) and lead (`lead/app/clients.py:106-111`)
already do, and so does notification's third mint, to account (`memberships.py:53-55`, A9). The
registry's `C77` already reads all three mints (two to customer, one to account), folded from this
document's first draft (registry §J.2), and `J20` puts it in R1.2a: the third is there so that the
invariant the guard asserts (every call under `/internal/v1/organizations/{org}/` carries that
organization) holds for all of A2 to A9 with no exception list. Additive: customer reads no claims on
its two routes until R3.1 (`J13`), and account's route checks only `iss`
(`account/app/api/internal.py:304-346`) and goes on ignoring the claim. Tests:
`notification/tests/test_targets.py::test_the_token_names_the_organization`,
`test_contacts.py::test_the_token_names_the_organization` and
`test_memberships.py::test_the_token_names_the_organization` decode the `X-Internal-Token` the client
sent with the shared secret and the callee's audience (`customer`, `customer`, `account`) and assert
`claims["org"]` equals the organization in the URL and `iss == "notification"`, the pattern customer's
own test uses in the reverse direction (`customer/tests/test_c1_customer360.py:168-175`); the
cross-service test of §5.3.2 asserts the same for A2 to A9, so no later client can drop it. Ordering
rule: this deploys (R1.2a) and soaks **before** R3.1 enforces the allow-list and the claim check; once
R3.1 is live, this change is rolled back only after it (`J13`).

#### 5.3.4 Event contracts

**(i) The four new schemas** (`EV4`, `EV5`), written to match what is emitted today, no more.
`validate_event_schema` is the validator (`events.py:203-224`); in this repo's environment it does
**not** check `format: date-time` because `rfc3339-validator` is not in `uv.lock`, so every producer
test below also asserts offset-awareness of a timestamp by hand.

`libs/contracts/events/crm.book.contact.created/v1.schema.json`
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

`libs/contracts/events/crm.book.contact.updated/v1.schema.json`
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

`libs/contracts/events/crm.book.contact.deleted/v1.schema.json`
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

`libs/contracts/events/crm.pipeline.activity.logged/v1.schema.json`
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

Later releases widen these in the same commit as their producers: R4.1 adds `crm_company_id`,
`crm_contact_id`, `owner_membership_id` (and `direction` on the activity event); R4.2 and R4.4 add
`message` and `ticket` to the activity `type` enum.

**(ii) Producer-side tests**, each building the event through the function the routes call and reading
the staged `OutboxEvent` row (the pattern of `notification/tests/test_event_contracts.py` and of the
unmerged `deal/tests/test_insights_event_contracts.py`):

| File | Tests |
|---|---|
| `customer/tests/test_event_contracts.py` (new) | `TestConformant`: `test_company_deleted_matches_its_schema`, `test_contact_created_matches_its_schema`, `test_contact_created_by_a_lead_conversion_matches_its_schema` (through `ensure_company_and_contact_for_lead`, `customer/app/services.py:1159` onward, which reuses `create_contact`), `test_contact_updated_matches_its_schema`, `test_contact_deleted_matches_its_schema`. `TestAheadOfProducer`: the two pins below |
| `deal/tests/test_event_contracts.py` (new) | `test_deal_deleted_matches_its_schema`; `test_activity_logged_matches_its_schema` for a deal-only, a contact-only and a both-named entry; `test_activity_logged_occurred_at_carries_an_offset`; the three deal pins |
| `deal/tests/test_write_routes.py` (extended) | `test_a_timestamp_without_an_offset_is_taken_as_utc`: `POST /api/v1/deals/activities` with an offset-less `occurred_at` for a deal that already has an activity answers 201, stores UTC, and publishes an offset-aware `occurred_at` |
| `lead/tests/test_event_contracts.py` (new) | the three lead pins; the conformant `conversion_aborted` test already exists (`test_lead_lifecycle_races.py:269,292`) and is cited, not copied |

*The offset-less timestamp.* `ActivityIn.occurred_at` accepts a datetime without an offset
(`deal/app/schemas.py:190-195` treats it as UTC for the future check only); the route passes it
through (`deal/app/api/routes.py:267`) and `log_activity` keeps it as given (`deal/app/services.py:376`),
compares it with the deal's timezone-aware `last_activity_at` (`:401`) and publishes `at.isoformat()`
(`:434`). *Inference, not executed:* that comparison raises `TypeError`, and a payload without an
offset would also make customer's consumer compare naive with aware
(`customer/app/consumers.py:82`, `customer/app/services.py:821`). R1.2-4 normalises at the write
(`if at.tzinfo is None: at = at.replace(tzinfo=UTC)` after `:376`), consistent with the validator's
own reading. The plan writes the failing test first; if it passes on the base commit, this
sub-item is dropped and the activity test keeps the offset assertion. The owner may strike it (§6, O-15).

**(iii) Static coverage** (`libs/contracts/tests/test_event_schemas.py`, extended; the docstring at
`:94-97` is corrected): `test_every_crm_event_a_service_declares_has_a_committed_schema` reads the
`EVT_*` constants starting `crm.` in `customer`, `deal` and `lead` from fresh interpreters (the
existing `_lead_aborted_event_type` trick), so a sixteenth CRM event cannot be published without a
schema; `test_every_crm_subscribable_key_has_a_committed_schema` does the same for notification's list;
`test_the_ahead_of_producer_schemas_are_exactly_the_pinned_eight` asserts that, among the `crm.*`
schemas, the set whose `$comment` starts "Ahead of the producer" equals the eight names below, so a
ninth cannot appear and a closed one must leave the set in the commit that closes it. (Across all
products 26 of the 77 committed schemas carry that comment; the other 18 are not CRM's and are out of
scope, §8.)

**(iv) The eight ahead-of-producer schemas, decided per schema** (DD-9). In every row the producer
moves to the schema and the schema never relaxes; the closing release changes the producer, removes the
schema's `$comment`, deletes the pin row and starts applying the plain conformance test, all in one
commit. R1.2 changes none of these.

| Event | Missing at HEAD | The schema also changes in the closing release | Closes in | Coordination |
|---|---|---|---|---|
| `crm.book.company.created` | `created_at` | `crm_company_id`, `owner_membership_id` added | R4.1 (`EV1`) | adopts `a4b248f1`'s customer field set; `created_at` is the row's own (an import may backdate) |
| `crm.book.company.updated` | `name, status, owner_id` (state after the patch) | same additions | R4.1 (`EV2`) | same |
| `crm.pipeline.deal.created` | `company_id, close_date, probability, closed_at, created_at` | `stage` enum becomes the key pattern `^[a-z0-9_]{1,20}$`; `stage_outcome`; later `deal_number`, `crm_*` | R2.3 (`EV6`) | `closed_at` needs the column of `a1c9e2f7b3d4`, adopted unchanged (`B18`, D-12); SP2's R2.3 owns the producer change |
| `crm.pipeline.deal.updated` | `name, stage, value_minor, currency, owner_id, company_id, close_date, probability, closed_at` | same, plus `stage_outcome` | R2.3 (`EV7`) | same |
| `crm.pipeline.deal.stage_changed` | `name, value_minor, currency, owner_id, probability, closed_at` | `from_outcome`, `to_outcome`, `to_label`; R2.5 adds `reason` | R2.3 (`EV8`) | `from` and `to` stay keys and never change meaning; consumers test the outcome |
| `crm.lead.created` | `status` | `source` enum widens (`B33`) | R3.7a (`EV10`) | the first release that changes what lead emits; adopts `a4b248f1`'s lead field set |
| `crm.lead.updated` | `status, source, owner_id, converted_at` | same | R3.7a (`EV11`) | same |
| `crm.lead.converted` | `owner_id, converted_at` | same | R3.7a (`EV12`) | same |

*The pin.* Each service's `test_event_contracts.py` holds `AHEAD_OF_PRODUCER: dict[str,
tuple[frozenset[str], str]]` (event type to missing required properties and closing release).
`TestAheadOfProducer` builds the event through the real function, computes the properties the schema
requires that the payload lacks (from `Draft202012Validator(...).iter_errors`, `required` errors only)
and asserts the set equals the pinned one and that nothing else is wrong (no type or enum error on a
field that *is* emitted). `test_the_ahead_comment_and_the_pin_table_agree` asserts a schema carries the
"Ahead of the producer" comment exactly when its event is pinned. If the unmerged insights stack lands
first, its merge turns these tests red on purpose; that merge deletes the rows it closes. This is what
makes "decide per schema" mechanical rather than a sentence in a document.

#### 5.3.5 Subscribable lead keys and the Engage recipe (`EV39`)

`notification/app/domain.py:146-151`: the `# crm` group, which is alphabetical, gains four lines
between `crm.book.contact.created` and `crm.pipeline.activity.logged`:
`"crm.lead.conversion_aborted"`, `"crm.lead.converted"`, `"crm.lead.created"`,
`"crm.lead.updated"`. The group is then nine keys.

| Key | Schema | Variables a rule can use today (emitted) | Listed by the builder but not sent until |
|---|---|---|---|
| `crm.lead.created` | exists, ahead | `lead_id`, `source`, `owner_id` (may be null) | `status`: R3.7a |
| `crm.lead.updated` | exists, ahead | `lead_id`, `changed` | `status, source, owner_id, converted_at`: R3.7a |
| `crm.lead.converted` | exists, ahead | `lead_id`, `company_id`, `contact_id` (null for a phone-only lead), `deal_id` | `owner_id, converted_at`: R3.7a |
| `crm.lead.conversion_aborted` | exists, conforms | `lead_id`, `reason`, `company_id`, `contact_id`, `deal_id` | R2.1 adds `generation` and `claim_id` |

What changes at runtime: at its next start notification binds four more routing keys on
`notification.events` (`notification/app/consumers.py:324-325`), so `crm.lead.*` events, which the broker
has been dropping, begin to be folded: one transaction per event, idempotent through
`processed_events`, and with no rule or automation on a key, a no-op. A tenant can now author a rule or
automation on these keys (`domain.py:365`; `a1_pure.py:452-457`). In a checkout the builder lists the
schema's properties as variables, including the ones the producer does not send yet; on the box it
lists none (§3.2). A placeholder naming a field the event does not carry fails the render, logs
`notification.rule.render_failed` and stages nothing (`notification/app/services.py:841-850`). That is
not a new failure: **no lead event carries an address**, and the only contact ids on a lead event are
`converted`'s (a contact that exists) and `conversion_aborted`'s (an orphan), so a lead-keyed rule could
never deliver a message before; R1.2b changes what can be *authored*, not what any tenant receives. The in-app route to the rep arrives with `owner_membership_id` (R4.1, R4.2).

*The recipe.* `product-engage/src/routes/automations/recipes.ts:21-26` ("Welcome a new lead" on
`crm.lead.created`, a `send_message` with no way to name a recipient) is replaced, not dropped:

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

`recipient_kind: "contact_id"` makes the engine resolve the address through customer's messaging target
(`notification/app/a1_services.py:945-957`), and nothing is sent until a template is chosen (the
builder flags the step until then). A phone-only lead converts with `contact_id: null`, the step fails
with `recipient_missing` and the run records it. The original lead-keyed welcome returns with R4.7
(consent evidence, `B39`).

*Consent, and why the recipe sets `purpose`.* This is the first shipped starter that messages a CRM
contact straight from a CRM event (a tenant could already author a rule on `crm.book.contact.created`
with `recipient_contact_path`, `notification/app/domain.py:351-385`, but nothing points them at it), and
it lands before the consent and erasure design (R4.1) and before a lead carries consent evidence
(R3.7). A contact created by a conversion has no consent event. Under the ledger, `transactional` is
allowed unless the latest event is `opted_out`, and only `marketing` treats silence as no consent
(`notification/app/consent.py:1-30`). The engine's default purpose is `transactional`
(`a1_services.py:972`), and `purpose_for_send` lifts a send to marketing only when the rule says so or
the chosen WhatsApp template is registered as MARKETING at Meta (`consent.py:337-356`). With the
default, whether a freshly converted contact is messaged would therefore depend entirely on the
tenant's template category, which SP1 cannot know. So the recipe sets `purpose: "marketing"`: with no
`opted_in` event on file the send is a recorded skip, whatever the template's category (the step is
`skipped` with `consent:no_opt_in` and the message `suppressed`, as in
`notification/tests/test_a1_automations.py:442-479`), and once the contact has opted in it goes out.
Until consent evidence exists the recipe will mostly skip, which is the intended behaviour, not a
defect. The builder's Purpose select is unchanged, so a tenant can still choose `transactional` for an
automation of their own; the recipe does not. The owner may strike the recipe (O-15); if so the four
keys stay subscribable, the broken "Welcome a new lead" recipe is dropped, and `recipient_kind` is not
typed until R4.7.

*Engage typing, and where the kind is valid.* The engine accepts `recipient_kind`
(`notification/app/a1_pure.py:372-376`) but Engage's `SendMessageConfig`
(`frontend/product-engage/src/api/automations.ts:60-67`) does not declare it and no Engage file
mentions it, so the literal above would not typecheck. R1.2b adds `recipient_kind?: "address" |
"contact_id"` to that interface. The kind is valid on WhatsApp only, today: customer's
`messaging-target` answers `whatsapp` and `sms` and refuses any other channel with a 422
(`customer/app/api/internal.py:83,163-166`), notification reads that refusal as `target.refused` and
returns `None` (`notification/app/targets.py:101-110`), and the engine then ends the step
`recipient_missing` (`a1_services.py:956-957`), while Engage's second channel is `email`. The email
channel arrives with R4.1 (`C45`), which lifts the first rule below in the same commit. So the builder
keeps two rules, both new code in `SendSettings.tsx`:

1. Switching the channel to `email` removes `recipient_kind` (`chooseChannel`, `:44-49`, spreads the
   existing config and would otherwise keep it); switching back to WhatsApp does not restore it.
2. Editing the "Send to" path (`:122-130`) removes `recipient_kind` unless the new path names a
   contact id, that is, its last segment is `contact_id`; a path that names anything else is read as an
   address.

A template pick or a purpose change keeps the kind (`patch` and `setPurpose` spread the config,
`:40-42,63-69`). While the kind is `contact_id` the "Send to" helper reads "The CRM contact on the
event. The WhatsApp number is looked up from the contact when the message is sent." with the
placeholder `payload.contact_id`; otherwise it keeps today's wording, which already depends on the
channel (`:128`). There is no new control to choose the kind: the recipe is the only way to set it. An
automation authored through the API can still carry `email` with `contact_id`; it fails at run time as
above and is not changed here.

Tests: `Automations.test.tsx:52-53` follows the new name and link; `recipes.test.ts` (new) pins the
graph, `purpose` included; `Builder.test.tsx` (extended) proves that a step carrying `recipient_kind:
"contact_id"` shows the contact helper and keeps the kind through a template pick and a purpose change;
that switching the channel to `email` drops the kind, shows the address helper and does not bring the
kind back when the channel returns to WhatsApp; and that a path edit to a value that does not end in
`contact_id` drops the kind while an edit to another path that does keeps it;
`tests/infra/test_engage_recipe_triggers.py` (new) reads every `emptyGraph("…")` literal in `recipes.ts`
and asserts each `crm.*` trigger is in `SUBSCRIBABLE_EVENTS` (read from a fresh interpreter in
`services/notification`). The other broken recipe, "Follow up a storefront enquiry" (`:50-55`),
triggers on a key that becomes subscribable in R4.7 (`EV42`); it is class 4 (§5.4), pinned by that test
only for `crm.*`.

#### 5.3.6 ops-monitor coverage

- **Relay list (`F6`).** `compose.prod.yml:851`, ops-monitor's `EDGE_RELAY_SCHEMAS` default, gains
  `lead` after `deal`, so it is identical to edge-relay's (`:791`): 29 names each; today edge-relay has
  29 and ops-monitor 28, and `lead` is the only difference. Effect: the container is recreated at
  deploy and starts an `outbox.lead` lag check each minute (`checks.py:154`;
  `ops_monitor/config.py:1-6` reuses the relay list verbatim). `.env.example:1167` stays "leave unset".
  `docs/deploy/ENV_SOURCE_MATRIX.md:245,837,925` and `docs/deploy/env-reference.md:330,828-829` are
  corrected: each states the divergence this change removes, and their counts were already stale ("27
  names" in the matrix; "28 … 27" in the reference's table row; HEAD is 29 against 28), so the
  corrected text says one default of 29 names, `lead` included, in both containers.
  Test: `test_ops_monitor_watches_every_schema_edge_relay_drains` in
  `tests/infra/test_ops_monitor_compose_env.py`: the two `${EDGE_RELAY_SCHEMAS:-…}` defaults, parsed
  from the YAML, are equal lists.
- **DLQ queues: R1.2 adds none, and the schedule is pinned in `docs/crm.md` §10** (registry `F5`):

| Queue | Profile key | Added by | Because |
|---|---|---|---|
| `deal.events` | `crm` | R2.4 | deal's fold and its `deal-worker` rollback container (`F7`, `F8`) |
| `lead.events` | `crm` | R3.8 | lead's unconditional fold (`F9`, `EV45`) |
| `scheduler.events` | moves from `finance` to the core list | R3.6 | `F10` |
| `operations.events` | new profile key `inventory` | R4.3 | operations already consumes inside its API but is unwatched: R4.3 adds the queue entry and an `operations-worker` rollback container, priced 0 while its profile is off, without repricing the API (`F5`, `F7`, `EV55`) |
| `support.events`, `marketing.events`, `storefront.events` | new profile keys | R4.4, R4.6, R4.7 | `F15`, `F16`, `F17` |
| `approval.events` | moves from the `hrms` list to the core list | R4.5 | approval moves to the core profile (`F18`) |

  `tests/infra/test_ops_monitor_dlq_queue_sync.py:83-129` fails both ways (a queue without a
  `./bin/worker` compose service, and the reverse), and a queue is declared only when its consumer first
  runs (`events.py:465-471`), so an entry added early would either fail the test or watch a queue that
  does not exist (the check reports "not declared yet" as healthy, `checks.py:212`). The stale comment at
  `ops_monitor/config.py:96-100` (it says `crm` runs no consumer; `:102` lists `customer.events`) is in
  `libs/platform-core`, so correcting it now would make the deploy a full rebuild; R2.4 edits that file
  and fixes it then (DD-12, DD-20).

#### 5.3.7 Gateway development table

`services/app-gateway/routes.json:79-80` loses the `/api/v1/campaigns` and `/api/v1/tickets` rows.
`infrastructure/edge/routes.prod.json` is not touched. `tests/infra/test_route_table_parity.py` gains
`test_the_crm_prefixes_are_identical_in_dev_and_prod`: the set of prefixes whose `product` is `crm` is
equal in both files (today the dev set has two extra). `docs/architecture.md:143-145` is corrected in
the same commit: of the five prefixes it calls reserved or dead, `/leave` now has a service and
`/campaigns` and `/tickets` are gone, leaving `/payroll` (8123) and `/recruit` (8124), which are class 4.
`services/app-gateway/tests` (several load the real dev table) and
`tests/infra/test_frontend_paths_are_routed.py` must stay green.

### 5.4 The removal classes

The rule (DD-4): a thing is deleted only when it has no live user, a *live user* being a referenced
symbol, a test that exercises it, a fixture or contract that pins it, a pixel baseline, or an external
caller. Each deletion's pull request description carries the search that proves it. Four classes.

#### Class 1 — safe delete now (R1.2b)

| # | Item | Where | Proof of no live user (re-run it) | What else changes |
|---|---|---|---|---|
| 1 | Dev gateway rows `/api/v1/campaigns` (8132) and `/api/v1/tickets` (8133), product `crm` | `services/app-gateway/routes.json:79-80` | `ls services` has no such service; `grep -rn "8132\|8133" services tools infrastructure e2e Makefile .github frontend/*/src` finds only these two lines; the prod table has neither prefix; no SPA calls them (`tests/infra/test_frontend_paths_are_routed.py` holds every literal `apiRequest` to the prod table) | `docs/architecture.md:143-145`; the CRM parity test (§5.3.7). Production loads `routes.prod.json` (`compose.prod.yml:731`), so the rows have no runtime effect, yet a change under `services/app-gateway/` rebuilds and recreates the front-door gateway (`remote.sh:191-196`): the stop conditions of §5.10 carry a gateway probe, and O-15 lets the owner defer this one item to R3.2's deploy, which rebuilds app-gateway anyway (SP3 §6.3.2, §6.8) |
| 2 | The `sales` entitlement alias | `opsgrids-client/src/platform/catalogue.ts:12,47`; test `platform.test.tsx:119-127` | no service mints `ent: sales` and no grant code starts `sales.` (`grep -rn '"code": "sales\.' libs/contracts/permissions` is empty; `storefront.sales.*` is a *module* inside `storefront`, `storefront/app/catalogue.py:195-214`); ADR-0017: there is no `sales` product | the test case is replaced by `a sales entitlement no longer resolves to CRM` |
| 3 | `STATUSES` | `lead/app/models.py:66` and the "See `STATUSES`" comment at `:99` | `grep -rnw STATUSES services/lead` finds the definition and that comment only; the vocabulary lives in `lead/app/domain.py:17` (`STATUS_ORDER`) and `lead/app/schemas.py:27` (`LeadStatus`) | the comment points at `domain.STATUS_ORDER` |
| 4 | `activities_for_deal` | `deal/app/selectors.py:138-151` | `grep -rn activities_for_deal services libs tools e2e` finds the definition and seven test call sites (`test_write_routes.py:332,360,381,410`, `test_activity_append_only.py:32,42`, `test_seed.py:83`); no app caller; it is an unscoped read inside the module that holds scoped reads (DD-14) | the helper moves to `deal/tests/helpers.py` with an explicit organization filter; seven imports change |
| 5 | Seven dead rollups and their orphans | `product-crm/src/data/derive.ts:14-62,120-123` (`pipelineValueMinor`, `weightedPipelineMinor`, `openCount`, `winRatePct`, `wonValueMinor`, `stageFunnel`, `dealsOwnedBy`); `data/stages.ts:41` (`OPEN_STAGES`) and `:57` (`isWon`) | `grep -rlw` for each name over `product-crm/src` finds `derive.ts` only (`OPEN_STAGES` and `isWon` lose their last user with them); no `derive.test.ts` exists; the Overview moved to `/deals/summary` | `openDeals`, `dealsByStage` and `needsAttention` stay (`needsAttention` and `dealsByStage` are imported by `Overview.tsx:10` and `Deals.tsx:6`; `openDeals` serves `needsAttention`) and gain `derive.test.ts`; imports and the module comment (which still names the KPIs and the funnel) are tidied. R3.4 deletes the three survivors and `derive.test.ts` with the file when it rebuilds Overview and Deals (SP3's ledger row is amended, §9.2.1) |
| 6 | Other dead exports | `product-crm/src/api/client.ts:87` (`LIVE`); `lib/format.ts:90` (`formatDateTime`); `reveal/testKit.tsx:22` (`SECOND_STEP_UP_TOKEN`); `api/index.ts` (a barrel); `ui/index.ts:38-39` (`KpiTone`, `MetricTrend` re-exports) | `grep -rnw` for each finds the definition only; nothing imports `@/api` as a barrel | `getContact` (`client.ts:526`) is **kept** (class 3) |
| 7 | `bin/cron` ×3 | `services/{customer,deal,lead}/bin/cron` | each is `exec python -m app.relay`, a copy of `bin/relay`; no compose service, `make` target or test runs it (`Makefile:121-126` runs `app.cron`, which these services lack; `tests/infra/test_sweeps_run_somewhere.py:37-43` reads only crons that run `app.cron`) | `docs/architecture.md:351-352` gains one sentence (DD-13) |
| 8 | Four placeholder routes and their nav entries | `product-crm/src/main.tsx:69-88,90-109` (Reports, Integrations, Settings, Help; the Team route at `:89` stays); `ui/useNavGroups.ts:21-22,55,61`; `routes/ComingSoon.tsx` | `grep -rn ComingSoon frontend/product-crm/src` finds `main.tsx` and `ComingSoon.tsx` only (four other products carry their own `ComingSoon.tsx`, untouched); `grep -rnE '"/(reports\|integrations\|settings\|help)"' frontend/product-crm/src` finds the nav file only; no other app links to them (Engage links `/contacts`, `product-engage/src/lib/dashboard.ts:33`); `opsgrids-visual/lib/pages.ts:105-133` names three other pages; `AppShell`'s `pinned` is optional (`opsgrids-ui/src/components/AppShell.tsx:78`) | the 404 route (`main.tsx:110-119`) moves to a new `routes/NotFound.tsx`, the one 404 component, which SP3's `NotFoundPage` becomes (§9.2.1); `ComingSoon.tsx` is deleted; its token-baseline entry (`opsgrids-config/token-baseline.json:193-196`, 4 offenders) goes with `pnpm tokens:baseline`, taking `product-crm`'s 101 offenders in 15 files to 97 in 14; three visual surfaces are re-baselined (§5.7) |
| 9 | Phantom event names in a test | `notification/tests/test_m1_analytics.py:258,266,274` (`crm.deal.won`, `crm.deal.lost`) | no producer publishes them (deal publishes `crm.pipeline.deal.stage_changed` with `to`); the test never asserts on `event_type` | replaced by `crm.pipeline.deal.created` and `crm.pipeline.deal.stage_changed` |

Stale text corrected in the same release (R1.2-10; each a comment, docstring or config note with no
behaviour). In R1.2a: `customer/app/c1_360.py:28-30,65-73`, the 360's own docstring and comment, next
to the path fix. In R1.2b: `product-crm/src/forms/OwnerFormDialog.tsx:14-18` ("no edit or deactivate —
the backend doesn't offer it"; `PATCH /customers/owners/{owner_id}` exists,
`customer/app/api/routes.py:212-238`); `customer/app/catalogue.py:1-9` ("one of two processes");
`deal/app/schemas.py:157-160` (says `owner_id` is "deliberately absent" from `ActivityIn`; it is a field
at `:175`; this docstring is rendered into `libs/contracts/openapi/deal.json:6`, hence R1.2-12);
`libs/contracts/tests/test_event_schemas.py:94-97`; `compose.prod.yml:1467-1471` ("Publishes only
`crm.lead.converted`"; lead publishes four types); `e2e/_seed_product.py:20-24` (CRM has three admin
templates, not two); `tools/dev-bootstrap/init-db.sql:103-104` (marketing and support listed under
"CRM"); `tools/shard-weights.txt:37` (`lead 1`; re-measure with the file's own header procedure);
`docs/architecture.md:143-145,351-352`; `docs/deploy/ENV_SOURCE_MATRIX.md:245,837,925` and
`docs/deploy/env-reference.md:330,828-829` (the relay-list divergence, with its counts corrected to 29,
§5.3.6).

#### Class 2 — replace, then delete (D-23, pending; nothing here is deleted in R1.2)

A class-2 item is deleted only when **every** live user listed is migrated; that list is the
replacement's acceptance test, and the owning release's plan copies it.

| Item | Live users (skeptic evidence) | Replacement | Replacement ships | Deleted |
|---|---|---|---|---|
| In-client fixtures mode (`USE_FIXTURES`, 35 references in `api/client.ts`, `api/fixtures.ts`) | `api/client.fixtures.test.ts` (17 cases) and `reveal/RevealableField.fixtures.test.tsx`; `reveal/useRevealedField.ts:31,130`; `lib/format.ts:7,21` (frozen clock); `lib/subject.ts:14,20`; `auth/AuthProvider.tsx:6,62-63` (fixture identity and `FIXTURE_GRANTS`, which lack the lead grants); `README.md:41`; `tools/demo/seed_crm.py` mirrors the dataset; ten sibling apps carry the same convention. `opsgrids-visual` fixtures are static route mocks for three pages, not a dev server | Development against the seeded demo organization through **one** seed (`H7`: reps created through the activation path), API fakes confined to tests, a frozen-clock helper in the test utilities | R2.7 (seeds) and R3.3 (test utilities) | R3.4 |
| `Login.tsx` | the dev sign-in and the only `/login` target when `VITE_AUTH_ORIGIN` is unset (`main.tsx:41-50`); `RequireAuth` redirects to `/login`; all eleven apps carry one | A shared dev login in `@opsgrids/client`, or dev builds use `RedirectToCentralLogin` against a local dashboard (unverifiable here, skeptic) | R3.3 | R3.4 |
| The page-only CSV export (`routes/Companies.tsx:10,232`, `lib/csv.ts`) | the only selection-scoped export; ten hand-rolled "Export selected" copies exist platform-wide (ADR-0022 context); formula-injection exposure, DD-19 | "Export selected" through the Excel engine with an `ids` filter | R3.2 (`ids`, `C35`) and R3.4 (the button) | R3.4 |
| `/deals/summary` fields the SPA does not render (`weighted_pipeline_minor`, `open_deals`, `win_rate_pct`, `won_value_minor`, `activities_this_week`) and the always-null `trend` | `deal/tests/test_authz.py:183-232`, `test_cross_tenant_isolation.py:180`, `test_domain.py:46-101`; `product-crm/src/api/client.test.ts:393-426`; `opsgrids-visual/fixtures/api/crm/summary.json` and `routes.ts:77-81` (validated against OpenAPI); ADR-0020's parity test | The rebuilt Overview renders the five fields; per-currency splitting is additive (`C9`). **No server field is deleted in this programme** (`C9` freezes all nine); `trend` stays a documented reserved key until insights supplies series. Only the SPA's `trend` scaffolding (`api/wire.ts:151-178`, `mappers.ts:123`, `types.ts`) is deleted | R3.2, R3.4 | SPA scaffolding R3.4 |
| The per-card stage `Select` on every kanban card (`ui/KanbanBoard.tsx:86-90`) | the only keyboard and touch path to move a deal; up to 200 native selects; no test pins it | The shared `KanbanBoard` with a move menu and a live announcer (`G1`) | R3.3 | R3.4 |
| Hand-built pieces folded into rebuilds: the LeadFormDialog "Source" select (`forms/LeadFormDialog.tsx:15-19,118-123`), the Deals nav count badge (`useNavGroups.ts:32-33`), local pills (`routes/Team.tsx:63-67`, `routes/DealDetail.tsx:59-63`, `routes/Activities.tsx:66-70`, `ui/NeedsAttention.tsx:67`, `ui/KanbanBoard.tsx:199`), the hand-rolled Contacts table, six hand-built filter toolbars, two component import paths | each file is rewritten in the release named; deleting first would re-baseline the same screens twice | `DataTable`, `FilterBar`, `StatusBadge`, capture sources (`B33`) | R3.3, R3.7 | R3.4 (Source select: R3.7) |

#### Class 3 — keep (removals proposed and rejected)

| Item | Proposed by | Why it stays | Evidence |
|---|---|---|---|
| `deal/app/consumers.py` and `deal/bin/worker` (an empty consumer) | discovery: delete unless NEW-9 | R2.4 registers handlers here and folds the consumer into deal's API; the module is the template for `deal.events` | registry `F1`, `F7`, `EV44` |
| `getContact` (`product-crm/src/api/client.ts:526`) | discovery: unused | the contact page of R3.5 needs it | registry `G10`; skeptic (missed list) |
| `needsAttention`, `dealsByStage`, `openDeals` (`derive.ts`) | discovery: dead rollups | used by `Overview.tsx:10`, `Deals.tsx:6` and by each other; they get tests instead | §5.4 row 5 |
| `cards-dev-a-lead.md` cards A1–A3 and A5, and the file | discovery: delete or rewrite as history | only A4 and the framing are stale; ADR-0017 `:202-205` follows A1–A3 and A5; `.github/pull_request_template.md:9` and `lead/app/catalogue.py:13` cite the file | skeptic correction |
| The `/deals/summary` server fields and `trend` | discovery: dead | tests, fixtures, contract tests and ADR-0020's parity test pin them; registry `C9` freezes them | class 2 row; skeptic correction |
| `scope.register()` calls and the three `scope.py` resolvers | skeptic: "dead but reserved" | they are the hook NEW-7 (R2.2) makes live | registry `A2`, `A13` |
| `customer.owners` and the roster routes | discovery: replace by members | R2.6 adds `membership_id`; the table stays (`L16`) | registry `B1`, `L16` |
| The six legacy role templates and all 17 permission codes | discovery: six roles for one product | frozen: customer data in roles and tenant rules (fact); R2.8 would add four and remove none, as ADR-0038 proposes (pending D-20) | registry §E.3, D-20 |
| `messaging-target` and `contacts:resolve` | discovery: accept any service token | the only path to a sealed number; R3.1 hardens them after C77 | registry `C45`, `C46`, `J13` |
| The `/api/v1/quotations` dev row (`routes.json:89`) | discovery: dead dev route | belongs to the `quotation` scaffold that SP4/R4.5 would delete with the quote work, as ADR-0038 proposes (pending D-19) | registry `C71` |

#### Class 4 — platform-wide scaffold items not CRM-owned (recorded; not done here)

| Item | Why it is not SP1's | Owner or release |
|---|---|---|
| `/api/v1/<service>/info` routes and `ServiceInfo` schemas: 32 services carry them; CRM's three are unreachable (`customer/app/api/routes.py:125`, `deal/app/api/routes.py:66`, `lead/app/api/routes.py:123`) | one cross-service cleanup (32 openapi regenerations, 32 test files, the `tools/new_service.py` template); deleting three leaves 29 inconsistent | a platform backlog item to raise |
| `services/eventbridge` scaffold (187 lines) | ADR-0016 `:120` and register row 48 make it the home of Q8's outbound webhooks; deleting it is a roadmap decision | Q8 |
| `services/config`, `services/quotation` scaffolds | `quotation` would go with the quote work in R4.5, as ADR-0038 proposes (pending D-19); `config` is platform | R4.5; platform |
| Dev rows `/api/v1/payroll` (8123), `/api/v1/recruit` (8124); no such service exists | HRMS roadmap (`planned` entries in `memory-budget.json`) | HRMS |
| `bin/cron` relay copies in 18 other services (`attendance, auth, billing, catalog, config, console, employee, eventbridge, itsm, marketing, metering, product, project, projects, quotation, standup, support, timesheet`) | platform template cleanup; `tools/new_service.py:439-444` is the target form | platform |
| Finance's hand-written sample figures (`domain.KPI_TRENDS` and related) | skeptic: flagged `sample=true`, not unflagged; live resolvers share the file | finance; SP4/R4.3 |
| `useGlobalSearch` exported with no caller (`opsgrids-client/src/index.ts:114,121`) | SP3 wires it (`G12`) | R3.3 |
| Reveal-budget script ×2 and E.164 ×4 duplicated across services; four natural-key concurrency strategies | `platform-core` refactors, guarded by `tests/infra/test_reveal_budget_parity.py`; none scheduled | unscheduled |
| The stale comment in `libs/platform-core/src/platform_core/ops_monitor/config.py:96-100` | touching `libs/` makes the deploy a full rebuild (DD-12, DD-20) | R2.4 |
| The "Follow up a storefront enquiry" Engage recipe (`recipes.ts:50-55`, trigger not subscribable until `EV42`) | Engage and storefront; flagged by the builder today, so no live user can save it | R4.7 |
| `notification`'s unwired `account_internal_url` (`notification/app/config.py:91`) and finance's unwired `customer_internal_url` (`finance/app/config.py:52`) | wiring, not CRM code (§3.4) | registry `L3`; `W3`, R4.3 |
| `product-crm/README.md` (violet accent, sparklines, "SampleChip", fixtures, `:5-6,41,51-54,68`) | the UI digest recommends rewriting it from the route registry after the redesign; a rewrite now is thrown away | R3.4 |

#### Every discovery finding has a disposition

| Digest findings | Disposition |
|---|---|
| Backend `dead`: deal consumer scaffold; `bin/cron`; `/info`; `phone_index`; `STATUSES`; `activities_for_deal`; derive helpers; `/deals/summary` fields; dev rows; `sales` alias; onboarding cards; ComingSoon; fixtures mode; stale comments | class 3; class 1 row 7; class 4; §5.2 two-step; class 1 rows 3, 4, 5; class 2 and class 3; class 1 rows 1, 2; R1.1 docs; class 1 row 8; class 2; the stale-text paragraph under class 1 (R1.2-10) and R1.1 |
| UI `dead`: ComingSoon; derive and `getContact`; stale docs; fixtures; `Login.tsx`; per-card `Select`; Source select; nav badge; local pills | class 1 row 8; class 1 row 5 and class 3; R1.1 (`product-crm` README: class 4); class 2 rows 1, 2, 5 and 6 (six items) |
| Integration `dead`: marketing placeholders; dev routes; ComingSoon; `useGlobalSearch`; scaffolds; stale docs; phantom events | class 4 (marketing UI belongs to marketing); class 1 row 1 and class 4; class 1 row 8; class 4; class 4; R1.1; class 1 row 9 |
| `duplicate`: six roles R2.8; funnel vocabularies R1.1 glossary then SP3 labels; `customer` against `company` naming R3.2; rep identity R2.6, R2.7; person records SP4; reveal and E.164 copies class 4; stage vocabulary R2.3, R3.4; rollups: SPA copy class 1 row 5, the rest later; demo dataset ×3 R2.7 (`H7`) and R3.4; `activity` meanings R1.1 glossary and R3.2; three type layers R3.2 (`G9`); three CRM processes D-11 (§4.8); READMEs R1.1; Kanban fork R3.3 (`G1`); detail rows R3.3 (`G2`); Contacts table, toolbars, import paths R3.4; reveal ×2 R3.3 (`G6`, `G7`); two contact books R4.2 (D-15); two export paths class 2; two id mechanisms R3.2 (`ids`) and R3.4 | as listed |

### 5.5 Domain rules

- **The 360 deal leg.** Path `/api/v1/deals`; parameters `company_id` and `size=200`; the forwarded
  identity allow-list `_IDENTITY_HEADERS` (`c1_360.py:77-92`) and the per-leg timeout
  (`customer360_upstream_timeout_seconds`, 2.0 s) are unchanged; any failure becomes `None`, the section
  is `null` and named in `unavailable`; `crm` leaves `unavailable` only when deal answers 200 with a page
  that parses.
- **Timestamps.** An activity's `occurred_at` is stored and published as an aware UTC instant; an
  offset-less value is read as UTC, as the validator already does.
- **Events** (registry §D): ids and enumerations only; the legacy `full_name` on
  `crm.book.contact.created` is frozen and new consumers do not read it; a payload change and its schema
  land in one commit; changes are additive within `v1`.
- **Subscribable list.** Explicit, never a wildcard; every `crm.*` key has a committed schema (tested).
- **Service tokens carry the organization.** Every token notification mints (two to customer, one to
  account) carries an `org` claim (`C77`, which names all three mints), and the guard asserts it for
  every call under `/internal/v1/organizations/{org}/`.
- **`phone_index`.** `create_lead` never computes a phone blind index; the column is mapped `deferred`.
- **Removal.** Only a thing with no live user; the proof is in the pull request.
- **Deploy scope.** Each R1.2 deploy changes nothing under `libs/` except `libs/contracts`, and no
  `pyproject.toml`, `uv.lock` or `.dockerignore` (DD-20).

### 5.6 Consumers and idempotency

- **customer** `contact_activity_logged` (`customer/app/consumers.py:54-97`) is unchanged: it is
  idempotent (`already_processed` before acting, `mark_processed` in the same transaction, `:73-87`),
  binds the tenant per message, and is a no-op for an activity naming no contact. The new activity
  schema describes exactly the keys it reads (`organization_id`, `contact_id`, `occurred_at`).
- **notification** folds four more event types from R1.2b (§5.3.5). `fold` is idempotent through `processed_events`
  and binds the tenant per message (`notification/app/consumers.py:94-129`); a redelivery is one
  `ProcessedEvent` row and no second message. The tests below prove the redelivery, the cross-tenant and
  the end-to-end cases for a lead key.
- **deal** and **lead** register no handler in R1.2.
- **Ordering.** No consumer is added and no producer's behaviour changes in a way a consumer must
  precede, so the consumer-first rule is satisfied vacuously; the one new binding (notification's four
  keys) is a consumer that precedes any rule that could use it, because a rule on a lead key cannot be
  created before the key is subscribable.

### 5.7 UI

**`product-crm`.**

- Navigation after R1.2b: Pipeline (Overview, Leads, Deals with its count, Activities), Records
  (Companies, Contacts), Workspace (Team Members); `pinned` is empty. Removed: Reports, Integrations,
  Settings, Help (and their routes). `useNavGroups` still returns `{ groups, pinned }` so the shell wiring
  is untouched.
- `routes/NotFound.tsx` (new), the `*` route: eyebrow "Not found", title "That page doesn't exist",
  text "Check the address, or head back to the overview.", one link "Back to overview" to `/`. It renders
  inside the shell for a signed-in user (the route sits under `RequireAuth`) and has no data states:
  nothing loads, so there is no loading, empty, error or forbidden state to design. The word "Overview"
  now matches the text (the old button said "Back to dashboard").
- No raw token values: new code is held to zero offenders; the shrunk baseline is rewritten by
  `pnpm tokens:baseline` and only ever shrinks.
- Re-baselined by `pnpm visual:update` on the darwin harness: `page/crm/overview`, `page/crm/deals-list`
  and `page/crm/new-deal-dialog` (the sidebar is part of each capture), both themes, three widths.

**`product-engage`.** The recipe card list shows "Welcome a converted lead" in place of "Welcome a new
lead", and a `send_message` step whose recipient kind is `contact_id` says in its "Send to" helper that
the WhatsApp number is looked up from the CRM contact; the builder drops that kind when the channel
becomes email or the path stops naming a contact id (§5.3.5). Nothing loads for either, so no new
loading, empty or error state exists. **`opsgrids-client`.** The module switcher no longer maps a `sales`
entitlement to CRM.

### 5.8 Tests

The rows 360 path, 360 authz, Internal clients and Token claim ship in R1.2a; every other row ships in
R1.2b, except Migrations and Gate, which apply to both.

| Area | File and test | Kind |
|---|---|---|
| 360 path | `customer/tests/test_c1_customer360.py`: `TestAggregation::test_deal_is_asked_for_this_account_under_the_callers_identity` (now `/api/v1/deals`); `TestDealLegPath::test_the_crm_section_comes_back_when_the_route_exists`, `::test_the_fake_would_have_refused_the_old_path`, `::test_the_query_parameters_the_leg_sends_are_declared_by_the_route` | contract, mount-aware |
| 360 authz (existing, must stay green) | `TestAuthorization::test_unauthenticated_is_rejected` (401), `::test_missing_the_permission_is_forbidden` (403), `::test_own_scope_cannot_read_another_reps_contact` (403), `::test_another_organizations_contact_is_not_found` (404, **cross-tenant**), `::test_an_unknown_id_and_a_company_id_are_not_found`; `TestEntitlements::test_no_crm_entitlement_is_payment_required` (402) | negative-authz, cross-tenant |
| 360 authz (new, §5.3.1) | `TestOwnWithoutSubjectRef::test_an_own_contact_reader_without_a_subject_ref_is_forbidden` (403, deal never called); `::test_a_mixed_grant_reader_without_a_subject_ref_gets_an_empty_crm_section` (characterization of customer's half) | negative-authz, characterization |
| Internal clients | `tests/infra/test_internal_client_paths.py`: `test_every_registered_call_reaches_a_route_the_callee_serves[A1…A9]`, `test_every_registered_call_names_its_organization_in_its_token[A2…A9]`, `test_every_internal_url_in_these_services_is_a_registered_call` (every service, with `UNAUDITED_BASELINE`), `test_the_guard_flags_an_unregistered_file` | contract, cross-service |
| Token claim | `notification/tests/test_targets.py::test_the_token_names_the_organization`; `test_contacts.py::test_the_token_names_the_organization`; `test_memberships.py::test_the_token_names_the_organization` | contract |
| Producer-side events | `customer/tests/test_event_contracts.py`, `deal/tests/test_event_contracts.py`, `lead/tests/test_event_contracts.py` (§5.3.4 ii) | contract |
| Static coverage | `libs/contracts/tests/test_event_schemas.py`: `test_every_crm_event_a_service_declares_has_a_committed_schema`, `test_every_crm_subscribable_key_has_a_committed_schema`, `test_the_ahead_of_producer_schemas_are_exactly_the_pinned_eight` | contract (run by `make contracts-check`) |
| Offset-less time | `deal/tests/test_write_routes.py::test_a_timestamp_without_an_offset_is_taken_as_utc` | regression, written red first |
| Subscribable keys | `notification/tests/test_crm_lead_keys.py`: `test_the_four_lead_keys_are_subscribable_and_have_schemas`; `test_the_crm_group_is_exactly_the_nine_expected_keys`; `test_a_rule_on_an_unlisted_lead_key_is_still_a_422` (`crm.lead.assigned`, which `EV41` adds in R4.2); `test_a_rule_on_lead_converted_messages_the_converted_contact` (a rule with `recipient_contact_path: "contact_id"` and a fake resolver stages one message); `test_an_automation_shaped_like_the_recipe_messages_the_converted_contact` (the A1 engine, the pattern of `test_a1_automations.py`: trigger `crm.lead.converted`, `send_message` with `recipient_path: "payload.contact_id"` and `recipient_kind: "contact_id"`, a fake resolver: one message staged; the same event with `contact_id: null` ends the run `failed` with `recipient_missing`); `test_a_redelivered_lead_event_is_folded_once` (**idempotency and redelivery**); `test_a_lead_event_never_reaches_another_organizations_rule` (**cross-tenant**); the consent trio of §5.3.5, in the pattern of `test_a1_automations.py:442-479` with the recipe's config and an approved WhatsApp template registered as UTILITY: `test_the_recipe_shaped_send_is_skipped_without_a_marketing_opt_in` (no consent event: the step is `skipped` with `consent:no_opt_in`, the message `suppressed`, nothing queued), `test_the_recipe_shaped_send_goes_out_after_an_opt_in` (after `consent.append` of an `opted_in` marketing event for the resolved number, the pattern of `test_consent.py:59`: one message staged, not suppressed) and `test_without_a_purpose_consent_follows_the_template_category` (characterization of the default the recipe avoids: no purpose, a UTILITY template and no event is staged; no purpose, a MARKETING-category template and no event is `skipped` with `consent:no_opt_in`) | integration |
| Rule authoring authz (existing) | `notification/tests/test_authz.py` (the `PUT /api/v1/notifications/rules` rows and `test_a_rule_on_an_unpublished_event_is_a_422`) | negative-authz |
| Recipe | `product-engage/src/routes/automations/recipes.test.ts` (graph and `purpose` pinned); `Builder.test.tsx` (a contact-id step shows the contact helper and keeps its kind through a template pick and a purpose change; switching the channel to email drops the kind, shows the address helper and does not restore the kind on the way back; a path edit to a value not ending in `contact_id` drops it and an edit to another path that does keeps it); `Automations.test.tsx:52-53` updated; `tests/infra/test_engage_recipe_triggers.py::test_every_crm_recipe_trigger_is_subscribable` | unit, contract |
| ops-monitor | `tests/infra/test_ops_monitor_compose_env.py::test_ops_monitor_watches_every_schema_edge_relay_drains` | infra |
| Gateway | `tests/infra/test_route_table_parity.py::test_the_crm_prefixes_are_identical_in_dev_and_prod`; `services/app-gateway/tests` and `tests/infra/test_frontend_paths_are_routed.py` unchanged and green | infra |
| `phone_index` | `lead/tests/test_phone_index_dormant.py`: `test_select_lead_does_not_name_phone_index` (compiled SQL), `test_a_lead_created_with_a_phone_stores_no_index`, `test_create_lead_never_computes_a_phone_blind_index` (a spy on `blind_index` sees `kind="email"` only) | unit, integration |
| Migrations | `make migration-roundtrip` and `make migration-check` unchanged and green (no revision is added; heads unchanged) | migration round-trip |
| Docs against contracts | `tests/infra/test_crm_doc_matches_contracts.py`: `test_the_event_table_lists_exactly_the_crm_schemas`, `test_the_permission_table_lists_exactly_the_manifest_codes`, `test_the_role_table_lists_exactly_the_manifest_templates`, `test_the_service_map_ports_match_the_gateway` | infra |
| Frontend | `product-crm/src/ui/useNavGroups.test.tsx` (pins the seven nav paths and an empty `pinned`); `routes/NotFound.test.tsx`; `data/derive.test.ts` for the three survivors (`openDeals`, `dealsByStage`, `needsAttention`); `opsgrids-client/src/platform/platform.test.tsx` (`a sales entitlement no longer resolves to CRM`) | unit |
| Visual and accessibility | `make ui-visual` after `pnpm visual:update` for the three CRM surfaces; the a11y ratchet may only improve | visual |
| Gate | `make gate` locally (Actions is blocked) | all rows |

### 5.9 Rollout

Two deploys, in this order, each review, merge, manual deploy, verify (P§2 M2); the owner stops on the
first failure. No migration, no flag, no `make edge-reconcile` (no permission or role changes;
`python tools/export_permissions.py --check` stays green) and no `.env` edit in either.

1. **Review and merge.** One pull request per item group is acceptable; each is independently
   revertible. The owner merges (squash). R1.2a's pull requests merge, and deploy, before R1.2b's.
2. **R1.2a deploy** (owner-run, manual: `make deploy HOST=… CONFIRM=… REF=<R1.2a merged sha>`).
   Per-service rebuild: **customer** (the new path) and **notification** (three tokens gain a claim).
   Because R1.1's READMEs changed since the last good ref, `remote.sh` also rebuilds deal and lead with
   no code change (§4.10). Nothing else is rebuilt or recreated: app-gateway, ops-monitor and every SPA
   are untouched. The order inside the deploy does not matter: the 360's new path already exists in the
   deployed deal, and notification's claim is additive and read by nobody yet.
3. **Verify R1.2a** (§6: O-9, then O-13): the 360 answers its `crm` section for an entitled
   organization, then the production smoke passes with its new step. R1.2b does not start before this.
4. **R1.2b deploy** (owner-run, manual, the R1.2b merged ref). Per-service rebuild: customer, deal,
   lead, notification and app-gateway (its dev table sits in the image and is unused in production; the
   rebuild and recreate of the front-door gateway is the price of removing two dead rows, which the
   owner may defer to R3.2's deploy, O-15), and an ops-monitor recreate (its environment default
   changed). The order inside the deploy does not matter: lead's events were already being published;
   ops-monitor reads a table that exists; notification binds its four new keys when it starts. A gateway
   probe is part of the stop conditions (§5.10).
5. **Publish the SPAs** after the R1.2b backend (owner-run): `make edge-web APP=crm` and `APP=engage`
   from a checkout of the deployed ref, then grep each published bundle: the CRM bundle must not
   contain the four "Coming soon" descriptions and the Engage bundle must contain "Welcome a converted
   lead". Engage is published after notification so the recipe's trigger is already in the catalogue.
6. **Memory-budget effect: none**, in either deploy. No container is added, removed or re-kinded; the
   compose change is an environment default. `python3 infrastructure/edge/lib/memory_budget_totals.py
   infrastructure/edge/memory-budget.json --profiles <ten>` prints 3747 (ten plus `admin`: 3944) before
   and after each; `make edge-preflight` prints the same and the box's ceiling is not touched.
7. **Reconcile steps: none.** The first reconcile the programme needs is R2.8's.

### 5.10 Rollback

- **Reversible, code-only:** everything. The only rollback is the whole ref: `make edge-rollback
  HOST=… CONFIRM=…` redeploys the previous good ref for every service (`Makefile:577-583`); there is no
  per-service rollback. That is why R1.2 is two deploys: rolling R1.2b back returns the box to the
  R1.2a ref and keeps the 360 fix, and rolling R1.2a back returns it to the ref before. `make
  edge-web-rollback` points each bundle back one symlink. No schema moved.
- **What a rollback leaves behind:** leads created while R1.2b was live carry `NULL` `phone_index`
  (never read; `H8` rebuilds it if R3.7 resumes the index); lead events folded by notification are
  `processed_events` rows (harmless); a tenant rule or automation authored on a lead key while it was
  subscribable stays in its table and goes dormant when notification stops binding the key, and is
  listed by the owner with a read of `notification.rules` and `automation` definitions whose event type
  starts `crm.lead.` (a count; no action required).
- **Ordering constraint:** `C77` (R1.2a) is rolled back only after R3.1's enforcement, never before
  (`J13`); while R3.1 is not shipped there is no constraint.
- **Stop conditions.** Any one stops the rollout and rolls back **the ref** of the deploy in hand. Each
  one compares against the baseline the owner recorded before that deploy (§6, O-6 and O-7), never
  against zero: an existing dead letter or backlog must neither stop the rollout falsely nor pass
  unnoticed.
  - *Both deploys:* the deploy's own `health` step does not end with "health: every container is up"
    (`remote.sh:386-412`); a container the deploy recreated has a restart count above its baseline;
    `dlq.notification.events` or `dlq.customer.events` is deeper than its baseline (ops-monitor alerts as
    `dlq.<queue>`); the count of 5xx responses on `/customers/{id}/360` or on `POST /deals/activities`
    in the minutes after the deploy exceeds the same-length window before it (O-6(e)).
  - *R1.2a:* the owner's 360 probe still lists `crm` in `unavailable` for an entitled organization
    holding `crm.pipeline.deal.read` at ORG (O-9).
  - *R1.2b:* `notification.events` has any baseline binding missing or any binding beyond the four lead
    keys (O-10); `outbox.lead` pending rows or rows at the attempt cap exceed their baseline (an alert
    whose cause is the baseline backlog does not stop it, O-11); the gateway probe fails after the
    recreate, that is, O-7's read of the 360 through app-gateway, repeated in O-11, no longer answers 200.

### 5.11 Acceptance criteria

Each criterion names the deploy that must satisfy it: **[a]** R1.2a, **[b]** R1.2b, **[a+b]** both.

- **AC-1.2.1 [a]** `uv run pytest tests/test_c1_customer360.py -q` (run in `services/customer`) passes,
  the two new `TestOwnWithoutSubjectRef` tests included; with `DEAL_LIST_PATH` temporarily set to
  `"/deals"`, `test_full_picture` and `test_deal_is_asked_for_this_account_under_the_callers_identity`
  fail (recorded in the pull request).
- **AC-1.2.2 [a]** `uv run pytest tests/infra/test_internal_client_paths.py -q` passes, with nine
  registered calls (the parametrized ids A1 to A9) and an `UNAUDITED_BASELINE` of 17 files; on the base
  commit it fails four times: once on A1 (`GET /deals`, status 404) and three times on the missing `org`
  claim of A7, A8 and A9. The three `test_the_token_names_the_organization` tests in notification pass.
- **AC-1.2.3 [b]** `ls libs/contracts/events | grep -c '^crm\.'` prints 15 (it prints 11 today);
  `make contracts-check` passes; `grep -l 'Ahead of the producer' libs/contracts/events/crm.*/v1.schema.json
  | wc -l` prints 8, naming exactly the eight events of §5.3.4 (iv).
- **AC-1.2.4 [b]** The three `test_event_contracts.py` files and `lead/tests/test_phone_index_dormant.py`
  pass; `grep -rn 'phone_index' services/lead/app/services.py` finds nothing.
- **AC-1.2.5 [b]** `grep -n 'crm.lead' services/notification/app/domain.py` prints the four keys, and
  `notification/tests/test_crm_lead_keys.py` passes, the three consent tests included; `grep -rn
  'welcome-new-lead\|Welcome a new lead' frontend/product-engage/src` prints nothing and `recipes.test.ts`
  passes; `grep -n recipient_kind frontend/product-engage/src/api/automations.ts` prints the optional
  field; `grep -n 'purpose: "marketing"' frontend/product-engage/src/routes/automations/recipes.ts`
  prints one line.
- **AC-1.2.6 [b]** `tests/infra/test_ops_monitor_compose_env.py` passes and `grep -c 'customer,deal,lead,project'
  infrastructure/edge/compose.prod.yml` prints 2 (both defaults); `grep -nE "27 names|ops-monitor
  27|ops-monitor.s does not" docs/deploy/ENV_SOURCE_MATRIX.md docs/deploy/env-reference.md` prints
  nothing (it prints five lines today).
- **AC-1.2.7 [b]** `grep -n 'api/v1/campaigns\|api/v1/tickets' services/app-gateway/routes.json` prints
  nothing; `tests/infra/test_route_table_parity.py` passes.
- **AC-1.2.8 [b]** Each of these searches prints nothing: `grep -rnw STATUSES services/lead`; `grep -rn
  activities_for_deal services/deal/app`; `ls services/customer/bin/cron services/deal/bin/cron
  services/lead/bin/cron 2>/dev/null`; `grep -rn ComingSoon frontend/product-crm/src`; `grep -n sales
  frontend/opsgrids-client/src/platform/catalogue.ts`; `grep -nE 'export function
  (pipelineValueMinor|weightedPipelineMinor|openCount|winRatePct|wonValueMinor|stageFunnel|dealsOwnedBy)'
  frontend/product-crm/src/data/derive.ts`.
- **AC-1.2.9 [b]** `pnpm --filter @opsgrids/product-crm typecheck lint test`, the same for
  `@opsgrids/product-engage` and `@opsgrids/client`, and `make frontend-gate` pass;
  `opsgrids-config/token-baseline.json` no longer has the key
  `product-crm/src/routes/ComingSoon.tsx`, and its `product-crm` entries total 97 offenders in 14 files
  (101 in 15 today); `make ui-visual` passes with changed baselines only for the three CRM surfaces.
- **AC-1.2.10 [b]** `make openapi` leaves a diff only in `libs/contracts/openapi/deal.json` (the `ActivityIn`
  description) and `make openapi-check` passes; `libs/contracts/permissions/*.json` are unchanged.
- **AC-1.2.11 [a+b]** `make migration-roundtrip` and `make migration-check` pass and the heads are
  `e230c025337e`, `e94d7a2c5f18`, `91dd3ca663fd`.
- **AC-1.2.12 [a]** `e2e/edge_product_crud.py` contains the step "the 360 shows the deal and its CRM
  section" between the stage move and the first delete (read by inspection; the owner runs it).
- **AC-1.2.13 [b]** `uv run pytest tests/infra/test_crm_doc_matches_contracts.py -q` passes.
- **AC-1.2.14 [a+b]** `git diff --name-only <base>...HEAD -- libs ':!libs/contracts' pyproject.toml
  uv.lock .dockerignore` prints nothing, so `needs_full_rebuild` is false; for a deploy, `<base>` is the
  ref `make edge-status` reports as last good, not `origin/main`.
- **AC-1.2.15 [a+b]** The memory CLI prints 3747 and 3944 before and after (§5.9 step 6).
- **AC-1.2.16 [a+b]** `make gate` is green locally.

## 6. Owner-run steps

Nothing here is assigned to an agent. An agent prepares the command, the expected output and the stop
rule; the owner runs it. Commands use the `HOST=…` and `CONFIRM=…` the owner already uses.

| # | When | Step | Kind | Expected, and the stop rule |
|---|---|---|---|---|
| O-1 | R1.1 review | Answer the decisions in ADR-0038 by editing each row's State cell (`approved <date>` or `amended <date>`, row grammar §4.3). D-11 and D-13 first: they re-cut SP2 | decision | An unanswered row stays `open (default recommended)`. SP1's own releases depend on no open row (what an answer would change in them is documents only, and each such statement is marked `(pending D-n)`), so R1.1 and R1.2 proceed with rows open and their documents hold the defaults as proposals. A later release whose row is open in the ADR's Needed-by cell does not start until the row is answered (P§11) |
| O-2 | before merging R1.1 | Confirm the ADR numbers: `git fetch --all`, then `git log --all --diff-filter=A --name-only -- 'docs/adr/0038*' 'docs/adr/0039*' 'docs/adr/004*'` and `ls docs/adr \| tail -3` | read | Nothing printed for 0038 and 0039; `0040-faster-ci.md` is expected and nothing above it. If a parallel branch took 0038, renumber before merging; if 0040 or a higher number is already on `main`, the decisions sentence names the number above it (DD-1) |
| O-3 | R1.1 | Merge (squash). No deploy | write (repo) | `uv run pytest tests/infra/test_purge_test_orgs.py -q -k next_free_number` green |
| O-4 | before each R1.2 deploy | `make edge-status`: which ref is live and whether `main` carries other unreleased work | read | If `main` is ahead of the live ref by more than the deploy in hand, read those releases' RUNBOOK notes first (R1.1's READMEs make deal and lead rebuild at R1.2a: expected, §4.10); neither R1.2 deploy itself needs anything from them |
| O-5 | before each R1.2 deploy | `make edge-preflight`: the memory verdict | read | The same total as today (3944 with `admin`, the owner's note) and the ceiling unchanged; R1.2 asks for **no** `.env` edit |
| O-6 | before each R1.2 deploy | Record the baselines the stop rules of §5.10 compare against, counts and names only: **(a)** (R1.2b only) on the box database, `SELECT count(*) FILTER (WHERE published_at IS NULL), min(created_at) FILTER (WHERE published_at IS NULL), count(*) FILTER (WHERE published_at IS NULL AND attempts >= 50) FROM lead.outbox;`, the three numbers of ops-monitor's own `outbox.<schema>` check (`libs/platform-core/src/platform_core/ops_monitor/checks.py:94-104`; 50 is the default cap of the relay and the monitor, `events.py:273`, `ops_monitor/config.py:209-211`: use the box's value if it sets another); **(b)** from `make edge-status`, the `dlq.notification.events` and `dlq.customer.events` lines of ops-monitor's `/status`; **(c)** (R1.2b only) `docker compose -f compose.prod.yml --env-file .env exec -T rabbitmq rabbitmqctl list_bindings source_name destination_name routing_key \| grep notification.events` (RUNBOOK `:1933-1935` shows the form); **(d)** `docker inspect --format '{{.Name}} {{.RestartCount}}'` over the containers the deploy will recreate (`docker compose … ps -q <service>`; customer and notification at R1.2a; customer, deal, lead, notification, app-gateway and ops-monitor at R1.2b); **(e)** the number of `request.completed` log lines with `status` 500 or above for `/api/v1/customers/{id}/360` (`make edge-logs SERVICE=customer`, with a `TAIL=` that covers the window) and for `POST /api/v1/deals/activities` (`SERVICE=deal`) over the last fifteen minutes, the fields the request middleware logs (`libs/platform-core/src/platform_core/logging.py:74-103`) | production read | So no stop rule meets an existing condition as if the deploy had caused it: an existing dead letter, a backlog, a pre-existing exhausted row or a crash-looping container is recorded, not acted on. A non-zero `outbox.lead` backlog is also D-10's input. Without these numbers the stop rules cannot be applied: do not deploy |
| O-7 | before R1.2a | Baseline the 360: as a user holding `crm.book.contact.read` and `crm.pipeline.deal.read` at ORG (the default rep roles; an OWN reader in a tenant without HRMS gets a 403 by design and proves nothing, §5.3.1), `GET /api/v1/customers/{contact_id}/360` through the gateway for a contact whose company has a deal (or open the Engage inbox panel) | production read | `crm` is **in** `unavailable`, confirming the inference of §3.3. If it already works, stop: the diagnosis is wrong and §5.3.2 needs rewriting. The same read, answered 200 through app-gateway, is R1.2b's gateway probe |
| O-8a | R1.2a | `make deploy HOST=… CONFIRM=… REF=<R1.2a merged sha>` | production write | Stop on the first failure; roll back the ref with `make edge-rollback` |
| O-8b | R1.2b, after O-9 and O-13 | `make deploy HOST=… CONFIRM=… REF=<R1.2b merged sha>` | production write | Stop on the first failure; roll back the ref with `make edge-rollback`, which returns the box to the R1.2a ref |
| O-9 | after R1.2a | Repeat O-7 | production read | `crm` is **absent** from `unavailable` and the section reads `{stage, open_opportunities, hot}` |
| O-10 | after R1.2b | On the broker, the O-6(c) command | production read | `notification.events` still has every binding O-6(c) recorded and exactly four more: `crm.lead.created`, `.updated`, `.converted` and `.conversion_aborted` |
| O-11 | after R1.2b | `make edge-status`: ops-monitor's `/status`; then repeat O-7 as the gateway probe | production read | an `outbox.lead` check is listed; its pending and at-cap counts are no higher than O-6(a)'s (a baseline backlog may still make it alert: that is not a failure); `dlq.notification.events` and `dlq.customer.events` are no deeper than O-6(b)'s; the 360 is answered 200 through app-gateway with `crm` still absent from `unavailable` |
| O-12 | after R1.2b | `make edge-web APP=crm`, then `APP=engage`, from a checkout of the deployed ref; grep each published bundle | production write | the CRM bundle has none of the four "Coming soon" descriptions; the Engage bundle has "Welcome a converted lead"; `make edge-web-rollback` if wrong |
| O-13 | after R1.2a | `uv run python e2e/edge_product_crud.py --product crm`. It signs up one throwaway organization and creates one roster owner, and it removes only its own company, contact and deal (`e2e/edge_lib.py:165-290`, `edge_product_crud.py:435-450`): the organization and the owner stay on the box until the owner runs `tools/purge_test_orgs.py` (ADR-0036, RUNBOOK "Removing test organisations", which says every run leaves one behind, `RUNBOOK.md:2206-2207`) | production write | every step passes, including the new 360 step; one new throwaway organization and one roster owner now exist, which is a count to note, not a failure. Run it once, for the 360 fix; a second run after R1.2b adds another organization |
| O-14 | optional | Search the notification logs for `memberships.unreachable` (`make edge-logs SERVICE=notification`) | production read | If present, §3.4's A9 inference is confirmed; the compose line is the owner's decision (registry `L3`) and is not part of R1.2 (R1.2a adds only the claim to that token) |
| O-15 | R1.2 review | Decide the strike-able items: the `phone_index` stop (DD-15); the offset-less timestamp fix (R1.2-4); removing the placeholders, the 404 page and the dead `derive.ts` rollups now rather than in R3.4 (DD-5, class-1 rows 5 and 8); the repaired Engage recipe (R1.2-5, §5.3.5: if struck, the broken "Welcome a new lead" recipe is dropped, `recipient_kind` is not typed until R4.7 and the four keys stay subscribable); and the two dead dev gateway rows (R1.2-7: they cost a rebuild and recreate of app-gateway for no runtime effect; if struck, R3.2's gateway commit removes them and adds the parity test, SP3 §6.3.2) | decision | Each, if struck, is one self-contained change removed from the release |

Order of use. R1.1: O-1 to O-3. R1.2a: O-4, O-5, O-6 (b, d, e), O-7, O-8a, O-9, O-13. R1.2b: O-4, O-5,
O-6 (all), O-8b, O-10, O-11, O-12. O-14 and O-15 as stated.

**Box `.env`.** Neither release edits it. The first programme-driven edit is `EDGE_BUDGET_ACCEPTED_MB`
at R2.4 (3945) if D-11 is answered no; an agent never makes it.

## 7. Risks specific to this sub-project

| Risk | Why it matters | Mitigation |
|---|---|---|
| The 360 starts returning data in Engage | The `crm` section has been `null` in production; Engage's panel will start rendering a stage and a hot flag for everyone who can read the contact. Default reps read contacts and deals at ORG, so they see every open deal on the company. An OWN contact reader in a tenant without HRMS gets a 403 as before; only a customer-authored mixed role (contact read ORG, deal read OWN) with no `subject_ref` sees `open_opportunities: 0`, not `null` | O-7 and O-9 bracket the change; §5.3.1 states the semantics and §5.3.2 pins the two edge cases; no data is written |
| The diagnosis (a 404 at `/deals`) is inference from the route table | If wrong, R1.2-1 changes nothing and the real cause is elsewhere | O-7 tests it before R1.2a; the layer-2 test proves the path against the real app |
| Merge collisions with the unmerged insights stack | `a4b248f1` changes four files that R1.2b also edits, in different hunks (`deal/app/selectors.py`, `deal/app/services.py`, `deal/app/schemas.py` and `lead/app/services.py`, §3.8), and it closes the eight gaps R1.2b pins | Distinct test file names; the plan lists all four files as rebase points; the pin tests turn red on purpose when insights merges and name the rows to delete; whichever stack merges second re-parents (precedent `RE-PARENT HERE`) |
| Lead events reach notification for the first time | one `processed_events` row per lead lifecycle event; a rule authored on a key whose variable the producer does not send fails the render silently (logged) | volume is small (leads, not messages); the builder lists no variables on the box (§3.2); `test_a_redelivered_lead_event_is_folded_once` |
| The repaired recipe messages a contact with no consent evidence | the first shipped starter that messages a CRM contact straight from a CRM event, before the consent design (R4.1) and lead consent evidence (R3.7); under the engine's default `transactional` purpose a conversion-created contact would be messaged whenever the tenant's template is not MARKETING at Meta | `purpose: "marketing"`: no `opted_in` event means a recorded skip; three notification tests pin the behaviour (§5.3.5); the owner can strike the recipe (O-15) |
| Stopping `phone_index` creates a backfill obligation | leads created between R1.2b and R3.7 carry `NULL`; `H8` must open their sealed phones, a bulk read with an audit row per organization | DD-15; the owner can strike R1.2-11 and keep computing; the drop is conditional |
| Visual baselines change twice | R1.2b re-baselines three CRM surfaces; R3.4 rebuilds the same screens | accepted (DD-5); one item the owner can strike (O-15) |
| A removal had a live user nobody searched for | the discovery was wrong about a dozen items | the proof-in-the-pull-request rule; every class-1 item is a separate, revertible change; the guard tests and the existing suites are the net |
| The guard tests couple later work | SP2, SP3 and SP4 add the internal clients behind the registry's wiring rows `W3`–`W52` that are not `exists` (SP4 owns most), many of them in services the audit did not cover, and must register each in `test_internal_client_paths.py`; they must also delete pin rows in the commit that closes a gap and edit `docs/crm.md` in the commit that changes a table it holds; when this was written none of the three specs said so | the guard scans every service, so a client anywhere fails until registered; each failure names the file or row to edit; `docs/crm.md` §12 states the rule; the registry already holds the matching rows (`F14`, and the `AHEAD_OF_PRODUCER` pins and the pinned-eight test in its §D introduction), and §9.2 is the hand-off table for SP2, SP3 and SP4 to apply before the specs are approved together |
| A whole-ref rollback reverts more than the change that failed | the only rollback redeploys the previous ref for every service (`Makefile:577-583`) | R1.2 is two deploys: the 360 fix (R1.2a) is never rolled back by a hygiene failure (R1.2b); stop conditions compare with recorded baselines (§5.10, O-6) |
| `date-time` is not machine-checked | `rfc3339-validator` is not in `uv.lock`, so `validate_event_schema` accepts any string for `format: date-time` | the activity test asserts an offset by hand; the finding is recorded here rather than assumed away |
| ADR-number race | another branch could take 0038 or 0039 | DD-1; O-2; the invariant test (§3.7) |
| CSV formula injection (`lib/csv.ts`) stays until R3.4 | a company name beginning `=`, `+`, `-` or `@` exports unneutralised; today only a tenant's own users write names, the first external writer is R3.7's public capture | DD-19: **R3.4 deploys before R3.7**, or a three-line guard in `csv.ts` ships first |
| A9 and A6 are unwired in production (inference for A9) | Engage conversation assignment may already be refused; finance's bridge cannot reach customer | recorded, not fixed (class 4); O-14 confirms A9; `W3` (R4.3) fixes A6; R1.2a adds only the `org` claim to A9's token |
| The guard baseline hides 17 unaudited files | the files use the same URL settings and may carry the path or claim defects this audit found only in the CRM-adjacent five | they are listed, so a change is visible and a new file fails; auditing them is a platform item (§8) |
| A deploy from `main` ships more than the R1.2 deploy in hand | production may lag `main`; R1.1's READMEs also make deal and lead rebuild at R1.2a | O-4 |
| The box ceiling of 3944 is the owner's note | cannot be verified from the repo | O-5 prints the verdict before any release that depends on it |

## 8. Out of scope

- Everything in SP2 to SP4, and the programme's scope traps (P§5): two-way email or calendar sync,
  telephony, AI scoring, a second workflow engine, full CPQ, custom objects, tiering CRM modules by plan,
  a container per feature, a CRM-local BI service, deleting the `eventbridge` scaffold.
- Closing any of the eight ahead-of-producer schemas (R2.3, R3.7, R4.1), the other 18 schemas that
  carry the same comment across the platform, and schemas for the support, itsm, marketing, pulseboard,
  projects and approval events.
- Class-2 deletions and class-4 items (§5.4), including the `/info` routes, the other `bin/cron` copies
  and the `product-crm` README.
- Executing D-11 (only its costing is here), any consumer fold, any DLQ queue, any compose service,
  any change to `EDGE_BUDGET_ACCEPTED_MB`.
- Wiring `ACCOUNT_INTERNAL_URL` for notification or `CUSTOMER_INTERNAL_URL` for finance (A9, A6).
- Auditing or probing the 17 baseline files' internal calls (§3.4) and the clients they construct: they
  are not CRM's, and the guard only keeps the list from growing unseen.
- The CSV formula-injection guard (DD-19), the consent and unsubscribe documents (registry `I11`, R4.1),
  any consent writer or consent screen (the repaired recipe only reads the ledger through the existing
  send path), a CRM end-to-end journey (`L14`), the e2e harness gaining `lead`.
- Any permission, role, route, request or response shape, or event name or payload that exists today.
- Any production data operation beyond the read-only counts of §6.

## 9. Registry amendments and hand-offs

This document edits no other file. Its first draft proposed twelve amendments to registry rows (§9.1)
and a set of edits the other specs must take (§9.2). The registry has folded the twelve; §9.2 stays the
authority for the SP2, SP3 and SP4 passages it names until those specs say the same.

### 9.1 Registry amendments

Folded: the registry's §J.2 ("Folded amendments") lists every row that this section first proposed as
Folded (`G15`; `C77` and `J13`, with the release-list change `J20`; `C71`; `F14`; `I5` and `I6`; `B40`
and `L2`; `EV39`; the `AHEAD_OF_PRODUCER` pins and the pinned-eight test, in the §D introduction;
`EV5`; `C45`; §M and §N). **None was rejected**, so nothing is left to apply to the registry, and this
document cites those rows by handle and reads each as the registry now words it. Two registry wordings
differed from this document when it was reconciled (2026-10-10) and were reported for the registry, not
copied here: `F14` labelled the whole set of SP1's guard tests "R1.2b", whereas
`test_internal_client_paths.py` ships in R1.2a (registry `J31`; §5.1, §5.8); and `F7`'s note named the
`bin/cron` copies of deal and lead, whereas R1.2b removes all three, customer's included (§5.4, class 1
row 7).

### 9.2 Hand-offs the other specs must take

These are not registry rows, so the registry cannot carry them: the SP2, SP3 and SP4 specs, plans and
acceptance criteria take them before the four specs are approved together. A `:n` below is a line number
of the named spec when SP1 was written; those specs have been edited since, so locate a passage by its
section or id. Where a spec already says what a row asks for, the row is closed.

#### 9.2.1 SP3 and the registry: who removes the placeholders, the 404 page and the dead rollups (DD-5)

SP1/R1.2b removes them; SP3 does not.

| Where | Says now | Must say |
|---|---|---|
| Registry `G15` | SP1/R1.2b for the four placeholder routes, `ComingSoon`, the 404 page and the seven dead `derive.ts` rollups; SP3/R3.4 for the rest (folded from this section's first draft, registry §J.2) | no edit: it already reads as it must |
| SP3 §3, `V20` (`:171`) and `V29` (`:180`) | five `ComingSoon` usages, 4 of 11 nav entries dead; 101 offenders in 15 files, `ComingSoon` 4 | these are facts at `HEAD`; add that R1.2b leaves no `ComingSoon`, seven nav entries and one `NotFound` route, and 97 offenders in 14 files |
| SP3 §4.4.5 (`:421`) and §8.6.4 (`:1512`) | `NotFoundPage` is created by R3.4 | the component is `routes/NotFound.tsx`, created by R1.2b with the same heading ("That page doesn't exist"); R3.4 may restyle it with an `EmptyState` and creates no second component |
| SP3 §4.4.7 token table (`:446-470`) | row `routes/ComingSoon.tsx`, 4, deleted, R3.4; total 101 | row deleted; total 97, still 0 at the end of R3.7 |
| SP3 §8.6.5 ledger, row 1 (`:1523`) | deletes `ComingSoon.tsx` and the `/reports`, `/integrations`, `/settings`, `/help`, `*` routes in R3.4 | row deleted, its gate and proof become R1.2b's evidence (§5.4 row 8); R3.4 adds `/settings` and R3.8 `/reports` as new routes |
| SP3 §8.6.4 (`:1509`) and §8.6.5 `derive.ts` row (`:1530`) | "the seven dead exports of `data/derive.ts`" are deleted in R3.4, and also `derive.ts` "(ten exports ...) goes whole" | the seven are deleted by R1.2b; three exports remain (`openDeals`, `dealsByStage`, `needsAttention`) with SP1's `derive.test.ts`; R3.4 deletes the file and the test together |
| SP3 §8.10 criteria 2 to 4 (`:1592-1597`) and the R3.4 task list in SP3's plan | `ls` of the `ComingSoon` file; the `ComingSoon` grep; `ComingSoon` in the `pnpm tokens:baseline` list | the `ls` and the baseline entry are already satisfied by R1.2b and drop out of R3.4's criteria; the grep stays as a regression check; the plan carries no task for these files |

#### 9.2.2 Obligations of the new guards, by test and release

SP1's new tests bind releases of SP2, SP3 and SP4. When this was written none of the three specs named
`AHEAD_OF_PRODUCER`, the pinned-eight test, `test_crm_doc_matches_contracts.py` or
`test_internal_client_paths.py`. Each row below is added to the named release's task list, tests and
acceptance criteria where it is not already there.

| Test, row or table | Edited by | Required edit, in that release's closing commit | Spec text to add or amend |
|---|---|---|---|
| `deal/tests/test_event_contracts.py`, `AHEAD_OF_PRODUCER`: `crm.pipeline.deal.created`, `.updated`, `.stage_changed` | R2.3 (`EV6`–`EV8`) | delete the three rows; the three events get the plain conformance tests through the real write path; delete the three names from `test_the_ahead_of_producer_schemas_are_exactly_the_pinned_eight` (8 to 5) | SP2 §6.7 (tests) and `AC-2.3-9`; **`AC-2.3-12` gains a carve-out**: "the only existing tests edited are those named in §6.7 and the two SP1 files `deal/tests/test_event_contracts.py` and `libs/contracts/tests/test_event_schemas.py`" |
| `lead/tests/test_event_contracts.py`, `AHEAD_OF_PRODUCER`: `crm.lead.created`, `.updated`, `.converted` | R3.7a (`EV10`–`EV12`, registry `J21`) | same, with the schema's `$comment` removed in the same commit | SP3 §11 tests and the `grep -c "Ahead of the producer"` criterion (`:2794`) |
| `customer/tests/test_event_contracts.py`, `AHEAD_OF_PRODUCER`: `crm.book.company.created`, `.updated` | R4.1 (`EV1`, `EV2`) | same | SP4 R4.1 tests and criteria |
| `test_the_ahead_of_producer_schemas_are_exactly_the_pinned_eight` | each of R2.3, R3.7a, R4.1 | remove that release's names; once the set is empty the test asserts it is empty and stays, so a ninth cannot appear | the three rows above |
| `tests/infra/test_crm_doc_matches_contracts.py` and `docs/crm.md` | every release whose registry rows add, rename or remove a CRM event type (with its schema), a permission code or role template (§E) or a gateway port or prefix (`C67`–`C71`): R2.5 (`crm.pipeline.stage.*`, `EV14`; the code `E18`, `crm.pipeline.stage.write`), R2.8 (four templates; the codes `E19`–`E22`), R3.2 (the `/api/v1/companies` and `/api/v1/activities` rows, `C67`, `C68`), R3.6 (`EV19`–`EV21`, `EV26`, `EV27`), R3.7a (`EV15`, `EV53`), R3.7b (the `/api/v1/leads/public` and `/api/v1/webhooks/leads` rows, `C69`, `C70`), R4.1 (`EV18`; the code `E24`), R4.2 (`EV16`, `EV17`), R4.3 (the code `E23`), R4.5 (`EV22`–`EV24`, `EV28`; the codes `E25`, `E26`), R4.7 (`EV25`) | edit the event, permission, role or service table in the same commit | each of those releases' closing task and the acceptance criteria that name `docs/crm.md` |
| `tests/infra/test_internal_client_paths.py`, `CALLS` and `UNAUDITED_BASELINE` | every release that carries a registry `W` row not marked `exists` (`W3`–`W12`, `W15`–`W19`, `W21`–`W23`, `W25`–`W52`: 46 rows; `W24` is withdrawn, registry `J29`, and `W13`, `W14` and `W20` stay `exists`), wherever the client lives: SP2 R2.6, SP3 R3.7 and R3.8, SP4 R4.1 and R4.3 to R4.7 (in finance, operations, support, project, projects, storefront, marketing, approval, deal, lead, notification and customer, and in the insights service of the unmerged stack, `W44`–`W51`) | register each call with a driver that returns the token it sent; a client in a new file fails the guard until registered, and a baseline file whose match disappears leaves the baseline | the task list and criteria of each of those releases |
| `tests/infra/test_engage_recipe_triggers.py` | R3.6 (the three follow-up and stale keys with their three recipes, `EV41`), R4.2 (`crm.lead.assigned`, `EV41`), R4.7 (`EV42`) | the trigger key is in `SUBSCRIBABLE_EVENTS` in the same commit | SP3 R3.6, SP4 R4.2 and R4.7 |
| `tests/infra/test_ops_monitor_compose_env.py::test_ops_monitor_watches_every_schema_edge_relay_drains`; `tests/infra/test_route_table_parity.py::test_the_crm_prefixes_are_identical_in_dev_and_prod` | any release that adds a schema to edge-relay's list; R3.2 (the `/companies` and `/activities` rows) | add the schema to both lists, the rows to both tables, in one commit; if O-15 defers `R1.2-7`, R3.2's gateway commit also removes the two dead dev rows and adds the parity test | SP3 R3.2 §6.3.2 and §6.8 |
| Engage `SendSettings` rules: drop `recipient_kind` when the channel is not WhatsApp (rule 1) and when the path does not end in `contact_id` (rule 2) | R4.1 (`C45`, the `email` channel) | lift rule 1 and the helper's WhatsApp wording in the same commit as the customer route; rule 2 stays | SP4 R4.1 |
| The repaired recipe's `purpose: "marketing"` | R3.7b (the lead stores consent evidence, `B39`, and forwards nothing: registry `J23`) or R4.7 (the forwarding through `C58`), whichever first lets a converted lead carry evidence | re-decide it with the consent design | SP3 R3.7, SP4 R4.1 and R4.7 |

#### 9.2.3 SP2: answers are read from ADR-0038's State cell

SP2 greps ADR-0038 for strings a table row cannot hold (`D-9: approved`, `D-20:`, `D-21: <answer> on
<date>`). Replace each with the row grammar of §4.3, so the owner's answers are checkable by every later
release with one command:

| SP2 passage | Replace with |
|---|---|
| `AC-2.7-13` (`:2545`), the R2.7 sign-off sentence (`:2480-2481`) and `O-10` (`:2880`) | `grep -E '^\| D-9 \|.*\| approved [0-9]{4}-[0-9]{2}-[0-9]{2} \|$' docs/adr/0038-crm-programme-decisions.md` prints the row; the signed checklist S-1 to S-12 is one line under Sign-offs, `D-9: approved on <date> against checklist S-1 to S-12 of the R2.7 spec` (evidence, not the answer) |
| R2.8 precondition 0 (`:2721`), docs step 9 (`:2738`), the D-21 acceptance paragraph (`:2682`), `AC-2.8-9` (`:2762`) and `O-13` (`:2883`) | the same command with `(approved\|amended)` for D-20 and for D-21; "D-21: <the owner's answer> on <date>" becomes a Sign-offs line when the answer carries a staff plan action |

## 10. Deviations from the programme spec

Each is explicit, with its evidence; the programme is otherwise followed.

1. **D-13 first blocks R2.4, not R4.6** (P§8; registry `J32` returns the same amendment against P§8).
   One fold takes ten profiles plus `admin` from 3944 to 3945 MB (`memory_budget_totals.py`), above the
   owner's accepted ceiling; the five registered folds reach 3949 (registry `F8`, and `L17`, which amends
   D-13). ADR-0038 records the earlier block.
2. **D-23 names the `/deals/summary` fields among the removals with live users; this spec deletes no
   server field.** Registry `C9` freezes all nine fields, and tests, fixtures and ADR-0020's parity test
   pin them. The five unrendered fields get a renderer in R3.4; `trend` stays a documented reserved key;
   only the SPA's `trend` scaffolding is deleted (R3.4).
3. **P§4.1 says `lead` "appears nowhere else as a status or stage name after the cleanup".** The
   stored values `lead` on company status, contact status and the first deal-stage key stay: events and
   tenant rules carry them (registry `B17`, `L4`). What disappears is the *label*, in SP3. The glossary
   says so.
4. **Additions to P§10's R1.2 line**, each small, each independently revertible and recorded in §6 as a
   strike-able item or a test: the offset-less `occurred_at` fix (R1.2-4, found while writing the
   activity schema; written red first); the production-smoke 360 step (R1.2-13); the `NotFound` page,
   the four-placeholder removal and the dead `derive.ts` rollups now (DD-5); the Engage recipe repair
   (`EV39` requires one; it brings a one-field type extension, two builder rules, a helper line and
   `purpose: "marketing"`); the `org` claim on notification's three tokens (`C77`, which registry `J20`
   puts in R1.2a; the third token is A9's, so the guard asserts the claim with no exception list); the
   `docs/architecture.md`, `docs/deploy/ENV_SOURCE_MATRIX.md`, `docs/deploy/env-reference.md` and
   ADR-0020 edits that accompany code or text this sub-project changes; three new guard-test files and
   three extended ones (`F14`).
5. **"ops-monitor coverage" is the relay list only.** No DLQ queue is added in R1.2 because each ships
   with its fold (`F5`, `F7`, `F9`) and `test_ops_monitor_dlq_queue_sync.py` fails both ways; the
   schedule is pinned in `docs/crm.md` §10.
6. **D-11 blocks R2.4, not R1.1** (P§8 lists "R1.1 (ADR)"; registry `J32` returns the same amendment).
   R1.1 merges with D-11 open and ADR-0038 records the costing; the answer decides nothing in R1.1 or
   R1.2. Per §4.8 it first matters at R2.4, and only if D-13 is answered "no increase": one fold takes
   ten profiles plus `admin` from 3944 to 3945 MB, above the ceiling, and the D-11 rows are the only
   registered way to stay within it. ADR-0038's D-11 row says R2.4.
7. **R1.1 changes three files outside `docs/`** (P§10 says "docs only"): the READMEs of customer, deal
   and lead (DD-17). No behaviour changes, but each sits inside its service's image, so the next deploy
   rebuilds those three (§4.10).
8. **R1.2 ships as two deploys, R1.2a and R1.2b** (P§10 lists one release; registry `J31` returns the
   same split against P§10). The box's only rollback is the whole ref (`Makefile:577-583`), so a single
   deploy would roll the only defect fix back with any hygiene failure, and P§2's M2 asks for releases
   reversible on their own. R1.2a is the 360 fix, its guard tests, the organization claim and the smoke
   step; R1.2b is the rest. A document that says "R1.2" without a letter means both (§0).
9. **The organization claim covers three token mints, not two** (registry `C77` already reads three,
   two to customer and one to account, folded from this document's first draft, and `J20` puts it in
   R1.2a; P§10's R1.2 line lists no claim at all): A9's token to account carries the claim too, so that
   the cross-service test can assert the organization claim for every call under
   `/internal/v1/organizations/{org}/` with no exception list (§5.3.3).

