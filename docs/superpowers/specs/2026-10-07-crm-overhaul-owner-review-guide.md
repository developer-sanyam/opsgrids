# CRM overhaul — owner review guide

Status: **written by hand on 2026-10-10** after the last automated writing pass stalled. This guide is the
review. The other documents are build contracts, read by whoever builds a release; do not read them end to
end. Everything below points to the few sections that need you.

## 1. Where things stand

| Piece | State |
|---|---|
| Programme spec (519 lines) | Done and reconciled on 2026-10-10. One decision register, D-1 to D-41 (§8) |
| Interface registry (1,175 lines) | Done. 197 amendments from the four specs folded in; §J is the audit trail |
| Four sub-project specs (1,958 / 3,429 / 4,045 / 4,396 lines) | Done, each reviewed on two lenses and aligned with the registry |
| Plan 1, foundations (11,334 lines) | Written at step level. **10 review issues are not applied** (1 high: ADR-0038 is sized for 25 decisions, the register has 41). Its second review never ran |
| Plan 2, backbone (15,770 lines) | Step level for R2.1 to R2.3, task cards for R2.4 to R2.9. **Not reviewed** |
| Plans 3 and 4 (862 and 893 lines) | Release outlines, expanded to step level when each release starts. **Not reviewed** |
| Building | **Nothing is built, deployed or committed.** 0 of 26 releases |

The documents still carry 58 follow-up notes, mostly cross-reference upkeep (§8 lists the ones that need you).
They are known and listed; none changes what you are asked to approve.

## 2. What to read

| Read | Why | Time |
|---|---|---|
| This guide | The whole decision, in one place | 15 min |
| Programme spec §2, "Known limits of M2 and the memory assumption" | Where the design cannot keep what you approved | 10 min |
| Programme spec §8 (the 41 decisions) and §10 (the release train) | What you answer and what ships when | 20 min |
| SP1 spec §6 and §10 | The only owner steps before the first deploys, and where SP1 departs from the programme | 10 min |
| SP2, SP3, SP4 specs: §13 (owner-run steps), §14 (risks), §17 (deviations) | One short block each; read before the first release of that sub-project | 15 min each |

Skip: the registry, the plans, and the bodies of the specs. Each plan opens with a "Plan status" and a
"Release map" that say what is step-level and what is an outline.

## 3. The release train

26 releases, about 35 to 40 separate deploys (R1.2, R3.1, R3.7 and R3.8 deploy twice; R4.5, R4.6 and R4.7 ship in
stages). Every release is built on a branch, verified locally with `make gate` (GitHub Actions billing is blocked),
merged by you, deployed by you naming a ref, and verified, and it stops on the first failure.

