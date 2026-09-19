#!/usr/bin/env bash
# Nightly backup, run ON THE BOX by cron. See RUNBOOK.md, "backups and restore".
#
# Two things go off-box, for two different disasters:
#
#   * `pg_dump` of the whole cluster — every merchant's store, domain claims and audit rows.
#     A backup on the same disk as the database is not a backup, which is also why MinIO on
#     this VM would not have counted.
#   * a tar of Caddy's `/data` volume — the ACME account key and every certificate. Losing it
#     is survivable (every name re-issues on its next handshake) and expensive (the whole
#     fleet re-issues at once, which is the Let's Encrypt rate-limit event). A few hundred
#     kilobytes a night is a cheap way not to find out.
#
# Exit codes matter: cron mails non-zero output to the box's local user, and in slice 1 that
# is the only thing resembling an alert. Read RUNBOOK.md "how a human finds out" before
# deciding that is enough.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
# shellcheck disable=SC1091
set -a
. ./.env
set +a

COMPOSE=(docker compose -f compose.prod.yml --env-file .env)
stamp=$(date -u +%Y%m%dT%H%M%SZ)
dir="${BACKUP_LOCAL_DIR:-/srv/opsgrids/backups}"
mkdir -p "$dir"
dump="$dir/opsgrids-$stamp.sql.gz"
certs="$dir/caddy-data-$stamp.tar.gz"

echo "backup $stamp"

# --- the database -------------------------------------------------------------
# Plain SQL, gzipped: restorable with psql alone, which is what someone wants at 3am, and
# readable by a future Postgres. `--clean --if-exists` so the restore procedure in the runbook
# is a single psql invocation into an empty or an existing database.
#
# Ownership IS dumped (no --no-owner), so a restore puts the schemas back under
# `opsgrids_admin` and the per-service grants stay meaningful. The price is that the roles must
# exist before a restore — they do, because 20-init.sql runs on any fresh data volume.
"${COMPOSE[@]}" exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres \
	pg_dump --username=opsgrids --dbname=opsgrids --clean --if-exists |
	gzip -9 >"$dump"

size=$(wc -c <"$dump")
# A dump that is suspiciously small is the classic silent backup failure: the command
# succeeded, the pipe did not. Anything under 10 KB is not this platform's schema.
if [ "$size" -lt 10240 ]; then
	echo "backup: dump is only ${size} bytes — refusing to call that a backup" >&2
	exit 1
fi
echo "  database: $dump ($size bytes)"

# --- Caddy's certificates and ACME account key --------------------------------
# Read from the volume through a throwaway container, so nothing needs to know where Docker
# keeps it. The archive holds private keys: it goes to the same bucket and inherits the same
# access policy, which must not be public.
"${COMPOSE[@]}" run --rm --no-deps -T --entrypoint sh -v "$dir:/backup" caddy \
	-c "tar czf /backup/$(basename "$certs") -C /data ." >/dev/null
echo "  certificates: $certs"

# --- off the box --------------------------------------------------------------
if [ -z "${BACKUP_S3_BUCKET:-}" ] || [ -z "${BACKUP_S3_ACCESS_KEY_ID:-}" ]; then
	echo "backup: no off-box destination configured — these files are ON THE SAME DISK as the" >&2
	echo "        database they back up. Set BACKUP_S3_* in .env." >&2
	exit 1
fi

endpoint_arg=()
# `if` rather than `[ … ] && …`: under `set -e` the second form exits the script when the test
# is false, which here is the normal case of using AWS itself.
if [ -n "${BACKUP_S3_ENDPOINT:-}" ]; then
	endpoint_arg=(--endpoint-url "$BACKUP_S3_ENDPOINT")
fi

upload() {
	local file="$1"
	docker run --rm \
		-e AWS_ACCESS_KEY_ID="$BACKUP_S3_ACCESS_KEY_ID" \
		-e AWS_SECRET_ACCESS_KEY="$BACKUP_S3_SECRET_ACCESS_KEY" \
		-e AWS_DEFAULT_REGION="${BACKUP_S3_REGION:-us-east-1}" \
		-v "$dir:/backup:ro" \
		amazon/aws-cli:latest s3 cp "/backup/$(basename "$file")" \
		"s3://$BACKUP_S3_BUCKET/${BACKUP_S3_PREFIX:-edge}/$(basename "$file")" \
		"${endpoint_arg[@]}" --only-show-errors
}

upload "$dump"
upload "$certs"
echo "  uploaded to s3://$BACKUP_S3_BUCKET/${BACKUP_S3_PREFIX:-edge}/"

# --- prune the local copies ---------------------------------------------------
keep="${BACKUP_LOCAL_KEEP:-7}"
find "$dir" -name 'opsgrids-*.sql.gz' -type f | sort | head -n -"$keep" | xargs -r rm -f
find "$dir" -name 'caddy-data-*.tar.gz' -type f | sort | head -n -"$keep" | xargs -r rm -f

echo "backup: ok ($(du -sh "$dir" | cut -f1) kept locally, $keep newest of each)"
