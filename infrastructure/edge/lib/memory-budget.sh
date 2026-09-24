#!/usr/bin/env bash
# Sourced by preflight.sh, and by tests/infra/test_memory_budget.py directly — the same shape
# as lib/rebuild-decision.sh and lib/health-check.sh: no side effects of its own (no `cd`, no
# `set -e`), so it is safe to source from either.
#
# memory_budget_verdict <mem_total_kb> <expected_mb> <soft_percent> <hard_percent>
#   Prints one line, "<verdict> <percent>", to stdout. `verdict` is `ok`, `warn` or `refuse`;
#   `percent` is the expected steady-state RSS as an integer percentage of MemTotal, rounded
#   down (a box exactly on the soft line reads as compliant, not borderline — rounding up would
#   flag boxes that are actually fine). `preflight.sh` reads the first word to decide whether to
#   fail the deploy; the percentage is for the human reading its output.
#
# `memory_budget_verdict` and `apply_accepted_ceiling` are pure arithmetic on a handful of
# numbers, nothing else: reading /proc/meminfo and summing memory-budget.json is
# `memory_budget_totals.py`'s job (a JSON lookup, not arithmetic — see that file's own header
# for why it is Python and this is bash) and `preflight.sh`'s to wire together. Keeping them to
# plain numbers is what makes them testable without either. `post_deploy_compute`, at the bottom,
# is the one function here that is not pure: it reads Docker (read-only) to decide what to price.
memory_budget_verdict() {
	local mem_total_kb="$1" expected_mb="$2" soft_percent="$3" hard_percent="$4"
	if [ -z "$mem_total_kb" ] || [ "$mem_total_kb" -le 0 ] 2>/dev/null; then
		# No usable MemTotal (missing /proc/meminfo, or a bad read): refuse rather than divide
		# by zero or silently wave a deploy through with no check at all.
		echo "refuse 0"
		return 0
	fi
	local mem_total_mb=$((mem_total_kb / 1024))
	local percent=$((expected_mb * 100 / mem_total_mb))
	if [ "$percent" -ge "$hard_percent" ]; then
		echo "refuse $percent"
	elif [ "$percent" -ge "$soft_percent" ]; then
		echo "warn $percent"
	else
		echo "ok $percent"
	fi
}

