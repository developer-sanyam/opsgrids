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
# Pure arithmetic on four numbers, nothing else: reading /proc/meminfo and summing
# memory-budget.json for the box's actual COMPOSE_PROFILES is `memory_budget_totals.py`'s job
# (a JSON lookup, not arithmetic — see that file's own header for why it is Python and this is
# bash) and `preflight.sh`'s to wire together. Keeping this function to four numbers is what
# makes it testable without either.
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
apply_accepted_ceiling() {
	local verdict="$1" percent="$2" expected_mb="$3" accepted_mb="${4:-}"
	if [ "$verdict" != "refuse" ] || [ -z "$accepted_mb" ]; then
		echo "$verdict $percent"
		return 0
	fi
	if [ "$expected_mb" -le "$accepted_mb" ]; then
		echo "warn $percent"
	else
		echo "refuse $percent"
	fi
}
