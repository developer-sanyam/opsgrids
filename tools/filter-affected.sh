#!/usr/bin/env bash
# Narrow a shard's service list to the ones a change could actually affect — CI's one
# extra skip on top of the skips `fan-out.sh` already does for "no app", "no tests",
# "no alembic.ini". See `tools/shard.sh`'s own comment for why this is safe to add here
# and not upstream of the shard split: the partition is still computed over *every*
# service, unchanged; this only trims what a shard hands its fan-out afterwards, which is
# exactly the "let the consumer skip what it cannot use" extension point that comment
# describes.
#
# Default behaviour — AFFECTED_ALL unset, or anything other than exactly "false" — is a
# plain passthrough, so `make migration-shard` / `make test-shard` run every service in
# the shard exactly as before whenever CI has not computed a filter (or has deliberately
# decided not to trust one) and always when run by hand. Only `AFFECTED_ALL=false` with a
# populated `AFFECTED_SERVICES` turns this into a filter.
#
# `.github/workflows/ci.yml`'s `changes` job sets both, and sets AFFECTED_ALL=false only
# when every changed path in the pull request resolved to specific services/<name>/ dirs —
# anything touching libs/platform-core, libs/contracts, root config, tooling or an
# unrecognised path falls back to AFFECTED_ALL=true there, on purpose.
#
# usage: tools/shard.sh N S | tools/filter-affected.sh
#   reads service names on stdin, one per line; writes the ones that survive the filter,
#   same format, to stdout.
set -euo pipefail

if [ "${AFFECTED_ALL:-true}" != "false" ] || [ -z "${AFFECTED_SERVICES:-}" ]; then
  cat
  exit 0
fi

# Space- or newline-separated; service names are [a-z0-9-], so normalising newlines to
# spaces and padding both ends turns membership into a plain substring match. No
# associative array: bash 3.2, which macOS still ships, does not have one (see
# tools/shard.sh's comment) — this costs nothing extra at this size and runs identically
# everywhere.
want=" $(printf '%s' "$AFFECTED_SERVICES" | tr '\n' ' ') "

while IFS= read -r svc; do
  [ -n "$svc" ] || continue
  case "$want" in
  *" $svc "*) printf '%s\n' "$svc" ;;
  esac
done
exit 0