| Release | What changes | You decide | You do | Reversible? |
|---|---|---|---|---|
| R1.1 | Decisions record, glossary, doc cleanup. Docs only | none | merge (nothing deploys) | n/a |
| R1.2a | The Customer 360's deal section starts working; tests guard that bug class | none | deploy, production smoke check | yes |
| R1.2b | Dead code removed, lead keys usable in rules, missing event contracts | none | deploy after R1.2a is verified | yes |
| R2.1 | A retried lead conversion can no longer collide with an aborted one | D-4 | deploy | yes |
| R2.2 | Platform change so a product can scope "own records" by membership; nothing switches yet | D-24 (part) | deploy (rebuilds every image) | yes |
| R2.3 | Pipeline stages become data per organization (same six by default) | D-5, D-12 | deploy | yes |
| R2.4 | Aborted-conversion cleanup, log-only first | D-1, D-2, D-3, D-13 | run the count of past aborts, raise the memory ceiling to 3945, read the log lines, then switch cleanup on | yes until switched on |
| R2.5 | Admins can edit pipeline stages | D-6 | deploy, reconcile one tenant then the fleet, publish the SPA | yes |
| R2.6 | Reps can be linked to members; the owner-id mapping tool exists | D-7, D-8, D-10 | production census; review the mapping plan per organization | yes (a journal reverses it) |
| **R2.7** | **Reps can work in a CRM-only tenant** (own-record scope on the membership) | D-9 | apply the mapping first, sign the security review, deploy three images, publish the SPA | rollback reverses via the mapping |
| R2.8 | Four roles replace six; new permissions | D-20, D-21, D-25, D-38 | reconcile, deploy four images | additive |
| R2.9 | Optional cleanup; optional email sealing | D-26 | none unless you say yes | n/a |
| R3.1 (2 deploys) | Company tax ids and addresses, phone-only contacts, deal numbers, win/loss reasons | D-14 | email-index backfill, soak | **one-way (K1)** once a phone-only contact exists |
| R3.2 | Search, sort and filters on every list; `/companies` | D-22 | deploy (rebuilds every image) | yes |
| R3.3 | Shared UI parts; no change for users | none | none | yes |
| R3.4 | New CRM shell, navigation and settings hub; rebuilt lists; placeholders removed | D-23 | publish the SPA | yes |
| R3.5 | Contact, company and deal pages with the 360 | D-41 | none | yes |
| R3.6 | Follow-ups, reminders, stale-deal alerts; scheduler moves to the core profile (+90 MB where `crm` runs without `finance`) | none | watch memory | yes |
| R3.7 (2 deploys) | Lead capture forms and webhook, duplicate detection, SLA, reassign, per-lead erase | D-27 (leads) | accept ADR 0041, set the capture secret on the box | **one-way (K2)** once a captured lead exists |
| R3.8 (2 deploys + tool) | Saved views, tags, history, reports, team scope | D-13, D-24, D-25 | run the tool, soak three working days, write the evidence down, then widen; ceiling 3946 | **the widening is one-way (K3)** |
| R4.1 | Party identity; ids on every CRM event; erase; consent writer | D-14, D-27, D-33, D-35 | accept ADR 0039 | an erase cannot be undone |
| R4.2 | Engage contact book becomes a CRM projection; sends from CRM; assignment alerts | D-15, D-37 | none | a sent message cannot be unsent |
| R4.3 | Finance, orders and payments connected; per-company projection; lifecycle setting | D-27, D-28, D-29, D-34 | log census before the order-to-invoice chain is switched on | issued tax invoices survive a rollback |
| R4.4 | Tickets and projects link to the company; a won deal drafts a project | D-13, D-17 | ceiling 3947 | yes |
| R4.5 (3 stages) | Quotes, discount approval, handoffs; PDFs wait for the private object store (R19) | D-18, D-19, D-39, D-40 | accept ADR 0042 before the approval deploy; confirm which R19 landed | not while a quote is pending approval |
| R4.6 (5 stages) | Analytics, attribution, insights, dashboard tiles, global search | D-12, D-13, D-16, D-36 | insights database check; answer on its +99 MB; ceiling 3948 | partly |
| R4.7 (4 parts) | HR hooks, storefront enquiries, orphan check, coverage audit | D-13, D-27, D-31 | ceiling 3949 | partly |

## 4. The decisions you answer

All 41 are open. Each has a recommended default in programme spec §8, so your answer can be "approve the
defaults" or a list of amendments. They are grouped here by what they unblock first.

**Nothing blocks R1.1, R1.2a or R1.2b.** Those can start the moment you approve the programme.

