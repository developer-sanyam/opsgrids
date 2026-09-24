#!/usr/bin/env bash
# Create any opsgrids_<service> role missing on the dev database, idempotently — the dev
# counterpart of `infrastructure/edge/remote.sh reconcile-roles`. Needed because
# `tools/dev-bootstrap/init-db.sql` only runs on a *fresh* postgres data volume: a service added
# after `make infra` first created your volume (this is how the `leave` service broke a live
# deploy) has no role at all until this runs, or until `make infra-reset` recreates the volume
# from scratch.
#
# Reuses the exact SQL `infrastructure/edge/lib/role-reconcile.sh` builds for production, so
# there is one definition of what a per-service role needs, not a dev copy that can drift from
# the one that actually runs on the box. The dev password is the literal 'opsgrids' every role
# already has (tools/dev-bootstrap/init-db.sql), not a secret read from anywhere.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# shellcheck source=infrastructure/edge/lib/role-reconcile.sh
source infrastructure/edge/lib/role-reconcile.sh

# The same list `infrastructure/edge/remote.sh` migrates against, read out of that file rather
# than duplicated here — a role list copied a third time is a role list that drifts a third way.
services=$(sed -n 's/^SERVICES_WITH_SCHEMA=(\(.*\))$/\1/p' infrastructure/edge/remote.sh)
if [ -z "$services" ]; then
	echo "reconcile-roles: could not find SERVICES_WITH_SCHEMA in infrastructure/edge/remote.sh" >&2
	exit 1
fi

# shellcheck disable=SC2086  # $services is a deliberately unquoted word list, like $(...) above.
role_reconcile_sql $services |
	docker compose -f tools/dev-bootstrap/compose.yml --env-file .env exec -T postgres \
		psql -v ON_ERROR_STOP=1 -v pw=opsgrids -U opsgrids -d opsgrids

# The BYPASSRLS directory readers, the same gap for the same reason (role-reconcile.sh's own
# docstring): discovered from each service's own `app/config.py` rather than hand-listed here.
directory_roles=$(directory_roles_from_config .)
if [ -n "$directory_roles" ]; then
	# shellcheck disable=SC2086  # deliberately word-split, like $services above
	directory_role_reconcile_sql $directory_roles |
		docker compose -f tools/dev-bootstrap/compose.yml --env-file .env exec -T postgres \
			psql -v ON_ERROR_STOP=1 -v pw=opsgrids -U opsgrids -d opsgrids
fi
