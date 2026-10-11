# ADR-0040 — Faster CI: service tests sharded by item, a tuned throwaway database, static checks in parallel

**Status:** Accepted (owner request, 2026-10-06: CI "was taking too much time … also getting failed too"). Built on
`ci/faster-pipeline`; the first sharded run is measured on real runners (below), the second round of changes could not
be, because Actions refused to start jobs (see Consequences) · **Date:** 2026-10-06 ·
**Changes:** `.github/workflows/ci.yml`, `Makefile`, `tools/ci_shards.py`, `tools/test-durations.json`,
`tools/test-shard.sh`, `tools/pytest-item.sh`, `tools/ci-postgres-tune.sh`, `tools/fan-out.sh`,
`tools/filter-affected.sh`, `tools/affected-areas.sh`, `infrastructure/edge/ci-plan.sh`, `tests/infra/test_ci_shards.py`
and friends; no service, migration, route or event

Builds on [ADR-0000](0000-phase-0-decisions.md) §10 (no foreign key crosses a schema, so a service's migrations and
tests touch only its own schema — the reason per-service work parallelises at all) and on the sharding that
`ci/reduce-actions-minutes` introduced (a `changes` job that narrows work to the services a pull request touches). It
does not change what any check asserts: every row that ran before runs now.

## Context

