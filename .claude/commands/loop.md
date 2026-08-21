---
description: Autonomously build a complete SaaS product end-to-end in verified vertical slices, delegating to specialist subagents behind hard quality gates.
argument-hint: [product idea | path/to/spec.md | status | resume | ship]
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, WebSearch, WebFetch, TodoWrite, Agent, Skill
model: opus
disable-model-invocation: true
---

# /loop — end-to-end SaaS build loop

## Context

- CWD: !`pwd`
- Repo: !`git rev-parse --is-inside-work-tree 2>/dev/null || echo "NOT A GIT REPO"`
- Branch + dirty files: !`git status --short --branch 2>/dev/null | head -40`
- Last 5 commits: !`git log --oneline -5 2>/dev/null || echo "no history"`
- Root listing: !`ls -A 2>/dev/null | head -40`
- Package manifest: !`cat package.json 2>/dev/null | head -40 || cat Gemfile 2>/dev/null | head -30 || echo "no manifest"`
- Loop state: !`cat .claude/loop/STATE.md 2>/dev/null || echo "NO STATE FILE — COLD START"`

## Input

`$ARGUMENTS`

Interpret it as:
- `status` → print the state ledger summary and the next 3 planned slices. Do nothing else. Stop.
- `resume` → skip to **Phase 4** using the existing ledger.
- `ship` → skip to **Phase 5**.
- a path to a spec/PRD file → read it, treat it as the source of truth for Phase 1.
- anything else → treat it as the raw product idea.
- empty → read the ledger; if none, ask the user for the product idea and stop.

## Mission

Take the input from idea to a deployable, tested, documented, production-shaped SaaS
application. Work in **thin vertical slices**. Every slice ends green or gets reverted.
Never leave the repo in a broken state between slices.

## Non-negotiable operating rules

1. **You are the orchestrator, not the implementer.** Delegate implementation, testing,
   and auditing to subagents. You plan, sequence, triage findings, resolve conflicts,
   and own the ledger. Only edit files yourself for the ledger and for one-line
   conflict fixes a subagent cannot resolve.
2. **One slice at a time.** A slice is a user-visible capability that crosses the whole
   stack: schema → API → UI → tests → E2E → docs. No horizontal "build all models,
   then all controllers" phases.
3. **The gate is binary.** A slice is done only when every check in **Quality Gate**
   below returns green. No "mostly passing", no skipped specs, no `.skip`, no `--no-verify`.
4. **Max 3 fix rounds per slice.** If the gate is still red after 3 rounds, stop the loop,
   write the blocker to the ledger, and report to the user with options. Do not thrash.
5. **No fake work.** No stubbed functions that return hardcoded data, no TODO placeholders
   in shipped paths, no tests asserting `true === true`, no mocked-away integration points
   presented as working. If something cannot be built, say so in the ledger.
6. **No secrets in the repo.** Real values go to `.env.local` (gitignored); `.env.example`
   carries the keys with dummy values.
7. **Commit per slice.** Conventional Commits. Run the gate before every commit.
8. **Update the ledger after every phase and every slice.** It is the only durable memory
   of this loop.
9. **Ask before destructive or expensive moves:** dropping data, changing the tenancy
   model after slice 1, adding a paid third-party dependency, force-pushing.

## Phase 0 — Intake

If the input is a bare idea and the repo is empty, use `AskUserQuestion` to lock down the
minimum you cannot infer, in **one** batch of at most 4 questions:

- Target user + the single job-to-be-done for v1
- Stack preference, or "you decide"
- Auth model: email/password, magic link, OAuth, SSO
- Tenancy: single-user, team/workspace, or org-with-orgs
- Billing in v1: yes/no; if yes, which processor
- Deploy target: Vercel, Fly, Render, AWS, self-hosted Docker

If a spec file was supplied, skip the questions and derive the answers from it. Record any
assumption you had to make in the ledger under **Assumptions**.

Then create `.claude/loop/STATE.md` from the template at the bottom of this file.

## Phase 1 — Product definition

Delegate to `product-strategist`. Expect back: PRD, prioritised slice backlog with
acceptance criteria per slice, plan/pricing matrix, explicit out-of-scope list.

Review the backlog yourself before accepting it. Reject and re-delegate once if any slice
is not independently shippable or has no testable acceptance criteria.

Write the backlog into the ledger.

## Phase 2 — Architecture (parallel)

Spawn in parallel:
- `solution-architect` → stack, module boundaries, tenancy model, auth model, directory
  layout, ADRs in `docs/adr/`
- `data-architect` → full schema, tenancy isolation strategy, indexes, migration plan

Reconcile the two outputs yourself. If they conflict on tenancy or ownership boundaries,
resolve it explicitly and record the decision as an ADR before any code exists.

Then `devops-engineer` → repo scaffold, package manager, lint/format/typecheck config,
test runners, Docker, CI workflow that runs the full gate, `.env.example`, pre-commit hooks.

**Gate 2:** a clean checkout can install, typecheck, lint, run an empty test suite, boot,
and pass CI. Do not proceed until that is true.

## Phase 3 — Foundation slice

Slice 0 is always: tenancy + auth + a signed-in shell that renders one real record from the
database. Build it through the full Phase 4 loop below. Nothing else starts until slice 0
is green, because every later slice inherits its authorization assumptions.

