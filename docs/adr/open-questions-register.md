# ADR open-questions register

Every question an ADR left open, what was decided, its status and the backlog item that builds it.
The register exists so that nothing is **decided but unbuilt without anyone noticing**. The
decisions are in the ADRs themselves, each under a section headed "Decisions on open questions
(2026-09-24)" (ADR-0013's sits under §7; the screen specification's is §9.3). The ADRs are the
authority, and this page is the index to them. Backlog item ids (R2, O6, …) are the ledger's
(`docs/backlog.md` and the release loop's item list).

**Statuses.**
- **DECIDED**: the question has an answer. Where it needs building, a backlog item is named.
- **DECIDED (default; owner may revise)**: a conservative answer given where the real input is
  commercial or legal. The row says what would change it.
- **DECIDED (resolved)**: the question had already been answered by code or a later ADR, and this
  pass recorded that.
- **NOT-TRIGGERED**: deliberately waiting on demand. The ADR records the exact trigger and what to
  build when it fires.

**Counts (2026-09-24).** 49 questions. **44 DECIDED**: 4 carry an owner-revisable default, 2
need an owner action, and 7 were already resolved or built. **5 NOT-TRIGGERED.** 8 of the
decisions need **new backlog items**, NEW-1 to NEW-8 at the end, and one more item, NEW-9, comes
from ADR-0017. A further 5 limitations are recorded by ADR-0017 (Q3, merged); they are listed
but not decided again here. The CRM overhaul's decisions are proposed in [ADR-0038](0038-crm-programme-decisions.md) and settle as the owner answers them; the counts above do not change.

ADRs with nothing open: [0000](0000-phase-0-decisions.md), [0001](0001-frontend-topology.md),
[0003](0003-surfaces-and-authorization-topology.md) (its two obligations are P4 and R31, not
questions), [0014](0014-inventory-and-manufacturing.md), and
[0018](0018-domain-verification-lifecycle.md) (its two "Revisit when" items are triggers, and it
decided against its own resolver-error alert).

---

## The register

| # | ADR | Question | Decision (one line) | Status | Built by | In ledger? |
|---|---|---|---|---|---|---|
| 1 | [0002](0002-row-level-security.md) Q5 | A global unique `org_domains.domain` lets one customer see another's claims | Exclusivity only once verified: a partial unique index on verified domains, and a per-organization unique index for claims | DECIDED (resolved) | P1 (merged, ADR-0018) | yes |
| 2 | [0004](0004-commercial-model.md) Q1 | Tax computation and jurisdiction, including OpsGrids' own GST on commerce billing | The supplier is the operator entity (billing settings). The recipient is a billing profile, snapshotted at finalize. Billing computes GST once on the subtotal (R2's rule), then IGST, or CGST plus SGST with the odd paisa to SGST. The Rule 46 series is supplier-wide, consecutive per financial year, kept in billing and allocated at finalize. Corrections are credit notes. Finance keeps OpsGrids' own returns and IRNs under the operator organization | DECIDED (direction; SAC value and AATO gate are defaults the owner may revise) | **O6** | yes (O6's scope must include all six parts: billing profile with 2 `core` permissions and an ADR-0007 reconcile, supplier settings, series table, credit notes, `platform_core.gst`, finance's operator-organization import and the IRN back-channel) |
| 3 | [0004](0004-commercial-model.md) Q2 | More than one currency on one account | One billing currency per organization, refused at subscribe. Two currencies means two organizations | DECIDED | **NEW-1** | **no** |
| 4 | [0004](0004-commercial-model.md) Q3 | Revenue recognition | A derived report, never a stored ledger. Add a nullable service window to every invoice line now, because lines are append-only and cannot be backfilled | DECIDED | **NEW-2** (column); the report when an audit asks (Q4's home) | **no** (column) |
| 5 | [0005](0005-dunning-and-grace.md) Q1 | Per-plan grace windows | None. One platform ladder. A contract would get a plan-version feature of 7–14 days | DECIDED | none; trigger: a signed contract clause | n/a |
| 6 | [0005](0005-dunning-and-grace.md) Q2 | Immediate de-entitlement on suspension | Stays eventual (≤900 s). An urgent stop uses account's levers, not commerce's | DECIDED | none; trigger: an incident that needed minutes | n/a |
| 7 | [0005](0005-dunning-and-grace.md) Q3 | Long-suspended subscriptions | A console filter at ≥30 days suspended, plus a human, audited, one-at-a-time operator cancel. Never a timer | DECIDED | **NEW-3** | **no** |
| 8 | [0005](0005-dunning-and-grace.md) Q4 | Retrying against a different instrument | Every debit uses the organization's current usable mandate. With none, it fails `no_mandate` without calling the provider | DECIDED | **R3**, **B5 (5g)** | yes (R3 must include the mandate chosen at debit time for dunning retries) |
| 9 | [0006](0006-approval-engine.md) Q1 | The fate of `memberships.manager_membership_id` | Kept as core's own TEAM rung. It never routes approvals | DECIDED | **R20** (documentation only; no migration) | yes |
| 10 | [0006](0006-approval-engine.md) Q2 | `processed_events` keyed on `event_id` alone | Key on `(organization_id, event_id)` from the envelope. Legacy rows are checked for 14 days. The table stays un-policied | DECIDED | **Q7** | yes |
| 11 | [screens](../design/approval-engine-screens.md) O2 | Can HR see an assigned request without deciding it? | Read-only, with no HR take-over (ADR-0006 §6). The override sentence shows only when the caller's grants include `attendance.write:ORG` | DECIDED | **NEW-4** (gate the sentence on grants) | **no** |
| 12 | [screens](../design/approval-engine-screens.md) C10 | A "mine only" filter on `GET /requests` | `?requester=me`, layered on top of scope | DECIDED (resolved; built) | — | — |
| 13 | [screens](../design/approval-engine-screens.md) C9 | Which timezone an attendance record renders in | `AttendanceOut.location_code`; a `null` falls back to labelled organization time | DECIDED (resolved; built) | — | — |
| 14 | [0007](0007-permission-rollout-to-live-tenants.md) Q1 | Removing a permission from every tenant, and what a narrowed token does | A separate `tools/revoke_permission.py`: system roles only, `pv` bump, the reverse of the rollout order, next request narrowed | DECIDED | none until the first removal (trigger) | n/a |
| 15 | [0007](0007-permission-rollout-to-live-tenants.md) Q2 | A second mechanism that writes grants (seat classes) | Account stays the only writer. Seat classes become system role templates plus an assignment rule | DECIDED | **O2** (after C6) | yes |
| 16 | [0008](0008-membership-employee-link.md) Q1 | Which screen writes `memberships.employee_id` | A "Linked member" field on the HRMS employee page, using the user's own token | DECIDED | **NEW-5** | **no** |
| 17 | [0008](0008-membership-employee-link.md) Q2 | An immediate, user-level session kill | An operator route `sessions:revoke` on auth, plus a `pv` bump in each of the user's organizations | DECIDED | **NEW-6** | **no** |
| 18 | [0008](0008-membership-employee-link.md) Q3 | Scheduling the link verify pass | Nightly, dry-run, inside the orphan-check job, alerting through ops-monitor | DECIDED | **R21**, **R13**, **R7/C10** | yes |
| 19 | [0008](0008-membership-employee-link.md) Q4 | When to drop `employee.employees.membership_id` | After one production dry-run reports zero; the input field keeps refusing permanently | DECIDED (owner input: the production dry-run) | **R21** | yes |
| 20 | [0008](0008-membership-employee-link.md) Q5 | `sref` is one slot for ten products; OWN scope is empty without HRMS | HR-shaped products keep the employee link. CRM, ITSM and projects key OWN on the membership (`mem`) | DECIDED | **C15** (CRM; [ADR-0038](0038-crm-programme-decisions.md) proposes it as R2.6 and R2.7 for OWN on the membership (pending D-7) and R2.3 and R2.5 for configurable stages (pending D-5)), **C14/Q11** (projects, standup, timesheet), **NEW-7** (platform-core resolver; ADR-0038 proposes it as R2.2 and finds it larger than this row says, because the resolver registry is written and never read, `libs/platform-core/src/platform_core/auth.py:297-301`), **NEW-8** (ITSM) | partly (C15 and C14 must name it in their scope) |
| 21 | [0009](0009-uploaded-images-and-object-storage.md) Q1 | The private half: payslips, contracts | A private bucket and a `private_objects` table. The owning product authorizes and audits. Presigned GETs last 60 s, through a dedicated Caddy route that keeps the query string | DECIDED | **R19** | yes |
| 22 | [0009](0009-uploaded-images-and-object-storage.md) Q2 | Per-organization quota and storage metering | When priced: the catalog feature `document.storage_bytes`, 402 when exceeded, a daily `byte_days` usage event, history backfilled from the table | DECIDED (default quota numbers; owner may revise) | **R19**, **C6** | yes |
| 23 | [0009](0009-uploaded-images-and-object-storage.md) Q3 | The reference warning on delete | Derived in the editor from the theme document, plus a batch `GET /assets?ids=` | DECIDED | **R19** | yes |
| 24 | [0009](0009-uploaded-images-and-object-storage.md) Q4 | A cookieless asset domain | A dedicated registrable domain outside `opsgrids.com`, when a CDN arrives or a second writer shares the bucket | DECIDED (default; owner names the domain) | owner action when triggered | n/a |
| 25 | [0009](0009-uploaded-images-and-object-storage.md) prerequisite | The gateway body cap in §5 | Landed: 2 MiB by default, 6 MiB on `/api/v1/documents` | DECIDED (resolved) | — | — |
| 26 | [0010](0010-delivery-serviceability-and-slots.md) Q1 | The entitlement-lapse consumer has no owner | Storefront owns it and listens to the **activation flip**. Product emits `core.product.(de)activated` when a projection flips it | DECIDED | **R14** | yes (R14 must include the product-side emission and a `storefront-worker` container in the budget) |
| 27 | [0010](0010-delivery-serviceability-and-slots.md) Q2 | API errata 7–9 | `rejected_lines`; a server-side demand `GROUP BY`; `GET /delivery/postal-codes` | DECIDED | **R16** | yes |
| 28 | [0010](0010-delivery-serviceability-and-slots.md) slice 6 | Sub-postal-code precision | Trigger: a merchant asks in writing **and** the buyer path has a coordinate source (slice 5 / R17, or a geolocation decision). Build: optional node coordinates and a radius, `ST_DWithin` applied only when a point is given, a new reason `outside_radius` | **NOT-TRIGGERED** | — | — |
| 29 | [0010](0010-delivery-serviceability-and-slots.md) prefix | Anonymous public paths by prefix | The platform capability has landed (`public_path_prefixes`, 2026-09-23). The storefront stays on exact paths. Trigger: the first anonymous GET that must be addressed by URL. Build: one resource-specific prefix plus route-table parity | **NOT-TRIGGERED** | — | — |
| 30 | [0011](0011-store-identity-brand-domain-and-publishing.md) multi-store | A second store per organization | Trigger: an organization needs a second live storefront, in writing. Build (its own ADR): drop the one-store constraint and re-key nine tables by `storefront_id` | **NOT-TRIGGERED** | — | — |
| 31 | [0012](0012-custom-domains.md) Q1 | Does Caddy re-consult `ask` when renewing? | Yes. Verified in certmagic v0.23.0, which Caddy 2.10.0 pins. Gate 2 stays unconditional | DECIDED (resolved; verified) | — (R15's certificate deletion is hygiene) | — |
| 32 | [0012](0012-custom-domains.md) Q2 | `on_demand_tls` options; the SNI-overridden probe | Resolved by errata 16 and 15 | DECIDED (resolved) | — | — |
| 33 | [0012](0012-custom-domains.md) Q3 | Let's Encrypt's rate limits | Re-read: 50 certificates a week per registered domain binds new store subdomains, with ZeroSSL as fallback. A total loss counts as renewals | DECIDED | none; trigger: >40 new stores a week, then a wildcard via a delegated zone | n/a |
| 34 | [0012](0012-custom-domains.md) Q4 | Registrars that mangle underscore labels | Keep `_opsgrids-challenge`. Add an apex TXT alternative only after two merchant reports | DECIDED | none (trigger) | n/a |
| 35 | [0012](0012-custom-domains.md) Q5 | One VM against a high-traffic merchant | Measure on a trigger (20 req/s on one host, or p95 above 500 ms); the answer is slice 4 | DECIDED | none; needs R12's metrics to see the trigger | n/a |
| 36 | [0012](0012-custom-domains.md) Q6 | Is the edge IP actually reserved (erratum 11)? | The owner confirms an Azure Standard, static IP before any merchant apex record | DECIDED (owner action) | owner deploy checklist | n/a |
| 37 | [0012](0012-custom-domains.md) Q7 | Slice 2's deploy pipeline and pager | Pager: R7 through C10. Deploys stay manual over SSH while Actions is blocked. CI images later | DECIDED | **R7**, **C10**, **E5** | yes |
| 38 | [0012](0012-custom-domains.md) Q8 | Slice 3 has no owner | R14 owns the lapse consumer. R15 owns certificate deletion, the 308, notifications, probes outside the lock and the 410 | DECIDED | **R14**, **R15** | yes |
| 39 | [0012](0012-custom-domains.md) Q9 | The `CLAUDE.md` line about `infrastructure/` | Already amended | DECIDED (resolved) | — | — |
| 40 | [0012](0012-custom-domains.md) slice 4 | Move the edge | Trigger: an uptime clause in writing, ~200 live or failing domains, or `caddy_data` lost once. Build: Cloudflare for SaaS first (our TXT stays the ownership proof), or a second box with shared certificate storage | **NOT-TRIGGERED** | — | — |
| 41 | [0012](0012-custom-domains.md) slice 5 | More than one domain per store | Trigger: one merchant needs two live domains, in writing. Build (its own ADR): a `storefront_domains` table, verified-only unique host, the directory role widened to a second table on named columns | **NOT-TRIGGERED** | — | — |
| 42 | [0013](0013-self-serve-products-and-the-one-box-deployment.md) §7 a | Per-seat or usage pricing | Flat stays the default. Seat classes (O2) and WhatsApp usage (R4), sold by sales first | DECIDED (default; owner sets the numbers) | **O2**, **R4**, **C6** | yes |
| 43 | [0013](0013-self-serve-products-and-the-one-box-deployment.md) §7 b | Mobile apps | No native app. The mobile audience (R31) comes first | DECIDED | **R31** | yes |
| 44 | [0013](0013-self-serve-products-and-the-one-box-deployment.md) §7 c | A second box | Not yet. When forced, Postgres moves off-box first | DECIDED | none; trigger: preflight refuses with nothing left to de-list, or ADR-0012 slice 4 | n/a |
| 45 | [0013](0013-self-serve-products-and-the-one-box-deployment.md) §7 d | Staff approval of signups | None. The rate limit (R1), the set-password link and the trial's end. On a junk wave: a per-domain cap and an organization suspend | DECIDED | **R1** | yes |
| 46 | [0015](0015-scheduler-and-not-before-delivery.md) Q1 | Parked scheduled jobs | Kept until a person acts. Alerted. A CLI unparks or discards them (`discarded` is a new status) | DECIDED | **PLAT-E** (S18), **R7** | yes (PLAT-E must include the CLI and the `discarded` status) |
| 47 | [0015](0015-scheduler-and-not-before-delivery.md) Q2 | `scheduler` coupled to the `finance` profile | Moves to core in the same change as its first producer outside finance, with a preflight check | DECIDED | scope of **L2** or **D4b**, whichever adds that producer | partly (a scope note) |
| 48 | [0016](0016-egress-guard.md) Q1 | Nothing enforces the use of `EgressGuard` | A static classification test with a checked-in allowlist, landed with Q8's webhooks | DECIDED | **Q8** | yes (Q8 must include the test) |
| 49 | [0019](0019-statutory-portal-credentials.md) | Where IRP, GSP, e-Way and IMS portal credentials live (O8) | In finance: sealed, under RLS, tied to the registration, used only in the call step. Not in config-service | DECIDED | **B5 (5b)** | yes |

### Recorded by ADR-0017 — listed, not decided again

[ADR-0017](0017-crm-lead-service-and-pipeline-scaffold-removal.md) (the CRM lead service; Q3,
merged 2026-09-24) records its "Known limitations" as its own decisions. They are listed here so
the register is complete. They are not re-decided.

| ADR-0017 limitation | Built by | In ledger? |
|---|---|---|
| `customer.contacts.email` stays plaintext when a lead converts; lead email is sealed | Accepted by ADR-0017 ("business contact data"). If it is ever sealed, that is a `customer` schema change for **C15/Q2**; [ADR-0038](0038-crm-programme-decisions.md) names the optional sealing as R2.9 Part C, after the C15 flip has soaked and only on an explicit yes (pending D-26; the proposed default is no) | not scheduled (accepted debt) |
| The PII blind-index key is platform-wide, not per tenant | **P3** (PII envelope v2, per-service HKDF) | yes |
| An aborted conversion leaves orphaned company, contact and deal records; `crm.lead.conversion_aborted` has no consumer | **NEW-9** (proposed in [ADR-0038](0038-crm-programme-decisions.md) as R2.1 and R2.4; pending D-1, D-2, D-3, D-4) | **no** |
| Pipeline stages stay hard-coded (`deal.app.domain.STAGE_ORDER`); configurable stages are accepted debt | **C15** (in deal's own schema, never a separate `pipeline` service; proposed in [ADR-0038](0038-crm-programme-decisions.md) as R2.3 and R2.5, pending D-5) | yes |
| Case-insensitive company-name uniqueness is enforced by the application, going forward only | no action (decided) | n/a |

Note on row 20: ADR-0017's `lead` scope keys OWN on `subject_ref`, as `customer` and `deal` do.
The ADR-0008 Q5 decision moves all three CRM processes to the membership together, in C15. It does
not contradict ADR-0017's current state. [ADR-0038](0038-crm-programme-decisions.md) (proposed) defines C15 and names the releases that build it (pending D-7).

---

## Decided but not covered by any ledger item

These need items added. Each is small unless marked otherwise, and the ordering constraint comes
from the file ownership of in-flight work.

| New item | From | What to build | Order |
|---|---|---|---|
| **NEW-1** One billing currency per organization | 0004 Q2 | A guard in `subscription._subscribe` refusing a version whose currency differs from the organization's live subscriptions, with a negative test | after R2 merges (subscription) |
| **NEW-2** A service window on every invoice line | 0004 Q3 | Nullable `service_start`/`service_end` on `billing.invoice_lines`, written at pricing time (advance recurring, arrears usage, proration and close windows) | after R2 merges (billing) |
| **NEW-3** Long-suspended subscriptions | 0005 Q3 | A `suspended_for_days_gte` filter on the console's `/admin/subscriptions` (projection keeps `suspended_since`); a `cancel` override kind; an operator cancel route in subscription with reason and audit | after R2 and Q1 merge |
| **NEW-4** O2's override sentence gated on grants | screens O2 | The HRMS `RequestDetail` shows "use the attendance record" only when `/members/me/grants` resolves `hrms.time.attendance.write:ORG` | with HR-C (R6/R20) |
| **NEW-5** Link an employee to a member | 0008 Q1 | A "Linked member" field on the HRMS employee page; picker `GET /members?linked=false`; `PATCH /members/{id}`; unlink with a warning that everyone's sign-in refreshes | any time (frontend only) |
| **NEW-6** Operator user-level session kill | 0008 Q2 | `POST /api/v1/auth/operator/users/{id}/sessions:revoke` behind `require_operator({owner, support})`: calls `revoke_all_for_user`, bumps `pv` in each of the user's organizations, audits | after Q1 merges (auth, operator routes) |
| **NEW-7** OWN keyed on the membership (platform-core) | 0008 Q5 | `ScopeResolver` may declare a membership subject; `ScopeContext.apply`/`assert_can_touch` honour it; tests | before C15 and C14 (ADR-0038, proposed: R2.2; its TEAM tasks pending D-24) |
| **NEW-8** ITSM OWN on the membership | 0008 Q5 | Switch ITSM's `requester_id` OWN to the membership and migrate existing rows through account's link, reporting any that cannot be mapped | after NEW-7 (ADR-0038, proposed, schedules only NEW-7, as R2.2; ITSM's own switch is outside the CRM programme) |
| **NEW-9** Consumers for aborted lead conversions | ADR-0017 | Deal marks the orphaned deal lost (annotation: closing as lost distorts win rate and fires tenant automations, programme design §3 fact F3, so [ADR-0038](0038-crm-programme-decisions.md) D-1 proposes a soft-delete instead, pending D-1); customer removes the orphaned company and contact if nothing else references them (narrowed in D-2, pending D-2); both bound to `crm.lead.conversion_aborted` (proposed as R2.1 and R2.4) | any time; Q3 has merged (fits with C15) |

### Scope notes for items that exist

Each of these items covers the decision but must name the part in brackets:
- **O6**: all six parts of row 2.
- **R14**: product emitting `core.product.(de)activated` when a projection flips it, and a
  `storefront-worker` container priced in the budget.
- **R3/B5g**: the mandate chosen at debit time for every charge, including dunning retries.
- **PLAT-E**: `tools/scheduler_jobs.py` and the `discarded` status. **R7**: a parked-count alert.
- **Q8**: the egress classification test.
- **R21**: the nightly verify run inside R13's job, and the column drop gated on the owner's
  production dry-run.
- **C15/C14**: OWN on the membership. [ADR-0038](0038-crm-programme-decisions.md) (proposed) names the C15 releases: R2.6 and R2.7 (pending D-7), with configurable stages in R2.3 and R2.5 (pending D-5).
- **L2/D4b**: moving the scheduler to core if either adds a producer outside finance.
- **R19**: the owning product authorizes private objects; the internal presign route; a Caddy
  route that keeps the query string and `Host`.
- **R16**: the errata shapes of row 27.
- **R20**: `manager_membership_id` is documented, not dropped.

### Owner inputs and actions these decisions name

- Supplier GSTIN, legal name and state; the SAC code; confirmation that OpsGrids' AATO has crossed
  the e-invoice threshold (row 2).
- Pricing numbers for any per-seat or usage version (row 42) and for storage (row 22).
- The production dry-run of `reconcile_membership_links.py` before R21 drops the column (row 19).
- Confirming the edge public IP is Azure Standard SKU and static before any apex record (row 36).
- Naming and registering the cookieless asset domain when its trigger fires (row 24).

R2's own "Deferred, deliberately" list in ADR-0005's billing-run addendum is R2's decision and is
not repeated here. Most of its entries have no ledger item: credit draw-down, cancel at period end,
late usage, dunning a second invoice, refunds, and a meter priced by both a bundle and a standalone
subscription. The loop state already tracks them under its deferred findings.
