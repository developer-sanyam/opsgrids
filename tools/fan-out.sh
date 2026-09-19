#!/usr/bin/env bash
# Run one command per service, several at a time.
#
# Thirty-odd invocations of mypy, pytest or alembic run one at a time was most of what CI
# spent its time on. Each invocation is an independent process against its own directory and
# its own Postgres schema, so they parallelise — and the reason that is true rather than
# merely convenient is ADR-0000 §10: no foreign key crosses a schema boundary, so no
# service's DDL can depend on another's.
#
# Two implementation notes, both learned the hard way:
#
#   * The parallelism is `xargs -P`, not a shell job loop. A loop needs `wait -n` to keep the
#     pipe full, and macOS ships bash 3.2, which does not have it — the fallback is `wait`,
#     which drains every job before starting the next batch and turns streaming parallelism
#     into lockstep waves. Measured: the wave version was no faster than serial.
#   * The per-service work is a re-invocation of this script (`--one`), so the command line
#     xargs rebuilds per input stays short. Substituting a long inline script into `xargs -I`
#     overflows BSD's replacement buffer: "command line cannot be assembled, too long".
#
# Output is captured per service rather than streamed: thirty interleaved mypy reports are
# unreadable. A service's output is printed in full only when it fails, and every service is
# still attempted, so one failure reports the others' results instead of hiding them.
#
# usage: fan-out.sh <jobs> <require> <ok-empty|strict> <command...>
#
#   jobs      how many services to process at once; 1 is strictly serial
#   require   a path that must exist inside services/<name> or the service is skipped
#             (`app` for mypy, `tests` for pytest, `alembic.ini` for migrations)
#   ok-empty  accept pytest's exit code 5 — "no tests collected" — as success, which it is
#             for a service that has none yet; `strict` treats it as failure
#   command   run with services/<name> as the working directory

set -uo pipefail

self=$(cd "$(dirname "$0")" && pwd)/$(basename "$0")

# ------------------------------------------------------------------ one service (recursive)
if [ "${1:-}" = "--one" ]; then
  shift
  svc=${1:?service}
  root=${2:?root}
  ok_empty=${3:?ok-empty}
  shift 3

  out=$(cd "$root/services/$svc" && "$@" 2>&1)
  rc=$?

  if [ "$rc" -eq 5 ] && [ "$ok_empty" = "ok-empty" ]; then
    printf '→ %s (no tests yet)\n' "$svc"
    exit 0
  fi
  if [ "$rc" -ne 0 ]; then
    printf '\n→ %s FAILED\n%s\n' "$svc" "$out"
    exit 1
  fi
  printf '→ %s\n' "$svc"
  exit 0
fi

# ------------------------------------------------------------------ the fan-out
jobs=${1:?jobs}
require=${2:?require}
ok_empty=${3:?ok-empty}
shift 3

if [ "$#" -eq 0 ]; then
  echo "fan-out.sh: no command given" >&2
  exit 2
fi

root=$(pwd)

# A shard supplies its own service list on stdin (see tools/shard.sh); otherwise every
# service carrying `require` is processed.
if [ ! -t 0 ]; then
  # A shard hands over every service it owns; the `require` filter still applies, which is
  # what lets one partition drive mypy, pytest and alembic even though not every service
  # has an `app`, a `tests/` or an `alembic.ini`.
  services=$(
    while read -r svc; do
      [ -n "$svc" ] || continue
      [ -e "$root/services/$svc/$require" ] && printf '%s\n' "$svc"
    done
  )
else
  services=$(
    for dir in "$root"/services/*/; do
      svc=$(basename "$dir")
      [ -e "$root/services/$svc/$require" ] && printf '%s\n' "$svc"
    done
  )
fi

[ -n "$services" ] || exit 0

# `-I SVC` keeps the rebuilt command line short; the child does the real work. xargs returns
# 123 when any child failed, which is mapped to 1 so callers see an ordinary failure.
printf '%s\n' "$services" \
  | xargs -P "$jobs" -I SVC "$self" --one SVC "$root" "$ok_empty" "$@"
rc=$?

if [ "$rc" -ne 0 ]; then
  exit 1
fi
exit 0
