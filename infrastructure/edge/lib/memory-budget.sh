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

# grandfathered_verdict <verdict> <percent> <expected_mb> [<last_ok_mb>]
#   B1(a), the memory-headroom slice's 2026-09-23 fix round: production runs every product it
#   sells (ten profiles), and the re-measured `kinds.background` price (memory-budget.json's own
#   header) puts that combination over the hard budget line on its own, with no planned service
#   added yet. Refusing every future deploy over a fact that was already true yesterday helps
#   nobody and blocks real fixes from shipping. This function is the one place that decides
#   whether an already-`refuse` verdict from `memory_budget_verdict` should actually refuse.
#
#   Only ever touches a `refuse` verdict — `ok` and `warn` pass through completely unchanged, so
#   a box that is not overcommitted is never affected by any of this.
#
#   No `last_ok_mb` (a fresh box, or one preflight has never recorded a successful deploy for):
#   today's behaviour, unchanged — refuse. This is deliberately NOT a way to launder a box's very
#   first over-budget deploy into a warning by never letting it succeed once.
#
#   `last_ok_mb` present: compares `expected_mb` (this deploy) against `last_ok_mb` (the most
#   recent deploy that actually went healthy) plus 1%. Within that: downgrade to a loud `warn` —
#   the box was already overcommitted and this deploy does not make it worse, so it is allowed
#   through with the overcommit stated plainly rather than blocked on a fact this deploy did not
#   create. Over that ceiling: still `refuse` — THIS deploy is what pushed it further, which is
#   exactly the case the hard budget line exists to catch.
#
#   Integer-only 1% ceiling (`last_ok_mb + last_ok_mb / 100`), rounded down the same way
#   `memory_budget_verdict`'s own percent is: a deploy landing exactly on the ceiling is treated
#   as "no worse", not as borderline-refuse.
grandfathered_verdict() {
	local verdict="$1" percent="$2" expected_mb="$3" last_ok_mb="${4:-}"
	if [ "$verdict" != "refuse" ] || [ -z "$last_ok_mb" ]; then
		echo "$verdict $percent"
		return 0
	fi
	local ceiling=$((last_ok_mb + last_ok_mb / 100))
	if [ "$expected_mb" -le "$ceiling" ]; then
		echo "warn $percent"
	else
		echo "refuse $percent"
	fi
}
