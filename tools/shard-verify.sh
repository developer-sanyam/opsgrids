#!/usr/bin/env bash
# Assert that the shards partition the service list: every service in exactly one shard.
#
# The failure this prevents is silent. A miscount does not break a build — it drops a
# service's tests, and the pipeline stays green while nothing checks that service.
#
# usage: shard-verify.sh <shards>
set -euo pipefail

shards=${1:?shards}

expected=$(for dir in services/*/; do basename "$dir"; done | sort)
actual=$(for i in $(seq 1 "$shards"); do tools/shard.sh "$i" "$shards"; done | sort)

if [ "$expected" != "$actual" ]; then
  echo "shard-verify: shards do not partition the service list" >&2
  diff <(printf '%s\n' "$expected") <(printf '%s\n' "$actual") >&2 || true
  exit 1
fi

dupes=$(printf '%s\n' "$actual" | uniq -d)
if [ -n "$dupes" ]; then
  echo "shard-verify: service in more than one shard: $dupes" >&2
  exit 1
fi

printf 'shard-verify: %s services partition cleanly across %s shards\n' \
  "$(printf '%s\n' "$expected" | wc -l | tr -d ' ')" "$shards"
