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
