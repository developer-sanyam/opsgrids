# /loop — end-to-end SaaS build loop for Claude Code

An orchestrator slash command plus 21 specialist subagents. `/loop` plans the product, builds
it in thin vertical slices, and refuses to advance a slice until a binary quality gate passes.

## Install

Already installed in this repo, under `.claude/`:

```
.claude/commands/loop.md            the orchestrator
.claude/agents/*.md                 21 specialists
.claude/loop/STATE.md               the ledger — carries this project's real state
.claude/loop/TOOLCHAIN-template.md  the reference tool set `tooling-engineer` picks from
.claude/settings.json               subagent spawn depth and concurrency
```

To install it somewhere else:

```bash
cp -r .claude/commands/loop.md   <your-repo>/.claude/commands/
cp -r .claude/agents/            <your-repo>/.claude/agents/
mkdir -p <your-repo>/.claude/loop
cp .claude/loop/STATE.md         <your-repo>/.claude/loop/STATE.md
```

Merge `.claude/settings.json` into your existing settings if you have one. Restart Claude Code
if `.claude/agents/` did not exist before the session started — the file watcher only covers
directories that existed at startup.

For use across every project instead of one repo, put the same files under `~/.claude/`.

## Usage

```
/loop a scheduling tool for physiotherapy clinics, teams of 2-10, Stripe billing
/loop docs/spec.md      # start from an existing PRD
/loop status            # print the ledger, change nothing
/loop resume            # continue from the ledger
/loop ship              # jump to the release phase
```

Run it in a git repo. It commits per slice, so `git log` is your undo.

## Shape of the loop

```
Phase 0  intake ........... one batch of questions, then the ledger
Phase 1  product .......... product-strategist -> PRD + slice backlog
Phase 2  architecture ..... solution-architect + data-architect (parallel),
                            then system-design-reviewer, devops-engineer, tooling-engineer
Phase 2.5 design .......... product-designer + design-system-architect (parallel)
                            tokens, type scale, component library, all states in the workshop
Phase 3  foundation ....... slice 0: tenancy + auth + signed-in shell
Phase 4  the loop ......... per slice:
             build ........ product-designer + data-architect
                            -> backend-engineer + frontend-engineer
                            (+ billing-engineer when money is involved)
             verify ....... test-engineer, e2e-engineer, code-quality-auditor,
                            security-auditor, performance-auditor,
                            accessibility-auditor, design-qa-auditor,
                            algorithm-reviewer  <- all in parallel
             triage ....... orchestrator merges findings, resolves conflicts
             fix .......... routed back to the owning agent, max 3 rounds
             gate ......... 18 checks, all green, then commit
Phase 5  ship ............. devops-engineer, docs-writer, release-manager (veto power)
```

Every third slice, the quality, security, and system-design reviewers run across the whole
codebase rather than the diff, to catch drift and architectural erosion.

The gate is 18 rows: the original 10 plus schema-drift, visual regression, automated
accessibility, performance budget, query-plan, SAST/secrets/dependency/license, dependency-
boundary, and design-token checks.

## The agents

| Agent | Role | Writes files |
|---|---|---|
| `product-strategist` | PRD, slice backlog, plan/pricing matrix | yes |
| `solution-architect` | stack, tenancy, C4 views, capacity + failure model, ADRs | yes |
| `data-architect` | access patterns, schema, index selection, verified query plans | yes |
| `product-designer` | IA, flows, screen specs, state inventory, interface copy | yes |
| `design-system-architect` | tokens, type scale, component library, workshop | yes |
| `tooling-engineer` | the professional toolchain, wired into the gate | yes |
| `backend-engineer` | endpoints, domain logic, authz, jobs | yes |
| `frontend-engineer` | routes, screens, forms, states | yes |
| `billing-engineer` | subscriptions, entitlements, metering, webhooks | yes |
| `test-engineer` | unit + integration, authz and isolation cases | yes |
| `e2e-engineer` | Playwright journeys per role and per plan | yes |
| `code-quality-auditor` | readability, duplication, complexity, dead code | no |
| `security-auditor` | authz coverage, tenant isolation, injection, secrets | no |
| `performance-auditor` | N+1s, indexes, payloads, bundle, caching | no |
| `accessibility-auditor` | WCAG 2.2 AA on new UI | no |
| `design-qa-auditor` | rendered UI vs spec, token compliance, polish, screenshots | no |
| `algorithm-reviewer` | complexity of hot paths, data-structure fit, correctness | no |
| `system-design-reviewer` | boundaries, coupling, failure modes, capacity, contracts | no |
| `devops-engineer` | scaffold, CI, Docker, deploy, observability | yes |
| `docs-writer` | README, API docs, runbook, changelog | yes |
| `release-manager` | independent re-verification, veto, release | yes |