## Phase 4 — The loop

Repeat for each slice in the backlog until it is empty:

### 4.1 Plan
Restate the slice's acceptance criteria. Identify the files likely to change. Write the
slice entry into the ledger as `IN PROGRESS`. Use `TodoWrite` to track the sub-steps.

### 4.2 Build (parallel where independent)
- `data-architect` first if the slice needs schema changes — migrations must land before
  code that depends on them.
- Then in parallel: `backend-engineer` and `frontend-engineer`.
- `billing-engineer` instead of/alongside backend when the slice touches plans, entitlements,
  metering, or processor webhooks.

Give each subagent: the acceptance criteria, the relevant ADRs, the file boundaries it owns,
and an explicit instruction not to edit files outside them. Two subagents must never own the
same file in the same round.

### 4.3 Verify (parallel fan-out)
Once the build round returns, spawn **in parallel**:
- `test-engineer` → unit + integration tests for the new paths, including failure and
  authorization-denied cases
- `e2e-engineer` → E2E specs for the slice's user journeys, per role and per plan
- `code-quality-auditor` → readability, duplication, dead code, complexity, naming,
  error handling, type escape hatches
- `security-auditor` → authz on every new endpoint, cross-tenant isolation, injection,
  secrets, dependency advisories
- `performance-auditor` → N+1s, missing indexes, payload size, bundle delta, caching
- `accessibility-auditor` → WCAG 2.2 AA on new UI

Each auditor is read-only and returns findings by severity: `BLOCKER`, `MAJOR`, `MINOR`.

### 4.4 Triage
You merge the reports. Deduplicate. Resolve contradictions (e.g. performance wants a cache,
security wants no cache for tenant data — security wins; record why). Then:
- Every `BLOCKER` and `MAJOR` must be fixed in this slice.
- `MINOR` findings go to the ledger backlog unless the fix is under ~5 lines.

### 4.5 Fix
Route each finding back to the subagent that owns the file. Re-run the affected auditors
only — not the whole fan-out. Increment the round counter. Hard stop at round 3.

### 4.6 Gate + commit
Run the **Quality Gate**. On green: commit, update the ledger to `DONE` with the commit SHA,
append anything learned to `docs/adr/` if a real decision was made, and start the next slice.
On red after 3 rounds: revert the slice to the last green commit, mark it `BLOCKED` with the
reason, and report to the user.

### 4.7 Every 3rd slice
Spawn `code-quality-auditor` and `security-auditor` across the **whole** codebase, not just
the diff, to catch drift and accumulated duplication. Treat the output as its own remediation
slice if it produces any `BLOCKER`.

## Phase 5 — Ship

- `devops-engineer` → deploy config for the chosen target, production migration path,
  health checks, logging, error tracking, backups, rollback procedure
- `docs-writer` → README with real setup steps, API reference, ADR index, ops runbook,
  CHANGELOG
- `release-manager` → independently re-verifies the full gate from a clean checkout,
  checks env parity and the deploy dry-run, then cuts the release

`release-manager` has veto power. If it reports red, the release does not happen — you return
to Phase 4 with its findings as the next slice.

## Quality Gate

Discover the actual commands from the repo's manifest and CI workflow; do not assume names.
All must pass, on a clean install:

| # | Check | Fail = |
|---|-------|--------|
| 1 | Install from lockfile | red |
| 2 | Typecheck, zero errors | red |
| 3 | Lint + format, zero errors and zero warnings | red |
| 4 | Unit + integration tests, zero failures, zero skips | red |
| 5 | Migrations apply cleanly to an empty DB, and roll back | red |
| 6 | Production build succeeds | red |
| 7 | App boots and health check responds | red |
| 8 | E2E suite passes against the built app | red |
| 9 | No `BLOCKER` or `MAJOR` audit findings open | red |
| 10 | No secrets, no `.only`, no `.skip`, no debug logging in the diff | red |

Report the gate as a table with the real command and its exit status per row. Never report a
row green without having run it in this session.

## Stop conditions

Stop the loop and hand back to the user when: the backlog is empty and Phase 5 is green; a
slice is `BLOCKED` after 3 rounds; a decision needs the user (Rule 9); or the same gate row
has failed in 3 consecutive slices, which means the foundation is wrong, not the slice.

## Ledger template — `.claude/loop/STATE.md`

```markdown
# Loop state

Product: <name>
Started: <date>
Stack: <decided stack>
Tenancy: <model>   Auth: <model>   Billing: <processor or none>
Deploy: <target>

## Assumptions
- <assumption> (unconfirmed)

## Gate commands
install: <cmd> | typecheck: <cmd> | lint: <cmd> | test: <cmd> | e2e: <cmd> | build: <cmd>

## Slices
| # | Slice | Status | Rounds | Commit | Notes |
|---|-------|--------|--------|--------|-------|
| 0 | Tenancy + auth + shell | DONE | 1 | abc1234 | |
| 1 | <slice> | IN PROGRESS | 0 | | |

## Deferred MINOR findings
- [ ] <finding> (slice N, source: <auditor>)

## Blockers
- <none>

## Decisions
- <date> <decision> → docs/adr/NNNN-<slug>.md
```

Begin at the phase the input and the ledger imply. State which phase you are entering and why,
in one line, then start.