| Needed before | Decisions | The recommended default, in one line each |
|---|---|---|
| R2.1 | D-4 | Bump the conversion generation on every abort, and announce the deleted-record bump |
| R2.2 | D-24 (part) | Include team scope for managers, taken from the reporting line; the widening is a separate act of yours |
| R2.3 | D-5 | One pipeline per organization; stage keys never change; each stage is open, won or lost; archive, never delete |
| R2.3 | D-12 | Stages land first; insights adopts the stage migration unchanged and moves to the outcome field |
| R2.4 | D-1 | The orphan deal is soft-deleted, not closed as lost |
| R2.4 | D-2 | Cleanup deletes only records the aborted attempt provably created, never a reused one; log-only first |
| R2.4 | D-3 | You count past aborts on the box before R2.4; a sweep is built only if the count is not zero |
| R2.4 | D-13 | You raise the memory ceiling one MB per consumer fold (3945, 3946, 3947, 3948, 3949), or free memory |
| R2.5 | D-6 | New permission `crm.pipeline.stage.write`, organization-level only |
| R2.6 | D-7, D-8, D-10 | Reps are linked to members by a new column; the mapping is a plan you review per organization and never guessed; you run the census |
| R2.7 | D-9 | Approve the access widening (reps gain write, convert and reveal on their own rows) after a signed review of every own-record route |
| R2.8 | D-20, D-21, D-25, D-38 | Four role templates; plan stays flat; managers do not reveal their team's phone and email; reps keep reading everything and write only their own |
| R3.1 | D-14 | Company is the party, contact is the person, phone-only contacts allowed |
| R3.2 to R3.5 | D-22, D-23, D-41 | `/companies` canonical; replace-then-delete for anything with a live user; closed deals stay editable through the API |
| R3.7, R4.1, R4.3, R4.7 | **D-27, D-31, D-33** | What each store keeps after an erase, marketing opt-in on public forms, and the STOP/unsubscribe rule. These are legal and data-subject answers, not engineering's; the affected releases do not ship until you and legal answer |
| R4.1 | D-35 | A party created from another product is owned by the caller, else by the organization's owner |
| R4.2 | D-15, D-37 | Engage contacts become a CRM projection; CRM sends need an Engage role |
| R4.3 | D-28, D-29, D-34 | An online refund is recorded only; a minimal fulfilment-settings slice; lifecycle promotion is off until an organization's admin turns it on |
| R4.4 | D-17 | The kickoff is built in PulseBoard first |
| R4.5 | D-18, D-19, D-39, D-40 | Discounts go through the approval engine; quotes live in `deal`; attachments wait; quote handoff needs no new permission |
| R4.6 | D-16, D-36 | Marketing plans and Engage sends; first-touch attribution |
| none | D-11, D-26, D-30, D-32 | Keep three CRM processes; do not seal contact email; defer the public pay page; build no cross-product company facts |

## 5. Where the design cannot keep what you approved

**M2: "each release reversible on its own."** It holds for code, schema and configuration. It does not hold for six
cases; you accept them, or refuse the release that needs one, by approving the specs:

1. **An erase** cannot be undone, and the previous customer image does not know it happened. customer is not rolled
   back once any contact has been erased.
2. **A tax invoice the order-to-invoice chain issued**, and **a company promoted by the lifecycle setting**, survive a
   rollback. The levers are each organization's own switches.
3. **A quote pending approval** has no handler in the previous images, so deal and approval are not rolled back
   while one exists.
4. **Approval moves from the `hrms` profile to core**: a 99 MB container on every box with `crm` and no `hrms`.
5. **Events a code rollback discards** (a bound queue outlives its handler) are recoverable only where a catch-up
   tool exists.
6. **Editing the unshipped insights migration in place** is done only after your read-only database check shows it
   has run nowhere.

**Three one-way points.** **K1:** after R3.1, once a phone-only contact exists, you cannot roll back below it.
**K2:** after R3.7, once a captured lead exists, the visitor's details can only be removed by an administrator's
per-lead erase. **K3:** the manager team-scope widening cannot be narrowed by any tool, so it is a separate act of
yours after a three-working-day soak, with the evidence written down first.

**Memory.** The box has 3862 MB. The ten products cost 3747 MB; with `admin`, 3944 MB, which is exactly the
accepted ceiling in the box's `.env`, so every extra MB is your edit. The programme needs five consumer folds at
+1 MB each (ceiling 3945 to 3949), then insights (+99 MB) and a PDF library in `deal` and `finance` (10 to 20 MB
each, not measured). With insights and PDFs the ten products reach 3871 to 3891 MB, above the box's 3862 MB, which
only a raised ceiling or a bigger VM covers. The default "no memory increase" is therefore not achievable; insights
and PDFs ship only after you answer that separately. Folding `lead` into `customer` (D-11) would free about 98 MB.

## 6. Your checklist, in order

About 60 owner-run steps in total. Each is written out with its command, expected result and stop rule in SP1 §6
and in SP2, SP3 and SP4 §13. An agent never runs one.

1. Approve or amend the programme and the first decisions (§4). Nothing else starts until you do.
2. Production reads, by hand on the box: the count of past aborted conversions (D-3) and the CRM census (D-10).
3. Before each consumer fold, edit `EDGE_BUDGET_ACCEPTED_MB` in the box `.env` to the number `make edge-preflight`
   prints (3945, 3946, 3947, 3948, 3949).
