#!/usr/bin/env bash
# Sourced by remote.sh, and by tests/infra/test_migration_revisions.py directly — no side
# effects of its own (no `cd`, no `set -e`), the same shape as lib/numeric-id.sh.
#
# Why this exists. `make edge-rollback` redeploys an OLDER ref and runs `remote.sh migrate` for it.
# If the newer release had already applied a migration, the database's alembic revision is one the
# older image's `migrations/versions/` has never heard of, and `alembic upgrade head` in that image
# stops with "Can't locate revision identified by '…'". Under `set -e` that aborted the whole
# rollback at the migrate step — the one moment the operator can least afford it. The right answer
# for a rollback is: the schema is newer than this code, which is fine (every migration is meant to
# be backward compatible with the previous release); leave that service's schema alone, say so
# loudly, and carry on with the rest.
#
# The decision is made from the checkout rather than by starting the image: the images are built
# from this very tree, and a fresh interpreter per service is exactly the memory cost `migrate`
# works hard to avoid.

# unknown_revisions VERSIONS_DIR REV... — prints, one per line, every REV that no migration file
# under VERSIONS_DIR declares as its own `revision`. Prints nothing (and returns 0) when all are
# known or none were given; an empty/blank REV is ignored.
unknown_revisions() {
	local dir="$1" rev
	shift
	for rev in "$@"; do
		[ -n "$rev" ] || continue
		# Revision ids are hex; refuse anything else rather than build a regex from it.
		if [[ ! "$rev" =~ ^[A-Za-z0-9_]+$ ]]; then
			printf '%s\n' "$rev"
			continue
		fi
		if ! grep -Eqs "^revision(: *str)? *= *[\"']${rev}[\"']" "$dir"/*.py; then
			printf '%s\n' "$rev"
		fi
	done
}

# migration_rollback_warning SERVICE REV... — the loud, multi-line message `migrate` prints
# (to stderr) when it skips SERVICE because the database is ahead of the image.
migration_rollback_warning() {
	local svc="$1"
	shift
	printf '%s\n' \
		"!!! $svc: SKIPPED — the database is at alembic revision(s) $* which this image does not know." \
		"!!! This is expected on a ROLLBACK to an older release: the schema stays as the newer release" \
		"!!! left it, and the older code runs against it. It is NOT downgraded." \
		"!!! If this is NOT a rollback, the image is missing a migration — stop and investigate." \
		"!!! Never 'alembic downgrade' a release that has created real data (see RUNBOOK, rollback)."
}
