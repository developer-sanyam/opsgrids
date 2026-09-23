#!/usr/bin/env bash
# Sourced by remote.sh, and by tests/infra/test_remote_health.py directly — this file has no
# side effects of its own (no `cd`, no `set -e`), the same shape as lib/rebuild-decision.sh and
# for the same reason: splitting it out of remote.sh is what makes the rule testable without a
# running compose project.
#
# unhealthy_from_ps reads lines of
#   SERVICE|STATE|HEALTH|EXITCODE|LABELS
# (produced by
#  `docker compose ps --all --format '{{.Service}}|{{.State}}|{{.Health}}|{{.ExitCode}}|{{.Labels}}'`)
# on stdin and prints one "service(state/health)" line per container that is NOT healthy.
#
# Pipe-delimited, not space-delimited: `.Health` is empty for any container with no
# HEALTHCHECK (every background worker and one-shot), and awk's default whitespace FS collapses
# the resulting run of spaces, silently shifting every field after it by one. A real
# `docker compose ps` line with an empty Health field reproduces this exactly, so a
# space-delimited format is wrong the first time it meets a background container, not a
# theoretical risk.
#
# A container is healthy when either:
#   * it is "running", and its Health is "" (no healthcheck defined), "<nil>" (compose's own
#     placeholder for the same) or "healthy"; or
#   * it is "exited" with exit code 0 AND labelled `opsgrids.oneshot=true`.
#
# The one-shot exception exists for bootstrap containers like `minio-init`, which do their job
# and exit 0 by design — before this existed, `health` treated every non-"running" container as
# a failure, so any deploy with the `storefront` profile waited the full HEALTH_TIMEOUT and
# failed (record_ref skipped, the apps job skipped) even though nothing was actually wrong.
#
# The label is the gate, not the exit code alone: an unlabelled container that exits 0 (a crash
# loop that happens to land on 0, or a service that was supposed to keep running) still fails
# immediately, which is the correct direction to be wrong in — a silent pass here is a deploy
# that "succeeds" while the thing it just started is not actually there.
unhealthy_from_ps() {
	awk -F'|' '
	{
		service = $1; state = $2; health = $3; exit_code = $4; labels = $5
		is_oneshot = (labels ~ /(^|,)opsgrids\.oneshot=true(,|$)/)
		if (state == "running") {
			if (health != "" && health != "healthy" && health != "<nil>") {
				print service"("state"/"health")"
			}
			next
		}
		if (state == "exited" && exit_code == "0" && is_oneshot) {
			next
		}
		print service"("state"/"health")"
	}'
}
