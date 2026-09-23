#!/usr/bin/env bash
# Sourced by remote.sh, and by tests/infra/test_remote_rebuild_decision.py directly — this
# file has no side effects of its own (no `cd`, no `set -e`) so it is safe to `source` from
# either. Splitting it out of remote.sh is what makes the rule testable without dragging in
# remote.sh's own top-level side effects (it `cd`s to its own directory and hashes
# routes.prod.json the moment it is sourced).
#
# needs_full_rebuild <repo> <prev> <next>
#   <repo> the git worktree/repo to diff in — remote.sh always passes its own `../..`, a test
#          passes a throwaway repo.
#   <prev> the ref last known to have served healthily, or "" for "no usable baseline" (a
#          first deploy, or a `last-good-ref` this clone no longer recognises).
#   <next> the ref being deployed.
#
# Returns 0 (true, in shell terms) when every image must be rebuilt; 1 when a per-service
# build is enough.
#
# `libs/contracts` is deliberately excluded: it is JSON, not a workspace package, and
# `.dockerignore` keeps it out of every image. Its one runtime reader — account's permission
# manifests — gets it through a bind mount, read afresh on each activation. But almost every
# service PR regenerates an OpenAPI file there, so counting it as "every image is made from
# this" took the whole box down once: every such deploy rebuilt and recreated all ~60
# containers, which on this box outlasts any retry window — an invitation accepted mid-deploy
# got a 502.
needs_full_rebuild() {
	local repo="$1" prev="$2" next="$3"
	[ -n "$repo" ] || {
		echo "needs_full_rebuild: no repo given" >&2
		return 2
	}
	[ -n "$next" ] || {
		echo "needs_full_rebuild: no target ref given" >&2
		return 2
	}
	# No usable baseline: build everything rather than guess at what changed.
	[ -n "$prev" ] || return 0
	! git -C "$repo" diff --quiet "$prev" "$next" -- \
		libs ':!libs/contracts' pyproject.toml uv.lock .dockerignore
}
