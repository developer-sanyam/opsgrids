#!/usr/bin/env bash
# Sourced by remote.sh (production) and tools/reconcile-roles.sh (dev), and by
# tests/infra/test_remote_role_reconcile.py directly — this file has no side effects of its own
# (no `cd`, no `set -e`, no psql call), the same shape as lib/health-check.sh and
# lib/rebuild-decision.sh, and for the same reason: the SQL it builds is pinned by a test that
# never touches a database.
#
# The gap this exists to close: `tools/dev-bootstrap/init-db.sql` creates every `opsgrids_<service>`
# role, but only on a *fresh* Postgres data volume — the docker entrypoint only runs
# /docker-entrypoint-initdb.d/* the first time a volume initialises. A service added after a box's
# first boot (this is how `leave` broke a live deploy) has no role at all: its migration's
# `CREATE POLICY ... TO opsgrids_leave` or `REVOKE ... FROM opsgrids_leave` fails outright, and
# `set -euo pipefail` in remote.sh aborts the whole deploy on it. `role_reconcile_sql` is what
# `remote.sh migrate` runs, idempotently, for every service in `SERVICES_WITH_SCHEMA`, before a
# single `alembic upgrade` — so the role exists (with the box's real password, not the
# bootstrap's dev placeholder) whether this is the service's first deploy ever or its hundredth.
#
# Deliberately narrow: this creates the role, sets its password and grants exactly what
# `tools/dev-bootstrap/init-db.sql` grants a per-service role at bootstrap time (CONNECT, and
# USAGE on the schemas PostGIS and reflection need) — CONNECT and schema USAGE. It never touches
# a table grant: those come from each service's own `apply_schema_grants` in `migrations/env.py`,
# run by the `alembic upgrade` that follows this. It is also never a substitute for
# `tools/dev-bootstrap/init-db.sql`'s own role list — that file is still what a fresh volume
# reads on its very first boot; this is the retrofit for every volume that already existed.

# roles_for_services SERVICE [SERVICE...] — one "opsgrids_<service>" per line.
roles_for_services() {
	local svc
	for svc in "$@"; do
		printf 'opsgrids_%s\n' "$svc"
	done
}

# role_reconcile_sql SERVICE [SERVICE...] — the full idempotent SQL script, one service at a
# time. `:'pw'` is psql variable interpolation, exactly like
# `infrastructure/edge/postgres-init/30-role-passwords.sh` uses for the identical reason: the SQL
# goes to psql on stdin (never `-c`), which is what makes `-v pw=...` interpolate it rather than
# sending the literal text `:'pw'` to the server.
#
# A service name outside `[a-z0-9_]` is refused rather than interpolated — the list only ever
# comes from `SERVICES_WITH_SCHEMA`/`services/*` in this repository, but a role name is about to
# be spliced into SQL text, and "this input is trusted today" is exactly the assumption that
# stops being true the one time it matters.
role_reconcile_sql() {
	local svc role
	for svc in "$@"; do
		case "$svc" in
		*[!a-z0-9_]* | "")
			echo "role_reconcile_sql: refusing service name '$svc'" >&2
			return 1
			;;
		esac
		role="opsgrids_$svc"
		cat <<-SQL
			DO \$do\$
			BEGIN
			  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$role') THEN
			    CREATE ROLE $role LOGIN NOBYPASSRLS;
			  END IF;
			END
			\$do\$;
			ALTER ROLE $role PASSWORD :'pw';
			GRANT CONNECT ON DATABASE opsgrids TO $role;
			GRANT USAGE ON SCHEMA public, tiger, tiger_data, topology TO $role;
		SQL
	done
}

# directory_roles_from_config REPO_ROOT — every BYPASSRLS directory-reader role name a
# service declares in its own `app/config.py` (`db_directory_role: str | None = "..."`),
# discovered rather than hand-listed here a second time — the same reason
# `tools/reconcile-roles.sh` reads `SERVICES_WITH_SCHEMA` out of `remote.sh` instead of
# copying it: a role list kept in two places is a role list that eventually drifts. Printed
# deduplicated and sorted, one per line, because more than one service intentionally shares a
# reader (`opsgrids_directory` is account's own config and the name every other service's
# `db_directory_role` for the *identity* lookup would also read, were one ever added).
directory_roles_from_config() {
	local root="${1:?directory_roles_from_config: repo root is required}"
	grep -hoE 'db_directory_role: str \| None = "[a-z0-9_]+"' "$root"/services/*/app/config.py 2>/dev/null |
		grep -oE '"[a-z0-9_]+"' | tr -d '"' | sort -u
}

# directory_role_reconcile_sql ROLE [ROLE...] — the BYPASSRLS counterpart of
# `role_reconcile_sql`, for the same gap: `tools/dev-bootstrap/init-db.sql` creates every
# `opsgrids_*_directory` reader, but only on a database's first boot. A directory role added
# after that (billing's `opsgrids_billing_directory`, added alongside the staff credit-ledger
# route — MAJOR 7 of the security review that found it missing here) otherwise has no role at
# all on a box that already existed, and the service's own `migrations/env.py` GRANT to it
# fails outright. `NOINHERIT BYPASSRLS` matches exactly what `init-db.sql` grants each one at
# bootstrap: CONNECT and schema USAGE only — the per-table SELECT is the owning service's own
# `migrations/env.py`, unchanged by this.
directory_role_reconcile_sql() {
	local role
	for role in "$@"; do
		case "$role" in
		*[!a-z0-9_]* | "")
			echo "directory_role_reconcile_sql: refusing role name '$role'" >&2
			return 1
			;;
		esac
		cat <<-SQL
			DO \$do\$
			BEGIN
			  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$role') THEN
			    CREATE ROLE $role LOGIN NOINHERIT BYPASSRLS;
			  END IF;
			END
			\$do\$;
			ALTER ROLE $role PASSWORD :'pw';
			GRANT CONNECT ON DATABASE opsgrids TO $role;
			GRANT USAGE ON SCHEMA public, tiger, tiger_data, topology TO $role;
		SQL
	done
}