# apply_accepted_ceiling <verdict> <percent> <expected_mb> [<accepted_mb>]
#   B2/H3/H4, the review's fix round 2 (2026-09-24) — replaces the previous `grandfathered_
#   verdict`, which compared a deploy against the *last* one that happened to go healthy, a
#   moving target that let an operator never actually decide anything. Production runs every
#   product it sells (ten profiles), and the re-measured `kinds.background` price
#   (memory-budget.json's own header) puts that combination over the hard budget line on its own,
#   with no planned service added yet — refusing every future deploy over a fact that already
#   exists helps nobody. The fix is an explicit owner decision, not an implicit ratchet:
#   `EDGE_BUDGET_ACCEPTED_MB` in `.env` is the ceiling the owner has actually looked at the
#   projection and agreed to run at (or the box that had to be resized, or the products that had
#   to be de-listed, so the number went back down and this stopped mattering). This function is
#   the one place that decides whether an already-`refuse` verdict from `memory_budget_verdict`
#   should actually refuse.
#
#   Only ever touches a `refuse` verdict — `ok` and `warn` pass through completely unchanged, so
#   a box that is not overcommitted is never affected by any of this.
#
#   No `accepted_mb` (the owner has not set one): today's behaviour, unchanged — refuse. There is
#   no implicit acceptance; silence means the deploy stops.
#
#   `accepted_mb` present and `expected_mb` is at or under it: downgrade to a loud `warn` naming
#   the overcommit — the owner already agreed to this much, in writing, in `.env`. Over it: still
#   `refuse` — this deploy is asking for more than what was actually agreed to, which is exactly
#   the case a hard line exists to catch even with a ceiling configured.
#
#   Static, not a moving target: raising `EDGE_BUDGET_ACCEPTED_MB` is itself the auditable record
#   of the decision (a diff to `.env`), never something a deploy adjusts on its own the way the
#   old last-ok ratchet did. `remote.sh`'s own recorded `edge-budget.last-ok` file is informational
#   only now — printed for reference, never read by this function or by preflight's own verdict.
#
#   `cap_mb` (optional fifth argument — round 3, M5): an absolute ceiling on what any ceiling can
#   cover, `host.max_accepted_percent` of MemTotal. Over it the verdict stays `refuse` no matter
#   what `accepted_mb` says: a typo'd `38940` for `3894` would otherwise be a blank cheque, and
#   even a correctly-typed number stops meaning "a deliberate, survivable overcommit" somewhere
#   past a quarter more than the box has — the kernel is then killing things, not swapping them.
apply_accepted_ceiling() {
	local verdict="$1" percent="$2" expected_mb="$3" accepted_mb="${4:-}" cap_mb="${5:-}"
	if [ "$verdict" != "refuse" ] || [ -z "$accepted_mb" ]; then
		echo "$verdict $percent"
		return 0
	fi
	# A ceiling that is not a plain integer (a typo: "4200MB", "4,200") is not a ceiling the
	# owner meaningfully set — treated as unset, so the deploy refuses rather than `[ -le ]`
	# erroring out of a `set -e` script half way through the verdict.
	case "$accepted_mb" in
	*[!0-9]*)
		echo "$verdict $percent"
		return 0
		;;
	esac
	case "$cap_mb" in
	*[!0-9]*)
		echo "$verdict $percent"
		return 0
		;;
	esac
	if [ -n "$cap_mb" ] && [ "$expected_mb" -gt "$cap_mb" ]; then
		echo "refuse $percent"
	elif [ "$expected_mb" -le "$accepted_mb" ]; then
		echo "warn $percent"
	else
		echo "refuse $percent"
	fi
}

# describe_budget_usage <expected_mb> <mem_total_mb>
#   One human-readable clause for preflight's memory lines, round 3 (M5). A percentage floored to
#   an integer reads "100%" for a box that is 3894 MB on 3862 MB — exactly the case the number
#   exists to make plain. Under MemTotal: "3400M of 3862M MemTotal (88%)". At or over: the
#   overcommit in megabytes, and the percentage to one decimal: "4112M against 3862M MemTotal:
#   250M over MemTotal (106.5%)".
describe_budget_usage() {
	local expected_mb="$1" mem_total_mb="$2" tenths
	if [ "$mem_total_mb" -le 0 ] 2>/dev/null; then
		echo "${expected_mb}M against an unreadable MemTotal"
		return 0
	fi
	tenths=$((expected_mb * 1000 / mem_total_mb))
	if [ "$expected_mb" -gt "$mem_total_mb" ]; then
		echo "${expected_mb}M against ${mem_total_mb}M MemTotal: $((expected_mb - mem_total_mb))M over MemTotal ($((tenths / 10)).$((tenths % 10))%)"
	else
		echo "${expected_mb}M of ${mem_total_mb}M MemTotal ($((tenths / 10)).$((tenths % 10))%)"
	fi
}

