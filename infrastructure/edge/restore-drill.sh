#!/usr/bin/env bash
# The quarterly restore drill, run ON THE BOX (`make edge-restore-drill`, RUNBOOK.md "Restore from
# backup"). A backup nobody has restored is a hypothesis; this turns it into a dated result line.
#
#   1. fetch the NEWEST `opsgrids-*.sql.gz[.age]` back from the bucket (proves the upload worked,
#      which a green backup.sh alone does not);
#   2. decrypt it if it is `.age`, with the private key read from STDIN — `make` feeds it from a
#      file on the operator's machine, so the key never touches this box's disk (it lives in one
#      shell variable and reaches `age` through a pipe fd, never argv and never a file);
#   3. restore it into the scratch database `opsgrids_restore` — the ONLY name accepted;
#   4. compare row counts of the biggest few tables per schema against live (SELECT count(*) only:
#      read-only toward production data);
#   5. drop the scratch database and the downloaded file, whatever happened;
#   6. print one line: `restore-drill <UTC date> OK|FAIL …`. Non-zero exit on FAIL.
#
# Usage on the box: ./restore-drill.sh [opsgrids_restore] < identity-file   (stdin empty for a
# plaintext backup).
set -euo pipefail

target="${1:-opsgrids_restore}"
# A typo here would `DROP DATABASE` something real: one name, spelled out, and never the live one.
if [ "$target" = opsgrids ] || [ "$target" != opsgrids_restore ]; then
	echo "restore-drill: refusing target database '$target' — only opsgrids_restore is allowed" >&2
	exit 64
fi

cd "$(dirname "${BASH_SOURCE[0]}")"
set -a
# shellcheck disable=SC1091 # .env is the box's own secrets, never in the repo to follow
. ./.env
set +a

COMPOSE=(docker compose -f compose.prod.yml --env-file .env)
# The same pin as backup.sh: bump both together.
AWS_CLI_IMAGE=amazon/aws-cli:2.36.50
age_bin="${BACKUP_AGE_BIN:-age}"
dir="${BACKUP_LOCAL_DIR:-/srv/opsgrids/backups}"
prefix="${BACKUP_S3_PREFIX:-edge}"
today=$(date -u +%Y-%m-%dT%H:%M:%SZ)

identity=""
if [ ! -t 0 ]; then identity=$(cat); fi

fail() {
	echo "restore-drill $today FAIL $*"
	exit 1
}

[ -n "${BACKUP_S3_BUCKET:-}" ] && [ -n "${BACKUP_S3_ACCESS_KEY_ID:-}" ] ||
	fail "no off-box bucket configured (BACKUP_S3_*)"

endpoint_arg=()
if [ -n "${BACKUP_S3_ENDPOINT:-}" ]; then
	endpoint_arg=(--endpoint-url "$BACKUP_S3_ENDPOINT")
fi

aws() {
	# Credentials through docker's environment, never argv (as backup.sh).
	AWS_ACCESS_KEY_ID="$BACKUP_S3_ACCESS_KEY_ID" \
		AWS_SECRET_ACCESS_KEY="$BACKUP_S3_SECRET_ACCESS_KEY" \
		docker run --rm \
		-e AWS_ACCESS_KEY_ID \
		-e AWS_SECRET_ACCESS_KEY \
		-e AWS_DEFAULT_REGION="${BACKUP_S3_REGION:-us-east-1}" \
		-v "$dir:/backup" \
		"$AWS_CLI_IMAGE" "$@" ${endpoint_arg[@]+"${endpoint_arg[@]}"}
}

psql_in() {
	local db="$1"
	shift
	PGPASSWORD="$POSTGRES_PASSWORD" "${COMPOSE[@]}" exec -T -e PGPASSWORD postgres \
		psql -v ON_ERROR_STOP=1 -X -q --username=opsgrids --dbname="$db" "$@"
}

# --- 1. the newest object -------------------------------------------------------
object=$(aws s3 ls "s3://$BACKUP_S3_BUCKET/$prefix/" |
	awk '{print $NF}' | { grep -E '^opsgrids-[0-9TZ]+\.sql\.gz(\.age)?$' || true; } | sort | tail -n 1) ||
	fail "could not list s3://$BACKUP_S3_BUCKET/$prefix/"
[ -n "$object" ] || fail "no opsgrids-*.sql.gz in s3://$BACKUP_S3_BUCKET/$prefix/"
case "$object" in
*.age) [ -n "$identity" ] || fail "object=$object is encrypted and no key was given on stdin (AGE_KEY=)" ;;
esac

# A name of its own, so the drill never overwrites backup.sh's local copy of the same night.
local_copy="$dir/restore-drill-$object"
cleanup() {
	rm -f "$local_copy"
	psql_in postgres -c "DROP DATABASE IF EXISTS $target" >/dev/null 2>&1 || true
}
trap cleanup EXIT

aws s3 cp "s3://$BACKUP_S3_BUCKET/$prefix/$object" "/backup/$(basename "$local_copy")" \
	--only-show-errors || fail "object=$object could not be downloaded"

# --- 2 + 3. decrypt and restore into the scratch database ---------------------------
psql_in postgres -c "DROP DATABASE IF EXISTS $target" >/dev/null
psql_in postgres -c "CREATE DATABASE $target" >/dev/null
decrypt() {
	case "$object" in
	*.age) "$age_bin" -d -i <(printf '%s\n' "$identity") "$local_copy" ;;
	*) cat "$local_copy" ;;
	esac
}
decrypt | gunzip | psql_in "$target" >/dev/null || fail "object=$object did not restore cleanly"

# --- 4. row counts, biggest three tables per schema (by live statistics) ------------
# `outbox` is drained continuously and `alembic_version` is one row: neither says anything here.
sample=$(psql_in opsgrids -At -c "
	SELECT format('%I.%I', s, t) FROM (
		SELECT n.nspname AS s, c.relname AS t,
		       row_number() OVER (PARTITION BY n.nspname ORDER BY c.reltuples DESC, c.relname) AS r
		FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
		WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition
		  AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'public', 'topology')
		  AND n.nspname NOT LIKE 'pg\\_%' AND n.nspname NOT LIKE 'tiger%'
		  AND c.relname NOT IN ('outbox', 'alembic_version')
	) x WHERE r <= 3 ORDER BY 1") || fail "could not read the live catalogue"
[ -n "$sample" ] || fail "object=$object live database has no tables to compare"

tables=0
problems=0
while IFS= read -r table; do
	tables=$((tables + 1))
	# `</dev/null`: `compose exec -T` would otherwise eat the rest of this loop's input.
	live=$(psql_in opsgrids -At -c "SELECT count(*) FROM $table" </dev/null)
	if ! restored=$(psql_in "$target" -At -c "SELECT count(*) FROM $table" </dev/null 2>/dev/null); then
		echo "  MISSING  $table (live $live)"
		problems=$((problems + 1))
	elif [ "$restored" -eq 0 ] && [ "$live" -gt 0 ]; then
		echo "  EMPTY    $table restored 0, live $live"
		problems=$((problems + 1))
	else
		echo "  ok       $table restored $restored, live $live"
	fi
done <<<"$sample"

# --- 5 + 6. cleanup runs on EXIT; the one line ----------------------------------------
if [ "$problems" -ne 0 ]; then
	fail "object=$object tables=$tables problems=$problems"
fi
echo "restore-drill $today OK object=$object tables=$tables"
