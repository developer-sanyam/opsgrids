#!/usr/bin/env bash
# Nightly backup, run ON THE BOX by cron. See RUNBOOK.md, "backups and restore".
#
# Three things go off-box, for three different disasters:
#
#   * `pg_dump` of the whole cluster — every merchant's store, domain claims and audit rows.
#     A backup on the same disk as the database is not a backup, which is also why MinIO's own
#     data living on this same VM would not count on its own — it goes off-box separately below.
#   * a tar of Caddy's `/data` volume — the ACME account key and every certificate. Losing it
#     is survivable (every name re-issues on its next handshake) and expensive (the whole
#     fleet re-issues at once, which is the Let's Encrypt rate-limit event). A few hundred
#     kilobytes a night is a cheap way not to find out.
#   * a tar of MinIO's `/data` volume — every uploaded merchant image (ADR-0009). Unlike the
#     certificates, there is no re-issue: a lost object is a merchant's broken hero image with
#     no automatic recovery. Skipped when the `storefront` profile is off, because there is then
#     no MinIO container and nothing to read the volume from.
#
# Order is deliberate, and it is the fix for a real incident: the database dump and the
# certificate archive are created and uploaded FIRST, unconditionally, under `set -e` — if
# either fails, the script dies loudly and nothing pretends to have backed up. The MinIO
# archive runs LAST and its failure is contained: `quay.io/minio/minio` has no shell and no
# `tar` (`docker run --entrypoint tar quay.io/minio/minio --version` exits 127, "executable
# file not found in $PATH" — verified, not assumed), so archiving it can never reuse the
# `minio` service the way the certs step reuses `caddy`. Before this was fixed, that 127 ran
# under `set -e` ahead of every upload, so the script died before the Postgres dump — the one
# backup that actually matters at 3am — ever reached the bucket. A MinIO archive/upload
# failure now still ends the script non-zero (cron mails it, same as any other failure), but
# only after the dump and the certificates are already off-box.
#
# Exit codes matter: cron mails non-zero output to the box's local user, and in slice 1 that
# is the only thing resembling an alert. Read RUNBOOK.md "how a human finds out" before
# deciding that is enough.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
set -a
# shellcheck disable=SC1091 # .env is the box's own secrets, never in the repo to follow
. ./.env
set +a

COMPOSE=(docker compose -f compose.prod.yml --env-file .env)
# Pinned, never `:latest`: this image holds the bucket credentials for the length of every
# upload, and a moved tag would change what runs with them at 03:00 with nobody watching. Bump
# it deliberately, and change RUNBOOK.md's restore drill in the same commit.
AWS_CLI_IMAGE=amazon/aws-cli:2.36.50
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
# access policy, which must not be public. `caddy:2.10.0-alpine` has both `sh` and `tar`.
"${COMPOSE[@]}" run --rm --no-deps -T --entrypoint sh -v "$dir:/backup" caddy \
	-c "tar czf /backup/$(basename "$certs") -C /data ." >/dev/null
echo "  certificates: $certs"

# --- off the box: the database and the certificates, unconditionally ----------
# These two happen BEFORE the MinIO archive below, on purpose: they are created under `set -e`
# above, so a failure in either has already stopped the script — nothing past this point runs
# on a box whose dump did not gzip cleanly or whose certs did not tar. Everything from here
# down is the box's *object storage* backup, which is real but strictly less critical than the
# database, and its failure must not retroactively un-upload what already succeeded.
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
		"$AWS_CLI_IMAGE" s3 cp "/backup/$(basename "$file")" \
		"s3://$BACKUP_S3_BUCKET/${BACKUP_S3_PREFIX:-edge}/$(basename "$file")" \
		"${endpoint_arg[@]}" --only-show-errors
}

upload "$dump"
upload "$certs"
echo "  uploaded database + certificates to s3://$BACKUP_S3_BUCKET/${BACKUP_S3_PREFIX:-edge}/"

# --- MinIO's uploaded images (ADR-0009), archived and uploaded LAST -----------
# Only when the storefront profile actually started it: there is then no MinIO container and
# nothing on the volume to lose anyway.
#
# `quay.io/minio/minio` has no shell and no `tar` — confirmed by running
# `docker run --entrypoint tar quay.io/minio/minio:RELEASE.2024-01-16T16-07-38Z --version`,
# which exits 127, "executable file not found in $PATH". Reusing the `minio` service the way
# the certs step above reuses `caddy` is therefore not available, and running it that way is
# exactly the bug this comment replaces: it failed with exit 127 *before* either upload above,
# and under `set -e` that killed the whole backup — including the database dump — every single
# night the `storefront` profile was on.
#
# The fix reads the volume's real name off the running container instead of assuming a naming
# convention (`docker inspect`, not a hardcoded `<project>_minio_data`; RUNBOOK.md's restore
# drill still uses the literal name for a human typing at a terminal, which is a different
# situation from a script that must not guess), and archives it with `caddy:2.10.0-alpine` —
# already pinned and already pulled for the step above, so this adds no new image to trust or
# to fail to pull at 3am.
#
# A failure anywhere in this block is caught and reported, never allowed to raise: the database
# and the certificates are already safely off-box by the time this runs, and that must stay
# true regardless of what MinIO does tonight.
minio_backup_failed=0
assets=""
if "${COMPOSE[@]}" ps --status running minio 2>/dev/null | grep -q minio; then
	assets="$dir/minio-data-$stamp.tar.gz"
	minio_volume=$(docker inspect \
		-f '{{ range .Mounts }}{{ if eq .Destination "/data" }}{{ .Name }}{{ end }}{{ end }}' \
		"$("${COMPOSE[@]}" ps -q minio)" 2>/dev/null || true)
	if [ -z "$minio_volume" ]; then
		echo "backup: FAILED to find MinIO's /data volume — object storage is NOT backed up off-box tonight" >&2
		minio_backup_failed=1
	elif ! docker run --rm --entrypoint sh \
		-v "$minio_volume:/data:ro" \
		-v "$dir:/backup" \
		caddy:2.10.0-alpine \
		-c "tar czf /backup/$(basename "$assets") -C /data ." >/dev/null; then
		echo "backup: FAILED to archive MinIO's data volume — object storage is NOT backed up off-box tonight" >&2
		minio_backup_failed=1
	elif ! upload "$assets"; then
		echo "backup: FAILED to upload $assets — object storage is NOT backed up off-box tonight" >&2
		minio_backup_failed=1
	else
		echo "  object storage: $assets, uploaded"
	fi
else
	echo "  object storage: minio is not running on this box (storefront profile off) — skipped"
fi

# --- prune the local copies ---------------------------------------------------
keep="${BACKUP_LOCAL_KEEP:-7}"
find "$dir" -name 'opsgrids-*.sql.gz' -type f | sort | head -n -"$keep" | xargs -r rm -f
find "$dir" -name 'caddy-data-*.tar.gz' -type f | sort | head -n -"$keep" | xargs -r rm -f
find "$dir" -name 'minio-data-*.tar.gz' -type f | sort | head -n -"$keep" | xargs -r rm -f

if [ "$minio_backup_failed" -ne 0 ]; then
	echo "backup: database and certificates are safely off-box. MinIO's object storage backup FAILED — see above." >&2
	exit 1
fi
echo "backup: ok ($(du -sh "$dir" | cut -f1) kept locally, $keep newest of each)"