`.claude/loop/TOOLCHAIN-template.md` is the reference tool set — contracts, database, frontend,
testing depth, security, quality automation, observability — with a JS/TS and a Python option
per row. `tooling-engineer` picks from it and writes the project's real `docs/TOOLCHAIN.md`.
It is a reference, not the project's toolchain: nothing in it is in force until that file exists.

Auditors are read-only by tool restriction, not by instruction — they have no `Write` or
`Edit`. `code-quality-auditor`, `security-auditor`, `performance-auditor`, `data-architect`,
`solution-architect`, `product-strategist`, `billing-engineer`, and `devops-engineer` use
`memory: project`, so they accumulate codebase knowledge in `.claude/agent-memory/` across
sessions. Commit that directory to share it with the team.

`e2e-engineer` and `design-qa-auditor` declare the Playwright MCP server inline, so those tools
exist only inside those subagents and never consume context in your main session.

## In this repo

The repo's rules win where they conflict with the harness.

**Commit messages never mention Claude, Claude Code, or any AI assistant** — no generated-by
footers, no co-author trailers. See the root `CLAUDE.md`.

**The gate commands are Python, not npm.** The loop says to discover them rather than assume:

| Check | Command |
|---|---|
| install | `make install` |
| typecheck | `make typecheck` (per service — `import app` is ambiguous across services) |
| lint + format | `make lint` |
| test | `make test` (needs the dev stack up: `make infra`) |
| migrations | `make migrate`, and `make migration-check` to fail on a model changed without one |

`make check` runs lint, typecheck and test together — that is what CI runs, and it is the
closest thing here to the loop's gate.

**Most of the 18 gate rows have nothing to run yet.** Rows 1–5, 7, 9 and 10 map onto the make
targets above. Rows **6, 8, 11–18** — production build, E2E, schema drift, visual regression,
automated accessibility, performance budget, query plans, SAST and license, dependency
boundaries, and token lint — have no tooling wired in this repo. Report them **N/A with the
reason**, never green: the orchestrator's own rule is that a row is never green without having
been run in that session. Wiring them is `tooling-engineer`'s job and has not happened.

**Phases 0–2 are already done for the platform.** `docs/adr/0000-phase-0-decisions.md` records
the eleven irreversible decisions, `0001-frontend-topology.md` the frontend split, and
`0002-row-level-security.md` is **proposed and awaiting review** — do not implement it without
that review. The foundation, account-service, auth-service and both gateways are built and
green. A `/loop` run here should start at Phase 4 against a specific product, not re-derive the
architecture.

**Phase 2.5 has no precedent here.** The only frontend so far is the marketing site; there are
no design tokens, no component workshop, and no product UI. `product-designer` and
`design-system-architect` would be starting from nothing rather than reconciling with an
existing system — which is the right time to run them, but it is a real slice, not a formality.

## Tuning

- Change `model:` per agent to trade cost against depth. The auditors and architects are the
  ones worth spending on.
- Drop `billing-engineer` or `accessibility-auditor` from the verify fan-out in `loop.md` if a
  project does not need them.
- Raise `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS` if the parallel fan-out is being throttled.
- `/loop` sets `disable-model-invocation: true` — it only runs when you type it, never on
  Claude's initiative.
