#!/usr/bin/env bash
# Sourced by remote.sh, and by tests/infra/test_remote_state_dir.py directly — no side effects of
# its own (no `cd`, no `set -e`), the same shape as lib/health-check.sh and lib/role-reconcile.sh.
#
# `require_numeric_id` exists for `remote.sh`'s `ensure_ops_monitor_state_dir`, which asks the
# `product` image what uid/gid it runs as (`id -u`, `id -g`) and then hands the answers to a
# root-owned `chown -R`. That output is text that crossed a `docker compose run` boundary; it
# is handed to a command that runs as root on the production box, so it is checked to be
# exactly what a uid/gid is — digits — rather than trusted to be. An image whose entrypoint
# prints a banner, a warning line, or anything else before `id`'s own output would otherwise
# reach `chown` as an argument.

# require_numeric_id LABEL VALUE — prints VALUE (with any carriage return or surrounding
# whitespace a tty or a wrapper added stripped) when it is one or more digits and nothing else;
# otherwise prints a message naming LABEL to stderr and returns 1, printing nothing to stdout.
require_numeric_id() {
	local label="$1" value="${2:-}"
	value=$(printf '%s' "$value" | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
	if [[ ! "$value" =~ ^[0-9]+$ ]]; then
		printf 'require_numeric_id: %s is not a number: %q\n' "$label" "$value" >&2
		return 1
	fi
	printf '%s\n' "$value"
}