# post_deploy_compute <budget_file>
#   B1 (review round 2): computes what this box will be running once the deploy in front of it
#   finishes — the union of (running containers that the new compose file still defines) and
#   (services configured for the declared profiles) — and sets three globals for the caller:
#   `POST_DEPLOY_MB` (the priced total), `POST_DEPLOY_UNPRICED` (space-separated names with no
#   memory-budget.json entry, priced at kinds.background and worth a warning) and
#   `POST_DEPLOY_ERROR` (why it returned non-zero). Globals, not stdout, because a caller needs
#   them all and `$(...)` would run this in a subshell that loses them.
#
#   **Fails closed (round 3, HIGH).** Any input it cannot read returns non-zero with
#   `POST_DEPLOY_MB` empty and `POST_DEPLOY_ERROR` naming the reason: `docker ps` failing, either
#   `docker compose config --services` failing, or either coming back empty (a valid compose file
#   always has core services, so an empty list is a failure, not "nothing to price"). The first
#   version of this function sent compose's stderr to /dev/null and ignored its exit status, so a
#   compose failure (a missing .env key, a syntax error, a missing binary) produced an empty
#   post-deploy set and a cheerful "ok memory budget: 0M (0%)" — a gate that passes exactly when
#   it cannot see. compose's own stderr is left visible for the same reason. A caller decides
#   whether non-zero is a failure (preflight) or just a reason to skip (remote.sh's
#   `record_budget`).
#
#   `docker ps` includes `restarting` and `paused` containers as well as `running` ones: a crash-
#   looping container is resident memory for as long as it spends between crashes, and a paused
#   one keeps every page it had — pricing only `running` would drop exactly the containers a
#   struggling box is most likely to be carrying. `exited`/`created` hold no RSS and stay out.
#
#   "Every profile" for the running-set intersection is compose's own `COMPOSE_PROFILES=*`, not a
#   list kept in memory-budget.json: a profile added to compose.prod.yml and forgotten in the JSON
#   must not silently fall out of the "still defined in the new file" set.
#
#   The one place this is computed: preflight.sh's gate and remote.sh's `record_budget` both call
#   it, so what a deploy is judged against and what gets recorded afterwards can never be priced
#   two different ways. Must run from infrastructure/edge (compose.prod.yml, .env and lib/ are
#   found relative to it), with COMPOSE_PROFILES exported as the box's declared profile list —
#   which is how `docker compose config --services` knows what "declared" means here.
#
#   Read-only against Docker: `docker ps` (filtered by compose.prod.yml's own project name, so it
#   never sees a stranger's containers) and `docker compose config --services`.
# shellcheck disable=SC2034 # POST_DEPLOY_MB/_UNPRICED/_ERROR are this function's outputs, read by its sourcers
post_deploy_compute() {
	local budget_file="$1" running_names running_csv declared_names declared_csv all_names all_csv
	POST_DEPLOY_MB=""
	POST_DEPLOY_UNPRICED=""
	POST_DEPLOY_ERROR=""
	running_names=$(docker ps --filter "label=com.docker.compose.project=opsgrids-edge" \
		--filter "status=running" --filter "status=restarting" --filter "status=paused" \
		--format '{{.Label "com.docker.compose.service"}}') || {
		POST_DEPLOY_ERROR="docker ps failed — cannot see what is running"
		return 1
	}
	running_csv=$(echo "$running_names" | tr '\n' ',' | sed 's/,$//')
	declared_names=$(docker compose -f compose.prod.yml --env-file .env config --services) || {
		POST_DEPLOY_ERROR="docker compose config --services failed for the declared profiles (compose's own error is above)"
		return 1
	}
	all_names=$(COMPOSE_PROFILES='*' docker compose -f compose.prod.yml --env-file .env config --services) || {
		POST_DEPLOY_ERROR="docker compose config --services failed with every profile enabled (compose's own error is above)"
		return 1
	}
	declared_csv=$(echo "$declared_names" | tr '\n' ',' | sed 's/,$//')
	all_csv=$(echo "$all_names" | tr '\n' ',' | sed 's/,$//')
	if [ -z "$declared_csv" ] || [ -z "$all_csv" ]; then
		POST_DEPLOY_ERROR="docker compose config --services listed no services — a priced total of 0M would be a gate that cannot see"
		return 1
	fi
	POST_DEPLOY_MB=$(python3 lib/memory_budget_totals.py "$budget_file" --post-deploy \
		--running "$running_csv" --all-services "$all_csv" --declared-services "$declared_csv") || {
		POST_DEPLOY_MB=""
		POST_DEPLOY_ERROR="lib/memory_budget_totals.py could not price the post-deploy set from $budget_file"
		return 1
	}
	POST_DEPLOY_UNPRICED=$(python3 lib/memory_budget_totals.py "$budget_file" --post-deploy --list-unpriced \
		--running "$running_csv" --all-services "$all_csv" --declared-services "$declared_csv") || POST_DEPLOY_UNPRICED=""
}
