# CI Actions minutes optimization — report

Branch: `ci/reduce-actions-minutes`, branched fresh from `main` (9da0b6b0).

## 1. What the current workflows already do

This repo's `.github/workflows/ci.yml` and `deploy.yml` are considerably more optimized
than a typical monorepo already. Before changing anything I read both files end to end and
confirmed the following were **already in place** (i.e. not opportunities — verified, not
assumed):

| Lever | Status before this change |
|---|---|
| Dependency caching | `astral-sh/setup-uv@v5` with `enable-cache: true` in every job that needs Python deps; `actions/setup-node@v4` with `cache: pnpm` in every job that needs the frontend; a dedicated `actions/cache@v4` for `.mypy_cache` keyed on `uv.lock` + `github.sha` with `restore-keys` fallback (documented as cutting the type-check step from 219s to 14s). |
| Concurrency cancellation | `concurrency: { group: ${{ github.workflow }}-${{ github.ref }}, cancel-in-progress: true }` at the workflow root of `ci.yml`. A superseded push cancels the in-flight run. |
| Oversized runners / matrix | Single `ubuntu-latest`, no OS or Python-version matrix. The only matrix dimension is a 3-way **test shard**, which is deliberate, measured parallelism (comment cites a 2-vCPU-runner measurement: splitting across jobs instead of processes), not waste. |
| Scheduled/cron workflows | None exist. Nothing to trim. |
| Trigger scoping | `pull_request` (any base branch) + `push: branches: [main]` only — a branch with an open PR does not also retrigger on every push via a second trigger; `deploy.yml` only fires after CI succeeds on `main`, on `deploy/**` pushes, or manually. No redundant triggers found. |
| Redundant jobs/duplicate steps | None found — each job does exactly one checkout (unavoidable, separate runners), no check is duplicated across the two workflow files. |
| Coarse path filtering | Already present: a `changes` job asks the GitHub API for the PR's file list (no checkout) and sets `backend`/`frontend` booleans that gate the `static`/`migrations`/`tests`/`contracts` jobs vs the `frontend` job. Deliberately fail-open (anything not under `frontend/` counts as backend). |

So most of the "almost certainly present" waste this task's brief predicted was **not**
present. The one real gap was **granularity**: the existing filter is binary
(backend/frontend), not per-service. A PR touching only `services/auth/` still ran
lint/typecheck for all 34 services (cheap, see below) **and** the full migration
round-trip and full test suite for all 34 services across all 3 shards (not cheap — this
is where the actual container-minutes are spent).

## 2. What I changed, and why

All changes are additive and default-safe: every new mechanism is a no-op (run
everything, exactly as before) unless CI explicitly turns it on. A target run locally or
by hand behaves identically to before this change.

### 2a. `tools/affected-areas.sh` (new)

Pulled the PR-file-list classification logic out of the `changes` job's inline YAML
script into a standalone script, for the same reason `infrastructure/edge/ci-plan.sh`'s
path-diff logic is a script and not inline YAML in `deploy.yml`: it can then be pinned by
a real test instead of trusted on read.

Given changed file paths on stdin, it outputs `backend=`, `frontend=`, `services_all=`,
`services_list=`. The classification rule, in order:

- `frontend/*` → frontend only.
- `.github/workflows/*` → backend + frontend + `services_all=true` (unchanged from the
  original rule, just now also forces the new "run every service" flag).
- `services/<name>/<anything>` → backend, and `<name>` is added to the candidate list.
- **Everything else** (`libs/platform-core/*`, `libs/contracts/*`, root config —
  `Makefile`, `pyproject.toml`, `uv.lock`, `.importlinter` — `tools/*`, `docs/*`, an
  unrecognised shape) → backend + `services_all=true`. This is the conservative,
  fail-open default the task asked for: **the moment any file outside a single
  `services/<name>/` directory changes, the per-service narrowing is abandoned and every
  service runs**, exactly as today.