Measured on the last seven runs (`gh run view`, per-step timestamps; table in [testing.md](../testing.md)): 22–37
minutes wall clock, and not one green run in the last hundred. The before and after rows of that table are
[run 37452127273](https://github.com/developer-sanyam/opsgrids/actions/runs/37452127273) (a `main` push) and
[run 37466031956](https://github.com/developer-sanyam/opsgrids/actions/runs/37466031956) (the first sharded run); logs
and artifacts expire, so the tables are the durable record. The wall clock was one job:

- `Backend — tests 3/3` took **32 minutes**, of which `operations` alone was **19** (1,995 tests, 1,138 s). The other
  two shards took 7 and 12 minutes. Services were dealt to shards by `tools/shard-weights.txt`, one number per service
  measured on 2026-09-19, when `operations` weighed 11 and `finance` 112. No assignment of *whole services* can finish
  before its slowest service.
- A red lint row stopped the rest of its job, so the `static` job reported 13 s and the next 12 minutes of checks
  (415 s of cold mypy, 221 s of repository-tooling tests) were invisible; the same for the supply-chain job, whose secret
  scan (209 s) had not run for as long as the advisory scan above it was red. A change with three problems took three
  30-minute round trips to find them one at a time.
- Service-local suites spend most of their time waiting on the disk: each fixture `TRUNCATE`s ~50 tables before every
  test.
- The frontend job ran five checks in a row (19 minutes, then the build, which has not run since the tests went red).

## Decision

1. **The unit of test work is an item: a service, or part of one.** `tools/ci_shards.py` cuts any service that
   outweighs half a shard's fair share into parts by recorded per-file duration (`tools/test-durations.json`), deals the
   items out longest-first, and prints each shard's share. Files, not tests: a module's tests stay together and in order.
   `operations` becomes five parts of ~160 s. `make test-shard-verify` (static job) proves **every test file is in
   exactly one item**; the failure it prevents is the same silent one `shard-verify.sh` prevents for services.
2. **Parts of one service never share a shard**, hence never a database — each shard is a runner with its own
   Postgres, and the service fixtures `TRUNCATE` their schema before every test. The plan forbids it by construction and
   `verify` re-checks it. Splits inside one database (template databases, `pytest-xdist` workers) were rejected: the
   fixtures cannot tell workers apart, `init-db.sql`'s grants name the database `opsgrids`, and the previous trial of two
   processes on a 2-vCPU runner measured *slower*, not faster.
3. **The `changes` job builds the matrix.** It computes which shards have anything affected (`ci_shards.py needed`) and
   the plan's fingerprint (`digest`); every shard recomputes the fingerprint from its own full checkout and fails on a
   mismatch, so a sparse checkout that missed a directory cannot make the matrix and the shards disagree. Shard 1 is
   always included: it also runs the library tests and the boot smoke. A change confined to `services/auth` starts three
   shards, not six.
4. **The throwaway database runs without fsync** (`tools/ci-postgres-tune.sh`: `fsync`, `synchronous_commit`,
   `full_page_writes` off by `ALTER SYSTEM` and a reload; refuses unless `CI=true`). Planner settings and autovacuum are
   untouched — tests assert on query plans. Measured locally on the same 135 `operations` tests: stock 123 s; the three
   settings 53 s; a tmpfs data directory 51 s; trust authentication 48 s (noise). **On CI the gain was smaller**:
   `operations` 1,138 s → 838 s, `finance` 125 → 84, `account` 126 → 82, `subscription` 169 → 103 — 25–40%, not
   the local 2.4×, because a GitHub runner's disk waits less than a laptop's Docker VM. It is kept; it is not the main
   win and the ADR does not claim it is.
5. **The static job is three jobs** — lint/boundaries/schemas, types, repository-tooling tests — each with every row
   running regardless of the one before (`if: ${{ !cancelled() }}`), and the mypy cache is *saved* even when the job is
   red (restore and save are separate steps; a cache step's own save only runs on success, so it had never been written).
   Only `main` saves it: an entry is ~110 MB under a per-commit key, and saving from every pull-request push would fill
   the 10 GB cache quota within about a week and evict the entry the pull requests restore from.
   The supply-chain rows and the library tests and boot smoke report independently the same way.
6. **One migrations job**, two services at a time, instead of three: with a tuned database the round-trip and the drift
   check take about two minutes, and three jobs paid three container starts and three rounded-up minutes for it.
7. **The frontend is a static job and four legs** that test and build a slice of the workspace at once
   (`FRONTEND_FILTER`, a pnpm filter; empty = every package, which is what the gate and every local run do). An infra
   test fails if a workspace package is in no leg or in two.
8. **A change that is only `docs/` skips the database-backed jobs** (migrations, service tests, contracts). Nothing
   wider is trusted: Markdown elsewhere and `infrastructure/` stay code, because service tests read
   `infrastructure/edge/RUNBOOK.md`, `routes.prod.json` and the preflight libraries by path. A renamed file counts under
   both its old and new path (the files API gives the old one only as `previous_filename`), so moving the RUNBOOK into
   `docs/` is still code.
9. **Concurrency is unchanged: a superseded run is cancelled, on `main` as well as on a pull request** (group = the
   ref, `cancel-in-progress: true`). An earlier draft of this change stopped cancelling `main` runs, on the reasoning that
   deploy ships what a *finished green* run proved. Review measured what that costs and found it buys nothing today: in
   the eight hours to 2026-10-06 11:27 UTC, 7 of 12 `main` runs were cancelled after billing 34–79 minutes each (414 in
   all, on the old pipeline), and a full run of this one bills about 95 ([testing.md](../testing.md)), so each
   superseded commit would have billed roughly 40 more — and could leave the newest run queued behind the others for
   runners. None of the last 40 `main` runs was green (33 failures, 7 cancelled), so no intermediate green commit has
   ever been lost to a cancellation; and `deploy.yml` plans from the commit the box last served, not from the previous
   commit, so a commit whose run was cancelled is part of the next deploy anyway. If never-cancel is wanted later it is
   its own owner decision with that minute cost stated. Every job has a `timeout-minutes`.
10. **`ci-plan.sh` does nothing when the box is already at or past the commit.** The run for commit A can still finish
    after commit B (built on A) has deployed — someone re-runs an old run from the Actions tab, or A's run completed a
    moment before B's push would have cancelled it. The box then reports B, which is not an ancestor of A, and the plan
    read that as "no usable base" and deployed *everything from A* — a rollback. Found by reasoning about the deploy
    path, pinned by a test; kept when the draft above was reverted, because the case does not need uncancelled runs.

## Consequences

- **Required-check names change.** `Backend — tests N/3` is now `Backend — tests N/6` and exists only for shards with
  work; `Backend — migrations N/3` is `Backend — migrations`; `Backend — lint, types, contracts` is three jobs;
  `Frontend — types, lint, boundaries, tests, build` is `Frontend — types, lint, boundaries` and
  `Frontend — tests and build N/4`. `main` has no branch protection (owner, 2026-09-23); if one is added it should
  require these names (or an aggregate job).
- **Durations drift.** `tools/test-durations.json` is a measurement. The plan stays *correct* when it goes stale (every
  file still runs exactly once); only the balance degrades, and an entry for a file that no longer exists is a `note:`
  from `verify`, not a failure. Refresh: download a run's `junit-shard-*` artifacts and run
  `python3 tools/ci_shards.py refresh --run <run url> <dir>`, which records the run in the file's `source`
  ([testing.md](../testing.md)). A test fails if one shard is handed more than 1.35× the mean — that one stays in the
  per-pull-request gate on purpose: it can only change with the durations or the test files, and a refresh that yields a
  lopsided plan should fail its own pull request.
- **More jobs run at once** (up to 20 for a full run), so a runner-concurrency limit can queue some; in the measured run
  one job waited 35 s.
- **Minutes.** A full run bills about as much as before once the rows that used to be skipped are counted (they were
  skipped, not absent), and a service-local pull request bills much less (three shards, not three-of-three). The wall
  clock is what moved.
- **Actions can be unavailable.** The second round of changes (the static split, the single migrations job, six shards
  from measured durations, the rebalanced frontend legs) was pushed and then refused by the platform — "The job was not
  started because recent account payments have failed or your spending limit needs to be increased" — so it is
  unmeasured. It reuses only commands that ran in the measured round, and the single migrations job was run end to end
  locally against a scratch database; it is still a prediction until a run proves it.
- **`fix/ci-failures` (PR #102) edits `ci.yml` too, so whichever merges second has one edit to make.** An earlier draft
  of this ADR said #102 did not touch `ci.yml`; it does. It widens the `rabbitmq` service's ports to the management port,
  adds a `mailhog` service right after it, and adds a step `Ops-monitor integration database` (`if: matrix.shard == 1`)
  between `Service tests` and `Library tests`. The two branches used to conflict on exactly one line, a faster `rabbitmq`
  health probe (4 s instead of 10 s) worth a few seconds per shard; that line is back to what `main` has, so a trial
  merge of the two tips (`git merge-tree`, 2026-10-06) is conflict-free and `actionlint` passes on the result. The merge
  is still not *right* as it stands: #102's step has a bare `if: matrix.shard == 1`, so a red `Service tests` skips it,
  and `Library tests` — which runs regardless — then fails for want of the `opsgrids_mon` database, a failure that is not
  its own. `test_a_shard_one_step_is_not_skipped_by_a_red_service_test_step` fails on the merged tree until that step's
  condition is `${{ !cancelled() && matrix.shard == 1 }}`. Either order merges; landing #102 first (the smaller change,
  and the one that fixes the red rows) and then rebasing this branch, making that edit and running `actionlint` on the
  result is the cheaper path, because no CI can check a hand-resolved workflow while Actions is unavailable.
- **Rollback is the whole change, not the workflow.** `tests/infra` reads `ci.yml`'s `TEST_SHARDS`, the `changes` job's
  outputs and the `tests` matrix, so reverting only `.github/workflows/ci.yml` leaves tests that expect the new workflow
  beside the old one and turns `Backend — tooling tests` red on top of whatever prompted the rollback. Revert the squash
  commit: workflow, Makefile, `tools/`, `ci-plan.sh` and the tests together.

## Not done, and why

- **An aggregate `ci-ok` job.** No single check means "CI passed": the test and migration jobs do not exist for a
  docs-only change, and the `tests` matrix has only the shards with work. Not added because nothing consumes it while
  `main` has no branch protection, and it bills a minute on every run. The day protection is added: a final job with
  `if: ${{ always() }}`, `needs` on every other job, failing if any result is `failure` or `cancelled` (skipped is fine),
  and that job as the only required check.
- **Failing the deploy plan when the box's last-good ref cannot be read.** `deploy.yml` leaves `BASE` empty on any ssh
  failure and `ci-plan.sh` then deploys everything, which is what a new box or a rewritten history needs. Making that a
  hard failure would stop a first deploy, and the rollback it guards against (an older run finishing after a newer
  deploy while the lookup fails) needed uncancelled `main` runs to be likely; with cancellation back it is the same rare
  case it was before this branch. A deploy-path decision for the owner, not this track's.
- **`pytest-xdist` or two processes per runner.** Needs per-worker databases the fixtures cannot select without
  editing every service's `conftest`; the earlier trial lost. Revisit with a CPU measurement (idle share while the tests
  run), not before.
- **Sharing a virtualenv or a migrated database between jobs.** `uv sync` is 2–5 s with the cache `setup-uv` already
  keeps; a `pg_dump` artifact would replace a ~30 s migrate with a ~15 s restore at the price of a roles/grants edge
  case on every service. Not worth the risk.
- **Fixing the tests that are slow for real.** The slowest 30 across every service (junit timings of a full local
  run) are migration round-trips (`operations`, `notification`, `catalog`; 4–17 s each), `EXPLAIN` checks over a large
  seeded table (`test_tracking_query_plans`, 37 s), and two tests that wait out a real five-second lock timeout on
  purpose. None is a stray sleep or a network call, and none has a trivial honest fix. The one that did was in the
  repository-tooling tests, which re-parsed the whole repository seventeen times (parse once: ~17 s locally of ~53 s).
  The rest of the cost is the per-test fixture, ~0.4 s × 2,000 tests in `operations`, which is a change to every
  service's `conftest` and so not this track's.
- **The known red rows** (OAUTH_AMR, route-table E501, the `exceljs` audit, `opsgrids_mon`/mailhog, the engage and
  purchase-order date fixtures). They are `fix/ci-failures`' to fix; the pipeline now reports all of them at once instead
  of one per round.
