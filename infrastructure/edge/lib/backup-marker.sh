#!/usr/bin/env bash
# Sourced by backup.sh, and by tests/infra/test_backup_marker.py directly — the same shape as
# lib/rebuild-decision.sh, lib/health-check.sh and lib/memory-budget.sh: no side effects of its
# own beyond what it is explicitly asked to do (no `cd`, no `set -e` at source time), so it is
# safe to source from either.
#
# write_backup_marker <state_dir>
#   Writes an ISO-8601 UTC timestamp, trailing newline, to "<state_dir>/backup.last-ok" —
#   the marker `platform_core.ops_monitor.checks.check_backup_freshness` reads. Creates
#   <state_dir> if it does not exist yet. Called from backup.sh ONLY after everything else in a
#   run has already succeeded (the database dump, the certificates, and object storage when
#   this box runs it) — see backup.sh's own comment at the call site for why the ordering
#   matters: a marker written earlier, or on a path that can still fail after it, tells the
#   monitor a backup happened when only part of one did.
#
# The one thing kept out of this function on purpose: it does not decide *whether* to write the
# marker, only *how*. backup.sh's own control flow (the `minio_backup_failed` check, `set -e`
# above it) is what "fully successful" means; this function has no opinion on that.
write_backup_marker() {
	local state_dir="$1"
	mkdir -p "$state_dir"
	date -u +%Y-%m-%dT%H:%M:%SZ >"$state_dir/backup.last-ok"
}
