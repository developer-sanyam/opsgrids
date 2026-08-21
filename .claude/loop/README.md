# /loop — end-to-end SaaS build loop for Claude Code

An orchestrator slash command plus 15 specialist subagents. `/loop` plans the product, builds
it in thin vertical slices, and refuses to advance a slice until a binary quality gate passes.

## Install

Already installed in this repo, under `.claude/`:

```
.claude/commands/loop.md      the orchestrator
.claude/agents/*.md           15 specialists
.claude/loop/STATE.md         the ledger (still the empty template — no run yet)
.claude/settings.json         subagent spawn depth and concurrency
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
directories that existed at startup. **That applies to this repo right now:** `.claude/` was
created after this session began, so `/loop` and the agents will not resolve until you restart.

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
Phase 2  architecture ..... solution-architect + data-architect (parallel), then devops-engineer
Phase 3  foundation ....... slice 0: tenancy + auth + signed-in shell
Phase 4  the loop ......... per slice:
             build ........ data-architect -> backend-engineer + frontend-engineer
                            (+ billing-engineer when money is involved)
             verify ....... test-engineer, e2e-engineer, code-quality-auditor,
                            security-auditor, performance-auditor,
                            accessibility-auditor  <- all in parallel
             triage ....... orchestrator merges findings, resolves conflicts
             fix .......... routed back to the owning agent, max 3 rounds
             gate ......... 10 checks, all green, then commit
Phase 5  ship ............. devops-engineer, docs-writer, release-manager (veto power)
```

Every third slice, the quality and security auditors run across the whole codebase rather
than the diff, to catch drift.

## The agents

| Agent | Role | Writes files |
|---|---|---|
| `product-strategist` | PRD, slice backlog, plan/pricing matrix | yes |
| `solution-architect` | stack, tenancy model, boundaries, ADRs | yes |
| `data-architect` | schema, migrations, indexes, isolation | yes |
| `backend-engineer` | endpoints, domain logic, authz, jobs | yes |
| `frontend-engineer` | routes, screens, forms, states | yes |
| `billing-engineer` | subscriptions, entitlements, metering, webhooks | yes |
| `test-engineer` | unit + integration, authz and isolation cases | yes |
| `e2e-engineer` | Playwright journeys per role and per plan | yes |
| `code-quality-auditor` | readability, duplication, complexity, dead code | no |
| `security-auditor` | authz coverage, tenant isolation, injection, secrets | no |
| `performance-auditor` | N+1s, indexes, payloads, bundle, caching | no |
| `accessibility-auditor` | WCAG 2.2 AA on new UI | no |
| `devops-engineer` | scaffold, CI, Docker, deploy, observability | yes |
| `docs-writer` | README, API docs, runbook, changelog | yes |
| `release-manager` | independent re-verification, veto, release | yes |

Auditors are read-only by tool restriction, not by instruction — they have no `Write` or
`Edit`. `code-quality-auditor`, `security-auditor`, `performance-auditor`, `data-architect`,
`solution-architect`, `product-strategist`, `billing-engineer`, and `devops-engineer` use
`memory: project`, so they accumulate codebase knowledge in `.claude/agent-memory/` across
sessions. Commit that directory to share it with the team.

`e2e-engineer` declares the Playwright MCP server inline, so those tools exist only inside
that subagent and never consume context in your main session.

## In this repo

Four things differ from the loop's defaults here. The repo's rules win where they conflict.

**Nothing is pushed.** Rule 9 asks before force-pushing; in atmora the standing instruction is
stronger — work stays local, and the repo has no remote configured. `release-manager` "cuts the
release" as far as a local tag and a green gate, no further.

**Commit messages never mention Claude, Claude Code, or any AI assistant** — no generated-by
footers, no co-author trailers. See the root `CLAUDE.md`. This overrides nothing else about
rule 7; Conventional Commits still apply where the repo already uses them.

**The gate commands are Python, not npm.** The loop says to discover them rather than assume,
and here they are:

| Check | Command |
|---|---|
| install | `make install` |
| typecheck | `make typecheck` (per service — `import app` is ambiguous across services) |
| lint + format | `make lint` |
| test | `make test` (needs the dev stack up: `make infra`) |
| migrations | `make migrate`, and `make migration-check` to fail on a model changed without one |

`make check` runs lint, typecheck and test together — that is what CI runs, and it is the
closest thing here to the loop's gate.

There is no E2E suite and no production build yet, so gate rows 6 and 8 have nothing to run.
Say that, rather than reporting them green.

**Phases 0–2 are already done for the platform.** `docs/adr/0000-phase-0-decisions.md` records
the eleven irreversible decisions, `0001-frontend-topology.md` the frontend split, and the
foundation, account-service, auth-service and both gateways are built and green. A `/loop` run
here should start at Phase 4 against a specific product, not re-derive the architecture.

## Tuning

- Change `model:` per agent to trade cost against depth. The auditors and architects are the
  ones worth spending on.
- Drop `billing-engineer` or `accessibility-auditor` from the verify fan-out in `loop.md` if a
  project does not need them.
- Raise `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS` if the parallel fan-out is being throttled.
- `/loop` sets `disable-model-invocation: true` — it only runs when you type it, never on
  Claude's initiative.
