#!/usr/bin/env bash
# Print the services belonging to one shard.
#
# CI runs on 2-vCPU runners, where running two of anything at once made each three times
# slower and the whole step lose (recorded in .github/workflows/ci.yml). The way to buy
# parallelism on hardware that small is another runner, not another process — so the
# per-service work is split across jobs, each with its own machine and its own database.
#
# Assignment is `position mod shards` over **every** service directory, sorted. Two
# properties worth keeping: it is deterministic, so a rerun of shard 2 runs exactly what it
# ran before; and a newly added service lands in a shard without anyone editing a list.
#
# The list is deliberately unfiltered. Sharding a *filtered* list is a trap I walked into:
# 33 services have `tests/` and 31 have `alembic.ini`, so `position mod shards` over each
# put the same service in different shards — and a test shard then ran a service whose
# migrations a different shard had applied, against tables that did not exist. Partition
# once over everything; let the consumer skip what it cannot use.
#
# usage: shard.sh <index> <shards>
#
#   index     1-based shard number
#   shards    how many shards in total
#
# `tools/shard-verify.sh` asserts the shards partition the service list exactly, so a
# miscount cannot quietly drop a service's tests.

set -euo pipefail

index=${1:?index}
shards=${2:?shards}

if [ "$index" -lt 1 ] || [ "$index" -gt "$shards" ]; then
  echo "shard.sh: index $index outside 1..$shards" >&2
  exit 2
fi

i=0
for dir in services/*/; do
  svc=$(basename "$dir")
  if [ $(( i % shards + 1 )) -eq "$index" ]; then
    printf '%s\n' "$svc"
  fi
  i=$(( i + 1 ))
done