- A defensive final check: if `backend=true`, `services_all` somehow stayed `false`, and
  the candidate list is empty, force `services_all=true`. This should be unreachable
  given the rules above, but a filter that silently runs zero services from a logic bug
  is a strictly worse failure mode than one that runs all of them.
- No input at all → `services_all=true` (run everything), not "nothing changed".

**Cross-service exception, found by reading the test suite, not assumed:** two files —
`services/approval/tests/test_attendance_round_trip.py` and
`.../test_cross_service_contract.py` — deliberately reach into `attendance`'s and
`employee`'s live schemas via subprocess to prove the cross-service contract holds (this
is documented in the Makefile's `test-services` comment, and I independently verified it
by reading both test files and `grep`ing the whole `services/` tree for any other
schema-crossing pattern — these two are the only ones). The dependency is
**one-directional**: approval's suite exercises attendance/employee, not the reverse. So
`tools/affected-areas.sh` explicitly adds `approval` to the candidate list whenever
`attendance` or `employee` appears, but an approval-only change does not pull in the
other two. Without this, a PR touching only `services/attendance/` would have silently
stopped running the one test that actually catches a broken cross-service contract — this
is exactly the kind of gap the task asked me to trace by hand, and I did.

Because `pull_request`-triggered workflows run with the PR's own `.github/workflows/*`
content, a PR modifying `ci.yml`'s old inline script could already, in principle, rewrite
its own gating logic — that pre-existing property of GitHub Actions is unchanged by moving
the same logic into a tracked file; and a PR that edits `tools/affected-areas.sh` itself
is caught by the "everything else" fail-open rule above (`tools/*` isn't `services/*/*`),
so it cannot use a self-modified copy of this script to narrow its own run — verified by
test (`test_a_change_to_this_script_itself_does_not_narrow_its_own_run`).

### 2b. `tools/filter-affected.sh` (new)

A tiny stdin→stdout filter: reads service names (as `tools/shard.sh` prints them) and,
only when `AFFECTED_ALL=false` and `AFFECTED_SERVICES` is non-empty, passes through only
the names present in `AFFECTED_SERVICES`. Any other state (unset, `AFFECTED_ALL=true`,
or an empty `AFFECTED_SERVICES`) is a plain passthrough — this is what makes every
existing local workflow (`make test-shard` by hand, etc.) behave exactly as before.

No associative array (bash 3.2 compatibility, matching `tools/shard.sh`'s own documented
reason for avoiding one on macOS); substring-safe matching (`auth` cannot match `auth2` or
`app-gateway`), verified by test.

### 2c. `tools/shard-filtered.sh` (new)

Chains `tools/shard.sh` → `tools/filter-affected.sh` → `tools/fan-out.sh --stdin` for the
three Makefile targets that need it. The one subtlety this exists to handle correctly:
`fan-out.sh`'s own "`--stdin` given but nothing was piped in" guard exists to catch
`shard.sh` itself producing nothing (always a bug — there's history here, per
`fan-out.sh`'s comment about a TTY-detection bug that once let this fail silently). Once a
filter sits upstream of that guard, an empty result can also be **legitimate** — "nothing
in `AFFECTED_SERVICES` landed in this shard" — and that must stay a success, not trip the
same guard. `shard-filtered.sh` tells the two apart explicitly: `shard.sh` producing
nothing is still a hard failure (exit 2, before the filter or fan-out ever run);
filtering a non-empty list down to nothing is a logged, intentional no-op (exit 0).

### 2d. `Makefile`

`test-shard`, `migration-check-shard`, `migration-shard` now call
`tools/shard-filtered.sh` instead of piping `shard.sh` straight into `fan-out.sh`. The
partition itself (`tools/shard.sh`, still scanning *every* service directory) is
untouched — per that script's own comment, partitioning a pre-filtered list is a trap this
repo has already fallen into once (shards disagreeing between migrations and tests). Only
what a shard hands to its fan-out, *after* the partition, is narrowed.

`typecheck-services`, `boundaries`, lint, hygiene, `openapi-check`, `shellcheck`,
`hadolint`, and `contracts-check` are **deliberately left unfiltered**. Reasoning:
`typecheck-services` is already fast (the mypy cache note above — 14s measured, cached);
`boundaries` (`import-linter`, serial, uncached) has no measured cost in this repo to
justify the added complexity against; the rest are whole-repo, not per-service, checks.
Filtering the two DB-container-backed, 3-shard jobs (`migrations`, `tests`) is where the
actual minutes are, for the reasons below.

### 2e. `.github/workflows/ci.yml`

- `changes` job: now does a tiny `sparse-checkout` (just `tools/affected-areas.sh`,
  non-cone mode) before running the classifier, since the script now has to exist on the
  runner. This is the one thing that costs the job's original "no checkout" property. I
  judged this an acceptable trade for testability: GitHub Actions bills jobs in whole-minute
  increments, and this job's duration (API call + sparse fetch of one file) is in the
  single-digit seconds either way, so it is extremely unlikely to change the job's billed
  minute at all — see the "could not verify" section below for the one caveat on this.
  New job outputs: `services_all`, `services_list`.
- `migrations` job: added `AFFECTED_ALL: ${{ needs.changes.outputs.services_all }}` and
  `AFFECTED_SERVICES: ${{ needs.changes.outputs.services_list }}` to the job-level `env`.
  Flows through unchanged into `make migration-shard` / `make migration-check-shard`.
- `tests` job: same two env vars added. The "Apply migrations" step (`make migrate`,
  unfiltered, every schema) is explicitly **not** touched — its existing comment already
  explains why (cross-schema negative-authz tests need every schema's tables to exist, not
  just the shard's own). Only the "Service tests" step (`make test-shard`) is filtered.
- Comments added at each touch point explaining the filter and pointing back to the
  scripts, matching this file's existing density of "why," not just "what."

### 2f. Tests (new)

`tests/infra/test_affected_areas.py` (13 cases) and `tests/infra/test_filter_affected.py`
(7 cases) — subprocess tests against the real scripts, in the same style as the existing
`tests/infra/test_ci_plan.py` ("run the real script... never a reimplementation of its
rules"). These are now part of `make test-infra`, which both the `static` CI job and
`make check` already run — no new CI step, no new job.

## 3. Before/after — representative scenarios

The only hard numbers available are `tools/shard-weights.txt`'s measured per-service test
durations (last refreshed 2026-09-19). Everything else below (per-job overhead: checkout,
`uv sync`, container health-wait, schema bootstrap, migrate-all) is an **estimate**, not a
measurement — I had no way to run the real workflow in this sandbox. I've kept the
estimates conservative and said so.

Using the actual shard assignment (reproduced by running `tools/shard.sh`):

- Shard 1: finance, approval, leave, marketing, payment, metering, config, lead,
  admin-gateway — **155s** of measured/assumed test time.
- Shard 2: account, support, employee, notification, project, itsm, catalog, customer,
  standup, app-gateway, document, quotation — **155s**.
- Shard 3: auth, subscription, timesheet, attendance, deal, operations, billing,
  storefront, projects, product, console, eventbridge, scheduler — **155s**.

(The balancer's job is to make these equal, and it does — 464s total / 3 ≈ 155s each.)

### Scenario A — single-service PR (`services/auth/` only)

- `changes` job: `services_all=false`, `services_list=auth`.
- **Before:** all 3 `tests` shards run their full ~155s suite; all 3 `migrations` shards
  round-trip every service in their shard.
- **After:** `auth` is in shard 3. Shards 1 and 2's "Service tests" step and "Migrations
  round-trip"/"Models match migrations" steps become a one-line no-op
  (`shard-filtered: ... nothing affected landed here`) — only per-job overhead remains.
  Shard 3 runs **only auth's ~44s** suite instead of the shard's full 155s, and
  round-trips only auth's migrations instead of 13 services' worth.
- Everything else (`static`, `supply-chain`, `contracts`) is unchanged — these were never
  filtered, by design (see 2d).
- Net: the `tests` and `migrations` jobs' actual fan-out work drops from 3×155s ≈ 465s to
  ~44s (shard 3) + near-zero (shards 1–2) ≈ a ~85–90% cut in that work, on the two jobs
  that hold the real container-minutes. Whole-PR estimate: roughly 20–28 CI-minutes before
  → roughly 10–14 after, order-of-magnitude (static/supply-chain/contracts are fixed cost
  either way and dominate what's left).

### Scenario B — shared-lib change (`libs/platform-core/...`)

- `changes` job: catch-all rule fires → `services_all=true`.
- **Before and after are identical.** Every shard runs everything, exactly as today. This
  is intentional, not a missed opportunity — CODEOWNERS' own comment on
  `/libs/platform-core/` says it plainly: "Every service depends on it, so a change here
  is a change to all 33" (34, now). Correctness wins over savings here, as instructed.

### Scenario C — full-repo / root-config change (e.g. `Makefile`, `uv.lock`, or a push to
`main`)

- Non-`pull_request` events already short-circuited to "run everything" before my change
  and still do. A `Makefile`/`uv.lock`/`.importlinter` change on a PR also still runs
  everything (catch-all rule). No change in behavior or cost for this scenario by design.

### Scenario D — the cross-service exception (`services/attendance/...` only)

- `changes` job: `services_list=approval attendance` (approval added automatically).
- Both `attendance` and `approval` run in their respective tests/migrations shards;
  everything else is skipped the same way as Scenario A. This is the scenario that proves
  the filter isn't just "narrow to the literal changed directory" — it also preserves the
  one documented cross-service test dependency in the repo.

## 4. What I verified, and how

- **YAML validity:** `python3 -c "yaml.safe_load(...)"` on both workflow files, plus
  `yamllint` (installed via pip in this sandbox) — clean on both.
- **GitHub Actions schema/expression correctness:** downloaded `actionlint` v1.7.7
  (darwin binary release) and ran it against both workflow files — **0 findings** (it also
  runs `shellcheck` over every embedded `run:` block; I fixed the two style/info findings
  it raised against my own new script before finishing, confirmed by re-running it to a
  clean exit).
- **Makefile wiring:** `make -n test-shard`, `make -n migration-shard`, `make -n
  migration-check-shard` (dry-run) to confirm the generated command lines are exactly
  `tools/shard-filtered.sh <shard> <shards> <jobs> <require> <ok-empty> <command...>` as
  designed.
- **The filter scripts' actual behavior:** exercised `tools/filter-affected.sh` and
  `tools/shard-filtered.sh` directly (bypassing Make) with real shard output and a
  harmless `echo` stand-in for the real command — confirmed passthrough-by-default,
  correct narrowing, correct "nothing landed here" no-op (exit 0, no output), and that an
  empty `shard.sh` output (simulated) is still treated as a hard failure.
- **The classification logic:** ran `tools/affected-areas.sh` directly against ~10 hand-built
  scenarios (single service, two services, shared lib, frontend-only, mixed, workflow
  file, root config, empty input, the two cross-service cases, a change to the script
  itself) and confirmed every output matched the intended design.
- **Automated regression suite:** added 20 subprocess-based pytest cases
  (`tests/infra/test_affected_areas.py`, `tests/infra/test_filter_affected.py`) covering
  all of the above scenarios plus edge cases (duplicate paths, substring-safety of service
  names, newline- vs space-separated lists). Ran the **entire** `tests/infra` suite (463
  tests, not just mine) — all pass, zero regressions.
- **Shell quality:** `make shellcheck` (shellcheck 0.11.0, the version this repo pins) over
  all 157 shell scripts including the 3 new ones — clean.
- **`make shard-verify SHARDS=3`** — still reports "34 services partition cleanly across 3
  shards" (the partition itself is untouched, as designed).
- **No required-check naming changed:** job ids (`changes`, `static`, `migrations`,
  `tests`, `supply-chain`, `contracts`, `frontend`) and job/matrix display names are
  byte-for-byte unchanged. Searched the repo for anything that names a GitHub status check
  explicitly (`docs/`, `.github/CODEOWNERS`, `pull_request_template.md`) — found no
  branch-protection config file in the repo and no document naming a specific check string,
  so I cannot see a literal required-check name to diff against, but since no job was
  added, removed, or renamed, and the matrix dimensions are unchanged, the set of possible
  check names a branch-protection rule could reference is identical before and after.

### What I could not verify (no live Actions run in this sandbox)

- **Real timings.** All "before/after minutes" numbers in §3 are estimates built from one
  real data point (`shard-weights.txt`'s per-service pytest durations) plus my own
  judgment about checkout/`uv sync`/container-boot overhead. I have not seen a real CI run
  of either the old or new workflow.
- **The `changes` job's new sparse-checkout actually behaving as intended on GitHub's
  runners.** `actions/checkout@v4`'s non-cone `sparse-checkout` for a single file is a
  documented feature, and I have no reason to think it won't work, but I could not
  exercise `actions/checkout` itself (it needs a real GitHub Actions `git` remote/context)
  — only the shell logic downstream of it. If it ever misbehaves, the safe failure mode is
  the job failing outright (missing `tools/affected-areas.sh`), not a silent bad
  classification — it is not a change that can fail open into "test less than it should."
- **`gh api --paginate .../files` behavior on a renamed file.** The API reports a rename's
  *new* path only in `.filename`; a file renamed from one service into another would be
  attributed only to its new service. This is a pre-existing blind spot in the original
  backend/frontend filter (same `files`/`--jq` call), not something I introduced — flagging
  it for completeness, not as a regression.
- **Whether `services_all=true` is reached by every path the task meant by "err toward
  running more."** I reasoned through libs/platform-core, libs/contracts, root config
  (`Makefile`, `pyproject.toml`, `uv.lock`, `.importlinter`), `tools/`, `docs/`, and the
  workflow files themselves, and all fall into the catch-all. I did not exhaustively
  enumerate every file in the repo against the classifier; the catch-all's fail-open
  design means an unrecognized path is just as safe as a known one, but I'm noting I
  worked from the shape of the rule rather than a line-by-line file inventory.

## 5. Files changed

- `.github/workflows/ci.yml` — `changes` job extended, `migrations`/`tests` jobs get two
  new env vars.
- `Makefile` — `test-shard`, `migration-check-shard`, `migration-shard` call the new
  wrapper script.
- `tools/affected-areas.sh` (new) — PR path classification, extracted from the workflow.
- `tools/filter-affected.sh` (new) — the stdin service-list filter.
- `tools/shard-filtered.sh` (new) — chains shard → filter → fan-out with the
  two-kinds-of-empty distinction.
- `tests/infra/test_affected_areas.py` (new, 13 tests).
- `tests/infra/test_filter_affected.py` (new, 7 tests).

## 6. Concerns for the controller

- None that block merging. The one judgment call worth a second look: adding a
  sparse-checkout to the `changes` job (§2e) trades its "zero checkout" property for
  testability of the classification logic. I believe this is a good trade (negligible cost,
  real correctness value, matches the repo's own `ci-plan.sh` precedent) but it is a
  trade, not a pure win, and I want it reviewed as one rather than folded in silently.
- Two **pre-existing, unrelated** failures on `main` at the commit this branch is based on
  (9da0b6b0), found incidentally while verifying my own changes didn't break anything —
  **not touched, out of scope for this task**:
  - `make lint` fails: `tests/infra/test_route_table_parity.py:141` is 104 characters
    (ruff `E501`, limit 100).
  - `make openapi-check` fails: `libs/contracts/permissions/operations.json` doesn't match
    the code's current permission manifest for `services/operations`.
  I did not modify either file and confirmed via `git diff --stat HEAD` and `git log` that
  both predate this branch. Flagging rather than fixing, since fixing either is outside a
  CI-minutes task and I have no domain context on whether `operations`' permission drift
  is intentional mid-flight work.
