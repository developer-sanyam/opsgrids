#!/usr/bin/env bash
# The one pipeline the three sharded, per-service Makefile targets share: partition over
# every service (tools/shard.sh), optionally narrow to what a change could affect
# (tools/filter-affected.sh), then fan out (tools/fan-out.sh --stdin).
#
# That middle step means an empty list reaching fan-out.sh can now mean two different
# things, and they are not the same severity. fan-out.sh's own "--stdin given but no
# services were piped in" guard exists for the case this script must keep treating as a
# hard failure: shard.sh itself produced nothing, which is always a bug (see its own
# comment, and the TTY-detection bug in fan-out.sh's history that a silent empty-success
# once hid). But once a filter sits upstream, emptying a shard out can also be entirely
# intentional — "nothing AFFECTED_SERVICES names landed in this shard" — and that must
# stay a success, not trip the same guard. So the two emptinesses are told apart here,
# before fan-out.sh ever sees the (possibly filtered) list.
#
# usage: shard-filtered.sh <shard> <shards> <jobs> <require> <ok-empty|strict> <command...>
set -euo pipefail

shard=${1:?shard}
shards=${2:?shards}
jobs=${3:?jobs}
require=${4:?require}
ok_empty=${5:?ok-empty}
shift 5
if [ "$#" -eq 0 ]; then
  echo "shard-filtered.sh: no command given" >&2
  exit 2
fi

root=$(cd "$(dirname "$0")/.." && pwd)

pre=$("$root/tools/shard.sh" "$shard" "$shards")
if [ -z "$pre" ]; then
  echo "shard-filtered: shard $shard/$shards produced no services — that is always a bug in tools/shard.sh, not an empty filter" >&2
  exit 2
fi

post=$(printf '%s\n' "$pre" | "$root/tools/filter-affected.sh")
if [ -z "$post" ]; then
  echo "shard-filtered: shard $shard/$shards — nothing affected landed here, nothing to do"
  exit 0
fi

printf '%s\n' "$post" | "$root/tools/fan-out.sh" --stdin "$jobs" "$require" "$ok_empty" "$@"
