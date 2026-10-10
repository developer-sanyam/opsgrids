# CRM overhaul — sub-project 3: core revamp (features and UI) — design

Status: **proposed, design only — pending owner review.** No code is changed by this document or by
its plan. Base: `main` @ `caa7f112`; every `path:line` below was re-read at working-tree `HEAD`
`3bc7f6ca`, where the CRM paths are identical. Alembic heads today: customer `e230c025337e`, deal
`e94d7a2c5f18`, lead `91dd3ca663fd`, scheduler `bff4691114ce` (re-verified from the
`revision`/`down_revision` pairs in each `migrations/versions`). Every release below chains its
migrations after whatever head exists when it merges (merge order, programme P§7); the marker
`# RE-PARENT HERE` goes on each `down_revision`.

Reading guide. `P§n` is a section of the programme contract
(`2026-10-07-crm-overhaul-programme-design.md`), `P§3 F1`..`F11` its facts, `D-1`..`D-24` its owner
decisions. Owner decisions raised after the programme was written keep the ids of the consolidated decision
register (programme §8 as reconciled, REG §K), and four of them reach this document: `D-25` (raised by
REG L20), `D-27` (raised by SP4, shared with this document), `D-38` (REG L18 and J19) and `D-41` (this
document's question about a closed deal). The interface registry
(`2026-10-07-crm-overhaul-interface-registry.md`, "REG") is
**binding for every name** that crosses a sub-project boundary; its rows are cited by handle
(`A15`, `B6`, `C22`, `EV6`, `E19`, `F9`, `G1`, `H8`, `J1`, `L12`) and its names are used exactly. A
decision still open is marked **(pending D-n)** and the shape shown is the recommended default: the
marker stands in the heading or at the first use of every section whose design rests on one, and §2.1
lists each decision with the first release it blocks, including the ones SP3 depends on only through an
SP2 release. Where this document had to choose because discovery and the registry were silent, the choice is a
numbered design decision in §2.2 with the alternative it rejected. Inference (not observed in code)
is labelled as such. Names this sub-project invents that never cross a boundary are listed in §4.1
so the plan can use them. Every name that does cross a boundary is a registry row: the rows this document
proposed were folded into the registry under final handles (REG §J.2 holds the handle map), and §16 records
the outcome. The handles used here are those final ones.
**Memory figures are running totals**: each release states its effect as a change against the total
`memory_budget_totals.py` prints before it merges, because every consumer fold adds one MB (REG F8) and
the folds of R2.4 and R3.8 land on either side of this sub-project's releases (V32, §4.5).

## 1. Goal and non-goals

**Goal.** Turn the CRM's three services and one SPA from a thin CRUD front over a six-stage board
into the daily-use tool a small sales team works from, without breaking any of the repo's root
rules: tenant isolation, append-only records, sealed PII with audited reveal, server-side
authorization with a negative test per endpoint, money in integer minor units, no cross-schema
foreign keys, outbox-after-commit, and the one-box memory budget. Concretely, in eight
independently releasable steps (programme M2):

1. **R3.1 Data-model fit.** A company can carry a legal name, GSTINs and addresses; a contact can
   exist with only a phone; a deal has a human number and can record why it was won or lost; a
   lead carries its campaign and UTM fields; a phone-only lead converts into a phone-only contact
   instead of losing the number.
2. **R3.2 API cleanup.** Every list is searchable, sortable, filterable and paged on the server
   with stable order; `/companies` is the canonical noun; the 360 lives under
   `/contacts/{id}/360`; ids resolve by `ids=` instead of a 200-row cache; errors are documented
   in OpenAPI; the SPA's wire types are generated from the committed contracts.
3. **R3.3 Shared UI foundations.** One Kanban, one detail-list, one customer-360 view, one
   reveal control, one saved-view menu, one money formatter, and CRM entity search in the command
   menu, all in `@opsgrids/ui` / `@opsgrids/client`, with no change to what a CRM user sees.
4. **R3.4 Shell, IA, settings hub and list rebuilds.** A route registry with lazy routes, no dead
   navigation, a settings hub, and Leads / Deals / Companies / Contacts rebuilt on the shared
   components; the in-client fixtures mode, the dev login, the page-only CSV export and the per-card
   stage select are removed after their replacements ship (the placeholder pages are SP1/R1.2b's).
5. **R3.5 Contact, company and deal pages.** `/contacts/:id` hosts the customer 360 and a merged
   timeline; company and deal pages get tabs, inline editing, stage history and win/loss prompts
   with confirmation on terminal moves.
6. **R3.6 Follow-ups and reminders.** A dated, mutable follow-up with a scheduled reminder in the
   rep's in-app feed, stale and overdue deal alerts, a "my follow-ups" tile and a rebuilt Overview.
7. **R3.7 Lead capture, dedupe and assignment.** A public web-form intake and a signed webhook with
   abuse controls, WhatsApp strangers becoming leads through Engage, duplicate detection across
   leads and contacts, an assignment-rule screen, SLA and aging, bulk reassign, and (a release earlier than
   any capture) an administrator's per-lead erase of a person's details.
8. **R3.8 Saved views, tags, history, reports and team.** Saved views and tags, record history, a
   forecast and a win/loss report, and TEAM scope for managers from `manager_membership_id` through
   a per-service owner-team projection.

**Success is checkable.** Each release section ends with acceptance criteria a reviewer can run:
named tests, `make` targets, `grep` assertions over the tree, or a stated count. §13 lists what only
the owner can run.

**Non-goals** (each is out of scope by decision, programme §5, or by this document, §15):
two-way mail or calendar sync, telephony, AI scoring or drafting, a second workflow engine, full
CPQ, custom objects or custom fields, tiering CRM modules by plan, a new container for any feature,
a CRM-local BI service, cross-product sections of the 360 (those are SP4's: orders, finance, support,
delivery, party timeline), any change to consent or to contact erasure (SP4, ADR slug
`crm-consent-and-erasure`; the one erase SP3 ships is an administrator's per-lead erase of a lead's own copy,
§11.3.3), merge of duplicates, relaxing deal-name uniqueness, and a dark-theme toggle.

## 2. Decisions

### 2.1 Owner decisions this sub-project needs (programme §8)

The recommended default is what every section below is written against. "If answered otherwise" is
the part of this design that moves.

| ID | Decision (programme §8) | Recommended default used here | If answered otherwise | First release blocked |
|---|---|---|---|---|
| D-1, D-2, D-3 | NEW-9: what happens to the orphan deal; how far customer cleanup may go; whether past aborts left orphans | Soft-delete with an audit row and no `stage_changed` event; only records provably created by the aborted attempt, log-only on the first deploy; the owner's read-only count of past `lead.conversion_aborted` audit rows. SP3 uses only the **fold** R2.4 builds (the deal consumer inside the API process, the `deal.events` queue, its DLQ entry and its +1 MB), never the cleanup handler | Only NEW-9's handler body and its sweep change; the fold that R3.6 and R3.8 stand on does not. **R2.4 cannot start until all three are answered (P§10), so neither can R3.6 or R3.8** | R3.6 and R3.8 (through R2.4) |
| D-5 | Stage model | One pipeline per org; stages are data from `GET /deals/stages` (C1); `outcome` `open\|won\|lost`; archive never delete; closed deals immutable | A second pipeline would add a pipeline picker to the board, list filters and the settings hub; every `stage` UI keys on the stage key, so only selectors change | R3.4 (board and settings), R3.5 |
| D-6 | Who edits stages | `crm.pipeline.stage.write` at an ORG floor; read stays under `crm.pipeline.deal.read` (E18) | A different code changes the `useCan` gate on the Pipeline settings page and one fixture grant | R3.4 |
| D-7 | What a rep is after C15 | `owners.membership_id`; Team screen lists org members and activates them (C25-C27); free-form creation retires | If reps stay a free roster, the Team screen stays the current create form and every "my" view keeps `owner=me` on the server | R3.4 |
| D-8, D-10 | The owner-id mapping rule; the production CRM census | Email match from roster to member with the employee link for employee-valued rows, unmapped rows untouched and listed, never guessed, one owner-run dry run per organization; the owner runs a read-only census (organizations with CRM data, roster sizes, employee links, the `lead.outbox` backlog) | SP2's tool changes, not SP3: after R2.7 every screen here reads `owner_id` as a membership id; an unmapped row is an ordinary row (visible at ORG, invisible at OWN) and the rep views are tested at ORG for reads and OWN for writes either way. **R2.6 cannot start before both are answered, and R2.7 builds on R2.6** | R3.4 (rep views, through R2.6 and R2.7); R3.8 (C57, through R2.6) |
| D-9 | Access widening C15 causes | Approved with a security review of every OWN route before the flip (reps write, convert and reveal on their own rows) | Not approved means R2.7 does not flip and the rep views in R3.4-R3.8 are tested at ORG only; no SP3 code changes | R3.4 (rep views) |
| D-11 | Fold `lead` into `customer` (REG J32: it blocks R2.4; P§8 says R1.1's ADR) | **Keep three processes** for this programme: R3.7 and R3.8 are written against `lead` as its own service with its own schema, capture routes, secret and, from R3.8, its own consumer | If yes: lead's tables, capture endpoints, capture secret, public routes and the lead fold (F9) move into `customer` through a cross-schema data move this document does not design. R3.7 and R3.8 are re-planned, the lead fold and its +1 MB disappear, the gateway rows (C69, C70) point at customer, and `owner_team` for leads becomes customer's | R3.6 and R3.8 (through R2.4, REG J32); R3.7 and R3.8's F9 for this design |
| D-12 | Insights vs stages order | Stages land first (`a1c9e2f7b3d4` adopted by R2.3); reports in R3.8 read `deal` directly behind an adapter and insights replaces the adapter later | If insights lands first, R3.8's reports are re-pointed through the adapter in §12.6 and `closed_at` is already there | R3.8 |
| D-13 (amended by REG L17) | Memory ceiling | R3.8's lead fold costs +1 MB on top of R2.4's deal fold, which R3.6 and R3.8 both stand on (every fold is +1 MB, REG F8); the owner raises `EDGE_BUDGET_ACCEPTED_MB` by one per fold, release by release, or frees memory (REG L17); each release here states its memory as a change against the running total (§4.5) | Refusing the +1 MB of R2.4's deal fold stops R2.4, and R3.6 and R3.8 with it; refusing the +1 MB of R3.8's own fold blocks TEAM scope on leads (lead has no other way to feed `owner_team`) | R3.6 and R3.8 (through R2.4, which D-13 first blocks: REG K, J32); R3.8 again for its own fold |
| D-14 | Party identity | Company is the party, contact the person, phone-only contacts allowed (email nullable) | If refused, R3.1 keeps `contacts.email` NOT NULL and "carry the lead phone" becomes a stored lead phone shown on the company page instead | R3.1 |
| D-20 | Role consolidation | Four `CRM_*` templates (REG E.2), TEAM widening of `†` cells in R3.8 | Keeping six roles changes only which role carries each new code in §4.3 | R3.8 |
| D-21 | Plan packaging (P§8: it blocks R2.8) | Stay flat: one `crm` entitlement gates everything here (public capture reads it, REG L12); fix or retire the INR 299 `crm/growth` plan; modules are not tiered by plan (out of scope, P§5) | If packaging became per-module, every route's `Entitled` check, public capture's entitlement read (L12) and Engage's stranger step would each name a module, and R2.8's catalogue would change with it | R3.4, R3.6 and R3.7 (through R2.8) |
| D-22 | API nouns | `/companies` canonical, `/customers` aliased, 360 under `/contacts/{id}/360`, `/activities` aliased | If refused, R3.2 shrinks to "list vocabulary + errors" and the SPA keeps `mappers.ts` | R3.2 |
| D-23 | Removals that had live users | Replace, then delete: fixtures mode, `Login.tsx`, CSV export, per-card select (the `/deals/summary` fields are kept, §8.6.5) | If refused, those four files stay and keep their token offenders | R3.4 |
| D-24 | TEAM scope | Included, from `manager_membership_id` through a per-service `owner_team` (B4) | If refused, R3.8 ships saved views, tags, history and reports only and the lead fold (F9) is not needed | R3.8 |
| D-25 (REG L20) | May managers reveal their team's sealed phone or email | No: `CRM_MANAGER` holds `crm.book.contact.read_sensitive` and `crm.lead.lead.read_sensitive` at OWN (REG E.2 ‡) | If yes, those two role cells become TEAM and the D-9 security review covers them; no code in SP3 changes | R3.8 |
| D-27 (raised by SP4 §2.1, shared with this document; absorbs this document's lead-erase question, REG L23) | What each store keeps after an erase (finance documents, consent evidence, audit rows, free text), and what the erase of a lead keeps | For leads: an administrator erases **one lead at a time, in place** (§11.3.3): its name, company, email, phone, notes, the visitor's messages, GSTIN and duplicate pointers are cleared and its audit copies rewritten; source, status, owner, attribution, the consent evidence (it holds no identifier) and the ids of what the lead converted into stay. Public capture stores personal data of people who never signed in, so the ADR `crm-public-lead-capture` records this answer | The list of cleared and kept fields in §11.3.3 changes; the route, its permission and its tests do not. Two other answers move more: a dedicated `crm.lead.lead.erase` code on the route instead of E22 (it needs SP2's catalogue and a reconcile, like E24), or a bulk erase (a second route, out of scope here, §15). **R3.7b does not ship before the owner has answered D-27 for leads** (§4.2) | R3.7b |
| D-38 (REG L18, J19) | Should a rep **read** only their own records? | No: reps read at ORG and write at OWN, as today (P§3 F2); managers and viewers read at ORG too (REG E.2); P§1 criterion 1 is reworded (REG J19, §17 item 10) | If yes, the read cells of the rep templates narrow to OWN in R2.8, a larger change than D-9 approves; the rep views are then tested at OWN for reads as well as writes, and teammates' records leave a rep's lists, board and 360. No SP3 code changes | R2.8 (the role templates); R3.4 here (rep views, through R2.8) |
| D-41 (this document's question; REG L22) | May `PATCH` still edit a closed deal's value, currency, probability and close date? (D-5 says "closed deals immutable") | Yes, no server change in this programme: D-5's rule is the no-move rule SP2 owns (a closed deal moves nowhere: 409), and the deal page renders a closed deal read-only (DD-34) | If "immutable" also covers field edits, the server answers 409 `closed_deal` on `PATCH` of those four fields (DD-34's rejected alternative) and live bulk update and import, which edit through the same route, change with it; the page is already read-only | R3.5 |

Registry questions this sub-project touches and the default it uses (the owner decisions among them have
rows in the table above): **L2** (R1.2b stops writing `leads.phone_index`; R3.7 resumes writing it and H8
backfills it), **L8** (R3.6 does not wait for the Engage-scheduled-send question; A17 keeps the notification
mapping out of scope), **L11** (tax ids, addresses and legal names of sole proprietors are personal data:
audit rows hold ids and field names only and events and summaries carry business facts only, §5.4 rule 8),
**L12** (public capture checks the `crm` entitlement itself through product-service, 60 s cache), **D-13**
(the five folds; R3.8 owns the lead one), **D-38** (reps read at ORG and write at OWN), and the two questions
this document raises, **D-41** (a closed deal's fields; first release R3.5) and **D-27**'s lead half (the erase
of a captured lead, pending).

**One-way points the owner acknowledges (O1).** Programme M2 says every release is reversible on its own.
Three points in this sub-project are not, and each is stated here so that the owner accepts them with
open eyes rather than finding them in a rollback: **K1**, below R3.1a stays closed once a phone-only
contact exists (the previous code cannot read a NULL email); **K2**, below R3.7a stays closed once a
captured lead exists (the previous code answers 500 for a list holding a `webhook`, `whatsapp` or
`storefront` source), and a visitor's stored details can then only be removed by an administrator's
per-lead erase (§11.3.3); **K3**, the TEAM widening of R3.8 cannot be narrowed by any tool, because
`reconcile_permissions.py` is union-only (ADR-0007 §1) and `tools/revoke_permission.py` does not exist. Each
has a mitigation (§14) and none is avoidable by design; the owner's O1 reply records that K1 to K3 are
accepted, and O16 records the soak evidence before the widening is applied.

### 2.2 Design decisions this document makes

Each row is a choice the programme and registry left open, the alternative rejected and why.

| # | Decision | Rejected alternative | Why |
|---|---|---|---|
| DD-1 | R3.1 ships as **two deploys**: (a) schema plus tolerant readers, creating nothing new; (b) the change that lets a contact exist without an email | One deploy that also enables phone-only creation | The previous release's `ContactOut.email: str` (`services/customer/app/schemas.py:218`) and `mask(contact.email, kind="email")` (`services/customer/app/c1_360.py:320`) fail on a NULL email, so a code rollback after a phone-only row exists would break every contacts list. With (a) soaked, rolling back (b) leaves rows (a) tolerates |
| DD-2 | Default win/loss reasons are **virtual with deterministic uuid5 ids**, materialised under an organization advisory lock by the first write that needs them | `GET` materialises; a migration seeds only organizations it can see; the move body carries a key | A read must not write; a migration cannot see organizations that have no deals yet; the registry's `StageMoveIn.close_reason_id` (C8) needs an id the browser can send before any row exists |
| DD-3 | Contact and company emails are canonicalised as `strip().lower()` and indexed with `platform_core.pii.blind_index(canonical, kind="email")`; **new** writes refuse a case-variant duplicate once the row's index exists | Keep the exact-string match (`selectors.py:301-309`) | `blind_index` hashes exactly what it is given (`pii.py:157-191`); without canonicalisation "A@x.com" and "a@x.com" are different people to every dedupe in R3.7 |
| DD-4 | A phone-only lead converts into a contact; conversion reuses only a **live phone-only contact with the same phone**, never an emailed contact matched by phone | Match any contact by phone | Two people legitimately share an office line (the reason `ix_contacts_org_phone_index` is not unique, `b5c81e02a7d9_contact_phone_index.py`); merging them silently is the data loss dedupe exists to prevent |
| DD-5 | Deal-name uniqueness (`deal/app/services.py:130-131`) and the status a converted contact starts in (`services/customer/app/services.py:1233`, `customer`) are **unchanged** | Relax the rule for renewals; start converted contacts as `qualified` | Neither is in the programme's R3.1 list; import update mode keys on deal name; SP4's lifecycle automation (R4.3) owns status transitions. Both are recorded in §15 with the trigger to revisit |
| DD-6 | List helpers (`escape_like`, `parse_sort`, `apply_sort`, `parse_ids`, `parse_tz`) live in a new `platform_core.listing` (REG A20); error documentation in `platform_core.errors` (REG A21) | Three per-service copies | Three services need byte-identical behaviour (422 on an unknown sort key, `-` prefix, `id` tie-break, LIKE metacharacter escaping); a copy would drift. Cost: a platform-core change reruns every service's tests (REG A15's note) |
| DD-7 | The organization's timezone for CRM date filters and "today" is `deal.pipeline_settings.timezone` (default `Asia/Kolkata`); the SPA reads it and sends `tz=` on list calls | Read `account.organizations.timezone` per call; hard-code IST | CRM services have no account call on the request path and `leave.leave_settings.timezone` is the repo's precedent for a service-local zone (`services/leave/app/models.py:110-114`); finance's `DEFAULT_TIMEZONE` fallback is the precedent for the default (`services/finance/app/domain.py:2669`) |
| DD-8 | No `pg_trgm` index; `q` is an `ILIKE` over the organization's own rows with metacharacters escaped | GIN trigram indexes | The extension is created by dev bootstrap (`tools/dev-bootstrap/init-db.sql:6`) but `infrastructure/edge/postgres-init/` holds only `role-passwords.sh`; whether the box has it is unverifiable from the repo. The scale ceiling is stated in §14 |
| DD-9 | `GET /deals/summary` is computed by **SQL aggregation per (currency, stage)** folded by a pure function, and **one integer rounding rule** replaces the float path in SQL and in Python alike: a deal's weighted value is `weighted_minor(value_minor, probability) = (value_minor * probability + 50) // 100` (half up; in SQL `floor((value_minor::numeric * probability + 50) / 100)`, exact, so no overflow and no float) and a win rate is `win_rate_pct(won, decided) = (200 * won + decided) // (2 * decided)` (half up). `domain.py` is changed to those two functions, and its rollups stay as the reference the SQL path is tested against | Keep loading every deal into Python (`selectors.py:97-102`); keep `round(value_minor * probability / 100)` as the reference | It removes the silent 200-row cap and the cross-currency sum. The existing arithmetic cannot be a reference for SQL: Python's `round` is banker's rounding over a float quotient (`services/deal/app/domain.py:74, 87`) while integer SQL division truncates, so 103 at 50 % is 52 in Python and 51 in SQL, 999 at 33 % is 330 and 329, and 1 won of 8 decided is 12 where half up gives 13; and ADR-0000 §8 keeps floats out of money. The frozen legacy fields keep their names and meaning but may move by **one minor unit per deal** (weighted pipeline) and **one point** (win rate) at exact halves; §6.4 rule 5 states it and a test pins it |
| DD-10 | `RevealableField` (ui) receives the **result of** `useRevealedField` (client) as a prop; REG G6's props `stepUp`, `resetKey`, `load` move to the hook call | The ui component calls the client hook | `@opsgrids/ui` may not import `@opsgrids/client` (`frontend/.dependency-cruiser.cjs:25-65`, rule `ui-imports-no-client`). REG G6 carries the amended props |
| DD-11 | URL-synced list state is a **CRM-local** hook (`useListQuery`) | A shared client hook now | Only CRM needs it today; promotion later costs one move. Identifiers (email, phone) never go in a URL: the SPA fallback `try_files {path} /index.html` (`infrastructure/edge/caddy/Caddyfile:332`) means a reloaded URL reaches Caddy's access log |
| DD-12 | Offline interactive development of CRM screens **ends** with fixtures mode; the replacements are typed builders for tests, contract-validated page fixtures for the visual harness, and the seeded demo organization for stateful work | A generated MSW handler set; a shared dev mock server | The visual fixtures are static route mocks validated against OpenAPI (`frontend/opsgrids-visual/fixtures/contracts.test.ts`), not a stateful server; building one is a new tool outside this programme. Ten sibling apps keep their own fixtures mode: the divergence is the cost D-23 accepts |
| DD-13 | `/login` in dev becomes a small **dev-only** component compiled out of production builds (`import.meta.env.DEV`); production keeps `RedirectToCentralLogin` | A shared dev login in `@opsgrids/ui` / `client` | A shared login changes the pattern ten siblings copy; that is a platform ticket, not this overhaul |
| DD-14 | "Export selected" calls the Excel export engine with `ids=` (C35, at most 100 rows); page sizes on list screens are 25, 50 or 100 | Keep `lib/csv.ts` | The CSV helper has no formula-injection guard (`frontend/product-crm/src/lib/csv.ts`) and exports only the loaded page; ADR-0022's engine has the guard and the whole-filtered-list semantic |
| DD-15 | Close reason is **optional on the wire, required in the UI** on a won or lost move | Required server-side | The import engine, bulk update and automations move deals through the same route (ADR-0022); a server requirement breaks them |
| DD-16 | The deal variant of C15 (`GET /deals/{deal_id}/history`, an allow-listed projection of `audit_logs`) ships in **R3.5**; company, contact and lead variants and every "History" tab ship in R3.8 | A new append-only `stage_history` table; ship C15 whole in R3.8 | `move_stage` already writes `deal.stage_changed` audit rows with `before.stage` and `after.stage` (`services/deal/app/services.py:320-331`); a second source of truth would disagree with the audit trail. REG C15 registers the deal variant in R3.5 (moved forward from R3.8) and the other variants in R3.8 |
| DD-17 | Follow-up reminders are armed at `due_at`; a follow-up created already due is **not armed** and is simply overdue | Arm everything | A reminder at "now" interrupts the person who just typed it |
| DD-18 | Completing a follow-up writes **no** activity; the completion dialog offers an optional "log what happened" that calls the ordinary activity route | An automatic system activity | Activities are append-only and free text (`models.py:99-138`); a system row built from the follow-up's title would copy possibly personal text into a record nothing can erase |
| DD-19 | Stale and overdue alerts run from **one scheduled review per organization per day**, a self-re-arming chain (REG EV27's payload is `organization_id, as_of`) | One scheduled job per deal and kind; a folded sweep | Per-deal arming adds two scheduler rows per activity and has no way to arm deals that predate it; a sweep needs a cross-tenant discovery role and a column-level grant (`services/scheduler/app/selectors.py:1-12`'s pattern) in a third schema. The chain is O(organizations) and the consumer is already tenant-bound per message |
| DD-20 | The first review per organization is a **silent baseline**: it marks currently stale and overdue deals as notified and publishes nothing | Notify everything at go-live | Prevents a notification storm the day R3.6 ships |
| DD-21 | The in-app reminder is delivered by **code-defined system rules** in notification (`domain.SYSTEM_IN_APP_RULES`, REG EV54), evaluated beside the tenant's own rules; it is the **one** mechanism for system in-app alerts, so R4.2's assignment alerts are more entries of the same mapping and not a second loop (SP4's first draft named the same mechanism `BUILTIN_CRM_ALERTS`, which is withdrawn: EV54 keeps this name and structure and SP4's R4.2 extends the mapping, §10.3.3) | A `Rule` row per organization seeded by a migration or by `core.product.activated`; deal staging messages itself; CRM calling notification routes | Rules are tenant data created through an API CRM roles cannot call (`services/notification/app/api/routes.py:291-312`); a migration cannot enumerate organizations; seeding on activation misses live tenants. Opt-out lives at the producer (`deal.pipeline_settings.notify_*`) |
| DD-22 | The reminder text is generic and carries no deep link until R4.2 adds `entity_ref` (C72) | Put the follow-up title in the message | The title is free text that may hold personal data and the bell (`frontend/opsgrids-client/src/notifications/NotificationsBell.tsx`) renders `subject ?? body` and links nowhere |
| DD-23 | Public capture accepts **JSON and `application/x-www-form-urlencoded`**; a browser form post is answered `303` to the endpoint's `success_url`; `Origin` is checked softly against `allowed_origins`; no cross-origin script call is supported | Per-tenant CORS in the gateway; a hosted form page; a CAPTCHA provider | CORS is a static box-level list (`libs/platform-core/src/platform_core/app.py:336-350`, `config.py:186-201`) and the edge states "no hostname adds a CORS surface" (`Caddyfile` same-origin comment); a plain HTML form post is not subject to CORS. A CAPTCHA needs an egress decision (ADR-0016) |
| DD-24 | Capture of an **already-known email never creates a second lead**: the existing lead's `last_touched_at` is set and the message is appended to its **capture messages** (DD-44), never to its notes; the response is identical either way | Return 409; create a second lead | `uq_leads_org_email_index` allows one live lead per email (`models.py:148-154`) and a 409 is an enumeration oracle (REG C41) |
| DD-25 | Duplicate flags are **persisted at capture** (`duplicate_of_lead_id`, `duplicate_contact_id`, REG B62) and **shown by permission**: a boolean `possible_duplicate` reaches every lead reader; the lead id only where that lead is inside the reader's `ctx.apply` scope; the contact id only to a caller holding `crm.book.contact.read` at ORG (§11.3.1) | Recompute on demand; return both ids to every lead reader | Recomputing needs the plaintext email and phone, which are sealed; opening them for a display would write a sensitive-read audit row per view. At capture the plaintext is in hand. A stored id would otherwise carry the existence of a contact, or of another rep's lead, across a permission boundary that programme §7 says no new route may widen |
| DD-26 | New lead email indexes use the canonical email (DD-3); H8 gains `--service lead --target email_index` to rebuild existing rows | Leave lead hashing case-sensitive | Dedupe across leads and contacts must agree on case (`lead/app/services.py:457-461` hashes the exact string) |
| DD-27 | "Round-robin skips inactive members" is today's active-owner check (`services/lead/app/services.py:409`) surfaced in the UI; membership revocation reaching the roster is R4.7's hook | Add a membership-status read to lead in R3.7 | It would duplicate R4.7 and needs W19 (the account URL) one release early |
| DD-28 | Lead SLA is a **first-response clock**: `first_response_at` is set when a rep takes a lead out of `new`; aging counts from `last_touched_at`; SP3 sends no SLA notification | Notify at 80 % and on breach | No event, rule or recipient for it is registered; the clocks and filters ship, the notifications are a later decision |
| DD-29 | TEAM stamping **restamps** `owner_manager_id` when a member's manager changes, not only when the owner is set | Stamp at assignment only, stale after a re-org (REG B5, leave's precedent) | A manager who left a team would keep write access to it; staleness here is an access defect, not a display one |
| DD-30 | Saved views store absolute filters plus `owner=me`; relative presets ("closing this month") are **code-defined links** resolved at click | Relative tokens inside stored filters | Keeps the stored shape exactly "the query parameters the list accepts" (B14) and the server free of date macros |
| DD-31 | Tags have **no routes and no index**; autocomplete uses the tags of rows already loaded; values are `strip`, lower-case, internal runs of spaces collapsed, `^[a-z0-9][a-z0-9 _-]{0,39}$`, at most 20 per record | A tag catalogue; a GIN index | REG C34 forbids routes; `jsonb_path_ops` cannot lead with `organization_id` without `btree_gin`; the filter runs inside the organization's own rows |
| DD-32 | Reports are SQL aggregations per currency with one adapter seam in the SPA | A CRM reporting service | REG TRAP-13; ADR-0020 chose one projection |
| DD-33 | Every audit row the CRM services write carries `actor_membership_id` inside `after` when the principal has a membership; the history projection lifts it out and never returns it inside `after` | A `membership_id` column on the platform-wide `AuditLogMixin`; resolving user ids to names through account | `AuditLog.actor_id` is the user id (`deal/app/services.py:156-168`) and no roster maps it to a name; widening the shared mixin means a migration in every service; an id in `after` is additive, non-personal and survives `redact` |
| DD-34 | A closed deal's page renders read-only in the UI; the server's no-move rule is unchanged and `PATCH` of a closed deal's fields is **not** newly refused (question D-41) | A server 409 `closed_deal` on `PATCH` of value, currency, probability and close date | D-5's "closed deals immutable" is read by SP2 as the no-move rule; refusing field edits changes live bulk-update and import behaviour and needs the owner's answer |
| DD-35 | The stale and overdue review chain is armed lazily by deal writes (`ensure_review_armed`) and its markers are cleared by the review itself; no migration data step | A migration that stages one `platform.schedule.requested` row per organization; clearing markers at every write path | A migration cannot see organizations that have no deals yet and would depend on the outbox's shape; write-time clearing couples four write paths to the review; the daily clear gives the same "one event per crossing" at day granularity |
| DD-36 | R3.7 ships as two deploys: tolerant sources first (R3.7a), capture second (R3.7b) | One deploy | The previous code's `LeadOut.source` literal makes any list holding a `webhook`, `whatsapp` or `storefront` lead a 500, so there is no safe rollback once one exists |
| DD-37 | Public capture is limited by four Redis budgets that fail closed (per address and endpoint, per endpoint, and per organization in **two** buckets: anonymous form posts and signed webhooks never share one, DD-46) and lead's `create_app` sets `trust_forwarded_for=True` so the address is the visitor's | The gateway's per-address limit alone; a CAPTCHA | The gateway's 60 a minute is per address and bounds neither an endpoint nor an organization; a CAPTCHA needs an egress decision (ADR-0016); without the flag every visitor is the gateway's own address |
| DD-38 | TEAM is activated by a separate, last, owner-run reconcile after a soak and a zero-drift H10 run; the wiring ships inert | Ship the wiring and the widening together | After the widening a code rollback makes every manager write a 403 and no tool narrows a grant; the soak is the only place that risk can be retired |
| DD-39 | A WhatsApp stranger becomes a lead only when the organization turned `whatsapp_leads_enabled` on (default off), capped at 60 an hour, named with the masked number, through claim, call, record, and **best effort** (DD-45): an unreachable lead is a counted skip, never a dead letter | A lead for every unknown sender; a rule-engine action | Creating records from inbound messages is processing the sender did not ask this product for; a flood would fill the book; the sealed number must not appear in a name; a network call must not hold a transaction |
| DD-40 | Three detail pages keep reading the 200-row directory inside a route-level legacy boundary until they are rebuilt (R3.5, R3.7) | Rebuild every detail page in R3.4; keep the provider around the whole app | R3.4 is already the largest release; the boundary keeps list and shell paint free of the three directory calls |
| DD-41 | Reports aggregate per currency in SQL from the same scoped statement the lists use, and `weighted_minor` and the win rate are the summary's functions (DD-9's two integer rules), so Overview and Reports can never differ by a rounding point | Python rollups over loaded rows; a second rounding rule in the reports | One definition of "weighted", one of "win rate", one scope rule, no row cap (DD-9, DD-32) |
| DD-42 | Each arm of a follow-up reminder takes **its own scheduler key**, `follow_up:{id}:{due_at_utc_iso}:{arm_seq}`, where `arm_seq` is a counter on the row that only grows: **no key is ever requested twice** | Reuse `follow_up:{id}:{due_at_iso}`; teach the scheduler to revive a cancelled row on a repeated key | The scheduler keeps a cancelled row, with its key, for the retention window (`scheduled_job_retention_days = 7`, `services/scheduler/app/config.py:20`) and ingests with `ON CONFLICT (organization_id, idempotency_key) DO NOTHING` (`services/scheduler/app/services.py:77-104`, `uq_scheduled_jobs_org_idempotency` in `13ef09d48a2b`). A follow-up moved from time A to B and back to A inside a week would re-request A's key, land on the cancelled row and create no job while `follow_ups.schedule_key` still said "armed": the reminder would never fire and nothing would log or dead-letter it. A per-arm key leaves the scheduler's idempotency exact for every other producer (finance's early-arrival defence relies on it) and needs no change to its semantics |
| DD-43 | Deleting a company **soft-deletes its tax ids and addresses in the same transaction** | Leave them live and filter every lookup through a join on `companies.deleted_at` | `delete_company` sets only `companies.deleted_at` (`services/customer/app/services.py:562-605`) and `uq_company_tax_ids_org_kind_value` is partial on the tax-id row's own `deleted_at`, so a deleted company would keep its GSTIN reserved: adding it elsewhere would answer 409 naming a deleted company, and conversion's "reuse the company that already holds that GSTIN" (C48) could pick a deleted holder and fail the later tax-id insert on every retry, because the ids are deterministic |
| DD-44 | The free-text message an anonymous visitor sends lives in **its own list on the lead** (`capture_messages`: at most five entries of 2,000 characters, each with its source and time), never in `notes` | Append to `notes`; a separate table | `notes` is rep-authored and rep-edited, and an address is public knowledge, so appending to it would let an unauthenticated party write unverified text inside a trusted record. A list the page renders as plain, labelled, read-only text leaves rep notes untouched, is erased with the lead, and needs no table (the lead row already holds the visitor's other details) |
| DD-45 | Engage's stranger step is **best effort**: an unreachable lead (down, no container, an unset or unresolvable `LEAD_INTERNAL_URL`, a timeout) is a counted skip that is neither retried nor dead-lettered, and a definite "no" (`not_entitled`, `disabled`) is cached per organization for five minutes in the notification process | Raise and let `EventConsumer` redeliver once then dead-letter, as the contact fold does; read the entitlement from product-service inside notification | Notification has no entitlement read and this release adds none (W36 has no notification-to-product row), and the switch lives in lead, so only lead can answer. A dead letter per stranger message during a lead outage would trip ops-monitor's `notification.events.dlq` alert for a convenience feature. A stranger who messages again is tried again, because the ledger row is per event |
| DD-46 | Public-capture budgets: **anonymous form posts and signed webhooks never share an organization bucket**, and exhaustion is shown to the tenant on the Sources page | One organization bucket for both | The form token is public by design (K8), so anyone who finds it could spend the organization's whole daily allowance and make its authenticated integrations answer 429. A signed sender proves possession of a secret, so its bucket is separate and larger; the anonymous bucket bounds what an attacker can spend |
| DD-47 | Erasing a lead is **an administrator's act on one lead**: anonymise in place, soft-delete, rewrite its audit copies; no bulk erase | A delete route for junk (the first framing of the lead-erase question, now part of D-27); waiting for SP4's contact erase to reach leads | SP4's erase is keyed on a contact and reaches only converted leads (SP4 §6.5: an unconverted lead "has no erase route"); a visitor who never converted has no contact. Public capture stores that person's details from R3.7b, so the means to remove them must exist one release earlier. It reuses E22 (`crm.lead.settings.write`, ORG, admin templates only) behind a step-up, so no permission code is added; a dedicated `crm.lead.lead.erase` is the alternative that D-27 puts to the owner |
| DD-48 | Tax-id, address and legal-name writes audit **ids and field names only** | Audit before and after values the way `update_company` does | `record_audit` redacts by key name only (`libs/platform-core/src/platform_core/audit.py:29-55, 106-115`: `pan` is listed, a value under `value`, `line1` or `postal_code` is not), and individuals are one-person companies (P§4.2), so an address or a legal name is personal data that no erase reaches (REG L11) |

## 3. Current state (re-verified at `3bc7f6ca`)

The facts below are the ones this design depends on; discovery mapper claims that a skeptic
corrected are stated in their corrected form.

| # | Fact | Evidence |
|---|---|---|
| V1 | `customer.contacts.email` is `NOT NULL`; live-row unique on `(organization_id, email)`; phone has `phone_enc`, `phone_index` and a **non-unique** index | `services/customer/app/models.py:171, 222-228, 188, 206, 234` |
| V2 | A company has no legal name, tax id, address or tags (only a free-text `domain`); `name` is unique among live rows (case-sensitive) with a non-unique case-folded lookup index | `services/customer/app/models.py:95-154, 132-138, 146-151` |
| V3 | Contact create and update write `email` and `full_name` into audit rows, and `contact.reused_for_lead` writes masked phones | `services/customer/app/services.py:676-687, 744-755, 1125-1156` |
| V4 | Conversion creates a contact only when the lead has an email, with status `customer`, even though a lead's phone is passed | `services/customer/app/services.py:1207-1236` (docstring `:1182-1184`); `services/lead/app/services.py:1324-1336` |
| V5 | Lists have no `sort`, no owner filter on companies and contacts, no search on leads; `q` is an unescaped `LIKE %q%`; companies and contacts order by name only; deals by value then created | `services/customer/app/selectors.py:140-166, 247-278`; `services/deal/app/selectors.py:33-62`; `services/lead/app/selectors.py:28-44` |
| V6 | `GET /deals/summary` loads every in-scope deal and activity into Python and sums across currencies, labelled with the first deal's currency | `services/deal/app/api/routes.py:134-191`; `services/deal/app/domain.py:62-125` |
| V7 | `deal.activities` is append-only by trigger; activities attach to deal, company, contact and never a lead; `log_activity` forces the owner for an OWN caller | `services/deal/migrations/versions/e94d7a2c5f18_enable_row_level_security.py:90-107`; `services/deal/app/models.py:99-138`; `services/deal/app/api/routes.py:240-245` |
| V8 | `move_stage` writes `deal.stage_changed` audit with `before={stage}` and `after={stage, probability}` and publishes `from`/`to` only | `services/deal/app/services.py:296-347` |
| V9 | Deal and lead run no consumer and have no `run_consumers_in_api`; customer is the template | `services/deal/app/consumers.py:15-29`; `services/deal/app/main.py:1-19`; `services/lead/app/main.py:1-18`; `services/customer/app/main.py:22-77`, `config.py:23` |
| V10 | The scheduler accepts a target only when its type starts with the envelope's producer plus `.`; job states are `pending`, `fired`, `parked`; there is no cancel; it runs only under the `finance` and `split-workers` profiles | `services/scheduler/app/consumers.py:83-111`; `services/scheduler/app/models.py:38-40`; `infrastructure/edge/compose.prod.yml:1613-1637`; `infrastructure/edge/memory-budget.json` `containers.scheduler` |
| V11 | Notification delivers an in-app message only through a tenant `Rule` row; rules are created through `/notifications/rules`; the feed is addressed by membership id and `NotificationOut` has no entity reference | `services/notification/app/services.py:608-679`; `services/notification/app/api/routes.py:291-312`; `services/notification/app/inbox.py:42-52`; `services/notification/app/domain.py:310-325` |
| V12 | Round-robin already filters to active owners and fails closed when the directory is unreachable | `services/lead/app/services.py:274-336, 409-417` |
| V13 | `create_lead` refuses a repeated email with 409, hashes the exact string, and calls customer over HTTP (cached 60 s) to validate owners | `services/lead/app/services.py:423-546, 119-139, 210-215` |
| V14 | Public paths are exact-match frozensets in the gateway service; the public branch skips the entitlement gate; anonymous traffic is limited to 60 per minute per address; webhooks are listed with methods and their own buckets | `services/app-gateway/app/main.py:36-82, 150-157, 170`; `services/app-gateway/app/config.py:32-39` |
| V15 | Lead already holds Redis and a Lua budget used for reveals, so a per-endpoint budget needs no new infrastructure | `services/lead/app/main.py:17`; `services/lead/app/services.py:693-701, 738` |
| V16 | The 360's deal leg calls `{deal_internal_url}/deals` without `/api/v1` (R1.2a fixes it); its open-stage tuple and "hot" rule are literals; its timeline is five derived kinds; sections orders, finance and support are always null | `services/customer/app/c1_360.py:65-73, 95-97, 157-179, 238-260`; `services/customer/app/c1_schemas.py:17, 99-113` |
| V17 | `ScopeContext.apply` and `assert_can_touch` read `principal.subject_ref`; TEAM raises 403 when no `team_col` is passed; every CRM selector passes `owner_col` only | `libs/platform-core/src/platform_core/auth.py:297-301, 321-376`; `customer/app/selectors.py:152, 262`; `deal/app/selectors.py:50, 122`; `lead/app/selectors.py:39` |
| V18 | `core.membership.created` carries `email` and `full_name`; `.updated` carries `changed[]`; `.revoked` carries `user_id`; none has a committed schema | `services/account/app/services.py:666, 884, 958` (the three `event_type=` lines); `ls libs/contracts/events` (no `core.membership.*`) |
| V19 | `account.organizations` carries `timezone`, `currency`, `locale`; every `MEMBER` holds `core.org.organization.read:ORG`, so any member can read the profile | `services/account/app/models.py:83`; `libs/contracts/permissions/core.json` `MEMBER`; `services/account/app/api/routes.py:105-110` |
| V20 | `product-crm` routes: ten authenticated screens, `Login`, and five `ComingSoon` usages (`/reports`, `/integrations`, `/settings`, `/help`, `*`); nav has 11 entries, 4 dead. **At HEAD only: SP1/R1.2b removes the four placeholder routes, their nav entries and `ComingSoon.tsx` and adds `routes/NotFound.tsx` (SP1 DD-5, class 1 row 8), so this sub-project starts from seven nav entries and one 404 page** | `frontend/product-crm/src/main.tsx:31-124` (`:69-119`); `frontend/product-crm/src/ui/useNavGroups.ts:20-65` |
| V21 | Silent caps: directory 200, board 200, company detail 100, `listActivities` 200; names render "Unassigned" until three 200-row calls resolve | `frontend/product-crm/src/data/DirectoryProvider.tsx:11, 28-33`; `routes/Deals.tsx:45`; `routes/CompanyDetail.tsx:77-78`; `api/client.ts:92` |
| V22 | The Kanban drag is pointer-only; the keyboard and touch path is a native `Select` on every card; a move calls `reload()`, which unmounts the board | `frontend/product-crm/src/ui/KanbanBoard.tsx:86-92, 128-154`; `frontend/opsgrids-client/src/useAsync.ts:10-15, 57-78` |
| V23 | The better Kanban is a fork in `product-projects` (row actions "Move to …" and a polite live region); a third fork is in `product-pulseboard` | `frontend/product-projects/src/ui/KanbanBoard.tsx:57-78, 235-237`; `frontend/product-pulseboard/src/ui/KanbanBoard.tsx` |
| V24 | `Tabs` supports `segmented`, `activation="manual"` and a wired tab panel; `DataTable` supports `onRowClick`, `sort`/`onSort`, `selection`, `bulkActions`, `loading`, `errorState`, `stickyHeader`, `mobileCard`; `FilterBar` supports search, selects, chips, Clear all and a phone bottom sheet | `frontend/opsgrids-ui/src/components/Tabs.tsx:1-100`; `DataTable.tsx:48-105`; `FilterBar.tsx:92-205` |
| V25 | `AppShell` already accepts `quickActions`, `bottomNavExtra`, `helpHref`, `banner`, `layout`; the command menu is navigation only and has no async result slot | `frontend/opsgrids-ui/src/components/AppShell.tsx:55-108`; `CommandMenu.tsx:41-49` |
| V26 | `useGlobalSearch` has CRM sources (companies, contacts, deals; no leads), is exported and has no caller; its contact result links to `/contacts?q=` | `frontend/opsgrids-client/src/platform/useGlobalSearch.ts:184-217`; `frontend/opsgrids-client/src/index.ts:114, 121` |
| V27 | The reveal pair is CRM-local with about 65 tests; `useRevealedField` imports `USE_FIXTURES`; HRMS has a weaker parallel reveal | `frontend/product-crm/src/reveal/RevealableField.tsx:39-60`; `useRevealedField.ts:36-78`; `frontend/product-hrms/src/routes/EmployeeDetail.tsx:230-300` |
| V28 | The Customer 360 panel is 528 lines inside Engage and links to the contacts **list** because no `/contacts/:id` exists | `frontend/product-engage/src/routes/inbox/Customer360Panel.tsx`; `frontend/product-engage/src/lib/dashboard.ts:24-35` |
| V29 | Token baseline **at HEAD**: 101 offenders in 15 `product-crm` files (R1.2b deletes `ComingSoon.tsx`, which takes it to 97 in 14, so 97 is the baseline R3.4 works from; Login 21, KanbanBoard 12, Deals 12, DealDetail 12, CompanyDetail 8, ActivityTimeline 6, Activities 5, Overview 4, LeadDetail 4, ComingSoon 4, Companies 3, Contacts 3, NeedsAttention 3, Team 2, Leads 2); a11y baseline holds 10 CRM renderings (`deals-list` 6, `new-deal-dialog` 4); `overview` is clean | `frontend/opsgrids-config/token-baseline.json`; `frontend/opsgrids-visual/a11y-baseline.json` |
| V30 | The visual harness renders three CRM pages from route mocks validated against OpenAPI; an unmocked call fails the spec | `frontend/opsgrids-visual/lib/pages.ts:105-135`; `fixtures/api/routes.ts:46-125`; `fixtures/contracts.test.ts` |
| V31 | Operations is the lazy-route pattern; Engage is the route-registry and permission-denied pattern; ITSM is the list pattern (row click, card, selection); Support passes a server `sort` | `frontend/product-operations/src/main.tsx:115-121`, `routes/RouteError.tsx`; `frontend/product-engage/src/routes/appRoutes.ts:10-29`, `shellRoutes.tsx:40-60`, `ui/PermissionDenied.tsx`; `frontend/product-itsm/src/routes/Incidents.tsx:71-97, 333-336`; `frontend/product-support/src/routes/Queue.tsx:53-59` |
| V32 | Memory **at HEAD**: ten sellable profiles 3747 MB, plus `admin` 3944 MB, core 1772 MB, core plus `crm` 2067 MB (computed with `infrastructure/edge/lib/memory_budget_totals.py` over `memory-budget.json`); `kinds.api` 98, `api_with_worker` 99, `background` 90. Every consumer fold moves a container from 98 to 99: REG F8's progression for the ten profiles and `admin` is 3748 and 3945 after R2.4's deal fold, 3749 and 3946 after R3.8's lead fold; R3.6 and R3.8 both need R2.4, so their baselines are the post-R2.4 figures (§4.5) | `infrastructure/edge/memory-budget.json`; the script's CLI |
| V33 | The edge serves every product SPA same-origin and has no per-tenant CORS; only the staff console sets frame protection | `infrastructure/edge/caddy/Caddyfile:75-91, 236-267, 322-345` |

## 4. Design that every release shares

### 4.1 Conventions stated once

**Tables.** Every new table below carries the `TenantBase` columns unless its row says "composite
key": `id UUID PRIMARY KEY`, `organization_id UUID NOT NULL` (with the model's own
`ix_<table>_organization_id`), `created_at` and `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`,
`created_by` and `updated_by VARCHAR(64) NULL`, `deleted_at TIMESTAMPTZ NULL`
(`libs/platform-core/src/platform_core/models.py:40-93`). In the **same** migration: `ENABLE` and
`FORCE ROW LEVEL SECURITY`, and the policy `tenant_isolation` `FOR ALL TO <app role>` with `USING`
and `WITH CHECK` on `<schema>.current_org()` (the function every schema already has: deal
`e94d7a2c5f18_enable_row_level_security.py:41-86`). The table joins the service's `TENANT_TABLES`
(`services/<svc>/tests/conftest.py`; deal's is `:45-51`). No `GRANT` is written: `apply_schema_grants`
(`libs/platform-core/src/platform_core/db.py:56`) runs from each service's `migrations/env.py` after
every migration and gives the app role DML. Every index leads with `organization_id`. No foreign key
leaves the schema; a link to another schema is a plain indexed UUID. Every query carries
`Model.organization_id == ctx.organization_id` as well as RLS (the repo's root rules). Each migration starts with
`SET LOCAL lock_timeout = '5s'`, is expand-only, has one revision per service per release, and states
its downgrade; a downgrade that would lose rows raises `RuntimeError` unless
`OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE=1` (the repo's precedent is `b7d41e92c05a`, quoted in the
warehouse spec §3). Data steps inside a migration are set-based, idempotent, need no PII key and log
counts only; the migration role bypasses RLS (`services/deal/migrations/env.py`, "Migrations run as
the admin role").

**Routes.** Public routes are mounted by `create_app` under `/api/v1`
(`libs/platform-core/src/platform_core/app.py:362-363`); paths below omit the prefix. Static paths are
declared before `/{id}` paths; each route uses `Depends(get_tenant_session, scope="function")`,
declares `require(code, minimum=...)` and `Entitled`, applies `ctx.apply(...)` on lists and
`ctx.assert_can_touch(...)` on details, and answers a foreign or missing id with the same 404. After
R2.2 the caller's own id is `ctx.own_ref` (A6); no SP3 code reads `ctx.subject_ref`. `owner=me` is
always resolved on the server.

**Events.** Payloads carry ids and enumerations only: never an email, phone, name or free text. Each
event's committed schema (`libs/contracts/events/<type>/v1.schema.json`, `$id
opsgrids://contracts/<type>/v1`, `additionalProperties: false`) lands in the **same commit** as its
producer, with a producer-side test that builds the payload through the real write path and calls
`platform_core.events.validate_event_schema` (`events.py:203-224`; the pattern is
`libs/contracts/tests/test_event_schemas.py`). A consumer deploys before its producer's behaviour
changes, because an event with no bound queue is dropped while its outbox row is marked published
(`events.py:250-262`). Every handler runs `already_processed` / `mark_processed` in the same
transaction as its effect, binds the tenant per message with `session_scope(organization_id=...)`, and
refuses an envelope whose `organization_id` differs from the payload's.

**Negative tests.** Besides the named test on each route below, every service gains
`services/<svc>/tests/test_authz_matrix.py` (customer and lead in R3.1, deal in R3.1 as well): a table
`EXPECTED[(method, path)] = (permission, minimum_scope)`, one test that walks `app.routes` and fails
when a public route is missing from the table, and parametrised tests that, for every row, call the
route with no token (401), with a token lacking the permission (403), below the floor (403, rows that
have one), without the `crm` entitlement (402), and with another organization's id (404). A route
added in any later release therefore fails CI until it has a row. The checks are behavioural: no
change to `platform_core.auth.require` is needed.

**Sealed values.** A sealed value (phone, lead email) is never returned, logged, put in an event, an
audit `after`, a URL or a query string. Identifier lookups are `POST` bodies. Reveal keeps
`require(code, amr={"otp", "stepup"})`, the shared Redis budget and the audit row.

### 4.2 Release prerequisites

"Needs" is hard. The programme's table (P§10) lists only some of these; the additions are recorded as
deviations in §17. Every SP2 release named below is itself blocked by owner decisions (§2.1: D-1, D-2, D-3, D-11 and
D-13 block R2.4; D-8 and D-10 block R2.6; D-9 blocks R2.7; D-21 and D-38 block R2.8; D-5 and D-6 the stage
releases), so those decisions are **transitive prerequisites** of every SP3 release that needs the SP2
release, and §2.1 names the first SP3 release each one blocks.

| Release | Needs | Why |
|---|---|---|
| R3.1 | R1.2a (C77 deployed and soaked) for the enforcement half; R2.3 only for the deal-side use of `closed_at` and outcomes (no DDL depends on it) | J13: enforcing C45 and C46 before notification sends the `org` claim would 403 every Engage address lookup |
| R3.2 | R2.2 (`own_ref`), R2.3 (C9's outcome-keyed rollups and the effective stage list), R1.2a (the 360 path fix), R3.1 (fields the filters and sorts name) | `owner=me` is `ctx.own_ref`; the summary rewrite and the `stage` sort read R2.3's stage list; `q` matches `legal_name`; sort keys name `number` |
| R3.3 | R1.1 | No behaviour change; independent of the backend |
| R3.4 | R1.2b (the placeholder pages, the 404 page and the dead rollups are removed there, §8.1), R2.5 (stage API, `crm.pipeline.stage.write`), R2.7 (membership-keyed OWN), R2.8 (catalogue of E19-E22), R3.2, R3.3 | Stage screens, rep views, settings routes, generated types, shared components; the ledgers of §8.6.5 start from the post-R1.2b tree |
| R3.5 | R3.4, R2.3 (`closed_at`, outcomes), R3.1 (close reasons), R3.2 (`/contacts/{id}/360`) | Detail pages stand on the rebuilt shell and the new fields |
| R3.6 | R2.4 (the deal consumer fold, its queue and DLQ entry), R2.7, R2.8, R3.4 (`deal.pipeline_settings`) | A consumer, an owner id a membership can match, the follow-up codes, the settings row |
| R3.7 | R2.8 (E22), R3.1 (UTM columns, C48, and the H8 run for customer's `email_index`), R3.4 (the settings hub and the Assignment page). **R3.7b also needs**: F12 set on the box with its `.env.example`, matrix, runbook and strength-check deliverables (§11.3.4); the ADR `crm-public-lead-capture` (REG I2) accepted; R3.7a's lead erase live (§11.3.3); and the owner's D-27 answer for leads | The SLA card and the Sources page live in the hub; the duplicate lookups need the contact index; public capture needs the secret. R3.7b is the first release that stores personal data of people who never signed in. P§7 designs erasure before a PII-adjacent *fan-out* and this is not one, but the same care applies, so the erase ships a release earlier and the data-subject treatment is decided before capture can be switched on |
| R3.8 | R2.4, R2.6 (C57, which H10 reads; R3.8's own Deploy 2 widens C57's caller allow-list to `deal` and `lead`, §12.3.4), R2.7, R2.8, R3.6 (follow-ups carry `owner_manager_id`), R3.7a (lead tables), and the order in §12.8 | TEAM needs three consumers, the lead fold, a seeded `owner_team` and an account read the two new callers may make |

### 4.3 Permissions this sub-project uses

SP3 creates **no** permission code: E18-E22 are catalogued by SP2 (E19-E22 in R2.8) and reach live
tenants through `make edge-reconcile PRODUCTS=crm` (REG E.4). Every route is added only after its code
exists.

| Route family | Read code | Write code | Floor and notes |
|---|---|---|---|
| Company tax ids and addresses (C22) | `crm.book.company.read` | `crm.book.company.write` | none; `assert_can_touch(owner_id=company.owner_id)` |
| Contact create and update (C33), `contacts:search` (C86) | `crm.book.contact.read` | `crm.book.contact.write` | none |
| Lists, summaries, `ids` (C35) | the entity's `read` | n/a | `ctx.apply` |
| `/deals/settings`, `/deals/close-reasons` (C11, C12) | `crm.pipeline.deal.read` | `crm.pipeline.settings.write` (E19) | `minimum=Scope.ORG` on writes |
| Follow-ups (C13, C14) | `crm.pipeline.follow_up.read` (E20) | `crm.pipeline.follow_up.write` (E21) | none; handing a follow-up to another rep needs scope above OWN |
| Lead reassign (C37), duplicates (C38) | `crm.lead.lead.read` | `crm.lead.lead.write` | reassign `minimum=Scope.TEAM`; duplicates: no floor, because the lead half is cut by `ctx.apply` (an OWN caller sees only their own leads) and the **contact half needs `crm.book.contact.read` at ORG** or is skipped and says so (§11.3.1) |
| Capture endpoints and lead settings (C39, C40) | `crm.lead.lead.read` | `crm.lead.settings.write` (E22) | `minimum=Scope.ORG` on writes |
| Lead erase (§11.3.3, DD-47) | n/a | `crm.lead.settings.write` (E22; no new code) | `minimum=Scope.ORG` and `amr={"otp","stepup"}`; admin templates only |
| Visitor messages on a lead (`GET /leads/{lead_id}/capture-messages`) | `crm.lead.lead.read` | n/a | `assert_can_touch(owner_id=lead.owner_id)` |
| Assignment rule (existing) | `crm.lead.lead.read` | `crm.lead.lead.write` | service-level ORG check (`lead/app/services.py:181-182`) |
| Saved views (C16, C32, C43) | the entity's `read` | the entity's `read` (own views) | `visibility: org` needs an ORG-scope read grant |
| History (C15, C32, C43), reports (C10) | the entity's `read` | n/a | none |
| Public capture (C41), signed webhook (C42) | none (no principal) | n/a | token MAC, entitlement check (L12) |
| Internal capture (C56) | caller allow-list `notification`, `storefront` | n/a | org claim |
| Account memberships read (C57, internal) | caller allow-list `customer`, `deal`, `lead` (SP2 ships `customer` only; R3.8's Deploy 2 adds the other two, §12.3.4) | n/a | org claim |

### 4.4 UI design foundation (R3.3 through R3.8)

Everything in this section is the target the release sections build toward; each release names the
parts it delivers.

#### 4.4.1 Information architecture

Navigation tree, filtered by the caller's grants (an entry the caller cannot read is absent, not
disabled; the server stays the enforcement point, the frontend rules, "Data and auth"). `/companies` is the
canonical noun pending D-22, and the Settings entries rest on D-5, D-6 and D-7 (§8.6.3):

```
Sales        Overview /               any CRM read code
             Leads /leads             crm.lead.lead.read
             Deals /deals             crm.pipeline.deal.read
             Follow-ups /follow-ups   crm.pipeline.follow_up.read        (from R3.6)
Records      Companies /companies     crm.book.company.read
             Contacts /contacts       crm.book.contact.read
             Activities /activities   crm.pipeline.activity.read
Insights     Reports /reports         crm.pipeline.deal.read             (from R3.8)
pinned       Settings /settings       any settings or roster code (the hub lists cards by grant)
```

The sidebar's four dead entries are gone (V20; SP1/R1.2b removes them, so R3.4 starts from seven). The Deals count badge is removed: it was an extra
request that counted closed deals and never refreshed (`useNavGroups.ts:32-33`). The "Team Members"
entry moves under Settings as "Sales team". `bottomNavExtra` is Leads and Deals; `quickActions`
(command menu and the phone "+" sheet) are New lead, New deal, New contact, Log activity and, from
R3.6, New follow-up, each shown only when the caller can write. `helpHref` is omitted: no help site
exists in the repo, and an entry that goes nowhere is a dead end.

Route registry (`frontend/product-crm/src/routes/appRoutes.ts`, the Engage pattern
`frontend/product-engage/src/routes/appRoutes.ts:10-29`): each area folder exports
`routes: AppRoute[]` with `{ path, element, title, permission?, nav? }`; the router, the sidebar, the
document title and the permission gate are all derived from the one list, so a screen cannot exist
without a title, a gate and a nav decision. Every non-landing route is `lazy()` inside
`Suspense fallback={<RouteFallback />}` with `errorElement={<RouteError />}`
(`frontend/product-operations/src/main.tsx:115-121`).

| Path | Screen | Gate (route level) | Release |
|---|---|---|---|
| `/` | Overview | any CRM read code | R3.4 shell, R3.6 rebuild |
| `/leads`, `/leads/:id` | Leads, Lead | `crm.lead.lead.read` | R3.4, R3.7 (detail) |
| `/deals`, `/deals/:id` | Deals (board or list), Deal | `crm.pipeline.deal.read` | R3.4, R3.5 |
| `/follow-ups` | Follow-ups | `crm.pipeline.follow_up.read` | R3.6 |
| `/companies`, `/companies/:id` | Companies, Company | `crm.book.company.read` | R3.4, R3.5 |
| `/contacts`, `/contacts/:id` | Contacts, Contact | `crm.book.contact.read` | R3.4, R3.5 |
| `/activities` | Activity log | `crm.pipeline.activity.read` | R3.4 |
| `/reports` | Reports | `crm.pipeline.deal.read` | R3.8 |
| `/settings` | Settings hub | any settings or roster code | R3.4 |
| `/settings/pipeline` | Pipeline stages | view `crm.pipeline.deal.read`; edit `crm.pipeline.stage.write` | R3.4 |
| `/settings/reasons` | Win and loss reasons | view `crm.pipeline.deal.read`; edit `crm.pipeline.settings.write` | R3.4 |
| `/settings/workspace` | Workspace (timezone, thresholds, number prefix, notifications) | view `crm.pipeline.deal.read`; edit `crm.pipeline.settings.write` | R3.4 (notifications R3.6) |
| `/settings/assignment` | Lead assignment | view `crm.lead.lead.read`; edit `crm.lead.lead.write` at ORG | R3.4 |
| `/settings/sources` | Lead sources and forms | view `crm.lead.lead.read`; edit `crm.lead.settings.write` | R3.7b |
| `/settings/team` | Sales team | view `crm.book.company.read`; edit `crm.book.owner.write` | R3.4 |
| `/team` | redirect to `/settings/team` | none | R3.4 |
| `/login` | `RedirectToCentralLogin` in production, dev sign-in otherwise | none | R3.4 |
| `*` | Not found | none | created by R1.2b, restyled in R3.4 |

`/integrations` and `/help` are not redirected: they were placeholders that R1.2b removed, so they answer
the Not found screen with a link to Overview.

#### 4.4.2 State model (every screen, every list, every form)

One state vocabulary, so no screen is "half a screen" (the frontend rules, "States"). Components are
the shared ones; copy is sentence case and says what happened and what to do next.

| State | When | Rendered by | Rules |
|---|---|---|---|
| Loading | first fetch, or filters changed | `DataTable loading` skeleton rows; detail pages a skeleton with the page heading present; a layout-reserving skeleton (`RouteFallback`) for lazy chunks | Same footprint as the loaded state (no layout shift). Refresh after a write uses `useAsync.refresh()` so rows stay on screen; `reload()` is used only when the identity of the data changed |
| Empty, nothing exists | list with no filter and zero rows | `EmptyState` with the next action as buttons (create, import, set up a source) | Buttons follow the caller's grants |
| Empty, filtered | zero rows with any filter or search | `EmptyState` "No {noun} match these filters" plus **Clear all** | The `FilterBar` stays mounted above the table (`DataTable toolbar` is kept on empty and failed bodies) |
| Error | a read failed | `LoadFailure` (CRM-local, wraps `ErrorState`) mapping status to title, tone, retry and `reference` (the problem's `request_id`) | 401: sign-in handled by `RequireAuth`; 402: "Revenue isn't part of your plan", no retry; 403: neutral tone "You don't have access to this", no retry; 404: "That {noun} isn't available" with a Back action, no retry; 422: shows the field message, no retry; 429 and 5xx and network: Try again. A 4xx is never retried (the frontend rules) |
| Forbidden | the caller lacks the route's read code | `PermissionDenied` page (the Engage component, promoted to CRM-local `ui/PermissionDenied.tsx` with the same copy rules), rendered by the route gate `RequireRead` | A page, not an error; names the screen; the route is reachable by URL either way and the endpoint refuses on its own (`PermissionDenied.tsx` docstring) |
| Forbidden control | the caller can read but not act | the control is absent (`Can`, `useCan(code, scope)` with the same `minimum` as the route, REG E.4 step 5) | Never a disabled button that 403s. A **conditionally unavailable** action (closed deal, last stage) is a `RowActions` item with `disabled` and `disabledReason` |
| Dirty form | a form with edits | `FormDialog` + `useFormDirty` + `UnsavedChangesGuard` (all seven existing dialogs already do this) | Closing, Escape and navigation ask; server field errors map onto the control (`ApiError.errors[].field`); a failure without a field shows `InlineAlert` with the reference |
| Conflict | a write answers 409 | the dialog stays open; duplicates show "Open the existing {noun}" using the extra key `existing_id` on the `errors[]` entry (A21; the client already ignores extra keys, `frontend/opsgrids-client/src/problem.ts:1-20`, so a CRM-local `existingIdOf(problem)` reads it) | A write that changed nothing is not an error (R3.2 skips audit and event on a no-op PATCH) |
| Partial | a section of the 360 is `null` and named in `unavailable` | "Not available" inside the section (`Customer360View`) | Never a page error |

#### 4.4.3 URL and filter state

The URL is the single source of list state, so a view is shareable, the back button works and a
reload restores it. A CRM-local hook `useListQuery(schema)` (`frontend/product-crm/src/data/useListQuery.ts`)
parses `useSearchParams` against a per-list schema and returns `{ params, set, clearAll }`.

| Key | Lists | Values |
|---|---|---|
| `q` | all | free text, at most 100 characters; names and companies only, never an email or phone (§4.4.6) |
| `status`, `stage`, `outcome` | per list | the server's enums; an unknown value is dropped on read and normalised away with `replace` |
| `owner` | all | `me`, `team` (R3.8), or a rep id; leads also `unassigned` (R3.7) |
| `source`, `tag` (repeatable) | leads and deals; all (R3.8) | channel enum, free-text deal source, tag strings |
| `created_from`, `created_to`, `close_from`, `close_to` | per C35 | `YYYY-MM-DD` |
| `attention`, `sla`, `aging` | deals (R3.6); leads (R3.7) | the server's enums |
| `sort` | all | one key, `-` for descending, from the server's per-list list; invalid dropped |
| `page`, `size` | all | `size` one of 25, 50, 100 |
| `layout` | deals | `board` (default) or `list` |
| `view` | all (R3.8) | a saved view id; selecting one sets every other key from it |
| `tab` | detail pages | `overview` (default), `timeline`, `deals`, `contacts`, `follow-ups`, `history`, ... |

Rules: (1) defaults are never written, so URLs stay short; (2) typing in search writes with
`replace`, every discrete change (filter, sort, page, size, layout, tab) writes with `push`; (3) any
change of a filter, sort or size resets `page`; (4) the table and the Excel export receive the same
parsed object, so "export" is always the filtered list (ADR-0022); (5) identifiers (email, phone),
`ids` and ids of selected rows are never in the URL; (6) `tz` is not in the URL, it is added to the
API call from the organization's settings (DD-7).

#### 4.4.4 Responsive, keyboard and accessibility rules

Each rule is a test target (§4.4.8). They extend the frontend rules; none relaxes them.

| # | Rule |
|---|---|
| U1 | Every screen has one `<h1>` (`PageHeader`) present in loading, empty, error and forbidden states, and sets `document.title` to `{title} · OpsGrids Revenue` from the route registry |
| U2 | Below 768px (`TABLET_QUERY`) every list is cards (`DataTable mobileCard="auto"` with each column's `mobile` placement); no sideways-scrolling table. Filters collapse into the `FilterBar` bottom sheet |
| U3 | Rows open their record by click and by Enter or Space (`onRowClick`); the first cell is also a real link, so middle-click and screen readers work |
| U4 | Row and card actions are `RowActions` (real buttons, 44 px targets, an accessible name that includes the record's name); destructive items use `tone="danger"` and open `ConfirmDialog` |
| U5 | Sorting is by header (`aria-sort` through `DataTable sort/onSort`) and mirrored by the `sort` URL key; the actions column header has a visually hidden name (the `empty-table-header` baseline entries) |
| U6 | Tabs use `Tabs` with `activation="manual"` for any panel that fetches, with a wired `tabpanel` (the current Board and List switch has none: the `button-name` baseline entries) |
| U7 | Anything that changes without a click is said once in the screen's `Announcer` (`useAnnounce`): a deal moved, a follow-up completed, rows appended, a filter changed the count ("12 deals") |
| U8 | After a create focus moves to the new row's link or the page heading; after a delete to the list heading; a closed dialog returns focus to its opener; a move on the board keeps focus on the moved card |
| U9 | A revealed value lives only in the hook's state, hides after `REVEAL_VISIBLE_SECONDS`, on Hide, on a record change and on unmount, and is announced in the field's live region (the existing contract, kept) |
| U10 | State is never colour alone: stage, status, SLA and attention are `StatusBadge` words; the board's drop target has a text label |
| U11 | Motion respects `prefers-reduced-motion`; no animation conveys state |
| U12 | Money is shown with `formatMoney(minor, currency)` and never summed across currencies; a multi-currency total is a list of per-currency lines |
| U13 | Dates and times render in the organization's timezone with the zone abbreviation on hover or in a `<time datetime>`; relative text ("2 days ago") always has the absolute instant available |
| U14 | A 360-section failure, a tab failure and a widget failure are local: an error boundary and retry inside the panel; the rest of the page stays up |

#### 4.4.5 Component inventory

"Home" is where the code lives after the programme; the rightmost column is the sibling pattern to
copy, with its file.

| Component or hook | Home | Delivered | Pattern to copy |
|---|---|---|---|
| `KanbanBoard` (G1) | `@opsgrids/ui` | R3.3 | `frontend/product-projects/src/ui/KanbanBoard.tsx:57-78` (row-actions "Move to …"), `:235-237` (live region) |
| `DetailList`, `PropertyRow` (G2) | `@opsgrids/ui` | R3.3 | `frontend/product-itsm/src/routes/IncidentDetail.tsx:105` (`PropertyRow`), `:446-476` (use) |
| `Customer360View`, `Customer360ViewModel` (G5) | `@opsgrids/ui` | R3.3 | `frontend/product-engage/src/routes/inbox/Customer360Panel.tsx` (`SectionRow`, `NotAvailable`, `Profile`, `LoadError`) |
| `RevealableField` (G6), `REVEAL_VISIBLE_SECONDS` | `@opsgrids/ui` | R3.3 | `frontend/product-crm/src/reveal/RevealableField.tsx` |
| `SavedViewMenu` (G8) | `@opsgrids/ui` | R3.3 (data R3.8) | `Menu`, `Drawer` for the phone sheet; `FilterBar`'s chips |
| `Timeline` gains `order: "asc" \| "desc"` and a `footer` (G20) | `@opsgrids/ui` | R3.3 | `frontend/opsgrids-ui/src/components/Timeline.tsx` (oldest-first today) |
| Command menu result slot (G12) | `@opsgrids/ui` `AppShell` and `CommandMenu` | R3.3 | `CommandMenuConfig`, `AppShell.tsx:55-71` |
| `formatMoney`, `toMajor`, `parseMinor`, `minorToInput` (G4) | `@opsgrids/ui` `lib/format.ts` | R3.3 | `frontend/opsgrids-internal/src/lib/money.ts:39` |
| `useRevealedField` (G7), `useSavedViews` (G8), `useCustomer360` (G10), `useOrgProfile` (G19), CRM sources of `useGlobalSearch` (G12) | `@opsgrids/client` | R3.3 (G10 R3.5) | `frontend/product-crm/src/reveal/useRevealedField.ts`; `frontend/opsgrids-client/src/platform/useGlobalSearch.ts:184-217`; HRMS `lib/zone.ts:2` for the org profile |
| `appRoutes.ts`, `RouteError`, `RouteFallback`; `NotFound` restyled | `product-crm` | R3.4 (`routes/NotFound.tsx` is **created by SP1/R1.2b**, DD-5; R3.4 restyles it with an `EmptyState` and creates no second component) | `frontend/product-engage/src/routes/appRoutes.ts`; `frontend/product-operations/src/routes/RouteError.tsx`, `RouteFallback.tsx` |
| `RequireRead`, `PermissionDenied` | `product-crm/src/auth`, `ui` | R3.4 | `frontend/product-engage/src/auth/RequireGrant.tsx`, `ui/PermissionDenied.tsx` |
| `LoadFailure` | `product-crm/src/ui` | R3.4 | `ErrorState` props `tone`, `reference`, `onRetry`, `action` (`States.tsx:46-96`); Engage `LoadError` (`Customer360Panel.tsx`) |
| `useListQuery`, `useEntityNames` (id to name, batched `ids=`), `useOrgSettings` | `product-crm/src/data` | R3.4 | `frontend/product-itsm/src/routes/Incidents.tsx:71-97` (URL-seeded state, here written back); `frontend/product-support/src/routes/Queue.tsx:53-59` (server sort) |
| List screens | `product-crm/src/routes/<area>` | R3.4 | ITSM `Incidents.tsx:333-336` (`onRowClick`, `mobileCard="auto"`, `selection`, `bulkActions`) |
| Detail screens with `Tabs`, `?tab=` | `product-crm/src/routes/<area>` | R3.5 | `frontend/product-operations/src/routes/InventoryItemDetail.tsx:102-105, 354` (URL tab, `activation="manual"`); `SupplierDetail.tsx:37-55` (stat tiles) |
| `StageBadge`, `StageTrack`, `OwnerBadge`, `DealCard`, `RecordPage`, `RecordTimeline`, `HistoryList`, `InlineEditField`, `CloseDealDialog`, `CloseReasonField`, `FollowUpDialog`, `CompleteFollowUpDialog`, `ReassignDialog`, `EndpointDialog`, `SecretOnceDialog`, `EmbedSnippet`, `WebhookHelp`, `TagInput` | `product-crm/src/ui` | R3.4-R3.8 | `StatusBadge tone label` (tones come from the API, `StatusTone` in `lib/status.ts:19`); ITSM worklog composer `IncidentDetail.tsx:252-319` |
| Create and edit dialogs | `product-crm/src/forms` | kept | `FormDialog`, `useFormDirty`, field-error mapping: all seven stay (the forms are the best-built part of the app, V3 of the discovery audit) |
| Import and export | `product-crm/src/data/importExport` | kept | ADR-0022; each `*Filters` gains the new filter keys and `ids` |

A shared variant is added to `@opsgrids/ui` (never forked into `product-crm`) wherever a second
product could need it; CRM-local components above are compositions of shared ones.

#### 4.4.6 Search and identifiers

The command menu searches **names and company names only**: `useGlobalSearch` gains an identifier
guard. A query containing `@`, or made of at least six digits, spaces, `+`, `(`, `)`, `-`, or `.`,
runs no GET source (an address in a `?q=` URL is kept by gateway and proxy logs, and a reloaded SPA
URL by Caddy). The palette then shows one action, "Look up this contact", which opens the lookup
dialog on the Contacts screen, where `POST /contacts:search` (C86) and `POST /leads:check-duplicates`
(C38) carry the identifier in a body and the result is held in memory, not in the URL.

#### 4.4.7 Token and accessibility baseline debt

New code is held to zero token offenders (the frontend rules); `pnpm tokens:baseline` is run after
each release and the baseline file only shrinks (`frontend/opsgrids-config/token-baseline.json`).

| File (`frontend/product-crm/src/…`) | Offenders | Fate | Release |
|---|---|---|---|
| `routes/Login.tsx` | 21 | replaced by the dev-only sign-in (DD-13) | R3.4 |
| `ui/KanbanBoard.tsx` | 12 | deleted; the shared board replaces it | R3.4 |
| `routes/Deals.tsx` | 12 | rebuilt | R3.4 |
| `routes/DealDetail.tsx` | 12 | rebuilt | R3.5 |
| `routes/CompanyDetail.tsx` | 8 | rebuilt | R3.5 |
| `ui/ActivityTimeline.tsx` | 6 | replaced by `RecordTimeline` over `Timeline order="desc"` | R3.5 |
| `routes/Activities.tsx` | 5 | rebuilt | R3.4 |
| `routes/Overview.tsx` | 4 | touched in R3.4 (server data, tokens clean), rebuilt | R3.4, R3.6 |
| `routes/LeadDetail.tsx` | 4 | rebuilt | R3.7 |
| `routes/ComingSoon.tsx` | 4 | deleted by SP1/R1.2b before this sub-project starts; not in the total below | R1.2b |
| `routes/Companies.tsx`, `routes/Contacts.tsx` | 3 + 3 | rebuilt | R3.4 |
| `ui/NeedsAttention.tsx` | 3 | deleted (an overdue list from a server filter replaces it; R3.6 adds stale) | R3.4 |
| `routes/Team.tsx`, `routes/Leads.tsx` | 2 + 2 | rebuilt | R3.4 |
| **Total** | **97 when R3.4 starts** (101 at HEAD, 15 files; 14 files without `ComingSoon.tsx`) | **0 at the end of R3.7** | |

Accessibility baseline (`frontend/opsgrids-visual/a11y-baseline.json`): the ten CRM entries
(`page/crm/deals-list` ×6: `empty-table-header` everywhere, `button-name` ×2 at 375, `color-contrast`
in dark at 768 and 1280; `page/crm/new-deal-dialog` ×4) disappear with the rebuilt Deals screen
(U5, U6 name the causes). New pages start with **no** baseline entries: any violation fails the
visual gate. The two Deals debts are fixed as part of the R3.4 rebuild, so the ratchet drops.

#### 4.4.8 Visual test fixtures and test strategy

The harness renders each surface at 375, 768 and 1280 px in light and dark (six renderings), with
`@axe-core/playwright`, from route mocks that are validated against `libs/contracts/openapi/*.json`
(`fixtures/contracts.test.ts`); an unmocked call fails the spec (V30). Every new `GET` therefore
needs a `MockRoute` in `frontend/opsgrids-visual/fixtures/api/routes.ts`, a fixture file under
`fixtures/api/crm/`, and every field a response gains must be in the fixture, or the contract test
fails. Each release adds its surfaces (counted in its section). Surfaces for states are produced by
alternate route sets in `lib/pages.ts` (an empty list, a `500`, a `403` on the page's read call).

| Release | Surfaces added to `lib/pages.ts` (`page/crm/…`) |
|---|---|
| R3.4 | `overview` (kept, fixtures updated), `leads-list`, `leads-empty`, `deals-board`, `deals-list` (replaced), `new-deal-dialog` (kept), `companies-list`, `contacts-list`, `activities`, `settings-hub`, `settings-pipeline`, `settings-reasons`, `settings-workspace`, `settings-assignment`, `settings-team`, `forbidden`, `load-error`, `not-found` (eighteen) |
| R3.5 | `contact-detail`, `company-detail`, `deal-detail`, `deal-detail-closed`, `deal-close-dialog` (five) |
| R3.6 | `follow-ups`, `follow-ups-empty`, `follow-up-dialog`, `overview` (rebuilt), `settings-workspace` (toggles added) (five) |
| R3.7 | R3.7a: `lead-detail`, `lead-detail-duplicate`, `reassign-dialog`, `erase-lead-dialog`, `leads-list` (updated), `settings-assignment` (SLA card added); R3.7b: `settings-sources`, `settings-sources-empty`, `settings-sources-limit`, `endpoint-dialog`, `endpoint-secret-dialog` |
| R3.8 | `reports-forecast`, `reports-win-loss`, `reports-empty`, `saved-views-menu`, `saved-view-dialog`, `history-tab` (six) |

Unit and component tests live beside the code (`vitest`, `@testing-library`), use **typed builders**
over the generated wire types (`frontend/product-crm/src/test/builders.ts`) and a `stubApi(routes)`
helper over `vi.stubGlobal("fetch", ...)` (the repo's pattern: `reveal/testKit.tsx:73`,
`api/client.test.ts:49`), so a contract change fails typecheck rather than a runtime assertion.

#### 4.4.9 Performance

1. No fixed cap on any display path: the 200-row directory, the 200-card board, the 200-deal
   Overview scan, the 100-row company tabs and the 200-row activity default are replaced by server
   paging, `ids=` resolution and per-column board paging (V21). `collectPages` stays only inside
   import and export.
2. Every non-landing route is a lazy chunk; the entry chunk imports no list, detail, board, chart,
   `exceljs` or import-export module. A dependency-cruiser rule `crm-routes-are-lazy` (in
   `frontend/.dependency-cruiser.cjs`) forbids a static import from `main.tsx` or `App.tsx` to
   `routes/<area>` modules other than Overview, NotFound, RouteError and RouteFallback, and
   `pnpm --filter @opsgrids/product-crm build` is reviewed for chunk count (the operations app
   splits all non-landing routes, V31).
3. First paint of a screen is bounded by a per-screen request budget, session providers included, on a
   cold mount (the budgets are in §8.10 criterion 7 and, for the rebuilt Overview, §10.10): the list itself; the
   session reads it needs (stages, settings, owners; each once per session); one `ids=` name-resolution
   call per entity kind it displays; and, for a board, one call per visible column. A test per screen
   counts `fetch` calls on mount and pins the number, so a new request is a reviewed change.
4. A name resolver batches ids per screen into one `ids=` call per entity (at most 100 ids per call)
   and caches in memory for the session; a screen never renders "Unassigned" for an id it has not
   yet resolved, it renders a skeleton.
5. Board columns load 25 cards each and append; the board refresh after a move uses `refresh()` and
   reconciles the moved deal from the `POST /deals/{id}/stage` response instead of refetching.

### 4.5 Standing acceptance criteria and the memory rule (every release)

Programme §7 asks the same verification of every release, and with GitHub Actions unavailable a local
`make gate` is the only full one (P§3 F11; the comment above the `gate` target in the `Makefile` says a
green gate is the same claim as a green CI run). Each release's own criteria in §5.10 to §12.10 are
**additional** to these; none replaces them, and each of those lists ends by naming the rows that apply to
it. The implementer runs them locally and attaches the output to the pull request; the box-side steps stay in
§13.

| # | Criterion | Applies to |
|---|---|---|
| S1 | `make check` (lint, typecheck, test) passes. Typecheck and tests loop per service because `import app` is ambiguous across services (`make typecheck-services`, `make test-services`); they are never collapsed into one invocation | every release |
| S2 | `make gate` passes: its static rows (`lint hygiene boundaries typecheck test-infra shard-verify openapi-check contracts-check shellcheck hadolint audit licenses secrets frontend-gate`) and its database rows (`test-libs test-services migration-roundtrip migration-check smoke`) | every release |
| S3 | `make contracts-check` passes; every event added or changed has its committed schema and a producer-side test through the real write path (`validate_event_schema`) in the same commit | every release that adds or changes an event type or payload |
| S4 | `make openapi` is committed and `make openapi-check` is clean; `python tools/export_permissions.py --check` passes (and is unchanged where the release adds no permission code) | every release that adds or changes a route or a permission |
| S5 | `make migration-roundtrip` and `make migration-check` pass and `tests/infra/test_migration_revisions.py` shows one head per service; every new table has `tenant_isolation`, forced RLS and a `TENANT_TABLES` entry | every release with a migration |
| S6 | Every public route has its row in `services/<svc>/tests/test_authz_matrix.py` and its named negative-authz tests (§4.1) | every release that adds or aliases a route |
| S7 | `make frontend-gate` (typecheck, lint, boundaries, test, build, audit, licenses) and `make ui-visual` pass, and `pnpm tokens:baseline` leaves `frontend/opsgrids-config/token-baseline.json` equal or smaller. `ui-visual` belongs to `gate-full`, not to `gate`, so a UI release runs it by name | every release that changes `frontend/` |
| S8 | `docs/crm.md` is updated in the same commit for every CRM event, permission, role or gateway prefix the release adds or changes (SP1's `tests/infra/test_crm_doc_matches_contracts.py`), and every new internal client is registered in SP1's `tests/infra/test_internal_client_paths.py` | as the content requires |

**The memory rule.** Memory is stated as a change against the total the same command prints before the
release merges, never as a literal, because every consumer fold is +1 MB (REG F8; the ceiling is D-13) and R2.4's deal fold
and R3.8's lead fold land on either side of this sub-project's releases (V32 gives HEAD's figures). A release
that adds no container, no kind change and no profile change leaves **every total unchanged from its parent
commit**; the check is to run `python3 infrastructure/edge/lib/memory_budget_totals.py
infrastructure/edge/memory-budget.json --profiles <list>` at the parent and at the release and compare, for
`""` (core), `crm`, the ten sellable profiles
`crm,hrms,inventory,support,storefront,projects,pulseboard,marketing,itsm,finance`, and the same list with
`admin` appended. A release that does change a total (R3.6 moves a container between profiles, R3.8 folds
a consumer) states the change as a delta with its arithmetic (§10.8, §12.8). `EDGE_BUDGET_ACCEPTED_MB` is
raised by the owner to the total `make edge-preflight` prints for the deploy being made, never to a number
copied from a specification.

## 5. R3.1 Data-model fit (expand-only)

### 5.1 Purpose and shape of the release

Make the book fit how a small Indian business sells: a company with a legal name, GSTINs and
addresses; a contact who exists with only a phone (WhatsApp-first buyers, **pending D-14**); a deal with a
human number and a recorded reason when it closes; a lead that remembers its campaign; a phone-only lead
that converts into a phone-only contact. Nothing in this release changes what a rep sees on a screen.

**Two deploys** (DD-1): **R3.1a** carries every migration, the tolerant readers (the CRM's own and
**finance's**, §5.3.3), the new company routes, deal numbers and close-reason tables, lead UTM fields, the
email index and the internal-route enforcement, and creates no contact without an email. **R3.1b** is the
small change that lets a contact exist without one and lets conversion carry a lead's phone. R3.1b ships
only after R3.1a has been deployed, the SPA has been published with the tolerant types (§5.6), and H8 has
run (§5.8).

### 5.2 Data model

#### 5.2.1 `platform_core.gst` (A15)

New `libs/platform-core/src/platform_core/gst.py` takes, unchanged in behaviour, the pure GSTIN code
that lives in `services/finance/app/domain.py:303-480`: `GST_STATE_CODES`, `_GSTIN_ALPHABET`,
`_GSTIN_PATTERN`, `GstinError`, `Gstin`, `gstin_check_digit`, `normalise_gstin`, `parse_gstin`,
`is_valid_gstin`. `state_code_for_name` and `_STATE_CODE_BY_NAME` stay in finance (they serve
finance's free-text state). Finance's `domain.py` re-exports the moved names so
`domain.GST_STATE_CODES`, `domain.parse_gstin` and every finance test and call site
(`services/finance/app/services.py:715, 1106`, `selectors.py:1039`) are untouched. `parse_gstin`
checks length, structure, state code and checksum, in that order, and raises `GstinError` with a
reason a person can act on. No PAN is stored anywhere (B7): `Gstin.pan` exists as characters 3-12 of
the number and a screen that needs it takes it from the GSTIN it already shows.

#### 5.2.2 customer — revision `<rev>_party_identity_and_phone_first_contacts`

`down_revision`: the customer head at merge (R2.6's `owner_rekey_journal` revision), marker
`# RE-PARENT HERE`.

```sql
SET LOCAL lock_timeout = '5s';

-- B6
ALTER TABLE customer.companies ADD COLUMN legal_name VARCHAR(200) NOT NULL DEFAULT '';

-- B7  (TenantBase columns, then:)
CREATE TABLE customer.company_tax_ids (
    company_id  UUID        NOT NULL REFERENCES customer.companies (id) ON DELETE CASCADE,
    kind        VARCHAR(10) NOT NULL,
    value       VARCHAR(15) NOT NULL,
    state_code  VARCHAR(2),
    label       VARCHAR(80) NOT NULL DEFAULT '',
    is_primary  BOOLEAN     NOT NULL DEFAULT false,
    CONSTRAINT ck_company_tax_ids_kind  CHECK (kind = 'gstin'),
    CONSTRAINT ck_company_tax_ids_value CHECK (value ~ '^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$'),
    CONSTRAINT ck_company_tax_ids_state CHECK (state_code IS NULL OR state_code = substr(value, 1, 2))
);
CREATE UNIQUE INDEX uq_company_tax_ids_org_kind_value
    ON customer.company_tax_ids (organization_id, kind, value) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX uq_company_tax_ids_org_company_primary
    ON customer.company_tax_ids (organization_id, company_id) WHERE is_primary AND deleted_at IS NULL;
CREATE INDEX ix_company_tax_ids_org_company ON customer.company_tax_ids (organization_id, company_id);

-- B8  (TenantBase columns, then:)
CREATE TABLE customer.addresses (
    company_id  UUID         NOT NULL REFERENCES customer.companies (id) ON DELETE CASCADE,
    kind        VARCHAR(12)  NOT NULL,
    line1       VARCHAR(200) NOT NULL,
    line2       VARCHAR(200) NOT NULL DEFAULT '',
    city        VARCHAR(120) NOT NULL DEFAULT '',
    state_code  VARCHAR(2),
    state       VARCHAR(120) NOT NULL DEFAULT '',
    postal_code VARCHAR(12)  NOT NULL DEFAULT '',
    country     VARCHAR(120) NOT NULL DEFAULT 'India',
    is_default  BOOLEAN      NOT NULL DEFAULT false,
    CONSTRAINT ck_addresses_kind       CHECK (kind IN ('billing', 'shipping', 'registered')),
    CONSTRAINT ck_addresses_state_code CHECK (state_code IS NULL OR state_code ~ '^[0-9]{2}$')
);
CREATE UNIQUE INDEX uq_addresses_org_company_kind_default
    ON customer.addresses (organization_id, company_id, kind) WHERE is_default AND deleted_at IS NULL;
CREATE INDEX ix_addresses_org_company ON customer.addresses (organization_id, company_id);

-- B9, B10, B11
ALTER TABLE customer.contacts ALTER COLUMN email DROP NOT NULL;
ALTER TABLE customer.contacts ADD COLUMN email_index VARCHAR(64);
CREATE INDEX ix_contacts_org_email_index ON customer.contacts (organization_id, email_index);
ALTER TABLE customer.contacts ADD CONSTRAINT ck_contacts_has_email_or_phone
    CHECK (email IS NOT NULL OR phone_index IS NOT NULL) NOT VALID;
ALTER TABLE customer.contacts VALIDATE CONSTRAINT ck_contacts_has_email_or_phone;
CREATE UNIQUE INDEX uq_contacts_org_phone_only
    ON customer.contacts (organization_id, phone_index)
    WHERE deleted_at IS NULL AND email IS NULL AND phone_index IS NOT NULL;
-- ENABLE + FORCE ROW LEVEL SECURITY and CREATE POLICY tenant_isolation on both new tables,
-- exactly as in §4.1; both added to TENANT_TABLES in customer's conftest.
```

Notes. `value`'s regex mirrors `_GSTIN_PATTERN` (`finance/app/domain.py`); the checksum is verified in
the application (A15) because the database cannot. `state_code` on a tax id is always the first two
characters of the GSTIN, so it is stored for filtering and the CHECK keeps it honest.
`state_code` on an address is validated against the keys of `GST_STATE_CODES` in the application.
`email_index` is `platform_core.pii.blind_index(canonical_email, kind="email")` and is **not**
backfilled in the migration: it is a keyed HMAC that needs the PII key, and the precedent for the
phone index declined a key-dependent backfill in a migration (`b5c81e02a7d9_contact_phone_index.py`,
docstring). Until H8 has run, `email_index IS NULL` reads as "not findable by index yet". The
existing `uq_contacts_org_email` is kept untouched (NULLs do not collide). `name` stays the unique
trading name (`uq_companies_org_name`); `legal_name` is not unique. `ON DELETE CASCADE` on `company_id` is the
database's net only: companies are soft-deleted in the application, so `delete_company` soft-deletes the
company's tax ids and addresses itself, in the same transaction (DD-43, §5.4 rule 9).

#### 5.2.3 deal — revision `<rev>_deal_number_and_close_reasons`

`down_revision`: the deal head at merge (R2.3's stage tables, then R2.6's journal), `# RE-PARENT HERE`.
The revision is chained after `a1c9e2f7b3d4` (adopted unchanged by R2.3, B18); this release creates no
`closed_at`.

```sql
SET LOCAL lock_timeout = '5s';

-- B23
ALTER TABLE deal.deals ADD COLUMN number INTEGER;
CREATE UNIQUE INDEX uq_deals_org_number ON deal.deals (organization_id, number) WHERE number IS NOT NULL;
CREATE TABLE deal.deal_counters (            -- composite key: no TenantBase columns
    organization_id UUID PRIMARY KEY,
    next_number     BIGINT      NOT NULL DEFAULT 1,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_deal_counters_positive CHECK (next_number >= 1)
);
-- backfill, set-based and idempotent: number every unnumbered deal per organization by
-- (created_at, id) after the organization's current maximum, soft-deleted rows included
WITH base AS (SELECT organization_id, COALESCE(MAX(number), 0) AS top FROM deal.deals GROUP BY 1),
     ranked AS (SELECT d.id, b.top + row_number() OVER (PARTITION BY d.organization_id
                                                        ORDER BY d.created_at, d.id) AS n
                  FROM deal.deals d JOIN base b USING (organization_id) WHERE d.number IS NULL)
UPDATE deal.deals d SET number = r.n FROM ranked r WHERE d.id = r.id;
INSERT INTO deal.deal_counters (organization_id, next_number)
SELECT organization_id, MAX(number) + 1 FROM deal.deals WHERE number IS NOT NULL GROUP BY 1
ON CONFLICT (organization_id) DO UPDATE
    SET next_number = GREATEST(deal.deal_counters.next_number, EXCLUDED.next_number);

-- B24  (TenantBase columns, then:)
CREATE TABLE deal.close_reasons (
    outcome     VARCHAR(8)   NOT NULL,
    key         VARCHAR(40)  NOT NULL,
    label       VARCHAR(120) NOT NULL,
    position    INTEGER      NOT NULL DEFAULT 0,
    archived_at TIMESTAMPTZ,
    CONSTRAINT ck_close_reasons_outcome CHECK (outcome IN ('won', 'lost')),
    CONSTRAINT ck_close_reasons_key     CHECK (key ~ '^[a-z0-9_]{1,40}$')
);
CREATE UNIQUE INDEX uq_close_reasons_org_outcome_key ON deal.close_reasons (organization_id, outcome, key);
ALTER TABLE deal.deals ADD COLUMN close_reason_id UUID REFERENCES deal.close_reasons (id) ON DELETE RESTRICT;
ALTER TABLE deal.deals ADD COLUMN close_note VARCHAR(500) NOT NULL DEFAULT '';
CREATE INDEX ix_deals_org_close_reason ON deal.deals (organization_id, close_reason_id)
    WHERE close_reason_id IS NOT NULL;
-- ENABLE + FORCE RLS and tenant_isolation on deal_counters and close_reasons (§4.1).
```

`number` is nullable only so the expand is cheap; after the backfill every row has one and every new
deal gets one in its creating transaction. `uq_close_reasons_org_outcome_key` is **not** partial: a key
is never reused, even archived (B20's rule applied here). The key is immutable after create.

#### 5.2.4 lead — revision `<rev>_lead_campaign_and_utm`

`down_revision`: lead's head at merge (R2.6's journal), `# RE-PARENT HERE`.

```sql
SET LOCAL lock_timeout = '5s';
ALTER TABLE lead.leads ADD COLUMN marketing_campaign_id UUID;       -- B32, plain UUID, no FK
ALTER TABLE lead.leads ADD COLUMN utm_source   VARCHAR(120);
ALTER TABLE lead.leads ADD COLUMN utm_medium   VARCHAR(120);
ALTER TABLE lead.leads ADD COLUMN utm_campaign VARCHAR(120);
ALTER TABLE lead.leads ADD COLUMN utm_term     VARCHAR(120);
ALTER TABLE lead.leads ADD COLUMN utm_content  VARCHAR(120);
CREATE INDEX ix_leads_org_marketing_campaign_id ON lead.leads (organization_id, marketing_campaign_id)
    WHERE marketing_campaign_id IS NOT NULL;
```

#### 5.2.5 Downgrade behaviour

| Revision | Downgrade drops | Refuses |
|---|---|---|
| customer | the new constraints, indexes, `email_index`, `company_tax_ids`, `addresses`, `legal_name`; restores `email NOT NULL` | **always**, with `RuntimeError`, while any contact has `email IS NULL` (a phone-only contact cannot be given back an email column that cannot be null, and the previous code cannot read it); unless `OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE=1`, also while `company_tax_ids` or `addresses` hold a live row |
| deal | `close_reason_id`, `close_note`, `close_reasons`, `deal_counters`, `number` | unless the override is set, while `close_reasons` holds a row or any deal has `close_reason_id` set |
| lead | the six columns and the index | unless the override is set, while any lead has a non-null campaign or UTM value |

On an empty database every revision fully reverses, so `make migration-roundtrip` stays green.

### 5.3 Backend contract

#### 5.3.1 New and changed public routes (customer)

Paths use `/companies/...` (C22, canonical from R3.2); until R3.2 adds the gateway row (C67) the same
handlers are reached as `/customers/{company_id}/...`, which the gateway already routes. The service
mounts both prefixes from R3.1.

| Route | Permission, floor | Request | Response | Errors | Named negative-authz test |
|---|---|---|---|---|---|
| `GET /companies/{company_id}/tax-ids` | `crm.book.company.read`, `assert_can_touch` | none | `{items: TaxIdOut[]}` | 404 | `test_tax_ids_list_refused_for_out_of_scope_company` |
| `POST /companies/{company_id}/tax-ids` | `crm.book.company.write`, `assert_can_touch` | `{kind: "gstin", value, label?, is_primary?}` (`extra="forbid"`: a `pan` key is 422) | 201 `TaxIdOut {id, company_id, kind, value, state_code, state_name, label, is_primary, created_at}` (`state_name` is derived from `GST_STATE_CODES`, read-only, so the SPA carries no copy of the table) | 422 `invalid_gstin` (checksum, shape, state), 422 `too_many_tax_ids` (more than 20), 409 `duplicate_gstin` | `test_tax_id_create_requires_company_write`; `test_tax_id_on_another_reps_company_is_403_for_own_scope` |
| `PATCH /companies/{company_id}/tax-ids/{tax_id}` | `crm.book.company.write` | `{label?, is_primary?}` (`value` is immutable: delete and add) | `TaxIdOut` | 404, 422 | `test_tax_id_patch_cross_tenant_is_404` |
| `DELETE /companies/{company_id}/tax-ids/{tax_id}` | `crm.book.company.write` | none | 204; the oldest remaining id becomes primary if the primary was deleted | 404 | `test_tax_id_delete_requires_company_write` |
| `GET\|POST /companies/{company_id}/addresses`, `PATCH\|DELETE .../{address_id}` | read `crm.book.company.read`; write `crm.book.company.write`; both with `assert_can_touch` | `AddressIn {kind, line1, line2?, city?, state_code?, state?, postal_code?, country?, is_default?}` | `AddressOut` | 422 `invalid_state_code`, 422 `too_many_addresses` (more than 10), 404 | `test_address_routes_follow_company_scope` |
| `POST /contacts` | `crm.book.contact.write` | `ContactIn`; `email` **optional from R3.1b**, at least one of `email`, `raw_phone` (422 `needs_email_or_phone`) | `ContactOut` | 409 `duplicate_email` or `duplicate_phone`, each with `existing_id` when the caller can read it | `test_contact_create_without_contact_write_is_403` |
| `PATCH /contacts/{id}` | `crm.book.contact.write` | `ContactPatch`: `email` may be **set** (including on a phone-only contact) and re-indexed; it still cannot be cleared (explicit `null` stays a 422, `_reject_explicit_null`) | `ContactOut` | 409 `duplicate_email` | `test_contact_patch_out_of_scope_is_403` |
| company create and patch | unchanged | `CompanyIn` / `CompanyPatch` gain `legal_name` (max 200) | `CompanyOut` gains `legal_name` and `primary_gstin: string \| null` (read from `company_tax_ids`) | unchanged | existing matrix rows |
| `DELETE /companies/{company_id}` (the existing delete, `/customers/{company_id}` until R3.2) | `crm.book.company.delete`, `assert_can_touch` | none | 204 as today. **Also soft-deletes the company's tax ids and addresses in the same transaction** (DD-43), after the existing refusal while live contacts exist | 404; 409 `"This company still has contacts. Reassign or delete them before deleting the account."` (the one 409 the route has; `services.py:562-577`) | existing matrix rows for `crm.book.company.delete`; behaviour: `test_deleted_company_releases_its_gstin` |

`ContactOut.email` and `ContactSummaryOut.email` become `str | None` (C33, C47);
`Customer360Customer.email` becomes `str | None` and the 360 never calls `mask(None)`
(`c1_360.py:320`). `ContactOut` gains no other field in this release. A 409 never leaks a foreign
record's id: `existing_id` is present only when the existing row is inside the caller's `ctx.apply`
scope, otherwise the message is generic ("That email is already on a contact you cannot open").

#### 5.3.2 Deal and lead models

`DealOut` gains `number: int | null`, `close_reason_key: string | null` and `close_note: string` (C7);
`DealIn` is unchanged (a number is allocated, never supplied). `LeadIn`, `LeadPatch` and `LeadOut` gain
`marketing_campaign_id: uuid | null` and the five `utm_*` strings (C36); a value is stripped and capped
at 120 characters, with control characters refused (422 `invalid_utm`). The campaign id is validated
by shape only.

#### 5.3.3 Internal routes

| Route | Change | Caller allow-list and org claim | Negative tests (three each) |
|---|---|---|---|
| customer `GET .../contacts/{id}/messaging-target` (C45) | `_require_caller(request, "notification")` and `_require_org_claim` added before any lookup | `notification` | `test_messaging_target_without_token_is_401`, `..._from_wrong_caller_is_403`, `..._with_wrong_org_claim_is_403`; plus the real-helper test below |
| customer `POST .../contacts:resolve` (C46) | body `{phone?, email?}` (at least one, else 422); phone matches `phone_index` as today; email is canonicalised and matches `email_index`, then exact `email` for rows not yet indexed; the oldest live match wins deterministically (`created_at, id`); guards added | `notification`, `lead` | `test_contacts_resolve_without_token_is_401`, `..._wrong_caller_is_403`, `..._wrong_org_claim_is_403` |
| customer `GET .../contacts/{id}` (C47) | `email` nullable. **Finance changes in R3.1a, before any phone-only contact can exist**: `CrmContactSummary.email` becomes `str \| None` (`services/finance/app/clients.py:44-51, 103`) and `ensure_customer_for_crm_contact` writes `email=summary.email or ""` (`services/finance/app/services.py:583-588`) into `Customer.email`, which is `NOT NULL` with default `""` (`services/finance/app/models.py:98`). Without it a null answer would insert NULL and raise an `IntegrityError` the function re-raises (it is not `uq_customers_org_crm_contact`). The bridge has no caller today, so the failure is latent; SP3 changes the contract, so SP3 ships the fix, and R3.1b stays behind it | `finance` (unchanged) | existing three, plus finance's `test_identity_bridge.py::test_a_contact_with_no_email_becomes_a_billed_party_with_an_empty_email` (a null `email` in the stubbed customer answer: the row is created with `email == ""`, the audit row and event are unchanged, a replay finds it) |
| customer `POST .../leads:convert` (C48) | creates a contact when the lead has an email **or** a phone (R3.1b); accepts optional `company_gstin` | `lead` (unchanged) | existing three |

**The real-helper test** (C45, J13): `tests/infra/test_service_token_org_claim_roundtrip.py` runs, in a
fresh interpreter with `services/notification` as cwd (the subprocess pattern of
`libs/contracts/tests/test_event_schemas.py:_notification_engage_event_types`), notification's real
`targets.resolve_contact_target` and `contacts.resolve_contact_by_phone` against a stub transport that
captures the `X-Internal-Token`; a second interpreter with `services/customer` as cwd decodes each
captured token with customer's `require_service_token` and `_require_org_claim` and asserts it passes
for the right organization and fails for another. It proves enforcement does not 403 the real caller.
Both enforcements deploy only after C77 (R1.2a) is live (J13).

#### 5.3.4 Event

`crm.pipeline.deal.created` gains optional `deal_number` (integer or null). Its committed schema
`libs/contracts/events/crm.pipeline.deal.created/v1.schema.json` and the producer change
(`deal/app/services.py:175-183`, after R2.3's full-state payload) land in one commit with
`test_deal_created_event_carries_deal_number` through the real `create_deal` path and
`validate_event_schema`. No other event changes.

#### 5.3.5 Tool H8 (built here)

`tools/backfill_pii_indexes.py` (wrapper `remote.sh backfill-pii-indexes`, REG H8) with
`--service customer --target email_index`. Plan (default, read-only) reports per organization: rows to
index, rows already indexed, and the number of case-variant duplicate groups, as counts. `apply`
requires `--confirm-count N`, `--i-took-a-backup` and `--i-know-this-is-the-live-database`; it opens one
transaction per organization on the app role with the tenant GUC bound, computes the index from the
plaintext email already in the row (no sealed value is opened and nothing is printed), writes **only**
`email_index`, leaves `updated_at` alone, stages no event, writes one audit row per organization
(`acting_as='backfill-pii-indexes-cli'`, field name and count), is idempotent and paced with `--pace`
(default 250 ms), and stops at the first failing organization. Output is counts and ids only.

#### 5.3.6 Documents (the same commits)

* **`docs/crm.md`** (REG I3): the event table records `deal_number` on `crm.pipeline.deal.created` and the
  glossary row for *company* names legal name, tax ids and addresses; SP1's
  `tests/infra/test_crm_doc_matches_contracts.py` fails the build otherwise.
* **REG I8**: H8's owner steps (the `plan` and `apply` commands and their three gates, the order relative to the
  R3.1b deploy) go into `docs/deploy/ENV_SOURCE_MATRIX.md`'s tool list and the RUNBOOK, and
  `docs/deploy/env-reference.md` where it lists owner-run tools.
* **REG I6**: any open-questions register row that tracks the sealed-index backfills (the phone index of B40
  and the email index of B10; the plan finds the row) is updated to say H8 exists, and `docs/decisions.md`
  needs no row (R3.1 adds no ADR).

### 5.4 Domain rules

1. **Canonical email** is `strip().lower()` (`services/customer/app/domain.py`, a new pure module,
   `canonical_email`); `email_index` is `blind_index(canonical, kind="email")` and is written on every
   create and every email change (DD-3). The stored `email` keeps what `EmailStr` validated. A write
   whose canonical email matches another live contact's `email_index`, or whose exact `email` matches,
   is a 409 `duplicate_email`.
2. **Phone-first uniqueness.** A contact with no email is unique by `phone_index` among live
   phone-only contacts (`uq_contacts_org_phone_only`); a contact **with** an email keeps email
   uniqueness and may share a phone with others (DD-4). Creation takes
   `pg_advisory_xact_lock(hashtext(org || ':contact-phone:' || phone_index))` before the check, and the
   unique index is the net (an `IntegrityError` on it maps to 409 `duplicate_phone`).
3. **Conversion** (R3.1b): lookup order for the contact is the caller's deterministic id in any state
   (a deleted row refuses with `ContactDeletedProblem`, as today), then, if the lead has an email, a
   contact by canonical email (phone reconciled as today, `customer/app/services.py:1096-1157`), then,
   if it has only a phone, a **live phone-only** contact with that `phone_index`; otherwise a new contact
   is created with the lead's email (if any), the sealed phone, status `customer` (unchanged, DD-5).
   `LeadConversionOut.contact_id` is therefore non-null whenever the lead had an email or a phone.
4. **`company_gstin`** (C48): parsed with `parse_gstin`; an invalid value is dropped, never a reason to
   refuse the conversion; a valid one first looks for a live company that already holds that GSTIN and
   **reuses it** (ahead of the name match), else the new or name-matched company gets it as its primary
   tax id. "Live" means `companies.deleted_at IS NULL` as well as the tax-id row's own `deleted_at`: the
   lookup joins `companies`, so a company deleted a moment earlier can never be the holder, whatever state its
   tax-id rows are in (rule 9 releases them, the join is the second net). Lead sends it from R3.7; until
   then the field is optional and absent.
5. **Deal number** is allocated in the creating transaction by
   `INSERT INTO deal.deal_counters (organization_id, next_number) VALUES (:org, 2) ON CONFLICT
   (organization_id) DO UPDATE SET next_number = deal_counters.next_number + 1, updated_at = now()
   RETURNING next_number - 1`, so a rolled-back create returns its number and numbers are gap-free per
   organization; the row lock serialises creates per organization for the transaction's length.
   `crm.pipeline.deal.created` carries it. Display (`D-42`) is a UI concern.
6. **Default close reasons** (DD-2): `GET` of the list (R3.4) returns the code default when the
   organization has no rows, each with `id = uuid5(NS, f"{organization_id}:{outcome}:{key}")` where
   `NS = 0d6f3a52-8c1e-4b97-a2f4-5e17c9b3d6a8` is fixed forever (the pattern of
   `lead/app/domain.py:118`) and `materialised: false`. The first write that needs them (a stage move
   naming a reason, any settings write) calls `ensure_default_close_reasons(session, org)`, which takes
   `pg_advisory_xact_lock(hashtext(org || ':close-reasons'))`, re-reads and inserts the defaults with
   `ON CONFLICT DO NOTHING`. Defaults, lost: `price` "Price too high", `competitor` "Chose a
   competitor", `no_budget` "No budget", `no_decision` "No decision made", `timing` "Bad timing",
   `requirements` "Requirements not met", `no_response` "Stopped responding", `other` "Other"; won:
   `price` "Price", `product_fit` "Product fit", `relationship` "Relationship", `service` "Service and
   support", `timing` "Timing", `other` "Other".
7. **Audit scrub** (B12, part 1): from this release customer's contact audit rows
   (`contact.created`, `contact.updated`, `contact.reused_for_lead`, and the lead-conversion phone
   write) carry `after={"changed_fields": [names]}` and no `before`, never `email`, `full_name` or a
   phone value. The tests that pin the old shape
   (`services/customer/tests/test_lead_conversion_internal.py:324, 380`) change on purpose. Rows
   written before this release keep their values until the erasure design (R4.1) rewrites them.
8. **Audit of company identity** (DD-48, REG L11). Tax-id, address and `legal_name` writes audit **ids and
   field names only**. `company.tax_id_added`, `.tax_id_updated`, `.tax_id_removed` and `company.address_added`,
   `.address_updated`, `.address_removed` write `after = {"tax_id_id" | "address_id": <uuid>,
   "changed_fields": [names]}` and no `before`; a change of `legal_name` through `update_company` lists
   `legal_name` in `changed_fields` and keeps its values out of `before` and `after`. Never `value`, `label`,
   `line1`, `line2`, `city`, `postal_code`, `state` or `country`, and never a GSTIN (its characters 3 to 12
   are a PAN, B7), because `record_audit` redacts by key name only (`libs/platform-core/src/platform_core/audit.py:29-55,
   106-115`) and individuals are one-person companies (P§4.2). Events carry no tax id, address or legal name,
   and the summary SP4's company route returns holds business facts only (the L11 default). Rows
   `update_company` wrote before this release keep their shape; the erasure design (R4.1) owns them.
9. **Deleting a company releases its identity rows** (DD-43). `delete_company` sets `deleted_at` on the
   company's live `company_tax_ids` and `addresses` rows in the same transaction (one statement each, `updated_by`
   stamped, no per-row audit row), after the existing check that refuses while live contacts exist; its own
   `company.deleted` audit row adds the counts `{"tax_ids_released": n, "addresses_released": m}` and nothing
   else. `uq_company_tax_ids_org_kind_value` therefore frees the GSTIN for another company and C48's
   holder lookup cannot see it. There is no undelete in this programme, so released rows are not restored.

### 5.5 Consumers and idempotency

No consumer is added. Idempotency: deal numbers by the counter (a replayed create is blocked by the
existing name check, `deal/app/services.py:130-131`); conversion by deterministic ids (unchanged);
`ensure_default_close_reasons` by the advisory lock and `ON CONFLICT DO NOTHING`; H8 by "skip rows
already indexed"; the deal backfill by "`WHERE number IS NULL`" and the counter's `GREATEST`.

### 5.6 UI (tolerance only, published after R3.1a)

No new screen. The SPA is changed to **tolerate** what the API may now return, so R3.1b is safe:
`Contact.email` is `string | null` in `frontend/product-crm/src/api/types.ts` and `wire.ts`; the contacts
table, the expanded row, `DealDetail` and `CompanyDetail` contact rows show "No email" and never render
`mailto:` for a null; the import and export definitions (`data/importExport/contacts.ts`,
`LOOKUP_CONTACT_EMAILS` in `shared.ts`) skip null emails; `useGlobalSearch`'s `ContactRow.email` is
`string | null` (`useGlobalSearch.ts:126-130`); Engage's `Customer360Profile.email` and fixtures are
`string | null` (`frontend/product-engage/src/api/customer360.ts:29`, `customer360.fixtures.ts:16`).
Import and export column catalogues gain: companies `Legal name` and `GSTIN` (the primary one; an
import row with a GSTIN makes a second call to `POST /companies/{id}/tax-ids`, the multi-call row
pattern the engine already has for deals and contacts), deals `Deal number` (exported; accepted as an
update key beside the Deal ID), leads `UTM source`, `UTM medium`, `UTM campaign`, `UTM term`, `UTM
content`. The contact form keeps email required in the SPA until R3.4 (phone-only creation is a screen
decision there).

### 5.7 Tests

| Test (file::name) | Kind | Proves |
|---|---|---|
| `libs/platform-core/tests/test_gst.py` (vectors copied from finance's GSTIN tests) and finance's own suite unchanged | unit | extraction is behaviour-preserving; `domain.parse_gstin is platform_core.gst.parse_gstin` |
| `services/customer/tests/test_company_identity.py::TestTaxIds` (`test_valid_gstin_is_stored_with_state_code`, `test_bad_checksum_is_422_invalid_gstin`, `test_pan_key_is_422`, `test_duplicate_gstin_is_409_and_hides_a_foreign_companys_id`, `test_first_tax_id_is_primary_and_primary_moves_atomically`, `test_deleting_primary_promotes_oldest`) | negative and unit | B7, C22 |
| `…::TestAddresses` (`test_one_default_per_kind`, `test_state_code_must_be_a_gst_code`, `test_limit_ten_per_company`) | unit | B8 |
| `…::TestCompanyDelete` (`test_deleted_company_releases_its_gstin` (the GSTIN can be added to another company at once, no 409), `test_deleted_company_releases_its_addresses`, `test_delete_with_live_contacts_is_still_409_and_releases_nothing`, `test_conversion_ignores_a_deleted_gstin_holder` (C48 creates or name-matches instead of failing the tax-id insert, and a retry converges), `test_delete_audit_row_carries_counts_only`) | unit | DD-43, §5.4 rules 4 and 9 |
| `services/customer/tests/test_company_identity_audit.py` (`test_company_identity_audit_rows_hold_ids_and_field_names_only`: after every tax-id, address and `legal_name` write path no `audit_logs.before` or `after` value contains the entered GSTIN, line, city, postal code, label or legal name; beside `test_contact_audit_rows_are_names_only`) | unit | DD-48, §5.4 rule 8 |
| `services/finance/tests/test_identity_bridge.py::test_a_contact_with_no_email_becomes_a_billed_party_with_an_empty_email` | unit | C47 tolerance, §5.3.3 |
| `services/customer/tests/test_contacts_phone_first.py` (`test_phone_only_contact_needs_raw_phone`, `test_phone_only_duplicate_is_409`, `test_two_emailed_contacts_may_share_a_phone`, `test_unique_index_is_the_net_under_concurrency` (20 concurrent creates), `test_patch_can_add_an_email_but_not_clear_it`, `test_check_constraint_refuses_a_row_with_neither`) | unit, concurrency | B9, B11 |
| `…/test_contacts_email_index.py` (`test_write_sets_the_blind_index_of_the_canonical_email`, `test_case_variant_duplicate_is_409`, `test_resolve_by_email_hits_index_then_exact`, `test_resolve_needs_one_identifier`) | unit | B10, C46 |
| `…/test_internal_guards.py` (the 3 + 3 + real-helper rows of §5.3.3) and `tests/infra/test_service_token_org_claim_roundtrip.py` | negative-authz, contract | C45, C46 |
| `…/test_lead_conversion_internal.py` (changed: audit rows names-only; new `test_phone_only_lead_converts_into_a_phone_only_contact`, `test_conversion_reuses_only_a_phone_only_contact`, `test_company_gstin_reuses_the_company_that_holds_it`, `test_invalid_company_gstin_is_dropped_not_refused`) | unit | C48 |
| `…/test_contact_audit_rows_are_names_only.py` | unit | B12 part 1: after every contact write path, no `audit_logs.before/after` value contains the entered email, name or phone |
| `services/deal/tests/test_deal_number.py` (`test_numbers_are_sequential_per_org`, `test_concurrent_creates_get_distinct_numbers` (20 gathered), `test_rolled_back_create_returns_its_number`, `test_numbers_are_per_organization`, `test_backfill_numbers_existing_rows_by_created_at`, `test_backfill_is_idempotent`) | unit, concurrency, migration | B23 |
| `…/test_close_reasons.py` (`test_default_ids_are_deterministic_per_org`, `test_ensure_defaults_is_idempotent_and_concurrent_safe`, `test_referenced_reason_cannot_be_deleted`) | unit | B24, DD-2 |
| `…/test_deal_event_contract.py::test_deal_created_event_carries_deal_number` | contract | EV6 |
| `services/lead/tests/test_campaign_utm.py` (`test_utm_fields_round_trip`, `test_control_characters_are_422`, `test_campaign_id_is_a_plain_uuid_no_lookup`) | unit | B32 |
| `services/{customer,deal,lead}/tests/test_authz_matrix.py` | negative-authz | §4.1: every route has a row |
| `services/*/tests/test_cross_tenant_isolation.py` (extended per new table and route) | cross-tenant | RLS and the org filter on every new table |
| `services/*/tests/test_migration_*` (round-trip on empty and seeded databases; downgrade refusal and override; one head; `pg_policies`, `relforcerowsecurity`, `has_table_privilege`; no cross-schema `pg_constraint`) | migration | §5.2 |
| `tests/infra/test_backfill_pii_indexes.py` (`test_plan_writes_nothing`, `test_apply_needs_all_three_gates`, `test_apply_is_idempotent`, `test_output_has_no_email`, `test_updated_at_is_untouched`) | tool | H8 |
| `frontend/product-crm/src/**.test.tsx` null-email cases for the contacts table, `CompanyDetail`, `DealDetail`, import and export | unit | §5.6 |

### 5.8 Rollout

1. **Prerequisite**: R1.2a (C77) deployed and a day of traffic observed. 2. **Deploy R3.1a**
(`make deploy HOST=… CONFIRM=… REF=<merged ref>`; the migration runs in `deploy`'s migrate step; every
service image rebuilds because platform-core changed, finance included, and finance's null-email tolerance
(§5.3.3) rides this deploy, ahead of any phone-only contact). 3. **Publish the SPA**
(`make edge-web HOST=… CONFIRM=… APP=crm` from the checkout of the deployed ref, per the owner's
standing rule) with the tolerant types, and Engage (`APP=engage`). 4. **Owner runs H8** (§13): plan,
read the counts, apply per organization. 5. **Deploy R3.1b**. 6. **Verify**: the owner runs
`e2e/edge_product_crud.py` (the production smoke, owner-run only) and creates one phone-only contact
through the API.

*Flags*: none; the two deploys are the staging. *Reconcile*: none (no new permission code). *Memory*:
no container, no kind change, no profile change, so every total `memory_budget_totals.py` prints over
`memory-budget.json` is **unchanged from the commit before this release** (§4.5) and
`EDGE_BUDGET_ACCEPTED_MB` is not touched. (At HEAD that is 3747 MB for the ten profiles and 3944 MB with
`admin`, V32; R2.4's deal fold moves both up by one if it merged first.)

### 5.9 Rollback

| Step | Reversible? | How |
|---|---|---|
| R3.1b code | yes | `make edge-rollback` to R3.1a; phone-only rows already created stay and R3.1a tolerates them |
| R3.1a code | yes **while no contact has a NULL email**; after R3.1b ran, the owner first runs `SELECT count(*) FROM customer.contacts WHERE email IS NULL` (owner-run) and rolls back below R3.1a only at zero | the previous release cannot read a NULL email |
| Schema | never downgraded on the box in the normal path ("code-only rollback, schema stays"); `alembic downgrade` is for development and refuses as §5.2.5 | |
| H8 | the index is derived data; re-running is safe; there is nothing to undo | |
| Deal numbers | not reversible and not harmful: a number once shown is never reused | |

Stop conditions: a migration fails; `contacts` list error rate above baseline after R3.1b; any audit
row written after the deploy containing an email or name; a 403 on notification's address lookups.

### 5.10 Acceptance criteria (mechanical)

1. `make test-services` (the per-service loop) passes for customer, deal, lead and finance;
   `uv run pytest libs/platform-core/tests/test_gst.py -q` passes.
2. `make migration-roundtrip` and `tests/infra/test_migration_revisions.py` pass (one head per service).
3. `make openapi` then `make openapi-check` pass; `libs/contracts/openapi/customer.json` lists
   `/api/v1/customers/{company_id}/tax-ids` and `/addresses`, and `ContactOut.email` is nullable.
4. `grep -rn "contact.email" services/customer/app/c1_360.py` shows no unguarded `mask(` call.
5. `python tools/export_permissions.py --check` passes unchanged (no code added).
6. For each of `company_tax_ids`, `addresses`, `deal_counters`, `close_reasons`:
   `pg_policies` has `tenant_isolation`, `pg_class.relforcerowsecurity` is true, and no `pg_constraint`
   references another schema (asserted in the migration tests).
7. `crm.pipeline.deal.created` validates with and without `deal_number` null, through the real path.
8. `tests/infra/test_backfill_pii_indexes.py` passes; the tool's stdout contains no `@`.
9. `frontend`: `pnpm typecheck && pnpm lint && pnpm test && pnpm build` in `product-crm` and
   `product-engage`; `pnpm tokens:baseline` shows no increase.
10. Finance tolerates a phone-only contact before R3.1b can create one: `services/finance/tests/test_identity_bridge.py::test_a_contact_with_no_email_becomes_a_billed_party_with_an_empty_email`
    passes, and `grep -n "summary.email" services/finance/app/services.py` shows `summary.email or ""`.
11. `test_deleted_company_releases_its_gstin`, `test_conversion_ignores_a_deleted_gstin_holder` and
    `test_company_identity_audit_rows_hold_ids_and_field_names_only` pass.
12. The standing criteria of §4.5: S1 to S6 and S8, and S7 for the two SPAs.

## 6. R3.2 API cleanup

### 6.1 Purpose

Every list answers the questions a screen asks (search, sort, filter, page) on the server with a
stable order, so the SPA can delete its page-local sorts, its 200-row directory and its five lead
count calls. The public nouns become `company` and `contact` (pending D-22) without breaking a single
existing caller. Errors are documented. The SPA's wire types are generated from the committed contracts.
Nothing a user sees changes in this release.

### 6.2 Data model: indexes only

One expand-only revision per service, `<rev>_list_indexes`, each with `SET LOCAL lock_timeout =
'5s'` and a downgrade that drops what it created. All lead with `organization_id`; no RLS change.

| Service | Index | Serves |
|---|---|---|
| customer | `ix_companies_org_created (organization_id, created_at, id)` | `sort=created_at`, `created_from/to` |
| customer | `ix_contacts_org_created (organization_id, created_at, id)`, `ix_contacts_org_last_activity (organization_id, last_activity_at)` | `sort=created_at`, `sort=last_activity_at`, date filters |
| deal | `ix_deals_org_created (organization_id, created_at, id)` | `sort=created_at`, `created_from/to` (`ix_deals_org_close` already serves `close_from/to`) |
| lead | `ix_leads_org_created (organization_id, created_at, id)`, `ix_leads_org_source (organization_id, source)` | default order, `source`, `created_from/to` |

`name` sorts are served by the existing `uq_companies_org_name` (`customer/app/models.py:132-138`).
Other sort keys are ordered inside the organization's own rows; there is no trigram index (DD-8).

### 6.3 Backend contract

#### 6.3.1 The list vocabulary (C35) and `platform_core.listing` (A20)

New `libs/platform-core/src/platform_core/listing.py` (A20), used by all four lists:

| Function | Behaviour |
|---|---|
| `escape_like(raw) -> str` | escapes `\`, `%`, `_`; queries use `ILIKE :needle ESCAPE '\'`, so a typed `%` matches only a literal `%` |
| `parse_sort(raw, allowed, *, default) -> SortSpec` | one key, `-` prefix for descending; an unknown key is `ValidationProblem` with `errors=[{field: "sort", code: "invalid_sort", message: "Sort by one of: …"}]` (422) |
| `apply_sort(stmt, spec, *, tiebreak) -> Select` | `ORDER BY <key> <dir> NULLS LAST, <tiebreak> <dir>`: the `id` tie-break makes every page boundary deterministic |
| `parse_ids(raw, *, limit=100) -> list[UUID] \| None` | comma-separated; more than `limit` or a malformed id is 422 `invalid_ids` |
| `parse_tz(raw) -> ZoneInfo` | default `Asia/Kolkata`; an unknown name is 422 `invalid_timezone` |
| `date_bounds(from_, to, tz) -> (start_utc, end_utc_exclusive)` | the inclusive date range `[from 00:00, to + 1 day 00:00)` in `tz` |

Parameters (every one optional; unknown parameters are ignored as today, an unknown **value** of an
enum parameter is a 422 because the OpenAPI document now types them):

| Parameter | `GET /companies` | `GET /contacts` | `GET /deals` | `GET /leads` |
|---|---|---|---|---|
| `q` | name, legal name, industry, city | full name, title, email (legacy, see below) | name; a numeric `q` (or `<letters>-<digits>`) also matches `number` | full name, company. **Never** email or phone (sealed; identifier lookups are `POST /leads:check-duplicates`, C38) |
| `sort` (default) | `name`, `created_at`, `status`, `annual_revenue` (`name`) | `name`, `created_at`, `last_activity_at` (`name`) | `name`, `value`, `close_date`, `created_at`, `closed_at`, `probability`, `stage` (value desc then created desc, the current order) | `created_at`, `name`, `status` (`-created_at`) |
| `status` | `customer\|prospect\|lead\|churned` | `customer\|qualified\|lead\|dormant` | n/a (`stage`, `outcome`) | `new\|working\|qualified\|disqualified\|converted` |
| `owner_id`, `owner` | yes (`owner=me`) | yes | yes (exists) | yes (exists) |
| `source` | n/a | n/a | free text, exact | `form\|import\|manual` (R3.7 adds `webhook\|whatsapp\|storefront`) |
| `created_from`, `created_to`, `tz` | yes | yes | yes | yes |
| `close_from`, `close_to` | n/a | n/a | yes (dates) | n/a |
| `company_id`, `stage`, `outcome` | n/a | `company_id` (exists) | `company_id` (exists), `stage` (exists), `outcome` (R2.3) | n/a |
| `ids` | yes | yes | yes | yes |
| `attention`, `tag` | n/a; `tag` R3.8 | `tag` R3.8 | `attention` R3.6, `tag` R3.8 | `tag` R3.8; leads also R3.7's `unassigned`, `sla`, `aging` |

Rules. `status` sorts by lifecycle order, not alphabetically (companies `lead, prospect, customer,
churned`; leads `new, working, qualified, converted, disqualified`); the deals `stage` sort orders by
the stage's `position` through a `CASE` built from the effective stage list (R2.3), unknown keys
last. `owner=me` is `ctx.own_ref`; a caller whose `own_ref` is `None` gets an **empty** page, never
the book (the `summary` precedent, `deal/app/api/routes.py:163-175`). `owner_id`, `owner`, `ids` and
every filter compose with `ctx.apply` and can only narrow it (a negative test per list: an OWN caller
naming another rep's id gets nothing). `ids` accepts at most 100 and only returns rows in scope; callers
pass `size` at least the id count. `created_from/to` are inclusive dates in `tz`; the SPA sends the
`timezone` of `GET /deals/settings` (DD-7). The contacts `q` match on `email` is a **documented legacy
exception** (an address in a `?q=` is retained by gateway and proxy logs): it stays for existing
callers, the OpenAPI description marks it deprecated, and new code uses `POST /contacts:search`.

#### 6.3.2 Routes

| Route | Permission, floor | Change | Errors | Named negative-authz test |
|---|---|---|---|---|
| `GET\|POST /companies`, `GET\|PATCH\|DELETE /companies/{company_id}` (C21) | `crm.book.company.read\|write\|delete` | the same handlers and schemas as `/customers…`, mounted under both prefixes; `/customers…` marked `deprecated` in OpenAPI, frozen | 401, 402, 403, 404, 409, 422 | existing rows, duplicated for `/companies` in the matrix |
| `GET /contacts/{contact_id}/360` (C23) | `crm.book.contact.read` + `assert_can_touch` + `Entitled` | canonical; `/customers/{contact_id}/360` stays an alias | as today | `test_360_alias_and_canonical_refuse_out_of_scope_contact` |
| `GET\|POST /activities` (C17) | `crm.pipeline.activity.read\|write` | the same handlers as `/deals/activities` (frozen alias: the dashboard calls `listDealActivities`, `frontend/opsgrids-dashboard/src/api/index.ts:388`); the list also gains `owner_id`, `owner` (`me`), `occurred_from`, `occurred_to` and `tz` (additive, REG C17: `activities_stmt` already takes `owner_id`, `selectors.py:108-135`, and no route exposed it) and orders by `occurred_at desc, id desc` | 422 | alias rows in the matrix; `test_activity_filters_cannot_widen_own_scope` |
| `POST /contacts:search` (C86) | `crm.book.contact.read` + `ctx.apply` | body `{email?, phone?}`; at least one else 422 `needs_identifier`; email by canonical `email_index` then exact; phone by `phone_index`; returns `Page[ContactOut]` for the caller's scope | 422 | `test_contacts_search_never_returns_out_of_scope_rows`; `test_contacts_search_identifier_is_not_logged` (asserts the access-log middleware sees no body) |
| `GET /leads/summary` (C88) | `crm.lead.lead.read`, `ctx.apply` | `LeadSummaryOut {total, by_status: {new, working, qualified, disqualified, converted}, unassigned}`; query `owner`, `owner_id`, `created_from/to`, `tz`; one `GROUP BY` replacing the Overview's five `size=1` calls; static path before `/{lead_id}` | 422 | `test_lead_summary_counts_only_in_scope_rows` |
| `GET /deals/summary` (C9) | `crm.pipeline.deal.read` | accepts the C35 filters (`q`, `stage`, `owner_id`, `owner`, `company_id`, `source`, `created_*`, `close_*`; `attention` and `tag` later) so the board header and Overview share one aggregation; **additive** `by_currency: CurrencySummaryOut[]` `{currency, pipeline_value_minor, weighted_pipeline_minor, open_deals, won_deals, lost_deals, won_value_minor, funnel: StageRollupOut[]}`; the legacy fields keep their names and meaning, and §6.4 rule 5 states the one rounding difference (DD-9) | 422 | `test_summary_filters_compose_with_scope` |

Gateway (C67, C68), both tables in one commit: `services/app-gateway/routes.json` gets
`{ "prefix": "/api/v1/companies", "upstream": "http://localhost:8130", "product": "crm" }` and
`{ "prefix": "/api/v1/activities", "upstream": "http://localhost:8131", "product": "crm" }`;
`infrastructure/edge/routes.prod.json` the same with `http://customer:8000` and `http://deal:8000`.
`tests/infra/test_route_table_parity.py` and `tests/infra/test_frontend_paths_are_routed.py` (which
scans every literal `apiRequest("/…")` first segment) gate it.

#### 6.3.3 Error shapes and OpenAPI error documentation (A21)

`platform_core.errors` gains two documentation-only models and one helper (the runtime shape is
unchanged: `ProblemError.to_problem`, `errors.py:28-52`):
`FieldError {field, code, message, existing_id?}`, `ProblemDocument {type, title, status, detail,
instance, request_id, errors?: FieldError[]}` and `problem_responses(*statuses) -> dict` returning the
FastAPI `responses=` mapping with `application/problem+json`. Every CRM operation declares what it can
answer: 401 always; 402 on entitled routes; 403 on guarded routes; 404 on `{id}` routes; 409 where a
uniqueness or state rule exists; 422 on bodies and typed queries; 429 on reveal and capture; 413 where
a cap applies. One OpenAPI tag per resource (`companies`, `contacts`, `owners`, `customer-360`;
`deals`, `activities`; `leads`, `assignment-rule`), path parameters named by their resource
(`company_id`, `contact_id`, `deal_id`, `lead_id`), and enum types on filter parameters. The stable
`errors[].code` values SP3 uses (a screen may branch on them; the `type` slug and status stay the
platform's):

| Status | `errors[].code` |
|---|---|
| 409 | `duplicate_email`, `duplicate_phone`, `duplicate_gstin`, `duplicate_name`, `duplicate_view_name`, `duplicate_endpoint_name`, `closed_deal`, `follow_up_not_open`, `conversion_in_progress` (each duplicate carries `existing_id` only when the caller can read that record) |
| 422 | `needs_email_or_phone`, `needs_identifier`, `invalid_gstin`, `invalid_state_code`, `too_many_tax_ids`, `too_many_addresses`, `invalid_utm`, `invalid_sort`, `invalid_ids`, `invalid_timezone`, `invalid_filter`, `unknown_stage`, `unknown_reason`, `reason_outcome_mismatch`, `no_target`, `consent_required`, `kind_not_rotatable`, `capture_not_configured`, `confirm_mismatch`, `owner_required`, `owner_not_active`, `invalid_tag`, `too_many_tags`, `too_many_views`, `invalid_report_range` |
| 400 | `invalid_capture_request` (the one answer to every failed check on the anonymous capture routes, R3.7b) |
| 403 | `out_of_scope`, `below_floor` |
| 429 | `rate_limited` (with `Retry-After`) |

A no-op `PATCH` (every submitted value equals the stored one) now returns the row unchanged and writes
**no** audit row and **no** outbox event (`update_company`, `update_contact`, `update_deal`,
`update_lead`); the import engine's update mode and a double-clicked save both stop manufacturing
history (ADR-0022:146).

#### 6.3.4 Generated wire types (G9)

`openapi-typescript` (exact-pinned in `frontend/opsgrids-config/package.json`; the rejected
alternative, a hand-rolled generator, would own OpenAPI 3.1 `anyOf`-null handling and `$ref` resolution
for nothing) generates `frontend/product-crm/src/api/wire.generated.ts` from
`libs/contracts/openapi/{customer,deal,lead}.json`, and
`frontend/opsgrids-client/src/crm/customer360.generated.ts` (exporting `Customer360`) for the shared
360. `pnpm --filter @opsgrids/product-crm types:generate` writes them; `types:check` regenerates to a
temporary file and fails on a diff; both join a new `frontend-types` target in `frontend-gate`
(`Makefile` row, run by `make gate` through `frontend-gate`). `DealStage` becomes `string` and
`StageOutcome = "open" | "won" | "lost"`. The hand-written `types.ts` becomes a thin re-export of the
generated types so screens migrate without churn; `wire.ts` and `mappers.ts` are deleted once no
rename remains (after `/companies` is canonical none does); `api/index.ts` (a barrel with zero
importers) is **already deleted by SP1/R1.2b** (class 1 row 6), so R3.2 neither deletes nor re-lists it.

### 6.4 Domain rules

1. **Stable order**: every list ends in the `id` tie-break; pagination of identically-valued rows
   neither repeats nor skips.
2. **Scope first, then filters**: `ctx.apply(stmt, owner_col=...)` is the first statement after the
   organization filter, exactly as today (`selectors.py:152, 262`; `deal/app/selectors.py:50`;
   `lead/app/selectors.py:39`); filters only narrow.
3. **Counts under scope**: `paginate` counts after scope and filters (`pagination.py:59-70`), so a
   total never reveals rows outside the caller's scope.
4. **`ids`** resolves names for screens: it never returns an out-of-scope row, so an id that is
   filtered out simply renders as "No access" in the UI.
5. **Summary parity and one rounding rule** (DD-9). The new `selectors.summary_rows` groups by
   `(currency, stage)` in SQL (`count`, `sum(value_minor)` and `sum(floor((value_minor::numeric *
   probability + 50) / 100))`: exact arithmetic, no float, no overflow) and `domain.fold_summary(rows, stages)`
   folds them. A deal's weighted value is `domain.weighted_minor(value_minor, probability) = (value_minor *
   probability + 50) // 100` (half up) and a win rate is `domain.win_rate_pct(won, decided) = (200 * won +
   decided) // (2 * decided)` (half up; 0 when nothing is decided, as the legacy field has always answered).
   Both replace the float expressions at `services/deal/app/domain.py:74, 87`, and the module's
   `weighted_pipeline_minor` and `win_rate_pct` call them, so the Python rollups stay the reference the SQL
   path is tested against and no money value passes through a float. The funnel's `pct` is a bar's share, not
   money, and keeps `stage_funnel`'s arithmetic. The legacy fields keep their names and meaning: they sum
   across currencies as today (frozen, deprecated in the OpenAPI description), and `currency` is the
   currency with the largest open pipeline (the first deal's currency before, which on a
   single-currency organization is the same value). **They can differ from today's answers only at exact
   halves**: `weighted_pipeline_minor` by at most one minor unit per deal (105 at 50 % was 52 under banker's
   rounding and is 53; 103 at 50 % was 52 and is still 52) and `win_rate_pct` by one point (1 won of 8 decided
   was 12 and is 13). `test_legacy_fields_move_only_at_exact_halves` pins both; the existing expectations
   (`services/deal/tests/test_domain.py:53, 63`, `test_seed.py:122`, the visual `summary.json`) hold no exact half,
   so none of them changes. `by_currency` is the correct view, and the Reports win rate (R3.8) is the
   same function, so Overview and Reports can never differ by a rounding point.
6. **No-op PATCH** compares the incoming values with the stored ones after normalisation
   (`.upper()` on currency, canonical email) before deciding anything was changed.

### 6.5 Consumers and idempotency

None added. The alias routes are idempotent by being the same handler.

### 6.6 UI

No user-visible change. `frontend/product-crm/src/api/client.ts` switches to `/companies`,
`/activities` and `/contacts/{id}/360`, takes the new list parameters, and drops the `getAllDeals`
200-row convenience from display paths (it stays only for `data/importExport/shared.ts`'s
`collectPages`). `fixtures/api/routes.ts` in the visual harness changes its CRM paths to
`/api/v1/companies` and `/api/v1/activities` with the new response fields in the fixture files
(`fixtures/api/crm/companies.json`, `contacts.json`, `deals*.json`, `summary.json`, `activities.json`)
in the same commit, or `contracts.test.ts` fails.

### 6.7 Tests

| Test (file::name) | Kind | Proves |
|---|---|---|
| `libs/platform-core/tests/test_listing.py` (`test_escape_like_literal_percent_underscore_backslash`, `test_unknown_sort_is_422_with_field_error`, `test_sort_always_ends_with_the_tiebreak`, `test_ids_over_100_is_422`, `test_malformed_id_is_422`, `test_tz_default_and_unknown`, `test_date_bounds_inclusive_end_in_asia_kolkata_and_new_york`) | unit | A20 |
| `services/{customer,deal,lead}/tests/test_list_vocabulary.py` (per list: `test_pages_of_equal_sort_values_neither_repeat_nor_skip` (120 rows, size 25), `test_sort_asc_and_desc_are_exact_reverses`, `test_unknown_sort_is_422`, `test_q_percent_matches_only_a_literal_percent`, `test_ids_only_return_rows_in_scope`, `test_owner_me_without_own_ref_is_empty`, `test_owner_filter_cannot_widen_own_scope`, `test_date_range_edges_in_org_timezone`, `test_default_list_uses_an_index_led_by_organization_id` (EXPLAIN)) | unit, negative-authz | C35, DD-6 |
| `services/deal/tests/test_summary_sql_parity.py` (`test_sql_summary_equals_the_python_reference_on_200_random_books`, `test_boundary_vectors_agree_in_sql_and_python` (value and probability `103/50`, `101/50`, `105/50`, `999/33`, `1/100`, `0/50`; win rate `1 of 8`, `3 of 8`, `1 of 3`, `0 decided`; run against the database, not only Python), `test_legacy_fields_move_only_at_exact_halves`, `test_multi_currency_by_currency_is_correct_and_legacy_fields_are_unchanged` (on a book with no exact half), `test_summary_has_no_row_cap_at_1000_deals`) and `services/deal/tests/test_domain.py::test_no_float_in_money_arithmetic` (an AST scan: no `/` and no `round(` in `weighted_minor`) | property | DD-9, C9 |
| `services/*/tests/test_noop_patch.py` (`test_noop_patch_writes_no_audit_row_and_no_event`, `test_real_change_still_writes_both`) | unit | §6.3.3 |
| `services/customer/tests/test_alias_routes.py`, `services/deal/tests/test_alias_routes.py` (identical bodies and status; OpenAPI `deprecated` flag; dashboard contract `GET /customers?status=customer&size=1` and `GET /deals/activities` shapes pinned) | contract | C17, C21, C23 |
| `services/customer/tests/test_contacts_search.py`, `services/lead/tests/test_lead_summary.py` | unit, negative-authz | C86, C88 |
| `libs/contracts/tests/test_openapi_problem_docs.py` (every CRM operation documents the statuses it can answer; every filter parameter has a type and description) | contract | A21 |
| `tests/infra/test_route_table_parity.py` (SP1's `test_the_crm_prefixes_are_identical_in_dev_and_prod` gains the two rows), `tests/infra/test_frontend_paths_are_routed.py`, and `docs/crm.md`'s service map with the two new prefixes (`test_crm_doc_matches_contracts.py`) | infra | C67, C68 |
| `frontend`: `pnpm --filter @opsgrids/product-crm types:check`; `client.test.ts` for canonical paths; `fixtures/contracts.test.ts` for the new mocks | contract | G9 |
| `services/*/tests/test_authz_matrix.py` rows for every new or aliased route; cross-tenant rows for `ids` | negative-authz, cross-tenant | §4.1 |

### 6.8 Rollout

Deploy order: platform-core change (every image rebuilds), customer, deal, lead and **app-gateway**
(its route table changed) in one `make deploy`, then publish the SPA (`APP=crm`). The aliases mean an
old bundle keeps working through the deploy and a rollback of the SPA is independent. Flags: none.
Reconcile: none. Memory: every total unchanged from the parent commit (§4.5).

### 6.9 Rollback

Code-only (`make edge-rollback`); the indexes are harmless to leave; the SPA bundle rolls back with
`make edge-web-rollback APP=crm`. Nothing is irreversible. Stop conditions: any CRM list slower than
baseline (the new indexes are the first suspect, not the sort), a 404 for `/companies` through the
gateway (the route-table row did not deploy), any dashboard Home error.

### 6.10 Acceptance criteria (mechanical)

1. `uv run pytest libs/platform-core -q` and `make test-services` pass; `make migration-roundtrip` passes.
2. `make openapi-check` passes and `libs/contracts/openapi/customer.json` has both `/api/v1/companies`
   and `/api/v1/customers` with the latter `deprecated: true`; the deal document has
   `/api/v1/activities`; every operation lists its problem responses
   (`libs/contracts/tests/test_openapi_problem_docs.py`).
3. `curl`-equivalent tests show `GET /customers?status=customer&size=1` and `GET /deals/activities`
   unchanged in shape.
4. `grep -rn "getAllDeals\|FULL_PAGE" frontend/product-crm/src --include='*.tsx'` returns nothing
   outside `data/importExport/`.
5. `pnpm types:check` is clean in `product-crm`; `make frontend-gate` includes the `frontend-types` row.
6. `make ui-visual` passes with only fixture-path changes in baselines (no pixel change expected).
7. `test_boundary_vectors_agree_in_sql_and_python` and `test_legacy_fields_move_only_at_exact_halves` pass
   against the real database; `grep -n "round(" services/deal/app/domain.py` finds only the funnel's `pct` line
   (a bar's share, not money: the one float left, DD-9) and nothing in `weighted_minor` or `win_rate_pct`.
8. The standing criteria of §4.5: S1 to S7 (S3 is not needed: no event changes) and S8 for the gateway rows.

## 7. R3.3 Shared UI foundations (no CRM behaviour change)

### 7.1 Purpose

Promote or introduce, once, the components the rebuilt CRM needs and that a second product already
has or will want, so `product-crm` never forks them (ADR-0001; the frontend rules, "An app may not
fork a component into itself"). Every export has a story and a test; no CRM screen looks different
afterwards except where the shared formatter corrects a non-INR amount (§7.5). Five other apps are
migrated onto the shared parts in the same release, because a promotion that leaves its forks behind
is a copy.

### 7.2 Data model and backend

None. The release reads two existing routes: `GET /organization` (any member, V19) through the new
`useOrgProfile`, and nothing else.

### 7.3 Component and hook contracts

Props use the registry's names exactly (G1-G8, G10, G12); the additive props that those rows carry from
this document are marked.

#### 7.3.1 `KanbanBoard` (G1) in `@opsgrids/ui`

```ts
export interface KanbanColumn {
  id: string; label: string;
  tone?: StatusTone;            // header dot, never the only carrier of meaning (U10)
  closed?: boolean;             // refuses drops and moves into it from the menu is allowed only if canMove says so
  summary?: ReactNode;          // "₹4.2L + $8K", supplied by the caller (never summed across currencies, U12)
  emptyLabel?: string;
  total?: number;               // additive: the server's count for this column, for "12 of 40"
}
export interface KanbanBoardProps<T> {
  columns: readonly KanbanColumn[];
  items: readonly T[];
  getItemId(item: T): string;
  getColumnId(item: T): string;
  renderCard(item: T, state: KanbanCardState): ReactNode;   // state: { dragging, moving, actions: ReactNode }
  canMove(item: T, toColumnId: string): boolean;
  onMove(item: T, toColumnId: string): Promise<void>;
  announce(item: T, fromId: string, toId: string, result: "moving" | "moved" | "failed"): string;
  moveMenuLabel(item: T): string;                           // "Move or edit Acme renewal"
  getExtraActions?(item: T): RowAction[];                   // additive: Edit, Delete for projects
  onLoadMore?(columnId: string): void;                      // additive: per-column "Show more"
  loadingColumnIds?: ReadonlySet<string>;                   // additive
  readOnly?: boolean;                                       // additive: no menu, no drag
}
```

Behaviour: one `<section aria-label={label}>` per column inside a horizontally scrolling region
(`snap-x` on a phone, column width 280 px, as both forks); the header shows label, count (`"12"` or
`"12 of 40"` when `total` exceeds the loaded items) and `summary`. **Every card carries a move menu**
(`RowActions` with `label={moveMenuLabel(item)}` and `inlineLimit={1}`, the value both forks use,
`frontend/product-projects/src/ui/KanbanBoard.tsx:68`, `frontend/product-pulseboard/src/ui/KanbanBoard.tsx:59`):
"Move to {label}" for each other column for which `canMove` is true, then `getExtraActions`; a move into a
column `canMove` refuses is shown **disabled with its reason** (`disabledReason`), not hidden, so the
keyboard user learns why. `1` and not `0` because `RowActions` computes `overflowing = visible.length >
inlineLimit`, `inline = visible.slice(0, inlineLimit - 1)` and `overflow = visible.slice(inlineLimit - 1)`
(`frontend/opsgrids-ui/src/components/RowActions.tsx:93-95`): with `0` that is `slice(0, -1)` and `slice(-1)`, so
every action but the last would render as an inline button and only the last would sit in the menu. With `1`
every action goes into the menu whenever the card has two or more visible actions, which every CRM card has
(a pipeline always has an open, a won and a lost stage); a generic board whose card has exactly one visible
action shows it as one inline button named "Move to {label}", still a real button, and `KanbanBoard.test.tsx`
pins both cases.
HTML5 drag stays as an enhancement (pointer only; closed columns ignore a drop and keep the
"drop target" label text). `onMove` is awaited: while pending the card has `aria-busy` and
`moving: true`; the **consumer** applies the optimistic change and reconciles; a rejection is announced
with `announce(..., "failed")` and the board restores nothing itself (the consumer's rollback changes
`items`). The polite live region (`useAnnounce`) says `announce(...)` for `moving` and the settled
result once. An item whose `getColumnId` matches no column is rendered in a trailing "Other" column
instead of throwing (the CRM board threw today: `frontend/product-crm/src/data/derive.ts:70`,
`ui/KanbanBoard.tsx:148-149`). Focus: after a move the card keeps focus by `getItemId`. Stories:
Default, Empty columns, Closed columns, Unknown column ("Other"), Per-column "Show more", Moving and
failure, Read-only, Phone width. It replaces the three forks
(`frontend/product-crm/src/ui/KanbanBoard.tsx`, `frontend/product-projects/src/ui/KanbanBoard.tsx`,
`frontend/product-pulseboard/src/ui/KanbanBoard.tsx`); Projects and PulseBoard adopt it in this release
(their stage sets become `columns`, their Edit and Delete become `getExtraActions`); **CRM adopts it
in R3.4**, so CRM's own file survives one more release.

#### 7.3.2 `DetailList`, `PropertyRow` (G2)

`DetailList { items: { label: string; value: ReactNode; hint?: string }[]; columns?: 1 | 2 }` renders a
`<dl>`; `PropertyRow { label: string; children: ReactNode; onEdit?: () => void; editLabel?: string }`
renders one `<dt>/<dd>` pair with a pencil `button` named `editLabel ?? "Edit {label}"` when `onEdit` is
set. The editor itself (input, Save, Cancel, Enter and Escape) is the app's `InlineEditField`
(CRM-local, R3.5), composed from `PropertyRow` and `TextField`. Adopted by CRM in R3.5 (replacing the
three local `Fact` rows). Stories: one and two columns, with edit.

#### 7.3.3 `Customer360View`, `Customer360ViewModel` (G5)

`Customer360ViewModel` is a structural interface in ui that the generated client type is assignable to
(`customer`, `crm`, `orders`, `finance`, `support`, `channels`, `timeline`, `unavailable: string[]`).
`Customer360View {data, revealSlot?, onOpenSection?(name), sectionLabel(name)}` **never fetches**. It
renders the profile (name, status, company and city, owner, tags, the masked phone with `revealSlot`
beneath it, the masked or absent email), then one row per section in a fixed order; a section that is
`null` or named in `unavailable` shows "Not available", never an error; a section name it does not
know (R4.4's `delivery`) renders through `sectionLabel` and the same fallback. Money goes through the
shared `formatMoney`. The fetch, the 402 and 403 messages and the collapse card stay in the app:
`product-engage`'s `Customer360Panel` (528 lines) keeps its card, live region and `LoadError` and
replaces its `Profile`, `SectionRow` and `SectionBody` with the view; its existing tests
(`Inbox360.test.tsx`) must pass unchanged. Stories: full, partial (`unavailable`), phone-only contact
(no email), long names.

#### 7.3.4 Reveal (G6, G7), with the props moved (DD-10)

`useRevealedField` moves to `@opsgrids/client` (`stepup/useRevealedField.ts`) with its wording module
(`stepup/revealMessages.ts`, from `reveal/messages.ts`, `RevealSubject` gains `"employee"`) and loses
the `USE_FIXTURES` import: `frontend/product-crm/src/reveal/useRevealedField.ts:31, 130` short-circuits
to `load(NO_STEP_UP)` in fixtures mode; fixtures mode survives until R3.4, so CRM wraps the shared hook
with a step-up object whose `run` calls `load` directly while `USE_FIXTURES` is true (deleted with
fixtures mode). The
hook takes an optional `visibleSeconds` (default 30) and returns `RevealedField {value, notice, pending,
justHidden, reveal, hide}` as today.

`RevealableField` moves to `@opsgrids/ui` and is **presentational**:

```tsx
<RevealableField
  kind="phone" | "email" | "text"      // text: label and noun come from props, no link
  label? noun?                           // required when kind="text" (HRMS: "PAN", "Aadhaar", …)
  masked={contact.phone} canReveal={canReveal}
  field={revealedField}                 // the useRevealedField result, structurally typed (RevealedFieldView)
  stepUpIdle={stepUp.state === "idle"}  // disables a second reveal while a code is out
/>
```
`REVEAL_VISIBLE_SECONDS` is exported by ui (30) and, as `DEFAULT_REVEAL_VISIBLE_SECONDS`, by client; a
test in `product-crm` asserts they are equal (the twin-by-structure precedent, `useStepUp.ts` docstring).
The component keeps its focus return, its single polite live region under the row, the `tel:` and
`mailto:` links for revealed phone and email, and the 30 s auto-hide. REG G6 carries this change: the
`stepUp`, `resetKey` and `load` props move to the hook call because ui may not import client.
**HRMS migration**: `product-hrms/src/routes/EmployeeDetail.tsx:219-300` replaces its per-field
`unmaskEmployeePii` calls, its warning alert and its `revealed` state with one `useStepUp()`, one
`<StepUpDialog />`, and a `useRevealedField` per field (`personal_email` as `email`, `personal_phone` as
`phone`, `pan`, `aadhaar`, `uan`, `esi`, `bank_account`, `date_of_birth` as `text`); `load` is
`unmaskEmployeePii(id, { fields: [key] }, revealRequest(headers))`. This is an intended behaviour
change for HRMS: the one-time-code dialog replaces the "A second factor is required" warning alert
(`EmployeeDetail.tsx:271-274`; the docstring above `PiiSection` says no login flow issues a step-up
yet), and the route's `amr={"otp"}` (`services/employee/app/api/routes.py:73`)
is satisfied by a step-up token. The roughly 65 CRM reveal tests move with the code; the HRMS tests are
rewritten onto the shared kit.

#### 7.3.5 `SavedViewMenu` (G8, component only)

`SavedViewMenu { views: SavedViewItem[]; activeId: string | null; dirty: boolean;
onSelect(id | null); onSave(name, visibility); onUpdate(id); onDelete(id); canShare: boolean }` with
`SavedViewItem { id, name, visibility: "private" | "org", mine: boolean, system?: boolean }`. A button
labelled "Views" opens a `Menu`: "All" (id `null`), system presets (links, not stored), the person's
views, shared views; a dot with a visually hidden "unsaved changes" when `dirty`; "Save as view…" opens
a small dialog (name 1-80 characters, visibility only when `canShare`); "Update view" when the active
view is the person's and `dirty`; "Delete view" through `ConfirmDialog`. Stories: empty, populated,
dirty, shared, phone width. `useSavedViews(entity)` (client) ships with its routes in R3.8 (REG G8: the
component is R3.3, the hook R3.8).

#### 7.3.6 Money (G4), org profile (G19), timeline (G20), command menu (G12)

* `@opsgrids/ui lib/format.ts` exports `formatMoney(minor, currency, { precise?, compact?, locale? })`,
  `toMajor`, `currencyExponent(currency)` (from `Intl.NumberFormat(...).resolvedOptions().maximumFractionDigits`,
  cached, 2 for an unrecognised code), `parseMinor(input, currency): {ok, minor} | {ok: false, message}`
  and `minorToInput(minor, currency)`, with the signature of `frontend/opsgrids-internal/src/lib/money.ts:39`
  (default locale `en-IN`). Tests: INR and USD identical to today's CRM output; JPY has no decimals and
  rejects a fractional input; KWD has three; `parseMinor("1,20,000.5", "INR")` is 12000050; no float
  ever carries money (integer arithmetic on the parsed string). The CRM's `lib/format.ts` money and
  `lib/money.ts` functions are deleted and their callers import the shared ones (INR without paise as
  before: `precise` is off by default).
* `@opsgrids/client` `useOrgProfile()` (new, G19) reads `GET /organization` once per session and
  returns `{ timezone, currency, locale }` with the `Asia/Kolkata` / `INR` / `en-IN` defaults on a
  failed read (status `fallback`); it exists so currency and locale defaults stop being literals
  (`DealIn.currency` default, `useOrgSettings`, `formatMoney` locale). The pattern is HRMS's
  `frontend/product-hrms/src/lib/zone.ts:2, 51`.
* `Timeline` (G20) gains `order?: "asc" | "desc"` (default `"asc"`; entries are rendered as given, the
  prop only chooses which end `collapseAfter` keeps) and `footer?: ReactNode` for "Load more". The CRM
  feed is newest first.
* `CommandMenu` and `AppShell` (G12): `CommandMenuConfig` gains optional
  `results?: { onQueryChange(query: string): void; status: "idle" | "loading" | "success" | "error";
  items: CommandMenuItem[]; partial: boolean; hint?: string | null }`. The menu calls `onQueryChange`
  with the trimmed query on every change (debouncing is the hook's), lists `results.items` after the
  matched static items under their own `group`, shows a polite status line ("Searching…", "Some results
  may be missing", "No matches", or `hint`), and keeps the existing last-resort fallback row. Omitting
  `results` changes nothing. `useGlobalSearch` gains a **Leads** source (`/leads?q=&size=5`, href
  `/leads/:id`, sublabel status and source) and the identifier guard of §4.4.6 (returns `status:
  "idle"` with `blocked: "identifier"`); its Contacts href changes from `/contacts?q=` to
  `/contacts/:id` **in R3.5**, when that route exists (REG G12: the contact result is R3.5, not
  R3.3). CRM wires it in R3.4.

### 7.4 Tests

| Test | Kind | Proves |
|---|---|---|
| `frontend/opsgrids-ui/src/stories.test.ts` | contract | every new ui export has a story |
| `KanbanBoard.test.tsx` (`every_move_to_item_is_inside_the_menu_and_none_is_an_inline_button` (three columns, with and without `getExtraActions`), `a_card_with_one_visible_action_shows_one_inline_button`, `moving_through_the_menu_calls_onMove_once`, `a_closed_column_refuses_a_drop_and_a_menu_move_is_disabled_with_a_reason`, `announcements_for_moving_moved_failed`, `an_unknown_column_renders_in_Other_and_does_not_throw`, `focus_stays_on_the_moved_card`, `read_only_has_no_menu_and_no_drag`, `column_header_says_12_of_40`) | unit, a11y | G1 |
| `Customer360View.test.tsx` (`null_and_unavailable_sections_say_Not_available`, `an_unknown_section_name_is_rendered_not_dropped`, `phone_only_profile_has_no_email_line`, `money_is_per_currency`), and Engage's `Inbox360.test.tsx` unchanged | unit | G5 |
| `RevealableField.test.tsx`, `useRevealedField.test.tsx`, `revealMessages.test.ts` (the moved CRM suites), `reveal_visible_seconds_parity.test.ts` in `product-crm`, HRMS `EmployeeDetail.reveal.test.tsx` | unit | G6, G7 |
| `SavedViewMenu.test.tsx` (`dirty_dot_has_text`, `share_option_only_when_canShare`, `delete_confirms`) | unit | G8 |
| `format.money.test.ts` (INR, USD, JPY, KWD, compact, parse round trips, no float) | unit | G4 |
| `Timeline.test.tsx` (`desc_order_collapse_keeps_the_head`), `CommandMenu.test.tsx` (`results_render_after_static_items`, `status_line_is_announced`, `without_results_behaviour_is_unchanged`), `useGlobalSearch.test.tsx` (`leads_source`, `identifier_guard_makes_no_request`) | unit | G12, G20 |
| Projects and PulseBoard board tests rewritten onto the shared component; their existing assertions kept | unit | migration |
| `make frontend-boundaries` (dependency-cruiser: no `ui-imports-no-client`, `client-imports-no-ui`, `apps-are-independent`, `ui-only-via-exports` violation) | architecture | the walls |
| Visual: `pnpm visual:check` renders the new stories at six renderings with axe | visual, a11y | no new violation; baselines for the new stories only |

### 7.5 Rollout and rollback

Frontend-only. Publish, from the checkout of the merged ref, `make edge-web HOST=… CONFIRM=… APP=<key>`
for `crm`, `engage`, `hrms`, `projects`, `pulseboard`. No deploy, flag, reconcile or memory effect.
The one visible CRM difference: a deal in a non-two-decimal currency shows its correct amount. Rollback
is `make edge-web-rollback APP=<key>` per app; nothing is irreversible. Stop condition: any reveal
failure rate above baseline on CRM or HRMS.

### 7.6 Acceptance criteria (mechanical)

1. `make frontend-gate` passes (typecheck, lint, boundaries, tests, build, audit, licenses).
2. `ls frontend/product-projects/src/ui/KanbanBoard.tsx frontend/product-pulseboard/src/ui/KanbanBoard.tsx`
   reports no such file; `frontend/product-crm/src/ui/KanbanBoard.tsx` still exists (R3.4 deletes it).
3. `ls frontend/product-crm/src/reveal` contains no `RevealableField.tsx` or `useRevealedField.ts`;
   `grep -rn "USE_FIXTURES" frontend/opsgrids-client/src frontend/opsgrids-ui/src` returns nothing.
4. `grep -rn "formatMoney\b" frontend/product-crm/src` shows only imports from `@opsgrids/ui`.
5. `pnpm tokens:baseline` leaves `frontend/opsgrids-config/token-baseline.json` equal or smaller.
6. `make ui-visual` passes after reviewed `pnpm visual:update` for the new stories only.
7. The shared board's menu test passes (`every_move_to_item_is_inside_the_menu_and_none_is_an_inline_button`,
   §7.4) and the standing criteria of §4.5 hold: S1, S2 and S7 (no migration, event or route changes).

## 8. R3.4 CRM shell, information architecture, settings hub and list rebuilds

### 8.1 Purpose

The first release a user sees (it stands on R2.5, R2.7 and R2.8, so on D-5, D-6, D-8, D-9, D-10, D-21 and D-38,
all pending). The shell becomes route-registry driven with lazy routes and honest
navigation; a settings hub gives every setting that exists a home; Leads, Deals, Companies, Contacts
and the activity log are rebuilt on the shared components with URL-synced filters, server sort and
paging, selection and phone cards. The in-client fixtures mode, the dev login, the page-only CSV export
and the per-card stage select are deleted **after** their replacements (D-23); the placeholder pages,
their nav entries and the seven dead `derive.ts` rollups are already gone, removed by SP1/R1.2b (SP1 DD-5),
which also created the 404 page this release restyles. Detail pages are not rebuilt here (R3.5, R3.7); they keep working inside the legacy boundary of
§8.6.1 and change only where a deleted module forces it (`OwnerBadge` reads the owners roster, the
shell no longer wraps them in the directory).

### 8.2 Data model

One deal revision `<rev>_pipeline_settings` (B27, R3.4's columns; later releases add the rest):

```sql
SET LOCAL lock_timeout = '5s';
-- TenantBase columns, then:
CREATE TABLE deal.pipeline_settings (
    stale_after_days        INTEGER     NOT NULL DEFAULT 14,
    closing_soon_days       INTEGER     NOT NULL DEFAULT 7,
    follow_up_default_hours INTEGER     NOT NULL DEFAULT 24,
    deal_number_prefix      VARCHAR(8)  NOT NULL DEFAULT 'D',
    timezone                VARCHAR(64) NOT NULL DEFAULT 'Asia/Kolkata',   -- B65
    CONSTRAINT ck_pipeline_settings_stale    CHECK (stale_after_days BETWEEN 1 AND 365),
    CONSTRAINT ck_pipeline_settings_closing  CHECK (closing_soon_days BETWEEN 1 AND 90),
    CONSTRAINT ck_pipeline_settings_followup CHECK (follow_up_default_hours BETWEEN 1 AND 720),
    CONSTRAINT ck_pipeline_settings_prefix   CHECK (deal_number_prefix ~ '^[A-Z]{1,8}$')
);
CREATE UNIQUE INDEX uq_pipeline_settings_org ON deal.pipeline_settings (organization_id);
-- ENABLE + FORCE RLS and tenant_isolation (§4.1); TENANT_TABLES in deal's conftest.
```

The row is created lazily (`INSERT … ON CONFLICT (organization_id) DO NOTHING`, then read, the shape
of `lead._get_or_create_rule`, `services/lead/app/services.py:145-166`); a read with no row returns the
defaults without writing. The timezone name is validated with `zoneinfo` in the application. Downgrade:
drops the table; refuses unless `OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE=1` while a row exists.

### 8.3 Backend contract

| Route | Permission, floor | Request | Response | Errors | Named negative-authz test |
|---|---|---|---|---|---|
| `GET /deals/settings` (C11) | `crm.pipeline.deal.read`; organization filter only (reference data, like `GET /customers/owners`) | none | `PipelineSettingsOut {stale_after_days, closing_soon_days, follow_up_default_hours, deal_number_prefix, timezone, materialised}`; defaults and `materialised: false` when no row, never a write | 401, 402, 403 | `test_settings_read_requires_deal_read` |
| `PUT /deals/settings` (C11) | `crm.pipeline.settings.write`, `minimum=Scope.ORG` | `PipelineSettingsIn` (`extra="forbid"`, every R3.4 field) | `PipelineSettingsOut`; one audit row `pipeline_settings.updated` (before and after, non-personal) | 422 `invalid_timezone`, range errors per field | `test_settings_write_below_org_floor_is_403`, `test_settings_write_without_settings_write_is_403` |
| `GET /deals/close-reasons` (C12) | `crm.pipeline.deal.read` | `outcome?`, `include_archived?` | `CloseReasonListOut {materialised, reasons: [{id, outcome, key, label, position, archived}]}`; virtual defaults with deterministic ids when no rows (DD-2) | 401, 402, 403 | `test_close_reasons_read_requires_deal_read` |
| `POST /deals/close-reasons` (C12) | `crm.pipeline.settings.write`, ORG | `{outcome, key, label, position?}`; `key` matches `^[a-z0-9_]{1,40}$` and is immutable | 201 `CloseReasonOut`; materialises the defaults first under the organization lock | 409 `duplicate_name`, 422 | `test_close_reason_create_below_org_floor_is_403` |
| `PATCH /deals/close-reasons/{reason_id}` (C12) | same | `{label?, position?}` (`key` or `outcome` is a 422: `extra="forbid"`) | `CloseReasonOut` | 404, 422 | `test_close_reason_patch_cross_tenant_is_404` |
| `POST /deals/close-reasons/{reason_id}:archive` (C12) | same | none | `CloseReasonOut` (archived); deals that reference it keep it | 404 | `test_close_reason_archive_requires_settings_write` |

Consumed, not built here: `GET /deals/stages` (C1), `POST /deals/stages`, `PATCH /deals/stages/{id}`,
`POST /deals/stages:reorder`, `POST /deals/stages/{id}:archive` (C2-C5, R2.5, permission
`crm.pipeline.stage.write`, ORG floor); `GET|POST|PATCH /customers/owners…`, `GET /customers/owners/candidates`
(C25-C28, R2.6); `GET|PUT /leads/assignment-rule` (unchanged); `GET /organization` (account).

### 8.4 Domain rules

1. **Number display**: `${deal_number_prefix}-${number}`; changing the prefix changes every display and
   no stored number.
2. **Settings defaults** are the code defaults until the first `PUT`; the SPA treats `materialised:
   false` as "defaults in force", not an error. The first `PUT` pre-fills `timezone` from
   `useOrgProfile()` in the UI (a prefill, not server logic).
3. **Terminal moves need confirmation** (this release): a move into a column whose outcome is `won` or
   `lost`, from the board or the list, opens `ConfirmDialog` ("Mark {deal} as lost? A closed deal can't
   be moved again.") before `POST /deals/{id}/stage`; R3.5 replaces it with `CloseDealDialog` (reason
   and note).
4. **Stages are data**: no list, board, form or import module imports a stage constant; the constants
   file `data/stages.ts` and the `STAGE_TONE` map stay **only** for `DealDetail` and `CompanyDetail`, which
   R3.5 rebuilds and whose rebuild deletes them (§8.6.5). An unknown stage key (a deal on an archived stage) renders under
   "Other" on the board and as its raw key in a neutral badge in the list.

### 8.5 Consumers and idempotency

None. The SPA's name resolver is idempotent by key (§8.6.1).

### 8.6 UI

#### 8.6.1 Shell, providers and data layer

`App.tsx`: the nav, `quickActions`, `bottomNavExtra` and `search` come from the route registry and
the caller's grants (§4.4.1). `search` is `{ placeholder: "Search deals, companies, contacts and
leads", fallback: …, results }` where `results` is fed by `useGlobalSearch` (R3.3). The banner keeps
`GrantsStatusBanner`; `DirectoryStatusBanner` (a function in `App.tsx:42`, not a file) moves out of
`App.tsx` into `routes/legacy.tsx`, the legacy boundary described below. A
session provider, `CrmSettingsProvider`, exposes `useStages()` (`GET /deals/stages`, C1),
`useOrgSettings()` (`GET /deals/settings`) and `useOwners()` (`GET /customers/owners`); each read happens
**on first use** by a mounted screen and is cached for the session, so a screen that needs no stages never
asks for them. `useOrgProfile()` (`GET /organization`) is called only by the forms, the money formatter's
locale and the Workspace page. `reload()` re-reads stages and settings after a settings write. A failure
of stages or settings does not blank the app: screens that need stages show `LoadFailure` with retry; the
rest render.

**The legacy boundary** (DD-40). `DirectoryProvider` no longer wraps the app. `routes/legacy.tsx` wraps only the
three detail routes that are not rebuilt yet (`/deals/:id`, `/companies/:id`, `/leads/:id`) in it, so no
shell, list or form request waits on the three 200-row directory calls. R3.5 removes the first two
wrappers when it rebuilds those pages and R3.7 removes the last one and deletes the provider (§8.6.5).

`data/useEntityNames.ts` resolves ids to names: `useEntityNames("company" | "contact" | "owner")`
collects the ids a screen renders, batches them into one `GET /companies?ids=…&size=100` (resp.
contacts) per 100 ids after the list loads, keeps an in-memory map for the session, and returns
`name(id)` plus `status(id)` in `pending | resolved | missing`. `pending` renders a skeleton, `missing`
(not in scope or deleted) renders "No access", never "Unassigned" for an id the server has not answered.
Owners are the roster already loaded. Pickers (company, contact, owner) are `Combobox` with
`loadOptions` calling `GET /companies?q=&size=10` and `GET /contacts?q=&company_id=&size=10`
(§4.4.6: no identifier is sent as `q`).

#### 8.6.2 Screen catalogue (lists)

All lists: `PageHeader` with the primary action; `FilterBar` (search, selects, chips, Clear all, phone
bottom sheet); `DataTable` with `loading`, `errorState`, `emptyState`, `onRowClick`, `mobileCard="auto"`,
`selection`, `bulkActions`, `sort`/`onSort`; numbered `Pagination` with `size` 25, 50 or 100; the
import and export button (`ImportExportButton`, ADR-0022) with the same parsed filters; state per §4.4.2
and URL per §4.4.3; document title from the registry.

| Screen | Columns (mobile placement) | Filters | Sort keys | Row actions | Bulk | Primary action and empty copy |
|---|---|---|---|---|---|---|
| **Leads** `/leads` | Lead and company (title), Status (status), Source, Owner, Captured | search (name or company), status (counts shown in the options from `GET /leads/summary`), owner (`me` chip and select), source, created range | `-created_at` default, `name`, `status` | Open; Qualify; Disqualify (confirm); Convert (qualified only, confirm; success toast links to the created records) | Export selected | "New lead". Empty: "No leads yet. Add one, import a sheet, or set up a web form." (the last only when the caller can write lead settings and R3.7 has shipped) |
| **Deals** `/deals` | Board: cards. List: Number, Deal and company (title), Stage (status), Value, Probability, Owner, Close date, Last activity | search (name or number), owner, source, company (`Combobox`), closing range chips | `-value` default, `name`, `close_date`, `created_at`, `probability`, `stage` | Open; Move stage; Log activity; Delete (needs `crm.pipeline.deal.delete`, confirm) | Export selected | "New deal". Empty: "No deals match" or "Open your first deal" |
| **Companies** `/companies` | Company and legal name (title), Status (status), Industry, City, Owner, Created | search, status, owner, created range | `name` default, `created_at`, `status`, `annual_revenue` | Open; Edit; Delete (needs delete code, confirm; 409 explains live contacts) | Export selected | "New company". Empty: "Add your first company or import a sheet" |
| **Contacts** `/contacts` | Name and title (title), Company, Email or "No email", Phone (masked), Status (status), Owner, Last activity | search (name or title), status, company, owner | `name` default, `created_at`, `last_activity_at` | Open (a `Drawer` with `ContactSummaryPanel` until R3.5, then the page); Edit; Delete | Export selected | "New contact", and **"Find by email or phone"** which opens the lookup dialog (`POST /contacts:search`, results in memory) |
| **Activity log** `/activities` | a day-grouped feed with `RecordTimeline`: type badge, subject, linked record, owner, time | type, owner (`me` chip), date range | newest first | none (append-only) | none | "Log activity" (the `task` type is no longer offered, DD in §8.6.5), `LoadMore` appends 25 and announces |

Detail notes.

* **Leads**: the quick status select inside the page is the `FilterBar` select; counts in option labels
  come from one `GET /leads/summary` (replacing the five `size=1` calls). Qualify and Disqualify are
  `PATCH /leads/{id}` status moves; there is no structured disqualification reason in this programme
  (none is registered), so Disqualify is a confirm. Convert shows the existing 409 and 422 sentences.
* **Deals board**: columns are the active stages from `useStages()` in position order, closed columns
  last; each column loads `GET /deals?stage=<key>&size=25&sort=-value` with the board's filters and the
  header count and per-currency sums come from one `GET /deals/summary` with the same filters
  (`by_currency[].funnel`). Closed columns sort `-closed_at` and are collapsed to 10 cards with "Show
  more". Cards: number and name (link), company name (resolver), value, probability, close date (overdue
  chip), owner avatar, and the move menu. A move is optimistic: the consumer updates its own items,
  calls `POST /deals/{id}/stage`, reconciles the one deal from the response and calls `refresh()`, and
  a rejection restores the card and announces the failure. Terminal moves ask first (§8.4 rule 3).
* **Deals list**: `Tabs variant="segmented"` Board and List with visible text labels at every width and
  the tab panel wired (`children`), fixing the two recorded accessibility entries; layout is `?layout=`.
* **Companies and Contacts**: the page-local sort and the "most contacts" sort over a 200-row directory
  are gone; sort is the header. Contacts no longer expands in place: row click is the drawer.
* **Contact form**: Name\*, Company\* (`Combobox`), Email, Phone (E.164 with country code, helper text
  "Include the country code, for example +91"), Title, Status, Owner\*. Validation: one of email or phone
  is required ("Give an email address or a phone number", mapped to `needs_email_or_phone`); a 409 shows
  "A contact with that {email|phone} already exists" with an **Open the existing contact** link from
  `existing_id`.
* **Company form**: Name\*, Legal name, Owner\*, Status, Industry, Size band (controlled list), City,
  Country, Annual revenue and currency, **GSTIN** (optional; the SPA validates only length and charset,
  the server verifies the checksum and returns the state name shown under the field as "27 ·
  Maharashtra"). Addresses and further GSTINs are edited on the company page (R3.5).
* **Deal form**: Name\*, Company\* (`Combobox`), Primary contact (`Combobox` scoped to the company),
  Owner\*, Value and Currency (`Select`: the organization's currency first, then INR, USD, EUR, GBP, AED,
  SAR, SGD, AUD, CAD, JPY, CNY, KWD), Stage (active stages, default the first open one), Probability
  (pre-filled from the stage's default, editable), Close date\*, Source (`Combobox` with Referral,
  Inbound, Outbound, Event, Partner, Website, WhatsApp, Other, and free text).
* **Lead form**: the "Source" select is removed (a hand-typed lead is `manual`; `import` is stamped by
  the importer); the detail shows source read-only.
* **Log activity form**: types Call, Email, Meeting, Note (the server still accepts `task`; existing
  `task` rows render with the label "Task (logged)"); `occurred_at` defaults to now **in the
  organization's timezone** (`useOrgSettings().timezone`, not the `Asia/Kolkata` literal at
  `frontend/product-crm/src/forms/ActivityFormDialog.tsx:30-31`).

#### 8.6.3 Settings hub and pages (Pipeline and Win and loss reasons pending D-5 and D-6; Sales team pending D-7 and D-9)

`/settings` is a grid of cards, each showing a title, one sentence, a live summary (`6 stages`, `14
reasons`, `Round-robin · 3 reps`, `5 reps`, `Asia/Kolkata`) and "Open"; a card the caller can only
read carries a "View only" tag; a card whose page needs a code the caller lacks and cannot even read is
absent. Pages (all under `PageHeader` with a back link to the hub):

| Page | Contents and states |
|---|---|
| **Pipeline** `/settings/pipeline` (pending D-5, D-6) | A table of stages in order: position, name, outcome badge (Open, Won, Lost), probability, tone preview (`StatusBadge`), status, and a deal count **only when `GET /deals/stages?include=counts` returns `deal_count`**: the API honours the parameter only for a caller whose `crm.pipeline.deal.read` grant is ORG and counts live deals only (REG C1), so for any other caller the column is omitted. A "Show archived" switch. **Edit** (label, tone from the five tones with previews, probability for open stages only); **Move earlier** and **Move later** through `RowActions`, each `disabled` with a reason at the ends and where a closed stage would pass an open one ("Won and lost stages stay last"); **Archive** opens a dialog that names the count when the table has one and asks for `move_open_deals_to` when the stage holds open deals; with no count the dialog is worded without one ("Deals still in this stage move to the stage you choose") and an open stage always shows the select, required, because the server requires a target whenever the stage holds any deal (REG C5). Archive is `disabled` with a reason when it is the last active open, won or lost stage; **Add stage** (label, outcome, tone, probability, "after" select). A caller without `crm.pipeline.stage.write` sees the table and a banner "You can view stages. Ask an administrator to change them." and no controls. Writes reload stages; failures map onto fields; a 409 shows its sentence |
| **Win and loss reasons** `/settings/reasons` | `Tabs` Lost and Won, each a `DataTable`: reason, status; actions Rename, Move earlier or later, Archive; "Add reason" (label, immutable key shown read-only after save). While the lists are the virtual defaults a notice says "These are the default reasons. Changing any of them saves your own list." |
| **Workspace** `/settings/workspace` | One form: Timezone (searchable `Combobox` over the IANA names), Stale after (days), Closing soon within (days), Default follow-up due (hours), Deal number prefix with a live preview "D-42". Dirty guard; Save needs `crm.pipeline.settings.write`; a read-only caller sees disabled fields and a note |
| **Lead assignment** `/settings/assignment` | Mode (Round-robin or Manual) and, for round-robin, the rotation: a list of every rep with a checkbox (in rotation) and Move up and Move down; inactive reps are listed with the tag "Inactive, skipped" and cannot be ticked (the service refuses them, `set_assignment_rule`, `services.py:169-207`). Copy: "New leads go to the next active rep in this list." The page shows no prediction of the next rep (the cursor is relative to the active subset). Save needs `crm.lead.lead.write` at ORG (service-level check, `services.py:181-182`); a lower scope sees it read-only |
| **Sales team** `/settings/team` (pending D-7, D-9) | A `DataTable` of reps (name, email, role label, Active or Inactive, "Linked" when `membership_id` is set); **Add team members** lists candidates from `GET /customers/owners/candidates` with checkboxes and a role label, and calls `POST /customers/owners {membership_id, role}`; **Edit** the role label; **Deactivate** explains the 409 ("Reassign their companies and contacts first"); **Reactivate**. Needs `crm.book.owner.write` (ORG); read-only for others |

The "Lead sources and forms" card and page arrive in R3.7b (their routes, C39 and C40, are R3.7's), and
R3.7a adds the SLA card to the Lead assignment page (§11.6.3).

#### 8.6.4 Overview, login and not-found

Overview keeps its layout in this release and changes its **data**: the four KPI tiles read
`GET /deals/summary` (`by_currency`, one line per currency, never summed) and `GET /leads/summary`
(replacing five calls); "Pipeline by stage" is the summary funnel; "Needs attention" becomes an
**overdue deals** list from `GET /deals?outcome=open&close_to=<yesterday>&sort=close_date&size=6` with
its `total` as the count (stale joins in R3.6 through `attention`); "Won this month" is dropped until
`closed_at` filters exist in R3.6; recent activity is `GET /activities?size=6`. `getAllDeals`, `data/derive.ts`
with its `derive.test.ts` (SP1/R1.2b already removed its seven dead rollups and left `openDeals`,
`dealsByStage` and `needsAttention`, whose two importers are rebuilt here), `NeedsAttention.tsx` and the
`Summary.trend` mapping are deleted.
`/login`: `RedirectToCentralLogin` when `VITE_AUTH_ORIGIN` is set; otherwise `DevSignIn`, a lazy
component imported only behind `import.meta.env.DEV` (about 60 lines, tokens only, no prefilled
credentials, no decoration), absent from a production build. `NotFound` (`routes/NotFound.tsx`, **created by
SP1/R1.2b**, the one 404 component): R3.4 restyles it with the heading "That page doesn't exist" and an
`EmptyState` with a link to Overview, and creates no second component.

#### 8.6.5 Replace-then-delete ledger

Each row is one removal, its live users, what replaces it, the gate that must be green before the
delete, and the proof it is gone. The PRs merge in this order inside the release: replacements, then
consumers moved, then deletions (so every intermediate commit passes `make check`).

| Removal | Live users today | Replacement | Gate before deleting | Proof after |
|---|---|---|---|---|
| *(not this release's: SP1/R1.2b removes it first, SP1 DD-5 and class 1 row 8)* `routes/ComingSoon.tsx`, the `/reports`, `/integrations`, `/settings`, `/help` placeholder routes (`main.tsx:69-119`) and their nav entries (`useNavGroups.ts:55, 61`) | none after R1.2b | `routes/NotFound.tsx` (R1.2b); R3.4 adds `/settings` and R3.8 adds `/reports` as **new** routes | SP1's evidence (no link in the tree targets the removed paths; the dashboard and Engage link only to `/contacts`, `product-engage/src/lib/dashboard.ts:33`) | R3.4 keeps one regression check only: `grep -rn "ComingSoon" frontend/product-crm/src` returns nothing; the registry test (§8.7) passes |
| In-client fixtures mode: `USE_FIXTURES` and its branches in `api/client.ts:84-87`, `api/fixtures.ts`, `api/client.fixtures.test.ts` (17 tests), `RevealableField.fixtures.test.tsx`, `FIXTURE_GRANTS` and the fixture identity in `auth/AuthProvider.tsx`, `FIXTURE_NOW_ISO` in `lib/format.ts`, the OWNERS import in `lib/subject.ts`, README "Data" section | those tests, the README's offline instructions, `tools/demo/seed_crm.py`'s "one-for-one mirror" claim, designers working offline | typed builders and `stubApi` over `fetch` for every test that used the fixture client; the contract-validated page fixtures for the visual harness; the seeded demo organization for stateful work (DD-12); a frozen-clock helper (`src/test/clock.ts`, `freezeNow(iso)` over vitest's fake timers) in place of `FIXTURE_NOW_ISO`, after which `nowIso()` returns the real clock; the README rewritten from the route registry. SP1 lists the builders, the clock and the dev sign-in as R3.3's (SP1 §5.4 class 2); they ship here, with the deletions they replace, and §17 records the deviation | every former fixture-client test is re-expressed against `stubApi` (same assertions), and every test that relied on the frozen instant calls `freezeNow`; `tools/demo/seed_crm.py` regenerated with dates relative to today, leads in every status, an assignment rule, sealed phones via `raw_phone`, reserved `example` domains and reps created through activation (H7) | `grep -rn "USE_FIXTURES\|fixtures\b" frontend/product-crm/src` is empty; `client.ts` loses its fixture half (about 500 of 1,027 lines) |
| `routes/Login.tsx` (293 lines, 21 offenders) | dev sign-in when `VITE_AUTH_ORIGIN` is unset; `RequireAuth`'s redirect target | `DevSignIn` (§8.6.4) and the production `RedirectToCentralLogin` branch kept | `main.tsx` test: `/login` renders `RedirectToCentralLogin` with `VITE_AUTH_ORIGIN` and `DevSignIn` without; a production build contains neither the demo credentials nor "DevSignIn" | `ls frontend/product-crm/src/routes/Login.tsx` fails; the 21 baseline offenders are gone |
| `lib/csv.ts` and the Companies "Export selected" CSV (`routes/Companies.tsx:10, 232, 337`) | Companies only | "Export selected" in all five bulk bars calls the Excel export engine with `ids=` (DD-14, at most 100 rows; the selection is capped at the page and the sizes are 25, 50, 100) | `data/importExport/*` tests: export with `ids` writes exactly those rows; formula-injection guard covered | `ls frontend/product-crm/src/lib/csv.ts` fails |
| `ui/KanbanBoard.tsx` per-card stage `Select` (`:86-92`) and the stage pills in board mode | the board; no test pinned it | the shared `KanbanBoard` (R3.3) with the move menu and live announcer | the shared board's keyboard and announcement tests pass; `DealsBoard.test.tsx` covers a menu move, a terminal-move confirm and a failed move | `ls frontend/product-crm/src/ui/KanbanBoard.tsx` fails; its 12 offenders are gone |
| Import `Stage` static options (`data/importExport/deals.ts:86-89`), and the stage constants in the board, list and forms (`data/stages.ts` imports there) | board, list, forms, import | `useStages()` (C1) and the import `Stage` lookup built from it (labels or keys accepted, the `types.ts:76-81` lookup mechanism) | import and export tests for deals pass with a custom stage set | `grep -rln "data/stages" frontend/product-crm/src` lists only `routes/DealDetail.tsx`, `routes/CompanyDetail.tsx` (`isOpen`, `:7`) and `ui/StatusPill.tsx` (`stageLabel`, `:2`): the three files R3.5 rebuilds or trims, and `data/stages.ts` with `STAGE_TONE` is deleted there (§9.6.5) |
| The directory as the shell's data layer: `DirectoryProvider` around the app, and every list, form and picker lookup through `data/lookups.ts` | the five lists, the forms, the pickers, `ActivityTimeline` | `useEntityNames`, pickers by `loadOptions`, the owners roster; the provider survives **only** inside `routes/legacy.tsx` for `DealDetail`, `CompanyDetail` and `LeadDetail` | no list, form, picker or shell file imports `useDirectory`; a screen test asserts the resolver issues `ids=` batches of at most 100 | `grep -rln "useDirectory" frontend/product-crm/src` lists exactly seven files: `routes/DealDetail.tsx`, `routes/CompanyDetail.tsx`, `routes/LeadDetail.tsx`, `ui/ActivityTimeline.tsx`, `routes/legacy.tsx` (the `DirectoryStatusBanner` function now lives there), `data/directoryContext.ts` (where the hook is defined) and `data/DirectoryProvider.test.tsx` (which calls it). `data/DirectoryProvider.tsx` is **not** one of them: it imports `DirectoryContext` and never names the hook (`ui/OwnerBadge.tsx` and `App.tsx` move to `useOwners` here and the five lists and `Overview` are rebuilt; R3.5 and R3.7 shorten the list and then empty it, §9.6.5, §11.6.5) |
| `getAllDeals`, `data/derive.ts` **as R1.2b leaves it** (R1.2b deleted the seven dead rollups and left `openDeals`, `dealsByStage` and `needsAttention` with its own `derive.test.ts`; the only importers, `routes/Deals.tsx:6` and `routes/Overview.tsx:10`, are rebuilt here, so the file and its test go together), `ui/NeedsAttention.tsx`, SPA `Summary.trend` mapping | Overview, the Deals screen | the server summary, the overdue list, the lead summary, the stage list from C1 | Overview test counts requests (at most nine on mount) | the files (including `data/derive.test.ts`) are gone; `/deals/summary.trend` stays in the API (frozen, always null) |
| Team page as a free-form roster and the stale README | Team screen | `/settings/team` activation (pending D-7); README rewritten from the registry | n/a | `/team` redirects |

The `/deals/summary` fields (`weighted_pipeline_minor`, `open_deals`, `win_rate_pct`, `won_value_minor`,
`activities_this_week`) are **kept**, not deleted: they have live users (contract tests, visual
fixtures, ADR-0020's parity test, `services/deal/tests/test_authz.py:183-232`) and the rebuilt Overview
now renders them. Only the SPA's null-trend scaffolding is removed.

#### 8.6.6 Seeds

`tools/demo/seed_crm.py` is the one demo dataset (the fixtures-mode mirror claim is deleted from its
docstring): relative dates, leads in every status, a configured assignment rule, contacts with sealed
phones through `raw_phone`, a phone-only contact, a company with a GSTIN and an address, `example.com`
and `.test` domains only, reps created through the activation path (H7, R2.7). It is run by the owner
against a development database; no agent runs it against the box.

### 8.7 Tests

Backend (deal): `test_pipeline_settings.py` (`test_read_without_a_row_returns_defaults_and_does_not_write`,
`test_first_put_creates_one_row_under_concurrency`, `test_put_writes_one_audit_row`,
`test_invalid_timezone_is_422`, `test_prefix_must_be_uppercase_letters`), `test_close_reasons_routes.py`
(`test_virtual_defaults_then_materialise_on_first_write`, `test_key_is_immutable`,
`test_duplicate_key_is_409`, `test_archive_keeps_referencing_deals`), the named negative-authz tests of
§8.3, `test_authz_matrix.py` rows, cross-tenant rows, migration round trip, `pg_policies` assertions.

Frontend (`product-crm`), one file per area unless stated:

| Test | Proves |
|---|---|
| `routes/appRoutes.test.ts` (`every_route_has_a_title_and_a_gate_or_is_marked_public`, `every_nav_entry_resolves_to_a_registered_route`, `visiting_every_nav_target_renders_a_screen_not_the_not_found_page`, `no_registered_path_is_a_placeholder`) | the registry; no dead navigation |
| `data/useListQuery.test.ts` (`defaults_are_not_written`, `search_replaces_and_filters_push`, `invalid_values_are_dropped`, `page_resets_on_filter_change`, `identifiers_never_reach_the_url`) | §4.4.3 |
| `data/useEntityNames.test.ts` (`batches_at_most_100_ids`, `caches_for_the_session`, `pending_is_not_unassigned`, `out_of_scope_is_No_access`) | V21 |
| `routes/{Leads,Deals,Companies,Contacts,Activities}.test.tsx`: loading, empty, filtered-empty, error per status (403 and 404 have no retry), forbidden page, URL round trip, selection plus Export selected sends `ids`, row click, mobile cards, sort by header, one `h1` | U1-U5, §4.4.2 |
| `routes/DealsBoard.test.tsx`, `DealsList.test.tsx` | board columns from stages; unknown stage in "Other"; terminal move asks; failed move restores and announces; tab panel wiring |
| `routes/settings/*.test.tsx` (Pipeline: disabled-with-reason at ends and for the last stage, archive needs a destination, the count column and the dialog's count appear only when the response carries `deal_count`; Reasons; Workspace dirty guard; Assignment inactive reps not selectable, ORG-only save; Team activation and the 409 sentence) | §8.6.3 |
| `forms/*.test.tsx` updated (contact email or phone; company GSTIN state line; deal pickers; lead without Source; activity without Task) | forms |
| `main.test.tsx` (`login_route_by_environment`) and a production-build assertion in the `frontend-build` row that `dist` contains no `DevSignIn` and no demo credential | DD-13 |
| `removed.test.ts` (the proof column of the ledger as assertions over the file tree and a source-text scan; the placeholder rows are R1.2b's, so only the one `ComingSoon` regression grep is asserted) | §8.6.5 |
| `routes/legacy.test.tsx` (`only_the_three_detail_routes_sit_under_the_directory`, `the_directory_banner_shows_only_inside_the_boundary`) and `test/clock.test.ts` (`freezeNow_pins_Date_and_nowIso`) | DD-40, the frozen-clock replacement |
| Visual: surfaces of §4.4.8 for R3.4 at six renderings with axe; the ten recorded CRM baseline entries removed | U1, U5, U6 |

### 8.8 Rollout

1. Merge (owner). 2. **Deploy** the deal revision: `make deploy HOST=… CONFIRM=… REF=<merged ref>`.
3. **Reconcile check** (owner): `make edge-reconcile HOST=… CONFIRM=… PRODUCTS=crm` prints the dry run;
it must show no pending change (R2.8 already applied E19 to admins); if it does, `APPLY=1` before step 4
(REG E.4: reconcile before the SPA that shows the control). 4. **Publish** the SPA
(`make edge-web HOST=… CONFIRM=… APP=crm`, from the checkout of the deployed ref). 5. **Verify**: open
Settings, save Workspace, add a reason, move a deal through the board by keyboard, run the production
smoke `e2e/edge_product_crud.py` (owner-run; its owner step already changed in R2.7).

*Flags*: none. *Memory*: no container, kind or profile change; every total unchanged from the parent
commit (§4.5).

### 8.9 Rollback

SPA: `make edge-web-rollback APP=crm` (one symlink). Backend: `make edge-rollback`; the settings table
stays (unused). Not reversible: nothing. What a rollback of the SPA brings back is the old shell,
including the directory cap, which is acceptable for a rollback. Stop conditions: settings saves
failing, any 5xx on lists above baseline, a board move that does not persist.

### 8.10 Acceptance criteria (mechanical)

1. `make frontend-gate` and `make test-services` pass; `make migration-roundtrip` passes.
2. `ls` of each file this release deletes in §8.6.5 fails (not `ComingSoon.tsx`, which R1.2b already removed:
   that `ls` and its baseline entry are satisfied before R3.4 starts and are not R3.4's criteria);
   `frontend/product-crm/src/removed.test.ts` passes.
3. `grep -rn "ComingSoon\|USE_FIXTURES\|getAllDeals" frontend/product-crm/src` returns nothing (import and
   export `collectPages` excepted and named; `ComingSoon` stays in the pattern as a regression check); the
   two partial-removal greps in §8.6.5 list exactly the files that row names: `grep -rln "data/stages"`
   lists `routes/DealDetail.tsx`, `routes/CompanyDetail.tsx` and `ui/StatusPill.tsx`, and `grep -rln
   "useDirectory"` lists the **seven** files of the directory row (`routes/DealDetail.tsx`,
   `routes/CompanyDetail.tsx`, `routes/LeadDetail.tsx`, `ui/ActivityTimeline.tsx`, `routes/legacy.tsx`,
   `data/directoryContext.ts`, `data/DirectoryProvider.test.tsx`, and not `data/DirectoryProvider.tsx`).
4. `pnpm tokens:baseline` leaves no `product-crm` entry for Login, KanbanBoard, Deals, Activities,
   Companies, Contacts, Team, Leads, NeedsAttention (`ComingSoon`'s entry went with R1.2b); the a11y baseline
   has no `page/crm/deals-list` or `page/crm/new-deal-dialog` entry.
5. `make ui-visual` passes with the R3.4 surfaces (eighteen, §4.4.8) added and the deleted ones removed.
6. `python tools/export_permissions.py --check` and `make openapi-check` pass.
7. The request-count tests (§4.4.9 item 3) pass: on a cold mount Companies issues at most 3 requests,
   Contacts and Leads at most 4, Activities at most 5, the Deals list at most 6, the Deals board at most
   5 plus one per visible column, Overview at most 9.
8. The standing criteria of §4.5: S1, S2, S4 (the two settings routes), S5, S6 and S7.

## 9. R3.5 Contact, company and deal pages

### 9.1 Purpose

The three record pages a rep lives in. `/contacts/:id` is new and hosts the customer 360, a merged
timeline and the audited phone reveal. `/companies/:id` and `/deals/:id` are rebuilt with tabs, inline
editing and local failure handling. A deal is closed through one dialog that asks for a reason and a note
(DD-15), the deal gets a stage history, and the interim confirm of R3.4 goes away. Nothing here changes
the data model; the one new route reads the audit trail the deal service already writes.

### 9.2 Data model

No migration. The release reads `deal.close_reasons`, `deals.close_reason_id` and `deals.close_note`
(R3.1), `deal.pipeline_stages` (R2.3) and `audit_logs` through `ix_audit_logs_org_entity (organization_id,
entity, entity_id)` (`libs/platform-core/src/platform_core/audit.py:99`). A deal's history is therefore
one indexed read of at most a few hundred rows; there is no history table (DD-16).

### 9.3 Backend contract

| Route | Permission, floor | Request | Response | Errors | Named negative-authz test |
|---|---|---|---|---|---|
| `POST /deals/{deal_id}/stage` (C8, changed) | `crm.pipeline.deal.write` + `assert_can_touch(owner_id=deal.owner_id)` | `StageMoveIn {stage, probability?, close_reason_id?, close_note?}` (`extra="forbid"`; `close_note` 0-500 characters, stripped, control characters refused) | `DealOut` (with `close_reason_key`, `close_note`) | 404; 409 `closed_deal`; 422 `unknown_stage`, `unknown_reason` (no such live reason in this organization), `reason_outcome_mismatch` (a reason or note on a move into an open stage, or a reason whose outcome is not the stage's) | `test_stage_move_on_another_reps_deal_is_403_for_own_scope`; `test_close_reason_of_another_organization_is_422_unknown_reason` |
| `GET /deals/{deal_id}/history` (C15, deal variant, moved forward by DD-16) | `crm.pipeline.deal.read` + `assert_can_touch(owner_id=deal.owner_id)` | `page`, `size` (default 25, at most 100) | `Page[HistoryEntryOut]`, `HistoryEntryOut {id, at, actor_id, actor_membership_id, action, changed_fields: string[], before: object \| null, after: object \| null}` newest first, ties broken by `id` | 404 | `test_deal_history_requires_deal_read`; `test_deal_history_out_of_scope_is_403_for_own_scope`; `test_deal_history_cross_tenant_is_404` |

Consumed unchanged: `GET /deals/close-reasons` (C12), `GET /deals/stages` (C1), `GET /deals/{id}`,
`PATCH /deals/{id}`, `GET /contacts/{id}`, `GET /contacts/{id}/360` (C23), `GET /companies/{id}` and its
`tax-ids` and `addresses` (C22), `GET /activities` with `deal_id`, `company_id`, `contact_id`, `type`,
`GET /deals/summary?company_id=`, `GET /contacts?company_id=`, `GET /deals?company_id=`.

**The history projection** is a pure function `domain.project_history(row)` in deal (and, from R3.8, its
twins in customer and lead). It never returns the stored row. Allow-lists: actions `deal.created`,
`deal.updated`, `deal.stage_changed` only (`activity.logged`, `entity.sensitive_read` and anything else
never appear); value keys in `before` and `after` `stage`, `probability`, `value_minor`, `currency`,
`close_date`, `owner_id`, `closed_at`, and `close_reason_key` (REG C15's allow-list, which
includes this one non-personal enumeration); every other changed key, `name`, `source`, `company_id`,
`primary_contact_id`, `close_note`, appears **by name** in `changed_fields` and never by value. A row
that carries the R3.1 shape `after = {"changed_fields": [...]}` yields the same output.

**The audit actor stamp (DD-33).** `AuditLog.actor_id` is the user id (`record_audit(...,
actor_id=ctx.principal.sub)`, `deal/app/services.py:156-168, 320-331`), and no roster maps a user id to a
name. From this release every audit row the deal service writes carries `actor_membership_id` inside
`after` when the principal has a membership (`principal.membership_id`; absent for operators and the
system). The projection lifts it into the top-level `actor_membership_id` and removes it from `after`; the
SPA resolves it through the owners roster (`OwnerOut.membership_id`, C25). Rows written before the release
have none and render "A teammate". It is an id, never personal data, and `redact` does not touch it.

**Event.** `crm.pipeline.deal.stage_changed` gains the optional `close_reason_key` (string matching
`^[a-z0-9_]{1,40}$`, or null; EV8, R3.5). Its committed schema and the producer change land in one commit
with `test_stage_changed_event_carries_close_reason_key`, which drives the real `move_stage` and calls
`validate_event_schema`, with and without a reason. Consumers (notification's tenant rules, insights) read
no new field.

### 9.4 Domain rules

1. **A reason is optional on the wire and required in the UI** (DD-15). The server never refuses a move
   for lack of one, so import, bulk update and automations keep working.
2. **Reason resolution.** `close_reason_id` is looked up among the organization's `close_reasons`. When
   the id equals one of the deterministic virtual default ids (DD-2) and no rows exist yet,
   `ensure_default_close_reasons` runs under the organization advisory lock first and the lookup is
   repeated. A reason must be live (`archived_at IS NULL`) at the time of the move; a deal that already
   carries a since-archived reason keeps it.
3. **Stored.** `deal.close_reason_id`, `deal.close_note` (stripped) on the move into a `won` or `lost`
   stage, in the same transaction as `closed_at`, the audit row and the event. The audit `after` holds
   `{stage, probability, close_reason_key, has_note}` (a boolean, never the text) and the stamp.
4. **Closed deals** move nowhere (409 `closed_deal`, unchanged). Their record page renders read-only in
   the UI; the server still accepts `PATCH` of a closed deal's commercial fields, which is recorded as
   owner question D-41 (§2.1) rather than changed here (DD-34): D-5's "closed deals immutable" (pending D-5) is read by
   SP2 as the no-move rule, and a stricter reading would change live bulk-update and import behaviour.
5. **Merged timeline** (SPA, pure): `mergeTimeline(activities, derived)` drops the 360's derived entry of
   kind `activity` when the page holds real activities, keeps `contact_created`, `deal_opened`,
   `whatsapp_message` and `email_sent` as muted "touchpoint" rows, orders newest first with the entry id as
   the tie-break, and never invents an entry. The company page's derived rows are "Deal opened" and
   "Deal won" or "Deal lost" taken from the deals it already loaded (`created_at`, `closed_at`); SP4's
   `party_timeline` replaces the derived rows when it exists.

### 9.5 Consumers and idempotency

None. A repeated close is refused by rule 4; a double-clicked "Mark as lost" sends one request because the
dialog disables its button while the request is in flight, and the second would be a 409.

### 9.6 UI

#### 9.6.1 Shared pieces

All CRM-local unless stated, built from shared components (§4.4.5).

* **`RecordPage`** (frame): `PageHeader` (eyebrow = the list's name, a back link to it, title, a one-line
  description, `actions`), a body grid (`lg:grid-cols-3`: main column spans two, side column one; one
  column below 1024 px, main first), and `Tabs variant="line" activation="manual"` driven by `?tab=`
  (default `overview`; an unknown value is dropped). Tab panels are lazy: a tab fetches only when first
  selected, and keeps its data while the page stays mounted.
* **`InlineEditField`** over `PropertyRow`: a pencil button named "Edit {label}" (hidden when the caller
  cannot write; **disabled with a reason** when the record is closed). Editors: text, select, date,
  number, money (`parseMinor`, currency fixed by the record), textarea. Enter saves, Escape cancels, a
  blur keeps the editor open when the value changed. Saving sets `aria-busy` and disables the editor; a
  field error from the server (`ApiError.errors[].field`) shows under the control; success announces
  "{label} saved". A save sends only the changed field; an unchanged value sends nothing.
* **`StageBadge`** (`StatusBadge` with the tone and label from the stage list, never a constant) and
  **`StageTrack`**: an ordered list of the open stages as buttons "Move to {label}" (the current one is
  `aria-current="step"` and not a button), then the closed outcomes as "Mark won" and "Mark lost"; it
  scrolls sideways with snap on a phone and scrolls the current stage into view; on a closed deal every
  control is absent and the outcome is stated in words.
* **`CloseDealDialog`** (§9.6.4) and **`CloseReasonField`** (a `Select` plus a `TextArea` with a counter,
  used by the dialog).
* **`RecordTimeline`** (built in R3.4 for the Activity log, reused here) over `Timeline order="desc"` with
  `footer={<LoadMore />}`: each entry is the type
  badge (call, email, meeting, note, or "Touchpoint" for derived rows), the subject, the linked record
  when the screen is not already scoped to it, the owner, and a `<time dateTime>` with the relative text.
  Derived rows are visually muted and never offer actions. Replaces `ui/ActivityTimeline.tsx`, whose
  directory lookups are the reason it cannot survive the directory.
* **`HistoryList`**: one row per `HistoryEntryOut` ("Priya moved the deal from Proposal to Negotiation",
  "Priya changed the value, the close date"), the stage and money values rendered with `formatMoney` and
  the stage list, the actor from the roster ("A teammate" when absent or unknown).
* **`useRecord(id, loader)`**: a thin wrapper over `useAsync` that maps 404 and 403 to the record
  states below and exposes `refresh()` for after-write updates; every record page uses it.

Record-page states (U1, U14, §4.4.2): **loading** a skeleton with the heading placeholder at the final
size; **404** (also a foreign id) "That {record} isn't available" with a Back action and no retry;
**403 from the route gate** the `PermissionDenied` page; **403 from the API** the same neutral message;
**402** "Revenue isn't part of your plan"; **5xx and network** `LoadFailure` with Try again and the
request id; **a tab or section failure** is local (`LoadFailure` inside the panel, the rest of the page
stays); **dirty**: dialogs use `useFormDirty`, inline editors keep their own pending state and the page
guards navigation while an editor holds an unsaved value.

#### 9.6.2 Contact page `/contacts/:id` (gate `crm.book.contact.read`)

Header: the contact's name; description "{title} at {company}" with the company as a link; actions "Log
activity" (needs `crm.pipeline.activity.write`), "Edit" (`crm.book.contact.write`), and a menu with
"Delete" (`crm.book.contact.delete`, `ConfirmDialog`). The server
**never refuses** a contact delete for being referenced: `delete_contact` is a plain soft delete ("No children
to worry about", `services/customer/app/services.py:773-808`), so the route answers 204, 403 or 404 and nothing
else, and the dialog warns instead of promising a refusal ("Deleting {name} removes the contact from lists
and search. Activities and deals that mention them keep their history."). A guard that refuses while live deals
name the contact would be new behaviour, reusing R2.4's deal references probe (REG C55); it is not part of this
release and no screen describes it.

Tabs: **Overview**, **Timeline**, **Deals**, and later **Follow-ups** (R3.6) and **History** (R3.8).

* **Overview.** Main column: `Customer360View` fed by `useCustomer360` (G10), with `revealSlot` the
  `RevealableField kind="phone"` and the additive prop `emailSlot` (G5) carrying the full work
  address as a `mailto:` link, or "No email" for a phone-only contact (the 360's own email line is masked
  and is not shown here; Engage's panel still passes no `emailSlot`). Sections the caller's products do
  not cover read "Not available". Side column: a `DetailList` "Details" with inline-editable Status,
  Title and Company (a `Combobox` in an edit dialog, since it moves the contact), the owner with a
  "Change owner" menu item shown when `useCan(write, TEAM)`, Created and Last activity.
* **Timeline.** `RecordTimeline` of `GET /activities?contact_id=`, merged with the 360's derived rows
  (§9.4 rule 5), a type filter as `Tabs variant="segmented"` (All, Calls, Emails, Meetings, Notes) written to
  `?type=`, 25 per `LoadMore`, "Log activity" opening `ActivityFormDialog` prefilled with the contact and
  its company. Empty: "No activity with {name} yet. Log a call or a note."
* **Deals.** The company's deals (`GET /deals?company_id=&size=25`, `LoadMore`), the ones whose
  `primary_contact_id` is this contact flagged "Primary contact". Empty: "No deals at {company}."

Reveal: `useRevealedField` with `load` = `revealContactPhone(id, headers)`; the value lives only in the
hook's state, hides after `REVEAL_VISIBLE_SECONDS`, on Hide, on a contact change and on unmount (U9); a
step-up is requested through the shared `StepUpDialog`; the audit row and the reveal budget stay on the
server. No phone number appears anywhere else on the page, and no `tel:` link exists before a reveal.

#### 9.6.3 Company page `/companies/:id` (gate `crm.book.company.read`)

Header: name, legal name as the description, status and owner badges; actions "New deal", "New contact"
(prefilled), "Log activity", "Edit", and a menu with "Delete" (`crm.book.company.delete`, confirm). The server's one
refusal is the 409 "This company still has contacts. Reassign or delete them before deleting the account.",
raised only while live contacts exist (`delete_company`, `services/customer/app/services.py:562-577`); the
dialog shows that sentence as the server words it. The server does **not** look at deals ("deal-service cannot
see this delete"), so the dialog warns that the company's deals keep their history and will show no company, and
no screen says deals block the delete. Deleting also releases the company's GSTINs and addresses (DD-43), which
the dialog says in one line. Tabs: **Overview**, **Contacts**, **Deals**,
**Activity**, later **Follow-ups** (R3.6) and **History** (R3.8); SP4 appends **Billing** by adding one
entry to the tab list (`routes/company/tabs.ts`), which is data.

* **Overview.** Stat tiles built from `GET /deals/summary?company_id=` (`by_currency`: one tile line per
  currency, never summed, U12): open deals and open value, won value, plus "Contacts" (the count from the
  contacts tab's `total`) and "Last activity". Then "Recent activity" (five entries). Side column: the
  `DetailList` of inline-editable Status, Industry, Size band, Domain, Annual revenue (money, currency
  chosen with it), City and Country; a **Tax IDs** card listing each GSTIN with its state name, the
  primary mark and Remove, with "Add GSTIN" opening `TaxIdDialog` (validates length and charset locally,
  shows the server's `invalid_gstin` sentence, then the state name the server derived); an **Addresses**
  card (kind, one-line address, default mark) with `AddressDialog`. Both cards need
  `crm.book.company.write` to show controls and are read-only lists otherwise; an empty card says what to
  add and why ("Add a GSTIN so invoices carry the right state").
* **Contacts.** `DataTable` (name, title, email or "No email", status, last activity) with server sort,
  `LoadMore`, "Add contact", row click to the contact page.
* **Deals.** `DataTable` (number, name, stage, value, close date, owner), per-currency summary line above
  it, "New deal".
* **Activity.** `RecordTimeline` of `GET /activities?company_id=` merged with the derived deal rows.

#### 9.6.4 Deal page `/deals/:id` (gate `crm.pipeline.deal.read`)

Header: eyebrow "Deals", title `D-42 · {name}` (the prefix from settings), `StageBadge`, the value and
probability, the owner; `StageTrack` beneath; actions "Log activity", "Edit" and a menu with "Delete"
(`crm.pipeline.deal.delete`, confirm). Tabs: **Overview**, **Activity**, **History**, later
**Follow-ups** (R3.6); SP4 appends **Quotes**.

* **Overview.** `DetailList`: Company (link), Primary contact (link, "Change" opens a `Combobox`
  scoped to the company), Owner, Close date with an **Overdue** chip when open and past due, Probability
  (inline, open deals only), Source, Created; for a closed deal also Closed on, Reason (label) and the
  note. A side card lists the company's contacts (five, "View all") and reveals the primary contact's
  phone through the same `RevealableField` (the behaviour `DealDetail.tsx` has today, kept).
* **Activity.** `RecordTimeline` of `GET /activities?deal_id=`; "Log activity" prefilled.
* **History.** `HistoryList` over `GET /deals/{id}/history`, 25 per `LoadMore`. Empty: "Nothing has
  changed since this deal was created." Error: local `LoadFailure`.

**`CloseDealDialog`.** Opened by "Mark won", "Mark lost", by a drop or menu move into a closed column on
the board or the list (replacing R3.4's `ConfirmDialog`), and by `StageTrack`. Title "Mark {name} as
{won|lost}". When more than one stage of that outcome is active it asks which, preselecting the first.
Fields: **Reason** (`Select` of live reasons of that outcome from C12, required when at least one exists;
none selected by default; the placeholder is "Choose a reason") and **Note** (0-500 characters, live
counter). While the reasons load the select shows a skeleton; if loading fails the dialog says "Reasons
couldn't be loaded" with Retry and lets the person continue without one (a closing deal is never blocked
by a settings read). Confirm is "Mark as lost" (danger tone) or "Mark as won"; it disables while the
request runs. Success reconciles the deal from the response, announces "{name} marked as lost", closes,
and moves focus to the page heading (or, from the board, back to the card's new position, which keeps its
focus by id). A 409 `closed_deal` says "This deal was already closed" and refreshes the record; field
errors map onto the controls; any other failure keeps the dialog open with the request id. Escape and
Cancel ask for confirmation only when a note or a reason was entered (`useFormDirty`).

Closed deal: an `InlineAlert` "Closed won on {date}. Reason: {label}." replaces the track; **Edit**, every
inline pencil and the move controls are disabled with the reason "A closed deal can't be changed" (DD-34);
"Log activity", the activity timeline and the history stay available.

Responsive, keyboard and accessibility rules of §4.4.4 apply to all three pages; in addition: the tab bar
scrolls on a phone and never wraps (`Tabs variant="line"`); each page has one `<h1>`; the tab panel is
wired to its tab; `StageTrack` is a labelled list of real buttons; every inline editor is reachable by
keyboard and announces its result; no information is conveyed by colour alone (stage, status and the
overdue chip carry words).

#### 9.6.5 Replace-then-delete ledger

| Removal | Live users | Replacement | Gate before deleting | Proof after |
|---|---|---|---|---|
| `routes/DealDetail.tsx` (390 lines, 12 offenders) and `routes/DealDetail.reveal.test.tsx` (188) | the `/deals/:id` route | `routes/deal/*` (§9.6.4) | the reveal assertions of the old test exist, re-expressed, in the new page's tests (`removed.test.ts` checks the test names); `deal-detail` visual surface reviewed | `ls frontend/product-crm/src/routes/DealDetail.tsx` fails |
| `routes/CompanyDetail.tsx` (445, 8 offenders) | the `/companies/:id` route | `routes/company/*` (§9.6.3) | its 100-row caps (`:77-78`) have no counterpart: `grep -n "size: 100" routes/company` is empty | file gone |
| `ui/ActivityTimeline.tsx` (102, 6 offenders) | `DealDetail`, `CompanyDetail` | `RecordTimeline` | no importer remains | file gone; export removed from `ui/index.ts:47` |
| `data/stages.ts`, and the `STAGE_TONE` map and `StagePill` (with its export in `ui/index.ts:45`) in `ui/StatusPill.tsx` | `DealDetail`, `CompanyDetail` | `useStages()`, `StageBadge` | `grep -rln "data/stages" frontend/product-crm/src` is empty | `data/stages.ts` gone |
| The `DirectoryProvider` wrappers for `/deals/:id` and `/companies/:id` in `routes/legacy.tsx` | those two routes | `useEntityNames`, `useOwners` | the two routes no longer render under the legacy boundary | `routes/legacy.tsx` wraps `/leads/:id` only |
| R3.4's interim `ConfirmDialog` for terminal moves in `routes/Deals.tsx` | board and list | `CloseDealDialog` | `DealsBoard.test.tsx` covers the dialog path (reason required, retry on a reasons failure, 409) | `grep -n "Mark .* as lost" routes/Deals.tsx` is empty |
| Engage's link to the contacts **list** (`frontend/product-engage/src/lib/dashboard.ts:24-35`, used at `routes/ConversationThread.tsx:312` and `routes/inbox/Customer360Panel.tsx:42`) | Engage | `crmContactUrl(contactId)` returning `${getPlatformHost("crm")}/contacts/{id}`; the list link is kept for conversations with no contact | Engage tests for both call sites | the docstring's "no `contacts/:id` route" sentence is gone |
| `useGlobalSearch`'s contact result href `/contacts?q=` (`useGlobalSearch.ts:204`) | CRM command menu | `/contacts/{id}` (G12, R3.5) | `useGlobalSearch.test.tsx` | no `?q=` in a result href |

#### 9.6.6 Visual surfaces and fixtures

Five surfaces added to `lib/pages.ts`: `contact-detail`, `company-detail`, `deal-detail`,
`deal-detail-closed` (an alternate fixture set: a closed deal with a reason), `deal-close-dialog`. New
`MockRoute`s and fixture files under `fixtures/api/crm/`: `contact.json`, `contact-360.json`,
`company.json`, `company-tax-ids.json`, `company-addresses.json`, `deal.json`, `deal-history.json`,
`close-reasons.json`, with every response field the OpenAPI document now has (`contracts.test.ts`). Each
renders at six renderings with axe and no baseline entry.

### 9.7 Tests

Backend (deal): `test_stage_close_reason.py` (`test_move_to_lost_with_reason_stores_id_and_note`,
`test_reason_outcome_must_match_stage`, `test_unknown_reason_is_422`, `test_archived_reason_cannot_be_chosen`,
`test_virtual_default_reason_id_materialises_defaults_then_applies`, `test_reason_on_an_open_stage_is_422`,
`test_close_note_is_capped_and_stripped`, `test_move_without_a_reason_still_works`,
`test_audit_after_holds_the_reason_key_and_never_the_note_text`, `test_closed_deal_still_cannot_move`,
`test_stage_changed_event_carries_close_reason_key`); `test_history.py`
(`test_projection_never_returns_a_key_outside_the_allow_list` (property over generated audit rows),
`test_projection_excludes_activity_and_sensitive_actions`, `test_projection_lifts_the_actor_stamp_and_hides_it_from_after`,
`test_rows_without_a_stamp_have_a_null_actor_membership`, `test_history_is_newest_first_with_id_tiebreak`,
`test_history_of_a_deal_in_another_organization_is_404`, plus the three named negative-authz tests of §9.3);
`test_authz_matrix.py` rows for both routes; cross-tenant rows; `libs/contracts` schema test for the
optional key. Customer: `test_delete_semantics.py` pins what the pages say about delete
(`test_contact_delete_is_204_for_a_contact_named_by_activities_and_deals`,
`test_company_delete_409_is_only_for_live_contacts_and_carries_the_servers_sentence`,
`test_company_delete_does_not_look_at_deals`).

Frontend (`product-crm`): per page, `loading`, `404`, `403 gate`, `5xx with retry`, `partial section
failure keeps the page`, `tab is URL state`, `unknown tab is dropped`, `one h1`, `request budget` (Contact at
most 5 requests on a cold mount, Company 7, Deal 7); `InlineEditField` (`saves_on_enter`,
`escape_cancels`, `unchanged_value_sends_nothing`, `maps_field_errors_to_the_control`,
`is_read_only_when_the_caller_cannot_write`, `is_disabled_with_a_reason_on_a_closed_record`,
`announces_the_result`); `CloseDealDialog` (`reason_required_when_options_exist`,
`continues_without_a_reason_when_reasons_fail_to_load`, `409_refreshes_the_record`,
`board_move_returns_focus_to_the_card`, `escape_asks_only_when_dirty`); `StageTrack` (`keyboard_order`,
`current_stage_is_not_a_button`, `closed_deal_has_no_controls`); `mergeTimeline` (`drops_the_derived_activity_row`,
`never_invents_an_entry`, `is_stable_for_equal_timestamps`); contact reveal (the moved suites of
`Contacts.reveal.test.tsx` and `DealDetail.reveal.test.tsx`: `value_hides_after_30_seconds`,
`hides_on_unmount_and_on_record_change`, `no_tel_link_before_reveal`, `budget_exhausted_is_announced`);
`useCustomer360.test.tsx` (`reads_the_canonical_path_once`, `maps_unavailable_sections`); Engage
(`Customer360Panel` and `ConversationThread` link to `/contacts/{id}`); `useGlobalSearch.test.tsx`
(`contact_result_opens_the_record`); `routes/legacy.test.tsx` narrowed to the one wrapper left
(`only_the_lead_detail_route_sits_under_the_directory`); the delete flows (the contact dialog warns and treats only 403 and 404 as
refusals; the company dialog shows the server's 409 sentence verbatim and never names deals as blockers); the
visual surfaces of §9.6.6.

### 9.8 Rollout

1. Merge (owner). 2. **Deploy** the deal service (`make deploy HOST=… CONFIRM=… REF=<merged ref>`): no
migration, so the deploy is a code swap. 3. **Publish** the SPAs from the checkout of the deployed ref:
`make edge-web HOST=… CONFIRM=… APP=crm` then `APP=engage`. 4. **Verify** (owner): close a test deal as
lost with a reason and a note and read its History tab; open a contact, reveal the phone with the
step-up, and confirm the row appears in the sensitive-read audit; open a conversation in Engage and
follow its contact link.

*Flags*: none. *Reconcile*: none (no new code). *Memory*: no container, kind or profile change; every
total `memory_budget_totals.py` prints is unchanged from the parent commit (§4.5).

### 9.9 Rollback

SPA: `make edge-web-rollback APP=crm` and `APP=engage`; the old pages return, and they call the stage
route without a reason, which the server still accepts. Backend: `make edge-rollback`; a deal closed with
a reason keeps it (the column stays), and the older code simply does not read it. Not reversible:
nothing. Stop conditions: the stage move failing for any deal that moved before, a reveal that does not
write its audit row, a history response containing a field outside the allow-list.

### 9.10 Acceptance criteria (mechanical)

1. `make frontend-gate`, `make test-services` pass; `make openapi-check` lists `GET /deals/{deal_id}/history`
   and `StageMoveIn.close_reason_id`.
2. `ls` of each file named in §9.6.5 as deleted fails; `grep -rln "useDirectory" frontend/product-crm/src`
   lists exactly four files, `routes/LeadDetail.tsx`, `routes/legacy.tsx` (the banner function),
   `data/directoryContext.ts` (the definition) and `data/DirectoryProvider.test.tsx` (`data/DirectoryProvider.tsx`
   never names the hook, so it is not listed); `grep -rln "data/stages" frontend/product-crm/src` is empty.
3. `libs/contracts/events/crm.pipeline.deal.stage_changed/v1.schema.json` has the optional
   `close_reason_key`, and the producer test passes.
4. `pnpm tokens:baseline` leaves no `product-crm` entry for `DealDetail`, `CompanyDetail` or
   `ActivityTimeline`; the a11y baseline gains no entry.
5. `make ui-visual` passes with the five R3.5 surfaces added and no baseline entry.
6. The three request-budget tests of §9.7 pass.
7. The standing criteria of §4.5: S1 to S4, S6, S7 and S8 (`docs/crm.md` records `close_reason_key` on the stage event).

## 10. R3.6 Follow-ups, reminders and a rebuilt Overview

### 10.1 Purpose

A rep can say "come back to this on Thursday" and be told, in the bell they already use, when Thursday
comes (this release stands on R2.4, so on D-1, D-2, D-3, D-11 and D-13, and on R2.8, so on D-21 and D-38; all pending). A follow-up is a dated, mutable promise attached to a deal, company, contact or lead; it is not an
activity, because activities are past-only and append-only (`services/deal/app/models.py:99-138`). The
release also alerts a deal's owner once when the deal goes stale or passes its close date, gives every
list an `attention` filter and every deal card its chips, and rebuilds Overview around the rep's day. It
ships the platform's first scheduler cancel (A16), moves the scheduler into the core profile (A18) and adds
the first code-defined in-app notification rules (EV54).

### 10.2 Data model

**deal**, revision `<rev>_follow_ups_and_review`, `down_revision` the deal head at merge (R3.4's
`pipeline_settings`), `# RE-PARENT HERE`:

```sql
SET LOCAL lock_timeout = '5s';

-- B25  (TenantBase columns, then:)
CREATE TABLE deal.follow_ups (
    owner_id      UUID         NOT NULL,                  -- a membership id after R2.7; plain UUID
    title         VARCHAR(200) NOT NULL,
    due_at        TIMESTAMPTZ  NOT NULL,
    status        VARCHAR(12)  NOT NULL DEFAULT 'open',
    completed_at  TIMESTAMPTZ,
    deal_id       UUID REFERENCES deal.deals (id) ON DELETE CASCADE,   -- same schema: an FK is allowed
    company_id    UUID,                                   -- plain UUIDs into customer and lead
    contact_id    UUID,
    lead_id       UUID,
    schedule_key  VARCHAR(120),                           -- the armed job's key: follow_up:{id}:{due_at_utc_iso}:{arm_seq}
    arm_seq       INTEGER      NOT NULL DEFAULT 0,        -- grows by one on every arm, never reused (DD-42)
    origin        VARCHAR(20)  NOT NULL DEFAULT 'rep',
    origin_ref    VARCHAR(80),
    CONSTRAINT ck_follow_ups_status CHECK (status IN ('open', 'done', 'cancelled')),
    CONSTRAINT ck_follow_ups_origin CHECK (origin IN ('rep', 'automation', 'system')),
    CONSTRAINT ck_follow_ups_target CHECK (deal_id IS NOT NULL OR company_id IS NOT NULL
                                           OR contact_id IS NOT NULL OR lead_id IS NOT NULL),
    CONSTRAINT ck_follow_ups_done_time CHECK (status <> 'done' OR completed_at IS NOT NULL),
    CONSTRAINT ck_follow_ups_arm_seq   CHECK (arm_seq >= 0)
);
CREATE INDEX ix_follow_ups_org_owner_status_due ON deal.follow_ups (organization_id, owner_id, status, due_at);
CREATE INDEX ix_follow_ups_org_status_due       ON deal.follow_ups (organization_id, status, due_at);
CREATE INDEX ix_follow_ups_org_deal             ON deal.follow_ups (organization_id, deal_id) WHERE deal_id IS NOT NULL;
CREATE INDEX ix_follow_ups_org_company          ON deal.follow_ups (organization_id, company_id) WHERE company_id IS NOT NULL;
CREATE INDEX ix_follow_ups_org_contact          ON deal.follow_ups (organization_id, contact_id) WHERE contact_id IS NOT NULL;
CREATE INDEX ix_follow_ups_org_lead             ON deal.follow_ups (organization_id, lead_id) WHERE lead_id IS NOT NULL;
CREATE UNIQUE INDEX uq_follow_ups_org_origin_ref
    ON deal.follow_ups (organization_id, origin, origin_ref) WHERE origin_ref IS NOT NULL AND deleted_at IS NULL;

-- B61
ALTER TABLE deal.deals ADD COLUMN stale_notified_on   DATE;
ALTER TABLE deal.deals ADD COLUMN overdue_notified_on DATE;

-- B65 (the R3.6 columns of B27)
ALTER TABLE deal.pipeline_settings ADD COLUMN notify_follow_up_due BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE deal.pipeline_settings ADD COLUMN notify_stale         BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE deal.pipeline_settings ADD COLUMN notify_overdue       BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE deal.pipeline_settings ADD COLUMN review_armed_for     DATE;   -- the date of the review currently armed
ALTER TABLE deal.pipeline_settings ADD COLUMN review_baselined_on  DATE;   -- NULL until the silent baseline ran
-- ENABLE + FORCE RLS and tenant_isolation on follow_ups (§4.1); added to TENANT_TABLES in conftest.
```

`ON DELETE CASCADE` on `deal_id` is the database's net only: deals are soft-deleted in the application and
`delete_deal` cancels the deal's open follow-ups explicitly (§10.4 rule 7). The deal columns and the settings
columns are nullable or defaulted, so the expand is cheap and the previous code ignores them. No backfill
runs in the migration (DD-35).

**scheduler**, revision `<rev>_scheduled_jobs_cancel`, `down_revision` `bff4691114ce`, `# RE-PARENT HERE`
(B64):

```sql
SET LOCAL lock_timeout = '5s';
ALTER TABLE scheduler.scheduled_jobs ADD COLUMN cancelled_at TIMESTAMPTZ;
CREATE INDEX ix_scheduled_jobs_cancelled_at ON scheduler.scheduled_jobs (cancelled_at) WHERE status = 'cancelled';
```

`status` is `VARCHAR(16)` with no CHECK (`13ef09d48a2b_scheduled_jobs.py:37`), so `cancelled` needs no DDL.
The index is the retention sweep's discovery, the shape of its `fired` sibling (cross-tenant discovery on
the outbox role, which is why it does not lead with `organization_id`). `grant_sweep_reader`
(`services/scheduler/migrations/env.py:33-64`) gains the sixth column: `GRANT SELECT (organization_id, id,
due_at, status, fired_at, cancelled_at)`; its `REVOKE ALL` first and the run after every alembic call
stay.

Downgrades: deal drops the table, the two deal columns and the five settings columns; it refuses unless
`OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE=1` while `follow_ups` holds a live row or any `notify_*` is false.
Scheduler drops the index and the column and refuses unless the override is set while any job is
`cancelled`. An empty database fully reverses, so `make migration-roundtrip` stays green.

### 10.3 Backend contract

#### 10.3.1 Public routes (deal; gateway prefix `/api/v1/deals`, no gateway change)

All declared before `GET /deals/{deal_id}`; permissions are E20 and E21 (catalogued by SP2/R2.8, reaching
live tenants through the reconcile of §10.8).

| Route | Permission, floor | Request | Response | Errors | Named negative-authz test |
|---|---|---|---|---|---|
| `GET /deals/follow-ups` (C13) | `crm.pipeline.follow_up.read`, `ctx.apply(owner_col=FollowUp.owner_id)` | `owner` (`me`), `owner_id`, `status` (`open\|done\|cancelled`), `due_before`, `due_after`, `deal_id`, `company_id`, `contact_id`, `lead_id`, `ids`, `sort` (`due_at` default, `-due_at`, `created_at`, `-created_at`), `tz`, `page`, `size` | `Page[FollowUpOut]`; `FollowUpOut {id, owner_id, title, due_at, status, completed_at, deal_id, company_id, contact_id, lead_id, origin, overdue}` | 422 `invalid_sort`, `invalid_ids`, `invalid_timezone` | `test_follow_up_list_is_limited_to_own_rows_for_own_scope`; `test_follow_up_owner_filter_cannot_widen_own_scope` |
| `POST /deals/follow-ups` (C13) | `crm.pipeline.follow_up.write` | `FollowUpIn {title (1-200), due_at, owner_id?, deal_id?, company_id?, contact_id?, lead_id?}` (`extra="forbid"`) | 201 `FollowUpOut` | 422 `no_target`, `owner_required`; 404 a `deal_id` outside the organization; 403 a deal the caller cannot touch (the `log_activity` rule, `deal/app/api/routes.py:250-254`) or an `owner_id` above the caller's scope | `test_follow_up_create_without_the_write_code_is_403`; `test_follow_up_for_a_deal_the_caller_cannot_touch_is_403`; `test_follow_up_for_another_rep_is_403_for_own_scope` |
| `GET /deals/follow-ups/summary` (C14) | `crm.pipeline.follow_up.read`, `ctx.apply` | `owner` (`me`), `owner_id`, `tz` (C14: a tile needs "mine", not the whole scope) | `FollowUpSummaryOut {overdue, due_today, upcoming_7d}`, disjoint (§10.4 rule 2) | 422 | `test_follow_up_summary_counts_only_in_scope_rows` |
| `GET /deals/follow-ups/{follow_up_id}` | read + `assert_can_touch(owner_id=…)` | none | `FollowUpOut` | 404, 403 | `test_follow_up_detail_cross_tenant_is_404`; `test_follow_up_detail_out_of_scope_is_403_for_own_scope` |
| `PATCH /deals/follow-ups/{follow_up_id}` (C13) | write + touch | `FollowUpPatch {title?, due_at?, owner_id?, deal_id?, company_id?, contact_id?, lead_id?}` | `FollowUpOut` | 409 `follow_up_not_open`; 422 `no_target` (a patch that would remove the last target) | `test_follow_up_patch_on_another_reps_item_is_403_for_own_scope`; `test_follow_up_reassign_above_scope_is_403` |
| `POST /deals/follow-ups/{follow_up_id}:complete` | write + touch | none | `FollowUpOut` (already `done`: 200 unchanged) | 409 `follow_up_not_open` (cancelled) | `test_follow_up_complete_on_another_reps_item_is_403_for_own_scope` |
| `POST /deals/follow-ups/{follow_up_id}:cancel` | write + touch | none | `FollowUpOut` (already `cancelled`: 200 unchanged) | 409 `follow_up_not_open` (done) | `test_follow_up_cancel_requires_the_write_code` |

`GET /deals` gains `attention` (one value: `stale`, `overdue` or `closing_soon`; C35) and `DealOut`
gains `attention: string[]` (C7), both computed from the settings row and the organization
timezone (`tz` is taken from the settings when the call omits it). `GET /deals/summary` accepts
`attention` too, so the board header and Overview agree with the filter.

#### 10.3.2 Events (committed schemas change in the same commit as their producers)

| Event | Producer, trigger | Payload | Notes |
|---|---|---|---|
| `crm.pipeline.follow_up.created`, `.updated`, `.completed` (EV19) | deal, the write | `organization_id, follow_up_id, owner_id, owner_membership_id, due_at, status, deal_id, crm_company_id, crm_contact_id, lead_id` (+ `changed[]` on `.updated`) | no title (free text); a cancel is `.updated` with `status: "cancelled"`; no consumer registered |
| `crm.pipeline.follow_up.due` (EV20) | deal, the reminder handler | `organization_id, follow_up_id, owner_membership_id, due_at, deal_id, crm_contact_id` | subscribable key (EV41) and the system rule of EV54 |
| `crm.pipeline.deal.stale`, `.overdue` (EV21) | deal, the review handler | `organization_id, deal_id, crm_company_id, owner_id, owner_membership_id, stage, stage_outcome, days_stale` or `days_overdue`, `as_of` | subscribable keys (EV41) and EV54 |
| `crm.pipeline.follow_up.reminder_requested` (EV26) | deal via `schedule_event`, fired by the scheduler | `organization_id, follow_up_id, due_at, arm_seq` (REG EV26 and B25 carry `arm_seq`, DD-42) | key `follow_up:{id}:{due_at_utc_iso}:{arm_seq}`, **single-use**: every arm takes the next `arm_seq`, so no key is requested twice; never subscribable |
| `crm.pipeline.deal.review_requested` (EV27) | deal via `schedule_event`, fired by the scheduler | `organization_id, as_of` (the first draft's `deal_id, kind` are dropped in REG EV27, DD-19) | key `deal_review:{organization_id}:{as_of}`, aggregate type `deal_review`, aggregate id the organization id; never subscribable |
| `platform.schedule.cancel_requested` (A16) | any producer via `cancel_scheduled_event` | `idempotency_key` | joins `OutboxRelay._GUARANTEED_ROUTING_EVENT_TYPES` |

`owner_membership_id` equals `owner_id` after R2.7 (§D rule). Every one of these has a committed
`libs/contracts/events/<type>/v1.schema.json` with `additionalProperties: false` and a producer-side test
through the real path (§10.7). The scheduler-target schemas exist because the scheduler carries
`target_payload` verbatim and consumers validate it.

#### 10.3.3 platform-core, scheduler and notification changes

* **A16.** `platform_core.events` gains `PLATFORM_SCHEDULE_CANCEL_REQUESTED` and `cancel_scheduled_event(
  session, outbox_cls, *, organization_id, idempotency_key, aggregate_type, aggregate_id,
  causation_id=None) -> uuid.UUID` beside `schedule_event` (`events.py:120`). A platform-core change
  reruns every service's tests.
* **Scheduler consumer.** A second binding, `platform.schedule.cancel_requested`, handled by
  `handle_cancel_requested`: `already_processed`; one tenant-bound transaction; `SELECT … WHERE
  organization_id = :org AND idempotency_key = :key FOR UPDATE`; nothing found, or `status` not `pending`:
  a debug log (`scheduler.schedule_cancel.noop`) and the event is marked processed; otherwise the stored
  job's `target_event_type` is checked against the envelope's producer by the **same** function that
  guards scheduling (A17), and on refusal `scheduler.schedule_cancel.rejected` (ids only) is logged and
  the event dropped; on success `status = 'cancelled'`, `cancelled_at = now()`. **A key is single-use for the
  retention window**: the cancelled row keeps its key and ingest stays `ON CONFLICT (organization_id,
  idempotency_key) DO NOTHING`, so a repeated request for a cancelled key creates no job. This release does not
  change that (finance's early-arrival defence relies on the same rule); it pins it with
  `test_a_repeated_request_for_a_cancelled_key_creates_no_job`, and the follow-up producer keeps to it with a
  fresh key per arm (§10.4 rule 3, DD-42).
* **A17.** A new pure module `services/scheduler/app/domain.py` (the service has none today, and the
  anatomy puts rules without I/O there) holds `PRODUCER_PREFIXES: Mapping[str, tuple[str, ...]] =
  {"deal": ("deal.", "crm.pipeline.")}` and `producer_may_own(producer, event_type)`, which accepts
  `f"{producer}."` or any mapped prefix; `_rejection_reason` (`consumers.py:83-111`) and the cancel
  handler both call it. Only `crm.pipeline.` is added: not `crm.`, so deal cannot schedule `crm.lead.*` or
  `crm.book.*`. The notification mapping that Engage's scheduled sends would need stays out (A17, L8).
* **Retention.** `services.purge_stale_fired_jobs` (`services.py:199`) becomes `purge_stale_jobs`,
  deleting `fired` rows past `fired_at` and `cancelled` rows past `cancelled_at` with the same window
  (`scheduled_job_retention_days`, 7); `selectors.orgs_with_stale_fired_jobs` (`selectors.py:90`) gains a
  second branch and `app/cron.py` (`:163, :226`) calls the new names. `models.py` gains
  `STATUS_CANCELLED`; its "No `cancelled` in this slice" paragraph is rewritten.
* **A18 and F5.** `infrastructure/edge/compose.prod.yml` drops `profiles: ['finance', 'split-workers']`
  from `scheduler` (`:1631-1639`); `memory-budget.json` `containers.scheduler.profile` becomes `null`;
  `"scheduler.events"` moves from `PROFILE_DLQ_QUEUES["finance"]` to `CORE_DLQ_QUEUES`
  (`ops_monitor/config.py:63-70, 104`); the RUNBOOK paragraph at `:3367-3374` ("Behind the `finance`
  profile") is rewritten in the same commit; the infra tests that name the two profiles
  (`tests/infra/test_ops_monitor_dlq_queue_sync.py:52`, `test_active_profiles.py`,
  `test_ops_monitor_profile_containers.py`, `test_compose_memory_budget.py`) change on purpose. The RUNBOOK's
  memory figures change **in the same commit**, because the move shifts 90 MB from `finance` to core: the "what
  this release costs" table (`infrastructure/edge/RUNBOOK.md:2999-3011`) takes core from 1772 to 1862, adds 90 to
  every cumulative row from storefront through marketing, and lowers finance's "adds" from 189 to 99, while the
  finance and itsm cumulative rows (3649 and 3747 at HEAD; each one higher once R2.4's fold is recorded, and
  unmoved by this change either way) and every ten-profile figure do not move; the worked example at
  `:2521` ("inventory alone" plus `admin`, 1870 and 2067 MB, 53.5 %, a figure R2.4 does not touch because
  the `inventory` profile does not include `deal`) becomes 1960 and 2157 MB, 55.9 %. Because
  `crm` runs without `finance` on some boxes, `--profiles crm` rises by the same 90 (§10.8). The ceiling literal
  `EDGE_BUDGET_ACCEPTED_MB=<ten-profile total>` that `tests/infra/test_planned_headroom.py::test_runbook_ceiling_example_is_the_number_preflight_would_print`
  pins does not change here (the ten-profile total is the same), so that test passes untouched. ADR-0015 Q2's
  `preflight.sh` guard, which REG F10 attaches to the move (refuse a deploy in which an enabled profile's service
  calls `schedule_event` while `scheduler` is not running), ships in the same commit.
* **EV54, system in-app rules (the one mechanism).** `services/notification/app/domain.py` gains
  `SYSTEM_IN_APP_RULES: Mapping[str, tuple[SystemInAppRule, ...]]` keyed by event type, with `SystemInAppRule
  {code, recipient_path, subject, body, category = "operational"}` (`operational` is the default category of
  rules and messages, `services/notification/app/models.py:134, 351`), and `SUBSCRIBABLE_EVENTS`
  (`domain.py:98-227`, the `# crm` group) gains `crm.pipeline.deal.overdue`, `crm.pipeline.deal.stale`,
  `crm.pipeline.follow_up.due`. **R3.6 creates this mapping and its evaluator with the three entries below;
  R4.2 adds its three assignment entries** (`crm.lead.assigned`, `crm.pipeline.deal.owner_changed`,
  `crm.book.company.owner_changed`) **to the same mapping**, gives `SystemInAppRule` an optional entity
  reference so the stored message carries the `entity_ref` of C72, and adds a skip when the recipient is the
  acting membership: no second loop, no second table, and the six keys share one structure. SP4's first draft
  specified the same mechanism under the name `BUILTIN_CRM_ALERTS` (same file, same evaluation point after the
  tenant rules, in-app, recipient `payload.owner_membership_id`, producer-side opt-out); the name
  `SYSTEM_IN_APP_RULES` and this structure are the ones kept, `BUILTIN_CRM_ALERTS` is withdrawn, and SP4's R4.2
  extends the mapping (REG EV54, which C72 points to). `apply_rules`
  (`services.py:608-679`), after the tenant rules, evaluates the system rules for the event type: the
  recipient is `domain.resolve_path(payload, "owner_membership_id")` normalised by
  `normalize_recipient("in_app", …)`, the wording is rendered by `domain.render` from a fixed template
  over integers only, and `stage_message(channel="in_app", category="operational", rule_code=<code>,
  dedupe_key=f"{code}:{event_id}", created_by=SYSTEM_ACTOR)` stores it (`in_app` has no consent regime,
  `consent.py:60-61`, `:359-371`). A missing or non-UUID recipient is a logged skip, never an error. The
  three rules and their words: `system.crm.follow_up_due` "Follow-up due" / "A follow-up you own is due
  now. Open Follow-ups to see it."; `system.crm.deal_stale` "Deal needs attention" / "A deal you own has
  had no activity for {{days_stale}} days."; `system.crm.deal_overdue` "Deal past its close date" / "A deal
  you own is {{days_overdue}} days past its close date." No title, name or note ever enters the text
  (DD-22); the bell links nowhere until R4.2 adds `entity_ref` (C72).

#### 10.3.4 Documents and recipes (the same commits)

* **`docs/crm.md`** (REG I3): the event table gains EV19 to EV21, EV26, EV27 and the scheduler's
  `platform.schedule.cancel_requested`, and the service map notes that `scheduler` now runs in every profile
  set; SP1's `tests/infra/test_crm_doc_matches_contracts.py` fails the build otherwise.
* **ADR-0015** gets a dated "Amended" note (cancel and the `cancelled` state, A16; the producer-prefix map,
  A17; `scheduler` in the core profile, Q2 resolved by A18), and the `docs/decisions.md` row and the
  open-questions register rows the change closes are updated (REG I6); `docs/deploy/ENV_SOURCE_MATRIX.md` and
  the RUNBOOK change as §10.3.3 says (REG I8).
* **Engage recipes** (REG D.5: a subscribable key lands with a committed schema and a repaired recipe in the
  same commit): in `frontend/product-engage/src/routes/automations/recipes.ts`, `follow-up-due` (trigger
  `crm.pipeline.follow_up.due`, one `send_message` step on email with the recipient left for the person to
  choose, the way `payment-receipt` leaves it), `deal-stale` (trigger `crm.pipeline.deal.stale`, one
  `create_task` step) and `deal-overdue` (trigger `crm.pipeline.deal.overdue`, one `create_task` step). Their copy
  is the plan's; SP1's `tests/infra/test_engage_recipe_triggers.py` proves each trigger is in
  `SUBSCRIBABLE_EVENTS`.

### 10.4 Domain rules

1. **Creation.** `owner_id` omitted means `ctx.own_ref` (A6); `own_ref` absent is 422 `owner_required`.
   An OWN caller's `owner_id` is forced to themselves; naming another rep needs a scope above OWN and
   `assert_can_touch(owner_id=…)`; deal validates no owner (C76). A `deal_id` must exist in the
   organization, and the caller must be able to touch it; the follow-up's `company_id` is then the deal's,
   exactly as an activity's is. Company, contact and lead ids are plain UUIDs into other services and are
   not validated across the boundary (`deal.company_id` is not either).
2. **Disjoint buckets** (pure `domain.bucket_for(due_at, now, tz)`): `overdue` is open and `due_at < now`;
   `due_today` is open and `now <= due_at` before the next local midnight; `upcoming_7d` is open and from
   that midnight for seven days; everything later is `later`. A row is in exactly one.
3. **Arming** (DD-17, DD-42). A follow-up whose `due_at` is in the future is armed. **Every arm takes the next
   `arm_seq`** (`arm_seq = arm_seq + 1`, in the same statement that writes the key) and
   `schedule_key = f"follow_up:{id}:{due_at_utc_iso}:{arm_seq}"`, so **no key is ever requested twice**. The
   reason is the scheduler's own idempotency: it keeps a cancelled row, key included, for
   `scheduled_job_retention_days` (7, `services/scheduler/app/config.py:20`) and ingests with `ON CONFLICT
   (organization_id, idempotency_key) DO NOTHING` (`services/scheduler/app/services.py:77-104`,
   `uq_scheduled_jobs_org_idempotency`). Re-requesting a cancelled key would land on that row and create no
   pending job, with nothing logged and no dead letter, while `follow_ups.schedule_key` still said "armed".
   The arm is `schedule_event(target_event_type="crm.pipeline.follow_up.reminder_requested",
   target_aggregate_type="follow_up", target_aggregate_id=id, target_payload={organization_id, follow_up_id,
   due_at, arm_seq})`. One created already due is not armed. A reschedule to a new `due_at` cancels the stored
   key (A16) and arms the new one, with the next `arm_seq`, in the same transaction; a patch that leaves
   `due_at` unchanged is a no-op (R3.2) and neither cancels nor arms; complete and cancel cancel the stored key;
   reassigning the owner changes nothing (the handler reads the owner when it fires). **A follow-up moved from
   time A to B and back to A arms three distinct keys** (`…:A:1`, `…:B:2`, `…:A:3`): the first two are cancelled
   and the third fires.
4. **Reminder handler.** On `reminder_requested` the handler (a) refuses an envelope whose `producer` is
   not `scheduler` or whose organization differs from the payload's; (b) re-reads the row; (c) does
   nothing unless `status = 'open'`, `deleted_at IS NULL`, `due_at` equals the payload's **and `arm_seq`
   equals the payload's** (a job from an older arm that fires before its cancel was processed is dropped,
   so one due time produces one reminder); (d) publishes `.due` unless `notify_follow_up_due` is false;
   (e) marks the envelope processed in the same transaction. A cancel that arrived late, a reschedule and a
   completed item therefore all end here.
5. **The daily review** (DD-19, DD-20, DD-35). Armed per organization for 06:00 in the organization
   timezone; payload `as_of` is that local date. The handler, in one transaction: loads (never creates)
   the settings row; if `review_baselined_on` is NULL it **baselines**: sets `stale_notified_on` and
   `overdue_notified_on` to `as_of` on every currently stale or overdue open deal, publishes nothing, sets
   `review_baselined_on`, and goes to step 5; otherwise (1) clears `stale_notified_on` on every deal that
   is no longer stale and `overdue_notified_on` on every deal no longer overdue or no longer open
   (set-based, `updated_at` untouched); (2) selects, oldest first, open deals that are stale and have
   `stale_notified_on IS NULL`, then those overdue with `overdue_notified_on IS NULL`, at most 500 of each
   kind per pass and at most ten passes per run; (3) for each stages EV21 unless the matching `notify_*`
   is false and sets the marker to `as_of` either way (so switching a notification back on never floods);
   (4) never notifies a deal whose stage outcome is not `open`; (5) arms the next review, for
   `max(as_of + 1 day, today_local)` at 06:00 local, key `deal_review:{organization_id}:{date}`, and sets
   `review_armed_for`. **Stale**: open, and `COALESCE(last_activity_at, created_at)` earlier than the start
   of the local day `stale_after_days` days before `as_of`. **Overdue**: open and `close_date < as_of`.
   **Closing soon** (filter and chip only, no event): open and `as_of <= close_date <= as_of +
   closing_soon_days`. "Open" means the stage's outcome is `open` in the effective stage list.
6. **Lazy arming** (DD-35). `services.ensure_review_armed(session, organization_id)` runs inside
   `create_deal`, `move_stage`, `log_activity` and the settings `PUT`: it reads `review_armed_for` through a
   process-local cache and, when it is NULL or earlier than today's local date, arms today's (or the next
   06:00's) review, sets `review_armed_for` and refreshes the cache. The scheduler key makes a double arm a
   no-op, and a broken chain heals on the organization's next deal write. An organization that has no deal
   write at all, and whose chain broke, gets no alerts: recorded in §14.
7. **Deleting a deal** cancels its open follow-ups (status `cancelled`, key cancelled, `.updated`). A
   **closed** deal's open follow-ups are left alone: the owner decides.
8. **Opt-outs** live at the producer (`notify_*`); the `attention` filters and chips do not depend on them.

### 10.5 Consumers and idempotency

| Handler (queue `deal.events`, EV44) | Binds | Idempotency |
|---|---|---|
| `handle_reminder_requested` | `crm.pipeline.follow_up.reminder_requested` | `already_processed`/`mark_processed` on the fired envelope's id; the row re-check of §10.4 rule 4 makes a second firing for the same key harmless |
| `handle_review_requested` | `crm.pipeline.deal.review_requested` | envelope id ledger; the markers are the business idempotency (one event per deal per crossing); the scheduler key makes the next arm a no-op; a redelivery after commit finds the ledger row |
| scheduler `handle_cancel_requested` | `platform.schedule.cancel_requested` | envelope id ledger; cancelling a `cancelled` job is a no-op |
| notification `fold` | the three subscribable keys | existing ledger plus `dedupe_key` per rule and event |

Every handler binds the tenant per message (`_session_scope(organization_id)`), refuses an envelope whose
organization differs from its payload's, runs under the system context `services.service_ctx(
organization_id)` (REG F1: ORG scope, all-zero `sub`, `own_ref` None) and writes audit rows with
`acting_as="deal-review"` or `"follow-up-reminder"` (ids and counts only).

### 10.6 UI

#### 10.6.1 Follow-ups page `/follow-ups` (gate `crm.pipeline.follow_up.read`)

`PageHeader` "Follow-ups" with "New follow-up" (`crm.pipeline.follow_up.write`). A `FilterBar`: an owner
`Tabs variant="segmented"` **Mine** (default, `owner=me`) and **Everyone** (shown only when
`useCan(read, TEAM)`; R3.8 makes it **My team** for managers), a status `Select` (Open default, Done,
Cancelled), a record-type select (Deals, Companies, Contacts, Leads), Clear all; URL keys `owner`,
`status`, `type`, `due` (`overdue|today|week`, set by the Overview tiles). The list is sorted by `due_at`
and grouped in the order **Overdue**, **Today**, **Tomorrow**, **This week**, **Later**, each a `<section>`
with a heading and a count; grouping is computed in the organization timezone from the loaded rows, 25 per
`LoadMore`. A row: a "Done" button named "Mark {title} as done", the title, the linked record's name
(`useEntityNames`; a lead uses `GET /leads?ids=`), the due time as `<time>` with the absolute instant in
`title`, the owner when it is not the caller, an **Overdue** `StatusBadge` (danger, word included), and
`RowActions` Reschedule, Reassign (when scope allows), Cancel (`ConfirmDialog`). States: loading skeleton
rows with group headings; empty "You're all caught up. Follow-ups you create appear here."; filtered-empty
with Clear all; 403 `PermissionDenied`; error `LoadFailure`; a row mutation shows a spinner on the row, an
error toast with the request id on failure and restores it.

**Completion** opens `CompleteFollowUpDialog`: title "Mark done", an unchecked **Log what happened**
(DD-18) that reveals type (Call, Email, Meeting, Note), a subject the person types and an optional body,
prefilled only with the follow-up's targets (deal, contact, company) and never its title; **Mark done**
is the default action (Enter), **Mark done and log** calls `POST /activities` after the completion
succeeds and says so if only the second call failed. Focus returns to the next row.

**`FollowUpDialog`** (create and edit): Title (required, helper "Describe the next step. Leave out
personal details."), Due (`DateTimeField` in the organization timezone, default now plus
`follow_up_default_hours`; quick picks **Tomorrow 9:00**, **In 3 days**, **Next week**), **Related to**
(`Combobox` for a deal, company, contact or lead, prefilled from the record the dialog was opened on; "Choose
what this follows up on" when none), Owner (shown only when scope allows, default the caller). Dirty
guard; field errors map; success announces "Follow-up created for {date}".

The dialog is opened from the page, from the shell quick action "New follow-up", and from a **Follow-ups**
tab added to the contact, company and deal pages of R3.5 (the tab list is data): that tab lists
`GET /deals/follow-ups?{target}_id=` with the same row, `LoadMore`, "New follow-up" prefilled, and shows
open items first.

#### 10.6.2 Overview rebuilt `/`

A "my day" screen; every widget loads and fails alone (U14), skeletons keep their footprint.

* **KPI row** (`KpiCard`): Follow-ups due today (links to `/follow-ups?due=today`), Overdue follow-ups
  (danger tone when above zero, `/follow-ups?due=overdue`), Open pipeline (`by_currency`: the largest
  currency's value and weighted value, and "+{n} more currencies" as a link to the Deals list; never
  summed), New leads (`GET /leads/summary`: `by_status.new`, `unassigned`).
* **Needs attention** (`Tabs variant="line"`, `activation="manual"`, so only the selected tab fetches):
  **Follow-ups** (the default: my overdue then due today, five, "View all"), **Deals** (a segmented switch
  **Overdue** and **Stale**; the selected one is `GET /deals?owner=me&attention=overdue&sort=close_date&size=6`
  or `attention=stale`; chips carry words), **Leads** (from R3.7a "New and unassigned":
  `GET /leads?status=new&owner=unassigned&size=6`, plus an SLA chip when an SLA is set).
* **Pipeline by stage**: the funnel from `by_currency[].funnel` for the selected currency (a `Tabs
  variant="segmented"` when more than one), as `BarList` with the share and the count.
* **Recent activity**: `RecordTimeline`, six entries, "View all".

Request budget on a cold mount, session reads included: deals summary (which also carries the funnel),
leads summary, follow-up summary, my follow-ups (the default tab), recent activities, owners, settings,
plus at most two `ids=` name resolutions (companies, deals): nine, so **at most 10** with one request of
slack, pinned by a test; the Deals and Leads tabs add one request when first selected. `NeedsAttention.tsx` (deleted in R3.4)
does not return; `Overview.tsx` is replaced by `routes/overview/*`.

#### 10.6.3 Chips, filters and settings

`DealCard` and the Deals list render `deal.attention` as `StatusBadge` words (Stale, Overdue, Closing
soon); the Deals filter bar gains an **Attention** select (`attention=`); the saved presets of R3.8 link
to it. **Settings, Workspace** gains three `Switch`es: "Remind owners when a follow-up is due", "Alert
owners when a deal goes stale", "Alert owners when a deal is past its close date" (`notify_*`), read-only
for a caller without `crm.pipeline.settings.write`; its helper copy says reminders appear in the bell and
that turning one off stops new alerts without a flood when it is turned back on.

#### 10.6.4 Replace-then-delete ledger

| Removal | Live users | Replacement | Gate | Proof |
|---|---|---|---|---|
| `routes/Overview.tsx` (R3.4's interim layout) | the `/` route | `routes/overview/*` | `overview` visual surface reviewed; the request-budget test passes | `ls frontend/product-crm/src/routes/Overview.tsx` fails |

#### 10.6.5 Visual surfaces and fixtures

`follow-ups`, `follow-ups-empty`, `follow-up-dialog`, `overview` (rebuilt), `settings-workspace`; new
`MockRoute`s and fixtures `follow-ups.json`, `follow-up-summary.json`, with `attention` added to
`deals*.json`; six renderings with axe, no baseline entry.

### 10.7 Tests

Deal: `test_follow_ups_service.py` (`test_create_arms_a_future_follow_up_once`,
`test_create_already_due_is_not_armed`, `test_reschedule_cancels_the_old_key_and_arms_the_new_one`,
`test_reschedule_back_to_a_cancelled_due_at_still_fires` (A to B to A stages three distinct idempotency keys,
`…:A:1`, `…:B:2`, `…:A:3`; each cancel names the key stored at that moment; only the last request is left
uncancelled), `test_arm_seq_only_grows_and_no_key_is_requested_twice` (property over random reschedule
sequences), `test_an_unchanged_due_at_neither_cancels_nor_arms`, `test_complete_and_cancel_cancel_the_key`, `test_complete_is_idempotent_and_cancelled_is_409`,
`test_deleting_a_deal_cancels_its_open_follow_ups`, `test_no_target_is_422`, `test_owner_defaults_to_own_ref_and_none_is_422`),
`test_follow_up_buckets.py` (property: every row is in exactly one bucket; boundaries at local midnight in
`Asia/Kolkata` and `America/New_York`, across a DST change), `test_reminder_handler.py`
(`test_open_matching_follow_up_publishes_due`, `test_completed_rescheduled_or_deleted_follow_up_is_ignored`,
`test_a_producer_other_than_scheduler_is_ignored`, `test_a_firing_from_an_older_arm_publishes_nothing`,
`test_redelivery_publishes_once`, `test_opt_out_publishes_nothing`),
`test_review_handler.py` (`test_first_run_baselines_silently`, `test_second_run_notifies_each_crossing_once`,
`test_condition_clearing_re_arms_a_deal`, `test_opt_out_marks_without_publishing`,
`test_caps_at_500_per_pass_and_continues`, `test_deals_in_won_or_lost_stages_are_never_notified`,
`test_next_review_is_armed_for_the_next_local_0600`, `test_catch_up_never_stacks_reviews`),
`test_ensure_review_armed.py` (`test_arms_once_per_day`, `test_heals_a_broken_chain_on_the_next_write`),
`test_attention_filters.py` (stale, overdue, closing soon, with the organization timezone and the
`DealOut.attention` agreeing with the filter), the seven named negative-authz tests of §10.3.1,
`test_authz_matrix.py` rows, cross-tenant rows for the table, event schema tests through the real path for
EV19, EV20, EV21, EV26, EV27 and `platform.schedule.cancel_requested`, migration round trips.
Scheduler: `test_cancel.py` (`test_cancel_by_the_owning_producer_marks_the_job_cancelled`,
`test_cancel_by_another_producer_is_rejected_and_logged`, `test_cancel_of_an_unknown_or_fired_key_is_a_noop`,
`test_cancel_redelivery_is_idempotent`, `test_a_repeated_request_for_a_cancelled_key_creates_no_job` (the
single-use rule the follow-up producer keeps to), `test_cancelled_job_is_never_fired`,
`test_cancelled_rows_are_purged_after_retention`), `test_producer_prefixes.py` (`test_deal_may_schedule_crm_pipeline_targets`,
`test_deal_may_not_schedule_crm_lead_or_crm_book_targets`, `test_finance_filing_targets_are_still_accepted`).
Notification: `test_system_in_app_rules.py` (`test_follow_up_due_stages_one_in_app_message_for_the_owner_membership`,
`test_redelivery_stages_nothing_more`, `test_missing_or_malformed_recipient_is_a_logged_skip`,
`test_wording_contains_only_integers_from_the_payload`, `test_tenant_rules_are_unchanged`). Infra: the
four tests of §10.3.3 and `tests/infra/test_io_outside_transaction.py` (no handler calls the network).
Frontend: follow-ups page (grouping, filters, completion dialog paths, every state), `FollowUpDialog`,
Overview widgets (each fails alone; budget), Workspace toggles, `DealCard` chips, plus the visual surfaces.

### 10.8 Rollout

1. **Prerequisites** (owner): R2.4 live (the deal consumer and its DLQ), R2.7, and R2.8's reconcile applied
for admins. 2. **Deploy** (`make deploy HOST=… CONFIRM=… REF=<merged ref>`): every image rebuilds
(platform-core changed); migrations run for deal and scheduler; compose and `memory-budget.json` change, so
`preflight.sh`'s memory gate runs (the total is unchanged, below). No event of the new kinds can be produced
in the first minutes after the deploy: a reminder needs a future due time and the first review runs at
06:00 organization-local, so notification, scheduler and deal can restart in any order. 3. **Smoke**
(owner, an admin): create a follow-up due in two minutes; at the due time the bell shows "Follow-up due";
`make edge-logs SERVICE=scheduler` shows no `scheduler.schedule_requested.rejected`. 4. **Reconcile**
(owner): `make edge-reconcile HOST=… CONFIRM=… PRODUCTS=crm` prints the dry run, which must show E20 and
E21 added to `CRM_MANAGER` and `CRM_REP`; read it, then `APPLY=1` (REG E.4 step 4: before the SPA). 5.
**Publish the SPA** (`make edge-web … APP=crm`). 6. **First morning**: read the deal logs for
`deal.review.baseline` (counts only) and confirm no stale or overdue alert was sent that day.

*Flags*: none; each organization's opt-outs are the three `notify_*` columns. *Memory*, as a change
against the running total (§4.5; `memory_budget_totals.py` over `memory-budget.json`, run at the parent commit
and at this one). R3.6 stands on R2.4's deal fold, so the parent's totals already include its +1 MB: **3748 MB
for the ten profiles and 3945 with `admin`**, where a tree without R2.4 would print 3747 and 3944 (V32, REG F8).
Moving the 90 MB `scheduler` from `finance` to core leaves the ten-profile total and the total with `admin`
**unchanged from the parent** (the container is counted once before and once after), so
`EDGE_BUDGET_ACCEPTED_MB` stays where R2.4's release left it. It raises the boxes that run `crm` without
`finance` by 90: core goes from 1772 to **1862** MB (R2.4's fold is in the `crm` profile, so it does not touch
core), and `--profiles crm` goes from **2068** MB (2067 plus R2.4's 1) to **2158** MB; on a tree without R2.4 the
same two figures are 2067 and 2157. The RUNBOOK's cumulative table and its "inventory alone plus `admin`"
example move with it (§10.3.3), and R3.8's lead fold adds its own +1 on top (§12.8).

### 10.9 Rollback

SPA: `make edge-web-rollback APP=crm`. Backend: `make edge-rollback`; tables and columns stay. After a
rollback, jobs armed earlier fire `reminder_requested` into a deal that has no handler: the event has no
bound queue and is dropped (reminders are lost, nothing breaks); jobs the new scheduler cancelled stay
`cancelled`, which the previous scheduler never selects; the compose profile change is reverted by the
same rollback. Not reversible: `follow_ups` rows and reminders already delivered. Stop conditions: any
`scheduler.job.parked` for a `crm.*` target, `deal.events.dlq` or `scheduler.events.dlq` above zero, a
`scheduler.schedule_requested.rejected` for producer `deal`, more than 200 stale or overdue alerts for one
organization in one run, or the review chain failing to re-arm (`review_armed_for` older than yesterday for
an organization with deal writes).

### 10.10 Acceptance criteria (mechanical)

1. `uv run pytest libs/platform-core -q`, `make test-services` and `make migration-roundtrip` pass; every
   committed schema named in §10.3.2 exists and validates through its producer test.
2. Memory, as a change against the parent commit (§4.5): `python3 infrastructure/edge/lib/memory_budget_totals.py
   infrastructure/edge/memory-budget.json --profiles <list>` run at the parent and at this release prints, for
   `""` (core) and for `crm`, **the parent's figure plus 90** (with R2.4 live, which R3.6 requires: 1772 to
   1862 and 2068 to 2158; on a tree without it 2067 to 2157), and for the ten-profile list and the same list
   with `admin` **the parent's figure unchanged** (3748 and 3945 with R2.4 live; 3747 and 3944 without);
   `jq .containers.scheduler.profile infrastructure/edge/memory-budget.json` prints `null`; and the RUNBOOK's
   cumulative table and "inventory alone plus `admin`" example carry the shifted figures of §10.3.3
   (`tests/infra/test_planned_headroom.py` passes).
3. `grep -n "profiles" infrastructure/edge/compose.prod.yml` shows no `profiles:` line inside the
   `scheduler:` block; `PROFILE_DLQ_QUEUES["finance"]` is `("finance.events",)` and `CORE_DLQ_QUEUES`
   contains `scheduler.events`.
4. `python tools/export_permissions.py --check` and `make openapi-check` pass; the OpenAPI document lists the
   seven follow-up operations and `DealOut.attention`.
5. `ls frontend/product-crm/src/routes/Overview.tsx` fails; `make ui-visual` passes with the five new
   surfaces; the Overview request-budget test (at most 10 requests) passes.
6. Every public route of §10.3.1 has a row in `test_authz_matrix.py` and the named tests exist.
7. The follow-up key rule holds: `test_reschedule_back_to_a_cancelled_due_at_still_fires`,
   `test_arm_seq_only_grows_and_no_key_is_requested_twice` and the scheduler's
   `test_a_repeated_request_for_a_cancelled_key_creates_no_job` pass.
8. §10.3.4's documents exist: `docs/crm.md` lists EV19 to EV21, EV26, EV27 and `platform.schedule.cancel_requested`
   (`tests/infra/test_crm_doc_matches_contracts.py` passes), ADR-0015 carries the dated amendment note, and
   `recipes.ts` has the three recipes whose triggers `tests/infra/test_engage_recipe_triggers.py` accepts.
9. The standing criteria of §4.5: S1 to S8.

## 11. R3.7 Lead capture, duplicate detection and assignment

### 11.1 Purpose

Leads should arrive without a rep typing them, and arrive safely (pending D-11, which keeps `lead` its own
service here, D-21 and D-38 through R2.8, and D-27 for what a stored lead may keep). R3.7 adds three intake paths (a public
web form, a signed webhook, a WhatsApp stranger through Engage), each behind abuse controls; it stores
what the form recorded about consent as evidence without ever deciding anything from it; it flags
duplicates against leads and contacts at the moment of capture; it assigns by the existing round-robin, an
endpoint's own default owner, or a bulk reassign; and it clocks every lead against a first-response SLA
and an aging window. The lead page is rebuilt, a Sources page joins the settings hub, and the lead events
finally carry the state their committed schemas already require (EV10-EV12) beside the new
`crm.lead.assigned` (EV15).

**Two deploys** (DD-36). `LeadOut.source` is a `Literal["form", "import", "manual"]`
(`services/lead/app/schemas.py:26`) and `selectors.lead_out` builds it from the stored value
(`selectors.py:96`), so the previous code answers 500 for any list that contains a lead whose source is
`webhook`, `whatsapp` or `storefront`. **R3.7a** ships everything that creates no such lead: migration,
widened sources, SLA and aging, settings, reassign, duplicate check, the events, the lead page, the list
changes, H8, and **the administrator's lead erase with its audit-scrub function** (§11.3.3; this revision creates the function, B86), so that the
means to remove a person's details exists before anything can store a stranger's. **R3.7b** ships what
creates them: the capture endpoints, the public route, the webhook, the internal route, the gateway rows,
Engage's stranger step, the capture secret's deliverables (§11.3.4) and the Sources page. R3.7b ships only
after R3.7a has been deployed, the SPA is published and a day has passed, **and** the ADR
`crm-public-lead-capture` is accepted with the owner's D-27 answer for leads (§4.2); a rollback below R3.7a is
not supported once a captured lead exists (§11.9, K2).

### 11.2 Data model

lead revision `<rev>_lead_capture`, `down_revision` the lead head at merge (R3.1's
`lead_campaign_and_utm`), `# RE-PARENT HERE`; in R3.7a:

```sql
SET LOCAL lock_timeout = '5s';

-- B36  (TenantBase columns, then:)  B63 adds success_url and consent_text_hash
CREATE TABLE lead.capture_endpoints (
    kind              VARCHAR(10)  NOT NULL,
    name              VARCHAR(120) NOT NULL,
    token_prefix      VARCHAR(12)  NOT NULL,
    secret_enc        BYTEA,                                  -- webhook signing secret, sealed; NULL for a form
    allowed_origins   JSONB        NOT NULL DEFAULT '[]',
    success_url       VARCHAR(500),
    consent_text      VARCHAR(500),
    consent_text_hash VARCHAR(64),
    default_owner_id  UUID,
    is_active         BOOLEAN      NOT NULL DEFAULT true,
    last_used_at      TIMESTAMPTZ,
    CONSTRAINT ck_capture_endpoints_kind   CHECK (kind IN ('form', 'webhook')),
    CONSTRAINT ck_capture_endpoints_secret CHECK ((kind = 'webhook') = (secret_enc IS NOT NULL))
);
CREATE UNIQUE INDEX uq_capture_endpoints_org_name ON lead.capture_endpoints (organization_id, name) WHERE deleted_at IS NULL;

-- B37  (TenantBase columns, then:)  one row per organization, lazily created; B63 adds whatsapp_leads_enabled
CREATE TABLE lead.lead_settings (
    first_response_sla_minutes INTEGER,
    aging_after_days           INTEGER NOT NULL DEFAULT 7,
    whatsapp_leads_enabled     BOOLEAN NOT NULL DEFAULT false,
    CONSTRAINT ck_lead_settings_sla   CHECK (first_response_sla_minutes IS NULL OR first_response_sla_minutes BETWEEN 5 AND 43200),
    CONSTRAINT ck_lead_settings_aging CHECK (aging_after_days BETWEEN 1 AND 365)
);
CREATE UNIQUE INDEX uq_lead_settings_org ON lead.lead_settings (organization_id);

-- B34, B35, B38, B39, B60, B62 (the two duplicate flags)
ALTER TABLE lead.leads ADD COLUMN capture_endpoint_id  UUID REFERENCES lead.capture_endpoints (id) ON DELETE SET NULL;
ALTER TABLE lead.leads ADD COLUMN external_ref         VARCHAR(120);
ALTER TABLE lead.leads ADD COLUMN first_response_at    TIMESTAMPTZ;
ALTER TABLE lead.leads ADD COLUMN last_touched_at      TIMESTAMPTZ;
ALTER TABLE lead.leads ADD COLUMN gstin                VARCHAR(15);
ALTER TABLE lead.leads ADD COLUMN consent_evidence     JSONB;
ALTER TABLE lead.leads ADD COLUMN duplicate_of_lead_id UUID;     -- plain UUID: the target may be soft-deleted
ALTER TABLE lead.leads ADD COLUMN duplicate_contact_id UUID;     -- plain UUID into customer
ALTER TABLE lead.leads ADD COLUMN capture_messages     JSONB NOT NULL DEFAULT '[]';   -- B85: the visitor's words, apart from `notes` (DD-44)
ALTER TABLE lead.leads ADD CONSTRAINT ck_leads_capture_messages
    CHECK (jsonb_typeof(capture_messages) = 'array' AND jsonb_array_length(capture_messages) <= 5);
CREATE UNIQUE INDEX uq_leads_org_source_external
    ON lead.leads (organization_id, source, external_ref) WHERE external_ref IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX ix_leads_org_duplicate ON lead.leads (organization_id)
    WHERE duplicate_of_lead_id IS NOT NULL OR duplicate_contact_id IS NOT NULL;
CREATE INDEX ix_leads_org_capture_endpoint ON lead.leads (organization_id, capture_endpoint_id)
    WHERE capture_endpoint_id IS NOT NULL;

-- set-based, idempotent data steps; no PII, no key; counts only in the migration log
UPDATE lead.leads SET first_response_at = updated_at WHERE status <> 'new' AND first_response_at IS NULL;
UPDATE lead.leads SET last_touched_at   = updated_at WHERE last_touched_at IS NULL;
-- ENABLE + FORCE RLS and tenant_isolation on capture_endpoints and lead_settings (§4.1); both join TENANT_TABLES.

-- B86: the audit scrub behind the lead erase (DD-47, §11.3.3). `apply_schema_grants` revokes UPDATE and
-- DELETE on audit_logs from the app role (`libs/platform-core/src/platform_core/db.py:140-146`), so the rewrite
-- is a SECURITY DEFINER function owned by the BYPASSRLS admin role, limited to one lead's rows in one
-- organization and to a fixed key list, and only ever to a tombstone. It has B12's two guards (its helpers act
-- on JSON objects only; it raises unless p_organization_id equals lead.current_org()). This revision is the only
-- one that creates it; R4.7 only calls it (J26).
CREATE FUNCTION lead.scrub_lead_audit(p_organization_id uuid, p_lead_id uuid) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = lead, pg_temp AS $$ ... $$;
ALTER FUNCTION lead.scrub_lead_audit(uuid, uuid) OWNER TO opsgrids_admin;   -- `db_admin_role`, services/lead/app/config.py:14
REVOKE ALL ON FUNCTION lead.scrub_lead_audit(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION lead.scrub_lead_audit(uuid, uuid) TO opsgrids_lead;
```

The `source` column is `VARCHAR(40)` with no CHECK, so widening it needs no DDL; B33 is a change of the
`LeadSource` literal, the OpenAPI enum and the committed event schemas, all in R3.7a. `consent_evidence`
is `{granted: bool, text_hash: string, captured_at: iso8601}`, nothing else in R3; R4.7 adds `ref` inside the
same object (B39). The `secret_enc` CHECK makes "a webhook has a sealed secret and a form has none" a
database fact. The sealed secret is bound to
its row with `aad = f"{organization_id}:{endpoint_id}".encode()` (`pii.seal(..., aad=...)`,
`pii.py:97-119`), so a ciphertext copied to another row does not open.

`capture_messages` is a JSON array of `{at: iso8601, source: "form" | "webhook" | "whatsapp" | "storefront",
text: string (1-2,000 characters)}`, appended and never edited, at most five entries (the CHECK is the net; the
application counts and drops a sixth). It is the one place an anonymous party's words are stored, so that they
never enter the rep-authored `notes` (DD-44). `lead.scrub_lead_audit` rewrites, in `before` and `after` of
every `audit_logs` row with `entity = 'lead'`, `entity_id = p_lead_id` and `organization_id =
p_organization_id`, the keys `full_name` (to `Erased lead`) and `company`, `notes`, `email`, `phone`,
`raw_phone` (to `[erased]`), and returns the number of rows it touched; nothing else in the table, and no
other lead or organization, is reachable through it. This revision is the only one that creates
`lead.scrub_lead_audit` (B86); SP4's R4.7 needs R3.7, so it only calls the function and asserts its two
guards (the helpers act on JSON objects only; it raises unless `p_organization_id` equals `lead.current_org()`),
REG J26. The six keys above contain the four that SP4 lists (`full_name`, `notes`, `email`, `phone`), so one
function serves both erases.

Downgrade: drops the function, the indexes, the columns and both tables; refuses unless
`OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE=1` while any lead has a non-null `external_ref`,
`capture_endpoint_id`, `consent_evidence` or duplicate flag, **or a non-empty `capture_messages`**, or either
table holds a live row. On an empty database it fully reverses.

### 11.3 Backend contract

#### 11.3.1 Authenticated routes (lead; gateway prefix `/api/v1/leads`)

The static paths are declared before `GET /leads/{lead_id}`; the two routes that carry `{lead_id}` (`:erase`,
`/capture-messages`) follow the existing `:convert` pattern. The R3.7a routes:

| Route | Permission, floor | Request | Response | Errors | Named negative-authz test |
|---|---|---|---|---|---|
| `GET /leads/settings` (C40) | `crm.lead.lead.read`; organization filter | none | `LeadSettingsOut {first_response_sla_minutes, aging_after_days, whatsapp_leads_enabled, materialised}`, defaults and `materialised: false` when no row, never a write | 401, 402, 403 | `test_lead_settings_read_requires_lead_read` |
| `PUT /leads/settings` (C40) | `crm.lead.settings.write`, `minimum=Scope.ORG` | `LeadSettingsIn` (`extra="forbid"`; SLA `null` or 5-43200 minutes; aging 1-365) | `LeadSettingsOut`; one audit row `lead_settings.updated` | 422 | `test_lead_settings_write_below_org_floor_is_403`; `test_lead_settings_write_requires_the_settings_code` |
| `POST /leads:reassign` (C37) | `crm.lead.lead.write`, `minimum=Scope.TEAM`, each lead re-checked with `assert_can_touch` | `{lead_ids: uuid[] (1-200), owner_id}` | `{updated: int, skipped: [{lead_id, reason}]}`, reasons `converted`, `conversion_in_progress`, `already_assigned`, `out_of_scope`, `not_found` | 422 `invalid_ids`, `owner_not_active` (the target is not an active rep, through `owners:validate`) | `test_reassign_below_team_floor_is_403`; `test_reassign_skips_out_of_scope_leads_and_reports_them`; `test_reassign_reports_another_organizations_ids_as_not_found` |
| `POST /leads:check-duplicates` (C38) | `crm.lead.lead.write`; the lead half is cut by `ctx.apply`; **the contact half runs only when the caller holds `crm.book.contact.read` at ORG** (`ctx.principal.scope_for(...)` is `Scope.ORG`). No floor of its own: the route returns only what the caller could already list (programme §7: a customer-granted OWN rung cannot widen it) | `{email?, phone?}`, at least one (a POST so identifiers stay out of URLs) | `{leads: [{id, status, owner_id}], contacts: [{id}], contacts_checked: bool}`, ids only. Without the ORG contact read the customer call is never made and the answer is `contacts: []`, `contacts_checked: false`, which says nothing about whether a contact exists. Customer's `contacts:resolve` (C46) answers a contact id only, so no `company_id` is returned: the page resolves the company through the scoped `ids=` read | 422 `needs_identifier` | `test_check_duplicates_requires_lead_write`; `test_check_duplicates_never_returns_out_of_scope_leads`; `test_check_duplicates_skips_the_contact_half_without_contact_read` (the customer client is not called); `test_check_duplicates_skips_the_contact_half_for_an_own_contact_grant` |
| `GET /leads` (C36) | `crm.lead.lead.read` | the C35 vocabulary plus `owner=unassigned`, `sla`, `aging`, `capture_endpoint_id`, `duplicate` (`true`) | `Page[LeadOut]`; `LeadOut` gains `first_response_at`, `last_touched_at`, `sla`, `aged`, `capture_endpoint_id`, `capture_endpoint_name`, `gstin`, `consent_evidence`, `capture_message_count`, `possible_duplicate` (always present), `duplicate_of_lead_id` (only when that lead is inside the caller's `ctx.apply` scope, one batched id lookup per page, else null) and `duplicate_contact_id` (only when the caller holds `crm.book.contact.read` at ORG, else null). The visitor's messages are not in a list row (see the next row) | 422 | existing and `test_lead_filters_cannot_widen_own_scope`; `test_lead_out_hides_an_out_of_scope_duplicate_lead_id`; `test_lead_out_hides_duplicate_contact_id_without_org_contact_read`; `test_possible_duplicate_is_visible_to_every_lead_reader` |
| `GET /leads/{lead_id}/capture-messages` (C109) | `crm.lead.lead.read` + `assert_can_touch(owner_id=lead.owner_id)` | none | `{items: [{at, source, text}]}`, oldest first, plain text | 404 | `test_capture_messages_out_of_scope_is_403_for_own_scope`; `test_capture_messages_cross_tenant_is_404`; `test_capture_messages_never_appear_in_a_list_row` |
| `POST /leads/{lead_id}:erase` (C108, §11.3.3) | `crm.lead.settings.write` (E22), `minimum=Scope.ORG`, `amr={"otp","stepup"}` | `LeadEraseIn {confirm_lead_id}` (`extra="forbid"`; must equal the path id) | 200 `LeadEraseOut {lead_id, erased_at, audit_rows_scrubbed}` | 404 (foreign, missing or already erased: one body); 409 `conversion_in_progress`; 422 `confirm_mismatch` | `test_lead_erase_below_org_floor_is_403`; `test_lead_erase_requires_the_settings_code`; `test_lead_erase_without_step_up_is_403`; `test_lead_erase_cross_tenant_is_404` |

`sla` is computed at read time by the pure `domain.sla_state(created_at, first_response_at, sla_minutes,
now)`: `off` (no SLA configured); `met` (responded within the window); `missed` (responded after it);
`breached` (not responded, deadline passed); `due_soon` (not responded, less than a quarter of the window
left, never less than five minutes); `ok`. The `sla` query parameter accepts `breached`, `due_soon`, `ok`,
`met`, `missed`. `aged` is true for a lead whose status is `new`, `working` or `qualified` and whose
`COALESCE(last_touched_at, created_at)` is earlier than the start of the local day `aging_after_days`
days ago; the `aging` parameter accepts `aged`. Both read the settings row without creating it.

The R3.7b routes:

| Route | Permission, floor | Request | Response | Errors | Named negative-authz test |
|---|---|---|---|---|---|
| `GET /leads/capture-endpoints` (C39) | `crm.lead.lead.read` | none | `{items: CaptureEndpointOut[], org_blocked_24h: int \| null, capture_configured: bool}`; an item is `{id, kind, name, token_prefix, endpoint_token, allowed_origins, success_url, consent_text, consent_text_hash, default_owner_id, is_active, last_used_at, created_at, blocked_24h: int \| null, discarded_24h: int \| null}`. The signing secret is **never** in a read. The three counters are Redis reads of the posts refused by a spent budget and the honeypot posts discarded in the last 24 hours (§11.3.2 step 2); they are `null` when Redis cannot answer, and a failed counter never fails the read. `capture_configured` is false while the server has no valid signing secret (§11.3.4), and `endpoint_token` is then null | 401, 402, 403 | `test_capture_endpoint_list_requires_lead_read`; `test_capture_endpoint_read_never_contains_a_secret`; `test_capture_endpoint_read_reports_blocked_and_discarded_counts`; `test_counters_are_null_when_redis_is_down_and_the_read_still_answers` |
| `POST /leads/capture-endpoints` (C39) | `crm.lead.settings.write`, `minimum=Scope.ORG` | `{kind, name (1-120), allowed_origins?, success_url?, consent_text?, default_owner_id?}` (`extra="forbid"`) | 201 `CaptureEndpointOut` plus, for a webhook, `signing_secret` (**shown once**) | 409 `duplicate_endpoint_name`; 422 (an origin that is not `https://host[:port]`, a `success_url` off the allowed origins, `owner_not_active` for a `default_owner_id` that is not an active rep, `capture_not_configured` while the server has no valid signing secret) | `test_capture_endpoint_create_below_org_floor_is_403`; `test_capture_endpoint_create_requires_the_settings_code`; `test_endpoint_create_is_422_while_the_secret_is_not_configured` |
| `PATCH /leads/capture-endpoints/{endpoint_id}` (C39) | same | `{name?, allowed_origins?, success_url?, consent_text?, default_owner_id?, is_active?}` (`kind` immutable) | `CaptureEndpointOut`; audit `capture_endpoint.updated` (a consent-text change records the old and new text: it is not personal data, and it is how a stored hash is ever mapped back to words) | 404, 409, 422 | `test_capture_endpoint_patch_cross_tenant_is_404` |
| `DELETE /leads/capture-endpoints/{endpoint_id}` (C39) | same | none | 204 (soft delete; later posts to its token answer as an unknown endpoint) | 404 | `test_capture_endpoint_delete_requires_the_settings_code` |
| `POST /leads/capture-endpoints/{endpoint_id}:rotate-secret` (C39) | same | none | `CaptureEndpointOut` plus a new `signing_secret` (shown once); the old secret stops working at once | 404, 422 `kind_not_rotatable` (a form has no secret and its token is permanent: delete and create) | `test_rotate_secret_below_org_floor_is_403`; `test_rotate_on_a_form_is_422` |

Lead's `create_app(...)` call gains `trust_forwarded_for=True` (`services/lead/app/main.py:8-18`): on the
public path the immediate peer is the gateway container (a private address) and `X-Forwarded-For` carries
Caddy's observation of the visitor as its rightmost entry, which `platform_core.proxy_headers.real_client_host`
reads (`proxy_headers.py:54`) and `request.client.host` then reflects. Only the gateway ever sets it
today (`services/app-gateway/app/main.py:213`); the cost of enabling it on lead is that a container on the
`back` network could forge an address to dodge a per-address budget, which is inside the trust boundary and
bounded by the per-endpoint budget (DD-37).

#### 11.3.2 Public and internal capture (R3.7b)

**`POST /leads/public/capture` (C41)**, anonymous, `Content-Type` `application/json` or
`application/x-www-form-urlencoded` (DD-23). Body `{form_token, full_name, email?, phone?, company?,
gstin?, message?, consent?: {granted, text_hash}, utm_source?, utm_medium?, utm_campaign?, utm_term?,
utm_content?, marketing_campaign_id?, opsgrids_hp?}`; the urlencoded spelling of `consent` is the two fields
`consent_granted` and `consent_text_hash`; `opsgrids_hp` is the honeypot (a name no browser or password
manager autofills, which `website`, `url` or `company` are, and which the embed snippet hides from every
user, §11.6.3). Order of work, each step before the next and none of the first three touching the database:

1. **MAC.** `form_token` is `<organization_id>.<endpoint_id>.<mac>` (base64url parts, `mac` =
   HMAC-SHA256 of the first two parts under `capture_token_secret`, F12), compared in constant time. Bad
   shape, bad MAC, anything else wrong in this step: the one `400 invalid_capture_request`. While
   `capture_enabled` is false (the secret is empty, short or a development value, §11.3.4) this is the answer
   to every request, so a missing secret is never a key anyone can compute.
2. **Budgets** (Redis, `platform_core.cache.enforce_rate_limit`, **fail closed**), four of them (DD-37,
   DD-46): per address and endpoint `leadcap:ip:{endpoint_id}:{ip}` (default 20 per hour), per endpoint
   `leadcap:ep:{endpoint_id}` (default 300 per hour), per organization **for anonymous form posts**
   `leadcap:orgform:{organization_id}` (default 500 per day) and, for signed webhooks only,
   `leadcap:orghook:{organization_id}` (default 5,000 per day). **The two organization buckets never share a
   counter**: a form post spends only the first and a webhook only the second, so a form token (public by
   design, K8) can exhaust the anonymous allowance and never the one an authenticated integration relies on.
   The limits are lead `Settings` fields with these defaults (`capture_ip_hourly_limit`,
   `capture_endpoint_hourly_limit`, `capture_org_daily_limit`, `capture_webhook_org_daily_limit`); the defaults
   live in code, and raising one is a reviewed compose change, not a runtime switch. A spent budget is
   `429 rate_limited` with `Retry-After`; an unreachable Redis is `503` (the repo's choice for a limiter that
   cannot answer, `cache.py:106-132`). **Every refusal is counted where the tenant can see it**: a refusal by
   any budget increments `leadcap:blocked:ep:{endpoint_id}:{yyyymmdd}`, a refusal by an organization bucket
   also increments `leadcap:blocked:org:{organization_id}:{yyyymmdd}`, and a discarded honeypot post
   increments `leadcap:honeypot:ep:{endpoint_id}:{yyyymmdd}` (each with a two-day expiry; an increment that
   fails is logged and never changes the response). The Sources page reads the three counters (§11.3.1,
   §11.6.3). The gateway's own 60 per minute per address sits in front.
3. **Entitlement.** `crm` must be among the organization's entitlements, read from product-service's
   existing internal route through `product_service_url` (F12, W36) with a 60-second positive cache; not
   entitled is the same `400 invalid_capture_request`; product-service unreachable with no answer younger
   than ten minutes is `503` (L12).
4. **Endpoint** (one short read-only tenant transaction on the app role: `session_scope(
   organization_id=…)`): it must exist, be active and not deleted, and be a `form`; if the request carries
   an `Origin` and the endpoint lists origins, the origin must be listed (a soft check: it blocks
   mis-embedded pages, not an attacker, DD-23); otherwise `400 invalid_capture_request`.
5. **Validation.** Pydantic with `extra="ignore"` for the browser's own fields and the caps of the table
   in §11.4; at least one of `email` or `phone` (`422 needs_email_or_phone`); `consent` required when the
   endpoint has `consent_text` (`422 consent_required`); an invalid `gstin` is dropped, never a refusal.
   A non-empty `opsgrids_hp` answers the success response below and stops here: nothing is stored and the
   post is counted in the endpoint's `discarded_24h`, so an administrator who sees real leads missing can see
   the discard count move. A real visitor loses a lead only if something fills a field that is off-screen,
   unlabelled, `tabindex="-1"`, `autocomplete="off"`, `aria-hidden` and named so that no autofill matches it
   (§11.6.3).
6. **Duplicate lookups, no transaction open**: customer `contacts:resolve` with the plaintext email and
   phone (C46, caller `lead`, 2-second budget; any failure leaves `duplicate_contact_id` unset), and the
   round-robin snapshot (`_resolve_round_robin_snapshot`) unless the endpoint names a `default_owner_id`,
   which is validated active through `owners:validate` (an inactive default falls back to the rotation and
   logs `lead.capture.default_owner_inactive`).
7. **One write transaction.** The canonical email's `email_index` is checked first: an existing live lead
   gets `last_touched_at` set and the message appended to its **`capture_messages`** as one entry `{at,
   source, text}` (a sixth entry is dropped and counted as `lead.capture.messages_full`; `notes` and every
   other rep-edited field are never touched, DD-44), one audit row (`acting_as="capture:form"`, ids and
   enumerations only) and `crm.lead.updated` (`changed: ["capture_messages"]`), and no second lead exists
   (DD-24). Otherwise the oldest live lead with the same `phone_index` becomes `duplicate_of_lead_id`, the
   contact from step 6 becomes `duplicate_contact_id`, the owner is the default owner or the rotation's
   next, and the lead is inserted (source `form`, `capture_endpoint_id`, `consent_evidence = {granted,
   text_hash, captured_at}`, `gstin`, UTM fields, `capture_messages = [the message]` when one was sent,
   `notes = ''`, `last_touched_at = now`) with its audit row (`acting_as="capture:form"`, ids and enumerations
   only), `crm.lead.created` and `crm.lead.assigned` (`assigned_by` `round_robin` or `manual`); the
   endpoint's `last_used_at` is written only when older than a minute.
8. **Response.** Identical whether the lead is new, merged or discarded: JSON `{"ok": true}` with 200; for
   an urlencoded post, `303 See Other` to the endpoint's `success_url` when it has one (validated at write
   time against the allowed origins, so it is never an open redirect) and otherwise a fixed page "Thanks.
   We'll be in touch." Validation failures on an urlencoded post return the JSON problem (a limitation:
   the embed code uses `required` and `type=email` so a normal visitor never meets it).

The route is in `LEAD_PUBLIC_PATHS = frozenset({"/api/v1/leads/public/capture"})` unioned into
`public_paths` beside `STOREFRONT_PUBLIC_PATHS` and `ACCOUNT_PUBLIC_PATHS`
(`services/app-gateway/app/main.py:170`; C69), and both route tables gain `{ "prefix":
"/api/v1/leads/public", "upstream": <lead>, "product": "crm", "max_body_bytes": 16384 }`
(`services/app-gateway/routes.json` next to `:81`, `infrastructure/edge/routes.prod.json` next to `:109`;
REG C69 carries the frozenset and the row). The public branch skips the entitlement gate and
forwards no organization, at most the host's `x-org-slug` (`gateway.py:586-609`), which is why steps 1, 3
and 4 live in lead.

**`POST /webhooks/leads` (C42)**, signed. In `WEBHOOK_PUBLIC_PATHS` as `{"POST"}` and a route-table row
with `max_body_bytes` 65536 in both tables (C70). Headers `X-Opsgrids-Endpoint` (the same token shape, its
MAC checked before any database work), `X-Opsgrids-Timestamp` (unix seconds, within 300 seconds of the
server clock) and `X-Opsgrids-Signature` (`v1=` and the hex HMAC-SHA256 of `<timestamp>.` followed by the
**raw body bytes**, under the endpoint's secret, unsealed with its `aad`, compared in constant time). Body:
`external_ref` (required, 1-120), `full_name`, `company?`, `email?`, `phone?`, `message?`, `gstin?`, UTM
fields, `marketing_campaign_id?`. Every failure of the token, the clock, the signature or the endpoint is
the same `400 invalid_capture_request`. After it passes: the per-endpoint budget of step 2 and the
organization's **webhook** bucket (`leadcap:orghook:…`; the per-address budget does not apply to a signed
sender, and the anonymous organization bucket is neither checked nor spent, DD-46), a refusal being a `429`
counted like a form's; then steps 6 and 7 with source `webhook` and **idempotency on `(source,
external_ref)`**: a body replayed inside the five-minute window returns the existing lead, there is no replay
store. A `message` goes to `capture_messages` like a form's. Because the sender is authenticated the response
is `{lead_id, created}` (200 either way).

**`POST /internal/v1/organizations/{organization_id}/leads:capture` (C56)**, in the new
`services/lead/app/api/internal.py`, callers `notification` and `storefront`, org claim required, the
three negative tests (no token 401, wrong caller 403, wrong organization claim 403). Body `{source
(whatsapp|storefront), external_ref, full_name, company?, gstin?, email?, phone?, message?,
consent?: {granted, text_hash}, marketing_campaign_id?, utm_*?}`; idempotent on `(source, external_ref)`; raw
email and phone are sealed on arrival and a `message`, when sent, goes to `capture_messages` (DD-44). A
`consent` block is stored on the lead as evidence only (B39). A block with `source = "storefront"` is stored
and **never forwarded by lead**: storefront writes its own ledger row through C58 (W25). C58 accepts
`source = "web_form"` only, so from R4.7 only a form lead is forwarded, never a storefront or WhatsApp lead.
The same entitlement check (not entitled answers `skipped_reason: "not_entitled"`, a 200, REG J27), and for
source `whatsapp` the organization's `whatsapp_leads_enabled` (off answers `skipped_reason: "disabled"`) and an
hourly cap (default 60, `skipped_reason: "rate_limited"`). Response (REG C56: `lead_id` may be null
and `skipped_reason` is present) `{lead_id, created, duplicate_of_lead_id, duplicate_contact_id,
skipped_reason}`.

**Engage's stranger step** (DD-39, DD-45; notification, W21; `lead_internal_url` and `lead_timeout_seconds` on
its `Settings`, `LEAD_INTERNAL_URL: http://lead:8000` in its compose block). `fold_inbound_contact`
(`consumers.py:190-262`) is unchanged. A new step, `capture_stranger_lead(envelope)`, runs after it in
`on_inbound_received` (`consumers.py:332`), **claim, call, record**, and is **best effort**: (1) in one short
tenant transaction it checks the contact fold is done for this event, the conversation exists, `contact_id IS
NULL` (a definite stranger, not an outage) and no ledger row `uuid5(event_id, "stranger_lead")` exists,
consults the definite-no cache below, and opens the sealed number; (2) with no transaction open it calls C56
with `source="whatsapp"`, `external_ref=str(conversation_id)`, the number, and `full_name = f"WhatsApp
{conversation.wa_address_masked}"` (a placeholder the rep renames; the masked form only, never the number);
(3) in a second transaction it marks the ledger row.

*Entitlement and the switch.* Notification holds no entitlement read and this release adds none (W36 has no
notification-to-product row), and `whatsapp_leads_enabled` lives in lead, so notification can know neither
answer: only lead can. The step asks lead and keeps an in-process cache, per organization, of a **definite no**
(`skipped_reason` `not_entitled` or `disabled`) for five minutes. An Engage-only organization, or a CRM
organization that never opted in, therefore costs at most one cheap call per five minutes per notification
process rather than one per stranger, and turning the switch on takes up to five minutes to apply on a busy
box (the Sources page says so). `rate_limited` is a definite answer for this event too (marked processed, not
cached).

*An unreachable lead is a counted skip, not an error.* A connection error, a timeout, a 5xx, a refused token,
a box with no lead container (no `crm` profile) and an unset or unresolvable `LEAD_INTERNAL_URL` (its default
points at `localhost`, which refuses the connection) all end the same way: one `inbound.stranger_lead.skipped`
log line with `reason=unreachable` (ids and counts only), **no ledger row, no exception, no redelivery**. The
ledger row is per event, so the same stranger's next message tries again; a stranger who writes once while
lead is down gets no lead, which is the accepted cost (K24). Nothing here can dead-letter `notification.events`,
so a lead outage never trips ops-monitor's DLQ alert. The contact fold in front of this step keeps its own
coupling to customer, unchanged: `ContactResolutionUnavailable` still redelivers once and then dead-letters, and
on a box where it raises this step does not run for that delivery.

#### 11.3.3 Lead erase (R3.7a; C108, DD-47; pending D-27)

`POST /leads/{lead_id}:erase` is the means of removing a person's details from the CRM, and it exists **before**
anything can store a stranger's (§11.1, §4.2). It is an administrator's act on one lead: `crm.lead.settings.write`
(E22, admin templates only) with `minimum=Scope.ORG` and `amr={"otp","stepup"}`, the step-up a reveal needs, so
no permission code is added; a dedicated `crm.lead.lead.erase` is the alternative that D-27 puts to the owner. The
body is `LeadEraseIn {confirm_lead_id: uuid}` (`extra="forbid"`, equal to the path id or 422 `confirm_mismatch`).
In one transaction:

1. **Refused while a conversion is live** (`domain.claim_is_live`): 409 `conversion_in_progress`. A converted lead
   may be erased: only the lead's own copy is anonymised, and the company, contact and deal it created are
   untouched (the contact's erase is R4.1's).
2. **Anonymise in place and soft-delete.** `full_name` becomes `Erased lead`; `company`, `email_enc`,
   `email_masked`, `email_index`, `phone_enc`, `phone_masked`, `phone_index`, `gstin`, `external_ref`,
   `duplicate_of_lead_id` and `duplicate_contact_id` become NULL; `notes` becomes empty and `capture_messages`
   `[]`; `deleted_at` is set and `updated_by` stamped. **Kept**: `id`, `source`, `status`, `owner_id`,
   `capture_endpoint_id`, the UTM and campaign columns, `consent_evidence` (a boolean, a hash and a time: no
   identifier), the `converted_*` ids, `first_response_at`, `last_touched_at` and the timestamps. What remains
   is an anonymous, soft-deleted tombstone: no list, count or filter shows it, and nothing in it identifies the
   person.
3. **Rewrite the audit copies.** `lead.scrub_lead_audit(org, lead_id)` (B86, §11.2) rewrites the PII keys in
   `before` and `after` of every audit row of this lead and returns the count as `audit_rows_scrubbed`.
4. **One audit row and one event, ids only.** `lead.erased` with `after = {"audit_rows_scrubbed": n}`, and
   `crm.lead.erased` (EV53: `organization_id, lead_id`; a committed schema and a producer-side test, not
   subscribable, no consumer in this programme). Erasing the same lead again is a 404, like any deleted record.
5. **What an erase does not reach**, stated in the dialog: free text a rep typed about the person on a company,
   contact, deal or activity (append-only deal text is SP4's D-27 residue); the sealed number in notification's
   conversation (SP4's contact erase owns that); backups and write-ahead logs.

There is **no bulk erase**: junk is disposed of by bulk disqualification through the Excel bulk-update engine
(ADR-0022), which keeps the junk's details, and an administrator erases the individual leads a person asks to
have erased. The key `(source, external_ref)` is freed with the row, so the same sender's next post or message
can create a new lead; pausing the source, or switching the WhatsApp setting off, stops that.

#### 11.3.4 The capture secret (R3.7b; REG F12)

`capture_token_secret: SecretStr` on lead's `Settings` is the HMAC key of the form token and of the webhook's
`X-Opsgrids-Endpoint` header. Making it real is a list of R3.7b deliverables, each in the same commit as the
first line that uses it, following `ENGAGE_UNSUBSCRIBE_SECRET` end to end
(`services/notification/app/config.py:108`, `infrastructure/edge/compose.prod.yml:1194`,
`infrastructure/edge/.env.example:746-755`, `docs/deploy/ENV_SOURCE_MATRIX.md:177`,
`docs/deploy/env-reference.md:269`, `infrastructure/edge/RUNBOOK.md:3109`):

| Deliverable | Content |
|---|---|
| lead `Settings` | `capture_token_secret: SecretStr = SecretStr("dev-only-capture-token-secret")` and a property `capture_enabled`, true only when the value is at least 32 bytes (`len(value.encode())`, the rule `ServiceSettings` applies to its own keys) and does not start `dev-only-` or `ci-only-`. An empty, short or development value is **capture off**: every capture route answers the one 400, `POST /leads/capture-endpoints` answers 422 `capture_not_configured`, and reads return `capture_configured: false` (§11.3.1). The service still starts, the way a placeholder unsubscribe secret turns signing off rather than stopping notification |
| compose | in lead's block, `CAPTURE_TOKEN_SECRET: ${CAPTURE_TOKEN_SECRET}` with **no default**, beside `CUSTOMER_INTERNAL_URL` and `DEAL_INTERNAL_URL` (`compose.prod.yml:1481-1482`) and never in `x-service-env`; `PRODUCT_SERVICE_URL` is the shared line that already exists (`:104`) |
| `.env.example` | an entry in the documented block style (`>> CAPTURE_TOKEN_SECRET`: required, secret, generate with `openssl rand -hex 32`, consumer lead, rotation invalidates every issued form token and webhook header). `preflight.sh` takes its required keys from this file (`:37`) and refuses a missing, empty, `dev-only-` or `ci-only-` value (`:240-274`), so **the owner must have set the key in the box `.env` before the R3.7b deploy**; "capture stays off with the dev default" describes local development and CI only |
| `infrastructure/edge/lib/secret-strength.sh` | `CAPTURE_TOKEN_SECRET` joins the keys `secret_strength_problems` checks for 32 bytes (the library mirrors the services' own startup rules, and its header now says it also mirrors lead's `capture_enabled`); `tests/infra/test_preflight_secrets.py` gains the empty, 31-byte and 32-byte cases |
| documents | `docs/deploy/ENV_SOURCE_MATRIX.md` (a category-A row: generated on the box, rotating breaks every embedded form and webhook sender), `docs/deploy/env-reference.md`, and the RUNBOOK's secrets table (`:3109`) |
| tests | `tests/infra/test_capture_secret_wiring.py` (the compose line exists, has no default, and sits only in lead's block; `.env.example` has the key; the matrix and `env-reference.md` name it); lead's `test_empty_secret_means_capture_is_off`, `test_short_secret_means_capture_is_off`, `test_dev_only_and_ci_only_secrets_mean_capture_is_off`, `test_a_32_byte_secret_enables_capture`, `test_no_token_verifies_while_capture_is_off` |

#### 11.3.5 Documents (the same commits)

* **The ADR `crm-public-lead-capture`** (REG I2; expected number 0041, **confirmed against `docs/decisions.md`
  when the file is written**; status `Accepted` before R3.7b deploys, §4.2). It records the token and header
  shapes (C41, C42), the replay rule (the five-minute window and `(source, external_ref)`), the entitlement check
  (L12), the capture secret and what rotating it costs (F12), the four budgets and the bucket split (DD-37,
  DD-46), and **the data-subject treatment of a captured lead**: what a lead holds, what the erase clears
  and keeps (DD-47, D-27), and the one-way consequences (K2, K6). Its row is added to `docs/decisions.md` in the
  same change (REG I6).
* **`docs/crm.md`** (REG I3): the event table gains EV15 and EV53 and the widened fields of EV10 to EV12;
  the service map gains the two gateway prefixes (`/api/v1/leads/public`, `/api/v1/webhooks/leads`) and the
  internal capture route.
* **REG I8**: H8's `--service lead` steps and the `CAPTURE_TOKEN_SECRET` rows of §11.3.4 go into
  `docs/deploy/ENV_SOURCE_MATRIX.md`, `docs/deploy/env-reference.md` and the RUNBOOK.

### 11.4 Domain rules

1. **Caps and shapes** (one table, tested): `full_name` 1-160; `company` 200; `email` 254, canonicalised
   `strip().lower()`; `phone` canonical E.164 (`domain.canonical_e164`) or the field is a 422; `message`
   2,000; `gstin` through A15 (`parse_gstin`) and dropped when invalid; each UTM 120; `marketing_campaign_id`
   a UUID, never looked up; `external_ref` 120; `capture_messages` at most five entries of 2,000 characters,
   and `notes` is never written by any capture path (DD-44).
2. **Canonical email index** (DD-26): lead's `email_index` is `blind_index(canonical, kind="email")`.
   Existing rows hashed the exact string; H8 gains `--service lead --target email_index` to rebuild them,
   skipping and counting any row whose canonical index would collide with another live lead's (the unique
   index `uq_leads_org_email_index` is the net). Until it has run, a case variant of an older lead's email
   can still create a second lead.
3. **Duplicate flags** (DD-25): written once at capture, never recomputed. `duplicate_of_lead_id` is the
   oldest live lead with the same `phone_index` (a shared office line is legal, so it is a flag, never a
   refusal or a merge, DD-4); `duplicate_contact_id` is customer's answer for the email or phone. A
   manually created lead (`POST /leads`) runs the same lookups when its form has an email or phone, with
   the same best-effort budget, and its contact half runs only for a caller who holds `crm.book.contact.read`
   at ORG (customer's `contacts:resolve` ignores the caller's scope, so lead decides who may ask). **What a
   reader sees is a permission question, not a storage one** (DD-25): `possible_duplicate` is a boolean every
   lead reader gets; `duplicate_of_lead_id` is returned only when that lead is inside the reader's `ctx.apply`
   scope; `duplicate_contact_id` only to a holder of `crm.book.contact.read` at ORG. A value outside that
   reads as null, so the response never confirms a record the caller could not open.
4. **First response and touch** (DD-28): `first_response_at` is set, once, by the first change of a lead's
   status away from `new` (`update_lead`, `reassign` does not count); `last_touched_at` is set by every
   `update_lead`, every reassignment, every merged capture and conversion. Reads never write.
5. **Assignment.** Round-robin is today's (`_assign_owner`, inactive reps skipped, fail closed when the
   directory is unreachable); an endpoint's `default_owner_id` wins when active; `crm.lead.assigned` is
   published for every assignment: `assigned_by` is `round_robin` (the rotation), `manual` (an explicit
   owner on create, or an endpoint default) or `reassign` (C37). Its payload carries `owner_membership_id`
   equal to `owner_id` after R2.7 and `previous_owner_*` for a reassignment (§D rule).
6. **Reassign** (C37) locks the named leads in id order in one transaction, skips (never fails) a converted
   lead, a lead with a live conversion claim (`domain.claim_is_live`), a lead already owned by the target,
   and one the caller cannot touch; each change writes one audit row and `crm.lead.updated`
   (`changed: ["owner_id"]`) beside `crm.lead.assigned`. Until R3.8 wires TEAM (A14) only an ORG-scope
   holder passes the floor (registry C37).
7. **Consent is evidence, not a decision** (B39, P§7): the stored object is read for display only. No
   code in lead or any other service reads it to decide whether anyone may be messaged; notification's
   ledger decides (ADR-0021). The forwarding to the ledger is R4.7's, and only a form lead's: C58 accepts
   `source = "web_form"` only, and a storefront lead's consent is written by storefront itself (C56, W25).
8. **Events (gap closure).** `crm.lead.created` emits `status` and widens `source`;
   `crm.lead.updated` emits `status, source, owner_id, converted_at` as the state after the patch;
   `crm.lead.converted` emits `owner_id, converted_at`. The three committed schemas lose their "Ahead of the
   producer" `$comment`, widen `source` to `form, import, manual, webhook, whatsapp, storefront`, and
   `crm.lead.converted`'s description stops saying the contact is null when the lead had no email (R3.1b:
   a phone-only lead has one). `crm.lead.assigned` (EV15) and `crm.lead.erased` (EV53) each get their own
   committed schema. All in the commit of the producer change, with producer tests through `create_lead`,
   `update_lead`, `finalize_conversion`, `capture_lead`, `reassign_leads` and `erase_lead` and
   `validate_event_schema`. Closing the gap also deletes the lead rows of SP1's `AHEAD_OF_PRODUCER` pin table in
   `services/lead/tests/test_event_contracts.py` and the three names from
   `test_the_ahead_of_producer_schemas_are_exactly_the_pinned_eight` (SP1 §9.2.2).
9. **An erased lead is invisible and keeps no identity** (§11.3.3): it is soft-deleted, so every existing
   selector already excludes it, and its email index, phone index and `(source, external_ref)` are released.
   Nothing reads a sealed column of an erased row, and no code path un-erases one.

### 11.5 Consumers and idempotency

| Handler or path | Idempotency |
|---|---|
| public capture | merge on the canonical email index; the unique index `uq_leads_org_email_index` as the net under concurrency (a `begin_nested` savepoint and the `IntegrityError` mapped to the merge path, the shape `create_lead` has); an honeypot post stores nothing; a merge appends to `capture_messages` and never to `notes` |
| webhook capture | `(source, external_ref)` unique partial index; replay inside the window returns the existing lead |
| internal capture | the same key; Engage's step is also guarded by its own ledger row `uuid5(event_id, "stranger_lead")`, so a redelivery calls lead at most once more and lead answers `created: false` |
| reassign | each lead is changed only when its owner differs; a repeated request reports `already_assigned` |
| lead erase | the row is soft-deleted, so a second request is a 404 and writes nothing; the audit scrub rewrites keys to a fixed tombstone, so running it twice changes nothing more; the event is staged once in the same transaction |
| Engage's stranger step | its ledger row per event; a counted skip writes no row, so the next message from the same stranger tries again (DD-45) |
| H8 | rows already indexed are skipped; resumable with `--pace` |

No new queue consumer is added in R3.7 (lead's fold is R3.8's, F9).

### 11.6 UI

#### 11.6.1 Leads list additions (R3.7a)

The R3.4 screen keeps its structure and gains:

* **Columns**: a **SLA** column (a `StatusBadge` with words: Within SLA, Due soon, Breached, Met, Missed; the
  column is absent while no SLA is configured), a **Source** label (Web form, Webhook, WhatsApp, Online
  store, Added by hand, Import), the owner shown as the word **Unassigned** when empty, and under the lead's
  name a **Possible duplicate** `Tag` when `possible_duplicate` is true (the boolean every lead reader gets,
  never an id the reader may not open, §11.4 rule 3).
* **Filters** (URL keys per §4.4.3): the owner select gains **Unassigned** (`owner=unassigned`), **SLA**
  (`sla=`), an **Aged** chip (`aging=aged`), a **Possible duplicates** chip (`duplicate=true`) and the
  extended **Source** options. The status options keep their counts from `GET /leads/summary`, which also
  supplies the **Unassigned (n)** chip.
* **Bulk**: **Reassign**, shown only when `useCan("crm.lead.lead.write", Scope.TEAM)`, opens
  `ReassignDialog`: an active-rep `Combobox`, "Reassign {n} leads", then a result view "Reassigned 18.
  Skipped 2." with the skipped leads listed by name (resolved with `ids=`) and a reason in words
  (Already converted, Conversion in progress, Already with them, You can't change this lead, Not found).
  The count is capped at the 100 selectable rows of a page.
* **Empty state** names the web-form route only for a caller who can write lead settings and only once the
  Sources page exists.

#### 11.6.2 Lead page `/leads/:id` (gate `crm.lead.lead.read`)

`RecordPage` (§9.6.1). Header: name, company, `StatusBadge`; actions **Qualify**, **Disqualify**
(`ConfirmDialog`), **Convert** (qualified only; `ConfirmDialog`; success links to the new company, contact
and deal), **Edit**, **Reassign** (`useCan(write, TEAM)`) and, in the menu, **Erase personal data...** (shown
only when `useCan("crm.lead.settings.write", Scope.ORG)`; the dialog is described after the Overview). A converted lead renders read-only with a banner
"Converted on {date}" and the three links, and its controls are disabled with the server's own reason ("A
converted lead can't be changed"). Tabs: **Overview**, later **Follow-ups** (R3.6, `lead_id`) and
**History** (R3.8). A lead has no activity timeline: activities attach to deals, companies and contacts
(V7), which the page says in its empty "Next steps" line ("Log calls and notes on the contact or deal once
the lead is converted; use a follow-up to remember the next step").

Overview, main column: an `InlineAlert` (warning tone, words not colour) when `possible_duplicate` is true:
"Possible duplicate: {name} is already a lead owned by {rep}" linking to it when `duplicate_of_lead_id` is
returned, "{name} is already a contact at {company}" linking to `/contacts/{id}` when `duplicate_contact_id`
is returned (names resolved with `ids=`), and, when the flag is set but the API returned no id for the
caller (the permission rules of §11.4 rule 3), only "A matching lead or contact exists that you can't open";
a **Messages from the visitor** card when `capture_message_count` is above zero, read from
`GET /leads/{lead_id}/capture-messages`: each entry with its source and time as read-only plain text (never
markup, `white-space: pre-wrap`), headed "Sent through a public form. Anyone can send this, so treat it as
unverified.", and kept apart from **Notes** so the rep's own words are never mixed with it; the SLA block (the badge and a sentence: "First response due
in 2 h", "Responded 40 minutes after capture", "Breached 3 h ago", or nothing when no SLA is set); the
sealed **email** and **phone** through `RevealableField` and `useRevealedField` (the behaviour and tests of
`LeadDetail.tsx`, moved intact: step-up, 30 s auto-hide, budget, audit row on the server); **Notes** as an
`InlineEditField` textarea (4,000). Side column `DetailList`: Source (label and the endpoint's name when it
has one), Captured, First response ("Waiting" or the time), Last touched, Owner (inline when scope
allows), GSTIN, **Consent** ("Consent given, captured 12 Oct 2026, 3:04 pm", "Consent not given" or "No
consent recorded"; the stored hash is available only as a `title` on the line) and an **Attribution** card
(UTM source, medium, campaign, term, content) shown only when any value exists.

**`EraseLeadDialog`** (danger tone, R3.7a). Title "Erase {name}'s personal data?". Body: "The name, company, email,
phone, notes and messages are removed from this lead and from its history, and the lead disappears from every
list. This can't be undone. This doesn't reach anything a teammate typed about this person on a company,
contact, deal or activity, the number stored with a WhatsApp conversation in Engage, or backups." A converted
lead adds one line: "The company, contact and deal created from this lead are not changed." **Cancel** and
**Erase**; **Erase** asks for the step-up through the shared `StepUpDialog`, then sends the request with
`confirm_lead_id`, and is disabled while it runs. A 409 `conversion_in_progress` reads "This lead is being
converted. Try again in a moment."; success announces "Personal data erased", moves focus to the Leads list
heading and navigates to `/leads`; any other failure keeps the dialog open with the request id.

#### 11.6.3 Assignment page addition (R3.7a) and Sources page (R3.7b)

**Assignment page** `/settings/assignment` (R3.4's page; the hub card keeps its name and its summary gains
the SLA, "Round-robin · 3 reps · first response in 4 h") gains a card **First response and aging**: SLA
(blank means off; a number with a minutes or hours unit, 5 minutes to 30 days) and aging days (1-365),
saved with `PUT /leads/settings` (`crm.lead.settings.write`, ORG floor); dirty guard; read-only with a
note for a caller without the code. It is on this page, not on a new one, because it is the same
question: who gets a lead and how fast they must answer.

**Sources page** `/settings/sources` (R3.7b; view `crm.lead.lead.read`, edit `crm.lead.settings.write`;
a new hub card "Lead sources and forms" whose summary reads "2 forms, 1 webhook"):

* **WhatsApp** card: the switch "Create a lead when an unknown number messages you on WhatsApp"
  (`whatsapp_leads_enabled`, off by default) with its helper ("The sender's number is stored sealed and the
  lead is named with the masked number. At most 60 leads an hour are created this way. A lead created this way
  keeps the sender's number until an administrator erases that lead; turning this off stops new leads and
  removes nothing already stored. It can take up to five minutes to take effect."), saved with the same
  `PUT /leads/settings`; read-only without the code.
* **Two banners above the table.** While `capture_configured` is false: "Lead capture isn't switched on for
  this installation. Ask whoever runs your OpsGrids installation to set its signing secret.", and every create
  action is disabled with that reason. When `org_blocked_24h` is above zero: "Posts to your forms were refused
  {n} times in the last 24 hours because a daily or hourly limit was reached. If this isn't a real campaign,
  pause the form that is receiving the traffic." (DD-46: the tenant sees exhaustion here; there is no push
  alert, K8).
* **Sources** table (forms and webhooks): name, kind badge, Active or Paused (words), token prefix, last
  used (relative with the absolute instant), default owner, **Refused (24 h)** and **Discarded (24 h)** (the
  endpoint's `blocked_24h` and `discarded_24h`, "Not available" when null), `RowActions` **Edit**, **Copy embed code** (a
  form) or **Show webhook details** (a webhook), **Rotate secret** (a webhook; `ConfirmDialog` warning that
  the old secret stops working at once), **Pause** and **Resume**, **Delete** (`ConfirmDialog`). Empty: "No
  sources yet. Add a web form or a webhook to bring leads in." A reader sees the table without actions.
* **`EndpointDialog`** (create and edit): kind (Web form or Webhook, fixed after create), name, default
  owner (a rep `Combobox`; "Use the rotation" is the default), allowed origins (a tag input; each must be
  `https://host[:port]`; helper "Leave empty to accept posts from any site"), success URL (forms; must sit on
  an allowed origin), consent text (forms; "Shown beside the checkbox on your form"). Field errors map; dirty
  guard.
* **`SecretOnceDialog`** after a webhook is created or its secret rotated: a `CopyField` "Signing
  secret", "Store it now. It can't be shown again.", and a checkbox "I've saved it" that enables **Done**;
  closing without it asks for confirmation. The secret exists only in that dialog's state.
* **`EmbedSnippet`** (CRM-local): the HTML form of §11.3.2 generated from the endpoint's token, the page's
  own origin and the consent text and hash, with a **Copy** button (same clipboard fallback as `CopyField`,
  stated on screen when the clipboard is refused) and a note on the honeypot field. **The honeypot markup is
  specified, not left to the author**: an `<input type="text" name="opsgrids_hp" tabindex="-1"
  autocomplete="off">` with **no label and no placeholder**, inside a wrapper that is `aria-hidden="true"` and
  positioned off-screen (`position:absolute; left:-10000px; width:1px; height:1px; overflow:hidden`, **not**
  `display:none`, which many bots skip), and named `opsgrids_hp` because no browser, password manager or
  assistive technology offers an autofill for it (`website`, `url`, `company` and `email` are all targets). A
  `tabindex="-1"` control inside an `aria-hidden` wrapper is what axe's `aria-hidden-focus` rule accepts. The
  snippet's own test pins every attribute. **`WebhookHelp`**: the URL, the three headers, the signing recipe in
  words, and one `curl` example with placeholders.
* **Send a test**: a button posts the endpoint's token and a fixed test body through the real public route
  (same origin, JSON) and reports "Lead created" or the problem's words; the created lead is named "Test
  lead" and carries the endpoint, so it is easy to delete.

States (both pages): loading skeleton per card; error `LoadFailure` per card; forbidden
`PermissionDenied`; every dialog follows the dirty-guard and field-error rules of §4.4.2.

#### 11.6.4 Forms and import

The lead form checks for duplicates when the email or phone field loses focus (`POST
/leads:check-duplicates`) and shows a non-blocking notice with links; the 409 on a repeated email keeps its
"Open the existing lead" link. Import and export gain, for leads, export-only **Source**, **Captured via**,
**First response at** and **Possible duplicate**; import behaviour is unchanged.

#### 11.6.5 Replace-then-delete ledger

| Removal | Live users | Replacement | Gate before deleting | Proof after |
|---|---|---|---|---|
| `routes/LeadDetail.tsx` (296 lines, 4 offenders) and `routes/LeadDetail.reveal.test.tsx` (707) | the `/leads/:id` route | `routes/lead/*` (§11.6.2) | every reveal assertion of the old test exists, re-expressed, in the new tests (`removed.test.ts` checks the test names); the `lead-detail` surfaces reviewed | `ls frontend/product-crm/src/routes/LeadDetail.tsx` fails |
| `data/DirectoryProvider.tsx` and its test, `data/directoryContext.ts`, `data/lookups.ts` and its test, `routes/legacy.tsx` and the R3.4 test of the boundary, `routes/legacy.test.tsx` (seven files; `DirectoryStatusBanner` is a function that R3.4 moved from `App.tsx` into `routes/legacy.tsx`, so it goes with that file and is not an eighth) | the one remaining wrapper, for `/leads/:id` | `useEntityNames`, `useOwners` | `grep -rln "useDirectory" frontend/product-crm/src` lists only the files that are about to be deleted (`routes/LeadDetail.tsx`, `routes/legacy.tsx`, `data/directoryContext.ts`, `data/DirectoryProvider.test.tsx`) and the new lead page does not | the seven files are gone and `DirectoryStatusBanner` with them; `grep -rln "useDirectory" frontend/product-crm/src` is empty; no route issues the three 200-row directory calls |

#### 11.6.6 Visual surfaces and fixtures

R3.7a: `lead-detail`, `lead-detail-duplicate` (alternate fixture: both flags, an SLA breach, visitor
messages), `reassign-dialog`, `erase-lead-dialog`, `leads-list` (updated), `settings-assignment`. R3.7b:
`settings-sources`, `settings-sources-empty`, `settings-sources-limit` (alternate fixture: a spent budget and no
secret configured), `endpoint-dialog`, `endpoint-secret-dialog`. New fixtures
`lead.json`, `lead-settings.json`, `lead-capture-messages.json`, `capture-endpoints.json`, with every new
`LeadOut` field added to `leads*.json`; six renderings with axe, no baseline entry; the secret dialog's fixture uses an obviously
fake value.

### 11.7 Tests

Lead (R3.7a): `test_lead_settings.py`, `test_sla.py` (`test_sla_state_boundaries` (property over `now`),
`test_due_soon_is_a_quarter_of_the_window_never_under_five_minutes`,
`test_first_response_is_set_once_when_leaving_new`, `test_aged_uses_last_touched_then_created`,
`test_legacy_leads_get_first_response_from_the_migration`), `test_reassign.py` (the three named tests of
§11.3.1 plus `test_reassign_locks_in_id_order_without_deadlock` (concurrent overlapping batches),
`test_reassign_skips_converted_and_claimed_leads`, `test_reassign_to_an_inactive_rep_is_422`,
`test_reassign_emits_assigned_with_previous_owner`), `test_duplicates.py`
(`test_phone_duplicate_flags_the_oldest_live_lead`, `test_email_matching_a_contact_sets_the_contact_flag`,
`test_unreachable_customer_creates_the_lead_without_a_flag`, `test_check_duplicates_returns_ids_only`,
`test_canonical_email_case_variants_collide`, and every permission test named in §11.3.1's check-duplicates
and `GET /leads` rows), `test_lead_erase.py` (the four named tests of §11.3.1 plus
`test_erase_anonymises_and_soft_deletes`, `test_erase_clears_every_pii_column_and_keeps_attribution` (property
over the column list), `test_erase_rewrites_the_audit_copies_through_the_function`,
`test_the_app_role_cannot_update_audit_logs_but_the_function_can` (a privilege test),
`test_the_function_reaches_only_the_named_lead_in_the_named_organization`,
`test_erase_refuses_a_live_conversion_claim`, `test_an_erased_lead_is_in_no_list_summary_or_filter`,
`test_an_erased_leads_email_phone_and_external_ref_are_released`,
`test_erase_stages_one_audit_row_and_one_event_with_ids_only`, `test_a_second_erase_is_404`,
`test_erasing_a_converted_lead_leaves_its_company_contact_and_deal_alone`,
`test_erase_logs_and_output_hold_no_personal_data` (a log scan)), `test_capture_messages.py`
(`test_a_message_is_stored_in_its_own_list_and_never_in_notes`, `test_a_sixth_message_is_dropped_and_counted`,
`test_a_merged_capture_leaves_rep_notes_byte_for_byte_unchanged`, `test_messages_are_not_in_a_list_row`, the
three named tests of the route), `test_lead_events.py` (EV10, EV11, EV12, EV15 and EV53 through the
real write paths and `validate_event_schema`; `test_assigned_by_values`), `test_lead_list_filters.py`
(`unassigned`, `sla`, `aging`, `duplicate`, `capture_endpoint_id`, the OWN-scope non-widening), migration
tests (round trip, downgrade refusal and override, `pg_policies`, no cross-schema FK, the two data steps, and
for `lead.scrub_lead_audit`: `SECURITY DEFINER`, owner `opsgrids_admin`, `EXECUTE` granted to `opsgrids_lead`
and nobody else, its two guards (a non-object value is left alone; another organization's id raises), and
`ck_leads_capture_messages` refuses a sixth entry),
`tests/infra/test_backfill_pii_indexes.py` extended for `--service lead` (`test_plan_writes_nothing`,
`test_collision_rows_are_skipped_and_counted`, `test_phone_is_opened_only_inside_the_service_process`).
Lead (R3.7b): `test_capture_public.py` (`test_valid_form_token_creates_one_lead`,
`test_bad_mac_is_the_same_400_as_an_unknown_endpoint_and_touches_no_table`,
`test_inactive_or_deleted_endpoint_is_indistinguishable`, `test_origin_not_listed_is_refused`,
`test_honeypot_returns_the_success_response_and_stores_nothing`,
`test_response_bytes_are_identical_for_new_merged_and_discarded`, `test_repeated_email_merges_into_the_existing_lead`
(`capture_messages` grows, `notes` is untouched),
`test_per_address_endpoint_and_organization_budgets_return_429_with_retry_after`,
`test_form_budget_exhaustion_leaves_the_webhook_bucket_untouched` and
`test_webhook_budget_exhaustion_leaves_the_form_bucket_untouched` (DD-46),
`test_a_refusal_and_a_honeypot_post_increment_their_counters_and_a_counter_failure_changes_nothing`,
`test_the_honeypot_field_name_is_not_an_autofill_name` (`opsgrids_hp`),
`test_redis_down_fails_closed_with_503`, `test_the_rightmost_forwarded_address_is_the_bucket_key` and
`test_a_forged_leftmost_address_does_not_change_it`, `test_form_post_answers_303_to_the_success_url`,
`test_urlencoded_and_json_bodies_are_equivalent`, `test_consent_required_when_the_endpoint_has_text`,
`test_consent_is_stored_as_evidence_and_read_by_no_decision`, `test_crm_not_entitled_looks_like_an_unknown_endpoint`,
`test_product_service_down_without_a_recent_answer_is_503`, `test_invalid_gstin_is_dropped`,
`test_no_database_work_before_the_mac_is_verified` (a query counter), `test_capture_holds_no_transaction_across_a_network_call`),
`test_capture_webhook.py` (`test_valid_signature_creates_the_lead`,
`test_wrong_secret_wrong_timestamp_and_wrong_body_are_the_same_400`, `test_timestamp_outside_five_minutes_is_refused`,
`test_replay_inside_the_window_returns_the_existing_lead`, `test_external_ref_is_required`,
`test_signature_is_over_the_raw_bytes_not_the_parsed_json`, `test_secret_is_sealed_with_the_endpoint_aad_and_never_returned_after_create`,
`test_rotate_secret_stops_the_old_secret`), `test_capture_endpoints.py` (CRUD, the named tests of §11.3.1, `test_form_token_is_permanent_and_rederivable`, `test_success_url_must_sit_on_an_allowed_origin`),
`test_capture_secret.py` (the five secret tests of §11.3.4),
`test_capture_internal.py` (the three internal negatives, `test_disabled_whatsapp_leads_are_skipped`,
`test_idempotent_on_source_and_external_ref`, `test_hourly_cap_skips_with_rate_limited`,
`test_not_entitled_is_skipped`). Gateway: `test_lead_public_paths.py` (an anonymous POST to the capture path
is forwarded and not answered 401; the same path with another prefix is; the webhook path with GET is
401; the two route-table rows exist in both tables and the body cap is enforced). Notification:
`test_stranger_lead.py` (`test_a_stranger_conversation_calls_lead_once_with_no_transaction_open`,
`test_a_known_contact_never_reaches_lead`,
`test_lead_unreachable_is_a_counted_skip_with_no_ledger_row_and_no_exception`,
`test_an_unset_or_unresolvable_lead_url_is_a_counted_skip`,
`test_the_next_message_from_the_same_stranger_tries_again`, `test_a_lead_outage_never_dead_letters_the_event`,
`test_a_definite_no_is_cached_for_five_minutes_per_organization` (and `test_the_cache_is_per_organization_and_expires`),
`test_a_skipped_reason_is_a_definite_answer`, `test_the_lead_name_uses_the_masked_number_only`). Infra:
`tests/infra/test_route_table_parity.py`, `test_frontend_paths_are_routed.py`,
`tests/infra/test_party_link_env_wiring.py` (rows W21 and W36), `test_io_outside_transaction.py`,
`test_capture_secret_wiring.py` and the `test_preflight_secrets.py` rows of §11.3.4, and the new clients
registered in SP1's `test_internal_client_paths.py` (W21, W36).
Frontend: Leads list (every new filter round-trips the URL; SLA column absent without SLA; reassign dialog
paths; the duplicate tag follows `possible_duplicate`), lead page (every state, duplicate banners for each id
visibility case, the visitor-messages card kept apart from Notes and rendered as plain text, reveal suite
moved, converted read-only, `EraseLeadDialog` paths: step-up, the 409 sentence, focus and navigation after
success), Sources page (every state, secret shown once and never again, both banners, the two counters,
read-only mode), `EmbedSnippet.test.tsx` (`honeypot_markup_is_off_screen_unlabelled_tabindex_minus_one_autocomplete_off_aria_hidden_and_not_display_none`),
plus the surfaces of §11.6.6.

### 11.8 Rollout

**R3.7a.** Prerequisites (owner): R2.8's reconcile applied, R3.1's H8 run for customer's `email_index`
(the duplicate lookups against contacts need it), R3.4 live. 1. Merge. 2. **Deploy** (`make deploy HOST=… CONFIRM=…
REF=<merged ref>`; the lead migration runs; app-gateway is unchanged). 3. **Publish** the SPA (`make edge-web
… APP=crm`). 4. **H8 for leads** (owner; the tool's wrapper and gates of §13): `plan` for `--service lead
--target phone_index`, read the counts, `apply`; then the same for `email_index`, reading the collision
count first. 5. **Verify**: save an SLA on the Assignment page and confirm the SLA column appears on the
Leads list, reassign two leads, run a duplicate check from the lead form, open a lead and reveal its
phone, and **erase one test lead** you created by hand (the step-up dialog, then confirm it leaves the list, and
that a read-only query of `lead.audit_logs` for its id, run by the owner, shows no name, email, phone or note in
`before` or `after`; a lead has no History tab until R3.8). 6. Soak one day.

**R3.7b.** 0. **Before anything else** (owner): the ADR `crm-public-lead-capture` is accepted, and D-27 is
answered for leads (§4.2); R3.7a's erase has been exercised on the box. 1. **Owner sets `CAPTURE_TOKEN_SECRET`**
in the box `.env` (`openssl rand -hex 32`); an agent never edits it (F12). **This comes before the deploy, not
after**: once `.env.example` carries the key, `preflight.sh` refuses a box whose `.env` lacks a real value
(§11.3.4), so there is no "deployed but off" state on the box; the `dev-only-` default is the local and CI
value. 2. Merge; **deploy**
(`app-gateway` for the frozenset and both route tables, `lead`, `notification`, and the compose lines for
W21 and W36). 3. **Publish** the SPA. 4. **Verify** (owner): create a form on the Sources page and post a
test lead to the public URL with `curl -i -X POST -d "form_token=…&full_name=Test&email=t@example.com"
https://<crm host>/api/v1/leads/public/capture` (expect 200 and a new lead assigned by the rotation);
repeat it 21 times from one address (expect 429 on the 21st within the hour); send one signed webhook
following the Sources page's recipe; switch on WhatsApp leads and message the business number from a number
that is not a contact (expect one lead named with the masked number); read `make edge-logs SERVICE=lead`
for `lead.capture` lines (counts only).

*Flags*: none per organization except `whatsapp_leads_enabled` (default off) and the endpoints themselves,
which do not exist until an admin creates one. The emergency brake is the secret: rotating
`CAPTURE_TOKEN_SECRET` invalidates every issued token and header at once (every embed and sender must then be
re-issued). **One-way consequence, stated to the owner**: switching capture on starts storing the personal
details of people who never signed in, the code cannot then be rolled back below R3.7a (§11.9, K2), and the
only removal is an administrator's per-lead erase (§11.3.3; there is no bulk erase). *Reconcile*: none (E22 is
admin-only and was applied in R2.8; the dry run must be empty). *Memory*: no container, kind or profile change;
every total `memory_budget_totals.py` prints is unchanged from the parent commit (§4.5); the Redis counters carry
a two-window TTL.

### 11.9 Rollback

R3.7b to R3.7a: `make edge-rollback` and `make edge-web-rollback APP=crm`. The public routes disappear
(visitors' form posts answer 404 through the gateway: every embedded form fails until R3.7b returns; this is
the cost and is stated to the owner), captured leads stay readable because R3.7a's `LeadSource` already
knows the new values, endpoints and secrets stay in their tables. **Below R3.7a is not supported once a lead
with a `webhook`, `whatsapp` or `storefront` source exists**: the previous code answers 500 for any list
containing it. The only repair, moving those leads' `source` to `manual`, is a production write that loses
provenance and needs the owner's explicit approval; an agent never runs it. An erase is never rolled back:
the details it removed are gone, and the scrubbed audit rows stay scrubbed. Stop conditions: any 5xx on
`GET /leads`; `lead.outbox` growing (the relay runs as `opsgrids_outbox`); a `lead.events` dead letter (the
stranger step cannot dead-letter `notification.events`, DD-45, so a `notification.events` dead letter here is
the contact fold's and is investigated as that); capture 429s above 5% of public posts (the budgets are too
tight or an endpoint is under attack); a captured lead whose sealed fields reveal as the wrong value.

### 11.10 Acceptance criteria (mechanical)

1. `make test-services`, `make migration-roundtrip` and `uv run pytest tests/infra -q` pass; every test named
   in §11.7 exists.
2. `grep -c "Ahead of the producer" libs/contracts/events/crm.lead.created/v1.schema.json
   libs/contracts/events/crm.lead.updated/v1.schema.json libs/contracts/events/crm.lead.converted/v1.schema.json`
   prints `0` for each; the three `source` enums list `form, import, manual, webhook, whatsapp, storefront`;
   `libs/contracts/events/crm.lead.assigned/v1.schema.json` and
   `libs/contracts/events/crm.lead.erased/v1.schema.json` exist; SP1's `AHEAD_OF_PRODUCER` rows for the three
   lead events are gone and `test_the_ahead_of_producer_schemas_are_exactly_the_pinned_eight` passes with their
   names removed.
3. `make openapi-check` passes; the lead document lists `POST /leads:reassign`, `POST /leads:check-duplicates`,
   `POST /leads/{lead_id}:erase`, `GET /leads/{lead_id}/capture-messages`, `GET|PUT /leads/settings` (R3.7a) and
   the capture-endpoint, public-capture and webhook operations (R3.7b); `LeadOut.source` lists all six values.
4. R3.7b: `jq '[.routes[] | select(.prefix == "/api/v1/leads/public" or .prefix == "/api/v1/webhooks/leads")] | length'`
   prints `2` for both route tables, and `grep -n "LEAD_PUBLIC_PATHS" services/app-gateway/app/main.py` shows
   the frozenset in `public_paths`.
5. `python tools/export_permissions.py --check` passes unchanged (no code added: the erase reuses E22).
6. `pnpm tokens:baseline` leaves no `product-crm` entry in `frontend/opsgrids-config/token-baseline.json`;
   the a11y baseline has no CRM entry; `ls frontend/product-crm/src/routes/LeadDetail.tsx` fails and
   `grep -rln "useDirectory" frontend/product-crm/src` is empty (the seven files of §11.6.5 are gone).
7. `make ui-visual` passes with the R3.7 surfaces added and no baseline entry.
8. `uv run pytest tests/infra/test_io_outside_transaction.py -q` passes, and `test_capture_holds_no_transaction_across_a_network_call` proves the capture path itself.
9. The erase and the visitor's words are bounded: `test_lead_erase.py` and `test_capture_messages.py` pass,
   including `test_a_merged_capture_leaves_rep_notes_byte_for_byte_unchanged` (no capture path writes a lead's
   `notes`, DD-44) and `test_the_app_role_cannot_update_audit_logs_but_the_function_can`.
10. R3.7b only: `tests/infra/test_capture_secret_wiring.py` and the new `test_preflight_secrets.py` rows pass,
    `grep -n "CAPTURE_TOKEN_SECRET" infrastructure/edge/.env.example docs/deploy/ENV_SOURCE_MATRIX.md
    docs/deploy/env-reference.md infrastructure/edge/compose.prod.yml` finds all four, and the ADR
    `crm-public-lead-capture` exists with its row in `docs/decisions.md`.
11. The standing criteria of §4.5: S1 to S8; `docs/crm.md` lists EV15 and EV53, and the lead rows of the
    service map are current.

## 12. R3.8 Saved views, tags, history, reports and team scope

### 12.1 Purpose

The last release turns a good tool into one a team can run on. Reps and managers save the filter
combinations they use daily (saved views), label records (tags), see who changed what (history), read a
forecast and a win and loss report per currency (the reports' adapter seam is pending D-12), and a manager
works a team (**pending D-24**, with D-20 and D-25 on the roles): **TEAM scope** means
"my own rows and my direct reports' rows", fed by each service's own copy of the reporting line
(`owner_team`), kept current by `core.membership.*` events, and fail-closed everywhere it is unsure. It
is the only release that changes how authorization resolves in the CRM, so it is the one with the longest
rollout and the one rollback step that is not reversible (§12.9).

### 12.2 Data model

One revision per service, each after that service's head at merge (`# RE-PARENT HERE`): customer
`<rev>_team_tags_views` (after R3.1's), deal `<rev>_team_tags_views` (after R3.6's), lead
`<rev>_team_tags_views` (after R3.7's). Each starts with `SET LOCAL lock_timeout = '5s'`. Shown for
customer; deal and lead are the same with their tables:

```sql
-- B4  (composite key; no TenantBase columns)
CREATE TABLE customer.owner_team (
    organization_id       UUID        NOT NULL,
    membership_id         UUID        NOT NULL,
    manager_membership_id UUID,
    status                VARCHAR(20) NOT NULL,
    source_occurred_at    TIMESTAMPTZ NOT NULL,       -- the version: applied only if strictly newer
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, membership_id),
    CONSTRAINT ck_owner_team_manager_not_self CHECK (manager_membership_id IS DISTINCT FROM membership_id)
);
CREATE INDEX ix_owner_team_org_manager ON customer.owner_team (organization_id, manager_membership_id, membership_id);

-- B5  (the snapshot TEAM compares; NULL matches nothing)
ALTER TABLE customer.companies ADD COLUMN owner_manager_id UUID;
ALTER TABLE customer.contacts  ADD COLUMN owner_manager_id UUID;
CREATE INDEX ix_companies_org_owner_manager ON customer.companies (organization_id, owner_manager_id);
CREATE INDEX ix_contacts_org_owner_manager  ON customer.contacts  (organization_id, owner_manager_id);

-- B13
ALTER TABLE customer.companies ADD COLUMN tags JSONB NOT NULL DEFAULT '[]';
ALTER TABLE customer.contacts  ADD COLUMN tags JSONB NOT NULL DEFAULT '[]';
ALTER TABLE customer.companies ADD CONSTRAINT ck_companies_tags CHECK (jsonb_typeof(tags) = 'array' AND jsonb_array_length(tags) <= 20);
ALTER TABLE customer.contacts  ADD CONSTRAINT ck_contacts_tags  CHECK (jsonb_typeof(tags) = 'array' AND jsonb_array_length(tags) <= 20);

-- B14  (TenantBase columns, then:)
CREATE TABLE customer.saved_views (
    membership_id UUID         NOT NULL,                -- the view's owner: principal.membership_id, never own_ref
    entity        VARCHAR(12)  NOT NULL,
    name          VARCHAR(80)  NOT NULL,
    filters       JSONB        NOT NULL DEFAULT '{}',
    sort          VARCHAR(40),
    visibility    VARCHAR(8)   NOT NULL DEFAULT 'private',
    position      INTEGER      NOT NULL DEFAULT 0,
    CONSTRAINT ck_saved_views_entity     CHECK (entity IN ('company', 'contact')),   -- deal: ('deal'), lead: ('lead')
    CONSTRAINT ck_saved_views_visibility CHECK (visibility IN ('private', 'org'))
);
CREATE UNIQUE INDEX uq_saved_views_org_member_entity_name
    ON customer.saved_views (organization_id, membership_id, entity, name) WHERE deleted_at IS NULL;
CREATE INDEX ix_saved_views_org_entity_visibility ON customer.saved_views (organization_id, entity, visibility);
-- ENABLE + FORCE RLS and tenant_isolation on owner_team and saved_views (§4.1); both join TENANT_TABLES.
```

**Forward note (B71, J28).** SP4's R4.7 adds `employee_id UUID NULL` and the partial index
`ix_owner_team_org_employee (organization_id, employee_id) WHERE employee_id IS NOT NULL` to `customer.owner_team` and
`lead.owner_team` (an HR exit and a leave event name an employee, not a membership, and EV29 already carries
`employee_id`). The column is R4.7's; this release may add it in its own revision with `ADD COLUMN IF NOT EXISTS`,
and either order of the two revisions works. SP3 keeps owning the table, its handler and H10; R4.7 extends the
handler and H10 (§12.3.4).

Deal adds `owner_manager_id` to `deals`, `follow_ups` and **`activities`** (a column addition is allowed
under the append-only trigger, which refuses `UPDATE` and `DELETE` only, `e94d7a2c5f18:90-107`; new rows
carry the stamp, old rows stay NULL and are never restamped), `tags` to `deals`, and
`CREATE INDEX ix_deals_org_closed ON deal.deals (organization_id, closed_at) WHERE closed_at IS NOT NULL`
(the win and loss report's range scan; `ix_deals_org_close` already serves the forecast). Lead adds
`owner_manager_id` and `tags` to `leads`. A tag has no index (DD-31): the filter runs inside one
organization's rows with `tags @> '["x"]'`, and a GIN index cannot lead with `organization_id` without
`btree_gin`.

`ADD COLUMN ... NOT NULL DEFAULT '[]'` is a metadata-only change on the box's Postgres 16
(`infrastructure/edge/compose.prod.yml:238`, `postgis/postgis:16-3.4`), so the expand is cheap. There is **no backfill in any migration**: the stamps are filled by the event consumers and the
owner-run tool H10 (§12.3.4), because they depend on data that lives in `account`.

Downgrade: drops what it created; refuses unless `OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE=1` while any
`saved_views` row, any non-empty `tags` or any `owner_team` row exists. On an empty database it fully
reverses.

### 12.3 Backend contract

#### 12.3.1 Saved views (C16, C32, C43)

Per service, same shape: customer `/companies/views` and `/contacts/views`, deal `/deals/views`, lead
`/leads/views`; each `GET`, `POST`, `PATCH /{view_id}`, `DELETE /{view_id}`, declared before the `/{id}`
routes. The permission is the entity's **read** code (a view is a stored query and writes no record):
`crm.book.company.read`, `crm.book.contact.read`, `crm.pipeline.deal.read`, `crm.lead.lead.read`.

| Operation | Rule | Errors | Named negative-authz test (per service) |
|---|---|---|---|
| `GET` | the caller's own views plus other members' `org` views whose owner is not known to be inactive (`owner_team.status`, absent counts as active); ordered by `position`, `name`; `ViewOut {id, entity, name, filters, sort, visibility, position, mine, owner_membership_id}` | | `test_views_list_never_returns_another_members_private_view` |
| `POST` | `{entity, name (1-80), filters, sort?, visibility?}`; `visibility: "org"` needs the read context's scope to be ORG; `membership_id` is `ctx.principal.membership_id` (a caller with none is 403) | 409 `duplicate_view_name`; 422 `invalid_filter`, `invalid_sort`, `too_many_views` (50 per member per entity); 403 `below_floor` (sharing without ORG read) | `test_view_org_visibility_requires_org_read_scope` |
| `PATCH` | owner only: `{name?, filters?, sort?, visibility?, position?}`; a shared view's owner is the only editor | 404 for a view that is not the caller's and not shared; 403 for another member's shared view | `test_view_patch_by_another_member_is_403`; `test_view_patch_cross_tenant_is_404` |
| `DELETE` | owner only; soft delete | 404, 403 | `test_view_delete_by_another_member_is_403` |

`filters` is exactly the query parameters the list route accepts (B14): a per-entity table of allowed keys,
each validated by the same parser the list route uses, in `platform_core.listing.validate_view_filters`
(A20, extended in this release). Allowed keys: **company** `q, status, owner, created_from, created_to,
tag`; **contact** `q, status, company_id, owner, created_from, created_to, tag`; **deal** `q, stage,
outcome, owner, source, company_id, close_from, close_to, created_from, created_to, attention, tag`;
**lead** `q, status, owner, source, sla, aging, duplicate, capture_endpoint_id, created_from, created_to,
tag`. `owner` is `me`, `team`, `unassigned` (leads) or a UUID. `page`, `size`, `ids` and `view` are never
stored. A `q` that looks like an identifier (the guard of §4.4.6) is `422 invalid_filter`: a shared view
must never hold an email address or a number.

#### 12.3.2 History for company, contact and lead (C15, C32, C43)

`GET /companies/{company_id}/history` (`crm.book.company.read`), `GET /contacts/{contact_id}/history`
(`crm.book.contact.read`), `GET /leads/{lead_id}/history` (`crm.lead.lead.read`), each with
`assert_can_touch`, the paging and the `HistoryEntryOut` of §9.3, and the same pure projection. Allow-lists:
company actions `company.created`, `company.updated`, with values only for `status`, `owner_id`,
`industry`; contact actions `contact.created`, `contact.updated`, `contact.reused_for_lead`; lead actions
`lead.created`, `lead.updated`, `lead.converted`; **contacts and leads never return a `before` or `after`
value**, only names in `changed_fields` (their audit rows hold names and free text, and R3.1 already stops
writing new values for contacts). `entity.sensitive_read`, `contact.messaging_target_resolved`,
`contact.phone_set`, `lead.conversion_aborted`, `lead.conversion_failed` and every other action never
appear. The audit actor stamp of §9.3 (DD-33) is added to the customer and lead services here, so rows
written before this release show "A teammate". Named tests per route: `…_requires_the_read_code`,
`…_out_of_scope_is_403_for_own_scope`, `…_cross_tenant_is_404`; and `test_contact_history_never_returns_a_value`,
`test_lead_history_never_returns_a_value`.

#### 12.3.3 Reports (C10, deal)

Both under `crm.pipeline.deal.read` (DD-41), aggregating the same scoped statement `deals_stmt(ctx)` lists from, so
a caller sees only what they can already list; static paths declared before `/deals/{deal_id}`; at most
three queries each, per currency and **never summed across currencies**.

| Route | Query | Response |
|---|---|---|
| `GET /deals/reports/forecast` | `period` (`month` default, `quarter`), `periods` (1-12, default 6), `owner` (`me`, `team`), `owner_id`, `tz` | `ForecastOut {period, periods, as_of, tz, currencies: [{currency, overdue: Bucket \| null, buckets: Bucket[], totals}]}`, `Bucket {start, end, label, deal_count, value_minor, weighted_minor, won_count, won_value_minor}` |
| `GET /deals/reports/win-loss` | `from`, `to` (dates in `tz`; default the last 90 days), `outcome` (`won`, `lost`), `owner`, `owner_id`, `tz` | `WinLossOut {from, to, tz, currencies: [{currency, won_count, lost_count, won_value_minor, lost_value_minor, win_rate_pct \| null, reasons: [{outcome, reason_key \| null, deal_count, value_minor}], owners: [{owner_id, won_count, lost_count, won_value_minor, lost_value_minor}]}]}` |

Errors: 422 `invalid_timezone`, `invalid_report_range` (`from` after `to`, or more than 366 days), `invalid_filter`.
Named tests: `test_forecast_requires_deal_read`, `test_win_loss_requires_deal_read`,
`test_reports_respect_own_scope_for_a_custom_role`, `test_report_range_over_a_year_is_422`,
`test_reports_cross_tenant_rows_never_count`.

#### 12.3.4 TEAM: the projection, the consumers, the tool

* **Events (EV29).** Account's three emission sites (`services/account/app/services.py:666, 884, 958`)
  build one full-state payload through a new helper: `organization_id, membership_id, status,
  manager_membership_id, employee_id` (all nullable except the first two and `status`), plus `changed[]` on
  `.updated`, plus the legacy `user_id`, `email` and `full_name` on `.created` and `user_id` on `.revoked`
  that exist today and are frozen (new consumers never read them, J12's reasoning). The committed schemas
  `libs/contracts/events/core.membership.{created,updated,revoked}/v1.schema.json` (none exist today) land
  in the same commit, with producer tests through the real write path. **Ships after the consumers are
  live** (§D rule): this is deploy 2 of §12.8.
* **Account's membership read, widened to its two new callers (C57; also deploy 2).** SP2 ships `GET
  /internal/v1/organizations/{organization_id}/memberships` with the caller allow-list **`customer` only** (REG
  C57 lists `customer`, `deal`, `lead`; SP2 §9.3 says deal and lead "join with their first caller, R3.8"). H10 is
  that first caller, once per service, so **R3.8's Deploy 2 changes account** in the same release as the
  events: `_require_caller(request, "customer", "deal", "lead")` with `_require_org_claim` kept, and no other
  caller added here (`approval` joins at R4.5, REG C57). On the calling side `deal` and `lead` each gain
  `account_service_url: str = "http://localhost:8111"` and `account_timeout_seconds: float = 5.0` on `Settings`
  (REG F4; the already-shared `ACCOUNT_SERVICE_URL` of `x-service-env`, so no compose line, rows W18 and W19) and
  a small client that mints its token with `mint_service_token(settings, audience="account",
  caller=settings.service_name, claims={"org": str(organization_id)})`, registered in SP1's
  `tests/infra/test_internal_client_paths.py` and used only by H10, outside any transaction. **Three negative
  tests per new caller**, in account: no token is 401; a token from a service that is not on the list
  (parametrised over `finance` and `notification`) is 403; a token whose `org` claim names another organization
  is 403; plus, for each of `deal` and `lead`, a test through that service's **real** client helper (the
  subprocess pattern of `tests/infra/test_service_token_org_claim_roundtrip.py`, §5.3.3) proving it is accepted
  for its own organization and refused for another's. Without this change O15's `plan --service deal` would
  answer 403 after the three consumers are already live, `owner_team` would stay empty and TEAM would match
  nothing, and nobody would find out before the owner ran the tool.
* **Consumers (EV43, EV44, EV45, F9).** Each of customer, deal and lead binds the three event types on its
  own queue (`customer.events`, `deal.events`, `lead.events`) with one handler, `owner_team_changed`, in
  its `app/consumers.py`: `already_processed`; tenant bound per message; refuse an envelope whose
  organization differs from the payload's; upsert the row `ON CONFLICT (organization_id, membership_id) DO
  UPDATE ... WHERE owner_team.source_occurred_at < EXCLUDED.source_occurred_at`; when the manager changed,
  **restamp** that service's rows owned by the member (§12.4 rule 2); `mark_processed` in the same transaction.
  R4.7 extends this handler in customer and lead to write `employee_id` from the same enriched payload under
  the same strictly-newer guard (B71).
  Lead has no consumer today (V9): R3.8 gives it the whole fold of F9 (below).
* **H10, `tools/backfill_owner_team.py`** (wrapper `remote.sh backfill-owner-team`, target `make
  edge-backfill-owner-team`): owner-run, `--service customer|deal|lead` (one child process per
  service, `import app` being ambiguous), `--org U | --all`, `--pace MS`. `plan` (read-only, the default)
  prints per organization the memberships account holds (C57), the `owner_team` rows the service holds, the
  drift between them and the number of rows whose stamp would change. `apply` (the purge tools' gates:
  `--confirm-count N`, `--i-took-a-backup`, `--i-know-this-is-the-live-database`) records the time just
  **before** it reads account as the rows' `source_occurred_at`, pages C57 (`after_id`, `limit` 500,
  `ACCOUNT_SERVICE_URL`, W17-W19), upserts `owner_team`, then restamps every owned row set-based with
  `updated_at` untouched, one transaction per organization on the app role with the tenant bound, stopping
  at the first failing organization, idempotent, counts and ids only. A second `plan` shows zero drift.
  It is also the repair tool after any consumer outage, because events are not replayable. R4.7 extends `apply`
  to write `employee_id` from the C57 rows and `plan` to count a row whose `employee_id` differs as drift; the
  owner re-runs `apply` once after the R4.7 deploy, because membership events cannot be replayed and existing
  rows stay NULL until it runs (B71, J28).

**F9, the lead fold** (unconditional, registry F9): `Settings.run_consumers_in_api: bool = False` and the
lifespan hooks copied from `customer/app/main.py:22-77` into `lead/app/main.py`; new
`services/lead/app/consumers.py` (the seam `use_session_scope`, `consumer = EventConsumer(…,
queue_name="lead.events")`, `main()` asserting the app role) and `services/lead/bin/worker`
(`exec python -m app.consumers`); `containers.lead.kind` `api` to `api_with_worker`; a `lead-worker`
compose service (`profiles: ['split-workers']`, `command: ['./bin/worker']`, the shape of `customer-worker`,
`compose.prod.yml:1449-1454`) and its `containers["lead-worker"]` entry; `PROFILE_DLQ_QUEUES["crm"]` gains
`"lead.events"` (`ops_monitor/config.py:102`, test pins `libs/platform-core/tests/test_ops_monitor_config.py:76-86`
change on purpose); the RUNBOOK literals named in F8 change in the same commit.

#### 12.3.5 TEAM wiring (A14) and tags

* **Selectors.** The five `ctx.apply` calls (`customer/app/selectors.py:152, 262`,
  `deal/app/selectors.py:50, 122`, `lead/app/selectors.py:39`) and the new follow-ups selector pass
  `team_col=<Model>.owner_manager_id`; the registered resolvers' `team_column()` return the same columns
  (`customer/app/scope.py:38-39`, `deal/app/scope.py:39-40`, `lead/app/scope.py:35-36`).
* **Object checks, two kinds.** The 22 `assert_can_touch` call sites (customer 9, deal 7, lead 6, listed in
  §12.4) are not all alike. **Eighteen check a row the caller reads or changes** and pass
  `team_owner_id=<row>.owner_manager_id`. **Four validate an owner the caller is assigning, and there is no
  row to read a stamp from**: deal `services.py:209` (a `PATCH` naming `owner_id`), deal `api/routes.py:115`
  (the default owner on create), lead `services.py:449` (create with an explicit owner) and lead `services.py:592`
  (update naming `owner_id`). Under TEAM `assert_can_touch` passes only when `team_owner_id` equals the caller
  (`libs/platform-core/src/platform_core/auth.py:375`), so passing nothing answers 403 to a manager assigning to
  their own report, and passing a literal `None` satisfies a naive guard while changing nothing. Each of the four
  therefore passes `team_owner_id=await services.manager_of(session, organization_id, owner_id)`, a
  primary-key read of the service's own `owner_team` returning that owner's `manager_membership_id` (None for
  an unknown owner, which fails closed). Two more checks take the same lookup because they validate a
  target owner without calling `assert_can_touch`: customer's `_assert_owner_assignable`
  (`services.py:187-206`, which today refuses only an OWN caller assigning to someone else, so under TEAM it
  would let a manager hand a record to anyone) and the target owner of R3.7's bulk `reassign_leads`. Under
  TEAM a target owner passes only when it is the caller or a member whose manager is the caller; ORG passes any
  active rep. Tests (each service that has such a site): `test_a_manager_can_assign_to_a_report`,
  `test_a_manager_cannot_assign_to_an_outsider`, `test_a_manager_can_bulk_reassign_leads_to_a_report`,
  `test_a_manager_cannot_bulk_reassign_leads_to_an_outsider`, `test_an_unknown_target_owner_fails_closed_under_team`.
  **A static guard**, `tests/infra/test_team_wiring.py`, walks the AST of the three services and fails on any
  `ctx.apply(` without `team_col=`, any `assert_can_touch(` without `team_owner_id=`, **and any
  `team_owner_id=None` or other constant**, so a literal `None` fails exactly like a missing keyword, in the
  manner of `tests/infra/test_no_raw_subject_ref.py` (A11); a call added later cannot silently leave TEAM
  unsupported.
* **List filters.** `owner=team` on every list resolves on the server to `owner_id = own_ref OR
  owner_manager_id = own_ref` (the same reading A8 gives TEAM for MEMBERSHIP); `own_ref` absent yields an
  empty page, never the book.
* **Tags.** `tags: string[]` on the create and patch bodies and the responses of companies, contacts,
  deals and leads (C34), under the entity's own write permission; list filter `tag` (repeatable, AND) in
  `GET` of each list and in the summaries; the 360's `customer.tags` is filled from the contact's tags.
  No routes, no catalogue. `platform_core.listing.normalise_tags` (A20 extended) is the one normaliser:
  `strip`, lower-case, internal runs of whitespace collapsed, each tag matching `^[a-z0-9][a-z0-9 _-]{0,39}$`,
  at most 20, duplicates collapsed, stored sorted (so a no-op patch compares equal); failures are 422
  `invalid_tag` and `too_many_tags`. Tag **values** never enter an event or an audit `after` (`changed`
  names the field); the history projection does not return them.

#### 12.3.6 Documents (the same commits)

* **`docs/crm.md`** (REG I3): the service map records that `lead` now runs a consumer on `lead.events` and the
  `crm` DLQ queue list; SP1's `tests/infra/test_crm_doc_matches_contracts.py` fails the build otherwise (no
  CRM event, permission or role changes here).
* **REG I8**: the lead fold's RUNBOOK literals (the ten-profile total and the `admin` total, the `split-workers`
  rollback list, REG F8's line list), `docs/deploy/ENV_SOURCE_MATRIX.md` and `docs/deploy/env-reference.md` for
  `lead-worker` and the crm DLQ queues, and the owner steps for H10 and for the TEAM widening; the RUNBOOK
  ceiling literal is pinned by `tests/infra/test_planned_headroom.py::test_runbook_ceiling_example_is_the_number_preflight_would_print`.
* **REG I6**: the open-questions register rows that track the membership subject (NEW-7) and the CRM owner
  re-key (REG I6 names rows 120 and 135) record that TEAM scope is wired and which step activates it;
  `docs/decisions.md` needs no row (no ADR).

### 12.4 Domain rules

1. **TEAM reads** (A8, A9; REG J10, pending D-24): for `OwnerSubject.MEMBERSHIP` a TEAM grant admits a row
   whose `owner_id` is the caller's `own_ref` **or** whose `owner_manager_id` is. One level only: a
   manager's manager does not see through; this is a stated product limit. A row whose stamp is NULL matches
   nothing for TEAM, and a caller with no `own_ref` matches nothing: **fail closed**, tested for every list
   and every object route.
2. **Stamping.** *At write time*: wherever a service sets or changes `owner_id` (create, update, assign,
   round-robin, conversion, capture, reassign, restamp) it reads `owner_team` by primary key and writes
   `owner_manager_id` in the same statement; an unknown owner stamps NULL. *When a member's manager
   changes* (DD-29): the consumer restamps every row that service holds with that `owner_id`, set-based,
   `updated_at = updated_at` so C53's keyset never sees a mass update; `deal.activities` is the one
   exception, being append-only (old rows keep the manager of their time, which only affects TEAM on
   writing new activities for them, not reading, since reads stay at ORG). A member removed from the
   organization keeps their `owner_team` row (`status` changes) and their rows stay owned; their manager
   keeps TEAM over them so the rows can be reassigned. Nothing is ever auto-reassigned (R4.7).
3. **Versioned upsert.** A membership event applies only when its `occurred_at` is strictly newer than the
   row's `source_occurred_at`, so redelivery and reordering are harmless (ADR-0020 §2).
4. **Saved views**: a view stores absolute filters plus `owner=me` or `owner=team`, never a relative date;
   "closing this month" and its kind are code-defined presets resolved at click (DD-30). Renaming a view to
   an existing name is 409. A deactivated member's shared views disappear from other members' lists and
   stay editable by nobody; they are not deleted (§14).
5. **Reports.** `weighted_minor` per deal is `domain.weighted_minor(value_minor, probability) = (value_minor *
   probability + 50) // 100`, the summary's own function (DD-9, DD-41), computed in SQL as
   `floor((value_minor::numeric * probability + 50) / 100)` and summed per bucket, so the forecast's total
   equals the summary's weighted pipeline for the same filters (a test pins it, boundary vectors included). The
   overdue bucket is open deals whose `close_date` is before the local `as_of`; a period is a calendar month
   or quarter in `tz`; `won_*` count deals whose `closed_at` falls in the bucket. `win_rate_pct` is the
   summary's `domain.win_rate_pct(won, decided) = (200 * won + decided) // (2 * decided)` (half up) by count,
   and **null** when `won + lost` is zero (the legacy summary field answers 0 there, frozen; the reports say
   "no data" on purpose). The reasons grouping includes `reason_key = null` for deals closed without one. A
   report is never cached.
6. **The widening reconcile** (REG E.2 `†`, DD-38, K3) is a separate, later, owner-run act (§12.8) and is not
   undone by a code rollback **or by any tool**: `reconcile_permissions.py` is union-only (ADR-0007 §1) and no
   narrowing tool exists. Until it runs, `CRM_MANAGER` holds OWN on the `†` codes and the whole of this
   release's TEAM wiring is inert for everyone but custom roles that already hold TEAM on a CRM code. Those
   roles today get a 403 on every CRM write (`auth.py:342-347`) and from Deploy 1, **before** the owner's act,
   would get the team. Nothing in the repository says how many such roles exist, so the owner counts them first
   (O12b), and a non-zero count is a stop condition for Deploy 1, not a surprise afterwards.

The 22 object-check sites, for the guard and the review: customer `services.py:1072`,
`api/c1_customer360.py:61`, `api/routes.py:251, 271, 288, 352, 372, 389, 416`; deal `services.py:209`,
`api/routes.py:115, 253, 283, 303, 320, 341`; lead `services.py:449, 592, 598, 845, 971`,
`api/routes.py:215`; plus every call the follow-ups, views, history, report and reassign routes add. Four of
the 22 validate a target owner and take `manager_of` (deal `services.py:209` and `api/routes.py:115`, lead
`services.py:449` and `592`); the other eighteen take the row's stamp (§12.3.5).

### 12.5 Consumers and idempotency

| Handler | Queue and binding | Idempotency |
|---|---|---|
| `owner_team_changed` in customer, deal, lead | `customer.events`, `deal.events`, `lead.events`; `core.membership.created`, `.updated`, `.revoked` | envelope-id ledger; the strictly-newer version guard; the restamp is `UPDATE … WHERE owner_manager_id IS DISTINCT FROM :new`, so a repeat changes nothing |
| H10 `apply` | none (owner-run) | upserts guarded by the same version rule; restamp as in §12.4 rule 2; re-runnable |

Every handler runs under `services.service_ctx(organization_id)` (REG F1), binds the tenant per message and
writes an audit row only when it restamped (`acting_as="owner-team-projection"`, counts only).

### 12.6 UI

#### 12.6.1 Saved views on the four lists

`SavedViewMenu` (G8) joins the `FilterBar` actions of Leads, Deals, Companies and Contacts, fed by
`useSavedViews(entity)` (client; REG G8: the hook ships here, not in R3.3) over the per-service
routes of §12.3.1. Selecting a view sets every URL key from its `filters` and `sort` and writes `?view=<id>`
(§4.4.3); any later change to a filter makes the page **dirty** (the menu's marker has a visually hidden
"unsaved changes"), computed by comparing the parsed URL state with the stored filters. **Update view**
appears for the person's own dirty view; **Save as view…** opens a dialog (name 1-80, visibility "Only me"
or "Everyone in {organization}", the latter only when `useCan(read, Scope.ORG)`); **Delete** asks through
`ConfirmDialog`. Presets are links, never stored (DD-30): Leads **My leads**, **Unassigned**, **SLA
breached**, **Aged**; Deals **My deals**, **Closing this month** (the dates are computed at the click in
the organization timezone), **Stale**, **Overdue**; Companies **My companies**, **Customers**, **Prospects**;
Contacts **My contacts**, **Dormant**; and **My team** on each when `useCan(write, Scope.TEAM)`.

States: loading ("Loading views…" in the menu), error ("Couldn't load your views. Try again."; the lists and
presets keep working), empty (presets only), 409 `duplicate_view_name` on the name field, 422
`invalid_filter` ("A saved view can't contain an email address or a phone number."), 422 `too_many_views`
("You have 50 saved views. Delete one to save another."). On a phone the menu opens as a `Drawer`. Focus
returns to the menu button after every action; each action is announced ("View saved", "View deleted").

#### 12.6.2 Tags

`TagInput` (CRM-local, on `Combobox` and `Tag`) in the four create and edit forms and as the editor of a
**Tags** row on the detail pages (`InlineEditField`): type, Enter or comma adds, Backspace on an empty input
removes the last, each tag is a removable chip with an accessible "Remove {tag}", at most 20 (the field says
so at 20), the server's `invalid_tag` and `too_many_tags` map onto the control. Suggestions are the tags of
the rows already loaded (DD-31). Lists show up to two tags as `Tag` chips under the name and "+n" for the
rest; the filter bar gains **Tags** (a multi `Combobox`, free entry, AND semantics) written to repeated
`tag=` keys. Import and export gain a **Tags** column (comma separated, validated by the API per row, so a
bad tag is a row error). `Customer360View` shows the contact's tags in its profile block.

#### 12.6.3 History tabs

A **History** tab on the company, contact and lead pages (the deal page has it since R3.5), `HistoryList`
over the routes of §12.3.2, 25 per `LoadMore`. Company rows may show old and new status, owner and industry;
contact and lead rows say which fields changed, never what they were ("Priya changed the phone number and
the status"), with a plain-language label for each known field name and the raw name for an unknown one;
actors resolve through the roster ("A teammate" for rows from before the stamp). Empty: "Nothing has
changed since this record was created." Error: local `LoadFailure`.

#### 12.6.4 Reports `/reports` (gate `crm.pipeline.deal.read`)

The **Reports** entry joins the sidebar under **Insights** (§4.4.1). `PageHeader` "Reports";
`Tabs variant="line"` **Forecast** and **Win and loss** (`?tab=`). All figures come through one adapter,
`routes/reports/source.ts` (`loadForecast`, `loadWinLoss`, returning view models), so the screen never
imports the wire types and a later insights-backed source replaces one file (DD-32, D-12).

* **Forecast.** Filter bar: **Period** (Month or Quarter), **Show** (3, 6 or 12 periods), **Owner**
  (Everyone, Me, My team, a rep); URL keys `period`, `periods`, `owner`. For each currency a `ChartCard`
  "Open pipeline by expected close, {currency}" with `BarChart` (series Pipeline and Weighted, values with
  `formatMoney(minor, currency, { compact: true })`), then a `DataTable` (Period, Deals, Pipeline,
  Weighted, Won so far; an **Overdue** row first when it exists, a **Total** row last; `mobileCard="auto"`).
  The chart names itself and carries its own off-screen table (`BarChart`), and the visible table is the
  primary reading, so nothing depends on colour. Currencies are separate sections, never summed (U12).
* **Win and loss.** A `DateRangeField` in the organization timezone with presets **Last 30 days**, **This
  quarter**, **Last quarter**, **This year** (written as `from` and `to`), the owner select, then per
  currency: a KPI row (Won, Lost, Win rate), two `BarList`s **Why we lose** and **Why we win** (count first,
  value in the label, "Unspecified" for no reason; labels from `GET /deals/close-reasons` including archived),
  and an owner table (Rep, Won, Lost, Won value, Win rate).
* **States.** Loading: skeletons at the chart's final height; empty: "No open deals with a close date in this
  range." or "No deals were closed in this range." with the range controls still mounted; 422 range errors
  as the field message "Choose a range of at most a year"; 403 `PermissionDenied`; failure `LoadFailure`
  with the request id. A report issues one call plus the session reads (settings, owners) and one
  close-reasons call: at most four requests on a cold mount, pinned by a test.

#### 12.6.5 Team scope in the UI

The owner filters on every list and on Follow-ups gain **My team** (`owner=team`); an empty result says "No
records are owned by you or the people who report to you." Reassign controls use `useCan(write,
Scope.TEAM)`; a per-row refusal from the server (the row is outside the team) surfaces as the toast "You
can't change this record" with the request id, never as a disabled control that explains nothing. Nothing
in the SPA reads `owner_team` or a manager id: TEAM is evaluated on the server only.

#### 12.6.6 Replace-then-delete and visual surfaces

Nothing is deleted in this release (the Reports route was removed in R3.4 and is added here). Surfaces added
to `lib/pages.ts`: `reports-forecast`, `reports-win-loss`, `reports-empty`, `saved-views-menu`,
`saved-view-dialog`, `history-tab`; new `MockRoute`s and fixtures `reports-forecast.json`,
`reports-win-loss.json`, `views.json`, `history.json`, with `tags` added to every entity fixture; six
renderings with axe, no baseline entry.

### 12.7 Tests

Customer, deal, lead (each): `test_saved_views.py` (the named tests of §12.3.1, `test_filters_are_validated_by_the_lists_own_parsers`,
`test_an_identifier_in_q_is_422`, `test_fifty_views_is_the_limit`, `test_views_of_an_inactive_member_are_hidden_from_others`,
`test_view_owner_is_the_membership_not_own_ref`), `test_tags.py` (`test_normalisation_is_idempotent` (property),
`test_stored_sorted_and_deduplicated`, `test_more_than_twenty_is_422_too_many_tags`, `test_filter_is_and_over_repeated_tags`,
`test_a_noop_tag_patch_writes_no_audit_row`, `test_tag_values_never_enter_an_event_or_the_history_projection`),
`test_history.py` (§12.3.2), `test_owner_team_consumer.py` (`test_upsert_applies_only_a_strictly_newer_event`,
`test_redelivery_changes_nothing`, `test_manager_change_restamps_owned_rows_and_leaves_updated_at`,
`test_restamp_never_touches_deal_activities`, `test_a_revoked_member_keeps_their_rows_and_their_managers_team_scope`,
`test_an_envelope_for_another_organization_is_refused`, `test_unknown_member_stamps_null`),
`test_team_scope.py` (for each list and each object route: `test_team_sees_own_and_reports_rows_only`,
`test_team_with_a_null_stamp_matches_nothing`, `test_team_without_an_own_ref_matches_nothing`,
`test_team_cannot_touch_outside_the_team_403`, `test_a_membership_of_another_organization_matches_nothing`,
`test_own_scope_is_unchanged`, `test_org_scope_is_unchanged`, `test_owner_team_filter_resolves_on_the_server`,
and the target-owner tests of §12.3.5: `test_a_manager_can_assign_to_a_report`,
`test_a_manager_cannot_assign_to_an_outsider`, `test_a_manager_can_bulk_reassign_leads_to_a_report`,
`test_a_manager_cannot_bulk_reassign_leads_to_an_outsider`, `test_an_unknown_target_owner_fails_closed_under_team`),
migration tests (round trip, downgrade refusal and override, `pg_policies`, `relforcerowsecurity`, no
cross-schema FK, the append-only trigger still refuses `UPDATE` on `deal.activities` after the column
exists). Deal: `test_reports.py` (the five named tests of §12.3.3, `test_forecast_total_equals_the_summary_weighted_pipeline`,
`test_buckets_use_calendar_months_and_quarters_in_the_organization_timezone`,
`test_multi_currency_never_sums`, `test_reasons_include_unspecified`, `test_win_rate_rounds_half_up_and_is_null_at_zero`).
Lead: `test_consumers_fold.py` (the lead consumer runs in the API process when `run_consumers_in_api` is on
and not otherwise; `bin/worker` runs it alone; the role assertion). Account: `test_membership_events.py`
(`test_created_updated_revoked_carry_full_state`, `test_legacy_fields_are_unchanged`, schema validation through
the real write paths) and `test_memberships_read_callers.py` (for each of `deal` and `lead`:
`test_memberships_read_without_a_token_is_401`, `test_memberships_read_from_a_caller_off_the_list_is_403`
(parametrised over `finance` and `notification`), `test_memberships_read_with_another_organizations_claim_is_403`,
`test_memberships_read_through_the_real_client_helper_is_accepted_for_its_own_organization`; the same three
negatives for `customer` stay green). Deal and lead: `test_account_client.py` (the token carries
`iss`, `aud="account"` and the `org` claim; an unreachable account is a clean typed failure, never a partial
page). Contracts: the three `core.membership.*` schemas validate. Tool:
`tests/infra/test_backfill_owner_team.py` (`test_plan_writes_nothing`, `test_apply_needs_all_three_gates`,
`test_apply_is_idempotent_and_a_second_plan_shows_zero_drift`, `test_snapshot_time_is_taken_before_the_read`,
`test_restamp_leaves_updated_at`, `test_output_has_no_email_or_name`, `test_stops_at_the_first_failing_organization`).
Infra: `tests/infra/test_team_wiring.py`, `test_ops_monitor_dlq_queue_sync.py`, `test_ops_monitor_profile_containers.py`,
`test_compose_memory_budget.py`, `test_planned_headroom.py::test_runbook_ceiling_example_is_the_number_preflight_would_print`,
`test_memory_budget_totals_containers.py`, `test_party_link_env_wiring.py` (rows W18 and W19),
`test_io_outside_transaction.py`. Frontend: saved views (every state and rule of §12.6.1; URL round trip;
dirty marker), tags (add, remove, limit, server errors, filter AND), history tabs, reports (every state,
adapter seam, chart name and table, range error, budget), My team filters; surfaces of §12.6.6.

### 12.8 Rollout

R3.8 is **two deploys, a tool run, a soak and a separate owner act**, in this order (§D rule: consumers
before producers; data before enforcement):

1. **Owner, before anything**: (a) the **census** of O12b, a read-only count of custom roles that already
   hold TEAM on a CRM code and that Deploy 1 would turn from "403 on every write" into "the team" before step 6:
   zero proceeds, non-zero is a stop until the owner has decided, per organization, what to do with those roles
   (K3); (b) raise `EDGE_BUDGET_ACCEPTED_MB` in the box `.env` to the total `make edge-preflight` prints for
   Deploy 1, which is the ceiling the previous fold's release set plus one (3946 with `admin` on when R2.4's
   fold is the only earlier one; REG F8 and REG L17); an agent never edits it. The RUNBOOK literals change in the
   same commit as the fold.
2. **Deploy 1** (`make deploy HOST=… CONFIRM=… REF=<merged ref>`): the three migrations; the lead fold
   (`lead` becomes `api_with_worker`, `lead-worker` defined and off); the three consumers; the TEAM wiring
   (inert, §12.4 rule 6); tags, views, history, reports. Publish the SPA (`make edge-web … APP=crm`). **Verify**
   (owner): `lead` is healthy, `lead.events`, `customer.events` and `deal.events` show a consumer and the
   `core.membership.*` bindings (RUNBOOK's RabbitMQ recipe), no `*.events.dlq` depth; a saved view, a tag and
   a report work.
3. **Deploy 2** (account only): the enriched membership events with their committed schemas **and C57's caller
   allow-list widened to `deal` and `lead`** (§12.3.4), nothing else. **Verify**: change a test member's manager
   in the admin console and see the `owner_team` log line (`owner_team.applied`, ids and counts) in each of
   the three services; then run `remote.sh backfill-owner-team plan --service deal` and `plan --service lead`
   for one organization, each of which must print the memberships account holds (a 200 from that service's
   real client; a 403 means the allow-list did not deploy, and nothing proceeds).
4. **H10**: `remote.sh backfill-owner-team` `plan --service customer`, read the drift, `apply` with the three
   gates, then deal, then lead; a second `plan` for each must show zero drift. Re-run it after any consumer
   outage.
5. **Soak three working days** with TEAM wired and no TEAM grants (`CRM_MANAGER` still holds OWN on the `†`
   codes).
6. **Widen (owner)**. K3 has no undo, so **the soak evidence is written down before `APPLY=1`**, in the release
   note: the census result of step 1; H10's second `plan` of each of the three services showing zero drift
   on the day; no `*.events.dlq` depth on the three queues across the soak; every organization with members has
   a non-empty `owner_team` in each service. Then `make edge-reconcile HOST=… CONFIRM=… PRODUCTS=crm` prints
   the dry run, which must show exactly the eight `†` cells of REG E.2 that exist by now (`company.write`, `contact.write`,
   `deal.write`, `activity.write`, `lead.write`, `lead.convert`, `follow_up.read`, `follow_up.write`) raised
   from OWN to TEAM on `CRM_MANAGER`; read it, then `APPLY=1`, off-peak, paced (REG E.4: it bumps the
   organization's permission version and refreshes every token). **Verify** with a manager's account: the
   **My team** view shows the team's records, an edit to a report's record succeeds, an edit to an
   outsider's record is a 403.

*Flags*: none; TEAM's activation is step 6, per role. *Memory*, as a change against the running total
(§4.5; `memory_budget_totals.py` over `memory-budget.json`): the lead fold is **+1 MB over the parent commit**.
With R2.4 live and no other fold between (SP4's R4.4, R4.6 and R4.7 add theirs, and the arithmetic is always
the parent plus one) the ten sellable profiles go from 3748 to 3749 MB (97.1% of 3862) and from 3945 to 3946 MB
with `admin` (102.2%); on a tree without R2.4 the same fold takes 3747 to 3748 and 3944 to 3945. `lead-worker`
is priced 0 while `split-workers` is off.

### 12.9 Rollback

| Step | Reversible | How |
|---|---|---|
| SPA | yes | `make edge-web-rollback APP=crm` |
| Deploy 2 (account) | yes | `make edge-rollback`; the three consumers see no new events, so `owner_team` stops updating and the stamps drift until the next H10 `apply` |
| Deploy 1 | **yes while no TEAM grant exists; no after step 6** | `make edge-rollback`; tables and columns stay. After the widening, the previous code answers 403 "TEAM scope is not supported" to every manager write (`auth.py:342-347`), and no tool narrows a grant (`tools/revoke_permission.py` does not exist, ADR-0007), so after step 6 the only way is forward |
| Lead fold | yes | `RUN_CONSUMERS_IN_API=false` and `split-workers` in `EDGE_PROFILES`, which adds the 90 MB `lead-worker` and so needs the owner's memory decision (§14) |
| Data (tags, views, stamps, `owner_team`) | harmless to leave | nothing reads it after a rollback |

Stop conditions, checked before step 6 and after it: a non-zero census (O12b) before Deploy 1; a 403 from
account to H10's `plan` after Deploy 2; any `*.events.dlq` depth above zero on the three queues; a non-zero
drift in H10's second `plan`; a manager seeing a record outside their team in the step 6 check; a spike of
403s on CRM writes after the widening; an organization with members and an empty `owner_team`.

### 12.10 Acceptance criteria (mechanical)

1. `uv run pytest libs/platform-core -q`, `make test-services`, `make migration-roundtrip` and
   `uv run pytest tests/infra -q` pass; every test named in §12.7 exists.
2. `tests/infra/test_team_wiring.py` passes (it rejects a missing `team_owner_id=` and a constant one such as
   `None`), `grep -rn "team_col=" services/customer/app services/deal/app services/lead/app | wc -l` prints at
   least `6`, and `grep -rn "manager_of(" services/customer/app services/deal/app services/lead/app` finds the
   four target-owner sites of §12.3.5, customer's `_assert_owner_assignable` and the reassign target check.
3. `ls libs/contracts/events/core.membership.created/v1.schema.json
   libs/contracts/events/core.membership.updated/v1.schema.json
   libs/contracts/events/core.membership.revoked/v1.schema.json` lists all three.
4. `jq -r .containers.lead.kind infrastructure/edge/memory-budget.json` prints `api_with_worker`; `jq
   '.containers["lead-worker"].profile' infrastructure/edge/memory-budget.json` prints `"split-workers"`;
   `grep -n "lead.events" libs/platform-core/src/platform_core/ops_monitor/config.py` finds the `crm` entry;
   `python3 infrastructure/edge/lib/memory_budget_totals.py infrastructure/edge/memory-budget.json --profiles
   crm,hrms,inventory,support,storefront,projects,pulseboard,marketing,itsm,finance` prints 3747 plus one for
   every fold merged before this one and including it.
5. `make openapi-check` lists `/api/v1/companies/views`, `/api/v1/contacts/views`, `/api/v1/deals/views`,
   `/api/v1/leads/views`, the three `history` routes and the two `reports` routes; `python
   tools/export_permissions.py --check` passes unchanged (no code added).
6. `pnpm tokens:baseline` leaves the token baseline without a `product-crm` entry; the a11y baseline has no
   CRM entry; `make ui-visual` passes with the six R3.8 surfaces.
7. `tests/infra/test_backfill_owner_team.py` passes and the tool's stdout in a test run contains no `@`.
8. The account read admits its two new callers and nobody else: `test_memberships_read_callers.py` passes, and
   `grep -n "account_service_url" services/deal/app/config.py services/lead/app/config.py` finds both settings
   (REG F4, W18, W19).
9. The standing criteria of §4.5: S1 to S8 (S3 for `core.membership.*` and `crm.lead.*` schemas; `docs/crm.md`
   and the lead rows of its service map are current).

## 13. Owner-run steps

Everything below is run by **the owner**. No agent runs a deploy, a `make edge-*` target, a production
tool, a production read or write, the production smoke (`e2e/edge_product_crud.py`) or an edit of the box's
`.env` (REG §H preamble, E.4; P§3 F11: review, merge, manual deploy, verify, stop on any failure). An agent's
part is to build and test locally and to leave the owner a runnable command and a verification that has a
stated expected result. `HOST=… CONFIRM=…` are the box's, omitted below for width.

| # | Release | Step | Command or action | Verify (expected result) | Stop and do not continue if |
|---|---|---|---|---|---|
| O1 | before R3.1 | Answer the decisions of §2.1, **including the ones SP3 reaches only through SP2 or SP4** (D-1, D-2, D-3, D-8, D-10, D-11, D-13, D-21, D-27 and D-38), and the two questions this document raises, D-41 (a closed deal's fields) and the lead half of D-27 (the erase of a captured lead); and **acknowledge the three one-way points**: K1 (below R3.1a stays closed once a phone-only contact exists), K2 (below R3.7a stays closed once a captured lead exists, and a stored visitor's details can then only be removed by an administrator's per-lead erase) and K3 (the TEAM widening cannot be narrowed by any tool) | the owner's reply | each default in §2.1 is confirmed or replaced, and K1 to K3 are acknowledged in the reply | a decision that blocks the next release is unanswered |
| O2 | R3.1a | Confirm R1.2a's C77 has been deployed for a day, then deploy and publish | `make deploy REF=<merged ref>`, then `make edge-web APP=crm` and `make edge-web APP=engage` from the checkout of the deployed ref | the migrations ran, contacts and companies lists load, Engage's panel loads | any migration error; a 403 on notification's address lookups |
| O3 | R3.1 | H8 for customer's `email_index` | `remote.sh backfill-pii-indexes`: `plan --service customer --target email_index`, read the counts and the case-variant duplicate groups, then `apply` with `--confirm-count`, `--i-took-a-backup`, `--i-know-this-is-the-live-database` | a second `plan` reports zero rows to index; output has no address | any organization fails (the tool stops there) |
| O4 | R3.1b | Deploy the phone-only change, then smoke | `make deploy REF=<merged ref>`; `e2e/edge_product_crud.py`; create one phone-only contact through the API | 201 and the contact opens; no audit row holds an email, name or phone | contacts list errors rise |
| O5 | R3.2 | Deploy (every image rebuilds, gateway included) and publish | `make deploy REF=…`, `make edge-web APP=crm` | `GET /companies` and `GET /customers` answer the same; the dashboard Home loads | any 404 for `/companies`; a dashboard error |
| O6 | R3.3 | Publish five SPAs | `make edge-web APP=crm`, then `engage`, `hrms`, `projects`, `pulseboard` | a phone reveal works in CRM and an employee reveal in HRMS, both with the step-up dialog | a reveal failure rate above baseline |
| O7 | R3.4 | Deploy, check the reconcile is empty, publish | `make deploy`; `make edge-reconcile PRODUCTS=crm` (dry run); `make edge-web APP=crm`; `e2e/edge_product_crud.py` | the dry run lists nothing; Settings saves; a keyboard board move persists | settings saves failing; list 5xx above baseline |
| O8 | R3.5 | Deploy and publish two SPAs | `make deploy`; `make edge-web APP=crm`; `make edge-web APP=engage` | a deal closes with a reason and shows in History; a reveal writes its audit row | a history response with a field outside the allow-list |
| O9 | R3.6 | Deploy, smoke, reconcile, publish, watch the first morning | `make deploy`; create a follow-up due in two minutes (an admin), move it to another time and back to the original one, and see exactly one entry in the bell at that time; `make edge-logs SERVICE=scheduler` shows no `scheduler.schedule_requested.rejected`; `make edge-reconcile PRODUCTS=crm` (dry run: E20 and E21 on `CRM_MANAGER` and `CRM_REP`), then `APPLY=1`; `make edge-web APP=crm`; next morning read the deal logs for `deal.review.baseline` | the reminder arrives; the dry run shows only those grants; no stale or overdue alert on day one | `scheduler.job.parked`; any DLQ above zero; more than 200 alerts for one organization in a run |
| O10 | R3.7a | Deploy, publish, backfill lead indexes | `make deploy`; `make edge-web APP=crm`; `remote.sh backfill-pii-indexes` `plan` then `apply` for `--service lead --target phone_index`, then `--target email_index` (read the collision count first) | the SLA saved on the Assignment page shows on the list; a second `plan` shows zero | any 5xx on `GET /leads` |
| O11 | R3.7b | Confirm the ADR and D-27, set the capture secret **before** the deploy, deploy, publish, exercise the intake. **One-way: switching capture on starts storing the personal details of people who never signed in; the code can then not be rolled back below R3.7a (K2), and the only removal is an administrator's per-lead erase (§11.3.3), with no bulk erase** | confirm `crm-public-lead-capture` is accepted and D-27 is answered for leads; edit the box `.env`: `CAPTURE_TOKEN_SECRET=<openssl rand -hex 32>` (never in the repo; `make edge-preflight` refuses the deploy without it, §11.3.4); `make deploy`; `make edge-web APP=crm`; the `curl` post, 21 posts from one address and one signed webhook of §11.8; switch on WhatsApp leads and message from a new number | 200 and an assigned lead; 429 on the 21st; the signed webhook creates one lead and a replay returns it; one WhatsApp lead named with the masked number | the ADR or the D-27 answer is missing; capture 429s above 5% of public posts; a sealed field revealing wrongly; `lead.outbox` growing |
| O12 | R3.8 | Raise the memory ceiling before the first deploy | edit the box `.env`: `EDGE_BUDGET_ACCEPTED_MB` to the total `make edge-preflight` prints for Deploy 1, the previous ceiling plus one (3946 with `admin` when R2.4's fold is the only earlier one) | `make edge-preflight` prints the post-deploy total at or under it | preflight refuses |
| O12b | R3.8 | **Census of custom roles that already hold TEAM on a CRM code** (a read-only count, nothing else; production reads are the owner's) | as the admin database role: `SELECT count(*) AS grants, count(DISTINCT r.organization_id) AS organizations FROM account.role_permissions rp JOIN account.roles r ON r.id = rp.role_id JOIN account.permissions p ON p.id = rp.permission_id WHERE p.code LIKE 'crm.%' AND rp.scope = 'TEAM';` (no CRM role template holds TEAM today, `libs/contracts/permissions/crm.json`) | `0` and `0` | any non-zero count: decide per organization before Deploy 1 (§12.8 step 1), because Deploy 1 would turn those roles' 403 into the team before step 6 |
| O13 | R3.8 | Deploy 1 and publish | `make deploy`; `make edge-web APP=crm` | `lead` healthy; `lead.events`, `customer.events`, `deal.events` have a consumer and the membership bindings; no `*.events.dlq` depth; a saved view, a tag and a report work | any DLQ depth |
| O14 | R3.8 | Deploy 2 (account: the enriched events **and C57's caller allow-list**) | `make deploy REF=<merged ref>`; then `remote.sh backfill-owner-team plan --service deal` and `plan --service lead` for one organization | changing a test member's manager logs `owner_team.applied` in all three services; each `plan` prints the memberships account holds (a 200 from that service's real client) | no log line, or a DLQ depth, or a 403 from account to either `plan` |
| O15 | R3.8 | H10 per service | `remote.sh backfill-owner-team` (`make edge-backfill-owner-team`): `plan --service customer`, `apply` with the three gates, then deal, then lead | a second `plan` per service shows zero drift | non-zero drift after `apply` |
| O16 | R3.8 | Soak three working days, **write the soak evidence down**, then widen (K3 has no undo) | record in the release note: the O12b result, H10's second `plan` of all three services at zero drift on the day, no `*.events.dlq` depth across the soak, a non-empty `owner_team` for every organization with members; then `make edge-reconcile PRODUCTS=crm` (dry run: exactly the eight `†` cells to TEAM on `CRM_MANAGER`), read it, then `APPLY=1`, off-peak | a manager's **My team** view shows the team; an outsider's record answers 403 on edit | the evidence is incomplete; a 403 spike on CRM writes; an unexpected cell in the dry run |
| O17 | every release | Production smoke | `e2e/edge_product_crud.py` (REG H7: its owner step uses the activation path since R2.7) | passes | any failure |

`tools/demo/seed_crm.py` is run against a development database only, by whoever is doing stateful UI
work; it is never pointed at the box.

## 14. Risks specific to SP3

| # | Risk | Where it bites | Mitigation in this design | What stays true |
|---|---|---|---|---|
| K1 | A phone-only contact (NULL email) breaks the previous code's list | R3.1 rollback | two deploys (DD-1): the tolerant readers soak first; the rollback refuses while a NULL-email row exists (§5.2.5, §5.9) | below R3.1a is closed once a phone-only contact exists (a one-way point the owner acknowledges, O1) |
| K2 | A lead with a new `source` breaks the previous code's `LeadOut` literal (a 500 on the list) | R3.7 rollback | two deploys (DD-36); the widened literal ships first | below R3.7a is closed once a captured lead exists; the only repair is an owner-approved production write that loses provenance, and a stored visitor's details can then only be removed by an administrator's per-lead erase (a one-way point the owner acknowledges, O1 and O11) |
| K3 | TEAM widening cannot be undone by a code rollback or by any tool | R3.8 | the widening is a separate owner act after a soak and a zero-drift H10; the custom-role census first (O12b) and the soak evidence **written down before `APPLY=1`** (O16); stop conditions before and after (§12.8, §12.9) | after step 6 the only way is forward: `reconcile_permissions.py` is union-only and no tool narrows a grant (ADR-0007); an owner acknowledgement, O1 |
| K4 | `owner_team` is stale or empty, so TEAM matches too little | R3.8 | fail closed (a NULL stamp matches nothing); the strictly-newer upsert; restamp on a manager change; H10 plan shows drift and `apply` repairs it | TEAM is only as fresh as the last event; events are not replayable, so a consumer outage needs an H10 run |
| K5 | TEAM is one level deep | R3.8 | stated; a manager's manager does not see through | a two-level organization gets one-level TEAM |
| K6 | Anonymous capture is abused, and it stores personal data of people who never signed in | R3.7b | MAC before any database work, four Redis budgets that fail closed with anonymous and signed traffic in separate organization buckets (DD-46), a honeypot that no autofill matches, the soft origin check, merge-by-email into a separate messages list (DD-44), identical responses, per-endpoint pause, counters on the Sources page, the secret as a box-wide brake (§11.3.2, §11.3.4), and an administrator's per-lead erase that ships a release earlier (§11.3.3) | there is **no CAPTCHA** (it needs an egress decision, ADR-0016) and **no bulk erase**, so junk that gets in under the caps (at most 500 a day an organization through forms, 300 an hour an endpoint) can be disqualified in bulk through the Excel bulk-update engine (ADR-0022), which keeps its details, or erased one lead at a time. **Switching capture on is one-way** (K2): stored details can only be removed by that per-lead erase. D-27 (pending) includes whether a dedicated `crm.lead.lead.erase` code or a bulk erase is wanted |
| K7 | `trust_forwarded_for` on lead trusts a header from the private network | R3.7b | the rightmost entry only, from a private peer only (`proxy_headers.py:54`); the per-endpoint budget does not depend on the address | a container on the `back` network could dodge the per-address budget; it is already inside the trust boundary |
| K8 | A leaked form token lets anyone post leads to one endpoint | R3.7b | the token is public by design (it sits in a web page); the endpoint's budgets and pause switch bound it; the soft origin check blocks accidents | a deliberate attacker with the token is limited by the budgets only, and can spend the anonymous organization allowance (500 a day), after which form posts are refused until the next day; signed webhooks have their own bucket and are unaffected (DD-46). The tenant sees the refusals on the Sources page; there is no push alert, because lead has no recipient set to alert |
| K9 | A form is embedded with an old consent text | R3.7b | the consent text hash is stored as evidence with each lead; every change of the text is audited with the old and new words | the evidence says which words, not that a person read them |
| K10 | Alert noise: stale and overdue notices flood a rep | R3.6 | silent baseline; one event per crossing; markers set even when a notification is off; per-run caps; per-organization opt-outs | a deal that goes stale again after a re-arm notifies again, by design |
| K11 | The scheduler is now a core, always-on dependency | R3.6 | the move leaves the ten-profile and `admin` totals unchanged from the parent commit and adds 90 MB only to boxes that run `crm` without `finance` (§10.8); `scheduler.events` joins the core DLQ list; the handlers re-check state so a late or duplicate firing is harmless | if the scheduler is down, reminders and reviews wait; nothing is lost, they fire late |
| K12 | A scheduled arm is lost or does not land: the review chain breaks and only heals on a deal write, or a follow-up's arm never takes effect | R3.6 | `ensure_review_armed` on every deal write, idempotent by key; the DLQ alert on `deal.events`; follow-up keys are **single-use** (one `arm_seq` per arm, DD-42), so a reschedule back to an earlier time can never land on a cancelled key, and the handler drops a firing from an older arm | an organization with no deal writes at all whose chain broke gets no alerts until one happens; a follow-up whose arm event is lost (for example by a rollback to a scheduler without the new bindings) never reminds, and still shows as overdue in the page and the tile |
| K13 | Reminder text carries no deep link | R3.6 | DD-22: free text may hold personal data; C72's `entity_ref` is R4.2's | the bell says "a follow-up is due" and not which |
| K14 | The legacy directory keeps its 200-row cap on three detail pages for two releases | R3.4-R3.7 | scoped to those routes only (§8.6.1); each page's rebuild removes its wrapper | names on those pages can still read "Unassigned" past 200 rows until R3.5 and R3.7 |
| K15 | Offline interactive development of CRM screens ends with fixtures mode | R3.4 | DD-12: typed builders, contract-validated page fixtures, the seeded demo organization | ten sibling apps keep fixtures mode; the divergence is the accepted cost of D-23 |
| K16 | `PATCH` still edits a closed deal's value and dates | R3.5 | the page renders closed deals read-only; D-41 asks the owner whether D-5's "closed deals immutable" means field edits too | win and loss figures change retroactively if someone edits a closed deal through the API or an import |
| K17 | Audit rows hold free text the history projection hides | R3.5, R3.8 | the projection is an allow-list; contact rows hold names only from R3.1 | `deal.updated` and `lead.updated` audit rows still hold the old and new names and notes; contact erasure (R4.1) rewrites a contact's, an administrator's per-lead erase rewrites a lead's (§11.3.3), and deals have no erase |
| K18 | Scale ceilings | R3.2, R3.6, R3.8 | no trigram index (DD-8), the review reads an organization's open deals once a day, history is one indexed read, tags have no index (DD-31) | an organization with tens of thousands of deals or leads will want a trigram index and a review index; the numbers are in the plan's decisions, not in this design |
| K19 | The box is over its memory line with `admin` on | R3.8 | +1 MB, priced with `memory_budget_totals.py`; the owner raises the accepted ceiling by one before the deploy (D-13) | 102.2% of MemTotal with `admin`, which the owner has accepted at 3944, is asked to accept at 3945 for R2.4's fold and at 3946 for this one (a running total: one more for every fold merged first); rolling the lead fold back to a separate worker adds the 90 MB `lead-worker`, which is a second memory decision |
| K20 | Contact `q` still matches an email | R3.2 | documented as legacy; new code uses `POST /contacts:search`; the SPA never sends an identifier as `q` | an old caller can still put an address in a URL until a later contractive release |
| K21 | A dependency on SP2 slips | all | the prerequisite table (§4.2); the deviations of §17 | R3.4 cannot ship before R2.5, R2.7 and R2.8, and R3.6 not before R2.4 |
| K22 | The legacy lead audit rows carry `full_name`, `company` and `notes` | R3.7 | the projection never returns them; new capture rows write ids and enumerations only; the lead erase rewrites a lead's old rows through `lead.scrub_lead_audit` (§11.3.3) | older `lead.created` and `lead.updated` rows of a lead nobody erases keep the name in the audit table |
| K23 | A deactivated member's shared saved views become orphans | R3.8 | they disappear from other members' lists (`owner_team.status`) and are not deleted | nobody can edit or delete them until the member is reactivated; there is no admin cleanup in this programme |
| K24 | WhatsApp stranger leads are best effort, and the step touches lead from inside notification | R3.7b | the step asks lead and caches a definite no per organization for five minutes; an unreachable lead (down, no container, unset URL, timeout) is a counted skip with no retry and no dead letter, so a lead outage can never trip `notification.events.dlq` (DD-45) | a stranger who writes once while lead is down gets no lead; turning the switch on takes up to five minutes to apply on a busy box; the contact fold in front of the step keeps its own coupling to customer (a customer outage still redelivers once and then dead-letters) |
| K25 | Tax ids, addresses and legal names of sole proprietors are personal data (REG L11) | R3.1, R3.5 | audit rows hold ids and field names only (DD-48, §5.4 rule 8); events and the company summary carry business facts only; the history route returns names only | the address, legal name and GSTIN of a one-person company stay in their tables until the consent and erasure design (SP4) decides; erasing a contact does not touch the company |

## 15. Out of scope

Decided out by the programme (P§5): two-way mail or calendar sync, telephony, AI scoring or drafting, a
second workflow engine, full CPQ, custom objects or custom fields, tiering CRM modules by plan, a new
container per feature, a CRM-local BI service. Decided out by this design, with the trigger that would
bring each back:

* **Reopening a closed deal** (D-5: a separate decision) and **editing a closed deal's commercial fields**
  server-side (D-41).
* **Duplicate merge** for leads, contacts or companies; relaxing the unique deal name; starting a converted
  contact as `qualified` (DD-5): SP4's lifecycle automation (R4.3) owns status transitions.
* **A CAPTCHA, a hosted form page, per-tenant CORS** (DD-23; ADR-0016's egress decision); **a bulk lead
  erase** and a dedicated `crm.lead.lead.erase` code (D-27; the per-lead erase on E22 is in scope, §11.3.3);
  a push alert when a capture budget is spent (K8).
* **SLA notifications** for first response (DD-28: no event, rule or recipient is registered).
* **A multi-level reporting hierarchy** for TEAM; **an org-chart UI**; sharing a saved view with named
  people; a per-user default view; a **tag catalogue** or tag management screen (DD-31).
* **Report export, forecast categories and quotas, per-pipeline reports** (one pipeline per organization,
  D-5).
* **Everything cross-product on the pages**: orders, finance, support, delivery, party timeline (SP4's
  sections arrive by adding entries to the tab and section lists, §9.6.3), notes attachments (R4.5, after
  R19), the Quotes tab (R4.5).
* **Consent decisions and contact erasure** (SP4, `crm-consent-and-erasure`); SP3 stores consent evidence
  only (B39) and ships only the per-lead erase of §11.3.3, which covers a lead's own copy, not a contact.
* **A dark-theme toggle**, a native mobile app, offline mode, and a shared dev login in `@opsgrids/ui`
  (DD-13).
* **Deleting any file the replace-then-delete ledgers (§8.6.5, §9.6.5, §10.6.4, §11.6.5) do not name**;
  the `/deals/summary` frozen fields stay (§8.6.5).
* **Notification deep links** (`entity_ref`, C72: R4.2) and Engage's own scheduled-send fix (L8).

## 16. Registry amendments

This document proposed new registry rows and changes to existing ones. They are no longer listed here: every
one was folded into the registry (`2026-10-07-crm-overhaul-interface-registry.md`, §J.2, "Folded amendments:
the handle map and the ledger"), and the registry is the binding source for every name. The handles used
throughout this document are the final ones from that map. The two questions this document raised are owner
decisions of the consolidated register, and §2.1 carries their rows: the closed-deal question is D-41
(REG L22) and the lead-erase question is part of D-27 (REG L23).

**Rejected: none of this document's proposals.** One was confirmed with no change: `G8`, the saved-view
component in R3.3 and `useSavedViews` in R3.8, which the registry's owner cell already said. The one rejection
that touches a row of this document is SP4's: its 403 `crm_not_active` for C56 is rejected for that route
(REG J27), so the answer stays this document's 200 with `skipped_reason: "not_entitled"` (§11.3.2); SP4 keeps
the 403 for customer's own routes.

Where the fold changed a name or a shape that this document had proposed, the body agrees with the registry:
the handles in the §J.2 map; the single notification mechanism (EV54); the type of C56's consent field and the
rule that a form lead alone is forwarded (J23, C56, C58); `owner_team.employee_id` (B71, J28); the count
column of the Pipeline settings page, shown only when C1 returns it; and the one creator of
`lead.scrub_lead_audit` (B86, J26). Differences from the programme contract that no registry row carries
are in §17.

## 17. Deviations from the programme spec

1. **The Sources settings page ships in R3.7b, not R3.4** (P§10 lists the settings hub in R3.4; REG J31 returns
   this against P§10, and the registry's C39 and C40 are R3.7's). R3.4's hub has Pipeline, Reasons, Workspace, Lead
   assignment and Sales team; "Lead sources and forms" is added with its routes.
2. **R3.1, R3.7 and R3.8 are not single deploys, and three points are one-way.** R3.1 is two deploys (DD-1),
   R3.7 two (DD-36), R3.8 two deploys plus an owner-run tool and a separate owner act (REG J31). Each remains
   independently releasable, and each is reversible as P§10 and M2 require **except three one-way points** that
   the owner acknowledges in O1: **K1** (below R3.1a stays closed once a phone-only contact exists), **K2**
   (below R3.7a stays closed once a captured lead exists, and a stored visitor's details can then only be removed
   by an administrator's per-lead erase) and **K3** (the TEAM widening cannot be narrowed by any tool). §14
   states each with its mitigation.
3. **Prerequisites are wider than P§10's "R2.7 for rep views; R2.5 for settings"** (§4.2, REG J18 and J30): R3.2
   needs R2.2 and R2.3; R3.4 needs R2.8; R3.5 needs R2.3; R3.6 needs R2.4, R2.7 and R2.8; R3.7 needs R2.8 and
   R3.1's H8 run; R3.8 needs R2.4, R2.6, R2.7, R2.8, R3.6 and R3.7a. R1.2 is two releases (REG J31): R3.1 and R3.2
   need R1.2a, whose C77 and 360 path fix they stand on, and R3.4 needs R1.2b, whose removals its ledgers assume. Through those releases the owner decisions
   D-1, D-2, D-3, D-8, D-10, D-11, D-13, D-21 and D-38 become prerequisites too (§2.1), and R3.7b additionally needs
   its ADR accepted and D-27 answered for leads. D-13 first blocks R2.4 (P§8 says R4.6) and D-11 blocks R2.4 (P§8 says R1.1's ADR), REG J32.
4. **The deal variant of the history route ships in R3.5**, not R3.8 (DD-16; REG C15 registers the deal
   variant in R3.5 and the company, contact and lead variants in R3.8).
5. **Overview drops "won this month"** until a closed-date filter exists; the reports (R3.8) answer it.
6. **EV27 is per organization, not per deal** (DD-19; REG EV27 carries it): one scheduled review per organization
   per day replaces a scheduler job per deal and kind.
7. **`RevealableField` takes the hook result as a prop** because `@opsgrids/ui` may not import `@opsgrids/client`
   (ADR-0001 wall; DD-10). REG G6 carries the change, so `stepUp`, `resetKey` and `load` sit on the
   `useRevealedField` call and not on the component.
8. **TEAM needs a bootstrap the programme did not name**: membership events cannot be replayed, so
   `owner_team` starts empty; H10 seeds and repairs it (§12.3.4; REG H10).
9. **Lead SLA has no notification** (DD-28): the clocks and filters ship; nothing alerts on a breach.
10. **P§1 criterion 1** ("edits exactly their own records through OWN scope") is read as REG J19 amends it:
    reps read at ORG and write at OWN, and a manager works a team; the owner is asked (D-38). This spec tests the
    reps' views at ORG for reads and at OWN for writes.
11. **A closed deal's fields remain editable through the API** (D-41): D-5's "closed deals immutable" is
    implemented as the no-move rule SP2 owns; the pages are read-only.
12. **The 360's company and billing sections are not built here**: the company page's tab list is data so
    SP4 adds Billing, Quotes and the delivery card without a redesign (§9.6.3).
13. **SP1 owns the placeholder removal, the 404 page and the dead `derive.ts` rollups** (SP1 DD-5; REG G15 and
    the R1.2b row of REG N carry the split). This document's ledgers, offender table (97 in 14 files at R3.4's
    start, 101 in 15 at HEAD), acceptance greps and §8.6.4 are stated against the post-R1.2b tree, and the 404
    component is SP1's `routes/NotFound.tsx`, which R3.4 only restyles.
14. **The typed builders, `stubApi`, the frozen-clock helper and the dev-only sign-in ship in R3.4, not R3.3**
    (SP1 §5.4 class 2 lists R3.3 for the test utilities and the login). They replace the fixture-client tests
    in the release that deletes fixtures mode, and the sign-in is CRM-local rather than shared (DD-13).
15. **R3.7 carries more than the programme lists.** R3.7a ships an administrator's per-lead erase and its
    audit-scrub function (DD-47, D-27; REG C108, B86 and the R3.7a row of REG N), because capture must not store
    what nothing can remove; R3.7b needs its ADR accepted, D-27 answered for leads, and the capture secret set
    **before** the deploy (preflight refuses an unset key, §11.3.4).
16. **R3.8's Deploy 2 also widens account's C57 caller allow-list** to `deal` and `lead`, which SP2 defers to
    "their first caller" and never itself performs (§12.3.4; REG C57 lists the widening in R3.8's Deploy 2), and
    adds a census step before Deploy 1 (O12b).
17. **The frozen summary fields can move at exact halves**: one minor unit per deal for the weighted pipeline
    and one point for the win rate, because the float path is replaced by one integer rule (DD-9, §6.4 rule 5).
18. **A follow-up's scheduler key carries an `arm_seq`** beyond the registry's first-draft
    `follow_up:{id}:{due_at_iso}` (DD-42; REG EV26 and B25 now carry it), because the scheduler keeps a
    cancelled key for a week.
19. **System in-app alerts are one mechanism** shared with SP4/R4.2 (EV54): SP4's first-draft
    `BUILTIN_CRM_ALERTS` is withdrawn in favour of `SYSTEM_IN_APP_RULES`, which SP4's R4.2 extends.
20. **The Pipeline settings page shows a deal count only when the API returns one.** REG C1 honours
    `include=counts` only for a caller whose `crm.pipeline.deal.read` grant is ORG, and counts live deals only, so
    for any other caller the count column is omitted and the archive dialog is worded without a count and always
    offers the `move_open_deals_to` select for an open stage, because the server requires a target whenever the
    stage holds a deal (REG C5). This is the minimal choice that keeps a deal-derived figure from reaching a
    caller no read route would show it to; the first draft showed the count to every caller who opened the page (§8.6.3).

## Appendix: what each release changes, in one line

R3.1 data-model fit (two deploys); R3.2 list vocabulary, `/companies`, generated types; R3.3 shared board,
detail list, 360 view, reveal, saved-view menu, money, command-menu results; R3.4 shell, settings hub, list
rebuilds and the first deletions; R3.5 contact, company and deal pages with close reasons and deal history;
R3.6 follow-ups, reminders, stale and overdue alerts, scheduler cancel, Overview; R3.7 capture, duplicates,
SLA, reassign, lead page, the administrator's per-lead erase, Sources (two deploys); R3.8 saved views, tags, history, reports and TEAM scope.