4. Review the owner-id mapping plan for every organization before R2.7 (D-8). Wrong mappings give a rep another
   rep's pipeline.
5. Per release: merge the PR, deploy naming the ref, verify, and stop on any failure.
6. After permission releases (R2.5, R2.8, and the ones that add codes): reconcile dry run, then apply, off-peak.
7. Publish each SPA last, from a checkout of the deployed `main` (`make edge-web` builds the local checkout).
8. Accept ADRs when their release needs them: 0038 (as you answer), 0039 (R4.1), 0041 (before R3.7b), 0042 (before
   the approval deploy), 0043 (R4.5). 0040 is already taken on another ref.
9. Before R4.5-B, confirm which version of the private object store (R19) landed. Before R4.6-B, the read-only
   insights database check and your answer on its +99 MB.

## 7. The ten risks that matter

1. A wrong owner mapping hands a rep another rep's pipeline and their sealed contact data. Mitigation: human-reviewed
   dry run, untouched unmapped rows, data before code, a reversible journal, a signed review.
2. NEW-9 cleanup destroys real data by trusting an event that can name reused records. Mitigation: lead fix first,
   provenance from customer's own audit, log-only first, soft-delete.
3. The platform scope change silently widens or empties a product's scope. Mitigation: employee link stays the
   default, a static test bans raw `subject_ref`, exhaustive negative tests.
4. Event changes break tenants' automations and the unmerged insights consumer. Mitigation: stage keys never change
   meaning; the outcome field is additive.
5. Memory (§5).
6. Fan-out leaks personal data or defeats erasure. Mitigation: ids-only events, consent stays in `notification`.
7. Legal and data-subject answers (D-27, D-31, D-33) are not engineering's to give.
8. The unmerged insights stack: four branches, three Alembic heads.
9. About 60 owner-run steps depend on your time; a skipped read-only check removes a stop rule.
10. The documents disagreed after parallel authoring. Mitigation: this reconcile, the precedence rule in programme §0.

## 8. The follow-ups that need you

Of the 58 notes the documents carry, these need an answer or acceptance from you:

1. `finance.payments.currency` is a named exception to "the payment ledger is append-only".
2. Approval moving to the core profile (+99 MB, ADR 0042, exception 4 in §5).
3. The R1.2a/R1.2b split of the single defect fix from the hygiene work.
4. ADR numbers 0038, 0039, 0041, 0042, 0043 are expectations; confirm each when filed.
5. SP3 and SP4 disagreed on route C56 (200 with `skipped_reason: not_entitled`, or 403). The registry settled it for
   SP3; SP4's author must accept it or you override.
6. The memory figures differ with and without insights (3945 to 3949, or 4047 and 4048 with insights).
7. SP2's two Alembic revision ids (`f3b8d1a65c02`, `d8a35c17e920`) are unused at HEAD but cannot be proven chain
   heads until the files exist.
8. The registry's open questions L18 to L23 are now D-38, D-39, D-25, D-40, D-41 and D-27.

The rest is my cross-reference upkeep: the registry's decision table stops at D-34, plan 1 still says ADR-0038 has
25 rows, some plan drafts cite old decision ids, and a few prerequisites are missing (R3.5 before R3.6 to R3.8,
R1.2b before R2.3 and R3.4).

## 9. What happens after you answer

- **R1.1 and R1.2a start immediately**: no decision blocks them. R1.1 is documents only and R1.2a fixes the 360.
- Each later release is expanded to step-level tasks (for cards and outlines), built on a short-lived `feat/*`
  branch, verified with `make gate`, then handed to you to merge and deploy.
- Builds are fast; your gates and the weekly usage limit set the pace. At three releases a week, 35 to 40 deploys
  is about 12 to 13 weeks; at two a week, about 18 to 20; at one a week, about 35 to 40. Reps can work (R2.7) about
  week 5 to 6 at three a week.
- If you want fewer risks first, answer only D-4, D-5, D-12 and D-24 now and the rest as each release approaches.
